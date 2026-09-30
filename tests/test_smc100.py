"""`devices.smc100.SMC100`: the driver's behaviour, not its bytes.

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

from devices import smc100 as driver
from devices.serial_port import SerialPort
from devices.smc100 import (
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

def test_the_driver_builds_a_real_serial_port_with_the_smc100s_own_settings():
    """Copied from what `legacy/src/lib/smc100.py` opened, not re-derived: 57600 8N1,
    software flow control, CRLF lines, a 50 ms read and a bounded write.

    Asserted against the real `SerialPort`, not a double -- constructing one
    does no I/O, so this is the configuration the bench will actually get.
    8/N/1 is fixed inside `SerialPort._open_handle` and already matches.
    """
    port = SMC100(1, "/dev/ttyS5")._port

    assert isinstance(port, SerialPort)
    assert port.port == "/dev/ttyS5"
    assert port.baud_rate == 57600
    assert port.xonxoff is True
    assert port.read_timeout == 0.05
    assert port.line_terminator == b"\r\n"


def test_the_port_is_opened_with_no_handshake():
    """There is no `s\\n` -> `DEV: x` protocol on a Newport controller, and a
    handshake write into a stage controller is not a harmless probe."""
    assert SMC100(1, "/dev/ttyS5")._port.has_handshake is False


def test_the_write_is_bounded():
    """ROTATOR-16. `xonxoff=True` lets a busy or faulted controller withhold
    XON exactly when someone is pressing FULL STOP, and pyserial's default
    `write_timeout=None` means block forever."""
    port = SMC100(1, "/dev/ttyS5")._port
    assert port.write_timeout is not None
    assert port.write_timeout == SMC100.WRITE_TIMEOUT_SEC == 0.2


def test_a_transport_that_cannot_take_the_settings_fails_loudly():
    """Never a silent fallback to a narrower signature: the write timeout is
    the whole of ROTATOR-16, so losing it must not be survivable."""
    with patch.object(driver, "SerialPort", side_effect=TypeError("no such kwarg")):
        with pytest.raises(TypeError):
            SMC100(1, "/dev/ttyS5")


def test_opening_vouches_for_the_link_once_the_controller_answers():
    """A `handshake=False` port opens UNVERIFIED by contract. `TS?` is this
    device's own identity question, so answering it is what earns VERIFIED --
    otherwise the rotator's status word stays 'unverified' however well the
    stage is talking."""
    port = FakePort(replies=["1TS000032"])
    port.verified = []
    port.mark_verified = lambda identity=None: port.verified.append(identity)
    _smc(port).open()
    assert port.verified, "the driver never vouched for the link"
    assert "SMC100" in str(port.verified[0])


# -- the FULL STOP latch, inside the port lock -----------------------------

@pytest.mark.parametrize("command, argument, expect", [
    ("PR", 1.5, False), ("PA", 10.0, False), ("OR", None, False),
    ("RS", None, False), ("PW", 1, False), ("ZX", 1, False),
    ("ST", None, False), ("ID", "?", True),
])
def test_every_command_that_is_not_a_poll_carries_the_latch_into_the_port_lock(
        command, argument, expect):
    port = FakePort(replies=["1IDTRB25CC"])
    latch = threading.Event()
    abort_if = latch.is_set
    smc = _smc(port, abort_if=abort_if)
    smc.sendcmd(command, argument, expect_response=expect)
    assert port.writes[0][2] is abort_if, (
        f"{command} did not carry abort_if, so the latch is only ever "
        "checked outside the lock, where it cannot catch a stop that landed "
        "while the command queued")


def test_the_read_only_polls_are_not_refused_by_the_latch():
    """SF-3: `TS?` and `TP?` move nothing, and after a stop they are what
    confirms it. Refusing them under the latch made every FULL STOP read as
    a lost cable. They carry no `abort_if`; everything that moves still
    does (above), and a latched move still writes nothing (below)."""
    port = FakePort(replies=["1TS000033", "1TP4.5"])
    latch = threading.Event()
    latch.set()
    smc = _smc(port, abort_if=latch.is_set)
    assert smc.get_status() == (0, "33")
    assert smc.get_position_deg() == 4.5
    assert port.payloads == [b"1TS?\r\n", b"1TP?\r\n"]
    assert [abort for _payload, _priority, abort in port.writes] == [None, None]
    with pytest.raises(SMC100Error):
        smc.move_relative_deg(1.0, wait_stop=False)
    assert port.payloads == [b"1TS?\r\n", b"1TP?\r\n"], "a latched move was written"


def test_a_wait_for_motion_is_still_abandoned_under_the_latch():
    """The polls are exempt; waiting on a MOVE is not. With `TS?` no longer
    aborting inside the port lock, `wait_states` must still give up on its
    own check of the latch rather than keep watching a stopped stage."""
    port = FakePort(replies=["1TS000028"] * 50)
    latch = threading.Event()
    latch.set()
    smc = _smc(port, abort_if=latch.is_set)
    with pytest.raises(SMC100Error, match="FULL STOP"):
        smc.wait_states(("33",))


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


# -- D2: one transaction at a time, write to reply -------------------------

class LatencyStage:
    """A controller behind a wire that answers `latency` seconds late.

    One shared input stream, as on the bench: a reply is not addressed to the
    thread that asked, it is simply the next line on the port. `TS?` answers
    with the state the controller was in **when the question arrived**, so a
    `TS?` that went out just before a `PR` answers READY even though it is
    read after the stage has started turning -- which is the whole of D2.
    """

    MOVE_SEC = 0.15

    def __init__(self, latency=0.020):
        self.latency = latency
        self.moving_until = 0.0
        self.writes = []
        self.priorities = []
        self.discarded = 0
        self._arriving = []          # (arrival time, line), in arrival order
        self._lock = threading.Lock()

    def write(self, payload, *, priority=False, abort_if=None):
        if abort_if is not None and abort_if():
            return False
        now = time.monotonic()
        body = bytes(payload).decode("ascii").strip()[1:]
        with self._lock:
            self.writes.append(bytes(payload))
            self.priorities.append(priority)
            if body.startswith("PR"):
                self.moving_until = now + self.MOVE_SEC
            elif body == "ST":
                self.moving_until = now
            elif body == "TS?":
                state = "28" if now < self.moving_until else "33"
                self._arriving.append((now + self.latency, f"1TS0000{state}"))
            elif body == "TP?":
                self._arriving.append((now + self.latency, "1TP0.0000"))
        return True

    def read_line(self, timeout=None):
        deadline = time.monotonic() + (0.05 if timeout is None else timeout)
        while True:
            with self._lock:
                if self._arriving and self._arriving[0][0] <= time.monotonic():
                    return self._arriving.pop(0)[1]
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.001)

    def discard_input(self):
        """Drop what has already arrived; a reply still on the wire stays."""
        with self._lock:
            now = time.monotonic()
            stale = [entry for entry in self._arriving if entry[0] <= now]
            self._arriving = [entry for entry in self._arriving if entry[0] > now]
            self.discarded += len(stale)
            return len(stale)

    def flush(self, timeout=None):
        return True

    @property
    def is_moving(self):
        return time.monotonic() < self.moving_until


def test_a_move_never_reports_done_while_the_poll_shares_the_link():
    """D2: Move/Home returned while the stage was still turning.

    The rebuild wrote and read with no lock across the pair, so the position
    poll and the move's status loop read each other's replies: a `TS?` the
    poll sent a moment before the `PR` answers READY, and the move's wait
    could take that line as its own. The lead measured 4/10 early returns at
    20 ms reply latency. The poll here runs back to back, a stress version of
    the model's 4 Hz sampler, so the collision is not left to luck.

    It also proves the lock is fair: a poll that could not get the link
    within its bound (the move's tight `TS?` loop starving it) would fail
    with `SMC100LinkBusy`, which the model would show as "Communication
    lost" mid-move.
    """
    trials, early, starved = 30, [], []
    for trial in range(trials):
        stage = LatencyStage(latency=0.020)
        smc = SMC100(1, "LATENCY", transport=stage)
        stop = threading.Event()

        def poll():
            while not stop.is_set():
                try:
                    smc.get_position_deg()
                    smc.get_status()
                except SMC100Error as exc:
                    if type(exc).__name__ == "SMC100LinkBusy":
                        starved.append((trial, repr(exc)))
                time.sleep(0.001)

        poller = threading.Thread(target=poll, daemon=True)
        poller.start()
        time.sleep(0.03)
        try:
            smc.move_relative_deg(1.0)
            if stage.is_moving:
                early.append(trial)
        finally:
            stop.set()
            poller.join(2.0)
    assert not early, (f"{len(early)}/{trials} moves reported done while the "
                       f"stage was still turning (trials {early})")
    assert not starved, f"the poll was starved off the link: {starved[:3]}"


class _StaleHandle:
    """A pyserial stand-in for the REAL `SerialPort`: answers `TS?` with
    MOVING, and can be pre-loaded with a late reply to an earlier question."""

    def __init__(self):
        self.is_open = True
        self.wire = b""
        self._pending = b""
        self._partial = b""

    @property
    def in_waiting(self):
        return len(self._pending)

    def write(self, data):
        self.wire += bytes(data)
        self._partial += bytes(data)
        while b"\r\n" in self._partial:
            frame, self._partial = self._partial.split(b"\r\n", 1)
            if frame.decode("ascii")[1:] == "TS?":
                self._pending += b"1TS000028\r\n"
        return len(data)

    def read(self, size=1):
        out, self._pending = self._pending[:size], self._pending[size:]
        return out

    def read_all(self):
        return self.read(self.in_waiting)

    def readline(self):
        return self.read_all()

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.is_open = False


def test_a_late_reply_is_discarded_before_the_next_question():
    """D2, the second half: `legacy/src` cleared the input before every
    question. A reply that arrived after its own read timed out -- READY,
    from before the move -- is otherwise read as the answer to the next
    `TS?`. Run over the real `SerialPort`, because the discard has to work
    on the transport the bench gets."""
    from types import SimpleNamespace

    from devices import serial_port

    handle = _StaleHandle()
    with patch.object(serial_port, "pyserial",
                      SimpleNamespace(Serial=lambda **kwargs: handle)):
        smc = SMC100(1, "/dev/fake-smc100", sleep=lambda seconds: None)
        smc._port.open()
        assert smc._port.wait_open(2.0)
        try:
            handle._pending = b"1TS000033\r\n"      # the late READY
            assert smc.get_status() == (0, "28"), (
                "a stale READY was read as the answer to this TS?")
            assert handle.wire == b"1TS?\r\n", "the discard must not write"
        finally:
            smc.close()


class _WedgedReadPort(FakePort):
    """A reply that does not come until the test says so."""

    def __init__(self):
        super().__init__()
        self.reading = threading.Event()
        self.release = threading.Event()

    def read_line(self, timeout=None):
        self.reading.set()
        self.release.wait(5)
        return "1TS000033"


def test_a_priority_stop_does_not_wait_behind_a_transaction_in_flight():
    """The transaction lock added for D2 must never be on the stop's path:
    `Rotator._halt_hardware` calls `stop(priority=True)`, and a poll that
    holds the link across a slow reply must not delay it."""
    port = _WedgedReadPort()
    smc = _smc(port)
    poll = threading.Thread(target=smc.get_status, daemon=True)
    poll.start()
    try:
        assert port.reading.wait(2.0), "the poll never reached its read"
        started = time.monotonic()
        landed = smc.stop(priority=True)
        elapsed = time.monotonic() - started
    finally:
        port.release.set()
        poll.join(2.0)
    assert landed is True
    assert elapsed < 0.1, f"the priority stop waited {elapsed:.3f}s"
    assert port.writes[-1][:2] == (b"1ST\r\n", True)


def test_an_ordinary_command_waits_a_bounded_time_for_the_link(monkeypatch):
    """The transaction lock is bounded: a command behind a wedged one gives
    up with `SMC100LinkBusy` -- a read timeout, so `wait_states` retries it
    -- instead of queueing forever. And it writes nothing."""
    busy = getattr(driver, "SMC100LinkBusy", None)
    assert busy is not None, "no bounded transaction lock in this driver"
    assert issubclass(busy, SMC100ReadTimeout)
    monkeypatch.setattr(SMC100, "TRANSACTION_LOCK_TIMEOUT", 0.1)
    port = _WedgedReadPort()
    smc = _smc(port)
    first = threading.Thread(target=smc.get_status, daemon=True)
    first.start()
    try:
        assert port.reading.wait(2.0)
        started = time.monotonic()
        with pytest.raises(busy):
            smc.get_position_deg()
        elapsed = time.monotonic() - started
    finally:
        port.release.set()
        first.join(2.0)
    assert elapsed < 1.0, f"the bounded wait took {elapsed:.2f}s"
    assert port.payloads == [b"1TS?\r\n"], port.payloads


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
