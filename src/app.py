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
    import pygame
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

    def __init__(self, devices, detected_ports):
        super().__init__()
        self.devices = devices
        self.detected_ports = detected_ports

    def run(self):
        self.status.emit("Scanning for active devices...")
        if not SERIAL_AVAILABLE:
            self.complete.emit()
            return

        import re
        DEV_PATTERN = re.compile(r"<([^>]+)>")
        DEVICE_MAP = {
            'c': 'Chuck Positioner',
            's': 'Stepper Probe',
            'd': 'DC Probe',
            't': 'Temperature Controller'
        }

        total_ports = len(self.detected_ports)
        if total_ports == 0:
            self.complete.emit()
            return

        for i, port in enumerate(self.detected_ports):
            device_found = False
            try:
                with serial.Serial(port, baudrate=115200, timeout=0.2, write_timeout=0.2) as ser:
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()
                    ser.write(b"<0,0,0>")
                    time.sleep(0.1)
                    
                    if ser.in_waiting > 0:
                        response_bytes = ser.read_all()
                        response_str = response_bytes.decode('utf-8', errors='ignore')
                        match = DEV_PATTERN.search(response_str)
                        if match:
                            dev_char = match.group(1).lower()
                            if dev_char in DEVICE_MAP:
                                device_name = DEVICE_MAP[dev_char]
                                self.found.emit(device_name, port)
                                device_found = True
                    else:
                        time.sleep(0.05)
            except Exception:
                pass

            if not device_found:
                try:
                    with serial.Serial(port, baudrate=57600, timeout=0.2, write_timeout=0.2, xonxoff=True) as ser:
                        ser.reset_input_buffer()
                        ser.reset_output_buffer()
                        ser.write(b"1ID?\r\n")
                        time.sleep(0.1)
                        response = ser.read_all().decode("utf-8", errors="ignore").strip()
                        if not response:
                            ser.write(b"1TS?\r\n")
                            time.sleep(0.1)
                            response = ser.read_all().decode("utf-8", errors="ignore").strip()
                        if response.startswith("1ID") or response.startswith("1TS"):
                            self.found.emit("SMC100 Rotator", port)
                            device_found = True
                except Exception:
                    pass
            
            prog = ((i + 1) / total_ports) * 100
            self.progress.emit(prog)

        self.complete.emit()

class SetupWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Device Configuration Setup (PySide6)")
        self.resize(850, 650)

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
            for i in range(pygame.joystick.get_count()):
                self.detected_controllers.append(f"Joy {i}")

    def create_widgets(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        title = QLabel("Select and Configure Active Systems")
        title.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 10px;")
        main_layout.addWidget(title)

        grid = QGridLayout()
        grid.addWidget(QLabel("Enable"), 0, 0)
        grid.addWidget(QLabel("System / Device"), 0, 1)
        grid.addWidget(QLabel("COM Port"), 0, 2)
        grid.addWidget(QLabel("Gamepad Mapping"), 0, 3)
        grid.addWidget(QLabel("Status"), 0, 4)

        for i, device in enumerate(self.devices):
            row = i + 1
            
            cb = QCheckBox()
            self.device_vars[device] = cb
            grid.addWidget(cb, row, 0)

            lbl = QLabel(device)
            grid.addWidget(lbl, row, 1)

            port_cb = QComboBox()
            port_cb.addItems(self.detected_ports)
            self.port_vars[device] = port_cb
            grid.addWidget(port_cb, row, 2)

            ctrl_cb = QComboBox()
            ctrl_cb.addItems(self.detected_controllers)
            self.controller_vars[device] = ctrl_cb
            grid.addWidget(ctrl_cb, row, 3)
            
            if device == "Red Percent Window":
                port_cb.setEnabled(False)
                ctrl_cb.setEnabled(False)

            status_lbl = QLabel("-")
            self.status_labels[device] = status_lbl
            grid.addWidget(status_lbl, row, 4)

        main_layout.addLayout(grid)

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
        self.launch_btn.setStyleSheet("background-color: #0078D4; color: white; font-weight: bold; padding: 10px;")
        btn_layout.addWidget(self.launch_btn)

        main_layout.addLayout(btn_layout)

    def refresh_ports(self):
        self.get_available_ports()
        for device in self.devices:
            if device != "Red Percent Window":
                self.port_vars[device].clear()
                self.port_vars[device].addItems(self.detected_ports)

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
            QMessageBox.warning(self, "No Devices Selected", "Please select at least one device to launch.")
            return

        devices_needing_ports = [c for c in active_configs if c["device"] != "Red Percent Window"]
        if len(assigned_ports) < len(devices_needing_ports):
            QMessageBox.critical(self, "Port Collision", "Error: You cannot assign the same COM port to multiple active devices!")
            return
            
        physical_configs = [c for c in active_configs if "None" not in c["controller"] and "Virtual" not in c["controller"] and c["device"] != "Red Percent Window"]
        if len(assigned_controllers) < len(physical_configs):
            QMessageBox.critical(self, "Controller Collision", "Error: You cannot map the same physical controller to multiple active devices!")
            return
            
        print("\n--- Launching Unified Control Dashboard ---")
        active_models = {}

        for config in active_configs:
            device = config["device"]
            port = config["port"]
            controllerID = config["controller"]
            
            if "Joy" in controllerID:
                controllerID = int(controllerID.replace("Joy ", ""))
            elif controllerID == "None":
                controllerID = None

            self.active_claims[device] = controllerID

            # Instantiate Domain Models
            if device == "Stepper Probe":
                from model.probes import StepperProbe
                active_models[device] = StepperProbe(port, controllerID, self.active_claims)
            elif device == "DC Probe":
                from model.probes import DCProbe
                active_models[device] = DCProbe(port, controllerID, self.active_claims)
            elif device == "Chuck Positioner":
                from model.probes import ChuckPositioner
                active_models[device] = ChuckPositioner(port, controllerID, self.active_claims)
            elif device == "Temperature Controller":
                from model.temperature_system import TemperatureSystem
                active_models[device] = TemperatureSystem(port)
            elif device == "SMC100 Rotator":
                from model.rotator_system import RotatorSystem
                active_models[device] = RotatorSystem(port)
            elif device == "Red Percent Window":
                from model.redpercent_system import RedPercentSystem
                active_models[device] = RedPercentSystem()

        stepper_model = active_models.get("Stepper Probe")
        red_model = active_models.get("Red Percent Window")
        if red_model and stepper_model:
            red_model.stepper_model = stepper_model

        from view_pyside import DashboardWindow
        from model.system_manager import SystemManager

        self.manager = SystemManager()
        for name, model in active_models.items():
            self.manager.register_model(name, model)

        self.dashboard = DashboardWindow(self.manager)
        self.dashboard.show()
        
        self.close()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SetupWindow()
    window.show()
    sys.exit(app.exec())
