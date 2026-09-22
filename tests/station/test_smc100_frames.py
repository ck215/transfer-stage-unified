"""The bytes did not change.

Firmware is not being touched, so the one thing the SMC100 rewrite is not
allowed to do is send something different. This file runs the **old**
`src/lib/smc100.py` and the **new** `station.devices.smc100.SMC100` through
the same script, against the same scripted stage, recording every byte each
one puts on the wire, and asserts the two streams are identical.

The old driver wrote the payload and the terminator as two separate
`port.write()` calls; the new one hands `SerialPort.write` a single frame.
That is a difference in *calls*, not in bytes, which is why the comparison is
on the concatenated stream -- and why it is also asserted frame by frame,
splitting on the terminator, so a failure names the command that drifted
rather than printing two long byte strings.
"""
import importlib.util
import pathlib

import pytest

from station.devices.smc100 import SMC100


OLD_SOURCE = (pathlib.Path(__file__).resolve().parents[2]
              / "src" / "lib" / "smc100.py")


def _old_module():
    """Load `src/lib/smc100.py` by path: `src/` is not on `sys.path` here, and
    importing it by name would depend on a conftest that may not run."""
    spec = importlib.util.spec_from_file_location("old_smc100", OLD_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- one scripted stage, two transport shapes ------------------------------

class Stage:
    """Answers the SMC100 protocol well enough to drive either driver.

    `states` is the queue of replies to `TS?`, so a test can script a homing
    run or a configuration cycle deterministically. The last entry repeats.
    """

    def __init__(self, states=("32",), position="12.5000"):
        self.states = list(states)
        self.position = position
        self.frames = []          # complete frames written, as bytes
        self._partial = b""
        self._pending = b""       # bytes waiting to be read back

    def feed(self, data):
        self._partial += bytes(data)
        while b"\r\n" in self._partial:
            frame, self._partial = self._partial.split(b"\r\n", 1)
            self.frames.append(frame + b"\r\n")
            self._answer(frame.decode("ascii"))

    def _answer(self, text):
        body = text[1:]                      # drop the controller id
        if body == "TS?":
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            self._pending += f"1TS0000{state}\r\n".encode("ascii")
        elif body == "TP?":
            self._pending += f"1TP{self.position}\r\n".encode("ascii")
        elif body == "ID?":
            self._pending += b"1IDTRB25CC\r\n"

    def read_byte(self):
        if not self._pending:
            return b""
        byte, self._pending = self._pending[:1], self._pending[1:]
        return byte

    def read_line(self):
        if b"\r\n" not in self._pending:
            return None
        line, self._pending = self._pending.split(b"\r\n", 1)
        return line.decode("ascii")

    @property
    def wire(self):
        return b"".join(self.frames)


class OldPort:
    """Shaped like `serial.Serial`, for the driver being replaced."""

    def __init__(self, stage, **kwargs):
        self.stage = stage
        self.kwargs = kwargs

    def write(self, data):
        self.stage.feed(data)
        return len(data)

    def read(self, size=1):
        return self.stage.read_byte()

    def flush(self):
        pass

    def flushInput(self):     # noqa: N802 - pyserial's own spelling
        pass

    def flushOutput(self):    # noqa: N802 - pyserial's own spelling
        pass

    def close(self):
        pass


class NewPort:
    """Shaped like `station.devices.serial_port.SerialPort` (pinned interface),
    for the driver replacing it. A stub in this worktree, so the tests use a
    minimal fake and the real one is joined at merge."""

    def __init__(self, stage):
        self.stage = stage
        self.writes = []
        self.is_open = True
        self.status = "verified"

    def open(self):
        self.is_open = True

    def wait_open(self, timeout=None):
        return True

    def write(self, payload, *, priority=False, abort_if=None):
        if abort_if is not None and abort_if():
            return False
        self.writes.append((bytes(payload), priority))
        self.stage.feed(payload)
        return True

    def read_line(self, timeout=None):
        return self.stage.read_line()

    def flush(self, timeout=None):
        pass

    def close(self):
        self.is_open = False


def _old(stage, **kwargs):
    old = _old_module()
    port = OldPort(stage, **kwargs)
    driver = old.SMC100.__new__(old.SMC100)
    # Through the real constructor's body would open a real `serial.Serial`;
    # the fields it sets are these four, and setting them directly keeps the
    # comparison about the protocol rather than about pyserial.
    import threading
    driver._serial_lock = threading.Lock()
    driver._port = port
    driver._smcID = "1"
    driver._silent = True
    driver._sleepfunc = lambda seconds: None
    driver._last_sendcmd_time = 0
    driver.printed_moving = False
    return driver


def _new(stage):
    return SMC100(1, "SIM-PORT", transport=NewPort(stage),
                  sleep=lambda seconds: None)


def _both(script, states=("32",), position="12.5000"):
    """Run `script(driver)` against each driver and return the two streams."""
    old_stage = Stage(states, position)
    script(_old(old_stage))
    new_stage = Stage(states, position)
    script(_new(new_stage))
    return old_stage.wire, new_stage.wire


def _assert_identical(script, states=("32",), position="12.5000"):
    old_wire, new_wire = _both(script, states, position)
    assert old_wire, "the script sent nothing; the comparison would be vacuous"
    old_frames = old_wire.split(b"\r\n")
    new_frames = new_wire.split(b"\r\n")
    assert old_frames == new_frames, (
        f"the frames changed:\n  old {old_frames}\n  new {new_frames}")
    assert old_wire == new_wire
    return new_wire


# -- the commands ----------------------------------------------------------

def test_home_sends_the_same_bytes():
    """READY_FROM_HOMING: OR, then poll until the controller says it arrived."""
    wire = _assert_identical(lambda smc: smc.home(), states=("1E", "32"))
    assert wire.startswith(b"1OR\r\n")


def test_home_from_a_moving_stage_still_settles_at_zero_the_same_way():
    """READY_FROM_MOVING takes the second branch: an absolute move to 0."""
    wire = _assert_identical(lambda smc: smc.home(), states=("28", "33", "33"))
    assert b"1PA0.0\r\n" in wire, wire


def test_move_absolute_sends_the_same_bytes():
    wire = _assert_identical(lambda smc: smc.move_absolute_deg(12.5),
                             states=("28", "33"))
    assert wire.startswith(b"1PA12.5\r\n")


def test_move_relative_sends_the_same_bytes():
    wire = _assert_identical(lambda smc: smc.move_relative_deg(-4.25),
                             states=("28", "33"))
    assert wire.startswith(b"1PR-4.25\r\n")


def test_stop_sends_the_same_bytes():
    assert _assert_identical(lambda smc: smc.stop()) == b"1ST\r\n"


def test_priority_stop_sends_the_same_bytes():
    """The priority lane changes which lock is taken, never what is sent."""
    assert _assert_identical(lambda smc: smc.stop(priority=True)) == b"1ST\r\n"


def test_status_sends_the_same_bytes():
    assert _assert_identical(lambda smc: smc.get_status()) == b"1TS?\r\n"


def test_position_sends_the_same_bytes():
    assert _assert_identical(lambda smc: smc.get_position_deg()) == b"1TP?\r\n"


def test_reset_and_configure_sends_the_same_bytes():
    """The whole configuration cycle: RS RS, ID?, PW1, ZX1, ZX2, PW0."""
    wire = _assert_identical(lambda smc: smc.reset_and_configure(),
                             states=("0A", "14", "0C"))
    assert wire == (b"1RS\r\n1RS\r\n1TS?\r\n1ID?\r\n1PW1\r\n1TS?\r\n"
                    b"1ZX1\r\n1ZX2\r\n1PW0\r\n1TS?\r\n"), wire


def test_a_whole_session_matches_frame_for_frame():
    """Every command in one run, so an ordering or pacing change shows up."""
    def script(smc):
        smc.get_status()
        smc.get_position_deg()
        smc.home()
        smc.move_absolute_deg(30.0)
        smc.move_relative_deg(1.5)
        smc.move_relative_mdeg(-2500)
        smc.move_absolute_mdeg(1250)
        smc.stop()
        smc.stop(priority=True)

    _assert_identical(script, states=("32", "33"))


def test_the_comparison_can_fail():
    """A guard on the harness itself: identical-looking streams must not be
    the only thing this file is able to produce."""
    stage = Stage(("32",))
    _new(stage).move_absolute_deg(1.0, wait_stop=False)
    other = Stage(("32",))
    _new(other).move_absolute_deg(2.0, wait_stop=False)
    assert stage.wire != other.wire
