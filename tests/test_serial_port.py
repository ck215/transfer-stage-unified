"""The station's one transport: the write path, the locks, and the truth it
tells about the link.

Ported from `tests/hardware/test_serial.py`, `test_serial6_async_connect.py`,
`test_serial12_print_and_flush.py`, `test_serial16_error_routing.py`,
`test_serial_flush.py`, `test_temp17_write_ordering.py` and the transport half
of `tests/core/test_transport_truth.py`. Every one of those encodes a bug that
comes back if the behaviour is not carried over, so they are adapted to the
new names rather than rewritten from the contract.

**No test here opens a real port.** `devices.serial_port.pyserial` is
replaced with a double in every test that needs hardware, and the simulated
path needs none.
"""
import threading
import time

import pytest

from devices import serial_port as mod
from devices.serial_port import (ConnectionState, SerialPort,
                                         SimulatedPort, TransportError,
                                         list_ports, query)

PORT = "/dev/ttyFAKE0"


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------

class FakeHandle:
    """A pyserial-shaped handle that records frames and detects overlap.

    `overlaps` is the whole point of the TEMP-17/SERIAL-23 tests: it counts
    the times two threads were inside `write()` at once, which on real
    firmware is a swallowed stop or an `atof(NULL)`.
    """

    def __init__(self, write_delay=0.0, delay_when=None):
        self.is_open = True
        self.frames = []
        self.calls = []            # every method name, in order
        self.overlaps = 0
        self.input_resets = 0
        self.write_error = None
        self.read_error = None
        self.reply_on_write = None   # what the device answers a write with
        self.close_error = None
        self.flush_blocks = None   # an Event a wedged tcdrain waits on
        self.write_delay = write_delay
        self._delay_when = delay_when or (lambda payload: True)
        self._inside = 0
        self._guard = threading.Lock()
        self._buf = b""

    # -- pyserial surface --
    @property
    def in_waiting(self):
        return len(self._buf)

    def write(self, payload):
        self.calls.append("write")
        if self.write_error is not None:
            raise self.write_error
        with self._guard:
            self._inside += 1
            if self._inside > 1:
                self.overlaps += 1
        try:
            if self.write_delay and self._delay_when(payload):
                time.sleep(self.write_delay)
            self.frames.append(bytes(payload))
            if self.reply_on_write is not None:
                self._buf += self.reply_on_write
        finally:
            with self._guard:
                self._inside -= 1
        return len(payload)

    def read(self, size=1):
        self.calls.append("read")
        if self.read_error is not None:
            raise self.read_error
        out, self._buf = self._buf[:size], self._buf[size:]
        return out

    def read_all(self):
        out, self._buf = self._buf, b""
        return out

    def reset_input_buffer(self):
        self.calls.append("reset_input_buffer")
        self.input_resets += 1
        self._buf = b""

    def reset_output_buffer(self):
        self.calls.append("reset_output_buffer")

    def flush(self):
        self.calls.append("flush")
        if self.flush_blocks is not None:
            self.flush_blocks.wait(10)

    def close(self):
        self.calls.append("close")
        self.is_open = False
        if self.close_error is not None:
            raise self.close_error

    # -- test helper --
    def feed(self, data):
        self._buf += data


def _fake_pyserial(handle, opens):
    class FakePySerial:
        SerialException = OSError
        SerialTimeoutException = OSError

        @staticmethod
        def Serial(*args, **kwargs):
            if args:  # `query` opens positionally, like the code it replaces
                kwargs = dict(kwargs, port=args[0])
            opens.append(kwargs)
            if isinstance(handle, Exception):
                raise handle
            return handle

    return FakePySerial


@pytest.fixture
def build(monkeypatch):
    """Build an OPEN transport over a fake handle. `handshake=False` by
    default so nothing waits out a bootloader; the handshake has its own
    file, on a fake clock."""
    opens = []

    def _build(handle=None, open_it=True, **kwargs):
        handle = FakeHandle() if handle is None else handle
        monkeypatch.setattr(mod, "pyserial", _fake_pyserial(handle, opens))
        kwargs.setdefault("handshake", False)
        port = SerialPort(PORT, **kwargs)
        if open_it:
            port.open()
            assert port.wait_open(2.0), "the connect worker never finished"
        return port, handle

    _build.opens = opens
    return _build


