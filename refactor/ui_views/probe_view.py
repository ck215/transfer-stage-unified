import tkinter as tk

class ProbeView(tk.Frame):
    def __init__(self, parent, probe_model):
        super().__init__(parent)
        self.model = probe_model
        
        # Create StringVars
        self.pos_x = tk.StringVar(value=self.model.pos_x)
        self.pos_y = tk.StringVar(value=self.model.pos_y)
        self.pos_z = tk.StringVar(value=self.model.pos_z)
        
        self.x_step = tk.StringVar(value=self.model.x_step)
        self.y_step = tk.StringVar(value=self.model.y_step)
        self.z_step = tk.StringVar(value=self.model.z_step)
        
        self.x_dist = tk.StringVar(value=self.model.x_dist)
        self.y_dist = tk.StringVar(value=self.model.y_dist)
        self.z_dist = tk.StringVar(value=self.model.z_dist)
        
        self.full_speed = tk.StringVar(value=self.model.full_speed)
        self.man_full_speed = tk.StringVar(value=self.model.man_full_speed)
        
        if hasattr(self.model, 'slow_speed'):
            self.slow_speed = tk.StringVar(value=self.model.slow_speed)
            self.brake_distance = tk.StringVar(value=self.model.brake_distance)
        
        self._build_ui()
        self._poll_model()

    def _build_ui(self):
        tk.Label(self, text="X Position:").grid(row=0, column=0)
        tk.Entry(self, textvariable=self.pos_x, state='readonly').grid(row=0, column=1)
        
        tk.Label(self, text="Y Position:").grid(row=1, column=0)
        tk.Entry(self, textvariable=self.pos_y, state='readonly').grid(row=1, column=1)
        
        tk.Label(self, text="Z Position:").grid(row=2, column=0)
        tk.Entry(self, textvariable=self.pos_z, state='readonly').grid(row=2, column=1)
        
        tk.Label(self, text="X Step:").grid(row=3, column=0)
        tk.Entry(self, textvariable=self.x_step).grid(row=3, column=1)
        self.x_step.trace_add("write", lambda *args: setattr(self.model, 'x_step', self.x_step.get()))
        
        tk.Label(self, text="Y Step:").grid(row=4, column=0)
        tk.Entry(self, textvariable=self.y_step).grid(row=4, column=1)
        self.y_step.trace_add("write", lambda *args: setattr(self.model, 'y_step', self.y_step.get()))
        
        tk.Label(self, text="Z Step:").grid(row=5, column=0)
        tk.Entry(self, textvariable=self.z_step).grid(row=5, column=1)
        self.z_step.trace_add("write", lambda *args: setattr(self.model, 'z_step', self.z_step.get()))
        
        # Add buttons
        self.enable_btn = tk.Button(self, text="Enable", command=self.model.enable)
        self.enable_btn.grid(row=6, column=0)
        
        self.disable_btn = tk.Button(self, text="Disable", command=self.model.disable)
        self.disable_btn.grid(row=6, column=1)
        
        self.auton_btn = tk.Button(self, text="Start Stepping", command=self.start_stepping)
        self.auton_btn.grid(row=7, column=0)

        self.stop_btn = tk.Button(self, text="Full Stop", command=self.model.full_stop)
        self.stop_btn.grid(row=7, column=1)

    def start_stepping(self):
        self.model.auton_flag = True
        self.model.manual_flag = False
        self.model.send_autonomous_command()

    def _poll_model(self):
        # Update UI variables from model if changed elsewhere
        if self.pos_x.get() != str(self.model.pos_x):
            self.pos_x.set(str(self.model.pos_x))
        if self.pos_y.get() != str(self.model.pos_y):
            self.pos_y.set(str(self.model.pos_y))
        if self.pos_z.get() != str(self.model.pos_z):
            self.pos_z.set(str(self.model.pos_z))
        
        self.after(100, self._poll_model)
