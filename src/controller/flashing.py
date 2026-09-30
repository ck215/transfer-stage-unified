"""Flashing the boards: the logic that was `firmware/flash_firmware.py`'s,
moved into `src/` so a frozen bundle can run it in-process (owner decision
2026-09-30: "firmware is checked and flashed by the bundle when an update
calls for it"; brief rb-dist-app A1).

What lives here, and nowhere else:

    BOARDS            board -> sketch directory and chip (Mega or Teensy)
    sketch_hash()     the "already current?" hash over a sketch and the
                      vendored libraries it #includes
    load_stamp() / record_flash()
                      the stamp (`~/transfer-stage-runs/flashed.json`, or
                      $STATION_FLASH_STAMP): per board, the hash of the sketch
                      this machine last put on it, the channel it came from
                      (`station` or `stable`) and the bundle's VERSION
    tools_for()       arduino-cli, its data directory and teensy_loader_cli:
                      on PATH in a checkout; `tools/` beside the launchers in
                      a frozen bundle (the bundle layout contract)
    detect()          port -> board, through the identity handshake Setup's
                      scan uses (`controller.setup.PortProbe`); nothing here
                      speaks the protocol
    flash()           compile and upload each board that needs it, record it

Every subprocess goes through one injectable runner,
`run(argv, cwd, on_line, timeout, env=None) -> exit code | None`, so the
tests never start a tool and never open a port. Nothing prints: every line
goes to `on_line` (the CLI hands it `print`, Setup its Flashing cell).
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

MEGA_FQBN = "arduino:avr:mega:cpu=atmega2560"
#: The Temperature Controller runs on a Teensy 3.5.
TEENSY_FQBN = "teensy:avr:teensy35"
TEENSY_MCU = "MK64FX512"
TEENSY_INDEX = "https://www.pjrc.com/teensy/package_teensy_index.json"
MEGA_LIBS = ["AccelStepper", "TMCStepper"]
#: Wire ships with the Teensy core; MAX6675 (Rob Tillaart's, `MAX6675.h`)
#: does NOT - the old comment said it did, and the sketch does not compile
#: without it (agent E, rb-dist-build B2; the bundle pins 0.3.4).
#: LiquidCrystal_I2C is the REGISTRY library (2.0.0 in the bundle), never
#: the NewLiquidCrystal vendored as firmware/libraries/LiquidCrystal_I2C:
#: the sketch uses the registry API (`LiquidCrystal_I2C lcd(0x27, 20, 4)`,
#: `lcd.init()`), and with `--libraries firmware/libraries` the vendored
#: one wins and the compile fails (`init()` is private there; checked
#: against the bundle's arduino-data, 2026-09-30). So no command here ever
#: passes `--libraries`.
TEENSY_LIBS = ["LiquidCrystal_I2C", "MAX6675"]

#: Board -> its sketch directory and chip, in flashing order. A board's name
#: is also its model's NAME, which is what the identity handshake answers.
#: The SMC100 Rotator is a purchased controller, never a flash target.
BOARDS = {
    "Stepper Probe": {"dir": "stepper_firmware", "board": "mega"},
    "DC Probe": {"dir": "high_polling_rate", "board": "mega"},
    "Chuck Positioner": {"dir": "chuck_firmware", "board": "mega"},
    "Temperature Controller": {"dir": "temp_controller", "board": "teensy"},
}

#: The checkout this file lives in: src/controller/flashing.py -> the repo.
REPO_ROOT = Path(__file__).resolve().parents[2]
#: Where "flash only if needed" remembers what went on each board. Outside
#: the install on purpose: it describes this machine's boards, and both
#: apps of a bundle (and both branches' checkouts) share it.
DEFAULT_STAMP = Path.home() / "transfer-stage-runs" / "flashed.json"
STAMP_ENV = "STATION_FLASH_STAMP"
#: Which app's sketches a stamp entry came from (owner decision 3,
#: 2026-09-30: the bundle carries the station and a frozen `stable`).
STATION, STABLE = "station", "stable"
CHANNELS = (STATION, STABLE)

#: A whole flash, compile and upload of four boards, with room for a Teensy
#: waiting on its bootloader. `teensy_loader_cli -w` waits forever when the
#: soft reboot fails, and Launch waits for the flash, so it must end.
FLASH_SECONDS = 900

ARDUINO_CLI, TEENSY_LOADER = "arduino-cli", "teensy_loader_cli"

# -- where things are ---------------------------------------------------------

def is_frozen():
    return bool(getattr(sys, "frozen", False))


def bundle_root():
    """The folder holding the launchers of a frozen bundle, else None
    (`Path(sys.executable).resolve().parent`, the layout contract's root)."""
    return Path(sys.executable).resolve().parent if is_frozen() else None


def install_root():
    """The station's own folder: the bundle root, or this checkout."""
    return bundle_root() or REPO_ROOT


def default_sketch_root():
    """The new app's sketches: `<bundle>/firmware`, or the repo's."""
    return install_root() / "firmware"


def stable_root():
    """`<bundle>/stable` when the frozen stable app sits beside the
    launchers, else None (a checkout has no stable; `dev/swap_branch.sh` is
    its way)."""
    root = bundle_root()
    if root is None:
        return None
    stable = root / "stable"
    return stable if (stable / _exe("station-stable")).is_file() else None


def default_stamp():
    return Path(os.environ.get(STAMP_ENV) or DEFAULT_STAMP).expanduser()


def bundle_version(root=None):
    """The first line of the bundle's VERSION (its tag), or None."""
    root = Path(root) if root is not None else bundle_root()
    if root is None:
        return None
    try:
        first = (root / "VERSION").read_text(encoding="utf-8").splitlines()[:1]
    except (OSError, UnicodeDecodeError):
        return None
    return (first[0].strip() or None) if first else None


def _exe(name):
    return name + ".exe" if os.name == "nt" else name


class Tools:
    """Where arduino-cli, its data and teensy_loader_cli are. `data_dir`
    (frozen only) is the offline data directory with the cores and the
    sketch libraries pre-installed (`packaging/tools.py`: libraries under
    `arduino-data/user/libraries`); every arduino-cli call is pointed at it,
    so a lab machine without network or a user-level arduino-cli setup can
    compile. `home` is the bundle's `tools/`: the bundle's
    `arduino-cli.yaml` holds paths RELATIVE to the working directory
    (arduino-cli's rule), so every tool runs with `cwd=home`, and the same
    directories are also given as absolute ARDUINO_DIRECTORIES_* values."""

    def __init__(self, arduino_cli, teensy_loader, data_dir=None, home=None):
        self.arduino_cli = str(arduino_cli) if arduino_cli else None
        self.teensy_loader = str(teensy_loader) if teensy_loader else None
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.home = Path(home) if home is not None else (
            self.data_dir.parent if self.data_dir is not None else None)

    @property
    def cwd(self):
        """The working directory every tool runs in (None: the caller's)."""
        return str(self.home) if self.home is not None else None

    @property
    def missing(self):
        """The tool names not found, in the old script's order (it asks for
        the Teensy loader whatever boards are named)."""
        return [name for name, path in ((ARDUINO_CLI, self.arduino_cli),
                                        (TEENSY_LOADER, self.teensy_loader))
                if not path]

    @property
    def config_file(self):
        """`tools/arduino-cli.yaml` (the layout contract), when it is there."""
        if self.home is None:
            return None
        found = self.home / "arduino-cli.yaml"
        return found if found.is_file() else None

    @property
    def env(self):
        """The environment every arduino-cli call runs in: None (inherit)
        in a checkout; frozen, the bundle's data directory for the cores,
        the libraries and the downloads, so nothing reaches for ~/.arduino15
        or the network."""
        if self.data_dir is None:
            return None
        env = dict(os.environ)
        env["ARDUINO_DIRECTORIES_DATA"] = str(self.data_dir)
        env["ARDUINO_DIRECTORIES_USER"] = str(self.data_dir / "user")
        env["ARDUINO_DIRECTORIES_DOWNLOADS"] = str(self.data_dir / "staging")
        env["ARDUINO_UPDATER_ENABLE_NOTIFICATION"] = "false"
        return env

    def arduino(self, *args):
        argv = [self.arduino_cli or ARDUINO_CLI]
        if self.config_file is not None:
            argv += ["--config-file", str(self.config_file)]
        return argv + list(args)


def tools_for(root=None, which=None, frozen=None):
    """The tools for this install. Frozen: `<root>/tools/arduino-cli`,
    `<root>/tools/teensy_loader_cli` and `<root>/tools/arduino-data` (the
    bundle layout contract), each only if it is there. A checkout: whatever
    is on PATH, as the old script did."""
    frozen = is_frozen() if frozen is None else frozen
    if frozen:
        root = Path(root) if root is not None else bundle_root()
        tools = root / "tools"
        cli, loader = tools / _exe(ARDUINO_CLI), tools / _exe(TEENSY_LOADER)
        return Tools(cli if cli.is_file() else None,
                     loader if loader.is_file() else None,
                     data_dir=tools / "arduino-data", home=tools)
    which = which or shutil.which
    return Tools(which(ARDUINO_CLI), which(TEENSY_LOADER))


# -- the hash rule ------------------------------------------------------------
_SOURCE_SUFFIXES = {".ino", ".pde", ".h", ".hpp", ".c", ".cpp", ".cc", ".s", ".S"}
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)


