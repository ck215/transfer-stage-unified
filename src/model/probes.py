import math
import time
import copy
import threading
from controller.serial import serial, PACKET_FORMAT
from error_routing import ErrorRouter as ErrorPopupManager
from model.numeric import num as _num

try:
    import gcodeparser
except ImportError:
    gcodeparser = None

class BaseProbe:
    # Overridable by tests to avoid waiting on the real 5-minute timeout.
    _INTERLOCK_POLL_INTERVAL = 5
    _INTERLOCK_TIMEOUT = 300

    # The one documented rate for each model-owned loop (RC-4).
    #
    # Manual commands: Tk drove this at 50 ms and PySide at 20 ms, so the two
    # frontends did not feel the same. 20 ms is adopted — the faster of the
    # two, and the one the primary GUI has been using. Related to D-12; if the
    # owner rules on the gamepad poll rate, revisit this alongside it.
    #
    # Hardware sampling: both frontends had dedicated 100 ms timers, but
    # PySide *additionally* sampled from _poll_model every 50 ms, roughly
    # tripling the serial traffic for one device. 100 ms restores the
    # documented rate, once, in one place.
    MANUAL_COMMAND_INTERVAL = 0.02   # s -> 50 Hz
    SAMPLE_INTERVAL = 0.10           # s -> 10 Hz

    def __del__(self):
        print(f"[{self.__class__.__name__}] Destructor called")

    def __init__(self, port, controller_id, active_claims=None):
        self.serial_comm = serial(port) if port and port != "None" else None
        
        self.packet_format = PACKET_FORMAT  # Standardized 42-byte float format
        
        # Position variables
        self.pos_x = "0"
        self.pos_y = "0"
        self.pos_z = "0"
        
        # Connection variables
        self.controller_var = controller_id
        self.serial_port = port
        self.active_claims = active_claims or {}
        self.poller = None
        try:
            from controller.gamepad import ControllerPoller
            self.poller = ControllerPoller(controller_id, self.active_claims, self.__class__.__name__)
        except Exception as e:
            print(f"[{self.__class__.__name__}] Gamepad unavailable, running headless: {e}")
        
        # Step sizes
        self.x_step = "16"
        self.y_step = "16"
        self.z_step = "16"
        
        # Step counts (distances)
        self.x_dist = "0"
        self.y_dist = "0"
        self.z_dist = "0"
        
        # Velocity control
        self.full_speed = "400"
        self.man_full_speed = "400"
        
        # State flags
        self.system_enabled = False
        self.auton_flag = False
        self.manual_flag = False
        self.is_stepping = False

        # Fault state (RC-2). Set when a command's fate is unknown — a write
        # that failed means the hardware may be in either state, and saying
        # "disabled" would be a guess presented as a fact. It persists until
        # a successful enable/disable proves the actual state.
        self.fault_reason = None

        # FULL STOP latch (RC-5). Set by emergency_stop() *before* any I/O and
        # checked immediately before every motion write, so a command already
        # in flight on another thread cannot land after the stop. It is
        # cleared only by an explicit operator action, never automatically:
        # a latch that clears itself is not a latch.
        self._estop = threading.Event()

        # Auto-disable interlock (mirrors main's stepper_frame.py/chuck_frame.py
        # 5-minute idle timeout). Lives in the model, not the view, so every
        # frontend shares it — the web dashboard previously had none at all.
        self.last_activity_time = time.time()
        self._interlock_stop = threading.Event()
        self._interlock_thread = None

        # Model-owned loops (RC-4). These used to live in the views: Tk's
        # _route_input (50 ms) and PySide's input_timer (20 ms) each pumped
        # the gamepad, and each frontend ran its own position/status timers.
        # Three copies meant three sets of rates and three chances to diverge,
        # and the Web frontend — which has no such timers — simply had no
        # manual mode at all.
        self._loops_stop = threading.Event()
        self._input_thread = None
        self._sample_thread = None
        self.last_sample_time = 0.0

    @property
    def vel_x(self):
        state = self.poller.get_mapped_state() if self.poller else {}
        val = state.get("x_axisStatus", 0.0)
        try: return float(val) if val is not None else 0.0
        except (ValueError, TypeError): return 0.0

    @property
    def vel_y(self):
        state = self.poller.get_mapped_state() if self.poller else {}
        val = state.get("y_axisStatus", 0.0)
        try: return float(val) if val is not None else 0.0
        except (ValueError, TypeError): return 0.0

    @property
    def vel_z(self):
        state = self.poller.get_mapped_state() if self.poller else {}
        r_val = state.get("z_axisStatusR", -1.0)
        l_val = state.get("z_axisStatusL", -1.0)
        try:
            r = float(r_val) if r_val is not None else -1.0
            l = float(l_val) if l_val is not None else -1.0
            return (r - l) / 2.0
        except (ValueError, TypeError):
            return 0.0

    @property
    def ui_schema(self):
        return {
            "sections": [
                {
                    "title": "Coordinate Frame",
                    "elements": [
                        {"type": "readonly", "text": "X Position:", "model_attr": "pos_x"},
                        {"type": "readonly", "text": "Y Position:", "model_attr": "pos_y"},
                        {"type": "readonly", "text": "Z Position:", "model_attr": "pos_z"}
                    ]
                },
                {
                    "title": "Configuration",
                    "elements": [
                        {"type": "readonly", "text": "Serial Port:", "model_attr": "serial_port"},
                        {"type": "dropdown", "text": "Controller ID:", "model_attr": "controller_var",
                         "options_command": "get_available_controllers", "command": "set_controller"},
                        {"type": "entry", "text": "X Step Size:", "model_attr": "x_step"},
                        {"type": "entry", "text": "Y Step Size:", "model_attr": "y_step"},
                        {"type": "entry", "text": "Z Step Size:", "model_attr": "z_step"},
                        {"type": "entry", "text": "Target X Dist:", "model_attr": "x_dist"},
                        {"type": "entry", "text": "Target Y Dist:", "model_attr": "y_dist"},
                        {"type": "entry", "text": "Target Z Dist:", "model_attr": "z_dist"},
                        {"type": "entry", "text": "Autonomous Speed:", "model_attr": "full_speed"},
                        {"type": "entry", "text": "Manual Speed:", "model_attr": "man_full_speed"}
                    ]
                },
                {
                    "title": "System Control",
                    "elements": [
                        # Per-device "System Power" toggle removed: enable/disable is already
                        # reachable via the Autonomous/Manual mode toggles below (both call
                        # enable() on entry, full_stop() on exit), and a separate System Power
                        # control was a redundant, easy-to-desync third way to the same state.
                        {"type": "toggle", "text": "Autonomous:", "model_attr": "auton_flag",
                         "true_text": "AUTONOMOUS MODE (Click to Stop)",
                         "false_text": "Enter Autonomous Mode", "command": "toggle_auton"},
                        {"type": "toggle", "text": "Manual / Gamepad:", "model_attr": "manual_flag",
                         "true_text": "MANUAL MODE (Click to Stop)",
                         "false_text": "Enter Manual Mode", "command": "toggle_manual"},
                        {"type": "button", "text": "Start Stepping", "command": "macro_start_auton", "bg": "darkgreen", "fg": "white"},
                        # Per-device "Full Stop" removed: SystemManager.full_stop_all() (the
                        # dashboard's global FULL STOP bar) already calls this model's
                        # full_stop() directly, so a dedicated per-tab button was a redundant,
                        # confusing second E-stop. Per-device stop is still reachable by
                        # toggling Autonomous/Manual mode off (both call full_stop()).
                        #
                        # "Run Script" (file_picker) removed from the live dashboard: the
                        # underlying run_script() framework stays in place for future
                        # development, but isn't exposed as a UI entry point yet.
                        #
                        # Runtime serial reconnect is purged (owner decision D-11).
                        # Serial port assignment happens once, at setup; the port is
                        # readonly above. Unlike gamepads (designed to hot-swap), a live
                        # serial reconnect desyncs Python-side enable/disable state from
                        # the firmware, which persists its enabled state across a reopen —
                        # Python would report disabled while coils stayed energized.
                        # Recovering from a lost port means relaunching.
                        {"type": "button", "text": "Controller Log Window", "command": "open_controller_log", "bg": "black", "fg": "white"}
                    ]
                }
            ]
        }

    def get_available_controllers(self):
        if self.poller:
            return self.poller.get_physical_controllers()
        return []

    def set_controller(self, controller_id):
        print(f"[{self.__class__.__name__}] Swapping controller to: {controller_id}")
        self.controller_var = controller_id
        if self.poller:
            self.poller.set_controller(controller_id)
        else:
            try:
                from controller.gamepad import ControllerPoller
                self.poller = ControllerPoller(controller_id, self.active_claims, self.__class__.__name__)
            except Exception as e:
                print(f"[{self.__class__.__name__}] Gamepad still unavailable: {e}")

    def open_controller_log(self):
        print(f"[{self.__class__.__name__}] Controller log window requested")

    def toggle_manual(self):
        if self.manual_flag:
            self.full_stop()
        else:
            self.enter_manual()

    def toggle_auton(self):
        if self.auton_flag:
            self.full_stop()
        else:
            self.enter_auton()

    def _enter_fault(self, reason):
        """Record that the hardware state is unknown (RC-2)."""
        self.fault_reason = reason
        print(f"[{self.__class__.__name__}] FAULT: {reason}")
        try:
            ErrorPopupManager.report_error(
                "Hardware State Unknown",
                f"{self.__class__.__name__}: {reason}\n\n"
                "The command may not have reached the hardware. Treat the "
                "device as live until this is resolved.", None)
        except Exception:
            pass

    @property
    def in_fault(self):
        return self.fault_reason is not None

    def _clear_fault(self):
        self.fault_reason = None

    @property
    def estop_latched(self):
        return self._estop.is_set()

    def clear_estop(self):
        """Explicit operator action. Nothing else may call this (RC-5)."""
        self._estop.clear()
        print(f"[{self.__class__.__name__}] FULL STOP latch cleared by operator")

    def _refuse_if_estopped(self, what):
        """True when the latch forbids `what`. Checked before every motion write."""
        if self._estop.is_set():
            print(f"[{self.__class__.__name__}] {what} refused: FULL STOP is latched")
            return True
        return False

    def send_stop_command(self):
        if self.serial_comm:
            params = {
                "x_step_size": 0, "y_step_size": 0, "z_step_size": 0,
                "full_speed": 0, "slow_speed": 0, "brake_distance": 0,
                "x_dist": 0, "y_dist": 0, "z_dist": 0,
                "command_code_manual": 0, "command_code_auton": 0
            }
            self.serial_comm.send_autonomous_command(params)

    def enter_auton(self):
        if not self.enable():
            return
        self.auton_flag = True
        self.manual_flag = False
        self.send_stop_command()

    def enter_manual(self):
        # Check gamepad presence BEFORE enable(): enable() unconditionally
        # sends the hardware 'e' command, energizing the coils. Checking
        # afterward (as send_manual_mode_command's defensive gamepad check
        # does on the next poll tick) is too late -- it can revert
        # manual_flag in Python, but the firmware has already been told to
        # enable and nothing walks that back, leaving coils falsely
        # energized for a mode that never actually engaged.
        if not self.poller or not self.poller.gamepad:
            msg = "Cannot enter manual mode: no gamepad/controller attached."
            print(f"[{self.__class__.__name__}] {msg}")
            ErrorPopupManager.report_warning("Manual Mode Blocked", msg)
            return
        if not self.enable():
            return
        self.auton_flag = False
        self.manual_flag = True
        self.send_stop_command()

    def macro_start_auton(self):
        if not self.enable():
            return
        self.auton_flag = True
        self.manual_flag = False
        self.is_stepping = True
        self.send_autonomous_command()

    def run_script(self, script_path=None):
        if not script_path or not self.serial_comm:
            return
            
        print(f"[{self.__class__.__name__}] Parsing and executing script: {script_path}")
        self.enter_auton()

        def _execute():
            self.is_stepping = True
            try:
                if gcodeparser is None:
                    e = ImportError("gcodeparser not installed")
                    print(f"[{self.__class__.__name__}] Script execution error: {e}")
                    ErrorPopupManager.report_error("Script Execution Error", f"Error running script:\n{e}", e)
                    return
                with open(script_path, 'r', encoding="utf-8") as f:
                    gcode_content = f.read()
                
                # Check if serial connection is active
                if not getattr(self.serial_comm, 'ser', None):
                    raise AttributeError("Serial connection not active.")
                
                # Support both GcodeParser and parse_gcode_lines
                if hasattr(gcodeparser, 'GcodeParser'):
                    parsed = gcodeparser.GcodeParser(gcode_content)
                    lines = parsed.lines
                elif hasattr(gcodeparser, 'parse_gcode_lines'):
                    import io
                    lines = list(gcodeparser.parse_gcode_lines(io.StringIO(gcode_content), include_comments=False))
                else:
                    lines = []

                for line in lines:
                    if not self.is_stepping or not self.auton_flag:
                        print(f"[{self.__class__.__name__}] Script execution halted by user state override.")
                        break
                    gcode_str = getattr(line, 'gcode_str', str(line))
                    params = getattr(line, 'params', {})
                    command = getattr(line, 'command', ('', 0))
                    
                    if command and command[0] == 'G':
                        x_dist = params.get('X', 0)
                        y_dist = params.get('Y', 0)
                        z_dist = params.get('Z', 0)
                        feedrate = params.get('F', self.full_speed)
                        
                        cmd_params = {
                            "x_step_size": self.x_step,
                            "y_step_size": self.y_step,
                            "z_step_size": self.z_step,
                            "full_speed": str(feedrate),
                            "slow_speed": 0,
                            "brake_distance": 0,
                            "x_dist": str(x_dist),
                            "y_dist": str(y_dist),
                            "z_dist": str(z_dist),
                            "command_code_manual": 0,
                            "command_code_auton": 1
                        }
                        if self._refuse_if_estopped("script motion"):
                            break
                        self.serial_comm.send_autonomous_command(cmd_params)
                        
                        x_steps = abs(float(self.x_step) * float(x_dist))
                        y_steps = abs(float(self.y_step) * float(y_dist))
                        z_steps = abs(float(self.z_step) * float(z_dist))
                        dist = math.sqrt(x_steps**2 + y_steps**2 + z_steps**2)
                        speed = float(feedrate) if float(feedrate) > 0 else float(self.full_speed)
                        duration = (dist / speed) + 0.05 if speed > 0 else 0.1
                        time.sleep(duration)
                    elif ',' in gcode_str:
                        if self._refuse_if_estopped("script motion"):
                            break
                        if self.serial_comm:
                            self.serial_comm.write_command(gcode_str.strip() + '\n')
                        time.sleep(0.1)
                    else:
                        if self._refuse_if_estopped("script motion"):
                            break
                        if self.serial_comm:
                            self.serial_comm.write_command(gcode_str + '\n')
                        time.sleep(0.1)
            except Exception as e:
                print(f"[{self.__class__.__name__}] Script execution error: {e}")
                ErrorPopupManager.report_error("Script Execution Error", f"Error running script:\n{e}", e)
            finally:
                self.full_stop()

        threading.Thread(target=_execute, daemon=True).start()

    def get_params(self):
        return {
            "x_step_size": _num(self.x_step, 16, minimum=1, integer=True),             
            "y_step_size": _num(self.y_step, 16, minimum=1, integer=True),              
            "z_step_size": _num(self.z_step, 16, minimum=1, integer=True),              
            "full_speed": _num(self.full_speed, 400, minimum=1),           
            "slow_speed": 0,           
            "brake_distance": 0,   
            "x_dist": _num(self.x_dist, 0),                   
            "y_dist": _num(self.y_dist, 0),                   
            "z_dist": _num(self.z_dist, 0),  
            "command_code_manual": int(self.manual_flag),               
            "command_code_auton": int(self.auton_flag)                      
        }

    def send_autonomous_command(self):
        if self._refuse_if_estopped("autonomous command"):
            return
        self.touch_activity()
        if self.serial_comm:
            self.serial_comm.send_autonomous_command(self.get_params())

    def send_manual_mode_command(self, controller_params):
        if self.manual_flag and (not self.poller or not self.poller.gamepad):
            self.manual_flag = False
            msg = "Manual mode disabled: no gamepad/controller attached."
            print(f"[{self.__class__.__name__}] {msg}")
            ErrorPopupManager.report_warning("Manual Mode Blocked", msg)
            controller_params = {}
            
        if self.serial_comm:
            if (controller_params.get("x_axisStatus", 0.0) or controller_params.get("y_axisStatus", 0.0)
                    or controller_params.get("z_axisStatusR", -1.0) != -1.0
                    or controller_params.get("z_axisStatusL", -1.0) != -1.0
                    or controller_params.get("dpad_LR", 0) or controller_params.get("dpad_UD", 0)
                    or controller_params.get("LBumper", 0) or controller_params.get("RBumper", 0)):
                self.touch_activity()
            params = {
                "x_axisStatus": controller_params.get("x_axisStatus", 0.0),
                "y_axisStatus": controller_params.get("y_axisStatus", 0.0),
                "z_axisStatusR": controller_params.get("z_axisStatusR", -1.0),
                "z_axisStatusL": controller_params.get("z_axisStatusL", -1.0),
                "x_stepSize": _num(self.x_step, 16, minimum=1, integer=True),
                "y_stepSize": _num(self.y_step, 16, minimum=1, integer=True),
                "z_stepSize": _num(self.z_step, 16, minimum=1, integer=True),
                "dpad_LR": controller_params.get("dpad_LR", 0),
                "dpad_UD": controller_params.get("dpad_UD", 0),
                "LBumper": controller_params.get("LBumper", 0),
                "RBumper": controller_params.get("RBumper", 0),
                "manual_jog_speed": _num(self.man_full_speed, 400, minimum=1),
                "packet_format": self.packet_format
            }
            if self._refuse_if_estopped("manual command"):
                return
            self.serial_comm.send_manual_mode_command(params)

    def read_position(self):
        if self.serial_comm:
            pos = self.serial_comm.read_position()
            if pos:
                self.pos_x, self.pos_y, self.pos_z = str(pos[0]), str(pos[1]), str(pos[2])

    # -- model-owned loops (RC-4) --------------------------------------

    def start_loops(self):
        """Start the input pump and the hardware sampler. Idempotent.

        Demand-driven rather than started at construction: an idle or
        test-constructed model should not be running two threads. `enable()`
        starts them, because that is the point at which the model is armed
        and something is worth pumping or sampling. Every frontend reaches
        this through the same path — including the Web dashboard, which had
        no input pump of its own at all.
        """
        self._loops_stop.clear()
        if self.poller is not None:
            # The model starts the poller, with no GUI root, so the poller
            # runs its own thread. The views used to do this and hand in an
            # adapter wrapping their own event loop — which is why the Web
            # frontend, having no such loop to offer, never polled at all.
            def _log(msg):
                print(f"[controllerDrive] {msg}")
            self.poller.start_polling(
                None, log_updater=_log, activity_callback=self.touch_activity)
        if self._input_thread is None or not self._input_thread.is_alive():
            self._input_thread = threading.Thread(
                target=self._input_loop, daemon=True,
                name=f"input-{self.__class__.__name__}")
            self._input_thread.start()
        if self._sample_thread is None or not self._sample_thread.is_alive():
            self._sample_thread = threading.Thread(
                target=self._sample_loop, daemon=True,
                name=f"sample-{self.__class__.__name__}")
            self._sample_thread.start()

    def stop_loops(self):
        self._loops_stop.set()

    def _input_loop(self):
        """Pump gamepad input to the hardware while manual mode is engaged.

        Exception-isolated: a failure here faults the model rather than
        killing the thread silently, which is what a view-owned timer did —
        the timer died and manual mode simply stopped responding with no
        indication that anything had gone wrong.
        """
        was_manual = False
        while not self._loops_stop.wait(self.MANUAL_COMMAND_INTERVAL):
            try:
                manual = bool(self.manual_flag)
                if manual and not self._estop.is_set():
                    params = self.poller.get_mapped_state() if self.poller else {}
                    self.send_manual_mode_command(params or {})
                elif was_manual:
                    # Neutral on exit (I-4.2). Leaving manual mode has to send
                    # one zeroed frame, or the last non-zero command stands and
                    # the axis keeps moving.
                    self.send_manual_mode_command({})
                was_manual = manual
            except Exception as e:
                self._enter_fault(f"manual input pump failed: {e}")
                return

    def _sample_loop(self):
        """Sample hardware into the model's cached fields.

        Views and /api/state read the cache and never touch the transport
        (invariant I-4.1). A stalled read therefore cannot block a render
        tick or FULL STOP (I-4.3).
        """
        while not self._loops_stop.wait(self.SAMPLE_INTERVAL):
            try:
                self.read_position()
                self.last_sample_time = time.time()
            except Exception as e:
                # Sampling is best-effort: a transport hiccup must not kill
                # the loop, or positions freeze silently for the rest of the
                # session.
                print(f"[{self.__class__.__name__}] Sample failed: {e}")

    def touch_activity(self):
        self.last_activity_time = time.time()

    def _start_interlock_watchdog(self):
        if self._interlock_thread and self._interlock_thread.is_alive():
            return
        self._interlock_stop.clear()
        self.touch_activity()

        def _watch():
            while not self._interlock_stop.wait(self._INTERLOCK_POLL_INTERVAL):
                if not self.system_enabled:
                    return
                if self.is_stepping or self.manual_flag:
                    # Deferred while actively operating, matching this
                    # refactor's existing Tkinter/PySide behavior.
                    continue
                if time.time() - self.last_activity_time > self._INTERLOCK_TIMEOUT:
                    msg = f"5 minutes of inactivity detected. Disabling {self.__class__.__name__}"
                    print(f"[Timeout] {msg}")
                    ErrorPopupManager.report_info("Idle Timeout", msg)
                    self.disable()
                    return

        self._interlock_thread = threading.Thread(target=_watch, daemon=True)
        self._interlock_thread.start()

    def enable(self):
        """Enable the hardware. Returns False if it did not happen.

        `system_enabled` moves to True **only on a successful write**. It used
        to be set even when the write raised, because the transport swallowed
        the exception and returned normally — so the UI showed the system
        armed when nothing had reached the board, and vice versa.
        """
        if self._refuse_if_estopped("enable"):
            return False
        if not self.system_enabled:
            if self.serial_comm:
                try:
                    self.serial_comm.enable()
                except Exception as e:
                    print(f"[{self.__class__.__name__}] {e}")
                    ErrorPopupManager.report_warning("Enable Failed", str(e))
                    return False
            self.system_enabled = True
            self._clear_fault()
        self._start_interlock_watchdog()
        self.start_loops()
        return True

    def toggle_enable(self):
        if self.system_enabled:
            self.disable()
        else:
            self.enable()

    def _stop_and_disarm(self):
        self.manual_flag = False
        self.auton_flag = False
        self.is_stepping = False
        self._interlock_stop.set()
        try:
            self.send_stop_command()
        except Exception as e:
            # A zero-motion frame that failed must not prevent the hardware
            # disable below. Same isolation rule as teardown: the step that
            # de-energizes hardware is never skipped because an earlier step
            # raised.
            print(f"[{self.__class__.__name__}] Stop frame failed, continuing to disable: {e}")
        # Always send the hardware disable, regardless of our own
        # system_enabled belief: the firmware's 'd' handler is explicitly
        # idempotent (safe to resend any time) and its own system_enabled
        # flag lives on the Arduino, independent of and persisting across
        # this Python model's lifetime (e.g. across a dock close/reopen
        # that reconstructs this model with system_enabled defaulting back
        # to False). Gating this send on the Python-side flag let the two
        # go out of sync and left the stepper coils energized with no way
        # to force a disable through Full Stop.
        if self.serial_comm:
            try:
                self.serial_comm.disable()
            except Exception as e:
                # The disable did not reach the board, so the coils may still
                # be energized. Recording system_enabled = False here — which
                # is what used to happen unconditionally — would report the
                # system safe on the strength of a command that failed.
                self._enter_fault(f"disable not confirmed — coils may be energized: {e}")
                return
        self.system_enabled = False
        self._clear_fault()

    def disable(self):
        self._stop_and_disarm()

    def full_stop(self):
        self._stop_and_disarm()

    def power_down(self):
        self._stop_and_disarm()
        if self.serial_comm:
            try:
                self.serial_comm.write_command(b'k\n')
                print(f"[{self.__class__.__name__}] Sent Power Down (Kill Coils) command 'k'")
            except Exception as e:
                # The kill never reached the board. Say so loudly rather than
                # letting the caller believe the coils are dead (RC-2).
                self._enter_fault(f"power down not confirmed: {e}")

    def teardown(self):
        """Safety-first, exception-safe shutdown (RC-1, invariant I-1.2).

        Order is hardware stop -> background activity -> transport close, and
        each step is isolated. This used to stop the gamepad poller first with
        no guard, so a poller cleanup that raised skipped power_down()
        entirely: the port then closed with the coils still energized while
        Python reported the system disabled.

        Residual window: the poller can still emit one manual-mode command
        between the stop and its own shutdown. That closes in S5, when the
        control loops move into the model and stop being the view's to drive.
        """
        try:
            self.power_down()
        except Exception as e:
            print(f"[{self.__class__.__name__}] Hardware stop failed during teardown: {e}")
        try:
            self.stop_loops()
        except Exception as e:
            print(f"[{self.__class__.__name__}] Stopping loops failed during teardown: {e}")
        try:
            if self.poller:
                try:
                    self.poller.stop_polling()
                finally:
                    self.poller.close()
        except Exception as e:
            print(f"[{self.__class__.__name__}] Poller cleanup failed during teardown: {e}")
        try:
            if self.serial_comm:
                self.serial_comm.close()
        except Exception as e:
            print(f"[{self.__class__.__name__}] Transport close failed during teardown: {e}")

    def emergency_stop(self):
        """Latch FULL STOP, then stop the hardware (RC-5).

        The latch is set *first* and before any I/O: a motion command already
        in flight on the script or gamepad thread would otherwise be able to
        land after the stop returned.
        """
        self._estop.set()
        self.power_down()


