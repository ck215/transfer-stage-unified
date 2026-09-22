"""The setup panel: ONE wizard, rendered by every view from its schema.

`Setup` is a `Panel`, so Tk, Qt and the Web client each render the same
description and there is no per-view setup code at all. It replaces the two
nested ~390-line wizards in `src/app.py` and the scan/setup half of
`web_adapter.py`, which disagreed about almost everything they shared: the Web
wizard invented placeholder gamepad names, dropped the `enabled` flag and so
built every unchecked device, and ran its own port enumeration in a `python3`
subprocess (WEB-4, WEB-15, WEB-16, MANAGER-12, MANAGER-18).

What it does, in the order the operator does it:

    scan_ports() / scan_gamepads()   what is attached
    scan()                           identify() every port, on a worker
    auto_assign()                    identity byte -> the matching row's port
    validate(configs)                refuse an impossible assignment
    build(configs)                   Controller.reset(), then construct

Autodetection is preserved exactly: the same Bluetooth/ttyS filtering and
USB-first sort, the same three-step handshake (SMC100 at 57600 first because
it is cheap, then the custom firmware at 500000 and 115200), and the same
"give up when asked" contract inside the polling loops (MANAGER-20).

Setup is the one place besides the models allowed to import
`station.devices.*` and the model classes: it is the composition root.
"""
import re
import threading
import time

from station import schema as sch
from station.devices import gamepad as gamepad_module
from station.devices import serial_port as serial_port_module
from station.devices.serial_port import ConnectionState, SerialPort
from station.events import events
from station.model import Model
from station.models.heater import Heater
from station.models.probe import ChuckPositioner, DCProbe, StepperProbe
from station.models.red_monitor import RedMonitor
from station.models.rotator import Rotator
from station.panel import Panel
from station.result import Refused

#: The mode dropdown, one row per model type.
OFF, HARDWARE, SIMULATED = "Off", "Hardware", "Simulated"
MODES = (OFF, HARDWARE, SIMULATED)

#: The desktop wizard's word for "no hardware"; the models' word is "SIM".
#: The translation used to happen at three different points in three
#: launchers - once *after* the collision check, so a headless device could
#: be reported as colliding with itself.
HEADLESS, SIM = "Headless", "SIM"

#: What `discover_ports` fell back to when pyserial was missing. Kept
#: verbatim: an operator who sees it knows what it means.
FALLBACK_PORTS = [HEADLESS, "COM1", "COM2", "COM3", "COM4"]

#: `DEV: s` / `<s>` - what the custom firmware answers with. Kept for the
#: case where `SerialPort.identity` hands back the raw reply rather than the
#: byte itself.
IDENTITY_PATTERN = re.compile(r"(?:DEV:\s*|<)([a-z])>?", re.IGNORECASE)

#: One baud attempt: 1.5 s for the board to leave its bootloader, then up to
#: 3.0 s of polling. `probe_device_at` spent exactly this long per baud.
PROBE_SECONDS = 4.5
#: The state a port reaches when the open itself failed.
LOST = ConnectionState.LOST.value
#: How often the wait is interrupted to ask whether the scan was cancelled.
#: The check that matters is the one *inside* the wait; between ports is not
#: enough, because that is not where the time goes (MANAGER-20).
PROBE_SLICE = 0.1


