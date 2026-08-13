import tkinter as tk

class DCModel:
    def __init__(self):
        # Position variables
        self.pos_x = tk.StringVar(value="0")
        self.pos_y = tk.StringVar(value="0")
        self.pos_z = tk.StringVar(value="0")

        # Connection variables
        self.controller_var = tk.StringVar(value="None")
        self.serial_port = tk.StringVar(value="/dev/ttys00X")
        
        # Step sizes
        self.x_step = tk.StringVar(value="1")
        self.y_step = tk.StringVar(value="1")
        self.z_step = tk.StringVar(value="1")

        # Step counts (distances)
        self.x_dist = tk.StringVar(value="0")
        self.y_dist = tk.StringVar(value="0")
        self.z_dist = tk.StringVar(value="0")

        # Velocity control
        self.full_speed = tk.StringVar(value="120")
        self.slow_speed = tk.StringVar(value="0")
        self.brake_distance = tk.StringVar(value="0")
        self.man_full_speed = tk.StringVar(value="120")

        # State flags
        self.autonFlag = False
        self.manualFlag = False
        
        # Claims (For multi-device coordination)
        self.active_claims = {}
