import tkinter as tk
from tkinter import ttk
from models.stepper_model import StepperModel

class StepperView:
    def __init__(self, root: tk.Tk, model: StepperModel, controller):
        self.root = root
        self.model = model
        self.controller = controller
        self.root.title("Stepper Probe Controller")

        # Initialize vars for controller log window
        self.controller_log_window = None
        self.controller_log_text = None

        self._build_ui()

    def _build_ui(self):
        row_counter = 0

        bg_main = '#0F172A'
        bg_entry = '#1E293B'
        fg_text = '#F8FAFC'
        fg_accent = '#38BDF8'

        self.root.configure(bg=bg_main)

        # Absolute Position
        tk.Label(self.root, text="--- Absolute Position (from boot zero) ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        tk.Label(self.root, text="MUST be 0 at startup, if 2 then error", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=0); row_counter+=1

        for axis, var in [("X", self.model.pos_x), ("Y", self.model.pos_y), ("Z", self.model.pos_z)]:
            tk.Label(self.root, text=f"{axis} Position:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
            tk.Label(self.root, textvariable=var, font=('Arial', 10, 'bold'), fg='red',bg=bg_main).grid(row=row_counter, column=1, padx=5, pady=2, sticky='w')
            row_counter += 1

        # Connections
        tk.Label(self.root, text="--- Connections ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        
        tk.Label(self.root,text="Controller:", bg=bg_main, fg=fg_accent).grid(row=row_counter,column=0,padx=5,pady=2,sticky='w')
        self.controller_dropdown = ttk.Combobox(self.root, textvariable=self.model.controller_var, state="readonly")
        self.controller_dropdown.grid(row=row_counter, column=1, columnspan=1, padx=5, pady=5, sticky="ew")
        self.controller_dropdown.bind("<<ComboboxSelected>>", self.controller.on_controller_dropdown_selected)
        row_counter+=1

        tk.Label(self.root, text="Serial Port:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Entry(self.root, textvariable=self.model.serial_port).grid(row=row_counter, column=1, padx=5, pady=2); row_counter += 1

        # Step Sizes
        tk.Label(self.root, text="--- Step Sizes ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        tk.Label(self.root, text="Powers of 2; 8 or 16 recommended", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=0); row_counter+=1

        for axis, var in [("X", self.model.x_step), ("Y", self.model.y_step), ("Z", self.model.z_step)]:
            tk.Label(self.root, text=f"{axis} Step Size:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
            tk.Entry(self.root, textvariable=var).grid(row=row_counter, column=1, padx=5, pady=2)
            row_counter += 1

        # Step Counts
        tk.Label(self.root, text="--- Relative Step Counts ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        for axis, var in [("X", self.model.x_dist), ("Y", self.model.y_dist), ("Z", self.model.z_dist)]:
            tk.Label(self.root, text=f"{axis} Steps:", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
            tk.Entry(self.root, textvariable=var).grid(row=row_counter, column=1, padx=5, pady=2)
            row_counter += 1

        # Script
        self.file_frame = tk.Frame(self.root)
        self.file_frame.configure(bg=bg_main)
        self.file_frame.grid(row=row_counter,column=1, padx=5, pady=3, sticky='w')
        tk.Label(self.root, text="Script", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        self.select_script_button = tk.Button(self.file_frame, text="Select", width=5, command=self.controller.select_script_button)
        self.select_script_button.grid(row=0, column=0, padx=(13,0), pady=2); row_counter+=1
        self.selected_script_label = tk.Label(self.file_frame, textvariable=self.model.selected_script, width=10, bg=bg_main, fg='white')
        self.selected_script_label.grid(row=0, column=1, padx=2, pady=2, sticky='w')

        # Velocity Control
        tk.Label(self.root, text="--- Velocity Control ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        tk.Label(self.root, text="<= 1600", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=0); row_counter+=1

        tk.Label(self.root, text="Full Speed (Microsteps/Sec):", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Entry(self.root, textvariable=self.model.full_speed).grid(row=row_counter, column=1, padx=5, pady=2); row_counter += 1
        
        tk.Label(self.root, text="Manual Mode Max Speed (Microsteps/Sec):", bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Entry(self.root, textvariable=self.model.man_full_speed).grid(row=row_counter, column=1, padx=5, pady=2); row_counter += 1

        # Buttons
        tk.Label(self.root, text="--- Motor Commands ---", bg=bg_main, fg=fg_accent, font=('Arial', 10, 'bold')).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1

        self.auton_mode_button = tk.Button(self.root, text="ENTER AUTONOMOUS MODE", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.controller.enter_autonomous_mode_button)
        self.auton_mode_button.grid(row=row_counter, column=0, padx=5, pady=5, sticky='ew')

        self.manual_mode_button = tk.Button(self.root, text="ENTER MANUAL MODE", bg='blue', fg='black', font=('Arial', 10, 'bold'), command=self.controller.enter_manual_mode_button)
        self.manual_mode_button.grid(row=row_counter, column=1, padx=5, pady=5, sticky='ew'); row_counter += 1

        self.enable_button = tk.Button(self.root, text="Enable System", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.controller.enable_button)
        self.enable_button.grid(row=row_counter, column = 0, columnspan=2, padx=5, pady=5, sticky='ew'); row_counter += 1

        self.start_stepping_button = tk.Button(self.root, text="Start Stepping", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.controller.start_stepping_button)
        self.start_stepping_button.grid(row=row_counter, column=0, columnspan=2,padx=5, pady=5, sticky='ew'); row_counter += 1

        self.run_script_button = tk.Button(self.root, text="Run Script", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.controller.run_script_button)
        self.run_script_button.grid(row=row_counter, column=0, columnspan=2, padx=5, pady=5, sticky='ew'); row_counter += 1
        
        self.full_stop_button = tk.Button(self.root, text="Full Stop", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.controller.full_stop_button)
        self.full_stop_button.grid(row=row_counter, column=0, columnspan=2,padx=5, pady=5, sticky='ew'); row_counter += 1

        tk.Label(self.root, text="--- Additional Controls ---", font=('Arial', 10, 'bold'), bg=bg_main, fg=fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        
        self.color_test_button = tk.Button(self.root, text="Red Percent Function", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.controller.color_test_window)
        self.color_test_button.grid(row=row_counter, column=0, columnspan=2,padx=5, pady=5, sticky='ew'); row_counter += 1

        self.serial_reconnect_button = tk.Button(self.root, text="Serial Reconnect", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.controller.serial_reconnect_button)
        self.serial_reconnect_button.grid(row=row_counter, column=0, columnspan=2,padx=5, pady=5, sticky='ew'); row_counter += 1

    def open_controller_log_window(self, is_controller_connected: bool):
        if self.controller_log_window and self.controller_log_window.winfo_exists():
            self.controller_log_window.lift() 
            return
        
        if not is_controller_connected:
            print("[ROOT_GUI] Cannot open controller log window: No controller connected.")
            return
        print("[ROOT_GUI] Opening controller log window...")

        self.controller_log_window = tk.Toplevel(self.root)
        self.controller_log_window.title("Controller Log")
        self.controller_log_text = tk.Text(self.controller_log_window, height=15, width=50, state='disabled', wrap='word')
        self.controller_log_text.pack(padx=5, pady=5)
        
        self.controller_log_window.protocol("WM_DELETE_WINDOW", self.close_controller_log_window) 

    def close_controller_log_window(self):
        print("[ROOT_GUI] Controller log window closing...")
        if self.controller_log_window:
            self.controller_log_window.destroy()

        self.controller_log_window = None
        self.controller_log_text = None

    def controller_log_print(self, message: str):
        if self.controller_log_text:
            self.controller_log_text.config(state='normal')
            self.controller_log_text.insert(tk.END, message + "\n")
            self.controller_log_text.see(tk.END)
            self.controller_log_text.config(state='disabled')
