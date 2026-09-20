# Libraries
import os
# The SDL environment is configured by `controller.input_service`, which is
# imported below and is the one owner of SDL (RC-13). It was set here too,
# and in three other places, with the copies disagreeing (MANAGER-18).

try:
    import pygame
except ImportError:
    pygame = None
import time
import sys
import re
import ctypes
from ctypes import wintypes
import threading
# The bind-counting refcount lived here: close() decremented it and called
# pygame.quit() at zero, so closing one device tore SDL down under another
# that was still running. Ownership is per-owner in InputService now.
from error_routing import ErrorRouter as ErrorPopupManager
from controller.input_service import input_service


# _ensure_pygame_video() lived here. Its own docstring described it as a
# recurring patch for SDL being torn down by whichever poller happened to
# close last — the anti-fix root-causes.md names, because it made the symptom
# survivable and so removed the pressure to fix the ownership. SDL now has one
# owner (controller/input_service.py), is initialised once, and is torn down
# only at process exit, so there is nothing left to re-establish.

# Windows API structure for polling raw joystick status
if sys.platform == "win32":
    class JOYINFOEX(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("dwXpos", wintypes.DWORD),
            ("dwYpos", wintypes.DWORD),
            ("dwZpos", wintypes.DWORD),
            ("dwRpos", wintypes.DWORD),
            ("dwUpos", wintypes.DWORD),
            ("dwVpos", wintypes.DWORD),
            ("dwButtons", wintypes.DWORD),
            ("dwButtonNumber", wintypes.DWORD),
            ("dwPOV", wintypes.DWORD),
            ("dwReserved1", wintypes.DWORD),
            ("dwReserved2", wintypes.DWORD),
        ]

class BaseGamepad:
    """Base class for all physical gamepads, providing standard mappings."""
    def __init__(self, joystick):
        self.joystick = joystick
        self.prev_axis_states = {}
        self.prev_button_states = {}
        self.prev_hat_states = {}
        
        # Initialize previous state dictionaries
        for i in range(self.joystick.get_numaxes()):
            self.prev_axis_states[i] = 0.0
        for i in range(self.joystick.get_numbuttons()):
            self.prev_button_states[i] = 0
        for i in range(self.joystick.get_numhats()):
            self.prev_hat_states[i] = (0, 0)
            
    def get_mapped_state(self):
        """Must return a dict of standardized hardware-agnostic inputs."""
        return {
            "x_axisStatus": 0.0,
            "y_axisStatus": 0.0,
            "z_axisStatusL": -1.0,
            "z_axisStatusR": -1.0,
            "dpad_LR": 0,
            "dpad_UD": 0,
            "LBumper": 0,
            "RBumper": 0,
        }
        
    def update_overrides(self):
        """Allows specific controllers to override state before mapping."""
        pass


class XboxGamepad(BaseGamepad):
    def __init__(self, joystick):
        super().__init__(joystick)
        # Default trigger axes to -1.0 (idle unpressed state)
        if sys.platform.startswith("linux"):
            self.prev_axis_states[2] = -1.0  # LT
            self.prev_axis_states[5] = -1.0  # RT
        else:
            self.prev_axis_states[4] = -1.0  # LT
            self.prev_axis_states[5] = -1.0  # RT

    def get_mapped_state(self):
        # Default Xbox mappings
        # Standard layout:
        # 0: Left Stick X
        # 1/3/4: Stick Y (Linux xpad uses 4 for right stick Y, Win uses 3 or 1)
        # Left Trigger: Linux 2, Windows/macOS 4 (idle -1.0 to pressed 1.0)
        # Right Trigger: 5 (idle -1.0 to pressed 1.0)
        # Bumper L: 4, Bumper R: 5
        # D-pad: hat 0 (LR: -1=Left, +1=Right; UD: +1=Up, -1=Down)
        
        if sys.platform.startswith("linux"):
            z_left_axis = 2
            z_right_axis = 5
            y_axis = 4 if 4 in self.prev_axis_states else 1
        else:
            z_left_axis = 4
            z_right_axis = 5
            y_axis = 3 if 3 in self.prev_axis_states else 1

        return {
            "x_axisStatus": self.prev_axis_states.get(0, 0.0),
            "y_axisStatus": self.prev_axis_states.get(y_axis, 0.0),
            "z_axisStatusL": self.prev_axis_states.get(z_left_axis, -1.0),
            "z_axisStatusR": self.prev_axis_states.get(z_right_axis, -1.0),
            "dpad_LR": self.prev_hat_states.get(0, (0, 0))[0],
            "dpad_UD": self.prev_hat_states.get(0, (0, 0))[1],
            "LBumper": self.prev_button_states.get(4, 0),
            "RBumper": self.prev_button_states.get(5, 0),
        }

