# Libraries
try:
    import pygame
except ImportError:
    pygame = None
import time
import os
import sys
import re
import ctypes
from ctypes import wintypes
from error_routing import ErrorRouter as ErrorPopupManager

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
    # Default fallback
    return XboxGamepad(joystick)

class ControllerPoller:
    
    # Poll 50 times per second (1000ms / 20ms = 50Hz)
    POLL_INTERVAL = 5

    def __init__(self, controllerID, active_claims, process_name):
        # Polling control flag
        self.is_polling = False
        self.controllerID = controllerID
        self.controller_index = None # Stores integer ID for OS queries
        self.gui_root = None  
        self.active_claims = active_claims
        self.process_name = process_name
        self.gamepad = None

        self._initialize_pygame_joystick(controllerID)

    def _is_os_connected(self):
        """OS-level and Pygame-level check for controller connection."""
        if self.controller_index is None:
            return False
        
        # Pygame joystick count / presence check
        try:
            if pygame and pygame.joystick.get_init():
                if self.controller_index >= pygame.joystick.get_count():
                    return False
        except Exception:
            pass

        if sys.platform.startswith("linux"):
            js_path = f"/dev/input/js{self.controller_index}"
            if os.path.exists(js_path):
                return True
            if pygame and pygame.joystick.get_init():
                return self.controller_index < pygame.joystick.get_count()
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
            if pygame and pygame.joystick.get_init():
                return self.controller_index < pygame.joystick.get_count()
            return True
        elif sys.platform == "darwin":
            if getattr(self, 'gamepad', None) and getattr(self.gamepad, 'joystick', None):
                try:
                    self.gamepad.joystick.get_name()
                    return True
                except Exception:
                    return False
            if pygame and pygame.joystick.get_init():
                return self.controller_index < pygame.joystick.get_count()
            return True
        return True

    def _handle_disconnect(self):
        msg = "[controllerDrive] Controller disconnected."
        print(msg)
        ErrorPopupManager.report_warning("Controller Disconnected", msg)
        self.gamepad = None
        self.active_claims[self.process_name] = "None Detected"
        if hasattr(self, '_latch_state'):
            self._latch_state.clear()
        self.stop_polling()
    
    def get_physical_controllers(self):
        try:
            if pygame:
                pygame.init()
        except Exception:
            pass
        try:
            if pygame and not pygame.joystick.get_init():
                pygame.joystick.init()
            if pygame:
                pygame.event.pump()
            hardware_controllers = []
            if pygame:
                for i in range(pygame.joystick.get_count()):
                    try:
                        js = pygame.joystick.Joystick(i)
                        hardware_controllers.append(f"ID {i}: {js.get_name()}")
                    except Exception:
                        pass
            return hardware_controllers
        except Exception as e:
            ErrorPopupManager.report_error("Controller Scan Error", f"[{self.process_name}] Error scanning physical controllers:\n{e}", e)
            return []

    def connect_controller(self):
        print("[controllerDrive] Restarting pygame...")
        try:
            if pygame:
                pygame.quit()
        except Exception:
            pass
        self._initialize_pygame_joystick(self.controllerID)
        return True if self.gamepad else False

    def _initialize_pygame_joystick(self, controllerID):
        self.stop_polling()

        if not controllerID or "None" in str(controllerID) or "Virtual" in str(controllerID):
            print(f"[{self.process_name}] Joystick set to None.")
            self.gamepad = None
            self.active_claims[self.process_name] = "None Detected"
            return False

        # Multi-controller claim check: verify this controller isn't claimed by another subsystem
        if self.active_claims:
            for proc, claimed_id in self.active_claims.items():
                if proc != self.process_name and claimed_id == controllerID and "None" not in claimed_id and "Virtual" not in claimed_id:
                    msg = f"[{self.process_name}] Controller collision: {controllerID} is already claimed by {proc}."
                    print(msg)
                    ErrorPopupManager.report_warning("Controller Claim Conflict", msg)
                    self.gamepad = None
                    self.active_claims[self.process_name] = "None Detected"
                    return False

        try:
            if pygame:
                pygame.init()
                pygame.joystick.init()
        
            try:
                if isinstance(controllerID, int):
                    controller_number = controllerID
                else:
                    # Extract numeric controller index (handles multi-digit indices, Joy X, ID X)
                    match = re.search(r'(?:Joy|ID)?\s*(\d+)', str(controllerID), re.IGNORECASE)
                    if match:
                        controller_number = int(match.group(1))
                    else:
                        controller_number = int(str(controllerID)[3:4])
                self.controller_index = controller_number

                if not self._is_os_connected():
                    raise RuntimeError("Device not physically present at OS level.")

                if not pygame:
                    raise RuntimeError("Pygame module not available.")

                joystick = pygame.joystick.Joystick(controller_number)
                joystick.init()

                self.active_claims[self.process_name] = controllerID
                print(f"\n[controllerDrive] Attempting to initialize joystick: {joystick.get_name()}")
                
                # Setup polymorphic wrapper
                self.gamepad = get_gamepad_wrapper(joystick)
                if hasattr(self, '_latch_state'):
                    self._latch_state.clear()
                print(f"[controllerDrive] Joystick initialization successful: {joystick.get_name()}")
                return True
            except Exception as e:
                msg = f"[controllerDrive] No joystick found ({e})."
                print(msg)
                ErrorPopupManager.report_warning("Joystick Not Found", msg, e)
                self._handle_disconnect()
                if pygame:
                    try:
                        pygame.quit()
                    except Exception:
                        pass
                return False
                
        except Exception as e:
            ErrorPopupManager.report_error("Pygame Init Error", f"[controllerDrive] Error initializing pygame:\n{e}", e)
            return False

    def change_controller(self, new_controller_id):
        print(f"[{self.process_name}] Hot-swapping to: {new_controller_id}")
        try:
            if pygame:
                pygame.quit()
        except Exception:
            pass
        self.controllerID = new_controller_id
        return self._initialize_pygame_joystick(new_controller_id)

    def start_polling(self, gui, log_updater, activity_callback=None):
        if self.is_polling: return
        if not self.gamepad: return

        print("[controllerDrive] Starting controller polling...")
        self.is_polling = True
        self.gui_root = gui 
        self.log_updater = log_updater
        self.activity_callback = activity_callback

        self._poll_loop() 

    def stop_polling(self):
        if self.is_polling:
            self.is_polling = False

    def close(self):
        if pygame:
            try:
                pygame.joystick.quit()
                pygame.quit()
            except Exception:
                pass

    def get_mapped_state(self):
        """Returns the universally mapped input dictionary from the current gamepad."""
        if not self.gamepad:
            return {}
            
        raw = self.gamepad.get_mapped_state()
        result = dict(raw)
        
        if not hasattr(self, '_latch_state'):
            self._latch_state = {}
            
        for key in ["dpad_LR", "dpad_UD", "LBumper", "RBumper"]:
            current_val = raw.get(key, 0)
            last_val = self._latch_state.get(key, 0)
            
            if current_val != 0:
                if current_val != last_val:
                    result[key] = current_val
                else:
                    result[key] = 0
            else:
                result[key] = 0
                
            self._latch_state[key] = current_val
            
        return result

    def _poll_loop(self):
        if not self.is_polling: return
        
        def _log(message):
            if self.log_updater: self.log_updater(message)
            else: print(f"[controllerDrive] {message}")

        if not self._is_os_connected():
            self._handle_disconnect()
            return
        
        try:
            if pygame:
                pygame.event.get() 
            
            if not self.gamepad or not self.gamepad.joystick:
                self._handle_disconnect()
                return

            # Check Axes
            for i in range(self.gamepad.joystick.get_numaxes()): # type: ignore
                current_val = self.gamepad.joystick.get_axis(i)  # type: ignore
                
                if abs(current_val) < 0.1: 
                    current_val = 0.0
                
                if round(current_val, 2) != round(self.gamepad.prev_axis_states.get(i, 0.0), 2):
                    _log(f"Axis {i} changed: {current_val:.2f}")

                    prev_val = self.gamepad.prev_axis_states.get(i, 0.0)
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

        except Exception as e:
            msg = f"[controllerDrive] Pygame error during polling:\n{e}"
            print(msg)
            ErrorPopupManager.report_error("Gamepad Polling Error", msg, e)
            self._handle_disconnect()
            return
        
        if self.gui_root and hasattr(self.gui_root, 'after'):
            self.gui_root.after(self.POLL_INTERVAL, self._poll_loop)
        else:
            self.stop_polling()