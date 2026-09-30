"""A lost serial link: the owner's stop goes first, then the port recovers by
itself, and every drop and stall is counted, published and warned about.

Owner, 2026-09-30: "track dropped packets like events and display warnings,
but have a system that can recover properly." This supersedes D-11 for the
AUTOMATIC recovery only; there is still no Reconnect button and no port
re-assignment.

Every test here runs a REAL `SerialPort` over a pyserial double, and a real
probe over that port, so what is asserted is what the wire would see. No
test opens a real port.
"""
import threading
import time

import pytest

from devices import serial_port as mod
from devices.serial_port import ConnectionState, SerialPort, TransportError
from events import events
from model.probe import ProbeMode, StepperProbe

PORT = "/dev/ttyLINK0"
ZERO = b"0,0,0,0,0,0,0,0,0,0,0,0\n"


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------

class FlakyHandle:
    """A pyserial-shaped handle whose next `fail_writes` writes raise.

    `attempts` records every write as (payload, was the handle open), so a
    test can tell a stop that was tried on the live handle from one tried on
    a closed one, or never tried at all.
    """

    def __init__(self, fail_writes=0):
        self.is_open = True
        self.attempts = []
        self.frames = []
        self.calls = []
        self.fail_writes = fail_writes
        self.in_waiting = 0
        self._buf = b""

    def write(self, payload):
        self.attempts.append((bytes(payload), self.is_open))
        self.calls.append(("write", bytes(payload)))
        if self.fail_writes:
            self.fail_writes -= 1
            raise OSError("write failed: device reports readiness to write "
                          "but returned no data")
        self.frames.append(bytes(payload))
        return len(payload)

    def read(self, size=1):
        out, self._buf = self._buf[:size], self._buf[size:]
        self.in_waiting = len(self._buf)
        return out

    def feed(self, data):
        self._buf += data
        self.in_waiting = len(self._buf)

    def reset_input_buffer(self):
        self._buf, self.in_waiting = b"", 0

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.calls.append(("close", None))
        self.is_open = False


class FakePad:
    """The least of the Gamepad surface a probe's pump touches."""

    status = "bound"
    is_hardware = False

    def __init__(self):
        self.is_bound = True
        self.is_gate_open = True
        self.levels = {}
        self.name = "Pad0"
        self.options = ["None", "Pad0"]
        self.log = []

    def open(self):
        pass

    def close(self):
        pass

    def bind(self, name):
        return True

    def set_gate(self, is_open):
        self.is_gate_open = bool(is_open)

    def drain_edges(self):
        return {}


def _pyserial(handles, opens):
    """`Serial(...)` hands out `handles` in order; an Exception is raised."""

    class FakePySerial:
        SerialException = OSError

        @staticmethod
        def Serial(*args, **kwargs):
            opens.append(kwargs)
            item = handles.pop(0) if len(handles) > 1 else handles[0]
            if isinstance(item, Exception):
                raise item
            return item

    return FakePySerial


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def rig(monkeypatch):
    """A real StepperProbe on a real SerialPort over `FlakyHandle`s.

    Yields a builder: `rig(*handles)` -> (probe, port, handles). The first
    handle is the one the port opens with.
    """
    built = []

    def _build(*handles):
        handles = list(handles) or [FlakyHandle()]
        first = handles[0]
        opens = []
        monkeypatch.setattr(mod, "pyserial", _pyserial(handles, opens))
        port = SerialPort(PORT, baud_rate=StepperProbe.BAUD_RATE,
                          handshake=False)
        probe = StepperProbe(port=port, gamepad=FakePad())
        probe.open()
        assert port.wait_open(2.0), "the connect worker never finished"
        built.append(probe)
        return probe, port, first, opens

    yield _build
    for probe in built:
        probe.close()


# --------------------------------------------------------------------------
# L1 -- a stop is attempted before a failed link is closed (BUGFIX_PLAN D5)
# --------------------------------------------------------------------------

