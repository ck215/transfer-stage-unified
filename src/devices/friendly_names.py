"""Friendly names for the ports and gamepads a station typically has.

Labels only: what a dropdown *shows*. The value a config, a choices file or
the wire carries stays the raw port path or gamepad id; nothing here is ever
parsed back. Anything this table does not know falls back to the raw string,
so a new, unexpected device still shows (and works) exactly as before.

A port's label, best first:

1. the identity the handshake answered ("Stepper Probe — ACM0"),
2. a known USB adapter by VID:PID ("Arduino Mega — ACM0"),
3. the raw path ("/dev/ttyACM0", "COM3").

Pure functions, no hardware access: the USB ids come from the `hwid` string
`serial_port.list_ports()` already returns ("USB VID:PID=2341:0042 SER=...",
the same shape on Linux, macOS and Windows).
"""
import re

#: (vid, pid) -> adapter name; pid None matches any product of that vendor.
#: Exact pairs are looked up before the vendor-wide fallbacks.
USB_ADAPTERS = {
    (0x2341, 0x0042): "Arduino Mega",       # Mega 2560 R3 (the lab's probes)
    (0x2341, 0x0010): "Arduino Mega",       # Mega 2560 (original)
    (0x2A03, 0x0042): "Arduino Mega",       # arduino.org Mega 2560
    (0x2341, 0x0043): "Arduino Uno",
    (0x2341, 0x0001): "Arduino Uno",
    (0x2341, None): "Arduino",
    (0x2A03, None): "Arduino",
    (0x16C0, 0x0483): "Teensy",             # Teensyduino USB Serial
    (0x16C0, 0x0487): "Teensy",             # Teensyduino dual serial
    (0x0403, 0x6001): "FTDI USB-serial",    # FT232R (the Rotator's cable)
    (0x0403, None): "FTDI USB-serial",
    (0x1A86, 0x7523): "CH340 USB-serial",
    (0x1A86, 0x55D4): "CH9102 USB-serial",
    (0x10C4, 0xEA60): "CP210x USB-serial",
    (0x067B, 0x2303): "Prolific USB-serial",
}

#: SDL gamepad names -> short names, first match wins (case-insensitive).
GAMEPADS = (
    (r"logitech.*\bf310\b", "Logitech F310"),
    (r"logitech.*\bf710\b", "Logitech F710"),
    (r"logitech.*\bf510\b", "Logitech F510"),
    (r"logitech dual action", "Logitech Dual Action"),
    (r"xbox series", "Xbox Series X"),
    (r"xbox one", "Xbox One"),
    (r"xbox 360|x360", "Xbox 360"),
    (r"dualsense|ps5", "PS5 DualSense"),
    (r"dualshock 4|ps4", "PS4 DualShock"),
    (r"switch pro", "Switch Pro"),
)

_VID_PID = re.compile(r"VID:PID=([0-9A-Fa-f]{1,4}):([0-9A-Fa-f]{1,4})")
_GAMEPAD_ID = re.compile(r"^ID (\d+): (.+)$")
_SEP = " — "


def short_port(port):
    """The distinguishing tail of a port path: "/dev/ttyACM0" -> "ACM0",
    "/dev/ttyUSB0" -> "USB0", "/dev/cu.usbmodem14101" -> "usbmodem14101",
    "COM3" -> "COM3"."""
    text = str(port)
    for prefix in ("/dev/tty.", "/dev/cu.", "/dev/tty", "/dev/"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix):]
    return text


def usb_ids(hwid):
    """(vid, pid) from a pyserial hwid string, or None."""
    match = _VID_PID.search(str(hwid or ""))
    if not match:
        return None
    return int(match.group(1), 16), int(match.group(2), 16)


def usb_adapter(hwid):
    """The known adapter name for a hwid string, or None."""
    ids = usb_ids(hwid)
    if ids is None:
        return None
    return USB_ADAPTERS.get(ids) or USB_ADAPTERS.get((ids[0], None))


def port_label(port, identity=None, hwid=None):
    """What a port dropdown shows for `port`. The raw path when nothing is
    known about it."""
    text = str(port)
    if identity:
        return f"{identity}{_SEP}{short_port(text)}"
    adapter = usb_adapter(hwid)
    if adapter:
        return f"{adapter}{_SEP}{short_port(text)}"
    return text


def gamepad_name(name):
    """A short name for an SDL gamepad name, or None when it is not known."""
    for pattern, short in GAMEPADS:
        if re.search(pattern, str(name), re.IGNORECASE):
            return short
    return None


def gamepad_labels(options):
    """Labels for gamepad option strings ("ID 0: Logitech Gamepad F310"),
    parallel to `options`. Unknown pads and "None" keep their raw string; two
    pads that would share a short name get their SDL index back
    ("Xbox One (ID 0)", "Xbox One (ID 1)")."""
    shorts = []
    for option in options:
        match = _GAMEPAD_ID.match(str(option))
        shorts.append(gamepad_name(match.group(2)) if match else None)
    labels = []
    for option, short in zip(options, shorts):
        if short is None:
            labels.append(str(option))
        elif shorts.count(short) > 1:
            index = _GAMEPAD_ID.match(str(option)).group(1)
            labels.append(f"{short} (ID {index})")
        else:
            labels.append(short)
    return labels
