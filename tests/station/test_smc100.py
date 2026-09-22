"""`station.devices.smc100.SMC100`: the driver's behaviour, not its bytes.

The bytes are pinned separately, against the old driver, in
`test_smc100_frames.py`. What is pinned here is everything that changed on
purpose: one transport instead of two, a bounded write (ROTATOR-16), a
priority stop that is never aborted by the latch it exists to serve, a wait
that no longer expires under a long move (ROTATOR-11), and one exception
family instead of five unrelated classes.
"""
import threading
import time
from unittest.mock import patch

import pytest

from station.devices import smc100 as driver
from station.devices.smc100 import (
    SMC100, SMC100Corruption, SMC100DisabledState, SMC100Error,
    SMC100InvalidResponse, SMC100ReadTimeout, SMC100WaitTimeout,
)


class FakePort:
    """The pinned `SerialPort` interface, and nothing more.

    `SerialPort` is a stub in this worktree, so the driver is tested against
    the interface the brief pins: `write(payload, *, priority, abort_if)
    -> bool`, `read_line(timeout) -> str | None`, `flush(timeout)`,
    `wait_open(timeout) -> bool`, `open/close/is_open/status`.
    """

    def __init__(self, replies=None, accept=True):
        self.writes = []            # (payload, priority, abort_if)
        self.replies = list(replies or [])
        self.accept = accept        # what `write` returns
        self.is_open = True
        self.status = "verified"
        self.opened = 0
        self.closed = 0
        self.wait_open_result = True
        self.flushes = 0
        self.block = threading.Event()

    def open(self):
        self.opened += 1
        self.is_open = True

    def wait_open(self, timeout=None):
        return self.wait_open_result

    def write(self, payload, *, priority=False, abort_if=None):
        if abort_if is not None and abort_if():
            return False
        if self.block.is_set() and not priority:
            self.block.wait(5)
        self.writes.append((bytes(payload), priority, abort_if))
        return self.accept

    def read_line(self, timeout=None):
        return self.replies.pop(0) if self.replies else None

    def flush(self, timeout=None):
        self.flushes += 1

    def close(self):
        self.closed += 1
        self.is_open = False

    @property
    def payloads(self):
        return [payload for payload, _priority, _abort in self.writes]


def _smc(port=None, **kwargs):
    kwargs.setdefault("sleep", lambda seconds: None)
    return SMC100(1, "SIM-PORT", transport=port or FakePort(), **kwargs)


# -- one transport, with this device's settings ----------------------------

def test_the_driver_builds_its_port_with_the_smc100s_own_serial_settings():
    """Copied from what `src/lib/smc100.py` opened, not re-derived: 57600 8N1,
    software flow control, CRLF."""
    built = {}

    def factory(port, **kwargs):
        built["port"] = port
        built["kwargs"] = kwargs
        return FakePort()

    with patch.object(driver, "SerialPort", side_effect=factory):
        SMC100(1, "/dev/ttyS5")

    assert built["port"] == "/dev/ttyS5"
    kwargs = built["kwargs"]
    assert kwargs["baud_rate"] == 57600
    assert kwargs["parity"] == "N"
    assert kwargs["byte_size"] == 8
    assert kwargs["stop_bits"] == 1
    assert kwargs["xonxoff"] is True
    assert kwargs["terminator"] == b"\r\n"


def test_the_port_is_opened_with_no_handshake():
    """There is no identity byte to ask an SMC100 for, and a handshake write
    into a stage controller is not a harmless probe."""
    built = {}
    with patch.object(driver, "SerialPort",
                      side_effect=lambda port, **kwargs: built.update(kwargs) or FakePort()):
        SMC100(1, "/dev/ttyS5")
    assert built["handshake"] is False


def test_the_write_is_bounded():
    """ROTATOR-16. `xonxoff=True` lets a busy or faulted controller withhold
    XON exactly when someone is pressing FULL STOP, and pyserial's default
    `write_timeout=None` means block forever."""
    built = {}
    with patch.object(driver, "SerialPort",
                      side_effect=lambda port, **kwargs: built.update(kwargs) or FakePort()):
        SMC100(1, "/dev/ttyS5")
    assert built["write_timeout"] is not None
    assert built["write_timeout"] == SMC100.WRITE_TIMEOUT_SEC


def test_a_transport_that_cannot_take_the_settings_fails_loudly():
    """Never a silent fallback to a narrower signature: the write timeout is
    the whole of ROTATOR-16, so losing it must not be survivable."""
    with patch.object(driver, "SerialPort", side_effect=TypeError("no such kwarg")):
        with pytest.raises(TypeError):
            SMC100(1, "/dev/ttyS5")


