"""The composition root (RC-9 item 1).

Every launcher — Tk, PySide, Web — assembles a running system by calling the
same six functions in the same order:

    discover_ports() / discover_controllers()   what hardware is attached
    normalize_config(raw)                       operator choices -> configs
    validate_assignment(configs)                refuse impossible assignments
    build_models(configs, manager)              construct and register
    link_models(manager)                        wire cross-model dependencies

There is no per-view setup logic beyond collecting the operator's choices.
Before this, each launcher had its own version of the last four steps, and
they disagreed: Web dropped the `enabled` flag so disabled devices were
built and wired anyway, Web enumerated controllers in a `python3` subprocess
that fabricated placeholder controller entries the desktop views never
and Tk reused one claims dict across every relaunch so the second launch
collided with the first one's stale claims.
"""

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

def discover_controllers() -> list[str]:
    """Attached game controllers as `["None", "ID 0: <name>", ...]`.

    **In-process, through the one SDL owner** (`InputService`, RC-13). The
    three enumerations this replaces disagreed in ways an operator could see:
    Tk and PySide each ran their own `pygame.joystick` block, and Web shelled
    out to a literal `python3` — not `sys.executable`, so in a virtualenv it
    was a different interpreter that usually had no pygame — then, finding
    nothing, **invented** two placeholder entries named after virtual
    controllers (MANAGER-18, GAMEPAD-18, WEB-15). Those are not controllers.
    They were offered in the web wizard only, they were special-cased out of
    the collision check in `validate_assignment`, and a device assigned one
    got no input at all.

    An empty list here means no controller is attached, and every frontend
    now says so the same way.
    """
    from controller.input_service import input_service
    return ["None"] + input_service.names()


def probe_device_at(port: str, should_abort=None) -> str | None:
    """Identify whatever is on `port`, or None.

    `should_abort` is an optional zero-argument predicate: return True and
    the scan gives up at the next check, without opening any further port
    (MANAGER-20). This call blocks for ~1.5 s + 3 s *per baud per port*, and
    the Qt setup window runs it on a `QThread` the operator can close
    underneath. `QThread.requestInterruption()` only helps if the thing the
    thread is blocked inside is watching for it, so the check is made between
    baud attempts **and inside the polling loops**, which is where the time
    actually goes.

    A predicate that raises is treated as "do not abort": a broken abort hook
    must not be able to stop the scan working at all.
    """
    def _aborted():
        if should_abort is None:
            return False
        try:
            return bool(should_abort())
        except Exception:
            return False

    if _aborted():
        return None

    try:
        import serial
    except ImportError:
        return None

    # Derived from the one registry (RC-7). This used to be a separate
    # literal that knew four of the six devices, which is how the sidebar,
    # the builder and the identity map came to disagree.
    from model.devices import IDENTITY_CHARS as DEVICE_MAP
    DEV_PATTERN = re.compile(r"(?:DEV:\s*|<)([sdct])>?", re.IGNORECASE)
    
    device_found = False
    device_name = None

    # 1. 57600 baud (SMC100-specific, cheap — tried first so the SMC100
    # rotator doesn't have to burn through both custom-firmware handshake
    # timeouts below before reaching the check that actually identifies it)
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

    if device_found or _aborted(): return device_name

    # 2. 500k baud
    try:
        with serial.Serial(port, baudrate=500000, timeout=0.1, write_timeout=0.2) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(1.5)
            start_time = time.time()
            response_buffer = ""
            while (time.time() - start_time < 3.0 and not device_found
                   and not _aborted()):
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

    if device_found or _aborted(): return device_name

    # 3. 115200 baud
    try:
        with serial.Serial(port, baudrate=115200, timeout=0.1, write_timeout=0.2) as ser:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(1.5)
            start_time = time.time()
            response_buffer = ""
            while (time.time() - start_time < 3.0 and not device_found
                   and not _aborted()):
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

    return device_name

def normalize_config(raw) -> list[dict]:
    """Operator choices -> the canonical config list every stage below wants.

    Accepts what each frontend actually has: a list of per-device dicts, or
    a `{device_name: config}` mapping (the shape the web wizard posts).
    Returns `[{"device", "port", "controller", "mode"}]`.

    **Disabled devices are dropped here** (RC-9 item 3). Tk and PySide never
    built an unchecked device; Web discarded the `enabled` flag during its
    own normalization and then read it back off the normalized dict, where
    it no longer existed — so `_disabled_in_setup` was computed as
    `not True` for every model and nothing was ever treated as disabled
    (WEB-4, MANAGER-12). A full set of probes was constructed, polled, shown
    as live cards, and offered to Red Percent as sync sources, for an
    operator who had enabled one device. Not constructing them is the fix
    the audit asked for; the flag goes with it.
    """
    if isinstance(raw, dict):
        items = [dict(cfg, device=name) for name, cfg in raw.items()
                 if isinstance(cfg, dict)]
    elif isinstance(raw, list):
        items = []
        for cfg in raw:
            if not isinstance(cfg, dict):
                raise TypeError("each configuration must be a dictionary")
            items.append(cfg)
    else:
        raise TypeError("configs must be a list or a dictionary")

    configs = []
    for cfg in items:
        if not cfg.get("enabled", True):
            continue
        mode = str(cfg.get("mode", "") or "").lower()
        port = cfg.get("port")
        # "Headless" is the desktop wizard's word and "SIM" is the models',
        # and the translation used to happen at three different points in
        # three different launchers — once *after* validate_assignment, so
        # a headless device could be reported as colliding with itself.
        if mode == "simulation" or port == "Headless":
            port = "SIM"
        configs.append({
            "device": cfg.get("device"),
            "port": port,
            "controller": cfg.get("controller_id", cfg.get("controller", "None")),
            # Derived, never carried through. `mode` is an input the web
            # wizard sends and the desktop wizards do not, and nothing
            # downstream reads it — so carrying it verbatim made two
            # launchers produce different configs for the same system while
            # meaning the same thing (I-9.2). The port is the truth.
            "mode": "simulation" if port == "SIM" else "hardware",
        })
    return configs


