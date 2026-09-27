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
$STATION_FLASH_STAMP). A board whose recorded hash equals the hash of the
sketch about to be flashed is skipped; --force flashes it anyway. When every
target board is already current, no port is opened at all. The stamp is this
machine's record of what *this tool* put on the boards: a board flashed from
elsewhere (the Arduino IDE, another computer) is not seen, so use --force
after doing that. A dry run records nothing.

Requires on PATH:
    arduino-cli        https://arduino.github.io/arduino-cli/latest/installation/
    teensy_loader_cli   https://www.pjrc.com/teensy/loader_cli.html
"""
import argparse
import datetime
import hashlib
import json
import os
import re
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

DEFAULT_SKETCH_ROOT = REPO_ROOT / "firmware"

# Where "flash only if needed" remembers what went on each board. Outside the
# repo on purpose: it describes this machine's boards, not a tree, and both
# branches' checkouts share it.
DEFAULT_STAMP = Path.home() / "transfer-stage-runs" / "flashed.json"

# Each device's sketch is <sketch-root>/<dir>/<dir>.ino. "sketch" is resolved
# against the default root; sketch_dir() resolves against any other.
DEVICES = {
    "Stepper Probe": {"dir": "stepper_firmware", "board": "mega"},
    "DC Probe": {"dir": "high_polling_rate", "board": "mega"},
    "Chuck Positioner": {"dir": "chuck_firmware", "board": "mega"},
    "Temperature Controller": {"dir": "temp_controller", "board": "teensy"},
}
for _cfg in DEVICES.values():
    _cfg["sketch"] = DEFAULT_SKETCH_ROOT / _cfg["dir"]

# Files that make up a sketch's source for the "already flashed?" hash.
_SOURCE_SUFFIXES = {".ino", ".pde", ".h", ".hpp", ".c", ".cpp", ".cc", ".s", ".S"}
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)


def sketch_dir(device_name, sketch_root=None):
    root = Path(sketch_root) if sketch_root else DEFAULT_SKETCH_ROOT
    return root / DEVICES[device_name]["dir"]


def _source_files(directory):
    """Every source file under `directory`, sorted, skipping build output."""
    out = []
    for path in sorted(Path(directory).rglob("*")):
        rel = path.relative_to(directory)
        if not path.is_file() or path.suffix not in _SOURCE_SUFFIXES:
            continue
        if any(part in ("build", ".git") for part in rel.parts[:-1]):
            continue
        out.append(path)
    return out


def _local_libraries(sketch, sketch_root):
    """Library directories under <sketch-root>/libraries whose header the
    sketch #includes. Hashed with the sketch so a change to a vendored
    library counts as a change to the firmware."""
    lib_root = Path(sketch_root) / "libraries"
    if not lib_root.is_dir():
        return []
    headers = set()
    for f in _source_files(sketch):
        headers.update(Path(h).name for h in _INCLUDE_RE.findall(f.read_text(errors="replace")))
    libs = []
    for lib in sorted(p for p in lib_root.iterdir() if p.is_dir()):
        if any((lib / h).is_file() or (lib / "src" / h).is_file() for h in headers):
            libs.append(lib)
    return libs


def sketch_hash(device_name, sketch_root=None):
    """sha256 over the sketch's own sources and its vendored libraries, by
    relative path and content. Independent of where the tree lives and of
    its line endings, so the same sketch in two checkouts hashes the same."""
    root = Path(sketch_root) if sketch_root else DEFAULT_SKETCH_ROOT
    sketch = sketch_dir(device_name, root)
    h = hashlib.sha256()
    parts = [("sketch", sketch)] + [("lib", lib) for lib in _local_libraries(sketch, root)]
    for kind, base in parts:
        for f in _source_files(base):
            rel = f"{kind}/{base.name}/{f.relative_to(base).as_posix()}"
            h.update(rel.encode() + b"\0")
            # CRLF -> LF: the same commit can check out with either line
            # ending (git's text=auto), and the compiler does not care.
            h.update(f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def load_stamp(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print(f"[WARN] ignoring unreadable stamp file {path}: {exc}")
        return {}


def record_flash(path, device_name, digest, sketch, port):
    """Record one successful upload. Read-modify-write, then an atomic
    replace, so an interrupted write never leaves a half stamp behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_stamp(path)
    data[device_name] = {
        "hash": digest,
        "sketch": str(sketch),
        "port": port,
        "flashed_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


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


def compile_and_upload(device_name, port, dry_run=False, sketch_root=None):
    cfg = DEVICES[device_name]
    sketch = sketch_dir(device_name, sketch_root)

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
    # -s: soft reboot. teensy_loader_cli asks the running sketch to jump into
    # the HalfKay bootloader over USB, so no one has to press the button.
    # That only works while a sketch with USB Serial is running and nothing
    # holds its serial port open (close the app, and any serial monitor,
    # first). A blank or crashed Teensy still needs the button. -w keeps
    # waiting for the bootloader to appear, so a button press also works.
    upload_cmd = ["teensy_loader_cli", f"--mcu={TEENSY_MCU}", "-w", "-s", "-v", str(hex_path)]
    print(f"  $ {' '.join(upload_cmd)}")
    if dry_run:
        return True
    result = subprocess.run(upload_cmd)
    return result.returncode == 0


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
                        default=os.environ.get("STATION_FLASH_STAMP", str(DEFAULT_STAMP)),
                        help="Where to record what was flashed (default: %(default)s).")
    parser.add_argument("--no-detect", action="store_true",
                        help="Skip auto-detect: open no serial port; flash only --port assignments.")
    args = parser.parse_args(argv)

    if args.install_deps:
        ensure_deps(dry_run=args.dry_run)
        return 0

    sketch_root = Path(args.sketch_root).expanduser().resolve()
    missing_sketches = [
        f"{name}: {sketch_dir(name, sketch_root) / (cfg['dir'] + '.ino')}"
        for name, cfg in DEVICES.items()
        if not (sketch_dir(name, sketch_root) / f"{cfg['dir']}.ino").is_file()
    ]
    if missing_sketches:
        print(f"[ERROR] sketches missing under --sketch-root {sketch_root}:\n  "
              + "\n  ".join(missing_sketches), file=sys.stderr)
        return 1

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

    if args.only:
        unknown = [d for d in args.only if d not in DEVICES]
        if unknown:
            print(f"[ERROR] Unknown device(s): {', '.join(unknown)}. Known: {', '.join(DEVICES)}", file=sys.stderr)
            return 1
    targets = list(args.only) if args.only else list(DEVICES)

    print(f"Sketches: {sketch_root}")
    digests = {name: sketch_hash(name, sketch_root) for name in targets}

    # Flash only if needed. --list is a detect-only query, so it skips this.
    current = set()
    if not args.list:
        stamp = load_stamp(args.stamp)
        for name in targets:
            recorded = (stamp.get(name) or {}).get("hash")
            if recorded == digests[name] and not args.force:
                current.add(name)
                print(f"  {name:<24} already current ({digests[name][:12]}), skipping")
        needed = [n for n in targets if n not in current]
        if not needed:
            print("Every board is already running these sketches; nothing to flash.")
            return 0
        print(f"Needs flashing unless absent: {', '.join(needed)}")

    if args.no_detect:
        found = {}
    else:
        print("Scanning for connected boards...")
        found = detect_devices()
    found.update(manual)  # manual assignments win over auto-detect

    found = {k: v for k, v in found.items() if k in targets}
    if not args.list:
        absent = [n for n in needed if n not in found]
        if absent:
            print(f"Not connected (nothing flashed, nothing recorded): {', '.join(absent)}")

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
        if name in current:
            results[name] = "current"
            continue
        print(f"\n{name} on {port}:")
        if not args.yes:
            reply = input(f"  Flash {name} now? This overwrites its running firmware. [y/N] ").strip().lower()
            if reply != "y":
                print("  skipped")
                results[name] = "skipped"
                continue
        ok = compile_and_upload(name, port, dry_run=args.dry_run, sketch_root=sketch_root)
        if ok and not args.dry_run:
            # Recorded only after the upload reported success.
            record_flash(args.stamp, name, digests[name], sketch_dir(name, sketch_root), port)
        results[name] = ("ok (dry run)" if args.dry_run else "ok") if ok else "FAILED"

    print("\nSummary:")
    for name, status in results.items():
        print(f"  {name:<24} {status}")

    return 1 if any(s == "FAILED" for s in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
