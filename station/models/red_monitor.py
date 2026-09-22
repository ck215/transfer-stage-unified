"""Red Percent: the red fraction of a screen region, logged as a force proxy
beside the probe's position.

What the instrument is for (owner, 2026-09-21): the dataset is the product.
A row pairs "how red is the region right now" with "where was the probe when
that was true", and the experiment compares runs against each other — so the
things that make two runs comparable (the region, the detection threshold, the
baseline, the tilt, the sample mode, the achieved rate) belong in the artifact,
not in someone's bench notebook.

Three shapes carry that:

`RunLog`     the samples of one run. Parallel lists, O(1) append, no
             allocation per frame beyond the numbers themselves.
`MonitorRun` one run: its frozen configuration, its own log, its own stop
             event, and what actually happened (frames, rows, rates).
`RedMonitor` the Model. Owns a `Screen`, follows a Probe for position, and
             owns exactly one run at a time.

The load-bearing repairs carried over from `src/model/redpercent_system.py`:

* **REDPERCENT-1/3** the run's configuration is frozen at Start and the loop
  reads only the run — never the model's live attributes. A mid-run edit
  cannot reach the thread, and two runs cannot alias state.
* **REDPERCENT-2** every run gets a fresh log and its own `last_logged_red`.
* **REDPERCENT-4** the loop is wrapped: a failure ends the run, is recorded on
  it, and is reported once — never `is_running == True` with a dead thread.
* **REDPERCENT-5** `current_red` and `red_change` are one tuple published in
  one assignment, so no poller can read a torn pair.
* **REDPERCENT-9** Start refuses with no region.
* **REDPERCENT-16** velocity comes from position deltas, and a failed read is
  an empty cell — `0.0` is a position the stage can actually be at.
* **REDPERCENT-21** the output root is `~/transfer-stage-runs` or
  `TRANSFER_STAGE_DATA_ROOT`, resolved absolutely and never from the CWD; every
  artifact is named `<run_id>_*`.
* **REDPERCENT-22** the CSV is a plain rectangle; the configuration goes to a
  sibling `<run_id>_station_meta.json`.
* **REDPERCENT-23** operator annotation is a table, kept separate from what the
  station actually did.
* **REDPERCENT-11/PYSIDE-3/STEPPER-13** the probe list follows
  `on_model_added`/`on_model_removed`, so a torn-down probe cannot go on
  supplying its last position forever.

And the owner's ruling of 2026-09-21, which replaces the old fixed ~60 FPS
change-triggered loop: sample as fast as the grab allows. `sample_mode`
defaults to `every_frame` — a row per captured frame, no sleep, only a
zero-wait check of the stop event.
"""
import csv
import json
import os
import threading
import time
from pathlib import Path

from station import schema as sch
from station.devices.screen import Screen
from station.events import events
from station.model import Model
from station.models import plot_data
from station.param import Param
from station.result import Refused, NeedsConfirm

try:                                  # guarded: a bench box without numpy
    import numpy                      # still starts, and says why it cannot run
except Exception:                     # pragma: no cover - environment specific
    numpy = None


