"""The stores' automatic backup (owner request 2026-10-07, B: "local live DB
+ automatic backup").

The live stores stay on this computer; after every write that matters (a
trial saved or finalized, a sample, chip or flake made or changed, a picture
added) and at Quit, a copy goes to the signed-in user's backup folder:

    STATION_BACKUP_DIR=off                     nobody is backed up
    the user's setting "backup_dir"            exactly that folder
    $STATION_BACKUP_DIR/<email>/               the station-wide root
    ~/QMDL_Drive/transfer-stage-dbs/<email>/   when ~/QMDL_Drive exists
    (none)                                     a Guest, or none of the above

Any of these under `~/QMDL_Drive` is used only while the drive is mounted.
A folder that is a store's own folder (or inside the folders it mirrors) is
refused: the snapshot would be renamed over the live database.

Each store has its own subfolder, `<name>-<8 hex>/` (`store_subfolder`: the
store's file name and a hash of its full path), so two stores with the same
file name never overwrite each other's copies (audit 2026-10-08 item 6). The
subfolder mirrors the store's own folder: `<name>.sqlite` beside the folders
the store keeps there (the Transfer Map's `<name>/` pictures and videos and
`exports/`, the Sample DB's `images/`). Copies an earlier version wrote flat
into the backup folder are left where they are. A database is copied with
SQLite's online backup API into a temporary file on THIS computer, that file
copied to a hidden part file in the backup folder, then renamed over the
backup: a reader never sees half a database, and SQLite never runs on the
(slow, rclone-mounted) drive. Other files are copied when their size or
modification time changed since the last copy (`.backup-manifest.json` in the
backup folder remembers them), so nothing on the drive is ever scanned
beyond one stat of a file the manifest does not know yet. A file modified
in the last `BackupService.SETTLE_S` seconds is still being written and waits
for the next run (and the Transfer Map hands over its database only while a
trial is open).

One background thread does the work; a request while it runs schedules one
more run after it (requests coalesce per backup folder). A run stops at
`RUN_BUDGET_S`. A failure (the mount missing, slow or read-only) is ONE
warning per streak of failures, never a dialog; the next success says so.
Restoring is in docs/rebuild/RECORDING_A_TRIAL.md, "Backups".
"""
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from events import events
from model.store_choice import local_snapshot, user_folder

SOURCE = "Backup"
ENV = "STATION_BACKUP_DIR"
#: `STATION_BACKUP_DIR` values that turn the backup off for the station.
OFF = ("0", "off", "none", "no", "false")
#: The user setting (`UserStore.put_setting(email, SETTING, folder)`).
SETTING = "backup_dir"
DRIVE = "QMDL_Drive"
DRIVE_FOLDER = "transfer-stage-dbs"
MANIFEST = ".backup-manifest.json"


class Unavailable(OSError):
    """The backup folder cannot be reached now (the drive is not mounted)."""


class NoFolder(Exception):
    """No backup folder: the default's drive does not exist on this
    computer. Not a failure; the status line says so."""


class Target:
    """Where one user's backup goes. Built on the caller's thread without
    touching the disk; `ready()` (on the backup thread) checks the drive and
    makes the folder."""

    def __init__(self, folder, anchor=None):
        self.folder = Path(folder)
        #: A folder that must already exist (the mounted drive); None: the
        #: folder is made with its parents.
        self.anchor = None if anchor is None else Path(anchor)

    def ready(self):
        """The folder, made when needed. With an anchor (the default drive,
        `~/QMDL_Drive`): NoFolder when it does not exist, Unavailable when it
        is not a folder or is a folder with nothing mounted on it (2026-10-08:
        an unmounted mountpoint is never written into; what is written there
        would hide under the drive and never reach it). A folder the user set
        has no anchor and is used as it is."""
        if self.anchor is not None:
            if not self.anchor.is_dir():
                if not os.path.lexists(self.anchor):
                    raise NoFolder(f"no {self.anchor} on this computer")
                raise Unavailable(f"{self.anchor} is not a folder (is the drive "
                                  "mounted?)")
            if not os.path.ismount(self.anchor):
                raise Unavailable(f"{self.anchor} is not mounted (is the drive "
                                  "connected?)")
        self.folder.mkdir(parents=True, exist_ok=True)
        return self.folder

    def __eq__(self, other):
        return isinstance(other, Target) and (self.folder, self.anchor) == (
            other.folder, other.anchor)

    def __repr__(self):
        return f"Target({str(self.folder)!r})"


def target(email, setting=None, env=None, home=None):
    """The `Target` for `email` (see the module docstring), or None: a Guest
    (no email), the backup off, or no folder known."""
    if not email:
        return None
    env = os.environ.get(ENV, "") if env is None else env
    env = (env or "").strip()
    if env.lower() in OFF:
        return None
    # The drive is looked at on the backup thread only (`Target.ready`): a
    # hung mount must never hold the caller.
    drive = (Path.home() if home is None else Path(home)) / DRIVE
    if setting:
        return _on_drive(Path(str(setting)).expanduser(), drive)
    if env:
        return _on_drive(Path(env).expanduser() / user_folder(email), drive)
    return Target(drive / DRIVE_FOLDER / user_folder(email), anchor=drive)


