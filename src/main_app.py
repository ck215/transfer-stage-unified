import tkinter as tk
from tkinter import ttk, messagebox
import sys
import os

import re
import time
import threading
import queue

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

try:
    import serial.tools.list_ports
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# Try to import pygame for physical joystick/gamepad detection.
try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

class DraggableClosableNotebook(ttk.Notebook):
    """A ttk.Notebook with draggable tabs and middle-click/right-click to close."""
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        
        self.bind("<Button-2>", self.on_middle_click) # Middle click to close on some systems
        self.bind("<Button-3>", self.on_right_click)  # Right click to close
        
        self._active = None
        self.on_close_tab_callback = None

    def on_press(self, event):
        try:
            self._active = self.index(f"@{event.x},{event.y}")
        except tk.TclError:
            self._active = None

    def on_drag(self, event):
        if self._active is None:
            return
        try:
            target = self.index(f"@{event.x},{event.y}")
            if self._active != target:
                self.insert(target, self.tabs()[self._active])
                self._active = target
        except tk.TclError:
            pass

    def on_release(self, event):
        self._active = None

    def on_middle_click(self, event):
        try:
            index = self.index(f"@{event.x},{event.y}")
            self.close_tab(index)
        except tk.TclError:
            pass

    def on_right_click(self, event):
        try:
            index = self.index(f"@{event.x},{event.y}")
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="Close Tab", command=lambda: self.close_tab(index))
            menu.tk_popup(event.x_root, event.y_root)
        except tk.TclError:
            pass

    def close_tab(self, index):
        if self.on_close_tab_callback:
            self.on_close_tab_callback(index)
        else:
            self.forget(index)

