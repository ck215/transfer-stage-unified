import tkinter as tk
from tkinter import filedialog
from tkinter import ttk

class ProbeView(tk.Frame):
    def __init__(self, parent, probe_model):
        super().__init__(parent)
        self.model = probe_model
        
        # Colors from original styling
        self.bg_main = 'black'
        self.fg_accent = 'white'
        self.configure(bg=self.bg_main)
        
        # Create StringVars
        self.pos_x = tk.StringVar(value=self.model.pos_x)
        self.pos_y = tk.StringVar(value=self.model.pos_y)
        self.pos_z = tk.StringVar(value=self.model.pos_z)
        
        self.x_step = tk.StringVar(value=self.model.x_step)
        self.y_step = tk.StringVar(value=self.model.y_step)
        self.z_step = tk.StringVar(value=self.model.z_step)
        
        self.full_speed = tk.StringVar(value=self.model.full_speed)
        self.man_full_speed = tk.StringVar(value=self.model.man_full_speed)
        
        self.slow_speed = None
        self.brake_distance = None
        if hasattr(self.model, 'slow_speed'):
            self.slow_speed = tk.StringVar(value=self.model.slow_speed)
            self.brake_distance = tk.StringVar(value=self.model.brake_distance)
            
        self.selected_script = None
        
        self._build_ui()
        self._poll_model()

    def _build_ui(self):
        row_counter = 0
        
        tk.Label(self, text="--- Coordinate Frame ---", font=('Arial', 10, 'bold'), bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        
        # Positions
        tk.Label(self, text="X Position:", bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Entry(self, textvariable=self.pos_x, state='readonly').grid(row=row_counter, column=1, padx=5, pady=2); row_counter += 1
        
        tk.Label(self, text="Y Position:", bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Entry(self, textvariable=self.pos_y, state='readonly').grid(row=row_counter, column=1, padx=5, pady=2); row_counter += 1
        
        tk.Label(self, text="Z Position:", bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Entry(self, textvariable=self.pos_z, state='readonly').grid(row=row_counter, column=1, padx=5, pady=2); row_counter += 1
        
        # Steps
        tk.Label(self, text="X Steps:", bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Entry(self, textvariable=self.x_step).grid(row=row_counter, column=1, padx=5, pady=2)
        self.x_step.trace_add("write", lambda *args: setattr(self.model, 'x_step', self.x_step.get()))
        row_counter += 1
        
        tk.Label(self, text="Y Steps:", bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Entry(self, textvariable=self.y_step).grid(row=row_counter, column=1, padx=5, pady=2)
        self.y_step.trace_add("write", lambda *args: setattr(self.model, 'y_step', self.y_step.get()))
        row_counter += 1
        
        tk.Label(self, text="Z Steps:", bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Entry(self, textvariable=self.z_step).grid(row=row_counter, column=1, padx=5, pady=2)
        self.z_step.trace_add("write", lambda *args: setattr(self.model, 'z_step', self.z_step.get()))
        row_counter += 1
        
        # Script
        tk.Label(self, text="Script:", bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        file_frame = tk.Frame(self, bg=self.bg_main)
        file_frame.grid(row=row_counter, column=1, padx=5, pady=3, sticky='w')
        tk.Button(file_frame, text="Select", width=5, command=self.select_script).grid(row=0, column=0, padx=(13,0), pady=2)
        self.selected_script_label = tk.Label(file_frame, text="None", width=10, bg=self.bg_main, fg='white')
        self.selected_script_label.grid(row=0, column=1, padx=2, pady=2, sticky='w')
        row_counter += 1

        # Velocity Control
        tk.Label(self, text="--- Velocity Control ---", font=('Arial', 10, 'bold'), bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        
        tk.Label(self, text="Full Speed (Microsteps/Sec):", bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Entry(self, textvariable=self.full_speed).grid(row=row_counter, column=1, padx=5, pady=2)
        self.full_speed.trace_add("write", lambda *args: setattr(self.model, 'full_speed', self.full_speed.get()))
        row_counter += 1
        
        tk.Label(self, text="Manual Mode Max Speed (Microsteps/Sec):", bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
        tk.Entry(self, textvariable=self.man_full_speed).grid(row=row_counter, column=1, padx=5, pady=2)
        self.man_full_speed.trace_add("write", lambda *args: setattr(self.model, 'man_full_speed', self.man_full_speed.get()))
        row_counter += 1

        if self.slow_speed and self.brake_distance:
            tk.Label(self, text="Slow Speed (Microsteps/Sec):", bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
            tk.Entry(self, textvariable=self.slow_speed).grid(row=row_counter, column=1, padx=5, pady=2)
            self.slow_speed.trace_add("write", lambda *args: setattr(self.model, 'slow_speed', self.slow_speed.get()))
            row_counter += 1
            
            tk.Label(self, text="Brake Distance (Microsteps):", bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
            tk.Entry(self, textvariable=self.brake_distance).grid(row=row_counter, column=1, padx=5, pady=2)
            self.brake_distance.trace_add("write", lambda *args: setattr(self.model, 'brake_distance', self.brake_distance.get()))
            row_counter += 1

        # Motor Commands
        tk.Label(self, text="--- Motor Commands ---", bg=self.bg_main, fg=self.fg_accent, font=('Arial', 10, 'bold')).grid(row=row_counter, column=0, columnspan=2, pady=5); row_counter += 1
        
        tk.Button(self, text="ENTER AUTONOMOUS MODE", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.enter_auton).grid(row=row_counter, column=0, padx=5, pady=5, sticky='ew')
        tk.Button(self, text="ENTER MANUAL MODE", bg='blue', fg='black', font=('Arial', 10, 'bold'), command=self.enter_manual).grid(row=row_counter, column=1, padx=5, pady=5, sticky='ew'); row_counter += 1

        tk.Button(self, text="Enable System", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.model.enable).grid(row=row_counter, column=0, padx=5, pady=5, sticky='ew')
        tk.Button(self, text="Disable System", bg='darkred', fg='black', font=('Arial', 10, 'bold'), command=self.model.disable).grid(row=row_counter, column=1, padx=5, pady=5, sticky='ew'); row_counter += 1
        
        tk.Button(self, text="Start Stepping", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.start_stepping).grid(row=row_counter, column=0, columnspan=2, padx=5, pady=5, sticky='ew'); row_counter += 1
        
        tk.Button(self, text="Run Script", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.run_script).grid(row=row_counter, column=0, columnspan=2, padx=5, pady=5, sticky='ew'); row_counter += 1
        
        tk.Button(self, text="Full Stop", bg='darkgreen', fg='black', font=('Arial', 10, 'bold'), command=self.model.full_stop).grid(row=row_counter, column=0, columnspan=2, padx=5, pady=5, sticky='ew'); row_counter += 1
        
    def select_script(self):
        filepath = filedialog.askopenfilename(title="Select Script File", filetypes=[("Text files", "*.txt")])
        if filepath:
            self.selected_script = filepath
            filename = filepath.split('/')[-1]
            self.selected_script_label.config(text=filename)
            if hasattr(self.model, 'read_script'):
                self.model.read_script(self.selected_script)

    def enter_auton(self):
        self.model.auton_flag = True
        self.model.manual_flag = False

    def enter_manual(self):
        self.model.auton_flag = False
        self.model.manual_flag = True

    def start_stepping(self):
        self.enter_auton()
        self.model.send_autonomous_command()
        
    def run_script(self):
        self.enter_auton()
        if hasattr(self.model, 'run_script') and self.selected_script:
            self.model.run_script(self.selected_script)

    def _poll_model(self):
        # Update UI variables from model if changed elsewhere (e.g. serial responses)
        if self.pos_x.get() != str(self.model.pos_x):
            self.pos_x.set(str(self.model.pos_x))
        if self.pos_y.get() != str(self.model.pos_y):
            self.pos_y.set(str(self.model.pos_y))
        if self.pos_z.get() != str(self.model.pos_z):
            self.pos_z.set(str(self.model.pos_z))
        
        self.after(100, self._poll_model)
