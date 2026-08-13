import tkinter as tk
from tkinter import Label, Button, Entry
from models.temp_model import TempModel

class TempView:
    def __init__(self, root: tk.Tk, model: TempModel, controller):
        self.root = root
        self.model = model
        self.controller = controller
        
        self.root.title("PID Temperature Controller")
        self._build_ui()
        
    def _build_ui(self):
        Label(self.root, text="Controls:").grid(row=0, column=0, columnspan=2, pady=5)
        Label(self.root, text="Current Temperature:").grid(row=7, column=0, sticky="W", padx=5)

        fields = [
            ("Set Temperature", self.model.setpoint),
            ("Ramping Rate", self.model.ramp_rate),
            ("P Term", self.model.p_term),
            ("I Term", self.model.i_term),
            ("D Term", self.model.d_term),
            ("Temperature Offset", self.model.offset)
        ]
        
        for i, (text, var) in enumerate(fields, start=1):
            Label(self.root, text=text).grid(row=i, column=0, sticky="W", padx=5)
            entry = Entry(self.root, textvariable=var)
            entry.grid(row=i, column=1, padx=5, pady=2)

        self.temp_display_label = Label(self.root, textvariable=self.model.current_temp, font=("Arial", 10, "bold"))
        self.temp_display_label.grid(row=7, column=1, sticky="W", padx=5)

        self.btn_enter = Button(self.root, text='Enter', command=self.controller.send_settings, width=10)
        self.btn_enter.grid(row=8, column=0, pady=10)

        self.btn_quit = Button(self.root, text='Quit', command=self.controller.stop_plot, width=10)
        self.btn_quit.grid(row=8, column=1, pady=10)
