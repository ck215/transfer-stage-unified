"""Where a station store lives: the rules the Transfer Map's trial store and
the Sample DB's store share (owner request 2026-10-07, "on first startup for
a user without a DB, or on creating a new DB, the user is prompted to save it
somewhere").

Both stores are per user and explicit: with none chosen the map's page is
the `new_store` prompt (a folder, prefilled with `suggested_dir`, a "Choose
folder…" list that walks the disk one level at a time, and a name), and every
new store asks where. Neither may live inside the station's own folder
(`install_root`): an update replaces that folder.

`snapshot_sqlite` is the one way a live store is copied: SQLite's online
backup API into a temporary file, then an atomic rename, so a copy is never
half a database (the migration of a store left in the install, and the
cloud backup, `controller.backup`).

No I/O at import; nothing here is called on a stop path.
"""
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from events import events
from result import Refused

#: The first bytes of every SQLite database file.
SQLITE_MAGIC = b"SQLite format 3\x00"
#: The "Choose folder…" list stops listing subfolders after this many (a
#: folder with thousands of entries must not stall the page).
FOLDER_LIMIT = 200
#: Where the prompt suggests a signed-in user keeps their stores:
#: `~/transfer-stage-runs/stores/<email>/` (the home is read at call time).
SUGGESTED_PARTS = ("transfer-stage-runs", "stores")


def install_root():
    """The station's own folder: beside the launchers in a PyInstaller
    bundle, else the checkout (the directory holding `src/`)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    return path == root or root in path.parents


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

    def _request_backup(self):
        hook = self.backup_hook
        if hook is None:
            return
        try:
            hook(self)
        except Exception as exc:
            events.debug("Backup Not Requested", repr(exc), source=self.NAME)
