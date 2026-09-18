import sys
import time
import re
from typing import Dict, Any, List, Optional

def discover_ports() -> list[str]:
    try:
        import serial.tools.list_ports
        SERIAL_AVAILABLE = True
    except ImportError:
        SERIAL_AVAILABLE = False
        
    if SERIAL_AVAILABLE:
        com_ports = list(serial.tools.list_ports.comports())
        valid_ports = []
        for port in com_ports:
            dev_name = getattr(port, "device", str(port))
            if "Bluetooth" in dev_name or "Wireless" in dev_name:
                continue
            if sys.platform.startswith("linux") and dev_name.startswith("/dev/ttyS"):
                if getattr(port, "hwid", "n/a") == "n/a" or not getattr(port, "hwid", None):
                    continue
            valid_ports.append(dev_name)
        
        if not valid_ports and com_ports:
            valid_ports = [getattr(p, "device", str(p)) for p in com_ports]
            
        def port_sort_key(dev_name):
            is_usb = any(dev_name.startswith(prefix) for prefix in ("/dev/ttyACM", "/dev/ttyUSB", "/dev/cu.usb", "/dev/tty.usb")) or "USB" in dev_name
            return (0 if is_usb else 1, dev_name)
            
        detected_ports = ["Headless"] + sorted(valid_ports, key=port_sort_key)
    else:
        detected_ports = []
        
    if not detected_ports or detected_ports == ["Headless"]:
        detected_ports = ["Headless", "COM1", "COM2", "COM3", "COM4"]
        
    return detected_ports

def probe_device_at(port: str) -> str | None:
    try:
        import serial
    except ImportError:
        return None

    DEVICE_MAP = {
        's': "Stepper Probe",
        'd': "DC Probe",
        'c': "Chuck Positioner",
        't': "Temperature Controller"
    }
    DEV_PATTERN = re.compile(r"(?:DEV:\s*|<)([sdct])>?", re.IGNORECASE)
    
    device_found = False
    device_name = None

    # 1. 500k baud
    try:
        with serial.Serial(port, baudrate=500000, timeout=0.1, write_timeout=0.2) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(1.5)
            start_time = time.time()
            response_buffer = ""
            while time.time() - start_time < 3.0 and not device_found:
                try:
                    ser.write(b"s\n")
                except Exception:
                    break
                if ser.in_waiting > 0:
                    response_bytes = ser.read(ser.in_waiting)
                    response_str = response_bytes.decode('utf-8', errors='ignore')
                    response_buffer += response_str
                    match = DEV_PATTERN.search(response_buffer)
                    if match:
                        dev_char = match.group(1).lower()
                        if dev_char in DEVICE_MAP:
                            device_name = DEVICE_MAP[dev_char]
                            device_found = True
                else:
                    time.sleep(0.05)
    except Exception:
        pass
        
    if device_found: return device_name

    # 2. 115200 baud
    try:
        with serial.Serial(port, baudrate=115200, timeout=0.1, write_timeout=0.2) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(1.5)
            start_time = time.time()
            response_buffer = ""
            while time.time() - start_time < 3.0 and not device_found:
                try:
                    ser.write(b"s\n")
                except Exception:
                    break
                if ser.in_waiting > 0:
                    response_bytes = ser.read(ser.in_waiting)
                    response_str = response_bytes.decode('utf-8', errors='ignore')
                    response_buffer += response_str
                    match = DEV_PATTERN.search(response_buffer)
                    if match:
                        dev_char = match.group(1).lower()
                        if dev_char in DEVICE_MAP:
                            device_name = DEVICE_MAP[dev_char]
                            device_found = True
                else:
                    time.sleep(0.05)
    except Exception:
        pass
        
    if device_found: return device_name

    # 3. 57600 baud
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
                device_name = "SMC100 Rotator"
                device_found = True
    except Exception:
        pass
        
    return device_name

def build_models(active_configs: list[dict], active_claims: dict) -> dict[str, object]:
    active_models = {}
    for config in active_configs:
        device = config.get("device")
        port = config.get("port")
        controllerID = config.get("controller")
        
        if device == "Stepper Probe":
            from model.probes import StepperProbe
            active_models[device] = StepperProbe(port, controllerID, active_claims)
        elif device == "DC Probe":
            from model.probes import DCProbe
            active_models[device] = DCProbe(port, controllerID, active_claims)
        elif device == "Chuck Positioner":
            from model.probes import ChuckPositioner
            active_models[device] = ChuckPositioner(port, controllerID, active_claims)
        elif device == "Temperature Controller":
            from model.temperature_system import TemperatureSystem
            active_models[device] = TemperatureSystem(port)
        elif device == "SMC100 Rotator":
            from model.rotator_system import RotatorSystem
            active_models[device] = RotatorSystem(port)
        elif device == "Red Percent Window":
            from model.redpercent_system import RedPercentSystem
            active_models[device] = RedPercentSystem()
            
    return active_models

def validate_assignment(active_configs: list[dict]) -> list[str]:
    errors = []
    assigned_ports = set()
    assigned_controllers = set()
    
    for c in active_configs:
        dev = c.get("device")
        port = c.get("port")
        ctrl = c.get("controller", "None")

        if port == "Headless":
            port = "SIM"

        if dev != "Red Percent Window" and port not in ("SIM", "None"):
            if port in assigned_ports:
                errors.append(f"Port collision: Port '{port}' is assigned to multiple devices")
            assigned_ports.add(port)

        if ctrl and "None" not in ctrl and "Virtual" not in ctrl and "N/A" not in ctrl and dev != "Red Percent Window":
            if ctrl in assigned_controllers:
                errors.append(f"Controller collision: Controller '{ctrl}' is assigned to multiple devices")
            assigned_controllers.add(ctrl)
            
    return errors