def link_models(manager) -> None:
    """Wire cross-model dependencies through the registry (RC-9 item 2).

    A model that needs to follow other models implements `bind_registry`
    and maintains its own references from `registered`/`released`. The
    launcher's job ends at calling this once.

    What this replaces: six lines of "find the Red Percent model, collect
    everything with a `pos_x`, assign `available_probes`, prefer the stepper"
    pasted into Tk's launcher, PySide's launcher and the web adapter. It was
    a snapshot taken at launch that nothing ever refreshed, so a released
    probe stayed selected and Red Percent logged its frozen last position
    for the rest of the run.
    """
    for _name, model in manager.get_active_models_snapshot().items():
        bind = getattr(model, "bind_registry", None)
        if callable(bind):
            bind(manager)


def build_models(active_configs: list[dict], manager=None,
                 claims: dict | None = None) -> dict[str, object]:
    """Construct the configured models, all or nothing (MANAGER-5).

    If a later constructor raises, every model already built is torn down
    before the exception propagates. Without that rollback a failed launch
    left earlier devices holding their serial ports open with no reference
    to them anywhere, so the next attempt could not reopen those ports and
    only a process restart recovered.

    When `manager` is given, models are registered as they are built, so
    ownership never sits in a local dict that an exception can strand.

    **The claims dict belongs to the build, not to the caller** (MANAGER-16).
    Tk passed one `SetupWindow.active_claims` to every launch and nothing
    ever cleared it, so a second launch from the same setup window saw the
    first launch's claims and refused the controller as already taken — by a
    model that no longer existed. PySide and Web each passed a fresh `{}`,
    which is why only Tk had the bug and why nobody noticed the divergence.
    One dict per build, created here, for all three.
    """
    active_claims = {} if claims is None else claims
    built_models = {}

    def _roll_back():
        for name, model in reversed(list(built_models.items())):
            try:
                if manager is not None and manager.get_model(name) is not None:
                    manager.release(name)
                else:
                    model.teardown()
            except Exception as e:
                print(f"[build_models] Rollback of {name} failed: {e}")

    try:
        return _build_each(active_configs, active_claims, manager, built_models)
    except Exception:
        _roll_back()
        raise


def _build_each(active_configs, active_claims, manager, built_models):
    for config in active_configs:
        device = config.get("device")
        port = config.get("port")
        controllerID = config.get("controller")
        
        # One registry, one dispatch (RC-7). The if/elif chain this replaces
        # was the copy of the device list that actually constructed things,
        # and it silently skipped any name the chain did not mention.
        from model import devices
        model = devices.build(device, port, controllerID, active_claims)
        if model is not None:
            built_models[device] = model

        built = built_models.get(device)
        if manager is not None and built is not None:
            manager.register(device, built, config)

    return built_models

def validate_assignment(active_configs: list[dict]) -> list[str]:
    errors = []
    assigned_ports = set()
    assigned_controllers = set()
    
    for c in active_configs:
        dev = c.get("device")
        port = c.get("port")
        ctrl = c.get("controller", "None")

        if not dev or not port:
            errors.append(f"Device name and port are required in config: {c}")
            continue

        if port == "Headless":
            port = "SIM"

        if dev != "Red Percent Window" and port not in ("SIM", "None"):
            if port in assigned_ports:
                errors.append(f"Port collision: Port '{port}' is assigned to multiple devices")
            assigned_ports.add(port)

        # The `"Virtual" not in ctrl` exemption that was here existed for the
        # placeholder names the web wizard used to invent when it could not
        # enumerate anything. Nothing produces those names now, so the
        # exemption only served to let two devices claim one controller
        # without complaint if a caller posted one by hand.
        if ctrl and "None" not in ctrl and "N/A" not in ctrl and dev != "Red Percent Window":
            if ctrl in assigned_controllers:
                errors.append(f"Controller collision: Controller '{ctrl}' is assigned to multiple devices")
            assigned_controllers.add(ctrl)
            
    return errors