class BluetoothXboxGamepad(XboxGamepad):
    def __init__(self, joystick):
        super().__init__(joystick)
        if sys.platform.startswith("linux"):
            self.prev_axis_states[5] = -1.0
            self.prev_axis_states[4] = -1.0

    def get_mapped_state(self):
        # Bluetooth mappings differ on linux (ERTM / generic HID mapping)
        if sys.platform.startswith("linux"):
            return {
                "x_axisStatus": self.prev_axis_states.get(0, 0.0),
                "y_axisStatus": self.prev_axis_states.get(3, 0.0),
                "z_axisStatusL": self.prev_axis_states.get(5, -1.0),
                "z_axisStatusR": self.prev_axis_states.get(4, -1.0),
                "dpad_LR": self.prev_hat_states.get(0, (0, 0))[0],
                "dpad_UD": self.prev_hat_states.get(0, (0, 0))[1],
                "LBumper": self.prev_button_states.get(6, 0),
                "RBumper": self.prev_button_states.get(7, 0),
            }
        return super().get_mapped_state()

class LogitechF310Gamepad(BaseGamepad):
    """Handles Logitech F310 in both X (XInput) and D (DirectInput) switch modes."""
    def __init__(self, joystick):
        super().__init__(joystick)
        if not self._is_dinput_mode():
            if sys.platform.startswith("linux"):
                self.prev_axis_states[2] = -1.0
                self.prev_axis_states[5] = -1.0
            else:
                self.prev_axis_states[4] = -1.0
                self.prev_axis_states[5] = -1.0

    def _is_dinput_mode(self):
        try:
            return self.joystick.get_numaxes() <= 4 and self.joystick.get_numbuttons() >= 8
        except Exception:
            return False

    def get_mapped_state(self):
        if self._is_dinput_mode():
            # DInput mode: triggers are digital buttons 6 and 7
            lt_pressed = self.prev_button_states.get(6, 0)
            rt_pressed = self.prev_button_states.get(7, 0)
            return {
                "x_axisStatus": self.prev_axis_states.get(0, 0.0),
                "y_axisStatus": self.prev_axis_states.get(3, self.prev_axis_states.get(1, 0.0)),
                "z_axisStatusL": 1.0 if lt_pressed else -1.0,
                "z_axisStatusR": 1.0 if rt_pressed else -1.0,
                "dpad_LR": self.prev_hat_states.get(0, (0, 0))[0],
                "dpad_UD": self.prev_hat_states.get(0, (0, 0))[1],
                "LBumper": self.prev_button_states.get(4, 0),
                "RBumper": self.prev_button_states.get(5, 0),
            }
        else:
            # XInput mode behaves as standard XboxGamepad
            if sys.platform.startswith("linux"):
                z_left_axis = 2
                z_right_axis = 5
                y_axis = 4 if 4 in self.prev_axis_states else 1
            else:
                z_left_axis = 4
                z_right_axis = 5
                y_axis = 3 if 3 in self.prev_axis_states else 1

            return {
                "x_axisStatus": self.prev_axis_states.get(0, 0.0),
                "y_axisStatus": self.prev_axis_states.get(y_axis, 0.0),
                "z_axisStatusL": self.prev_axis_states.get(z_left_axis, -1.0),
                "z_axisStatusR": self.prev_axis_states.get(z_right_axis, -1.0),
                "dpad_LR": self.prev_hat_states.get(0, (0, 0))[0],
                "dpad_UD": self.prev_hat_states.get(0, (0, 0))[1],
                "LBumper": self.prev_button_states.get(4, 0),
                "RBumper": self.prev_button_states.get(5, 0),
            }