# -- the FULL STOP latch, inside the port lock -----------------------------

def test_every_ordinary_command_carries_the_latch_into_the_port_lock():
    port = FakePort(replies=["1TS000032"])
    latch = threading.Event()
    abort_if = latch.is_set
    smc = _smc(port, abort_if=abort_if)
    smc.get_status()
    assert port.writes[0][2] is abort_if, (
        "the command did not carry abort_if, so the latch is only ever "
        "checked outside the lock, where it cannot catch a stop that landed "
        "while the command queued")


def test_a_latched_command_writes_nothing_and_says_so():
    port = FakePort()
    latch = threading.Event()
    latch.set()
    smc = _smc(port, abort_if=latch.is_set)
    with pytest.raises(SMC100Error) as raised:
        smc.move_absolute_deg(10.0, wait_stop=False)
    assert not port.writes, "a latched move still put bytes on the wire"
    assert "full stop" in str(raised.value).lower()


def test_the_priority_stop_is_never_aborted_by_the_latch():
    """The latch is the reason this write exists; it must not be the reason
    it is dropped."""
    port = FakePort()
    latch = threading.Event()
    latch.set()
    smc = _smc(port, abort_if=latch.is_set)
    assert smc.stop(priority=True) is True
    assert port.payloads == [b"1ST\r\n"]
    assert port.writes[0][2] is None, "the priority stop carried an abort_if"


def test_the_priority_stop_takes_the_priority_lane():
    port = FakePort()
    _smc(port).stop(priority=True)
    assert port.writes[0][1] is True, "the stop queued on the ordinary lane"


def test_an_ordinary_stop_does_not_take_the_priority_lane():
    port = FakePort()
    _smc(port).stop()
    assert port.writes[0][1] is False


def test_a_stop_that_was_not_written_reports_false():
    """MANAGER-21: a stop that did not land must not read as one that did."""
    port = FakePort(accept=False)
    assert _smc(port).stop(priority=True) is False


def test_a_priority_stop_does_not_wait_behind_a_blocked_ordinary_write():
    """ROTATOR-8, at this level: the lane is what the driver asks for; the
    port honours it. A blocked ordinary write must not hold the stop."""
    port = FakePort()
    port.block.set()
    smc = _smc(port)

    slow = threading.Thread(target=lambda: smc.sendcmd("PA", 10.0), daemon=True)
    slow.start()
    time.sleep(0.05)
    started = time.monotonic()
    landed = smc.stop(priority=True)
    elapsed = time.monotonic() - started
    port.block.clear()

    assert landed is True
    assert elapsed < 0.5, f"the priority stop waited {elapsed:.2f}s"


# -- replies ---------------------------------------------------------------

def test_no_reply_is_a_read_timeout():
    with pytest.raises(SMC100ReadTimeout):
        _smc(FakePort(replies=[])).get_status()


def test_a_reply_for_another_command_is_an_invalid_response():
    port = FakePort()
    port.read_line = lambda timeout=None: "1TP12.5"     # every retry, too
    with pytest.raises(SMC100InvalidResponse):
        _smc(port).get_status()


def test_an_unprintable_byte_in_the_reply_is_corruption():
    port = FakePort()
    port.read_line = lambda timeout=None: "1TS00\x0003"
    with pytest.raises(SMC100Corruption):
        _smc(port).get_status()


def test_a_read_only_command_retries_before_giving_up():
    port = FakePort(replies=["", "", "1TS000032"])
    assert _smc(port).get_status() == (0, "32")
    assert len(port.payloads) == 3, "the retry did not re-send"


def test_a_relative_move_is_never_retried():
    """Repeating PR repeats MOTION. The driver refuses a retry on PR and OR
    whatever the caller asks for."""
    port = FakePort(replies=[])
    smc = _smc(port)
    with pytest.raises(SMC100Error):
        smc.sendcmd("PR", 5.0, expect_response=True, retry=10)
    assert len(port.payloads) == 1, (
        f"PR was sent {len(port.payloads)} times; every repeat is another "
        f"5 degrees of real motion")


def test_status_parses_the_error_word_and_the_state():
    assert _smc(FakePort(replies=["1TS001C28"])).get_status() == (0x1C, "28")


def test_position_is_read_as_degrees_and_millidegrees():
    assert _smc(FakePort(replies=["1TP12.5"])).get_position_deg() == 12.5
    assert _smc(FakePort(replies=["1TP12.5"])).get_position_mdeg() == 12500


# -- waiting (ROTATOR-11) --------------------------------------------------

