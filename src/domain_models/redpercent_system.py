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
    def __init__(self, use_x_sync=False):
        self.use_x_sync = use_x_sync
        self.red_values = []
        self.x_values = []
        
    def add_entry(self, red_pct, x_loc=None):
        self.red_values.append(red_pct)
        if self.use_x_sync:
            self.x_values.append(x_loc if x_loc is not None else 0.0)
            
    def save_to_csv(self, filepath):
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            if self.use_x_sync:
                writer.writerow(["Red Percent", "Stepper X Location"])
                for r, x in zip(self.red_values, self.x_values):
                    writer.writerow([r, x])
            else:
                writer.writerow(["Red Percent"])
                for r in self.red_values:
                    writer.writerow([r])

class RedPercentSystem:
    def __init__(self):
        self.monitoring = False
        self.focus_area: typing.Optional[dict] = None
        self.baseline_red = 0.0
        self.current_red = 0.0
        self.red_change = 0.0
        self._monitor_thread = None
        
        self.stepper_model = None
        self.sync_x_location = False
        self.data_log = None

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
            print(f"Error capturing screen: {e}")
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

    def start_monitoring(self):
        if self.monitoring:
            return
        self.monitoring = True
        self.data_log = RedPercentDataLog(self.sync_x_location)
        self._monitor_thread = threading.Thread(target=self._monitor_colors)
        self._monitor_thread.daemon = True
        self._monitor_thread.start()

    def stop_monitoring(self):
        self.monitoring = False

    def reset_baseline(self):
        self.baseline_red = self.current_red

    def _monitor_colors(self):
        first_reading = True
        with mss.mss() as sct:
            while self.monitoring:
                image = self.capture_focus_area(sct)
                if image is not None:
                    red_pct = self.detect_red(image)
                    if first_reading:
                        self.baseline_red = red_pct
                        first_reading = False
                    
                    self.current_red = red_pct
                    self.red_change = ((red_pct - self.baseline_red) / max(self.baseline_red, 0.1)) * 100
                    
                    if self.data_log:
                        x_loc = self.stepper_model.pos_x if (self.sync_x_location and self.stepper_model) else None
                        self.data_log.add_entry(red_pct, x_loc)
                        
                time.sleep(0.1)
