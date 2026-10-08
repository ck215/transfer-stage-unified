"""The setup panel: ONE wizard, rendered by every view from its schema.

`Setup` is a `Panel`, so Tk, Qt and the Web client each render the same
description and there is no per-view setup code at all. It replaces the two
nested ~390-line wizards in `legacy/src/app.py` and the scan/setup half of
`web_adapter.py`, which disagreed about almost everything they shared: the Web
wizard invented placeholder gamepad names, dropped the `enabled` flag and so
built every unchecked device, and ran its own port enumeration in a `python3`
subprocess (WEB-4, WEB-15, WEB-16, MANAGER-12, MANAGER-18).

What it does, in the order the operator does it (Addendum 2):

    start()                          the scan begins by itself, at startup
    scan_ports() / scan_gamepads()   what is attached
    scan()                           identify() every port, on a worker
    auto_assign()                    identity byte -> the matching row's port,
                                     automatically, when a scan completes
    refresh()                        the one button: cancel, re-scan, re-assign
    validate(configs)                refuse an impossible assignment
    build(configs)                   Controller.reset(), then construct

**One dropdown per row.** The Mode dropdown is gone (owner ruling, Addendum
2): a row's Port choice carries the whole state. "Off" is the disabled row,
"SIM" is the simulator, and anything else is a real port. There is no second
control to contradict the first, which is what made three launchers produce
three different configs for one system.

Autodetection is preserved exactly: the same Bluetooth/ttyS filtering and
USB-first sort, the same three-step handshake (SMC100 at 57600 first because
it is cheap, then the custom firmware at 500000 and 115200), and the same
"give up when asked" contract inside the polling loops (MANAGER-20).

Setup is the one place besides the models allowed to import
`devices.*` and the model classes: it is the composition root.
"""
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import schema as sch
from controller import flashing, user_config
from controller.firmware import FirmwareCheck
from controller.updater import Updater
from devices import friendly_names
from devices import gamepad as gamepad_module
from devices import serial_port as serial_port_module
from devices.serial_port import ConnectionState, SerialPort
from events import events
from model import profile as profiles_module
from model import user_store as user_store_module
from model.base import Model
from model.heater import Heater
from model.probe import ChuckPositioner, DCProbe, StepperProbe
from model.rgb_analysis import RgbAnalysis
from model.rotator import Rotator
from model.sample_map import SampleMap
from model.transfer_map import TransferMap
from model.user import User
from model.user_store import AccountError, UserStore
from panel import Panel
from param import Param
from result import NeedsConfirm, Refused

#: The Port dropdown's one fixed entry. Everything else in the list is a
#: real port name. SIM is the development choice (a model without hardware);
#: it is the only way a row launches without a board that answered.
SIM = "SIM"
#: What a model that needs no port (the screen-capture monitor) offers instead
#: of a port name: it is either on (real) or simulated.
ON = "On"
#: A port row's value while no board has answered for it (owner 2026-10-07:
#: "on setup all connected devices should be launched"). Not an option: the
#: operator does not choose which devices to add; a row launches when its
#: board answered on its port (or it is set to SIM), and not otherwise.
NOT_CONNECTED = ""

#: What `discover_ports` fell back to when pyserial was missing. Kept
#: verbatim: an operator who sees it knows what it means. It is offered ONLY
#: when the listing itself is unavailable or raised - a listing that worked
#: and found nothing means no port is attached, and now says so, because
#: "Off" and "SIM" are always on offer and no longer need a placeholder.
FALLBACK_PORTS = ["COM1", "COM2", "COM3", "COM4"]

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
#: Refresh never waits on the calling (view) thread for a cancelled scan to
#: notice: a helper thread waits and starts the next scan (F18; the Qt audit
#: measured a 1 s freeze when Refresh joined here).


def _declared(model_class, attribute):
    """`model_class`'s own value for `attribute`, else whatever it inherits.

    "Its own" means declared by the class or one of its bases *below* the
    contract base classes; `_declares` asks only that question, so inheriting
    `Panel.NAME` does not count as naming a model.
    """
    for base in model_class.__mro__:
        if base in (Model, Panel, object):
            break
        if attribute in base.__dict__:
            return base.__dict__[attribute]
    return getattr(model_class, attribute, None)


def _declares(model_class, attribute):
    for base in model_class.__mro__:
        if base in (Model, Panel, object):
            return False
        if attribute in base.__dict__:
            return True
    return False


#: What Setup can fill in for a model, by the resource name's prefix: a
#: `port*` resource is a serial-port dropdown (SIM or a scanned port), a
#: `gamepad*` resource a gamepad dropdown. A class declares its own as
#: `RESOURCES = ("port", "gamepad")`, the keyword names its constructor takes.
RESOURCE_KINDS = ("port", "gamepad")


def _kind(resource):
    for kind in RESOURCE_KINDS:
        if str(resource).startswith(kind):
            return kind
    return None


def resources_of(model_class):
    """The keyword arguments Setup fills in when it constructs `model_class`,
    besides `sim`. A class's own `RESOURCES` wins; without one they follow
    from `NEEDS_PORT` / `NEEDS_GAMEPAD`, so a class written before resources
    existed needs no edit."""
    declared = _declared(model_class, "RESOURCES")
    if declared is not None:
        return (declared,) if isinstance(declared, str) else tuple(declared)
    resources = []
    if _declared(model_class, "NEEDS_PORT"):
        resources.append("port")
    if _declared(model_class, "NEEDS_GAMEPAD"):
        resources.append("gamepad")
    return tuple(resources)


#: Registration order is display order, as it was in the old registry: the
#: wizard rows, the sidebar and the tab strip all iterate this, so the model
#: list cannot differ between frontends. Filled by `register` only; mutated
#: in place, so every `from controller.setup import MODEL_TYPES` sees it.
MODEL_TYPES = {}


def register(model_class):
    """Add `model_class` to the station: a Setup row, identification by its
    `IDENTITY` byte or its `identify_port` hook, construction, and reopen.
    Returns the class, so it also works as a decorator.

    A module function rather than a Setup classmethod because the registry is
    module state that exists before any Setup is built (app.py and the tests
    read `MODEL_TYPES` straight from the module); `Setup.register` is the same
    function. Register before the `Setup` is constructed: the panel's rows are
    built once, from the registry as it stands then.

    Refused with ValueError, before anything is added: a class that names no
    model of its own, a name already taken by another class, a name whose row
    key is taken, an identity byte another model already answers with, and a
    resource Setup has no dropdown for. Registering the same class again is a
    no-op.
    """
    types = MODEL_TYPES
    if not _declares(model_class, "NAME"):
        raise ValueError(f"{model_class.__name__} declares no NAME of its own")
    name = model_class.NAME
    if types.get(name) is model_class:
        return model_class
    if name in types:
        raise ValueError(f"{name!r} is already registered to "
                         f"{types[name].__name__}")
    key = _key_for(name)
    for other in types:
        if _key_for(other) == key:
            raise ValueError(f"{name!r} would share the Setup row {key!r} "
                             f"with {other!r}")
    identity = _declared(model_class, "IDENTITY")
    if identity:
        for other, other_class in types.items():
            theirs = _declared(other_class, "IDENTITY")
            if theirs and str(theirs).lower() == str(identity).lower():
                raise ValueError(f"{name!r} answers with identity "
                                 f"{identity!r}, which {other!r} already does")
    resources = resources_of(model_class)
    for resource in resources:
        if _kind(resource) is None or resource in ("model", "sim"):
            raise ValueError(f"{name!r} declares resource {resource!r}; Setup "
                             "fills only port* and gamepad* resources")
    if len(set(resources)) != len(resources):
        raise ValueError(f"{name!r} declares a resource twice: {resources}")
    if getattr(model_class, "HOST", None) and resources:
        # A hosted model has no Setup row (owner ruling 2026-09-28: "it should
        # just be Transfer Map that calls both those tools"), so nothing could
        # fill a port or gamepad for it.
        raise ValueError(f"{name!r} is drawn on {model_class.HOST!r} and so "
                         f"has no Setup row; it cannot declare resources {resources}")
    types[name] = model_class
    return model_class


