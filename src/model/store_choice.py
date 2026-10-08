"""Where a station store lives: the rules the Transfer Map's trial store and
the Sample DB's store share (owner request 2026-10-07, "on first startup for
a user without a DB, or on creating a new DB, the user is prompted to save it
somewhere").

Both stores are per user and explicit: with none chosen the map's page is
the `new_store` prompt (a folder, prefilled with `suggested_dir`, a "Choose
folder…" list that walks the disk one level at a time, and a name), and every
new store asks where. Neither may live inside the station's own folder
(`install_root`): an update replaces that folder.

A store on the cloud drive (`~/QMDL_Drive`) or any FUSE or network mount
(`remote_reason`) is never opened live there (owner ruling 2026-10-08
morning: "Store on cloud is fine, just make a local copy for stability of db
ops"): New store / Open store, and a remembered store, work on a LOCAL
WORKING COPY under the user's stores folder (`working_copy_path`:
`<suggested_dir(email)>/<name>-<8 hex>/<name>.sqlite`, its side folders
beside it), and the drive location is the copy's HOME. A hidden record
beside the copy (`.<name>.sqlite.home.json`: the home, and the home's size
and modification time when the two were last the same) is how the station
tells "the drive was changed elsewhere" from "only this computer changed":
the backup (`controller.backup.home_jobs`) writes the copy back to its home
after saves and at Quit, never over a home changed elsewhere; opening a copy
whose home changed elsewhere asks before replacing the copy (and keeps the
replaced copy as `<name>.local-<time>.sqlite`).

`snapshot_sqlite` is the one way a live store is copied: SQLite's online
backup API into a temporary file, then an atomic rename, so a copy is never
half a database (the migration of a store left in the install, and the
cloud backup, `controller.backup`).

No I/O at import; nothing here is called on a stop path.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from events import events
from result import NeedsConfirm, Refused

#: The first bytes of every SQLite database file.
SQLITE_MAGIC = b"SQLite format 3\x00"
#: The "Choose folder…" list stops listing subfolders after this many (a
#: folder with thousands of entries must not stall the page).
FOLDER_LIMIT = 200
#: Where the prompt suggests a signed-in user keeps their stores:
#: `~/transfer-stage-runs/stores/<email>/` (the home is read at call time).
SUGGESTED_PARTS = ("transfer-stage-runs", "stores")
#: The cloud drive's mountpoint in the home folder (rclone; `controller.backup`
#: writes the automatic backups there). No live store may be under it.
DRIVE = "QMDL_Drive"
#: The mount table read to tell a remote folder from a local one (Linux);
#: where it cannot be read, only the `DRIVE` path rule holds.
MOUNTS = "/proc/mounts"
#: Network file systems: no live store on them either. Every `fuse.<kind>`
#: (rclone, sshfs, gvfs...) is refused too; `fuseblk` (NTFS or exFAT on a
#: local disk) is a local disk.
REMOTE_FSTYPES = ("nfs", "nfs4", "cifs", "smb3", "smbfs", "9p", "afs")


def install_root():
    """The station's own folder: beside the launchers in a PyInstaller
    bundle, else the checkout (the directory holding `src/`)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    return path == root or root in path.parents


def _mount_of(path):
    """`(mountpoint, fstype)` of the mount holding `path` (the longest
    mountpoint above it in `MOUNTS`), or None when the table cannot be
    read. Reads one small file; never touches the store's folder."""
    try:
        with open(MOUNTS, encoding="utf-8", errors="replace") as handle:
            rows = handle.read().splitlines()
    except OSError:
        return None
    best = None
    for row in rows:
        fields = row.split()
        if len(fields) < 3:
            continue
        # /proc/mounts escapes a space in a mountpoint as \040.
        point = Path(fields[1].replace("\\040", " ").replace("\\011", "\t"))
        if (path == point or point in path.parents) and (
                best is None or len(point.parts) > len(best[0].parts)):
            best = (point, fields[2])
    return best


