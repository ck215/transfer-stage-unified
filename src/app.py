import sys

def parse_controller_id(controllerID):
    """Safely parses a controller ID string into an integer or None."""
    if controllerID is None:
        return None
    if not isinstance(controllerID, str):
        return None
    if "None" in controllerID or "Virtual" in controllerID or controllerID == "N/A":
        return None
    import re
    m = re.search(r'(?:Joy|ID)?\s*(\d+)', controllerID, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


import os
os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"

# Fix macOS Qt cocoa platform plugin discovery.
# When launched via os.execv (Homebrew Python), the venv's rpath context is
# stripped and Qt cannot locate its platform plugins. Set the path explicitly
# using PySide6's own location — must happen before any PySide6 import.
try:
    import importlib.util as _ilu
    _ps6_spec = _ilu.find_spec("PySide6")
    if _ps6_spec and _ps6_spec.submodule_search_locations:
        _ps6_dir = list(_ps6_spec.submodule_search_locations)[0]
        _qt_plugins = os.path.join(_ps6_dir, "Qt", "plugins")
        if os.path.isdir(_qt_plugins):
            os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
            os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH",
                                  os.path.join(_qt_plugins, "platforms"))
except Exception:
    pass

def run_legacy_app():
    import tkinter as tk
    from tkinter import ttk, messagebox
    import sys
    import os
    
    import re
    import time
    import threading
    import queue
    
    from controller.serial import serial
    from controller.gamepad import ControllerPoller
    
    from model.probes import StepperProbe, DCProbe, ChuckPositioner
    from model.temperature_system import TemperatureSystem
    from model.rotator_system import RotatorSystem
    from model.redpercent_system import RedPercentSystem
    
    
    try:
        import serial.tools.list_ports
        import serial
        SERIAL_AVAILABLE = True
    except ImportError:
        SERIAL_AVAILABLE = False
    
    # Try to import pygame for physical joystick/gamepad detection.
    try:
        import os
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"
        os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
        import pygame
        if not pygame.get_init():
            pygame.init()
        PYGAME_AVAILABLE = True
    except ImportError:
        PYGAME_AVAILABLE = False
    
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
            import app_bootstrap
            self.detected_ports = app_bootstrap.discover_ports()

        def get_available_controllers(self):
            self.detected_controllers = ["None"]
            if PYGAME_AVAILABLE:
                if not pygame.get_init():
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
                    
        def _reset_device_status(self, device):
            if device not in self.status_labels:
                return
            if device == "Red Percent Window":
                self.status_labels[device].config(text="Headless", fg="gray")
            else:
                self.status_labels[device].config(text="Waiting...", fg="black")

        def toggle_dropdown_state(self, device):
            is_checked = self.device_vars[device].get()
            serial_widget = self.dropdown_widgets[device]
            ctrl_widget = self.controller_widgets[device]
            
            if is_checked:
                if ((device != "Temperature Controller") and (device != "SMC100 Rotator") and (device != "Red Percent Window")):
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
                
                if device in ["Red Percent Window", "SMC100 Rotator", "Temperature Controller"]:
                    ctrl_var = tk.StringVar(value="N/A")
                    self.controller_vars[device] = ctrl_var
                    ctrl_dropdown = ttk.OptionMenu(grid_frame, ctrl_var, "N/A", "N/A")
                    ctrl_dropdown.grid(row=idx+1, column=2, padx=10, pady=10, sticky="ew")
                    ctrl_dropdown.state(["disabled"])
                    self.controller_widgets[device] = ctrl_dropdown
                else:
                    ctrl_var = tk.StringVar(value=self.detected_controllers[0] if self.detected_controllers else "None")
                    self.controller_vars[device] = ctrl_var
                    ctrl_dropdown = ttk.OptionMenu(grid_frame, ctrl_var, ctrl_var.get(), *self.detected_controllers if self.detected_controllers else ["None"])
                    ctrl_dropdown.grid(row=idx+1, column=2, padx=10, pady=10, sticky="ew")
                    ctrl_dropdown.state(["disabled"])
                    self.controller_widgets[device] = ctrl_dropdown
                
                lbl = tk.Label(grid_frame, font=("Helvetica", 10, "bold"))
                lbl.grid(row=idx+1, column=3, padx=10, pady=10, sticky="w")
                self.status_labels[device] = lbl
                self._reset_device_status(device)
                
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
                    self._reset_device_status(device)
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
            import app_bootstrap
            total_ports = len(self.detected_ports)
            
            for i, port in enumerate(self.detected_ports):
                if port == "Headless":
                    continue
                self.gui_queue.put(('status', f"Scanning {port}..."))
                
                already_assigned = False
                for dev_name, check_var in self.device_vars.items():
                    if check_var.get() and self.port_vars[dev_name].get() == port:
                        already_assigned = True
                        break
                
                if not already_assigned:
                    device_name = app_bootstrap.probe_device_at(port)
                    if device_name:
                        self.gui_queue.put(('found', (device_name, port)))
                        print(f"[main_app] Auto-detected {device_name} on {port}")
                
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
            self.progress_var.set(100)
            self.status_var.set("Scan complete.")
            for device in self.devices:
                if device != "Red Percent Window" and device not in self.autodetected_devices:
                    self.status_labels[device].config(text="Not Found", fg="#D13438")
            self.update()
            # Hold the completed state for 1 second so the user can visually register it before it vanishes
            self.after(1000, self._cleanup_scan_ui)
    
        def _cleanup_scan_ui(self):
            self.is_scanning = False
            self.progress_bar.pack_forget()
            self.status_label.pack_forget()
            self.update()
            
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

                    if port == "Headless":
                        port = "SIM"
    
                    active_configs.append({
                        "device": device, 
                        "port": port, 
                        "controller": controller
                    })
                    if device != "Red Percent Window" and port != "SIM":
                        assigned_ports.add(port)
                    
                    if "None" not in controller and "Virtual" not in controller and "N/A" not in controller and device != "Red Percent Window":
                        assigned_controllers.add(controller)
                    
            if not active_configs:
                messagebox.showwarning("No Devices Selected", "Please select at least one device to launch.")
                return
                
            import app_bootstrap
            errors = app_bootstrap.validate_assignment(active_configs)
            if errors:
                title = "Port Collision" if "Port collision" in errors[0] else "Controller Collision"
                messagebox.showerror(title, "\n".join(errors))
                return
            print("\n--- Launching Unified Control Dashboard ---")

            from model.system_manager import SystemManager
            import lifecycle

            system_manager = SystemManager()
            try:
                active_models = app_bootstrap.build_models(
                    active_configs, self.active_claims, system_manager)
            except Exception as e:
                # The setup window used to be withdrawn *before* this call, so a
                # failed build left the user with no setup window and no
                # dashboard — nothing on screen at all (MANAGER-6). It is now
                # hidden only once the build has succeeded.
                messagebox.showerror(
                    "Device Initialization Failed",
                    f"Could not start the selected devices:\n\n{e}\n\n"
                    "Nothing was left running; adjust the configuration and try again.")
                return

            # Hide the setup window launcher panel, now that launching worked.
            self.withdraw()

            # Link RedPercentSystem to the active positioning probe for X/Y/Z syncing.
            red_model = active_models.get("Red Percent Window")
            if red_model:
                probe_models = {name: model for name, model in active_models.items() if hasattr(model, 'pos_x')}
                red_model.available_probes = probe_models
                if "Stepper Probe" in probe_models:
                    red_model.set_stepper_model("Stepper Probe")
                elif probe_models:
                    red_model.set_stepper_model(list(probe_models.keys())[0])
    
            # Launch Tkinter Dashboard
            from views.tkinter.view import DashboardWindow, ErrorPopupManager

            lifecycle.set_current_manager(system_manager)
            lifecycle.install_exit_hooks()

            dash = DashboardWindow(self, system_manager)
            # **Not** `ErrorPopupManager.initialize(dash)`. Binding the popup
            # manager to the dashboard is ERRORS-5 / VIEW-TKINTER-2 /
            # MANAGER-17: its `after` loop died when the dashboard was
            # destroyed and every later report vanished. It is already bound
            # to the SetupWindow below, which lives for the whole process.
            # macOS Dock "Quit" and Cmd-Q bypass window close handlers, so Tk
            # needs this one wired explicitly or the app exits with hardware
            # still enabled (VIEW-TKINTER-8).
            try:
                dash.createcommand("::tk::mac::Quit", lambda: (lifecycle.shutdown("tk quit"), dash.quit()))
            except Exception:
                pass
            
            # We don't destroy self here, we withdrew it.
            # dash will call self.deiconify() on close.

    app = SetupWindow()
    from views.tkinter.view import ErrorPopupManager
    from error_routing import install_exception_hooks
    ErrorPopupManager.initialize(app)
    # One installer for all three launchers (RC-8 item 4). Tk additionally
    # needs `report_callback_exception`, which is where an exception raised
    # inside a widget callback goes and which nothing used to cover.
    install_exception_hooks(tk_root=app)
    app.mainloop()
    
    