def test_a_lost_link_is_stopped_on_the_still_open_handle_before_it_closes(rig):
    """The first transport failure used to drop the handle at once, so the
    owner's `_halt_hardware` then raised "port not open" and no zero frame
    or `'d'` was ever attempted: the stage could drift at the last jog
    value. Now the stop goes out on the handle that is still open, and only
    then is the handle closed."""
    probe, port, handle, _ = rig()
    probe.set_mode("autonomous")
    assert probe.mode is ProbeMode.AUTO
    handle.attempts.clear()
    handle.calls.clear()

    handle.fail_writes = 1
    with pytest.raises(TransportError):
        port.write(b"1,1,1,0,400.0,0,0,5.0,0,0,0,1\n")

    assert _wait_for(lambda: ("close", None) in handle.calls), \
        "the lost handle was never closed"
    after_failure = handle.attempts[1:]
    stop_bytes = [payload for payload, _ in after_failure]
    assert ZERO in stop_bytes and b"d" in stop_bytes, (
        f"no stop was attempted on the lost link: {handle.attempts}")
    assert all(was_open for _, was_open in after_failure), (
        "the stop was attempted on a handle already closed")
    close_at = handle.calls.index(("close", None))
    d_at = handle.calls.index(("write", b"d"))
    assert d_at < close_at, "the handle closed before the stop went out"


def test_a_lost_link_leaves_the_mode_disabled_not_fault(rig):
    """FAULT is the needs-a-person latch and would block the automatic
    recovery; a lost link leaves the mode through DISABLED."""
    probe, port, handle, _ = rig()
    probe.set_mode("autonomous")
    handle.fail_writes = 1
    with pytest.raises(TransportError):
        port.write(b"1,1,1,0,400.0,0,0,5.0,0,0,0,1\n")
    assert _wait_for(lambda: probe.mode is ProbeMode.DISABLED), probe.mode
    assert probe.is_faulted is False, probe.fault


def test_a_failed_stop_attempt_on_a_lost_link_is_logged_never_raised(rig):
    """Every write fails: the stop cannot land. The attempt is bounded and
    logged, the handle still closes, and nothing raises into the caller of
    the write that found the loss beyond its own TransportError."""
    probe, port, handle, _ = rig()
    probe.set_mode("autonomous")
    handle.fail_writes = 10 ** 6
    with pytest.raises(TransportError):
        port.write(b"1,1,1,0,400.0,0,0,5.0,0,0,0,1\n")
    assert _wait_for(lambda: ("close", None) in handle.calls)
    assert probe.mode is ProbeMode.DISABLED
    assert probe.is_faulted is False


def test_nothing_but_a_stop_reaches_a_lost_links_handle(rig):
    """While the handle is kept for the owner's stop, an ordinary (motion)
    write must not go out on it: only the priority lane may."""
    probe, port, handle, _ = rig()
    gate = threading.Event()
    real = probe._on_link_lost

    def _slow_owner_stop(why):
        gate.wait(2.0)
        return real(why)

    port.set_link_handlers(on_lost=_slow_owner_stop,
                           on_restored=probe._on_link_restored)
    handle.fail_writes = 1
    with pytest.raises(TransportError):
        port.write(b"x")
    with pytest.raises(TransportError):
        port.write(b"\xaa\x01jog")
    gate.set()
    assert _wait_for(lambda: ("close", None) in handle.calls)
    assert (b"\xaa\x01jog", True) not in handle.attempts


def test_a_jog_write_that_finds_the_link_lost_does_not_fault_the_probe(rig):
    """The manual pump's write is the one most likely to find the loss. It
    must not turn a lost link into FAULT through the pump's fault hook."""
    probe, port, handle, _ = rig()
    probe.set_mode("manual")
    assert probe.mode is ProbeMode.MANUAL
    handle.fail_writes = 1
    assert probe._send_jog({"axis_x": 0.5}) is False
    assert _wait_for(lambda: probe.mode is ProbeMode.DISABLED), probe.mode
    assert probe.is_faulted is False


# --------------------------------------------------------------------------
# L2 -- the link recovers by itself (reverses D-11 for automatic recovery)
# --------------------------------------------------------------------------

