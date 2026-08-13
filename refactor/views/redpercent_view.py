import tkinter as tk
from tkinter import ttk

class RedPercentView:
    def __init__(self, master):
        self.master = master
        self.master.title("Screen Color Detector (Red Only)")
        self.master.geometry("350x250")
        
        # GUI Setup
        control_frame = ttk.Frame(self.master)
        control_frame.pack(pady=10)

        self.select_btn = ttk.Button(control_frame, text="Select Focus Area")
        self.select_btn.pack(side=tk.LEFT, padx=5)

        self.start_btn = ttk.Button(control_frame, text="Start Monitoring", state=tk.DISABLED)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(control_frame, text="Stop Monitoring", state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        status_frame = ttk.Frame(self.master)
        status_frame.pack(pady=10)

        ttk.Label(status_frame, text="Focus Area:").grid(row=0, column=0, sticky=tk.W)
        self.area_label = ttk.Label(status_frame, text="Not selected")
        self.area_label.grid(row=0, column=1, sticky=tk.W)

        color_frame = ttk.LabelFrame(self.master, text="Red Detection")
        color_frame.pack(pady=10, padx=10, fill=tk.X)

        ttk.Label(color_frame, text="Red %:").grid(row=0, column=0, sticky=tk.W)
        self.red_label = ttk.Label(color_frame, text="0.0%")
        self.red_label.grid(row=0, column=1, sticky=tk.W)

        ttk.Label(color_frame, text="Red Change:").grid(row=1, column=0, sticky=tk.W)
        self.red_change_label = tk.Label(color_frame, text="0.0%", fg="black")
        self.red_change_label.grid(row=1, column=1, sticky=tk.W)

        self.reset_btn = ttk.Button(color_frame, text="Reset Baseline", state=tk.DISABLED)
        self.reset_btn.grid(row=2, column=0, columnspan=2, pady=5)

        self.save_btn = ttk.Button(color_frame, text="Save Log")
        self.save_btn.grid(row=3, column=0, columnspan=2, pady=5)

        info_frame = ttk.LabelFrame(self.master, text="Serial Output")
        info_frame.pack(pady=5, padx=10, fill=tk.X)
        ttk.Label(info_frame, text="Red % values are printed to console",
                  font=('Arial', 8)).pack()
