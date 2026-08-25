import threading
import time
try:
    from lib import smc100
except ImportError:
    smc100 = None

class RotatorSystem:
    def __init__(self, default_port="COM1"):
        self.port = default_port
        self.smc_id = 1
        
        self._lock = threading.Lock()
        self._position = None
        self._state = "Disconnected"
        self._error = "0"
        
        self.smc = None
        self.is_connected = False
        self.error_callback = None
        
        self.target_deg = "0"
        self.step_deg = "0"
        
    @property
    def position(self):
        with self._lock: return self._position
        
    @position.setter
    def position(self, value):
        with self._lock: self._position = value

    @property
    def state(self):
        with self._lock: return self._state
        
    @state.setter
    def state(self, value):
        with self._lock: self._state = value

    @property
    def error(self):
        with self._lock: return self._error
        
    @error.setter
    def error(self, value):
        with self._lock: self._error = value

    def _run_async(self, func, *args):
        """Helper to run blocking operations in a thread."""
        thread = threading.Thread(target=self._async_wrapper, args=(func, args))
        thread.daemon = True
        thread.start()

    def _async_wrapper(self, func, args):
        try:
            func(*args)
        except Exception as e:
            if self.error_callback:
                self.error_callback(e)

    def connect(self, port: str, smc_id: int):
        if not self.is_connected:
            self.port = port
            self.smc_id = smc_id
            self.smc = smc100.SMC100(
                smcID=self.smc_id,
                port=self.port,
                silent=True,
                sleepfunc=time.sleep,
            )
            self.is_connected = True

    def disconnect(self):
        if self.smc:
            try:
                self.smc.close()
            except Exception:
                pass
        self.smc = None
        self.is_connected = False
        self.position = None
        self.state = "Disconnected"
        self.error = "0"

    def home(self):
        if self.smc:
            self._run_async(self.smc.home)

    def _move_abs_ui(self):
        try:
            self.move_absolute(float(self.target_deg))
        except ValueError:
            pass

    def _move_rel_pos_ui(self):
        try:
            self.move_relative(float(self.step_deg))
        except ValueError:
            pass

    def _move_rel_neg_ui(self):
        try:
            self.move_relative(-float(self.step_deg))
        except ValueError:
            pass

    @property
    def ui_schema(self):
        return {
            "sections": [
                {
                    "title": "Device Status",
                    "elements": [
                        {"type": "readonly", "text": "Position (deg):", "model_attr": "position"},
                        {"type": "readonly", "text": "State Code:", "model_attr": "state"},
                        {"type": "readonly", "text": "Error Code:", "model_attr": "error"},
                    ]
                },
                {
                    "title": "Commands",
                    "elements": [
                        {"type": "button", "text": "Home Stage", "command": "home"},
                        {"type": "button", "text": "STOP", "command": "stop"},
                        {"type": "button", "text": "Reset & Config", "command": "reset_and_configure"}
                    ]
                },
                {
                    "title": "Motion Control",
                    "elements": [
                        {"type": "entry", "text": "Target (deg):", "model_attr": "target_deg"},
                        {"type": "button", "text": "Move Absolute", "command": "_move_abs_ui"},
                        {"type": "entry", "text": "Step (deg):", "model_attr": "step_deg"},
                        {"type": "button", "text": "Move +", "command": "_move_rel_pos_ui"},
                        {"type": "button", "text": "Move -", "command": "_move_rel_neg_ui"}
                    ]
                }
            ]
        }

    def stop(self):
        if self.smc:
            self.smc.stop()

    def reset_and_configure(self):
        if self.smc:
            self._run_async(self.smc.reset_and_configure)

    def move_absolute(self, target_deg: float):
        if self.smc:
            self._run_async(self.smc.move_absolute_deg, target_deg)

    def move_relative(self, step_deg: float):
        if self.smc:
            self._run_async(self.smc.move_relative_deg, step_deg)

    def poll_status(self):
        if self.is_connected and self.smc:
            try:
                pos = self.smc.get_position_deg()
                err, state = self.smc.get_status(silent=True)
                
                self.position = pos
                self.state = state
                self.error = str(err)
            except Exception:
                pass