class Recorder:
    """The port's state transitions, and the info events published."""

    def __init__(self, port, monkeypatch):
        self.states = []
        self.infos = []
        real = port._note_state

        def _note(old, new, why):
            self.states.append(new)
            return real(old, new, why)

        monkeypatch.setattr(port, "_note_state", _note)
        events.subscribe(self._seen)

    def _seen(self, event):
        if event.severity == "info":
            self.infos.append((event.title, event.message, event.source))

    def close(self):
        events.unsubscribe(self._seen)


def _lose(port, handle):
    handle.fail_writes = 1
    with pytest.raises(TransportError):
        port.write(b"1,1,1,0,400.0,0,0,5.0,0,0,0,1\n")


def test_the_link_reconnects_by_itself_on_the_backoff_schedule(rig, monkeypatch):
    """Open fails twice, then succeeds on the third try: the state goes
    LOST -> RECONNECTING -> UNVERIFIED (this port has no handshake), the
    waits are 1, 2, 4 s, the owner publishes Connection Restored, and the
    probe stays DISABLED until the operator re-enters a mode."""
    h1, h2 = FlakyHandle(), FlakyHandle()
    probe, port, _, opens = rig(h1, OSError("gone"), OSError("still gone"), h2)
    probe.set_mode("autonomous")
    delays = []
    monkeypatch.setattr(port, "_reconnect_sleep",
                        lambda seconds: delays.append(seconds) and False)
    rec = Recorder(port, monkeypatch)
    try:
        _lose(port, h1)
        assert _wait_for(lambda: port.state is ConnectionState.UNVERIFIED), \
            (port.state, rec.states)
        assert _wait_for(lambda: any(t == "Connection Restored"
                                     for t, _, _ in rec.infos)), rec.infos
    finally:
        rec.close()
    assert rec.states[:2] == [ConnectionState.LOST, ConnectionState.RECONNECTING]
    assert rec.states[-1] is ConnectionState.UNVERIFIED
    assert delays == [1.0, 2.0, 4.0], delays
    assert len(opens) == 4, "one initial open and three reconnect attempts"
    title, message, source = next(i for i in rec.infos
                                  if i[0] == "Connection Restored")
    assert message == (f"Stepper Probe is back on {PORT}. Re-enable it when "
                       "you are ready.")
    assert source == "Stepper Probe"
    assert probe.mode is ProbeMode.DISABLED
    assert port.is_open and port._handle is h2
    # Nothing restarted by itself: no enable reached the new handle.
    assert b"e" not in h2.frames


def test_the_backoff_goes_on_every_ten_seconds_after_eight(rig, monkeypatch):
    probe, port, h1, _ = rig(FlakyHandle(), OSError("gone"))
    delays = []

    def _sleep(seconds):
        delays.append(seconds)
        if len(delays) >= 7:
            port.close()
            return True
        return False

    monkeypatch.setattr(port, "_reconnect_sleep", _sleep)
    _lose(port, h1)
    assert _wait_for(lambda: len(delays) >= 7 and port.state is ConnectionState.CLOSED)
    time.sleep(0.05)
    assert delays == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0], delays


def test_close_while_reconnecting_stops_the_loop(rig, monkeypatch):
    """A deliberate `close()` cancels the loop through the generation
    counter, and wakes it out of its wait at once."""
    probe, port, h1, opens = rig(FlakyHandle(), OSError("gone"))
    monkeypatch.setattr(port, "RECONNECT_BACKOFF", (0.01,))
    monkeypatch.setattr(port, "RECONNECT_EVERY", 0.01)
    _lose(port, h1)
    assert _wait_for(lambda: port.state is ConnectionState.RECONNECTING)
    assert _wait_for(lambda: len(opens) >= 3)
    port.close()
    assert port.state is ConnectionState.CLOSED
    time.sleep(0.05)
    settled = len(opens)
    time.sleep(0.1)
    assert len(opens) == settled, "the reconnect loop outlived close()"
    assert port.state is ConnectionState.CLOSED
    assert not any(t.name.startswith("serial-recover") and t.is_alive()
                   for t in threading.enumerate())


