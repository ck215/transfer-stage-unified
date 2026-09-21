import threading
from model import schema as sch
from model.params import Param, table as _param_table
from model.base import SchemaCommands
from results import Refused, Ok
import time
try:
    from PIL import Image
    import mss
    import numpy as np
except ImportError:
    Image = None
    mss = None
    np = None
import typing
import csv
import json
import os
from pathlib import Path


def _default_output_root():
    """Resolved once, at import, and never from the process CWD.

    REDPERCENT-21: `autosave_log` used to hand `save_log` a bare relative
    path, so an unattended stop wrote into whatever directory the launcher
    happened to start in — a different one for run.sh, run_macos.sh and the
    web server. The run's output location is not allowed to be one of the
    things that varies between the three frontends.
    """
    env = os.environ.get("TRANSFER_STAGE_DATA_ROOT")
    return Path(env).expanduser().resolve() if env else \
        (Path.home() / "transfer-stage-runs").resolve()


DEFAULT_OUTPUT_ROOT = _default_output_root()

class RedPercentDataLog:
    def __init__(self, sync_dimensions=None, probe_name="", probe_tilt_angle=""):
        self.sync_dimensions = sync_dimensions or []
        self.probe_name = probe_name
        self.probe_tilt_angle = probe_tilt_angle
        self.red_values = []
        #: REDPERCENT-16: one wall-clock timestamp per sample, so the series
        #: has a time axis at all. Written by `add_entry`; never backfilled.
        self.timestamps = []
        self.loc_values = {dim: [] for dim in self.sync_dimensions}
        self.vel_values = {dim: [] for dim in self.sync_dimensions}
        self.lock = threading.Lock()

    def add_entry(self, red_pct, locs=None, vels=None, timestamp=None):
        """Append one sample.

        `locs`/`vels` may carry `None` for a dimension whose read failed —
        an explicit sentinel (ERRORS-7), never a fabricated `0.0`. A
        dimension **absent** from `locs`/`vels` entirely (as opposed to
        present with value `None`) still defaults to `0.0`; that is a
        different caller contract (a dimension never attempted) and several
        tests pin it (`test_redpercent_datalog.py`).
        """
        with self.lock:
            self.red_values.append(red_pct)
            self.timestamps.append(timestamp if timestamp is not None else time.time())
            locs = locs or {}
            vels = vels or {}
            for dim in self.sync_dimensions:
                self.loc_values[dim].append(locs.get(dim, 0.0))
                self.vel_values[dim].append(vels.get(dim, 0.0))

    def save_to_csv(self, filepath):
        """A plain rectangle: header row, then samples.

        REDPERCENT-22: this used to prepend `# Metadata`, `# Probe Name`,
        `# Probe Tilt Angle` and a blank row. That is not a comment
        convention — they are four DATA rows before the header, so a default
        `pandas.read_csv` takes `# Metadata` as the column names. The
        workaround was `skiprows=4`, a magic number that breaks the moment a
        field is added.

        The configuration now goes to a sibling `<run_id>_station_meta.json`
        (see `RedPercentSystem.station_meta`). Files already on disk keep
        working: `plot_data.parse_red_percent_csv` still skips `#` rows and
        finds the header by name.

        REDPERCENT-16: a `Timestamp` column sits right after `Red Percent`,
        so the series has a time axis. A `None` location or velocity (an
        invalid read, ERRORS-7) is written as an empty cell by `csv.writer`
        — never as `0.0`, which is a position the stage can actually be at.
        """
        with self.lock:
            with open(filepath, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                headers = ["Red Percent", "Timestamp"]
                for dim in self.sync_dimensions:
                    headers.append(f"Stepper {dim} Location")
                    headers.append(f"Stepper {dim} Velocity")
                writer.writerow(headers)

                for i in range(len(self.red_values)):
                    ts = self.timestamps[i] if i < len(self.timestamps) else None
                    row = [self.red_values[i], ts]
                    for dim in self.sync_dimensions:
                        row.append(self.loc_values[dim][i])
                        row.append(self.vel_values[dim][i])
                    writer.writerow(row)


class MonitoringRun:
    """One monitoring session's frozen configuration plus its live data
    (RC-11 item 1).

    Created by `RedPercentSystem.start_monitoring()`, never reused, never
    mutated by the UI after creation. Before this class existed, the
    "configuration" was just the model's own live attributes, read directly
    by the monitor thread on every frame — so a mid-run edit (a Sync toggle,
    a re-run of `start_monitoring`) reached the thread instantly and
    unsynchronized (REDPERCENT-1, REDPERCENT-3).

    `sync_dimensions` and `focus_area` are copied, not aliased: a `list`
    handed in is frozen into a `tuple` / a fresh `dict` here, so nothing the
    operator does to the model afterward can reach the run in flight.
    """

    def __init__(self, *, sync_dimensions, focus_area, probe_name,
                 probe_tilt_angle, run_id, output_root, annotations,
                 generation):
        self.sync_dimensions = tuple(sync_dimensions or ())
        self.focus_area = dict(focus_area) if focus_area else None
        self.probe_name = probe_name
        self.probe_tilt_angle = probe_tilt_angle
        self.run_id = run_id
        self.output_root = output_root
        self.annotations = dict(annotations or {})

        self.data_log = RedPercentDataLog(self.sync_dimensions, probe_name,
                                          probe_tilt_angle)

        #: REDPERCENT-3: a stop that only clears a flag can leave the old
        #: thread inside its sleep when a new run starts. This event is
        #: this run's own, never shared with any other run's thread.
        self.stop_event = threading.Event()
        self.thread = None
        #: The generation-token pattern from `probes.py` (`_new_run_generation`
        #: / `_generation_is_current`, RC-5 item 1): a thread whose generation
        #: has been superseded exits without writing, even if its own
        #: `stop_event` was never set.
        self.generation = generation

        #: REDPERCENT-2: initialised here, once, instead of a `hasattr`
        #: check inside the thread that let the previous run's last value
        #: leak into this one's first dedup decision.
        self.last_logged_red = -1000.0
        #: `{dim: (position, timestamp)}` of the last successfully read
        #: sample, for velocity-from-position-deltas (REDPERCENT-16).
        self.last_position = {}

        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.stopped_at = None
        #: The exception that ended the run unexpectedly, if any
        #: (REDPERCENT-4's redpercent_system.py remainder).
        self.failure = None

    @property
    def active(self):
        """True from creation until stopped or failed. The single source
        `RedPercentSystem.monitoring` is derived from."""
        return not self.stop_event.is_set() and self.failure is None

    def mark_stopped(self):
        """Latch the stop. Idempotent, and safe to call from any thread —
        this is the only I/O-free half of what used to be `stop_monitoring`,
        so `emergency_stop` can call it without ever blocking."""
        self.stop_event.set()
        if self.stopped_at is None:
            self.stopped_at = time.strftime("%Y-%m-%dT%H:%M:%S")


class RedPercentSystem(SchemaCommands):
    #: A hint, not a class: Tk and PySide need different widgets for the same
    #: device. Each frontend maps the hint to its own class, so neither has to
    #: branch on the device's *name* to find the right view (RC-7, I-7.1).
    #: A model with no hint renders with the generic schema renderer.
    VIEW_HINT = "red_percent"

    #: What the operator INTENDED, as data rather than as attributes.
    #: REDPERCENT-23: these are experiment-specific, so hardcoding this
    #: experiment's set guarantees the next one needs a code change. Adding a
    #: field is a line in this table; the schema, the snapshot and the
    #: sidecar all follow from it.
    #:
    #: These never hold what the station actually did — that lives in
    #: `station_meta()`. Merging the two loses exactly the planned-versus-
    #: actual comparison the experiment exists to make.
    ANNOTATION_FIELDS = (
        Param("specimen_id", "text", default="", label="Specimen ID"),
        Param("consumable_id", "text", default="", label="Tip / Consumable ID"),
        Param("stage_x", "float", default=0.0, decimals=3, label="Stage X"),
        Param("stage_y", "float", default=0.0, decimals=3, label="Stage Y"),
        Param("note", "text", default="", label="Note"),
    )

    #: The `detect_red` decision, named so the sidecar can record it. Two runs
    #: with the same red percent and different thresholds are not comparable.
    RED_THRESHOLD = {"r_min": 150, "g_max": 100, "b_max": 100}

    #: Seconds between frames. Also the bound on how quickly a stop is felt:
    #: the loop waits on `run.stop_event` for this long, not a bare sleep, so
    #: `stop_monitoring()` wakes it rather than waiting out the interval.
    FRAME_INTERVAL = 0.016  # ~60 FPS continuous logging

    PARAMS = _param_table(
        Param("run_id", "text", default="", label="Run / Cut ID"),
        Param("probe_name", "text", default="", label="Probe Name"),
        Param("probe_tilt_angle", "float", default=0.0, decimals=2,
              unit="deg", label="Probe Tilt Angle"),
        Param("current_red", "float", default=0.0, decimals=2, unit="%",
              label="Current Red %"),
        Param("red_change", "float", default=0.0, decimals=2, unit="%",
              label="Red Change %"),
        *ANNOTATION_FIELDS,
    )

    def __init__(self):
        # `red_percent`, `is_monitoring`, `stop_event`, `thread`,
        # `monitor_thread` and `baseline` used to be assigned here and
        # never read anywhere else (REDPERCENT-20, grep-verified against
        # the whole tree). The live equivalents are `current_red`,
        # `monitoring`, `_monitor_thread` and `baseline_red` below.
        self.data_log = None

        self.stepper_model = None
        self.available_probes = {}
        self.selected_probe_name = None
        self._registry = None

        self.sync_dimensions = []
        self.focus_area: typing.Optional[dict] = None
        self.baseline_red = 0.0

        # -- REDPERCENT-5: current_red/red_change published as a pair -----
        # A single attribute holding the pair, swapped in one assignment,
        # so every poller (Tk, PySide's `_poll_model`, the web `get_state`)
        # either sees the old pair or the new one, never a torn mix of the
        # two. `current_red`/`red_change` below are read-only views onto it.
        self._red_state = (0.0, 0.0)

        # -- RC-11 item 1: the run is the authority ------------------------
        self._run: typing.Optional[MonitoringRun] = None
        self._monitor_thread = None
        #: Legacy affordance only. `monitoring` is derived from `self._run`
        #: whenever a run exists; this backs the getter/setter when it does
        #: not, for the white-box teardown tests and the leaked-thread sweep
        #: in `tests/conftest.py` that poke this attribute directly instead
        #: of going through `start_monitoring`/`stop_monitoring`.
        self._legacy_monitoring = False

        #: The generation-token pattern from `probes.py:467-480` (RC-5 item
        #: 1), copied rather than reinvented: a thread whose generation has
        #: been superseded by a newer `start_monitoring()` call exits
        #: without writing (REDPERCENT-3).
        self._monitor_generation_lock = threading.Lock()
        self._monitor_generation = 0

        # New metadata fields
        self.probe_name = ""
        # Declared `float` in PARAMS; it used to be initialized to "" so the
        # very first save wrote a string into a numeric field (REDPERCENT-22).
        self.probe_tilt_angle = 0.0

        # -- run identity and artifacts (REDPERCENT-21/22/23) ------------
        #: Operator-set. Blank falls back to a timestamp slug rather than
        #: producing an unnamed run; see `effective_run_id`. These five stay
        #: the *pre-run editable* values: `start_monitoring` snapshots them
        #: into the `MonitoringRun`, which becomes the read authority for as
        #: long as it exists (`station_meta`, `run_dir`, `save_run`).
        self.run_id = ""
        self.output_root = DEFAULT_OUTPUT_ROOT
        #: What the operator intended, keyed by ANNOTATION_FIELDS.
        self.run_annotations = {f.name: f.default for f in self.ANNOTATION_FIELDS}
        self._run_started_at = None
        self._run_stopped_at = None

        #: D-10 seam (RC-11 item 3). `None` by default, meaning "no view is
        #: watching": `teardown()` autosaves unsaved data on its own, which
        #: is the D-10 default that needs no UI at all. A view able to show
        #: a modal may set this to a zero-arg callable returning True ("the
        #: operator was asked and chose discard") / False ("save it"/"ask
        #: failed"); `teardown()` (see `_operator_confirmed_discard`)
        #: consults it before autosaving, so a `SystemManager.release`/
        #: `shutdown_all` call — which already reaches `teardown()` with no
        #: changes needed in `system_manager.py`, outside this file's write
        #: set — honors it automatically. What is NOT wired here: any actual
        #: *prompt* implementation (PySide's own dock-close prompt, PYSIDE-4,
        #: is agent A's file).
        self.confirm_discard = None

    def set_focus_area(self, x, y, w, h):
        self.focus_area = {'top': int(y), 'left': int(x), 'width': int(w), 'height': int(h)}
        return True

    def set_stepper_model(self, probe_name):
        if self.available_probes and probe_name in self.available_probes:
            self.stepper_model = self.available_probes[probe_name]
            self.selected_probe_name = probe_name
            print(f"[{self.__class__.__name__}] Active position probe set to: {probe_name}")

    # -- the probe registry (RC-9 item 2) -------------------------------
    #
    # `available_probes` used to be assigned from outside, by whichever
    # launcher had just finished building models — the same six lines
    # copy-pasted into Tk's launcher, PySide's launcher and the web adapter.
    # Nothing updated it afterwards, so a released probe stayed in the dict
    # and stayed selected: `_monitor_colors` went on reading `pos_x` off a
    # torn-down model and logged its last value forever, with zero velocity
    # and no warning (PYSIDE-3, STEPPER-13, REDPERCENT-11).
    #
    # The dependent model owns the reference now. It learns about probes
    # from the registry, which is the only thing that knows when one arrives
    # or goes away.

    #: What makes a model usable as a position source. A duck-type, not a
    #: device name: this is the same test all three launchers used, and it
    #: belongs here rather than in each of them.
    POSITION_ATTR = "pos_x"

    @classmethod
    def is_position_source(cls, model):
        return hasattr(model, cls.POSITION_ATTR)

    def bind_registry(self, manager):
        """Track `manager`'s position sources for as long as this model lives.

        Seeds from what is registered *now* and subscribes for the rest, so
        it does not matter whether Red Percent is built before or after the
        probes it syncs against — which is exactly the ordering the launchers
        each guessed at differently.
        """
        self.unbind_registry()
        self._registry = manager
        for name, model in manager.get_active_models_snapshot().items():
            self.probe_registered(name, model)
        manager.subscribe("registered", self.probe_registered)
        manager.subscribe("released", self.probe_released)

    def unbind_registry(self):
        manager, self._registry = self._registry, None
        if manager is not None:
            manager.unsubscribe(self.probe_registered)
            manager.unsubscribe(self.probe_released)

    def probe_registered(self, name, model):
        """A model appeared. Track it if it can report a position."""
        if not self.is_position_source(model):
            return
        self.available_probes[name] = model
        self._reselect()

    def probe_released(self, name, model=None):
        """A model went away. Drop it, and stop pointing at it."""
        if self.available_probes.pop(name, None) is None:
            return
        if self.selected_probe_name == name:
            self.stepper_model = None
            self.selected_probe_name = None
        self._reselect()

    def _reselect(self):
        """Hold a live selection whenever one is available.

        Ties break in device-registry order, so the choice is the same in
        every frontend and does not depend on build order. That preference
        used to be `if "Stepper Probe" in probe_models` written out at each
        launch site.
        """
        if self.selected_probe_name in self.available_probes:
            self.stepper_model = self.available_probes[self.selected_probe_name]
            return
        names = self.get_available_probe_names()
        if not names:
            self.stepper_model = None
            self.selected_probe_name = None
            return
        self.set_stepper_model(names[0])

    def capture_focus_area(self, sct):
        if not self.focus_area:
            return None
        try:
            screenshot = sct.grab(self.focus_area)
            if Image is not None and np is not None:
                img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')
                return np.array(img)
            return None
        except Exception as e:
            from error_routing import ErrorRouter as ErrorPopupManager
            ErrorPopupManager.report_error("Screen Capture Error", f"Error capturing screen:\n{e}", e)
            return None

    def detect_red(self, image):
        if image is None:
            return 0.0
        r = image[:, :, 0]
        g = image[:, :, 1]
        b = image[:, :, 2]
        red_mask = (r > 150) & (g < 100) & (b < 100)
        total_pixels = image.shape[0] * image.shape[1]
        if total_pixels == 0:
            return 0.0
        red_pixels = np.sum(red_mask)
        return (red_pixels / total_pixels) * 100

    # -- Sync dimensions (RC-11 item 2) ----------------------------------
    #
    # The schema already renders these toggles `disabled_when=("monitoring",)`
    # — but that only stops a *rendered, enabled* widget. The web client can
    # reach `sync_x` directly through `set_device_attribute` (its `model_attr`
    # is a real, non-readonly one), and nothing stopped a caller anywhere
    # from calling `toggle_sync_x()` mid-run either. Both paths used to
    # mutate `sync_dimensions` while the monitor thread iterated it — a
    # `RuntimeError`/`KeyError` race that killed the thread with `monitoring`
    # still True (REDPERCENT-1, VIEW-TKINTER-15). The monitor thread no
    # longer reads this list at all (it reads the run's own frozen tuple),
    # so a toggle that slips through the guard below can no longer crash it
    # — but it still must refuse: silently ignoring an operator's click is
    # its own defect.

    @property
    def sync_x(self):
        return 'X' in self.sync_dimensions

    @sync_x.setter
    def sync_x(self, value):
        if self.monitoring:
            print(f"[{self.__class__.__name__}] Sync X is fixed for the "
                  f"duration of the active run; ignoring change.")
            return
        if value and 'X' not in self.sync_dimensions:
            self.sync_dimensions.append('X')
        elif not value and 'X' in self.sync_dimensions:
            self.sync_dimensions.remove('X')

    @property
    def sync_y(self):
        return 'Y' in self.sync_dimensions

    @sync_y.setter
    def sync_y(self, value):
        if self.monitoring:
            print(f"[{self.__class__.__name__}] Sync Y is fixed for the "
                  f"duration of the active run; ignoring change.")
            return
        if value and 'Y' not in self.sync_dimensions:
            self.sync_dimensions.append('Y')
        elif not value and 'Y' in self.sync_dimensions:
            self.sync_dimensions.remove('Y')

    @property
    def sync_z(self):
        return 'Z' in self.sync_dimensions

    @sync_z.setter
    def sync_z(self, value):
        if self.monitoring:
            print(f"[{self.__class__.__name__}] Sync Z is fixed for the "
                  f"duration of the active run; ignoring change.")
            return
        if value and 'Z' not in self.sync_dimensions:
            self.sync_dimensions.append('Z')
        elif not value and 'Z' in self.sync_dimensions:
            self.sync_dimensions.remove('Z')

    def toggle_sync_x(self):
        if self.monitoring:
            return Refused("Sync dimensions are fixed for the duration of "
                            "the active run.")
        self.sync_x = not self.sync_x

    def toggle_sync_y(self):
        if self.monitoring:
            return Refused("Sync dimensions are fixed for the duration of "
                            "the active run.")
        self.sync_y = not self.sync_y

    def toggle_sync_z(self):
        if self.monitoring:
            return Refused("Sync dimensions are fixed for the duration of "
                            "the active run.")
        self.sync_z = not self.sync_z

    # -- run identity and artifacts (REDPERCENT-21/22/23) ---------------

    def effective_run_id(self):
        """The operator's run id, or a timestamp slug when they set none.

        Never empty: an unattended stop is exactly the run whose bench notes
        are least complete, so it is the one that most needs a name.
        """
        return self.run_id.strip() or time.strftime("run_%Y%m%d_%H%M%S")

    def run_dir(self, run_id=None):
        """`output_root/<run_id>/`. Absolute, and never CWD-relative.

        Reads the active/most-recent run's own frozen snapshot once one
        exists (RC-11 item 1) — an explicit `run_id` argument always wins,
        for a caller that wants a specific, different location (e.g. the
        Save-As file dialog).
        """
        run = self._run
        if run_id is None and run is not None:
            return Path(run.output_root) / run.run_id
        return Path(self.output_root) / (run_id or self.effective_run_id())

    def station_meta(self, run_id=None):
        """What the station actually did — the half a CSV column cannot carry.

        Red percent is uninterpretable without these: two runs with the same
        number and different focus-area sizes are not comparable, and the old
        artifact said nothing about it (REDPERCENT-22).

        `annotations` is what the operator *intended* (REDPERCENT-23). The two
        are separate keys on purpose and must never be merged.

        Reads the run's own snapshot once a run exists, and the model's live
        values when one does not — so a sidecar written after the operator
        edits a field mid-run still describes the run that actually
        happened, and a sidecar written before any run ever started (a
        directly-populated `data_log`, as the artifact tests do) still works.
        """
        run = self._run
        if run is not None:
            return {
                "run_id": run_id or run.run_id,
                "probe_name": run.probe_name,
                "probe_tilt_angle": run.probe_tilt_angle,
                "selected_probe_name": self.selected_probe_name,
                "sync_dimensions": list(run.sync_dimensions),
                "focus_area": dict(run.focus_area) if run.focus_area else None,
                "baseline_red": self.baseline_red,
                "red_threshold": dict(self.RED_THRESHOLD),
                "sample_count": len(run.data_log.red_values),
                "started_at": run.started_at,
                "stopped_at": run.stopped_at,
                "annotations": dict(run.annotations),
            }
        log = self.data_log
        return {
            "run_id": run_id or self.effective_run_id(),
            "probe_name": self.probe_name,
            "probe_tilt_angle": self.probe_tilt_angle,
            "selected_probe_name": self.selected_probe_name,
            "sync_dimensions": list(log.sync_dimensions) if log
                               else list(self.sync_dimensions),
            "focus_area": dict(self.focus_area) if self.focus_area else None,
            "baseline_red": self.baseline_red,
            "red_threshold": dict(self.RED_THRESHOLD),
            "sample_count": len(log.red_values) if log else 0,
            "started_at": self._run_started_at,
            "stopped_at": self._run_stopped_at,
            "annotations": dict(self.run_annotations),
        }

    def save_run(self, directory=None, run_id=None):
        """Write the run's whole artifact set, and report where it landed.

        Every file is named `<run_id>_*` so it stays self-describing after
        someone moves it (REDPERCENT-21).
        """
        if not self.data_log or not self.data_log.red_values:
            print(f"[{self.__class__.__name__}] No data to save.")
            return None

        rid = run_id or (self._run.run_id if self._run is not None
                          else self.effective_run_id())
        directory = Path(directory) if directory else self.run_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)

        self.data_log.probe_name = self.probe_name
        self.data_log.probe_tilt_angle = self.probe_tilt_angle

        csv_path = directory / f"{rid}_position.csv"
        meta_path = directory / f"{rid}_station_meta.json"
        try:
            self.data_log.save_to_csv(str(csv_path))
            meta_path.write_text(json.dumps(self.station_meta(rid), indent=2))
        except Exception as e:
            from error_routing import ErrorRouter
            msg = f"[color_test] Error saving run {rid}: {e}"
            print(msg)
            ErrorRouter.report_error("File Save Error", msg, e)
            return None

        print(f"[{self.__class__.__name__}] Run saved to: {directory}")
        return csv_path

    def autosave_log(self):
        """D-10: autosave, into the run's own directory.

        Kept as the unattended path — shutdown, and any client that cannot
        raise a file dialog. The `file_save` composite is the attended one and
        passes the operator's chosen path to `save_log`.

        REDPERCENT-21: this used to build a bare `redpercent_log_<ts>.csv`
        with no directory, so the file landed wherever the launcher started.
        """
        return self.save_run()

    def plot_series(self):
        """The data behind the `plot` composite (D-6).

        Returns `{"x": [...], "y": [...]}`. This is the whole of what a plot
        renderer needs, and it is the reason the plot can now be schema-driven
        in all three views: Tk hand-built a `RedPercentView` around matplotlib
        and PySide bolted on its own duplicate, each reaching into the data
        log directly. Neither was reachable from the Web client at all.
        """
        log = self.data_log
        values = list(getattr(log, "red_values", []) or []) if log else []
        return {"x": list(range(len(values))), "y": values}

    def get_available_probe_names(self) -> list:
        """Live position sources, in device-registry order.

        The `_disabled_in_setup` filter that was here is gone with the
        attribute (RC-9 item 3): a disabled device is not constructed, so it
        cannot be registered, so it cannot be listed. The filter only ever
        did anything on the web path, where it read a flag that normalization
        had already forced to False (WEB-4, MANAGER-12) — it excluded nothing.
        """
        from model import devices
        order = devices.names()
        return sorted(
            self.available_probes,
            key=lambda name: (order.index(name) if name in order else len(order),
                              name),
        )

    # -- unsaved-data query (RC-11 item 3) -------------------------------

    def pending_run_data(self) -> dict:
        """What is unsaved, as data — so a view can ask this instead of
        reaching into `data_log.red_values` itself (D-10's discard prompt,
        and `has_unsaved_data` below share this one source of truth).
        """
        log = self.data_log
        if not log or not log.red_values:
            return {"has_data": False, "sample_count": 0, "run_id": None}
        run = self._run
        return {
            "has_data": True,
            "sample_count": len(log.red_values),
            "run_id": run.run_id if run is not None else self.effective_run_id(),
        }

    @property
    def has_unsaved_data(self) -> bool:
        return self.pending_run_data()["has_data"]

    # -- current_red / red_change (RC-11 item: REDPERCENT-5) -------------
    #
    # Read-only views onto `_red_state`, published as a pair by the monitor
    # loop in one assignment. Before this, `current_red` and `red_change`
    # were two separate attribute writes, so any poller — Tk, PySide's 50 ms
    # `_poll_model`, the web `get_state` — could read the new `current_red`
    # next to the *previous* frame's `red_change`.

    @property
    def current_red(self):
        return self._red_state[0]

    @property
    def red_change(self):
        return self._red_state[1]

    # -- monitoring: derived from the run (RC-11 item 1) ------------------

    @property
    def monitoring(self):
        """Never a free boolean. True exactly while the current run is
        active — never disagrees with whether a thread is actually running,
        because it is not a second thing that has to be kept in sync with
        one (REDPERCENT-4: an exception in the loop used to leave this True
        forever with the thread already dead)."""
        run = self._run
        if run is not None:
            return run.active
        return self._legacy_monitoring

    @monitoring.setter
    def monitoring(self, value):
        """Legacy affordance. `tests/conftest.py`'s leaked-thread sweep and
        `tests/core/test_lifecycle_teardown.py` (outside this file's write
        set) poke this attribute directly rather than calling
        `start_monitoring`/`stop_monitoring`. Setting it False always
        latches whatever run is live exactly like `stop_monitoring()` does,
        so an external caller flipping this off still actually stops the
        thread rather than only hiding the fact that it is running. Setting
        it True with no run active starts nothing; it only makes a
        hand-built instance report itself as monitoring, which is all the
        white-box teardown tests need.
        """
        if value:
            self._legacy_monitoring = True
            return
        self._legacy_monitoring = False
        if self._run is not None:
            self._run.mark_stopped()

    @property
    def ui_schema(self):
        P = self.PARAMS
        return sch.schema(
            sch.section(
                "Run",
                # Fixed for the run's duration, like the rest of the
                # configuration (REDPERCENT-21).
                sch.entry("Run / Cut ID:", "run_id", P["run_id"],
                          disabled_when=("monitoring",)),
            ),
            sch.section(
                # REDPERCENT-23, D-6: declared once here so all three views
                # render it. None of them hand-builds an annotation form.
                "Operator Annotation (intended)",
                *[sch.entry(f"{f.label}:", f.name, P[f.name],
                            disabled_when=("monitoring",))
                  for f in self.ANNOTATION_FIELDS],
            ),
            sch.section(
                "Probe Metadata",
                sch.entry("Probe Name:", "probe_name", P["probe_name"]),
                sch.entry("Probe Tilt Angle:", "probe_tilt_angle",
                          P["probe_tilt_angle"]),
                # **PYSIDE-7.** This dropdown had `model_attr` and no
                # `command`, so PySide reached `getattr(self.model, None)` and
                # raised TypeError. The v2 builder makes `command` mandatory:
                # the broken shape is not expressible.
                sch.dropdown("Position Source:", "selected_probe_name",
                             command="set_stepper_model",
                             options_command="get_available_probe_names"),
            ),
            sch.section(
                "Sync Dimensions",
                # Locked during a run: toggling one mid-run used to raise
                # KeyError inside the monitor thread and kill it, while
                # `monitoring` stayed True (RC-11). The model itself refuses
                # now too (`toggle_sync_x`/`sync_x.setter`) — this gate is
                # rendering only, not the invariant.
                sch.toggle("Sync X", "sync_x", "toggle_sync_x",
                           "Sync X: ON", "Sync X: OFF",
                           disabled_when=("monitoring",)),
                sch.toggle("Sync Y", "sync_y", "toggle_sync_y",
                           "Sync Y: ON", "Sync Y: OFF",
                           disabled_when=("monitoring",)),
                sch.toggle("Sync Z", "sync_z", "toggle_sync_z",
                           "Sync Z: ON", "Sync Z: OFF",
                           disabled_when=("monitoring",)),
            ),
            sch.section(
                "Red Detection",
                # REDPERCENT-19 (model half): a declared display precision,
                # not the box's own `decimals` re-derived by each renderer.
                # Tk (`view.py:_display`) and PySide (`pyside/view.py:_display`)
                # already render readonly numerics through `Param.format`, so
                # `12.3456789012`/`-0.0` are already gone there; the Web
                # client's `pollState` still writes `String(val)` straight
                # from `/api/state` with no formatting step at all (app.js
                # `pollState`'s readonly branch) — that half is outside this
                # file's write set (per-view rendering code), so `format` is
                # declared here and not yet consumed by any renderer. See
                # REDPERCENT-19 in the handoff: partly closed on that basis.
                sch.readonly("Current Red %:", "current_red",
                             param=P["current_red"], format=".2f"),
                sch.readonly("Red Change %:", "red_change",
                             param=P["red_change"], format=".2f"),
                # REDPERCENT-13: published so the Web client can gate the
                # plotter on "is a run actually active" instead of sampling
                # every poll regardless of state (app.js `pollState`). Tk and
                # PySide already have this for free — `monitoring` gates their
                # Start/Stop buttons via `disabled_when`/`enabled_when` — so
                # this is a new *readable* surface for them, not new
                # information; it renders as one more line in this section.
                sch.readonly("Monitoring:", "monitoring", role="info"),
                # **D-6: schema-driven in all three views.** Tk hand-built a
                # RedPercentView and PySide bolted on duplicates; the plot is
                # a composite with one contract now.
                sch.plot("Red % over time", "plot_series",
                         x_label="sample", y_label="red %"),
            ),
            sch.section(
                "System Control",
                sch.button("Start Monitoring", "start_monitoring",
                           inputs=("probe_name", "probe_tilt_angle"),
                           role="go", disabled_when=("monitoring",)),
                sch.button("Stop Monitoring", "stop_monitoring", role="danger",
                           enabled_when=("monitoring",)),
                sch.button("Reset Baseline", "reset_baseline"),
                sch.region_select("Set Focus Area", "set_focus_area",
                                  model_attr="focus_area", role="info"),
                sch.file_save("Save Log", "save_log", extensions=("csv",),
                              role="info"),
            ),
        )

    def save_log(self, file_path=None):
        if not self.data_log or not self.data_log.red_values:
            print(f"[{self.__class__.__name__}] No data to save.")
            return

        # Catch late UI edits before saving
        self.data_log.probe_name = self.probe_name
        self.data_log.probe_tilt_angle = self.probe_tilt_angle

        if file_path:
            try:
                self.data_log.save_to_csv(file_path)
                # The configuration follows the data. A CSV saved through the
                # file dialog is as un-interpretable without its sidecar as an
                # autosaved one (REDPERCENT-22).
                chosen = Path(file_path)
                meta_path = chosen.with_name(chosen.stem + "_station_meta.json")
                meta_path.write_text(
                    json.dumps(self.station_meta(chosen.stem), indent=2))
                print(f"[{self.__class__.__name__}] Log saved to: {file_path}")
            except Exception as e:
                from error_routing import ErrorRouter
                msg = f"[color_test] Error saving file: {e}"
                print(msg)
                ErrorRouter.report_error("File Save Error", msg, e)
        else:
            print(f"[{self.__class__.__name__}] Save cancelled or no file path provided.")

    # -- generation tokens (RC-5 item 1's pattern, copied from probes.py) -

    def _new_monitor_generation(self):
        """Invalidate any monitor thread in flight and return a token for
        the new one."""
        with self._monitor_generation_lock:
            self._monitor_generation += 1
            return self._monitor_generation

    def _monitor_generation_is_current(self, generation):
        with self._monitor_generation_lock:
            return self._monitor_generation == generation

    def start_monitoring(self):
        """Create and start a `MonitoringRun` (RC-11 item 1).

        Returns a `CommandResult`. `Refused`, in order:
          - no focus area set (REDPERCENT-9): today's code starts anyway,
            `capture_focus_area` returns `None` forever, and the loop spins
            with `monitoring` True and zero samples;
          - a run is already active;
          - `mss`/`numpy`/`PIL` failed to import (REDPERCENT-4's `mss=None`
            half): today `_monitor_colors` does `with mss.mss()` and raises
            `AttributeError` inside the thread the instant it starts.
        """
        if not self.focus_area:
            return Refused("Set a focus area before starting monitoring.")
        if self.monitoring:
            return Refused("Monitoring is already running.")
        if mss is None or np is None or Image is None:
            return Refused(
                "Screen capture is unavailable: mss/numpy/PIL failed to "
                "import in this environment.")

        # A previous, unsaved run is about to be replaced (REDPERCENT-2: the
        # new run always gets a fresh log). D-10's default — autosave rather
        # than silently discard — applies here too, not only at teardown.
        if self.pending_run_data()["has_data"]:
            try:
                self.autosave_log()
            except Exception as e:
                from error_routing import ErrorRouter
                ErrorRouter.report_error(
                    "Autosave Failed",
                    f"Could not autosave the previous run before starting "
                    f"a new one: {e}", exception=e,
                    source=self.__class__.__name__)

        generation = self._new_monitor_generation()
        run = MonitoringRun(
            sync_dimensions=self.sync_dimensions,
            focus_area=self.focus_area,
            probe_name=self.probe_name,
            probe_tilt_angle=self.probe_tilt_angle,
            run_id=self.effective_run_id(),
            output_root=self.output_root,
            annotations=self.run_annotations,
            generation=generation,
        )
        self._run = run
        self.data_log = run.data_log
        self._run_started_at = run.started_at
        self._run_stopped_at = None
        print(f"[{self.__class__.__name__}] === MONITORING STARTED ===")

        thread = threading.Thread(
            target=self._monitor_colors, args=(run,), daemon=True,
            name=f"{self.__class__.__name__}-monitor")
        run.thread = thread
        self._monitor_thread = thread
        thread.start()
        return Ok(run)

    def stop_monitoring(self):
        """Latch the current run's stop and return. Never blocks, never
        joins — that is `teardown()`'s job, with a timeout (RC-11's safety
        constraint, see docs/architecture/safety-pattern.md)."""
        print(f"[{self.__class__.__name__}] === MONITORING STOPPED ===")
        self._legacy_monitoring = False
        run = self._run
        if run is not None:
            run.mark_stopped()
            self._run_stopped_at = run.stopped_at
        else:
            self._run_stopped_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    def teardown(self):
        """Stop monitoring, wait for the thread to actually leave, then
        autosave whatever it was still holding (D-10).

        stop_monitoring() only latches the stop; the monitor thread can
        still be inside an mss screen grab. Teardown that returns while it
        runs is what let a torn-down RedPercent keep writing to a datalog
        owned by the next run.
        """
        self.unbind_registry()
        self.stop_monitoring()
        thread = self._monitor_thread
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=2.0)
            except Exception as e:
                print(f"[{self.__class__.__name__}] Monitor thread would not join: {e}")
        self._monitor_thread = None

        if self.pending_run_data()["has_data"] and not self._operator_confirmed_discard():
            try:
                self.autosave_log()
            except Exception as e:
                from error_routing import ErrorRouter
                ErrorRouter.report_error(
                    "Autosave Failed",
                    f"Could not autosave pending Red Percent data on "
                    f"teardown: {e}", exception=e,
                    source=self.__class__.__name__)

    def _operator_confirmed_discard(self) -> bool:
        """Consult the D-10 seam, if a view installed one.

        `True` means the operator was asked and explicitly chose to
        discard rather than save; the autosave is skipped. Anything else —
        no hook installed, the hook returning falsy, or the hook itself
        raising — falls through to the safe D-10 default, autosave. A
        prompt that failed to show is not consent to lose the run.
        """
        hook = self.confirm_discard
        if hook is None:
            return False
        try:
            return bool(hook())
        except Exception as e:
            from error_routing import ErrorRouter
            ErrorRouter.report_warning(
                "Discard Prompt Failed",
                f"confirm_discard raised; autosaving instead of losing the "
                f"run: {e}", source=self.__class__.__name__)
            return False

    def emergency_stop(self):
        """Latch and return. No I/O, no lock held across I/O, no join —
        `stop_monitoring`/`MonitoringRun.mark_stopped` are exactly that."""
        self.stop_monitoring()

    def reset_baseline(self):
        self.baseline_red = self.current_red
        print(f"BASELINE RESET - Red: {self.baseline_red:.1f}%")

    def _publish_red(self, red_pct):
        """REDPERCENT-5: read `baseline_red` exactly once, compute the pair,
        and publish both in one assignment.

        Split out from the loop so it is unit-testable on its own: before
        this, `red_change` read `self.baseline_red` twice in the same
        expression (once for the numerator, once for `max(...)`), so a
        `reset_baseline()` landing between the two reads mixed a value
        computed from two different baselines. And `current_red`/
        `red_change` were two separate statements, so any poller could read
        a mismatched pair.
        """
        baseline = self.baseline_red
        change = ((red_pct - baseline) / max(baseline, 0.1)) * 100
        self._red_state = (red_pct, change)
        return red_pct, change

    def _read_dim(self, run, dim, ts):
        """Read one synced dimension's position and derive its velocity
        from the delta against the last successful read on this run.

        Returns `(loc, vel)`, either of which may be `None` — an explicit
        sentinel for "could not measure", never a fabricated `0.0`
        (REDPERCENT-16, ERRORS-7): `0.0` is a position the stage can
        actually be at, so a failed read must not look like one.

        Velocity is `None` for the dimension's first sample on this run (no
        prior point to difference against) and whenever the elapsed time
        since the last successful read is zero or negative.
        """
        try:
            pos = float(getattr(self.stepper_model, f"pos_{dim.lower()}"))
        except (TypeError, ValueError, AttributeError):
            return None, None

        prev = run.last_position.get(dim)
        run.last_position[dim] = (pos, ts)
        if prev is None:
            return pos, None
        prev_pos, prev_ts = prev
        dt = ts - prev_ts
        if dt <= 0:
            return pos, None
        return pos, (pos - prev_pos) / dt

    def _monitor_colors(self, run):
        """The monitor thread's body. Runs entirely off `run` — its own
        frozen configuration, its own stop event, its own data log — never
        `self.sync_dimensions`/`self.data_log` directly, so a second run
        started while this one is still finishing cannot alias state with
        it (REDPERCENT-1, REDPERCENT-3).

        REDPERCENT-4 remainder: the whole body used to run with no exception
        handling, so any failure left `monitoring` True with a dead thread
        and the UI showing "monitoring" forever. Any unexpected exception is
        now caught, recorded on `run.failure` (which is what makes
        `monitoring` read False afterward — see `MonitoringRun.active`),
        and reported through the error bus.
        """
        first_reading = True
        try:
            with mss.mss() as sct:
                while (not run.stop_event.is_set()
                       and self._monitor_generation_is_current(run.generation)):
                    image = self.capture_focus_area(sct)
                    if image is not None:
                        red_pct = self.detect_red(image)
                        if first_reading:
                            self.baseline_red = red_pct
                            print(f"BASELINE SET - Red: {self.baseline_red:.1f}%")
                            first_reading = False

                        self._publish_red(red_pct)

                        rounded_red = round(red_pct, 1)
                        if abs(rounded_red - run.last_logged_red) >= 0.1:
                            print(f"RED: {rounded_red:.1f}%")
                            run.last_logged_red = rounded_red
                            ts = time.time()
                            locs, vels = {}, {}
                            for dim in run.sync_dimensions:
                                if self.stepper_model is None:
                                    locs[dim], vels[dim] = None, None
                                else:
                                    locs[dim], vels[dim] = self._read_dim(run, dim, ts)
                            run.data_log.add_entry(rounded_red, locs, vels,
                                                   timestamp=ts)

                    run.stop_event.wait(self.FRAME_INTERVAL)
        except Exception as exc:
            run.failure = exc
            print(f"[{self.__class__.__name__}] Monitor thread failed: {exc}")
            try:
                from error_routing import ErrorRouter
                ErrorRouter.report_error(
                    "Red Percent Monitor Failed",
                    f"Monitoring stopped unexpectedly: {exc}",
                    exception=exc, source=self.__class__.__name__,
                    requires_ack=True)
            except Exception:
                pass
        finally:
            run.mark_stopped()


# -- annotation fields as model attributes (REDPERCENT-23) -----------------
#
# The schema declares each annotation field with `model_attr=<name>`, so every
# view reaches it as `getattr(model, name)` / `set_device_attribute`. The
# values live in one `run_annotations` dict rather than in five attributes, so
# that snapshotting a run and emitting its sidecar stay single statements and
# adding a field stays a single line in ANNOTATION_FIELDS.
#
# Generated here rather than written out five times: a hand-written pair per
# field is exactly the duplication that lets the table and the attributes
# drift apart.
def _annotation_property(name):
    def getter(self):
        return self.run_annotations.get(name)

    def setter(self, value):
        self.run_annotations[name] = value

    return property(getter, setter)


for _f in RedPercentSystem.ANNOTATION_FIELDS:
    setattr(RedPercentSystem, _f.name, _annotation_property(_f.name))
del _f
