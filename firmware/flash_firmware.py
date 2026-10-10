#!/usr/bin/env python3
"""Auto-detect connected stage-control boards and flash each one with its
current firmware.

A thin command line over `src/controller/flashing.py`, where the logic
lives (brief rb-dist-app A1): the board table, the "flash only if needed"
hash, the stamp, arduino-cli / teensy_loader_cli, and detection through
the SAME identity handshake Setup's scan uses (`controller.setup.PortProbe`),
so a port only ever gets identified one way in this codebase. A board must
already run *some* firmware that answers the handshake to be found; a blank
chip needs --port to name it for its first flash. COM ports are probed like
any other (the old copy skipped them).

Board split:
    Stepper Probe, DC Probe, Chuck Positioner  -> Mega2560, via arduino-cli
    Temperature Controller                     -> Teensy, compiled with
                                                   arduino-cli's Teensy core,
                                                   rebooted through its own
                                                   port, uploaded with
                                                   teensy_loader_cli (no -s)
    XYZ Stage                                  -> three Teensy boards, one
                                                   per axis (DEV: x X|Y|Z):
                                                   all three, each on its own
                                                   port, or none

The SMC100 Rotator is a purchased Newport motion controller, not one of
our own firmwares -- it is identified by the handshake but never a flash
target here.

Usage:
    python flash_firmware.py                        # detect, confirm, flash all
    python flash_firmware.py --list                  # detect only, flash nothing
    python flash_firmware.py --only "Stepper Probe" "Chuck Positioner"
    python flash_firmware.py --port "Temperature Controller=/dev/ttyACM0"
    python flash_firmware.py --yes                    # skip per-board confirmation
    python flash_firmware.py --dry-run                # print commands, run nothing
    python flash_firmware.py --install-deps           # install missing arduino-cli
                                                        # cores/libraries, then exit
    python flash_firmware.py --sketch-root ../transfer-stage-unified-main/firmware
                                                      # flash another tree's sketches
    python flash_firmware.py --force                  # flash even if already current
    python flash_firmware.py --no-detect --port "Stepper Probe=/dev/ttyACM2"
                                                      # open no port but the named ones

Flash only if needed: after a board's upload succeeds, a content hash of the
sketch it got (its .ino and any other source in the sketch directory, plus
any library under <sketch-root>/libraries that the sketch #includes) is
recorded in a stamp file outside the repo, by default
~/transfer-stage-runs/flashed.json (override: --stamp or
$STATION_FLASH_STAMP), with the channel it came from (--channel, `station`
by default; `stable` for the lab's original app's sketches) and the bundle's
VERSION when there is one. A board whose recorded hash equals the hash of
the sketch about to be flashed is skipped; --force flashes it anyway. When
every target board is already current, no port is opened at all. A dry run
records nothing.

Requires on PATH (a packaged bundle carries its own under tools/):
    arduino-cli        https://arduino.github.io/arduino-cli/latest/installation/
    teensy_loader_cli   https://www.pjrc.com/teensy/loader_cli.html
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from controller import flashing  # noqa: E402

DEFAULT_SKETCH_ROOT = REPO_ROOT / "firmware"
DEFAULT_STAMP = flashing.DEFAULT_STAMP

# The board table as LITERALS: packaging/layout.py and packaging/tools.py
# (agent E's bundle build) read these with `ast`, without importing this
# file, to know which sketch directories to ship and which cores and
# libraries to pin. `controller.flashing` is the table the code uses;
# tests/test_flashing.py pins the two equal.
MEGA_FQBN = "arduino:avr:mega:cpu=atmega2560"
TEENSY_FQBN = "teensy:avr:teensy35"
TEENSY_MCU = "MK64FX512"
MEGA_LIBS = ["AccelStepper", "TMCStepper"]
TEENSY_LIBS = ["LiquidCrystal_I2C", "MAX6675"]  # MAX6675 does NOT ship with the Teensy core
DEVICES = {
    "Stepper Probe": {"dir": "stepper_firmware", "board": "mega"},
    "DC Probe": {"dir": "high_polling_rate", "board": "mega"},
    "Chuck Positioner": {"dir": "chuck_firmware", "board": "mega"},
    "Temperature Controller": {"dir": "temp_controller", "board": "teensy"},
    "XYZ Stage": {"dir": "xyz_stage_axis", "board": "teensy", "tags": ["X", "Y", "Z"]},
    "XYZ Stage (Mega)": {"dir": "xyz_stage_mega", "board": "mega"},
}


def sketch_hash(device_name, sketch_root=None):
    """The "already current?" hash (`controller.flashing.sketch_hash`)."""
    return flashing.sketch_hash(sketch_root or DEFAULT_SKETCH_ROOT, device_name)


def _run(argv, cwd, on_line, timeout, env=None):
    """This terminal's runner: the tool's own output, straight through."""
    try:
        return subprocess.run(argv, cwd=cwd, env=env, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="Detect connected boards and exit, flash nothing.")
    parser.add_argument("--only", nargs="+", metavar="DEVICE", help="Limit to these device names (e.g. \"Stepper Probe\").")
    parser.add_argument("--port", action="append", default=[], metavar="DEVICE=PORT",
                        help="Manually assign a device to a port, bypassing auto-detect "
                             "(needed for a blank board that doesn't answer the handshake yet). Repeatable.")
    parser.add_argument("--yes", action="store_true", help="Skip the per-board confirmation prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Print the compile/upload commands without running them.")
    parser.add_argument("--install-deps", action="store_true", help="Install/update required arduino-cli cores and libraries, then exit.")
    parser.add_argument("--sketch-root", metavar="PATH", default=str(DEFAULT_SKETCH_ROOT),
                        help="Directory holding the sketch folders (stepper_firmware/, ...). "
                             "Default: this repo's firmware/. Point it at another checkout's "
                             "firmware/ to flash that tree's sketches with this tool.")
    parser.add_argument("--force", action="store_true",
                        help="Flash even boards whose recorded sketch hash matches.")
    parser.add_argument("--stamp", metavar="PATH",
                        default=os.environ.get(flashing.STAMP_ENV, str(DEFAULT_STAMP)),
                        help="Where to record what was flashed (default: %(default)s).")
    parser.add_argument("--no-detect", action="store_true",
                        help="Skip auto-detect: open no serial port; flash only --port assignments.")
    parser.add_argument("--channel", choices=flashing.CHANNELS, default=flashing.STATION,
                        help="Which app's sketches these are, for the stamp (default: %(default)s).")
    args = parser.parse_args(argv)

    tools = flashing.tools_for()
    if args.install_deps:
        for cmd in flashing.install_deps_commands(tools):
            print(f"$ {' '.join(cmd)}")
            if not args.dry_run:
                subprocess.run(cmd, check=False, env=tools.env)
        return 0

    manual = {}
    for entry in args.port:
        if "=" not in entry:
            print(f"[ERROR] --port expects DEVICE=PORT, got: {entry}", file=sys.stderr)
            return 1
        name, path = entry.split("=", 1)
        if name not in flashing.BOARDS:
            print(f"[ERROR] Unknown device {name!r}. Known: {', '.join(flashing.BOARDS)}", file=sys.stderr)
            return 1
        manual[name] = path
    if args.only:
        unknown = [d for d in args.only if d not in flashing.BOARDS]
        if unknown:
            print(f"[ERROR] Unknown device(s): {', '.join(unknown)}. Known: {', '.join(flashing.BOARDS)}", file=sys.stderr)
            return 1
    sketch_root = Path(args.sketch_root).expanduser().resolve()

    if args.list:
        # Detect only: no stamp, no tools.
        if args.no_detect:
            found = {}
        else:
            print("Scanning for connected boards...")
            probe = flashing.default_probe()
            found = flashing.detect(probe.scan_ports(), probe.identify, print,
                                    targets=args.only)
        found.update({k: v for k, v in manual.items() if not args.only or k in args.only})
        if not found:
            print("No flashable boards identified. Use --port DEVICE=PORT to assign one manually.")
            return 0
        print("\nIdentified:")
        for name, port in found.items():
            print(f"  {name:<24} {port}")
        return 0

    def confirm(board, port):
        reply = input(f"  Flash {board} now? This overwrites its running firmware. [y/N] ")
        return reply.strip().lower() == "y"

    answer = flashing.flash(
        args.only, sketch_root=sketch_root, stamp=args.stamp, tools=tools,
        run=_run, manual=manual, force=args.force, dry_run=args.dry_run,
        detect_ports=not args.no_detect, confirm=None if args.yes else confirm,
        channel=args.channel, version=flashing.bundle_version(), on_line=print)
    return answer["returncode"]


if __name__ == "__main__":
    sys.exit(main())
