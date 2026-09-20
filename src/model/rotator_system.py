import threading
import time
try:
    from lib import smc100
except ImportError:
    smc100 = None

class RotatorSystem:
    def __del__(self):
        print(f"[{self.__class__.__name__}] Destructor called")

    def __init__(self, default_port=None):
        self.port = default_port
        self.smc_id = 1
        
        self._lock = threading.Lock()
        self._position = None
        self._state = "Disconnected"
        self._error = "0"
        
        self.smc = None
        self.is_connected = False
        self.error_callback = None
        self.confirm_rotation_callback = None  # Optional[Callable[[float], bool]]
        
        self.target_deg = "0"
        self.step_deg = "0"
        
        if default_port and default_port != "None" and default_port != "SIM":
            self.connect(default_port, self.smc_id)
        
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
            else:
                try:
                    from error_routing import ErrorRouter
                    ErrorRouter.report_error("Rotator Controller Error", f"Action failed:\n{e}", e)
                except Exception:
                    pass

    def connect(self, port: str, smc_id: int = 1):
        with self._lock:
            if self.is_connected:
                return
            self.port = port
            self.smc_id = smc_id
        if smc100 is not None:
            try:
                smc_inst = smc100.SMC100(
                    smcID=self.smc_id,
                    port=self.port,
                    silent=True,
                    sleepfunc=time.sleep,
                )
                smc_inst.get_status(silent=True)
                with self._lock:
                    self.smc = smc_inst
                    self.is_connected = True
                    self._state = "Connected"
            except Exception as e:
                with self._lock:
                    self.smc = None
                    self.is_connected = False
                    self._state = "Disconnected"
                if self.error_callback:
                    self.error_callback(e)
                else:
                    try:
                        from error_routing import ErrorRouter
                        ErrorRouter.report_error("Rotator Connection Error", f"Failed to connect to SMC100 on {port}:\n{e}", e)
                    except Exception:
                        pass

    def disconnect(self):
        with self._lock:
            smc = self.smc
            self.smc = None
            self.is_connected = False
            self._position = None
            self._state = "Disconnected"
            self._error = "0"
        if smc:
            try:
                smc.close()
            except Exception:
                pass

    def teardown(self):
        """Stop the stage, then release the port (MANAGER-10, ROTATOR-1).

        This used to call disconnect() alone, so tearing the rotator down
        never sent ST — a stage mid-move kept moving after the port closed,
        with nothing left able to stop it.
        """
        try:
            self.stop()
        except Exception as e:
            print(f"[{self.__class__.__name__}] Stop failed during teardown: {e}")
        self.disconnect()

    def emergency_stop(self):
        self.stop()

    def home(self):
        if self.smc:
            self._run_async(self.smc.home)

    def _move_abs_ui(self):
        from model.numeric import safe_float
        val = safe_float(self.target_deg)
        if val is None:
            return
        self.move_absolute(val)

    def _move_rel_pos_ui(self):
        from model.numeric import safe_float
        val = safe_float(self.step_deg)
        if val is None:
            return
        self.move_relative(val)

    def _move_rel_neg_ui(self):
        from model.numeric import safe_float
        val = safe_float(self.step_deg)
        if val is None:
            return
        self.move_relative(-val)

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
            try:
                self.smc.stop()
            except Exception as e:
                if self.error_callback:
                    self.error_callback(e)
                else:
                    try:
                        from error_routing import ErrorRouter
                        ErrorRouter.report_error("Rotator Error", f"Failed to send stop:\n{e}", e)
                    except Exception:
                        pass

    def reset_and_configure(self):
        if self.smc:
            self._run_async(self.smc.reset_and_configure)

    def _confirm_rotation(self, target_deg: float) -> bool:
        if abs(target_deg) <= 30.0:
            return True
        if self.confirm_rotation_callback:
            return self.confirm_rotation_callback(target_deg)
        print(f"[{self.__class__.__name__}] Rotation past ±30 blocked automatically (no confirmation handler registered).")
        return False

    def move_absolute(self, target_deg: float):
        if self.smc:
            if not self._confirm_rotation(target_deg):
                return
            self._run_async(self.smc.move_absolute_deg, target_deg)

    def move_relative(self, step_deg: float):
        if self.smc:
            try:
                current = float(self.position)
            except (ValueError, TypeError):
                current = 0.0
            target = current + step_deg
            if not self._confirm_rotation(target):
                return
            self._run_async(self.smc.move_relative_deg, step_deg)

    def _map_state_code(self, code: str) -> str:
        code = str(code).upper()
        try:
            from lib.smc100 import (
                STATE_NOT_REFERENCED_FROM_RESET, STATE_NOT_REFERENCED_FROM_HOMING, STATE_NOT_REFERENCED_FROM_CONFIGURATION,
                STATE_READY_FROM_HOMING, STATE_READY_FROM_MOVING, STATE_READY_FROM_DISABLE,
                STATE_HOMING_FROM_RS232, STATE_HOMING_FROM_SMC_RC,
                STATE_MOVING,
                STATE_DISABLE_FROM_READY, STATE_DISABLE_FROM_MOVING, STATE_DISABLE_FROM_JOGGING
            )
            if code in (STATE_NOT_REFERENCED_FROM_RESET, STATE_NOT_REFERENCED_FROM_HOMING, STATE_NOT_REFERENCED_FROM_CONFIGURATION):
                return "Not referenced - run Home"
            elif code in (STATE_READY_FROM_HOMING, STATE_READY_FROM_MOVING, STATE_READY_FROM_DISABLE):
                return "Ready"
            elif code in (STATE_HOMING_FROM_RS232, STATE_HOMING_FROM_SMC_RC):
                return "Homing"
            elif code == STATE_MOVING:
                return "Moving"
            elif code in (STATE_DISABLE_FROM_READY, STATE_DISABLE_FROM_MOVING, STATE_DISABLE_FROM_JOGGING, "3F"): # 3F not in SMC100 docs
                return "Disabled"
        except ImportError:
            if code in ("0A", "0B", "0C"):
                return "Not referenced - run Home"
            elif code in ("32", "33", "34"):
                return "Ready"
            elif code in ("1E", "1F"):
                return "Homing"
            elif code == "28":
                return "Moving"
            elif code in ("3C", "3D", "3E", "3F"):
                return "Disabled"
        return code

    def poll_status(self):
        if self.is_connected and self.smc:
            try:
                pos = self.smc.get_position_deg()
                err, state = self.smc.get_status(silent=True)
                
                self.position = pos
                self.state = self._map_state_code(state)
                self.error = str(err)
            except Exception as e:
                try:
                    from error_routing import ErrorRouter
                    ErrorRouter.report_warning("Rotator Poll Error", f"Failed to read status:\n{e}")
                except Exception:
                    pass
