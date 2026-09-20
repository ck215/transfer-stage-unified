import threading
import time
from model import schema as sch
from model.params import Param, table as _param_table
from model.base import SchemaCommands
try:
    from lib import smc100
except ImportError:
    smc100 = None

class RotatorSystem(SchemaCommands):
    #: Past this, moving risks damaging physical tubing.
    SAFE_ROTATION_DEG = 30.0

    PARAMS = _param_table(
        Param("target_deg", "float", default=0, minimum=-175, maximum=175,
              decimals=2, unit="deg", label="Target (deg)"),
        Param("step_deg", "float", default=0, minimum=-175, maximum=175,
              decimals=2, unit="deg", label="Step (deg)"),
        Param("position", "text", default="0", label="Position (deg)"),
    )

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

    # -- schema commands (D-5): the values are already validated ---------
    #
    # These take no arguments. `execute_command` has committed the declared
    # inputs to the model before calling, so there is nothing left to parse
    # here — which is why the three `safe_float`-then-bail shims these replace
    # are gone. A value that could not be parsed never reaches a command now;
    # it is refused by name, at the field, where the operator can see it.

    def move_absolute(self, confirmed=False):
        return self._guarded_move(
            self.PARAMS["target_deg"].coerce(self.target_deg),
            lambda target: self.smc.move_absolute_deg(target),
            "move_absolute", confirmed)

    def move_relative_positive(self, confirmed=False):
        step = self.PARAMS["step_deg"].coerce(self.step_deg)
        return self._guarded_move(
            self._current_position() + step,
            lambda _target: self.smc.move_relative_deg(step),
            "move_relative_positive", confirmed)

    def move_relative_negative(self, confirmed=False):
        step = self.PARAMS["step_deg"].coerce(self.step_deg)
        return self._guarded_move(
            self._current_position() - step,
            lambda _target: self.smc.move_relative_deg(-step),
            "move_relative_negative", confirmed)

    def _current_position(self):
        try:
            return float(self.position)
        except (ValueError, TypeError):
            return 0.0

    def _guarded_move(self, target, run, command_name, confirmed):
        """The ±30° tubing check, in one place, for all three frontends.

        **Returns `NeedsConfirmation` rather than calling back into a view.**
        The old `confirm_rotation_callback` was injected into the model by
        whichever view happened to build it — and the Web client never
        injected one, so `_confirm_rotation` fell through to its "blocked
        automatically" branch and the check existed only as a refusal nobody
        was shown. A guard that silently declines is not the same as a guard
        the operator can answer (S10 item 3).
        """
        if not self.smc:
            return False
        if abs(target) > self.SAFE_ROTATION_DEG and not confirmed:
            return sch.NeedsConfirmation(
                f"Target rotation {target:.2f}\u00b0 exceeds the safe "
                f"\u00b1{self.SAFE_ROTATION_DEG:.0f}\u00b0 range.\n\n"
                "Moving past this limit risks damaging physical tubing.\n\n"
                "Proceed?",
                command_name)
        self._run_async(run, target)
        return True

    @property
    def ui_schema(self):
        P = self.PARAMS
        return sch.schema(
            sch.section(
                "Device Status",
                sch.readonly("Position (deg):", "position", param=P["position"]),
                sch.readonly("State Code:", "state"),
                sch.readonly("Error Code:", "error", role="warning"),
            ),
            sch.section(
                "Commands",
                sch.button("Home Stage", "home", role="go"),
                sch.button("STOP", "stop", role="danger"),
                sch.button("Reset & Config", "reset_and_configure"),
            ),
            sch.section(
                "Motion Control",
                sch.entry("Target (deg):", "target_deg", P["target_deg"]),
                # **D-5 + the confirm contract.** The target travels with the
                # command, and a target outside +/-30 deg comes back as
                # NeedsConfirmation rather than going through a callback the
                # view injected into the model. That callback was never
                # supplied by the Web client, so the tubing check simply did
                # not exist there — a refusal the operator never sees is not
                # a check (S10 item 3).
                sch.button("Move Absolute", "move_absolute",
                           inputs=("target_deg",), role="go"),
                sch.entry("Step (deg):", "step_deg", P["step_deg"]),
                sch.button("Move +", "move_relative_positive",
                           inputs=("step_deg",)),
                sch.button("Move -", "move_relative_negative",
                           inputs=("step_deg",)),
            ),
        )

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

    # `_confirm_rotation`, `move_absolute(target_deg)` and
    # `move_relative(step_deg)` are gone, replaced by the guarded schema
    # commands above. The view-injected `confirm_rotation_callback` went with
    # them: the confirmation is a value the model returns, which every
    # frontend renders with one generic dialog.

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
