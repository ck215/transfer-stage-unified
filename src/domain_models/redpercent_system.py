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

class RedPercentSystem:
    def __init__(self):
        self.monitoring = False
        self.focus_area: typing.Optional[dict] = None
        self.baseline_red = 0.0
        self.current_red = 0.0
        self.last_printed_red = -1.0
        self.log_data = []
        self._monitor_thread = None
        self.red_change = 0.0

    def add_log(self, entry):
        self.log_data.append(entry)

    def capture_focus_area(self, sct):
        if not self.focus_area:
            return None
        try:
            screenshot = sct.grab(self.focus_area)
            img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')
            return np.array(img)
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
        print("=== MONITORING STARTED ===")
        self._monitor_thread = threading.Thread(target=self._monitor_colors)
        self._monitor_thread.daemon = True
        self._monitor_thread.start()

    def stop_monitoring(self):
        self.monitoring = False
        print("=== MONITORING STOPPED ===")

    def reset_baseline(self):
        self.baseline_red = self.current_red
        log_entry = f"BASELINE RESET - Red: {self.baseline_red:.1f}%"
        print(log_entry)
        self.add_log(log_entry)

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
                        log_entry = f"BASELINE SET - Red: {red_pct:.1f}%"
                        print(log_entry)
                        self.add_log(log_entry)
                    
                    self.current_red = red_pct
                    rounded_red = round(red_pct, 1)
                    if abs(rounded_red - self.last_printed_red) >= 0.1:
                        log_entry = f"RED: {rounded_red:.1f}%"
                        print(log_entry)
                        self.add_log(log_entry)
                        self.last_printed_red = rounded_red
                    
                    self.red_change = ((red_pct - self.baseline_red) / max(self.baseline_red, 0.1)) * 100
                time.sleep(0.1)

    def get_log_data(self):
        return self.log_data