def _key_for(name):
    """A schema-safe attribute prefix for a model name ("DC Probe" -> dc_probe)."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "model"


#: Owner 2026-10-06: the Sample DB and the user profiles (sign-in) are OFF
#: until they are validated; the work is kept whole on the branch
#: `feature/sample-map-profiles` and in this tree. Turn either on for a run
#: with `STATION_SAMPLE_MAP=1` / `STATION_PROFILES=1`; to bring them back for
#: good, delete these two lines' defaults and the guards that read them.
# Merge of the lab's stage (2026-10-07): the Sample DB is ON by default
# again: the approved proposal of 2026-10-07 picks every trial's sample,
# chip and flake from it (the Transfer Map refuses Arm without them).
# `STATION_SAMPLE_MAP=0` turns it off. An owner call, flagged to the lead.
SAMPLE_MAP_ENABLED = os.environ.get("STATION_SAMPLE_MAP") != "0"
# Accounts (owner 2026-10-07) re-enable the Profile row as the Account
# section; Guest keeps the station defaults, so the bench runs unchanged
# until someone signs in. `STATION_PROFILES=0` hides the section.
PROFILES_ENABLED = os.environ.get("STATION_PROFILES") != "0"

# The built-ins, in today's display order. The Sample DB (flake-coords,
# 2026-10-04) follows the Transfer Map: its own page, no port.
for _built_in in (StepperProbe, DCProbe, ChuckPositioner, Heater, Rotator,
                  RgbAnalysis, TransferMap,
                  *((SampleMap,) if SAMPLE_MAP_ENABLED else ())):
    register(_built_in)
del _built_in

#: Owner 2026-10-07: "guest users should have no access to transfer map or
#: sample map, just tool controls." The classes (never their names, which
#: are being renamed) of the models only a signed-in user gets, with
#: accounts on; a model drawn on one of their pages (Model.HOST: RGB
#: Analysis on the Transfer Map's) goes with it. A Guest's launch skips
#: them, a sign-in adds them to a running station, and a switch back to
#: Guest removes them - refused while a trial is open.
SIGNED_IN_ONLY = (TransferMap, SampleMap)

# A3: the Transfer Map remembers the operator's store choice in the one
# choices file. Wired here, at the composition root: `model/` never imports
# the controller.
TransferMap.choices = user_config


def is_signed_in_only(name):
    """`name` is a `SIGNED_IN_ONLY` model, or one drawn on such a model's
    page (its `HOST`)."""
    owners = {cls.NAME for cls in SIGNED_IN_ONLY}
    if name in owners:
        return True
    return getattr(MODEL_TYPES.get(name), "HOST", None) in owners


#: `Setup(stable_root=...)`'s "find it yourself" (None means "there is none").
_UNSET = object()


class PortProbe:
    """Setup's port listing and identity handshake, without the panel: the
    one way a port is identified in this codebase. `Setup` is one; so is
    what `controller.flashing` uses to find the boards it flashes (the old
    flash script imported `legacy/src`'s copy and skipped every COM port,
    OP-12). Holds no port open between calls."""

    NAME = "Setup"

    def __init__(self):
        super().__init__()
        self._warned_ports = set()  # one warning per port per scan
        self._warned_missing = set()
        self._busy_ports = set()    # held by another program, this scan

    def scan_ports(self):
        """Attached serial ports as the wizard shows them.
        was <app_bootstrap>.discover_ports

        Same filtering and ordering as today: Bluetooth/Wireless out, Linux
        `/dev/ttyS*` without a hwid out, everything back if that left nothing,
        USB first. "Off" and "SIM" are added by `port_options`, not here.
        """
        listing = getattr(serial_port_module, "list_ports", None)
        if listing is None:
            self._warn_missing("list_ports", "serial_port.list_ports() is not "
                               "available; offering the placeholder port list")
            return list(FALLBACK_PORTS)
        try:
            entries = [self._port_entry(e) for e in listing()]
        except Exception as exc:
            events.debug("Port Listing Failed", repr(exc), source=self.NAME,
                         exception=exc)
            events.warn("Port Listing Failed", "The port list could not be "
                        "read, so a placeholder list is offered. Press Refresh "
                        "to try again.", source=self.NAME, exception=exc)
            return list(FALLBACK_PORTS)

        # The USB ids behind each path, for the dropdown's labels only.
        self._port_hwids = {name: hwid for name, hwid in entries}
        usable = []
        for name, hwid in entries:
            if "Bluetooth" in name or "Wireless" in name:
                continue
            if name.startswith("/dev/ttyS") and (not hwid or hwid == "n/a"):
                continue
            usable.append(name)
        if not usable and entries:
            usable = [name for name, _ in entries]

        ports = sorted(usable, key=self._port_sort_key)
        events.debug("Ports", f"{len(ports)} port(s): {ports}", source=self.NAME)
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
        # 1. The classes' own checks, for devices without a `DEV:` byte (the
        # SMC100 at 57600): tried first because they are cheap, so the
        # rotator does not have to burn through both firmware handshake
        # timeouts before reaching the check that identifies it.
        name = self._identify_by_class(port, aborted)
        if name or aborted() or port in self._busy_ports:
            self._log_probe(port, name, started)
            return name

        # 2. and 3. the custom firmware, at its two baud rates.
        for baud in (500000, 115200):
            if aborted():
                break
            name = self._identify_firmware(port, baud, aborted)
            if name or port in self._busy_ports:
                break
        self._log_probe(port, name, started)
        return name

    def _identify_by_class(self, port, aborted):
        """Ask each registered class that can identify its own board, in
        registration order; which check is cheap is the class's business.

        A class opts in with a classmethod `identify_port(port, should_abort)
        -> bool` (the Rotator's SMC100 query is the one built-in that does).
        A hook that raises is one "Probe Failed" for that port; the scan
        goes on.
        """
        for name, model_class in list(MODEL_TYPES.items()):
            if aborted():
                return None
            hook = getattr(model_class, "identify_port", None)
            if not callable(hook):
                continue
            try:
                if hook(port, aborted):
                    return name
            except Exception as exc:
                if serial_port_module.is_busy_error(exc):
                    self._note_busy(port, exc)
                    return None
                self._warn_probe(port, exc)
        return None

    def _identify_firmware(self, port, baud, aborted):
        device = None
        try:
            device = SerialPort(port, baud)
            device.probe = True   # the scan asks; an unanswered port is information
            device.open()
            identity = self._wait_identity(device, aborted)
            if self._status_of(device) == LOST and getattr(device, "open_busy", False) is True:
                self._note_busy(port, f"busy at {baud} baud")
            elif self._status_of(device) == LOST:
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

    def _note_busy(self, port, exc):
        """Another program holds `port` (bench 2026-10-07: a second station's
        scan reset the running station's boards). Nothing was sent to it and
        no further open is tried this scan; said once, plainly, not as a
        failure."""
        events.debug("Port In Use", f"{port}: {exc}", source=self.NAME)
        if port in self._busy_ports:
            return
        self._busy_ports.add(port)
        events.info("Port In Use", f"{port} is {serial_port_module.BUSY_WORDS} "
                    f"(another station still running?), so it was not checked. "
                    f"Quit that program, then press Refresh.", source=self.NAME)

    @property
    def busy_ports(self):
        """The ports the last scan found held by another program."""
        return sorted(self._busy_ports)

    def _warn_probe(self, port, exc):
        """One warning per port per scan; the rest go to the log file."""
        message = f"{port}: {exc or type(exc).__name__}"
        events.debug("Probe Failed", message, source=self.NAME, exception=exc)
        if port in self._warned_ports:
            return
        self._warned_ports.add(port)
        cause = re.sub(r"b'[^']*'|b\"[^\"]*\"", "", str(exc or "")).strip(" :;")
        events.warn("Probe Failed", f"{port} could not be checked"
                    f"{f' ({cause})' if cause else ''}. If a device is on it, "
                    "choose the port by hand.", source=self.NAME, exception=exc)

    def _warn_missing(self, what, message):
        """One warning per missing collaborator, then silence."""
        if what in self._warned_missing:
            return
        self._warned_missing.add(what)
        events.debug("Not Available", message, source=self.NAME)
        events.warn("Not Available", self._MISSING_SENTENCES.get(
            what, "Part of hardware detection is unavailable. Choose ports "
            "by hand."), source=self.NAME)

    #: What the operator reads when a collaborator is missing (F19); the
    #: module-level detail goes to the file log.
    _MISSING_SENTENCES = {
        "list_ports": "Ports cannot be listed on this computer, so a "
                      "placeholder list is offered. Choose ports by hand.",
        "hub": "Gamepads cannot be listed on this computer, so none can be "
               "assigned.",
    }


class Setup(PortProbe, Panel):
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
    #: `Setup.register(cls)`: the module's `register`, see there.
    register = staticmethod(register)
    #: `mode_name`, which is what `enabled_when` / `disabled_when` match.
    READY, SCANNING, LAUNCHED = "ready", "scanning", "launched"
    #: `state["scan"]["phase"]`: what the worker is doing right now, for a
    #: view that wants to show more than the status line.
    IDLE, LISTING, IDENTIFYING, DONE, CANCELLED = (
        "idle", "listing", "identifying", "done", "cancelled")

    #: A3: the Trial store row's fields (the same Open/New the Transfer
    #: Map's own Store section offers).
    PARAMS = {p.name: p for p in (
        Param("map_store_path", "text", default="", label="Store file"),
        Param("map_store_dir", "text", default="", label="Folder for a new store"),
        Param("map_store_name", "text", default="transfer_map",
              label="New store name"),
    )}

    def __init__(self, controller, updater=None, firmware=None, restart=None,
                 stable_root=_UNSET, stable_firmware=None, launch_stable=None,
                 exit_app=None):
        """`firmware` (rb-launch L2) checks and flashes the boards; `restart`
        (rb-restart R3) replaces this process with a fresh one
        (`app.restart_process`); None means this station cannot restart
        itself, and `restart_station` says so.

        A4, Switch to stable: `stable_root` is the bundle's `stable/` folder
        (default: found beside the launcher; None in a checkout, which hides
        the row), `stable_firmware` the FirmwareCheck over its sketches,
        `launch_stable(argv, cwd)` starts the stable app detached and
        `exit_app()` ends this process (`app.exit_process`)."""
        super().__init__()
        self._stable_root = (flashing.stable_root() if stable_root is _UNSET
                             else (None if stable_root is None else Path(stable_root)))
        self._stable_firmware = stable_firmware
        self._launch_stable = launch_stable or _launch_detached
        self._exit_app = exit_app
        legacy = TransferMap.legacy_store_path()
        if legacy is not None:
            self.map_store_path = str(legacy)   # offered, never opened for them
        self.controller = controller
        self._restart = restart
        # The accounts (2026-10-07; were the Phase 1 profiles): the station
        # scope, users.sqlite and the session, a Guest until someone signs in.
        self._init_accounts()
        self._rows = self._build_rows()
        self._schema = self._build_schema()
        self._lock = threading.RLock()
        self._scan_thread = None
        self._abort = threading.Event()
        self._ports = []
        self._gamepads = ["None"]
        self._found = {}            # port -> model name the handshake gave
        self._chosen = set()        # rows the operator set by hand
        self._warned_ports = set()  # one warning per port per scan
        self._warned_missing = set()
        self._busy_ports = set()    # held by another program, this scan
        self._is_launched = False
        #: Rows whose board answered only after the launch: {key: port}. Not
        #: launched (owner 2026-10-07: a new device needs a restart).
        self._seen = {}
        self._restart_thread = None     # Refresh's "then scan again" helper
        self._scan_started = None       # monotonic, for the elapsed seconds
        self._scan_port = None          # the port being probed right now
        self._port_started = None
        self.scan_phase = self.IDLE
        self.scan_status = "not scanned yet"
        self.scan_progress = 0
        self._selected = "nothing selected"
        for key, row in self._rows.items():
            setattr(self, f"{key}_name", row["name"])
            # `<row>_enabled` is what the row WILL launch with, derived in
            # `_refresh_rows`, never ticked (owner 2026-10-07). A port row
            # is not connected until its board answers; a row with no port
            # (the Transfer Map) is always on.
            setattr(self, f"{key}_enabled", False)
            setattr(self, f"{key}_port", NOT_CONNECTED if row["needs_port"] else ON)
            setattr(self, f"{key}_gamepad", "None")
            for field, kind, _, _ in row["columns"]:
                if field != "gamepad":
                    setattr(self, f"{key}_{field}",
                            SIM if kind == "port" else "None")
            setattr(self, f"{key}_status", "off")
        # The selection commands are per row, because a view sends a dropdown
        # choice as the command's only argument and nothing else identifies
        # the row. Binding them here keeps one implementation.
        for key, row in self._rows.items():
            fields = ["port", "gamepad"] + [
                field for field, _, _, _ in row["columns"] if field != "gamepad"]
            for field in fields:
                setattr(self, f"set_{key}_{field}",
                        _Selector(self, key, field))
            if row["needs_port"]:
                setattr(self, f"hard_reset_{key}", _HardReset(self, key))
        # Reopen goes through the registry from the start, not only after a
        # build: a model added with `controller.add(NAME, model, config)`
        # comes back from its remembered config like one Setup built.
        if controller is not None:
            controller.factory = self.model_from_config
        self._refresh_rows()
        # Last: the startup update check's thread reads nothing above.
        self._init_updates(updater)
        self._init_firmware(firmware)

    # -- what a view reads -------------------------------------------------
    @property
    def schema(self):
        return self._schema

    @property
    def mode_name(self):
        """`scanning` gates Launch and Relaunch; `launched` swaps Launch for
        Relaunch. The dropdowns are gated by neither: the operator may point a
        row at a port while the scan is still walking the rest of them, and
        that choice then wins over auto-assign."""
        if self.is_scanning:
            return self.SCANNING
        return self.LAUNCHED if self._is_launched else self.READY

    @property
    def is_scanning(self):
        thread = self._scan_thread
        return bool(thread is not None and thread.is_alive())

    @property
    def scan_status(self):
        """The one status line. While a port is being probed it names the port
        and how long it has been answering nothing, so a hung scan reads as
        hung rather than as a frozen line (F18)."""
        port, since = self._scan_port, self._port_started
        if port is None or since is None or not self.is_scanning:
            return self._scan_note
        waited = int(time.monotonic() - since)
        # The Launch row no longer carries a sentence (I4): while the scan
        # runs, the reason Launch is greyed out lives here, on the one line
        # the operator is already reading.
        return (f"{self._scan_note} {waited} s on this port. Launch waits "
                "for the scan; press Cancel scan to launch now.")

    @scan_status.setter
    def scan_status(self, text):
        self._scan_note = text

    @property
    def scan_elapsed(self):
        """Seconds since the running scan began, or None when none runs."""
        started = self._scan_started
        if started is None or not self.is_scanning:
            return None
        return round(time.monotonic() - started, 1)

    @property
    def summary(self):
        """What will launch, and - when Launch is greyed out or would refuse -
        why, as a sentence the operator can act on (F18)."""
        selected = self._selected
        if self.is_scanning:
            return ("Scanning. Launch waits for the scan to finish; "
                    "press Cancel scan to launch now.")
        if selected == "nothing selected":
            return ("Nothing to launch: no device is connected. Plug it in "
                    "and press Refresh.")
        return selected

    @summary.setter
    def summary(self, text):
        self._selected = text

    @property
    def is_launched(self):
        """True once `build()` has put models into the Controller. The views
        collapse the Setup panel on it; `stop_system()` clears it."""
        return self._is_launched

    @property
    def state(self):
        if self._offer_on_read:
            # A2: the Web page's first read of Setup; see `startup_checks`.
            with self._lock:
                self._offer_on_read, self._startup_listening = False, True
            self._deliver_startup_offer()
        snapshot = super().state
        with self._lock:
            found = dict(self._found)
            ports, gamepads = list(self._ports), list(self._gamepads)
            chosen = set(self._chosen)
            seen = dict(self._seen)
        is_scanning = self.is_scanning
        rows = []
        for key, row in self._rows.items():
            choice = getattr(self, f"{key}_port")
            rows.append({
                "key": key,
                "name": row["name"],
                "enabled": getattr(self, f"{key}_enabled"),
                "port": choice,
                "gamepad": getattr(self, f"{key}_gamepad"),
                "status": getattr(self, f"{key}_status"),
                "detected": found.get(choice) if choice not in (SIM, ON) else None,
                "needs_port": row["needs_port"],
                "needs_gamepad": row["needs_gamepad"],
                "is_chosen": key in chosen,
                # A board that answered after the launch: its port, until a
                # restart (or Close every model) lets it launch.
                "seen_after_launch": seen.get(key),
                "options_command": row["options_command"],
            })
        snapshot.update({
            "is_scanning": is_scanning,
            "is_launched": self._is_launched,
            # The sign-in gate (2026-10-07): the Web view shows its sign-in
            # screen while `account_chosen` is False.
            "account_chosen": self._account_chosen,
            "account": self.account,
            "scan": {"phase": self.scan_phase, "status": self.scan_status,
                     "progress": self.scan_progress, "is_scanning": is_scanning,
                     "ports": ports, "found": found,
                     "elapsed": self.scan_elapsed, "port": (
                         self._scan_port if is_scanning else None),
                     "restart_pending": self._is_restart_pending},
            "ports": ports,
            "gamepads": gamepads,
            "rows": rows,
            "configs": self.configs,
            "has_update": self.has_update,
            "update": {"status": self._update_code, "has_update": self.has_update,
                       "log": list(self._update_lines),
                       "is_checking": _alive(self._update_thread),
                       "is_applying": _alive(self._apply_thread),
                       "updated_to": self._updated_to},
        })
        return snapshot

    @property
    def model_types(self):
        """Every model name, in display order. was <model.devices>.names"""
        return list(MODEL_TYPES)

    @property
    def configs(self):
        """What Launch builds: every row that is connected (its board
        answered on its port), every row set to SIM, and every row with no
        port. A row that is not connected is dropped here and nowhere else
        (owner 2026-10-07; there is no Launch tick any more). A Guest's
        launch leaves out the signed-in-only models (`SIGNED_IN_ONLY`)."""
        configs = self._all_configs()
        if self.guest_locked:
            configs = [c for c in configs if not is_signed_in_only(c["model"])]
        return configs

    @property
    def guest_locked(self):
        """True while a Guest works with accounts on: the Transfer Map and
        the Sample Map (and what they host) are not theirs (owner
        2026-10-07)."""
        user = getattr(self, "user", None)
        return bool(PROFILES_ENABLED) and (user is None or user.is_guest)

    def session_refusal(self, name, command=""):
        """Why `name`'s `command` is refused for this session, or "": a
        signed-in-only model while a Guest works. The Web server asks
        before it runs any model command (a belt under their absence). A
        stop is never refused (`Panel.UNGATED_COMMANDS`)."""
        if command in Panel.UNGATED_COMMANDS:
            return ""
        if self.guest_locked and is_signed_in_only(name):
            return (f"{name} is for signed-in users: a Guest has the tool "
                    "controls only. Sign in (the account menu, Switch user) "
                    "to use it.")
        return ""

    def _all_configs(self):
        with self._lock:
            found = dict(self._found)
        configs = []
        for key, row in self._rows.items():
            if not self._will_launch(key, row, found):
                continue
            choice = getattr(self, f"{key}_port")
            is_sim = choice == SIM
            # The models drawn on this row's page (Model.HOST) launch with
            # it, before it, with no resources and its SIM choice: one row,
            # "Transfer Map", brings RGB Analysis (owner ruling 2026-09-28).
            for hosted_name, hosted_class in MODEL_TYPES.items():
                if getattr(hosted_class, "HOST", None) == row["name"]:
                    configs.append({"model": hosted_name, "port": None,
                                    "gamepad": None, "sim": bool(is_sim)})
            if not row["needs_port"]:
                port = None         # the screen monitor: on, or simulated
            elif is_sim:
                port = SIM
            else:
                port = choice
            gamepad = getattr(self, f"{key}_gamepad") if row["needs_gamepad"] else "None"
            config = {
                "model": row["name"],
                "port": port,
                "gamepad": None if gamepad in ("None", "", None) else gamepad,
                # Derived from the one dropdown, never carried through as its
                # own input: `mode` was an input the web wizard sent and the
                # desktop ones did not, so two launchers produced different
                # configs for one system.
                "sim": bool(is_sim),
            }
            # Every resource under its own name as well, so the config is
            # `{"model", ...resources, "sim"}` whatever the class calls them.
            # For the six built-ins these are `port` / `gamepad` themselves.
            if row["port_resource"] not in (None, "port"):
                config[row["port_resource"]] = port
            for field, kind, _, resource in row["columns"]:
                if field == "gamepad":
                    if resource != "gamepad":
                        config[resource] = config["gamepad"]
                    continue
                value = getattr(self, f"{key}_{field}")
                if kind == "port":
                    config[resource] = SIM if is_sim else value
                else:
                    config[resource] = None if value in ("None", "", None) else value
            configs.append(config)
        return configs

    # -- options (one list per row shape) ----------------------------------
    def port_options(self):
        """What a row that needs a port offers: the simulator, or a port."""
        with self._lock:
            return [SIM, *self._ports]

    def device_options(self):
        """What a row that needs no port offers. The screen-capture monitor
        has nothing to plug in, so its dropdown is the same control with the
        port names left out rather than a second kind of widget."""
        return [ON, SIM]

    def gamepad_options(self):
        with self._lock:
            return list(self._gamepads)

    def option_labels(self, command, options):
        """What the dropdown shows for each of `options` (the values one of
        the `*_options` commands above returned), in the same order. The
        values stay the raw port paths and gamepad ids; only the text the
        operator reads is friendlier ("Stepper Probe — ACM0", "Logitech
        F310"), falling back to the raw string for anything unknown."""
        options = [str(o) for o in options]
        if command == "gamepad_options":
            return friendly_names.gamepad_labels(options)
        with self._lock:
            found = dict(self._found)
            hwids = dict(getattr(self, "_port_hwids", {}))
        return [o if o in (SIM, ON) else
                friendly_names.port_label(o, found.get(o), hwids.get(o))
                for o in options]

    # -- scanning ----------------------------------------------------------
    def start(self):
        """Begin the automatic scan. `app.launch()` calls this immediately
        before the view opens, so the operator finds the scan already running
        instead of having to ask for one (Addendum 2). Never raises: a scan
        that is somehow already running is simply left alone."""
        if self.is_scanning:
            events.debug("Scan", "start: already scanning", source=self.NAME)
            return False
        self.scan()
        return True

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
            events.debug("Gamepad Listing Failed", repr(exc), source=self.NAME,
                         exception=exc)
            events.warn("Gamepad Listing Failed", "The gamepad list could not "
                        "be read. Press Refresh to try again.",
                        source=self.NAME, exception=exc)
            return ["None"]

    def refresh(self):
        """The one button: re-scan ports and gamepads.
        was WebModelAdapter.start_hardware_scan (as a Scan button)

        A scan already running is cancelled first, so Refresh always means
        "start again from what is attached now" and never has to be pressed
        twice. Single-flight: there is never a second scan over the same
        ports, which is what made the web wizard's progress jump backwards.

        **Never blocks the caller** (F18): with a scan still running, a helper
        thread waits for it to notice the cancel and then starts the next
        one. Refresh returns at once either way.
        """
        with self._lock:
            if self._is_restart_pending:
                return True             # already restarting; pressing again is fine
            if self.is_scanning:
                try:
                    self.cancel_scan()
                except Refused:
                    pass                # it finished between the two checks
                old = self._scan_thread
                self._restart_thread = threading.Thread(
                    target=self._scan_after, args=(old,), daemon=True,
                    name="setup-rescan")
                self._restart_thread.start()
                events.debug("Scan", "refresh: cancel requested; the next scan "
                             "starts when this one stops", source=self.NAME)
                return True
        return self.scan()

    @property
    def _is_restart_pending(self):
        thread = self._restart_thread
        return bool(thread is not None and thread.is_alive())

    def _scan_after(self, old):
        """Refresh's helper: wait for the cancelled scan, then scan again."""
        if old is not None:
            old.join()
        try:
            self.scan()
        except Refused as refusal:
            events.debug("Scan", f"refresh restart: {refusal.reason}",
                         source=self.NAME)
        except Exception as exc:        # never let a worker die silently
            events.warn("Scan Failed", "The scan could not restart. Press "
                        "Refresh to try again.", source=self.NAME, exception=exc)

    def scan(self):
        """Start a scan on a worker thread. Never blocks a view.
        was WebModelAdapter.scan_hardware / start_hardware_scan /
        get_scan_status (progress is part of Setup.state)

        Single-flight: a scan already running is refused rather than started
        a second time over the same ports.
        """
        with self._lock:
            if self.is_scanning:
                self._refuse("A hardware scan is already running.")
            if self.is_flashing:
                # The flash tool probes and uploads over these same ports.
                self._refuse("The firmware is being flashed. Refresh when "
                             "the Flashing cell is empty.")
            self._abort.clear()
            self._warned_ports.clear()
            self._busy_ports.clear()
            # A port a launched model holds is never probed (opening it
            # again would reset its board under the model): what it
            # answered before stands.
            held = self._held_ports()
            self._found = {port: name for port, name in self._found.items()
                           if port in held}
            self.scan_phase = self.LISTING
            self.scan_status = "scanning for ports..."
            self.scan_progress = 0
            self._scan_started = time.monotonic()
            self._scan_port = self._port_started = None
            thread = threading.Thread(target=self._scan_loop, daemon=True,
                                      name="setup-scan")
            self._scan_thread = thread
        self._refresh_rows()
        events.debug("Scan", "started", source=self.NAME)
        thread.start()
        return True

    def cancel_scan(self):
        """Ask the scan to give up. It stops inside the current port's wait."""
        if not self.is_scanning:
            self._refuse("No scan is running.")
        self._abort.set()
        events.debug("Scan", "cancel requested", source=self.NAME)
        return True

    def _scan_loop(self):
        started = time.monotonic()
        ports, gamepads = self.scan_ports(), self.scan_gamepads()
        with self._lock:
            self._ports, self._gamepads = ports, gamepads
        self._drop_stale_selections()
        held = self._held_ports()
        targets = [p for p in ports if p not in (SIM, ON) and p not in held]
        self.scan_phase = self.IDENTIFYING
        self.scan_status = (f"scanning {len(targets)} port(s)..." if targets
                            else "no ports found")
        self._refresh_rows()
        for index, port in enumerate(targets):
            if self._abort.is_set():
                break
            self.scan_status = (f"scanning {port} "
                                f"({index + 1} of {len(targets)}),")
            self._port_started = time.monotonic()
            self._scan_port = port
            try:
                found = self.identify(port, should_abort=self._abort.is_set)
            finally:
                self._scan_port = self._port_started = None
            with self._lock:
                self._found[port] = found
            if found:
                events.info("Device Found", f"{found} on {port}", source=self.NAME)
            self.scan_progress = int(((index + 1) / len(targets)) * 100)
            self._refresh_rows()
        if self._abort.is_set():
            self.scan_phase = self.CANCELLED
            self.scan_status = "scan cancelled"
            self._refresh_rows()
        else:
            self.scan_progress = 100
            self.scan_phase = self.DONE
            # Auto-assign is not a button any more: identifying a device and
            # then making the operator press "Auto-assign" to act on it was
            # the step the owner struck out (Addendum 2).
            try:
                self.auto_assign()
            except Exception as exc:       # never let a worker die silently
                events.debug("Auto-assign Failed", repr(exc), source=self.NAME,
                             exception=exc)
                events.warn("Auto-assign Failed", "Ports could not be assigned "
                            "automatically. Choose them by hand.",
                            source=self.NAME, exception=exc)
            with self._lock:
                detected = sum(1 for name in self._found.values() if name)
            self.scan_status = ("ready" if not detected else
                                f"ready - {detected} device(s) detected")
            busy = self.busy_ports
            if busy:
                self.scan_status += (f"; {', '.join(busy)} "
                                     f"{'is' if len(busy) == 1 else 'are'} "
                                     f"{serial_port_module.BUSY_WORDS}")
        events.debug("Scan", f"{self.scan_status}; {len(targets)} port(s) in "
                     f"{time.monotonic() - started:.1f} s", source=self.NAME)
        self._scan_settled = True
        self._deliver_startup_offer()

    def _refuse(self, reason):
        """Every refusal reaches the log file, even when `build()` was called
        outside `Panel.run` (Addendum 1)."""
        events.debug("Refused", reason, source=self.NAME)
        raise Refused(reason)


    # -- assignment --------------------------------------------------------
    def auto_assign(self, force=False):
        """Identity byte -> the matching row's port, for everything found.

        Runs by itself when a scan completes (Addendum 2), so it never raises
        for "nothing to assign": it returns what it assigned, possibly
        nothing. A row the operator has already set by hand is left alone -
        the machine's guess never overrides a person's choice - unless
        `force` says otherwise.
        """
        assigned, kept, taken, seen = [], [], {}, []
        with self._lock:
            found = dict(self._found)
            chosen = set(self._chosen)
        running = set(self._running_names())
        for port, name in found.items():
            key = self._key_of(name)
            if key is None:
                continue
            if name in running:
                continue        # launched: its port is held, its row stands
            if self._is_launched:
                # Owner 2026-10-07: a device plugged in after the launch is
                # not added to the running station; a restart launches it.
                with self._lock:
                    is_new = self._seen.get(key) != port
                    self._seen[key] = port
                if is_new:
                    seen.append((name, port))
                continue
            if key in chosen and not force:
                kept.append(f"{name}: operator chose "
                            f"{getattr(self, f'{key}_port')}")
                continue
            if key in taken:
                # Two boards answering as one model is a wiring question, not
                # something to resolve by silently preferring the later port.
                events.warn("Two Devices Answered Alike",
                            f"{port} and {taken[key]} both answered as {name}; "
                            f"{taken[key]} was assigned. Check the wiring or "
                            "choose the port by hand.", source=self.NAME)
                continue
            taken[key] = port
            if getattr(self, f"{key}_port") == port:
                continue
            # A board that answered is a row that launches.
            setattr(self, f"{key}_port", port)
            assigned.append(f"{name} on {port}")
        self._refresh_rows()
        if assigned:
            events.info("Auto-assign", ", ".join(assigned), source=self.NAME)
        if seen:
            words = _and([f"{name} on {port}" for name, port in seen])
            many = len(seen) > 1
            events.warn(events.RESTART_NEEDED,
                        f"{words} {'were' if many else 'was'} plugged in after "
                        f"the launch and {'are' if many else 'is'} not running. "
                        "Restart the station to launch "
                        f"{'them' if many else 'it'}.", source=self.NAME, ack=True,
                        action=("Restart now", events.SETUP_PANEL,
                                "restart_station", (True,)))
        events.debug("Auto-assign", "; ".join(assigned + kept) or
                     "nothing to assign", source=self.NAME)
        return assigned

    def _select(self, key, field, choice):
        """Apply one dropdown choice. Reached through `set_<row>_<field>`."""
        if key not in self._rows:
            self._refuse(f"{key} is not a configurable model")
        row = self._rows[key]
        if field == "gamepad" and not row["needs_gamepad"]:
            self._refuse(f"{row['name']} does not use a gamepad")
        if field in ("port", "gamepad"):
            kind = field
        else:
            kind = next((k for f, k, _, _ in row["columns"] if f == field), None)
            if kind is None:
                self._refuse(f"{row['name']} has no {field} to choose")
        if field == "port":
            choices = self._port_choices(key)
        elif kind == "port":
            choices = self.port_options()
        else:
            choices = self.gamepad_options()
        if choice not in choices:
            self._refuse(f"{choice!r} is not one of {row['name']}'s "
                         f"{field} options")
        setattr(self, f"{key}_{field}", choice)
        if field == "port":
            # From here on auto-assign leaves this row alone: the operator
            # has said what is plugged into it.
            with self._lock:
                self._chosen.add(key)
        events.debug("Selected", f"{row['name']} {field} = {choice}",
                     source=self.NAME)
        self._refresh_rows()
        return choice

    def _hard_reset(self, key, confirmed=False):
        """Hard reset one launched device (owner 2026-10-07). Reached
        through `hard_reset_<row>`. Its model is stopped and closed (the
        Controller's own remove: estop, then the port is closed), then built
        again from the config it was launched with and opened on the same
        port: opening the port pulses DTR, which resets the board, and the
        identity handshake runs again. Refused while the model is energized
        or running; asked first. A reset that did not come back can be
        pressed again: the config is remembered."""
        if key not in self._rows:
            self._refuse(f"{key} is not a configurable model")
        name = self._rows[key]["name"]
        controller = self.controller
        if name not in self._running_names() and \
                name not in list(getattr(controller, "closed_names", ()) or ()):
            self._refuse(f"{name} is not launched, so there is nothing to reset.")
        model = controller._model_or_none(name)
        if model is not None and (getattr(model, "is_energized", False)
                                  or getattr(model, "is_active", False)):
            self._refuse(f"{name} is energized. Stop it and put it out of its "
                         "mode first, then hard reset.")
        config = controller.config(name)
        where = ("its simulator" if config.get("sim")
                 else f"{config.get('port') or 'its port'}")
        if not confirmed:
            latched = (" It is stopped now; the reset clears that."
                       if getattr(model, "is_estopped", False) else "")
            raise NeedsConfirm(
                f"Hard reset {name}? It is stopped and disconnected, its board "
                f"is reset, and it is opened again on {where}.{latched}",
                f"hard_reset_{key}")
        events.info("Hard Reset", f"{name} on {where}: closing, then opening "
                    "again.", source=self.NAME)
        if model is not None:
            controller.remove(name)
        try:
            controller.reopen(name)
        except Refused:
            raise
        except Exception as exc:
            events.debug("Hard Reset Failed", f"{name}: {exc!r}", source=self.NAME,
                         exception=exc)
            self._refresh_rows()
            self._refuse(f"{name} did not come back on {where}. Check its cable, "
                         "then press Hard reset again or restart the station.")
        self._refresh_rows()
        return name

    def _running_names(self):
        return list(getattr(self.controller, "model_names", None) or ())

    def _held_ports(self):
        """The real ports the launched models hold, by their configs."""
        held = set()
        config_of = getattr(self.controller, "config", None)
        if not callable(config_of):
            return held
        for name in self._running_names():
            try:
                config = config_of(name) or {}
            except Exception:
                continue
            if config.get("sim"):
                continue
            for resource, value in config.items():
                if (_kind(resource) == "port" and isinstance(value, str)
                        and value not in (NOT_CONNECTED, SIM, ON, "None", "Off")):
                    held.add(value)
        return held

    def _port_choices(self, key):
        return (self.port_options() if self._rows[key]["needs_port"]
                else self.device_options())

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
            port_resources, gamepad_resources = _split(resources_of(model_class))
            for index, resource in enumerate(port_resources):
                if config.get("sim"):
                    break
                port = _resource_value(config, model_class, resource)
                label = "port" if index == 0 else resource
                # A second port has no row-level SIM of its own: the row's
                # Port dropdown decides, so SIM there on a live row is unset.
                unset = ("None", "Off") if index == 0 else ("None", "Off", SIM)
                if not port or port in unset:
                    self._refuse(f"{name} {label}: choose a port, or set the "
                                 "row to SIM")
                if port in ports:
                    self._refuse(f"{name} {label}: {port} is already assigned "
                                 f"to {ports[port]}")
                ports[port] = name
            for index, resource in enumerate(gamepad_resources):
                gamepad = _resource_value(config, model_class, resource)
                label = "gamepad" if index == 0 else resource
                if gamepad:
                    if gamepad in gamepads:
                        self._refuse(f"{name} {label}: {gamepad} is already "
                                     f"claimed by {gamepads[gamepad]}")
                    gamepads[gamepad] = name
        events.debug("Validate", f"{len(configs)} row(s) ok", source=self.NAME)
        return True

    # -- building ----------------------------------------------------------
    def launch(self, confirmed=False):
        """Validate the current choices and build them. The Launch button,
        and the Relaunch button once the system is up (a build resets the
        Controller first, so relaunching is the same call). A relaunch over
        energized models asks first (round 8, PM8-6)."""
        if self.is_flashing:
            # A board mid-upload is not a board to open: its port is the
            # uploader's, and its firmware is neither the old nor the new.
            self._refuse("The firmware is being flashed. Launch when the "
                         "Flashing cell is empty.")
        if _alive(self._apply_thread):
            # An update is landing in this checkout: nothing starts on files
            # that are being replaced underneath it.
            self._refuse("An update is being applied. Wait for it to finish, "
                         "then quit and start the station again.")
        with self._lock:
            updated_to = self._updated_to
        if updated_to and self._update_pending:
            # Windows: the new version waits in <install>.next; only the
            # Restart button's swap script puts it in place.
            self._refuse(f"The station was updated to {updated_to}. Press "
                         "Restart before launching.")
        if updated_to:
            # The files on disk are newer than the code that is running: a
            # late import would mix the two. Only a restart runs the update.
            self._refuse(f"The station was updated to {updated_to}. Quit and "
                         "start it again before launching.")
        configs = self.configs
        if not configs:
            self._refuse("No device is connected. Plug it in and press "
                         "Refresh; every device that answers launches.")
        self.validate(configs)
        self._ask_about_firmware(configs, confirmed)
        self._ask_before_taking_down("Relaunch", "launch", confirmed)
        return self.build(configs)

    def _ask_before_taking_down(self, verb, command, confirmed):
        energized = [n for n in self.controller.model_names
                     if getattr(self.controller._model_or_none(n), "is_energized", False)]
        if energized and not confirmed:
            names = (", ".join(energized[:-1]) + " and " + energized[-1]
                     if len(energized) > 1 else energized[0])
            plural = len(energized) > 1
            raise NeedsConfirm(
                f"{verb}? {names} {'are' if plural else 'is'} energized; this stops "
                f"and disconnects {'them' if plural else 'it'} first.", command)

    def stop_system(self, confirmed=False):
        """Take the whole system down without leaving the panel. Every model
        is estopped and closed by `Controller.reset()`; Launch comes back.
        Asks first while anything is energized (round 8, PM8-6)."""
        running = self.controller.model_names
        if not running and not self._is_launched:
            self._refuse("Nothing is running.")
        self._ask_before_taking_down("Close every model", "stop_system", confirmed)
        self.controller.reset()
        self._is_launched = False
        self._take_seen()
        self._refresh_rows()
        events.info("Stopped", ", ".join(running) or "nothing was running",
                    source=self.NAME)
        return running

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
        if self.guest_locked:
            # A Guest's station has no Transfer Map or Sample Map (owner
            # 2026-10-07); a sign-in adds them (`_add_signed_in_only`).
            configs = [c for c in configs if not is_signed_in_only(c.get("model"))]
        self.validate(configs)
        self._check_identities(configs)

        self.controller.factory = self.model_from_config
        self.controller.reset()
        self._is_launched = False
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
                raise Refused(f"Could not start the {name}. Nothing was "
                              "left running; check its port and try again. "
                              "The details are in the log file.")
            built.append(name)
            events.debug("Built", f"{name} port={config.get('port')} "
                         f"gamepad={config.get('gamepad')} sim={config.get('sim')} "
                         f"in {(time.monotonic() - started) * 1000:.0f} ms",
                         source=self.NAME)
        self._is_launched = True
        with self._lock:
            self._seen.clear()
        self._refresh_rows()
        events.info("Launched", ", ".join(built), source=self.NAME)
        return built

    # -- the account (owner request 2026-10-07; was the Phase 1 profiles) ---------
    #: The section's title: the station's defaults, the accounts' one
    #: station-wide control (the user's own are on the account menu).
    ACCOUNT_SECTION = "Station defaults"
    #: Merged with the Trial store row's fields above (A3): a second plain
    #: `PARAMS =` here would replace them.
    PARAMS = {**PARAMS,
              "account_email": Param("account_email", "text", default="",
                                     label="Email"),
              "account_password": Param("account_password", "text", default="",
                                        label="Password")}
    #: The typed password never reaches the log: `Panel.run` writes
    #: `<redacted>` for it (user-system section 6.1).
    SECRET_INPUTS = frozenset({"account_password"})
    #: The sign-in screen's commands and fields (2026-10-07, the sign-in
    #: gate). The Web view draws that screen itself, beside the rail and in
    #: front of everything but the stop, so they are no longer controls of the
    #: Account section; they stay commands of Setup, allowed by name here
    #: (`_allows`, `_apply_inputs`) exactly as the schema allowed them before.
    GATE_COMMANDS = frozenset({"sign_in", "create_account", "open_as_guest",
                               "sign_out", "switch_user"})
    GATE_INPUTS = ("account_email", "account_password")

    def _init_accounts(self):
        """The station scope, the accounts file with the Phase 1 profiles
        migrated into it, and the session: a Guest until someone signs in.
        Setup is the composition root, the one place these are built
        (section 5.4)."""
        root = profiles_module.profiles_root()
        self.profiles = profiles_module.ProfileService(
            profiles_module.LocalFilesSource(root), self._params_of)
        self.users = UserStore(user_store_module.default_path())
        self.user = self._user_for(None)
        self._account_lock = threading.RLock()
        # The sign-in gate: nobody has chosen how this session works yet.
        # With accounts off there is nothing to choose.
        self._account_chosen = not PROFILES_ENABLED
        #: The Transfer Map's store choice, per session (`_SessionChoices`).
        self._map_choices = _SessionChoices(self)
        try:
            migrated = self.users.migrate_profiles(self.profiles.source)
        except Exception as exc:
            migrated = []
            events.warn("Profiles Not Migrated", f"The profiles in {root} could not "
                        "be made accounts; they are left as they are, and nobody "
                        "can sign in with them until this is fixed.",
                        source=self.NAME, exception=exc)
        if migrated:
            events.info("Profiles Migrated", f"{_and(migrated)}: now accounts with no "
                        "password yet; each sets one at the first sign-in.",
                        source=self.NAME)

    @staticmethod
    def _params_of(name):
        return getattr(MODEL_TYPES.get(name), "PARAMS", {})

    @property
    def account_password(self):
        """Always "": a typed password is never read back, so it is never in
        `state`. The setter keeps it for the one command it travels with."""
        return ""

    @account_password.setter
    def account_password(self, value):
        self._pending_password = "" if value is None else str(value)

    def _take_password(self):
        """The typed password, forgotten as it is handed over."""
        password, self._pending_password = self._pending_password, ""
        return password

    @property
    def account_status(self):
        if self.user.is_guest:
            return "Guest (station defaults)"
        return f"Signed in as {self.user.user_name} ({self.user.auth})"

    def sign_in(self, confirmed=False):
        """The typed email and password -> that account's User, whose config
        reaches every open model. A Phase 1 profile has no password yet: the
        one typed now becomes its password, after a confirmation (the typed
        text waits here for the re-run, never in the confirmation)."""
        email = user_store_module.normalize_email(self.account_email)
        if not email:
            self._take_password()
            self._refuse("Type your email and password, or press Proceed as guest.")
        record = self.users.user(email)
        if record is None:
            self._take_password()
            self._refuse(f"No account for {email} on this station. Press Create "
                         "account… to make one, or Proceed as guest.")
        if record["must_set_password"]:
            if len(self._pending_password) < user_store_module.MIN_PASSWORD:
                self._take_password()
                self._refuse(f"{record['name']} has no password yet (a profile from "
                             "before accounts). Type the password to keep, at least "
                             f"{user_store_module.MIN_PASSWORD} characters, then "
                             "Sign in.")
            if not confirmed:
                raise NeedsConfirm(
                    f"{record['name']} ({email}) has no password yet: it was a "
                    "profile before accounts. Keep the password you typed as its "
                    "password and sign in?", "sign_in")
            try:
                self.users.set_password(email, self._take_password())
            except AccountError as refusal:
                self._refuse(str(refusal))
        elif not self.users.verify(email, self._take_password()):
            self._refuse("That email and password do not match an account on this "
                         "station.")
        self._become(self._user_for(email))
        self._chose_account()
        return self.account_status

    def create_account(self, confirmed=False):
        """A new account from the typed email and password, then signed in.
        Setup has no phases, so the second step is a confirmation; the
        password waits here for it, never in the confirmation."""
        try:
            email = user_store_module.check_email(self.account_email)
            user_store_module.check_password(self._pending_password)
        except AccountError as refusal:
            self._take_password()
            self._refuse(str(refusal))
        if self.users.user(email) is not None:
            self._take_password()
            self._refuse(f"An account for {email} already exists. Press Sign in "
                         "instead.")
        if not confirmed:
            raise NeedsConfirm(
                f"Create an account for {email} on this station? The password is "
                "kept here as a salted hash: nobody can read it back, and nothing "
                "is sent anywhere.", "create_account")
        try:
            self.users.create(email, self._take_password())
        except AccountError as refusal:
            self._refuse(str(refusal))
        events.info("Account Created", f"{email}.", source=self.NAME)
        self._become(self._user_for(email))
        self._chose_account()
        return self.account_status

    def open_as_guest(self):
        """The station's defaults and nothing else. Signs out whoever is in."""
        self._take_password()
        if not self.user.is_guest:
            self._refuse_guest_mid_trial()
            self._become(self._user_for(None))
        self._chose_account()
        return self.account_status

    def _chose_account(self):
        """The sign-in screen is answered: Setup is next, so its device table
        is scanned afresh (owner 2026-10-07). The startup scan ran while the
        screen was up, possibly while another program still held the ports.
        Only when the station has scanned before (a running app, not a test
        that never started one), is not launched and is not scanning now."""
        self._account_chosen = True
        if getattr(self, "_scan_thread", None) is None or self._is_launched \
                or self.is_scanning:
            return
        try:
            self.scan()
        except Refused as refusal:
            events.debug("Scan", f"after sign-in: {refusal.reason}", source=self.NAME)

    def sign_out(self):
        """Back to Guest: every model's user parameters rebuilt from its
        Params with the station's defaults over them; nothing restarts. The
        session is open again: the Web view shows its sign-in screen."""
        self._take_password()
        if self.user.is_guest:
            self._refuse("Nobody is signed in: the station is on its defaults.")
        self._refuse_guest_mid_trial()
        self._become(self._user_for(None))
        self._account_chosen = False
        return self.account_status

    def switch_user(self):
        """Switch user: back to the sign-in screen. A signed-in user is signed
        out (as `sign_out`); a Guest just chooses again. Refused only while
        a trial is open (a Guest has no Transfer Map)."""
        self._take_password()
        if not self.user.is_guest:
            self._refuse_guest_mid_trial()
            self._become(self._user_for(None))
        self._account_chosen = False
        return self.account_status

    @property
    def account_chosen(self):
        """True once the operator signed in, made an account or chose Guest
        on the sign-in screen; False at start and after Sign out or Switch
        user. Always True with accounts off. `state["account"]["chosen"]`."""
        return self._account_chosen

    @property
    def account(self):
        """The session, for a view's sign-in screen and its account menu:
        whether a choice was made, who is in (`name`: the display name, or
        "Guest"). The user's own sheet is `Setup.user`, never a model."""
        return {"enabled": bool(PROFILES_ENABLED),
                "chosen": self._account_chosen,
                "signed_in": not self.user.is_guest,
                "email": "" if self.user.is_guest else self.user.email,
                "name": self.user.user_name,
                "status": self.account_status,
                # What a Guest does not get (owner 2026-10-07): a view
                # greys what needs them (the tutorials that use them).
                "signed_in_only": sorted(n for n in MODEL_TYPES if is_signed_in_only(n))
                if PROFILES_ENABLED else []}

    def _allows(self, command, args=()):
        """The sign-in screen's commands are Setup's though no control of the
        page shows them (`GATE_COMMANDS`)."""
        if PROFILES_ENABLED and command in self.GATE_COMMANDS:
            return
        super()._allows(command, args)

    def _apply_inputs(self, inputs):
        """`Panel._apply_inputs`, plus the sign-in screen's two fields
        (`GATE_INPUTS`), parsed by their Params, all or nothing."""
        inputs = dict(inputs or {})
        gate = {}
        if PROFILES_ENABLED:
            for name in self.GATE_INPUTS:
                if name in inputs:
                    ok, value = self.PARAMS[name].parse(inputs.pop(name))
                    if not ok:
                        raise Refused(value)
                    gate[name] = value
        super()._apply_inputs(inputs)
        for name, value in gate.items():
            setattr(self, name, value)

    def _user_for(self, email):
        """The session's `User` for `email` (None: a Guest), wired to this
        Setup: the open models for "Remember current values", and Sign out
        and Switch user. Never added to the Controller (owner 2026-10-07:
        the user is a settings menu, not a device); a view reaches its sheet
        through `Setup.user`."""
        return User(store=self.users if email else None, email=email,
                    params_of=self._params_of, models=self._models_open,
                    on_sign_out=self.sign_out, on_switch_user=self.switch_user)

    def _models_open(self):
        return dict(self.controller.models) if self.controller is not None else {}

    def _become(self, user):
        """Switch the session: undo the previous user's config on every open
        model (`User.revert`) and load the new one's (`User.load_into`, which
        stamps the operator). The user is Setup's, never a Controller
        model."""
        with self._account_lock:
            previous, self.user = self.user, user
            models = self.controller.models if self.controller is not None else {}
            if not previous.is_guest:
                self._warn_not_applied(previous.revert(models, self.profiles))
                events.info("Signed Out", f"{previous.user_name} ({previous.email}); "
                            "the station's defaults are back.", source=self.NAME)
            if not user.is_guest:
                self.users.touch_sign_in(user.email)
                events.info("Signed In", f"{user.user_name} ({user.email}).",
                            source=self.NAME)
            self._warn_not_applied(user.load_into(models, self.profiles))
            if PROFILES_ENABLED and user.is_guest and not previous.is_guest:
                self._drop_signed_in_only()
            self._follow_store(previous)
            if PROFILES_ENABLED and previous.is_guest and not user.is_guest:
                self._add_signed_in_only()
            self._refresh_rows()

    # -- what a Guest does not get (owner 2026-10-07) -------------------------------
    def _open_signed_in_only(self):
        if self.controller is None:
            return {}
        return {name: model for name, model in self.controller.models.items()
                if is_signed_in_only(name)}

    def _refuse_guest_mid_trial(self):
        """Back to Guest takes the Transfer Map and the Sample Map away, so
        not while one of them is busy (an armed trial, a recording)."""
        if not PROFILES_ENABLED or self.user.is_guest:
            return
        busy = [name for name, model in self._open_signed_in_only().items()
                if getattr(model, "is_active", False)]
        if busy:
            self._refuse(f"{_and(busy)} {'has' if len(busy) == 1 else 'have'} a trial "
                         "open. Finish or abort it first: a Guest has no Transfer "
                         "Map or Sample DB, so they close when you switch.")

    def _drop_signed_in_only(self):
        """A Guest's station: the signed-in-only models closed (stopped,
        disconnected) - a guest first, so a host never closes over one."""
        names = sorted(self._open_signed_in_only(),
                       key=lambda n: getattr(MODEL_TYPES.get(n), "HOST", None) is None)
        for name in names:
            try:
                self.controller.remove(name)
            except Exception as exc:
                events.warn("Model Not Closed", f"{name} did not close for the Guest "
                            "session.", source=self.NAME, exception=exc)
        if names:
            events.info("Closed for Guest", f"{_and(names)}: for signed-in users.",
                        source=self.NAME)

    def _add_signed_in_only(self):
        """A sign-in on a running station: the signed-in-only models the
        launch left out, built now (they need no port), the user's config
        and store on them. Nothing else restarts."""
        if self.controller is None or not self._is_launched:
            return
        running = set(self.controller.model_names)
        added = []
        for config in self._all_configs():
            name = config["model"]
            if not is_signed_in_only(name) or name in running:
                continue
            try:
                self.controller.add(name, self.model_from_config(config), config)
            except Exception as exc:
                events.warn("Model Not Opened", f"{name} could not be opened for "
                            f"{self.user.user_name}; press Relaunch in Settings.",
                            source=self.NAME, exception=exc)
                continue
            added.append(name)
        if added:
            events.info("Opened for Signed-in User", _and(added), source=self.NAME)

    def _apply_account(self, model):
        """At build and reopen: the station's defaults, the signed-in user's
        config and who is working, for one model. With accounts off
        (`STATION_PROFILES=0`) the model is left exactly as built."""
        if not PROFILES_ENABLED:
            return
        self._warn_not_applied(self.user.load_into(
            {getattr(model, "NAME", None) or "": model}, self.profiles))
        if isinstance(model, TransferMap):
            model.choices = self._map_choices
            if not self.user.is_guest:
                self._open_users_store(model)

    def _warn_not_applied(self, refused):
        for name, problems in (refused or {}).items():
            for param, reason in problems.items():
                events.warn("Profile Value Not Applied", f"{name}.{param}: {reason}",
                            source=self.NAME)

    def _open_values(self, names):
        """Every open model's stored Params named in `names`, as they are now
        (`profile.current_params`, the inverse of `apply_defaults`)."""
        out = {}
        for model in self.controller.models.values():
            name = getattr(model, "NAME", None)
            if not name or isinstance(model, User):
                continue
            values = profiles_module.current_params(model, names)
            if values:
                out[name] = values
        return out

    def save_station_settings(self, confirmed=False):
        """The station's defaults: user and station-only parameters of the
        open models (never the brakes). Everyone at this station gets them;
        a Guest gets nothing else."""
        if not confirmed:
            raise NeedsConfirm("Save the open models' settings as this station's "
                               "defaults? Everyone who signs in here starts from "
                               "them; the brake fields are never saved.",
                               "save_station_settings")
        values = self._open_values(profiles_module.USER_PARAMS
                                   | profiles_module.STATION_PARAMS)
        try:
            self.profiles.save_station(values)
        except profiles_module.ProfileError as refusal:
            raise Refused(str(refusal))
        return sorted(values)

    def _account_section(self):
        """First on the page: the station-wide half of the accounts, the
        station's defaults everyone starts from. Who is in, Switch user and
        everything that is one user's own (name, password, "Remember current
        values as my defaults") are on the user's sheet (`model.user`), the
        rail's account menu (owner 2026-10-07). Signing in, making an
        account and choosing Guest happen on the sign-in screen
        (`GATE_COMMANDS`)."""
        return sch.section(
            self.ACCOUNT_SECTION,
            sch.button("Save station settings", "save_station_settings",
                       role="neutral"),
            layout="row",
        )

    def model_from_config(self, config):
        """One config -> one Model. `Controller.factory`, so `reopen(name)`
        reconstructs a closed tab's model from the remembered config."""
        model_class = MODEL_TYPES.get(config.get("model"))
        refusal = self.session_refusal(config.get("model"))
        if refusal:
            raise Refused(refusal)      # a Guest's build or reopen of a map
        if model_class is None:
            raise Refused(f"{config.get('model')!r} is not a known model")
        is_sim = bool(config.get("sim"))
        # Only what the class declares: a class without a gamepad does not
        # have to accept `gamepad=None`, and one with a second port gets it.
        resources = {}
        for resource in resources_of(model_class):
            value = _resource_value(config, model_class, resource)
            if _kind(resource) == "port" and is_sim:
                value = SIM
            resources[resource] = value
        model = model_class(sim=is_sim, **resources)
        self._apply_account(model)
        return model

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
                events.debug("Rollback Failed", f"{name}: {exc!r}",
                             source=self.NAME, exception=exc)
                events.warn("Rollback Failed", f"The {name} could not be shut "
                            "down after the failed launch. Restart the app "
                            "before launching again.", source=self.NAME,
                            exception=exc)

    # -- the update check (owner, 2026-09-28) -------------------------------
    #: `apply_update`'s confirmation: the one question before the checkout moves.
    UPDATE_CONFIRM = ("Update the station now? The station must be "
                      "restarted afterwards.")
    #: `Updater.check()`'s status -> the one sentence the Updates line says.
    #: `behind` is counted, `error` carries the check's own reason.
    UPDATE_SENTENCES = {
        "up_to_date": "Up to date.",
        "offline": "Could not reach GitHub; the station runs as it is.",
        "dirty": "This checkout has local edits; update by hand.",
        "diverged": "This checkout has commits GitHub does not; update by hand.",
        "not_git": "Not a git checkout.",
        "bundle": "A packaged bundle updates by installing a new one.",
        "no_release": "No release has been published yet.",
    }
    CHECKING = "Checking for updates…"
    CHECK_OFF = ("The update check is off for this run "
                 "(STATION_NO_UPDATE_CHECK). Press Check again to check now.")

    def _init_updates(self, updater):
        """The Update row's state, then - unless `STATION_NO_UPDATE_CHECK` is
        set - the startup check on a daemon thread. The check fetches; it
        never touches the tree (`controller.updater`)."""
        self._updater = updater if updater is not None else Updater()
        self._update_thread = self._apply_thread = None
        self._update_code = None        # the last check's status
        self._update_lines = []         # the incoming `--oneline` lines
        self._updated_to = None         # sha7 once an update landed
        self._update_pending = False    # a bundle's swap waits for Restart
        self._version_read = False
        self._warned_updates = set()
        #: What is RUNNING: read once, so a landed update does not claim to be
        #: running before the restart.
        self.station_version = "unknown"
        self.has_update = False
        self.update_log = ""
        if os.environ.get("STATION_NO_UPDATE_CHECK", "") not in ("", "0"):
            self.update_status = self.CHECK_OFF
            events.debug("Update Check", "off: STATION_NO_UPDATE_CHECK is set",
                         source=self.NAME)
            return
        self.update_status = self.CHECKING
        self._start_update_thread()

    def check_updates(self):
        """Check again: fetch and publish, on a thread. Refused while a check
        or an update is already running."""
        with self._lock:
            if _alive(self._apply_thread):
                self._refuse("An update is being applied. Wait for it to finish.")
            if _alive(self._update_thread):
                self._refuse("An update check is already running. Wait for its "
                             "answer on the Updates line.")
            self.update_status = self.CHECKING
            self._start_update_thread()
        return True

    def apply_update(self, confirmed=False):
        """Update now: fast-forward this checkout (`Updater.apply`) on a
        thread. Never under running devices, never without an update to
        apply, and never unasked."""
        with self._lock:
            if _alive(self._apply_thread):
                self._refuse("An update is already being applied. Wait for it "
                             "to finish.")
            if _alive(self._update_thread):
                self._refuse("The update check is still running. Wait for its "
                             "answer, then press Update now.")
            running = list(getattr(self.controller, "model_names", None) or [])
            if self._is_launched or running:
                self._refuse("Close every model first: an update must not land "
                             "under running devices.")
            if not self.has_update:
                self._refuse("There is no update to apply. Press Check again "
                             "to look for one.")
            if not confirmed:
                raise NeedsConfirm(self.UPDATE_CONFIRM, "apply_update")
            self.update_status = ("Updating. Wait for it to finish; Launch "
                                  "waits too.")
            self._apply_thread = threading.Thread(
                target=self._apply_worker, daemon=True, name="setup-update-apply")
            self._apply_thread.start()
        events.debug("Update", "apply started", source=self.NAME)
        return True

    #: `restart_station`'s question when it came from a button, not the dialog.
    RESTART_CONFIRM = "Restart the station now? Every model closes first."

    def restart_station(self, confirmed=False):
        """Restart now (rb-restart R3): close every model through the
        Controller, flush the log, then hand over to `restart` - the same
        interpreter, argv and view, from the checkout root. Refused while
        anything is energized; asked once unless `confirmed` (the Restart
        Needed dialog passes it: that dialog IS the question)."""
        with self._lock:
            if _alive(self._apply_thread):
                self._refuse("An update is being applied. Wait for it to "
                             "finish, then restart.")
        if self._restart is None:
            self._refuse("This station cannot restart itself: quit and start "
                         "it again by hand.")
        if getattr(self.controller, "is_energized", False):
            energized = [n for n in list(getattr(self.controller, "model_names", ()) or ())
                         if getattr(self.controller._model_or_none(n),
                                    "is_energized", False)]
            if energized:
                names = (", ".join(energized[:-1]) + " and " + energized[-1]
                         if len(energized) > 1 else energized[0])
                verb = "are" if len(energized) > 1 else "is"
                self._refuse(f"{names} {verb} energized. Stop it and put it "
                             "out of its mode first, then restart.")
            self._refuse("A model is energized. Stop it and put it out of its "
                         "mode first, then restart.")
        if not confirmed:
            raise NeedsConfirm(self.RESTART_CONFIRM, "restart_station")
        running = list(getattr(self.controller, "model_names", ()) or ())
        events.info("Restart", "Restarting the station: closing "
                    f"{', '.join(running) or 'nothing'} first.", source=self.NAME)
        self.controller.reset()
        self._is_launched = False
        events.flush_file()
        try:
            self._restart()
        except Exception as exc:        # the process is still this one
            events.error("Restart Failed", f"The station did not restart ({exc}). "
                         "Every model was closed; quit and start it again by "
                         "hand.", source=self.NAME, exception=exc)
            self._refresh_rows()
            self._refuse(f"The station did not restart ({exc}). Quit and start "
                         "it again by hand.")
        return True

    def _start_update_thread(self):
        self._update_thread = threading.Thread(
            target=self._check_worker, daemon=True, name="setup-update-check")
        self._update_thread.start()

    def _check_worker(self):
        try:
            if not self._version_read:
                self.station_version = self._updater.version()
                self._version_read = True
            result = self._updater.check()
        except Exception as exc:        # never let a worker die silently
            events.debug("Update Check Failed", repr(exc), source=self.NAME,
                         exception=exc)
            with self._lock:
                self._update_code, self._update_lines = "error", []
                self.has_update, self.update_log = False, ""
                self.update_status = ("The update check failed; the station "
                                      "runs as it is.")
            self._warn_update_once("Update Check Failed",
                                   "The update check failed; the station runs as "
                                   "it is. The details are in the log file.",
                                   exception=exc)
            return
        self._publish_check(result)

    def _publish_check(self, result):
        code = result.get("status")
        behind = int(result.get("behind") or 0)
        lines = [str(line) for line in (result.get("log") or [])][:8]
        reason = result.get("reason") or ""
        # A release (a bundle's check, B4) names its tag, never "bundle":
        # the copy is the same sentence shape in both worlds.
        latest, running = result.get("latest"), result.get("tag")
        if code == "behind" and behind and latest:
            sentence = _release_ready(latest, lines)
        elif code == "behind" and behind:
            sentence = (f"{behind} new commit{'s are' if behind != 1 else ' is'} "
                        "ready. Update now, then restart the station.")
        elif code == "up_to_date" and self._updated_to:
            sentence = self._restart_sentence()
        elif code == "up_to_date" and running:
            sentence = f"Up to date ({running})."
        elif code in self.UPDATE_SENTENCES:
            sentence = self.UPDATE_SENTENCES[code]
        else:
            sentence = reason or "The update check failed; the station runs as it is."
        with self._lock:
            self._update_code = code
            self._update_lines = lines if behind else []
            self.update_log = "\n".join(self._update_lines)
            self.has_update = code == "behind" and behind > 0
            self.update_status = sentence
        events.debug("Update Check", f"{code}: behind={behind} ahead="
                     f"{result.get('ahead')} head={result.get('head')} "
                     f"remote={result.get('remote')} {reason}".rstrip(),
                     source=self.NAME)
        if code == "error":
            self._warn_update_once("Update Check Failed", sentence)
        if code == "behind" and behind:
            self._ask_to_update(behind, lines, result.get("remote"), latest)

    def _ask_to_update(self, behind, lines, remote, latest=None):
        """R2: the Update Ready dialog, once per distinct remote sha (a Check
        again that finds the same commits says nothing new); for a release,
        once per tag, in the release's own words."""
        key = ("ready", remote or "|".join(lines))
        with self._lock:
            if key in self._warned_updates:
                return
            self._warned_updates.add(key)
        if latest:
            message = _release_ready(latest, lines)
        else:
            first = _subject(lines[0]) if lines else ""
            count = (f"{behind} new commit{'s are' if behind != 1 else ' is'} ready"
                     + (f": {first}" if first else ""))
            message = f"{count}. Update now, then restart the station."
        events.warn(events.UPDATE_READY, message, source=self.NAME, ack=True,
                    action=("Update now", events.SETUP_PANEL, "apply_update"))

    def _restart_sentence(self):
        if self._update_pending:
            return f"Updated to {self._updated_to}. Press Restart to run it."
        return (f"Updated to {self._updated_to}. Quit and start the station "
                "again to run it.")

    def _apply_worker(self):
        try:
            result = self._updater.apply()
        except Exception as exc:        # never let a worker die silently
            events.debug("Update Failed", repr(exc), source=self.NAME, exception=exc)
            with self._lock:
                self.has_update = False
                self.update_status = ("The update did not complete. Press Check "
                                      "again to see where this checkout stands.")
            events.warn("Update Failed", "The update did not complete. Press Check "
                        "again to see where this checkout stands; the details "
                        "are in the log file.", source=self.NAME, exception=exc)
            return
        reason = result.get("reason") or ""
        events.debug("Update", f"apply: {result}", source=self.NAME)
        if not result.get("updated"):
            with self._lock:
                self.has_update = False
                self.update_status = reason or ("The update was not applied; "
                                                "nothing was changed.")
            events.warn("Update Not Applied", self.update_status, source=self.NAME)
            return
        extras = []
        if result.get("deps_changed") and not result.get("deps_ok", True):
            extras.append("The dependencies changed and pip install failed: run "
                          "pip install -e '.[qt]' by hand before starting again.")
        if result.get("firmware_changed"):
            extras.append("The firmware changed: after the restart, the "
                          "Firmware row flashes the boards that are out of date.")
        with self._lock:
            self._updated_to = result.get("new")
            self._update_pending = bool(result.get("pending"))
            self._update_code = "updated"
            self._update_lines = []
            self.update_log = ""
            self.has_update = False
            self.update_status = " ".join([self._restart_sentence(), *extras])
        events.info("Station Updated", reason or self.update_status, source=self.NAME)
        # R2: the line alone was easy to miss; the dialog asks, and its
        # Restart now is the answer (confirmed: the dialog is the question).
        # Not when the restart must be by hand: a re-exec into dependencies
        # pip could not install would not come back, and only the Firmware row
        # flashes changed firmware on the way up.
        if extras:
            events.warn(events.RESTART_NEEDED, " ".join(
                [f"Updated to {self._updated_to}.", *extras,
                 "Then quit and start the station again."]),
                source=self.NAME, ack=True)
        else:
            events.warn(events.RESTART_NEEDED, f"Updated to {self._updated_to}. "
                        "Restart the station to run it.", source=self.NAME,
                        ack=True, action=("Restart now", events.SETUP_PANEL,
                                          "restart_station", (True,)))
        if result.get("deps_changed") and not result.get("deps_ok", True):
            events.warn("Reinstall Failed", reason, source=self.NAME)

    def _warn_update_once(self, title, message, exception=None):
        """One warning per distinct failure for the session, not one per
        press: an offline bench is not told again every time."""
        key = (title, message)
        with self._lock:
            if key in self._warned_updates:
                return
            self._warned_updates.add(key)
        events.warn(title, message, source=self.NAME, exception=exception)

    # -- the firmware check (owner, 2026-09-28: the old launcher's, on the page) --
    FIRMWARE_CHECKING = "checking…"
    FIRMWARE_CHECK_OFF = ("not checked this run (STATION_NO_FIRMWARE_CHECK); "
                          "press Check firmware")
    #: The button's static text; the question actually asked names the boards.
    FLASH_CONFIRM = ("Flash the out-of-date boards? This overwrites their "
                     "running firmware.")

    def _init_firmware(self, firmware):
        """The Firmware row's state, then - unless `STATION_NO_FIRMWARE_CHECK`
        is set - the startup check on a daemon thread. The check reads the
        stamp file and the sketches and opens no port; when it finds a board
        to flash it asks once (`_offer_flash`), and Flash now on that dialog
        is the flash, unattended, as the old launcher's was (owner 2026-09-28).
        Nothing flashes without that answer or the row's own key."""
        self._firmware = firmware if firmware is not None else FirmwareCheck()
        self._firmware_thread = self._flash_thread = None
        self._firmware_result = None    # the last check()'s answer
        self._firmware_asked = set()    # stale-board sets Launch already asked about
        self._flash_offered = set()     # board sets the startup dialog already offered
        # A2 (OP-4): the startup check's offer waits for a view to listen.
        self._startup_offer = None      # the startup check's answer, not yet offered
        self._startup_offered = False
        #: A scan has finished at least once: the startup offer names only
        #: the boards it found (owner 2026-10-07: no Flash now for a board
        #: that is switched off or unplugged).
        self._scan_settled = False
        self._startup_listening = False
        self._offer_on_read = False
        self.firmware_progress = ""
        #: The Web view's address, which it fills in once it serves; the
        #: desktop views leave it empty. It used to be a terminal line.
        self.web_address = ""
        if os.environ.get("STATION_NO_FIRMWARE_CHECK", "") not in ("", "0"):
            self.firmware_status = self.FIRMWARE_CHECK_OFF
            events.debug("Firmware Check", "off: STATION_NO_FIRMWARE_CHECK is set",
                         source=self.NAME)
            return
        self.firmware_status = self.FIRMWARE_CHECKING
        self._start_firmware_thread(offer=True)

    @property
    def is_flashing(self):
        return _alive(getattr(self, "_flash_thread", None))

    def check_firmware(self):
        """Check firmware: the board statuses again, on a thread."""
        with self._lock:
            if self.is_flashing:
                self._refuse("A flash is running; the Boards line updates when "
                             "it finishes.")
            if _alive(self._firmware_thread):
                self._refuse("The firmware check is already running. Wait for "
                             "its answer on the Boards line.")
            self.firmware_status = self.FIRMWARE_CHECKING
            self._start_firmware_thread()
        return True

    def flash_firmware(self, confirmed=False):
        """Flash out-of-date boards: `firmware/flash_firmware.py --yes --only
        <the boards the check found>` on a thread, its lines streaming into
        the Flashing cell. Never while the station holds a port, never while
        the scan does, never with nothing to flash, never unasked."""
        with self._lock:
            if self.is_flashing:
                self._refuse("A flash is already running. Wait for it to finish.")
            if _alive(self._firmware_thread):
                self._refuse("The firmware check is still running. Wait for its "
                             "answer, then press Flash.")
            running = list(getattr(self.controller, "model_names", None) or [])
            if self._is_launched or running:
                self._refuse("Close every model first: a board cannot be "
                             "flashed while the station holds its port.")
            if self.is_scanning or self._is_restart_pending:
                self._refuse("The scan is using the ports. Flash when it has "
                             "finished, or press Cancel scan.")
            result = self._firmware_result
            if result is None:
                self._refuse("The firmware has not been checked. Press Check "
                             "firmware first.")
            boards = list(result.get("to_flash") or [])
            if not boards:
                self._refuse("Every board is current; there is nothing to flash.")
            missing = list(result.get("missing_tools") or [])
            if missing:
                self._refuse(f"{_and(missing)} {'is' if len(missing) == 1 else 'are'} "
                             "not installed on this computer: flash by hand "
                             "with firmware/flash_firmware.py.")
            script = getattr(self._firmware, "script", None)
            if script is not None and not os.path.isfile(script):
                self._refuse("The flash tool is not part of this installation: "
                             "flash by hand.")
            if not confirmed:
                raise NeedsConfirm(
                    f"Flash {_and(boards)} now? This overwrites the running "
                    "firmware of every one of them that is plugged in; the "
                    "others are skipped. Launch waits until it finishes.",
                    "flash_firmware")
            self.firmware_status = f"flashing {_and(boards)}"
            self.firmware_progress = "starting…"
            self._flash_thread = threading.Thread(
                target=self._flash_worker, args=(boards,), daemon=True,
                name="setup-firmware-flash")
            self._flash_thread.start()
        events.debug("Firmware Flash", f"started: {', '.join(boards)}", source=self.NAME)
        return True

    def _start_firmware_thread(self, offer=False):
        """`offer`: the startup check asks to flash what it finds; a check by
        hand does not (the Flash key is beside its answer)."""
        self._firmware_thread = threading.Thread(
            target=self._firmware_check_worker, args=(offer,), daemon=True,
            name="setup-firmware-check")
        self._firmware_thread.start()

    def _firmware_check_worker(self, offer=False):
        result = self._check_firmware_now()
        self._publish_firmware(result)
        if offer:
            with self._lock:
                self._startup_offer = result or {}
            self._deliver_startup_offer()

    def startup_checks(self, on_next_read=False):
        """The view is listening (A2, OP-4): the startup firmware check's
        Flash now dialog may go out. It used to be published from the
        check's thread at construction, before any view had subscribed to
        the event log, so it reached nobody and Flash now was unreachable.

        `app.launch` calls this the moment a desktop view subscribes; for
        the Web (`on_next_read`), the offer goes out with the next read of
        this panel's state - the page's first Setup read, after it has
        taken its place in the event stream (older events it replays as
        history, never as a dialog). A check still running offers when it
        finishes. Offered once per run; a second call offers nothing new."""
        with self._lock:
            if on_next_read:
                self._offer_on_read = True
                return True
            self._startup_listening = True
        self._deliver_startup_offer()
        return True

    def _deliver_startup_offer(self):
        with self._lock:
            if not self._startup_listening or self._startup_offer is None \
                    or self._startup_offered:
                return
            # A scan that is still running holds the offer until it knows
            # which boards are plugged in; it offers when it finishes.
            if getattr(self, "_scan_thread", None) is not None \
                    and not self._scan_settled:
                return
            result, self._startup_offered = self._startup_offer, True
        self._offer_flash(result)

    def _offer_flash(self, result):
        """The startup check found boards to flash and the tool to do it: one
        acknowledged notice names them, and its Flash now runs
        `flash_firmware(True)` - the same unattended flash as the row's key,
        the dialog having been the question. Once per board set per run;
        Later leaves the row's key. Nothing is offered that the key would
        refuse for want of the tool (the Boards line says "by hand")."""
        result = dict(result or {})
        if self._scan_settled:
            with self._lock:
                connected = {name for name in self._found.values() if name}
            for key in ("to_flash", "stale", "never"):
                result[key] = [b for b in (result.get(key) or []) if b in connected]
        boards = list(result.get("to_flash") or [])
        if not boards or result.get("missing_tools"):
            return
        script = getattr(self._firmware, "script", None)
        if script is not None and not os.path.isfile(script):
            return
        key = frozenset(boards)
        with self._lock:
            if key in self._flash_offered:
                return
            self._flash_offered.add(key)
        if len(boards) == 1:
            detail = ("This overwrites its running firmware if it is plugged in; "
                      "otherwise it is skipped.")
        else:
            detail = ("This overwrites the running firmware of every one of them "
                      "that is plugged in; the others are skipped.")
        # Named here, not from the row's summary: that line says "never
        # flashed here" alone when it means every board, and a dialog that
        # is about to overwrite four boards names them.
        found = []
        if result.get("stale"):
            found.append(f"{_and(result['stale'])} out of date")
        if result.get("never"):
            found.append(f"{_and(result['never'])} never flashed here")
        events.warn(events.FIRMWARE_OUT_OF_DATE,
                    f"{'; '.join(found)}. Flash {'it' if len(boards) == 1 else 'them'} "
                    f"now? {detail} Launch waits until the flash finishes.",
                    source=self.NAME, ack=True,
                    action=("Flash now", events.SETUP_PANEL, "flash_firmware", (True,)))

    def _check_firmware_now(self):
        try:
            return self._firmware.check()
        except Exception as exc:        # never let a worker die silently
            events.debug("Firmware Check Failed", repr(exc), source=self.NAME,
                         exception=exc)
            events.warn("Firmware Check Failed", "The firmware check failed; "
                        "the details are in the log file.", source=self.NAME,
                        exception=exc)
            return None

    def _publish_firmware(self, result, prefix=""):
        with self._lock:
            self._firmware_result = result
            summary = (result or {}).get("summary") or "the check failed: see the log"
            self.firmware_status = prefix + summary
        events.debug("Firmware Check", f"{summary}: {(result or {}).get('boards')}",
                     source=self.NAME)

    def _flash_worker(self, boards):
        def on_line(line):
            events.debug("Firmware Flash", line, source=self.NAME)
            if line.strip():
                self.firmware_progress = line.strip()

        try:
            outcome = self._firmware.flash(boards, on_line=on_line)
        except Exception as exc:        # never let a worker die silently
            events.debug("Firmware Flash Failed", repr(exc), source=self.NAME,
                         exception=exc)
            outcome = {"ok": False, "last": repr(exc), "lines": []}
        result = self._check_firmware_now()
        left = [b for b in boards if b in ((result or {}).get("to_flash") or [])]
        absent = next((line.strip() for line in outcome.get("lines") or []
                       if line.startswith("Not connected")), "")
        with self._lock:
            self.firmware_progress = ""
        if not outcome.get("ok"):
            self._publish_firmware(result, prefix="the last flash failed; ")
            events.warn("Firmware Flash Failed",
                        f"Flashing {_and(boards)} failed: {outcome.get('last') or 'no output'}. "
                        + "".join(f"{h} " for h in outcome.get("hints") or ())
                        + "A board may be half-flashed; fix the cause and flash "
                        "again. The whole output is in the log file.",
                        source=self.NAME)
            return
        self._publish_firmware(result)
        done = [b for b in boards if b not in left]
        if left:
            events.warn("Firmware Not Flashed",
                        f"{_and(left)} still {'needs' if len(left) == 1 else 'need'} "
                        f"flashing. {absent or outcome.get('last') or ''}".strip(),
                        source=self.NAME)
        if done:
            events.info("Firmware Flashed", f"{_and(done)} now run this "
                        "checkout's firmware.", source=self.NAME)
        with self._lock:
            self._firmware_asked.clear()

    def _ask_about_firmware(self, configs, confirmed):
        """Launch warns, once, when a board it is about to open runs firmware
        older than this checkout's (a NeedsConfirm, not a refusal: the
        operator may mean it - main's boards). A board never flashed from
        here is not warned about: nothing says what it runs."""
        result = self._firmware_result or {}
        opening = {c.get("model") for c in configs if not c.get("sim")}
        stale = [b for b in (result.get("stale") or []) if b in opening]
        if not stale:
            return
        key = frozenset(stale)
        with self._lock:
            if confirmed or key in self._firmware_asked:
                self._firmware_asked.add(key)
                return
        sentence = (f"{stale[0]}'s firmware is out of date." if len(stale) == 1
                    else f"The firmware on {_and(stale)} is out of date.")
        prompt = f"{sentence} Launch anyway?"
        try:
            self._ask_before_taking_down("Relaunch", "launch", False)
        except NeedsConfirm as energized:
            # One question, not two: the rerun comes back confirmed.
            prompt = f"{energized.prompt} {prompt}"
        raise NeedsConfirm(prompt, "launch")

    def _firmware_section(self):
        """The Firmware row, right after Update: what the boards run against
        this checkout's sketches, the flash while it runs, and the two keys."""
        return sch.section(
            "Firmware",
            sch.readonly("Boards", "firmware_status", role="info"),
            sch.readonly("Flashing", "firmware_progress"),
            sch.button("Flash out-of-date boards", "flash_firmware", role="go",
                       confirm=self.FLASH_CONFIRM),
            sch.button("Check firmware", "check_firmware", role="neutral"),
            layout="row",
        )

    # -- A4: Switch to stable (owner decision 3, 2026-09-30; temporary) -------
    #: The one question before the boards are flashed with the stable
    #: firmware; the way back is the station's own startup Flash now.
    STABLE_CONFIRM = ("Switch to the stable station? The boards will be flashed "
                      "with the stable firmware, every model is closed, and the "
                      "stable app opens. To come back, start the station again "
                      "and accept Flash now.")

    @property
    def has_stable(self):
        return self._stable_root is not None

    @property
    def stable_status(self):
        return ("The lab's original app, beside this one. Switching flashes the "
                "boards with its firmware.")

    def _stable_check(self):
        if self._stable_firmware is None:
            self._stable_firmware = FirmwareCheck(
                sketch_root=self._stable_root / "firmware", channel=flashing.STABLE)
        return self._stable_firmware

    def switch_to_stable(self, confirmed=False):
        """Switch to stable: close every model (the Controller's own close
        path), flash the stable sketches to the boards that are plugged in,
        stamp them `stable`, start the stable app detached and end this one.
        A board that fails to flash stops the switch before anything is
        started; this station keeps running and says which board."""
        if not self.has_stable:
            self._refuse("This station has no stable app beside it: switch "
                         "branches by hand (dev/swap_branch.sh).")
        with self._lock:
            if self.is_flashing:
                self._refuse("A flash is already running. Wait for it to finish.")
            if _alive(self._apply_thread):
                self._refuse("An update is being applied. Wait for it to finish.")
            if self.is_scanning or self._is_restart_pending:
                self._refuse("The scan is using the ports. Switch when it has "
                             "finished, or press Cancel scan.")
        if getattr(self.controller, "is_energized", False):
            self._refuse("A model is energized. Stop it and put it out of its "
                         "mode first, then switch.")
        if not confirmed:
            raise NeedsConfirm(self.STABLE_CONFIRM, "switch_to_stable")
        running = list(getattr(self.controller, "model_names", ()) or ())
        events.info("Switch to Stable", "Closing "
                    f"{', '.join(running) or 'nothing'}, then flashing the stable "
                    "firmware.", source=self.NAME)
        self.controller.reset()
        self._is_launched = False
        self._refresh_rows()
        with self._lock:
            self.firmware_status = "switching to stable: flashing"
            self.firmware_progress = "starting…"
            self._flash_thread = threading.Thread(
                target=self._stable_worker, daemon=True, name="setup-stable-switch")
            self._flash_thread.start()
        return True

    def _stable_worker(self):
        def on_line(line):
            events.debug("Stable Flash", line, source=self.NAME)
            if line.strip():
                self.firmware_progress = line.strip()

        boards = list(flashing.BOARDS)
        try:
            outcome = self._stable_check().flash(boards, on_line=on_line)
        except Exception as exc:        # never let a worker die silently
            events.debug("Stable Flash Failed", repr(exc), source=self.NAME,
                         exception=exc)
            outcome = {"ok": False, "last": repr(exc), "lines": [], "results": {}}
        with self._lock:
            self.firmware_progress = ""
        if not outcome.get("ok"):
            failed = [b for b, status in (outcome.get("results") or {}).items()
                      if status == "FAILED"]
            which = _and(failed) if failed else "A board"
            events.warn("Switch to Stable Failed",
                        f"{which} could not be flashed with the stable firmware "
                        f"({outcome.get('last') or 'no output'}). "
                        + "".join(f"{h} " for h in outcome.get("hints") or ())
                        + "The stable app was not started; this station keeps "
                        "running. A board may be half-flashed: flash again from "
                        "the Firmware row.",
                        source=self.NAME)
            self._publish_firmware(self._check_firmware_now(),
                                   prefix="the switch to stable failed; ")
            return
        argv = [str(self._stable_root / _exe("station-stable"))]
        try:
            self._launch_stable(argv, str(self._stable_root))
        except Exception as exc:
            events.warn("Switch to Stable Failed", f"The stable app could not be "
                        f"started ({exc}). The boards now run the stable "
                        "firmware: start the stable app by hand, or accept "
                        "Flash now here to come back.", source=self.NAME,
                        exception=exc)
            self._publish_firmware(self._check_firmware_now())
            return
        events.info("Switch to Stable", "The stable app is starting; this "
                    "station closes.", source=self.NAME)
        events.flush_file()
        if self._exit_app is not None:
            self._exit_app()
        else:
            os._exit(0)

    def _stable_section(self):
        return sch.section(
            "Stable",
            sch.readonly("Stable app", "stable_status"),
            sch.button("Switch to stable", "switch_to_stable", role="neutral",
                       confirm=self.STABLE_CONFIRM),
            layout="row",
        )

    # -- the trial store (A3) -----------------------------------------------
    def _transfer_map(self):
        """The open Transfer Map, if one is: it adopts a store chosen here."""
        lookup = getattr(self.controller, "_model_or_none", None)
        model = lookup(TransferMap.NAME) if callable(lookup) else None
        return model if isinstance(model, TransferMap) else None

    @property
    def map_store_status(self):
        model = self._transfer_map()
        if model is not None:
            return model.store_status
        if os.environ.get("STATION_MAP_DB"):
            return TransferMap.describe_store(TransferMap.default_db_path())
        chosen = self._map_choices.read("map_store")
        return TransferMap.describe_store(Path(chosen) if chosen else None)

    def _map_target(self):
        """The open map, or a stand-in that only validates and remembers;
        either remembers through this session (`_SessionChoices`)."""
        target = self._transfer_map() or TransferMap()
        if PROFILES_ENABLED:
            target.choices = self._map_choices
        return target

    def open_map_store(self):
        """Open store, from Setup: the Transfer Map's own command, on the
        open map (which then records there) or on a stand-in that only
        validates and remembers the choice - in the signed-in user's
        settings, or the station's for a Guest."""
        target = self._map_target()
        target.store_path = self.map_store_path
        return target.open_store()

    def new_map_store(self):
        target = self._map_target()
        target.store_dir, target.store_name = self.map_store_dir, self.map_store_name
        return target.new_store()

    def _follow_store(self, previous):
        """After a sign-in or sign-out, the open map records where the new
        session's store is: the signed-in user's remembered store, or - back
        to Guest from a user - the station's remembered one."""
        if not PROFILES_ENABLED:
            return
        model = self._transfer_map()
        if model is None:
            return
        model.choices = self._map_choices
        if not self.user.is_guest:
            self._open_users_store(model)
        elif not previous.is_guest and not os.environ.get("STATION_MAP_DB"):
            station = user_config.read("map_store")
            if station:
                self._open_store_on(model, station, "The station's")

    def _open_users_store(self, model):
        """The signed-in user's remembered store (`UserStore` setting
        `map_store`) becomes `model`'s, when it is still there. `--map-db`
        (STATION_MAP_DB) wins over it, as over the station's choice."""
        if os.environ.get("STATION_MAP_DB"):
            return
        try:
            path = self.users.setting(self.user.email, "map_store")
        except Exception as exc:
            events.warn("Trial Store Not Read", f"{self.user.user_name}'s trial store "
                        "could not be read from the accounts file; trials go where "
                        "the Transfer Map says.", source=self.NAME, exception=exc)
            return
        if not path:
            return
        if not Path(path).is_file():
            events.warn("Trial Store Missing", f"{self.user.user_name}'s trial store "
                        f"{path} is not there any more. Trials go where the Transfer "
                        "Map says; open or make a store there.", source=self.NAME)
            return
        self._open_store_on(model, path, f"{self.user.user_name}'s")

    def _open_store_on(self, model, path, whose):
        """Open `path` on `model` the way Open store does; a refusal is a
        warning, never a failed sign-in."""
        current = getattr(model, "db_path", None)
        if (current is not None and getattr(model, "has_store", False)
                and Path(current) == Path(path).expanduser().resolve()):
            return
        model.store_path = str(path)
        try:
            model.open_store()
        except Refused as refusal:
            events.warn("Trial Store Not Opened", f"{whose} trial store {path} was "
                        f"not opened: {refusal.reason}", source=self.NAME)
        except Exception as exc:
            events.warn("Trial Store Not Opened", f"{whose} trial store {path} was "
                        "not opened; the details are in the log file.",
                        source=self.NAME, exception=exc)

    def _store_section(self):
        """The Trial store row: where the Transfer Map's trials go, and the
        same Open/New its own Store section offers (owner decision 4,
        2026-09-30: the operator chooses; nothing is chosen for them).
        Built, not yet in `_build_schema`: its place is just before Launch,
        and the section list is pinned by `tests/test_setup_registry.py`,
        outside this change's write set (handoff fix-dist-app A3)."""
        P = self.PARAMS
        return sch.section(
            "Trial store",
            sch.readonly("Store", "map_store_status", role="info"),
            sch.entry("Store file", "map_store_path", P["map_store_path"]),
            sch.button("Open store", "open_map_store", inputs=("map_store_path",)),
            sch.entry("Folder for a new store", "map_store_dir", P["map_store_dir"]),
            sch.entry("New store name", "map_store_name", P["map_store_name"]),
            sch.button("New store", "new_map_store",
                       inputs=("map_store_dir", "map_store_name")),
            layout="row",
        )

    # -- schema ------------------------------------------------------------
    def _build_rows(self):
        rows = {}
        for name, model_class in MODEL_TYPES.items():
            if getattr(model_class, "HOST", None):
                continue        # launched by its host's row, drawn on its page
            resources = resources_of(model_class)
            ports, gamepads = _split(resources)
            port_resource = ports[0] if ports else None
            gamepad_resource = gamepads[0] if gamepads else None
            # The row's Port dropdown holds the first port resource (or On /
            # SIM when there is none) and its Gamepad dropdown the first
            # gamepad; any further resource gets a dropdown of its own kind,
            # in declared order: (field, kind, label, resource).
            columns = []
            for resource in resources:
                if resource == port_resource:
                    continue
                if resource == gamepad_resource:
                    columns.append(("gamepad", "gamepad", "Gamepad", resource))
                else:
                    columns.append((resource, _kind(resource),
                                    resource.replace("_", " ").title(), resource))
            rows[_key_for(name)] = {
                "name": name,
                "needs_port": bool(ports),
                "needs_gamepad": bool(gamepads),
                "options_command": "port_options" if ports else "device_options",
                "port_resource": port_resource,
                "columns": columns,
            }
        return rows

    def _build_schema(self):
        """One compact table: a Devices header row, one row per model type,
        and a Launch row (Addendum 2). Every section is `layout="row"`, which
        is the hint each renderer lays out horizontally.

        The Update row comes first (owner, 2026-09-28): what this station
        runs, whether GitHub has something newer, and the one press that
        takes it. `sch.button` has no `enabled_by`, so Update now is gated by
        refusal (nothing to apply, a model running, a check under way)."""
        sections = [sch.section(
            "Update",
            sch.readonly("Station", "station_version"),
            sch.readonly("Updates", "update_status", role="info"),
            sch.button("Update now", "apply_update", role="go",
                       confirm=self.UPDATE_CONFIRM),
            sch.button("Check again", "check_updates", role="neutral"),
            # rb-restart R3: the Restart Needed dialog's action, and the way
            # back to it after "Later". Asks first; refused while energized.
            sch.button("Restart", "restart_station", role="neutral",
                       confirm=self.RESTART_CONFIRM),
            # Setup has no Diagnostics: the incoming commits sit in this row,
            # empty when nothing is coming.
            sch.readonly("Coming", "update_log"),
            layout="row",
        ), self._firmware_section()]
        if self.has_stable:
            # A4: a frozen bundle with the stable app beside it only.
            sections.append(self._stable_section())
        sections += [sch.section(
            "Devices",
            sch.button("Refresh", "refresh", role="info"),
            sch.readonly("Scan:", "scan_status"),
            # F18: a hung scan can be given up without restarting it.
            sch.button("Cancel scan", "cancel_scan", role="neutral",
                       enabled_when=[self.SCANNING]),
            # L3: where the Web view is served; empty in the desktop views.
            sch.readonly("Address", "web_address"),
            layout="row",
        )]
        for key, row in self._rows.items():
            # The row's caption is the model's name; no second copy in a cell.
            # No Launch tick (owner 2026-10-07): every connected device
            # launches, and the Status cell says which are.
            elements = [
                sch.dropdown("Port", f"{key}_port", f"set_{key}_port",
                             row["options_command"]),
            ]
            if row["needs_port"]:
                # Its board reset and the model opened again on the same
                # port; asks first, refused while energized (`_hard_reset`).
                # Right after Port, so a row without it (no port) or without
                # a Gamepad still lines up: a view pads before the Status.
                elements.append(sch.button("Hard reset", f"hard_reset_{key}",
                                           role="neutral",
                                           enabled_when=[self.LAUNCHED]))
            # Built from the class's resources; for the six built-ins that is
            # a Gamepad dropdown where one is declared, and nothing else.
            for field, kind, label, _ in row["columns"]:
                elements.append(sch.dropdown(
                    label, f"{key}_{field}", f"set_{key}_{field}",
                    "port_options" if kind == "port" else "gamepad_options"))
            elements.append(sch.readonly("Status:", f"{key}_status"))
            sections.append(sch.section(row["name"], *elements, layout="row"))
        sections.append(sch.section(
            "Launch",
            # No sentence here (I4, audit round 5): the rows say what is
            # ticked, the scan line says why Launch waits, and a Launch with
            # nothing ticked is refused with the reason. `summary` stays a
            # state value for the API and the tests.
            sch.button("Launch", "launch", role="go",
                       enabled_when=[self.READY]),
            sch.button("Relaunch", "launch", role="go",
                       enabled_when=[self.LAUNCHED]),
            # Never gated: taking the system down must not depend on what the
            # panel happens to be doing.
            # L17: not a third "stop" word beside the disc; it closes the
            # models (stop, disconnect, destruct) and Launch builds them again.
            sch.button("Close every model", "stop_system", role="neutral"),
            layout="row",
        ))
        if PROFILES_ENABLED:
            sections.insert(0, self._account_section())
        return sch.schema(*sections)

    # -- plumbing ----------------------------------------------------------
    def _key_of(self, name):
        if not name:
            return None
        for key, row in self._rows.items():
            if row["name"] == name:
                return key
        return None

    def _refresh_rows(self):
        """Every row's launch flag, status line and the launch summary, from
        one place."""
        with self._lock:
            found = dict(self._found)
        for key, row in self._rows.items():
            setattr(self, f"{key}_enabled", self._will_launch(key, row, found))
            setattr(self, f"{key}_status", self._row_status(key, row, found))
        self._refresh_summary()

    def _will_launch(self, key, row, found):
        """A row launches when its board answered as this model on its port,
        when it is set to SIM, or when it needs no port. Nothing else."""
        choice = getattr(self, f"{key}_port")
        if choice == SIM or not row["needs_port"]:
            return True
        return bool(choice) and found.get(choice) == row["name"]

    def _row_status(self, key, row, found):
        """What the row's Status cell says: simulated / on / detected: X /
        not connected / not scanned, and a board seen after the launch. The
        one sentence the operator reads to know whether the row launches."""
        with self._lock:
            seen = self._seen.get(key)
        if seen:
            return f"seen on {seen}: restart to launch"
        choice = getattr(self, f"{key}_port")
        if self.guest_locked and is_signed_in_only(row["name"]):
            return "sign in to use"
        if choice == SIM:
            return "simulated"
        if not row["needs_port"]:
            return "on"
        if choice == NOT_CONNECTED:
            return "not connected"
        if choice not in found:
            return "not scanned"
        answered = found[choice]
        return f"detected: {answered}" if answered else "not connected"

    def _take_seen(self):
        """After Close every model: a board seen after the launch is a row
        like any other again, so the next Launch includes it."""
        with self._lock:
            seen, self._seen = dict(self._seen), {}
            chosen = set(self._chosen)
        for key, port in seen.items():
            if key not in chosen and port in self._port_choices(key):
                setattr(self, f"{key}_port", port)

    def _drop_stale_selections(self):
        """A port that is no longer attached must not stay selected: the old
        wizard reset the dropdown to the first entry on every refresh. The
        operator's claim on that row goes with it, so the next auto-assign is
        free to fill the row in."""
        gamepads = self.gamepad_options()
        for key, row in self._rows.items():
            choice = getattr(self, f"{key}_port")
            if choice != NOT_CONNECTED and choice not in self._port_choices(key):
                # The board is gone: the row is not connected again, so a
                # Launch cannot silently open some other port.
                setattr(self, f"{key}_port", NOT_CONNECTED)
                with self._lock:
                    self._chosen.discard(key)
                    self._seen.pop(key, None)
            if row["needs_gamepad"] and getattr(self, f"{key}_gamepad") not in gamepads:
                setattr(self, f"{key}_gamepad", "None")
            for field, kind, _, _ in row["columns"]:
                if field == "gamepad":
                    continue
                if kind == "port" and getattr(self, f"{key}_{field}") not in self.port_options():
                    setattr(self, f"{key}_{field}", SIM)
                elif kind == "gamepad" and getattr(self, f"{key}_{field}") not in gamepads:
                    setattr(self, f"{key}_{field}", "None")

    def _refresh_summary(self):
        """A count, not a list (G3): the rows already say which model is on
        which port, and the joined list widened every table it sat in."""
        count = len(self.configs)
        if count == 0:
            self._selected = "nothing selected"
        else:
            self._selected = (f"{count} device{'s' if count != 1 else ''} "
                              "to launch.")


def _exe(name):
    return name + ".exe" if os.name == "nt" else name


def _launch_detached(argv, cwd):
    """Start `argv` so it outlives this process: its own session (POSIX), or
    detached from this console in a new process group (Windows) - the
    pattern `app.restart_process` uses for the update's swap script."""
    kwargs = {"cwd": cwd, "stdin": subprocess.DEVNULL,
              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def _and(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _release_ready(latest, lines):
    """B4: "v1.3.0 is ready: <the notes' first line>. Update now, then restart." """
    first = str(lines[0]).strip() if lines else ""
    return (f"{latest} is ready" + (f": {first.rstrip('.')}" if first else "")
            + ". Update now, then restart.")


def _subject(oneline):
    """A `git log --oneline` line without its leading short sha."""
    head, _, rest = str(oneline).strip().partition(" ")
    if rest and len(head) >= 7 and all(c in "0123456789abcdef" for c in head.lower()):
        return rest.strip()
    return str(oneline).strip()


def _alive(thread):
    return bool(thread is not None and thread.is_alive())


def _split(resources):
    """(port resources, gamepad resources), each in declared order."""
    return ([r for r in resources if _kind(r) == "port"],
            [r for r in resources if _kind(r) == "gamepad"])


def _resource_value(config, model_class, resource):
    """`resource`'s value in a build config. A config names each resource;
    the first of each kind may also go by the kind's own key (`port`,
    `gamepad`), which is what Setup's rows have always written."""
    if resource in config:
        return config[resource]
    ports, gamepads = _split(resources_of(model_class))
    for kind, group in (("port", ports), ("gamepad", gamepads)):
        if group and group[0] == resource:
            return config.get(kind)
    return None


class _SessionChoices:
    """`TransferMap.choices` for one Setup's session (2026-10-07, the
    per-account trial store): a signed-in user's store choice is kept in
    their settings (`UserStore.put_setting(email, "map_store", path)`) and
    read from there; a Guest reads and writes the station's choices file
    (`controller.user_config`), so a Guest keeps the station's store."""

    def __init__(self, setup):
        self._setup = setup

    def _email(self):
        user = self._setup.user
        return None if user.is_guest else user.email

    def read(self, key, default=None):
        email = self._email()
        if email:
            try:
                value = self._setup.users.setting(email, key)
            except Exception:
                value = None
            if value:
                return value
        return user_config.read(key, default)

    def write(self, key, value):
        email = self._email()
        if not email:
            return user_config.write(key, value)
        try:
            return self._setup.users.put_setting(email, key, value)
        except Exception as exc:
            # The map says "Store Not Remembered" for an OSError.
            raise OSError(str(exc)) from exc


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


class _HardReset:
    """One launched row's Hard reset: `hard_reset_<row>(confirmed=False)`."""

    __slots__ = ("setup", "key")

    def __init__(self, setup, key):
        self.setup, self.key = setup, key

    def __call__(self, confirmed=False):
        return self.setup._hard_reset(self.key, confirmed)

    def __repr__(self):
        return f"<hard_reset_{self.key}>"
