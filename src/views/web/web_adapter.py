"""
Web MVC Adapter for Transfer Stage Unified.

Provides thread-safe model and controller abstraction bridging the
HTTP request handlers in `web_server.py` with the hardware models and SystemManager.
Ensures zero coupling to Qt/PySide6.
"""

import sys
import threading
import traceback
from model.schema import NeedsConfirmation
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
        import app_bootstrap

        ports = ["SIM"]
        discovered = app_bootstrap.discover_ports()
        for p in discovered:
            if p not in ports:
                ports.append(p)

        # The same enumeration the desktop launchers use (RC-9 item 1). This
        # was a `subprocess.run(["python3", ...])` — not `sys.executable`, so
        # inside a virtualenv it ran an interpreter that usually had no
        # pygame, silently produced nothing, and then **fabricated** two
        # placeholder entries so the wizard had something to show
        # (WEB-15, MANAGER-18, GAMEPAD-18). Assigning one of those names got
        # a device no input at all.
        controllers = app_bootstrap.discover_controllers()

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

        import app_bootstrap

        # One normalizer for all three frontends (RC-9 item 1). The version
        # that stood here kept disabled devices — with port "None" and a
        # controller of "None" — and then set `_disabled_in_setup` from the
        # dict it had just stripped the `enabled` key out of, so the flag was
        # always False and every unchecked device was built, polled, shown as
        # a live card and offered to Red Percent as a sync source
        # (WEB-4, MANAGER-12). `normalize_config` drops them instead.
        try:
            configs = app_bootstrap.normalize_config(configs)
        except TypeError as e:
            return {"status": "error", "code": 400, "message": str(e)}

        if not configs:
            return {"status": "error", "code": 400, "message": "No active devices configured"}

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

        def _build(manager):
            # Registering as it builds, like the desktop launchers, so the
            # manager carries each model's config too (I-9.2). This used to
            # build into a local dict and register afterwards, which left
            # `manager.configs` empty on the web path only.
            return app_bootstrap.build_models(configs, manager)

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
            # Put the hardware in a known-disabled state rather than asserting
            # one. This used to write `model.system_enabled = False` when the
            # attribute existed and only fall back to `disable()` otherwise —
            # i.e. for every probe it declared the system disabled without
            # telling the board, which is the RC-2 belief-vs-reality defect in
            # miniature. `system_enabled` is a read-only property now (RC-3),
            # so the write is not merely wrong, it raises.
            if hasattr(model, 'disable'):
                model.disable()

        # Wire each new model's poller log to this adapter's own log buffer
        # (WEB-5). `WebDashboardWindow.__init__` only wires models that
        # exist at construction time, and `run_web_app` builds the window
        # around an empty SystemManager before any device exists (the real
        # models are built here, later, by the setup wizard) - so that
        # wiring never ran for a model built through the web view, and the
        # Controller Log modal stayed empty forever. `append_log` already
        # caps the buffer at 500 under its own lock, so this closure does
        # not need - and must not repeat - a manual pop of its own; the
        # `WebAPIHandler.log_buffer` proxy that used to be poked directly
        # here has no `pop`, and calling it would raise AttributeError.
        for dev, model in active_models.items():
            poller = getattr(model, "poller", None)
            if poller is not None:
                def _make_logger(device_name):
                    def _log(message):
                        self.append_log(f"[{device_name}] {message}")
                    return _log
                poller.log_updater = _make_logger(dev)

        # Cross-model wiring, through the registry (RC-9 item 2) — the same
        # one call the desktop launchers make.
        app_bootstrap.link_models(new_manager)

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
                    # No `_disabled` flag any more (RC-9 item 3). A device the
                    # operator disabled is not constructed, so it is not
                    # registered and does not appear here at all — which is
                    # what Tk and PySide have always done. The flag it
                    # replaced was computed from a key normalization had
                    # already dropped, so it was never once set (WEB-4).
                    devices[name] = getattr(model, "ui_schema", {"sections": []}).copy()
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
                #
                # One device's read is isolated from the rest (WEB-14): a
                # raising property getter or a broken ui_schema used to blow
                # up the whole /api/state response, graying out every device
                # card over one bad one instead of just its own.
                try:
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
                except Exception as e:
                    state[name] = {"_error": str(e)}
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
                for key in ("command", "options_command", "data_command",
                            "source_command"):
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

    # Element types whose model_attr is meant to be operator-writable
    # (REDPERCENT-20, web half). `readonly` elements also carry a
    # `model_attr` — that's how they render the value — but that is a
    # display binding, not a write grant. Before this, `_schema_attrs`
    # allowlisted every element with a `model_attr` regardless of type, so
    # a client could POST /api/set_attr for a `readonly` field such as
    # RedPercentSystem's `current_red`/`red_change` and overwrite a value
    # the model computes from live monitoring data.
    _WRITABLE_ELEMENT_TYPES = frozenset({"entry", "dropdown", "toggle"})

    @classmethod
    def _schema_attrs(cls, model) -> set:
        """Every model_attr a model's own ui_schema exposes for writing."""
        allowed = set()
        schema = getattr(model, "ui_schema", {"sections": []})
        for sec in schema.get("sections", []):
            for el in sec.get("elements", []):
                attr = el.get("model_attr")
                if attr and el.get("type") in cls._WRITABLE_ELEMENT_TYPES:
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

    def dispatch_command(self, device_name: str, command_name: str,
                         args: Any = None, inputs: Any = None) -> Dict[str, Any]:
        """
        Thread-safely dispatches a command (move, stop, home, calibrate, etc.)
        to the target device model using per-device locking. Only commands the
        device's own ui_schema declares are eligible for dispatch.

        **`inputs` is D-5.** The client sends the current value of every field
        the command declared, and the model validates them as a set before
        running anything. Before this the Web client had no way at all to
        commit an edit and run a command atomically — it issued a `set_attr`
        per field and hoped, which is the same stale-value class the desktop
        views had, minus even Tk's focus flush.
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
                runner = getattr(model, "execute_command", None)
                if callable(runner):
                    call_args = args if isinstance(args, list) else [args]
                    res = runner(command_name, inputs=inputs or {},
                                 args=call_args)
                elif isinstance(args, list):
                    res = func(*args)
                elif isinstance(args, dict):
                    res = func(**args)
                else:
                    res = func(args)

                # The confirm contract (S10 item 3). A command that must not
                # proceed unattended comes back as a question rather than a
                # silent refusal — which is what it was here, because the
                # ±30° check depended on a callback only the desktop views
                # ever injected into the model.
                if isinstance(res, NeedsConfirmation):
                    return {
                        "status": "ok", "code": 200,
                        "needs_confirmation": {
                            "prompt": res.prompt,
                            "command": res.command,
                        },
                    }
                if res is False:
                    # A refused command is not an executed one.
                    return {"status": "error", "code": 400,
                            "message": f"{command_name} was refused"}
                return {"status": "ok", "code": 200, "result": str(res)}
            except Exception as e:
                print(f"[WebModelAdapter] dispatch_command({device_name}.{command_name}) failed:\n{traceback.format_exc()}")
                return {
                    "status": "error",
                    "code": 500,
                    "message": str(e),
                }

    def full_stop_all(self) -> Dict[str, Any]:
        """Broadcast FULL STOP and report what actually confirmed (WEB-18).

        `SystemManager.full_stop_all` already fans the stop out to every
        model on its own thread, lock-free, and returns `{name: ok}` without
        raising. This used to call it and report `status: ok` unconditionally,
        discarding that per-device result — so a model that failed to
        confirm looked identical, over the API, to a clean stop. The
        omission surfaced only later, and only if a toast happened to be
        seen, through the destructive error-poll path (WEB-9).

        Deliberately does NOT touch `self.system_manager.lock` or any
        per-device lock here: the whole point of `full_stop_all` is that it
        is not blocked by a wedged device lock, and wrapping it in one here
        would reintroduce exactly that.
        """
        with self._state_lock:
            if not self.system_manager:
                return {"status": "error", "code": 500, "message": "SystemManager not initialized"}
            manager = self.system_manager

        try:
            results = manager.full_stop_all()
        except Exception as e:
            print(f"[WebModelAdapter] full_stop_all failed:\n{traceback.format_exc()}")
            return {"status": "error", "code": 500, "message": str(e)}

        response: Dict[str, Any] = {"status": "ok", "code": 200}
        if isinstance(results, dict):
            response["results"] = results
            unconfirmed = sorted(name for name, ok in results.items() if not ok)
            if unconfirmed:
                response["status"] = "error"
                response["message"] = (
                    "FULL STOP did not confirm for: " + ", ".join(unconfirmed))
        return response

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
        """**Superseded by `errors_since` (RC-8 item 3).**

        Destructive by construction: whichever client polled first consumed
        the error and every other open tab never saw it (ERRORS-2, WEB-17).
        Kept only because the shutdown path drains the buffer through it;
        `/api/errors` does not call it any more.
        """
        with self._state_lock:
            errors = list(self.error_buffer)
            self.error_buffer.clear()
            return errors

    def errors_since(self, event_id: int = 0) -> List[Dict[str, Any]]:
        """Every event newer than `event_id`, oldest first, non-destructively.

        The client sends back the highest id it has seen, so two browser
        tabs each keep their own cursor and both receive everything. This is
        the Web half of the bus contract; the desktop views subscribe.
        """
        from error_routing import bus
        return [e.to_dict() for e in bus.since(event_id)]

    def latest_error_id(self) -> int:
        from error_routing import bus
        return bus.latest_id()
