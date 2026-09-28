"""The Transfer Map: the heatmap this project exists for.

A 3D map over **tilt angle, speed and force** whose value of interest is the
**channel width** of the transferred sample (owner, 2026-09-27). It builds
itself as trials are recorded: the operator arms a trial, lowers the tip,
presses Mark force at the force they want, and finishes; the station keeps
the red-percent slice of the lowering, a before and an after picture of the
capture region, and the tilt and speed at the time. AFM widths and
thicknesses are attached later, with their uncertainties.

**Force** has no sensor. It is approximated from the red-percent trace of
the lowering (`model.transfer_map_analysis`): several definitions, computed
from the stored raw profile whenever a figure or an export asks, never
stored in its place.

**It moves nothing.** No port, no gamepad, no device. `_halt_hardware`
disarms the trial in memory and returns at once; the aborted trial is
written on a worker, so a stop is never held by the disk.

**The store is local** (owner ruling 2026-09-27): one SQLite file inside the
project checkout, `<repo root>/data/transfer_map.sqlite` (the repo root is
the directory holding `src/`), created with its schema when the model opens
(the event log says where, and how many trials it holds). `STATION_MAP_DB=
<path>` overrides it. Pictures sit beside it in `data/<database name>/
<trial_id>/` (`data/transfer_map/` for the default file), exports in
`data/exports/`. "New session database" starts another file in the same
folder. Building the model creates nothing (the contract test builds every
registered class).

Live sources are duck-typed from `on_model_added`, never a class name: tilt
from a model with `position_deg` (the Rotator), else the operator's typed
"Tilt without a rotator" (blank = no tilt, never a default 0); speed and Z from a model with
`position`, `position_time` and `mode_name` (a probe: `live_speed` if it has
one, else the manual or autonomous speed for the mode it is in); samples
from a model with `subscribe` and `grab_frame` (Red Percent).
"""
import csv
import json
import math
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import schema as sch
from events import events
from model import plot_data
from model import transfer_map_analysis as analysis
from model.base import Model
from param import Param
from result import NeedsConfirm, Refused

#: The figure dropdown, in the operator's words -> `plot_data` kind.
FIGURES = {
    "3D map": "map3d",
    "Slice at a force band": "slice",
    "Compare force definitions": "compare",
    "Trial profile": "profile",
}

#: The trials table. The brief's columns, then four of ours: where the
#: trial came from, where its tilt and speed were read, and the force
#: indices an import gave for a trial with no profile (JSON).
TRIAL_COLUMNS = (
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"), ("started_at", "TEXT"),
    ("tip_id", "TEXT"), ("tilt_deg", "REAL"), ("speed_steps_s", "REAL"),
    ("z_contact", "REAL"), ("mark_operator_t", "REAL"),
    ("mark_auto_max_t", "REAL"), ("mark_auto_min_t", "REAL"),
    ("broke", "INTEGER NOT NULL DEFAULT 0"), ("red_min", "REAL"),
    ("red_max", "REAL"), ("red_baseline", "REAL"), ("width_um", "REAL"),
    ("width_sigma_um", "REAL"), ("thickness_nm", "REAL"),
    ("thickness_sigma_nm", "REAL"), ("note", "TEXT"), ("before_path", "TEXT"),
    ("after_path", "TEXT"), ("status", "TEXT NOT NULL"),
    ("origin", "TEXT NOT NULL DEFAULT 'recorded'"), ("tilt_source", "TEXT"),
    ("speed_source", "TEXT"), ("force_given", "TEXT"),
)
_TRIAL_NAMES = frozenset(name for name, _kind in TRIAL_COLUMNS)
PROFILE_COLUMNS = ("trial_id", "t_s", "red", "z", "x", "y")
SCHEMA_VERSION = 1

_CREATE = (
    "CREATE TABLE IF NOT EXISTS trials ("
    + ", ".join(name + " " + kind for name, kind in TRIAL_COLUMNS) + ")",
    "CREATE TABLE IF NOT EXISTS profile (trial_id INTEGER NOT NULL "
    "REFERENCES trials(id), t_s REAL NOT NULL, red REAL, z REAL, x REAL, "
    "y REAL)",
    "CREATE INDEX IF NOT EXISTS profile_trial ON profile(trial_id)",
    "PRAGMA user_version = 1",
)

#: Samples kept per trial. At Red Percent's fastest a lowering is minutes
#: of a few hundred rows a second; past this the rest are counted, not kept.
MAX_SAMPLES = 500_000


