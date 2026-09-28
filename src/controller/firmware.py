"""The firmware check, moved out of the launcher onto the Setup page (owner,
2026-09-28: "retire any system that doesn't use the SWAP check. Push any
setup left in the terminal to GUI indicators").

`FirmwareCheck` answers the question `run_swap.sh` (now `dev/swap_branch.sh`) asked before every
launch - is each board running the sketch this checkout carries? - without
arduino-cli and without opening a port:

    check()   each board's status from the stamp file and the sketch hashes,
              exactly as `firmware/flash_firmware.py` decides "already
              current": the stamp (`~/transfer-stage-runs/flashed.json`, or
              $STATION_FLASH_STAMP) records the hash of the sketch this
              machine last put on each board; a board whose recorded hash
              differs is out of date, one with no record was never flashed
              from here.
    flash()   runs `firmware/flash_firmware.py --yes --only <boards>` as a
              subprocess, streaming its lines to a callback. Only ever
              because the operator pressed the key; nothing here flashes on
              its own.

Nothing is imported from `firmware/` (nothing under `src/` may import
outside it). The hash rule is copied from the script and pinned against
the script's own function by `tests/test_firmware_check.py`, so an edit to
one without the other fails the suite instead of drifting silently.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

#: The checkout this file lives in: src/controller/firmware.py -> the repo.
ROOT = Path(__file__).resolve().parents[2]
#: The flashing tool. Run, never imported.
SCRIPT = ROOT / "firmware" / "flash_firmware.py"
#: The script's own default; both branches' checkouts share it.
DEFAULT_STAMP = Path.home() / "transfer-stage-runs" / "flashed.json"
STAMP_ENV = "STATION_FLASH_STAMP"

#: Board -> its sketch directory under the sketch root, in the script's
#: order (`flash_firmware.DEVICES`). A board's name is also its model's NAME.
BOARDS = {
    "Stepper Probe": "stepper_firmware",
    "DC Probe": "high_polling_rate",
    "Chuck Positioner": "chuck_firmware",
    "Temperature Controller": "temp_controller",
}
#: What the script refuses to start without (`flash_firmware.check_tools`:
#: it asks for the Teensy loader whatever boards are named).
TOOLS = ("arduino-cli", "teensy_loader_cli")

#: A whole flash, compile and upload of four boards, with room for a Teensy
#: waiting on its bootloader. `teensy_loader_cli -w` waits forever when the
#: soft reboot fails, and Launch waits for the flash, so it must end.
FLASH_SECONDS = 900

CURRENT, OUT_OF_DATE, NEVER, NO_SKETCH = "current", "out_of_date", "never_flashed", "no_sketch"

# -- the hash rule: flash_firmware.py's, verbatim in behaviour -----------------
_SOURCE_SUFFIXES = {".ino", ".pde", ".h", ".hpp", ".c", ".cpp", ".cc", ".s", ".S"}
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)


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
    """sha256 over the sketch's sources and its vendored libraries, by
    relative path and content, CRLF read as LF."""
    root = Path(sketch_root)
    sketch = root / BOARDS[board]
    h = hashlib.sha256()
    parts = [("sketch", sketch)] + [("lib", lib) for lib in _local_libraries(sketch, root)]
    for kind, base in parts:
        for f in _source_files(base):
            rel = f"{kind}/{base.name}/{f.relative_to(base).as_posix()}"
            h.update(rel.encode() + b"\0")
            h.update(f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def _names(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def stream_lines(argv, cwd, on_line, timeout):
    """The default runner: start `argv`, hand each output line (stdout and
    stderr, merged) to `on_line` as it arrives, return the exit code - or
    None when `timeout` seconds ran out and the process was killed. No
    stdin: a prompt the script should never reach reads end-of-file."""
    env = dict(os.environ, PYTHONUNBUFFERED="1")
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


class FirmwareCheck:
    """`check()` and `flash()` over one sketch tree and one stamp file.
    `run=` stands in for `stream_lines` and `which=` for `shutil.which`, so
    the tests never compile, upload or look at the bench's PATH."""

    def __init__(self, root=None, *, sketch_root=None, stamp=None, run=None,
                 which=None, python=None):
        self.root = Path(root) if root is not None else ROOT
        self.sketch_root = Path(sketch_root) if sketch_root is not None else self.root / "firmware"
        self.script = self.root / "firmware" / "flash_firmware.py"
        self.stamp = Path(stamp) if stamp is not None else Path(
            os.environ.get(STAMP_ENV) or DEFAULT_STAMP).expanduser()
        self._run = run or stream_lines
        self._which = which or shutil.which
        self._python = python or sys.executable

    def sketch_hash(self, board):
        return sketch_hash(self.sketch_root, board)

    def _has_sketch(self, board):
        directory = BOARDS[board]
        return (self.sketch_root / directory / f"{directory}.ino").is_file()

    def _recorded(self):
        """The stamp as a dict; missing or unreadable is "nothing recorded",
        as the script reads it."""
        try:
            data = json.loads(self.stamp.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def check(self):
        """-> {"boards": {board: status}, "stale", "never", "to_flash",
        "missing_tools", "summary"}. `stale` has a record that differs;
        `to_flash` is what the script would flash (stale and never
        flashed), in the script's order. Reads files only."""
        recorded = self._recorded()
        boards = {}
        for board in BOARDS:
            if not self._has_sketch(board):
                boards[board] = NO_SKETCH
                continue
            entry = recorded.get(board)
            theirs = entry.get("hash") if isinstance(entry, dict) else None
            if theirs is None:
                boards[board] = NEVER
            else:
                boards[board] = CURRENT if theirs == self.sketch_hash(board) else OUT_OF_DATE
        stale = [b for b, s in boards.items() if s == OUT_OF_DATE]
        never = [b for b, s in boards.items() if s == NEVER]
        missing = [tool for tool in TOOLS if not self._which(tool)]
        return {"boards": boards, "stale": stale, "never": never,
                "to_flash": stale + never, "missing_tools": missing,
                "summary": self._summary(boards, stale, never, missing)}

    @staticmethod
    def _summary(boards, stale, never, missing):
        if all(status == NO_SKETCH for status in boards.values()):
            return "firmware sketches not found: flash by hand"
        parts = []
        if stale:
            parts.append(f"{_names(stale)} out of date")
        if never:
            present = [b for b, s in boards.items() if s != NO_SKETCH]
            parts.append("never flashed here" if never == present
                         else f"{_names(never)} never flashed here")
        if not parts:
            return "all current"
        if missing:
            parts.append(f"{_names(missing)} not found: flash by hand")
        return "; ".join(parts)

    def command(self, boards):
        """The one command line: `--yes` because the operator already
        confirmed on the Setup page, never `--force`."""
        return [self._python, "-u", str(self.script), "--yes",
                "--sketch-root", str(self.sketch_root), "--stamp", str(self.stamp),
                "--only", *boards]

    def flash(self, boards, on_line=None, timeout=FLASH_SECONDS):
        """Flash `boards` with the script. -> {"ok", "returncode", "last",
        "lines"}; `last` is the last non-empty line, what the Flashing cell
        showed at the end. A runner that raises is a failed flash."""
        boards = list(boards)
        unknown = [b for b in boards if b not in BOARDS]
        if not boards or unknown:
            raise ValueError(f"flash needs known boards, got {boards!r}")
        lines = []

        def take(line):
            lines.append(line)
            if on_line is not None:
                on_line(line)

        try:
            code = self._run(self.command(boards), str(self.root), take, timeout)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            take(f"The flash tool could not start: {exc}")
            code = -1
        if code is None:
            take(f"The flash took longer than {timeout:g} s and was stopped.")
        last = next((l.strip() for l in reversed(lines) if l.strip()), "")
        return {"ok": code == 0, "returncode": code, "last": last, "lines": lines}