class _Stub:
    """Class attributes for a model class that has not declared its own yet.

    The six model classes are being written in parallel with this file and
    most are still skeletons, so `cls.NAME` currently inherits `Panel.NAME`
    ("Panel") for all six and `MODEL_TYPES` would collapse to one entry. Any
    attribute a class declares for itself wins; this table only fills the
    gaps, and it is deleted once the six classes carry their own. The values
    are the old registry's, unchanged (`src/model/devices.py`).
    """

    TABLE = {
        StepperProbe: ("Stepper Probe", "s", True, True),
        DCProbe: ("DC Probe", "d", True, True),
        ChuckPositioner: ("Chuck Positioner", "c", True, True),
        Heater: ("Temperature Controller", "t", True, False),
        # The SMC100 answers an ASCII protocol at 57600 rather than the
        # custom firmware's DEV: reply, so it has no identity byte.
        Rotator: ("SMC100 Rotator", None, True, False),
        # Screen capture, not hardware: no port, no gamepad.
        RedMonitor: ("Red Percent Window", None, False, False),
    }
    FIELDS = ("NAME", "IDENTITY", "NEEDS_PORT", "NEEDS_GAMEPAD")


def _declared(model_class, attribute):
    """`model_class`'s own value for `attribute`, or the stub table's.

    "Its own" means declared by the class or one of its bases *below* the
    contract base classes, so inheriting `Panel.NAME` does not count as
    declaring a name.
    """
    for base in model_class.__mro__:
        if base in (Model, Panel, object):
            break
        if attribute in base.__dict__:
            return base.__dict__[attribute]
    stub = _Stub.TABLE.get(model_class)
    if stub is not None:
        return stub[_Stub.FIELDS.index(attribute)]
    return getattr(model_class, attribute, None)


def _model_types():
    types = {}
    for model_class in (StepperProbe, DCProbe, ChuckPositioner, Heater,
                        Rotator, RedMonitor):
        types[_declared(model_class, "NAME")] = model_class
    return types


#: Registration order is display order, as it was in the old registry: the
#: wizard rows, the sidebar and the tab strip all iterate this, so the model
#: list cannot differ between frontends.
MODEL_TYPES = _model_types()


