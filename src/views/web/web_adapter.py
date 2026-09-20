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
        # Re-setup is single-flight (RC-10 item 1). Two concurrent rebuilds
        # would each tear down and re-open the same ports, and the loser would
        # leave orphaned models holding them. A second caller gets 409.
        self._reconfiguring = threading.Lock()
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
        """Scans and enumerates available serial COM ports and gamepad controllers.

        Port listing only — deliberately does not probe device type per port
        (that requires opening each port and waiting up to ~4.5s per baud
        rate, same as tkinter/pyside's background-threaded scan). Doing that
        synchronously here would turn this fast GET into a multi-second block
        per connected port. Real device-type autodetection for the web view
        needs an async scan flow (background task + poll/websocket), not a
        loop bolted onto this handler; tracked as a follow-up, not done here.
        """
        ports = ["SIM"]
        import app_bootstrap
        discovered = app_bootstrap.discover_ports()
        for p in discovered:
            if p not in ports:
                ports.append(p)

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

        Single-flight: a second concurrent re-setup gets 409 rather than
        racing the first one onto the same serial ports (RC-10 item 1).
        """
        if not self._reconfiguring.acquire(blocking=False):
            return {"status": "error", "code": 409,
                    "message": "A device re-setup is already in progress"}
        try:
            return self._initialize_setup_locked(configs)
        finally:
            self._reconfiguring.release()

    def _initialize_setup_locked(self, configs):
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
        import app_bootstrap
        
        # We need to map Headless to SIM for configs
        for c in configs:
            if c.get("port") == "Headless":
                c["port"] = "SIM"
                
        errors = app_bootstrap.validate_assignment(configs)
        if errors:
            return {
                "status": "error",
                "code": 400,
                "message": "\n".join(errors)
            }
        # Re-setup goes through the manager's reconfigure(), which tears the
        # outgoing models down *before* building the new ones. This used to
        # build first and shut down afterwards, so for the duration of the
        # rebuild two live handles existed on the same port — the second
        # open would fail or silently attach to a half-closed device
        # (MANAGER-4, SERIAL-5, TEMP-8, ROTATOR-14; invariant I-1.4).
        import lifecycle
        from model.system_manager import SystemManager

        with self._state_lock:
            old_manager = self.system_manager

        active_claims = {}

        def _build(manager):
            active_models = app_bootstrap.build_models(configs, active_claims)
            for dev, model in active_models.items():
                manager.register(dev, model)
            return active_models

        new_manager = SystemManager()
        try:
            if old_manager is not None:
                old_manager.shutdown_all()
            active_models = _build(new_manager)
        except Exception as e:
            import traceback
            print(f"[WebModelAdapter] Failed to initialize devices:\n{traceback.format_exc()}")
            # Roll back whatever was built before the failure, so a failed
            # re-setup does not leave orphaned models holding ports open.
            new_manager.shutdown_all()
            return {
                "status": "error",
                "code": 500,
                "message": f"Failed to initialize devices: {str(e)}"
            }

        for dev, model in active_models.items():
            cfg = next((c for c in configs if c["device"] == dev), {})
            model._disabled_in_setup = not cfg.get("enabled", True)
            # Put the hardware in a known-disabled state rather than asserting
            # one. This used to write `model.system_enabled = False` when the
            # attribute existed and only fall back to `disable()` otherwise —
            # i.e. for every probe it declared the system disabled without
            # telling the board, which is the RC-2 belief-vs-reality defect in
            # miniature. `system_enabled` is a read-only property now (RC-3),
            # so the write is not merely wrong, it raises.
            if hasattr(model, 'disable'):
                model.disable()
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
            self.system_manager = new_manager
            self.mode = "running"

        # Exit hooks resolve the manager when they fire, so the new one takes
        # over immediately; a captured reference would keep stopping the
        # manager this call just replaced.
        lifecycle.set_current_manager(new_manager)

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

        # Ask the transport first (RC-5 item 3). Everything below this is the
        # old guessing ladder, which inferred the badge from the *editable*
        # serial_port field — so typing "SIM" into a hardware probe's port box
        # relabelled it as simulated while it went on driving real hardware
        # (DC-13). It also could not distinguish "port opened" from "board
        # answered", so a cable into a powered-off board badged as connected.
        state = self._transport_state(model)
        if state is not None:
            return state

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

    #: ConnectionState -> the badge vocabulary the dashboard already renders.
    _BADGE_FOR_STATE = {
        "simulated": "simulated",
        "verified": "hardware",
        "unverified": "unverified",
        "connecting": "connecting",
        "lost": "disconnected",
        "closed": "disconnected",
    }

    def _transport_state(self, model):
        """The transport's own account of the link, or None if it has none."""
        transport = getattr(model, "serial_comm", None) or getattr(model, "serial_conn", None)
        state = getattr(transport, "connection_state", None)
        if state is None:
            return None
        return self._BADGE_FOR_STATE.get(str(state), None)

    def get_state(self) -> Dict[str, Any]:
        """Returns live hardware/device attributes (position, busy flag, status, connection_status, etc.)."""
        state = {}
        with self._state_lock:
            if not self.system_manager:
                return state
            manager = self.system_manager
        models = manager.get_active_models_snapshot()

        for name, model in models.items():
            dev_lock = self._get_device_lock(name)
            with dev_lock:
                # No hardware I/O here (RC-4, invariant I-4.1). /api/state used
                # to call read_position() and poll_status() inline, so the
                # sampling rate was whatever the browser happened to poll at,
                # and a stalled read blocked the HTTP handler thread. The model
                # samples on its own thread; this reads the cache.
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
    def _schema_options_commands(model) -> set:
        """Every options_command name a model's own ui_schema exposes —
        deliberately narrower than _schema_commands, which also includes
        plain "command" entries (real actions, e.g. home_axis). Mixing the
        two would let a GET request to /api/options invoke a
        side-effecting action (real hardware motion) through what must
        stay a read-only options lookup."""
        allowed = set()
        schema = getattr(model, "ui_schema", {"sections": []})
        for sec in schema.get("sections", []):
            for el in sec.get("elements", []):
                val = el.get("options_command")
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

    def resolve_options(self, device_name: str, options_command: str) -> Dict[str, Any]:
        with self._state_lock:
            if not self.system_manager:
                return {"status": "error", "code": 500, "message": "SystemManager not initialized"}
            model = getattr(self.system_manager, "active_models", {}).get(device_name)
            if not model:
                return {"status": "error", "code": 404, "message": f"Device {device_name} not found"}
            if options_command not in self._schema_options_commands(model):
                return {"status": "error", "code": 400, "message": f"{options_command} is not an exposed options_command on {device_name}"}
            func = getattr(model, options_command, None)
            if not func or not callable(func):
                return {"status": "error", "code": 400, "message": f"{options_command} not found on {device_name}"}
                
        dev_lock = self._get_device_lock(device_name)
        with dev_lock:
            try:
                result = func()
                return {"status": "ok", "code": 200, "options": list(result) if result else []}
            except Exception as e:
                return {"status": "error", "code": 500, "message": str(e)}

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

    def full_stop_all(self) -> Dict[str, Any]:
        with self._state_lock:
            if not self.system_manager:
                return {"status": "error", "code": 500, "message": "SystemManager not initialized"}
            
        try:
            self.system_manager.full_stop_all()
            return {"status": "ok", "code": 200}
        except Exception as e:
            import traceback
            print(f"[WebModelAdapter] full_stop_all failed:\n{traceback.format_exc()}")
            return {"status": "error", "code": 500, "message": str(e)}

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
                # A read-only property is not writable through the API. This
                # is invariant I-3.4: the mode flags (`manual_flag`,
                # `auton_flag`, `system_enabled`) render as schema toggles but
                # must not be settable, or a POST arms a mode without the
                # gamepad check and without the hardware enable (STEPPER-11,
                # DC-11). 403, not 500 — it is a refusal, not a crash.
                descriptor = getattr(type(model), attr, None)
                if isinstance(descriptor, property) and descriptor.fset is None:
                    return {"status": "error", "code": 403,
                            "message": f"Attribute {attr} is read-only on {device_name}"}
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
