# Libraries
import pygame
import time
import os
import sys
import ctypes
from ctypes import wintypes

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

DEADZONE = 0.1

class ControllerPoller:
    
    # Poll 50 times per second (1000ms / 20ms = 50Hz)
    POLL_INTERVAL = 5

    controller_binds = []

    def __init__(self, controllerID, active_claims, process_name):
        # Polling control flag
        self.is_polling = False

        self.controllerID = controllerID
        self.controller_index = None # Stores integer ID for OS queries

        # Will store the reference to the tkinter root window
        self.gui_root = None  

        # Dictionaries to store the previous state
        self.prev_axis_states = {}
        self.prev_button_states = {}
        self.prev_hat_states = {}

        self.active_claims = active_claims
        self.process_name = process_name
        self.joystick = None

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

        return True

    def _handle_disconnect(self):
        print(f"[controllerDrive] Controller disconnected.")
        self.joystick = None
        self.active_claims[self.process_name] = "None Detected"
        self.prev_axis_states.clear()
        self.prev_button_states.clear()
        self.prev_hat_states.clear()
        self.stop_polling()
    
    def get_physical_controllers(self):
        try: pygame.init()
        except: pass
        try:
            # Ensure the joystick module is alive before scanning
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
            print(f"[{self.process_name}] Error scanning physical controllers: {e}")
            return []

    def connect_controller(self):
        # Restart Pygame to attempt reconnection
        print("[controllerDrive] Restarting pygame...")
        try: pygame.quit()
        except: pass
        self._initialize_pygame_joystick(self.controllerID)
        return True if self.joystick else False

    # Initalizes Pygame instance, ONCE PER APPLICATION START
    def _initialize_pygame_joystick(self, controllerID):
        self.stop_polling()

        if not controllerID or "None" in controllerID or "Virtual" in controllerID:
            print(f"[{self.process_name}] Joystick set to None.")
            self.joystick = None
            self.active_claims[self.process_name] = "None Detected"
            return False

        try:
            pygame.init()
            pygame.joystick.init()
        
            try:
                # Initialize the chosen joystick
                controller_number = int(controllerID[3:4])
                self.controller_index = controller_number

                # Check if OS is connected
                if not self._is_os_connected():
                    raise RuntimeError("Device not physically present at OS level.")

                self.joystick = pygame.joystick.Joystick(controller_number)
                self.joystick.init()

                self.active_claims[self.process_name] = controllerID

                print(f"\n[controllerDrive] Attempting to initialize joystick: {self.joystick.get_name()}")
                print(f"  Axes: {self.joystick.get_numaxes()}")
                print(f"  Buttons: {self.joystick.get_numbuttons()}")
                print(f"  Hats: {self.joystick.get_numhats()}")

                # BINDS FORMAT: X, Y, Z+, Z-, Z+step, Z-step
                if sys.platform.startswith("linux"):
                    STANDARD_CONTROLLER_BINDS = [0,4,5,2,4,5]
                elif sys.platform.startswith("win32"):
                    STANDARD_CONTROLLER_BINDS = [0,3,5,4,4,5]
                
                if (sys.platform.startswith("win32")) and self.joystick.get_name().startswith("Controller"):
                    self.controller_binds = STANDARD_CONTROLLER_BINDS
                else:
                    match self.joystick.get_name():
                        case "Xbox Series X Controller":
                            if sys.platform.startswith("win32"):
                                self.controller_binds = STANDARD_CONTROLLER_BINDS
                            elif sys.platform.startswith("linux"):
                                print(self.joystick.get_guid())
                                match self.joystick.get_guid()[1:2]: # DETECT USB VS BLUETOOTH
                                    case '3': # USB
                                        self.controller_binds = STANDARD_CONTROLLER_BINDS
                                    case '5': # BLUETOOTH
                                        self.controller_binds = [0,3,4,5,6,7]
                                    case _:
                                        raise ValueError("Error connecting Xbox Controller: Connection bus not recognized!")
                        case "T.16000M": 
                            self.controller_binds = [0,1,9,10,7,9] # WINDOWS name; 9 and 10 will be buttons simulated to be axes
                            DEADZONE = 0.03
                        case "Thrustmaster T.16000M":
                            self.controller_binds = [0,1,10,9,7,9] # MINT name; 2 and 3 will be buttons simulated to be axes
                            DEADZONE = 0.03
                        case "Logitech Gamepad F310": self.controller_binds = STANDARD_CONTROLLER_BINDS
                        case _: raise ValueError("Unsupported joystick detected! Add axis binds in controllerDrive.py!")

                self.prev_axis_states.clear()
                self.prev_button_states.clear()
                self.prev_hat_states.clear()

                # Initialize previous state dictionaries
                for i in range(self.joystick.get_numaxes()):
                    self.prev_axis_states[i] = 0.0
                    print("axes")
                for i in range(self.joystick.get_numbuttons()):
                    self.prev_button_states[i] = 0
                    print("buttons")
                for i in range(self.joystick.get_numhats()):
                    self.prev_hat_states[i] = (0, 0)
                    print("hats")

                print(f"[controllerDrive] Joystick initialization succesful: {self.joystick.get_name()}")
                return True
            except:
                print("[controllerDrive] No joystick found.")
                self._handle_disconnect()
                self.active_claims[self.process_name] = "None"
                pygame.quit()
                return False
                
        except Exception as e:
            print(f"[controllerDrive] Error initializing pygame: {e}")
            return False

    def change_controller(self, new_controller_id):
        print(f"[{self.process_name}] Hot-swapping to: {new_controller_id}")
        try: pygame.quit()
        except: pass
        return self._initialize_pygame_joystick(new_controller_id)

    def start_polling(self, gui, log_updater, activity_callback=None):
        
        # Do nothing if already polling
        if self.is_polling:
            print("[controllerDrive] Went to enable controller polling, but it is already active.")
            return

        # Do nothing if joystick is not initialized 
        if not self.joystick:
            print("[controllerDrive] Cannot start polling: Joystick not initialized. Please connect a controller.")
            return

        # Start polling loop by setting flag and passing root, log windows
        print("[controllerDrive] Starting controller polling...")
        self.is_polling = True
        self.gui_root = gui 
        self.log_updater = log_updater
        self.activity_callback = activity_callback

        self._poll_loop() 


    def stop_polling(self):
        
        # If actively polling, stop it
        if self.is_polling:
            print("[controllerDrive] Stopping controller polling.")
            self.is_polling = False

        # If not polling, do nothing
        else:
            print("[controllerDrive] Went to stop controller polling, but it is not active.")


    def close(self):
        # Closes the full Pygame instance, ONCE PER APPLICATION EXIT
        print("[controllerDrive] Quitting Pygame.")
        pygame.joystick.quit()
        pygame.quit()

    # Loop for when polling is live
    def _poll_loop(self):

        # End loop if flag is set to off
        if not self.is_polling:
            return
        
        # Helper function to send messages to the GUI log or the terminal
        def _log(message):
            if self.log_updater:
                # If a log updater function was provided, use it
                self.log_updater(message)
            else:
                # Fallback to standard print if no log updater is set
                print(f"[controllerDrive] {message}")

        if not self._is_os_connected():
            self._handle_disconnect()
            return
        
        # Send Pygame event queue to update joystick states
        pygame.event.get() 

        try:
            # Check Axes
            for i in range(self.joystick.get_numaxes()): # type: ignore
                current_val = self.joystick.get_axis(i)  # type: ignore
                
                # Original polling logic (with smaller deadzone)
                if abs(current_val) < DEADZONE: 
                    current_val = 0.0
                
                if round(current_val, 2) != round(self.prev_axis_states.get(i, 0.0), 2):
                    _log(f"Axis {i} changed: {current_val:.2f}") # <--- REPLACED print()

                    # Detect "hard snaps" to absolute values to ignore crash/sleep states
                    prev_val = self.prev_axis_states.get(i, 0.0)
                    is_hard_snap = (abs(current_val) >= 1.0) and (abs(current_val - prev_val) > 0.5)
                    if not is_hard_snap and self.activity_callback:
                        self.activity_callback()

                    self.prev_axis_states[i] = current_val
                    activity_detected = True 
            
            # Override for T.16000M Z Axis
            if ((self.joystick.get_name() == "T.16000M") | (self.joystick.get_name() == "Thrustmaster T.16000M")):
                self.prev_axis_states[9] = self.joystick.get_button(2)
                self.prev_axis_states[10] = self.joystick.get_button(3)

            # Check Buttons
            for i in range(self.joystick.get_numbuttons()): # type: ignore
                current_val = self.joystick.get_button(i)   # type: ignore

                # Compare to previous state, print changed state
                if current_val != self.prev_button_states.get(i, 0):
                    _log(f"Button {i} {'pressed' if current_val else 'released'}") # <--- REPLACED print()
                    if self.activity_callback:
                        self.activity_callback()
                    self.prev_button_states[i] = current_val

            # Check Hats (DPad)
            for i in range(self.joystick.get_numhats()):   # type: ignore
                current_val = self.joystick.get_hat(i)     # type: ignore
                # Same as button but four dimensions for the hat
                if current_val != self.prev_hat_states.get(i, (0, 0)):
                    _log(f"Hat {i} (DPad) changed: {current_val}") # <--- REPLACED print()
                    if self.activity_callback:
                        self.activity_callback()
                    self.prev_hat_states[i] = current_val

        # Exception handling for disconnected joystick      
        except pygame.error as e:
            print(f"[controllerDrive] Pygame error during polling (joystick disconnected?): {e}")
            self._handle_disconnect()
            return
        
        # Reschedule this function to run again after POLL_INTERVAL milliseconds
        # Make sure it can find the root window to schedule with
        if self.gui_root:
            self.gui_root.after(self.POLL_INTERVAL, self._poll_loop)
        else:
            print("[controllerDrive] Error: tkinter root window not found. Stopping poll.")
            self.stop_polling()

    def get_button_edge(self, button_id):
        if not hasattr(self, 'button_read_states'):
            self.button_read_states = {}
            
        val = self.prev_button_states.get(button_id, 0)
        
        if val == 1:
            if self.button_read_states.get(button_id, False):
                return 0
            else:
                self.button_read_states[button_id] = True
                return 1
        else:
            self.button_read_states[button_id] = False
            return 0

    def get_hat_edge(self, hat_id):
        if not hasattr(self, 'hat_read_states'):
            self.hat_read_states = {}
            
        val = self.prev_hat_states.get(hat_id, (0, 0))
        read_val = self.hat_read_states.get(hat_id, (False, False))
        
        out_x, out_y = val[0], val[1]
        new_read_x, new_read_y = read_val[0], read_val[1]
        
        if val[0] != 0:
            if read_val[0]:
                out_x = 0
            else:
                new_read_x = True
        else:
            new_read_x = False
            
        if val[1] != 0:
            if read_val[1]:
                out_y = 0
            else:
                new_read_y = True
        else:
            new_read_y = False
            
        self.hat_read_states[hat_id] = (new_read_x, new_read_y)
        return (out_x, out_y)