def _key_for(name):
    """A schema-safe attribute prefix for a model name ("DC Probe" -> dc_probe)."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "model"


class Setup(Panel):
    """The setup panel. Scans ports and gamepads, validates an assignment,
    constructs Models into the Controller.

    Absorbs: <app_bootstrap>, <model.devices>, WebModelAdapter

    MUST SATISFY:
    [CARRY] ONE setup for three views, with autodetection preserved: port
    scan, handshake identity byte, gamepad list. Scan runs off the UI thread
    with progress and can be cancelled. Build is all-or-nothing with
    rollback. The identity byte is checked against the model class. SIM
    works for every model. A disabled row builds nothing. Probing errors are
    reported, not swallowed. CLI args are strict.  (MANAGER-5, MANAGER-6,
    MANAGER-12, MANAGER-14, MANAGER-18, MANAGER-20, SERIAL-6, SERIAL-7,
    SERIAL-9, SERIAL-17, WEB-4, WEB-15, WEB-16, DC-12, DC-14, REDPERCENT-15,
    VIEW-TKINTER-7)
    """

    NAME = "Setup"
    SCANNING = "scanning"

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._rows = self._build_rows()
        self._schema = self._build_schema()
        self._lock = threading.RLock()
        self._scan_thread = None
        self._abort = threading.Event()
        self._ports = list(FALLBACK_PORTS)
        self._gamepads = ["None"]
        self._found = {}            # port -> model name the handshake gave
        self._warned_ports = set()  # one warning per port per scan
        self._warned_missing = set()
        self.scan_status = "Ready to scan."
        self.scan_progress = 0
        self.summary = "nothing selected"
        for key in self._rows:
            setattr(self, f"{key}_mode", OFF)
            setattr(self, f"{key}_port", HEADLESS)
            setattr(self, f"{key}_gamepad", "None")
            setattr(self, f"{key}_found", "")
        # The selection commands are per row, because a view sends a dropdown
        # choice as the command's only argument and nothing else identifies
        # the row. Binding them here keeps one implementation.
        for key in self._rows:
            for field in ("mode", "port", "gamepad"):
                setattr(self, f"set_{key}_{field}",
                        _Selector(self, key, field))
        self._refresh_summary()

    # -- what a view reads -------------------------------------------------
    @property
    def schema(self):
        return self._schema

    @property
    def mode_name(self):
        """`scanning` gates Launch, Scan and every dropdown; nothing else."""
        return self.SCANNING if self.is_scanning else "ready"

    @property
    def is_scanning(self):
        thread = self._scan_thread
        return bool(thread is not None and thread.is_alive())

    @property
    def state(self):
        snapshot = super().state
        with self._lock:
            found = dict(self._found)
            ports, gamepads = list(self._ports), list(self._gamepads)
        snapshot.update({
            "is_scanning": self.is_scanning,
            "scan": {"status": self.scan_status, "progress": self.scan_progress,
                     "is_scanning": self.is_scanning, "found": found},
            "ports": ports,
            "gamepads": gamepads,
            "configs": self.configs,
        })
        return snapshot

    @property
    def model_types(self):
        """Every model name, in display order. was <model.devices>.names"""
        return list(MODEL_TYPES)

    @property
    def configs(self):
        """The operator's current choices as build configs. Disabled rows are
        dropped here and nowhere else: Web used to carry them through with a
        stripped `enabled` flag and build every one of them (WEB-4)."""
        configs = []
        for key, row in self._rows.items():
            mode = getattr(self, f"{key}_mode")
            if mode == OFF:
                continue
            port = getattr(self, f"{key}_port")
            is_sim = mode == SIMULATED or (row["needs_port"] and port == HEADLESS)
            if not row["needs_port"]:
                port = None
            elif is_sim:
                port = SIM
            gamepad = getattr(self, f"{key}_gamepad") if row["needs_gamepad"] else "None"
            configs.append({
                "model": row["name"],
                "port": port,
                "gamepad": None if gamepad in ("None", "", None) else gamepad,
                # Derived from the port, never carried through: `mode` was an
                # input the web wizard sent and the desktop ones did not, so
                # two launchers produced different configs for one system.
                "sim": bool(is_sim),
            })
        return configs

    # -- options (one list serves every row) -------------------------------
    def port_options(self):
        with self._lock:
            return list(self._ports)

    def gamepad_options(self):
        with self._lock:
            return list(self._gamepads)

    def mode_options(self):
        return list(MODES)

    # -- scanning ----------------------------------------------------------
    def scan_ports(self):
        """Attached serial ports as the wizard shows them.
        was <app_bootstrap>.discover_ports

        Same filtering and ordering as today: Bluetooth/Wireless out, Linux
        `/dev/ttyS*` without a hwid out, everything back if that left nothing,
        USB first, "Headless" at the top.
        """
        listing = getattr(serial_port_module, "list_ports", None)
        if listing is None:
            self._warn_missing("list_ports", "serial_port.list_ports() is not "
                               "available; offering the placeholder port list")
            return list(FALLBACK_PORTS)
        try:
            entries = [self._port_entry(e) for e in listing()]
        except Exception as exc:
            events.warn("Port Listing Failed", str(exc), source=self.NAME,
                        exception=exc)
            return list(FALLBACK_PORTS)

        usable = []
        for name, hwid in entries:
            if "Bluetooth" in name or "Wireless" in name:
                continue
            if name.startswith("/dev/ttyS") and (not hwid or hwid == "n/a"):
                continue
            usable.append(name)
        if not usable and entries:
            usable = [name for name, _ in entries]

        ports = [HEADLESS] + sorted(usable, key=self._port_sort_key)
        if ports == [HEADLESS]:
            ports = list(FALLBACK_PORTS)
        events.debug("Ports", f"{len(ports) - 1} port(s): {ports[1:]}",
                     source=self.NAME)
        return ports

    @staticmethod
    def _port_entry(entry):
        """(name, hwid) from whatever `list_ports()` yields."""
        if isinstance(entry, str):
            return entry, None
        if isinstance(entry, (tuple, list)):
            name = str(entry[0])
            return name, (str(entry[1]) if len(entry) > 1 and entry[1] else None)
        if isinstance(entry, dict):
            return str(entry.get("device", entry.get("name", entry))), entry.get("hwid")
        return str(getattr(entry, "device", entry)), getattr(entry, "hwid", None)

    @staticmethod
    def _port_sort_key(name):
        is_usb = ("USB" in name or name.startswith(
            ("/dev/ttyACM", "/dev/ttyUSB", "/dev/cu.usb", "/dev/tty.usb")))
        return (0 if is_usb else 1, name)

    def scan_gamepads(self):
        """Attached gamepads as `["None", ...]`.
        was <app_bootstrap>.discover_controllers ('controller' now means only
        the Controller)

        Through the one SDL owner. The three enumerations this replaces
        disagreed: Web shelled out to a literal `python3`, found nothing, and
        then **invented** two placeholder entries that got a device no input
        at all (WEB-15, MANAGER-18, GAMEPAD-18). An empty list means no
        gamepad is attached, and every frontend now says so the same way.
        """
        hub = getattr(gamepad_module, "hub", None)
        if hub is None:
            self._warn_missing("hub", "gamepad.hub is not available; no "
                               "gamepad can be assigned")
            return ["None"]
        try:
            return ["None"] + [str(n) for n in hub.names]
        except Exception as exc:
            events.warn("Gamepad Listing Failed", str(exc), source=self.NAME,
                        exception=exc)
            return ["None"]

    def scan(self):
        """Start a scan on a worker thread. Never blocks a view.
        was WebModelAdapter.scan_hardware / start_hardware_scan /
        get_scan_status (progress is part of Setup.state)

        Single-flight: a scan already running is refused rather than started
        a second time over the same ports.
        """
        with self._lock:
            if self.is_scanning:
                raise Refused("A hardware scan is already running.")
            self._abort.clear()
            self._warned_ports.clear()
            self.scan_status = "Scanning..."
            self.scan_progress = 0
            thread = threading.Thread(target=self._scan_loop, daemon=True,
                                      name="setup-scan")
            self._scan_thread = thread
        events.debug("Scan", "started", source=self.NAME)
        thread.start()
        return True

    def cancel_scan(self):
        """Ask the scan to give up. It stops inside the current port's wait."""
        if not self.is_scanning:
            raise Refused("No scan is running.")
        self._abort.set()
        events.debug("Scan", "cancel requested", source=self.NAME)
        return True

    def _scan_loop(self):
        started = time.monotonic()
        ports, gamepads = self.scan_ports(), self.scan_gamepads()
        with self._lock:
            self._ports, self._gamepads = ports, gamepads
        self._drop_stale_selections()
        targets = [p for p in ports if p != HEADLESS]
        for index, port in enumerate(targets):
            if self._abort.is_set():
                break
            self.scan_status = f"Scanning {port}..."
            found = self.identify(port, should_abort=self._abort.is_set)
            with self._lock:
                self._found[port] = found
            if found:
                events.info("Device Found", f"{found} on {port}", source=self.NAME)
            self.scan_progress = int(((index + 1) / len(targets)) * 100)
        if self._abort.is_set():
            self.scan_status = "Scan cancelled."
        else:
            self.scan_progress = 100
            self.scan_status = "Scan complete."
        self._mark_found()
        events.debug("Scan", f"{self.scan_status} {len(targets)} port(s) in "
                     f"{time.monotonic() - started:.1f} s", source=self.NAME)

    def identify(self, port, should_abort=None):
        """Identify whatever is on `port`, or None.
        was <app_bootstrap>.probe_device_at

        `should_abort` is an optional zero-argument predicate: return True and
        the scan gives up at the next check, without opening any further port
        (MANAGER-20). The check is made between attempts **and inside the
        waits**, which is where the time actually goes. A predicate that
        raises is treated as "do not abort": a broken abort hook must not be
        able to stop the scan working at all.

        Every failure is reported (`events.warn`, once per port). The old
        implementation wrapped each of its three attempts in a bare
        `except Exception: pass`, so a port that raised on every open was
        indistinguishable from a port with nothing attached (SERIAL-17).
        """
        def aborted():
            if should_abort is None:
                return False
            try:
                return bool(should_abort())
            except Exception:
                return False

        if aborted():
            return None

        started = time.monotonic()
        # 1. 57600 baud, SMC100-specific and cheap - tried first so the
        # rotator does not have to burn through both firmware handshake
        # timeouts before reaching the check that identifies it.
        name = self._identify_rotator(port)
        if name or aborted():
            self._log_probe(port, name, started)
            return name

        # 2. and 3. the custom firmware, at its two baud rates.
        for baud in (500000, 115200):
            if aborted():
                break
            name = self._identify_firmware(port, baud, aborted)
            if name:
                break
        self._log_probe(port, name, started)
        return name

    def _identify_rotator(self, port):
        query = getattr(serial_port_module, "query", None)
        if query is None:
            self._warn_missing("query", "serial_port.query() is not available; "
                               "the SMC100 handshake is skipped")
            return None
        name = _declared(Rotator, "NAME")
        if name not in MODEL_TYPES:
            return None
        try:
            reply = query(port, 57600, b"1ID?\r\n", xonxoff=True)
            if not self._text(reply):
                reply = query(port, 57600, b"1TS?\r\n", xonxoff=True)
            text = self._text(reply)
        except Exception as exc:
            self._warn_probe(port, exc)
            return None
        return name if text.startswith(("1ID", "1TS")) else None

    def _identify_firmware(self, port, baud, aborted):
        device = None
        try:
            device = SerialPort(port, baud)
            device.open()
            identity = self._wait_identity(device, aborted)
            if self._status_of(device) == LOST:
                # `wait_open()` answers False both for "still connecting" and
                # for "the open failed"; the state is the honest answer, and
                # a port that could not be opened at all is worth saying out
                # loud rather than reporting as an empty socket.
                self._warn_probe(port, f"could not open at {baud} baud")
            return self._name_for_identity(identity)
        except Exception as exc:
            self._warn_probe(port, exc)
            return None
        finally:
            if device is not None:
                try:
                    device.close()
                except Exception as exc:
                    events.debug("Probe", f"{port}@{baud} close failed: {exc}",
                                 source=self.NAME, exception=exc)

    def _wait_identity(self, device, aborted):
        """`wait_open` in slices, so an abort is noticed inside the wait.

        `wait_open()` returns True once the connect worker has finished and
        the port is open - verified or not, so the identity it carries is the
        answer either way. False means "still connecting" *or* "the open
        failed"; only the state tells those apart, and a failed open is not
        worth waiting out the rest of the handshake budget for.
        """
        deadline = time.monotonic() + PROBE_SECONDS
        while True:
            if aborted():
                return None
            started = time.monotonic()
            if device.wait_open(PROBE_SLICE):
                return device.identity
            if self._status_of(device) == LOST:
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return device.identity
            # A `wait_open` that returns early must not turn this into a
            # busy loop: the port is being polled, not spun on.
            idle = PROBE_SLICE - (time.monotonic() - started)
            if idle > 0:
                time.sleep(min(idle, remaining))

    @staticmethod
    def _status_of(device):
        """The port's state as its one word. A test double may carry none."""
        return str(getattr(device, "status", "") or "")

    @staticmethod
    def _text(reply):
        if reply is None:
            return ""
        if isinstance(reply, (bytes, bytearray)):
            return bytes(reply).decode("utf-8", errors="ignore").strip()
        return str(reply).strip()

    def _name_for_identity(self, identity):
        """The identity byte, mapped through the model classes themselves.

        `DEVICE_MAP` used to be a separate literal that knew four of the six
        devices, which is how the sidebar, the builder and the identity map
        came to disagree (RC-7).
        """
        text = self._text(identity)
        if not text:
            return None
        identities = {str(_declared(cls, "IDENTITY")).lower(): name
                      for name, cls in MODEL_TYPES.items()
                      if _declared(cls, "IDENTITY")}
        if text.lower() in identities:
            return identities[text.lower()]
        match = IDENTITY_PATTERN.search(text)
        if match and match.group(1).lower() in identities:
            return identities[match.group(1).lower()]
        return None

    def _log_probe(self, port, name, started):
        events.debug("Probe", f"{port} -> {name or 'nothing'} in "
                     f"{(time.monotonic() - started) * 1000:.0f} ms",
                     source=self.NAME)

    def _warn_probe(self, port, exc):
        """One warning per port per scan; the rest go to the log file."""
        message = f"{port}: {exc or type(exc).__name__}"
        if port in self._warned_ports:
            events.debug("Probe Failed", message, source=self.NAME, exception=exc)
            return
        self._warned_ports.add(port)
        events.warn("Probe Failed", message, source=self.NAME, exception=exc)

    def _refuse(self, reason):
        """Every refusal reaches the log file, even when `build()` was called
        outside `Panel.run` (Addendum 1)."""
        events.debug("Refused", reason, source=self.NAME)
        raise Refused(reason)

    def _warn_missing(self, what, message):
        """One warning per missing collaborator, then silence."""
        if what in self._warned_missing:
            return
        self._warned_missing.add(what)
        events.warn("Not Available", message, source=self.NAME)

    # -- assignment --------------------------------------------------------
    def auto_assign(self):
        """Identity byte -> the matching model's port, for everything found."""
        assigned = []
        with self._lock:
            found = dict(self._found)
        for port, name in found.items():
            key = self._key_of(name)
            if key is None:
                continue
            setattr(self, f"{key}_port", port)
            setattr(self, f"{key}_mode", HARDWARE)
            assigned.append(f"{name} on {port}")
        self._mark_found()
        self._refresh_summary()
        events.debug("Auto-assign", "; ".join(assigned) or "nothing to assign",
                     source=self.NAME)
        if not assigned:
            raise Refused("Nothing was identified; scan first, or assign by hand.")
        events.info("Auto-assign", ", ".join(assigned), source=self.NAME)
        return assigned

    def _select(self, key, field, choice):
        """Apply one dropdown choice. Reached through `set_<row>_<field>`."""
        if key not in self._rows:
            raise Refused(f"{key} is not a configurable model")
        choices = {"mode": self.mode_options(), "port": self.port_options(),
                   "gamepad": self.gamepad_options()}[field]
        row = self._rows[key]
        if field == "port" and not row["needs_port"]:
            raise Refused(f"{row['name']} does not use a port")
        if field == "gamepad" and not row["needs_gamepad"]:
            raise Refused(f"{row['name']} does not use a gamepad")
        if choice not in choices:
            raise Refused(f"{choice!r} is not one of {row['name']}'s "
                          f"{field} options")
        setattr(self, f"{key}_{field}", choice)
        events.debug("Selected", f"{row['name']} {field} = {choice}",
                     source=self.NAME)
        self._refresh_summary()
        return choice

    def validate(self, configs):
        """Refuse an impossible assignment, naming the field.
        was <app_bootstrap>.validate_assignment

        Simulated rows never collide: any number of them can share "SIM", and
        a model that needs no port cannot collide at all. "None" means *no*
        gamepad, so any number of rows can have none.
        """
        ports, gamepads = {}, {}
        for config in configs:
            name = config.get("model")
            model_class = MODEL_TYPES.get(name)
            if model_class is None:
                self._refuse(f"{name!r} is not a known model")
            port, gamepad = config.get("port"), config.get("gamepad")
            if _declared(model_class, "NEEDS_PORT") and not config.get("sim"):
                if not port or port in ("None", HEADLESS):
                    self._refuse(f"{name} port: choose a port, or set the "
                                 "row to Simulated")
                if port in ports:
                    self._refuse(f"{name} port: {port} is already assigned "
                                 f"to {ports[port]}")
                ports[port] = name
            if gamepad:
                if gamepad in gamepads:
                    self._refuse(f"{name} gamepad: {gamepad} is already "
                                 f"claimed by {gamepads[gamepad]}")
                gamepads[gamepad] = name
        events.debug("Validate", f"{len(configs)} row(s) ok", source=self.NAME)
        return True

    # -- building ----------------------------------------------------------
    def launch(self):
        """Validate the current choices and build them. The one Launch button."""
        configs = self.configs
        if not configs:
            raise Refused("Select at least one model to launch.")
        self.validate(configs)
        return self.build(configs)

    def build(self, configs=None):
        """Construct the configured models into the Controller.
        was <app_bootstrap>.build_models / _build_each, <model.devices>.get /
        build, WebModelAdapter.initialize_setup / initialize_system

        `Controller.reset()` runs FIRST, so the outgoing models are torn down
        before the new ones are built and one port never has two handles
        (MANAGER-4, SERIAL-5, TEMP-8, ROTATOR-14; invariant I-1.4).

        All or nothing: if a later model fails, every model already added is
        removed before this raises. Without that rollback a failed launch left
        earlier devices holding their serial ports open with no reference to
        them anywhere, and only a process restart recovered (MANAGER-5).
        """
        configs = self.configs if configs is None else list(configs)
        self.validate(configs)
        self._check_identities(configs)

        self.controller.factory = self.model_from_config
        self.controller.reset()
        built = []
        for config in configs:
            name = config["model"]
            started = time.monotonic()
            try:
                self.controller.add(name, self.model_from_config(config), config)
            except Exception as exc:
                self._roll_back(built)
                events.debug("Build Failed", f"{name}: {exc}", source=self.NAME,
                             exception=exc)
                raise Refused(f"Could not start {name}: {exc}. Nothing was "
                              "left running; adjust the configuration and "
                              "try again.")
            built.append(name)
            events.debug("Built", f"{name} port={config.get('port')} "
                         f"gamepad={config.get('gamepad')} sim={config.get('sim')} "
                         f"in {(time.monotonic() - started) * 1000:.0f} ms",
                         source=self.NAME)
        events.info("Launched", ", ".join(built), source=self.NAME)
        return built

    def model_from_config(self, config):
        """One config -> one Model. `Controller.factory`, so `reopen(name)`
        reconstructs a closed tab's model from the remembered config."""
        model_class = MODEL_TYPES.get(config.get("model"))
        if model_class is None:
            raise Refused(f"{config.get('model')!r} is not a known model")
        is_sim = bool(config.get("sim"))
        needs_port = _declared(model_class, "NEEDS_PORT")
        port = config.get("port")
        if not needs_port:
            port = None
        elif is_sim:
            port = SIM
        gamepad = config.get("gamepad") if _declared(
            model_class, "NEEDS_GAMEPAD") else None
        return model_class(port=port, gamepad=gamepad, sim=is_sim)

    def _check_identities(self, configs):
        """A row pointed at a port that answered as something else is a wiring
        mistake, not a build to attempt. A port that answered nothing is not:
        a handshake can miss a live board, and it always could."""
        with self._lock:
            found = dict(self._found)
        for config in configs:
            if config.get("sim"):
                continue
            answered = found.get(config.get("port"))
            if answered and answered != config["model"]:
                self._refuse(f"{config['model']} port: {config['port']} "
                             f"answered as {answered}")

    def _roll_back(self, built):
        for name in reversed(built):
            try:
                self.controller.remove(name)
            except Exception as exc:
                events.warn("Rollback Failed", f"{name}: {exc}",
                            source=self.NAME, exception=exc)

    # -- schema ------------------------------------------------------------
    def _build_rows(self):
        rows = {}
        for name, model_class in MODEL_TYPES.items():
            rows[_key_for(name)] = {
                "name": name,
                "needs_port": bool(_declared(model_class, "NEEDS_PORT")),
                "needs_gamepad": bool(_declared(model_class, "NEEDS_GAMEPAD")),
            }
        return rows

    def _build_schema(self):
        sections = [sch.section(
            "Hardware Scan",
            sch.readonly("Status:", "scan_status"),
            sch.readonly("Progress (%):", "scan_progress"),
            sch.button("Scan", "scan", role="info",
                       disabled_when=[self.SCANNING]),
            sch.button("Cancel Scan", "cancel_scan", role="warning",
                       enabled_when=[self.SCANNING]),
            sch.button("Auto-assign", "auto_assign",
                       disabled_when=[self.SCANNING]),
        )]
        for key, row in self._rows.items():
            elements = [sch.dropdown("Mode", f"{key}_mode",
                                     f"set_{key}_mode", "mode_options",
                                     disabled_when=[self.SCANNING])]
            if row["needs_port"]:
                elements.append(sch.dropdown("Port", f"{key}_port",
                                             f"set_{key}_port", "port_options",
                                             disabled_when=[self.SCANNING]))
            if row["needs_gamepad"]:
                elements.append(sch.dropdown("Gamepad", f"{key}_gamepad",
                                             f"set_{key}_gamepad",
                                             "gamepad_options",
                                             disabled_when=[self.SCANNING]))
            elements.append(sch.readonly("Detected:", f"{key}_found"))
            sections.append(sch.section(row["name"], *elements))
        sections.append(sch.section(
            "Launch",
            sch.readonly("Selected:", "summary"),
            sch.button("Launch", "launch", role="go",
                       disabled_when=[self.SCANNING]),
        ))
        return sch.schema(*sections)

    # -- plumbing ----------------------------------------------------------
    def _key_of(self, name):
        for key, row in self._rows.items():
            if row["name"] == name:
                return key
        return None

    def _mark_found(self):
        with self._lock:
            found = dict(self._found)
        for key, row in self._rows.items():
            if not row["needs_port"]:
                continue        # nothing to find: no port, no handshake
            where = [port for port, name in found.items() if name == row["name"]]
            setattr(self, f"{key}_found", where[0] if where else
                    ("" if not found else "not found"))

    def _drop_stale_selections(self):
        """A port that is no longer attached must not stay selected: the old
        wizard reset the dropdown to the first entry on every refresh."""
        ports, gamepads = self.port_options(), self.gamepad_options()
        for key, row in self._rows.items():
            if row["needs_port"] and getattr(self, f"{key}_port") not in ports:
                setattr(self, f"{key}_port", ports[0] if ports else HEADLESS)
            if row["needs_gamepad"] and getattr(self, f"{key}_gamepad") not in gamepads:
                setattr(self, f"{key}_gamepad", "None")

    def _refresh_summary(self):
        parts = []
        for config in self.configs:
            where = "simulated" if config["sim"] else config["port"]
            parts.append(f"{config['model']} ({where})")
        self.summary = ", ".join(parts) or "nothing selected"


class _Selector:
    """One row's dropdown handler.

    A view sends a dropdown choice as the command's only argument, so the row
    has to be part of the command name; this is what `set_<row>_<field>`
    resolves to. Callable rather than a lambda so it has a readable repr in
    the log and can be compared in a test.
    """

    __slots__ = ("setup", "key", "field")

    def __init__(self, setup, key, field):
        self.setup, self.key, self.field = setup, key, field

    def __call__(self, choice):
        return self.setup._select(self.key, self.field, choice)

    def __repr__(self):
        return f"<set_{self.key}_{self.field}>"
