import tkinter as tk

class TempModel:
    def __init__(self):
        self.setpoint = tk.StringVar(value="0")
        self.ramp_rate = tk.StringVar(value="10")
        self.p_term = tk.StringVar(value="2.0")
        self.i_term = tk.StringVar(value="0.5")
        self.d_term = tk.StringVar(value=".1")
        self.offset = tk.StringVar(value="0")
        
        self.current_temp = tk.StringVar(value="N/A")
        
        self.tempC = []
        self.time = []
        self.sp = []
        self.cnt = 0