def test_a_reconnecting_probe_refuses_motion_and_says_why(rig, monkeypatch):
    probe, port, h1, _ = rig(FlakyHandle(), OSError("gone"))
    release = threading.Event()
    monkeypatch.setattr(port, "_reconnect_sleep",
                        lambda seconds: release.wait(2.0) and False)
    _lose(port, h1)
    assert _wait_for(lambda: port.state is ConnectionState.RECONNECTING)
    from result import Refused
    for command in (lambda: probe.set_mode("autonomous"), probe.step):
        with pytest.raises(Refused) as refused:
            command()
        assert "reconnect" in str(refused.value.reason).lower(), refused.value.reason
    assert probe.mode is ProbeMode.DISABLED
    port.close()
    release.set()


def test_the_reconnect_loop_never_holds_the_transaction_lock_or_blocks_a_stop(
        rig, monkeypatch):
    probe, port, h1, _ = rig(FlakyHandle(), OSError("gone"))
    release = threading.Event()
    monkeypatch.setattr(port, "_reconnect_sleep",
                        lambda seconds: release.wait(2.0) and False)
    _lose(port, h1)
    assert _wait_for(lambda: port.state is ConnectionState.RECONNECTING)
    assert port._lock.acquire(timeout=0.05), "the loop holds the transaction lock"
    port._lock.release()
    started = time.monotonic()
    probe.halt()            # no handle: unconfirmed, but it must not wait
    assert time.monotonic() - started < 0.5
    port.close()
    release.set()


# --------------------------------------------------------------------------
# L3 -- dropped packets and stalls are counted, published and warned about
# --------------------------------------------------------------------------

class Warnings:
    def __init__(self):
        self.seen = []
        events.subscribe(self.seen.append)

    def titled(self, title, severity=None):
        return [e for e in self.seen if e.title == title
                and (severity is None or e.severity == severity)]

    def close(self):
        events.unsubscribe(self.seen.append)


def test_a_stalled_position_stream_warns_once_and_counts_on_recovery(rig, monkeypatch):
    """The link is up but no POS line arrives: one warning per episode,
    `stalled` true; the first line after it ends the episode and counts
    it."""
    monkeypatch.setattr(StepperProbe, "STREAM_STALL_SECONDS", 0.15)
    events.clear()
    seen = Warnings()
    try:
        probe, port, handle, _ = rig()
        assert _wait_for(lambda: probe.state["link"]["stalled"], 2.0)
        time.sleep(0.2)
        stalled = seen.titled("Position Stream Stalled", "warning")
        assert len(stalled) == 1, [e.text for e in seen.seen]
        assert stalled[0].count == 1
        assert stalled[0].message.startswith("Stepper Probe has sent no position")
        assert "the link is up. Check the board." in stalled[0].message
        assert probe.state["link"]["stalls"] == 0
        handle.feed(b"POS:1,2,3\n")
        assert _wait_for(lambda: probe.position == (1, 2, 3))
        link = probe.state["link"]
        assert link["stalled"] is False and link["stalls"] == 1
    finally:
        seen.close()


def test_a_simulated_port_never_stalls(monkeypatch):
    monkeypatch.setattr(StepperProbe, "STREAM_STALL_SECONDS", 0.05)
    probe = StepperProbe(port="SIM", gamepad=FakePad())
    probe.open()
    try:
        time.sleep(0.2)
        assert probe.state["link"]["stalled"] is False
    finally:
        probe.close()


def test_the_link_counters_reach_the_state_after_a_loss_and_a_recovery(
        rig, monkeypatch):
    import re
    h1, h2 = FlakyHandle(), FlakyHandle()
    probe, port, _, _ = rig(h1, h2)
    monkeypatch.setattr(port, "_reconnect_sleep", lambda seconds: False)
    _lose(port, h1)
    assert _wait_for(lambda: port.state is ConnectionState.UNVERIFIED)
    link = probe.state["link"]
    assert link["status"] == "unverified"
    assert link["losses"] == 1 and link["reconnects"] == 1
    assert re.fullmatch(r"\d\d:\d\d:\d\d", link["last_loss"])
