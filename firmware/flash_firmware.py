#!/usr/bin/env python3
"""Auto-detect connected stage-control boards and flash each one with this
branch's firmware.

Detection is self-contained: this script re-implements the exact "DEV:"
handshake that src/mainGUI.py's SetupWindow uses at setup time (500000 baud,
2 s settle, write b"s\\n", read for 1.5 s, match ``DEV:\\s*([sdct])``), so a
port is identified here the same way the app identifies it. A board must
already be running *some* firmware that answers the handshake to be
auto-identified -- a blank/never-flashed chip won't respond and needs
--port to name it manually for its first flash.

The 57600-baud SMC100 fallback that mainGUI also runs is deliberately NOT
here: the SMC100 Rotator is a purchased Newport controller, never a flash
target.

Board split:
    Stepper Probe, DC Probe, Chuck Positioner  -> Mega2560, via arduino-cli
    Temperature Controller                     -> Teensy 3.5, compiled with
                                                  arduino-cli's Teensy core,
                                                  uploaded with the dedicated
                                                  teensy_loader_cli

Usage:
    python flash_firmware.py                        # detect, confirm, flash all
    python flash_firmware.py --list                 # detect only, flash nothing
    python flash_firmware.py --only "Stepper Probe" "Chuck Positioner"
    python flash_firmware.py --port "Temperature Controller=COM7"
    python flash_firmware.py --yes                  # skip per-board confirmation
    python flash_firmware.py --dry-run              # print commands, run nothing
    python flash_firmware.py --install-deps         # install arduino-cli
                                                    # cores/libraries, then exit

Requires on PATH:
    arduino-cli         https://arduino.github.io/arduino-cli/latest/installation/
    teensy_loader_cli   https://www.pjrc.com/teensy/loader_cli.html
"""
import argparse
import re
import shlex
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import serial
import serial.tools.list_ports

REPO_ROOT = Path(__file__).resolve().parent.parent

MEGA_FQBN = "arduino:avr:mega:cpu=atmega2560"

# Temperature Controller runs on a Teensy 3.5.
TEENSY_FQBN = "teensy:avr:teensy35"
TEENSY_MCU = "MK64FX512"
TEENSY_URL = "https://www.pjrc.com/teensy/package_teensy_index.json"

# --- Libraries, derived from the #include lines of THIS branch's sketches ---
#   stepper_firmware.ino / chuck_firmware.ino: AccelStepper.h, TMCStepper.h
#   high_polling_rate.ino:                     (no third-party includes)
#   temp_controller.ino:                       max6675.h, Wire.h,
#                                              LiquidCrystal_I2C.h
# Wire ships with every core. max6675.h is Adafruit's "MAX6675 library" in the
# arduino-cli index. LiquidCrystal_I2C.h is NOT the index library here -- see
# MANUAL_LIBS below.
MEGA_LIBS = ["AccelStepper", "TMCStepper"]
TEENSY_LIBS = ["MAX6675 library"]

# Libraries arduino-cli cannot install, because they are not in its index.
MANUAL_LIBS = {
    "LiquidCrystal_I2C (NewLiquidCrystal fork)": (
        "temp_controller.ino calls LiquidCrystal_I2C(0x27, 2, 1, 0, 4, 5, 6, 7, 3, "
        "POSITIVE) -- a 10-argument constructor that only the NewLiquidCrystal "
        "fork provides. It is NOT in the arduino-cli library index and the "
        "index's own 'LiquidCrystal I2C' will NOT compile against this sketch. "
        "Install it by hand into your Arduino libraries/ directory from "
        "https://github.com/fmalpartida/New-LiquidCrystal before flashing the "
        "Temperature Controller."
    ),
}

DEVICES = {
    "Stepper Probe": {
        "sketch": REPO_ROOT / "firmware" / "stepper_firmware",
        "board": "mega",
    },
    "DC Probe": {
        "sketch": REPO_ROOT / "firmware" / "high_polling_rate",
        "board": "mega",
    },
    "Chuck Positioner": {
        "sketch": REPO_ROOT / "firmware" / "chuck_firmware",
        "board": "mega",
    },
    "Temperature Controller": {
        "sketch": REPO_ROOT / "firmware" / "temp_controller",
        "board": "teensy",
    },
}

# --- Handshake constants, copied from src/mainGUI.py SetupWindow ------------
HANDSHAKE_BAUD = 500000
HANDSHAKE_TIMEOUT = .1
HANDSHAKE_WRITE_TIMEOUT = .2
HANDSHAKE_SETTLE_S = 2.0
HANDSHAKE_READ_WINDOW_S = 1.5
DEV_PATTERN = re.compile(r"DEV:\s*([sdct])", re.IGNORECASE)
DEVICE_MAP = {
    "s": "Stepper Probe",
    "d": "DC Probe",
    "c": "Chuck Positioner",
    "t": "Temperature Controller",
}

# Ports that are placeholders, not real hardware.
NON_HARDWARE_PORTS = ("Headless", "SIM")

