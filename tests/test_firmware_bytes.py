"""The probes' declared control bytes against the `.ino` sources (gate G3).

Port of legacy `tests/hardware/test_serial10_power_down_truth.py::
test_declared_control_bytes_match_the_ino_files` and
`::test_no_firmware_handles_the_kill_coils_byte`.

`Probe.FIRMWARE_CONTROL_BYTES` (`src/model/probe.py`) is what the model
believes a board's firmware handles; the model uses it to decide what it may
claim about a stop (the DC probe says once that it cannot kill coils). These
tests read `firmware/<board>/<board>.ino` and check that belief against the
sketch's dispatch code: the `==` comparisons, `>= && <=` ranges and
`switch`/`case` labels on a variable that holds `Serial.peek()` or
`Serial.read()`. Comments and string literals are stripped first, so a byte
mentioned in prose or printed as text does not count as handled.

The sketch for each board is `controller.firmware.BOARDS[cls.NAME]`, the
same table the flashing check uses. A missing sketch directory fails the
test; it never skips.
"""
import re
from pathlib import Path

import pytest

from controller.firmware import BOARDS
from model.probe import ChuckPositioner, DCProbe, StepperProbe
from model.xyz_stage_mega import XyzStageMega

REPO = Path(__file__).resolve().parents[1]
FIRMWARE = REPO / "firmware"

#: The XYZ Stage (Mega) speaks the Stepper Probe's frame protocol byte for
#: byte (MEGA_STANDARD section 1): its sketch must handle `d`, `e`, `s`.
PROBES = (StepperProbe, DCProbe, ChuckPositioner, XyzStageMega)

KILL_COILS = ord("k")   # 0x6B

_LIT = r"(0[xX][0-9A-Fa-f]+|'(?:\\.|[^'\\])'|\d+|[A-Za-z_]\w*)"
_ESCAPES = {"n": 10, "r": 13, "t": 9, "0": 0, "\\": 92, "'": 39}


# -- a small reader for the dispatch constructs -------------------------------

def _strip(source):
    """Remove comments and string literals; keep char literals."""
    out, i, n = [], 0, len(source)
    while i < n:
        two = source[i:i + 2]
        if two == "//":
            j = source.find("\n", i)
            i = n if j < 0 else j
        elif two == "/*":
            j = source.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif source[i] == '"':
            j = i + 1
            while j < n and source[j] != '"':
                j += 2 if source[j] == "\\" else 1
            out.append('""')
            i = j + 1
        elif source[i] == "'":
            j = i + 1
            while j < n and source[j] != "'":
                j += 2 if source[j] == "\\" else 1
            out.append(source[i:j + 1])
            i = j + 1
        else:
            out.append(source[i])
            i += 1
    return "".join(out)


def _constants(code):
    """`char startMarker = '<';` and friends: name -> literal text."""
    found = {}
    for name, lit in re.findall(
            r"\b(?:const\s+)?(?:char|byte|uint8_t|int)\s+([A-Za-z_]\w*)\s*=\s*"
            r"(0[xX][0-9A-Fa-f]+|'(?:\\.|[^'\\])'|\d+)\s*;", code):
        found[name] = lit
    return found


def _value(lit, constants, seen=()):
    if lit.lower().startswith("0x"):
        return int(lit, 16)
    if lit.startswith("'"):
        body = lit[1:-1]
        if body.startswith("\\x"):
            return int(body[2:], 16)
        if body.startswith("\\"):
            return _ESCAPES[body[1]]
        return ord(body)
    if lit.isdigit():
        return int(lit)
    if lit in constants and lit not in seen:
        return _value(constants[lit], constants, seen + (lit,))
    return None   # an identifier that is not a known constant


def _block_after(code, start):
    """The `{...}` body that begins at or after `start`."""
    open_at = code.find("{", start)
    depth = 0
    for k in range(open_at, len(code)):
        if code[k] == "{":
            depth += 1
        elif code[k] == "}":
            depth -= 1
            if depth == 0:
                return code[open_at:k + 1]
    return code[open_at:]


