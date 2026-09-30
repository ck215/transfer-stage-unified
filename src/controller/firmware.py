"""The firmware check, moved out of the launcher onto the Setup page (owner,
2026-09-28: "retire any system that doesn't use the SWAP check. Push any
setup left in the terminal to GUI indicators").

`FirmwareCheck` answers the question `run_swap.sh` (now `dev/swap_branch.sh`) asked before every
launch - is each board running the sketch this install carries? - without
arduino-cli and without opening a port:

    check()   each board's status from the stamp file and the sketch hashes
              (`controller.flashing`, the one copy of the rule): the stamp
              (`~/transfer-stage-runs/flashed.json`, or $STATION_FLASH_STAMP)
              records the hash of the sketch this machine last put on each
              board; a board whose recorded hash differs is out of date, one
              with no record was never flashed from here.
    flash()   `controller.flashing.flash` in this process (brief rb-dist-app
              A1: a frozen bundle's `sys.executable` is its launcher, so
              the old "spawn the script with this interpreter" started the
              station again instead), streaming its lines to a callback. Only
              ever because the operator answered a key - Flash now on the
              startup dialog (owner 2026-09-28: unattended, as the old
              launcher's flash, after one popup) or the row's Flash
              out-of-date boards; nothing here flashes on its own.
"""
import os
from pathlib import Path

from controller import flashing
from controller.flashing import stream_lines  # noqa: F401  (the default runner)

#: The checkout this file lives in: src/controller/firmware.py -> the repo.
ROOT = Path(__file__).resolve().parents[2]
#: The script's own default; both branches' checkouts share it.
DEFAULT_STAMP = flashing.DEFAULT_STAMP
STAMP_ENV = flashing.STAMP_ENV

#: Board -> its sketch directory under the sketch root, in flashing order.
#: A board's name is also its model's NAME.
BOARDS = {name: cfg["dir"] for name, cfg in flashing.BOARDS.items()}
#: What a flash needs (the Teensy loader whatever boards are named, as the
#: old script asked).
TOOLS = (flashing.ARDUINO_CLI, flashing.TEENSY_LOADER)

FLASH_SECONDS = flashing.FLASH_SECONDS

CURRENT, OUT_OF_DATE, NEVER, NO_SKETCH = "current", "out_of_date", "never_flashed", "no_sketch"


def sketch_hash(sketch_root, board):
    """sha256 over the sketch's sources and its vendored libraries
    (`flashing.sketch_hash`)."""
    return flashing.sketch_hash(sketch_root, board)


def _names(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


class FirmwareCheck:
    """`check()` and `flash()` over one sketch tree and one stamp file.
    `run=` stands in for the tool runner, `which=` for `shutil.which` (a
    checkout's PATH), `tools=` for the whole tool lookup, and `identify=` /
    `ports=` for Setup's handshake and port listing, so the tests never
    compile, upload, open a port or look at the bench's PATH.

    `channel` is what the stamp records the flash as (`station`, or
    `stable` for the Switch to stable's flash), `version` the bundle's
    VERSION (None in a checkout)."""

    def __init__(self, root=None, *, sketch_root=None, stamp=None, run=None,
                 which=None, tools=None, identify=None, ports=None,
                 channel=flashing.STATION, version=None):
        self.root = Path(root) if root is not None else ROOT
        self.sketch_root = Path(sketch_root) if sketch_root is not None else self.root / "firmware"
        self.stamp = Path(stamp) if stamp is not None else Path(
            os.environ.get(STAMP_ENV) or DEFAULT_STAMP).expanduser()
        self._run = run or stream_lines
        self._which = which
        self._tools = tools
        self._identify, self._ports = identify, ports
        self.channel = channel
        self.version = version

    @property
    def tools(self):
        """Looked up on every call: a tool installed while the station runs
        is found by the next check."""
        if self._tools is not None:
            return self._tools
        return flashing.tools_for(which=self._which)

    def sketch_hash(self, board):
        return sketch_hash(self.sketch_root, board)

    def _has_sketch(self, board):
        return flashing.has_sketch(board, self.sketch_root)

    def _recorded(self):
        """The stamp as a dict; missing or unreadable is "nothing recorded"."""
        return flashing.load_stamp(self.stamp)

    def check(self):
        """-> {"boards": {board: status}, "stale", "never", "to_flash",
        "missing_tools", "summary"}. `stale` has a record that differs;
        `to_flash` is what a flash would flash (stale and never flashed), in
        flashing order. Reads files only."""
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
        missing = self.tools.missing
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

    def flash(self, boards, on_line=None, timeout=FLASH_SECONDS, force=False):
        """Flash `boards`, the ones plugged in, unattended (the operator
        already confirmed on the Setup page). -> {"ok", "returncode", "last",
        "lines", "results", "absent"}; `last` is the last non-empty line,
        what the Flashing cell showed at the end. A step that raises is a
        failed flash, never an exception out of here."""
        boards = list(boards)
        unknown = [b for b in boards if b not in BOARDS]
        if not boards or unknown:
            raise ValueError(f"flash needs known boards, got {boards!r}")
        lines = []

        def take(line):
            lines.append(line)
            if on_line is not None:
                on_line(line)

        answer = {"returncode": -1, "results": {}, "absent": []}
        try:
            answer = flashing.flash(
                boards, sketch_root=self.sketch_root, stamp=self.stamp,
                tools=self.tools, run=self._run, identify=self._identify,
                ports=self._ports, force=force, channel=self.channel,
                version=self.version, on_line=take, timeout=timeout,
                cwd=self.root)
        except Exception as exc:        # reported, never raised into Setup
            take(f"The flash could not run: {exc}")
        code = answer.get("returncode")
        last = next((l.strip() for l in reversed(lines) if l.strip()), "")
        return {"ok": code == 0, "returncode": code, "last": last, "lines": lines,
                "results": dict(answer.get("results") or {}),
                "absent": list(answer.get("absent") or [])}