def run_pyside_app():
    import sys
    import time
    import threading
    import queue
    
    try:
        import serial.tools.list_ports
        import serial
        SERIAL_AVAILABLE = True
    except ImportError:
        SERIAL_AVAILABLE = False
    
    try:
        import os
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"
        os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
        os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
        import pygame
        if not pygame.get_init():
            pygame.init()
        PYGAME_AVAILABLE = True
    except ImportError:
        PYGAME_AVAILABLE = False
    
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
        QCheckBox, QComboBox, QPushButton, QLabel, QProgressBar, QMessageBox, QGridLayout
    )
    from PySide6.QtCore import Qt, QTimer, QThread, Signal
    
    class ScannerThread(QThread):
        progress = Signal(float)
        status = Signal(str)
        found = Signal(str, str)
        complete = Signal()
        pinging = Signal(str)
    
        def __init__(self, devices, detected_ports):
            super().__init__()
            self.devices = devices
            self.detected_ports = detected_ports
    
        def run(self):
            import app_bootstrap
            self.status.emit("Scanning for active devices...")
            if not SERIAL_AVAILABLE:
                self.complete.emit()
                return
    
            total_ports = len(self.detected_ports)
            if total_ports == 0:
                self.complete.emit()
                return
    
            for i, port in enumerate(self.detected_ports):
                if port == "Headless": continue
                self.pinging.emit(port)
                
                device_name = app_bootstrap.probe_device_at(port)
                if device_name:
                    self.found.emit(device_name, port)
                    print(f"[main_app] Auto-detected {device_name} on {port}")
                
                prog = ((i + 1) / total_ports) * 100
                self.progress.emit(prog)
    
            self.complete.emit()
    class SetupWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Device Configuration Setup (PySide6)")
            self.resize(850, 400)
    
            self.devices = ["Stepper Probe", "DC Probe", "Chuck Positioner", "Temperature Controller", "SMC100 Rotator", "Red Percent Window"]
            self.device_vars = {}        
            self.port_vars = {}          
            self.controller_vars = {}    
            self.status_labels = {}
            self.autodetected_devices = set()
    
            self.detected_ports = []
            self.detected_controllers = []
            
            self.active_claims = {}
            self.is_scanning = False
    
            self.get_available_ports()
            self.get_available_controllers()
    
            self.create_widgets()
            
            # Start autodetect
            self.start_autodetect()
    
        def get_available_ports(self):
            import app_bootstrap
            self.detected_ports = app_bootstrap.discover_ports()

        def get_available_controllers(self):
            self.detected_controllers = ["None"]
            if PYGAME_AVAILABLE:
                if not pygame.get_init():
                    pygame.init()
                pygame.joystick.init()
                for i in range(pygame.joystick.get_count()):
                    try:
                        js = pygame.joystick.Joystick(i)
                        js.init()
                        self.detected_controllers.append(f"ID {i}: {js.get_name()}")
                    except Exception:
                        pass
    
        def create_widgets(self):
            central = QWidget()
            self.setCentralWidget(central)
            main_layout = QVBoxLayout(central)
    
            title = QLabel("Select and Configure Active Systems")
            title.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 10px;")
            main_layout.addWidget(title)
    
            grid = QGridLayout()
            enable_hdr = QLabel("Enable")
            enable_hdr.setMinimumWidth(60)
            enable_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(enable_hdr, 0, 0)
            grid.addWidget(QLabel("System / Device"), 0, 1)
            grid.addWidget(QLabel("COM Port"), 0, 2)
            grid.addWidget(QLabel("Gamepad Mapping"), 0, 3)
            grid.addWidget(QLabel("Status"), 0, 4)
            grid.setColumnMinimumWidth(0, 60)
    
            for i, device in enumerate(self.devices):
                row = i + 1
                
                cb = QCheckBox()
                self.device_vars[device] = cb
                grid.addWidget(cb, row, 0, alignment=Qt.AlignmentFlag.AlignCenter)
    
                lbl = QLabel(device)
                grid.addWidget(lbl, row, 1)
    
                port_cb = QComboBox()
                port_cb.addItems(self.detected_ports)
                self.port_vars[device] = port_cb
                grid.addWidget(port_cb, row, 2)
    
                ctrl_cb = QComboBox()
                if device in ["Red Percent Window", "SMC100 Rotator", "Temperature Controller"]:
                    ctrl_cb.addItem("N/A")
                    ctrl_cb.setEnabled(False)
                else:
                    ctrl_cb.addItems(self.detected_controllers)
                self.controller_vars[device] = ctrl_cb
                grid.addWidget(ctrl_cb, row, 3)
                
                if device == "Red Percent Window":
                    port_cb.setEnabled(False)
    
                if device == "Red Percent Window":
                    status_lbl = QLabel("Headless")
                    status_lbl.setStyleSheet("color: gray;")
                else:
                    status_lbl = QLabel("Waiting...")
                self.status_labels[device] = status_lbl
                grid.addWidget(status_lbl, row, 4)
    
    
            main_layout.addLayout(grid)
            main_layout.addStretch(1)  # Prevents grid from stretching vertically
    
            # Bottom section
    
            self.progress_bar = QProgressBar()
            self.progress_bar.hide()
            main_layout.addWidget(self.progress_bar)
    
            self.status_label = QLabel("")
            self.status_label.hide()
            main_layout.addWidget(self.status_label)
    
            btn_layout = QHBoxLayout()
            self.refresh_btn = QPushButton("Refresh Ports")
            self.refresh_btn.clicked.connect(self.refresh_ports)
            btn_layout.addWidget(self.refresh_btn)
    
            self.launch_btn = QPushButton("Launch Application")
            self.launch_btn.clicked.connect(self.launch_unified)
            self.launch_btn.setStyleSheet("""
                QPushButton { background-color: #0078D4; color: white; font-weight: bold; padding: 10px; }
                QPushButton:disabled { background-color: #cccccc; color: #666666; }
            """)
            btn_layout.addWidget(self.launch_btn)
    
            main_layout.addLayout(btn_layout)
    
        def refresh_ports(self):
            if getattr(self, 'is_scanning', False):
                return
            self.get_available_ports()
            for device in self.devices:
                if device != "Red Percent Window":
                    current = self.port_vars[device].currentText()
                    self.port_vars[device].clear()
                    self.port_vars[device].addItems(self.detected_ports)
                    idx = self.port_vars[device].findText(current)
                    if idx >= 0:
                        self.port_vars[device].setCurrentIndex(idx)
            self.start_autodetect()
    
        def start_autodetect(self):
            self.is_scanning = True
            self.progress_bar.setValue(0)
            self.progress_bar.show()
            self.status_label.setText("Scanning for active devices...")
            self.status_label.show()
            self.launch_btn.setEnabled(False)
            self.refresh_btn.setEnabled(False)
    
            self.scanner = ScannerThread(self.devices, self.detected_ports)
            self.scanner.progress.connect(self.progress_bar.setValue)
            self.scanner.status.connect(self.status_label.setText)
            self.scanner.pinging.connect(lambda p: self.status_label.setText(f"Scanning port {p}..."))
            self.scanner.found.connect(self._update_device_ui)
            self.scanner.complete.connect(self._scan_complete)
            self.scanner.start()
    
        def _update_device_ui(self, device_name, port):
            if device_name in self.device_vars:
                self.autodetected_devices.add(device_name)
                self.device_vars[device_name].setChecked(True)
                idx = self.port_vars[device_name].findText(port)
                if idx >= 0:
                    self.port_vars[device_name].setCurrentIndex(idx)
                self.status_labels[device_name].setText("✓ Auto-Verified")
                self.status_labels[device_name].setStyleSheet("color: #107C10;")
    
        def _scan_complete(self):
            self.is_scanning = False
            self.progress_bar.setValue(100)
            self.status_label.setText("Scan complete.")
            
            for device in self.devices:
                if device not in self.autodetected_devices and device != "Red Percent Window":
                    self.status_labels[device].setText("Not Found")
                    self.status_labels[device].setStyleSheet("color: #D13438;")
                    
            QTimer.singleShot(1000, self._cleanup_scan_ui)
    
        def _cleanup_scan_ui(self):
            self.progress_bar.hide()
            self.status_label.hide()
            self.launch_btn.setEnabled(True)
            self.refresh_btn.setEnabled(True)
    
        def launch_unified(self):
            if self.is_scanning: return
    
            active_configs = []
            assigned_ports = set()
            assigned_controllers = set()
            
            for device in self.devices:
                if self.device_vars[device].isChecked():
                    port = self.port_vars[device].currentText()
                    controller = self.controller_vars[device].currentText()
    
                    if port == "Headless":
                        port = "SIM"
    
                    active_configs.append({
                        "device": device, 
                        "port": port, 
                        "controller": controller
                    })
                    if device != "Red Percent Window" and port != "SIM":
                        assigned_ports.add(port)
                    if "None" not in controller and "Virtual" not in controller and "N/A" not in controller and device != "Red Percent Window":
                        assigned_controllers.add(controller)
    
            if not active_configs:
                QMessageBox.warning(self, "No Devices Selected", "Please select at least one device to launch.")
                return
    
            import app_bootstrap
            errors = app_bootstrap.validate_assignment(active_configs)
            if errors:
                title = "Port Collision" if "Port collision" in errors[0] else "Controller Collision"
                QMessageBox.critical(self, title, "\n".join(errors))
                return
                
            print("\n--- Launching Unified Control Dashboard ---")
            import app_bootstrap
            from model.system_manager import SystemManager

            self.manager = SystemManager()
            try:
                active_models = app_bootstrap.build_models(
                    active_configs, self.active_claims, self.manager)
            except Exception as e:
                QMessageBox.critical(
                    self, "Device Initialization Failed",
                    f"Could not start the selected devices:\n\n{e}\n\n"
                    "Nothing was left running; adjust the configuration and try again.")
                return

            # Link RedPercentSystem to the available positioning probes for X/Y/Z syncing.
            red_model = active_models.get("Red Percent Window")
            if red_model:
                probe_models = {name: model for name, model in active_models.items() if hasattr(model, 'pos_x')}
                red_model.available_probes = probe_models
                if "Stepper Probe" in probe_models:
                    red_model.set_stepper_model("Stepper Probe")
                elif probe_models:
                    red_model.set_stepper_model(list(probe_models.keys())[0])
    
            from views.pyside.view import DashboardWindow

            import lifecycle

            lifecycle.set_current_manager(self.manager)
            lifecycle.install_exit_hooks()

            self.dashboard = DashboardWindow(self.manager)
            self.dashboard.show()
            
            self.close()
    
    app = QApplication.instance() or QApplication(sys.argv)

    # Qt can quit without any window's closeEvent running (Cmd-Q, the Dock,
    # a session logout), so the manager is torn down here rather than only in
    # DashboardWindow.closeEvent (MANAGER-2/3).
    import lifecycle
    app.aboutToQuit.connect(lambda: lifecycle.shutdown("Qt aboutToQuit"))

    from views.pyside.view import QtErrorPopupManager
    from error_routing import install_exception_hooks
    QtErrorPopupManager.initialize(app)
    install_exception_hooks()
    
    window = SetupWindow()
    window.show()
    return app.exec()


