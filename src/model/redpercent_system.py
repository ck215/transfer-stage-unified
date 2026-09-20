import threading
from model import schema as sch
from model.params import Param, table as _param_table
from model.base import SchemaCommands
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

class RedPercentDataLog:
    def __init__(self, sync_dimensions=None, probe_name="", probe_tilt_angle=""):
        self.sync_dimensions = sync_dimensions or []
        self.probe_name = probe_name
        self.probe_tilt_angle = probe_tilt_angle
        self.red_values = []
        self.loc_values = {dim: [] for dim in self.sync_dimensions}
        self.vel_values = {dim: [] for dim in self.sync_dimensions}
        self.lock = threading.Lock()
        
    def add_entry(self, red_pct, locs=None, vels=None):
        with self.lock:
            self.red_values.append(red_pct)
            locs = locs or {}
            vels = vels or {}
            for dim in self.sync_dimensions:
                self.loc_values[dim].append(locs.get(dim, 0.0))
                self.vel_values[dim].append(vels.get(dim, 0.0))
            
    def save_to_csv(self, filepath):
        with self.lock:
            with open(filepath, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                # Write metadata header block
                writer.writerow(["# Metadata"])
                writer.writerow(["# Probe Name", self.probe_name])
                writer.writerow(["# Probe Tilt Angle", self.probe_tilt_angle])
                writer.writerow([])
                
                headers = ["Red Percent"]
                for dim in self.sync_dimensions:
                    headers.append(f"Stepper {dim} Location")
                    headers.append(f"Stepper {dim} Velocity")
                writer.writerow(headers)
                
                for i in range(len(self.red_values)):
                    row = [self.red_values[i]]
                    for dim in self.sync_dimensions:
                        row.append(self.loc_values[dim][i])
                        row.append(self.vel_values[dim][i])
                    writer.writerow(row)

class RedPercentSystem(SchemaCommands):
    #: A hint, not a class: Tk and PySide need different widgets for the same
    #: device. Each frontend maps the hint to its own class, so neither has to
    #: branch on the device's *name* to find the right view (RC-7, I-7.1).
    #: A model with no hint renders with the generic schema renderer.
    VIEW_HINT = "red_percent"

    PARAMS = _param_table(
        Param("probe_name", "text", default="", label="Probe Name"),
        Param("probe_tilt_angle", "float", default=0.0, decimals=2,
              unit="deg", label="Probe Tilt Angle"),
        Param("current_red", "float", default=0.0, decimals=2, unit="%",
              label="Current Red %"),
        Param("red_change", "float", default=0.0, decimals=2, unit="%",
              label="Red Change %"),
    )

    def __del__(self):
        print(f"[{self.__class__.__name__}] Destructor called")

    def __init__(self):
        self.red_percent = 0.0
        self.is_monitoring = False
        self.stop_event = threading.Event()
        self.thread = None
        self.monitor_thread = None
        
        self.baseline = None
        self.data_log = None
        
        self.stepper_model = None
        self.available_probes = {}
        self.selected_probe_name = None
        self._registry = None
        
        self.sync_dimensions = []
        self.monitoring = False
        self.focus_area: typing.Optional[dict] = None
        self.baseline_red = 0.0
        self.current_red = 0.0
        self.red_change = 0.0
        self._monitor_thread = None
        
        # New metadata fields
        self.probe_name = ""
        self.probe_tilt_angle = ""

    def set_focus_area(self, x, y, w, h):
        self.focus_area = {'top': int(y), 'left': int(x), 'width': int(w), 'height': int(h)}
        print(f"[{self.__class__.__name__}] Focus Area set to: {self.focus_area}")
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

    @property
    def sync_x(self):
        return 'X' in self.sync_dimensions

    @sync_x.setter
    def sync_x(self, value):
        if value and 'X' not in self.sync_dimensions:
            self.sync_dimensions.append('X')
        elif not value and 'X' in self.sync_dimensions:
            self.sync_dimensions.remove('X')

    @property
    def sync_y(self):
        return 'Y' in self.sync_dimensions

    @sync_y.setter
    def sync_y(self, value):
        if value and 'Y' not in self.sync_dimensions:
            self.sync_dimensions.append('Y')
        elif not value and 'Y' in self.sync_dimensions:
            self.sync_dimensions.remove('Y')

    @property
    def sync_z(self):
        return 'Z' in self.sync_dimensions

    @sync_z.setter
    def sync_z(self, value):
        if value and 'Z' not in self.sync_dimensions:
            self.sync_dimensions.append('Z')
        elif not value and 'Z' in self.sync_dimensions:
            self.sync_dimensions.remove('Z')

    def autosave_log(self):
        """D-10: autosave to a timestamped file.

        Kept as the unattended path — shutdown, and any client that cannot
        raise a file dialog. The `file_save` composite is the attended one and
        passes the operator's chosen path to `save_log`.
        """
        path = f"redpercent_log_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        self.save_log(path)
        return path

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

    def toggle_sync_x(self):
        self.sync_x = not self.sync_x

    def toggle_sync_y(self):
        self.sync_y = not self.sync_y

    def toggle_sync_z(self):
        self.sync_z = not self.sync_z

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

    @property
    def has_unsaved_data(self) -> bool:
        return bool(self.data_log and self.data_log.red_values)

    @property
    def ui_schema(self):
        P = self.PARAMS
        return sch.schema(
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
                # `monitoring` stayed True (RC-11).
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
                sch.readonly("Current Red %:", "current_red",
                             param=P["current_red"]),
                sch.readonly("Red Change %:", "red_change",
                             param=P["red_change"]),
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
                print(f"[{self.__class__.__name__}] Log saved to: {file_path}")
            except Exception as e:
                from error_routing import ErrorRouter
                msg = f"[color_test] Error saving file: {e}"
                print(msg)
                ErrorRouter.report_error("File Save Error", msg, e)
        else:
            print(f"[{self.__class__.__name__}] Save cancelled or no file path provided.")

    def start_monitoring(self):
        if self.monitoring:
            return
        self.monitoring = True
        print(f"[{self.__class__.__name__}] === MONITORING STARTED ===")
        if not self.data_log:
            self.data_log = RedPercentDataLog(self.sync_dimensions, self.probe_name, self.probe_tilt_angle)
        self._monitor_thread = threading.Thread(target=self._monitor_colors)
        self._monitor_thread.daemon = True
        self._monitor_thread.start()

    def stop_monitoring(self):
        print(f"[{self.__class__.__name__}] === MONITORING STOPPED ===")
        self.monitoring = False

    def teardown(self):
        """Stop monitoring and wait for the thread to actually leave.

        stop_monitoring() only clears the flag; the monitor thread can still
        be inside an mss screen grab. Teardown that returns while it runs is
        what let a torn-down RedPercent keep writing to a datalog owned by
        the next run.
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

    def emergency_stop(self):
        self.stop_monitoring()

    def reset_baseline(self):
        self.baseline_red = self.current_red
        print(f"BASELINE RESET - Red: {self.baseline_red:.1f}%")

    def _monitor_colors(self):
        first_reading = True
        
        with mss.mss() as sct:
            while self.monitoring:
                image = self.capture_focus_area(sct)
                if image is not None:
                    red_pct = self.detect_red(image)
                    if first_reading:
                        self.baseline_red = red_pct
                        print(f"BASELINE SET - Red: {self.baseline_red:.1f}%")
                        first_reading = False
                    
                    self.current_red = red_pct
                    self.red_change = ((red_pct - self.baseline_red) / max(self.baseline_red, 0.1)) * 100
                    
                    if self.data_log:
                        rounded_red = round(red_pct, 1)
                        if not hasattr(self, 'last_logged_red'):
                            self.last_logged_red = -1000.0
                            
                        if abs(rounded_red - self.last_logged_red) >= 0.1:
                            print(f"RED: {rounded_red:.1f}%")
                            self.last_logged_red = rounded_red
                            locs = {}
                            vels = {}
                            if self.stepper_model:
                                if 'X' in self.sync_dimensions:
                                    try: locs['X'] = float(self.stepper_model.pos_x)
                                    except: locs['X'] = 0.0
                                    vels['X'] = getattr(self.stepper_model, 'vel_x', 0.0)
                                if 'Y' in self.sync_dimensions:
                                    try: locs['Y'] = float(self.stepper_model.pos_y)
                                    except: locs['Y'] = 0.0
                                    vels['Y'] = getattr(self.stepper_model, 'vel_y', 0.0)
                                if 'Z' in self.sync_dimensions:
                                    try: locs['Z'] = float(self.stepper_model.pos_z)
                                    except: locs['Z'] = 0.0
                                    vels['Z'] = getattr(self.stepper_model, 'vel_z', 0.0)
                                    
                            self.data_log.add_entry(rounded_red, locs, vels)
                        
                time.sleep(0.016) # ~60 FPS continuous logging
