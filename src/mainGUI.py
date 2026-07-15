import tkinter as tk
from tkinter import ttk, messagebox
import sys
import multiprocessing

# Import your external device modules
import stepper_frame
import DC_frame
import chuck_frame

# create and start threads

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


# ==========================================
# THE SETUP / ASSIGNMENT WINDOW
# ==========================================

class SetupWindow(tk.Tk):
    """Configuration interface to select systems and assign COM ports & physical joysticks."""
    def __init__(self):
        super().__init__()
        self.title("Device Configuration Setup")
        self.geometry("750x420")
        self.resizable(False, False)
        
        self.devices = ["Stepper Probe", "DC Probe", "Chuck"]
        self.device_vars = {}        
        self.port_vars = {}          
        self.controller_vars = {}    
        
        self.dropdown_widgets = {}   
        self.controller_widgets = {} 
        
        self.detected_ports = []
        self.detected_controllers = []
        
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
        self.detected_controllers = []
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
            self.detected_controllers = ["None Detected", "Virtual Controller A", "Virtual Controller B"]

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
            serial_widget.state(["!disabled"])
            ctrl_widget.state(["!disabled"])
        else:
            serial_widget.state(["disabled"])
            ctrl_widget.state(["disabled"])

    def create_widgets(self):
        header = ttk.Label(self, text="System Hardware Configuration", font=("Helvetica", 14, "bold"))
        header.pack(pady=15)
        
        grid_frame = ttk.LabelFrame(self, text="Configure Devices", padding="15")
        grid_frame.pack(fill="x", padx=20, pady=5)
        
        ttk.Label(grid_frame, text="Active Device", font=("Helvetica", 10, "bold")).grid(row=0, column=0, padx=10, pady=5, sticky="w")
        ttk.Label(grid_frame, text="Serial COM Assignment", font=("Helvetica", 10, "bold")).grid(row=0, column=1, padx=10, pady=5, sticky="w")
        ttk.Label(grid_frame, text="Joystick / Gamepad Controller", font=("Helvetica", 10, "bold")).grid(row=0, column=2, padx=10, pady=5, sticky="w")
        
        grid_frame.columnconfigure(1, weight=1)
        grid_frame.columnconfigure(2, weight=1)
        
        for idx, device in enumerate(self.devices):
            check_var = tk.BooleanVar(value=False)
            self.device_vars[device] = check_var
            
            chk = ttk.Checkbutton(grid_frame, text=device, variable=check_var, 
                                  command=lambda d=device: self.toggle_dropdown_state(d))
            chk.grid(row=idx+1, column=0, padx=10, pady=10, sticky="w")
            
            port_var = tk.StringVar(value=self.detected_ports[0])
            self.port_vars[device] = port_var
            
            dropdown = ttk.OptionMenu(grid_frame, port_var, self.detected_ports[0], *self.detected_ports)
            dropdown.grid(row=idx+1, column=1, padx=10, pady=10, sticky="ew")
            dropdown.state(["disabled"])
            self.dropdown_widgets[device] = dropdown
            
            ctrl_var = tk.StringVar(value=self.detected_controllers[0])
            self.controller_vars[device] = ctrl_var
            
            ctrl_dropdown = ttk.OptionMenu(grid_frame, ctrl_var, self.detected_controllers[0], *self.detected_controllers)
            ctrl_dropdown.grid(row=idx+1, column=2, padx=10, pady=10, sticky="ew")
            ctrl_dropdown.state(["disabled"])
            self.controller_widgets[device] = ctrl_dropdown
            
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=20)
        
        refresh_btn = ttk.Button(btn_frame, text="🔄 Refresh Devices", command=self.refresh_devices)
        refresh_btn.pack(side="left", padx=10)
        
        launch_btn = ttk.Button(btn_frame, text="🚀 Launch Controllers", command=self.launch_modules)
        launch_btn.pack(side="left", padx=10)
        
    def launch_modules(self):
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
                assigned_ports.add(port)
                
                if "None Detected" not in controller and "Virtual" not in controller:
                    assigned_controllers.add(controller)
                
        if not active_configs:
            messagebox.showwarning("No Devices Selected", "Please select at least one device to launch.")
            return
            
        if len(assigned_ports) < len(active_configs):
            messagebox.showerror("Port Collision", "Error: You cannot assign the same COM port to multiple active devices!")
            return
            
        physical_configs = [c for c in active_configs if "None Detected" not in c["controller"] and "Virtual" not in c["controller"]]
        if len(assigned_controllers) < len(physical_configs):
            messagebox.showerror("Controller Collision", "Error: You cannot map the same physical controller to multiple active devices!")
            return
            
        # Hide the setup window launcher panel
        self.withdraw()
        
        # Call the respective main(port, controller) functions directly
        for config in active_configs:
            device = config["device"]
            port = config["port"]
            controllerID = config["controller"]
            
            if device == "Stepper Probe":
                stepper_process = multiprocessing.Process(target=stepper_frame.main, args=(port,controllerID))
                stepper_process.start()
            elif device == "DC Probe":
                DC_process = multiprocessing.Process(target=DC_frame.main, args=(port,controllerID))
                DC_process.start()
            elif device == "Chuck":
                chuck_process = multiprocessing.Process(target=chuck_frame.main, args=(port,controllerID))
                chuck_process.start()
        
        try:
            stepper_process.join()
        except:
            None
        try:
            DC_process.join()
        except:
            None
        try:
            chuck_process.join()
        except:
            None


if __name__ == "__main__":
    app = SetupWindow()
    app.mainloop()