class T16000MGamepad(BaseGamepad):
    def update_overrides(self):
        # Keep original behavior: always map button 2 -> 9, button 3 -> 10
        try:
            self.prev_axis_states[9] = self.joystick.get_button(2)
            self.prev_axis_states[10] = self.joystick.get_button(3)
        except Exception:
            pass
        
    def get_mapped_state(self):
        # Button 3 (virtual axis 10) = Top-Right (Up / z_axisStatusL)
        # Button 2 (virtual axis 9) = Top-Left (Down / z_axisStatusR)
        # Unpressed button (0.0) maps to idle trigger (-1.0)
        # Pressed button (1.0) maps to active trigger (1.0)
        z_l = 10  # Button 3 (Up)
        z_r = 9   # Button 2 (Down)
        return {
            "x_axisStatus": self.prev_axis_states.get(0, 0.0),
            "y_axisStatus": self.prev_axis_states.get(1, 0.0),
            "z_axisStatusL": self.prev_axis_states.get(z_l, 0.0) * 2.0 - 1.0,  # Remap to [-1, 1] range
            "z_axisStatusR": self.prev_axis_states.get(z_r, 0.0) * 2.0 - 1.0,  # Remap to [-1, 1] range
            "dpad_LR": self.prev_hat_states.get(0, (0, 0))[0],
            "dpad_UD": self.prev_hat_states.get(0, (0, 0))[1],
            "LBumper": self.prev_button_states.get(4, self.prev_button_states.get(7, 0)),
            "RBumper": self.prev_button_states.get(5, self.prev_button_states.get(9, 0)),
        }

def get_gamepad_wrapper(joystick):
    """Factory to return the correctly mapped BaseGamepad subclass."""
    name = joystick.get_name() if hasattr(joystick, 'get_name') else ""
    name_lower = name.lower()
    
    if "t.16000m" in name_lower or "thrustmaster" in name_lower:
        return T16000MGamepad(joystick)
    if "f310" in name_lower or "dual action" in name_lower:
        return LogitechF310Gamepad(joystick)
    if "wireless" in name_lower or (sys.platform.startswith("linux") and hasattr(joystick, 'get_guid') and len(joystick.get_guid()) > 2 and joystick.get_guid()[1:2] == '5'):
        return BluetoothXboxGamepad(joystick)
    if "xbox" in name_lower or "controller" in name_lower or "x-box" in name_lower:
        return XboxGamepad(joystick)
    raise ValueError(f"Unsupported joystick detected: '{name}'. Add axis binds in gamepad.py!")