def sketch_dir(board, sketch_root):
    return Path(sketch_root) / BOARDS[board]["dir"]


def has_sketch(board, sketch_root):
    directory = BOARDS[board]["dir"]
    return (Path(sketch_root) / directory / f"{directory}.ino").is_file()


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
    sketch #includes: a vendored library is part of the firmware."""
    lib_root = Path(sketch_root) / "libraries"
    if not lib_root.is_dir():
        return []
    headers = set()
    for f in _source_files(sketch):
        headers.update(Path(h).name for h in _INCLUDE_RE.findall(f.read_text(errors="replace")))
    return [lib for lib in sorted(p for p in lib_root.iterdir() if p.is_dir())
            if any((lib / h).is_file() or (lib / "src" / h).is_file() for h in headers)]


def sketch_hash(sketch_root, board):
    """sha256 over the sketch's own sources and its vendored libraries, by
    relative path and content, CRLF read as LF: independent of where the tree
    lives and of its line endings, so the same sketch in two trees (a
    checkout, a bundle, stable/) hashes the same."""
    root = Path(sketch_root)
    sketch = sketch_dir(board, root)
    h = hashlib.sha256()
    parts = [("sketch", sketch)] + [("lib", lib) for lib in _local_libraries(sketch, root)]
    for kind, base in parts:
        for f in _source_files(base):
            rel = f"{kind}/{base.name}/{f.relative_to(base).as_posix()}"
            h.update(rel.encode() + b"\0")
            h.update(f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


# -- the stamp ------------------------------------------------------------------

def load_stamp(path, on_line=None):
    """The stamp as a dict; missing or unreadable is "nothing recorded"."""
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        if on_line is not None:
            on_line(f"[WARN] ignoring unreadable stamp file {path}: {exc}")
        return {}


def record_flash(path, board, digest, sketch, port, channel=STATION, version=None):
    """Record one successful upload. Read-modify-write, then an atomic
    replace, so an interrupted write never leaves a half stamp behind."""
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_stamp(path)
    data[board] = {
        "hash": digest,
        "sketch": str(sketch),
        "port": port,
        "channel": channel,
        "version": version,
        "flashed_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


# -- the commands ------------------------------------------------------------------

def commands(board, port, sketch_root, tools):
    """The argv lists that compile and upload `board` on `port`: one
    arduino-cli compile+upload for a Mega; for the Teensy, a compile to a
    .hex and teensy_loader_cli (-s soft reboot into the bootloader, -w keep
    waiting so a button press also works)."""
    sketch = sketch_dir(board, sketch_root)
    if BOARDS[board]["board"] == "mega":
        return [tools.arduino("compile", "--fqbn", MEGA_FQBN, "--upload",
                              "-p", port, str(sketch))]
    build = sketch / "build"
    return [tools.arduino("compile", "--fqbn", TEENSY_FQBN,
                          "--output-dir", str(build), str(sketch)),
            [tools.teensy_loader or TEENSY_LOADER, f"--mcu={TEENSY_MCU}",
             "-w", "-s", "-v", str(build / f"{sketch.name}.ino.hex")]]


def install_deps_commands(tools):
    """Install the cores and libraries into arduino-cli's data directory
    (a checkout's own, or the bundle's). Needs the network."""
    cmds = [tools.arduino("core", "update-index"),
            tools.arduino("core", "install", "arduino:avr"),
            tools.arduino("core", "install", "teensy:avr",
                          "--additional-urls", TEENSY_INDEX)]
    for lib in MEGA_LIBS + TEENSY_LIBS:
        cmds.append(tools.arduino("lib", "install", lib))
    return cmds


#: What a failing tool's output means for the operator (rb-dist-build's
#: UNVERIFIED list): the tool's own words, matched, -> one sentence saying
#: what to do. Keyed on the output, never on the platform.
HINTS = (
    (re.compile(r"bad CPU type", re.I),
     "The Mega boards' compiler is an Intel program: on an Apple-silicon Mac, "
     "install Rosetta 2 (softwareupdate --install-rosetta --agree-to-license), "
     "then flash again."),
    (re.compile(r"libusb-0\.1|libusb\S*: cannot open shared object", re.I),
     "teensy_loader_cli needs libusb 0.1: install it (on Debian or Ubuntu: "
     "sudo apt install libusb-0.1-4), then flash again."),
    (re.compile(r"(can't open device|could not open port|cannot open port)"
                r".*(permission denied|access is denied)|permission denied.*/dev/tty", re.I),
     "This user may not open the board's port: on Linux add it to the "
     "dialout group (sudo usermod -aG dialout $USER), log out and in, then "
     "flash again."),
    (re.compile(r"unable to open device|error opening usb device", re.I),
     "The Teensy loader cannot open the board: on Linux install the Teensy "
     "udev rules (https://www.pjrc.com/teensy/00-teensy.rules into "
     "/etc/udev/rules.d/), unplug and plug the board in, then flash again."),
)
#: The Teensy loader waited for a bootloader that never came.
TEENSY_WAIT_HINT = ("The Teensy never reached its bootloader: press the button "
                    "on the board while it waits, or, on Linux, install the "
                    "Teensy udev rules (https://www.pjrc.com/teensy/00-teensy.rules "
                    "into /etc/udev/rules.d/), then flash again.")


def hints_for(lines):
    """The operator sentences a failing tool's output calls for, in order,
    each once."""
    found = []
    for pattern, sentence in HINTS:
        if sentence not in found and any(pattern.search(l) for l in lines):
            found.append(sentence)
    return found


def stream_lines(argv, cwd, on_line, timeout, env=None):
    """The default runner: start `argv`, hand each output line (stdout and
    stderr, merged) to `on_line` as it arrives, return the exit code - or
    None when `timeout` seconds ran out and the process was killed. No
    stdin: a prompt the tool should never reach reads end-of-file."""
    env = dict(env if env is not None else os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1, env=env)
    expired = threading.Event()

    def kill():
        expired.set()
        proc.kill()

    timer = threading.Timer(timeout, kill)
    timer.daemon = True
    timer.start()
    try:
        for line in proc.stdout:
            on_line(line.rstrip("\r\n"))
        code = proc.wait()
    finally:
        timer.cancel()
        proc.stdout.close()
    return None if expired.is_set() else code


# -- detection -------------------------------------------------------------------

def default_probe():
    """Setup's own port listing and handshake, without a Setup panel.
    Imported here, not at the top: `controller.setup` imports this module."""
    from controller.setup import PortProbe
    return PortProbe()


def detect(ports, identify, on_line=None, targets=None):
    """{board: port} for every port whose identity handshake names a flash
    target. `identify(port) -> model name or None` is Setup's handshake;
    `ports` are whatever Setup's listing offers (COM ports included: the old
    script skipped them, so Flash now found nothing on the lab PC, OP-12)."""
    say = on_line or (lambda line: None)
    found = {}
    for port in ports:
        if port in ("SIM", "On", "Headless"):
            continue
        try:
            device = identify(port)
        except Exception as exc:        # a broken port is one line, not a crash
            say(f"  probing {port} ... failed ({exc})")
            continue
        if device is None:
            say(f"  probing {port} ... no response")
            continue
        if device not in BOARDS:
            say(f"  probing {port} ... identified as {device!r} (not a flash target, skipping)")
            continue
        if targets is not None and device not in targets:
            say(f"  probing {port} ... identified as {device} (not asked for)")
            continue
        say(f"  probing {port} ... identified as {device}")
        found.setdefault(device, port)
    return found


# -- the flash -------------------------------------------------------------------

def flash(boards=None, *, sketch_root, stamp, tools, run=None, identify=None,
          ports=None, manual=None, force=False, dry_run=False, detect_ports=True,
          confirm=None, channel=STATION, version=None, on_line=None,
          timeout=FLASH_SECONDS, cwd=None):
    """Flash `boards` (default: all four) from `sketch_root` - only the ones
    whose recorded hash differs from the sketch's, unless `force` - on the
    ports the handshake finds them on (`manual` {board: port} wins), and
    record each upload that succeeded in `stamp` with `channel` and
    `version`. `confirm(board, port) -> bool` asks per board (None: yes).

    -> {"returncode": 0 | 1, "results": {board: status}, "found": {board:
    port}, "absent": [boards]}. Returns 1 when a sketch or a tool is
    missing or any upload failed; a board that is not plugged in is said,
    never a failure."""
    say = on_line or (lambda line: None)
    runner = run or stream_lines
    sketch_root = Path(sketch_root)
    manual = dict(manual or {})
    answer = {"returncode": 1, "results": {}, "found": {}, "absent": [], "hints": []}
    targets = list(BOARDS) if boards is None else list(boards)
    unknown = [b for b in targets + list(manual) if b not in BOARDS]
    if not targets or unknown:
        say(f"[ERROR] Unknown device(s): {', '.join(unknown) or 'none named'}. "
            f"Known: {', '.join(BOARDS)}")
        return answer
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")

    missing = [f"{b}: {sketch_dir(b, sketch_root) / (BOARDS[b]['dir'] + '.ino')}"
               for b in BOARDS if not has_sketch(b, sketch_root)]
    if missing:
        say(f"[ERROR] sketches missing under {sketch_root}:")
        for line in missing:
            say(f"  {line}")
        return answer
    if not dry_run and tools.missing:
        for name in tools.missing:
            say(f"[ERROR] {name} not found" + ("" if tools.data_dir is not None
                                               else " on PATH"))
        return answer

    say(f"Sketches: {sketch_root}")
    digests = {b: sketch_hash(sketch_root, b) for b in targets}
    recorded = load_stamp(stamp, say)
    current = set()
    for board in targets:
        theirs = (recorded.get(board) or {}).get("hash") if isinstance(
            recorded.get(board), dict) else None
        if theirs == digests[board] and not force:
            current.add(board)
            say(f"  {board:<24} already current ({digests[board][:12]}), skipping")
    needed = [b for b in targets if b not in current]
    if not needed:
        say("Every board is already running these sketches; nothing to flash.")
        answer["returncode"] = 0
        return answer
    say(f"Needs flashing unless absent: {', '.join(needed)}")

    found = {}
    if detect_ports:
        say("Scanning for connected boards...")
        if identify is None or ports is None:
            probe = default_probe()
            identify = identify or probe.identify
            ports = probe.scan_ports() if ports is None else ports
        found = detect(list(ports() if callable(ports) else ports), identify,
                       say, targets=needed)
    found.update(manual)
    found = {b: p for b, p in found.items() if b in needed}
    answer["found"] = dict(found)
    absent = [b for b in needed if b not in found]
    answer["absent"] = absent
    if absent:
        say(f"Not connected (nothing flashed, nothing recorded): {', '.join(absent)}")
    if not found:
        say("No flashable boards identified. Use --port DEVICE=PORT to assign one manually.")
        answer["returncode"] = 0
        return answer

    say("")
    say("Identified:")
    for board, port in found.items():
        chip = (MEGA_FQBN if BOARDS[board]["board"] == "mega"
                else f"{TEENSY_FQBN} (mcu={TEENSY_MCU})")
        say(f"  {board:<24} {port:<20} {chip}")

    deadline = time.monotonic() + timeout
    results = answer["results"]
    for board in needed:
        if board not in found:
            continue
        port = found[board]
        say("")
        say(f"{board} on {port}:")
        if confirm is not None and not confirm(board, port):
            say("  skipped")
            results[board] = "skipped"
            continue
        ok = True
        for argv in commands(board, port, sketch_root, tools):
            output = []

            def said(line, output=output):
                output.append(line)
                say(line)

            say("  $ " + " ".join(argv))
            if dry_run:
                continue
            left = deadline - time.monotonic()
            if left <= 0:
                say(f"The flash took longer than {timeout:g} s and was stopped.")
                ok = False
                break
            try:
                code = runner(argv, tools.cwd or str(cwd or sketch_root), said,
                              left, env=tools.env)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                said(f"The flash tool could not start: {exc}")
                code = -1
            if code is None:
                say(f"The flash took longer than {timeout:g} s and was stopped.")
            if code != 0:
                hints = hints_for(output)
                if code is None and argv[0] == (tools.teensy_loader or TEENSY_LOADER):
                    hints.append(TEENSY_WAIT_HINT)
                for hint in hints:
                    if hint not in answer["hints"]:
                        answer["hints"].append(hint)
                    say(f"[HINT] {hint}")
                ok = False
                break
        if ok and not dry_run:
            # Recorded only after the upload reported success.
            record_flash(stamp, board, digests[board], sketch_dir(board, sketch_root),
                         port, channel=channel, version=version)
        results[board] = ("ok (dry run)" if dry_run else "ok") if ok else "FAILED"

    say("")
    say("Summary:")
    for board, status in results.items():
        say(f"  {board:<24} {status}")
    answer["returncode"] = 1 if any(s == "FAILED" for s in results.values()) else 0
    return answer