def run_web_app(port=8080, open_browser=True):
    from model.probes import StepperProbe, DCProbe, ChuckPositioner
    from model.temperature_system import TemperatureSystem
    from model.rotator_system import RotatorSystem
    from model.redpercent_system import RedPercentSystem
    from model.system_manager import SystemManager
    from views.web.web_view import WebDashboardWindow

    import lifecycle

    manager = SystemManager()
    lifecycle.set_current_manager(manager)
    lifecycle.install_exit_hooks()
    active_claims = {}

    # In Web Mode, we bypass default SIM initialization so the UI can boot directly into the Setup Wizard
    # Models will be registered dynamically via WebModelAdapter.initialize_setup()

    dashboard = WebDashboardWindow(manager, port=port, open_browser=open_browser)
    dashboard.show()
    print(f"[Launcher] Web View is live at http://127.0.0.1:{dashboard.server.port}")

    # The same installer the other two launchers call (RC-8 item 4). This
    # was the only launcher that covered `threading.excepthook` at all, and
    # it did so with a local copy that the other two did not have.
    # `WebDashboardServer.start()` runs `serve_forever()` on a daemon thread,
    # so `sys.excepthook` alone never sees what is raised there.
    from error_routing import install_exception_hooks
    install_exception_hooks()

    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[Launcher] Shutting down Web View...")
        dashboard.close()
    except Exception as e:
        print(f"[Launcher] Unhandled exception: {e}")
        traceback.print_exception(type(e), e, e.__traceback__)
        ErrorRouter.report_error('Unhandled Exception', f'An unexpected error occurred:\n\n{e}', exception=e)
        dashboard.close()