def _hold_transaction_lock(port, seconds):
    """Hold `_lock` the way a reader's blocking read does."""
    holding = threading.Event()

    def _run():
        with port._lock:
            holding.set()
            time.sleep(seconds)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert holding.wait(1.0), "the lock holder never started"
    return thread


# --------------------------------------------------------------------------
# Construction does no I/O, and every bound is a real number (ROTATOR-16)
# --------------------------------------------------------------------------

def test_constructing_opens_nothing(build):
    port, handle = build(open_it=False)
    assert build.opens == []
    assert port.state is ConnectionState.CLOSED
    assert port.is_open is False


def test_the_port_is_opened_with_a_bounded_write_timeout(build):
    """ROTATOR-16: pyserial's own default is None -- block forever -- and
    with xonxoff the device can withhold XON exactly when someone is
    pressing FULL STOP."""
    build()
    kwargs = build.opens[-1]
    assert kwargs["write_timeout"] is not None
    assert 0 < kwargs["write_timeout"] < 10
    assert kwargs["timeout"] is not None


@pytest.mark.parametrize("bad", [0, -1, None if False else -0.5, "1"])
def test_an_unbounded_or_nonsense_write_timeout_is_refused(bad):
    with pytest.raises(ValueError):
        SerialPort(PORT, write_timeout=bad)


def test_constructor_overrides_reach_the_driver(build):
    """The SMC100 uses this class as its raw transport: 57600, xonxoff, a
    50 ms read timeout and a 0.2 s write timeout (legacy/src/lib/smc100.py)."""
    build(baud_rate=57600, xonxoff=True, read_timeout=0.05,
          write_timeout=0.2, line_terminator="\r\n")
    kwargs = build.opens[-1]
    assert kwargs["baudrate"] == 57600
    assert kwargs["xonxoff"] is True
    assert kwargs["timeout"] == 0.05
    assert kwargs["write_timeout"] == 0.2
    assert (kwargs["bytesize"], kwargs["parity"], kwargs["stopbits"]) == (8, "N", 1)


# --------------------------------------------------------------------------
# SIM is a device, not a different code path (SERIAL-9, SERIAL-20)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", [None, "None", "SIM"])
def test_a_simulated_name_gives_a_simulated_port(name):
    port = SerialPort(name)
    port.open()
    assert port.state is ConnectionState.SIMULATED
    assert port.status == "simulated"
    assert port.is_open


def test_simulator_writes_go_through_the_same_path_and_are_recorded():
    port = SerialPort("SIM")
    port.open()
    assert port.write(b"e") is True
    assert port.write("a string payload") is True
    assert port.writes == [b"e", b"a string payload"]


def test_the_simulated_port_answers_every_call_the_transport_makes():
    """SERIAL-9 and SERIAL-20 are the same shape: a method the simulated
    port forgot turns SIM back into a separate code path."""
    sim = SimulatedPort()
    for name in ("open", "write", "read", "readline", "reset_input_buffer",
                 "reset_output_buffer", "flush", "close"):
        assert callable(getattr(sim, name)), name
    sim.flush()  # SERIAL-20: must not raise


def test_a_simulated_port_flushes_successfully():
    port = SerialPort("SIM")
    port.open()
    assert port.flush(0.5) is True


def test_a_closed_simulator_refuses_writes_like_a_closed_port():
    port = SerialPort("SIM")
    port.open()
    port.close()
    with pytest.raises(TransportError):
        port.write(b"x")
    assert port.writes == [], "nothing was written, so nothing is recorded"


def test_simulated_reads_return_nothing_rather_than_raising():
    port = SerialPort("SIM")
    port.open()
    assert port.read_line(timeout=0) is None


# --------------------------------------------------------------------------
# The one write path
# --------------------------------------------------------------------------

def test_a_write_puts_exactly_the_bytes_it_was_given_on_the_wire(build):
    port, handle = build()
    assert port.write(b"<80,6.0,2,0.5,0.1,0>") is True
    assert handle.frames == [b"<80,6.0,2,0.5,0.1,0>"]


def test_a_string_payload_is_encoded_and_the_terminator_is_not_appended(build):
    port, handle = build(line_terminator="\r\n")
    port.write("<0,1,2>")
    assert handle.frames == [b"<0,1,2>"], "write() sends what it is given"


