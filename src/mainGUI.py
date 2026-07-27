import tkinter as tk
from tkinter import ttk, messagebox
import sys
import multiprocessing
from multiprocessing import Manager
import threading
import os
import serial
import re
import time

# Import your external device modules
import stepper_frame
import DC_frame
import chuck_frame
import temp_control

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
        
        self.devices = ["Stepper Probe", "DC Probe", "Chuck Positioner", "Temperature Controller"]
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

        # Initialize multiprocess manager
        self.manager = Manager()
        self.active_claims = self.manager.dict()

        
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
            if (device != "Temperature Controller"):
                ctrl_widget.state(["!disabled"])
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
        ttk.Label(grid_frame, text="Serial COM Assignment", font=("Helvetica", 10, "bold")).grid(row=0, column=1, padx=10, pady=5, sticky="w")
        ttk.Label(grid_frame, text="Joystick / Gamepad Controller", font=("Helvetica", 10, "bold")).grid(row=0, column=2, padx=10, pady=5, sticky="w")
        
        grid_frame.columnconfigure(1, weight=1)
        grid_frame.columnconfigure(2, weight=1)

        # Detect and populate active ports
        found_devices = {}
        DEVICE_MAP = {
            's': "Stepper Probe",
            'd': "DC Probe",
            'c': "Chuck Positioner",
            't': "Temperature Controller"
        }
        DEV_PATTERN = re.compile(r"DEV:\s*([sdct])", re.IGNORECASE)
        for port in self.detected_ports:
            print(f"[mainGUI] Scanning for devices on {port}...")
            try: # check at baud rate 1
                with serial.Serial(port, baudrate=500000, timeout=2.5) as ser:
                    time.sleep(2.0)
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()
                    ser.write(b"s\n")
                    start_time = time.time()
                    device_found = False
                    while (time.time() - start_time < 1.5):
                        response_bytes = ser.readline()
                        response_str = response_bytes.decode('utf-8', errors='ignore').strip()

                        match = DEV_PATTERN.search(response_str)
                        if match:
                            print("[mainGUI] Match on port")
                            code = match.group(1)
                            device_type = DEVICE_MAP.get(code, "unknown")
                            if (device_type != "unknown"):
                                found_devices[device_type] = port
                        time.sleep(0.05)
                    if not device_found:
                        print(f"[mainGUI] No devices found at baud rate 500000, checking 115200")
                        raise Exception
            except:
                try: # check at baud rate 2
                    print("checking baud rate 2")
                    with serial.Serial(port, baudrate=115200, timeout=2.5) as ser:
                        time.sleep(2.0)
                        ser.reset_input_buffer()
                        ser.reset_output_buffer()
                        ser.write(b"s\n")
                        start_time = time.time()
                        device_found = False
                        while (time.time() - start_time < 1.5):
                            response_bytes = ser.readline()
                            response_str = response_bytes.decode('utf-8', errors='ignore').strip()

                            match = DEV_PATTERN.search(response_str)
                            if match:
                                print("[mainGUI] Match on port")
                                code = match.group(1)
                                device_type = DEVICE_MAP.get(code, "unknown")
                                if (device_type != "unknown"):
                                    found_devices[device_type] = port
                            time.sleep(0.05)
                        if not device_found:
                            print(f"[mainGUI] No devices found at baud rate 115200, checking next port")
                            raise Exception
                except:
                    pass

        for idx, device in enumerate(self.devices):
            check_var = tk.BooleanVar(value=False)
            self.device_vars[device] = check_var
            
            chk = ttk.Checkbutton(grid_frame, text=device, variable=check_var, 
                                  command=lambda d=device: self.toggle_dropdown_state(d))
            chk.grid(row=idx+1, column=0, padx=10, pady=10, sticky="w")

            
            # load detected arduinos
            try:
                if (found_devices.get(device)):
                    port_var = tk.StringVar(value=found_devices.get(device))
                else:
                    raise Exception
            except: # use dummy vars
                port_var = tk.StringVar(value=self.detected_ports[0])
            self.port_vars[device] = port_var
            
            
            dropdown = ttk.OptionMenu(grid_frame, port_var, self.detected_ports[self.detected_ports.index(port_var.get())], *self.detected_ports)
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
                
                if "None" not in controller and "Virtual" not in controller:
                    assigned_controllers.add(controller)
                
        if not active_configs:
            messagebox.showwarning("No Devices Selected", "Please select at least one device to launch.")
            return
            
        if len(assigned_ports) < len(active_configs):
            messagebox.showerror("Port Collision", "Error: You cannot assign the same COM port to multiple active devices!")
            return
            
        physical_configs = [c for c in active_configs if "None" not in c["controller"] and "Virtual" not in c["controller"]]
        if len(assigned_controllers) < len(physical_configs):
            messagebox.showerror("Controller Collision", "Error: You cannot map the same physical controller to multiple active devices!")
            return
            
        # Hide the setup window launcher panel
        self.withdraw()

        # Clear any old references
        self.spawned_processes = []
        
        self.active_claims.clear()

        # Call the respective main(port, controller) functions directly, store references
        for config in active_configs:
            device = config["device"]
            port = config["port"]
            controllerID = config["controller"]

            self.active_claims[device] = controllerID
            
            p = None
            if device == "Stepper Probe":
                p = multiprocessing.Process(target=stepper_frame.main, args=(port,controllerID, self.active_claims, "Stepper Probe"))
            elif device == "DC Probe":
                p = multiprocessing.Process(target=DC_frame.main, args=(port,controllerID, self.active_claims, "DC Probe"))
            elif device == "Chuck Positioner":
                p = multiprocessing.Process(target=chuck_frame.main, args=(port,controllerID, self.active_claims, "Chuck Positioner"))
            elif device == "Temperature Controller":
                p = multiprocessing.Process(target=temp_control.main, args=(port,))

            self.spawned_processes.append(p)

            p.start()

        closing_thread = threading.Thread(target=self.close)
        closing_thread.start()
        closing_thread.join()
        self.destroy()
    
    def close(self):
        for p in self.spawned_processes:
            p.join()
            try:
                self.manager.shutdown()
            except Exception:
                pass


if __name__ == "__main__":
    multiprocessing.set_start_method('spawn')
    app = SetupWindow()
    app.mainloop()