class SetupWindow(tk.Tk):
    """Configuration interface to select systems and assign COM ports & physical joysticks."""
    def __init__(self):
        super().__init__()
        self.title("Device Configuration Setup")
        self.geometry("850x650")
        self.resizable(True, True)
        
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
        
        self.is_scanning = False
        self.autodetected_devices = set()
        self.status_labels = {}
        self.gui_queue = queue.Queue()
        self.create_widgets()
        
        self.after(200, self.start_autodetect)
        self.after(50, self.check_queue)

    def check_queue(self):
        try:
            while True:
                msg_type, data = self.gui_queue.get_nowait()
                if msg_type == 'status':
                    self.status_var.set(data)
                elif msg_type == 'progress':
                    self.progress_var.set(data)
                elif msg_type == 'found':
                    device_name, port = data
                    self._update_device_ui(device_name, port)
                elif msg_type == 'complete':
                    self._scan_complete()
        except queue.Empty:
            pass
        self.after(50, self.check_queue)


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
            pygame.init()
            pygame.joystick.init()
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
        if getattr(self, 'is_scanning', False):
            return
            
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
                
        self.start_autodetect(force=True)
                
    def toggle_dropdown_state(self, device):
        is_checked = self.device_vars[device].get()
        serial_widget = self.dropdown_widgets[device]
        ctrl_widget = self.controller_widgets[device]
        
        if is_checked:
            if ((device != "Temperature Controller") & (device != "SMC100 Rotator") & (device != "Red Percent Window")):
                ctrl_widget.state(["!disabled"])
            if device != "Red Percent Window":
                if device not in self.autodetected_devices:
                    serial_widget.state(["!disabled"])
        else:
            serial_widget.state(["disabled"])
            ctrl_widget.state(["disabled"])

    def create_widgets(self):
        header = ttk.Label(self, text="System Hardware Configuration", font=("Helvetica", 14, "bold"))
        header.pack(pady=15)
        
        self.scan_frame = ttk.Frame(self)
        self.scan_frame.pack(fill="x", pady=0)
        
        self.status_var = tk.StringVar(value="Ready to scan...")
        self.status_label = ttk.Label(self.scan_frame, textvariable=self.status_var, font=("Helvetica", 10))
        self.status_label.pack(pady=5)
        
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(self.scan_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill="x", padx=40, pady=5)
        
        grid_frame = ttk.LabelFrame(self, text="Configure Devices", padding="15")
        grid_frame.pack(fill="x", padx=20, pady=5)
        
        ttk.Label(grid_frame, text="Active Device", font=("Helvetica", 10, "bold")).grid(row=0, column=0, padx=10, pady=5, sticky="w")
        ttk.Label(grid_frame, text="Port Assignment", font=("Helvetica", 10, "bold")).grid(row=0, column=1, padx=10, pady=5, sticky="w")
        ttk.Label(grid_frame, text="Controller", font=("Helvetica", 10, "bold")).grid(row=0, column=2, padx=10, pady=5, sticky="w")
        ttk.Label(grid_frame, text="Status", font=("Helvetica", 10, "bold")).grid(row=0, column=3, padx=10, pady=5, sticky="w")
        
        grid_frame.columnconfigure(1, weight=1)
        grid_frame.columnconfigure(2, weight=1)
        grid_frame.columnconfigure(3, weight=1)

        for idx, device in enumerate(self.devices):
            check_var = tk.BooleanVar(value=False)
            self.device_vars[device] = check_var
            
            port_var = tk.StringVar(value=self.detected_ports[0] if self.detected_ports else "None")
            self.port_vars[device] = port_var
            
            chk = ttk.Checkbutton(grid_frame, text=device, variable=check_var, 
                                  command=lambda d=device: self.toggle_dropdown_state(d))
            chk.grid(row=idx+1, column=0, padx=10, pady=10, sticky="w")
            
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
            
            lbl = tk.Label(grid_frame, text="", font=("Helvetica", 10, "bold"))
            lbl.grid(row=idx+1, column=3, padx=10, pady=10, sticky="w")
            self.status_labels[device] = lbl
            
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=20)
        
        self.refresh_btn = ttk.Button(btn_frame, text="🔄 Refresh Devices", command=self.refresh_devices)
        self.refresh_btn.pack(side="left", padx=10)
        
        self.launch_btn = ttk.Button(btn_frame, text="🚀 Launch Unified Application", command=self.launch_unified)
        self.launch_btn.pack(side="left", padx=10)

    def start_autodetect(self, force=False):
        if getattr(self, 'is_scanning', False):
            return
            
        if not SERIAL_AVAILABLE or not self.detected_ports:
            self.status_var.set("No serial ports detected.")
            self.progress_bar.pack_forget()
            self.status_label.pack_forget()
            return
            
        if force:
            self.autodetected_devices.clear()
            for device in self.devices:
                self.device_vars[device].set(False)
                if hasattr(self, 'status_labels') and device in self.status_labels:
                    self.status_labels[device].config(text="")
                self.toggle_dropdown_state(device)
            
        self.is_scanning = True
        
        self.launch_btn.config(state=tk.DISABLED)
        self.refresh_btn.config(state=tk.DISABLED)
        
        self.status_label.pack(pady=5)
        self.progress_bar.pack(fill="x", padx=40, pady=5)
        self.progress_var.set(0)
        self.status_var.set("Scanning for devices...")
        
        thread = threading.Thread(target=self._scan_ports_thread)
        thread.daemon = True
        thread.start()

    def _scan_ports_thread(self):
        DEVICE_MAP = {
            's': "Stepper Probe",
            'd': "DC Probe",
            'c': "Chuck Positioner",
            't': "Temperature Controller"
        }
        DEV_PATTERN = re.compile(r"DEV:\s*([sdct])", re.IGNORECASE)
        total_ports = len(self.detected_ports)
        
        for i, port in enumerate(self.detected_ports):
            self.gui_queue.put(('status', f"Scanning {port}..."))
            
            # Check if this port is already assigned to an active device
            already_assigned = False
            for dev_name, check_var in self.device_vars.items():
                if check_var.get() and self.port_vars[dev_name].get() == port:
                    already_assigned = True
                    break
            
            if not already_assigned:
                try: 
                    with serial.Serial(port, baudrate=500000, timeout=.1, write_timeout=.2) as ser:
                        ser.reset_input_buffer()
                        ser.reset_output_buffer()
                        start_time = time.time()
                        device_found = False
                        while ((time.time() - start_time < 1.5) and not device_found):
                            try:
                                ser.write(b"s\n")
                            except Exception:
                                break
                            
                            if ser.in_waiting > 0: 
                                response_bytes = ser.read(ser.in_waiting)
                                response_str = response_bytes.decode('utf-8', errors='ignore').strip()
                                match = DEV_PATTERN.search(response_str)
                                if match:
                                    dev_char = match.group(1).lower()
                                    if dev_char in DEVICE_MAP:
                                        device_name = DEVICE_MAP[dev_char]
                                        self.gui_queue.put(('found', (device_name, port)))
                                        print(f"[main_app] Auto-detected {device_name} on {port}")
                                    device_found = True
                            else:
                                time.sleep(0.05)
                except Exception as e:
                    time.sleep(0.1) # Add a small sleep so invalid ports don't instantly finish and make the bar look frozen
                    pass
            
            progress = ((i + 1) / total_ports) * 100
            self.gui_queue.put(('progress', progress))
            
        self.gui_queue.put(('complete', None))

    def _update_device_ui(self, device_name, port):
        if device_name in self.device_vars:
            self.autodetected_devices.add(device_name)
            self.device_vars[device_name].set(True)
            self.port_vars[device_name].set(port)
            self.status_labels[device_name].config(text="✓ Auto-Verified", fg="green")
            self.toggle_dropdown_state(device_name)

    def _scan_complete(self):
        self.is_scanning = False
        self.status_var.set("Scan complete.")
        self.progress_bar.pack_forget()
        self.status_label.pack_forget()
        self.launch_btn.config(state=tk.NORMAL)
        self.refresh_btn.config(state=tk.NORMAL)

    def launch_unified(self):
        if getattr(self, 'is_scanning', False):
            return
            
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
        
        self.tab_metadata = {}

        # Create a unified Dashboard Notebook
        self.dashboard_window = tk.Toplevel(self)
        self.dashboard_window.title("Transfer Stage Unified Control")
        self.dashboard_window.protocol("WM_DELETE_WINDOW", self.shutdown)

        self.notebook = DraggableClosableNotebook(self.dashboard_window)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)
        
        def _on_close_tab(index):
            tab_id = self.notebook.tabs()[index]
            if tab_id in self.tab_metadata:
                meta = self.tab_metadata[tab_id]
                
                # Stop Poller
                if meta['poller']:
                    meta['poller'].stop_polling()
                    meta['poller'].close()
                    if meta['poller'] in self.pollers:
                        self.pollers.remove(meta['poller'])
                
                # Cancel after timers
                for timer_id in meta.get('timers', []):
                    try:
                        self.dashboard_window.after_cancel(timer_id)
                    except Exception:
                        pass
                
                # Close Models / Connections
                model = meta['model']
                if hasattr(model, 'disconnect'):
                    model.disconnect()
                if hasattr(model, 'stop'):
                    try:
                        model.stop()
                    except Exception:
                        pass
                        
                serial_conn = meta['serial_conn']
                if serial_conn and hasattr(serial_conn, 'close'):
                    serial_conn.close()
                    
                del self.tab_metadata[tab_id]
                
            self.notebook.forget(index)
            
            # If all tabs are closed, destroy dashboard and show setup window
            if not self.notebook.tabs():
                self.dashboard_window.destroy()
                self.deiconify()
                
        self.notebook.on_close_tab_callback = _on_close_tab

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
                
            timers = []

            # If the device has a controller, hook up the manual polling loop
            if poller:
                # Provide dummy log updater to avoid crash
                model.last_activity_time = time.time()
                model.disable_timer_id = None
                
                def _reset_disable_timer(model_ref=model):
                    model_ref.last_activity_time = time.time()
                    if hasattr(model_ref, 'disable_timer_id') and model_ref.disable_timer_id:
                        self.dashboard_window.after_cancel(model_ref.disable_timer_id)
                        model_ref.disable_timer_id = None
                        
                    if hasattr(model_ref, 'system_enabled') and model_ref.system_enabled:
                        model_ref.disable_timer_id = self.dashboard_window.after(300000, lambda: _auto_disable(model_ref))
                        
                def _auto_disable(model_ref):
                    print(f"[Timeout] 5 minutes of inactivity detected. Disabling {model_ref.__class__.__name__}")
                    if hasattr(model_ref, 'disable'):
                        model_ref.disable()
                    
                poller.start_polling(self.dashboard_window, log_updater=print, activity_callback=_reset_disable_timer)
                # Let's write a generic loop to route controller state into the model
                def _route_input(m=model, p=poller):
                    if hasattr(m, 'send_manual_mode_command') and getattr(m, 'manual_flag', False):
                        controller_params = p.get_mapped_state()
                        m.send_manual_mode_command(controller_params)
                    timer_id = self.dashboard_window.after(50, lambda: _route_input(m, p))
                    timers.append(timer_id)
                timer_id = self.dashboard_window.after(50, _route_input)
                timers.append(timer_id)
                
            # For probes, setup continuous position polling
            if hasattr(model, 'read_position'):
                def _poll_pos(m=model):
                    m.read_position()
                    timer_id = self.dashboard_window.after(100, lambda: _poll_pos(m))
                    timers.append(timer_id)
                timer_id = self.dashboard_window.after(100, _poll_pos)
                timers.append(timer_id)
                
            # Register in metadata for cleanup
            self.tab_metadata[str(frame)] = {
                'model': model,
                'view': view,
                'poller': poller,
                'serial_conn': serial_conn,
                'timers': timers
            }

        # Link RedPercentSystem to StepperProbe for X-coordinate syncing
        stepper_model = None
        red_model = None
        for tab_id, meta in self.tab_metadata.items():
            if meta['model'].__class__.__name__ == 'StepperProbe':
                stepper_model = meta['model']
            if meta['model'].__class__.__name__ == 'RedPercentSystem':
                red_model = meta['model']
        if red_model and stepper_model:
            red_model.stepper_model = stepper_model

    def shutdown(self):
        for poller in self.pollers:
            poller.stop_polling()
            poller.close()
        self.destroy()

if __name__ == "__main__":
    app = SetupWindow()
    app.mainloop()