def test_writing_to_a_port_that_is_not_open_raises_without_marking_it_lost(build):
    port, _ = build(open_it=False)
    with pytest.raises(TransportError):
        port.write(b"e")
    assert port.state is ConnectionState.CLOSED, (
        "a port that was never opened has not been *lost*")


def test_probe_commands_did_not_come_across(build):
    """SerialPort knows nothing about probes. `send_autonomous_command`,
    `send_manual_mode_command`, `read_position`, `enable` and `disable`
    belong to the model that builds the frame, which then calls `write`."""
    port, _ = build(open_it=False)
    for name in ("send_autonomous_command", "send_manual_mode_command",
                 "read_position", "enable", "disable"):
        assert not hasattr(port, name), f"{name} leaked into the transport"


# --------------------------------------------------------------------------
# abort_if is evaluated INSIDE the lock (TEMP-17)
# --------------------------------------------------------------------------

def test_abort_if_true_writes_nothing_and_returns_false(build):
    port, handle = build()
    assert port.write(b"<1>", abort_if=lambda: True) is False
    assert port.write(b"<2>", abort_if=lambda: False) is True
    assert handle.frames == [b"<2>"]


def test_a_frame_superseded_while_it_waited_for_the_lock_is_not_written(build):
    """TEMP-17's order inversion, at transport level. A settings frame that
    passed its own stop check blocks on `_lock` while a reader holds it; a
    stop is commanded meanwhile. When the lock comes free the stale frame
    must NOT go out -- which is only true if `abort_if` is evaluated inside
    the lock, immediately before the write, rather than by the caller."""
    port, handle = build()
    stopped = threading.Event()
    result = []

    holder = _hold_transaction_lock(port, 0.3)

    def _send():
        result.append(port.write(b"<80,6.0,2,0.5,0.1,0>", abort_if=stopped.is_set))

    sender = threading.Thread(target=_send, daemon=True)
    sender.start()
    time.sleep(0.05)                       # the sender is now blocked on _lock
    assert port.write(b"<0,0,0>", priority=True) is True   # FULL STOP
    stopped.set()
    holder.join(2)
    sender.join(2)

    assert result == [False], "the superseded heating frame was written anyway"
    assert handle.frames == [b"<0,0,0>"], handle.frames


def test_an_aborted_write_still_releases_both_locks(build):
    port, handle = build()
    assert port.write(b"<1>", abort_if=lambda: True) is False
    assert port.write(b"<2>") is True            # would deadlock if it did not
    assert port._write_io_lock.acquire(timeout=0.5)
    port._write_io_lock.release()


# --------------------------------------------------------------------------
# The priority lane (SERIAL-23 / TEMP-17 / DC-18)
# --------------------------------------------------------------------------

def test_a_held_transaction_lock_does_not_hold_up_a_priority_write(build):
    port, handle = build()
    holder = _hold_transaction_lock(port, 1.0)

    started = time.monotonic()
    assert port.write(b"d", priority=True) is True
    elapsed = time.monotonic() - started

    bound = port.PRIORITY_LOCK_TIMEOUT + 0.25
    assert elapsed < bound, f"the stop waited {elapsed:.3f}s (bound {bound:.3f}s)"
    assert handle.frames == [b"d"]
    holder.join(2)


def test_an_ordinary_write_still_waits_for_the_lock(build):
    """Forcing past the transaction lock is the stop path's privilege."""
    port, handle = build()
    order = []
    release = threading.Event()
    holding = threading.Event()

    def _hold():
        with port._lock:
            holding.set()
            order.append("holder-in")
            release.wait(2.0)
            order.append("holder-out")

    threading.Thread(target=_hold, daemon=True).start()
    assert holding.wait(1.0)

    def _ordinary():
        port.write(b"move")
        order.append("write")

    writer = threading.Thread(target=_ordinary, daemon=True)
    writer.start()
    time.sleep(0.15)
    release.set()
    writer.join(2.0)

    assert order == ["holder-in", "holder-out", "write"], order