class StepperProbe(BaseProbe):
    def __init__(self, port, controller_id, active_claims=None):
        super().__init__(port, controller_id, active_claims)
        self.x_step = "1"
        self.y_step = "1"
        self.z_step = "1"


class DCProbe(BaseProbe):
    def __init__(self, port, controller_id, active_claims=None):
        super().__init__(port, controller_id, active_claims)
        self.packet_format = PACKET_FORMAT
        self.x_step = "1"
        self.y_step = "1"
        self.z_step = "1"
        self.full_speed = "120"
        self.slow_speed = "0"
        self.brake_distance = "0"
        self.man_full_speed = "120"

    @property
    def ui_schema(self):
        schema = copy.deepcopy(super().ui_schema)
        # Find the Configuration section and insert the two new fields
        for section in schema["sections"]:
            if section["title"] == "Configuration":
                section["elements"].append({"type": "entry", "text": "Brake Speed (Slow):", "model_attr": "slow_speed"})
                section["elements"].append({"type": "entry", "text": "Brake Distance (steps):", "model_attr": "brake_distance"})
                break
        return schema

    def get_params(self):
        params = super().get_params()
        params["slow_speed"] = _num(self.slow_speed, 0)
        params["brake_distance"] = _num(self.brake_distance, 0)
        return params


class ChuckPositioner(BaseProbe):
    def __init__(self, port, controller_id, active_claims=None):
        super().__init__(port, controller_id, active_claims)
        self.x_step = "2"
        self.y_step = "2"
        self.z_step = "2"
