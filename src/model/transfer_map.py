"""The Transfer Map: the heatmap this project exists for.

A map over **speed and force** whose value of interest is the **channel
width** of the transferred sample (owner, 2026-09-27; since 2026-10-07 the
map draws no tilt: the tilt is collected with every trial, never demanded
and never plotted, and `_map_rows` carries it and the bench's
`force_class` for the figures to use). It builds
itself as trials are recorded: the operator arms a trial, picks the capture
region, lowers the tip, presses Mark force at the force they want, and
finishes; the station keeps the red-percent slice of the lowering, a video
of the whole display from the region to End recording (owner ruling
2026-10-07, "record everything possible, trim in analysis": a
`devices.screen_recorder.ScreenRecorder` H.264 MP4 at the display's own
resolution with its `frames.csv`), every stream the station publishes on
the same clock (`model.trial_telemetry`, a `telemetry.csv` beside the
video), the stage still at Arm and the display at the Mark, and the tilt
and speed at the time. AFM widths and thicknesses are attached later, with
their uncertainties.

**The trial is a procedure** (owner ruling 2026-10-07; MODEL_CONTRACT
"Phases"): `setup` (tip, tilt, speed) -> Arm -> `region` (armed and
waiting: a full-display still of the stage is taken first, it is the
trial's first picture, and the capture region is picked ON it; nothing
records yet) -> `live` (the region landed: the trial's row, Red Percent's
run, the video) -> `marked` (after Mark force) -> `finish` (End recording:
review the pictures and the profile; Finish keeps the trial, then back to
`setup`). Abort works from `region` on; in `region` it stores nothing. The
step is `phase`, published beside the mode; the schema shows each step
only its own controls, and the Panel refuses the rest.

**Tips are records** (`tips` table): created on demand by the first trial
on a tip (or an import naming one), with the trial it broke on and whether
it is retired; which trials used it is derived from `trials.tip_id`. The
setup step has ONE tip control (approved proposal, 2026-10-07): the Tip
dropdown, every tip labelled "<tip_id> · <model> · <n> trials", retired
ones last; the tip picked there is the trial's tip. "New tip…" opens the
`new_tip` step, a prompt of its own (Tip ID, Model, Add tip, Cancel). The
catalogue records each tip's MODEL (owner, 2026-10-07): `tips.model` and
the `tip_models` list, both by presence; the tips catalogued before it
are all TAP300 and are labelled so once, at the migration that adds the
column; a tip made later gets the model the operator picks, or none.

**The cut is traced to its flake** (approved proposal, 2026-10-07): the
setup step picks the Sample, Chip and Flake from the Sample DB's store
(read-only, `model.sample_store.SampleStore.open_readonly`, found through
the Sample DB's public `db_path`), Arm refuses without all three, and the
trial row names them with the cut's number on that flake (`cut_id`, 1 +
the trials already on it). The picks stay for the next trial.

**Force** has no sensor. THE force model is the tip's SHADE (the lab's
`model.tip_shade`, owner ruling 2026-10-07): each accepted frame of the
capture region (the analysis's `subscribe_frames`) is measured on the
trial's shade worker (the median green of the region's right half) into a
`ShadeTracker`; the sheet's **Force estimate** reads it ("Medium force ·
0.43"), the Mark freezes it, and Finish stores its six columns
(`force_position`, `force_class`, `contact_lowered`, `shade_baseline`,
`shade_peak`, `shade_mark`); `shade_position` is the map's default force
definition, read from that column, and `rebuild_force` recomputes it from
the footage (cropping the stored region out of the full display) or from
the shade stored per frame. The red-percent extrema
(`model.transfer_map_analysis`) are an analysis factor beside it, computed
from the stored raw profile when a figure or an export asks, never shown
as force. A trial marked `invalid` (the lab's flag) stays on record and in
the export, off the map and out of every analysis.

**It moves nothing.** No port, no gamepad, no device. `_halt_hardware`
disarms the trial in memory and returns at once; the video and the
telemetry are stopped (each within its own bounded stop) and the aborted
trial written on a worker, so a stop is never held by the disk or the
encoder.

**The store is chosen by the operator** (owner decision 4, 2026-09-30,
replacing the 2026-09-27 "inside the checkout" default): with no choice
recorded the map has NO store - its state says `store: {"path": None,
"chosen": False}`, its page is the `new_store` step (2026-10-08, the Sample
DB's prompt, `store_choice.StorePrompt`: a folder prefilled with
`~/transfer-stage-runs/stores/<email>/`, Choose folder…, a name, New store;
or an existing file and Open store) and every recording command is refused
until one is chosen. The choice is remembered in the operator's choices
file (`controller.user_config`, wired in as `TransferMap.choices` by the
composition root; the model never imports the controller). A store inside
the station's own folder (the bundle root, or this checkout) is refused:
updates replace that folder. `STATION_MAP_DB=<path>` / `--map-db` override
the choice and skip the question. Pictures sit beside the file in
`<folder>/<database name>/<trial_id>/`, exports in `<folder>/exports/`.
"New session database" opens the same prompt (the open store's folder and
a fresh `transfer_map_<stamp>` name, both changeable): a new file is never
made without asking where. A store's files are never moved or rewritten
(the trial rows hold full picture paths). Building the
model creates nothing (the contract test builds every registered class).

Live sources are duck-typed from `on_model_added`, never a class name: tilt
typed for the trial (it wins, the lab's bench fix), else from a model with
`position_deg` (the Rotator) (blank = no tilt, never a default 0); speed and Z from a model with
`position`, `position_time` and `mode_name` (a probe: `live_speed` if it has
one, else the manual or autonomous speed for the mode it is in); samples
from a model with `subscribe` and `grab_frame` (RGB analysis), and its
region frames through `subscribe_frames` when it has it; the Sample DB's
store from the model named "Sample DB"; the telemetry reads every model
met there.
"""
import csv
import json
import math
import os
import queue
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path

import schema as sch
from devices import video
from devices.screen_recorder import ScreenRecorder
from events import events
from model import finalize
from model import plot_data
from model import shade_offline
from model import store_choice
from model import tip_shade
from model import transfer_map_analysis as analysis
from model.base import Model
from model.sample_store import SampleStore
from model.sample_store import PicturePreview, StoreRefused as _PreviewRefused
from model.trial_telemetry import TrialTelemetry
from param import Param
from result import NeedsConfirm, Refused

#: Every recording command's refusal while no store is chosen (A3).
NO_STORE = ("Choose a trial store first: press New store or Open store on "
            "the Transfer Map page.")
#: The file the SQLite library writes first in every database.
_SQLITE_MAGIC = b"SQLite format 3\x00"


def _count(n, noun):
    """ "1 trial", "2 trials" (UX audit 2026-10-08 #14: no "trial(s)")."""
    return f"{n} {noun if n == 1 else noun + 's'}"


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
    # Version 6 (owner 2026-10-04; proposal-user-system.md section 7.3 with
    # Q17/Q19 as answered): the chip and flake being cut, who cut it, the
    # colour profile, and the cut descriptors. NULL = not measured.
    ("sample_id", "TEXT"), ("flake_uid", "TEXT"), ("operator_id", "TEXT"),
    # How operator_id was established (user-system Q1): "station" (nobody
    # signed in), "offline-unverified" (no lab server checked a PIN), later
    # "verified".
    ("operator_auth", "TEXT"),
    ("camera_profile_id", "TEXT"),
    # The AFM step from the substrate to the channel's top (positive up) and
    # the depth the tip cut into the flake (positive down), each optional.
    ("channel_height_nm", "REAL"), ("channel_height_sigma_nm", "REAL"),
    ("trench_depth_nm", "REAL"), ("trench_depth_sigma_nm", "REAL"),
    # The approximate channel width by optical microscopy. `width_um` stays
    # the AFM width: only it makes a trial `measured`.
    ("width_optical_um", "REAL"), ("width_optical_sigma_um", "REAL"),
    ("width_optical_method", "TEXT"),
    # 7 (the lab's store, bench 2026-09-28 and 2026-10-04): the chip and
    # flake the cut was made on and the cut's number on that flake (picked
    # on the setup step since the approved proposal of 2026-10-07: cut_id
    # is TEXT, as the lab's list has it), and the invalid flag ("vacuum was
    # off, data is invalid"): kept on record and in the export, left off the
    # map and out of every analysis.
    ("chip_id", "TEXT"), ("flake_id", "TEXT"), ("cut_id", "TEXT"),
    ("invalid", "INTEGER NOT NULL DEFAULT 0"),
    # 8 (owner 2026-10-06, `model/tip_shade.py`): the force from the tip's
    # shade. `contact_lowered`: steps lowered since the first frame, at
    # contact. `force_position`: 0 at the shade's peak, 1 back at its
    # baseline, at the Mark; `force_class` its word (Contact, Low, Medium,
    # High). NULL = no contact before the Mark, or no frames.
    ("contact_lowered", "REAL"), ("force_position", "REAL"),
    ("force_class", "TEXT"), ("shade_baseline", "REAL"),
    ("shade_peak", "REAL"), ("shade_mark", "REAL"),
    # The capture region the analysis measured, in desktop coordinates, and
    # the display the full-display video shows (its place on the desktop),
    # stored when the region lands (2026-10-07, with the lab's merge): what
    # `rebuild_force` crops the region from in each frame of `screen.mp4`.
    # NULL for trials recorded before; added by presence.
    ("region_left", "INTEGER"), ("region_top", "INTEGER"),
    ("region_width", "INTEGER"), ("region_height", "INTEGER"),
    ("display_left", "INTEGER"), ("display_top", "INTEGER"),
    ("display_width", "INTEGER"), ("display_height", "INTEGER"),
)
_TRIAL_NAMES = frozenset(name for name, _kind in TRIAL_COLUMNS)
#: The colour channels Red Percent's rows carry beside `red` (its RGB
#: analysis, 2026-10-07): the region's mean R, G and B and its green and
#: blue fractions. Kept per profile row when the row has them (NULL
#: otherwise, and for every row recorded before them); added BY PRESENCE.
PROFILE_CHANNELS = ("r_mean", "g_mean", "b_mean", "green", "blue")
#: The tip's shade (`model.tip_shade`, owner 2026-10-06) of the accepted
#: frame the row was measured on: what an offline rebuild reads when the
#: video is gone (with `telemetry.csv`'s `transfer_map.shade`, every frame's).
PROFILE_SHADE = "shade"
#: The profile columns added after the first store, by presence.
PROFILE_ADDED = PROFILE_CHANNELS + (PROFILE_SHADE,)
PROFILE_COLUMNS = ("trial_id", "t_s", "red", "z", "x", "y") + PROFILE_ADDED
#: One stored profile row: (t_s, red, z, x, y, *PROFILE_ADDED).
_PROFILE_WIDTH = 5 + len(PROFILE_ADDED)
_PROFILE_INSERT = ("INSERT INTO profile (" + ", ".join(PROFILE_COLUMNS)
                   + ") VALUES (" + ", ".join("?" for _ in PROFILE_COLUMNS) + ")")
#: The tips table (version 3): one record per tip, created on demand (the
#: first Arm on a tip it has not seen, or an import naming one). Usage is
#: derived from `trials.tip_id`, never stored twice: `TrialStore.tip`
#: adds the trial ids, their count and whether it broke.
TIP_COLUMNS = (
    ("tip_id", "TEXT PRIMARY KEY"), ("created_at", "TEXT"),
    ("first_trial_id", "INTEGER"), ("last_trial_id", "INTEGER"),
    ("last_used_at", "TEXT"), ("broke_trial_id", "INTEGER"),
    ("retired_at", "TEXT"), ("note", "TEXT"),
    # The tip's model (owner, 2026-10-07), one of `tip_models`; added by
    # presence. NULL: not catalogued (never a silent default).
    ("model", "TEXT"),
)
_TIP_NAMES = frozenset(name for name, _kind in TIP_COLUMNS)
#: The one tip model the lab has used so far (owner ruling 2026-10-07):
#: the `tip_models` list starts with it, and every tip catalogued before
#: models were recorded is labelled with it, once, by the migration.
DEFAULT_TIP_MODEL = "TAP300"
#: `PRAGMA user_version`. 1: the first store. 2: `before_full_path` and
#: `after_full_path` (the whole-screen pictures). 3: `mark_path` and
#: `mark_full_path` (the pictures at the Mark) and the `tips` table. 4:
#: `speed_measured_steps_s`. 5: the video (`video_path`, `video_index_path`,
#: `video_frames`, `video_dropped`); the region stills and the Mark and
#: After whole-screen pictures are no longer taken, their columns stay for
#: the rows that have them. 6: the trial names its chip, flake and operator,
#: the cut descriptors (two AFM heights, an optical width with its method),
#: and the `meta` table with the store's identity (`map_db_uuid`, written
#: once). An older file gains the columns by `ALTER TABLE ... ADD COLUMN`,
#: a tip record for every tip its trials name and its identity, the first
#: time it is opened or written, and keeps every trial it holds. Columns are
#: added BY PRESENCE at every version (2026-10-07): a file at this version
#: or newer gains the columns it lacks and keeps its version; nothing it has
#: is touched. 7 (the lab's): `chip_id`, `flake_id`, `cut_id`, `invalid`.
#: 8 (the lab's, adopted with its merge 2026-10-07): the tip-shade force
#: columns (`contact_lowered`, `force_position`, `force_class`,
#: `shade_baseline`, `shade_peak`, `shade_mark`). The version is never
#: lowered; a file numbered ahead of its columns is completed.
SCHEMA_VERSION = 8
#: How an optical width was measured (Q19, owner 2026-10-04: pixels on the
#: capture-region picture at the Sample DB's um_per_px, the default).
WIDTH_OPTICAL_METHODS = ("capture_px", "reticle", "vendor_tool", "estimate")