class ControllerPoller:
    
    # Poll interval in milliseconds.
    #
    # NOTE (S5, ruled D-12 on 2026-09-20): the comment here used to read "Poll
    # 50 times per second (1000ms / 20ms = 50Hz)" above a value of 5, i.e.
    # 200 Hz — four times the documented rate. The owner ruled the *code* was
    # right and the comment wrong: 5 ms stands. The poller therefore samples
    # at 4x the manual command rate, which is deliberate — it catches button
    # edges shorter than one pump tick. Do not "optimise" it to match.
    POLL_INTERVAL = 5  # ms -> ~200 Hz  (D-12: ruled, do not change)

    def __init__(self, controllerID, active_claims, process_name):
        # Polling control flag
        self.is_polling = False
        self.controllerID = controllerID
        self.controller_index = None # Stores integer ID for OS queries
        self.gui_root = None  
        self.log_updater = None
        self.activity_callback = None
        self.active_claims = active_claims
        self.process_name = process_name
        self.gamepad = None
        self._closed = False

        # Input state, latched at poll time (RC-13 item 2).
        #
        # Edges used to be computed inside get_mapped_state(), which meant the
        # *reader* detected them and consumed them by updating the latch. Two
        # consequences: whichever caller read first swallowed the edge for
        # everyone else, and a button tap shorter than the gap between reads
        # was never seen at all. Edges are now latched by the poll loop and
        # held until a single consumer drains them.
        self._init_input_state()
        self._thread = None

        self._initialize_pygame_joystick(controllerID)

    def _init_input_state(self):
        """Set up the latched-input fields.

        Separate from __init__ so a poller assembled piecemeal — as several
        tests do, to avoid touching real hardware — can set them up without
        duplicating the field list.
        """
        self._state_lock = threading.RLock()
        self._levels = {}
        self._pending_edges = {}
        self._latch_state = {}

    def _is_os_connected(self):
        """OS-level and Pygame-level check for controller connection."""
        if self.controller_index is None:
            return False
        
        # SDL presence check, through SDL's one owner and under its lock.
        # Behaviour is unchanged: SDL gets to say "gone", and where it is not
        # up the OS-level checks below answer alone. What changed is that the
        # four `pygame.joystick.get_count()` reads this replaces ran on the
        # poller's own thread, outside the lock, against an API that is not
        # thread-safe and that every other poller was reading at the same
        # time (RC-13).
        if input_service.initialised and \
                not input_service.is_index_connected(self.controller_index):
            return False

        if sys.platform.startswith("linux"):
            return True
        elif sys.platform == "win32":
            try:
                info = JOYINFOEX()
                info.dwSize = ctypes.sizeof(JOYINFOEX)
                info.dwFlags = 255
                if ctypes.windll.winmm.joyGetPosEx(self.controller_index, ctypes.byref(info)) == 0:
                    return True
            except Exception:
                pass
            return True
        elif sys.platform == "darwin":
            if getattr(self, 'gamepad', None) and getattr(self.gamepad, 'joystick', None):
                try:
                    self.gamepad.joystick.get_name()
                    return True
                except Exception:
                    return False
            return True
        return True

    def _handle_disconnect(self):
        msg = "[controllerDrive] Controller disconnected."
        print(msg)
        ErrorPopupManager.report_warning("Controller Disconnected", msg)
        self.gamepad = None
        self.active_claims[self.process_name] = "None Detected"
        with self._state_lock:
            self._latch_state.clear()
            self._pending_edges.clear()
            self._levels = {}
        self.stop_polling()
    
    def get_physical_controllers(self):
        try:
            return input_service.names()
        except Exception as e:
            ErrorPopupManager.report_error("Controller Scan Error", f"[{self.process_name}] Error scanning physical controllers:\n{e}", e)
            return []

    def set_controller(self, controllerID):
        """Reassigns this poller to a different physical controller, tearing down any existing connection first."""
        self.controllerID = controllerID
        success = self._initialize_pygame_joystick(controllerID)
        if success and self.gui_root and not self.is_polling:
            self.start_polling(self.gui_root, self.log_updater, self.activity_callback)
        return success

    # connect_controller() lived here: no callers in src (GAMEPAD-17,
    # grep-verified against this commit), and it called pygame.quit()
    # unconditionally — a process-wide teardown that would have killed every
    # other live poller's joystick handle at the same time (RC-13).
    # set_controller() above is the live duplicate: same re-initialization
    # path, without the SDL-wide teardown.

    def _initialize_pygame_joystick(self, controllerID):
        self.stop_polling()

        if controllerID is None or "None" in str(controllerID) or "Virtual" in str(controllerID) or str(controllerID) == "N/A":
            print(f"[{self.process_name}] Joystick set to None.")
            self.gamepad = None
            self.active_claims[self.process_name] = "None Detected"
            return False

        # Extract numeric controller index (handles int, multi-digit indices, Joy X, ID X)
        if isinstance(controllerID, int):
            controller_number = controllerID
        else:
            match = re.search(r'(?:Joy|ID)?\s*(\d+)', str(controllerID), re.IGNORECASE)
            if match:
                controller_number = int(match.group(1))
            else:
                try:
                    controller_number = int(str(controllerID)[3:4])
                except (ValueError, IndexError):
                    controller_number = None

        if controller_number is None:
            print(f"[{self.process_name}] Invalid controller ID: {controllerID}")
            self.gamepad = None
            self.active_claims[self.process_name] = "None Detected"
            return False

        # Multi-controller claim check: verify this controller index isn't claimed by another subsystem
        if self.active_claims:
            for proc, claimed_id in list(self.active_claims.items()):
                if proc != self.process_name and claimed_id is not None and "None" not in str(claimed_id) and "Virtual" not in str(claimed_id) and str(claimed_id) != "N/A":
                    # Compare parsed numeric indices
                    if isinstance(claimed_id, int):
                        claimed_num = claimed_id
                    else:
                        m = re.search(r'(?:Joy|ID)?\s*(\d+)', str(claimed_id), re.IGNORECASE)
                        claimed_num = int(m.group(1)) if m else None

                    if claimed_num is not None and claimed_num == controller_number:
                        msg = f"[{self.process_name}] Controller collision: {controllerID} is already claimed by {proc}."
                        print(msg)
                        ErrorPopupManager.report_warning("Controller Claim Conflict", msg)
                        self.gamepad = None
                        self.active_claims[self.process_name] = "None Detected"
                        return False

        try:
            input_service.ensure_init()

            try:
                self.controller_index = controller_number

                if not self._is_os_connected():
                    raise RuntimeError("Device not physically present at OS level.")

                joystick = input_service.acquire(self.process_name, controller_number)
                if joystick is None:
                    raise RuntimeError(
                        f"Controller {controller_number} is unavailable or already claimed.")

                self.active_claims[self.process_name] = controllerID
                print(f"\n[controllerDrive] Attempting to initialize joystick: {joystick.get_name()}")
                
                # Setup polymorphic wrapper
                self.gamepad = get_gamepad_wrapper(joystick)
                with self._state_lock:
                    self._latch_state.clear()
                    self._pending_edges.clear()
                print(f"[controllerDrive] Joystick initialization successful: {joystick.get_name()}")
                return True
            except Exception as e:
                msg = f"[controllerDrive] No joystick found ({e})."
                print(msg)
                ErrorPopupManager.report_warning("Joystick Not Found", msg, e)
                self._handle_disconnect()
                return False
                
        except Exception as e:
            ErrorPopupManager.report_error("Pygame Init Error", f"[controllerDrive] Error initializing pygame:\n{e}", e)
            return False

    def change_controller(self, new_controller_id):
        print(f"[{self.process_name}] Hot-swapping to: {new_controller_id}")
        self.controllerID = new_controller_id
        success = self._initialize_pygame_joystick(new_controller_id)
        if success and self.gui_root and not self.is_polling:
            self.start_polling(self.gui_root, self.log_updater, self.activity_callback)
        return success

    def start_polling(self, gui=None, log_updater=None, activity_callback=None):
        if gui is not None:
            self.gui_root = gui
        if log_updater is not None:
            self.log_updater = log_updater
        if activity_callback is not None:
            self.activity_callback = activity_callback

        if self.is_polling: return
        if not self.gamepad: return

        print("[controllerDrive] Starting controller polling...")
        self.is_polling = True

        if self.gui_root is not None and hasattr(self.gui_root, "after"):
            # Legacy path: driven by the Tk event loop.
            self._poll_loop()
        else:
            # The poller owns its own clock (RC-13 item 2). Without this the
            # loop ran exactly once and then called stop_polling(), because
            # rescheduling depended on a Tk widget's `after`. That is why the
            # Web frontend has no manual mode at all: no Tk root, no polling,
            # so entering manual mode energized the coils and then did
            # nothing else.
            self._thread = threading.Thread(
                target=self._poll_forever, daemon=True,
                name=f"poller-{self.process_name}")
            self._thread.start()

    def _poll_forever(self):
        while self.is_polling and not self._closed:
            self._poll_loop()
            time.sleep(self.POLL_INTERVAL / 1000.0)

    def stop_polling(self):
        if self.is_polling:
            self.is_polling = False

    def close(self):
        """Release this poller's device handle. SDL stays up.

        The old version decremented a process-wide poller count and called
        pygame.quit() when it reached zero — so closing one device could tear
        SDL down under another poller that was still running, and the next
        reconnect had to resurrect it. Process-wide teardown belongs to
        lifecycle.shutdown() at exit, and nowhere else (RC-13).
        """
        if self._closed:
            return
        self._closed = True
        self.stop_polling()
        input_service.release(self.process_name)

    EDGE_KEYS = ("dpad_LR", "dpad_UD", "LBumper", "RBumper")

    def _read_raw(self):
        """Raw mapped state from the wrapper, or None if the device is gone.

        Every wrapper's get_mapped_state() (BaseGamepad and its subclasses,
        above) only does dict lookups against its own prev_*_states caches —
        no pygame calls — so this except clause is unreachable today
        (GAMEPAD-17). Kept as a guard for a future wrapper that does touch
        hardware directly, but scoped so the guard itself cannot crash: if
        pygame failed to import, `except pygame.error` used to evaluate
        `None.error` the moment anything else in the try block raised,
        replacing that exception with an unrelated AttributeError instead of
        just letting it propagate.
        """
        if not self.gamepad:
            return None
        try:
            return self.gamepad.get_mapped_state()
        except (pygame.error if pygame else ()) as e:
            ErrorPopupManager.report_error(
                "Gamepad Disconnected", f"Hardware error during poll:\n{e}")
            self.gamepad = None
            self.is_polling = False
            return None

    @staticmethod
    def _apply_deadzones(state):
        for k in ("x_axisStatus", "y_axisStatus"):
            if abs(state.get(k, 0.0)) < 0.12:
                state[k] = 0.0
        for k in ("z_axisStatusL", "z_axisStatusR"):
            if state.get(k, 0.0) < -0.9:
                state[k] = -1.0
        return state

    def _capture_state(self):
        """Latch one tick of input. Called by the poll loop, never by a reader.

        Edges are detected *here*, at poll time, and accumulate until drained.
        They used to be detected inside get_mapped_state(), so the first
        reader consumed the edge for every other reader, and a tap shorter
        than the gap between reads was never seen at all.
        """
        raw = self._read_raw()
        if raw is None:
            return
        levels = self._apply_deadzones(dict(raw))
        with self._state_lock:
            for key in self.EDGE_KEYS:
                current = raw.get(key, 0)
                if current != 0 and current != self._latch_state.get(key, 0):
                    self._pending_edges[key] = current
                self._latch_state[key] = current
                levels[key] = 0  # levels never carry edges
            self._levels = levels

    def poll_once(self):
        """Run exactly one poll tick. The unit of the loop, exposed for tests."""
        self._poll_loop()

    def read_levels(self):
        """Current continuous input (axes, triggers). Non-consuming.

        Any number of readers may call this; it changes nothing.
        """
        with self._state_lock:
            return dict(self._levels)

    def drain_edges(self):
        """Pending discrete presses since the last drain. Single consumer.

        Returns them and clears them, so exactly one caller acts on each
        press. That caller is the model's input pump.
        """
        with self._state_lock:
            edges, self._pending_edges = self._pending_edges, {}
            return edges

    def get_mapped_state(self):
        """Levels plus any pending edges — the shape callers already expect.

        Kept so existing call sites keep working while RC-4 moves the input
        pump into the models. It *drains*, so it is still a single-consumer
        read; the difference is that the edge was latched when it happened
        rather than when someone got round to looking.
        """
        if not self.gamepad or not self.is_polling:
            return {}
        thread = self._thread
        if thread is None or not thread.is_alive():
            # Nothing is driving captures on our behalf — the Tk path is
            # clocked by a widget, and some callers poll by reading. Sample
            # now so the answer reflects the hardware, not the last tick.
            # Capturing twice for one physical state produces no extra edge:
            # _capture_state compares against the latch.
            self._capture_state()
        result = self.read_levels()
        if not result:
            return {}
        edges = self.drain_edges()
        for key in self.EDGE_KEYS:
            result[key] = edges.get(key, 0)
        return result

    def flush_neutral(self):
        """Reset the controller state to neutral, typically when focus is lost."""
        if self.gamepad is None:
            return
        
        # Clear axis state caches so get_mapped_state reads neutral 0
        for k in self.gamepad.prev_axis_states.keys():
            # Triggers (typically axes 2, 4, 5 depending on OS) idle at -1.0
            if k in (2, 4, 5): 
                self.gamepad.prev_axis_states[k] = -1.0
            else:
                self.gamepad.prev_axis_states[k] = 0.0
            
        with self._state_lock:
            self._latch_state.clear()
            self._pending_edges.clear()
            self._levels = {}

    def _poll_loop(self):
        if not self.is_polling: return
        
        def _log(message):
            if self.log_updater: self.log_updater(message)
            else: print(f"[controllerDrive] {message}")

        if not self._is_os_connected():
            self._handle_disconnect()
            return
        
        try:
            # One tick reads many joystick values, and each poller now has its
            # own thread (RC-13 item 2). pygame's joystick API is not
            # thread-safe, so the whole tick is taken under the SDL lock
            # rather than leaving two threads to interleave inside it.
            with input_service.lock():
                if pygame:
                    pygame.event.pump()
                    pygame.event.get()

                if not self.gamepad or not self.gamepad.joystick:
                    self._handle_disconnect()
                    return

                self._read_hardware_changes(_log)

        except Exception as e:
            msg = f"[controllerDrive] Pygame error during polling:\n{e}"
            print(msg)
            ErrorPopupManager.report_error("Gamepad Polling Error", msg, e)
            self._handle_disconnect()
            return

        # Latch this tick's input so a consumer sees every edge (RC-13 item 2).
        self._capture_state()

        if self.gui_root is not None and hasattr(self.gui_root, "after"):
            try:
                self.gui_root.after(self.POLL_INTERVAL, self._poll_loop)
            except Exception as e:
                # gui_root can be destroyed between the is_polling check at
                # the top of this method and this call (dashboard/tab torn
                # down mid-poll) — Tk raises TclError. This used to sit
                # outside the try/except above and propagate straight out of
                # the scheduled callback (GAMEPAD-17). Treat it like any
                # other lost-device signal instead.
                msg = f"[controllerDrive] Poll re-arm failed (widget destroyed?):\n{e}"
                print(msg)
                self._handle_disconnect()
        # Otherwise _poll_forever owns the cadence. This used to call
        # stop_polling() here, which is what limited polling to frontends that
        # happen to have a Tk event loop.

    def _read_hardware_changes(self, _log):
        """Log and report activity for anything that moved since the last tick.

        Caller holds the SDL lock.
        """
        # Check Axes
        for i in range(self.gamepad.joystick.get_numaxes()): # type: ignore
            current_val = self.gamepad.joystick.get_axis(i)  # type: ignore
            
            if abs(current_val) < 0.1: 
                current_val = 0.0
            
            prev_val = self.gamepad.prev_axis_states.get(i, 0.0)
            if round(current_val, 2) != round(prev_val, 2):
                _log(f"Axis {i} changed: {current_val:.2f}")

                is_hard_snap = (abs(current_val) >= 1.0) and (abs(current_val - prev_val) > 0.5)
                if not is_hard_snap and self.activity_callback:
                    self.activity_callback()

            self.gamepad.prev_axis_states[i] = current_val
        
        # Apply controller-specific overrides (like T16000M buttons mapped as axes)
        self.gamepad.update_overrides()

        # Check Buttons
        for i in range(self.gamepad.joystick.get_numbuttons()): # type: ignore
            current_val = self.gamepad.joystick.get_button(i)   # type: ignore
            if current_val != self.gamepad.prev_button_states.get(i, 0):
                _log(f"Button {i} {'pressed' if current_val else 'released'}")
                if self.activity_callback:
                    self.activity_callback()
                self.gamepad.prev_button_states[i] = current_val

        # Check Hats (DPad)
        for i in range(self.gamepad.joystick.get_numhats()):   # type: ignore
            current_val = self.gamepad.joystick.get_hat(i)     # type: ignore
            if current_val != self.gamepad.prev_hat_states.get(i, (0, 0)):
                _log(f"Hat {i} (DPad) changed: {current_val}")
                if self.activity_callback:
                    self.activity_callback()
                self.gamepad.prev_hat_states[i] = current_val

