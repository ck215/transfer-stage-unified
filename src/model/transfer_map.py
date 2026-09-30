"""The Transfer Map: the heatmap this project exists for.

A 3D map over **tilt angle, speed and force** whose value of interest is the
**channel width** of the transferred sample (owner, 2026-09-27). It builds
itself as trials are recorded: the operator arms a trial, lowers the tip,
presses Mark force at the force they want, and finishes; the station keeps
the red-percent slice of the lowering, a labelled video of the capture
region from Arm to the end (owner, 2026-09-28: "a video where timestamps
label the footage for analysis afterward"; an H.264 MP4, or JPEG frames
without the encoder, with a `video_index.csv` saying what each frame
shows), the whole screen at Arm (the microscope feed as displayed, for
context), and the tilt and speed at the time. AFM widths and thicknesses
are attached later, with their uncertainties.

**Tips are records** (`tips` table): created on demand by the first Arm on
a tip (or an import naming one), with the trial it broke on and whether it
is retired; which trials used it is derived from `trials.tip_id`.
**Polling starts itself**: once the capture region is set and a tip ID is
committed, the map starts Red Percent's run (a fresh baseline from its
first frame) and ends it as it ends a run Arm started.

**Force** has no sensor. It is approximated from the red-percent trace of
the lowering (`model.transfer_map_analysis`): several definitions, computed
from the stored raw profile whenever a figure or an export asks, never
stored in its place.

**It moves nothing.** No port, no gamepad, no device. `_halt_hardware`
disarms the trial in memory, tells its video to close and returns at once;
the aborted trial is written on a worker, and the video closed on the
map's picture thread, so a stop is never held by the disk or the encoder.

**The store is chosen by the operator** (owner decision 4, 2026-09-30,
replacing the 2026-09-27 "inside the checkout" default): with no choice
recorded the map has NO store - its state says `store: {"path": None,
"chosen": False}`, its Store section asks (Open store: an existing file;
New store: a folder and a name) and every recording command is refused
until one is chosen. The choice is remembered in the operator's choices
file (`controller.user_config`, wired in as `TransferMap.choices` by the
composition root; the model never imports the controller). A store inside
the station's own folder (the bundle root, or this checkout) is refused:
updates replace that folder. `STATION_MAP_DB=<path>` / `--map-db` override
the choice and skip the question. Pictures sit beside the file in
`<folder>/<database name>/<trial_id>/`, exports in `<folder>/exports/`.
"New session database" starts another file in the same folder. Building the
model creates nothing (the contract test builds every registered class).

Live sources are duck-typed from `on_model_added`, never a class name: tilt
from a model with `position_deg` (the Rotator), else the operator's typed
"Tilt without a rotator" (blank = no tilt, never a default 0); speed and Z from a model with
`position`, `position_time` and `mode_name` (a probe: `live_speed` if it has
one, else the manual or autonomous speed for the mode it is in); samples
from a model with `subscribe` and `grab_frame` (Red Percent), frames for
the video from its `subscribe_frames` when it has one.
"""
import csv
import json
import math
import os
import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path

import schema as sch
from devices.video import TrialRecorder, to_rgb
from events import events
from model import plot_data
from model import transfer_map_analysis as analysis
from model.base import Model
from param import Param
from result import NeedsConfirm, Refused

#: Every recording command's refusal while no store is chosen (A3).
NO_STORE = "Choose a trial store first (Transfer Map, Store)."
INSIDE_INSTALL = ("the store cannot live inside the station's own folder; "
                  "updates replace that folder")
#: The file the SQLite library writes first in every database.
_SQLITE_MAGIC = b"SQLite format 3\x00"


def _install_root():
    """The station's own folder: beside the launchers in a PyInstaller
    bundle, else the checkout (the directory holding `src/`)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    return path == root or root in path.parents


#: The figure dropdown, in the operator's words -> `plot_data` kind.
FIGURES = {
    "3D map": "map3d",
    "Slice at a force band": "slice",
    "Compare force definitions": "compare",
    "Trial profile": "profile",
}

#: The trials table. The brief's columns (with the whole-screen pictures
#: beside the region ones, schema version 2, and the Mark's pair, version
#: 3), then four of ours: where the trial came from, where its tilt and
#: speed were read, and the force indices an import gave for a trial with
#: no profile (JSON).
TRIAL_COLUMNS = (
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"), ("started_at", "TEXT"),
    ("tip_id", "TEXT"), ("tilt_deg", "REAL"), ("speed_steps_s", "REAL"),
    ("z_contact", "REAL"), ("mark_operator_t", "REAL"),
    ("mark_auto_max_t", "REAL"), ("mark_auto_min_t", "REAL"),
    ("broke", "INTEGER NOT NULL DEFAULT 0"), ("red_min", "REAL"),
    ("red_max", "REAL"), ("red_baseline", "REAL"), ("width_um", "REAL"),
    ("width_sigma_um", "REAL"), ("thickness_nm", "REAL"),
    ("thickness_sigma_nm", "REAL"), ("note", "TEXT"), ("before_path", "TEXT"),
    ("after_path", "TEXT"), ("before_full_path", "TEXT"),
    ("after_full_path", "TEXT"), ("mark_path", "TEXT"),
    ("mark_full_path", "TEXT"), ("status", "TEXT NOT NULL"),
    ("origin", "TEXT NOT NULL DEFAULT 'recorded'"), ("tilt_source", "TEXT"),
    ("speed_source", "TEXT"), ("force_given", "TEXT"),
    ("speed_measured_steps_s", "REAL"),
    # Version 5: the trial's video (the MP4, or its JPEG frames folder), its
    # frame index, how many frames it holds and how many were dropped.
    ("video_path", "TEXT"), ("video_index_path", "TEXT"),
    ("video_frames", "INTEGER"), ("video_dropped", "INTEGER"),
)
_TRIAL_NAMES = frozenset(name for name, _kind in TRIAL_COLUMNS)
PROFILE_COLUMNS = ("trial_id", "t_s", "red", "z", "x", "y")
#: The tips table (version 3): one record per tip, created on demand (the
#: first Arm on a tip it has not seen, or an import naming one). Usage is
#: derived from `trials.tip_id`, never stored twice: `TrialStore.tip`
#: adds the trial ids, their count and whether it broke.
TIP_COLUMNS = (
    ("tip_id", "TEXT PRIMARY KEY"), ("created_at", "TEXT"),
    ("first_trial_id", "INTEGER"), ("last_trial_id", "INTEGER"),
    ("last_used_at", "TEXT"), ("broke_trial_id", "INTEGER"),
    ("retired_at", "TEXT"), ("note", "TEXT"),
)
_TIP_NAMES = frozenset(name for name, _kind in TIP_COLUMNS)
#: `PRAGMA user_version`. 1: the first store. 2: `before_full_path` and
#: `after_full_path` (the whole-screen pictures). 3: `mark_path` and
#: `mark_full_path` (the pictures at the Mark) and the `tips` table. 4:
#: `speed_measured_steps_s`. 5: the video (`video_path`, `video_index_path`,
#: `video_frames`, `video_dropped`); the region stills and the Mark and
#: After whole-screen pictures are no longer taken, their columns stay for
#: the rows that have them. An older file gains the columns by `ALTER
#: TABLE ... ADD COLUMN` and a tip record for every tip its trials name,
#: the first time it is opened or written, and keeps every trial it holds.
SCHEMA_VERSION = 5

_CREATE = (
    "CREATE TABLE IF NOT EXISTS trials ("
    + ", ".join(name + " " + kind for name, kind in TRIAL_COLUMNS) + ")",
    "CREATE TABLE IF NOT EXISTS profile (trial_id INTEGER NOT NULL "
    "REFERENCES trials(id), t_s REAL NOT NULL, red REAL, z REAL, x REAL, "
    "y REAL)",
    "CREATE INDEX IF NOT EXISTS profile_trial ON profile(trial_id)",
    "CREATE TABLE IF NOT EXISTS tips ("
    + ", ".join(name + " " + kind for name, kind in TIP_COLUMNS) + ")",
)

#: A version-2 (or older) file's tips, from its trials: first and last
#: trial, the earliest trial marked broke. Names are this module's own.
_BACKFILL_TIPS = (
    "INSERT OR IGNORE INTO tips (tip_id, created_at, first_trial_id, "
    "last_trial_id, last_used_at, broke_trial_id) SELECT tip_id, "
    "MIN(started_at), MIN(id), MAX(id), MAX(started_at), "
    "MIN(CASE WHEN broke THEN id END) FROM trials WHERE "
    "TRIM(COALESCE(tip_id, '')) != '' GROUP BY tip_id")

#: Samples kept per trial. At Red Percent's fastest a lowering is minutes
#: of a few hundred rows a second; past this the rest are counted, not kept.
MAX_SAMPLES = 500_000

#: The video's rate: at most this many of Red Percent's frames a second are
#: written (it grabs at ~66 Hz; the rest are skipped by design, not counted).
VIDEO_FPS = 15
#: Frames waiting for the picture thread, about two seconds of video; a
#: frame arriving when it is full is dropped and counted, never waited for.
VIDEO_QUEUE = 2 * VIDEO_FPS
VIDEO_INDEX_COLUMNS = ("frame", "t_s", "red", "z", "marked")


def _checked(names, allowed=_TRIAL_NAMES, table="trials"):
    """Column names come from this module's own table, never from input."""
    unknown = [n for n in names if n not in allowed]
    if unknown:
        raise ValueError(f"not a {table} column: " + ", ".join(map(str, unknown)))
    return list(names)