_CREATE = (
    "CREATE TABLE IF NOT EXISTS trials ("
    + ", ".join(name + " " + kind for name, kind in TRIAL_COLUMNS) + ")",
    "CREATE TABLE IF NOT EXISTS profile (trial_id INTEGER NOT NULL "
    "REFERENCES trials(id), t_s REAL NOT NULL, red REAL, z REAL, x REAL, "
    "y REAL, " + ", ".join(f"{name} REAL" for name in PROFILE_ADDED) + ")",
    "CREATE INDEX IF NOT EXISTS profile_trial ON profile(trial_id)",
    "CREATE TABLE IF NOT EXISTS tips ("
    + ", ".join(name + " " + kind for name, kind in TIP_COLUMNS) + ")",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
    # The tip models to pick from (owner, 2026-10-07), by presence.
    "CREATE TABLE IF NOT EXISTS tip_models (name TEXT PRIMARY KEY)",
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

#: The trial's recording: the whole display, this many frames a second
#: (`devices.screen_recorder`; the region recorder of 2026-09-28 retired).
VIDEO_FPS = 15
#: The telemetry sidecar beside the video (until part 2 gives it a table).
TELEMETRY_NAME = "telemetry.csv"
TELEMETRY_COLUMNS = ("t", "stream", "value")
#: The display the stage still is taken of (an index into the capture
#: library's monitors: 1 is the primary; `TransferMap(monitor=...)`).
STAGE_MONITOR = 1
#: Where a stage still waits, under the pictures folder, until its trial
#: has a number (or is disarmed, which deletes it).
STAGING = ".stage"
#: Region frames waiting for the trial's shade worker (about two seconds at
#: the source rate); a frame arriving when it is full is dropped and
#: counted, never waited for (the analysis loop's rate is the measurement's).
SHADE_QUEUE = 2 * VIDEO_FPS
#: `analysis_health` compares Red Percent's counters over windows of at
#: least this many seconds (a window closes at the first read after it).
HEALTH_WINDOW_S = 1.0
#: ... and calls the analysis "unsettled" when more than this share of a
#: window's reads were rejected (black, stale or unsettled; CAP-1).
UNSETTLED_SHARE = 0.5


class _HealthWatch:
    """Red Percent's settle-gate counters, window by window, for
    `analysis_health`: "stalled" when a window saw no read at all,
    "unsettled" when more than `UNSETTLED_SHARE` of its reads were
    rejected, else "settled". O(1) per observation; a new run (or counters
    that went back) starts a new window."""

    def __init__(self):
        self._lock = threading.Lock()
        self._run = None
        self._start = 0.0
        self._counts = (0, 0)
        self._word = "settled"

    def observe(self, run, now, accepted, rejected):
        with self._lock:
            if (run is not self._run or accepted < self._counts[0]
                    or rejected < self._counts[1]):
                self._run, self._start = run, now
                self._counts, self._word = (accepted, rejected), "settled"
            elif now - self._start >= HEALTH_WINDOW_S:
                fresh = accepted - self._counts[0]
                refused = rejected - self._counts[1]
                if fresh + refused == 0:
                    self._word = "stalled"
                elif refused > UNSETTLED_SHARE * (fresh + refused):
                    self._word = "unsettled"
                else:
                    self._word = "settled"
                self._start, self._counts = now, (accepted, rejected)
            return self._word


def _make_recorder(out_dir, fps, monitor):
    """The full-display recorder (`devices.screen_recorder`): the stage
    still, the trial's video and the still at the Mark are taken through it.
    `TransferMap(recorder_factory=...)` replaces it, so a test never
    touches the screen."""
    return ScreenRecorder(out_dir, fps, monitor)


def _make_telemetry(controller):
    """The trial's telemetry (`model.trial_telemetry`) over the map's peers.
    `TransferMap(telemetry_factory=...)` replaces it."""
    return TrialTelemetry(controller)


class _Peers:
    """What `TrialTelemetry` reads of a controller: `models`, the models the
    map has met through `on_model_added` (the map itself records nothing)."""

    def __init__(self, owner):
        self._owner = owner

    @property
    def models(self):
        return dict(self._owner._peers)


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
        self._file_version = SCHEMA_VERSION
        self._labelled = 0

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
            self._labelled = 0
            try:
                with db:
                    for statement in _CREATE:
                        db.execute(statement)
                    added = self._migrate(db, fresh)
                    result = fn(db)
            finally:
                db.close()
            labelled = self._labelled
        if fresh:
            events.info("Map Database Created", "A new trial store "
                        "was created at " + str(self.path) + ".",
                        source="Transfer Map")
        elif added is not None:
            events.info("Database Upgraded", f"{self.path} now has "
                        f"{', '.join(added) or 'every column'} (version "
                        f"{max(self._file_version, SCHEMA_VERSION)}). Its "
                        "trials are kept.", source="Transfer Map")
        if labelled:
            events.info("Tip Models", f"Tip models: {labelled} tips labelled "
                        f"{DEFAULT_TIP_MODEL}", source="Transfer Map")
        return result

    def _migrate(self, db, fresh):
        """Bring the file to `SCHEMA_VERSION`. Every trials column it lacks
        is added first, at ANY version (by presence, 2026-10-07: a column
        that exists is skipped, so an upgrade cut short finishes and the
        lab's v7/v8 files gain only what they lack). A file at this version
        or newer keeps its version; an older one gets the rest of the
        upgrade, then the version is set. Returns the columns added, or None
        when nothing was done or the file is new."""
        version = self._file_version = \
            db.execute("PRAGMA user_version").fetchone()[0]
        have = {row[1] for row in db.execute("PRAGMA table_info(trials)")}
        added = []
        for name, kind in TRIAL_COLUMNS:
            if name not in have:
                # Names and kinds are this module's own, never input.
                db.execute(f"ALTER TABLE trials ADD COLUMN {name} {kind}")
                added.append(name)
        have = {row[1] for row in db.execute("PRAGMA table_info(profile)")}
        for name in PROFILE_ADDED:
            if name not in have:
                db.execute(f"ALTER TABLE profile ADD COLUMN {name} REAL")
                added.append(name)
        have = {row[1] for row in db.execute("PRAGMA table_info(tips)")}
        modelled = "model" in have
        for name, kind in TIP_COLUMNS:
            if name not in have:
                db.execute(f"ALTER TABLE tips ADD COLUMN {name} {kind}")
                added.append("tips." + name)
        db.execute("INSERT INTO tip_models (name) SELECT ? WHERE NOT EXISTS "
                   "(SELECT 1 FROM tip_models)", (DEFAULT_TIP_MODEL,))
        if not modelled:
            # Once, as the column arrives: every tip catalogued so far is a
            # TAP300 (owner ruling 2026-10-07).
            self._label_tips(db)
        if version >= SCHEMA_VERSION and not added:
            # Current or newer, and nothing missing: left alone.
            return None
        if version < 3 and not fresh:
            # The tips table is new in 3 (`_CREATE` made it empty): one
            # record per tip the file's trials already name, catalogued
            # before models were, so TAP300 like the rest.
            db.execute(_BACKFILL_TIPS)
            self._label_tips(db)
            added.append("tips")
        # The store's identity (for the lab server's trial links): new in 6,
        # written once and never changed (OR IGNORE keeps an existing one,
        # and a file numbered ahead of its columns gets one too).
        db.executemany("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                       [("map_db_uuid", str(uuid.uuid4())),
                        ("created_at", _now())])
        db.execute(f"PRAGMA user_version = {max(int(version), SCHEMA_VERSION)}")
        return None if fresh else added

    def _label_tips(self, db):
        """Every tip with no model becomes `DEFAULT_TIP_MODEL`; counted for
        the one "Tip Models" event. Only ever run by the migration."""
        self._labelled += db.execute(
            "UPDATE tips SET model = ? WHERE model IS NULL OR TRIM(model) = ''",
            (DEFAULT_TIP_MODEL,)).rowcount

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
                # A row without the colour channels (t_s, red, z, x, y)
                # stores them as NULL.
                db.executemany(_PROFILE_INSERT, [
                    (trial_id, *(tuple(row) + (None,) * _PROFILE_WIDTH)
                     [:_PROFILE_WIDTH]) for row in profile])
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

    def create_tip(self, tip_id, when, model=None):
        """A tip record with no trial yet (New tip on the sheet, or a note
        on a tip before its first trial), of `model` (None: not catalogued).
        -> created (False: it existed)."""
        return self.write(lambda db: db.execute(
            "INSERT OR IGNORE INTO tips (tip_id, created_at, model) VALUES "
            "(?, ?, ?)", (tip_id, when, model)).rowcount == 1)

    def tip_models(self):
        """The tip models to pick from, in the order they were added; a
        file with no list yet (or none) answers the seed, as the first
        write makes it."""
        return [r["name"] for r in self.read(
            "SELECT name FROM tip_models ORDER BY rowid")] or [DEFAULT_TIP_MODEL]

    def add_tip_model(self, name):
        """Add a model (case-insensitive: one already listed is returned in
        its stored spelling). -> the stored name."""
        def _do(db):
            for (known,) in db.execute("SELECT name FROM tip_models"):
                if known.lower() == name.lower():
                    return known
            db.execute("INSERT INTO tip_models (name) VALUES (?)", (name,))
            return name
        return self.write(_do)

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

    def meta(self):
        """The store's identity (`map_db_uuid`, `created_at`); empty for a
        file that does not exist yet or is not upgraded yet (a read never
        writes)."""
        return {row["key"]: row["value"]
                for row in self.read("SELECT key, value FROM meta")}

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

    def count_for_flake(self, sample_id, chip_id, flake_id, before=None):
        """Stored trials on this flake (every status), the IDs compared
        trimmed and case-insensitively as the Sample DB compares them; with
        `before`, only those numbered below it. 0 for a file without the
        columns (a read never migrates)."""
        sql = ("SELECT COUNT(*) AS n FROM trials WHERE "
               "lower(trim(sample_id)) = lower(trim(?)) AND "
               "lower(trim(chip_id)) = lower(trim(?)) AND "
               "lower(trim(flake_id)) = lower(trim(?))")
        args = [str(sample_id), str(chip_id), str(flake_id)]
        if before is not None:
            sql += " AND id < ?"
            args.append(int(before))
        rows = self.read(sql, args)
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
        self._file_version = SCHEMA_VERSION

    @property
    def exists(self):
        return False

    def write(self, fn):
        raise Refused(NO_STORE)


class _Pending:
    """Armed and waiting for the capture region (the `region` step): the
    stage still is taken, nothing else exists yet (no row, no run, no
    video). `size` is the still's pixels, `bounds` the display's place on
    the desktop in the capture library's coordinates (None when the screen
    could not say)."""

    def __init__(self, tip, still, size, bounds, where=(None, None, None)):
        self.tip = tip
        self.still = still
        self.size = size
        self.bounds = bounds
        #: (sample_id, chip_id, flake_id) picked at Arm.
        self.where = tuple(where)


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
        #: The whole display's recording (a started `ScreenRecorder`), or
        #: None; `device` takes the stills (the Mark's) either way; `video`
        #: is its `RecorderResult` once stopped; `video_error` why there is
        #: none. `telemetry` (`TrialTelemetry`) and its rows once stopped.
        self.recorder = None
        self.device = None
        self.video = None
        self.video_error = None
        self.telemetry = None
        self.telemetry_rows = None
        #: Monotonic times of the Marks, for the telemetry sidecar.
        self.marks = []
        #: The capture's start and stop never overlap (a stop's writer may
        #: come while the start is still running).
        self.capture_lock = threading.Lock()
        #: The still at the Mark: one worker at a time; a Mark while it
        #: runs asks for one more.
        self.mark_lock = threading.Lock()
        self.mark_wanted = False
        self.mark_worker = None
        #: End recording ran (the `finish` step): no more rows, no video.
        self.ended = False
        #: The stage still's display on the desktop (`_Pending.bounds`).
        self.bounds = None
        #: The review figure's PNG (`trial_figure`), drawn once.
        self.review = None
        #: (sample_id, chip_id, flake_id) the cut is on (`_Pending.where`).
        self.where = (None, None, None)
        #: The tip's shade (owner 2026-10-06, `model.tip_shade`): THE force
        #: model, the Force estimate's only source; fed one accepted region
        #: frame at a time, in order, by the trial's shade worker.
        self.shade = tip_shade.ShadeTracker()
        #: The analysis's frame time (its run's t_s) -> that frame's shade:
        #: a row and the frame it was measured on share the time, so the
        #: profile row gets its frame's shade at Finish.
        self.shade_at = {}
        self.shade_queue = queue.Queue(maxsize=SHADE_QUEUE)
        #: Set when nothing more is fed: the worker drains the queue, leaves.
        self.shade_done = threading.Event()
        self.shade_worker = None
        self.shade_dropped = 0
        #: (monotonic t, shade) per frame, for the telemetry sidecar.
        self.shades = []


