import threading
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

class RedPercentSystem:
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

    def save_log_web(self):
        self.save_log(f"redpercent_log_{time.strftime('%Y%m%d_%H%M%S')}.csv")

    def plot_data_ui(self):
        pass

    def set_focus_area_ui(self):
        """Invoked by the web/PySide UI to open the client-side focus-area
        selector. The actual ROI coordinates land via set_focus_area() once
        the user finishes the drag selection; this call itself is a no-op
        on the model."""
        pass

    @property
    def ui_schema(self):
        return {
            "sections": [
                {
                    "title": "Probe Metadata",
                    "elements": [
                        {"type": "entry", "text": "Probe Name:", "model_attr": "probe_name"},
                        {"type": "entry", "text": "Probe Tilt Angle:", "model_attr": "probe_tilt_angle"},
                        {"type": "dropdown", "text": "Position Source:", "model_attr": "selected_probe_name"}
                    ]
                },
                {
                    "title": "Sync Dimensions",
                    "elements": [
                        {"type": "toggle", "text": "Sync X", "model_attr": "sync_x"},
                        {"type": "toggle", "text": "Sync Y", "model_attr": "sync_y"},
                        {"type": "toggle", "text": "Sync Z", "model_attr": "sync_z"}
                    ]
                },
                {
                    "title": "Red Detection",
                    "elements": [
                        {"type": "readonly", "text": "Current Red %:", "model_attr": "current_red"},
                        {"type": "readonly", "text": "Red Change %:", "model_attr": "red_change"}
                    ]
                },
                {
                    "title": "System Control",
                    "elements": [
                        {"type": "button", "text": "Start Monitoring", "command": "start_monitoring", "bg": "darkgreen", "fg": "white"},
                        {"type": "button", "text": "Stop Monitoring", "command": "stop_monitoring", "bg": "darkred", "fg": "white"},
                        {"type": "button", "text": "Reset Baseline", "command": "reset_baseline", "bg": "gray", "fg": "white"},
                        {"type": "button", "text": "Set Focus Area", "command": "set_focus_area_ui", "bg": "darkblue", "fg": "white"},
                        # Not rendered (unknown element type to the UI renderers): registers
                        # set_focus_area in the schema-derived command allowlist. The button
                        # above only opens the client-side selector; the real coordinates are
                        # submitted via this command once the user finishes the drag selection.
                        {"type": "internal", "command": "set_focus_area"},
                        {"type": "button", "text": "Save Log", "command": "save_log_web", "bg": "blue", "fg": "white"},
                        {"type": "button", "text": "Plot Data", "command": "plot_data_ui", "bg": "purple", "fg": "white"}
                    ]
                }
            ]
        }

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
