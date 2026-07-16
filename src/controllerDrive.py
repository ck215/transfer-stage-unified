# Libraries
import pygame
import time
import os

class ControllerPoller:
    
    # Poll 50 times per second (1000ms / 20ms = 50Hz)
    POLL_INTERVAL = 5

    # Controller binds, format [x,y,+z,-z]
    controller_binds = []
    xbox_controller = False
    T160000M = False

    def __init__(self, controllerID, active_claims, process_name):
        # Polling control flag
        self.is_polling = False

        self.controllerID = controllerID

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
        pygame.quit()  
        self._initialize_pygame_joystick(self.controllerID)
        return True if self.joystick else False

    # Initalizes Pygame instance, ONCE PER APPLICATION START
    def _initialize_pygame_joystick(self, controllerID):
        self.stop_polling()
        self.xbox_controller = False
        self.T160000M = False

        if not controllerID or "None" in controllerID or "Virtual" in controllerID:
            print(f"[{self.process_name}] Joystick set to None.")
            self.joystick = None
            self.active_claims[self.process_name] = "None"
            return False

        try:
            pygame.init()
            pygame.joystick.init()
        
            try:
                # Initialize the chosen joystick
                controller_number = int(controllerID[3:4])

                self.joystick = pygame.joystick.Joystick(controller_number)
                self.joystick.init()

                self.active_claims[self.process_name] = controllerID

                print(f"\n[controllerDrive] Initialized Joystick: {self.joystick.get_name()}")
                print(f"  Axes: {self.joystick.get_numaxes()}")
                print(f"  Buttons: {self.joystick.get_numbuttons()}")
                print(f"  Hats: {self.joystick.get_numhats()}")

                # Controller binds -- add more for new controllers!

                match self.joystick.get_name():
                    case "Xbox Series X Controller":
                        self.controller_binds = [0,3]
                        self.xbox_controller = True
                    case "T.16000M":
                        self.controller_binds = [0,1]
                    case _:
                        raise ValueError("Controller not recognized!")
                
                # Initialize previous state dictionaries
                for i in self.controller_binds:
                    self.prev_axis_states[i] = 0.0
                for i in range(self.joystick.get_numbuttons()):
                    self.prev_button_states[i] = 0
                for i in range(self.joystick.get_numhats()):
                    self.prev_hat_states[i] = (0, 0)
                return True
            except:
                print("[controllerDrive] No joystick found.")
                self.joystick = None
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

    def start_polling(self, gui, log_updater):
        
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
        
        # Send Pygame event queue to update joystick states
        pygame.event.get() 

        try:
            # Check Axes
            index = 'x' # track which axis
            for i in self.controller_binds: # type: ignore
                current_val = self.joystick.get_axis(i)  # type: ignore
                
                # Original polling logic (with smaller deadzone)
                if abs(current_val) < 0.1: 
                    current_val = 0.0

                if round(current_val, 2) != round(self.prev_axis_states.get(i, 0.0), 2):
                    _log(f"Axis {i} changed: {current_val:.2f}") # <--- REPLACED print()
                    self.prev_axis_states[index] = current_val
                    activity_detected = True 
                
                match index:
                    case 'x': index = 'y',
                    case 'y': index = 'z'
                
            
            # If Xbox, add together Z axis commands

            if (self.xbox_controller):
                # Get raw trigger values (assuming idle is -1)
                z_trigger_l_raw = self.prev_axis_states.get(5, -1.0)
                z_trigger_r_raw = self.prev_axis_states.get(4, -1.0)

                # Remap from [-1, 1] to [0, 1] 
                z_up_value = (z_trigger_l_raw + 1.0) / 2.0
                z_down_value = (z_trigger_r_raw + 1.0) / 2.0

                # Combine the values. UP (L) is positive, DOWN (R) is negative.
                self.prev_axis_states['z'] = z_up_value - z_down_value
            elif (self.T160000M):
                self.prev_axis_states['z'] = self.joystick.get_button(3)-self.joystick.get_button(4)
            else:
                print("[ControllerDrive] Unsupported controller detected!")

            # Check Buttons
            for i in range(self.joystick.get_numbuttons()): # type: ignore
                current_val = self.joystick.get_button(i)   # type: ignore

                # Compare to previous state, print changed state
                if current_val != self.prev_button_states.get(i, 0):
                    _log(f"Button {i} {'pressed' if current_val else 'released'}") # <--- REPLACED print()
                    self.prev_button_states[i] = current_val

            # Check Hats (DPad)
            for i in range(self.joystick.get_numhats()):   # type: ignore
                current_val = self.joystick.get_hat(i)     # type: ignore
                # Same as button but four dimensions for the hat
                if current_val != self.prev_hat_states.get(i, (0, 0)):
                    _log(f"Hat {i} (DPad) changed: {current_val}") # <--- REPLACED print()
                    self.prev_hat_states[i] = current_val

        # Exception handling for disconnected joystick      
        except pygame.error as e:
            print(f"[controllerDrive] Pygame error during polling (joystick disconnected?): {e}")
            self.joystick = None 
            self.prev_axis_states.clear()
            self.prev_button_states.clear()
            self.prev_hat_states.clear()
            self.stop_polling()
            return
        
        # Reschedule this function to run again after POLL_INTERVAL milliseconds
        # Make sure it can find the root window to schedule with
        if self.gui_root:
            self.gui_root.after(self.POLL_INTERVAL, self._poll_loop)
        else:
            print("[controllerDrive] Error: tkinter root window not found. Stopping poll.")
            self.stop_polling()