class RunLog:
    """The samples of one run, and the CSV they are written as.

    Parallel lists rather than a list of rows: appending is O(1) with no
    per-sample object, which is what lets `every_frame` mode keep up with the
    grab. `add` never builds a dict — the loop hands it the same two
    dictionaries every frame.

    A value of `None` is "not measured on this row" and `csv.writer` writes it
    as an empty cell. That distinction is the whole of REDPERCENT-16/ERRORS-7:
    the old code wrote `0.0` for a failed position read, which is a position
    the stage can actually be at, so a broken read and a real measurement were
    the same number in the dataset.
    """

    def __init__(self, axes=(), probe_name="", probe_tilt_angle=0.0):
        self.axes = tuple(axes or ())
        self.probe_name = probe_name
        self.probe_tilt_angle = probe_tilt_angle
        self.times = []
        self.red_values = []
        self.positions = {axis: [] for axis in self.axes}
        self.velocities = {axis: [] for axis in self.axes}
        self.position_ages = []
        self._lock = threading.Lock()

    def __len__(self):
        return len(self.red_values)

    @property
    def headers(self):
        """Units in every header, and never the word "Stepper": the position
        source is a probe of whatever family, named once in the sidecar."""
        header = [plot_data.TIME_COLUMN, plot_data.RED_COLUMN]
        for axis in self.axes:
            header.append(f"{axis.lower()}_position_{plot_data.POSITION_UNIT}")
            header.append(f"{axis.lower()}_velocity_{plot_data.VELOCITY_UNIT}")
        header.append(plot_data.AGE_COLUMN)
        return header

    def add(self, t_s, red, positions=None, velocities=None, position_age=None):
        """Append one sample. `positions`/`velocities` are read, never kept —
        the caller reuses its own dictionaries frame after frame."""
        with self._lock:
            self.times.append(t_s)
            self.red_values.append(red)
            self.position_ages.append(position_age)
            for axis in self.axes:
                self.positions[axis].append(
                    positions.get(axis) if positions else None)
                self.velocities[axis].append(
                    velocities.get(axis) if velocities else None)

    def save(self, path):
        """Write the CSV: a header row, then samples. Nothing before the
        header — REDPERCENT-22's `# Metadata` block was four DATA rows that a
        default `pandas.read_csv` took as the column names."""
        with self._lock:
            with open(path, "w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(self.headers)
                for index in range(len(self.red_values)):
                    row = [self.times[index], self.red_values[index]]
                    for axis in self.axes:
                        row.append(self.positions[axis][index])
                        row.append(self.velocities[axis][index])
                    row.append(self.position_ages[index])
                    writer.writerow(row)
        return Path(path)


class MonitorRun:
    """One run: what was configured, what it owns, and what it achieved.

    Created by `RedMonitor.start_run`, never reused and never mutated by a
    view. `axes` and `region` are copied rather than aliased, so nothing the
    operator does afterwards can reach a run in flight (REDPERCENT-1).

    The generation counter the old code carried alongside this is gone. It
    existed because a stop only cleared a flag, so a Stop/Start race could
    leave the previous thread alive with its own stop event unset. A run owns
    its stop event now and `start_run` ends the previous run before building a
    new one, so "is this thread's run still the current one" is the same
    question as "is this run's stop event clear".
    """

    def __init__(self, *, axes, region, probe_name, probe_tilt_angle, run_id,
                 output_root, annotations, source_name, baseline_red,
                 sample_mode, sample_interval_s, red_threshold):
        self.axes = tuple(axes or ())
        self.region = dict(region) if region else None
        self.probe_name = probe_name
        self.probe_tilt_angle = probe_tilt_angle
        self.run_id = run_id
        self.output_root = Path(output_root)
        self.annotations = dict(annotations or {})
        self.source_name = source_name
        self.baseline_red = baseline_red
        self.sample_mode = sample_mode
        self.sample_interval_s = sample_interval_s
        self.red_threshold = dict(red_threshold)

        self.log = RunLog(self.axes, probe_name, probe_tilt_angle)
        self.stop_event = threading.Event()
        self.thread = None

        #: Initialised here, once. A `hasattr` check inside the thread let the
        #: previous run's last value decide this run's first dedup (REDPERCENT-2).
        self.last_logged_red = None
        #: `{axis: (value, position_time)}` of the last *distinct* position
        #: sample, for velocity. Never differenced across identical samples.
        self.last_position = {}
        self.last_position_time = None

        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.started_monotonic = time.monotonic()
        self.stopped_at = None
        self.duration_s = 0.0
        self.failure = None

        self.frames = 0
        self.rows = 0
        self.grab_failures = 0
        self._interval_total = 0.0
        self._interval_count = 0
        self.max_frame_interval = 0.0

    @property
    def is_active(self):
        """True from creation until ended or failed. The one thing
        `RedMonitor.is_running` is derived from, so the flag and the thread
        cannot disagree (REDPERCENT-4)."""
        return not self.stop_event.is_set() and self.failure is None

    @property
    def mean_frame_interval(self):
        return (self._interval_total / self._interval_count
                if self._interval_count else 0.0)

    @property
    def frame_rate(self):
        mean = self.mean_frame_interval
        return 1.0 / mean if mean > 0 else 0.0

    def note_frame(self, interval):
        self.frames += 1
        if interval is None:
            return
        self._interval_total += interval
        self._interval_count += 1
        if interval > self.max_frame_interval:
            self.max_frame_interval = interval

    def end(self):
        """Latch the stop. Idempotent, safe from any thread, and free of I/O —
        which is what lets `estop` call it without ever blocking."""
        self.stop_event.set()
        if self.stopped_at is None:
            self.stopped_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            self.duration_s = time.monotonic() - self.started_monotonic


class RedMonitor(Model):
    """Was `RedPercentSystem`. Owns a `Screen`. Follows a Probe for position."""

    NAME = "Red Percent"
    IDENTITY = None
    NEEDS_PORT = False
    NEEDS_GAMEPAD = False

    #: The firmware streams position at a fixed 10 Hz. It is not a sampling
    #: choice this model gets to make, and it is why velocity is differenced
    #: between *position* samples rather than between rows: at `every_frame`
    #: there are tens of rows per position update, and differencing across
    #: them would divide a zero displacement by a real interval and report a
    #: velocity of zero for a moving stage.
    POSITION_RATE_HZ = 10.0

    #: A row per captured frame / a row when the red percent changes / a row
    #: every `sample_interval_s`. The owner's ruling: fastest possible by
    #: default, the other two kept for a long unattended run.
    SAMPLE_MODES = ("every_frame", "change", "fixed")

    #: The change `sample_mode="change"` triggers on, in percentage points.
    CHANGE_STEP = 0.1

    AXES = ("X", "Y", "Z")
    _AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}

    #: How long the run thread gets to leave before close() gives up on it.
    JOIN_BUDGET = 2.0

    #: What the operator INTENDED, as data rather than as attributes
    #: (REDPERCENT-23). Adding a field for the next experiment is a line here;
    #: the schema, the snapshot and the sidecar all follow from it. These never
    #: hold what the station actually did — that is `_station_meta`.
    ANNOTATION_FIELDS = (
        Param("specimen_id", "text", default="", label="Specimen ID"),
        Param("consumable_id", "text", default="", label="Tip / Consumable ID"),
        Param("stage_x", "float", default=0.0, decimals=3, label="Stage X"),
        Param("stage_y", "float", default=0.0, decimals=3, label="Stage Y"),
        Param("note", "text", default="", label="Note"),
    )

    PARAMS = {
        p.name: p for p in (
            Param("run_name", "text", default="", label="Run / Cut ID"),
            Param("probe_name", "text", default="", label="Probe Name"),
            Param("probe_tilt_angle", "float", default=0.0, decimals=2,
                  unit="deg", label="Probe Tilt Angle"),
            *ANNOTATION_FIELDS,
            Param("red_min", "int", default=150, minimum=0, maximum=255,
                  label="Red at least"),
            Param("green_max", "int", default=100, minimum=0, maximum=255,
                  label="Green at most"),
            Param("blue_max", "int", default=100, minimum=0, maximum=255,
                  label="Blue at most"),
            Param("sample_interval_s", "float", default=0.05, minimum=0.001,
                  maximum=60.0, decimals=3, unit="s",
                  label="Fixed Sample Interval"),
            Param("sample_mode", "text", default="every_frame",
                  label="Sample Mode"),
            Param("sync_axes", "text", default="", label="Synced Axes"),
            Param("run_id", "text", default="", label="Run ID"),
            Param("current_red", "float", default=0.0, decimals=2, unit="%",
                  label="Current Red"),
            Param("red_change", "float", default=0.0, decimals=2, unit="%",
                  label="Red Change"),
            Param("frame_rate", "float", default=0.0, decimals=1, unit="Hz",
                  label="Frame Rate"),
            Param("position_age", "float", default=0.0, decimals=3, unit="s",
                  label="Position Age"),
            Param("rows_written", "int", default=0, label="Rows"),
            Param("frames_captured", "int", default=0, label="Frames"),
        )
    }

    def __init__(self, port=None, gamepad=None, sim=False, screen=None):
        super().__init__()
        self.screen = screen if screen is not None else Screen()
        self.sim = sim

        self.region = None
        self.output_root = self._default_output_root()
        self.baseline_red = 0.0

        #: REDPERCENT-5: one attribute holding the pair, swapped in one
        #: assignment, so every poller sees the old pair or the new one and
        #: never a torn mix. `current_red`/`red_change` are views onto it.
        self._red_state = (0.0, 0.0)

        self._run = None
        self._sources = {}
        self._source = None
        self.source_name = None
        self._saved_rows = 0

        # -- the analysis plot, for all three views ------------------------
        self._loaded = None
        self._loaded_path = None
        self._plot_type = "0D"
        self._plot_dims = (None, None, None)

    # -- Panel plumbing ----------------------------------------------------
    def _defaults(self):
        """Every Param seeds an attribute — except the ones this class exposes
        as a derived property. `current_red`/`red_change` are a published pair,
        `run_id` falls back to a timestamp slug, `frame_rate` is measured. They
        are declared as Params so the schema carries their type, unit and
        display precision (REDPERCENT-19), not so a view can write them."""
        return {name: value for name, value in super()._defaults().items()
                if not isinstance(getattr(type(self), name, None), property)}

    @property
    def devices(self):
        return [self.screen]

    @property
    def mode_name(self):
        return "running" if self.is_running else "idle"

    @property
    def is_active(self):
        return self.is_running

    # -- run identity and artifacts ---------------------------------------
    @staticmethod
    def _default_output_root():
        """`TRANSFER_STAGE_DATA_ROOT`, or `~/transfer-stage-runs`. Absolute,
        and never the process CWD — the three launchers start in three
        different directories and the run's output location is not allowed to
        be one of the things that varies between them (REDPERCENT-21)."""
        configured = os.environ.get("TRANSFER_STAGE_DATA_ROOT")
        if configured:
            return Path(configured).expanduser().resolve()
        return (Path.home() / "transfer-stage-runs").resolve()

    @property
    def run_id(self):
        """The active run's identity, else the operator's name for the next
        one, else a timestamp slug. Never empty: an unattended stop is exactly
        the run whose bench notes are thinnest, so it is the one that most
        needs a name."""
        if self._run is not None:
            return self._run.run_id
        return (self.run_name or "").strip() or time.strftime("run_%Y%m%d_%H%M%S")

    @property
    def run_dir(self):
        """`output_root/<run_id>/`. Absolute, always."""
        if self._run is not None:
            return self._run.output_root / self._run.run_id
        return Path(self.output_root) / self.run_id

    # -- live readouts -----------------------------------------------------
    @property
    def current_red(self):
        return self._red_state[0]

    @property
    def red_change(self):
        return self._red_state[1]

    @property
    def is_running(self):
        """Derived from the run, never a free boolean — it cannot disagree
        with whether a thread is actually running, because it is not a second
        thing that has to be kept in sync with one (REDPERCENT-4)."""
        run = self._run
        return bool(run is not None and run.is_active)

    @property
    def frame_rate(self):
        run = self._run
        return round(run.frame_rate, 1) if run is not None else 0.0

    @property
    def rows_written(self):
        run = self._run
        return run.rows if run is not None else 0

    @property
    def frames_captured(self):
        run = self._run
        return run.frames if run is not None else 0

    @property
    def position_age(self):
        """Seconds since the probe's latest position sample, or None when
        there is no source or it has never reported one."""
        source = self._source
        if source is None:
            return None
        try:
            age = source.position_age
        except Exception:
            return None
        return None if age is None else float(age)

    @property
    def has_unsaved_data(self):
        run = self._run
        rows = len(run.log) if run is not None else 0
        return rows > self._saved_rows

    @property
    def series(self):
        """The live plot's data: red percent against seconds since Start."""
        run = self._run
        if run is None:
            return {"x": [], "y": []}
        log = run.log
        values = list(log.red_values)
        times = list(log.times)[:len(values)]
        return {"x": times, "y": values[:len(times)]}

    # -- the probe this follows -------------------------------------------
    @staticmethod
    def _is_position_source(model):
        """A duck-type, not a device name. `position_time` is the member that
        makes velocity possible at all, so a model that has it is a source and
        one that does not is not — whatever family of probe it belongs to."""
        return (hasattr(model, "position")
                and hasattr(model, "position_time")
                and model is not None)

    @property
    def source_options(self):
        """Live position sources, in the order the Controller added them, so
        every frontend offers the same list in the same order."""
        return list(self._sources)

    def on_model_added(self, name, model):
        """A model appeared. Follow it if it can report a position.

        The launchers each assigned this dict from outside and nothing updated
        it afterwards, so a released probe stayed selected and the loop logged
        its last position forever with no warning (REDPERCENT-11, PYSIDE-3,
        STEPPER-13)."""
        if not self._is_position_source(model):
            return
        self._sources[name] = model
        events.debug("Source Appeared", name, source=self.NAME)
        self._reselect()

    def on_model_removed(self, name, model=None):
        if self._sources.pop(name, None) is None:
            return
        events.debug("Source Gone", name, source=self.NAME)
        if self.source_name == name:
            self._source, self.source_name = None, None
        self._reselect()

    def set_source(self, name):
        """Choose which probe's position lands in the next row."""
        if name not in self._sources:
            raise Refused(f"{name!r} is not an available position source")
        if name == self.source_name:
            return name
        self._source = self._sources[name]
        self.source_name = name
        # The operator who did not click this themselves — the `_reselect`
        # path after a probe went away — has no other way to learn that the
        # source under them just changed (ERRORS-7).
        events.info("Position Source Changed",
                    f"position now read from: {name}", source=self.NAME)
        self._touch()
        return name

    def _reselect(self):
        """Hold a live selection whenever one is available. Ties break in
        Controller order, so the choice is the same in every frontend and does
        not depend on build order."""
        if self.source_name in self._sources:
            self._source = self._sources[self.source_name]
            return
        names = self.source_options
        if not names:
            self._source, self.source_name = None, None
            self._touch()
            return
        self.set_source(names[0])

    # -- configuration commands -------------------------------------------
    def set_region(self, x, y, width, height):
        """The rectangle to watch. mss-shaped, so `Screen.grab` reads only
        this and never the whole screen followed by a crop."""
        if self.is_running:
            raise Refused("The capture region is fixed for the duration of a run.")
        try:
            left, top = int(x), int(y)
            wide, high = int(width), int(height)
        except (TypeError, ValueError):
            raise Refused(f"Not a region: {(x, y, width, height)!r}")
        if wide <= 0 or high <= 0:
            raise Refused(f"A region needs a positive size, not {wide}x{high}")
        self.region = {"top": top, "left": left, "width": wide, "height": high}
        events.debug("Region", sch.format_region(self.region), source=self.NAME)
        self._touch()
        return dict(self.region)

    def set_sync(self, axis):
        """Toggle one axis in or out of `sync_axes`.

        Nine members — three properties, three setters, three toggle commands,
        each with its own copy of the mid-run guard — become this. The loop no
        longer reads `sync_axes` at all (it reads the run's frozen tuple), but
        a toggle that slips through the rendering gate still has to refuse:
        silently ignoring an operator's click is its own defect
        (REDPERCENT-1, VIEW-TKINTER-15)."""
        axis = str(axis).upper()
        if axis not in self.AXES:
            raise Refused(f"{axis!r} is not an axis")
        if self.is_running:
            raise Refused("Synced axes are fixed for the duration of a run.")
        current = list(self.sync_axes_list)
        if axis in current:
            current.remove(axis)
        else:
            current.append(axis)
        self.sync_axes = ",".join(a for a in self.AXES if a in current)
        events.debug("Sync", f"synced axes now {self.sync_axes or 'none'}",
                     source=self.NAME)
        self._touch()
        return self.sync_axes

    @property
    def sync_axes_list(self):
        return [a for a in self.AXES if a in (self.sync_axes or "")]

    @property
    def is_sync_x(self):
        return "X" in self.sync_axes_list

    @property
    def is_sync_y(self):
        return "Y" in self.sync_axes_list

    @property
    def is_sync_z(self):
        return "Z" in self.sync_axes_list

    def set_sample_mode(self, mode):
        """`every_frame` (default), `change`, or `fixed`."""
        if self.is_running:
            raise Refused("The sample mode is fixed for the duration of a run.")
        if mode not in self.SAMPLE_MODES:
            raise Refused(f"{mode!r} is not a sample mode "
                          f"({', '.join(self.SAMPLE_MODES)})")
        self.sample_mode = mode
        events.debug("Sample Mode", mode, source=self.NAME)
        self._touch()
        return mode

    @property
    def sample_mode_options(self):
        return list(self.SAMPLE_MODES)

    @property
    def red_threshold(self):
        """The `_measure_red` decision, named so the sidecar can record it.
        Two runs with the same red percent and different thresholds are not
        comparable."""
        return {"r_min": int(self.red_min), "g_max": int(self.green_max),
                "b_max": int(self.blue_max)}

    @property
    def annotations(self):
        return {field.name: getattr(self, field.name, field.default)
                for field in self.ANNOTATION_FIELDS}

    def reset_baseline(self):
        self.baseline_red = self.current_red
        if self._run is not None:
            self._run.baseline_red = self.baseline_red
        events.info("Baseline Reset", f"baseline is now {self.baseline_red:.2f}%",
                    source=self.NAME)
        return self.baseline_red

    # -- the run -----------------------------------------------------------
    def start_run(self, confirmed=False):
        """Freeze the configuration into a `MonitorRun` and start its thread.

        Refuses outright for the things that make a run impossible; asks for
        the things that make one *useless but possible*, because the operator
        is at the bench and may well mean it — a red-only run with no probe
        attached is a legitimate thing to record.
        """
        self._guard("Start")
        if self.is_running:
            raise Refused("A run is already active.")
        if not self.region:
            raise Refused("Set a capture region before starting a run.")
        if numpy is None:
            raise Refused("Red detection is unavailable: numpy failed to import.")
        if not self.screen.is_available:
            raise Refused(self.screen.error
                          or "Screen capture is unavailable in this environment.")
        if not self.screen.is_open:
            self.screen.open()
            if not self.screen.is_open:
                raise Refused(self.screen.error or "Screen capture would not open.")

        axes = self.sync_axes_list
        doubts = []
        if self._source is None:
            doubts.append("no position source is selected, so every position "
                          "cell will be empty")
        if not axes:
            doubts.append("no axis is synced, so the run records red percent only")
        pending = self._pending_rows
        if pending:
            doubts.append(f"{pending} unsaved row(s) from run "
                          f"'{self.run_id}' will be autosaved first")
        if doubts and not confirmed:
            raise NeedsConfirm(
                "Start the run anyway?\n\n- " + "\n- ".join(doubts),
                "start_run")

        if pending:
            self._autosave("a new run is starting")

        run = MonitorRun(
            axes=axes,
            region=self.region,
            probe_name=self.probe_name,
            probe_tilt_angle=self.probe_tilt_angle,
            run_id=self.run_id,
            output_root=self.output_root,
            annotations=self.annotations,
            source_name=self.source_name,
            baseline_red=None,          # taken from this run's first frame
            sample_mode=self.sample_mode,
            sample_interval_s=float(self.sample_interval_s),
            red_threshold=self.red_threshold,
        )
        self._run = run
        self._saved_rows = 0
        self._red_state = (0.0, 0.0)

        run.thread = threading.Thread(target=self._run_loop, args=(run,),
                                      daemon=True, name=f"red-monitor-{run.run_id}")
        run.thread.start()
        events.info("Run Started", f"{run.run_id}: {run.sample_mode} sampling of "
                    f"{sch.format_region(run.region)}", source=self.NAME)
        events.debug("Run Configuration", json.dumps(self._station_meta(),
                     default=str), source=self.NAME)
        self._touch()
        return run.run_id

    def end_run(self):
        """Latch the current run's stop and return. Never blocks, never joins —
        that is `close()`'s job, with a budget."""
        run = self._run
        if run is None or not run.is_active:
            return False
        run.end()
        events.info("Run Ended", f"{run.run_id}: {run.rows} row(s) from "
                    f"{run.frames} frame(s) in {run.duration_s:.2f} s "
                    f"({run.frame_rate:.1f} Hz)", source=self.NAME)
        self._touch()
        return True

    def _halt_hardware(self):
        """The strongest stop this model has: end the run. No I/O, no lock
        held across I/O, no join — so `estop` can never be held by a screen
        grab that is still in flight."""
        self.end_run()
        return True

    def _start_threads(self):
        """A run is started by the operator, not by opening the model."""

    def _stop_threads(self):
        run = self._run
        if run is None:
            return
        run.end()
        thread = run.thread
        if thread is None or not thread.is_alive():
            return
        started = time.monotonic()
        thread.join(timeout=self.JOIN_BUDGET)
        events.debug("Run Thread", f"join took "
                     f"{(time.monotonic() - started) * 1000:.0f} ms",
                     source=self.NAME)
        if thread.is_alive():
            events.warn("Run Thread Still Running",
                        f"the run thread did not stop within {self.JOIN_BUDGET}s; "
                        "it may still be capturing and writing to this run's log",
                        source=self.NAME)

    def close(self):
        """Stop, then autosave whatever the run was still holding (D-10).

        The ordered hardware close is inherited and untouched; this only adds
        the autosave after it, because the run's only copy of its data must not
        be dropped by a window closing."""
        super().close()
        if self._pending_rows:
            self._autosave("the model is closing")

    # -- the loop ----------------------------------------------------------
    def _run_loop(self, run):
        """The run thread's body. Runs entirely off `run` — its own frozen
        configuration, its own stop event, its own log — never the model's live
        attributes, so a second run cannot alias state with this one
        (REDPERCENT-1, REDPERCENT-3).

        The whole body is wrapped: any failure is recorded on `run.failure`
        (which is what makes `is_running` read False afterwards) and reported
        once. The old loop had no handler at all, so a failure left the UI
        showing "monitoring" forever with a dead thread (REDPERCENT-4).
        """
        screen, region, axes = self.screen, run.region, run.axes
        stop, threshold = run.stop_event, run.red_threshold
        mode, interval = run.sample_mode, run.sample_interval_s
        # Reused every frame: `RunLog.add` reads them and keeps nothing, so
        # the hot path allocates no dictionary per sample.
        positions = {axis: None for axis in axes}
        velocities = {axis: None for axis in axes}
        previous_frame = None

        events.debug("Run Loop", f"started: mode={mode} axes={axes or 'none'} "
                     f"threshold={threshold}", source=self.NAME)
        try:
            while not stop.is_set():
                frame = screen.grab(region)
                if frame is None:
                    run.grab_failures += 1
                    events.debug("Grab Returned Nothing",
                                 f"{run.grab_failures} so far this run",
                                 source=self.NAME, every=1.0)
                    if stop.wait(0.05):
                        break
                    previous_frame = None   # the gap across a failure is not
                    continue                # a frame interval worth averaging

                now = time.monotonic()
                run.note_frame(None if previous_frame is None
                               else now - previous_frame)
                previous_frame = now

                red = self._measure_red(frame, threshold)
                if run.baseline_red is None:
                    run.baseline_red = red
                    self.baseline_red = red
                    events.info("Baseline Set", f"{red:.2f}% at run start",
                                source=self.NAME)
                self._publish_red(red)

                if self._wants_row(run, red):
                    run.last_logged_red = round(red, 1)
                    age = self._read_position(run, axes, positions, velocities)
                    run.log.add(now - run.started_monotonic, red, positions,
                                velocities, age)
                    run.rows += 1

                events.debug("Rate", f"{run.frames} frames, {run.rows} rows, "
                             f"{run.frame_rate:.1f} Hz, "
                             f"max gap {run.max_frame_interval * 1000:.1f} ms",
                             source=self.NAME, every=1.0)

                if mode == "fixed" and stop.wait(interval):
                    break
        except Exception as exc:
            run.failure = exc
            events.error("Red Percent Run Failed",
                         f"run {run.run_id} stopped unexpectedly: {exc}",
                         source=self.NAME, exception=exc)
        finally:
            run.end()
            events.debug("Run Loop", f"exited after {run.frames} frame(s), "
                         f"{run.rows} row(s), {run.grab_failures} grab "
                         f"failure(s)", source=self.NAME)

    def _wants_row(self, run, red):
        if run.sample_mode == "change":
            rounded = round(red, 1)
            return (run.last_logged_red is None
                    or abs(rounded - run.last_logged_red) >= self.CHANGE_STEP)
        return True     # every_frame and fixed: the loop's pace IS the rate

    def _measure_red(self, frame, threshold=None):
        """The red fraction of `frame`, as a percentage.

        Reads the BGRA buffer the screenshot already holds — no PIL round-trip
        and no per-frame RGB copy. A plain `numpy` array is accepted too, RGB
        or BGRA, which is how the detector stays testable without a display.
        """
        if frame is None or numpy is None:
            return 0.0
        threshold = threshold or self.red_threshold
        blue, green, red = self._channels(frame)
        if red is None or red.size == 0:
            return 0.0
        matched = numpy.count_nonzero((red > threshold["r_min"])
                                      & (green < threshold["g_max"])
                                      & (blue < threshold["b_max"]))
        return (matched / red.size) * 100.0

    @staticmethod
    def _channels(frame):
        """`(blue, green, red)` planes of a screenshot or an array."""
        buffer = getattr(frame, "bgra", None)
        if buffer is not None:
            pixels = numpy.frombuffer(buffer, dtype=numpy.uint8)
            height = getattr(frame, "height", None)
            width = getattr(frame, "width", None)
            if height is None or width is None:
                width, height = frame.size
            pixels = pixels.reshape(int(height), int(width), 4)
            return pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
        if isinstance(frame, numpy.ndarray) and frame.ndim == 3:
            if frame.shape[2] >= 4:
                return frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
            return frame[:, :, 2], frame[:, :, 1], frame[:, :, 0]
        return None, None, None

    def _publish_red(self, red):
        """Read the baseline exactly once, compute the pair, publish both in
        one assignment (REDPERCENT-5). `red_change` used to read
        `self.baseline_red` twice in one expression, so a `reset_baseline()`
        landing between the two reads mixed a value from two baselines — and
        the pair was two separate statements, so any poller could read the new
        `current_red` beside the previous frame's `red_change`."""
        baseline = self.baseline_red
        change = ((red - baseline) / max(baseline, 0.1)) * 100.0
        self._red_state = (red, change)
        return red, change

    def _read_position(self, run, axes, positions, velocities):
        """Fill `positions`/`velocities` for this row and return the age of
        the position sample they came from.

        One `position_age_s` column per row, not one per axis: the firmware
        streams x, y and z together at 10 Hz, so they share an age, and three
        copies of one number is three chances for them to disagree.
        """
        source = self._source
        if source is None:
            for axis in axes:
                positions[axis] = velocities[axis] = None
            return None
        try:
            position = source.position
            position_time = source.position_time
            age = source.position_age
        except Exception as exc:
            events.debug("Position Read Failed", str(exc), source=self.NAME,
                         exception=exc, every=1.0)
            for axis in axes:
                positions[axis] = velocities[axis] = None
            return None

        is_new = (position_time is not None
                  and position_time != run.last_position_time)
        for axis in axes:
            positions[axis], velocities[axis] = self._read_dim(
                run, axis, position, position_time, is_new)
        if is_new:
            run.last_position_time = position_time
        return None if age is None else float(age)

    def _read_dim(self, run, axis, position, position_time, is_new_sample):
        """One axis's position, and its velocity when — and only when — this
        is a position sample the run has not already seen.

        REDPERCENT-16's reopened half. The old code differenced position
        against the wall clock of the *red* event, so at any capture rate above
        10 Hz most rows differenced two identical position readings across a
        real interval and reported a velocity of zero for a moving stage. A
        velocity is only meaningful between two distinct samples; every other
        row leaves the cell empty, which is honest and is what a reader can
        interpolate from.
        """
        try:
            value = float(position[self._AXIS_INDEX[axis]])
        except (TypeError, ValueError, IndexError, KeyError):
            return None, None       # never 0.0: a real position, not a failure

        previous = run.last_position.get(axis)
        if not is_new_sample:
            return value, None
        run.last_position[axis] = (value, position_time)
        if previous is None:
            return value, None      # nothing to difference the first one against
        previous_value, previous_time = previous
        gap = position_time - previous_time
        if gap <= 0:
            return value, None
        return value, (value - previous_value) / gap

    # -- artifacts ---------------------------------------------------------
    @property
    def _pending_rows(self):
        run = self._run
        rows = len(run.log) if run is not None else 0
        return max(0, rows - self._saved_rows)

    def _station_meta(self, run_id=None):
        """What the station actually DID — the half a CSV column cannot carry.

        `annotations` is what the operator INTENDED. The two are separate keys
        on purpose and must never be merged: the planned-versus-actual
        comparison is the thing the experiment exists to make (REDPERCENT-23).
        """
        run = self._run
        if run is not None:
            return {
                "run_id": run_id or run.run_id,
                "probe_name": run.probe_name,
                "probe_tilt_angle": run.probe_tilt_angle,
                "source_name": run.source_name,
                "sync_axes": list(run.axes),
                "region": dict(run.region) if run.region else None,
                "baseline_red": run.baseline_red,
                "red_threshold": dict(run.red_threshold),
                "sample_mode": run.sample_mode,
                "sample_interval_s": run.sample_interval_s,
                "position_rate_hz": self.POSITION_RATE_HZ,
                "frames_captured": run.frames,
                "rows_written": run.rows,
                "grab_failures": run.grab_failures,
                "mean_frame_interval_s": round(run.mean_frame_interval, 6),
                "max_frame_interval_s": round(run.max_frame_interval, 6),
                "achieved_rate_hz": round(run.frame_rate, 3),
                "duration_s": round(run.duration_s or
                                    (time.monotonic() - run.started_monotonic), 3),
                "started_at": run.started_at,
                "stopped_at": run.stopped_at,
                "annotations": dict(run.annotations),
            }
        return {
            "run_id": run_id or self.run_id,
            "probe_name": self.probe_name,
            "probe_tilt_angle": self.probe_tilt_angle,
            "source_name": self.source_name,
            "sync_axes": self.sync_axes_list,
            "region": dict(self.region) if self.region else None,
            "baseline_red": self.baseline_red,
            "red_threshold": self.red_threshold,
            "sample_mode": self.sample_mode,
            "sample_interval_s": float(self.sample_interval_s),
            "position_rate_hz": self.POSITION_RATE_HZ,
            "frames_captured": 0,
            "rows_written": 0,
            "grab_failures": 0,
            "mean_frame_interval_s": 0.0,
            "max_frame_interval_s": 0.0,
            "achieved_rate_hz": 0.0,
            "duration_s": 0.0,
            "started_at": None,
            "stopped_at": None,
            "annotations": self.annotations,
        }

    def save(self):
        """Write this run's artifacts under `output_root` and report where.

        No caller passes a path. The old `save_log(file_path)` took one from
        whichever view raised a file dialog, which on the Web client meant a
        browser handing the server a path on the server's own filesystem
        (finding 9). A view copies or downloads what this returns.
        """
        run = self._run
        rows = len(run.log) if run is not None else 0
        if not rows:
            raise Refused("No data has been recorded yet; there is nothing to write.")

        run_id = run.run_id
        directory = self.run_dir
        directory.mkdir(parents=True, exist_ok=True)
        csv_path = directory / f"{run_id}_position.csv"
        meta_path = directory / f"{run_id}_station_meta.json"

        started = time.monotonic()
        run.log.save(csv_path)
        meta_path.write_text(json.dumps(self._station_meta(run_id), indent=2,
                                        default=str))
        self._saved_rows = rows
        events.debug("Save", f"{rows} row(s) -> {csv_path} in "
                     f"{(time.monotonic() - started) * 1000:.0f} ms",
                     source=self.NAME)
        events.info("Run Saved", f"{rows} row(s) written to {directory}",
                    source=self.NAME)
        self._touch()
        return str(csv_path)

    def _autosave(self, why):
        """The unattended path: nobody clicked anything, so the console line
        the old code wrote was the only place the run's address ever appeared.
        An autosaved run is exactly the one whose bench notes are thinnest."""
        try:
            path = self.save()
        except Refused:
            return None
        except Exception as exc:
            events.error("Autosave Failed",
                         f"could not autosave the run ({why}): {exc}",
                         source=self.NAME, exception=exc)
            return None
        events.info("Run Autosaved",
                    f"unsaved data was saved automatically ({why}): {path}",
                    source=self.NAME)
        return path

    # -- the analysis plot -------------------------------------------------
    def load_run(self, path):
        """Load a saved CSV for the analysis plot.

        This lived in a PySide-only `PlotDialog`, so Tk and the Web client had
        no way to look at a finished run at all (REDPERCENT-17, PYSIDE-18).
        """
        try:
            loaded = plot_data.load_run(path)
        except OSError as exc:
            raise Refused(f"Could not read {path}: {exc}")
        if not loaded["red_percents"]:
            raise Refused(f"No samples found in {Path(path).name}.")
        self._loaded = loaded
        self._loaded_path = str(path)
        self._plot_type, self._plot_dims = "0D", (None, None, None)
        events.info("Run Loaded", f"{len(loaded['red_percents'])} sample(s), "
                    f"axes {loaded['dims'] or 'none'}, from "
                    f"{Path(path).name}", source=self.NAME)
        events.debug("Run Loaded", f"{loaded['dropped_rows']} unreadable row(s) "
                     f"skipped in {path}", source=self.NAME)
        self._touch()
        return {"samples": len(loaded["red_percents"]),
                "dims": list(loaded["dims"]),
                "dropped_rows": loaded["dropped_rows"],
                "metadata": loaded["metadata"]}

    @property
    def loaded_run(self):
        return Path(self._loaded_path).name if self._loaded_path else None

    @property
    def plot_dims(self):
        """The current selection, as it appears in the dropdown."""
        return self._dim_label(self._plot_type, *self._plot_dims)

    @property
    def plot_dim_options(self):
        """Every plot this run's data can actually produce, and nothing else.

        One dropdown of whole selections rather than a plot-type dropdown plus
        three independent axis dropdowns. The combinations are few (at most
        eight for three axes), and it designs out REDPERCENT-17's shape
        entirely: "2D with dim2 unselected" is not a state this control can be
        left in.
        """
        dims = list(self._loaded["dims"]) if self._loaded else []
        options = [self._dim_label("0D", None, None, None)]
        for first in dims:
            options.append(self._dim_label("1D", first, None, None))
        for index, first in enumerate(dims):
            for second in dims[index + 1:]:
                options.append(self._dim_label("2D", first, second, None))
        if len(dims) >= 3:
            for third in dims[2:]:
                options.append(self._dim_label("3D", dims[0], dims[1], third))
        return options

    def set_plot_dims(self, selection, *rest):
        """Choose the analysis plot's dimensions.

        Takes either one option from `plot_dim_options` (`"2D: X+Y"`) or the
        explicit `(plot_type, dim1, dim2, dim3)` a test or script would pass.
        """
        if rest:
            plot_type = str(selection).upper()
            dims = tuple((list(rest) + [None, None, None])[:3])
        else:
            plot_type, dims = self._parse_dim_label(selection)
        if plot_type not in ("0D", "1D", "2D", "3D"):
            raise Refused(f"{selection!r} is not a plot selection")
        self._plot_type, self._plot_dims = plot_type, dims
        events.debug("Plot Dims", self.plot_dims, source=self.NAME)
        self._touch()
        return self.plot_dims

    @property
    def figure(self):
        """PNG bytes of the analysis plot, rendered once here and displayed by
        every view — rather than Tk, PySide and the Web client each building
        their own matplotlib canvas out of their own copy of the data."""
        loaded = self._loaded
        if loaded is None:
            return plot_data.render_figure("0D", red_percents=[])
        return plot_data.render_figure(
            self._plot_type, *self._plot_dims,
            red_percents=loaded["red_percents"], dim_data=loaded["dim_data"],
            times=loaded["times"])

    @staticmethod
    def _dim_label(plot_type, dim1, dim2, dim3):
        axes = [d for d in (dim1, dim2, dim3) if d]
        return f"{plot_type}: {'+'.join(axes)}" if axes else plot_type

    @staticmethod
    def _parse_dim_label(label):
        text = str(label or "0D").strip()
        head, _, tail = text.partition(":")
        axes = [part.strip().upper() for part in tail.split("+") if part.strip()]
        axes += [None, None, None]
        return head.strip().upper(), tuple(axes[:3])

    # -- state and schema --------------------------------------------------
    @property
    def state(self):
        snapshot = super().state
        run = self._run
        snapshot["run"] = {
            "run_id": self.run_id,
            "is_running": self.is_running,
            "rows": self.rows_written,
            "frames": self.frames_captured,
            "rate_hz": self.frame_rate,
            "has_unsaved_data": self.has_unsaved_data,
            "output_root": str(self.output_root),
            "run_dir": str(self.run_dir),
            "baseline_red": run.baseline_red if run is not None else self.baseline_red,
            "failure": str(run.failure) if run is not None and run.failure else "",
            "source_options": self.source_options,
            "loaded_run": self.loaded_run,
        }
        return snapshot

    @property
    def schema(self):
        P = self.PARAMS
        return sch.schema(
            sch.section(
                "Run",
                sch.entry("Run / Cut ID:", "run_name", P["run_name"],
                          disabled_when=("running",)),
                sch.readonly("Run ID:", "run_id", param=P["run_id"]),
            ),
            sch.section(
                # REDPERCENT-23, D-6: declared once so all three views render
                # it. None of them hand-builds an annotation form.
                "Operator Annotation (intended)",
                *[sch.entry(f"{field.label}:", field.name, P[field.name],
                            disabled_when=("running",))
                  for field in self.ANNOTATION_FIELDS],
            ),
            sch.section(
                "Probe Metadata",
                sch.entry("Probe Name:", "probe_name", P["probe_name"],
                          disabled_when=("running",)),
                sch.entry("Probe Tilt Angle:", "probe_tilt_angle",
                          P["probe_tilt_angle"], disabled_when=("running",)),
                # PYSIDE-7: a dropdown with `model_attr` and no `command`
                # reached `getattr(self.model, None)` and raised TypeError.
                # The v2 builder makes `command` mandatory, so the broken
                # shape is not expressible.
                sch.dropdown("Position Source:", "source_name", "set_source",
                             "source_options", disabled_when=("running",)),
                sch.readonly("Position Age:", "position_age",
                             param=P["position_age"]),
            ),
            sch.section(
                "Synced Axes",
                sch.toggle("Sync X", "is_sync_x", "set_sync", "Sync X: ON",
                           "Sync X: OFF", on_args=("X",), off_args=("X",),
                           disabled_when=("running",)),
                sch.toggle("Sync Y", "is_sync_y", "set_sync", "Sync Y: ON",
                           "Sync Y: OFF", on_args=("Y",), off_args=("Y",),
                           disabled_when=("running",)),
                sch.toggle("Sync Z", "is_sync_z", "set_sync", "Sync Z: ON",
                           "Sync Z: OFF", on_args=("Z",), off_args=("Z",),
                           disabled_when=("running",)),
                sch.readonly("Synced:", "sync_axes", param=P["sync_axes"]),
            ),
            sch.section(
                "Red Detection",
                sch.entry("Red at least:", "red_min", P["red_min"],
                          disabled_when=("running",)),
                sch.entry("Green at most:", "green_max", P["green_max"],
                          disabled_when=("running",)),
                sch.entry("Blue at most:", "blue_max", P["blue_max"],
                          disabled_when=("running",)),
            ),
            sch.section(
                "Sampling",
                sch.dropdown("Sample Mode:", "sample_mode", "set_sample_mode",
                             "sample_mode_options", disabled_when=("running",)),
                sch.entry("Fixed Sample Interval:", "sample_interval_s",
                          P["sample_interval_s"], disabled_when=("running",)),
                sch.readonly("Frame Rate:", "frame_rate", param=P["frame_rate"]),
                sch.readonly("Frames:", "frames_captured",
                             param=P["frames_captured"]),
                sch.readonly("Rows:", "rows_written", param=P["rows_written"]),
            ),
            sch.section(
                "Live",
                # REDPERCENT-19: a declared display precision, rather than each
                # renderer re-deriving one from the box.
                sch.readonly("Current Red:", "current_red",
                             param=P["current_red"], format=".2f"),
                sch.readonly("Red Change:", "red_change", param=P["red_change"],
                             format=".2f"),
                # REDPERCENT-13: published so every client can gate its plotter
                # on "is a run actually active" instead of sampling regardless.
                sch.readonly("Running:", "is_running", role="info"),
                sch.plot("Red % over time", "series", x_label="time (s)",
                         y_label="red (%)"),
            ),
            sch.section(
                "Control",
                sch.region_select("Set Capture Region", "set_region",
                                  model_attr="region", role="info"),
                sch.button("Start", "start_run", inputs=self._entry_names,
                           role="go", disabled_when=("running",)),
                sch.button("Stop", "end_run", role="danger",
                           enabled_when=("running",)),
                sch.button("Reset Baseline", "reset_baseline"),
                sch.file_save("Save", "save", extensions=("csv",), role="info"),
            ),
            sch.section(
                "Analysis",
                sch.file_open("Load Run", "load_run", extensions=("csv",)),
                sch.readonly("Loaded:", "loaded_run"),
                sch.dropdown("Plot:", "plot_dims", "set_plot_dims",
                             "plot_dim_options"),
                sch.image("Analysis Plot", "figure"),
            ),
            self._safety_section(),
        )

    @property
    def _entry_names(self):
        """Every editable field travels with Start (D-5), so a value typed a
        moment ago is frozen into the run rather than being one edit behind.
        Tk hid this by forcing focus away before every command — a named
        anti-fix, because it only ever worked in Tk."""
        return ("run_name", "probe_name", "probe_tilt_angle",
                *[field.name for field in self.ANNOTATION_FIELDS],
                "red_min", "green_max", "blue_max", "sample_interval_s")
