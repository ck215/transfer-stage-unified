"""RGB analysis (was "Red Percent", renamed RG-3 2026-10-07): the colour of a
screen region, logged as a force proxy beside the probe's position. The red
fraction it always measured, and since RG-1 the green and blue fractions and
the channel means beside it.

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

RG-1 (2026-10-07): every settled sample is six numbers, not one. Beside the
red share, `_measure_rgb` takes the green and blue shares (the red mask's
rule turned on the other two channels) and the region's mean red, green and
blue (0-255), in one deinterleave of the frame and a handful of numpy
reductions. The five ride with the red share everywhere a sample goes: the
row a subscriber gets, the run's CSV, `state["run"]["latest"]`, and the
readouts behind Details. The red share is `_measure_red`'s to the bit, so no
bench number moves; which column drives the force extrema is an analysis
setting (`transfer_map_analysis`, `factor=`), not this model's.
`RgbAnalysis` the Model. Owns a `Screen`, follows a Probe for position, and
             owns exactly one run at a time.

The load-bearing repairs carried over from `legacy/src/model/redpercent_system.py`:

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

And the owner's ruling of 2026-09-21 ("sample as fast as the grab allows",
which replaced the old fixed ~60 FPS loop), as amended 2026-10-07 (CAP-1):
sample SETTLED frames at the source's own rate. The bench showed why: AmLite
repaints its camera image at ~7 fps, and a loop grabbing at kHz caught the
repaints part-way (a black fill, an older cached picture, a frame painted only
at the top) in about half of every profile, and the video flashed. A sample is
now two reads at least `SETTLE_S` apart that agree; a black fill or the
remembered stale picture is refused even when it holds still; the loop paces
itself at about `SOURCE_OVERSAMPLE` samples per measured source frame, between
`MIN_SAMPLE_INTERVAL_S` and `MAX_SAMPLE_INTERVAL_S`. A rejected read reaches
nothing but a counter: not the red %, not a row, not a frame subscriber.
"""
import csv
import json
import os
import statistics
import threading
import time
from pathlib import Path

import schema as sch
from devices.screen import Screen
from events import events
from model.base import Model
from model import plot_data
from param import Param
from result import Refused, NeedsConfirm

try:                                  # guarded: a bench box without numpy
    import numpy                      # still starts, and says why it cannot run
except Exception:                     # pragma: no cover - environment specific
    numpy = None

#: RG-1: the five columns a run's CSV carries after `position_age_s`: the
#: green and blue shares (%, beside `red_percent`) and the region's mean red,
#: green and blue (0-255). `plot_data` reads a run by header name, so a run
#: with them loads exactly as one without.
CHANNEL_COLUMNS = ("green_percent", "blue_percent", "r_mean", "g_mean",
                   "b_mean")


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
        #: RG-1: one list per `CHANNEL_COLUMNS` entry, in that order.
        self.channels = tuple([] for _ in CHANNEL_COLUMNS)
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
        header.extend(CHANNEL_COLUMNS)
        return header

    def add(self, t_s, red, positions=None, velocities=None, position_age=None,
            rgb=None):
        """Append one sample. `positions`/`velocities` are read, never kept —
        the caller reuses its own dictionaries frame after frame. `rgb` is
        the sample's six numbers in `RgbAnalysis.RGB_KEYS` order (RG-1); the
        five after the red share fill the channel columns, and without it
        they are empty cells."""
        with self._lock:
            self.times.append(t_s)
            self.red_values.append(red)
            self.position_ages.append(position_age)
            for axis in self.axes:
                self.positions[axis].append(
                    positions.get(axis) if positions else None)
                self.velocities[axis].append(
                    velocities.get(axis) if velocities else None)
            for column, value in zip(self.channels,
                                     rgb[1:] if rgb else (None,) * 5):
                column.append(value)

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
                    row.extend(column[index] for column in self.channels)
                    writer.writerow(row)
        return Path(path)


class MonitorRun:
    """One run: what was configured, what it owns, and what it achieved.

    Created by `RgbAnalysis.start_run`, never reused and never mutated by a
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

        #: CAP-1: what the settle gate did with every read. Counters only: a
        #: rejected read reaches nothing else. `frames` is the accepted count.
        self.grabs = 0
        self.rejected_black = 0
        self.rejected_stale = 0
        self.rejected_unsettled = 0
        #: The gate's memory, the run thread's alone: the last accepted
        #: picture and its red percent, the one remembered stale picture, the
        #: reads since then that no second read confirmed, and the intervals
        #: between successive distinct accepted pictures (the source's frame
        #: period, as measured; a tuple, replaced whole, so a poller reading
        #: `source_rate_hz` never sees it change under it).
        self.accepted_pixels = None
        self.accepted_red = None
        self.stale_pixels = None
        self.unconfirmed = []
        self.source_intervals = ()
        self.last_change_at = None

    @property
    def is_active(self):
        """True from creation until ended or failed. The one thing
        `RgbAnalysis.is_running` is derived from, so the flag and the thread
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

    @property
    def accepted(self):
        """Samples the settle gate let through: `frames`, by the name the
        Diagnostics counters use."""
        return self.frames

    @property
    def source_rate_hz(self):
        """The source's frame rate as measured: one over the median interval
        between successive distinct accepted pictures; 0.0 until there is
        one."""
        intervals = self.source_intervals
        if not intervals:
            return 0.0
        median = statistics.median(intervals)
        return 1.0 / median if median > 0 else 0.0

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