def launch_legacy():
    print("[Launcher] Starting Legacy Tkinter Dashboard...")
    sys.stdout.flush()
    run_legacy_app()

def launch_pyside():
    run_pyside_app()


def select_view(requested, platform=None, pyside_available=None):
    """Resolve the view to launch. Pure, so the defaults are testable.

    `requested` is the parsed --view value, or None for "no flag given".
    """
    if requested is not None:
        return requested
    if platform is None:
        platform = sys.platform
    # Owner decision D-9: Tkinter is the macOS default until this codebase is
    # stabilized. The Web view stays available with --web, but it is not what
    # an unqualified launch on a lab Mac should start.
    if platform == "darwin":
        return "legacy"
    if pyside_available is None:
        try:
            from PySide6.QtWidgets import QApplication  # noqa: F401
            pyside_available = True
        except ImportError:
            pyside_available = False
    return "pyside" if pyside_available else "web"

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Unified Stage Control Application",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Views:
  web       Modern browser-based dashboard (cross-platform)
  pyside    Native Qt desktop GUI (PySide6)
  legacy    Tkinter interface (the default on macOS)

Examples:
  python3 src/app.py --view web
  python3 src/app.py --pyside
  python3 src/app.py --web --port 8080 --no-browser
"""
    )

    # View selection
    view_group = parser.add_mutually_exclusive_group()
    view_group.add_argument(
        "--view",
        choices=["web", "pyside", "legacy"],
        help="Select UI interface to launch (web, pyside, legacy)"
    )
    view_group.add_argument(
        "--web",
        action="store_const",
        dest="view",
        const="web",
        help="Launch modern browser-based web dashboard"
    )
    view_group.add_argument(
        "--pyside",
        action="store_const",
        dest="view",
        const="pyside",
        help="Launch native PySide6 desktop GUI"
    )
    view_group.add_argument(
        "--tkinter",
        action="store_const",
        dest="view",
        const="legacy",
        help="Launch legacy Tkinter GUI"
    )

    # Web view configuration options
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for the web dashboard server (default: 8080)"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the web dashboard in a browser"
    )

    # parse_args, not parse_known_args: an unrecognized flag must be an error.
    # Under parse_known_args a typo like `--pyside6` was silently dropped and
    # the platform default took over — on a lab Mac that started a
    # hardware-capable web server instead of the view the operator asked for
    # (MANAGER-14).
    args = parser.parse_args()

    selected_view = select_view(args.view)
    if args.view is None:
        print(f"[Launcher] No view requested - defaulting to {selected_view}.")

    if selected_view == "legacy":
        launch_legacy()
    elif selected_view == "web":
        print("[Launcher] Starting Cross-Platform Web Dashboard...")
        sys.stdout.flush()
        run_web_app(port=args.port, open_browser=not args.no_browser)
    else:
        launch_pyside()


if __name__ == "__main__":
    main()