def remote_reason(path):
    """Why `path` cannot hold a LIVE store (a sentence), or None: anything
    under `~/QMDL_Drive` (by its path alone, mounted or not), and anything on
    a FUSE (rclone, sshfs...) or network mount. A live SQLite database needs
    a local disk (its locking and every write over FUSE are slow and can
    lose data when the mount drops; with the drive unmounted a write would
    land in the bare mountpoint). Not a refusal for New / Open store (owner
    ruling 2026-10-08 morning): such a store is worked on through a local
    copy (`StorePrompt._working_store`)."""
    path = Path(path).expanduser()
    why = ("SQLite over a cloud or network mount is slow and can lose data "
           "when the mount drops, so the station works on a local copy and "
           "writes it back (with the backup) to the drive")
    drive = Path.home() / DRIVE
    for candidate in {path, path.resolve()}:
        if candidate == drive or drive in candidate.parents:
            return f"{path} is on the cloud drive ({drive}); {why}."
    found = _mount_of(path.resolve())
    if found is not None:
        point, kind = found
        if kind.startswith("fuse.") or kind in REMOTE_FSTYPES:
            return (f"{path} is on the cloud drive or a network folder "
                    f"({point} is a {kind} mount); {why}.")
    return None


def refuse_remote(path):
    """Refused (`remote_reason`) when `path` cannot hold a live store (only
    where no working copy applies: the Sample DB's legacy copy)."""
    reason = remote_reason(path)
    if reason:
        raise Refused(reason)


def user_folder(email):
    """A folder name for one user: the email with anything but letters,
    digits, `@`, `.`, `_` and `-` made `_` ("" for no email)."""
    return re.sub(r"[^A-Za-z0-9@._-]", "_", str(email or "").strip().lower())


def suggested_dir(email=None):
    """`~/transfer-stage-runs/stores/<email>/` for a signed-in user, the
    `stores/` folder itself for nobody in particular. Under
    `TRANSFER_STAGE_DATA_ROOT` when it is set (a test or SIM run must never
    suggest the operator's real data folder; audit 2026-10-08)."""
    root = os.environ.get("TRANSFER_STAGE_DATA_ROOT", "").strip()
    base = (Path(root).expanduser() / SUGGESTED_PARTS[-1] if root
            else Path.home().joinpath(*SUGGESTED_PARTS))
    name = user_folder(email)
    return base / name if name else base


def is_sqlite(path):
    """True when `path` starts like a SQLite database; OSError propagates."""
    with open(path, "rb") as handle:
        return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC


def folder_options(typed, suggested=None):
    """The "Choose folder…" list for the folder `typed`: the folder itself
    (or its nearest existing parent), that folder's parent, the suggested
    folder, the home folder, then the folder's subfolders (hidden ones and
    any past `FOLDER_LIMIT` left out). Absolute paths, no repeats. Only the
    local folder being walked is listed, never anything recursively."""
    out = []

    def add(path):
        text = str(path)
        if text and text not in out:
            out.append(text)

    here = Path(str(typed or "").strip() or (suggested or Path.home())).expanduser()
    try:
        here = here.resolve()
        while not here.is_dir() and here.parent != here:
            here = here.parent
    except OSError:
        here = Path.home()
    add(here)
    if here.parent != here:
        add(here.parent)
    if suggested:
        add(Path(suggested).expanduser())
    add(Path.home())
    try:
        children = sorted((p for p in here.iterdir()
                           if p.is_dir() and not p.name.startswith(".")),
                          key=lambda p: p.name.lower())
    except OSError:
        children = []
    for child in children[:FOLDER_LIMIT]:
        add(child)
    return out


def snapshot_sqlite(source, dest, timeout=10.0):
    """A consistent copy of the live database `source` at `dest`: SQLite's
    online backup API into `<dest>.<pid>.part` beside it, then an atomic
    rename. A failure leaves no part file and never a half-written `dest`.
    Returns `dest`."""
    source, dest = Path(source), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f".{dest.name}.{os.getpid()}.part")
    try:
        src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True,
                              timeout=timeout)
        try:
            dst = sqlite3.connect(str(part))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(part, dest)
    finally:
        try:
            part.unlink()
        except OSError:
            pass
    return dest


def local_snapshot(source, timeout=10.0):
    """`snapshot_sqlite` into a fresh file in the system's temporary folder
    (never on a slow or remote mount); the caller deletes it."""
    handle, name = tempfile.mkstemp(prefix="station-store-", suffix=".sqlite")
    os.close(handle)
    os.unlink(name)
    return snapshot_sqlite(source, Path(name), timeout=timeout)


def copy_store(source, dest, folders=()):
    """Copy the store `source` to the new file `dest` (never over one that
    exists), with each of `folders` (names beside `source`) copied beside
    `dest`. The original stays exactly where it is."""
    source, dest = Path(source), Path(dest)
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")
    snapshot_sqlite(source, dest)
    for name in folders:
        here = source.parent / name
        if here.is_dir():
            shutil.copytree(here, dest.parent / name, dirs_exist_ok=True)
    return dest


