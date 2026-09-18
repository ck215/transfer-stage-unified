"""
Web MVC Adapter for Transfer Stage Unified.

Provides thread-safe model and controller abstraction bridging the
HTTP request handlers in `web_server.py` with the hardware models and SystemManager.
Ensures zero coupling to Qt/PySide6.
"""

import sys
import threading
import traceback
from typing import Dict, Any, Optional, List, Union


class WebModelAdapter:
    """
    Adapter bridging Web API operations to SystemManager and underlying hardware models.
    Encapsulates thread safety, attribute updates, device discovery, polling, and command dispatch.
    """

    def __init__(self, system_manager=None, mode="setup"):
        self.system_manager = system_manager
        self.mode = mode  # "setup" or "running"
        self._state_lock = threading.RLock()
        self._device_locks: Dict[str, threading.Lock] = {}
        self.log_buffer: List[str] = []
        self.error_buffer: List[Dict[str, Any]] = []

    def _get_device_lock(self, device_name: str) -> threading.Lock:
        with self._state_lock:
            if device_name not in self._device_locks:
                self._device_locks[device_name] = threading.Lock()
            return self._device_locks[device_name]

    def set_system_manager(self, system_manager):
        with self._state_lock:
            self.system_manager = system_manager

    def scan_hardware(self) -> Dict[str, Any]:
        """Scans and enumerates available serial COM ports and gamepad controllers."""
        ports = ["SIM"]
        try:
            import serial.tools.list_ports
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

            sorted_ports = sorted(valid_ports, key=port_sort_key)
            ports.extend(sorted_ports)
        except Exception:
            pass

        controllers = ["None"]
        try:
            import subprocess
            code = "import os; os.environ['SDL_VIDEODRIVER']='dummy'; os.environ['PYGAME_HIDE_SUPPORT_PROMPT']='1'; import pygame; pygame.joystick.init(); count = pygame.joystick.get_count(); print(','.join([pygame.joystick.Joystick(i).get_name() for i in range(count)]))"
            res = subprocess.run(["python3", "-c", code], capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and res.stdout.strip():
                names = res.stdout.strip().split(',')
                for i, name in enumerate(names):
                    controllers.append(f"ID {i}: {name}")
        except Exception:
            pass

        if len(controllers) == 1:
            controllers.extend(["Virtual Controller A", "Virtual Controller B"])

        return {
            "ports": ports,
            "controllers": controllers,
            "status": "ok"
        }

    def initialize_setup(self, configs: Union[Dict[str, Any], List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        Validates device configs, checks port/controller collisions,
        initializes models into SystemManager, and transitions mode to 'running'.
        Supports both list of configs and dict of device_name -> config.
        """
        if not configs:
            return {"status": "error", "code": 400, "message": "Configs must be non-empty"}

        normalized_configs = []
        if isinstance(configs, dict):
            for dev_name, cfg in configs.items():
                if not isinstance(cfg, dict):
                    continue
                # Support enabled flag (default True if not specified)
                is_enabled = cfg.get("enabled", True)
                mode = cfg.get("mode", "").lower() if is_enabled else "simulation"
                port = cfg.get("port") if is_enabled else "None"
                if mode == "simulation":
                    port = "SIM" if is_enabled else "None"
                ctrl = cfg.get("controller_id", cfg.get("controller", "None")) if is_enabled else "None" 
                normalized_configs.append({
                    "device": dev_name,
                    "port": port,
                    "controller": ctrl,
                    "mode": mode
                })
        elif isinstance(configs, list):
            for c in configs:
                if not isinstance(c, dict):
                    return {"status": "error", "code": 400, "message": "Each configuration must be a dictionary"}
                is_enabled = c.get("enabled", True)
                mode = c.get("mode", "").lower() if is_enabled else "simulation"
                port = c.get("port") if is_enabled else "None"
                if mode == "simulation":
                    port = "SIM" if is_enabled else "None"
                ctrl = c.get("controller_id", c.get("controller", "None")) if is_enabled else "None" 
                normalized_configs.append({
                    "device": c.get("device"),
                    "port": port,
                    "controller": ctrl,
                    "mode": mode
                })
        else:
            return {"status": "error", "code": 400, "message": "Configs must be a list or dictionary"}

        if not normalized_configs:
            return {"status": "error", "code": 400, "message": "No active devices configured"}

        configs = normalized_configs
        assigned_ports = set()
        assigned_controllers = set()

        for c in configs:
            dev = c.get("device")
            port = c.get("port")
            ctrl = c.get("controller", "None")

            if not dev:
                return {"status": "error", "code": 400, "message": "Device name is required"}

            if port == "Headless":
                port = "SIM"
                c["port"] = "SIM"

            if dev != "Red Percent Window" and port not in ("SIM", "None"):
                if port in assigned_ports:
                    return {
                        "status": "error",
                        "code": 400,
                        "message": f"Port collision: Port '{port}' is assigned to multiple devices"
                    }
                assigned_ports.add(port)

            if ctrl and "None" not in ctrl and "Virtual" not in ctrl and "N/A" not in ctrl and dev != "Red Percent Window":
                if ctrl in assigned_controllers:
                    return {
                        "status": "error",
                        "code": 400,
                        "message": f"Controller collision: Controller '{ctrl}' is assigned to multiple devices"
                    }
                assigned_controllers.add(ctrl)

        # Instantiate Domain Models via SystemManager
        from model.system_manager import SystemManager
        new_manager = SystemManager()
        active_claims = {}

        for c in configs:
            dev = c["device"]
            port = c["port"]
            ctrl = c.get("controller", "None")

            try:
                if dev == "Stepper Probe":
                    from model.probes import StepperProbe
                    new_manager.register_model(dev, StepperProbe(port, ctrl, active_claims))
                elif dev == "DC Probe":
                    from model.probes import DCProbe
                    new_manager.register_model(dev, DCProbe(port, ctrl, active_claims))
                elif dev == "Chuck Positioner":
                    from model.probes import ChuckPositioner
                    new_manager.register_model(dev, ChuckPositioner(port, ctrl, active_claims))
                elif dev == "Temperature Controller":
                    from model.temperature_system import TemperatureSystem
                    new_manager.register_model(dev, TemperatureSystem(port))
                elif dev == "SMC100 Rotator":
                    from model.rotator_system import RotatorSystem
                    new_manager.register_model(dev, RotatorSystem(port))
                elif dev == "Red Percent Window":
                    from model.redpercent_system import RedPercentSystem
                    new_manager.register_model(dev, RedPercentSystem())
                else:
                    # Generic mock model or custom device if provided in config
                    pass
                
                # Apply disabled state so all devices initialize as connected but disabled
                model = new_manager.get_model(dev)
                if model:
                    model._disabled_in_setup = not c.get("enabled", True)
                    if hasattr(model, 'system_enabled'):
                        model.system_enabled = False
                    elif hasattr(model, 'disable'):
                        # Fallback to general disable
                        model.disable()

            except Exception as e:
                print(f"[WebModelAdapter] Failed to initialize device '{dev}':\n{traceback.format_exc()}")
                return {
                    "status": "error",
                    "code": 500,
                    "message": f"Failed to initialize device '{dev}': {str(e)}",
                }

        # Link Red Percent Window probes if active
        red_model = new_manager.active_models.get("Red Percent Window")
        if red_model:
            probe_models = {k: v for k, v in new_manager.active_models.items() if hasattr(v, 'pos_x')}
            red_model.available_probes = probe_models
            if "Stepper Probe" in probe_models:
                red_model.set_stepper_model("Stepper Probe")
            elif probe_models:
                red_model.set_stepper_model(list(probe_models.keys())[0])

        with self._state_lock:
            old_manager = self.system_manager
            self.system_manager = new_manager
            self.mode = "running"

        # Tear down the outgoing manager's models (closes serial ports) outside
        # the lock — shutdown_all does blocking hardware I/O and must not stall
        # API access to the manager we just installed.
        if old_manager and old_manager is not new_manager:
            old_manager.shutdown_all()

        return {
            "status": "ok",
            "code": 200,
            "mode": self.mode,
            "initialized_devices": list(new_manager.active_models.keys())
        }

    def initialize_system(self, device_configs: Union[Dict[str, Any], List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        Accepts device configurations as either:
        1. A list of dicts: [{"device": "...", "port": "...", "controller": "...", "mode": "hardware" | "simulation"}]
        2. A dict mapping device names to configs: {"Stepper Probe": {"port": "...", "controller_id": "...", "enabled": True, "mode": "..."}}
        Instantiates and registers the appropriate hardware or simulated models into SystemManager.
        """
        return self.initialize_setup(device_configs)

    def get_system_info(self) -> Dict[str, Any]:
        """Returns current system setup/running status and list of active devices."""
        with self._state_lock:
            active_devs = []
            if self.system_manager:
                models = getattr(self.system_manager, "active_models", {})
                active_devs = list(models.keys())
            return {
                "status": self.mode,
                "active_devices": active_devs
            }

    def get_devices(self) -> Dict[str, Any]:
        """Returns connected devices and their respective ui_schema."""
        devices = {}
        with self._state_lock:
            if self.system_manager:
                models = getattr(self.system_manager, "active_models", {})
                for name, model in models.items():
                    schema = getattr(model, "ui_schema", {"sections": []}).copy()
                    if getattr(model, "_disabled_in_setup", False):
                        schema["_disabled"] = True
                    devices[name] = schema
        return devices

    def _determine_connection_status(self, model) -> str:
        """Determines if device is 'simulated', 'hardware', or 'disconnected'."""
        if model.__class__.__name__ == "RedPercentSystem":
            return "online"
            
        if hasattr(model, "connection_status") and getattr(model, "connection_status"):
            status_val = str(getattr(model, "connection_status")).lower()
            if status_val in ("hardware", "simulated", "disconnected"):
                return status_val

        # Check explicit simulation flags
        if getattr(model, "is_simulated", False):
            return "simulated"

        # Check port
        port = getattr(model, "serial_port", getattr(model, "port", getattr(model, "default_port", None)))
        if port in ("SIM", "Headless", "Simulated"):
            return "simulated"

        # Check is_connected attribute
        if hasattr(model, "is_connected"):
            conn = model.is_connected
            is_conn = conn() if callable(conn) else conn
            if not is_conn:
                return "disconnected"
            return "hardware"

        # Check serial_comm or serial_conn or smc
        ser = getattr(model, "serial_comm", getattr(model, "serial_conn", getattr(model, "smc", None)))
        if ser is not None:
            ser_port = getattr(ser, "SERIAL_PORT", getattr(ser, "port", None))
            if ser_port == "SIM":
                return "simulated"
            # If pySerial instance or custom serial wrapper with .ser
            ser_obj = getattr(ser, "ser", ser)
            if hasattr(ser_obj, "is_open"):
                return "hardware" if ser_obj.is_open else "disconnected"
            elif hasattr(ser_obj, "isOpen"):
                return "hardware" if ser_obj.isOpen() else "disconnected"

        # If connected flag exists
        if hasattr(model, "connected"):
            return "hardware" if model.connected else "disconnected"

        # If serial port was provided but no active serial object
        if port and port not in ("SIM", "Headless", "None", ""):
            return "disconnected"

        return "disconnected"

    def get_state(self) -> Dict[str, Any]:
        """Returns live hardware/device attributes (position, busy flag, status, connection_status, etc.)."""
        state = {}
        with self._state_lock:
            if not self.system_manager:
                return state
            models = dict(getattr(self.system_manager, "active_models", {}))

        for name, model in models.items():
            dev_lock = self._get_device_lock(name)
            with dev_lock:
                # Invoke polling/reading routines if present
                if hasattr(model, "read_position") and callable(model.read_position):
                    try:
                        model.read_position()
                    except Exception:
                        pass
                if hasattr(model, "poll_status") and callable(model.poll_status):
                    try:
                        model.poll_status()
                    except Exception:
                        pass

                model_state = {}
                schema = getattr(model, "ui_schema", {"sections": []})
                for sec in schema.get("sections", []):
                    for el in sec.get("elements", []):
                        attr = el.get("model_attr")
                        if attr and hasattr(model, attr):
                            model_state[attr] = getattr(model, attr)

                # Attach connection_status badging
                model_state["connection_status"] = self._determine_connection_status(model)
                state[name] = model_state
        return state

    @staticmethod
    def _schema_commands(model) -> set:
        """Every command/options_command name a model's own ui_schema exposes.
        This is the allowlist: only what the UI can already trigger is
        dispatchable over the API — never arbitrary attribute/method access."""
        allowed = set()
        schema = getattr(model, "ui_schema", {"sections": []})
        for sec in schema.get("sections", []):
            for el in sec.get("elements", []):
                for key in ("command", "options_command"):
                    val = el.get(key)
                    if val:
                        allowed.add(val)
        return allowed

    @staticmethod
    def _schema_attrs(model) -> set:
        """Every model_attr a model's own ui_schema exposes for writing."""
        allowed = set()
        schema = getattr(model, "ui_schema", {"sections": []})
        for sec in schema.get("sections", []):
            for el in sec.get("elements", []):
                attr = el.get("model_attr")
                if attr:
                    allowed.add(attr)
        return allowed

    def dispatch_command(self, device_name: str, command_name: str, args: Any = None) -> Dict[str, Any]:
        """
        Thread-safely dispatches a command (move, stop, home, calibrate, etc.)
        to the target device model using per-device locking. Only commands the
        device's own ui_schema declares are eligible for dispatch.
        """
        if args is None:
            args = []

        with self._state_lock:
            if not self.system_manager:
                return {"status": "error", "code": 500, "message": "SystemManager not initialized"}

            model = getattr(self.system_manager, "active_models", {}).get(device_name)
            if not model:
                return {"status": "error", "code": 404, "message": f"Device {device_name} not found"}

            # Not in the allowlist and doesn't exist are reported identically:
            # don't let a caller distinguish "blocked" from "doesn't exist".
            func = getattr(model, command_name, None)
            if command_name not in self._schema_commands(model) or not func or not callable(func):
                return {"status": "error", "code": 400, "message": f"Command {command_name} not found on {device_name}"}

        dev_lock = self._get_device_lock(device_name)
        with dev_lock:
            try:
                if isinstance(args, list):
                    res = func(*args)
                elif isinstance(args, dict):
                    res = func(**args)
                else:
                    res = func(args)
                return {"status": "ok", "code": 200, "result": str(res)}
            except Exception as e:
                print(f"[WebModelAdapter] dispatch_command({device_name}.{command_name}) failed:\n{traceback.format_exc()}")
                return {
                    "status": "error",
                    "code": 500,
                    "message": str(e),
                }

    def set_device_attribute(self, device_name: str, attr: str, value: Any) -> Dict[str, Any]:
        """
        Thread-safely updates configurable parameters/attributes on target devices.
        Performs safe type coercion matching existing attribute types. Only
        attributes the device's own ui_schema declares as model_attr are
        eligible for writing.
        """
        with self._state_lock:
            if not self.system_manager:
                return {"status": "error", "code": 500, "message": "SystemManager not initialized"}

            model = getattr(self.system_manager, "active_models", {}).get(device_name)
            if not model:
                return {"status": "error", "code": 404, "message": f"Device {device_name} not found"}

            if attr not in self._schema_attrs(model):
                return {"status": "error", "code": 403, "message": f"Attribute {attr} is not exposed on {device_name}"}

        dev_lock = self._get_device_lock(device_name)
        with dev_lock:
            try:
                # Type cast if model attribute exists with known type
                if hasattr(model, attr):
                    curr = getattr(model, attr)
                    if isinstance(curr, bool):
                        value = str(value).lower() in ("true", "1", "yes")
                    elif isinstance(curr, int):
                        value = int(value)
                    elif isinstance(curr, float):
                        value = float(value)
                setattr(model, attr, value)
                return {"status": "ok", "code": 200, "attr": attr, "value": getattr(model, attr)}
            except Exception as e:
                print(f"[WebModelAdapter] set_device_attribute({device_name}.{attr}) failed:\n{traceback.format_exc()}")
                return {"status": "error", "code": 500, "message": str(e)}

    def append_log(self, message: str, max_size: int = 500):
        with self._state_lock:
            self.log_buffer.append(message)
            if len(self.log_buffer) > max_size:
                self.log_buffer.pop(0)

    def get_logs(self) -> List[str]:
        with self._state_lock:
            return list(self.log_buffer)

    def append_error(self, err_dict: Dict[str, Any], max_size: int = 500):
        with self._state_lock:
            self.error_buffer.append(err_dict)
            if len(self.error_buffer) > max_size:
                self.error_buffer.pop(0)

    def pop_errors(self) -> List[Dict[str, Any]]:
        with self._state_lock:
            errors = list(self.error_buffer)
            self.error_buffer.clear()
            return errors