class RgbAnalysis(Model):
    """Was `RedMonitor` ("Red Percent" to the operator, until RG-3), and
    before that `RedPercentSystem`. Owns a `Screen`. Follows a Probe for
    position."""

    #: The registry key and the name every operator sees (RG-3, 2026-10-07:
    #: "Red Percent" became "RGB Analysis"). Setup's MODEL_TYPES, the
    #: Controller, the telemetry slug (`rgb_analysis`), the stop's tooltip
    #: and every event's source follow from it. The stored databases are
    #: untouched: the profile column is still `red`.
    NAME = "RGB Analysis"
    #: The words on the tier-2 disclosure (sentence case, as the views draw
    #: every disclosure).
    DISCLOSURE = "RGB analysis details"
    IDENTITY = None
    HOST = "Transfer Map"    # drawn on the Transfer Map page (one dashboard)
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
    #: Green and blue caps of the red mask. Fixed (the operator tunes red
    #: only); today's values, so the measurement is unchanged.
    GREEN_MAX = 100
    BLUE_MAX = 100
    # -- RG-1: the green and blue shares (2026-10-07) ---------------------
    #: The green and blue masks are the red mask's rule turned on the other
    #: two channels: the channel ABOVE its threshold and the other two BELOW
    #: their caps, both strict. Fixed, as the red mask's caps are (the
    #: operator tunes red only): each threshold is `red_min`'s default and
    #: each cap the red mask's own, so a pure green or blue pixel counts the
    #: way a pure red one does. Recorded in the sidecar (`channel_thresholds`).
    GREEN_MIN = 150
    BLUE_MIN = 150
    #: The red cap of the green and blue masks (GREEN_MAX and BLUE_MAX above
    #: are the other two caps).
    RED_MAX = 100
    #: A sample's six numbers, in this order everywhere they travel: the red,
    #: green and blue shares (% of the region's pixels each mask passes) and
    #: the region's mean red, green and blue (0-255).
    RGB_KEYS = ("red", "green", "blue", "r_mean", "g_mean", "b_mean")
    #: The five a row adds beside the red share it already carried: the keys
    #: of a subscriber's row dict, read with `.get`.
    CHANNEL_KEYS = RGB_KEYS[1:]
    #: One sampling mode: a row when the red percentage changes (owner ruling
    #: 2026-09-22). Since 2026-10-07 the loop samples settled frames at about
    #: the source's rate (CAP-1, below), not as fast as it can grab.
    SAMPLE_MODE = "change"

    # -- CAP-1: the settle gate and the rate cap (owner ruling 2026-10-07) --
    #: A sample is two reads of the region at least this far apart (from the
    #: end of one to the start of the next) that show the same picture. The
    #: bench's repaint (black fill -> an older cached picture -> live) runs
    #: its course in 2-6 ms, so two reads 5 ms apart do not both land in one
    #: state of it; a read the next one disagrees with is discarded, and the
    #: next is checked against its own successor.
    SETTLE_S = 0.005
    #: "The same picture": at most this fraction of the pixels differ. It is
    #: half of CHANGE_STEP as a fraction, so two reads that pass measure
    #: within 0.05 points of red % of each other and can never fall either
    #: side of a logged row, while every transient the bench showed (a black
    #: fill, the stale picture, a part-painted frame) differs in most of its
    #: pixels. A still picture reads back byte-equal; the slack is for a
    #: cursor-sized overlay animating inside the region, which would
    #: otherwise keep the gate shut for good.
    SETTLE_TOLERANCE = 0.0005
    #: Reads one sample may take before it is given up until the next tick:
    #: five settle gaps, several times the bench's whole repaint. A viewer
    #: still not holding still by then is not re-read at once.
    SETTLE_READS = 6
    #: A settled picture with every channel at or below this (of 255) is a
    #: dark fill, not a lit microscope field (the bench scene sits near 140):
    #: refused when it has no red and the last accepted picture had some. An
    #: all-zero picture is refused always.
    DARK_LEVEL = 32
    #: The rate cap. One sample starts SOURCE_OVERSAMPLE times per frame of
    #: the source, its period measured as the median interval between the
    #: last SOURCE_WINDOW distinct accepted pictures: two a frame lands a
    #: sample inside every frame the viewer shows, and any more only reads
    #: the same picture again, or catches its repaint.
    SOURCE_OVERSAMPLE = 2.0
    SOURCE_WINDOW = 9
    #: The floor under the interval: never more than 60 samples a second,
    #: whatever is measured. A display refreshes at 60 Hz, so a faster read
    #: sees what the last one saw or a repaint in progress; this is what
    #: keeps a flickering or mis-measured viewer from being spun on.
    MIN_SAMPLE_INTERVAL_S = 1.0 / 60.0
    #: The ceiling over it: never fewer than 15 samples a second. Used until
    #: a source rate is measured and while the picture is not changing (a
    #: stalled viewer, a long static hover), so that one is read at this
    #: steady rate rather than spun on, and a picture that moves again is
    #: seen within 67 ms. It is also the Transfer Map's video rate
    #: (VIDEO_FPS). The bench's AmLite, at 7.1 fps, is sampled here.
    MAX_SAMPLE_INTERVAL_S = 1.0 / 15.0

    #: The change `sample_mode="change"` triggers on, in percentage points.
    CHANGE_STEP = 0.1

    AXES = ("X", "Y", "Z")
    _AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}

    #: What the operator INTENDED, as data rather than as attributes
    #: (REDPERCENT-23). Adding a field for the next experiment is a line here;
    #: the schema, the snapshot and the sidecar all follow from it. These never
    #: hold what the station actually did — that is `_station_meta`.
    ANNOTATION_FIELDS = (
        Param("specimen_id", "text", default="", label="Specimen ID"),
        Param("consumable_id", "text", default="", label="Tip / Consumable ID"),
        Param("note", "text", default="", label="Note"),
    )

    PARAMS = {
        p.name: p for p in (
            Param("run_name", "text", default="", label="Run / cut ID"),
            Param("probe_name", "text", default="", label="Probe Name"),
            Param("probe_tilt_angle", "float", default=0.0, decimals=2,
                  unit="deg", label="Probe Tilt Angle"),
            *ANNOTATION_FIELDS,
            Param("red_min", "int", default=150, minimum=0, maximum=255,
                  label="Red at least"),
            Param("sync_axes", "text", default="", label="Synced Axes"),
            Param("run_id", "text", default="", label="Run ID"),
            Param("current_red", "float", default=0.0, decimals=2, unit="%",
                  label="Current Red"),
            Param("red_change", "float", default=0.0, decimals=2, unit="%",
                  label="Red Change"),
            # RG-1: the other five numbers of the latest sample (Details).
            Param("current_green", "float", default=0.0, decimals=2, unit="%",
                  label="Current Green"),
            Param("current_blue", "float", default=0.0, decimals=2, unit="%",
                  label="Current Blue"),
            Param("mean_red", "float", default=0.0, decimals=1,
                  label="Mean Red"),
            Param("mean_green", "float", default=0.0, decimals=1,
                  label="Mean Green"),
            Param("mean_blue", "float", default=0.0, decimals=1,
                  label="Mean Blue"),
            Param("frame_rate", "float", default=0.0, decimals=1, unit="Hz",
                  label="Frame Rate"),
            Param("position_age", "float", default=0.0, decimals=3, unit="s",
                  label="Position Age"),
            Param("rows_written", "int", default=0, label="Rows"),
            Param("frames_captured", "int", default=0, label="Frames"),
            # CAP-1: the settle gate's counters (Diagnostics).
            Param("frames_accepted", "int", default=0, label="Accepted"),
            Param("rejected_black", "int", default=0, label="Rejected black"),
            Param("rejected_stale", "int", default=0, label="Rejected stale"),
            Param("rejected_unsettled", "int", default=0,
                  label="Rejected unsettled"),
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
        #: RG-1: the latest accepted sample's six numbers (RGB_KEYS order),
        #: one tuple swapped in one assignment like the pair above; None
        #: until a run accepts its first sample.
        self._rgb_state = None

        self._run = None
        #: MAP-2: callables fed `(t_s, red, row)` for every row the run log
        #: appends, `row` the positions by axis and the five RG-1 numbers. A
        #: tuple, replaced whole, so the run thread reads it without a lock
        #: and never sees a list change under it.
        self._subscribers = ()
        #: V3 (2026-09-28): callables fed `(t_s, frame, red)` for every frame
        #: the loop accepts and measures (the Transfer Map's video; never a
        #: rejected read, CAP-1). Same discipline: a tuple, replaced whole,
        #: read without a lock.
        self._frame_subscribers = ()
        self._sources = {}
        self._source = None
        self.source_name = None
        self._saved_rows = 0

        # -- the analysis plot, for all three views ------------------------
        self._loaded = None
        self._loaded_path = None
        self._plot_type = "0D"
        self._plot_dims = (None, None, None)
        self._figure_cache = None     # (key, loaded, png), see `figure`

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

    #: The analysis figure's size (inches) and resolution; `plot_data`'s
    #: defaults, named here so a test or a view build can change them.
    FIGURE_SIZE = plot_data.FIGURE_SIZE
    FIGURE_DPI = plot_data.FIGURE_DPI

    @property
    def mode_name(self):
        return "running" if self.is_running else "idle"

    @property
    def next_step(self):
        """What unblocks Start run, or "" (quiet) when nothing does (L3)."""
        if self.gate_mode == "no_region":
            return "Set a capture region to start a run."
        return ""

    @property
    def gate_mode(self):
        """`latched` outranks everything; then `no_region` while idle with no
        capture region, so Start run is greyed out until it can start (F11)."""
        token = super().gate_mode
        if token == "idle" and not self.region:
            return "no_region"
        return token

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

    # -- RG-1: the other five numbers of the latest sample -----------------
    @property
    def latest(self):
        """The latest accepted sample as `{key: value}` in RGB_KEYS order;
        every value None before a run accepts one."""
        rgb = self._rgb_state
        return dict(zip(self.RGB_KEYS, rgb if rgb is not None
                        else (None,) * len(self.RGB_KEYS)))

    def _latest(self, index):
        rgb = self._rgb_state
        return None if rgb is None else rgb[index]

    @property
    def current_green(self):
        return self._latest(1)

    @property
    def current_blue(self):
        return self._latest(2)

    @property
    def mean_red(self):
        return self._latest(3)

    @property
    def mean_green(self):
        return self._latest(4)

    @property
    def mean_blue(self):
        return self._latest(5)

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

    # -- CAP-1: the settle gate's counters, for Diagnostics ----------------
    def _run_count(self, name):
        run = self._run
        return getattr(run, name) if run is not None else 0

    @property
    def frames_accepted(self):
        return self._run_count("frames")

    @property
    def rejected_black(self):
        return self._run_count("rejected_black")

    @property
    def rejected_stale(self):
        return self._run_count("rejected_stale")

    @property
    def rejected_unsettled(self):
        return self._run_count("rejected_unsettled")

    @property
    def source_rate_hz(self):
        """The source's frame rate as the run measured it, or 0.0."""
        run = self._run
        return round(run.source_rate_hz, 1) if run is not None else 0.0

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
                    f"Position is now read from {name}.", source=self.NAME)
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

    @property
    def red_threshold(self):
        """The `_measure_red` decision, named so the sidecar can record it.
        Two runs with the same red percent and different thresholds are not
        comparable."""
        return {"r_min": int(self.red_min), "g_max": self.GREEN_MAX,
                "b_max": self.BLUE_MAX}

    @property
    def channel_thresholds(self):
        """The green and blue masks' fixed thresholds (RG-1), for the
        sidecar: two runs are comparable channel for channel only under the
        same ones."""
        return {"green": {"g_min": self.GREEN_MIN, "r_max": self.RED_MAX,
                          "b_max": self.BLUE_MAX},
                "blue": {"b_min": self.BLUE_MIN, "r_max": self.RED_MAX,
                         "g_max": self.GREEN_MAX}}

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
            raise Refused("RGB analysis is unavailable: numpy failed to import.")
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
            sample_mode=self.SAMPLE_MODE,
            sample_interval_s=0.0,
            red_threshold=self.red_threshold,
        )
        self._run = run
        self._saved_rows = 0
        self._red_state = (0.0, 0.0)
        self._rgb_state = None

        run.thread = threading.Thread(target=self._run_loop, args=(run,),
                                      daemon=True, name=f"rgb-analysis-{run.run_id}")
        run.thread.start()
        events.info("Run Started", f"{run.run_id}: {run.sample_mode} sampling of "
                    f"{sch.format_region(run.region)}", source=self.NAME)
        events.debug("Run Configuration", json.dumps(self._station_meta(),
                     default=str), source=self.NAME)
        self._touch()
        return run.run_id

    def label_run(self, run_id, annotations=None):
        """Name the active run (the Transfer Map's Arm: the polling run the
        map started becomes trial N's, and its folder under `output_root`
        should say so; bench 2026-09-28, "the runs no longer have labels").
        Only while nothing of the run is on disk: a run with saved rows keeps
        the name its files carry. `annotations` are merged into the run's
        (specimen, consumable, note). Returns the run's id, or None when
        there is no active run or it is already saved under its name."""
        run = self._run
        if run is None or not run.is_active or self._saved_rows:
            return None
        name = str(run_id or "").strip()
        if not name or name == run.run_id and not annotations:
            return run.run_id
        was = run.run_id
        run.run_id = name
        if annotations:
            run.annotations.update({k: str(v) for k, v in dict(annotations).items()})
        if name != was:
            events.info("Run Named", f"Run {was} is now {name}.", source=self.NAME)
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

    def _stop_threads(self):
        """End the in-flight run, then join it; then the base join (MOD-2).

        The one override of `_stop_threads` left (MOD-2): a run is a one-shot
        per-command worker with its own stop, and ending it is something a
        join cannot do. Opening this model starts no loop; a run is started by
        the operator.
        """
        try:
            self._end_run_thread()
        finally:
            super()._stop_threads()

    def _end_run_thread(self):
        run = self._run
        if run is None:
            return
        run.end()
        thread = run.thread
        if thread is None or not thread.is_alive():
            return
        started = time.monotonic()
        thread.join(timeout=self.THREAD_JOIN_TIMEOUT)
        events.debug("Run Thread", f"join took "
                     f"{(time.monotonic() - started) * 1000:.0f} ms",
                     source=self.NAME)
        if thread.is_alive():
            events.warn("Run Thread Still Running",
                        f"the run thread did not stop within {self.THREAD_JOIN_TIMEOUT}s; "
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

        One sample per tick (CAP-1): `_settled_read` reads until two reads
        agree, `_judge` refuses a black fill or the stale picture, and only
        then is the frame measured, published, logged and forwarded. The
        next tick is `_sample_interval` after this one started. Every wait is
        the run's stop event, so a stop never waits on the pacing or on a
        re-read.

        The whole body is wrapped: any failure is recorded on `run.failure`
        (which is what makes `is_running` read False afterwards) and reported
        once. The old loop had no handler at all, so a failure left the UI
        showing "monitoring" forever with a dead thread (REDPERCENT-4).
        """
        screen, region, axes = self.screen, run.region, run.axes
        stop, threshold = run.stop_event, run.red_threshold
        mode = run.sample_mode
        # Reused every frame: `RunLog.add` reads them and keeps nothing, so
        # the hot path allocates no dictionary per sample.
        positions = {axis: None for axis in axes}
        velocities = {axis: None for axis in axes}
        previous_frame = None

        events.debug("Run Loop", f"started: mode={mode} axes={axes or 'none'} "
                     f"threshold={threshold} settle={self.SETTLE_S * 1000:.0f} ms "
                     f"x{self.SETTLE_READS}, interval "
                     f"{self.MIN_SAMPLE_INTERVAL_S * 1000:.1f}-"
                     f"{self.MAX_SAMPLE_INTERVAL_S * 1000:.1f} ms",
                     source=self.NAME)
        # The one thread that grabs continuously keeps its capture handle;
        # every other grab opens and closes its own (`Screen`, 2026-09-28).
        keep = getattr(screen, "keep_handle", None)
        if callable(keep):
            keep()
        try:
            due = time.monotonic()
            while not stop.is_set():
                wait = due - time.monotonic()
                if wait > 0 and stop.wait(wait):
                    break
                started = time.monotonic()
                outcome, frame, pixels = self._settled_read(run, screen, region,
                                                            stop)
                if outcome == "stopped":
                    break
                if outcome == "failed":
                    run.grab_failures += 1
                    events.debug("Grab Returned Nothing",
                                 f"{run.grab_failures} so far this run",
                                 source=self.NAME, every=1.0)
                    if stop.wait(0.05):
                        break
                    # The gap across a failure is not a frame interval worth
                    # averaging, nor a source interval worth measuring.
                    previous_frame = None
                    run.last_change_at = None
                    due = time.monotonic()
                    continue
                due = started + self._sample_interval(run)

                verdict, red = ("unsettled", None)
                if outcome == "settled":
                    verdict, red = self._judge(run, frame, pixels, threshold)
                if verdict == "black":
                    run.rejected_black += 1
                elif verdict == "stale":
                    run.rejected_stale += 1
                elif verdict == "live":
                    now = time.monotonic()
                    self._remember(run, pixels, now)
                    run.note_frame(None if previous_frame is None
                                   else now - previous_frame)
                    previous_frame = now

                    # RG-1: the six numbers, the red share among them (the
                    # same value `_judge` took for a dark picture).
                    rgb = self._measure_rgb(frame, threshold)
                    red = rgb[0]
                    self._rgb_state = rgb
                    run.accepted_red = red
                    if run.baseline_red is None:
                        run.baseline_red = red
                        self.baseline_red = red
                        events.info("Baseline Set", f"{red:.2f}% at run start",
                                    source=self.NAME)
                    self._publish_red(red)
                    frame_subscribers = self._frame_subscribers
                    if frame_subscribers:
                        self._notify_frames(frame_subscribers,
                                            now - run.started_monotonic, frame,
                                            red)

                    if self._wants_row(run, red):
                        run.last_logged_red = round(red, 1)
                        age = self._read_position(run, axes, positions,
                                                  velocities)
                        run.log.add(now - run.started_monotonic, red, positions,
                                    velocities, age, rgb)
                        run.rows += 1
                        subscribers = self._subscribers
                        if subscribers:
                            self._notify(subscribers,
                                         now - run.started_monotonic, red,
                                         positions, rgb)

                events.debug("Rate", f"{run.frames} frames, {run.rows} rows, "
                             f"{run.frame_rate:.1f} Hz (source "
                             f"{run.source_rate_hz:.1f} Hz), "
                             f"max gap {run.max_frame_interval * 1000:.1f} ms; "
                             f"{run.grabs} reads, rejected "
                             f"{run.rejected_black} black, {run.rejected_stale} "
                             f"stale, {run.rejected_unsettled} unsettled",
                             source=self.NAME, every=1.0)
        except Exception as exc:
            run.failure = exc
            events.debug("RGB Analysis Run Failed", f"{run.run_id}: {exc!r}",
                         source=self.NAME, exception=exc)
            events.error("RGB Analysis Run Failed",
                         f"Run {run.run_id} stopped unexpectedly. The rows "
                         "recorded so far are kept; save them, then start a "
                         "new run.", source=self.NAME, exception=exc)
        finally:
            drop = getattr(screen, "drop_handle", None)
            if callable(drop):
                drop()
            run.end()
            events.debug("Run Loop", f"exited after {run.frames} frame(s), "
                         f"{run.rows} row(s), {run.grab_failures} grab "
                         f"failure(s); {run.grabs} reads, rejected "
                         f"{run.rejected_black} black, {run.rejected_stale} "
                         f"stale, {run.rejected_unsettled} unsettled",
                         source=self.NAME)

    # -- CAP-1: the settle gate --------------------------------------------
    def _settled_read(self, run, screen, region, stop):
        """Read the region until two reads at least SETTLE_S apart show the
        same picture, at most SETTLE_READS reads. Returns `(outcome, frame,
        pixels)`: "settled" with the newer of the two reads; "unsettled" when
        the reads ran out first; "failed" for a grab that returned nothing;
        "stopped" when the stop landed in a settle wait.

        A read no second read confirmed is counted and kept for the stale
        bookkeeping (`_remember`), and goes nowhere else. Each wait is the
        run's stop event: a stop never waits on a re-read, and no read
        starts after one."""
        pending, have = None, False
        for _ in range(self.SETTLE_READS):
            if have and stop.wait(self.SETTLE_S):
                return "stopped", None, None
            frame = screen.grab(region)
            if frame is None:
                if have:
                    self._unconfirmed(run, pending)
                return "failed", None, None
            run.grabs += 1
            pixels = self._pixels(frame)
            if have and self._same_picture(pending, pixels):
                return "settled", frame, pixels
            if have:
                self._unconfirmed(run, pending)
            pending, have = pixels, True
        self._unconfirmed(run, pending)
        return "unsettled", None, None

    def _unconfirmed(self, run, pixels):
        run.rejected_unsettled += 1
        if pixels is not None:
            run.unconfirmed.append(pixels)
            del run.unconfirmed[:-self.SETTLE_READS]

    def _judge(self, run, frame, pixels, threshold):
        """A settled picture's verdict and, when the verdict needed it, its
        red percent (else None): "black" for an all-zero fill, or for a dark
        fill with no red after a picture that had some; "stale" for the
        remembered stale picture; "live" for anything else."""
        if not pixels.any():
            return "black", 0.0
        red = None
        if run.accepted_red and int(pixels.max()) <= self.DARK_LEVEL:
            red = self._measure_red(frame, threshold)
            if red == 0.0:
                return "black", red
        if self._same_picture(run.stale_pixels, pixels):
            return "stale", red
        return "live", red

    def _remember(self, run, pixels, now):
        """Book an accepted picture. The stale picture becomes the most recent
        one this settled picture contradicts: the latest unconfirmed read
        since the last accepted sample that differs from it (all-zero fills
        aside: they are refused on sight anyway), else the last accepted
        picture when this one replaces it. One is kept. A change of picture
        is also one interval of the source's measured frame period."""
        previous = run.accepted_pixels
        changed = not self._same_picture(previous, pixels)
        stale = previous if changed else None
        for read in reversed(run.unconfirmed):
            if read.any() and not self._same_picture(read, pixels):
                stale = read
                break
        if stale is not None:
            run.stale_pixels = stale
        run.unconfirmed = []
        run.accepted_pixels = pixels
        if changed:
            if run.last_change_at is not None:
                run.source_intervals = (*run.source_intervals,
                                        now - run.last_change_at
                                        )[-self.SOURCE_WINDOW:]
            run.last_change_at = now

    def _sample_interval(self, run):
        """Seconds from one sample's start to the next: SOURCE_OVERSAMPLE
        samples per measured source frame, held between the floor and the
        ceiling; the ceiling until a source rate is measured. "fixed" mode
        (no longer offered) keeps its own interval, floored."""
        if run.sample_mode == "fixed":
            return max(run.sample_interval_s, self.MIN_SAMPLE_INTERVAL_S)
        intervals = run.source_intervals
        if not intervals:
            return self.MAX_SAMPLE_INTERVAL_S
        period = statistics.median(intervals) / self.SOURCE_OVERSAMPLE
        return min(self.MAX_SAMPLE_INTERVAL_S,
                   max(self.MIN_SAMPLE_INTERVAL_S, period))

    def _same_picture(self, a, b):
        """Two reads show the same picture: equal shape, and at most
        SETTLE_TOLERANCE of the pixels differ in any colour channel."""
        if a is None or b is None or a.shape != b.shape:
            return False
        if numpy.array_equal(a, b):
            return True
        allowed = self.SETTLE_TOLERANCE * a.shape[0] * a.shape[1]
        return (allowed >= 1
                and numpy.count_nonzero((a != b).any(axis=2)) <= allowed)

    @staticmethod
    def _pixels(frame):
        """A frame's colour planes as one `(height, width, 3)` uint8 array, a
        view with no copy, in the frame's own channel order; for comparing
        reads. The fourth byte of a BGRA grab is left out: mss does not
        promise what it holds. None for anything that is not a frame."""
        buffer = getattr(frame, "bgra", None)
        if buffer is not None:
            height = getattr(frame, "height", None)
            width = getattr(frame, "width", None)
            if height is None or width is None:
                width, height = frame.size
            pixels = numpy.frombuffer(buffer, dtype=numpy.uint8)
            return pixels.reshape(int(height), int(width), 4)[:, :, :3]
        if isinstance(frame, numpy.ndarray) and frame.ndim == 3:
            return frame[:, :, :3]
        return None

    # -- MAP-2: what another model may read (additive; nothing above changes)
    def subscribe(self, fn):
        """Call `fn(t_s, red, row)` for every row the run log appends from
        now on, on the run thread. `row` is the subscriber's own dict: the
        synced axes' positions by axis ("X", "Y", "Z") and the sample's
        other five numbers by `CHANNEL_KEYS` ("green", "blue", "r_mean",
        "g_mean", "b_mean"; RG-1); read it with `.get`. `fn` must return at
        once; one that raises is logged and skipped, never allowed to end
        the run."""
        if fn not in self._subscribers:
            self._subscribers = self._subscribers + (fn,)

    def unsubscribe(self, fn):
        self._subscribers = tuple(s for s in self._subscribers if s != fn)

    def subscribe_frames(self, fn):
        """Call `fn(t_s, frame, red)` for every frame the run loop accepts
        from now on, on the run thread: the settled frame it measured (as the
        screen returned it: an mss screenshot, or an array), and its red
        percent. A rejected read (unsettled, black, stale; CAP-1) is never
        offered.
        `fn` must return at once (hand the frame to a queue; never convert
        or encode here): the loop's rate is the measurement's. One that
        raises is logged and skipped, never allowed to end the run."""
        if fn not in self._frame_subscribers:
            self._frame_subscribers = self._frame_subscribers + (fn,)

    def unsubscribe_frames(self, fn):
        self._frame_subscribers = tuple(s for s in self._frame_subscribers
                                        if s != fn)

    def _notify_frames(self, subscribers, t_s, frame, red):
        for fn in subscribers:
            try:
                fn(t_s, frame, red)
            except Exception as exc:
                events.debug("Frame Subscriber Failed", repr(exc),
                             source=self.NAME, exception=exc, every=1.0)

    @property
    def run_token(self):
        """The active run itself, as an identity no later run shares (the
        Transfer Map ends the run it started and no other; `run_id` repeats
        whenever the operator has typed a Run / Cut ID). None when no run
        is active. Compare with `is`; never written through."""
        run = self._run
        return run if run is not None and run.is_active else None

    def _notify(self, subscribers, t_s, red, positions, rgb=None):
        for fn in subscribers:
            try:
                row = dict(positions)
                if rgb is not None:
                    row.update(zip(self.CHANNEL_KEYS, rgb[1:]))
                fn(t_s, red, row)
            except Exception as exc:
                events.debug("Subscriber Failed", repr(exc), source=self.NAME,
                             exception=exc, every=1.0)

    def grab_frame(self):
        """The capture region as it looks now, as PNG bytes, or None when
        there is no region, the screen is not open, or the grab failed. Its
        own grab through the Screen device: the run loop is not touched."""
        region = self.region
        if not region or not self.screen.is_open or numpy is None:
            return None
        frame = self.screen.grab(region)
        if frame is None:
            return None
        try:
            import io
            from PIL import Image
            blue, green, red = self._channels(frame)
            if red is None:
                return None
            rgb = numpy.ascontiguousarray(numpy.stack([red, green, blue], axis=2),
                                          dtype=numpy.uint8)
            buffer = io.BytesIO()
            Image.fromarray(rgb, "RGB").save(buffer, format="PNG")
            return buffer.getvalue()
        except Exception as exc:
            events.debug("Frame Grab Failed", repr(exc), source=self.NAME,
                         exception=exc)
            return None

    def grab_screen(self):
        """The whole virtual desktop as it looks now, at full size, as PNG
        bytes (the microscope feed as displayed, for the Transfer Map's
        whole-screen pictures), or None when the screen is not open or the
        grab failed. `screen_image`, the picker's picture, stays downscaled."""
        if not self.screen.is_open:
            return None
        try:
            png, _bounds = self.screen.screenshot_png(max_width=None)
        except Exception as exc:
            events.debug("Screen Grab Failed", repr(exc), source=self.NAME,
                         exception=exc)
            return None
        return png or None

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

    def _measure_rgb(self, frame, threshold=None):
        """The six numbers of `frame` (RG-1), a tuple in RGB_KEYS order:
        the red, green and blue shares (% of the pixels) and the mean red,
        green and blue (0-255).

        One deinterleave of the region into three contiguous planes, then
        vectorised comparisons and reductions over them; no per-pixel
        Python. The red share is `_measure_red`'s to the bit: the same three
        comparisons, counted over the same pixels, divided the same way. The
        green and blue masks read the class's fixed thresholds
        (`channel_thresholds`); a comparison two masks share is made once.
        Six zeros for no frame, as `_measure_red` answers 0.0.
        """
        if frame is None or numpy is None:
            return (0.0,) * len(self.RGB_KEYS)
        threshold = threshold or self.red_threshold
        blue, green, red = self._channels(frame)
        if red is None or red.size == 0:
            return (0.0,) * len(self.RGB_KEYS)
        # Contiguous copies: every reduction below then reads memory in
        # order rather than one byte in four of a BGRA buffer.
        blue = numpy.ascontiguousarray(blue)
        green = numpy.ascontiguousarray(green)
        red = numpy.ascontiguousarray(red)
        size = red.size
        green_low = green < threshold["g_max"]
        blue_low = blue < threshold["b_max"]
        red_low = red < self.RED_MAX
        if threshold["g_max"] != self.GREEN_MAX:
            green_capped = green < self.GREEN_MAX
        else:
            green_capped = green_low
        if threshold["b_max"] != self.BLUE_MAX:
            blue_capped = blue < self.BLUE_MAX
        else:
            blue_capped = blue_low
        red_share = (numpy.count_nonzero((red > threshold["r_min"])
                                         & green_low & blue_low)
                     / size) * 100.0
        green_share = (numpy.count_nonzero((green > self.GREEN_MIN)
                                           & red_low & blue_capped)
                       / size) * 100.0
        blue_share = (numpy.count_nonzero((blue > self.BLUE_MIN)
                                          & red_low & green_capped)
                      / size) * 100.0
        return (red_share, green_share, blue_share,
                float(red.sum(dtype=numpy.uint64)) / size,
                float(green.sum(dtype=numpy.uint64)) / size,
                float(blue.sum(dtype=numpy.uint64)) / size)

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
                "channel_thresholds": self.channel_thresholds,
                "sample_mode": run.sample_mode,
                "sample_interval_s": run.sample_interval_s,
                "position_rate_hz": self.POSITION_RATE_HZ,
                "frames_captured": run.frames,
                "rows_written": run.rows,
                "grab_failures": run.grab_failures,
                "mean_frame_interval_s": round(run.mean_frame_interval, 6),
                "max_frame_interval_s": round(run.max_frame_interval, 6),
                "achieved_rate_hz": round(run.frame_rate, 3),
                "grabs": run.grabs,
                "rejected_black": run.rejected_black,
                "rejected_stale": run.rejected_stale,
                "rejected_unsettled": run.rejected_unsettled,
                "source_rate_hz": round(run.source_rate_hz, 3),
                "settle_gate": self._settle_gate(),
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
            "channel_thresholds": self.channel_thresholds,
            "sample_mode": self.SAMPLE_MODE,
            "position_rate_hz": self.POSITION_RATE_HZ,
            "frames_captured": 0,
            "rows_written": 0,
            "grab_failures": 0,
            "mean_frame_interval_s": 0.0,
            "max_frame_interval_s": 0.0,
            "achieved_rate_hz": 0.0,
            "grabs": 0,
            "rejected_black": 0,
            "rejected_stale": 0,
            "rejected_unsettled": 0,
            "source_rate_hz": 0.0,
            "settle_gate": self._settle_gate(),
            "duration_s": 0.0,
            "started_at": None,
            "stopped_at": None,
            "annotations": self.annotations,
        }

    def _settle_gate(self):
        """The CAP-1 constants a run was sampled under: two runs taken under
        different ones are not comparable sample for sample."""
        return {"settle_s": self.SETTLE_S,
                "settle_tolerance": self.SETTLE_TOLERANCE,
                "settle_reads": self.SETTLE_READS,
                "dark_level": self.DARK_LEVEL,
                "source_oversample": self.SOURCE_OVERSAMPLE,
                "min_sample_interval_s": round(self.MIN_SAMPLE_INTERVAL_S, 6),
                "max_sample_interval_s": round(self.MAX_SAMPLE_INTERVAL_S, 6)}

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
            events.debug("Autosave Failed", f"{why}: {exc!r}",
                         source=self.NAME, exception=exc)
            events.error("Autosave Failed",
                         f"The run could not be saved automatically ({why}). "
                         "Save it by hand now.", source=self.NAME,
                         exception=exc)
            return None
        events.info("Run Autosaved",
                    f"Unsaved data was saved automatically ({why}): {path}",
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
            events.debug("Load Failed", f"{path}: {exc!r}", source=self.NAME,
                         exception=exc)
            raise Refused(f"Could not read {Path(path).name}. Check that the "
                          "file exists and is a saved run.")
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

    def _expects_heartbeat(self):
        return self.is_running        # idle, there is no loop to be stale

    @property
    def screen_image(self):
        """The desktop as PNG for the browser's region picker: `{"image":
        bytes, "left", "top", "width", "height"}` (the full-size bounds the
        picture was scaled from), or None when capture is unavailable."""
        # Full size since 2026-09-28: the desktop pickers draw it 1:1 in
        # desktop coordinates (views/picking.py); the browser scales it.
        png, bounds = self.screen.screenshot_png(max_width=None)
        return None if png is None else {"image": png, **bounds}

    @property
    def figure(self):
        """PNG bytes of the analysis plot, rendered once here and displayed by
        every view — rather than Tk, PySide and the Web client each building
        their own matplotlib canvas out of their own copy of the data."""
        # Cached until the loaded data or the plot selection changes: views
        # poll this every second and a render cost 214 ms (UXPM-13). The key
        # holds the loaded dict itself, so a reload is a new key even when
        # the path is the same.
        loaded = self._loaded
        key = (id(loaded), self._plot_type, self._plot_dims,
               self.FIGURE_SIZE, self.FIGURE_DPI)
        cached = self._figure_cache
        if cached is not None and cached[0] == key and cached[1] is loaded:
            return cached[2]
        if loaded is None:
            # Nothing loaded: no figure. The schema's `empty` sentence is what
            # a view draws, one caption line tall (L15), instead of a
            # full-size "No samples in this run." picture.
            png = b""
        else:
            png = plot_data.render_figure(
                self._plot_type, *self._plot_dims,
                red_percents=loaded["red_percents"],
                dim_data=loaded["dim_data"], times=loaded["times"],
                size=self.FIGURE_SIZE, dpi=self.FIGURE_DPI)
        self._figure_cache = (key, loaded, png)
        return png

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
            # CAP-1: what the settle gate threw away, and the source's rate.
            "accepted": self.frames_accepted,
            "grabs": self._run_count("grabs"),
            "rejected_black": self.rejected_black,
            "rejected_stale": self.rejected_stale,
            "rejected_unsettled": self.rejected_unsettled,
            "source_rate_hz": self.source_rate_hz,
            # RG-1: the latest accepted sample's six numbers (None before one).
            "latest": self.latest,
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
        # Tiers (owner ruling 2026-09-25): RGB analysis as most operators see
        # it is two numbers and two buttons. Every statistic, the live plot,
        # the annotations, the region, save/load and the analysis figure are
        # on demand, behind Details - never implied.
        return sch.schema(
            sch.section(
                "Live",
                # REDPERCENT-19: a declared display precision, rather than each
                # renderer re-deriving one from the box.
                sch.readonly("Current Red:", "current_red", rail=True,
                             param=P["current_red"], format=".2f", unit="%"),
                sch.readonly("Red Change:", "red_change", rail=True, param=P["red_change"],
                             format=".2f", unit="%"),
                # REDPERCENT-13: published so every client can gate its plotter
                # on "is a run actually active" instead of sampling regardless.
                sch.readonly("Running:", "is_running", role="info"),
                # L3: the one thing that greys Start run from launch is the
                # missing capture region; say so where Start run is, not two
                # tiers down. Empty (quiet) whenever there is nothing to do.
                sch.readonly("Next step:", "next_step", role="info"),
                sch.button("Start run", "start_run", inputs=self._entry_names,
                           role="go",
                           disabled_when=("running", "latched", "no_region")),
                sch.button("Stop run", "end_run", role="neutral",
                           enabled_when=("running",), stop=True),
            ),
            sch.section(
                "Run",
                sch.entry("Run / Cut ID:", "run_name", P["run_name"],
                          disabled_when=("running",)),
                sch.readonly("Run ID:", "run_id", param=P["run_id"]),
                tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                # REDPERCENT-23, D-6: declared once so all three views render
                # it. None of them hand-builds an annotation form.
                "Operator annotation (intended)",
                *[sch.entry(f"{field.label}:", field.name, P[field.name],
                            disabled_when=("running",))
                  for field in self.ANNOTATION_FIELDS],
                tier=2, disclosure=self.DISCLOSURE,
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
                tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                "Synced Axes",
                sch.toggle("Sync X", "is_sync_x", "set_sync", "On",
                           "Off", on_args=("X",), off_args=("X",),
                           disabled_when=("running",)),
                sch.toggle("Sync Y", "is_sync_y", "set_sync", "On",
                           "Off", on_args=("Y",), off_args=("Y",),
                           disabled_when=("running",)),
                sch.toggle("Sync Z", "is_sync_z", "set_sync", "On",
                           "Off", on_args=("Z",), off_args=("Z",),
                           disabled_when=("running",)),
                sch.readonly("Synced:", "sync_axes", param=P["sync_axes"]),
                tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                "Red Detection",
                sch.entry("Red at least:", "red_min", P["red_min"],
                          disabled_when=("running",)),
                tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                # RG-1: the latest sample's other five numbers. The red share
                # stays the tier-1 reading; these are on demand.
                "Channels",
                sch.readonly("Current Green:", "current_green",
                             param=P["current_green"], format=".2f", unit="%"),
                sch.readonly("Current Blue:", "current_blue",
                             param=P["current_blue"], format=".2f", unit="%"),
                sch.readonly("Mean Red:", "mean_red", param=P["mean_red"],
                             format=".1f"),
                sch.readonly("Mean Green:", "mean_green", param=P["mean_green"],
                             format=".1f"),
                sch.readonly("Mean Blue:", "mean_blue", param=P["mean_blue"],
                             format=".1f"),
                tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                "Sampling",
                sch.readonly("Frame Rate:", "frame_rate", param=P["frame_rate"]),
                sch.readonly("Frames:", "frames_captured",
                             param=P["frames_captured"]),
                sch.readonly("Rows:", "rows_written", param=P["rows_written"]),
                # 2026-10-07 (TM-2): no live plot; it is drawn on the
                # Transfer Map's page, and redrawing the whole run each
                # refresh slowed the view (CAP-5). `series` stays, and the
                # Analysis figure plots a saved run.
                tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                "Control",
                sch.region_select("Set Capture Region", "set_region",
                                  model_attr="region", role="info",
                                  data_command="screen_image"),
                sch.button("Reset Baseline", "reset_baseline"),
                sch.file_save("Save", "save", extensions=("csv",), role="info"),
                tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                "Analysis",
                sch.file_open("Load Run", "load_run", extensions=("csv",)),
                sch.readonly("Loaded:", "loaded_run"),
                sch.dropdown("Plot:", "plot_dims", "set_plot_dims",
                             "plot_dim_options"),
                sch.image("Analysis Plot", "figure",
                          empty="No analysis yet. Load a run to plot it."),
                tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                "Diagnostics",
                sch.readonly("Position Age:", "position_age",
                             param=P["position_age"]),
                # CAP-1: what the settle gate let through and threw away, so
                # the bench can see the viewer's repaint transients.
                sch.readonly("Accepted:", "frames_accepted",
                             param=P["frames_accepted"]),
                sch.readonly("Rejected black:", "rejected_black",
                             param=P["rejected_black"]),
                sch.readonly("Rejected stale:", "rejected_stale",
                             param=P["rejected_stale"]),
                sch.readonly("Rejected unsettled:", "rejected_unsettled",
                             param=P["rejected_unsettled"]),
                tier=3, disclosure="Diagnostics",
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
                "red_min")
