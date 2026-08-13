import tkinter as tk

class RotatorModel:
    def __init__(self, default_port="COM1"):
        self.port = tk.StringVar(value=default_port)
        self.smc_id = tk.StringVar(value="1")
        
        self.position = tk.StringVar(value="--.-- deg")
        self.state = tk.StringVar(value="Disconnected")
        self.error = tk.StringVar(value="0")
        
        self.target_abs = tk.StringVar(value="0.0")
        self.step_rel = tk.StringVar(value="1.0")
        
        self.smc = None
        self.is_connected = False
