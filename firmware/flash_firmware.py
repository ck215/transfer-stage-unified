#!/usr/bin/env python3
"""Auto-detect connected stage-control boards and flash each one with its
current firmware.

Detection reuses app_bootstrap's own serial auto-detect + "DEV:" handshake
(the same code the running app uses at setup time), so a port only ever
gets identified one way in this codebase. That means a board must already
be running *some* firmware that answers the handshake to be auto-identified
-- a truly blank/never-flashed chip won't respond, and needs --port to
name it manually for its first flash.

Board split:
    Stepper Probe, DC Probe, Chuck Positioner  -> Mega2560, via arduino-cli
    Temperature Controller                     -> Teensy, compiled with
                                                   arduino-cli's Teensy core,
                                                   uploaded with the
                                                   dedicated teensy_loader_cli

The SMC100 Rotator is a purchased Newport motion controller, not one of
our own firmwares -- it's identified by probe_device_at() but never a
flash target here.

Usage:
    python flash_firmware.py                        # detect, confirm, flash all
    python flash_firmware.py --list                  # detect only, flash nothing
    python flash_firmware.py --only "Stepper Probe" "Chuck Positioner"
    python flash_firmware.py --port "Temperature Controller=/dev/ttyACM0"
    python flash_firmware.py --yes                    # skip per-board confirmation
    python flash_firmware.py --dry-run                # print commands, run nothing
    python flash_firmware.py --install-deps           # install missing arduino-cli
                                                        # cores/libraries, then exit

Requires on PATH:
    arduino-cli        https://arduino.github.io/arduino-cli/latest/installation/
    teensy_loader_cli   https://www.pjrc.com/teensy/loader_cli.html
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# The detection helpers still live in the old repair tree, kept under
# legacy/ until the porting wave finishes. Porting this tool onto
# src/controller/setup.py is BUGFIX_PLAN.md D-12.
sys.path.insert(0, str(REPO_ROOT / "legacy" / "src"))

from app_bootstrap import discover_ports, probe_device_at  # noqa: E402

MEGA_FQBN = "arduino:avr:mega:cpu=atmega2560"

# Temperature Controller runs on a Teensy 3.5.
TEENSY_FQBN = "teensy:avr:teensy35"
TEENSY_MCU = "MK64FX512"

MEGA_LIBS = ["AccelStepper", "TMCStepper"]
TEENSY_LIBS = ["LiquidCrystal_I2C"]  # Wire and MAX6675 ship with the Teensy core

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
    """Install the Teensy board core (not present in arduino-cli's default
    index) and the third-party libraries each sketch depends on."""
    cmds = [
        ["arduino-cli", "core", "update-index"],
        ["arduino-cli", "core", "install", "arduino:avr"],
        [
            "arduino-cli", "core", "install", "teensy:avr",
            "--additional-urls", "https://www.pjrc.com/teensy/package_teensy_index.json",
        ],
    ]
    for lib in MEGA_LIBS + TEENSY_LIBS:
        cmds.append(["arduino-cli", "lib", "install", lib])

    for cmd in cmds:
        print(f"$ {' '.join(cmd)}")
        if not dry_run:
            subprocess.run(cmd, check=False)


def detect_devices(only_ports=None):
    """Returns {device_name: port} for every board that answers the
    handshake. Skips the SMC100 Rotator (not a flash target)."""
    ports = only_ports if only_ports else [p for p in discover_ports() if p not in ("Headless",) and not p.startswith("COM")]
    found = {}
    for port in ports:
        print(f"  probing {port} ...", end=" ", flush=True)
        device = probe_device_at(port)
        if device is None:
            print("no response")
            continue
        if device not in DEVICES:
            print(f"identified as {device!r} (not a flash target, skipping)")
            continue
        print(f"identified as {device}")
        found[device] = port
    return found


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
        print(f"  $ {' '.join(cmd)}")
        if dry_run:
            return True
        result = subprocess.run(cmd)
        return result.returncode == 0

    # Teensy: arduino-cli compiles a .hex, teensy_loader_cli does the
    # actual upload (the dedicated tool the user wants for this board
    # specifically, rather than arduino-cli's own upload path).
    build_dir = sketch / "build"
    compile_cmd = [
        "arduino-cli", "compile",
        "--fqbn", TEENSY_FQBN,
        "--output-dir", str(build_dir),
        str(sketch),
    ]
    print(f"  $ {' '.join(compile_cmd)}")
    if not dry_run:
        result = subprocess.run(compile_cmd)
        if result.returncode != 0:
            return False

    hex_path = build_dir / f"{sketch.name}.ino.hex"
    upload_cmd = ["teensy_loader_cli", f"--mcu={TEENSY_MCU}", "-w", "-v", str(hex_path)]
    print(f"  $ {' '.join(upload_cmd)}")
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