# --- Protocol this branch's app speaks, read off src/serialDrive.py ---------
PACKET_FORMAT = "<BBfffhhhhhhh"
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)


def validate_sketches():
    """Every DEVICES entry must point at a real sketch directory holding an
    .ino named after that directory. Raises RuntimeError naming every
    problem, so a bad checkout fails loudly at startup instead of halfway
    through a flash."""
    problems = []
    for name, cfg in DEVICES.items():
        sketch = cfg["sketch"]
        if not sketch.is_dir():
            problems.append(f"{name}: sketch directory missing: {sketch}")
            continue
        ino = sketch / f"{sketch.name}.ino"
        if not ino.is_file():
            problems.append(
                f"{name}: {sketch} has no sketch file named {ino.name} "
                "(arduino-cli requires the .ino to match its directory)"
            )
    if problems:
        raise RuntimeError(
            "firmware sketches are not where flash_firmware.py expects them:\n  "
            + "\n  ".join(problems)
        )
    return True


def print_protocol_banner():
    """State what these sketches speak, so an operator knows what is going on
    the board. A board carrying newer, incompatible firmware will not talk to
    this branch's app until it is flashed back to these sketches."""
    print("Firmware protocol these sketches speak (must match this branch's app):")
    print("  enable/disable = 't' toggle")
    print(f"  manual packet  = {PACKET_SIZE}-byte '{PACKET_FORMAT}'")
    print("  identity query = 's' -> 'DEV: <s|d|c|t>'")
    print(
        "  src/serialDrive.py sends exactly this. A board running newer or "
        "different firmware will not drive from this branch -- flashing these "
        "sketches is how you restore compatibility."
    )


def discover_ports():
    """Every serial port the OS reports, sorted. Windows COM ports included --
    the lab PC is Windows, so filtering names beginning with 'COM' would throw
    away every real port."""
    try:
        return sorted(p.device for p in serial.tools.list_ports.comports())
    except Exception as exc:  # pragma: no cover - list_ports is not expected to raise
        print(f"  [WARN] could not enumerate serial ports: {exc}")
        return []


def probe_device_at(port):
    """Run mainGUI's DEV: handshake against one port.

    Returns the device name ("Stepper Probe" / "DC Probe" /
    "Chuck Positioner" / "Temperature Controller") or None. Open/IO failures
    return None but are printed -- a port that cannot be opened is almost
    always the thing the operator needs to know about (busy, permissions,
    wrong driver).
    """
    try:
        with serial.Serial(
            port,
            baudrate=HANDSHAKE_BAUD,
            timeout=HANDSHAKE_TIMEOUT,
            write_timeout=HANDSHAKE_WRITE_TIMEOUT,
        ) as ser:
            time.sleep(HANDSHAKE_SETTLE_S)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            ser.write(b"s\n")
            start = time.time()
            while time.time() - start < HANDSHAKE_READ_WINDOW_S:
                if ser.in_waiting > 0:
                    response = ser.read(ser.in_waiting)
                else:
                    time.sleep(0.05)
                    continue
                text = response.decode("utf-8", errors="ignore").strip()
                match = DEV_PATTERN.search(text)
                if match:
                    device = DEVICE_MAP.get(match.group(1).lower())
                    if device is not None:
                        return device
                    print(f"  [WARN] {port}: unrecognised DEV code {match.group(1)!r}")
                time.sleep(0.05)
    except Exception as exc:
        print(f"  [WARN] {port}: handshake failed: {exc}")
        return None
    return None


def detect_devices(only_ports=None):
    """Returns {device_name: port} for every board that answers the handshake.

    Only obvious non-hardware placeholders are excluded. COM* names are real
    ports on the lab PC and are kept.
    """
    ports = only_ports if only_ports else [
        p for p in discover_ports() if p not in NON_HARDWARE_PORTS
    ]
    found = {}
    for port in ports:
        print(f"  probing {port} ...", flush=True)
        device = probe_device_at(port)
        if device is None:
            print(f"  {port}: no response")
            continue
        if device not in DEVICES:
            print(f"  {port}: identified as {device!r} (not a flash target, skipping)")
            continue
        print(f"  {port}: identified as {device}")
        found[device] = port
    return found


def check_tools():
    missing = []
    if shutil.which("arduino-cli") is None:
        missing.append(
            "arduino-cli not found on PATH -- install: "
            "https://arduino.github.io/arduino-cli/latest/installation/"
        )
    if any(d["board"] == "teensy" for d in DEVICES.values()) and shutil.which("teensy_loader_cli") is None:
        missing.append(
            "teensy_loader_cli not found on PATH -- install: "
            "https://www.pjrc.com/teensy/loader_cli.html"
        )
    return missing


