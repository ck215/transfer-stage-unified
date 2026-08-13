import tkinter as tk
from tkinter import Label, Button, Entry

class TempView(tk.Frame):
    def __init__(self, parent, model, **kwargs):
        super().__init__(parent, **kwargs)
        self.model = model
        
        self.setpoint_var = tk.StringVar(value=self.model.setpoint)
        self.ramp_rate_var = tk.StringVar(value=self.model.ramp_rate)
        self.p_term_var = tk.StringVar(value=self.model.p_term)
        self.i_term_var = tk.StringVar(value=self.model.i_term)
        self.d_term_var = tk.StringVar(value=self.model.d_term)
        self.offset_var = tk.StringVar(value=self.model.offset)
        
        self.current_temp_var = tk.StringVar(value=self.model.current_temp)
        
        self._build_ui()
        self._poll_model()
        
    def _build_ui(self):
        Label(self, text="Controls:").grid(row=0, column=0, columnspan=2, pady=5)
        Label(self, text="Current Temperature:").grid(row=7, column=0, sticky="W", padx=5)
        
        fields = [
            ("Set Temperature", self.setpoint_var),
            ("Ramping Rate", self.ramp_rate_var),
            ("P Term", self.p_term_var),
            ("I Term", self.i_term_var),
            ("D Term", self.d_term_var),
            ("Temperature Offset", self.offset_var)
        ]
        
        for i, (text, var) in enumerate(fields, start=1):
            Label(self, text=text).grid(row=i, column=0, sticky="W", padx=5)
            entry = Entry(self, textvariable=var)
            entry.grid(row=i, column=1, padx=5, pady=2)
            
        self.temp_display_label = Label(self, textvariable=self.current_temp_var, font=("Arial", 10, "bold"))
        self.temp_display_label.grid(row=7, column=1, sticky="W", padx=5)
        
        self.btn_enter = Button(self, text='Enter', command=self.on_enter, width=10)
        self.btn_enter.grid(row=8, column=0, pady=10)
        
        self.btn_quit = Button(self, text='Quit', command=self.on_quit, width=10)
        self.btn_quit.grid(row=8, column=1, pady=10)
        
    def on_enter(self):
        self.model.send_settings(
            self.setpoint_var.get(),
            self.ramp_rate_var.get(),
            self.p_term_var.get(),
            self.i_term_var.get(),
            self.d_term_var.get(),
            self.offset_var.get()
        )
        
    def on_quit(self):
        self.model.stop()
        self.winfo_toplevel().destroy()
        
    def _poll_model(self):
        # Sync StringVars from model by polling
        if self.current_temp_var.get() != self.model.current_temp:
            self.current_temp_var.set(self.model.current_temp)
            
        self.after(50, self._poll_model)