def test_a_priority_write_never_lands_inside_a_frame_on_the_wire(build):
    """SERIAL-23: both binary firmwares `readBytes` a fixed-size packet
    after 0xAA, so a 'd' written mid-packet is swallowed as payload -- a
    *lost* stop. TEMP-17: a split `<...>` frame reaches `atof(NULL)`."""
    handle = FakeHandle(write_delay=0.12,
                        delay_when=lambda payload: payload[:1] != b"d")
    port, handle = build(handle)

    slow = threading.Thread(
        target=port.write, args=(b"\xaa\x01" + b"\x00" * 40,), daemon=True)
    slow.start()
    time.sleep(0.02)                       # the packet is now inside write()
    port.write(b"d", priority=True)
    slow.join(2)

    assert handle.overlaps == 0, "the stop overlapped an in-flight frame"
    assert [f[:1] for f in handle.frames] == [b"\xaa", b"d"], handle.frames


def test_a_wedged_writer_still_cannot_hold_a_stop_for_long(build):
    """Bounded, so a writer stuck inside `handle.write` cannot detain FULL
    STOP indefinitely: past WRITE_IO_LOCK_TIMEOUT the stop goes anyway."""
    handle = FakeHandle(write_delay=1.0,
                        delay_when=lambda payload: payload[:1] != b"d")
    port, handle = build(handle)

    wedged = threading.Thread(target=port.write, args=(b"<80,6.0>",), daemon=True)
    wedged.start()
    time.sleep(0.02)

    started = time.monotonic()
    port.write(b"d", priority=True)
    elapsed = time.monotonic() - started
    wedged.join(3)

    bound = port.PRIORITY_LOCK_TIMEOUT + port.WRITE_IO_LOCK_TIMEOUT + 0.2
    assert elapsed < bound, f"the stop waited {elapsed:.2f}s (bound {bound:.2f}s)"
    assert b"d" in handle.frames


# --------------------------------------------------------------------------
# Loss is a state, reported once (SERIAL-8)
# --------------------------------------------------------------------------

def test_a_failed_write_marks_the_link_lost_and_raises(build):
    port, handle = build()
    handle.write_error = OSError("unplugged")

    with pytest.raises(TransportError):
        port.write(b"x")

    assert port.state is ConnectionState.LOST
    assert port.status == "lost"
    assert port.is_open is False
    assert "close" in handle.calls, "the handle must be released, not dangling"


def test_a_failed_read_marks_the_link_lost_and_raises(build):
    port, handle = build()
    handle.feed(b"anything")
    handle.read_error = OSError("unplugged")

    with pytest.raises(TransportError):
        port.read_line(timeout=0.2)
    assert port.state is ConnectionState.LOST


def test_the_loss_is_reported_once_not_on_every_later_command(build, monkeypatch):
    """Loss used to be popup spam on a 5 s dedupe while the reported state
    never moved at all."""
    port, handle = build()
    handle.write_error = OSError("unplugged")

    reports = []
    monkeypatch.setattr(mod.events, "warn",
                        lambda title, message, **kw: reports.append(title))

    for _ in range(4):
        with pytest.raises(TransportError):
            port.write(b"x")

    assert reports.count("Connection Lost") == 1, reports


def test_a_write_failure_raises_but_reports_nothing_itself(build, monkeypatch):
    """`write()` never reports: the owning model decides what a failed write
    means, and a caller that sees a reported failure treats it as handled."""
    port, handle = build()
    handle.write_error = OSError("unplugged")
    errors = []
    monkeypatch.setattr(mod.events, "error",
                        lambda *a, **kw: errors.append(a))
    with pytest.raises(TransportError):
        port.write(b"x")
    assert errors == [], "a transport failure is not an acknowledged popup"


def test_an_open_that_fails_ends_lost_not_unverified(build):
    port, _ = build(OSError("no such device"), open_it=False)
    port.open()
    port.wait_open(2.0)
    assert port.state is ConnectionState.LOST
    assert port.is_open is False


def test_writes_do_not_print(build, capsys):
    """SERIAL-12: the old path printed a line per frame at 50 Hz. Frames go
    to the debug FILE now, never to the terminal."""
    port, _ = build()
    for _ in range(200):
        port.write(b"\xaa\x01" + b"\x00" * 40)
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------
# close() (SERIAL-16) and the bounded drain (SERIAL-12)
# --------------------------------------------------------------------------

def test_close_drains_before_it_releases_the_handle(build):
    """POSIX `close()` may discard bytes the OS accepted but never sent, and
    the last thing a model writes is its off-frame."""
    port, handle = build()
    port.write(b"<0,0,0>")
    port.close()
    assert handle.calls.index("flush") < handle.calls.index("close")
    assert port.state is ConnectionState.CLOSED