def handled_bytes(source):
    """Every byte value the sketch's serial dispatch compares an input byte to."""
    code = _strip(source)
    constants = _constants(code)
    readers = set(re.findall(
        r"\b([A-Za-z_]\w*)\s*=\s*(?:\(\s*\w+\s*\)\s*)?Serial\d*\.(?:peek|read)\s*\(\s*\)",
        code))
    handled = set()
    for var in readers:
        v = rf"(?:\(\s*\w+\s*\)\s*)?\b{re.escape(var)}\b"
        for a, b in re.findall(rf"{v}\s*>=\s*{_LIT}\s*&&\s*{v}\s*<=\s*{_LIT}", code):
            lo, hi = _value(a, constants), _value(b, constants)
            if lo is not None and hi is not None:
                handled.update(range(lo, hi + 1))
        for lit in re.findall(rf"{v}\s*==\s*{_LIT}", code) + \
                re.findall(rf"{_LIT}\s*==\s*{v}", code):
            value = _value(lit, constants)
            if value is not None:
                handled.add(value)
        for match in re.finditer(rf"\bswitch\s*\(\s*{v}\s*\)", code):
            for lit in re.findall(rf"\bcase\s+{_LIT}\s*:", _block_after(code, match.end())):
                value = _value(lit, constants)
                if value is not None:
                    handled.add(value)
    return handled


def _sketch(directory):
    path = FIRMWARE / directory
    assert path.is_dir(), f"sketch directory {path} is missing"
    inos = sorted(path.glob("*.ino"))
    assert inos, f"no .ino under {path}"
    return "\n".join(p.read_text(errors="replace") for p in inos)


# -- the reader itself is not vacuous -----------------------------------------

def test_the_reader_finds_each_dispatch_construct_and_ignores_prose():
    sketch = """
        char mark = 'k';
        void loop() {
            uint8_t c = (uint8_t)Serial.peek();
            // if (c == 0x6B) in a comment is not a handler
            Serial.println("c == 'k'");
            if (c == 0x64) { Serial.read(); }
            else if ((uint8_t)c == 'e') { }
            else if (c >= '0' && c <= '2') { }
            int r = Serial.read();
            switch (r) { case 0x41: break; case 'B': break; }
            if (r == mark) { }
        }
    """
    got = handled_bytes(sketch)
    assert got == {0x64, ord("e"), ord("0"), ord("1"), ord("2"), 0x41, ord("B"), ord("k")}
    assert KILL_COILS not in handled_bytes(sketch.replace("if (r == mark) { }", ""))


# -- (a) every declared byte is handled by that board's sketch -----------------

@pytest.mark.parametrize("cls", PROBES, ids=lambda c: c.NAME)
def test_every_declared_control_byte_is_handled_by_the_boards_sketch(cls):
    assert cls.NAME in BOARDS, f"{cls.NAME} has no sketch in controller.firmware.BOARDS"
    handled = handled_bytes(_sketch(BOARDS[cls.NAME]))
    declared = {b[0] for b in cls.FIRMWARE_CONTROL_BYTES}
    missing = sorted(chr(b) for b in declared - handled)
    assert not missing, f"{cls.NAME} declares {missing} but {BOARDS[cls.NAME]} has no branch for them"


@pytest.mark.parametrize("cls", PROBES, ids=lambda c: c.NAME)
def test_the_declared_bytes_follow_the_sketch_for_every_control_byte_the_host_sends(cls):
    """The converse, for the bytes the stop and arm paths send: a sketch that
    gains (or loses) a `d`/`e`/`k`/`s` branch must be reflected in the
    declaration, or the model misreports what a stop achieved."""
    handled = handled_bytes(_sketch(BOARDS[cls.NAME]))
    for byte in (b"d", b"e", b"k", b"s"):
        assert (byte in cls.FIRMWARE_CONTROL_BYTES) == (byte[0] in handled), (
            f"{cls.NAME}: {byte!r} declared={byte in cls.FIRMWARE_CONTROL_BYTES} "
            f"handled={byte[0] in handled} in {BOARDS[cls.NAME]}")


# -- (b) no sketch handles the kill-coils byte ---------------------------------

def test_no_sketch_handles_the_kill_coils_byte():
    """Record of fact tied to OPEN owner decision D-7 (the DC board has no
    coil kill; whether the protocol should grow a `'k'` handler is the
    owner's, at the bench). The host sends `'k'` on every stop; no board acts
    on it. This test does not say that is right. It says it is so, and fails
    the day a sketch grows a `'k'` branch, so the declarations and the D-7
    record get revisited together instead of drifting."""
    for directory in BOARDS.values():
        _sketch(directory)   # every sketch named in the table exists
    sketches = sorted(FIRMWARE.glob("*/*.ino"))
    assert len(sketches) >= len(BOARDS)
    handling = [str(p.relative_to(REPO)) for p in sketches
                if KILL_COILS in handled_bytes(p.read_text(errors="replace"))]
    assert handling == [], f"'k' (0x6B) is handled by {handling}: D-7 has moved"
    assert not any(b"k" in cls.FIRMWARE_CONTROL_BYTES for cls in PROBES)
