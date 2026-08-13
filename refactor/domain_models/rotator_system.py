import threading
import time
from lib import smc100

class RotatorSystem:
    def __init__(self, default_port="COM1"):
        self.port = default_port
        self.smc_id = 1
        
        self.position = None
        self.state = "Disconnected"
        self.error = "0"
        
        self.smc = None
        self.is_connected = False
        
        self.error_callback = None

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