class MovingStage(FakePort):
    """Reports MOVING for `moving_for` seconds, then READY_FROM_MOVING."""

    def __init__(self, moving_for):
        super().__init__()
        self.moving_for = moving_for
        self.started = time.monotonic()

    def read_line(self, timeout=None):
        moving = time.monotonic() - self.started < self.moving_for
        return "1TS0000" + ("28" if moving else "33")


def test_a_long_move_is_not_capped(monkeypatch):
    """ROTATOR-11. The 12 s bounds time spent *not making progress*. A 60 deg
    move at a low velocity used to raise 'Wait timed out' while the stage was
    still turning -- and nothing in the timeout path stops the stage, so the
    move then completed behind an error popup saying it had failed."""
    port = MovingStage(moving_for=0.3)
    smc = _smc(port)
    smc.MAX_IDLE_WAIT_SEC = 0.1     # far shorter than the move
    smc.MAX_MOVING_WAIT_SEC = 5.0

    state = smc.wait_states(("33", "32"))

    assert state == "33"
    assert time.monotonic() - port.started > 0.3, (
        "the wait returned before the move finished")


def test_a_stage_making_no_progress_still_times_out():
    """The other half: 'wait while it says it is moving' must not become
    'wait forever' for a controller sitting in one idle state."""
    port = FakePort()
    port.read_line = lambda timeout=None: "1TS00000A"   # NOT REFERENCED, idle
    smc = _smc(port)
    smc.MAX_IDLE_WAIT_SEC = 0.15
    with pytest.raises(SMC100WaitTimeout):
        smc.wait_states("33")


def test_a_wedged_moving_controller_hits_the_absolute_backstop():
    port = MovingStage(moving_for=60.0)
    smc = _smc(port)
    smc.MAX_IDLE_WAIT_SEC = 10.0
    smc.MAX_MOVING_WAIT_SEC = 0.2
    with pytest.raises(SMC100WaitTimeout):
        smc.wait_states("33")


def test_a_disabled_state_raises_unless_it_is_what_we_are_waiting_for():
    """A stage that sticks transitions into DISABLE_FROM_MOVING and stays
    there forever."""
    port = FakePort()
    port.read_line = lambda timeout=None: "1TS00003D"
    smc = _smc(port)
    smc.MAX_IDLE_WAIT_SEC = 1.0
    with pytest.raises(SMC100DisabledState):
        smc.wait_states("33")
    assert smc.wait_states("3D") == "3D"


def test_the_wait_is_abandoned_when_the_latch_goes_down():
    """A move already waiting must not keep polling for five minutes after a
    FULL STOP."""
    port = MovingStage(moving_for=60.0)
    latch = threading.Event()
    smc = _smc(port, abort_if=latch.is_set)
    smc.MAX_MOVING_WAIT_SEC = 30.0

    def _latch_soon():
        time.sleep(0.05)
        latch.set()

    threading.Thread(target=_latch_soon, daemon=True).start()
    started = time.monotonic()
    with pytest.raises(SMC100Error):
        smc.wait_states("33")
    assert time.monotonic() - started < 5.0


# -- lifecycle -------------------------------------------------------------

def test_open_proves_the_controller_answers():
    port = FakePort(replies=["1TS000032"])
    smc = _smc(port)
    assert smc.open() is True
    assert port.opened == 1
    assert port.payloads == [b"1TS?\r\n"], (
        "open() did not transact; a port that opens is not a controller that "
        "answers")


def test_open_fails_when_the_port_never_comes_up():
    port = FakePort()
    port.wait_open_result = False
    with pytest.raises(SMC100Error):
        _smc(port).open()


def test_close_closes_the_port_once_and_stays_closed():
    port = FakePort()
    smc = _smc(port)
    smc.close()
    smc.close()
    assert port.closed == 1
    assert smc.is_open is False
    assert smc.status == "closed"


def test_the_status_word_comes_from_the_port():
    port = FakePort()
    port.status = "lost"
    assert _smc(port).status == "lost"


# -- what was removed ------------------------------------------------------

def test_the_five_exception_classes_are_one_family():
    for failure in (SMC100ReadTimeout, SMC100WaitTimeout, SMC100DisabledState,
                    SMC100Corruption, SMC100InvalidResponse):
        assert issubclass(failure, SMC100Error)
        assert str(failure()), f"{failure.__name__} has no default message"


def test_the_bench_scripts_and_the_destructor_are_gone():
    """`test_configure` and `test_general` ran real motion from inside a
    library module, on names pytest collects; `__del__` closed the port from
    the garbage collector, which is now `Rotator.close`'s job."""
    for gone in ("test_configure", "test_general"):
        assert not hasattr(driver, gone), f"{gone} came back"
    assert "__del__" not in SMC100.__dict__
