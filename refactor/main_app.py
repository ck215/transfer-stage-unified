import tkinter as tk
from tkinter import ttk, messagebox
import sys
import os
import serial
import re
import time

from hardware.serial_comm import SerialArduino
from hardware.gamepad import ControllerPoller

from domain_models.probes import StepperProbe, DCProbe, ChuckPositioner
from domain_models.temperature_system import TemperatureSystem
from domain_models.rotator_system import RotatorSystem
from domain_models.redpercent_system import RedPercentSystem

from ui_views.probe_view import ProbeView
from ui_views.temp_view import TempView
from ui_views.rotator_view import RotatorView
from ui_views.redpercent_view import RedPercentView

# Try to import pyserial for real hardware detection.
try:
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# Try to import pygame for physical joystick/gamepad detection.
try:
    import pygame
    pygame.init()
    pygame.joystick.init()
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

class SetupWindow(tk.Tk):
    """Configuration interface to select systems and assign COM ports & physical joysticks."""
    def __init__(self):
        super().__init__()
        self.title("Device Configuration Setup")
        self.geometry("750x520")
        self.resizable(False, False)
        
        self.devices = ["Stepper Probe", "DC Probe", "Chuck Positioner", "Temperature Controller", "SMC100 Rotator", "Red Percent Window"]
        self.device_vars = {}        
        self.port_vars = {}          
        self.controller_vars = {}    
        
        self.dropdown_widgets = {}   
        self.controller_widgets = {} 
        
        self.detected_ports = []
        self.detected_controllers = []
        
        self.active_claims = {}
        
        self.get_available_ports()
        self.get_available_controllers()
        self.create_widgets()

    def get_available_ports(self):
        if SERIAL_AVAILABLE:
            ports = [port.device for port in serial.tools.list_ports.comports()]
            self.detected_ports = sorted(ports)
        else:
            self.detected_ports = []
            
        if not self.detected_ports:
            self.detected_ports = ["COM1", "COM2", "COM3", "COM4"]

    def get_available_controllers(self):
        self.detected_controllers = ["None"]
        if PYGAME_AVAILABLE:
            pygame.event.pump()
            joystick_count = pygame.joystick.get_count()
            for i in range(joystick_count):
                try:
                    js = pygame.joystick.Joystick(i)
                    name = f"ID {i}: {js.get_name()}"
                    self.detected_controllers.append(name)
                except Exception:
                    pass
                    
        if not self.detected_controllers:
            self.detected_controllers = ["None", "Virtual Controller A", "Virtual Controller B"]

    def refresh_devices(self):
        self.get_available_ports()
        self.get_available_controllers()
        
        for device in self.devices:
            # Refresh Serial Dropdowns
            menu = self.dropdown_widgets[device]["menu"]
            menu.delete(0, "end")
            for port in self.detected_ports:
                menu.add_command(label=port, command=lambda p=port, d=device: self.port_vars[d].set(p))
            if self.port_vars[device].get() not in self.detected_ports:
                self.port_vars[device].set(self.detected_ports[0])
                
            # Refresh Controller Dropdowns
            ctrl_menu = self.controller_widgets[device]["menu"]
            ctrl_menu.delete(0, "end")
            for ctrl in self.detected_controllers:
                ctrl_menu.add_command(label=ctrl, command=lambda c=ctrl, d=device: self.controller_vars[d].set(c))
            if self.controller_vars[device].get() not in self.detected_controllers:
                self.controller_vars[device].set(self.detected_controllers[0])
                
    def toggle_dropdown_state(self, device):
        is_checked = self.device_vars[device].get()
        serial_widget = self.dropdown_widgets[device]
        ctrl_widget = self.controller_widgets[device]
        
        if is_checked:
            if ((device != "Temperature Controller") & (device != "SMC100 Rotator") & (device != "Red Percent Window")):
                ctrl_widget.state(["!disabled"])
            if device != "Red Percent Window":
                serial_widget.state(["!disabled"])
        else:
            serial_widget.state(["disabled"])
            ctrl_widget.state(["disabled"])

    def create_widgets(self):
        header = ttk.Label(self, text="System Hardware Configuration", font=("Helvetica", 14, "bold"))
        header.pack(pady=15)
        
        grid_frame = ttk.LabelFrame(self, text="Configure Devices", padding="15")
        grid_frame.pack(fill="x", padx=20, pady=5)
        
        ttk.Label(grid_frame, text="Active Device", font=("Helvetica", 10, "bold")).grid(row=0, column=0, padx=10, pady=5, sticky="w")
        ttk.Label(grid_frame, text="Port Assignment", font=("Helvetica", 10, "bold")).grid(row=0, column=1, padx=10, pady=5, sticky="w")
        ttk.Label(grid_frame, text="Controller", font=("Helvetica", 10, "bold")).grid(row=0, column=2, padx=10, pady=5, sticky="w")
        
        grid_frame.columnconfigure(1, weight=1)
        grid_frame.columnconfigure(2, weight=1)

        for idx, device in enumerate(self.devices):
            check_var = tk.BooleanVar(value=False)
            self.device_vars[device] = check_var
            
            chk = ttk.Checkbutton(grid_frame, text=device, variable=check_var, 
                                  command=lambda d=device: self.toggle_dropdown_state(d))
            chk.grid(row=idx+1, column=0, padx=10, pady=10, sticky="w")

            port_var = tk.StringVar(value=self.detected_ports[0] if self.detected_ports else "COM1")
            self.port_vars[device] = port_var
            
            dropdown = ttk.OptionMenu(grid_frame, port_var, port_var.get(), *self.detected_ports if self.detected_ports else ["COM1"])
            dropdown.grid(row=idx+1, column=1, padx=10, pady=10, sticky="ew")
            dropdown.state(["disabled"])
            self.dropdown_widgets[device] = dropdown
            
            ctrl_var = tk.StringVar(value=self.detected_controllers[0] if self.detected_controllers else "None")
            self.controller_vars[device] = ctrl_var
            
            ctrl_dropdown = ttk.OptionMenu(grid_frame, ctrl_var, ctrl_var.get(), *self.detected_controllers if self.detected_controllers else ["None"])
            ctrl_dropdown.grid(row=idx+1, column=2, padx=10, pady=10, sticky="ew")
            ctrl_dropdown.state(["disabled"])
            self.controller_widgets[device] = ctrl_dropdown
            
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=20)
        
        refresh_btn = ttk.Button(btn_frame, text="🔄 Refresh Devices", command=self.refresh_devices)
        refresh_btn.pack(side="left", padx=10)
        
        launch_btn = ttk.Button(btn_frame, text="🚀 Launch Unified Application", command=self.launch_unified)
        launch_btn.pack(side="left", padx=10)

    def launch_unified(self):
        active_configs = []
        assigned_ports = set()
        assigned_controllers = set()
        
        for device in self.devices:
            if self.device_vars[device].get():
                port = self.port_vars[device].get()
                controller = self.controller_vars[device].get()

                active_configs.append({
                    "device": device, 
                    "port": port, 
                    "controller": controller
                })
                if device != "Red Percent Window":
                    assigned_ports.add(port)
                
                if "None" not in controller and "Virtual" not in controller and device != "Red Percent Window":
                    assigned_controllers.add(controller)
                
        if not active_configs:
            messagebox.showwarning("No Devices Selected", "Please select at least one device to launch.")
            return
            
        devices_needing_ports = [c for c in active_configs if c["device"] != "Red Percent Window"]
        if len(assigned_ports) < len(devices_needing_ports):
            messagebox.showerror("Port Collision", "Error: You cannot assign the same COM port to multiple active devices!")
            return
            
        physical_configs = [c for c in active_configs if "None" not in c["controller"] and "Virtual" not in c["controller"] and c["device"] != "Red Percent Window"]
        if len(assigned_controllers) < len(physical_configs):
            messagebox.showerror("Controller Collision", "Error: You cannot map the same physical controller to multiple active devices!")
            return
            
        # Hide the setup window launcher panel
        self.withdraw()
        
        # We will keep a reference to instantiated UI frames and Domain Models
        self.domain_models = {}
        self.views = {}
        self.pollers = []

        # Create a unified Dashboard Notebook
        self.dashboard_window = tk.Toplevel(self)
        self.dashboard_window.title("Transfer Stage Unified Control")
        self.dashboard_window.geometry("800x600")
        self.dashboard_window.protocol("WM_DELETE_WINDOW", self.shutdown)

        self.notebook = ttk.Notebook(self.dashboard_window)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)

        for config in active_configs:
            device = config["device"]
            port = config["port"]
            controllerID = config["controller"]

            self.active_claims[device] = controllerID
            
            # 1. Instantiate Hardware Transceivers
            if device != "Red Percent Window":
                serial_conn = SerialArduino(port=port)
            else:
                serial_conn = None

            # Instantiate physical controller poller if applicable
            if "None" not in controllerID and "Virtual" not in controllerID:
                poller = ControllerPoller(controllerID, self.active_claims, device)
                self.pollers.append(poller)
            else:
                poller = None

            # 2. Instantiate Domain Model & 3. UI View
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=device)

            if device == "Stepper Probe":
                model = StepperProbe(serial_conn)
                view = ProbeView(frame, model)
                view.pack(fill='both', expand=True)
            elif device == "DC Probe":
                model = DCProbe(serial_conn)
                view = ProbeView(frame, model)
                view.pack(fill='both', expand=True)
            elif device == "Chuck Positioner":
                model = ChuckPositioner(serial_conn)
                view = ProbeView(frame, model)
                view.pack(fill='both', expand=True)
            elif device == "Temperature Controller":
                model = TemperatureSystem(serial_conn)
                view = TempView(frame, model)
                view.pack(fill='both', expand=True)
            elif device == "SMC100 Rotator":
                model = RotatorSystem(port)
                view = RotatorView(frame, model)
                view.pack(fill='both', expand=True)
            elif device == "Red Percent Window":
                model = RedPercentSystem()
                view = RedPercentView(frame, model)
                view.pack(fill='both', expand=True)
                
            # If the device has a controller, hook up the manual polling loop
            # Note: For strict OOP, the poller logic can either push to the model, or the model can poll. 
            # ControllerDrive historically calls callback to push updates.
            if poller:
                # Provide dummy log updater to avoid crash
                poller.start_polling(self.dashboard_window, log_updater=print, activity_callback=None)
                # Let's write a generic loop to route controller state into the model
                def _route_input(m=model, p=poller):
                    if hasattr(m, 'send_manual_mode_command') and m.manual_flag:
                        controller_params = p.get_mapped_state()
                        m.send_manual_mode_command(controller_params)
                    self.dashboard_window.after(50, _route_input)
                self.dashboard_window.after(50, _route_input)
                
            # For probes, setup continuous position polling
            if hasattr(model, 'read_position'):
                def _poll_pos(m=model):
                    m.read_position()
                    self.dashboard_window.after(100, _poll_pos)
                self.dashboard_window.after(100, _poll_pos)

    def shutdown(self):
        for poller in self.pollers:
            poller.stop_polling()
            poller.close()
        self.destroy()

if __name__ == "__main__":
    app = SetupWindow()
    app.mainloop()
