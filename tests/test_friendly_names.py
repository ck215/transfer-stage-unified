"""Friendly device names: labels only, raw strings as the fallback."""
import pytest

from devices import friendly_names as fn

# What this lab PC's `serial.tools.list_ports.comports()` reported (2026-10-07).
MEGA = "USB VID:PID=2341:0042 SER=03536383236351B02382 LOCATION=3-1.3:1.0"
TEENSY = "USB VID:PID=16C0:0483 SER=3284150 LOCATION=1-8:1.0"
FTDI = "USB VID:PID=0403:6001 SER=FTHG9FB5 LOCATION=1-7.3"


@pytest.mark.parametrize("port,short", [
    ("/dev/ttyACM0", "ACM0"), ("/dev/ttyUSB0", "USB0"),
    ("/dev/cu.usbmodem14101", "usbmodem14101"),
    ("/dev/tty.usbserial-A1", "usbserial-A1"),
    ("COM3", "COM3"), ("SIM", "SIM"), ("/dev/", "/dev/"),
])
def test_short_port_keeps_the_distinguishing_tail(port, short):
    assert fn.short_port(port) == short


@pytest.mark.parametrize("hwid,name", [
    (MEGA, "Arduino Mega"), (TEENSY, "Teensy"), (FTDI, "FTDI USB-serial"),
    ("USB VID:PID=2341:8036 SER=1", "Arduino"),          # vendor fallback
    ("USB VID:PID=1A86:7523 LOCATION=1-2", "CH340 USB-serial"),
    ("USB VID:PID=10C4:EA60 SER=0001", "CP210x USB-serial"),
    ("USB VID:PID=067B:2303", "Prolific USB-serial"),
    # Windows reports the same shape.
    ("USB VID:PID=2341:0042 SER=7&1A2B&0&3 LOCATION=1-3", "Arduino Mega"),
    ("USB VID:PID=1234:5678 SER=1", None),               # unknown vendor
    ("n/a", None), ("", None), (None, None), ("PCI", None),
])
def test_usb_adapter_by_vid_pid(hwid, name):
    assert fn.usb_adapter(hwid) == name


def test_port_label_prefers_the_handshake_then_the_adapter_then_the_path():
    assert fn.port_label("/dev/ttyACM0", "Stepper Probe", MEGA) == \
        "Stepper Probe — ACM0"
    assert fn.port_label("/dev/ttyACM1", None, TEENSY) == "Teensy — ACM1"
    assert fn.port_label("/dev/ttyUSB0", None, FTDI) == "FTDI USB-serial — USB0"
    assert fn.port_label("/dev/ttyS1", None, "PNP0501") == "/dev/ttyS1"
    assert fn.port_label("COM7") == "COM7"
    assert fn.port_label("COM7", None, MEGA) == "Arduino Mega — COM7"
    assert fn.port_label("/dev/cu.usbmodem1101", "Rotator") == \
        "Rotator — usbmodem1101"


@pytest.mark.parametrize("name,short", [
    ("Logitech Gamepad F310", "Logitech F310"),
    ("Logitech Gamepad F710", "Logitech F710"),
    ("Xbox Series X Controller", "Xbox Series X"),
    ("Microsoft Xbox Series S|X Controller", "Xbox Series X"),
    ("Xbox One S Controller", "Xbox One"),
    ("Xbox 360 Controller", "Xbox 360"),
    ("PS4 Controller", "PS4 DualShock"),
    ("DualSense Wireless Controller", "PS5 DualSense"),
    ("Nintendo Switch Pro Controller", "Switch Pro"),
    ("Some Arcade Stick", None),
])
def test_gamepad_short_names(name, short):
    assert fn.gamepad_name(name) == short


def test_gamepad_labels_are_parallel_and_fall_back_to_the_raw_string():
    options = ["None", "ID 0: Logitech Gamepad F310",
               "ID 1: Xbox Series X Controller", "ID 2: Some Arcade Stick",
               "Pad0"]
    assert fn.gamepad_labels(options) == [
        "None", "Logitech F310", "Xbox Series X", "ID 2: Some Arcade Stick",
        "Pad0"]


def test_two_identical_pads_keep_their_ids_apart():
    assert fn.gamepad_labels(["ID 0: Xbox One Controller",
                              "ID 1: Xbox One S Controller"]) == [
        "Xbox One (ID 0)", "Xbox One (ID 1)"]