class TrialStore:
    """The SQLite file. A connection per call (the command thread, the stop's
    worker and a view's poll are different threads); one lock for writes.
    A read of a file that does not exist answers empty and creates nothing."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    @property
    def exists(self):
        return self.path.is_file()

    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    def write(self, fn):
        """Run `fn(db)` in one transaction, creating the file on first use
        and bringing an older one up to `SCHEMA_VERSION` first."""
        with self._lock:
            fresh = not self.exists
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = self._connect()
            try:
                with db:
                    for statement in _CREATE:
                        db.execute(statement)
                    added = self._migrate(db, fresh)
                    result = fn(db)
            finally:
                db.close()
        if fresh:
            events.info("Map Database Created", "A new Transfer Map database "
                        "was created at " + str(self.path) + ".",
                        source="Transfer Map")
        elif added is not None:
            events.info("Database Upgraded", f"{self.path} now has "
                        f"{', '.join(added) or 'every column'} (version "
                        f"{SCHEMA_VERSION}). Its trials are kept.",
                        source="Transfer Map")
        return result

    @staticmethod
    def _migrate(db, fresh):
        """Bring the file to `SCHEMA_VERSION`. A file already there (or
        newer) is left alone. Older: every trials column it lacks is added
        (a column that exists is skipped, so an upgrade cut short finishes),
        then the version is set. Returns the columns added, or None when
        nothing was done or the file is new."""
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return None
        have = {row[1] for row in db.execute("PRAGMA table_info(trials)")}
        added = []
        for name, kind in TRIAL_COLUMNS:
            if name not in have:
                # Names and kinds are this module's own, never input.
                db.execute(f"ALTER TABLE trials ADD COLUMN {name} {kind}")
                added.append(name)
        if version < 3 and not fresh:
            # The tips table is new in 3 (`_CREATE` made it empty): one
            # record per tip the file's trials already name.
            db.execute(_BACKFILL_TIPS)
            added.append("tips")
        db.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
        return None if fresh else added

    def ensure(self):
        """Create the file and its schema if missing, and upgrade an older
        one; a no-op on a current file (every statement is IF NOT EXISTS).
        True when it was created now."""
        fresh = not self.exists
        self.write(lambda db: None)
        return fresh

    def read(self, sql, args=()):
        if not self.exists:
            return []
        db = self._connect()
        try:
            return [dict(row) for row in db.execute(sql, args)]
        except sqlite3.OperationalError:
            return []                  # an empty file, or one being created
        finally:
            db.close()

    # -- the operations the model needs --------------------------------------
    def insert(self, fields):
        names = _checked(fields)
        sql = ("INSERT INTO trials (" + ", ".join(names) + ") VALUES ("
               + ", ".join("?" for _ in names) + ")")
        return self.write(lambda db: db.execute(sql, [fields[n] for n in names]).lastrowid)

    def update(self, trial_id, fields, profile=None):
        names = _checked(fields)
        sql = ("UPDATE trials SET " + ", ".join(n + " = ?" for n in names)
               + " WHERE id = ?")

        def _do(db):
            if names:
                db.execute(sql, [fields[n] for n in names] + [trial_id])
            if profile:
                db.executemany("INSERT INTO profile (trial_id, t_s, red, z, x, y) "
                               "VALUES (?, ?, ?, ?, ?, ?)",
                               [(trial_id, *row) for row in profile])
        return self.write(_do)

    def delete(self, trial_id):
        """The trial and its profile; its tip's record keeps up (first and
        last trial, and the trial it broke on, from the trials left)."""
        def _do(db):
            row = db.execute("SELECT tip_id FROM trials WHERE id = ?",
                             (trial_id,)).fetchone()
            db.execute("DELETE FROM profile WHERE trial_id = ?", (trial_id,))
            db.execute("DELETE FROM trials WHERE id = ?", (trial_id,))
            if row is not None and row["tip_id"]:
                tip = row["tip_id"]
                db.execute(
                    "UPDATE tips SET "
                    "first_trial_id = (SELECT MIN(id) FROM trials WHERE tip_id = :tip), "
                    "last_trial_id = (SELECT MAX(id) FROM trials WHERE tip_id = :tip), "
                    "broke_trial_id = CASE WHEN broke_trial_id = :id THEN "
                    "(SELECT MIN(id) FROM trials WHERE tip_id = :tip AND broke = 1) "
                    "ELSE broke_trial_id END WHERE tip_id = :tip",
                    {"tip": tip, "id": trial_id})
        return self.write(_do)

    # -- tips ------------------------------------------------------------------
    def use_tip(self, tip_id, trial_id, when):
        """A trial on this tip: create its record if it has none (True), else
        move its last trial and last use (False)."""
        def _do(db):
            created = db.execute(
                "INSERT OR IGNORE INTO tips (tip_id, created_at, first_trial_id, "
                "last_trial_id, last_used_at) VALUES (?, ?, ?, ?, ?)",
                (tip_id, when, trial_id, trial_id, when)).rowcount == 1
            if not created:
                db.execute("UPDATE tips SET last_trial_id = ?, last_used_at = ?, "
                           "first_trial_id = COALESCE(first_trial_id, ?) "
                           "WHERE tip_id = ?", (trial_id, when, trial_id, tip_id))
            return created
        return self.write(_do)

    def create_tip(self, tip_id, when):
        """A tip record with no trial yet (New tip on the sheet, or a note
        on a tip before its first trial). -> created (False: it existed)."""
        return self.write(lambda db: db.execute(
            "INSERT OR IGNORE INTO tips (tip_id, created_at) VALUES (?, ?)",
            (tip_id, when)).rowcount == 1)

    def set_tip(self, tip_id, fields):
        names = _checked(fields, _TIP_NAMES, "tips")
        sql = ("UPDATE tips SET " + ", ".join(n + " = ?" for n in names)
               + " WHERE tip_id = ?")
        return self.write(lambda db: db.execute(
            sql, [fields[n] for n in names] + [tip_id]).rowcount)

    def set_tip_broke(self, tip_id, trial_id, broke):
        """The tip broke on this trial (the earliest such trial is kept), or
        it did not: then, if this was the trial it broke on, the next
        earliest stored trial marked broke, else none."""
        def _do(db):
            if broke:
                db.execute("UPDATE tips SET broke_trial_id = "
                           "MIN(COALESCE(broke_trial_id, :id), :id) "
                           "WHERE tip_id = :tip", {"tip": tip_id, "id": trial_id})
            else:
                db.execute("UPDATE tips SET broke_trial_id = (SELECT MIN(id) "
                           "FROM trials WHERE tip_id = :tip AND broke = 1 AND "
                           "id != :id) WHERE tip_id = :tip AND broke_trial_id = :id",
                           {"tip": tip_id, "id": trial_id})
        return self.write(_do)

    def _trial_ids_by_tip(self):
        ids = {}
        for row in self.read("SELECT id, tip_id FROM trials ORDER BY id"):
            ids.setdefault(row["tip_id"], []).append(row["id"])
        return ids

    @staticmethod
    def _with_usage(record, ids):
        record["trials"] = list(ids)
        record["count"] = len(ids)
        record["broke"] = record["broke_trial_id"] is not None
        return record

    def tip(self, tip_id):
        """The tip's record plus its usage (`trials`, the ids in order;
        `count`; `broke`), or None when the tip has no record."""
        rows = self.read("SELECT * FROM tips WHERE tip_id = ?", (tip_id,))
        if not rows:
            return None
        ids = [r["id"] for r in self.read(
            "SELECT id FROM trials WHERE tip_id = ? ORDER BY id", (tip_id,))]
        return self._with_usage(rows[0], ids)

    def tips(self):
        """Every tip record, in the order they were created, with its usage."""
        ids = self._trial_ids_by_tip()
        return [self._with_usage(row, ids.get(row["tip_id"], ()))
                for row in self.read("SELECT * FROM tips ORDER BY rowid")]

    def trials(self):
        return self.read("SELECT * FROM trials ORDER BY id")

    def trial(self, trial_id):
        rows = self.read("SELECT * FROM trials WHERE id = ?", (trial_id,))
        return rows[0] if rows else None

    def last(self):
        rows = self.read("SELECT * FROM trials ORDER BY id DESC LIMIT 1")
        return rows[0] if rows else None

    def count(self):
        rows = self.read("SELECT COUNT(*) AS n FROM trials")
        return rows[0]["n"] if rows else 0

    def count_for_tip(self, tip_id, up_to=None):
        """Stored trials on this tip (every status); with `up_to`, only those
        numbered up to it, so a trial's own place among its tip's trials."""
        tip = (tip_id or "").strip()
        if up_to is None:
            rows = self.read("SELECT COUNT(*) AS n FROM trials WHERE tip_id = ?",
                             (tip,))
        else:
            rows = self.read("SELECT COUNT(*) AS n FROM trials WHERE tip_id = ? "
                             "AND id <= ?", (tip, int(up_to)))
        return rows[0]["n"] if rows else 0

    def next_id(self):
        """The number the next recorded trial will get (AUTOINCREMENT never
        reuses one, so this is the sequence, not the row count)."""
        rows = self.read("SELECT seq FROM sqlite_sequence WHERE name = 'trials'")
        return (rows[0]["seq"] + 1) if rows and rows[0]["seq"] is not None else 1

    def profile(self, trial_id):
        rows = self.read("SELECT t_s, red, z, x, y FROM profile WHERE "
                         "trial_id = ? ORDER BY t_s, rowid", (trial_id,))
        out = {"t": [r["t_s"] for r in rows], "red": [r["red"] for r in rows]}
        if any(r["z"] is not None for r in rows):
            out["z"] = [r["z"] for r in rows]
        return out

    def profile_rows(self):
        return self.read("SELECT * FROM profile ORDER BY trial_id, t_s, rowid")

    def given_definitions(self):
        names = []
        for row in self.read("SELECT force_given FROM trials WHERE "
                             "force_given IS NOT NULL"):
            for name in json.loads(row["force_given"] or "{}"):
                if name not in names:
                    names.append(name)
        return names


class _NoStore(TrialStore):
    """The store while none is chosen: every read answers empty, every
    write is refused with `NO_STORE`. Never touches the disk."""

    def __init__(self):
        self.path = None
        self._lock = threading.Lock()

    @property
    def exists(self):
        return False

    def write(self, fn):
        raise Refused(NO_STORE)


class _Trial:
    """The armed trial, in memory. `samples` is appended on Red Percent's
    run thread (a list append, nothing else); everything else is written
    by the command thread."""

    def __init__(self, trial_id, tilt, speed, tip=""):
        self.id = trial_id
        self.tip = tip
        self.armed = time.monotonic()
        self.samples = []            # (t_s, red, z, x, y)
        self.dropped = 0
        self.operator_t = None
        self.z_mark = None
        self.speed = speed
        self.tilt = tilt
        self.broke = False
        self.closed = False
        #: The Red Percent run this trial started (its `run_token`), or None
        #: when the operator started it on the Red Percent page (then it is
        #: theirs to end).
        self.run = None
        #: The first Mark's time since Arm: the video says MARK from here on.
        self.first_mark_t = None
        #: The trial's video (`_Recording`), or None when the Red Percent
        #: found has no frame hook.
        self.recording = None


class _Recording:
    """The armed trial's video. Red Percent's run thread offers frames
    (`TransferMap._on_frame`: a time check and a `put_nowait`, nothing
    else); the map's picture thread labels, encodes and indexes them, and
    closes the file when `closing` is set (Finish, Abort, the stop, the
    model closing), then fills the trial's video columns and sets
    `closed`. The folder and the store are the trial's own, fixed at Arm,
    so a new session database never receives them."""

    def __init__(self, trial, folder, store):
        self.trial = trial
        self.trial_id = trial.id
        self.folder = folder
        self.store = store
        self.queue = queue.Queue(maxsize=VIDEO_QUEUE)
        self.next_due = 0.0
        self.frames = 0                # written
        self.dropped = 0               # offered when the queue was full
        self.recorder = None           # opened at the first frame (its size)
        self.index = None              # the open video_index.csv
        self.index_writer = None
        self.failed = None             # why the video stopped, once it has
        self.mark_t = None             # the Mark the saved Mark frame is of
        self.path = None
        self.lock = threading.Lock()   # the picture thread vs an inline close
        self.closing = threading.Event()
        self.closed = threading.Event()

    @property
    def index_path(self):
        return self.folder / "video_index.csv"


class TransferMap(Model):
    NAME = "Transfer Map"
    IDENTITY = None
    NEEDS_PORT = False
    NEEDS_GAMEPAD = False
    RESOURCES = ()

    #: Figure size (inches) and resolution: `plot_data`'s defaults.
    FIGURE_SIZE = plot_data.FIGURE_SIZE
    FIGURE_DPI = plot_data.FIGURE_DPI

    GATE_REASONS = {
        **Model.GATE_REASONS,
        "ready": "no trial is armed. Arm one first.",
        "armed": "a trial is armed. Finish or abort it first.",
    }

    PARAMS = {p.name: p for p in (
        Param("tip_id", "text", default="", label="Tip ID"),
        Param("tip_note", "text", default="", label="Tip note"),
        # Text, so blank means "no tilt" rather than a default of 0 degrees.
        Param("typed_tilt", "text", default="",
              label="Tilt without a rotator (deg)"),
        # Bench 2026-09-28: the probe's speed setting is one number for the
        # whole session; the trial's intended cut speed is typed, and the
        # cut's speed is measured from the Z trace at Finish.
        Param("typed_speed", "text", default="",
              label="Speed for this trial (steps/s)"),
        Param("note", "text", default="", label="Note"),
        Param("afm_trial_id", "int", default=0, minimum=0, label="Trial"),
        Param("width_um", "float", default=0.0, minimum=0, decimals=3,
              unit="um", label="Channel width"),
        Param("width_sigma_um", "float", default=0.0, minimum=0, decimals=3,
              unit="um", label="Width uncertainty"),
        Param("thickness_nm", "float", default=0.0, minimum=0, decimals=1,
              unit="nm", label="Sample thickness"),
        Param("thickness_sigma_nm", "float", default=0.0, minimum=0,
              decimals=1, unit="nm", label="Thickness uncertainty"),
        Param("trial_pick", "int", default=0, minimum=0,
              label="Trial to show (0 = latest)"),
        # Readouts: declared for their type and unit only.
        Param("tilt_now", "float", default=0.0, decimals=2, unit="deg",
              label="Tilt"),
        Param("speed_now", "int", default=0, unit="steps/s", label="Speed"),
        Param("red_now", "float", default=0.0, decimals=2, unit="%",
              label="Red"),
        Param("trial_count", "int", default=0, label="Trials"),
        # A3: the trial store the operator chooses.
        Param("store_path", "text", default="", label="Store file"),
        Param("store_dir", "text", default="", label="Folder for a new store"),
        Param("store_name", "text", default="transfer_map",
              label="New store name"),
    )}

    #: Where the operator's store choice is remembered: an object with
    #: `read(key)` / `write(key, value)` (`controller.user_config`, set by
    #: the composition root). None remembers nothing.
    choices = None

    def __init__(self, port=None, gamepad=None, sim=False, db_path=None):
        super().__init__()
        self.sim = sim
        self._lock = threading.Lock()
        path = Path(db_path) if db_path else self.default_db_path()
        if path is None:
            self._no_store()
        else:
            self._adopt(path)
        # Migration by choice: a store a previous build left inside the
        # install is offered in the path field, never opened for the operator.
        legacy = self.legacy_store_path()
        if legacy is not None and not self.store_path:
            self.store_path = str(legacy)
        self._trial = None
        #: The trial being armed (V8): subscribed to before a run starts, so
        #: the run's first row is its own; numbered and made `_trial` once
        #: its row is written, dropped if the Arm fails.
        self._arming = None
        self._persisting = []          # abort writers the stop started
        self._red = None
        self._red_name = None
        self._tilts = {}               # name -> model with position_deg
        self._probes = {}              # name -> model with position + mode
        self._figure_type = next(iter(FIGURES))
        self._definition = next(iter(analysis.FORCE_DEFINITIONS))
        self._band = plot_data.FORCE_BANDS[0]
        self._revision = 0
        self._figure_cache = None
        self._indices = {}             # trial id -> force indices
        #: The Red Percent run the map started by itself (M3), until a trial
        #: takes it over; the (tip, region) it started for, so a commit that
        #: changes neither never starts a run the operator ended; the last
        #: refusal, said once and shown on Next step.
        self._auto_run = None
        self._poll_key = None
        self._poll_refused = None
        #: Videos being written or closed, for the picture thread (a list
        #: replaced whole under the lock, read as a snapshot).
        self._recordings = []
        self._recordings_lock = threading.Lock()
        self._wake = threading.Event()    # a frame or a close is waiting
        self._encoder = None              # `TrialRecorder.probe()`, once read

    @classmethod
    def default_db_path(cls):
        """`STATION_MAP_DB` (the `--map-db` flag), else the store the
        operator chose and the choices file remembers, else None: no store
        until one is chosen (owner decision 4, 2026-09-30: no default)."""
        configured = os.environ.get("STATION_MAP_DB")
        if configured:
            return Path(configured).expanduser().resolve()
        chosen = cls.choices.read("map_store") if cls.choices is not None else None
        return Path(chosen) if chosen else None

    @staticmethod
    def install_root():
        """The station's own folder: no store may live under it."""
        return _install_root()

    @classmethod
    def legacy_store_path(cls):
        """`<install>/data/transfer_map.sqlite`, where builds before
        2026-09-30 kept the store, when one is there; else None."""
        left = cls.install_root() / "data" / "transfer_map.sqlite"
        return left if left.is_file() else None

    # -- the store (A3) ------------------------------------------------------
    def _adopt(self, path):
        self.db_path = Path(path)
        #: Pictures, exports and uploads all live beside the database, so a
        #: Web download is checked against the same folder (CON-5).
        self.output_root = self.db_path.parent
        self._store = TrialStore(self.db_path)
        self._store_chosen = True

    def _no_store(self):
        self.db_path = None
        self.output_root = None          # no download is served
        self._store = _NoStore()
        self._store_chosen = False

    @property
    def has_store(self):
        return self._store_chosen

    def _need_store(self):
        if not self._store_chosen:
            raise Refused(NO_STORE)

    #: What the Store line says while nothing is chosen.
    NOT_CHOSEN = ("Not chosen. Open an existing store, or make a new one in "
                  "a folder of your choice.")

    @classmethod
    def describe_store(cls, path):
        """The Store line for `path` (None: nothing chosen)."""
        if path is None:
            return cls.NOT_CHOSEN
        if os.environ.get("STATION_MAP_DB"):
            return f"{path} (set by STATION_MAP_DB / --map-db)"
        return str(path)

    @property
    def store_status(self):
        return self.describe_store(self.db_path if self._store_chosen else None)

    def _refuse_inside_install(self, path):
        if _inside(self.install_root(), path):
            raise Refused(f"{path}: {INSIDE_INSTALL}. Choose a folder outside "
                          f"{self.install_root()}.")

    def open_store(self):
        """Open store: the SQLite file typed in Store file becomes the
        trial store, and is remembered."""
        typed = (self.store_path or "").strip()
        if not typed:
            raise Refused("Type the path of an existing store under Store file.")
        path = Path(typed).expanduser().resolve()
        self._refuse_inside_install(path)
        if not path.is_file():
            raise Refused(f"{path}: no file there. Check the path, or press "
                          "New store to make one.")
        try:
            with open(path, "rb") as handle:
                magic = handle.read(len(_SQLITE_MAGIC))
        except OSError as exc:
            raise Refused(f"{path} could not be read ({exc}).")
        if magic != _SQLITE_MAGIC:
            raise Refused(f"{path} is not a Transfer Map store (not a "
                          "database file).")
        return self._choose(path, created=False)

    def new_store(self):
        """New store: `<folder>/<name>.sqlite`, created now with its schema
        (the folder too), and remembered. Never over an existing file."""
        folder = (self.store_dir or "").strip()
        name = (self.store_name or "").strip() or "transfer_map"
        if not folder:
            raise Refused("Type the folder for the new store under Folder for "
                          "a new store.")
        if any(sep in name for sep in ("/", "\\")) or name in (".", ".."):
            raise Refused("The store name is a file name, not a path.")
        if not name.endswith(".sqlite"):
            name += ".sqlite"
        path = (Path(folder).expanduser() / name).resolve()
        self._refuse_inside_install(path)
        if path.exists():
            raise Refused(f"{path} already exists. Press Open store to use it.")
        return self._choose(path, created=True)

    def _choose(self, path, created):
        if self.is_armed:
            raise Refused("A trial is armed. Finish or abort it before choosing "
                          "another store.")
        store = TrialStore(path)
        try:
            store.ensure()          # a new file's schema, an old one's upgrade
        except (OSError, sqlite3.Error) as exc:
            raise Refused(f"The store could not be {'created' if created else 'opened'} "
                          f"at {path} ({exc}).")
        self.adopt_store(path)
        if self.choices is not None:
            try:
                self.choices.write("map_store", str(path))
            except OSError as exc:
                events.warn("Store Not Remembered", f"Trials go to {path}, but "
                            f"the choice could not be saved ({exc}); the station "
                            "will ask again next time.", source=self.NAME)
        events.info("Trial Store", f"Trials go to {path}: {store.count()} "
                    "trial(s).", source=self.NAME)
        return str(path)

    def adopt_store(self, path):
        """Use `path` from now on (Setup's Open/New store tells an open map
        through this). The previous store, if any, stays on disk untouched."""
        for writer in list(self._persisting):      # an abort still being written
            writer.join(self.THREAD_JOIN_TIMEOUT)
        self._adopt(Path(path))
        self._indices = {}
        self._figure_cache = None
        self._changed()
        return str(path)

    # -- the Model contract ------------------------------------------------
    @property
    def devices(self):
        return []

    def _expects_heartbeat(self):
        return False                   # no loop of its own

    def open(self):
        """Open, then make the database ready and say where it is (bench
        2026-09-27: "no prompt to create the db on startup"). Construction
        still creates nothing; a store that cannot be created is an error
        the operator sees, never a model that fails to open."""
        super().open()
        self._announce_store()

    def _announce_store(self):
        if not self._store_chosen:
            legacy = self.legacy_store_path()
            events.warn("Trial Store Not Chosen", "Choose where the Transfer "
                        "Map keeps its trials: Transfer Map, Store - Open store "
                        "for an existing file, or New store in a folder of your "
                        "choice." + (f" A store from an earlier version is at "
                                     f"{legacy}; move it out of the station's "
                                     "folder and open it there to keep its "
                                     "trials." if legacy else ""),
                        source=self.NAME)
            return False
        try:
            self._store.ensure()
        except Exception as exc:
            events.error("Database Not Ready", f"The Transfer Map database "
                         f"could not be created at {self.db_path}. Check that "
                         "the folder exists and can be written, or start with "
                         "--map-db PATH.", source=self.NAME, exception=exc)
            return False
        events.info("Database Ready", f"{self.db_path}: "
                    f"{self._store.count()} trial(s)", source=self.NAME)
        return True

    @property
    def is_armed(self):
        return self._trial is not None

    @property
    def is_active(self):
        return self.is_armed

    @property
    def mode_name(self):
        return "armed" if self.is_armed else "ready"

    def _halt_hardware(self):
        """Disarm now; write the aborted trial on a worker. No I/O here and
        no lock wait without a timeout: the stop is never held by the disk."""
        trial = self._claim(timeout=0.05)
        self._end_auto_run()           # polling the map started, no trial yet
        if trial is not None:
            self._release_red()
            self._stop_recording(trial)    # an Event set: no join, no I/O
            self._end_own_run(trial)
            # The store the trial was armed in: a new session database made
            # while this is being written must not receive it.
            writer = threading.Thread(target=self._save_aborted,
                                      args=(trial, self._store),
                                      daemon=True, name="transfer-map-abort")
            self._persisting.append(writer)
            writer.start()
        return True

    def disable(self):
        """Nothing to de-energize; wait (bounded) for an abort being written,
        so a close does not end the process before the trial is saved."""
        for writer in list(self._persisting):
            writer.join(self.THREAD_JOIN_TIMEOUT)
        self._persisting = [w for w in self._persisting if w.is_alive()]

    # -- the live sources --------------------------------------------------
    def on_model_added(self, name, model):
        if callable(getattr(model, "subscribe", None)) and \
                callable(getattr(model, "grab_frame", None)):
            self._red, self._red_name = model, name
        if hasattr(model, "position_deg"):
            self._tilts[name] = model
        if hasattr(model, "position") and hasattr(model, "position_time") \
                and hasattr(model, "mode_name"):
            self._probes[name] = model

    def on_model_removed(self, name, model=None):
        if name == self._red_name:
            self._release_red()
            self._red, self._red_name = None, None
            self._auto_run = self._poll_key = self._poll_refused = None
        self._tilts.pop(name, None)
        self._probes.pop(name, None)

    def _end_own_run(self, trial):
        """End the Red Percent run this trial started, and no other: a run
        the operator started, or started again after this one ended, is
        left running. `end_run` latches and returns (no I/O, no join), so
        this is safe on the stop path; a missing or stopped Red Percent is
        nothing to do."""
        red = self._red
        if trial.run is None or red is None:
            return
        try:
            if getattr(red, "run_token", None) is trial.run:
                red.end_run()
        except Exception as exc:
            events.debug("End Run Failed", repr(exc), source=self.NAME)

    def _end_auto_run(self):
        """End the run the map started by itself (M3) if it is still the
        active one; a run the operator started, or a later one, is left
        alone. Latches and returns, like `_end_own_run`: safe on the stop."""
        token, self._auto_run = self._auto_run, None
        red = self._red
        if token is None or red is None:
            return
        if self._trial is None and self._arming is None:
            self._release_red()        # V8: the polling's subscription goes too
        try:
            if getattr(red, "run_token", None) is token:
                red.end_run()
        except Exception as exc:
            events.debug("End Run Failed", repr(exc), source=self.NAME)

    # -- M3: polling starts itself ----------------------------------------------
    def _commit(self):
        """An entry was committed (`Panel.set_value`), after its value was
        applied: the Tip ID may have made the sheet ready to poll."""
        self._start_polling()

    def _start_polling(self):
        """Once the capture region is set and a tip ID typed, start Red
        Percent's run so the sheet's Red moves and its baseline is fresh
        (the run takes its baseline from its own first frame). Only when
        Red Percent is open and not running, no trial is armed, the map is
        not stopped, and the (tip, region) differs from the last one it
        started for: a run the operator ended stays ended until they change
        the tip or the region, or press Arm. Never a stop, never raises: a
        refusal is one warning and the Next step line. True if it started."""
        red = self._red
        tip = (self.tip_id or "").strip()
        region = self.region
        if red is None or not region or not tip or self.is_armed \
                or self.is_estopped or getattr(red, "is_running", False) \
                or not callable(getattr(red, "start_run", None)):
            return False
        key = (tip, tuple(sorted(region.items())) if isinstance(region, dict)
               else region)
        if key == self._poll_key:
            return False
        try:
            # V8: subscribed before the run starts, so no row of it is
            # missed. No trial is armed yet, so `_on_sample` keeps nothing
            # until Arm takes the run over; a refusal lets go again.
            self._subscribe_red(red)
            red.start_run(confirmed=True)
        except (Refused, NeedsConfirm) as refusal:
            self._release_red()
            self._polling_refused(getattr(refusal, "reason", None)
                                  or getattr(refusal, "prompt", ""))
            return False
        except Exception as exc:
            self._release_red()
            events.debug("Polling Failed", repr(exc), source=self.NAME,
                         exception=exc)
            self._polling_refused("Red Percent could not start its run; the "
                                  "details are in the log file.")
            return False
        self._auto_run = getattr(red, "run_token", None)
        self._poll_key = key
        self._poll_refused = None
        events.info("Polling Started", f"Red Percent is polling the capture "
                    f"region for tip {tip}, from a fresh baseline. Frame the "
                    "sample, then press Arm trial.", source=self.NAME)
        self._touch()
        return True

    def _polling_refused(self, reason):
        reason = str(reason or "Red Percent refused to start.").strip()
        if reason != self._poll_refused:
            events.warn("Polling Not Started", f"Red Percent did not start "
                        f"polling: {reason} Arm trial tries again.",
                        source=self.NAME)
        self._poll_refused = reason
        self._touch()

    def _release_red(self):
        red = self._red
        if red is not None:
            try:
                red.unsubscribe(self._on_sample)
                unsubscribe_frames = getattr(red, "unsubscribe_frames", None)
                if callable(unsubscribe_frames):
                    unsubscribe_frames(self._on_frame)
            except Exception as exc:
                events.debug("Unsubscribe Failed", repr(exc), source=self.NAME)

    def _read_tilt(self):
        """(degrees, source name) or (None, None)."""
        for name, model in self._tilts.items():
            try:
                value = model.position_deg
            except Exception:
                continue
            if value is not None:
                return float(value), name
        typed = _number(self.typed_tilt)
        if typed is not None:
            return typed, "typed"
        return None, None

    def _probe(self):
        """The probe Red Percent follows, else the first one in a mode."""
        followed = getattr(self._red, "source_name", None)
        if followed in self._probes:
            return followed, self._probes[followed]
        for name, model in self._probes.items():
            if self._speed_of(model) is not None:
                return name, model
        return None, None

    @staticmethod
    def _speed_of(model):
        live = getattr(model, "live_speed", None)
        if live is not None:
            return float(live)
        mode = getattr(model, "mode_name", None)
        field = {"manual": "man_full_speed", "autonomous": "full_speed"}.get(mode)
        value = getattr(model, field, None) if field else None
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    def _read_speed(self):
        """(steps/s, source): the typed speed for this trial wins over the
        probe's live or configured speed."""
        typed = _number(self.typed_speed)
        if typed is not None:
            return typed, "typed"
        name, model = self._probe()
        speed = self._speed_of(model) if model is not None else None
        return speed, (name if speed is not None else None)

    def _probe_axes(self):
        _name, model = self._probe()
        try:
            position = model.position if model is not None else None
            return tuple(float(v) for v in position[:3])
        except Exception:
            return (None, None, None)

    @property
    def tilt_now(self):
        return self._read_tilt()[0]

    @property
    def speed_now(self):
        speed = self._read_speed()[0]
        return None if speed is None else int(round(speed))

    @property
    def red_now(self):
        return getattr(self._red, "current_red", None)

    # -- Red Percent's controls, forwarded (T1): the trial sheet is the one
    # page of a trial; Red Percent keeps them, this only reaches them.
    @property
    def region(self):
        """Red Percent's capture region, or None."""
        red = self._red
        return getattr(red, "region", None) if red is not None else None

    @property
    def has_region(self):
        return bool(self.region)

    def set_region(self, x, y, width, height):
        red = self._red
        if red is None:
            raise Refused("Open Red Percent first: the capture region is the "
                          "part of the screen it measures.")
        if self.is_armed:
            raise Refused("The capture region is fixed while a trial is armed. "
                          "Finish or abort the trial to change it.")
        region = red.set_region(x, y, width, height)
        self._touch()
        self._start_polling()
        return region

    @property
    def screen_image(self):
        """Red Percent's desktop picture for the region picker, or None."""
        red = self._red
        return getattr(red, "screen_image", None) if red is not None else None

    @property
    def next_step(self):
        """The one thing to do next on the trial sheet; "" while latched
        (the stop says what to do then)."""
        if self.gate_mode == "latched":
            return ""
        red = self._red
        if red is None:
            return "Open Red Percent"
        tip = (self.tip_id or "").strip()
        running = bool(getattr(red, "is_running", False))
        if not self.has_region:
            if not tip and not running:
                return "Set the capture region and a tip ID"
            return "Set the capture region"
        trial = self._trial
        if trial is not None:
            if trial.operator_t is None:
                return "Lower the tip; press Mark force when the force is right"
            return "Press Finish trial"
        if not tip:
            return "Type a tip ID"
        if self._read_tilt()[0] is None:
            return "Type the tilt for this trial"
        if self._poll_refused and not running:
            reason = self._poll_refused.rstrip(".")
            return f"Polling did not start: {reason}. Fix that, then press Arm trial"
        return "Press Arm trial"

    @property
    def state(self):
        snapshot = super().state
        snapshot["has_region"] = self.has_region
        snapshot["store"] = {"path": str(self.db_path) if self._store_chosen else None,
                             "chosen": self._store_chosen}
        return snapshot

    # -- the samples, on Red Percent's run thread ------------------------------
    def _on_sample(self, t_s, red, positions):
        """One row of Red Percent's log. Appends and returns; never raises
        into the run loop (Red Percent catches it anyway)."""
        trial = self._trial or self._arming
        if trial is None or trial.closed:
            return
        if len(trial.samples) >= MAX_SAMPLES:
            trial.dropped += 1
            return
        x, y, z = positions.get("X"), positions.get("Y"), positions.get("Z")
        if x is None or y is None or z is None:
            px, py, pz = self._probe_axes()
            x = px if x is None else x
            y = py if y is None else y
            z = pz if z is None else z
        trial.samples.append((time.monotonic() - trial.armed, red, z, x, y))

    # -- the guided trial --------------------------------------------------
    def arm_trial(self, confirmed=False):
        """Arm a trial. Starts Red Percent's run when none is running (T2: a
        trial is a red-only run by definition, so the start's doubts are
        accepted here) and remembers that it did, so Finish, Abort and the
        stop end that run and no other."""
        self._need_store()
        self._guard("Arm")
        if self.is_armed:
            raise Refused("A trial is already armed. Finish or abort it first.")
        red = self._red
        if red is None:
            raise Refused("Open Red Percent first: a trial records its red percent.")
        tip = (self.tip_id or "").strip()
        if (self.typed_tilt or "").strip() and _number(self.typed_tilt) is None:
            raise Refused("Tilt without a rotator must be a number of degrees, "
                          "or left blank.")
        if not tip:
            raise Refused("Type a tip ID before arming, so the trial can be "
                          "traced to its tip.")
        if not getattr(red, "region", None):
            raise Refused("Set the capture region first: the trial's pictures "
                          "and its red percent are read from it.")
        if not confirmed:
            # T3: the before picture is taken on the operator's word, with
            # the sample framed; nothing is started or written until then.
            # A broken or retired tip is asked in the same prompt (M2): one
            # question, one Continue.
            tilt_now, tilt_from = self._read_tilt()
            tilt_words = (f" at {tilt_now:g} deg" + (f" ({tilt_from})" if tilt_from != "typed" else "")
                          if tilt_now is not None else ", with NO tilt recorded")
            speed_now, speed_from = self._read_speed()
            speed_words = (f", {speed_now:g} steps/s"
                           + ("" if speed_from == "typed" else f" ({speed_from})")
                           if speed_now is not None else ", NO speed")
            prompt = (f"Frame the sample now. Continue takes the whole-screen "
                      f"picture, starts the video and arms trial "
                      f"{self._store.next_id()} on tip {tip}"
                      f"{tilt_words}{speed_words}.")
            doubt = self._tip_doubt(tip)
            raise NeedsConfirm(doubt + "\n\n" + prompt if doubt else prompt,
                               "arm_trial",
                               inputs={"tip_id": self.tip_id or "",
                                       "typed_tilt": self.typed_tilt or ""})
        # V8: the trial exists (unnumbered) and the map is subscribed, rows
        # and frames, BEFORE a run is started, so the run's first row (its
        # baseline frame) and first frame are the trial's. Its time zero is
        # the operator's Continue.
        arming = _Trial(None, None, None, tip)
        if callable(getattr(red, "subscribe_frames", None)):
            arming.recording = _Recording(arming, None, None)
        self._arming = arming
        run, started = None, False
        trial_id = None
        created = False
        try:
            self._subscribe_red(red)
            if not getattr(red, "is_running", False):
                red.start_run(confirmed=True)           # a Refused stops here
                run, started = red.run_token, True
            elif self._auto_run is not None and \
                    getattr(red, "run_token", None) is self._auto_run:
                run = self._auto_run      # the map's own polling: the trial's now
            # The capture gate: no picture of the region, no Arm (the video
            # and the red percent are both read from it). Nothing is kept.
            if not self._take_picture():
                raise Refused("No picture of the capture region: the capture "
                              "region is not set or the screen is not open.")
            full = self._take_full_picture()
            # The whole-screen grab takes a moment; a stop inside it wins.
            self._guard("Arm")
            tilt, tilt_source = self._read_tilt()
            speed, speed_source = self._read_speed()
            trial_id = self._store.insert({
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "tip_id": tip,
                "tilt_deg": tilt, "speed_steps_s": speed, "status": "armed",
                "origin": "recorded", "tilt_source": tilt_source,
                "speed_source": speed_source, "note": ""})
            if full:
                self._store.update(trial_id, {"before_full_path": self._write_picture(
                    trial_id, "before_full", full)})
            created = self._store.use_tip(tip, trial_id, _now())
        except BaseException:
            self._arming = None
            self._release_red()
            if started:
                red.end_run()                            # ours: undo it
            if trial_id is not None:
                self._forget_row(trial_id)
            raise
        trial = arming
        trial.id, trial.tilt, trial.speed = trial_id, tilt, speed
        trial.run = run
        if run is not None and not started:
            self._auto_run = None                        # the trial ends it
        self._poll_refused = None
        rec = trial.recording
        if rec is not None:
            rec.trial_id = trial_id
            rec.folder = self.pictures_root / str(trial_id)
            rec.store = self._store
            with self._recordings_lock:
                self._recordings = self._recordings + [rec]
        with self._lock:
            self._trial = trial
            self._arming = None
        if rec is not None:
            self._spawn("transfer-map-pictures", self._picture_loop)
            self._wake.set()
        self._changed()
        if created:
            events.info("Tip Created", f"Tip {tip} created.", source=self.NAME)
        if not full:
            self._warn_no_full(trial_id, "Arm", "context")
        events.info("Trial Armed", f"Trial {trial_id} armed, "
                    f"{self._place_on_tip(trial)}. Lower the tip, press Mark "
                    "force at the force you want, then Finish.",
                    source=self.NAME)
        return trial_id

    def _subscribe_red(self, red):
        """Rows for the profile and, when Red Percent has the hook, frames
        for the video. Idempotent (Red Percent keeps each callable once)."""
        red.subscribe(self._on_sample)
        subscribe_frames = getattr(red, "subscribe_frames", None)
        if callable(subscribe_frames):
            subscribe_frames(self._on_frame)

    def mark_force(self):
        trial = self._trial
        if trial is None:
            raise Refused("No trial is armed.")
        trial.operator_t = time.monotonic() - trial.armed
        if trial.first_mark_t is None:
            trial.first_mark_t = trial.operator_t
        trial.z_mark = self._probe_axes()[2]
        speed = self._read_speed()[0]
        if speed is not None:
            trial.speed = speed      # the speed while lowering, at the Mark
        # V4: no still of its own. The video says MARK from here on, and its
        # first frame at or after this Mark is the sheet's Mark frame: the
        # next frame Red Percent grabs is taken at once, not up to 1/VIDEO_FPS
        # later.
        if trial.recording is not None:
            trial.recording.next_due = 0.0
        self._wake.set()
        events.info("Force Marked", f"Trial {trial.id}: force marked at "
                    f"{trial.operator_t:.2f} s.", source=self.NAME)
        self._touch()
        return round(trial.operator_t, 3)

    # -- the video, on the picture thread (V3, V5) -------------------------------
    def _on_frame(self, t_s, frame, red):
        """One frame Red Percent grabbed and measured, on its run thread:
        a time check and a `put_nowait`, nothing else (no copy, no convert,
        no encode). At most VIDEO_FPS a second are taken; one offered when
        the queue is full is dropped and counted."""
        trial = self._trial or self._arming
        rec = trial.recording if trial is not None else None
        if rec is None or trial.closed or rec.closing.is_set():
            return
        t = time.monotonic() - trial.armed
        if t < rec.next_due:
            return
        period = 1.0 / VIDEO_FPS
        rec.next_due = rec.next_due + period if t - rec.next_due < period else t + period
        try:
            rec.queue.put_nowait((t, frame, red))
        except queue.Full:
            rec.dropped += 1
            return
        self._wake.set()

    def _picture_loop(self):
        """The map's one picture thread: writes each recording's frames as
        they come, closes a recording once it is closing and drained, and
        leaves when the model closes, closing whatever is still open first
        (a stop's abort writer then finds its video closed)."""
        while True:
            stopping = self._threads_stop.is_set()
            busy = False
            for rec in list(self._recordings):
                if stopping:
                    rec.closing.set()
                try:
                    item = rec.queue.get_nowait()
                except queue.Empty:
                    item = None
                if item is not None:
                    self._write_frame(rec, item)
                    busy = True
                elif rec.closing.is_set():
                    self._close_recording(rec)
            if not busy:
                if stopping and not self._recordings:
                    return
                self._wake.wait(0.05)
                self._wake.clear()

    def _label(self, trial, t, red):
        """ `t=12.34 s  red 63.2 %  z -1520  MARK`: seconds since Arm, the
        frame's red, Z from the latest profile row (else the probe now),
        MARK from the first Mark on. -> (text, z, marked)."""
        samples = trial.samples
        z = samples[-1][2] if samples else self._probe_axes()[2]
        marked = trial.first_mark_t is not None and t >= trial.first_mark_t
        text = (f"t={t:.2f} s  red {red:.1f} %  z "
                + ("-" if z is None else f"{z:.0f}") + ("  MARK" if marked else ""))
        return text, z, marked

    def _write_frame(self, rec, item):
        t, frame, red = item
        with rec.lock:
            if rec.closed.is_set() or rec.failed:
                return
            try:
                rgb = to_rgb(frame)
                if rgb is None:
                    return
                if rec.recorder is None:
                    self._open_recording(rec, rgb)
                text, z, marked = self._label(rec.trial, t, float(red))
                labelled = rec.recorder.write(rgb, [text])
                rec.frames += 1
                rec.index_writer.writerow([rec.frames, round(t, 4),
                                           round(float(red), 4), z, int(marked)])
                mark = rec.trial.operator_t
                if rec.frames == 1:
                    self._save_still(rec, "first_frame", labelled)
                if mark is not None and t >= mark and rec.mark_t != mark:
                    rec.mark_t = mark
                    self._save_still(rec, "mark_frame", labelled)
                if rec.frames == 1 or rec.frames % VIDEO_FPS == 0:
                    self._touch()
            except Exception as exc:
                rec.failed = str(exc) or type(exc).__name__
                events.debug("Video Failed", repr(exc), source=self.NAME,
                             exception=exc)
                events.warn("Video Stopped", f"Trial {rec.trial_id}'s video "
                            f"stopped after {rec.frames} frame(s): {rec.failed}. "
                            "The trial goes on: its red-percent profile is the "
                            "measurement.", source=self.NAME)
                self._touch()

    def _open_recording(self, rec, rgb):
        rec.folder.mkdir(parents=True, exist_ok=True)
        height, width = rgb.shape[:2]
        rec.recorder = TrialRecorder().open(rec.folder / "trial.mp4", VIDEO_FPS,
                                            (width, height))
        rec.index = open(rec.index_path, "w", newline="")
        rec.index_writer = csv.writer(rec.index)
        rec.index_writer.writerow(VIDEO_INDEX_COLUMNS)
        if rec.recorder.kind == "jpeg":
            events.warn("No Video Encoder", f"Trial {rec.trial_id} is recorded "
                        f"as JPEG frames in {rec.recorder.path}: "
                        f"{rec.recorder.fallback_reason}. Install imageio-ffmpeg "
                        "for an MP4.", source=self.NAME)

    def _save_still(self, rec, name, labelled):
        from PIL import Image
        Image.fromarray(labelled, "RGB").save(rec.folder / f"{name}.png",
                                              format="PNG")

    def _close_recording(self, rec):
        """Finish the file, the index and the trial's video columns, once.
        On the picture thread; inline on a writer thread (never the stop's)
        only when no picture thread is left to do it."""
        with rec.lock:
            if rec.closed.is_set():
                return
            try:
                if rec.recorder is not None:
                    rec.path = rec.recorder.close()
            except Exception as exc:
                events.debug("Video Close Failed", repr(exc), source=self.NAME,
                             exception=exc)
                events.warn("Video Not Closed", f"Trial {rec.trial_id}'s video "
                            "did not close cleanly; its frames so far may not "
                            "play.", source=self.NAME)
                rec.path = getattr(rec.recorder, "path", None)
                rec.path = None if rec.path is None else str(rec.path)
            finally:
                if rec.index is not None:
                    try:
                        rec.index.close()
                    except OSError:
                        pass
            try:
                rec.store.update(rec.trial_id, {
                    "video_path": rec.path,
                    "video_index_path": (str(rec.index_path)
                                         if rec.index is not None else None),
                    "video_frames": rec.frames, "video_dropped": rec.dropped})
            except Exception as exc:
                events.debug("Video Columns Failed", repr(exc), source=self.NAME,
                             exception=exc)
            finally:
                rec.closed.set()
        with self._recordings_lock:
            self._recordings = [r for r in self._recordings if r is not rec]
        self._touch()

    def _stop_recording(self, trial):
        """Tell the trial's video to close: an Event set and a wake, safe on
        the stop's own thread (the picture thread does the closing)."""
        rec = trial.recording
        if rec is not None:
            rec.closing.set()
            self._wake.set()

    def _finish_recording(self, trial):
        """Close the trial's video and wait for it, bounded, so the row
        Finish or Abort writes sits beside a complete video; a close that
        hangs is logged and the trial is written anyway (the picture thread
        fills the video columns when it lands). Never on the stop's thread."""
        rec = trial.recording
        if rec is None:
            return
        self._stop_recording(trial)
        worker = self._thread("transfer-map-pictures")
        if worker is None or not worker.is_alive():
            self._close_recording(rec)
        elif not rec.closed.wait(self.THREAD_JOIN_TIMEOUT):
            events.debug("Video Late", f"trial {trial.id}: the video was not "
                         f"closed within {self.THREAD_JOIN_TIMEOUT} s",
                         source=self.NAME)

    def finish_trial(self, confirmed=False):
        armed = self._trial
        if armed is None:
            raise Refused("No trial is armed.")
        if not confirmed:
            raise NeedsConfirm(f"Continue ends trial {armed.id} and closes its "
                               "video.", "finish_trial",
                               inputs={"note": self.note or ""})
        trial = self._claim()
        if trial is None:
            raise Refused("No trial is armed.")          # the stop took it
        self._release_red()
        self._end_own_run(trial)
        samples = list(trial.samples)
        profile = {"t": [s[0] for s in samples], "red": [s[1] for s in samples]}
        found = analysis.detect(profile, trial.operator_t) or {}
        fields = {
            "status": "recorded", "note": (self.note or "").strip(),
            "speed_steps_s": trial.speed,
            "speed_measured_steps_s": analysis.cut_speed(
                [s[0] for s in samples], [s[2] for s in samples],
                trial.operator_t),
            "z_contact": trial.z_mark, "mark_operator_t": trial.operator_t,
            "mark_auto_max_t": found.get("max_t"),
            "mark_auto_min_t": found.get("min_t"),
            "red_min": found.get("red_min"), "red_max": found.get("red_max"),
            "red_baseline": found.get("baseline"), "broke": int(trial.broke)}
        self._finish_recording(trial)
        self._store.update(trial.id, fields, samples)
        self._store.use_tip(trial.tip, trial.id, _now())
        self._indices.pop(trial.id, None)
        self._changed()
        if not samples:
            events.warn("Empty Trial", f"Trial {trial.id} has no red-percent "
                        "samples. Check that Red Percent was recording while "
                        "you lowered the tip.", source=self.NAME)
        if trial.dropped:
            events.warn("Trial Too Long", f"Trial {trial.id} kept its first "
                        f"{MAX_SAMPLES} samples; {trial.dropped} more were not "
                        "stored.", source=self.NAME)
        events.info("Trial Recorded", f"Trial {trial.id} recorded, "
                    f"{self._place_on_tip(trial)}: {len(samples)} samples.",
                    source=self.NAME)
        self.note = ""
        return trial.id

    def abort_trial(self):
        trial = self._claim()
        if trial is None:
            raise Refused("No trial is armed.")
        self._release_red()
        self._stop_recording(trial)
        self._end_own_run(trial)
        self._save_aborted(trial)
        return trial.id

    def _place_on_tip(self, trial):
        """ "the 3rd on tip T7": this trial's place among its tip's trials."""
        n = self._store.count_for_tip(trial.tip, up_to=trial.id)
        return f"the {_ordinal(n)} on tip {trial.tip}"

    def _claim(self, timeout=-1):
        """Take the armed trial, once: Finish, Abort and the stop race for it
        and exactly one wins. The stop passes a timeout; if the lock is held
        past it, the stop takes the trial anyway (a stop never waits)."""
        if self._lock.acquire(timeout=timeout):
            try:
                trial, self._trial = self._trial, None
            finally:
                self._lock.release()
        else:
            trial, self._trial = self._trial, None
        if trial is not None:
            if trial.closed:
                return None          # already taken by the other side
            trial.closed = True
        return trial

    def _save_aborted(self, trial, store=None):
        store = store if store is not None else self._store
        try:
            self._finish_recording(trial)
            samples = list(trial.samples)
            store.update(trial.id, {
                "status": "aborted", "mark_operator_t": trial.operator_t,
                "z_contact": trial.z_mark, "broke": int(trial.broke)}, samples)
            self._indices.pop(trial.id, None)
            self._changed()
            events.info("Trial Aborted", f"Trial {trial.id} was aborted; its "
                        f"{len(samples)} samples are kept.", source=self.NAME)
        except Exception as exc:
            events.error("Trial Not Saved", f"Trial {trial.id} was aborted but "
                         "could not be saved. Check the database folder.",
                         source=self.NAME, exception=exc)

    def _take_picture(self):
        """Red Percent's capture region as it looks now, as PNG bytes, or
        None: Arm's capture gate (the video and the red percent are read
        from it). Nothing is kept."""
        red = self._red
        try:
            return (red.grab_frame() if red is not None else None) or None
        except Exception as exc:
            events.debug("Frame Failed", repr(exc), source=self.NAME)
            return None

    def _take_full_picture(self):
        """The whole screen at full size (the microscope feed as displayed),
        as PNG bytes, or None. The record, not the measurement: a missing
        one is a warning, never a refusal. `grab_screen` is optional on the
        Red Percent the map found."""
        grab = getattr(self._red, "grab_screen", None)
        try:
            return (grab() if callable(grab) else None) or None
        except Exception as exc:
            events.debug("Full Frame Failed", repr(exc), source=self.NAME)
            return None

    def _warn_no_full(self, trial_id, moment, which):
        """ "Trial 4 has no whole-screen picture at Arm; its video of the
        capture region is kept." """
        events.warn("No Full Picture", f"Trial {trial_id} has no whole-screen "
                    f"picture at {moment}; its video of the capture region is "
                    "kept. Check that Red Percent can capture the screen.",
                    source=self.NAME)

    def _write_picture(self, trial_id, which, png):
        folder = self.pictures_root / str(trial_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{which}.png"
        path.write_bytes(png)
        return str(path)

    def _forget_row(self, trial_id):
        """An Arm that failed after its row was written leaves no row."""
        try:
            self._store.delete(trial_id)
        except Exception as exc:
            events.debug("Forget Failed", repr(exc), source=self.NAME)

    def _picture(self, which, column=True):
        """PNG bytes of the armed trial's picture, else the last trial's,
        else b"" (the element then says when it is taken). `column=False`:
        a still of the video, found in the trial's folder by name."""
        trial = self._trial
        if trial is not None:
            path = self.pictures_root / str(trial.id) / f"{which}.png"
        else:
            last = self._store.last()
            if last is None:
                path = None
            elif column:
                stored = last.get(f"{which}_path")
                path = Path(stored) if stored else None
            else:
                path = self.pictures_root / str(last["id"]) / f"{which}.png"
        try:
            return path.read_bytes() if path is not None and path.is_file() else b""
        except OSError:
            return b""

    @property
    def before_full_image(self):
        return self._picture("before_full")

    @property
    def first_frame_image(self):
        """The video's first frame, labelled."""
        return self._picture("first_frame", column=False)

    @property
    def mark_frame_image(self):
        """The video's first frame at or after the last Mark, labelled MARK."""
        return self._picture("mark_frame", column=False)

    @property
    def video_status(self):
        """ "recording, 312 frames" / "trial.mp4, 1240 frames, 3 dropped" /
        "no encoder: JPEG frames, 1240 frames, 0 dropped"."""
        trial = self._trial
        if trial is not None:
            rec = trial.recording
            if rec is None:
                return "no video: Red Percent has no frame hook"
            if rec.failed:
                return f"stopped after {rec.frames} frames: {rec.failed}"
            words = f"recording, {rec.frames} frames"
            if rec.recorder is not None and rec.recorder.kind == "jpeg":
                words += " (no encoder: JPEG frames)"
            return words + (f", {rec.dropped} dropped" if rec.dropped else "")
        last = self._store.last()
        if last is None:
            return "No video yet."
        if last.get("video_frames") is None:
            return "No video for this trial."
        path = last.get("video_path")
        frames, dropped = last["video_frames"], last.get("video_dropped") or 0
        if not path:
            return "no frames were recorded"
        name = Path(path).name
        where = "no encoder: JPEG frames" if name == "frames" else name
        return f"{where}, {frames} frames, {dropped} dropped"

    @property
    def video_encoder(self):
        """Which way this station records a trial's video (Diagnostics)."""
        if self._encoder is None:
            self._encoder = TrialRecorder.probe()
        return self._encoder["detail"]

    # -- after the trial ---------------------------------------------------
    @property
    def is_broke(self):
        trial = self._trial
        if trial is not None:
            return trial.broke
        last = self._store.last()
        return bool(last and last.get("broke"))

    def mark_broke(self, broke=True):
        """The tip broke (or did not), on the armed trial, else the last one."""
        flag = bool(broke) and str(broke).lower() not in ("false", "0")
        trial = self._trial
        if trial is not None:
            trial.broke = flag
            if trial.tip:
                self._store.set_tip_broke(trial.tip, trial.id, flag)
            self._changed()
            return flag
        last = self._store.last()
        if last is None:
            raise Refused("There is no trial to mark yet.")
        self._store.update(last["id"], {"broke": int(flag)})
        if (last.get("tip_id") or "").strip():
            self._store.set_tip_broke(last["tip_id"], last["id"], flag)
        self._changed()
        return flag

    # -- M2: tips as records ---------------------------------------------------
    def _tip_record(self, tip):
        return self._store.tip(tip) if tip else None

    def _tip_doubt(self, tip):
        """The question Arm adds for a broken or retired tip, else ""."""
        record = self._tip_record(tip)
        if record is None:
            return ""
        broke, retired = record["broke_trial_id"], record["retired_at"]
        if broke is not None and retired:
            return (f"Tip {tip} broke on trial {broke} and is retired. Arm on "
                    "it anyway?")
        if broke is not None:
            return f"Tip {tip} broke on trial {broke}. Arm on it anyway?"
        if retired:
            return f"Tip {tip} is retired. Arm on it anyway?"
        return ""

    @property
    def tip_status(self):
        """The typed tip, in a word or four: "new", "in use since trial 3",
        "broke on trial 12", "retired". None (blank) while the entry is."""
        tip = (self.tip_id or "").strip()
        if not tip:
            return None
        record = self._tip_record(tip)
        if record is None:
            return "new"
        broke, retired = record["broke_trial_id"], record["retired_at"]
        if broke is not None:
            return f"broke on trial {broke}" + (", retired" if retired else "")
        if retired:
            return "retired"
        if record["trials"]:
            return f"in use since trial {record['trials'][0]}"
        return "new"

    #: The dropdown's blank line: the typed tip is not a known one.
    NO_TIP = "-"

    @property
    def tip_options(self):
        """Every tip on record, for the Known tips dropdown (bench
        2026-09-28: the operator could not see the tips that existed)."""
        return [self.NO_TIP] + [t["tip_id"] for t in self._store.tips()]

    @property
    def tip_pick(self):
        tip = (self.tip_id or "").strip()
        return tip if tip and tip in self.tip_options else self.NO_TIP

    def pick_tip(self, label):
        """The Known tips dropdown: fills the Tip ID entry with a tip on
        record. The blank line changes nothing."""
        if label == self.NO_TIP:
            return None
        if label not in self.tip_options:
            raise Refused(f"{label!r} is not a tip on record. Type a new ID "
                          "in Tip ID and press New tip.")
        self.tip_id = label
        self._changed()
        self._start_polling()
        return label

    def new_tip(self):
        """A tip record made on demand from the typed ID, before any trial
        (bench 2026-09-28: "I can't create new tips")."""
        self._need_store()
        tip = (self.tip_id or "").strip()
        if not tip:
            raise Refused("Type the new tip's ID in Tip ID first, then press "
                          "New tip.")
        if self._store.tip(tip) is not None:
            raise Refused(f"Tip {tip} is already on record: pick it under "
                          "Known tips.")
        self._store.create_tip(tip, _now())
        self._changed()
        events.info("Tip Created", f"Tip {tip} created.", source=self.NAME)
        self._start_polling()
        return tip

    def _typed_tip(self):
        """(tip, record) for the typed tip, or Refused naming what is missing."""
        tip = (self.tip_id or "").strip()
        if not tip:
            raise Refused("Type the tip ID first.")
        record = self._tip_record(tip)
        if record is None:
            raise Refused(f"Tip {tip} has no record yet: it is created when a "
                          "trial is armed on it.")
        return tip, record

    def retire_tip(self, confirmed=False):
        self._need_store()
        tip, record = self._typed_tip()
        if record["retired_at"]:
            raise Refused(f"Tip {tip} is already retired.")
        if self._trial is not None and self._trial.tip == tip:
            raise Refused(f"A trial is armed on tip {tip}. Finish or abort it "
                          "first.")
        if not confirmed:
            raise NeedsConfirm(f"Retire tip {tip}? Its {record['count']} "
                               "trial(s) are kept; arming on it later asks "
                               "first.", "retire_tip",
                               inputs={"tip_id": self.tip_id or ""})
        self._store.set_tip(tip, {"retired_at": _now()})
        self._changed()
        events.info("Tip Retired", f"Tip {tip} retired after "
                    f"{record['count']} trial(s).", source=self.NAME)
        return tip

    def unretire_tip(self):
        self._need_store()
        tip, record = self._typed_tip()
        if not record["retired_at"]:
            raise Refused(f"Tip {tip} is not retired.")
        self._store.set_tip(tip, {"retired_at": None})
        self._changed()
        events.info("Tip In Use", f"Tip {tip} is back in use.", source=self.NAME)
        return tip

    def set_tip_note(self):
        self._need_store()
        tip = (self.tip_id or "").strip()
        if not tip:
            raise Refused("Type the tip ID first.")
        if self._store.tip(tip) is None:
            # A note before the first trial creates the record (owner call
            # 2026-09-28).
            self._store.create_tip(tip, _now())
            events.info("Tip Created", f"Tip {tip} created.", source=self.NAME)
        note = (self.tip_note or "").strip()
        self._store.set_tip(tip, {"note": note})
        self._changed()
        events.info("Tip Note Saved", f"Tip {tip}: "
                    f"{note or 'note cleared'}.", source=self.NAME)
        return tip

    @property
    def tips_log(self):
        """One line per tip: its trials, first and last, and how it ended."""
        lines = []
        for tip in self._store.tips():
            ids = tip["trials"]
            span = (f"trials {ids[0]}-{ids[-1]}" if len(ids) > 1
                    else f"trial {ids[0]}" if ids else "no trials")
            end = ""
            if tip["broke_trial_id"] is not None:
                end += f"  broke on trial {tip['broke_trial_id']}"
            if tip["retired_at"]:
                end += "  retired"
            note = f"  {tip['note']}" if tip["note"] else ""
            lines.append(f"{tip['tip_id']}  {tip['count']} trial(s), "
                         f"{span}{end}{note}")
        return lines

    def set_trial_tilt(self):
        """Correct a recorded trial's tilt from the sheet (bench 2026-09-28:
        two trials were armed before the tilt was asked for)."""
        self._need_store()
        trial_id = int(self.afm_trial_id or 0)
        if trial_id <= 0:
            raise Refused("Type the trial number under AFM measurement, Trial.")
        row = self._store.trial(trial_id)
        if row is None:
            raise Refused(f"No trial {trial_id} in the database.")
        if row["status"] == "armed":
            raise Refused(f"Trial {trial_id} is still armed. Finish it first.")
        tilt = _number(self.typed_tilt)
        if tilt is None:
            raise Refused("Type the tilt in degrees under Tilt for this trial.")
        self._store.update(trial_id, {"tilt_deg": float(tilt),
                                      "tilt_source": "typed later"})
        self._indices.pop(trial_id, None)
        self._changed()
        events.info("Tilt Set", f"Trial {trial_id}: tilt set to {tilt:g} deg.",
                    source=self.NAME)
        return trial_id

    def set_trial_speed(self):
        """Correct a recorded trial's intended speed from the sheet."""
        self._need_store()
        trial_id = int(self.afm_trial_id or 0)
        if trial_id <= 0:
            raise Refused("Type the trial number under AFM measurement, Trial.")
        row = self._store.trial(trial_id)
        if row is None:
            raise Refused(f"No trial {trial_id} in the database.")
        if row["status"] == "armed":
            raise Refused(f"Trial {trial_id} is still armed. Finish it first.")
        speed = _number(self.typed_speed)
        if speed is None:
            raise Refused("Type the speed in steps/s under Speed for this trial.")
        self._store.update(trial_id, {"speed_steps_s": float(speed),
                                      "speed_source": "typed later"})
        self._indices.pop(trial_id, None)
        self._changed()
        events.info("Speed Set", f"Trial {trial_id}: speed set to {speed:g} "
                    "steps/s.", source=self.NAME)
        return trial_id

    def attach_afm(self):
        self._need_store()
        trial_id = int(self.afm_trial_id or 0)
        if trial_id <= 0:
            raise Refused("Type the trial number the AFM measurement belongs to.")
        row = self._store.trial(trial_id)
        if row is None:
            raise Refused(f"No trial {trial_id} in the database.")
        if row["status"] == "armed":
            raise Refused(f"Trial {trial_id} is still armed. Finish it first.")
        if not self.width_um or self.width_um <= 0:
            raise Refused("Type the channel width measured by AFM.")
        given = lambda value: float(value) if value else None   # noqa: E731
        self._store.update(trial_id, {
            "width_um": float(self.width_um),
            "width_sigma_um": given(self.width_sigma_um),
            "thickness_nm": given(self.thickness_nm),
            "thickness_sigma_nm": given(self.thickness_sigma_nm),
            "status": "aborted" if row["status"] == "aborted" else "measured"})
        self._changed()
        events.info("AFM Attached", f"Trial {trial_id}: width "
                    f"{self.width_um:g} um attached.", source=self.NAME)
        return trial_id

    def delete_trial(self, confirmed=False):
        self._need_store()
        trial_id = self._picked_id()
        if trial_id is None:
            raise Refused("There is no trial to delete.")
        if self._trial is not None and self._trial.id == trial_id:
            raise Refused(f"Trial {trial_id} is armed. Abort it first.")
        if not confirmed:
            raise NeedsConfirm(f"Delete trial {trial_id} and its profile?\n\n"
                               "This cannot be undone; export first to keep a "
                               "copy.", "delete_trial",
                               inputs={"trial_pick": str(self.trial_pick or 0)})
        self._store.delete(trial_id)
        self._indices.pop(trial_id, None)
        self._changed()
        events.info("Trial Deleted", f"Trial {trial_id} was deleted.",
                    source=self.NAME)
        return trial_id

    def _picked_id(self):
        pick = int(self.trial_pick or 0)
        if pick > 0:
            return pick if self._store.trial(pick) else None
        last = self._store.last()
        return last["id"] if last else None

    # -- the session database ----------------------------------------------
    def new_database(self):
        """A new database beside this one, for a new session. The current
        file stays on disk untouched; pictures and exports stay in the same
        folder (`output_root`), the pictures under the new file's own name
        so trial 1 of the new database never overwrites trial 1 of the old."""
        self._need_store()
        if self.is_armed:
            raise Refused("A trial is armed. Finish or abort it before starting "
                          "a new database.")
        for writer in list(self._persisting):      # an abort still being written
            writer.join(self.THREAD_JOIN_TIMEOUT)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = self.output_root / f"transfer_map_{stamp}.sqlite"
        suffix = 2
        while path.exists():
            path = self.output_root / f"transfer_map_{stamp}_{suffix}.sqlite"
            suffix += 1
        store = TrialStore(path)
        store.ensure()
        previous = self.db_path
        self.db_path, self._store = path, store
        self._indices = {}
        self._figure_cache = None
        self._changed()
        events.info("New Database", f"Trials now go to {path}. The previous "
                    f"database stays at {previous}.", source=self.NAME)
        return str(path)

    @property
    def pictures_root(self):
        """`<output_root>/<database name>/`: `transfer_map/` for the default
        file, so one folder of pictures per database. None without a store."""
        if self.db_path is None:
            return None
        return self.output_root / self.db_path.stem

    # -- export and import -------------------------------------------------
    def _export(self):
        self._need_store()
        rows = self._store.trials()
        if not rows:
            raise Refused("No trials yet; there is nothing to export.")
        folder = self.output_root / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        trials_path = folder / f"transfer_map_{stamp}_trials.csv"
        profile_path = folder / f"transfer_map_{stamp}_profile.csv"
        tips_path = folder / f"transfer_map_{stamp}_tips.csv"
        names = list(analysis.FORCE_DEFINITIONS)
        names += [n for n in self._store.given_definitions() if n not in names]
        columns = [name for name, _kind in TRIAL_COLUMNS]
        with open(trials_path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns + ["force_" + n for n in names])
            for row in rows:
                force = self._force_of(row)
                writer.writerow([row.get(c) for c in columns]
                                + [force.get(n) for n in names])
        with open(profile_path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(PROFILE_COLUMNS)
            for row in self._store.profile_rows():
                writer.writerow([row[c] for c in PROFILE_COLUMNS])
        tip_columns = [name for name, _kind in TIP_COLUMNS]
        with open(tips_path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(tip_columns + ["trial_count", "trial_ids"])
            for tip in self._store.tips():
                writer.writerow([tip.get(c) for c in tip_columns]
                                + [tip["count"], " ".join(map(str, tip["trials"]))])
        events.info("Map Exported", f"{len(rows)} trial(s) written to {folder}",
                    source=self.NAME)
        return str(trials_path), str(profile_path), str(tips_path)

    def export_csv(self):
        """Both tables under `output_root/exports/`; the trials file's path
        (with every force index as a column) is what a view downloads."""
        return self._export()[0]

    def export_profile_csv(self):
        """The same export; the profile file's path."""
        return self._export()[1]

    def export_tips_csv(self):
        """The same export; the tips file's path (one row per tip record,
        with its trial count and trial ids)."""
        return self._export()[2]

    def import_csv(self, path):
        """Trials measured elsewhere: tilt and speed, and the force index
        given directly (`force_index`, optionally named by
        `force_definition`; or `force_<name>` columns, as an export writes).
        No profile. Rows without a tilt or a speed are skipped."""
        self._need_store()
        try:
            with open(path, newline="") as handle:
                rows = list(csv.DictReader(handle))
        except OSError:
            raise Refused(f"Could not read {Path(path).name}.")
        if not rows or not ({"tilt_deg", "tilt"} & set(rows[0])) or \
                not ({"speed_steps_s", "speed"} & set(rows[0])):
            raise Refused(f"{Path(path).name} needs a tilt_deg and a "
                          "speed_steps_s column.")
        reserved = {"force_given", "force_index", "force_definition"}
        imported = skipped = 0
        new_tips = []
        for row in rows:
            tilt = _number(row.get("tilt_deg", row.get("tilt")))
            speed = _number(row.get("speed_steps_s", row.get("speed")))
            if tilt is None or speed is None:
                skipped += 1
                continue
            given = {key[len("force_"):]: _number(value)
                     for key, value in row.items()
                     if key and key.startswith("force_") and key not in reserved
                     and _number(value) is not None}
            if _number(row.get("force_index")) is not None:
                name = (row.get("force_definition") or "").strip() or "given"
                given[name] = _number(row["force_index"])
            width = _number(row.get("width_um"))
            tip = (row.get("tip_id") or "").strip()
            broke = str(row.get("broke") or "").strip().lower() in ("1", "yes", "true")
            trial_id = self._store.insert({
                "started_at": row.get("started_at") or None,
                "tip_id": tip,
                "tilt_deg": tilt, "speed_steps_s": speed, "width_um": width,
                "width_sigma_um": _number(row.get("width_sigma_um")),
                "thickness_nm": _number(row.get("thickness_nm")),
                "thickness_sigma_nm": _number(row.get("thickness_sigma_nm")),
                "broke": 1 if broke else 0,
                "note": row.get("note") or "",
                "status": "measured" if width is not None else "recorded",
                "origin": "imported", "force_given": json.dumps(given)})
            if tip:
                # An imported trial may name a tip with no record: it gets one.
                if self._store.use_tip(tip, trial_id,
                                       row.get("started_at") or _now()):
                    new_tips.append(tip)
                if broke:
                    self._store.set_tip_broke(tip, trial_id, True)
            imported += 1
        self._changed()
        created = (f"; tip(s) created: {', '.join(new_tips)}" if new_tips else "")
        events.info("Map Imported", f"{imported} trial(s) imported from "
                    f"{Path(path).name}; {skipped} skipped{created}.",
                    source=self.NAME)
        return {"imported": imported, "skipped": skipped}

    # -- the map ------------------------------------------------------------
    def _force_of(self, row):
        """Every force index of one trial: computed from its profile, or the
        values an import gave."""
        cached = self._indices.get(row["id"])
        if cached is not None:
            return cached
        given = json.loads(row["force_given"]) if row.get("force_given") else {}
        if row.get("origin") == "imported":
            out = {name: None for name in analysis.FORCE_DEFINITIONS}
            out.update(given)
        else:
            profile = self._store.profile(row["id"])
            out = analysis.force_indices(profile, {
                "operator_t": row.get("mark_operator_t"),
                "auto_max_t": row.get("mark_auto_max_t"),
                "auto_min_t": row.get("mark_auto_min_t"),
                "baseline": row.get("red_baseline")})
        self._indices[row["id"]] = out
        return out

    def _map_rows(self):
        rows = []
        for row in self._store.trials():
            if row["status"] in ("armed", "aborted"):
                continue
            rows.append({"id": row["id"], "tilt": row["tilt_deg"],
                         "speed": row["speed_steps_s"],
                         "force": self._force_of(row),
                         "width": row["width_um"],
                         "width_sigma": row["width_sigma_um"]})
        return rows

    def _changed(self):
        self._revision += 1
        self._touch()

    @property
    def figure(self):
        key = (self._revision, self._figure_type, self._definition, self._band,
               self.trial_pick, self.FIGURE_SIZE, self.FIGURE_DPI)
        cached = self._figure_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        if not self._store.count():
            png = b""
        else:
            kind = FIGURES[self._figure_type]
            profile, marks = None, None
            if kind == "profile":
                profile, marks = self._profile_and_marks()
            png = plot_data.render_transfer_figure(
                kind, self._map_rows(), self._definition, band=self._band,
                profile=profile, marks=marks,
                definitions=self.force_definition_options,
                size=self.FIGURE_SIZE, dpi=self.FIGURE_DPI)
        self._figure_cache = (key, png)
        return png

    def _profile_and_marks(self):
        trial_id = self._picked_id()
        row = self._store.trial(trial_id) if trial_id else None
        if row is None:
            return None, {}
        profile = self._store.profile(trial_id)
        return profile, {"trial_id": trial_id,
                         "operator_t": row["mark_operator_t"],
                         "max_t": row["mark_auto_max_t"],
                         "min_t": row["mark_auto_min_t"],
                         "baseline": row["red_baseline"],
                         "red_max": row["red_max"], "red_min": row["red_min"]}

    # -- the dropdowns -------------------------------------------------------
    @property
    def figure_type(self):
        return self._figure_type

    @property
    def figure_type_options(self):
        return list(FIGURES)

    def set_figure_type(self, label):
        if label not in FIGURES:
            raise Refused(f"{label!r} is not a figure this map draws.")
        self._figure_type = label
        self._touch()
        return label

    @property
    def force_definition(self):
        return self._definition

    @property
    def force_definition_options(self):
        names = list(analysis.FORCE_DEFINITIONS)
        return names + [n for n in self._store.given_definitions()
                        if n not in names]

    def set_force_definition(self, name):
        if name not in self.force_definition_options:
            raise Refused(f"{name!r} is not a force definition.")
        self._definition = name
        self._touch()
        return name

    @property
    def force_band(self):
        return self._band

    @property
    def force_band_options(self):
        return list(plot_data.FORCE_BANDS)

    def set_force_band(self, band):
        if band not in plot_data.FORCE_BANDS:
            raise Refused(f"{band!r} is not a force band.")
        self._band = band
        self._touch()
        return band

    # -- readouts ------------------------------------------------------------
    @property
    def trial_count(self):
        return self._store.count()

    @property
    def tip_trial_count(self):
        """Stored trials on the typed tip: None (shown blank) while the Tip
        ID entry is blank, 0 for a tip the database has not seen."""
        tip = (self.tip_id or "").strip()
        return self._store.count_for_tip(tip) if tip else None

    @property
    def trial_status(self):
        trial = self._trial
        if trial is not None:
            marked = (f", force marked at {trial.operator_t:.1f} s"
                      if trial.operator_t is not None else "")
            return f"Trial {trial.id} armed: {len(trial.samples)} samples{marked}."
        last = self._store.last()
        if last is None:
            return "No trials yet."
        return f"Last: trial {last['id']}, {last['status']}."

    @property
    def live_series(self):
        trial = self._trial
        if trial is None:
            return {"x": [], "y": []}
        samples = list(trial.samples)
        return {"x": [s[0] for s in samples], "y": [s[1] for s in samples]}

    @property
    def trials_log(self):
        lines = []
        for row in self._store.trials():
            width = (f"{row['width_um']:g} um" if row["width_um"] is not None
                     else "no width")
            tilt = "?" if row["tilt_deg"] is None else f"{row['tilt_deg']:g} deg"
            speed = ("?" if row["speed_steps_s"] is None
                     else f"{row['speed_steps_s']:g} steps/s")
            measured = row["speed_measured_steps_s"]
            if measured is not None:
                speed += f" (cut measured {measured:g})"
            lines.append(f"{row['id']:>4}  {row['status']:<8} "
                         f"{row['tip_id'] or '-'}  {tilt}  {speed}  {width}"
                         f"{'  broke' if row['broke'] else ''}"
                         f"{'  ' + row['note'] if row['note'] else ''}")
        return lines

    @property
    def last_trial_numbers(self):
        """The detector's numbers and every force index for the last
        recorded trial, one line."""
        rows = [r for r in self._store.trials()
                if r["status"] in ("recorded", "measured")
                and r["origin"] == "recorded"]
        if not rows:
            return ""
        row = rows[-1]
        fmt = lambda v: "-" if v is None else f"{v:.3g}"   # noqa: E731
        force = self._force_of(row)
        return (f"Trial {row['id']}: peak {fmt(row['red_max'])}% at "
                f"{fmt(row['mark_auto_max_t'])} s, dip {fmt(row['red_min'])}% "
                f"at {fmt(row['mark_auto_min_t'])} s, baseline "
                f"{fmt(row['red_baseline'])}%, Mark "
                f"{fmt(row['mark_operator_t'])} s; "
                + ", ".join(f"{k} {fmt(v)}" for k, v in force.items()))

    @property
    def width_gradient(self):
        """d(width)/d(tilt) and d(width)/d(speed) at the centre of the map,
        with one sigma, from the Gaussian process over the measured trials
        (all force bands)."""
        import numpy
        rows = [r for r in self._map_rows() if r["width"] is not None
                and r["tilt"] is not None and r["speed"] is not None]
        if len(rows) < 3:
            return ""
        tilt = numpy.array([r["tilt"] for r in rows], dtype=float)
        speed = numpy.array([r["speed"] for r in rows], dtype=float)
        spans = numpy.array([max(v.max() - v.min(), 1e-9) for v in (tilt, speed)])
        x = numpy.column_stack([(tilt - tilt.min()) / spans[0],
                                (speed - speed.min()) / spans[1]])
        widths = numpy.array([r["width"] for r in rows], dtype=float)
        spread = float(widths.std()) or 1.0
        noise = numpy.array([(r["width_sigma"] or 0.05 * spread) ** 2
                             for r in rows])
        grad, var = analysis.gp_gradient(x, widths, numpy.array([[0.5, 0.5]]),
                                         length=plot_data.SLICE_LENGTH,
                                         noise=noise)
        g = grad[0] / spans
        s = numpy.sqrt(var[0]) / spans
        return (f"At the map centre: {g[0]:+.3g} ± {s[0]:.2g} um/deg, "
                f"{g[1]:+.3g} ± {s[1]:.2g} um per step/s")

    # -- schema --------------------------------------------------------------
    @property
    def schema(self):
        P = self.PARAMS
        configure = "Configure Transfer Map"
        return sch.schema(
            sch.section(
                "Session",
                sch.readonly("Database", "db_path"),
                sch.readonly("Trials", "trial_count", param=P["trial_count"]),
                sch.button("New session database", "new_database",
                           confirm="Start a new database beside this one? The "
                                   "current one stays on disk.",
                           disabled_when=("armed",)),
            ),
            sch.section(
                "Store",
                sch.readonly("Trial store", "store_status", role="info"),
                sch.entry("Store file", "store_path", P["store_path"],
                          disabled_when=("armed",)),
                sch.button("Open store", "open_store", inputs=("store_path",),
                           disabled_when=("armed",)),
                sch.entry("Folder for a new store", "store_dir", P["store_dir"],
                          disabled_when=("armed",)),
                sch.entry("New store name", "store_name", P["store_name"],
                          disabled_when=("armed",)),
                sch.button("New store", "new_store",
                           inputs=("store_dir", "store_name"),
                           disabled_when=("armed",)),
            ),
            sch.section(
                "Trial",
                sch.readonly("Next step", "next_step", role="info"),
                sch.region_select("Set capture region", "set_region",
                                  model_attr="region", role="info",
                                  data_command="screen_image"),
                sch.readonly("Tilt", "tilt_now", rail=True, param=P["tilt_now"]),
                sch.readonly("Speed", "speed_now", rail=True, param=P["speed_now"]),
                sch.readonly("Red", "red_now", param=P["red_now"], format=".2f"),
                sch.entry("Tip ID", "tip_id", P["tip_id"]),
                sch.dropdown("Known tips", "tip_pick", "pick_tip", "tip_options"),
                sch.button("New tip", "new_tip", inputs=("tip_id",)),
                sch.readonly("Trials on this tip", "tip_trial_count"),
                sch.readonly("Tip", "tip_status"),
                # Bench 2026-09-28: the tilt varies between trials of one
                # tip and was buried two tiers down; it is asked here, per
                # trial, and Next step insists on it when no rotator reads.
                sch.entry("Tilt for this trial (deg)", "typed_tilt",
                          P["typed_tilt"]),
                sch.entry("Speed for this trial (steps/s)", "typed_speed",
                          P["typed_speed"]),
                sch.button("Arm trial", "arm_trial",
                           inputs=("tip_id", "typed_tilt", "typed_speed"),
                           role="go", disabled_when=("armed", "latched")),
                sch.button("Mark force", "mark_force", enabled_when=("armed",)),
                sch.entry("Note", "note", P["note"]),
                sch.button("Finish trial", "finish_trial", inputs=("note",),
                           role="go", enabled_when=("armed",)),
                sch.button("Abort trial", "abort_trial", enabled_when=("armed",),
                           stop=True),
                sch.toggle("Tip broke", "is_broke", "mark_broke", "Broke",
                           "Not broken", on_args=(True,), off_args=(False,)),
                sch.readonly("Status", "trial_status", role="info"),
                # V4: the pictures are the video's own frames, labelled.
                sch.image("First frame", "first_frame_image",
                          empty="The video's first frame, labelled, once you "
                                "arm."),
                sch.image("Mark frame", "mark_frame_image",
                          empty="The video's frame at Mark force, labelled MARK."),
                sch.readonly("Video", "video_status"),
                sch.plot("Red % since Arm", "live_series", x_label="time (s)",
                         y_label="red (%)",
                         empty="Arm a trial and its red percent plots here."),
                sch.image("Transfer map", "figure",
                          empty="No trials yet. Record one, or import trials."),
            ),
            sch.section(
                "Context",
                sch.image("Arm, whole screen", "before_full_image",
                          empty="The whole screen, taken when you arm."),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Tip",
                sch.entry("Tip note", "tip_note", P["tip_note"]),
                sch.button("Save tip note", "set_tip_note",
                           inputs=("tip_id", "tip_note")),
                sch.button("Retire tip", "retire_tip", inputs=("tip_id",)),
                sch.button("Return tip to use", "unretire_tip",
                           inputs=("tip_id",)),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Figure",
                sch.dropdown("Figure", "figure_type", "set_figure_type",
                             "figure_type_options"),
                sch.dropdown("Force definition", "force_definition",
                             "set_force_definition", "force_definition_options"),
                sch.dropdown("Force band", "force_band", "set_force_band",
                             "force_band_options"),
                sch.entry("Trial to show (0 = latest)", "trial_pick",
                          P["trial_pick"]),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "AFM measurement",
                sch.entry("Trial", "afm_trial_id", P["afm_trial_id"]),
                sch.entry("Channel width", "width_um", P["width_um"]),
                sch.entry("Width uncertainty", "width_sigma_um",
                          P["width_sigma_um"]),
                sch.entry("Sample thickness", "thickness_nm", P["thickness_nm"]),
                sch.entry("Thickness uncertainty", "thickness_sigma_nm",
                          P["thickness_sigma_nm"]),
                sch.button("Attach AFM", "attach_afm",
                           inputs=("afm_trial_id", "width_um", "width_sigma_um",
                                   "thickness_nm", "thickness_sigma_nm")),
                sch.button("Set tilt for trial", "set_trial_tilt",
                           inputs=("afm_trial_id", "typed_tilt")),
                sch.button("Set speed for trial", "set_trial_speed",
                           inputs=("afm_trial_id", "typed_speed")),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Data",
                sch.file_save("Export trials", "export_csv", extensions=("csv",)),
                sch.file_save("Export profiles", "export_profile_csv",
                              extensions=("csv",)),
                sch.file_save("Export tips", "export_tips_csv",
                              extensions=("csv",)),
                sch.file_open("Import trials", "import_csv", extensions=("csv",)),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Diagnostics",
                sch.readonly("Last trial", "last_trial_numbers"),
                sch.readonly("Width gradient", "width_gradient"),
                sch.readonly("Video encoder", "video_encoder"),
                sch.log_stream("Trials", "trials_log"),
                sch.log_stream("Tips", "tips_log"),
                sch.button("Delete trial", "delete_trial", inputs=("trial_pick",),
                           disabled_when=("armed",)),
                tier=3, disclosure="Diagnostics",
            ),
            self._safety_section(),
        )


def _ordinal(n):
    """1st, 2nd, 3rd, 4th, 11th, 12th, 13th, 21st, 101st, 111th."""
    n = int(n)
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _number(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
