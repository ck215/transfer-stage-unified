# ---------------- IMPORTS ---------------------
# External Modules
import controllerDrive as controllerDrive
import serialDrive as serialDrive
import color_test_new as color_test
import serial
import serial.tools.list_ports

# GUI library
import tkinter as tk
from tkinter import ttk

# Timings library
import time 

# Arduino search function
def get_arduino_port():
    print("Scanning system for Arduino Mega...")
    ports = serial.tools.list_ports.comports()

    # Identifiers for Arduinoi Mega 2560 and clones (common VID/PID combinations)
    MEGA_VIDS = [0x2341, 0x1A86]
    MEGA_PIDS = [0x0042, 0x0010, 0x7523]
    
    for port in ports:
        # Convert text attributes to lowercase safely
        desc = port.description.lower() if port.description else ""
        hwid = port.hwid.lower() if port.hwid else ""
        
        # Method 1: Check by precise numeric Vendor/Product IDs (Win/Mac/Linus)
        if port.vid in MEGA_VIDS and port.pid in MEGA_PIDS:
            print(f"--> Auto-detected via Hardware ID: Official/Clone Mega 2560 oon {port.device}")
            return port.device
        
        # Method 2: String matching fallback (For edge-case descriptors)
        if "arduino" in desc or "mega" in desc or "ch340" in desc:
            print(f"--> Auto-detected via Descriptor: {port.description} on {port.device}")
            return port.device     
            
    # Fallback if no matching signature is plugged in
    print("Could not auto-detect an Arduino Mega.")
    print("Please provide the serial port manually.")
    print("Examples: COM3 (Windows), /dev/ttyACM0 (Linux), /dev/tty.usbmodem14101 (Mac)")
    return input("Serial Port: ")