def test_close_does_not_raise_when_the_handle_fails_to_close(build):
    port, handle = build()
    handle.close_error = OSError("device vanished")
    port.close()                      # SERIAL-16: must not propagate
    assert port.state is ConnectionState.CLOSED


def test_closing_deliberately_is_not_recorded_as_loss(build):
    port, _ = build()
    port.close()
    assert port.state is ConnectionState.CLOSED


def test_a_wedged_drain_gives_up_inside_its_budget(build):
    port, handle = build()
    handle.flush_blocks = threading.Event()
    try:
        started = time.monotonic()
        assert port.flush(0.1) is False
        assert time.monotonic() - started < 0.6
    finally:
        handle.flush_blocks.set()


def test_a_wedged_drain_leaves_no_non_daemon_thread_behind(build):
    port, handle = build()
    handle.flush_blocks = threading.Event()
    try:
        port.flush(0.05)
        alive = [t for t in threading.enumerate()
                 if t.name.startswith("serial-flush") and not t.daemon]
        assert alive == [], alive
    finally:
        handle.flush_blocks.set()


def test_a_failed_drain_does_not_move_the_connection_state(build):
    """A teardown-path query must not raise "Connection Lost" on the way
    out; a write that mattered has already faulted through `write()`."""
    port, handle = build()
    before = port.state
    handle.flush_blocks = threading.Event()
    try:
        assert port.flush(0.05) is False
        assert port.state is before
    finally:
        handle.flush_blocks.set()


def test_the_default_drain_budget_is_bounded_and_short():
    assert 0 < SerialPort.FLUSH_TIMEOUT <= 2.0


def test_flushing_a_closed_port_reports_failure(build):
    port, _ = build()
    port.close()
    assert port.flush(0.1) is False


# --------------------------------------------------------------------------
# read_line
# --------------------------------------------------------------------------

def test_read_line_returns_one_line_without_its_terminator(build):
    port, handle = build()
    handle.feed(b"POS:1,2,3\nPOS:4,5,6\n")
    assert port.read_line(timeout=0.5) == "POS:1,2,3"
    assert port.read_line(timeout=0.5) == "POS:4,5,6"
    assert port.read_line(timeout=0) is None


def test_read_line_strips_a_trailing_carriage_return(build):
    port, handle = build()
    handle.feed(b"DEV: s\r\n")
    assert port.read_line(timeout=0.5) == "DEV: s"


def test_read_line_honours_a_custom_terminator(build):
    port, handle = build(line_terminator="\r\n")
    handle.feed(b"1TS000000A\r\n")
    assert port.read_line(timeout=0.5) == "1TS000000A"


def test_read_line_returns_none_rather_than_blocking_forever(build):
    port, _ = build()
    started = time.monotonic()
    assert port.read_line(timeout=0.1) is None
    assert time.monotonic() - started < 0.6


def test_undecodable_bytes_come_back_as_replacement_characters(build):
    port, handle = build()
    handle.feed(b"PO\xffS\n")
    line = port.read_line(timeout=0.5)
    assert "�" in line, line


def test_a_device_that_never_terminates_a_line_cannot_grow_the_buffer(build):
    port, handle = build()
    handle.feed(b"x" * 200000)
    assert port.read_line(timeout=0.2) is None
    assert len(port._read_buffer) <= SerialPort._READ_BUFFER_LIMIT


def test_reading_a_closed_port_raises(build):
    port, _ = build()
    port.close()
    with pytest.raises(TransportError):
        port.read_line(timeout=0)


# --------------------------------------------------------------------------
# The two module-level helpers Setup calls
# --------------------------------------------------------------------------

class _Comport:
    def __init__(self, device, hwid):
        self.device, self.hwid = device, hwid


def test_list_ports_reports_every_port_unfiltered(monkeypatch):
    class FakeListPorts:
        @staticmethod
        def comports():
            return [_Comport("/dev/ttyUSB0", "USB VID:PID=2341:0043"),
                    _Comport("/dev/cu.Bluetooth-Incoming-Port", None)]

    monkeypatch.setattr(mod, "_list_ports", FakeListPorts)
    assert list_ports() == [
        ("/dev/ttyUSB0", "USB VID:PID=2341:0043"),
        ("/dev/cu.Bluetooth-Incoming-Port", ""),
    ], "no filtering and no sorting: which ports are worth offering is Setup's"