class TransferMap(store_choice.StorePrompt, Model):
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

    #: The trial's procedure, in order (owner ruling 2026-10-07): `phase`
    #: says which step the operator is at; the schema draws each step's
    #: controls only in it. `new_tip` is the New tip prompt, entered from
    #: `setup` and left back to it (Add tip or Cancel); it arms nothing.
    #: `new_store` (2026-10-08) is the store prompt: the page while no store
    #: is chosen, and Change store… / New session database.
    PHASES = ("setup", "new_tip", "region", "live", "marked", "finish",
              store_choice.StorePrompt.PROMPT)
    #: The user setting (and the station choices key) that remembers the
    #: store (`UserStore.put_setting(email, STORE_KEY, path)`).
    STORE_KEY = "map_store"

    PARAMS = {p.name: p for p in (
        # The trial's tip: set by the Tip dropdown (`pick_tip`) or Add tip,
        # never typed on the setup row (approved proposal, 2026-10-07).
        Param("tip_id", "text", default="", label="Tip"),
        # The New tip prompt's entries (the `new_tip` step): the ID, and a
        # model name to add to the list.
        Param("new_tip_id", "text", default="", label="Tip ID"),
        Param("new_model_name", "text", default="", label="New model"),
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
        # Store v6, Q17: two AFM heights of the cut, each optional.
        Param("channel_height_nm", "float", default=0.0, minimum=0, decimals=1,
              unit="nm", label="Channel height"),
        Param("channel_height_sigma_nm", "float", default=0.0, minimum=0,
              decimals=1, unit="nm", label="Channel height uncertainty"),
        Param("trench_depth_nm", "float", default=0.0, minimum=0, decimals=1,
              unit="nm", label="Trench depth"),
        Param("trench_depth_sigma_nm", "float", default=0.0, minimum=0,
              decimals=1, unit="nm", label="Trench depth uncertainty"),
        # Store v6, Q19: the approximate width by optical microscopy.
        Param("width_optical_um", "float", default=0.0, minimum=0, decimals=3,
              unit="um", label="Channel width (optical)"),
        Param("width_optical_sigma_um", "float", default=0.0, minimum=0,
              decimals=3, unit="um", label="Optical width uncertainty"),
        Param("trial_pick", "int", default=0, minimum=0,
              label="Trial to show (0 = latest)"),
        # Readouts: declared for their type and unit only.
        Param("tilt_now", "float", default=0.0, decimals=2, unit="deg",
              label="Tilt"),
        Param("speed_now", "int", default=0, unit="steps/s", label="Speed"),
        Param("trial_count", "int", default=0, label="Trials"),
        # A3: the trial store the operator chooses.
        Param("store_path", "text", default="", label="Existing store file"),
        Param("store_dir", "text", default="", label="Folder"),
        Param("store_name", "text", default="transfer_map", label="Name"),
    )}

    #: Where the operator's store choice is remembered: an object with
    #: `read(key)` / `write(key, value)` (`controller.user_config`, set by
    #: the composition root). None remembers nothing.
    choices = None

    def __init__(self, port=None, gamepad=None, sim=False, db_path=None, *,
                 recorder_factory=None, telemetry_factory=None,
                 monitor=STAGE_MONITOR, sample_store_factory=None):
        super().__init__()
        self.sim = sim
        #: `(path) -> store` with `samples()`, `chips(sample_id)`,
        #: `flakes(sample_id, chip_id)`; None means `_open_samples` (the
        #: Sample DB's store, read-only). A test injects a fake.
        self._sample_store_factory = sample_store_factory
        #: The Sample DB's store file (its public `db_path`), from
        #: `on_model_added`; None while no Sample DB is open.
        self._sample_db = None
        #: The setup step's picks: the sample, chip and flake the next cut
        #: is on. Kept from trial to trial (the next starts on the last
        #: flake); changing the sample clears the chip and the flake.
        self._sample = self._chip = self._flake = None
        #: `(out_dir, fps, monitor) -> ScreenRecorder`; None means
        #: `_make_recorder`. The stage still, the trial's video of the whole
        #: display and the still at the Mark are taken through it.
        self._recorder_factory = recorder_factory
        #: `(controller) -> TrialTelemetry`; None means `_make_telemetry`.
        self._telemetry_factory = telemetry_factory
        #: The display the stills and the video are taken of.
        self.monitor = monitor
        self._lock = threading.Lock()
        path = self._startup_store(Path(db_path) if db_path else self.default_db_path())
        if path is None:
            self._no_store()
        else:
            self._adopt(path)
        # Migration by choice: a store a previous build left inside the
        # install is offered in the path field, never opened for the operator.
        legacy = self.legacy_store_path()
        if legacy is not None and not self.store_path:
            self.store_path = str(legacy)
        self._prefill_store_dir()
        self._trial = None
        #: Armed and waiting for the capture region (`_Pending`, the
        #: `region` step), or None.
        self._pending = None
        #: The New tip prompt is open (the `new_tip` step).
        self._adding_tip = False
        #: The model picked in the prompt (kept for the next prompt; ""
        #: until one is picked: Add tip requires it).
        self._new_tip_model = ""
        #: The trial being started (V8): subscribed to before a run starts,
        #: so the run's first row is its own; numbered and made `_trial` once
        #: its row is written, dropped if the start fails.
        self._arming = None
        self._persisting = []          # abort writers the stop started
        self._red = None
        self._red_name = None
        self._tilts = {}               # name -> model with position_deg
        self._probes = {}              # name -> model with position + mode
        self._peers = {}               # name -> every other open model
        self._figure_type = next(iter(FIGURES))
        self._definition = next(iter(analysis.FORCE_DEFINITIONS))
        self._band = plot_data.FORCE_BANDS[0]
        self._width_source = plot_data.WIDTH_SOURCES[0]
        self._width_optical_method = WIDTH_OPTICAL_METHODS[0]
        #: Who cuts (store v6 `operator_id`) and how that was established
        #: (`operator_auth`): Setup sets both from the signed-in profile.
        self.operator_id = "station"
        self.operator_auth = "station"
        self._revision = 0
        self._figure_cache = None
        self._indices = {}             # trial id -> force indices
        self._encoder = None              # `video_encoder`, once read
        self._health = _HealthWatch()     # `analysis_health`'s windows

    @classmethod
    def default_db_path(cls):
        """`STATION_MAP_DB` (the `--map-db` flag), else the store the
        operator chose and the choices file remembers, else None: no store
        until one is chosen (owner decision 4, 2026-09-30: no default)."""
        configured = os.environ.get("STATION_MAP_DB")
        if configured:
            return Path(configured).expanduser().resolve()
        chosen = cls.choices.read(cls.STORE_KEY) if cls.choices is not None else None
        return Path(chosen) if chosen else None

    @staticmethod
    def install_root():
        """The station's own folder: no store may live under it (the shared
        rule, `store_choice.install_root`)."""
        return store_choice.install_root()

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

    def open_store(self, confirmed=False):
        """Open store: the SQLite file typed in Store file becomes the
        trial store, and is remembered. One on the cloud drive is worked on
        through a local copy (`StorePrompt._working_store`; `confirmed`
        answers its question about a drive copy changed elsewhere)."""
        typed = (self.store_path or "").strip()
        if not typed:
            raise Refused("Type the path of an existing store under Existing "
                          "store file.")
        path = Path(typed).expanduser().resolve()
        self._refuse_store_place(path)
        if not path.is_file():
            raise Refused(f"{path}: no file there. Check the path, or press "
                          "New store to make one.")
        try:
            with open(path, "rb") as handle:
                magic = handle.read(len(_SQLITE_MAGIC))
        except OSError as exc:
            raise Refused(f"{path} could not be read ({exc}).")
        if magic != _SQLITE_MAGIC:
            raise Refused(f"{path} is not a trial store (not a "
                          "database file).")
        self._refuse_store_change()
        return self._choose(self._working_store(path, confirmed), created=False)

    def new_store(self):
        """New store: `<folder>/<name>.sqlite`, created now with its schema
        (the folder too), and remembered. Never over an existing file."""
        folder = (self.store_dir or "").strip()
        name = (self.store_name or "").strip() or "transfer_map"
        if not folder:
            raise Refused("Type or choose the folder for the new store.")
        if any(sep in name for sep in ("/", "\\")) or name in (".", ".."):
            raise Refused("The store name is a file name, not a path.")
        if not name.endswith(".sqlite"):
            name += ".sqlite"
        path = (Path(folder).expanduser() / name).resolve()
        self._refuse_store_place(path)
        if path.exists():
            raise Refused(f"{path} already exists. Type it under Existing store "
                          "file and press Open store to use it.")
        self._refuse_store_change()
        local = self._new_working_store(path)
        try:
            return self._choose(local, created=True)
        except Refused:
            if local != path:
                store_choice.forget_home(local)
            raise

    def _side_names(self, db):
        """The pictures and videos (`<name>/`) and `exports/`."""
        return (Path(db).stem, "exports")

    def _refuse_store_change(self):
        if self.is_armed or self._pending is not None:
            raise Refused("A trial is armed. Finish or abort it before choosing "
                          "another store.")

    def backup_sources(self):
        """`[(database, [folders beside it])]` for the backup: the database's
        pictures and videos (`pictures_root`) and the exports; [] with no
        store. While a trial is open (Arm to Finish or Abort) the database
        only: its video is still being written (audit 2026-10-08 item 14);
        the folders go with the backup after the trial is saved."""
        if not self._store_chosen:
            return []
        if self._trial is not None or self._pending is not None:
            return [(self.db_path, [])]
        return [(self.db_path, [self.pictures_root, self.output_root / "exports"])]

    def _choose(self, path, created):
        if self.is_armed or self._pending is not None:
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
                self.choices.write(self.STORE_KEY, str(path))
            except OSError as exc:
                events.warn("Store Not Remembered", f"Trials go to {path}, but "
                            f"the choice could not be saved ({exc}); the station "
                            "will ask again next time.", source=self.NAME)
        self._remember_home()
        events.info("Trial Store", f"Trials go to {path}: "
                    f"{_count(store.count(), 'trial')}.", source=self.NAME,
                    resolves=events.TRIAL_STORE_NOT_CHOSEN)
        self._request_backup()
        return str(path)

    def adopt_store(self, path):
        """Use `path` from now on (Setup's Open/New store tells an open map
        through this). The previous store, if any, stays on disk untouched."""
        for writer in list(self._persisting):      # an abort still being written
            writer.join(self.THREAD_JOIN_TIMEOUT)
        self._adopt(Path(path))
        self._choosing_store = False
        self._indices = {}
        self._figure_cache = None
        self._changed()
        # The Sample DB reads this store for its trial listing: told here.
        sample = self._peers.get(self.SAMPLE_MAP)
        if sample is not None:
            try:
                sample.on_model_added(self.NAME, self)
            except Exception as exc:
                events.debug("Peer Not Told", repr(exc), source=self.NAME)
        return str(path)

    def release_store(self):
        """No store until the operator chooses one: the `new_store` prompt,
        prefilled with the suggested folder (Setup, at a user switch: owner
        ruling 2026-10-08, a user never records into another's store). The
        previous store stays on disk untouched; nothing of it is left in the
        prompt's fields. Refused while a trial is armed."""
        self._refuse_store_change()
        for writer in list(self._persisting):      # an abort still being written
            writer.join(self.THREAD_JOIN_TIMEOUT)
        self._no_store()
        self._choosing_store = False
        self.store_path = ""
        self.store_dir = self.suggested_folder
        self._indices = {}
        self._figure_cache = None
        self._revision += 1
        self._touch()
        sample = self._peers.get(self.SAMPLE_MAP)
        if sample is not None:
            try:
                sample.on_model_added(self.NAME, self)
            except Exception as exc:
                events.debug("Peer Not Told", repr(exc), source=self.NAME)

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
            events.warn(events.TRIAL_STORE_NOT_CHOSEN, "Open the Transfer Map "
                        "page and press New store or Open store to choose "
                        "where its trials are saved." + (f" A store from an earlier version is at "
                                     f"{legacy}; move it out of the station's "
                                     "folder and open it there to keep its "
                                     "trials." if legacy else ""),
                        source=self.NAME)
            return False
        try:
            self._store.ensure()
        except Exception as exc:
            events.error("Database Not Ready", f"The trial store "
                         f"could not be created at {self.db_path}. Check that "
                         "the folder exists and can be written, or start with "
                         "--map-db PATH.", source=self.NAME, exception=exc)
            return False
        events.info("Database Ready", f"{self.db_path}: "
                    f"{_count(self._store.count(), 'trial')}", source=self.NAME)
        return True

    @property
    def is_armed(self):
        """A trial with a row is open (`live`, `marked` or `finish`)."""
        return self._trial is not None

    @property
    def is_active(self):
        return self.is_armed

    @property
    def is_arming(self):
        """Arm pressed and no row yet: the `region` step, or the Arm in
        flight (`_start_trial`). Not `is_active` (nothing records yet), but
        Setup refuses a Guest switch on it: removing the map would discard
        the Arm (arch audit #13)."""
        return self._pending is not None or self._arming is not None

    @property
    def mode_name(self):
        """What the station is doing: "armed" from Arm (the `region` step
        included) to Finish or Abort, else "ready". Where in the procedure
        the operator is, is `phase`."""
        return ("armed" if self.is_armed or self._pending is not None
                else "ready")

    @property
    def phase(self):
        """The procedure step, from the model's own state (never from the
        mode): `setup` nothing armed; `region` armed, waiting for the capture
        region; `live` recording, no Mark yet; `marked` recording, marked;
        `finish` the recording ended, the trial to review and keep;
        `new_tip` the New tip prompt is open (nothing armed); `new_store`
        no store is chosen, or the store prompt is open (Change store…, New
        session database)."""
        trial = self._trial
        if trial is not None:
            if trial.ended:
                return "finish"
            return "live" if trial.operator_t is None else "marked"
        if self._pending is not None:
            return "region"
        if not self._store_chosen or self._choosing_store:
            return self.PROMPT
        return "new_tip" if self._adding_tip else "setup"

    def _halt_hardware(self):
        """Disarm now; write the aborted trial on a worker. No I/O here and
        no lock wait without a timeout: the stop is never held by the disk.
        A New tip prompt or a store prompt over a chosen store closes (the
        stop returns to setup; with no store the page stays the prompt)."""
        adding, self._adding_tip = self._adding_tip, False
        choosing, self._choosing_store = self._choosing_store, False
        pending, self._pending = self._pending, None
        if pending is not None:
            self._discard_still(pending, wait=False)   # nothing was recorded
        trial = self._claim(timeout=0.05)
        if trial is not None:
            self._release_red()
            trial.shade_done.set()         # its worker drains and leaves
            self._end_own_run(trial)
            # The video and the telemetry are stopped by the writer below,
            # never on the stop's thread (their stops are bounded, not free).
            # The store the trial was armed in: a new session database made
            # while this is being written must not receive it.
            writer = threading.Thread(target=self._save_aborted,
                                      args=(trial, self._store),
                                      daemon=True, name="transfer-map-abort")
            self._persisting.append(writer)
            writer.start()
        if pending is not None or trial is not None or adding or choosing:
            self._touch()                  # the step went back to setup
        return True

    def disable(self):
        """Nothing to de-energize; wait (bounded) for an abort being written,
        so a close does not end the process before the trial is saved."""
        for writer in list(self._persisting):
            writer.join(self.THREAD_JOIN_TIMEOUT)
        self._persisting = [w for w in self._persisting if w.is_alive()]

    # -- the live sources --------------------------------------------------
    #: The model whose store holds the samples, chips and flakes.
    SAMPLE_MAP = "Sample DB"

    def on_model_added(self, name, model):
        # The Sample DB's store, read-only, for the setup pickers: its
        # public `db_path`, never a private attribute (the mirror of how the
        # Sample DB finds this map's store).
        if name == self.SAMPLE_MAP and model is not self:
            path = getattr(model, "db_path", None)
            # None too: a released store is never read on (2026-10-08).
            self._sample_db = Path(path) if path else None
            self._touch()
        if callable(getattr(model, "subscribe", None)) and \
                callable(getattr(model, "grab_frame", None)):
            self._red, self._red_name = model, name
        if hasattr(model, "position_deg"):
            self._tilts[name] = model
        if hasattr(model, "position") and hasattr(model, "position_time") \
                and hasattr(model, "mode_name"):
            self._probes[name] = model
        self._peers[name] = model          # what the telemetry records

    def on_model_removed(self, name, model=None):
        if name == self.SAMPLE_MAP:
            self._sample_db = None
            self._touch()
        if name == self._red_name:
            self._release_red()
            self._red, self._red_name = None, None
        self._tilts.pop(name, None)
        self._probes.pop(name, None)
        self._peers.pop(name, None)

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

    def _release_red(self):
        red = self._red
        if red is not None:
            try:
                red.unsubscribe(self._on_sample)
                frames = getattr(red, "unsubscribe_frames", None)
                if callable(frames):
                    frames(self._on_frame)
            except Exception as exc:
                events.debug("Unsubscribe Failed", repr(exc), source=self.NAME)

    def _read_tilt(self):
        """(degrees, source name) or (None, None). A typed tilt wins over a
        rotator's reading (the lab, bench 2026-09-28: the Rotator read 0.0
        on two trials tilted by hand to 6.5 and 7 deg, and the typed value
        was ignored). Blank the entry and the first rotator that reads is
        the source again."""
        typed = _number(self.typed_tilt)
        if typed is not None:
            return typed, "typed"
        for name, model in self._tilts.items():
            try:
                value = model.position_deg
            except Exception:
                continue
            if value is not None:
                return float(value), name
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
        """The `region` step: the capture region, picked ON the stage still,
        in desktop coordinates like every Red Percent region (the picker
        maps its drag through the still's bounds, which `stage_still`
        publishes; without them the still's pixels are the desktop's).
        Landing it starts the trial (`_start_trial`); a refusal leaves the
        step where it was."""
        pending = self._pending
        if pending is None:
            if self._red is None:
                raise Refused("Open Red Percent first: the capture region is "
                              "the part of the screen it measures.")
            if self.is_armed:
                raise Refused("The capture region is fixed while a trial is "
                              "armed. Finish or abort the trial to change it.")
            raise Refused("Press Arm trial first: the capture region is picked "
                          "on the picture of the stage it takes.")
        return self._start_trial(pending, (x, y, width, height))

    @property
    def stage_still(self):
        """The stage still (owner ruling 2026-10-07): the full display at
        Arm, the trial's first picture and the one its capture region is
        picked on. The armed trial's, in every step from `region` to
        `finish`; b"" before Arm and between trials. PNG bytes, or, when the
        display's place on the desktop is known, `{"image": bytes, "left",
        "top", "width", "height"}` (the shape of Red Percent's
        `screen_image`): the picker then maps its drag to the desktop (a
        Retina still is twice its points; a second display does not start
        at 0, 0), and outlines the region Red Percent holds where it is."""
        pending, trial = self._pending, self._trial
        if pending is not None:
            path, bounds = pending.still, pending.bounds
        elif trial is not None:
            path = self.pictures_root / str(trial.id) / "before_full.png"
            bounds = trial.bounds
        else:
            return b""
        try:
            png = path.read_bytes() if path.is_file() else b""
        except OSError:
            png = b""
        return {"image": png, **bounds} if png and bounds else png

    #: What `next_step` says in each step after `setup`.
    #: Every Arm's first question (the lab, bench 2026-10-04).
    VACUUM = "Is the sample vacuum ON? Check it now."

    STEP_WORDS = {
        "new_tip": "Type the new tip's ID, then press Add tip",
        "new_store": "Choose where to save the trials: New store, or Open store",
        "region": "Drag the capture region on the picture of the stage",
        "live": "Lower the tip; press Mark force when the force is right",
        "marked": "Press End recording when the cut is done",
        "finish": "Review the trial, then press Finish trial to keep it",
    }

    @property
    def next_step(self):
        """The one thing to do next, derived from `phase` so the two cannot
        disagree; "" while latched (the stop says what to do then)."""
        if self.gate_mode == "latched":
            return ""
        phase = self.phase
        if phase != "setup":
            return self.STEP_WORDS[phase]
        red = self._red
        if red is None:
            return "Open Red Percent"
        if not (self.tip_id or "").strip():
            return "Pick a tip, or press New tip…"
        if not all(self._where()):
            return (self.PICK_FLAKE if self.sample_options
                    else self.SAMPLE_FIRST)
        if getattr(red, "is_running", False):
            return "Stop Red Percent's run, then press Arm trial"
        return "Press Arm trial"

    @property
    def state(self):
        snapshot = super().state
        snapshot["has_region"] = self.has_region
        snapshot["store"] = {"path": str(self.db_path) if self._store_chosen else None,
                             "chosen": self._store_chosen}
        # The procedure strip's data (approved proposal 2026-10-07): beside
        # `phase` and `phases`, the Next-step sentence and one word for the
        # analysis ("settled", "unsettled", "stalled", "no region", or ""
        # while no run is expected).
        # (The analysis's own live group, drawn here through `host`, takes
        # the tier its section's `hosted_tier` gives it: the lead's schema
        # key; this page declares nothing for it.)
        snapshot["step_text"] = self.next_step
        snapshot["analysis_health"] = self.analysis_health
        # The pickers' `enabled_by` booleans (the sample > chip > flake
        # hierarchy), where a view reads every gate value.
        snapshot["values"]["has_sample_pick"] = self.has_sample_pick
        snapshot["values"]["has_chip_pick"] = self.has_chip_pick
        snapshot["values"]["preview_alt"] = self.preview_alt
        return snapshot

    # -- the samples, on Red Percent's run thread ------------------------------
    def _on_sample(self, t_s, red, positions, *row):
        """One row of Red Percent's log. Appends and returns; never raises
        into the run loop (Red Percent catches it anyway). The colour
        channels (`PROFILE_CHANNELS`) are read with `.get` from a row dict
        passed after `positions`, else from `positions` itself; a row
        without them stores NULL."""
        trial = self._trial or self._arming
        if trial is None or trial.closed or trial.ended:
            return
        if len(trial.samples) >= MAX_SAMPLES:
            trial.dropped += 1
            return
        positions = positions if isinstance(positions, dict) else {}
        carrier = next((r for r in row if isinstance(r, dict)), positions)
        channels = tuple(_number_or_none(carrier.get(name))
                         for name in PROFILE_CHANNELS)
        x, y, z = positions.get("X"), positions.get("Y"), positions.get("Z")
        if x is None or y is None or z is None:
            px, py, pz = self._probe_axes()
            x = px if x is None else x
            y = py if y is None else y
            z = pz if z is None else z
        t = time.monotonic() - trial.armed
        # The last element is the analysis's own time for the row: the key
        # of its frame's shade (`_profile_rows`), never stored as such.
        trial.samples.append((t, red, z, x, y, *channels, t_s))

    # -- the guided trial --------------------------------------------------
    def arm_trial(self, confirmed=False):
        """Arm: the `setup` -> `region` step. Asks first (the operator frames
        the stage), then takes the stage still, the trial's first picture,
        and waits for the capture region to be picked on it. Nothing else is
        started or written: no row, no run, no video (`_start_trial` does
        those when the region lands)."""
        self._need_store()
        self._guard("Arm")
        if self.is_armed or self._pending is not None:
            raise Refused("A trial is already armed. Finish or abort it first.")
        red = self._red
        if red is None:
            raise Refused("Open Red Percent first: a trial records its red percent.")
        tip = (self.tip_id or "").strip()
        if (self.typed_tilt or "").strip() and _number(self.typed_tilt) is None:
            raise Refused("Tilt without a rotator must be a number of degrees, "
                          "or left blank.")
        if not tip:
            raise Refused("Pick a tip before arming (or press New tip…), so "
                          "the trial can be traced to its tip.")
        where = self._where()
        if not all(where):
            # Owner default (approved proposal 2026-10-07): required at Arm.
            if not self.sample_options:
                raise Refused(self.SAMPLE_FIRST + ", then pick it here "
                              "(Sample, Chip, Flake).")
            raise Refused(self.missing_tier(where) + ": pick the sample, chip "
                          "and flake before arming, so the cut can be traced "
                          "to its flake.")
        if getattr(red, "is_running", False):
            # The trial's run starts on the region picked after Arm; a run
            # already going measures some other region, and Red Percent
            # cannot change a region mid-run.
            raise Refused("Red Percent is running a run of its own. Stop it on "
                          "Red Percent first: the trial starts its own run once "
                          "the capture region is picked.")
        if not confirmed:
            # T3: the picture is taken on the operator's word, with the
            # stage framed. A broken or retired tip is asked in the same
            # prompt (M2): one question, one Continue.
            # TM-3: a tilt is collected when there is one, never demanded.
            tilt_now, tilt_from = self._read_tilt()
            tilt_words = (f" at {tilt_now:g} deg ({tilt_from})"
                          if tilt_now is not None else "")
            speed_now, speed_from = self._read_speed()
            speed_words = (f", {speed_now:g} steps/s"
                           + ("" if speed_from == "typed" else f" ({speed_from})")
                           if speed_now is not None else ", NO speed")
            prompt = (f"Frame the sample now. Continue takes the picture of the "
                      f"stage for trial {self._store.next_id()} on tip {tip}"
                      f"{tilt_words}{speed_words}, cut {self.cut_next} on "
                      f"{' · '.join(where)}; you then pick the capture region "
                      "on it, and the recording starts.")
            doubt = self._tip_doubt(tip)
            if doubt:
                prompt = doubt + "\n\n" + prompt
            # The lab, bench 2026-10-04: trials were cut with the sample
            # vacuum off. The station cannot sense it, so every Arm asks,
            # first, in this same prompt: still one question, one Continue.
            prompt = self.VACUUM + "\n\n" + prompt
            raise NeedsConfirm(prompt, "arm_trial",
                               inputs={"typed_tilt": self.typed_tilt or "",
                                       "typed_speed": self.typed_speed or ""})
        still, size = self._take_still()
        pending = _Pending(tip, still, size, self._display_bounds(), where)
        with self._lock:
            if self.is_estopped:           # a stop inside the grab wins
                stopped = True
            else:
                stopped = False
                self._pending = pending
        if stopped:
            self._discard_still(pending)
            self._guard("Arm")
        self._touch()
        events.info("Stage Taken", f"Pick the capture region on the picture "
                    f"of the stage to start the trial on tip {tip}.",
                    source=self.NAME)
        return None

    def _take_still(self):
        """The stage still: one full-resolution PNG of the display, through
        the recorder's own device (`capture_still`), into the staging folder
        until the trial has a number. -> (path, (width, height)). A still
        that cannot be taken is a refusal: the region is picked on it."""
        folder = self.pictures_root / STAGING
        path = folder / f"stage_{uuid.uuid4().hex}.png"
        try:
            factory = self._recorder_factory or _make_recorder
            factory(folder, VIDEO_FPS, self.monitor).capture_still(path)
            from PIL import Image
            with Image.open(path) as image:
                size = image.size
        except Exception as exc:
            events.debug("Still Failed", repr(exc), source=self.NAME,
                         exception=exc)
            self._unlink(path)
            raise Refused(f"No picture of the stage ({exc}). Check that the "
                          "display can be captured, then press Arm trial again.")
        return path, size

    def _display_bounds(self):
        """The still's display on the desktop (`left, top, width, height`),
        as Red Percent's screen reports it, or None when it cannot say."""
        if isinstance(self.monitor, dict):
            found = self.monitor
        else:
            try:
                found = self._red.screen.monitors[int(self.monitor)]
            except Exception:
                return None
        try:
            return {k: int(found[k]) for k in ("left", "top", "width", "height")}
        except (KeyError, TypeError, ValueError):
            return None

    def _discard_still(self, pending, wait=True):
        """A disarmed trial keeps nothing: its staged still is deleted, on a
        worker when the stop is the caller (no I/O on the stop's thread)."""
        if wait:
            self._unlink(pending.still)
        else:
            threading.Thread(target=self._unlink, args=(pending.still,),
                             daemon=True, name="transfer-map-discard").start()

    @staticmethod
    def _unlink(path):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            events.debug("Still Not Deleted", repr(exc), source="Transfer Map")

    def _start_trial(self, pending, region):
        """The `region` -> `live` step, the old Arm body: Red Percent's region
        and run, the trial's row, the stage still kept as its first picture,
        the video. The trial and the map's subscription exist BEFORE the run
        starts (V8), so the run's first row and frame are the trial's; time
        zero is now. Anything that fails, or a stop or an Abort meanwhile,
        leaves no row and no run, and the step stays `region`."""
        red = self._red
        if red is None:
            raise Refused("Open Red Percent first: a trial records its red percent.")
        self._guard("Arm")
        if getattr(red, "is_running", False):
            raise Refused("Red Percent is running a run of its own. Stop it on "
                          "Red Percent, then pick the capture region again.")
        red.set_region(*region)                  # Red Percent's own checks
        tip = pending.tip
        arming = _Trial(None, None, None, tip)
        self._start_shade(arming)
        self._arming = arming
        started = False
        trial_id = None
        try:
            self._subscribe_red(red)
            red.start_run(confirmed=True)        # a Refused stops here
            started = True
            run = red.run_token
            # The capture gate: no picture of the region, no trial (the
            # video and the red percent are both read from it).
            if not self._take_picture():
                raise Refused("No picture of the capture region: the capture "
                              "region is not set or the screen is not open.")
            tilt, tilt_source = self._read_tilt()
            speed, speed_source = self._read_speed()
            sample, chip, flake = pending.where
            cut = (self._store.count_for_flake(sample, chip, flake) + 1
                   if all(pending.where) else None)
            trial_id = self._store.insert({
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "tip_id": tip,
                "tilt_deg": tilt, "speed_steps_s": speed, "status": "armed",
                "origin": "recorded", "tilt_source": tilt_source,
                "speed_source": speed_source, "note": "",
                "operator_id": self.operator_id,
                "operator_auth": self.operator_auth,
                "sample_id": sample, "chip_id": chip, "flake_id": flake,
                # TEXT, as the bench's v8 column is.
                "cut_id": None if cut is None else str(cut)})
            # The run this trial records through is the map's own: its
            # folder says which trial, tip, sample, chip, flake and cut.
            self._name_run(red, trial_id, tip, sample, chip, flake,
                           None if cut is None else str(cut))
            # What an offline rebuild crops the region from (the full-
            # display video): the region as landed and the display's place.
            self._store.update(trial_id, self._region_fields(
                getattr(red, "region", None) or region, pending.bounds))
            created = self._store.use_tip(tip, trial_id, _now())
            with self._lock:
                if self.is_estopped:
                    self._guard("Arm")
                if self._pending is not pending:
                    raise Refused("The trial was aborted before its recording "
                                  "started; nothing was kept.")
                arming.id, arming.tilt, arming.speed = trial_id, tilt, speed
                arming.run, arming.bounds = run, pending.bounds
                arming.where = pending.where
                self._trial, self._pending, self._arming = arming, None, None
        except BaseException:
            self._arming = None
            arming.shade_done.set()
            self._release_red()
            if started:
                red.end_run()                    # ours: undo it
            if trial_id is not None:
                self._forget_row(trial_id)
            raise
        trial = arming
        self._keep_still(trial, pending)
        self._start_capture(trial, self.pictures_root / str(trial_id))
        self._changed()
        if created:
            events.info("Tip Created", f"Tip {tip} created.", source=self.NAME)
        events.info("Trial Armed", f"Trial {trial_id} armed, "
                    f"{self._place_on_tip(trial)}. Lower the tip, press Mark "
                    "force at the force you want, then End recording.",
                    source=self.NAME)
        return trial_id

    def _keep_still(self, trial, pending):
        """The stage still becomes the trial's `before_full.png` (the column
        of the whole screen at Arm). One that cannot be moved is a warning:
        the trial records anyway."""
        folder = self.pictures_root / str(trial.id)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "before_full.png"
            os.replace(pending.still, target)
            self._store.update(trial.id, {"before_full_path": str(target)})
        except Exception as exc:
            events.debug("Still Not Kept", repr(exc), source=self.NAME,
                         exception=exc)
            self._warn_no_full(trial.id, "Arm", "context")

    def _subscribe_red(self, red):
        """Rows for the profile, and the region's settled frames for the
        tip's shade (owner 2026-10-06) when the analysis offers them.
        Idempotent (it keeps each callable once). The video is the
        display's own (TM-4), not these frames."""
        red.subscribe(self._on_sample)
        frames = getattr(red, "subscribe_frames", None)
        if callable(frames):
            frames(self._on_frame)

    # -- the tip's shade (owner 2026-10-06, merged 2026-10-07) -------------------
    def _on_frame(self, t_s, frame, red=None):
        """One ACCEPTED (settled) region frame, on the analysis's run thread
        (`subscribe_frames`): handed to the trial's shade worker with its
        times, never measured here (the loop's rate is the measurement's).
        A full queue drops the frame and counts it."""
        trial = self._trial or self._arming
        if trial is None or trial.closed or trial.ended:
            return
        try:
            trial.shade_queue.put_nowait((time.monotonic(), t_s, frame))
        except queue.Full:
            trial.shade_dropped += 1

    def _start_shade(self, trial):
        worker = threading.Thread(target=self._shade_loop, args=(trial,),
                                  daemon=True, name="transfer-map-shade")
        trial.shade_worker = worker
        worker.start()

    def _stop_shade(self, trial):
        """Nothing more is fed; wait (bounded) for the worker to read what
        is queued, so Finish stores the shade of every frame. Never on the
        stop's thread (`_halt_hardware` only sets the flag)."""
        trial.shade_done.set()
        worker = trial.shade_worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(self.THREAD_JOIN_TIMEOUT)

    def _shade_loop(self, trial):
        queued = trial.shade_queue
        while True:
            try:
                when, run_t, frame = queued.get(timeout=0.05)
            except queue.Empty:
                if trial.shade_done.is_set():
                    return
                continue
            self._shade_frame(trial, when - trial.armed, frame, run_t)

    def _shade_frame(self, trial, t, frame, run_t=None):
        """The lab's per-frame wiring (its picture thread's `_write_frame`,
        here the shade worker's): the frame as RGB, its shade
        (`tip_shade.right_half_median_green`), `tracker.update(t, shade, z)`
        with `t` seconds since Arm and the probe's Z, and `tracker.mark()`
        on the first frame at or after the Mark. The shade is kept for the
        telemetry and, by the frame's analysis time, for its profile row."""
        try:
            shade = tip_shade.right_half_median_green(video.to_rgb(frame))
        except Exception as exc:
            events.debug("Shade Failed", repr(exc), source=self.NAME, every=5.0)
            return
        if shade is None:
            return
        trial.shade.update(t, shade, self._probe_axes()[2])
        trial.shades.append((trial.armed + t, round(shade, 2)))
        if run_t is not None:
            trial.shade_at[run_t] = shade
        mark = trial.operator_t
        if mark is not None and t >= mark:
            trial.shade.mark()

    @staticmethod
    def _profile_rows(trial):
        """The trial's samples as stored profile rows: (t_s, red, z, x, y,
        the five channels, the shade of the frame the row was measured on,
        or None)."""
        rows = []
        for sample in list(trial.samples):
            head, key = tuple(sample[:10]), (sample[10] if len(sample) > 10
                                             else None)
            head += (None,) * (10 - len(head))
            rows.append(head + (trial.shade_at.get(key),))
        return rows

    # -- the run's name (the lab, bench 2026-09-28) ------------------------------
    @staticmethod
    def _id_words(sample, chip, flake, cut, tip=None, trial_id=None):
        """`trial 12  tip T7  sample 7/27/26  chip 2  flake 13  cut 3`: the
        fields given, two spaces apart; "" if none."""
        parts = []
        if trial_id is not None:
            parts.append(f"trial {trial_id}")
        for word, value in (("tip", tip), ("sample", sample), ("chip", chip),
                            ("flake", flake), ("cut", cut)):
            if value:
                parts.append(f"{word} {value}")
        return "  ".join(parts)

    @staticmethod
    def _slug(value):
        """A folder-safe piece of a run name: letters, digits, `.` and `-`;
        anything else (a `/` in "9/27/26 Tip1", a space) becomes `-`."""
        return re.sub(r"[^A-Za-z0-9.-]+", "-", str(value or "")).strip("-")

    def run_label(self, trial_id, tip, sample, chip, flake, cut):
        """The analysis run's name for a trial, so its folder under the
        runs root sorts and reads: `trial012_tip-T7_sample-7-27-26_chip-2_
        flake-13_cut-3`, blank fields left out."""
        parts = [f"trial{int(trial_id):03d}"]
        for word, value in (("tip", tip), ("sample", sample), ("chip", chip),
                            ("flake", flake), ("cut", cut)):
            piece = self._slug(value)
            if piece:
                parts.append(f"{word}-{piece}")
        return "_".join(parts)

    def _name_run(self, red, trial_id, tip, sample, chip, flake, cut):
        """Name the run the trial records through (`label_run`), when the
        analysis can: never fails the trial."""
        label_run = getattr(red, "label_run", None)
        if not callable(label_run):
            return None
        try:
            return label_run(
                self.run_label(trial_id, tip, sample, chip, flake, cut),
                {"specimen_id": " ".join(p for p in (
                    f"sample {sample}" if sample else "",
                    f"chip {chip}" if chip else "",
                    f"flake {flake}" if flake else "") if p),
                 "consumable_id": tip,
                 "note": self._id_words(sample, chip, flake, cut,
                                        trial_id=trial_id)})
        except Exception as exc:
            events.debug("Run Label Failed", repr(exc), source=self.NAME)
            return None

    def mark_force(self):
        """The `live` -> `marked` step: the Mark's time, Z and speed, stamped
        at once. Again in `marked`, the Mark moves to now."""
        trial = self._trial
        if trial is None:
            raise Refused("No trial is armed.")
        if trial.ended:
            raise Refused(f"Trial {trial.id}'s recording has ended: there is "
                          "nothing left to mark.")
        trial.operator_t = time.monotonic() - trial.armed
        if trial.first_mark_t is None:
            trial.first_mark_t = trial.operator_t
        trial.z_mark = self._probe_axes()[2]
        speed = self._read_speed()[0]
        if speed is not None:
            trial.speed = speed      # the speed while lowering, at the Mark
        # TM-4: the video's next frame is flagged "mark" (frames.csv), and
        # the whole display is kept as it is now, on a worker: the Mark is
        # stamped first and never waits for a picture.
        trial.marks.append(trial.armed + trial.operator_t)
        recorder = trial.recorder
        if recorder is not None:
            try:
                recorder.mark("mark")
            except Exception as exc:
                events.debug("Mark Not Flagged", repr(exc), source=self.NAME,
                             exception=exc)
        self._want_mark_still(trial)
        events.info("Force Marked", f"Trial {trial.id}: force marked at "
                    f"{trial.operator_t:.2f} s.", source=self.NAME)
        self._touch()
        return round(trial.operator_t, 3)

    # -- TM-4: the trial's recording: the whole display and the telemetry ------
    def _start_capture(self, trial, folder):
        """When the region lands: one `ScreenRecorder` of the display the
        still came from, into the trial's folder, and one `TrialTelemetry`
        over the map's peers, both through the injected factories. A
        recorder that cannot start is an event, never a refusal: the
        profile is the measurement, the video the record."""
        with trial.capture_lock:
            if trial.closed or trial.ended:    # a stop got here first
                return
            factory = self._recorder_factory or _make_recorder
            try:
                trial.device = factory(folder, VIDEO_FPS, self.monitor)
                trial.device.start()
                trial.recorder = trial.device
            except Exception as exc:
                trial.video_error = str(exc) or type(exc).__name__
                events.debug("Video Not Started", repr(exc), source=self.NAME,
                             exception=exc)
                events.warn("No Video", f"Trial {trial.id} records without a "
                            f"video: {trial.video_error}. Its red-percent "
                            "profile is the measurement.", source=self.NAME)
            try:
                telemetry = (self._telemetry_factory or _make_telemetry)(
                    _Peers(self))
                telemetry.start(trial.id)
                trial.telemetry = telemetry
            except Exception as exc:
                events.debug("Telemetry Not Started", repr(exc),
                             source=self.NAME, exception=exc)
                events.warn("No Telemetry", f"Trial {trial.id} records "
                            "without its telemetry. Its red-percent profile is "
                            "the measurement.", source=self.NAME)

    def _stop_capture(self, trial, store=None):
        """Stop the trial's video and telemetry, once, each within its own
        bounded stop, and write what they left: the video columns from the
        `RecorderResult`, the telemetry to `telemetry.csv` beside it, the
        still at the last Mark. Never on the stop's own thread (End
        recording, Finish, Abort, or the stop's abort writer)."""
        store = store if store is not None else self._store
        with trial.capture_lock:
            if trial.recorder is not None and trial.video is None:
                try:
                    trial.video = trial.recorder.stop()
                except Exception as exc:
                    trial.video_error = str(exc) or type(exc).__name__
                    events.debug("Video Stop Failed", repr(exc),
                                 source=self.NAME, exception=exc)
                    events.warn("Video Not Closed", f"Trial {trial.id}'s video "
                                "did not close cleanly; its frames so far may "
                                "still play.", source=self.NAME)
            if trial.telemetry is not None and trial.telemetry_rows is None:
                try:
                    trial.telemetry_rows = list(trial.telemetry.stop())
                except Exception as exc:
                    trial.telemetry_rows = []
                    events.debug("Telemetry Stop Failed", repr(exc),
                                 source=self.NAME, exception=exc)
            worker = trial.mark_worker
        with trial.mark_lock:
            trial.mark_wanted = False
        if worker is not None:
            worker.join(self.THREAD_JOIN_TIMEOUT)
        fields = {}
        video = trial.video
        if video is not None:
            fields.update({
                "video_path": None if video.video_path is None else str(video.video_path),
                "video_index_path": (None if video.index_path is None
                                     else str(video.index_path)),
                "video_frames": int(video.frames),
                "video_dropped": int(video.dropped)})
        mark = self._folder_of(trial, store) / "mark_full.png"
        if mark.is_file():
            fields["mark_full_path"] = str(mark)
        for write in (lambda: self._write_telemetry(trial, store),
                      lambda: store.update(trial.id, fields) if fields else None):
            try:
                write()
            except Exception as exc:
                events.debug("Recording Not Kept", repr(exc), source=self.NAME,
                             exception=exc)
                events.warn("Recording Not Kept", f"Trial {trial.id}'s video "
                            "columns or telemetry could not be written; its "
                            "files are in its folder.", source=self.NAME)
        self._touch()

    def _folder_of(self, trial, store):
        """The trial's pictures folder in `store` (a stop's writer keeps the
        store the trial was armed in)."""
        path = Path(store.path) if store.path is not None else self.db_path
        return path.parent / path.stem / str(trial.id)

    def _write_telemetry(self, trial, store):
        """`telemetry.csv` (t, stream, value): the telemetry's rows plus
        the map's own, on the same monotonic clock as the video's frames.csv:
        `transfer_map.armed` (time zero of the profile's t_s, value the
        trial id) and one `transfer_map.mark` per Mark."""
        rows = list(trial.telemetry_rows or ())
        if trial.telemetry is None and not trial.marks and not trial.shades:
            return
        rows.append((trial.armed, "transfer_map.armed", trial.id))
        rows += [(t, "transfer_map.mark", round(t - trial.armed, 6))
                 for t in trial.marks]
        # The tip's shade per region frame (owner 2026-10-06): the record a
        # later re-estimate reads.
        rows += [(t, "transfer_map.shade", shade) for t, shade in trial.shades]
        rows.sort(key=lambda row: row[0])
        folder = self._folder_of(trial, store)
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / TELEMETRY_NAME, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(TELEMETRY_COLUMNS)
            for t, stream, value in rows:
                writer.writerow([f"{float(t):.6f}", stream, value])

    def _want_mark_still(self, trial):
        """Ask for the still at the Mark; start its worker if none runs."""
        if trial.device is None:
            return
        with trial.mark_lock:
            trial.mark_wanted = True
            if trial.mark_worker is not None:
                return
            trial.mark_worker = threading.Thread(
                target=self._mark_still_loop, args=(trial,), daemon=True,
                name="transfer-map-mark-still")
        trial.mark_worker.start()

    def _mark_still_loop(self, trial):
        """The whole display at the (last) Mark, as `mark_full.png` (the v3
        column): full resolution, through the recorder's device, written
        beside and then moved over the old one, so a reader never sees half
        a file."""
        folder = self.pictures_root / str(trial.id)
        while True:
            with trial.mark_lock:
                if not trial.mark_wanted or trial.ended or trial.closed:
                    trial.mark_worker = None
                    return
                trial.mark_wanted = False
            part = folder / "mark_full.part.png"
            try:
                trial.device.capture_still(part)
                os.replace(part, folder / "mark_full.png")
            except Exception as exc:
                self._unlink(part)
                events.debug("Mark Still Failed", repr(exc), source=self.NAME,
                             exception=exc)
                events.warn("No Mark Picture", f"Trial {trial.id} has no "
                            f"picture of the display at its Mark ({exc}); the "
                            "Mark itself is stamped.", source=self.NAME)
            self._touch()

    def end_recording(self):
        """The `marked` -> `finish` step: everything that records the trial
        stops (the rows, the run it started, the video), so the operator
        reviews what was recorded before keeping it with Finish (or Abort).
        The trial is still open: its row is written by Finish or Abort."""
        trial = self._trial
        if trial is None:
            raise Refused("No trial is armed.")
        if trial.ended:
            raise Refused(f"Trial {trial.id}'s recording has already ended.")
        self._end_recording(trial)
        events.info("Recording Ended", f"Trial {trial.id}: "
                    f"{len(trial.samples)} samples. Review it, then Finish "
                    "trial keeps it.", source=self.NAME)
        self._touch()
        return trial.id

    def _end_recording(self, trial):
        """Stop what records `trial`, once: no row is kept after this, the
        run it started ends, its video closes (bounded). On the command
        thread (End recording, Finish, Abort), never the stop's."""
        if trial.ended:
            return
        trial.ended = True
        self._release_red()
        self._stop_shade(trial)
        self._end_own_run(trial)
        self._stop_capture(trial)

    def finish_trial(self, confirmed=False):
        """Keep the trial ("recorded"), then back to `setup`. Shown in the
        `finish` step; from an earlier one it ends the recording first."""
        armed = self._trial
        if armed is None:
            raise Refused("No trial is armed.")
        if not confirmed:
            words = (f"Continue keeps trial {armed.id}." if armed.ended else
                     f"Continue ends trial {armed.id} and closes its video.")
            raise NeedsConfirm(words, "finish_trial",
                               inputs={"note": self.note or ""})
        trial = self._claim()
        if trial is None:
            raise Refused("No trial is armed.")          # the stop took it
        self._end_recording(trial)
        samples = self._profile_rows(trial)
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
        # The lab's tip-shade force (owner 2026-10-06): the frame at the
        # Mark decides it; all NULL with no contact before the Mark, or no
        # frames (`tip_shade.columns`).
        fields.update(tip_shade.columns(trial.shade))
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
        """From `region` on. In `region` a disarm: nothing was recorded and
        nothing is stored (the stage still is deleted). Later the trial is
        kept as "aborted", with its profile and video."""
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is not None:
            self._discard_still(pending)
            events.info("Trial Disarmed", "Nothing was recorded: the trial was "
                        "aborted before its capture region was picked.",
                        source=self.NAME)
            self._touch()
            return None
        trial = self._claim()
        if trial is None:
            raise Refused("No trial is armed.")
        self._release_red()
        trial.shade_done.set()
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
            self._stop_capture(trial, store)
            self._stop_shade(trial)        # on the writer, never the stop's
            samples = self._profile_rows(trial)
            store.update(trial.id, {
                "status": "aborted", "mark_operator_t": trial.operator_t,
                "z_contact": trial.z_mark, "broke": int(trial.broke)}, samples)
            self._indices.pop(trial.id, None)
            self._changed()
            events.info("Trial Aborted", f"Trial {trial.id} was aborted; its "
                        f"{len(samples)} samples are kept.", source=self.NAME)
        except Exception as exc:
            events.error("Trial Not Saved", f"Trial {trial.id} was aborted but "
                         "could not be saved. Check the trial store's folder.",
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

    def _warn_no_full(self, trial_id, moment, which):
        """ "Trial 4 has no whole-screen picture at Arm; its video is kept." """
        events.warn("No Full Picture", f"Trial {trial_id} has no whole-screen "
                    f"picture at {moment}; its video is kept. Check that the "
                    "pictures folder can be written.", source=self.NAME)

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
    def mark_full_image(self):
        """The whole display at the (last) Mark: the armed trial's, else the
        last trial's; b"" before one is taken."""
        return self._picture("mark_full")

    # -- the trial row's readouts (approved proposal 2026-10-07) ---------------
    @property
    def force_estimate(self):
        """The trial row's Force estimate, from the tip's SHADE (owner
        ruling 2026-10-07: the lab's estimator, `model.tip_shade`, is THE
        force model; the red extrema are an analysis factor, never shown as
        force): `tip_shade.status_text(status)` and, once known, the
        position on the shade's peak (0 at the peak, 1 back at its
        baseline): "No contact", "Contact · 0.04", "Medium force · 0.43".
        "" (blank) outside a recording; "Unsettled" while the analysis is
        rejecting frames (the settle gate's counters, `analysis_health`).
        Reads what the shade worker computed: no analysis on a poll."""
        trial = self._trial
        if trial is None or trial.ended:
            return ""
        if self.analysis_health == "unsettled":
            return "Unsettled"
        tracker = trial.shade
        words = tip_shade.status_text(tracker.status)
        position = tracker.position
        return words if position is None else f"{words} · {position:.2f}"

    @staticmethod
    def _region_fields(region, bounds):
        """The trial row's region and display columns from a region (a dict
        with left, top, width, height, or a 4-tuple) and the display's
        bounds (`_Pending.bounds`, or None)."""
        if isinstance(region, (tuple, list)):
            region = dict(zip(("left", "top", "width", "height"), region))
        fields = {}
        for name in ("left", "top", "width", "height"):
            for prefix, source in (("region", region), ("display", bounds)):
                try:
                    value = (source or {}).get(name)
                    fields[f"{prefix}_{name}"] = (None if value is None
                                                  else int(value))
                except (TypeError, ValueError, AttributeError):
                    fields[f"{prefix}_{name}"] = None
        return fields

    @property
    def force_position_text(self):
        """Diagnostics (the lab's): where the shade is on its peak (0 at the
        peak, 1 back at its baseline) and the numbers behind it."""
        trial = self._trial
        if trial is None or trial.shade.position is None:
            return ""
        tracker = trial.shade
        words = f"{tracker.position:.2f} of the way down from the peak"
        numbers = (tracker.peak, tracker.shade, tracker.baseline)
        if any(n is None for n in numbers):
            return words
        return words + " (peak {:.0f}, now {:.0f}, baseline {:.0f})".format(*numbers)

    @property
    def analysis_health(self):
        """One word for the procedure strip, from Red Percent's published
        state: "no region" (no Red Percent, or no capture region), "stalled"
        (a run that reads nothing, or no run while recording), "unsettled"
        (most reads rejected), "settled"; "" while no run is expected
        (setup, the tip prompt, review).

        UX audit 2026-10-08 #15: "no region" only from the step where a
        region is due (Capture region, then the recording); in Setup, step
        1, it was a warning about something the operator cannot do yet."""
        if self.phase not in ("region", "live", "marked"):
            return ""
        red = self._red
        if red is None or not getattr(red, "region", None):
            return "no region"
        run = getattr(red, "run_token", None)
        if run is None:
            return "stalled" if self.phase in ("live", "marked") else ""

        def count(name):
            try:
                return int(getattr(red, name, 0) or 0)
            except (TypeError, ValueError):
                return 0
        rejected = (count("rejected_black") + count("rejected_stale")
                    + count("rejected_unsettled"))
        return self._health.observe(run, time.monotonic(),
                                    count("frames_accepted"), rejected)

    @property
    def video_word(self):
        """The trial row's Video, one word: "Recording" while the display's
        recorder runs, else "Stopped". Its frames and drops are the trial
        row's columns, `frames.csv` and Diagnostics' (`video_status`)."""
        trial = self._trial
        recorder = trial.recorder if trial is not None else None
        if recorder is None or trial.ended or trial.video is not None:
            return "Stopped"
        if not getattr(recorder, "is_recording", True):
            return "Stopped"
        stats = getattr(recorder, "stats", None) or {}
        return "Stopped" if stats.get("encoder_error") else "Recording"

    @property
    def trial_samples(self):
        """Diagnostics: the armed trial's red-percent rows (and those past
        MAX_SAMPLES, not kept); blank between trials."""
        trial = self._trial
        if trial is None:
            return None
        n = len(trial.samples)
        return f"{n} (+{trial.dropped} not kept)" if trial.dropped else str(n)

    @property
    def video_status(self):
        """ "recording, 312 frames" / "screen.mp4, 1240 frames, 3 dropped" /
        "no video: <why>"; the last trial's between trials (Diagnostics)."""
        trial = self._trial
        if trial is not None:
            video = trial.video
            if video is not None:
                name = "no file" if video.video_path is None else Path(video.video_path).name
                return f"{name}, {video.frames} frames, {video.dropped} dropped"
            if trial.recorder is not None:
                stats = getattr(trial.recorder, "stats", None) or {}
                frames = stats.get("frames", 0)
                dropped = sum(stats.get(k, 0) for k in (
                    "dropped_full", "dropped_size", "dropped_encoder"))
                return f"recording, {frames} frames" + (
                    f", {dropped} dropped" if dropped else "")
            if trial.video_error:
                return f"no video: {trial.video_error}"
            return "no video yet"
        last = self._store.last()
        if last is None:
            return "No video yet."
        if last.get("video_frames") is None:
            return "No video for this trial."
        path = last.get("video_path")
        frames, dropped = last["video_frames"], last.get("video_dropped") or 0
        if not path:
            return "no frames were recorded"
        return f"{Path(path).name}, {frames} frames, {dropped} dropped"

    @property
    def video_encoder(self):
        """Which way this station records a trial's video (Diagnostics)."""
        if self._encoder is None:
            try:
                exe = video.ffmpeg_exe()
                self._encoder = (f"H.264 MP4 of the whole display via "
                                 f"{Path(exe).name}")
            except Exception as exc:
                self._encoder = (f"No encoder ({exc}): trials record without "
                                 "a video")
        return self._encoder

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
        """The trial's tip, in a word or four: "new", "in use since trial 3",
        "broke on trial 12", "retired". None (blank) while no tip is picked."""
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

    #: What a tip's line says while it has no model on record.
    NO_MODEL = "no model"

    @classmethod
    def tip_label(cls, record):
        """A tip as the Tip dropdown shows it: "T7 · TAP300 · 3 trials",
        "T8 · TAP300 · 1 trial", "T3 · TAP300 · 5 trials · retired",
        "T9 · no model · 0 trials" (`record` from `TrialStore.tips`)."""
        n = int(record["count"])
        model = (record.get("model") or "").strip() or cls.NO_MODEL
        label = (f"{record['tip_id']} · {model} · "
                 f"{n} trial{'' if n == 1 else 's'}")
        return label + " · retired" if record["retired_at"] else label

    @property
    def tip_options(self):
        """The Tip dropdown: every tip on record, in the order they were
        created, the retired ones last (bench 2026-09-28: the operator could
        not see the tips that existed; 2026-10-07: the only tip control)."""
        records = self._store.tips()
        in_use = [r for r in records if not r["retired_at"]]
        retired = [r for r in records if r["retired_at"]]
        return [self.tip_label(r) for r in in_use + retired]

    @property
    def tip_pick(self):
        """The trial's tip as its dropdown line; "" (nothing chosen) while
        no tip is picked or the tip has no record yet."""
        tip = (self.tip_id or "").strip()
        record = self._tip_record(tip)
        return self.tip_label(record) if record is not None else ""

    def pick_tip(self, label):
        """The Tip dropdown: the tip on that line becomes the trial's tip.
        Its line or its bare ID are accepted."""
        for record in self._store.tips():
            if label in (self.tip_label(record), record["tip_id"]):
                self.tip_id = record["tip_id"]
                self._changed()
                return record["tip_id"]
        raise Refused(f"{label!r} is not a tip on record. Press New tip… to "
                      "add it.")

    def new_tip(self):
        """New tip…: the `setup` -> `new_tip` step, the prompt for a tip
        record made on demand before any trial (bench 2026-09-28: "I can't
        create new tips"). Nothing is written until Add tip."""
        self._need_store()
        if self.is_armed or self._pending is not None:
            raise Refused("A trial is armed. Finish or abort it first.")
        self.new_tip_id = ""
        self._adding_tip = True
        self._touch()
        return None

    def add_tip(self):
        """Add tip: the typed ID becomes a tip record of the picked model
        (created now, no trial yet), it is picked for the trial, and the
        step returns to setup. An empty ID, one already on record, or no
        model is refused and the prompt stays."""
        self._need_store()
        tip = (self.new_tip_id or "").strip()
        if not tip:
            raise Refused("Type the new tip's ID, then press Add tip.")
        if self._store.tip(tip) is not None:
            raise Refused(f"{tip} already exists. Choose it from the list or "
                          "type a different ID.")
        model = self._known_model(self._new_tip_model)
        if model is None:
            raise Refused("Pick the tip's model (or add a new one under New "
                          "model), then press Add tip.")
        self._store.create_tip(tip, _now(), model)
        self.tip_id, self.new_tip_id = tip, ""
        self._adding_tip = False
        self._changed()
        events.info("Tip Created", f"Tip {tip} ({model}) created.",
                    source=self.NAME)
        return tip

    # -- the tip's model (owner, 2026-10-07) -------------------------------------
    @property
    def tip_model_options(self):
        """The tip models on record (`tip_models`), in the order added."""
        return self._store.tip_models()

    def _known_model(self, name):
        """`name` as the list spells it (trimmed, case-insensitive), or None."""
        wanted = str(name or "").strip().lower()
        return next((m for m in self.tip_model_options
                     if wanted and m.lower() == wanted), None)

    @property
    def new_tip_model(self):
        """The New tip prompt's Model dropdown."""
        return self._new_tip_model

    def set_new_tip_model(self, name):
        found = self._known_model(name)
        if found is None:
            raise Refused(f"{name!r} is not a tip model: pick one, or add it "
                          "under New model.")
        self._new_tip_model = found
        self._touch()
        return found

    def add_tip_model(self):
        """Add model: the typed name joins the model list and is picked."""
        self._need_store()
        name = (self.new_model_name or "").strip()
        if not name:
            raise Refused("Type the new model's name first.")
        name = self._store.add_tip_model(name)
        self._new_tip_model, self.new_model_name = name, ""
        self._changed()
        events.info("Tip Model Added", f"Tip model {name} added.",
                    source=self.NAME)
        return name

    @property
    def tip_model(self):
        """The picked tip's model (the Tip section's dropdown); "" without
        one."""
        record = self._tip_record((self.tip_id or "").strip())
        return (record.get("model") or "") if record is not None else ""

    def set_tip_model(self, tip_or_model, model=None):
        """Correct a tip's model label: `set_tip_model(tip_id, model)`, or,
        as the Tip section's dropdown sends it, `set_tip_model(model)` for
        the picked tip. The model must be on the list."""
        self._need_store()
        if model is None:
            tip, model = (self.tip_id or "").strip(), tip_or_model
        else:
            tip = str(tip_or_model or "").strip()
        if not tip:
            raise Refused("Pick the tip first (Tip, on the trial's setup).")
        if self._tip_record(tip) is None:
            raise Refused(f"Tip {tip} has no record yet: it is created when a "
                          "trial is armed on it.")
        found = self._known_model(model)
        if found is None:
            raise Refused(f"{model!r} is not a tip model: add it under New "
                          "model first.")
        self._store.set_tip(tip, {"model": found})
        self._changed()
        events.info("Tip Model Set", f"Tip {tip}: model {found}.",
                    source=self.NAME)
        return found

    def cancel_new_tip(self):
        """Cancel: back to setup; nothing is written."""
        self._adding_tip = False
        self.new_tip_id = ""
        self._touch()
        return None

    def _picked_tip(self):
        """(tip, record) for the trial's tip, or Refused naming what is
        missing."""
        tip = (self.tip_id or "").strip()
        if not tip:
            raise Refused("Pick the tip first (Tip, on the trial's setup).")
        record = self._tip_record(tip)
        if record is None:
            raise Refused(f"Tip {tip} has no record yet: it is created when a "
                          "trial is armed on it.")
        return tip, record

    def retire_tip(self, confirmed=False):
        self._need_store()
        tip, record = self._picked_tip()
        if record["retired_at"]:
            raise Refused(f"Tip {tip} is already retired.")
        armed = self._trial or self._pending
        if armed is not None and armed.tip == tip:
            raise Refused(f"A trial is armed on tip {tip}. Finish or abort it "
                          "first.")
        if not confirmed:
            n = record['count']
            raise NeedsConfirm(f"Retire tip {tip}? Its {_count(n, 'trial')} "
                               f"{'is' if n == 1 else 'are'} kept; arming on "
                               "it later asks first.", "retire_tip")
        self._store.set_tip(tip, {"retired_at": _now()})
        self._changed()
        events.info("Tip Retired", f"Tip {tip} retired after "
                    f"{_count(record['count'], 'trial')}.", source=self.NAME)
        return tip

    def unretire_tip(self):
        self._need_store()
        tip, record = self._picked_tip()
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
            raise Refused("Pick the tip first (Tip, on the trial's setup).")
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
            model = (tip.get("model") or "").strip() or self.NO_MODEL
            lines.append(f"{tip['tip_id']}  {model}  {_count(tip['count'], 'trial')}, "
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
            raise Refused(f"No trial {trial_id} in the trial store.")
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

    def set_trial_invalid(self, invalid=True):
        """Mark a recorded trial invalid (or valid again), the lab's flag:
        an invalid trial stays on record and in the export, off the map and
        out of every analysis (`_map_rows`, `rebuild_force`)."""
        self._need_store()
        trial_id = int(self.afm_trial_id or 0)
        if trial_id <= 0:
            raise Refused("Type the trial number under AFM measurement, Trial.")
        row = self._store.trial(trial_id)
        if row is None:
            raise Refused(f"No trial {trial_id} in the trial store.")
        if row["status"] == "armed":
            raise Refused(f"Trial {trial_id} is still armed. Finish it first.")
        flag = bool(invalid) and str(invalid).lower() not in ("false", "0")
        self._store.update(trial_id, {"invalid": int(flag)})
        self._indices.pop(trial_id, None)
        self._changed()
        events.info("Trial Invalid" if flag else "Trial Valid",
                    f"Trial {trial_id} is " + ("invalid: it stays on record, "
                    "off the map." if flag else "valid again: it is back on "
                    "the map."), source=self.NAME)
        return trial_id

    # -- the tip-shade force, again from the footage (the lab, 2026-10-06) -------
    #: The full-display recorder's frame index (`devices.screen_recorder`).
    DISPLAY_INDEX = "frames.csv"

    def _footage_of(self, row):
        """(video path, index path) of a recorded trial's footage: what its
        row names, else where the map kept them; None for what is not on
        disk. The index is the region recorder's `video_index.csv` (trials
        recorded before 2026-10-07: the region with a label band) or the
        full-display recorder's `frames.csv` (since: the whole display)."""
        folder = self.pictures_root / str(row["id"])

        def found(stored, *fallbacks):
            for candidate in ([stored] if stored else []) + list(fallbacks):
                if candidate and Path(candidate).exists():
                    return str(candidate)
            return None
        index = found(row.get("video_index_path"), folder / "video_index.csv",
                      folder / self.DISPLAY_INDEX)
        video_path = found(row.get("video_path"), folder / "trial.mp4",
                           folder / "frames", folder / "screen.mp4")
        return video_path, index

    def _rebuilt_shade(self, row):
        """The six tip-shade columns of one recorded trial, rebuilt by the
        lab's `shade_offline.estimate_from_footage` (unchanged) from, in
        order: its REGION video (the lab's case: the label band stripped by
        `image_top`); its FULL-DISPLAY video, the stored capture region
        cropped out of every frame (no band to strip), each frame's shade
        handed to the lab's reader as a stored `shade` column; else the
        shade stored per frame (`telemetry.csv`'s `transfer_map.shade`,
        else the profile's `shade`). None when there is nothing to read."""
        mark_t = row.get("mark_operator_t")
        footage, index = self._footage_of(row)
        if index is not None and Path(index).name != self.DISPLAY_INDEX:
            return shade_offline.estimate_from_footage(footage, index, mark_t)
        series = None
        if index is not None and footage is not None and Path(footage).exists():
            series = self._display_shades(row, footage, index, mark_t)
        if not series:
            series = self._stored_shades(row)
        if not series:
            return None
        z_at = self._z_lookup(row["id"])
        with tempfile.TemporaryDirectory(prefix="shade-") as folder:
            path = Path(folder) / "shade_index.csv"
            with open(path, "w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(("t_s", "z", "shade"))
                for t, shade in series:
                    writer.writerow((round(t, 4), z_at(t), round(shade, 2)))
            return shade_offline.estimate_from_footage(None, path, mark_t)

    def _armed_at(self, folder):
        """The trial's time zero on the monotonic clock (`telemetry.csv`'s
        `transfer_map.armed`), or None."""
        path = folder / TELEMETRY_NAME
        if not path.is_file():
            return None
        with open(path, newline="") as handle:
            for line in csv.DictReader(handle):
                if line.get("stream") == "transfer_map.armed":
                    return _number(line.get("t"))
        return None

    @staticmethod
    def _crop_box(row, frame_shape):
        """(y0, y1, x0, x1) of the stored capture region in a full-display
        frame: desktop coordinates, less the display's place, scaled by the
        frame's pixels per display point (a Retina display records twice
        its points). None without a stored region."""
        region = [row.get(f"region_{k}") for k in ("left", "top", "width", "height")]
        if any(v is None for v in region):
            return None
        left, top, width, height = (float(v) for v in region)
        height_px, width_px = frame_shape[:2]
        display = [row.get(f"display_{k}") for k in ("left", "top", "width", "height")]
        if all(v is not None for v in display) and display[2] and display[3]:
            sx, sy = width_px / float(display[2]), height_px / float(display[3])
            left, top = left - float(display[0]), top - float(display[1])
        else:
            sx = sy = 1.0                 # the still's pixels are the desktop's
        x0 = max(0, int(round(left * sx)))
        y0 = max(0, int(round(top * sy)))
        x1 = min(width_px, int(round((left + width) * sx)))
        y1 = min(height_px, int(round((top + height) * sy)))
        return (y0, y1, x0, x1) if x1 > x0 and y1 > y0 else None

    def _display_shades(self, row, footage, index, mark_t):
        """[(t since Arm, shade)] from a full-display video: each frame's
        capture region, cropped, through `tip_shade.right_half_median_green`
        (no label band: `image_top` is not applied); frames after the first
        at or past the Mark are not decoded. None without a stored region."""
        with open(index, newline="") as handle:
            times = [_number(line.get("t_monotonic"))
                     for line in csv.DictReader(handle)]
        if not times or times[0] is None:
            return None
        zero = self._armed_at(Path(index).parent) or times[0]
        series, box = [], None
        frames = video.read_frames(footage)
        try:
            for i, frame in enumerate(frames):
                if i >= len(times) or times[i] is None:
                    break
                if box is None:
                    box = self._crop_box(row, frame.shape)
                    if box is None:
                        return None
                y0, y1, x0, x1 = box
                shade = tip_shade.right_half_median_green(frame[y0:y1, x0:x1])
                if shade is None:
                    continue
                t = times[i] - zero
                series.append((t, shade))
                if mark_t is not None and t >= mark_t:
                    break
        finally:
            close = getattr(frames, "close", None)
            if callable(close):
                close()
        return series

    def _stored_shades(self, row):
        """[(t since Arm, shade)] the trial stored while recording: every
        frame's (`telemetry.csv`, `transfer_map.shade`), else its profile
        rows' (`shade`); [] for none."""
        folder = self.pictures_root / str(row["id"])
        path = folder / TELEMETRY_NAME
        zero = self._armed_at(folder)
        if zero is not None:
            with open(path, newline="") as handle:
                series = [(_number(line["t"]) - zero, _number(line["value"]))
                          for line in csv.DictReader(handle)
                          if line.get("stream") == "transfer_map.shade"]
            series = [(t, s) for t, s in series if s is not None]
            if series:
                return series
        rows = self._store.read("SELECT t_s, shade FROM profile WHERE "
                                "trial_id = ? AND shade IS NOT NULL ORDER BY "
                                "t_s, rowid", (row["id"],))
        return [(r["t_s"], r["shade"]) for r in rows]

    def _z_lookup(self, trial_id):
        """t (s since Arm) -> the profile's Z at or before it (None without
        one): the Z `contact_lowered` is read from."""
        import bisect
        profile = self._store.profile(trial_id)
        pairs = [(t, z) for t, z in zip(profile["t"], profile.get("z") or ())
                 if z is not None]
        times = [t for t, _z in pairs]

        def z_at(t):
            i = bisect.bisect_right(times, t) - 1
            return pairs[i][1] if i >= 0 else (pairs[0][1] if pairs else None)
        return z_at

    def rebuild_force(self, trial_id=0):
        """Recompute the tip-shade force columns of recorded trials from
        their footage or their stored shade (`_rebuilt_shade`). With no
        trial, every valid recorded trial (not invalid, not aborted, not
        imported); with one, that trial whatever its flags. Returns `{trial:
        columns}` for what was rebuilt; a trial with nothing to read is
        skipped and said so."""
        self._need_store()
        trial_id = int(trial_id or 0)
        rows = self._store.trials()
        if trial_id:
            rows = [r for r in rows if r["id"] == trial_id]
            if not rows:
                raise Refused(f"No trial {trial_id} in the trial store.")
        else:
            rows = [r for r in rows if r["status"] in ("recorded", "measured")
                    and r.get("origin") == "recorded" and not r.get("invalid")]
        rebuilt = {}
        for row in rows:
            try:
                fields = self._rebuilt_shade(row)
            except (OSError, RuntimeError, ValueError, KeyError) as exc:
                events.warn("Footage Not Read", f"Trial {row['id']}: {exc}",
                            source=self.NAME, exception=exc)
                continue
            if fields is None:
                events.warn("No Footage", f"Trial {row['id']} has no video "
                            "or stored shade on disk; its force was not "
                            "rebuilt.", source=self.NAME)
                continue
            self._store.update(row["id"], fields)
            self._indices.pop(row["id"], None)
            rebuilt[row["id"]] = fields
        if rebuilt:
            self._changed()
        events.info("Force Rebuilt", f"{_count(len(rebuilt), 'trial')} rebuilt from "
                    "their footage: " + (", ".join(
                        f"{i} {f['force_class'] or 'no force'}"
                        for i, f in rebuilt.items()) or "none") + ".",
                    source=self.NAME)
        return rebuilt

    # -- the data finalizer (the lab, owner 2026-10-06) --------------------------
    # One trial at a time, sample by sample, a form for the AFM and optical
    # estimates beside its pictures. Post-AFM data entry, not the review of
    # the procedure (that is the `finish` step). The window was the retired
    # Qt view's; the commands stay declared `internal` (no view draws them)
    # so a later view can open the same walk; rules in `model/finalize.py`.
    def open_finalizer(self):
        """The word a view with a finalizer window acts on."""
        return "open:finalizer"

    def finalize_queue(self, only_missing=False):
        """The trials to walk, in sample order (`finalize.queue`)."""
        return finalize.queue(self._store.trials(),
                              only_missing=bool(only_missing))

    def finalize_media(self, trial_id):
        """What the window shows beside the form: the stills (PNG bytes, b""
        when there is none) and where the video is. `video_kind` is "file"
        (an MP4), "frames" (a folder of JPEGs, `frame_paths` in order) or
        "none"."""
        row = self._store.trial(int(trial_id or 0))
        if row is None:
            raise Refused(f"No trial {trial_id} in the trial store.")
        folder = self.pictures_root / str(row["id"])

        def still(name):
            try:
                path = folder / f"{name}.png"
                return path.read_bytes() if path.is_file() else b""
            except OSError:
                return b""
        media = {"id": row["id"], "first_frame": still("first_frame"),
                 "mark_frame": still("mark_frame"),
                 "before_full": still("before_full"),
                 "video_path": "", "video_kind": "none", "frame_paths": [],
                 "frames": row.get("video_frames") or 0,
                 "row": dict(row)}
        stored = row.get("video_path")
        if stored:
            path = Path(stored)
            if path.is_file():
                media["video_path"], media["video_kind"] = str(path), "file"
            elif path.is_dir():
                media["video_path"], media["video_kind"] = str(path), "frames"
                media["frame_paths"] = sorted(str(p) for p in path.glob("*.jpg"))
        return media

    def finalize_save(self, trial_id, fields):
        """Write the estimates typed for one trial: AFM, optical, both, or
        none (nothing typed writes nothing). All or nothing: one bad box
        refuses the whole save. An AFM width makes the trial `measured`, an
        optical one never does, as in `attach_afm` / `attach_optical`."""
        trial_id = int(trial_id or 0)
        row = self._store.trial(trial_id)
        if row is None:
            raise Refused(f"No trial {trial_id} in the trial store.")
        if row["status"] == "armed":
            raise Refused(f"Trial {trial_id} is still armed. Finish it first.")
        try:
            updates = finalize.parse_fields(fields or {}, WIDTH_OPTICAL_METHODS,
                                            current=row)
        except ValueError as exc:
            raise Refused(str(exc)) from None
        if updates:
            if "width_optical_um" in updates and not row.get("width_optical_method") \
                    and "width_optical_method" not in updates:
                updates["width_optical_method"] = "estimate"      # typed by eye
            updates["status"] = finalize.status_after(row, updates)
            self._store.update(trial_id, updates)
            self._changed()
            events.info("Trial Finalized", f"Trial {trial_id}: "
                        f"{', '.join(k for k in updates if k != 'status')} saved.",
                        source=self.NAME)
        return {"id": trial_id, "missing": finalize.missing(self._store.trial(trial_id))}

    def set_trial_speed(self):
        """Correct a recorded trial's intended speed from the sheet."""
        self._need_store()
        trial_id = int(self.afm_trial_id or 0)
        if trial_id <= 0:
            raise Refused("Type the trial number under AFM measurement, Trial.")
        row = self._store.trial(trial_id)
        if row is None:
            raise Refused(f"No trial {trial_id} in the trial store.")
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
            raise Refused(f"No trial {trial_id} in the trial store.")
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
            "channel_height_nm": given(self.channel_height_nm),
            "channel_height_sigma_nm": given(self.channel_height_sigma_nm),
            "trench_depth_nm": given(self.trench_depth_nm),
            "trench_depth_sigma_nm": given(self.trench_depth_sigma_nm),
            "status": "aborted" if row["status"] == "aborted" else "measured"})
        self._changed()
        parts = [f"width {self.width_um:g} um"]
        if given(self.channel_height_nm) is not None:
            parts.append(f"channel height {self.channel_height_nm:g} nm")
        if given(self.trench_depth_nm) is not None:
            parts.append(f"trench depth {self.trench_depth_nm:g} nm")
        events.info("AFM Attached", f"Trial {trial_id}: {', '.join(parts)} "
                    "attached.", source=self.NAME)
        return trial_id

    def attach_optical(self):
        """Store v6 (Q19): the approximate channel width by optical
        microscopy. It never makes a trial `measured`: that stays "an AFM
        width exists", so the 3D map's filled marker keeps its meaning."""
        trial_id = int(self.afm_trial_id or 0)
        if trial_id <= 0:
            raise Refused("Type the trial number the optical width belongs to.")
        row = self._store.trial(trial_id)
        if row is None:
            raise Refused(f"No trial {trial_id} in the trial store.")
        if row["status"] == "armed":
            raise Refused(f"Trial {trial_id} is still armed. Finish it first.")
        if not self.width_optical_um or self.width_optical_um <= 0:
            raise Refused("Type the channel width read by optical microscopy.")
        sigma = self.width_optical_sigma_um
        self._store.update(trial_id, {
            "width_optical_um": float(self.width_optical_um),
            "width_optical_sigma_um": float(sigma) if sigma else None,
            "width_optical_method": self._width_optical_method})
        self._changed()
        events.info("Optical Width Attached", f"Trial {trial_id}: optical width "
                    f"{self.width_optical_um:g} um ({self._width_optical_method}) "
                    "attached.", source=self.NAME)
        return trial_id

    @property
    def width_optical_method(self):
        return self._width_optical_method

    @property
    def width_optical_method_options(self):
        return list(WIDTH_OPTICAL_METHODS)

    def set_width_optical_method(self, method):
        if method not in WIDTH_OPTICAL_METHODS:
            raise Refused(f"{method!r} is not an optical width method.")
        self._width_optical_method = method
        self._touch()
        return method

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

    # -- the sample, chip and flake (approved proposal 2026-10-07) -------------
    #: The setup step's Next step and Arm's refusal while the Sample DB
    #: offers no sample (no Sample DB open, no store, or an empty one).
    SAMPLE_FIRST = "Add a sample in the Sample DB first"
    PICK_FLAKE = "Pick the sample, chip and flake"

    def _where(self):
        return (self._sample, self._chip, self._flake)

    def _samples_store(self):
        """The Sample DB's store, read-only, or None (no Sample DB open,
        or no store at its path). Opened per read: the file may appear,
        move or go while the station runs."""
        path = self._sample_db
        if path is None:
            return None
        try:
            return (self._sample_store_factory or _open_samples)(path)
        except Exception as exc:
            events.debug("Sample Store Not Opened", repr(exc),
                         source=self.NAME, every=5.0)
            return None

    def _sample_rows(self, what, *ids):
        """`store.<what>(*ids)` as a list, [] when there is no store or it
        cannot be read (the pickers then offer nothing; Next step says
        why)."""
        store = self._samples_store()
        if store is None:
            return []
        try:
            return list(getattr(store, what)(*ids))
        except Exception as exc:
            events.debug("Sample Store Not Read", repr(exc),
                         source=self.NAME, every=5.0)
            return []

    @property
    def sample_options(self):
        return [str(r["sample_id"]) for r in self._sample_rows("samples")]

    @property
    def chip_options(self):
        if not self._sample:
            return []
        return [str(r["chip_id"]) for r in self._sample_rows("chips", self._sample)]

    @property
    def flake_options(self):
        if not (self._sample and self._chip):
            return []
        return [str(r["flake_id"])
                for r in self._sample_rows("flakes", self._sample, self._chip)]

    @property
    def has_sample_pick(self):
        """A sample is chosen: the Chip dropdown is live (`enabled_by`)."""
        return bool(self._sample)

    @property
    def has_chip_pick(self):
        """A chip is chosen: the Flake dropdown is live (`enabled_by`)."""
        return bool(self._sample and self._chip)

    @property
    def sample_pick(self):
        return self._sample or ""

    @property
    def chip_pick(self):
        return self._chip or ""

    @property
    def flake_pick(self):
        return self._flake or ""

    # -- the picked level's picture (owner 2026-10-08) ------------------------------
    def _preview_store_rows(self):
        """(store, level, rows): the picked level's own pictures from the
        Sample DB's store, read-only; a level with none of its own
        previews what is under it."""
        level = (self._sample or None, self._chip if self._sample else None,
                 self._flake if self._sample and self._chip else None)
        if not level[0]:
            return None, level, []
        store = self._samples_store()
        if store is None:
            return None, level, []
        try:
            # Read once per store change, not per readout (audit 2026-10-08
            # item 8): opening the store is a stat, its rows are cached.
            rows = self._preview_or_new().rows(store, level)
        except Exception as exc:
            events.debug("Preview Not Read", repr(exc), source=self.NAME, every=5.0)
            rows = []
        return store, level, rows

    def _preview_or_new(self):
        preview = getattr(self, "_picture_preview", None)
        if preview is None:
            preview = self._picture_preview = PicturePreview()
        return preview

    @property
    def preview_magnification(self):
        _store, level, rows = self._preview_store_rows()
        return self._preview_or_new().magnification(level, rows)

    @property
    def preview_magnification_options(self):
        _store, level, rows = self._preview_store_rows()
        return self._preview_or_new().options(level, rows)

    def set_preview_magnification(self, magnification):
        _store, level, rows = self._preview_store_rows()
        try:
            return self._preview_or_new().choose(level, rows, magnification)
        except _PreviewRefused as refusal:
            raise Refused(str(refusal))

    @property
    def preview_key(self):
        _store, level, rows = self._preview_store_rows()
        return self._preview_or_new().key(level, rows)

    @property
    def preview_text(self):
        _store, level, rows = self._preview_store_rows()
        if not level[0]:
            return "Pick a sample"
        return self._preview_or_new().text(level, rows)

    @property
    def preview_alt(self):
        """The preview's alt text (UX audit #19)."""
        _store, level, rows = self._preview_store_rows()
        return self._preview_or_new().alt(level, rows)

    @property
    def preview_picture(self):
        store, level, rows = self._preview_store_rows()
        return self._preview_or_new().png(store, level, rows)

    @staticmethod
    def _match(label, options):
        """The option `label` names: itself, else the one equal to it
        trimmed and case-insensitively (the Sample DB's rule); None."""
        if label in options:
            return label
        wanted = str(label or "").strip().lower()
        return next((o for o in options if o.strip().lower() == wanted), None)

    def pick_sample(self, label):
        """The Sample dropdown. A different sample clears the chip and the
        flake (they belong to the sample)."""
        options = self.sample_options
        if not options:
            raise Refused(self.SAMPLE_FIRST + ".")
        found = self._match(label, options)
        if found is None:
            raise Refused(f"{label!r} is not a sample in the Sample DB.")
        if found != self._sample:
            self._sample, self._chip, self._flake = found, None, None
        self._touch()
        return found

    def pick_chip(self, label):
        """The Chip dropdown (the sample's chips). A different chip clears
        the flake."""
        if not self._sample:
            raise Refused(self.missing_tier(("", "", "")) + ".")
        found = self._match(label, self.chip_options)
        if found is None:
            raise Refused(f"{label!r} is not a chip of sample {self._sample}. "
                          "Add it in the Sample DB first.")
        if found != self._chip:
            self._chip, self._flake = found, None
        self._touch()
        return found

    def pick_flake(self, label):
        """The Flake dropdown (the chip's flakes)."""
        if not (self._sample and self._chip):
            raise Refused(self.missing_tier((self._sample, "", "")) + ".")
        found = self._match(label, self.flake_options)
        if found is None:
            raise Refused(f"{label!r} is not a flake of chip {self._chip}. "
                          "Add it in the Sample DB first.")
        self._flake = found
        self._touch()
        return found

    @staticmethod
    def missing_tier(where):
        """The sentence for the first tier `where` (sample, chip, flake)
        lacks, in the hierarchy's order: a chip is a required choice (only
        its photo is optional), so a flake given without one is refused at
        the chip."""
        sample, chip, _flake = (str(v or "").strip() for v in where)
        if not sample:
            return "Choose a sample first"
        if not chip:
            return "Choose a chip first: a flake always belongs to a chip"
        return "Choose a flake first"

    @property
    def cut_next(self):
        """The number the next cut on the picked flake gets: 1 + the trials
        already on it (every status). None (blank) until all three are
        picked."""
        if not all(self._where()):
            return None
        return self._store.count_for_flake(*self._where()) + 1

    def set_trial_sample(self, trial_id=None, sample_id=None, chip_id=None,
                         flake_id=None):
        """Back-fill a recorded trial's sample, chip and flake (its cut
        number follows: 1 + the trials on that flake numbered below it).
        The arguments win; without them, the Trial entry under Data and the
        setup step's picks."""
        self._need_store()
        number = int(trial_id if trial_id is not None
                     else (self.afm_trial_id or 0))
        if number <= 0:
            raise Refused("Type the trial number under Data, Trial.")
        given = (sample_id, chip_id, flake_id)
        where = tuple(str(v or "").strip() for v in (
            given if any(v is not None for v in given) else self._where()))
        if not all(where):
            # Sample -> chip -> flake: a flake never stands without its chip.
            raise Refused(self.missing_tier(where) + ": pick the sample, chip "
                          "and flake on the trial's setup, then press Set "
                          "sample for trial.")
        row = self._store.trial(number)
        if row is None:
            raise Refused(f"No trial {number} in the trial store.")
        if row["status"] == "armed":
            raise Refused(f"Trial {number} is still armed. Finish it first.")
        cut = self._store.count_for_flake(*where, before=number) + 1
        self._store.update(number, {"sample_id": where[0], "chip_id": where[1],
                                    "flake_id": where[2], "cut_id": str(cut)})
        self._changed()
        events.info("Trial Sample Set", f"Trial {number}: on "
                    f"{' · '.join(where)}, cut {cut}.", source=self.NAME)
        return number

    @staticmethod
    def _trial_name(number, where, word="Trial"):
        """ "Trial 4 on 4oct26 · 2 · F3" (or "Trial 4" with no flake)."""
        if all(where):
            return f"{word} {number} on {' · '.join(str(w) for w in where)}"
        return f"{word} {number}"

    # -- the session database ----------------------------------------------
    def new_database(self):
        """New session database: the store prompt (`new_store`), prefilled
        with the open store's folder and a fresh `transfer_map_<stamp>` name;
        New store there makes it (2026-10-08: never silently beside the
        current file). The current file stays on disk untouched; each
        database keeps its pictures under its own name, so trial 1 of the new
        one never overwrites trial 1 of the old. Cancel goes back."""
        self._need_store()
        if self.is_armed or self._pending is not None:
            raise Refused("A trial is armed. Finish or abort it before starting "
                          "a new trial store.")
        self.change_store()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name, suffix = f"transfer_map_{stamp}", 2
        while (Path(self.store_dir).expanduser() / f"{name}.sqlite").exists():
            name, suffix = f"transfer_map_{stamp}_{suffix}", suffix + 1
        self.store_name = name
        self._touch()
        return self.store_dir

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
                writer.writerow([row.get(c) for c in PROFILE_COLUMNS])
        tip_columns = [name for name, _kind in TIP_COLUMNS]
        with open(tips_path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(tip_columns + ["trial_count", "trial_ids"])
            for tip in self._store.tips():
                writer.writerow([tip.get(c) for c in tip_columns]
                                + [tip["count"], " ".join(map(str, tip["trials"]))])
        events.info("Map Exported", f"{_count(len(rows), 'trial')} written to {folder}",
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
        """Trials measured elsewhere: the speed (and the tilt, when the file
        has one: collected, never demanded since 2026-10-07), and the force
        index given directly (`force_index`, optionally named by
        `force_definition`; or `force_<name>` columns, as an export writes).
        No profile. Rows without a speed are skipped."""
        self._need_store()
        try:
            with open(path, newline="") as handle:
                rows = list(csv.DictReader(handle))
        except OSError:
            raise Refused(f"Could not read {Path(path).name}.")
        if not rows or not ({"speed_steps_s", "speed"} & set(rows[0])):
            raise Refused(f"{Path(path).name} needs a speed_steps_s column.")
        reserved = {"force_given", "force_index", "force_definition"}
        imported = skipped = 0
        new_tips = []
        for row in rows:
            tilt = _number(row.get("tilt_deg", row.get("tilt")))
            speed = _number(row.get("speed_steps_s", row.get("speed")))
            if speed is None:
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
                **{name: _number(row.get(name)) for name in (
                    "channel_height_nm", "channel_height_sigma_nm",
                    "trench_depth_nm", "trench_depth_sigma_nm",
                    "width_optical_um", "width_optical_sigma_um")},
                **{name: (row.get(name) or "").strip() or None for name in (
                    "width_optical_method", "sample_id", "flake_uid",
                    "operator_id", "operator_auth", "camera_profile_id",
                    "chip_id", "flake_id", "cut_id")},
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
        created = (f"; {'tip' if len(new_tips) == 1 else 'tips'} created: "
                   f"{', '.join(new_tips)}" if new_tips else "")
        events.info("Map Imported", f"{_count(imported, 'trial')} imported from "
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
                "baseline": row.get("red_baseline"),
                # The lab's: the shade's place on its peak, stored at Finish.
                "shade_position": row.get("force_position")})
        self._indices[row["id"]] = out
        return out

    def _map_rows(self):
        rows = []
        models = {t["tip_id"]: t.get("model") for t in self._store.tips()}
        for row in self._store.trials():
            # The lab's invalid flag: kept on record, never analysed.
            if row["status"] in ("armed", "aborted") or row.get("invalid"):
                continue
            width, sigma, source = analysis.pick_width(row)
            # TM-3: the tilt rides along (never drawn); `force_class` is the
            # bench store's column, absent from this one's v6 (None then).
            rows.append({"id": row["id"], "tilt": row["tilt_deg"],
                         "force_class": row.get("force_class"),
                         # The tip's model (owner, 2026-10-07).
                         "model": models.get(row.get("tip_id")),
                         # The cut's flake (approved proposal 2026-10-07).
                         "sample_id": row.get("sample_id"),
                         "chip_id": row.get("chip_id"),
                         "flake_id": row.get("flake_id"),
                         "speed": row["speed_steps_s"],
                         "force": self._force_of(row),
                         "width": width, "width_sigma": sigma,
                         "width_source": source,
                         "width_afm": row["width_um"],
                         "width_afm_sigma": row["width_sigma_um"],
                         "width_optical": row.get("width_optical_um"),
                         "width_optical_sigma": row.get("width_optical_sigma_um")})
        return rows

    def _changed(self):
        self._revision += 1
        self._touch()
        # The backup (2026-10-07): after a trial is saved or finalized, never
        # while one is being recorded (its video is still being written).
        if self._trial is None and self._pending is None:
            self._request_backup()

    @property
    def figure(self):
        key = (self._revision, self._figure_type, self._definition, self._band,
               self._width_source, self.trial_pick, self.FIGURE_SIZE,
               self.FIGURE_DPI)
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
                size=self.FIGURE_SIZE, dpi=self.FIGURE_DPI,
                width_source=self._width_source)
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

    @property
    def width_source(self):
        """Which widths the slice, the comparison and the gradient use: AFM
        only (the default), or AFM else optical (store v6, Q19)."""
        return self._width_source

    @property
    def width_source_options(self):
        return list(plot_data.WIDTH_SOURCES)

    def set_width_source(self, source):
        if source not in plot_data.WIDTH_SOURCES:
            raise Refused(f"{source!r} is not a width source.")
        self._width_source = source
        self._touch()
        return source

    # -- readouts ------------------------------------------------------------
    @property
    def trial_count(self):
        return self._store.count()

    @property
    def trial_status(self):
        """The trial by its name: "Trial 4 on 4oct26 · 2 · F3" (its number
        is the next one in the region step), the Mark when there is one;
        between trials the last one's. The sample count is Diagnostics'."""
        trial, pending = self._trial, self._pending
        if trial is not None:
            marked = (f", force marked at {trial.operator_t:.1f} s"
                      if trial.operator_t is not None else "")
            return self._trial_name(trial.id, trial.where) + marked
        if pending is not None:
            return self._trial_name(self._store.next_id(), pending.where)
        last = self._store.last()
        if last is None:
            return "No trials yet."
        where = tuple(last.get(k) for k in ("sample_id", "chip_id", "flake_id"))
        return (f"Last: {self._trial_name(last['id'], where, 'trial')}, "
                f"{last['status']}.")

    @property
    def trial_figure(self):
        """The trial just recorded, for its review (the `finish` step): its
        red-percent profile with the detector's peak and dip and the Mark,
        drawn once from memory, before Finish writes it. b"" in any other
        step (TM-2: there is no live plot)."""
        trial = self._trial
        if trial is None or not trial.ended:
            return b""
        if trial.review is None:
            samples = list(trial.samples)
            profile = {"t": [s[0] for s in samples],
                       "red": [s[1] for s in samples]}
            found = analysis.detect(profile, trial.operator_t) or {}
            trial.review = plot_data.render_transfer_figure(
                "profile", [], self._definition, profile=profile,
                marks={"trial_id": trial.id, "operator_t": trial.operator_t,
                       "max_t": found.get("max_t"),
                       "min_t": found.get("min_t"),
                       "baseline": found.get("baseline"),
                       "red_max": found.get("red_max"),
                       "red_min": found.get("red_min")},
                size=self.FIGURE_SIZE, dpi=self.FIGURE_DPI)
        return trial.review

    @property
    def trials_log(self):
        lines = []
        for row in self._store.trials():
            width = _width_text(row)
            tilt = "?" if row["tilt_deg"] is None else f"{row['tilt_deg']:g} deg"
            speed = ("?" if row["speed_steps_s"] is None
                     else f"{row['speed_steps_s']:g} steps/s")
            measured = row["speed_measured_steps_s"]
            if measured is not None:
                speed += f" (cut measured {measured:g})"
            ids = self._id_words(row.get("sample_id"), row.get("chip_id"),
                                 row.get("flake_id"), row.get("cut_id"))
            lines.append(f"{row['id']:>4}  {row['status']:<8} "
                         f"{row['tip_id'] or '-'}  {ids + '  ' if ids else ''}"
                         f"{tilt}  {speed}  {width}"
                         f"{'  force ' + row['force_class'] if row.get('force_class') else ''}"
                         f"{'  broke' if row['broke'] else ''}"
                         f"{'  invalid' if row.get('invalid') else ''}"
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
        """d(width)/d(speed) at the centre of the speed range, with one
        sigma, from the Gaussian process over the measured trials (all
        force bands). Speed only since 2026-10-07: the map has no tilt
        axis, and a trial without a tilt still counts."""
        import numpy
        rows = [r for r in plot_data.with_width(self._map_rows(), self._width_source)
                if r["speed"] is not None]
        if len(rows) < 3:
            return ""
        speed = numpy.array([r["speed"] for r in rows], dtype=float)
        span = max(speed.max() - speed.min(), 1e-9)
        x = ((speed - speed.min()) / span).reshape(-1, 1)
        widths = numpy.array([r["width"] for r in rows], dtype=float)
        spread = float(widths.std()) or 1.0
        noise = numpy.array(plot_data.width_noise(rows, spread))
        grad, var = analysis.gp_gradient(x, widths, numpy.array([[0.5]]),
                                         length=plot_data.SLICE_LENGTH,
                                         noise=noise)
        g = grad[0][0] / span
        s = numpy.sqrt(var[0][0]) / span
        return f"At the centre of the speeds: {g:+.3g} ± {s:.2g} um per step/s"

    # -- schema --------------------------------------------------------------
    @property
    def schema(self):
        P = self.PARAMS
        configure = "Configure Transfer Map"
        past_setup = [p for p in self.PHASES if p != "setup"]
        return sch.schema(
            # The start screen's (owner ruling 2026-10-07: what a step does
            # not use gets out of the way).
            sch.section(
                "Trial store",
                sch.readonly("Trial store", "db_path"),
                sch.readonly("Trials", "trial_count", param=P["trial_count"]),
                sch.button("New trial store\u2026", "new_database",
                           disabled_when=("armed",)),
                phases=("setup",),
            ),
            # The store prompt (2026-10-08, the Sample DB's): the page with
            # no store chosen, Change store… and New session database…. A
            # new store always asks where.
            sch.section(
                "Where to save the trial store",
                sch.readonly("Trial store", "store_status", role="info"),
                sch.entry("Folder", "store_dir", P["store_dir"]),
                sch.dropdown("Choose folder\u2026", "store_folder_pick",
                             "pick_store_folder", "store_folder_options"),
                sch.entry("Name", "store_name", P["store_name"]),
                sch.button("New store", "new_store", role="go",
                           inputs=("store_dir", "store_name")),
                sch.entry("Existing store file", "store_path", P["store_path"]),
                sch.button("Open store", "open_store", inputs=("store_path",)),
                sch.button("Cancel", "cancel_store_choice"),
                phases=(self.PROMPT,),
            ),
            # Every step: what to do next and the stage's readouts. In
            # Setup the readouts are read under the entries that override
            # them (Start, "Now:"), not in a row of their own beside them
            # (UX audit 2026-10-08 #15); from Region on they are here.
            sch.section(
                "Trial",
                sch.readonly("Next step", "next_step", role="info"),
                sch.phased(sch.readonly("Tilt", "tilt_now", rail=True,
                                        param=P["tilt_now"]), *past_setup),
                sch.phased(sch.readonly("Speed", "speed_now", rail=True,
                                        param=P["speed_now"]), *past_setup),
            ),
            # setup: the preliminary information, then Arm. One tip
            # control (approved proposal 2026-10-07): the dropdown, whose
            # line carries the tip's trial count; New tip… opens the
            # `new_tip` step.
            sch.section(
                "Start",
                sch.dropdown("Tip", "tip_pick", "pick_tip", "tip_options"),
                sch.button("New tip…", "new_tip"),
                sch.readonly("Tip status", "tip_status"),
                # The cut's flake, from the Sample DB's store (cascading:
                # a new sample clears the chip and the flake, a new chip the
                # flake). The hierarchy (owner 2026-10-07): Chip is greyed
                # with no options until a sample is chosen, Flake until a
                # chip is.
                sch.dropdown("Sample", "sample_pick", "pick_sample",
                             "sample_options"),
                sch.dropdown("Chip", "chip_pick", "pick_chip", "chip_options",
                             enabled_by="has_sample_pick",
                             enabled_by_reason="Choose a sample first"),
                sch.dropdown("Flake", "flake_pick", "pick_flake",
                             "flake_options", enabled_by="has_chip_pick",
                             enabled_by_reason="Choose a chip first"),
                # The picked flake's picture (owner 2026-10-08): 100x, else
                # 50x, else the next lower; read from the Sample DB's store.
                sch.image("Picture", "preview_picture", model_attr="preview_key",
                          empty=PicturePreview.NONE, alt_attr="preview_alt"),
                sch.readonly("Shown", "preview_text"),
                sch.dropdown("Show magnification", "preview_magnification",
                             "set_preview_magnification",
                             "preview_magnification_options"),
                sch.readonly("Cut", "cut_next"),
                # Bench 2026-09-28: the tilt varies between trials of one
                # tip and was buried two tiers down; it is asked here, per
                # trial. Collected, never demanded (TM-3, 2026-10-07).
                # Under each, what the trial would take now: the typed value,
                # else the rotator's angle / the probe's speed (`tilt_now`,
                # `speed_now`, the readouts of the other steps).
                sch.entry("Tilt for this trial (deg)", "typed_tilt",
                          P["typed_tilt"]),
                sch.readonly("Tilt now", "tilt_now", param=P["tilt_now"],
                             secondary=True, lead="Now:"),
                sch.entry("Speed for this trial (steps/s)", "typed_speed",
                          P["typed_speed"]),
                sch.readonly("Speed now", "speed_now", param=P["speed_now"],
                             secondary=True, lead="Now:"),
                sch.button("Arm trial", "arm_trial",
                           inputs=("typed_tilt", "typed_speed"),
                           role="go", disabled_when=("armed", "latched")),
                phases=("setup",),
            ),
            # new_tip: the New tip prompt. Add tip refuses an empty or a
            # known ID and stays; Add tip or Cancel returns to setup.
            # The model is required (owner, 2026-10-07); a model not on the
            # list is added the way the Sample DB adds a material.
            sch.section(
                "New tip",
                sch.entry("Tip ID", "new_tip_id", P["new_tip_id"]),
                sch.dropdown("Model", "new_tip_model", "set_new_tip_model",
                             "tip_model_options"),
                sch.entry("New model", "new_model_name", P["new_model_name"]),
                sch.button("Add model", "add_tip_model",
                           inputs=("new_model_name",)),
                sch.button("Add tip", "add_tip", inputs=("new_tip_id",),
                           role="go"),
                sch.button("Cancel", "cancel_new_tip"),
                phases=("new_tip",),
            ),
            # region: picked ON the stage still Arm took.
            sch.section(
                "Capture region",
                sch.region_select("Capture region", "set_region",
                                  model_attr="region", role="info",
                                  data_command="stage_still"),
                phases=("region",),
            ),
            # live and marked: the recording.
            sch.section(
                "Recording",
                # Where the live Red was (approved proposal 2026-10-07):
                # the force class and its shadow_vs_peak value, at the row
                # rate; Red Percent's own readings are not on this page.
                sch.readonly("Force estimate", "force_estimate", rail=True),
                sch.button("Mark force", "mark_force", enabled_when=("armed",)),
                sch.phased(sch.button("End recording", "end_recording",
                                      role="go", enabled_when=("armed",)),
                           "marked"),
                # TM-4: the whole display, at its own resolution, from the
                # region landing to End recording; no picture is drawn live.
                # One word; the counts are Diagnostics'.
                sch.readonly("Video", "video_word"),
                # TM-2 (2026-10-07): no live plot. Redrawing the whole trace
                # every refresh slowed the bench's view (CAP-5); the trace
                # is drawn once, for the review, in the finish step.
                phases=("live", "marked"),
            ),
            # finish: review what was recorded, then keep it.
            sch.section(
                "Review",
                sch.image("Stage", "stage_still",
                          empty="The picture of the stage taken at Arm."),
                sch.image("At Mark force", "mark_full_image",
                          empty="The whole display at Mark force."),
                sch.readonly("Video", "video_word"),
                sch.image("This trial", "trial_figure",
                          empty="The trial's red percent, once its recording "
                                "has ended."),
                sch.entry("Note", "note", P["note"]),
                sch.button("Finish trial", "finish_trial", inputs=("note",),
                           role="go", enabled_when=("armed",)),
                phases=("finish",),
            ),
            # Every step: the trial's state, and the abort (a stop control:
            # never hidden by a step; greyed while nothing is armed).
            sch.section(
                "This trial",
                sch.readonly("Status", "trial_status", role="info"),
                sch.phased(sch.toggle("Tip broke", "is_broke", "mark_broke",
                                      "Broke", "Not broken", on_args=(True,),
                                      off_args=(False,)),
                           "marked", "finish"),
                sch.button("Abort trial", "abort_trial", enabled_when=("armed",),
                           stop=True),
            ),
            # The map of the trials so far, on the start screen.
            sch.section(
                "Map",
                sch.image("Transfer map", "figure",
                          empty="No trials yet. Record one, or import trials."),
                phases=("setup",),
            ),
            sch.section(
                "Context",
                sch.image("Arm, whole screen", "before_full_image",
                          empty="The whole screen, taken when you arm."),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Tip",
                # The tip these act on: the one picked for the trial.
                sch.readonly("Tip", "tip_id"),
                sch.entry("Tip note", "tip_note", P["tip_note"]),
                sch.button("Save tip note", "set_tip_note",
                           inputs=("tip_note",)),
                sch.button("Retire tip", "retire_tip"),
                sch.button("Return tip to use", "unretire_tip"),
                # Corrects the picked tip's model label (owner, 2026-10-07).
                sch.dropdown("Tip model", "tip_model", "set_tip_model",
                             "tip_model_options"),
                # After the fact: the armed trial's, else the last trial's
                # (the sheet's own Tip broke is drawn in marked and finish).
                sch.toggle("Tip broke", "is_broke", "mark_broke", "Broke",
                           "Not broken", on_args=(True,), off_args=(False,)),
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
                sch.dropdown("Width source", "width_source", "set_width_source",
                             "width_source_options"),
                sch.entry("Trial to show (0 = latest)", "trial_pick",
                          P["trial_pick"]),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "AFM measurement",
                sch.entry("Trial", "afm_trial_id", P["afm_trial_id"]),
                sch.entry("Channel width (AFM)", "width_um", P["width_um"]),
                sch.entry("Width uncertainty", "width_sigma_um",
                          P["width_sigma_um"]),
                sch.entry("Sample thickness", "thickness_nm", P["thickness_nm"]),
                sch.entry("Thickness uncertainty", "thickness_sigma_nm",
                          P["thickness_sigma_nm"]),
                sch.entry("Channel height", "channel_height_nm",
                          P["channel_height_nm"]),
                sch.entry("Channel height uncertainty", "channel_height_sigma_nm",
                          P["channel_height_sigma_nm"]),
                sch.entry("Trench depth", "trench_depth_nm", P["trench_depth_nm"]),
                sch.entry("Trench depth uncertainty", "trench_depth_sigma_nm",
                          P["trench_depth_sigma_nm"]),
                sch.button("Attach AFM", "attach_afm",
                           inputs=("afm_trial_id", "width_um", "width_sigma_um",
                                   "thickness_nm", "thickness_sigma_nm",
                                   "channel_height_nm", "channel_height_sigma_nm",
                                   "trench_depth_nm", "trench_depth_sigma_nm")),
                sch.button("Set tilt for trial", "set_trial_tilt",
                           inputs=("afm_trial_id", "typed_tilt")),
                sch.button("Set speed for trial", "set_trial_speed",
                           inputs=("afm_trial_id", "typed_speed")),
                # The lab's (bench 2026-10-04: "vacuum was off, data is
                # invalid"): on record and in the export, off the map.
                sch.button("Mark trial invalid", "set_trial_invalid",
                           inputs=("afm_trial_id",), args=(True,)),
                sch.button("Mark trial valid", "set_trial_invalid",
                           inputs=("afm_trial_id",), args=(False,)),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Optical measurement",
                sch.entry("Trial", "afm_trial_id", P["afm_trial_id"]),
                sch.entry("Channel width (optical)", "width_optical_um",
                          P["width_optical_um"]),
                sch.entry("Uncertainty", "width_optical_sigma_um",
                          P["width_optical_sigma_um"]),
                sch.dropdown("Method", "width_optical_method",
                             "set_width_optical_method",
                             "width_optical_method_options"),
                sch.button("Attach optical width", "attach_optical",
                           inputs=("afm_trial_id", "width_optical_um",
                                   "width_optical_sigma_um")),
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
                # Back-fill a trial recorded before its flake was asked: the
                # trial number here, the flake picked on the setup step.
                sch.entry("Trial", "afm_trial_id", P["afm_trial_id"]),
                sch.button("Set sample for trial", "set_trial_sample",
                           inputs=("afm_trial_id",)),
                tier=2, disclosure=configure,
            ),
            # Where the trials go (A3): the line, and Change store… (the
            # store prompt above; refused while a trial is armed).
            sch.section(
                "Store",
                sch.readonly("Trial store", "store_status", role="info"),
                sch.button("Change store…", "change_store",
                           disabled_when=("armed",)),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Diagnostics",
                sch.readonly("Last trial", "last_trial_numbers"),
                sch.readonly("Width gradient", "width_gradient"),
                sch.readonly("Video encoder", "video_encoder"),
                # The trial row's counts (2026-10-07: off the sheet).
                sch.readonly("Video", "video_status"),
                sch.readonly("Samples", "trial_samples"),
                sch.readonly("Shade position", "force_position_text"),
                # The lab's commands no view draws (2026-10-06): the tip-shade
                # force rebuilt from region footage, and the data finalizer's
                # walk (its window was the retired Qt view's).
                {"type": "internal", "command": "rebuild_force",
                 "writable": False, "role": "neutral"},
                {"type": "internal", "command": "open_finalizer",
                 "writable": False, "role": "neutral"},
                {"type": "internal", "command": "finalize_queue",
                 "writable": False, "role": "neutral"},
                {"type": "internal", "command": "finalize_media",
                 "writable": False, "role": "neutral"},
                {"type": "internal", "command": "finalize_save",
                 "writable": False, "role": "neutral"},
                sch.log_stream("Trials", "trials_log"),
                sch.log_stream("Tips", "tips_log"),
                sch.button("Delete trial", "delete_trial", inputs=("trial_pick",),
                           disabled_when=("armed",)),
                tier=3, disclosure="Diagnostics",
            ),
            self._safety_section(),
        )


def _open_samples(path):
    """The Sample DB's store at `path`, read-only (never created, migrated
    or written); raises when there is no file there."""
    return SampleStore.open_readonly(path)


def _width_text(row):
    """The trials log's width: "1.8 um (AFM)", "~2.1 um (optical)", both
    ("1.8 um (AFM), optical 2.1 um"), or "no width"; the two AFM heights
    after it when measured."""
    afm, optical = row.get("width_um"), row.get("width_optical_um")
    if afm is not None:
        text = f"{afm:g} um (AFM)" + (f", optical {optical:g} um"
                                       if optical is not None else "")
    elif optical is not None:
        text = f"~{optical:g} um (optical)"
    else:
        text = "no width"
    if row.get("channel_height_nm") is not None:
        text += f", height {row['channel_height_nm']:g} nm"
    if row.get("trench_depth_nm") is not None:
        text += f", trench {row['trench_depth_nm']:g} nm"
    return text


def _ordinal(n):
    """1st, 2nd, 3rd, 4th, 11th, 12th, 13th, 21st, 101st, 111th."""
    n = int(n)
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _number_or_none(value):
    """A channel value as a float, or None (absent, not a number, or not
    finite): a row never fails for a channel it does not carry."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _number(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
