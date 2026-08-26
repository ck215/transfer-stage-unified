# Libraries
try:
    import pygame
except ImportError:
    pygame = None
import time
import os
import sys
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
    def get_mapped_state(self):
        # Default Xbox mappings
        # Win32 vs Linux axis indexes differ slightly, but we will assume standard layout here
        # or use the provided binds.
        
        # Standard:
        # 0: Left Stick X
        # 1: Left Stick Y
        # 4: Left Trigger (Linux: 2)
        # 5: Right Trigger (Linux: 5)
        # Bumper L: 4, Bumper R: 5
        
        if sys.platform.startswith("linux"):
            z_left_axis = 2
            z_right_axis = 5
            y_axis = 4
        else:
            z_left_axis = 4
            z_right_axis = 5
            y_axis = 3

        return {
            "x_axisStatus": self.prev_axis_states.get(0, 0.0),
            "y_axisStatus": self.prev_axis_states.get(y_axis, 0.0),
            "z_axisStatusL": self.prev_axis_states.get(z_left_axis, -1.0),
            "z_axisStatusR": self.prev_axis_states.get(z_right_axis, -1.0),
            "dpad_LR": -self.prev_hat_states.get(0, (0,0))[0],
            "dpad_UD": -self.prev_hat_states.get(0, (0,0))[1],
            "LBumper": self.prev_button_states.get(4, 0),
            "RBumper": self.prev_button_states.get(5, 0),
        }

class BluetoothXboxGamepad(XboxGamepad):
    def get_mapped_state(self):
        # Bluetooth mappings differ on linux
        if sys.platform.startswith("linux"):
            return {
                "x_axisStatus": self.prev_axis_states.get(0, 0.0),
                "y_axisStatus": self.prev_axis_states.get(3, 0.0),
                "z_axisStatusL": self.prev_axis_states.get(5, -1.0),
                "z_axisStatusR": self.prev_axis_states.get(4, -1.0),
                "dpad_LR": self.prev_hat_states.get(0, (0,0))[0],
                "dpad_UD": self.prev_hat_states.get(0, (0,0))[1],
                "LBumper": self.prev_button_states.get(6, 0),
                "RBumper": self.prev_button_states.get(7, 0),
            }
        return super().get_mapped_state()

class T16000MGamepad(BaseGamepad):
    def update_overrides(self):
        # Keep original behavior: always map button 2 -> 9, button 3 -> 10
        self.prev_axis_states[9] = self.joystick.get_button(2)
        self.prev_axis_states[10] = self.joystick.get_button(3)
        
    def get_mapped_state(self):
        if sys.platform.startswith("linux"):
            z_l = 9; z_r = 10
        else:
            z_l = 10; z_r = 9
        return {
            "x_axisStatus": self.prev_axis_states.get(0, 0.0),
            "y_axisStatus": self.prev_axis_states.get(1, 0.0),
            "z_axisStatusL": self.prev_axis_states.get(z_l, 0.0) * 2.0 - 1.0, # Remap to [-1, 1] range
            "z_axisStatusR": self.prev_axis_states.get(z_r, 0.0) * 2.0 - 1.0, # Remap to [-1, 1] range
            "dpad_LR": self.prev_hat_states.get(0, (0,0))[0],
            "dpad_UD": self.prev_hat_states.get(0, (0,0))[1],
            "LBumper": self.prev_button_states.get(7, 0),
            "RBumper": self.prev_button_states.get(9, 0),
        }

def get_gamepad_wrapper(joystick):
    """Factory to return the correctly mapped BaseGamepad subclass."""
    name = joystick.get_name()
    if name.startswith("Controller"):
        return XboxGamepad(joystick)
    if name == "Xbox Series X Controller":
        if sys.platform.startswith("linux") and joystick.get_guid()[1:2] == '5':
            return BluetoothXboxGamepad(joystick)
        return XboxGamepad(joystick)
    if name == "T.16000M" or name == "Thrustmaster T.16000M":
        return T16000MGamepad(joystick)
    if name == "Logitech Gamepad F310":
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

    def _is_os_connected(self): # OS check for controller
        if self.controller_index is None:
            return False
        if sys.platform.startswith("linux"):
            return os.path.exists(f"/dev/input/js{self.controller_index}")
        elif sys.platform == "win32":
            info = JOYINFOEX()
            info.dwSize = ctypes.sizeof(JOYINFOEX)
            info.dwFlags = 255
            return ctypes.windll.winmm.joyGetPosEx(self.controller_index, ctypes.byref(info)) == 0
        elif sys.platform == "darwin":
            if not getattr(self, 'gamepad', None) or not getattr(self.gamepad, 'joystick', None):
                return False
            try:
                self.gamepad.joystick.get_name()
                return True
            except:
                return False
        return True

    def _handle_disconnect(self):
        msg = "[controllerDrive] Controller disconnected."
        ErrorPopupManager.report_warning("Controller Disconnected", msg)
        self.gamepad = None
        self.active_claims[self.process_name] = "None Detected"
        self.stop_polling()
    
    def get_physical_controllers(self):
        try: pygame.init()
        except: pass
        try:
            if not pygame.joystick.get_init():
                pygame.joystick.init()
            pygame.event.pump()
            hardware_controllers = []
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
        try: pygame.quit()
        except: pass
        self._initialize_pygame_joystick(self.controllerID)
        return True if self.gamepad else False

    def _initialize_pygame_joystick(self, controllerID):
        self.stop_polling()

        if not controllerID or "None" in controllerID or "Virtual" in controllerID:
            print(f"[{self.process_name}] Joystick set to None.")
            self.gamepad = None
            self.active_claims[self.process_name] = "None Detected"
            return False

        try:
            pygame.init()
            pygame.joystick.init()
        
            try:
                controller_number = int(controllerID[3:4])
                self.controller_index = controller_number

                if not self._is_os_connected():
                    raise RuntimeError("Device not physically present at OS level.")

                joystick = pygame.joystick.Joystick(controller_number)
                joystick.init()

                self.active_claims[self.process_name] = controllerID
                print(f"\n[controllerDrive] Attempting to initialize joystick: {joystick.get_name()}")
                
                # Setup polymorphic wrapper
                self.gamepad = get_gamepad_wrapper(joystick)
                print(f"[controllerDrive] Joystick initialization succesful: {joystick.get_name()}")
                return True
            except:
                msg = "[controllerDrive] No joystick found."
                print(msg)
                ErrorPopupManager.report_warning("Joystick Not Found", msg)
                self._handle_disconnect()
                self.active_claims[self.process_name] = "None"
                pygame.quit()
                return False
                
        except Exception as e:
            ErrorPopupManager.report_error("Pygame Init Error", f"[controllerDrive] Error initializing pygame:\n{e}", e)
            return False

    def change_controller(self, new_controller_id):
        print(f"[{self.process_name}] Hot-swapping to: {new_controller_id}")
        try: pygame.quit()
        except: pass
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
        pygame.joystick.quit()
        pygame.quit()

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
            pygame.event.get() 
            
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

        except pygame.error as e:
            msg = f"[controllerDrive] Pygame error during polling:\n{e}"
            print(msg)
            ErrorPopupManager.report_error("Gamepad Polling Error", msg, e)
            self._handle_disconnect()
            return
        
        if self.gui_root:
            self.gui_root.after(self.POLL_INTERVAL, self._poll_loop)
        else:
            self.stop_polling()