# ---------------- GUI CLASS ---------------------
class StepperFrame:

    # Member funct. to initialize the GUI
    def __init__(self, root: tk.Tk):
        
        # Initialize root window
        self.root = root
        self.root.title("Stepper Probe Controller")

        # Initialize vars for controller log window
        self.controller_log_window: tk.Toplevel | None = None
        self.controller_log_text: tk.Text | None = None

        # NEW: StringVars for live absolute position display (updated from firmware POS: messages)
        self.pos_x_var: tk.StringVar = tk.StringVar(value="0")
        self.pos_y_var: tk.StringVar = tk.StringVar(value="0")
        self.pos_z_var: tk.StringVar = tk.StringVar(value="0")

        # Initialize vars to hold GUI input fields
        self.entry_x_step: tk.Entry
        self.entry_y_step: tk.Entry
        self.entry_z_step: tk.Entry
        self.entry_x_dist: tk.Entry
        self.entry_y_dist: tk.Entry
        self.entry_z_dist: tk.Entry
        self.entry_full_speed: tk.Entry
        self.entry_enable: tk.Entry
        self.entry_brake_distance: tk.Entry
        self.entry_man_full_speed: tk.Entry

        # Button Widgets
        self.auton_mode_button: tk.Button
        self.manual_mode_button: tk.Button
        self.enable_button: tk.Button
        self.start_stepping_button: tk.Button
        self.full_stop_button: tk.Button
        self.color_test_button: tk.Button
        self.connect_controller_button: tk.Button
        self.controller_log_button: tk.Button

        # Call the main window setup function that formats using outline below
        self._main_window()

    # Main window outline function    
    def _main_window(self):
        row_counter = 0

        # Global styling variables applied to widgets
        bg_main = '#0F172A'
        bg_entry = '#1E293B'
        fg_text = '#F8FAFC'
        fg_muted = '#64748B'
        fg_accent = '#38BDF8'
        fg_telemetry = '#00F0FF'

        self.root.configure(bg=bg_main)

        # NEW: Absolute Position Display Section — always visible, updated globally
        tk.Label(self.root, text="--- Absolute Position (from boot zero) ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1

        tk.Label(self.root, text="MUST be 0 at startup, if 2 then error", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=0); row_counter+=1

        tk.Label(self.root, text="X Position:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Label(self.root, textvariable=self.pos_x_var, font=('Arial', 10, 'bold'), fg='blue').grid(row=row_counter, column=1, padx=5, pady=2, sticky='w'); row_counter += 1

        tk.Label(self.root, text="Y Position:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Label(self.root, textvariable=self.pos_y_var, font=('Arial', 10, 'bold'), fg='blue').grid(row=row_counter, column=1, padx=5, pady=2, sticky='w'); row_counter += 1

        tk.Label(self.root, text="Z Position:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Label(self.root, textvariable=self.pos_z_var, font=('Arial', 10, 'bold'), fg='blue').grid(row=row_counter, column=1, padx=5, pady=2, sticky='w'); row_counter += 1
        # END NEW

        # INPUT FIELDS
        tk.Label(self.root, text="--- Connections ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        
        tk.Label(self.root,text="Controller:", bg=bg_main, fg=fg_accent).grid(row=row_counter,column=0,padx=5,pady=2,sticky='w')
        self.controller_var = tk.StringVar(value="None")
        self.controller_dropdown = ttk.Combobox(self.root, textvariable = self.controller_var, state = "readonly")
        self.controller_dropdown.grid(row=row_counter, column=1, columnspan=1, padx=5, pady=5, sticky="ew")
        row_counter+=1

        tk.Label(self.root, text="Serial Port:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.entry_serial_port = tk.Entry(self.root); self.entry_serial_port.grid(row=row_counter, column=1, padx=5, pady=2); self.entry_serial_port.insert(0, "/dev/ttys00X"); row_counter += 1

        tk.Label(self.root, text="--- Step Sizes ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1

        tk.Label(self.root, text="Powers of 2; 8 or 16 recommended", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=0); row_counter+=1

        tk.Label(self.root, text="X Step Size:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.entry_x_step = tk.Entry(self.root); self.entry_x_step.grid(row=row_counter, column=1, padx=5, pady=2); self.entry_x_step.insert(0, "16"); row_counter += 1
        
        tk.Label(self.root, text="Y Step Size:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.entry_y_step = tk.Entry(self.root); self.entry_y_step.grid(row=row_counter, column=1, padx=5, pady=2); self.entry_y_step.insert(0, "16"); row_counter += 1
        
        tk.Label(self.root, text="Z Step Size:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.entry_z_step = tk.Entry(self.root); self.entry_z_step.grid(row=row_counter, column=1, padx=5, pady=2); self.entry_z_step.insert(0, "16"); row_counter += 1

        # CHANGED: Section header and labels renamed for relative stepping clarity
        tk.Label(self.root, text="--- Relative Step Counts ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        
        # CHANGED: "X Distance:" → "X Steps:", default "0.0" → "0"
        tk.Label(self.root, text="X Steps:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.entry_x_dist = tk.Entry(self.root); self.entry_x_dist.grid(row=row_counter, column=1, padx=5, pady=2); self.entry_x_dist.insert(0, "0"); row_counter += 1
        
        # CHANGED: "Y Distance:" → "Y Steps:", default "0.0" → "0"
        tk.Label(self.root, text="Y Steps:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.entry_y_dist = tk.Entry(self.root); self.entry_y_dist.grid(row=row_counter, column=1, padx=5, pady=2); self.entry_y_dist.insert(0, "0"); row_counter += 1
        
        # CHANGED: "Z Distance" → "Z Steps:", default "0.0" → "0", fixed missing colon
        tk.Label(self.root, text="Z Steps:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.entry_z_dist = tk.Entry(self.root); self.entry_z_dist.grid(row=row_counter, column=1, padx=5, pady=2); self.entry_z_dist.insert(0, "0"); row_counter += 1

        tk.Label(self.root, text="--- Velocity Control ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        
        tk.Label(self.root, text="<= 1600", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=0); row_counter+=1

        tk.Label(self.root, text="Full Speed (Microsteps/Sec):", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.entry_full_speed = tk.Entry(self.root); self.entry_full_speed.grid(row=row_counter, column=1, padx=5, pady=2); self.entry_full_speed.insert(0, "400"); row_counter += 1
        
        tk.Label(self.root, text="Manual Mode Max Speed (Microsteps/Sec):", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.entry_man_full_speed = tk.Entry(self.root); self.entry_man_full_speed.grid(row=row_counter, column=1, padx=5, pady=2); self.entry_man_full_speed.insert(0, "400"); row_counter += 1

        
        tk.Label(self.root, text="Enable System (1/0)", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.entry_enable = tk.Entry(self.root); self.entry_enable.grid(row=row_counter, column=1, padx=5, pady=2); self.entry_enable.insert(0, "1"); row_counter += 1
        
        tk.Label(self.root, text="Brake Distance (Counts):", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.entry_brake_distance = tk.Entry(self.root); self.entry_brake_distance.grid(row=row_counter, column=1, padx=5, pady=2); self.entry_brake_distance.insert(0, "0"); row_counter += 1

        # BUTTONS HEADER
        tk.Label(self.root, text="--- Motor Commands ---", bg=bg_main, fg=fg_accent, font=('Arial', 10, 'bold')).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1

        # Switch to Autonomous Mode Button
        self.auton_mode_button = tk.Button(self.root, text="ENTER AUTONOMOUS MODE",
        bg='darkgreen', fg='black', font=('Arial', 10, 'bold'))
        self.auton_mode_button.grid(row=row_counter, column=0, padx=5, pady=5, sticky='ew')

        # Switch to Manual Mode Button
        self.manual_mode_button = tk.Button(self.root, text="ENTER MANUAL MODE",
        bg='blue', fg='black', font=('Arial', 10, 'bold'))
        self.manual_mode_button.grid(row=row_counter, column=1, padx=5, pady=5, sticky='ew'); row_counter += 1

        # Enable / Disable Button
        self.enable_button = tk.Button(self.root, text="Enable System",
        bg='darkgreen', fg='black', font=('Arial', 10, 'bold'))
        self.enable_button.grid(row=row_counter, column = 0, columnspan=2, padx=5, pady=5, sticky='ew'); row_counter += 1

        # Start Stepper Button 
        self.start_stepping_button = tk.Button(self.root, text="Start Stepping",
        bg='darkgreen', fg='black', font=('Arial', 10, 'bold'))
        self.start_stepping_button.grid(row=row_counter, column=0, columnspan=2,padx=5, pady=5, sticky='ew'); row_counter += 1
        
        self.full_stop_button = tk.Button(self.root, text="Full Stop",
        bg='darkgreen', fg='black', font=('Arial', 10, 'bold'))
        self.full_stop_button.grid(row=row_counter, column=0, columnspan=2,padx=5, pady=5, sticky='ew'); row_counter += 1

        # Additional Controls Header
        tk.Label(self.root, text="--- Additional Controls ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        
        # Color_Test Button
        self.color_test_button = tk.Button(self.root, text="Red Percent Function", command=self.color_test_window,
        bg='darkgreen', fg='black', font=('Arial', 10, 'bold'))
        self.color_test_button.grid(row=row_counter, column=0, columnspan=2,padx=5, pady=5, sticky='ew')
        row_counter += 1

        # Connect Controller Button
        self.connect_controller_button = tk.Button(self.root, text="Connect Controller",
        bg='darkgreen', fg='black', font=('Arial', 10, 'bold'))
        self.connect_controller_button.grid(row=row_counter, column=0, columnspan=2,padx=5, pady=5, sticky='ew')
        row_counter += 1

        # Serial Reconnect Button
        self.serial_reconnect_button = tk.Button(self.root, text="Serial Reconnect",
        bg='darkgreen', fg='black', font=('Arial', 10, 'bold'))
        self.serial_reconnect_button.grid(row=row_counter, column=0, columnspan=2,padx=5, pady=5, sticky='ew')
        row_counter += 1

        # Debug header
        tk.Label(self.root, text="--- Debug or Unfinished ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        self.controller_log_button = tk.Button(self.root, text="Controller Log Window",
        bg='darkgreen', fg='black', font=('Arial', 10, 'bold'))
        self.controller_log_button.grid(row=row_counter, column=0, columnspan=2,padx=5, pady=5, sticky='ew')
        
    # Function to initialize the controller log window
    def open_controller_log_window(self, is_controller_connected: bool):

        # Window still open, bring to front if needed and return
        if self.controller_log_window and self.controller_log_window.winfo_exists():
            self.controller_log_window.lift() 
            return
        
        # No controller connected, cannot open log window
        if not is_controller_connected:
            print("[ROOT_GUI] Cannot open controller log window: No controller connected.")
            return
        print("[ROOT_GUI] Opening controller log window...")


        # Create the window and text widget
        self.controller_log_window = tk.Toplevel(self.root)
        self.controller_log_window.title("Controller Log")
        self.controller_log_text = tk.Text(self.controller_log_window, height=15, width=50, state='disabled', wrap='word')
        self.controller_log_text.pack(padx=5, pady=5)
        
        # Setup cleanup function when the window is closed by the user
        self.controller_log_window.protocol("WM_DELETE_WINDOW", self.close_controller_log_window) 

    # Cleanup function for when the log window is closed  
    def close_controller_log_window(self):
        print("[ROOT_GUI] Controller log window closing...")
        
        # Check if window is even open
        if self.controller_log_window:

            # Properly destroy the Toplevel window
            self.controller_log_window.destroy()

        # Reset the references so a new window can be created later
        self.controller_log_window = None
        self.controller_log_text = None

    # Function to print in the controller log window
    def controller_log_print(self, message: str):
        if self.controller_log_text:
            self.controller_log_text.config(state='normal')
            self.controller_log_text.insert(tk.END, message + "\n")
            self.controller_log_text.see(tk.END)                    # Auto-scroll to newest entry
            self.controller_log_text.config(state='disabled')

    # Function to open red percent function test window (placeholder for now)
    def color_test_window(self):
        color_test.run_color_test()
        print("\n[ROOT_GUI] COLOR TEST button clicked.")
        return

    # Reads the GUI input fields and returns a dictionary of parameters
    def get_gui_params(self, manual_flag: bool, auton_flag: bool):
        return {
            "x_step_size": self.entry_x_step.get(),             
            "y_step_size": self.entry_y_step.get(),              
            "z_step_size": self.entry_z_step.get(),              
            "full_speed": self.entry_full_speed.get(),           
            "slow_speed": self.entry_enable.get(),           
            "brake_distance": self.entry_brake_distance.get(),   
            "x_dist": self.entry_x_dist.get(),                   
            "y_dist": self.entry_y_dist.get(),                   
            "z_dist": self.entry_z_dist.get(),  
            "command_code_manual": int(manual_flag),               
            "command_code_auton": int(auton_flag)                      
        }
    
    # Define the cleanup function on GUI close
    def _on_closing(self):
        print("\n[ROOT_GUI] GUI closing...")
        self.close_controller_log_window()  # Close controller log window if open
        #TODO                                 Cleanup red percent function test resources if any
        self.root.destroy()                 # Destroy the Tkinter window

    # Main run function to launch the GUI
    def run(self):

        # Welcome Message
        print("[ROOT_GUI] GUI launched.")
        print("\nWelcome to the Transfer Stage Control Interface")
        
        # Main GUI loop
        self.root.mainloop()



# ---------------- APP LOGIC CLASS ------------------
class AppLogic:

    system_enabled = False

    def __init__(self, root: tk.Tk, gui: StepperFrame, controller: controllerDrive.ControllerPoller, serial: serialDrive.SerialArduino, active_claims, process_name):
        
        # Initialize members
        self.root = root
        self.gui = gui
        self.controller = controller
        self.serial = serial
        self.active_claims = active_claims
        self.process_name = process_name

        # Initalize mode flags
        self.autonFlag: bool = False
        self.manualFlag: bool = False

        # NEW: Flag to cleanly stop the position polling loop on shutdown
        self._running: bool = True

        # Bind buttons from GUI class to logic functions
        self.gui.auton_mode_button.config(command=self.enter_autonomous_mode_button)
        self.gui.manual_mode_button.config(command=self.enter_manual_mode_button)
        self.gui.enable_button.config(command=self.enable_button)
        self.gui.start_stepping_button.config(command=self.start_stepping_button)
        self.gui.full_stop_button.config(command=self.full_stop_button)
        self.gui.connect_controller_button.config(command=self.connect_controller_button)
        self.gui.controller_log_button.config(command=self.open_controller_log_window)
        self.gui.serial_reconnect_button.config(command=self.serial_reconnect_button)
        self.gui.controller_dropdown.bind("<<ComboboxSelected>>", self.on_controller_dropdown_selected)
        
        # Protocal for window closing
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # NEW: Start the global position polling loop (runs in all modes)
        self._poll_position()
        self._update_controller_dropdown_loop()

    def _update_controller_dropdown_loop(self):
        if not self._running:
            return

        hardware_controllers = self.controller.get_physical_controllers()

        other_claims = {
            id_str for proc, id_str in self.active_claims.items()
            if proc != self.process_name
        }

        available_options = ["None"]
        for ctrl in hardware_controllers:
            if ctrl not in other_claims:
                available_options.append(ctrl)

        self.gui.controller_dropdown['values'] = available_options

        current_selection = self.active_claims.get(self.process_name, "None")
        self.gui.controller_var.set(current_selection)

        self.root.after(500, self._update_controller_dropdown_loop)
    
    def on_controller_dropdown_selected(self, event):
        selected = self.gui.controller_var.get()

        success = self.controller.change_controller(selected)

        if not success or "None" in selected:
            self.active_claims[self.process_name] = "None"
            if self.manualFlag:
                self.full_stop_button()
        else:
            self.active_claims[self.process_name] = selected
            print(f"[{self.process_name}] Successfully mapped to {selected}")

    # NEW: Periodically reads position from firmware and updates the GUI display.
    #      Self-scheduling via root.after(), runs regardless of mode (manual, auton, idle).
    def _poll_position(self):
        if not self._running:
            self._is_polling_pos = False
            return

        if self.serial.ser is None:
            print("[AppLogic] Serial connection not established. Cannot poll position.")
            self._is_polling_pos = False
            return
        
        else:
            pos = self.serial.read_position()
            if pos is not None:
                self.gui.pos_x_var.set(str(pos[0]))
                self.gui.pos_y_var.set(str(pos[1]))
                self.gui.pos_z_var.set(str(pos[2]))

            # Poll every 50ms (firmware sends every 100ms, so this keeps display responsive)
            self.root.after(50, self._poll_position)

    # Opens the controller log window from the GUI
    def open_controller_log_window(self):
        is_connected = self.controller.joystick is not None
        self.gui.open_controller_log_window(is_controller_connected=is_connected)
        if is_connected:
            try:
                self.gui.controller_log_print(f"[AppLogic] Controller connected: {self.controller.joystick.get_name()}") #type: ignore
            except Exception as e:
                print(f"[AppLogic] Controller connected, but error printing controller name in log window: {e}")

    # Enters autonomous mode
    def enter_autonomous_mode_button(self):
        if not (self.system_enabled):
            print("\n[AppLogic] System is not enabled. Command not sent.")
            return
        print("\n[AppLogic] ENTER AUTONOMOUS MODE button clicked.")
        print("AUTONOMOUS MODE ENGAGED")
        print("Send stepping requests with START STEPPING button.")
        print("Press FULL STOP to stop stepping.")
        print("Enter manual mode using the MANUAL MODE button to control the stage manually with the Xbox Controller.")
        self.autonFlag = True
        self.manualFlag = False

        # Stop monitoring controller input
        self.controller.stop_polling()

        # Exit controller log window if open
        if self.gui.controller_log_window:
            self.gui.close_controller_log_window()

        # FIX #5: Send a stop command so firmware halts motors immediately
        # (previously, firmware kept running last manual values until next command)
        try:
            params = self.gui.get_gui_params(manual_flag=False, auton_flag=False)
            self.serial.send_autonomous_command(params)
        except Exception as e:
            print(f"[AppLogic] Error sending stop on mode switch: {e}")

    # Enables or disables controllers, i.e. power to motors
    def enable_button(self):
        print("\n[AppLogic] ENABLE/DISABLE button clicked.")
        if (self.system_enabled):
            self.system_enabled = False
            self.full_stop_button()
            self.serial.disable()
            self.gui.enable_button.config(text="Enable System",
            bg='darkgreen', fg='black', font=('Arial', 10, 'bold'))

        else:
            try:
                self.serial.enable()
                self.system_enabled = True
                self.gui.enable_button.config(text="Disable System",
                bg='darkred', fg='black', font=('Arial', 10, 'bold'))
            except ValueError as e:
                print(e)


    # Sends command using current GUI parameters over serial
    def start_stepping_button(self):
        if not (self.system_enabled):
            print("\n[AppLogic] System is not enabled. Command not sent.")
            return
        if self.autonFlag and not self.manualFlag:
            print("\n[AppLogic] START STEPPING button clicked.")
            try:
                params = self.gui.get_gui_params(self.manualFlag, self.autonFlag)
                self.serial.send_autonomous_command(params)
            except Exception as e:
                print(f"[AppLogic] Error in calling send_autonomous_command: {e}")
        else:
            print("\n[AppLogic] Cannot step while not in AUTONOMOUS MODE.")
    
    # FULL STOP of either mode, used before swapping modes
    def full_stop_button(self):
        print("\n[AppLogic] FULL STOP button clicked.")
        print("FULL STOP engaged. Select a mode to continue.")
        try:
            self.manualFlag = False
            self.autonFlag = False
            self.controller.stop_polling()                          # FIX #6: Stop controller polling on full stop
            params = self.gui.get_gui_params(self.manualFlag, self.autonFlag)
            self.gui.close_controller_log_window()
            self.serial.send_autonomous_command(params)
            
        except Exception as e:
            print(f"[AppLogic] Error in stopping stepping: {e}")

    # Manual mode loop, polls controller and sends commands
    def _manual_mode_loop(self):
        if self.controller.joystick is None:
            print("[AppLogic] No controller connected. Please connect a controller before entering MANUAL MODE.")
            self.manualFlag = False
            return
        
            try:
                params = self.gui.get_gui_params(manual_flag=False, auton_flag=False)
                self.serial.send_autonomous_command(params)
            except Exception as e:
                print(f"[AppLogic] Error sending stop command on controller disconnect: {e}")
            return
        
        # Exits manual loop when flag is unset
        if self.manualFlag == False:
            print("[AppLogic] Exiting manual mode loop.")
            return
        
         # Get controller axis states and send manual command
        try:
            params = self.get_controller_params()
            self.serial.send_manual_mode_command(params)

            # Call this function again after a short delay
            self.root.after(5, self._manual_mode_loop)  
            
        except Exception as e:
            print(f"[AppLogic] Error in manual mode loop: {e}")

    # We are going to allow mode switching WITHOUT a full stop first, for convenience
    def enter_manual_mode_button(self):
        # if self.autonFlag:
        #     print("\n[AppLogic] Cannot enter MANUAL MODE while in AUTONOMOUS MODE. Press FULL STOP first.")
        #     return
        if not (self.system_enabled):
            print("\n[AppLogic] System is not enabled. Command not sent.")
            return
        if self.controller.joystick is None:
            print("\nNo controller connected. Please connect a controller before entering MANUAL MODE.")
            self.manualFlag = False
            return
        if self.manualFlag:
            print("\n[AppLogic] Already in MANUAL MODE.")
            return

        print("\n[AppLogic] ENTER MANUAL MODE button clicked.")
        print("MANUAL MODE ENGAGED")
        print("Reading controller input... press FULL STOP to stop controller input and switch modes.")
        self.manualFlag = True
        self.autonFlag = False

        # Start monitoring controller input
        self.controller.start_polling(gui=self.root, log_updater=self.gui.controller_log_print)

        # Open controller log window if not already open
        self.open_controller_log_window()
        
        # Begin manual loop
        self._manual_mode_loop()
    
    # Connects to controller when button clicked in GUI      
    def connect_controller_button(self):
        print("\n[AppLogic] CONNECT CONTROLLER button clicked.")
        success = self.controller.connect_controller()
        if success:
            print("[AppLogic] Controller connected successfully.")
        else:
            print("[AppLogic] Failed to connect controller.")
            
    # Passes controller axis states to a dictionary for serialDrive to send to arduino
    def get_controller_params(self):
        return {
            "x_axisStatus": self.controller.prev_axis_states.get(0, 0.0),     # FIX #4: Added default 0.0 (was None)
            "y_axisStatus": self.controller.prev_axis_states.get(3, 0.0),     # FIX #4: Added default 0.0 (was None)
            "z_axisStatusR": self.controller.prev_axis_states.get(4, -1.0),   # FIX #4: Added default -1.0 idle trigger (was None)
            "z_axisStatusL": self.controller.prev_axis_states.get(5, -1.0),   # FIX #4: Added default -1.0 idle trigger (was None)
            "dpad_left": self.controller.prev_hat_states.get(0, (0, 0))[0],
            "dpad_right": self.controller.prev_hat_states.get(0, (0, 0))[0],
            "dpad_up": self.controller.prev_hat_states.get(0, (0, 0))[1],
            "dpad_down": self.controller.prev_hat_states.get(0, (0, 0))[1],
            "manual_jog_speed": self.gui.entry_man_full_speed.get()                             
        }
    
    def serial_reconnect_button(self):
        print("\n[AppLogic] SERIAL RECONNECT button clicked.")
        self.serial.close()                                                                       # Close existing connection if any
        time.sleep(1)                                                                             # Short delay to ensure port is released
        self.serial = serialDrive.SerialArduino(port=self.gui.entry_serial_port.get())  # Attempt to reconnect
        if self.serial.ser and self.serial.ser.is_open:
            print("[AppLogic] Serial reconnected successfully.")
            if getattr(self, '_is_polling_pos', False) is False:
                self._poll_position()
        else:
            print("[AppLogic] Failed to reconnect serial.")
    
    # Define the cleanup function on GUI close
    def _on_closing(self):
        print("[AppLogic] Closing application...")
        if (self.system_enabled):
            self.serial.disable()
        self._running = False               # NEW: Stop the position polling loop
        self.controller.stop_polling()      # Stop the controller module
        self.controller.close()             # Quit Pygame properly
        self.serial.close()                 # Close the serial module
        self.gui._on_closing()              # Call the app's cleanup and destroy the window



# ---------------- MAIN LOOP ---------------------

def main(port, controllerID, active_claims, process_name):

    print("[main] Starting main loop")

    # Get serial port for serial function
    serial_port = port

    # Setup GUI class and its members
    root = tk.Tk()
    print("[main] Initializing GUI...")
    gui = StepperFrame(root)

    # Connect Arudino via serial
    print("[main] Initializing arduino connection...")
    serial = serialDrive.SerialArduino(port=serial_port)

    # Setup controller class
    print("[main] Initializing controller polling class...")
    controller = controllerDrive.ControllerPoller(controllerID, active_claims, process_name)
    
    # Setup Logic class
    print("[main] Initializing App Logic...")
    gui_logic = AppLogic(root, gui, controller, serial, active_claims, process_name)

    # Run the GUI application
    print("[main] Launching GUI...")
    gui.run() 

    # Shutdown code once GUI is closed
    print("[main] Finished main loop, program terminated.")

# ---------------------------------------------------



# For packaged executable to run main
if __name__ == "__main__":
    main()