def ensure_deps(dry_run=False):
    """Install the AVR + Teensy board cores and the third-party libraries the
    sketches include, as far as arduino-cli's index can. Anything the index
    does not carry is named, not silently skipped."""
    cmds = [
        ["arduino-cli", "core", "update-index"],
        ["arduino-cli", "core", "install", "arduino:avr"],
        ["arduino-cli", "core", "install", "teensy:avr", "--additional-urls", TEENSY_URL],
    ]
    for lib in MEGA_LIBS + TEENSY_LIBS:
        cmds.append(["arduino-cli", "lib", "install", lib])

    for cmd in cmds:
        print(f"$ {shlex.join(cmd)}")
        if not dry_run:
            subprocess.run(cmd, check=False)

    for name, note in MANUAL_LIBS.items():
        print(f"[WARN] {name} must be installed by hand -- {note}")


def compile_and_upload(device_name, port, dry_run=False):
    cfg = DEVICES[device_name]
    sketch = cfg["sketch"]

    if cfg["board"] == "mega":
        cmd = [
            "arduino-cli", "compile",
            "--fqbn", MEGA_FQBN,
            "--upload", "-p", port,
            str(sketch),
        ]
        print(f"  $ {shlex.join(cmd)}")
        if dry_run:
            return True
        result = subprocess.run(cmd)
        return result.returncode == 0

    # Teensy: arduino-cli compiles a .hex, teensy_loader_cli does the actual
    # upload (the dedicated tool for this board, rather than arduino-cli's own
    # upload path).
    build_dir = sketch / "build"
    compile_cmd = [
        "arduino-cli", "compile",
        "--fqbn", TEENSY_FQBN,
        "--output-dir", str(build_dir),
        str(sketch),
    ]
    print(f"  $ {shlex.join(compile_cmd)}")
    if not dry_run:
        result = subprocess.run(compile_cmd)
        if result.returncode != 0:
            return False

    hex_path = build_dir / f"{sketch.name}.ino.hex"
    upload_cmd = ["teensy_loader_cli", f"--mcu={TEENSY_MCU}", "-w", "-v", str(hex_path)]
    print(f"  $ {shlex.join(upload_cmd)}")
    if dry_run:
        return True
    result = subprocess.run(upload_cmd)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="Detect connected boards and exit, flash nothing.")
    parser.add_argument("--only", nargs="+", metavar="DEVICE", help="Limit to these device names (e.g. \"Stepper Probe\").")
    parser.add_argument("--port", action="append", default=[], metavar="DEVICE=PORT",
                        help="Manually assign a device to a port, bypassing auto-detect "
                             "(needed for a blank board that doesn't answer the handshake yet). Repeatable.")
    parser.add_argument("--yes", action="store_true", help="Skip the per-board confirmation prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Print the compile/upload commands without running them.")
    parser.add_argument("--install-deps", action="store_true", help="Install/update required arduino-cli cores and libraries, then exit.")
    args = parser.parse_args()

    try:
        validate_sketches()
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if args.install_deps:
        ensure_deps(dry_run=args.dry_run)
        return 0

    if not args.dry_run:
        missing = check_tools()
        if missing:
            for m in missing:
                print(f"[ERROR] {m}", file=sys.stderr)
            return 1

    manual = {}
    for entry in args.port:
        if "=" not in entry:
            print(f"[ERROR] --port expects DEVICE=PORT, got: {entry}", file=sys.stderr)
            return 1
        name, path = entry.split("=", 1)
        if name not in DEVICES:
            print(f"[ERROR] Unknown device {name!r}. Known: {', '.join(DEVICES)}", file=sys.stderr)
            return 1
        manual[name] = path

    print("Scanning for connected boards...")
    found = detect_devices()
    found.update(manual)  # manual assignments win over auto-detect

    if args.only:
        unknown = [d for d in args.only if d not in DEVICES]
        if unknown:
            print(f"[ERROR] Unknown device(s): {', '.join(unknown)}. Known: {', '.join(DEVICES)}", file=sys.stderr)
            return 1
        found = {k: v for k, v in found.items() if k in args.only}

    if not found:
        print("No flashable boards identified. Use --port DEVICE=PORT to assign one manually.")
        return 0

    print("\nIdentified:")
    for name, port in found.items():
        cfg = DEVICES[name]
        board_desc = MEGA_FQBN if cfg["board"] == "mega" else f"{TEENSY_FQBN} (mcu={TEENSY_MCU})"
        print(f"  {name:<24} {port:<20} {board_desc}")

    if args.list:
        return 0

    print()
    print_protocol_banner()
    if "Temperature Controller" in found:
        for lib_name, note in MANUAL_LIBS.items():
            print(f"[WARN] {lib_name} must be installed by hand -- {note}")

    results = {}
    for name, port in found.items():
        print(f"\n{name} on {port}:")
        if not args.yes:
            reply = input(f"  Flash {name} now? This overwrites its running firmware. [y/N] ").strip().lower()
            if reply != "y":
                print("  skipped")
                results[name] = "skipped"
                continue
        ok = compile_and_upload(name, port, dry_run=args.dry_run)
        results[name] = "ok" if ok else "FAILED"

    print("\nSummary:")
    for name, status in results.items():
        print(f"  {name:<24} {status}")

    return 1 if any(s == "FAILED" for s in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
