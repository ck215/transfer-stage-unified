"""One station per computer (owner 2026-10-07).

Two station processes on one machine fight over the serial ports: the first
holds every port, so the second one's scan finds no device - and an old
station left running unattended is a heater nobody watches. A launch takes
an OS lock on `<data root>/station.lock` before it builds anything; a second
launch that finds the lock held is pointed at the running station instead.

The lock is the operating system's own (`fcntl.flock` on macOS and Linux,
`msvcrt.locking` on Windows), so it goes away with the process that holds
it however that process ends: a crashed or killed station leaves a file, not
a lock, and the next launch takes it over. What the holder says about itself
(its PID and the address it serves) is in `station-instance.json` beside it,
a separate file because Windows refuses reads of a locked byte range.

A restart replaces the process (`os.execv`: same PID, the lock's descriptor
is closed on exec) or, on Windows, starts the new one before the old one has
exited: `RESTART_ENV` names the PID being replaced, and a lock held by that
PID is waited for rather than taken as a second station.
"""
import errno
import json
import os
import time

from events import events

SOURCE = "app"

LOCK_NAME = "station.lock"
INFO_NAME = "station-instance.json"

#: Set by a restart to the PID it replaces; the new process waits for that
#: PID's lock instead of treating it as another station.
RESTART_ENV = "STATION_RESTART_OF"
#: How long a restarted process waits for the old one's lock.
RESTART_WAIT_SECONDS = 20.0
#: How long a second launch waits for a starting holder to say its address.
ADDRESS_WAIT_SECONDS = 10.0

if os.name == "nt":                      # pragma: no cover - Windows only
    import msvcrt

    def _try_lock(handle):
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle):
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

#: What a lock already held looks like, per platform.
_BUSY = {errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES, errno.EDEADLK}


def data_root():
    """`TRANSFER_STAGE_DATA_ROOT`, or `~/transfer-stage-runs` - where the
    logs live (events.open_file), so the lock is per data root."""
    return (os.environ.get("TRANSFER_STAGE_DATA_ROOT")
            or os.path.join(os.path.expanduser("~"), "transfer-stage-runs"))


def pid_alive(pid):
    """True when a process with this PID exists (best effort)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":                  # pragma: no cover - Windows only
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class InstanceLock:
    """The held lock. `set_url` records the address once the view serves;
    `release` gives the lock up (idempotent)."""

    note = ""

    def __init__(self, root, handle):
        self.root = root
        self._handle = handle
        self.info_path = os.path.join(root, INFO_NAME)
        self._info = {"pid": os.getpid(), "url": "", "started": time.time()}
        self._write()

    def set_url(self, url):
        self._info["url"] = url or ""
        self._write()

    def _write(self):
        tmp = self.info_path + f".{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._info, f)
            os.replace(tmp, self.info_path)
        except OSError as exc:
            events.debug("Instance Info Not Written", repr(exc), source=SOURCE,
                         exception=exc)

    @property
    def held(self):
        return self._handle is not None

    def release(self):
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            # Only our own record goes: a successor may already have written its.
            if read_info(self.root).get("pid") == os.getpid():
                os.remove(self.info_path)
        except OSError:
            pass
        try:
            _unlock(handle)
        except OSError:
            pass
        handle.close()


def read_info(root):
    """What the holder wrote about itself: {"pid", "url", ...}, or {}."""
    try:
        with open(os.path.join(root, INFO_NAME), encoding="utf-8") as f:
            info = json.load(f)
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError):
        return {}


def _open_lock(root):
    os.makedirs(root, exist_ok=True)
    # "a+": never truncates what is there, creates it when it is not.
    handle = open(os.path.join(root, LOCK_NAME), "a+", encoding="utf-8")
    if os.name == "nt" and os.fstat(handle.fileno()).st_size == 0:   # pragma: no cover
        handle.write("\0")               # msvcrt locks a byte that must exist
        handle.flush()
    return handle


def acquire(root=None, clock=time.monotonic, sleep=time.sleep):
    """Take the one-station lock. Returns `(InstanceLock, None)` when this
    process is now the station, or `(None, info)` when another live station
    holds it (`info` is what it wrote: its pid and url, perhaps empty).

    A lock that cannot be taken for any reason but "held" (a file system
    without locks, a read-only data root) is logged and the launch goes on
    unlocked: the lock is a guard, not a gate to starting at all."""
    root = root or data_root()
    try:
        handle = _open_lock(root)
    except OSError as exc:
        return _unlocked(root, f"{os.path.join(root, LOCK_NAME)} could not be "
                         f"opened ({exc})"), None
    restart_of = os.environ.pop(RESTART_ENV, None)
    waited_until = None
    while True:
        try:
            _try_lock(handle)
        except OSError as exc:
            if exc.errno not in _BUSY:
                handle.close()
                return _unlocked(root, f"{os.path.join(root, LOCK_NAME)} could not "
                                 f"be locked ({exc})"), None
            info = read_info(root)
            holder = info.get("pid")
            if restart_of and str(holder) == restart_of:
                # Windows restart: the old process is still on its way out.
                if waited_until is None:
                    waited_until = clock() + RESTART_WAIT_SECONDS
                if clock() < waited_until:
                    sleep(0.2)
                    continue
            handle.close()
            return None, info
        stale = read_info(root)
        lock = InstanceLock(root, handle)
        # Said once the log file is open (launch() logs `lock.note`).
        lock.note = (f"this station (PID {os.getpid()}) holds "
                     f"{os.path.join(root, LOCK_NAME)}"
                     + (f"; taken over from PID {stale.get('pid')}, which had exited"
                        if stale.get("pid") not in (None, os.getpid()) else ""))
        return lock, None


def _unlocked(root, why):
    """An InstanceLock that holds nothing (the lock was unavailable); its
    note says why, for the log, once the log file is open."""
    lock = InstanceLock.__new__(InstanceLock)
    lock.root, lock._handle = root, None
    lock.info_path = os.path.join(root, INFO_NAME)
    lock._info = {"pid": os.getpid(), "url": "", "started": time.time()}
    lock.note = f"{why}; a second station on this computer would not be refused."
    return lock


def wait_for_address(root, info, clock=time.monotonic, sleep=time.sleep,
                     seconds=ADDRESS_WAIT_SECONDS):
    """The running station's address: what it wrote, or, while it is still
    starting (no address yet), what it writes within `seconds`."""
    end = clock() + seconds
    while not info.get("url") and clock() < end and pid_alive(info.get("pid")):
        sleep(0.2)
        info = read_info(root) or info
    return info.get("url") or ""