def _on_drive(folder, drive):
    """A folder someone set: anchored to the drive when it lies under
    `~/QMDL_Drive` (architecture audit 2026-10-08: the default's mount check,
    so an unmounted drive is never written into), else used as it is. A
    lexical test only: nothing on the disk is touched here."""
    under = folder == drive or drive in folder.parents
    return Target(folder, anchor=drive if under else None)


def store_subfolder(db):
    """The backup subfolder of the store `db`: its file name without
    `.sqlite` and the first 8 hex digits of the SHA-1 of its full path, e.g.
    `transfer_map-1a2b3c4d`. The same store always lands in the same place;
    two stores of the same name in different folders never share one."""
    db = Path(db)
    digest = hashlib.sha1(str(db.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{db.stem}-{digest}"


def _clash(folder, sources):
    """The database whose own folder `folder` is, or whose mirrored folders
    hold `folder`; None. Backing up there would rename the snapshot over
    the live database or copy a folder into itself."""
    here = Path(folder).resolve()
    for db, folders in sources:
        if here == Path(db).parent.resolve():
            return db
        for side in folders:
            side = Path(side).resolve()
            if here == side or side in here.parents:
                return db
    return None


class Job:
    """One backup to do: a `Target` and the stores, each `(database file,
    [folders beside it])`."""

    def __init__(self, where, sources):
        self.target = where
        self.sources = [(Path(db), [Path(f) for f in folders]) for db, folders in sources]

    @property
    def key(self):
        return str(self.target.folder)

    def merge(self, other):
        """This job with `other`'s stores added (a coalesced request)."""
        seen = {db: folders for db, folders in self.sources}
        for db, folders in other.sources:
            seen[db] = sorted({*seen.get(db, []), *folders})
        return Job(other.target, list(seen.items()))


def _signature(path):
    st = path.stat()
    return [st.st_size, st.st_mtime_ns]


class BackupService:
    """The backup thread, its coalescing and its status line."""

    #: One run gives up after this long; the rest goes the next run.
    RUN_BUDGET_S = 300.0
    #: How long Quit waits for the final backup.
    QUIT_WAIT_S = 20.0
    #: A file modified less than this long ago is still being written (a
    #: trial video recording, a picture being saved): not copied, and not
    #: marked done, so the next run copies it (audit 2026-10-08 item 14).
    SETTLE_S = 5.0

    def __init__(self):
        self._cond = threading.Condition()
        self._pending = {}           # folder -> Job, in request order
        self._thread = None
        self._failing = False
        self._manifests = {}         # folder -> {key: signature}
        #: Completed runs (a test reads it to see two requests coalesce).
        self.runs = 0
        #: (time, folder) of the last good run; (time, folder, reason) of
        #: the last failure.
        self.last_ok = None
        self.last_error = None
        #: What the last good run copied (relative paths), for the tests.
        self.copied = []
        #: Why the default folder does not apply here (no drive), or None.
        self.no_folder = None

    # -- requests -----------------------------------------------------------
    def request(self, job):
        """Back `job` up soon, on the backup thread. Returns at once."""
        if job is None or not job.sources:
            return False
        with self._cond:
            pending = self._pending.get(job.key)
            self._pending[job.key] = job if pending is None else pending.merge(job)
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True,
                                                name="store-backup")
                self._thread.start()
        return True

    @property
    def busy(self):
        with self._cond:
            return self._thread is not None

    def wait(self, timeout):
        """Wait (at most `timeout` s) until nothing is pending or running;
        True when that happened in time."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while self._thread is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
        return True

    def _loop(self):
        while True:
            with self._cond:
                if not self._pending:
                    self._thread = None
                    self._cond.notify_all()
                    return
                key = next(iter(self._pending))
                job = self._pending.pop(key)
            try:
                self.run(job)
            except Exception as exc:          # never let the thread die
                self._failed(job, exc)

    # -- one run ------------------------------------------------------------
    def run(self, job):
        """Do `job` now, on this thread. True when it all went."""
        deadline = time.monotonic() + self.RUN_BUDGET_S
        copied = []
        clash = _clash(job.target.folder, job.sources)
        if clash is not None:
            self._failed(job, OSError(
                f"the backup folder is a store's own folder ({Path(clash).parent}) "
                "or inside it; choose another backup folder"))
            return False
        try:
            folder = job.target.ready()
        except NoFolder as missing:
            with self._cond:
                self.no_folder = str(missing)
            return False
        except OSError as exc:
            self._failed(job, exc)
            return False
        try:
            manifest = self._manifest(folder)
            try:
                for db, folders in job.sources:
                    if not db.is_file():
                        continue
                    sub = store_subfolder(db)
                    here = folder / sub
                    here.mkdir(exist_ok=True)
                    if self._copy_db(db, here, manifest, sub):
                        copied.append(f"{sub}/{db.name}")
                    for side in folders:
                        copied += self._mirror(db.parent, side, here, manifest,
                                               deadline, sub)
            finally:
                self._save_manifest(folder, manifest)
        except (OSError, sqlite3.Error) as exc:
            self._failed(job, exc)
            return False
        with self._cond:
            self.no_folder = None
            self.runs += 1
            self.copied = copied
            self.last_ok = (time.time(), str(job.target.folder))
            recovered, self._failing = self._failing, False
        if recovered:
            events.info("Backup Working Again", f"The stores are backed up to "
                        f"{job.target.folder} again.", source=SOURCE)
        events.debug("Backed Up", f"{job.target.folder}: {len(copied)} file(s) "
                     "copied", source=SOURCE)
        return True

    def _copy_db(self, db, folder, manifest, sub):
        """The database, through a local snapshot, a part file and a rename
        into `folder` (its subfolder `sub`); skipped while the file (and its
        WAL) are as they were last time."""
        key = f"db:{sub}/{db.name}"
        signature = _signature(db)
        wal = db.with_name(db.name + "-wal")
        if wal.exists():
            signature += _signature(wal)
        final = folder / db.name
        if manifest.get(key) == signature and final.exists():
            return False
        local = local_snapshot(db)
        part = folder / f".{db.name}.part"
        try:
            shutil.copyfile(local, part)
            os.replace(part, final)
        finally:
            for leftover in (local, part):
                try:
                    leftover.unlink()
                except OSError:
                    pass
        manifest[key] = signature
        return True

    def _mirror(self, root, side, folder, manifest, deadline, sub):
        """New or changed files under `side` (a folder beside the database
        in `root`), copied to the same place under `folder` (the store's
        subfolder `sub`; the manifest keys and the copied list name it)."""
        side = Path(side)
        if not side.is_dir():
            return []
        try:
            base = side.relative_to(root)
        except ValueError:
            base = Path(side.name)
        copied = []
        for here, dirs, files in os.walk(side):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                if name.startswith("."):
                    continue
                source = Path(here) / name
                relative = (base / source.relative_to(side)).as_posix()
                key = f"file:{sub}/{relative}"
                try:
                    signature = _signature(source)
                except OSError:
                    continue                  # gone while we walked
                if manifest.get(key) == signature:
                    continue
                if time.time() - signature[1] / 1e9 < self.SETTLE_S:
                    continue                  # still being written: next run
                dest = folder / relative
                if key not in manifest and _same(dest, signature):
                    manifest[key] = signature   # there from an earlier run
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"the backup took longer than "
                                       f"{self.RUN_BUDGET_S:g} s; the rest goes "
                                       "next time")
                dest.parent.mkdir(parents=True, exist_ok=True)
                part = dest.with_name(f".{dest.name}.part")
                shutil.copy2(source, part)
                os.replace(part, dest)
                manifest[key] = signature
                copied.append(f"{sub}/{relative}")
        return copied

    def _manifest(self, folder):
        key = str(folder)
        if key not in self._manifests:
            try:
                data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            self._manifests[key] = data if isinstance(data, dict) else {}
        return self._manifests[key]

    def _save_manifest(self, folder, manifest):
        part = folder / (MANIFEST + ".part")
        try:
            part.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            os.replace(part, folder / MANIFEST)
        except OSError:
            pass                       # the copies themselves are what matter

    def _failed(self, job, exc):
        reason = str(exc) or type(exc).__name__
        with self._cond:
            first, self._failing = not self._failing, True
            self.last_error = (time.time(), str(job.target.folder), reason)
        if first:
            events.warn("Backup Failed", f"The stores could not be backed up to "
                        f"{job.target.folder}: {reason}. They are safe on this "
                        "computer; the station tries again after the next save "
                        "(and says so here only when it works again).",
                        source=SOURCE, exception=exc)
        else:
            events.debug("Backup Failed Again", reason, source=SOURCE)

    # -- the status line ----------------------------------------------------
    def status(self, folder=None):
        """"Last backup 23:41 → <folder>", what failed, or "No backup yet"."""
        with self._cond:
            ok, error, failing = self.last_ok, self.last_error, self._failing
            running, no_folder = self._thread is not None, self.no_folder
        lines = []
        if no_folder:
            lines.append(f"No backup folder ({no_folder}); set one under Account")
        if failing and error is not None:
            lines.append(f"Backup failed {_clock(error[0])}: {error[2]}")
        if ok is not None:
            lines.append(f"Last backup {_clock(ok[0])} → {ok[1]}")
        if not lines:
            lines.append("No backup yet" + (f" (to {folder})" if folder else ""))
        if running:
            lines.append("Backing up…")
        return "; ".join(lines)


def _same(dest, signature):
    """`dest` exists with the size and (to 2 s, a FAT or FUSE clock) the
    modification time of `signature`."""
    try:
        st = dest.stat()
    except OSError:
        return False
    return st.st_size == signature[0] and abs(st.st_mtime_ns - signature[1]) < 2e9


def _clock(when):
    return time.strftime("%H:%M", time.localtime(when))