# -- a store on the drive: the local working copy (2026-10-08) ---------------

#: The record beside a working copy: `.<name>.sqlite` + this.
HOME_SUFFIX = ".home.json"
#: Modification times this close are the same file (a cloud drive keeps
#: them to the millisecond: Google Drive through rclone); any closer
#: tolerance would miss an edit made elsewhere in the same second.
SAME_MTIME_NS = 1_000_000


def store_key(db):
    """`<name>-<8 hex>`: the file name without `.sqlite` and the first 8 hex
    digits of the SHA-1 of its full path. The same store always gets the
    same key; two stores of the same name in different folders never share
    one (the backup's subfolders, the working copies' folders)."""
    db = Path(db)
    digest = hashlib.sha1(str(db.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{db.stem}-{digest}"


def working_copy_path(home, base):
    """Where the store `home` (on the drive) is worked on: `<base>/<key>/
    <name>.sqlite`, its side folders beside it."""
    home = Path(home)
    return Path(base).expanduser() / store_key(home) / home.name


def _record_path(local):
    local = Path(local)
    return local.with_name(f".{local.name}{HOME_SUFFIX}")


def read_home(local):
    """`(home, signature)` recorded beside the working copy `local` (the
    signature None: the home did not exist yet), or None: not a working
    copy."""
    try:
        data = json.loads(_record_path(local).read_text(encoding="utf-8"))
        return Path(data["home"]), data.get("signature")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_home(local, home, signature, mine=None):
    """Record (atomically) that `local` is the working copy of `home`, the
    two last the same when `home` had `signature` and `local` had `mine`
    (default: what it has now, `local_signature`)."""
    record = _record_path(local)
    record.parent.mkdir(parents=True, exist_ok=True)
    part = record.with_name(record.name + ".part")
    part.write_text(json.dumps({
        "home": str(home), "signature": signature,
        "local": local_signature(local) if mine is None else mine}),
        encoding="utf-8")
    os.replace(part, record)


def local_signature(local):
    """The working copy's `file_signature`, its WAL's appended (None: no
    file)."""
    mine = file_signature(local)
    wal = file_signature(Path(f"{local}-wal"))
    return None if mine is None else mine + (wal or [])


def unchanged_since_sync(local):
    """True when the working copy `local` is exactly as it was when it and
    its home were last the same (nothing to write back)."""
    try:
        data = json.loads(_record_path(local).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("local") is not None and \
        data.get("local") == local_signature(local)


def forget_home(local):
    try:
        _record_path(local).unlink()
    except OSError:
        pass


def file_signature(path):
    """`[size, mtime_ns]` of `path`, or None when there is no file."""
    try:
        st = Path(path).stat()
    except FileNotFoundError:
        return None
    return [st.st_size, st.st_mtime_ns]


def same_signature(a, b):
    if a is None or b is None:
        return a is None and b is None
    return a[0] == b[0] and abs(a[1] - b[1]) <= SAME_MTIME_NS


def home_changed(local):
    """True when the home of the working copy `local` was changed elsewhere
    since the two were last the same: it must not be written over. OSError
    when the home cannot be looked at."""
    record = read_home(local)
    if record is None:
        return False
    home, signature = record
    return not same_signature(signature, file_signature(home))


def drive_is_newer(local, home):
    """Opening the working copy `local` of `home`: True when the drive's copy
    has something this one has not (changed elsewhere since they were last
    the same; with no record, modified later than this one). OSError when
    the drive cannot be looked at."""
    now = file_signature(home)
    if now is None:
        return False
    record = read_home(local)
    if record is not None and Path(record[0]) == Path(home):
        return not same_signature(record[1], now)
    mine = [file_signature(p) for p in (local, Path(f"{local}-wal"))]
    newest = max(s[1] for s in mine if s is not None)
    return now[1] > newest + SAME_MTIME_NS


def _copy_missing(source, dest):
    """Every file under `source` that `dest` does not have yet, copied (a
    part file, then a rename); hidden files skipped, nothing overwritten."""
    copied = 0
    for here, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            src = Path(here) / name
            out = Path(dest) / src.relative_to(source)
            if out.exists():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            part = out.with_name(f".{out.name}.part")
            shutil.copy2(src, part)
            os.replace(part, out)
            copied += 1
    return copied


def pull_home(home, local, folders=()):
    """Make (or refresh) the working copy `local` of `home`. The database
    goes through SQLite's online backup API: a fresh copy into a part file
    and a rename; over an existing copy, that copy is first kept as
    `<name>.local-<time>.sqlite`, then the backup API writes into it (so its
    WAL stays consistent). Each side folder in `folders` (names beside
    `home`) is copied where the copy lacks a file (nothing is overwritten).
    Returns the kept copy, or None."""
    home, local = Path(home), Path(local)
    kept = None
    if local.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        kept = snapshot_sqlite(local, local.with_name(
            f"{local.stem}.local-{stamp}{local.suffix}"))
        src = sqlite3.connect(home.resolve().as_uri() + "?mode=ro", uri=True,
                              timeout=10.0)
        try:
            dst = sqlite3.connect(str(local), timeout=10.0)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    else:
        for stale in (Path(f"{local}-wal"), Path(f"{local}-shm")):
            if stale.exists():
                stale.unlink()
        snapshot_sqlite(home, local)
    for name in folders:
        if (home.parent / name).is_dir():
            _copy_missing(home.parent / name, local.parent / name)
    write_home(local, home, file_signature(home))
    return kept


class StorePrompt:
    """What the Transfer Map and the Sample DB share about the `new_store`
    prompt: the suggested folder, the "Choose folder…" list, Change store /
    Cancel, and the backup hook. The host has the Params `store_path`,
    `store_dir` and `store_name`, `has_store`, `db_path`, `output_root`,
    `_touch()` and `NAME`; it checks `_choosing_store` in its `phase`."""

    #: The procedure step that asks where the store goes.
    PROMPT = "new_store"
    #: The folder the prompt suggests (Setup sets it per signed-in user:
    #: `suggested_dir(email)`); None: `suggested_dir()`.
    suggested_store_dir = None
    #: `hook(model)` after the store was written (Setup: the backup), or None.
    backup_hook = None
    #: The prompt is open although a store is chosen (Change store…).
    _choosing_store = False

    @property
    def suggested_folder(self):
        return str(self.suggested_store_dir or suggested_dir())

    def _prefill_store_dir(self):
        if not str(getattr(self, "store_dir", "") or "").strip():
            self.store_dir = self.suggested_folder

    def suggest_store_dir(self, folder):
        """Setup, at a sign-in: the user's own suggestion. A folder the
        operator typed is kept; the previous suggestion is replaced."""
        previous = self.suggested_folder
        self.suggested_store_dir = str(folder) if folder else None
        typed = str(getattr(self, "store_dir", "") or "").strip()
        if not typed or typed == previous:
            self.store_dir = self.suggested_folder
        self._touch()

    # -- "Choose folder…" ---------------------------------------------------
    @property
    def store_folder_pick(self):
        return str(self.store_dir or "")

    @property
    def store_folder_options(self):
        return folder_options(self.store_dir, self.suggested_folder)

    def pick_store_folder(self, folder):
        """A folder from the list becomes the folder for the new store (and
        the list then shows its subfolders)."""
        self.store_dir = str(folder)
        self._touch()
        return self.store_dir

    # -- the prompt ---------------------------------------------------------
    def change_store(self):
        """Change store…: the prompt again, prefilled with the open store."""
        self._refuse_store_change()
        self._choosing_store = True
        if self.has_store:
            self.store_dir = str(self.output_root)
            self.store_path = str(self.db_path)
        self._prefill_store_dir()
        self._touch()
        return self.store_dir

    def cancel_store_choice(self):
        """Back to the open store; with none there is nothing to go back to."""
        if not self.has_store:
            raise Refused("There is no store to go back to: open one, or make "
                          "a new one.")
        self._choosing_store = False
        self._touch()
        return str(self.db_path)

    def _refuse_store_change(self):
        """The host refuses here while a store change would hurt."""

    def _refuse_inside_install(self, path):
        if inside(install_root(), path):
            raise Refused(f"{path}: the store cannot live inside the station's "
                          "own folder; updates replace that folder. Choose a "
                          f"folder outside {install_root()}.")

    def _refuse_store_place(self, path):
        """Every store path New store / Open store accepts passes here: not
        inside the install. (A store on the cloud drive or a network mount
        is worked on through a local copy: `_working_store`.)"""
        self._refuse_inside_install(path)

    # -- a store on the drive: the local working copy ------------------------
    def _side_names(self, db):
        """The folders beside the database `db` that belong to it (names)."""
        return ()

    @property
    def store_home(self):
        """The drive location the open store is a working copy of (its
        cloud home), or None."""
        if not self.has_store:
            return None
        record = read_home(self.db_path)
        return record[0] if record else None

    def _working_store(self, path, confirmed=False):
        """Open store's database for the file `path`: on the drive, its
        local working copy (made now, or the one already there); a local
        working copy (the remembered store) is itself. Either way, a copy
        whose drive home was changed elsewhere is replaced by the drive's
        only once the operator confirms (`NeedsConfirm`). Anything else is
        `path`."""
        path = Path(path)
        if remote_reason(path):
            home, local = path, working_copy_path(path, self.suggested_folder)
        else:
            record = read_home(path)
            if record is None:
                return path
            home, local = record[0], path
        folders = self._side_names(home)
        if not local.exists():
            try:
                pull_home(home, local, folders)
            except (OSError, sqlite3.Error) as exc:
                raise Refused(f"{home} could not be copied to {local} ({exc}).")
            self._say_working_copy(local, home)
            return local
        try:
            newer = drive_is_newer(local, home)
        except OSError:
            newer = False                  # the drive is not there: work here
        if newer and not confirmed:
            raise NeedsConfirm(
                f"The drive's copy {home} was changed elsewhere since this "
                f"computer's working copy {local} was last the same. Replace "
                "the working copy with the drive's? This computer's copy is "
                f"kept beside it as {local.stem}.local-<time>.sqlite. Cancel "
                "changes nothing, and nothing is written back over the "
                "drive's copy.", "open_store")
        if newer:
            try:
                kept = pull_home(home, local, folders)
            except (OSError, sqlite3.Error) as exc:
                raise Refused(f"{home} could not be copied to {local} ({exc}).")
            events.warn("Working Copy Replaced", f"{local} now holds the "
                        f"drive's newer copy {home}; the previous one is kept "
                        f"as {kept}.", source=self.NAME)
        self._say_working_copy(local, home)
        return local

    def _startup_store(self, path):
        """The store a constructor opens (`--map-db` / `--sample-db`, a
        remembered choice): one on the drive through its working copy, never
        live. Nobody can be asked here, so a working copy whose drive copy
        was changed elsewhere is opened as it is (nothing is written back
        over the drive's; Open store on the drive file asks). None: it could
        not be copied."""
        if path is None or not remote_reason(path):
            return path
        try:
            return self._working_store(path)
        except NeedsConfirm:
            local = working_copy_path(path, self.suggested_folder)
            events.warn("Working Copy Kept", f"{path} was changed elsewhere; "
                        f"working on this computer's copy {local} as it is "
                        "(nothing is written back over the drive's). Open "
                        f"store on {path} to choose which copy to keep.",
                        source=self.NAME)
            return local
        except Refused as refusal:
            events.warn("Store Not Opened", refusal.reason, source=self.NAME)
            return None

    def _say_working_copy(self, local, home):
        events.info("Working Copy", f"Working on {local} on this computer, a "
                    f"copy of {home}; it syncs back to the drive after each "
                    "save and at Quit.", source=self.NAME)

    def _new_working_store(self, path):
        """New store at `path`: on the drive, made as a local working copy
        whose home is `path` (the sync writes it there); else `path`."""
        path = Path(path)
        if not remote_reason(path):
            return path
        local = working_copy_path(path, self.suggested_folder)
        if local.exists():
            raise Refused(f"{local} (this computer's working copy of {path}) "
                          f"already exists. Type {path} under Existing store "
                          "file and press Open store to use it.")
        write_home(local, path, None)
        return local

    def _remember_home(self):
        """The open store's cloud home (None: none) beside the remembered
        store, as `<STORE_KEY>_home`."""
        if self.choices is None:
            return
        home = self.store_home
        try:
            self.choices.write(self.STORE_KEY + "_home",
                               str(home) if home else None)
        except OSError as exc:
            events.debug("Store Home Not Remembered", repr(exc), source=self.NAME)

    def _request_backup(self):
        hook = self.backup_hook
        if hook is None:
            return
        try:
            hook(self)
        except Exception as exc:
            events.debug("Backup Not Requested", repr(exc), source=self.NAME)