def test_list_ports_is_empty_without_pyserial(monkeypatch):
    monkeypatch.setattr(mod, "_list_ports", None)
    assert list_ports() == []


def test_query_opens_resets_writes_waits_reads_and_always_closes(monkeypatch):
    handle = FakeHandle()
    handle.reply_on_write = b"1ID?\r\n1IDTRA25CC\r\n"   # the SMC100 echoes
    opens = []
    monkeypatch.setattr(mod, "pyserial", _fake_pyserial(handle, opens))

    reply = query(PORT, 57600, b"1ID?\r\n", wait=0.0, xonxoff=True)

    assert reply == "1ID?\r\n1IDTRA25CC\r\n"
    assert opens[-1]["baudrate"] == 57600
    assert opens[-1]["xonxoff"] is True
    assert opens[-1]["timeout"] == 0.2
    assert opens[-1]["write_timeout"] == 0.2
    assert handle.calls[:3] == ["reset_input_buffer", "reset_output_buffer", "write"]
    assert handle.frames == [b"1ID?\r\n"]
    assert handle.is_open is False, "query must always close the port"


def test_query_closes_the_port_even_when_the_write_fails(monkeypatch):
    handle = FakeHandle()
    handle.write_error = OSError("unplugged")
    monkeypatch.setattr(mod, "pyserial", _fake_pyserial(handle, []))
    with pytest.raises(TransportError):
        query(PORT, 57600, b"1ID?\r\n", wait=0.0)
    assert handle.is_open is False


def test_query_without_pyserial_raises_transport_error(monkeypatch):
    monkeypatch.setattr(mod, "pyserial", None)
    with pytest.raises(TransportError):
        query(PORT, 57600, b"1ID?\r\n")


# --------------------------------------------------------------------------
# Diagnostics (Addendum 1): the log FILE, never a view and never the terminal
# --------------------------------------------------------------------------

@pytest.fixture
def log_file(tmp_path):
    mod.events._debug_seen.clear()      # the `every=` cache is global
    path = mod.events.open_file(str(tmp_path))
    yield lambda: open(path, encoding="utf-8").read()
    mod.events.close_file()
    mod.events._debug_seen.clear()


def test_a_priority_write_records_its_lock_wait_and_its_frame(build, log_file,
                                                              capsys):
    port, _ = build()
    holder = _hold_transaction_lock(port, 0.2)
    port.write(b"d", priority=True)
    holder.join(2)
    port.close()

    text = log_file()
    assert "Priority Write" in text
    assert "lock_wait=" in text
    assert b"d".hex() in text, "the frame is not in the log in hex"
    assert "State Change" in text and "Closing" in text
    assert "Priority Write" not in capsys.readouterr().out, (
        "debug must never reach the terminal")


def test_a_streamed_write_is_rate_limited_not_one_line_per_frame(build, log_file,
                                                                 monkeypatch):
    """A jog stream runs at 50 Hz. The log reports a rate, not 50 lines a
    second (Addendum 1). The interval is shortened here so the suppressed
    count -- which lands on the next line emitted, not at the end of the
    burst -- is observable inside a test."""
    monkeypatch.setattr(SerialPort, "WRITE_DEBUG_INTERVAL", 0.05)
    port, _ = build()
    for _ in range(300):
        port.write(b"\xaa\x01" + b"\x00" * 40)
    time.sleep(0.06)
    port.write(b"\xaa\x01" + b"\x00" * 40)

    text = log_file()
    lines = [ln for ln in text.splitlines() if "] Write:" in ln]
    assert 2 <= len(lines) <= 8, f"{len(lines)} lines for 301 frames"
    assert "suppressed" in text, "the rate was not reported"


def test_an_aborted_write_is_never_rate_limited_away(build, log_file):
    port, _ = build()
    for _ in range(3):
        assert port.write(b"<1>", abort_if=lambda: True) is False
    lines = [ln for ln in log_file().splitlines() if "Write Not Sent" in ln]
    assert len(lines) == 3, lines
    assert "aborted" in log_file()


def test_query_does_not_touch_a_port_a_transport_holds(build):
    """Not a transport: it has no priority lane and no abort check, so it
    must never be pointed at a port a SerialPort already owns. Nothing
    enforces that but the docstring -- this test pins the docstring."""
    assert "never be pointed at a port" in query.__doc__
