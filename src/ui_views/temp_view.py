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
        # Center the content horizontally and vertically by giving weight to outer edges
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(3, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(9, weight=1)
        
        inner_frame = tk.Frame(self)
        inner_frame.grid(row=1, column=1, pady=20, padx=20)
        
        Label(inner_frame, text="--- Controls ---", font=('Arial', 10, 'bold')).grid(row=0, column=0, columnspan=2, pady=(0, 10))
        Label(inner_frame, text="Current Temperature:").grid(row=7, column=0, sticky="W", padx=5, pady=(10, 0))
        
        fields = [
            ("Set Temperature", self.setpoint_var),
            ("Ramping Rate", self.ramp_rate_var),
            ("P Term", self.p_term_var),
            ("I Term", self.i_term_var),
            ("D Term", self.d_term_var),
            ("Temperature Offset", self.offset_var)
        ]
        
        for i, (text, var) in enumerate(fields, start=1):
            Label(inner_frame, text=text).grid(row=i, column=0, sticky="W", padx=5)
            entry = Entry(inner_frame, textvariable=var)
            entry.grid(row=i, column=1, padx=5, pady=2)
            
        self.temp_display_label = Label(inner_frame, textvariable=self.current_temp_var, font=("Arial", 12, "bold"), fg="darkred")
        self.temp_display_label.grid(row=7, column=1, sticky="W", padx=5, pady=(10, 0))
        
        btn_frame = tk.Frame(inner_frame)
        btn_frame.grid(row=8, column=0, columnspan=2, pady=15)
        
        self.btn_enter = Button(btn_frame, text='Enter', command=self.on_enter, width=10, bg='darkgreen', fg='black')
        self.btn_enter.pack(side='left', padx=10)
        
        self.btn_quit = Button(btn_frame, text='Stop System', command=self.on_quit, width=10, bg='darkred', fg='black')
        self.btn_quit.pack(side='left', padx=10)
        
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