def _checked(names):
    """Column names come from this module's own table, never from input."""
    unknown = [n for n in names if n not in _TRIAL_NAMES]
    if unknown:
        raise ValueError("not a trials column: " + ", ".join(map(str, unknown)))
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
        """Run `fn(db)` in one transaction, creating the file on first use."""
        with self._lock:
            fresh = not self.exists
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = self._connect()
            try:
                with db:
                    for statement in _CREATE:
                        db.execute(statement)
                    result = fn(db)
            finally:
                db.close()
        if fresh:
            events.info("Map Database Created", "A new Transfer Map database "
                        "was created at " + str(self.path) + ".",
                        source="Transfer Map")
        return result

    def ensure(self):
        """Create the file and its schema if missing; a no-op otherwise
        (every statement is IF NOT EXISTS). True when it was created now."""
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
        def _do(db):
            db.execute("DELETE FROM profile WHERE trial_id = ?", (trial_id,))
            db.execute("DELETE FROM trials WHERE id = ?", (trial_id,))
        return self.write(_do)

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
        # Text, so blank means "no tilt" rather than a default of 0 degrees.
        Param("typed_tilt", "text", default="",
              label="Tilt without a rotator (deg)"),
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
    )}

    def __init__(self, port=None, gamepad=None, sim=False, db_path=None):
        super().__init__()
        self.sim = sim
        self.db_path = Path(db_path) if db_path else self.default_db_path()
        #: Pictures, exports and uploads all live beside the database, so a
        #: Web download is checked against the same folder (CON-5).
        self.output_root = self.db_path.parent
        self._store = TrialStore(self.db_path)
        self._lock = threading.Lock()
        self._trial = None
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

    @staticmethod
    def default_db_path():
        """`STATION_MAP_DB`, else `<repo root>/data/transfer_map.sqlite`,
        the repo root being the directory that holds `src/`."""
        configured = os.environ.get("STATION_MAP_DB")
        if configured:
            return Path(configured).expanduser().resolve()
        if getattr(sys, "frozen", False):
            # A PyInstaller bundle: `__file__` points inside the bundle, so
            # "local" means beside the executable (owner ruling 2026-09-27:
            # the store is local to the installation, never global).
            return Path(sys.executable).resolve().parent / "data" / "transfer_map.sqlite"
        return Path(__file__).resolve().parents[2] / "data" / "transfer_map.sqlite"

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
        if trial is not None:
            self._release_red()
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

    def _release_red(self):
        red = self._red
        if red is not None:
            try:
                red.unsubscribe(self._on_sample)
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
        if self._red is None:
            return "Open Red Percent"
        if not self.has_region:
            return "Set the capture region"
        trial = self._trial
        if trial is not None:
            if trial.operator_t is None:
                return "Lower the tip; press Mark force when the force is right"
            return "Press Finish trial"
        if not (self.tip_id or "").strip():
            return "Type a tip ID"
        return "Press Arm trial"

    @property
    def state(self):
        snapshot = super().state
        snapshot["has_region"] = self.has_region
        return snapshot

    # -- the samples, on Red Percent's run thread ------------------------------
    def _on_sample(self, t_s, red, positions):
        """One row of Red Percent's log. Appends and returns; never raises
        into the run loop (Red Percent catches it anyway)."""
        trial = self._trial
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
        run = None
        if not getattr(red, "is_running", False):
            red.start_run(confirmed=True)               # a Refused stops here
            run = red.run_token
        try:
            tilt, tilt_source = self._read_tilt()
            speed, speed_source = self._read_speed()
            trial_id = self._store.insert({
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "tip_id": tip,
                "tilt_deg": tilt, "speed_steps_s": speed, "status": "armed",
                "origin": "recorded", "tilt_source": tilt_source,
                "speed_source": speed_source, "note": ""})
            before = self._save_frame(trial_id, "before")
            if before:
                self._store.update(trial_id, {"before_path": before})
        except BaseException:
            if run is not None:
                red.end_run()                            # ours: undo it
            raise
        trial = _Trial(trial_id, tilt, speed, tip)
        trial.run = run
        with self._lock:
            self._trial = trial
        red.subscribe(self._on_sample)
        self._changed()
        events.info("Trial Armed", f"Trial {trial_id} armed, "
                    f"{self._place_on_tip(trial)}. Lower the tip, press Mark "
                    "force at the force you want, then Finish.",
                    source=self.NAME)
        return trial_id

    def mark_force(self):
        trial = self._trial
        if trial is None:
            raise Refused("No trial is armed.")
        trial.operator_t = time.monotonic() - trial.armed
        trial.z_mark = self._probe_axes()[2]
        speed = self._read_speed()[0]
        if speed is not None:
            trial.speed = speed      # the speed while lowering, at the Mark
        events.info("Force Marked", f"Trial {trial.id}: force marked at "
                    f"{trial.operator_t:.2f} s.", source=self.NAME)
        return round(trial.operator_t, 3)

    def finish_trial(self):
        trial = self._claim()
        if trial is None:
            raise Refused("No trial is armed.")
        self._release_red()
        after = self._save_frame(trial.id, "after")
        self._end_own_run(trial)
        samples = list(trial.samples)
        profile = {"t": [s[0] for s in samples], "red": [s[1] for s in samples]}
        found = analysis.detect(profile, trial.operator_t) or {}
        fields = {
            "status": "recorded", "note": (self.note or "").strip(),
            "after_path": after, "speed_steps_s": trial.speed,
            "z_contact": trial.z_mark, "mark_operator_t": trial.operator_t,
            "mark_auto_max_t": found.get("max_t"),
            "mark_auto_min_t": found.get("min_t"),
            "red_min": found.get("red_min"), "red_max": found.get("red_max"),
            "red_baseline": found.get("baseline"), "broke": int(trial.broke)}
        self._store.update(trial.id, fields, samples)
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

    def _save_frame(self, trial_id, which):
        red = self._red
        png = None
        try:
            png = red.grab_frame() if red is not None else None
        except Exception as exc:
            events.debug("Frame Failed", repr(exc), source=self.NAME)
        if not png:
            events.warn("No Picture", f"No {which} picture was captured for "
                        f"trial {trial_id}. Check the Red Percent capture "
                        "region.", source=self.NAME)
            return None
        folder = self.pictures_root / str(trial_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{which}.png"
        path.write_bytes(png)
        return str(path)

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
            return flag
        last = self._store.last()
        if last is None:
            raise Refused("There is no trial to mark yet.")
        self._store.update(last["id"], {"broke": int(flag)})
        self._changed()
        return flag

    def attach_afm(self):
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
        file, so one folder of pictures per database."""
        return self.output_root / self.db_path.stem

    # -- export and import -------------------------------------------------
    def _export(self):
        rows = self._store.trials()
        if not rows:
            raise Refused("No trials yet; there is nothing to export.")
        folder = self.output_root / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        trials_path = folder / f"transfer_map_{stamp}_trials.csv"
        profile_path = folder / f"transfer_map_{stamp}_profile.csv"
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
        events.info("Map Exported", f"{len(rows)} trial(s) written to {folder}",
                    source=self.NAME)
        return str(trials_path), str(profile_path)

    def export_csv(self):
        """Both tables under `output_root/exports/`; the trials file's path
        (with every force index as a column) is what a view downloads."""
        return self._export()[0]

    def export_profile_csv(self):
        """The same export; the profile file's path."""
        return self._export()[1]

    def import_csv(self, path):
        """Trials measured elsewhere: tilt and speed, and the force index
        given directly (`force_index`, optionally named by
        `force_definition`; or `force_<name>` columns, as an export writes).
        No profile. Rows without a tilt or a speed are skipped."""
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
            self._store.insert({
                "started_at": row.get("started_at") or None,
                "tip_id": row.get("tip_id") or "",
                "tilt_deg": tilt, "speed_steps_s": speed, "width_um": width,
                "width_sigma_um": _number(row.get("width_sigma_um")),
                "thickness_nm": _number(row.get("thickness_nm")),
                "thickness_sigma_nm": _number(row.get("thickness_sigma_nm")),
                "broke": 1 if str(row.get("broke") or "").strip().lower()
                in ("1", "yes", "true") else 0,
                "note": row.get("note") or "",
                "status": "measured" if width is not None else "recorded",
                "origin": "imported", "force_given": json.dumps(given)})
            imported += 1
        self._changed()
        events.info("Map Imported", f"{imported} trial(s) imported from "
                    f"{Path(path).name}; {skipped} skipped.", source=self.NAME)
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
                "Trial",
                sch.readonly("Next step", "next_step", role="info"),
                sch.region_select("Set capture region", "set_region",
                                  model_attr="region", role="info",
                                  data_command="screen_image"),
                sch.readonly("Tilt", "tilt_now", rail=True, param=P["tilt_now"]),
                sch.readonly("Speed", "speed_now", rail=True, param=P["speed_now"]),
                sch.readonly("Red", "red_now", param=P["red_now"], format=".2f"),
                sch.entry("Tip ID", "tip_id", P["tip_id"]),
                sch.readonly("Trials on this tip", "tip_trial_count"),
                sch.button("Arm trial", "arm_trial",
                           inputs=("tip_id", "typed_tilt"),
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
                sch.plot("Red % since Arm", "live_series", x_label="time (s)",
                         y_label="red (%)",
                         empty="Arm a trial and its red percent plots here."),
                sch.image("Transfer map", "figure",
                          empty="No trials yet. Record one, or import trials."),
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
                sch.entry("Tilt without a rotator (deg)", "typed_tilt",
                          P["typed_tilt"]),
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
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Data",
                sch.file_save("Export trials", "export_csv", extensions=("csv",)),
                sch.file_save("Export profiles", "export_profile_csv",
                              extensions=("csv",)),
                sch.file_open("Import trials", "import_csv", extensions=("csv",)),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Diagnostics",
                sch.readonly("Last trial", "last_trial_numbers"),
                sch.readonly("Width gradient", "width_gradient"),
                sch.log_stream("Trials", "trials_log"),
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


def _number(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
