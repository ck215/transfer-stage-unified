"""The identity handshake and the connection states around it.

Ported from `tests/hardware/test_serial17_handshake.py` (the handshake is a
handshake, not a flood, and a truncated `DEV:` is not an identity),
`tests/hardware/test_serial6_async_connect.py` (opening does not block the
caller, and a connect in flight can neither delay nor swallow a stop) and the
ConnectionState half of `tests/core/test_transport_truth.py`.

**The bytes and the timing are the firmware's, not ours.** `s\\n`, the 1.5 s
bootloader wait, the 0.25 s ping interval and the 3 s window are pinned here
because the boards are untouched by the rebuild.

The clock in the first half is virtual, so a handshake costs milliseconds
instead of the four-and-a-half real seconds it takes on a bench -- and "how
many pings in one second of waiting" becomes an assertion rather than a
stopwatch race. No test opens a real port.
"""
import threading
import time

import pytest

from station.devices import serial_port as mod
from station.devices.serial_port import ConnectionState, SerialPort, TransportError

PORT = "/dev/ttyFAKE0"


class Clock:
    """Virtual monotonic time. `sleep` is the only thing that advances it."""

    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, float(seconds))


class ScriptedPort:
    """A pyserial handle that delivers scripted bytes at scripted times."""

    def __init__(self, clock, deliveries=()):
        self.clock = clock
        self.pending = sorted(deliveries)      # [(virtual_time, payload)]
        self.is_open = True
        self.writes = []                       # [(when, payload)]
        self.input_resets = []                 # [when]
        self._buf = b""

    def _pump(self):
        while self.pending and self.pending[0][0] <= self.clock.now:
            self._buf += self.pending.pop(0)[1]

    @property
    def in_waiting(self):
        self._pump()
        return len(self._buf)

    def read(self, size=1):
        self._pump()
        out, self._buf = self._buf[:size], self._buf[size:]
        return out

    def write(self, payload):
        self.writes.append((self.clock.now, payload))
        return len(payload)

    def reset_input_buffer(self):
        self.input_resets.append(self.clock.now)
        self._buf = b""

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.is_open = False


def _fake_pyserial(handle):
    class FakePySerial:
        SerialException = OSError
        SerialTimeoutException = OSError

        @staticmethod
        def Serial(*args, **kwargs):
            return handle

    return FakePySerial


@pytest.fixture
def handshake(monkeypatch):
    """An opened transport over a scripted port and a virtual clock."""

    def _build(deliveries=(), start=1000.0, **kwargs):
        clock = Clock(start)
        handle = ScriptedPort(clock, deliveries)
        monkeypatch.setattr(mod, "time", clock)
        monkeypatch.setattr(mod, "pyserial", _fake_pyserial(handle))
        port = SerialPort(PORT, **kwargs)
        port.open()
        # Real seconds: `Thread.join` knows nothing about the virtual clock,
        # and the work behind it is a handful of Python-level iterations.
        assert port.wait_open(timeout=5.0), "the connect worker never finished"
        return port, handle, clock

    return _build


def _pings(handle):
    return [when for when, payload in handle.writes if payload == b"s\n"]


# --------------------------------------------------------------------------
# The wire contract: these are the firmware's numbers.
# --------------------------------------------------------------------------

def test_the_handshake_bytes_and_timings_match_the_firmware():
    assert SerialPort.PING == b"s\n"
    assert SerialPort.BOOTLOADER_WAIT == 1.5
    assert SerialPort.PING_INTERVAL == 0.25
    assert SerialPort.HANDSHAKE_TIMEOUT == 3.0


# --------------------------------------------------------------------------
# A truncated `DEV:` is not an identity (SERIAL-17, SERIAL-7)
# --------------------------------------------------------------------------

def test_a_dev_prefix_split_across_reads_is_not_parsed_as_a_device(handshake):
    """`DEV:` arrives, then ` s\\n` 400 ms later. The old break condition
    fired on the first four bytes and parsed the empty string as the id."""
    port, _, _ = handshake([(1001.6, b"DEV:"), (1002.0, b" s\n")])
    assert port.identity == "s"
    assert port.state is ConnectionState.VERIFIED
    assert port.status == "verified"


def test_bootloader_garbage_before_the_reply_is_ignored(handshake):
    port, _, _ = handshake([
        (1001.6, b"\x00\xff\xfe rubbish from the bootloader"),
        (1002.0, b"\r\nDEV: s\r\n"),
    ])
    assert port.identity == "s"


def test_a_complete_reply_in_one_read_still_works(handshake):
    port, _, _ = handshake([(1001.7, b"DEV: t\r\n")])
    assert port.identity == "t"
    assert port.state is ConnectionState.VERIFIED


# --------------------------------------------------------------------------
# The ping is a handshake, not a flood (SERIAL-17)
# --------------------------------------------------------------------------

def test_the_ping_does_not_flood_while_waiting_for_a_slow_board(handshake):
    """Every ping is answered, so twenty of them leave nineteen replies
    queued behind the one that was read."""
    port, handle, _ = handshake([(1002.5, b"DEV: s\r\n")])
    assert port.state is ConnectionState.VERIFIED
    assert len(_pings(handle)) <= 8, f"{len(_pings(handle))} pings in one second"


def test_the_ping_does_not_flood_a_board_that_never_answers(handshake):
    port, handle, _ = handshake([])
    assert port.state is ConnectionState.UNVERIFIED
    assert len(_pings(handle)) <= 16


def test_pings_are_spaced_by_at_least_the_declared_interval(handshake):
    _, handle, _ = handshake([])
    times = _pings(handle)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(gap >= SerialPort.PING_INTERVAL - 1e-9 for gap in gaps), gaps


def test_the_input_buffer_is_drained_after_the_match(handshake):
    """The replies to pings already in flight are not left for the owning
    model's reader to wade through."""
    port, handle, _ = handshake([(1002.0, b"DEV: s\r\nDEV: s\r\nDEV: s\r\n")])
    assert port.identity == "s"
    assert any(when >= 1002.0 for when in handle.input_resets), (
        "the queued replies were left in the buffer")


def test_the_handshake_gives_up_within_its_declared_window(handshake):
    _, _, clock = handshake([])
    elapsed = clock.now - 1000.0
    budget = SerialPort.BOOTLOADER_WAIT + SerialPort.HANDSHAKE_TIMEOUT + 0.5
    assert elapsed <= budget, f"the handshake ran for {elapsed}s"


# --------------------------------------------------------------------------
# An opened port proves nothing (SERIAL-7)
# --------------------------------------------------------------------------

def test_a_silent_board_is_unverified_not_verified(handshake):
    port, _, _ = handshake([])
    assert port.identity is None
    assert port.state is ConnectionState.UNVERIFIED
    assert port.state.is_usable, "blind is still usable; it is not a failure"
    assert port.is_open


def test_unverified_is_not_verified(handshake):
    port, _, _ = handshake([])
    assert port.state is not ConnectionState.VERIFIED


# --------------------------------------------------------------------------
# handshake=False: the SMC100 uses this class as a raw transport
# --------------------------------------------------------------------------

def test_handshake_false_sends_nothing_and_waits_for_nothing(handshake):
    port, handle, clock = handshake([], handshake=False)
    assert handle.writes == [], "a device that does not speak `s\\n` was pinged"
    assert clock.now == 1000.0, "the bootloader wait ran for a non-Arduino"
    assert port.state is ConnectionState.UNVERIFIED
    assert port.is_open


def test_a_protocol_driver_can_vouch_for_an_unverified_link(handshake):
    port, _, _ = handshake([], handshake=False)
    assert port.mark_verified("SMC100 Rotator") is True
    assert port.state is ConnectionState.VERIFIED
    assert port.identity == "SMC100 Rotator"
    assert port.mark_verified("again") is False, "only UNVERIFIED -> VERIFIED"


def test_marking_a_closed_link_verified_is_refused(handshake):
    port, _, _ = handshake([], handshake=False)
    port.close()
    assert port.mark_verified("SMC100") is False
    assert port.state is ConnectionState.CLOSED


# --------------------------------------------------------------------------
# SERIAL-6: opening does not block, and a connect in flight cannot delay or
# swallow a stop. Real clock here -- the point is elapsed wall time.
# --------------------------------------------------------------------------

class GatedPort:
    """A handle whose *ping* write can be held open on command, while every
    other write goes straight through. Deliberately narrower than a slow
    port: it isolates the handshake's own I/O, which runs under `_lock`."""

    def __init__(self, release_after=None):
        self.is_open = True
        self.in_waiting = 0
        self.writes = []
        self.ping_started = threading.Event()
        self._release = threading.Event()
        self._release_after = release_after

    def write(self, payload):
        if payload == b"s\n" and not self._release.is_set():
            self.ping_started.set()
            if self._release_after is not None:
                self._release.wait(self._release_after)
                self._release.set()
            else:
                self._release.wait()
        self.writes.append(payload)
        return len(payload)

    def read(self, _size=1):
        return b""

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    def release(self):
        self._release.set()


@pytest.fixture
def gated(monkeypatch):
    def _build(release_after=None):
        handle = GatedPort(release_after=release_after)
        monkeypatch.setattr(mod, "pyserial", _fake_pyserial(handle))
        monkeypatch.setattr(SerialPort, "BOOTLOADER_WAIT", 0.0)
        # GatedPort never answers, so every handshake here runs to its own
        # timeout. Shrunk so the file stays fast without changing what is
        # proven; the `release_after` values below are smaller than this.
        monkeypatch.setattr(SerialPort, "HANDSHAKE_TIMEOUT", 0.5)
        port = SerialPort(PORT)
        return port, handle

    return _build


def test_open_returns_before_the_handshake_finishes(gated):
    """SERIAL-6: this used to run on the GUI thread, or on the HTTP handler
    building a model, for up to 4.5 s per device."""
    port, handle = gated(release_after=0.4)

    started = time.monotonic()
    port.open()
    elapsed = time.monotonic() - started

    assert elapsed < 0.2, (
        f"open() took {elapsed:.3f}s -- the handshake is back on the "
        f"caller's thread (SERIAL-6 regression)")
    assert port.state is ConnectionState.CONNECTING

    assert port.wait_open(timeout=3.0)
    assert port.state is ConnectionState.UNVERIFIED


def test_opening_the_simulator_does_not_block_either():
    port = SerialPort("SIM")
    started = time.monotonic()
    port.open()
    assert time.monotonic() - started < 0.2
    assert port.wait_open(0) is True


def test_a_priority_write_is_not_delayed_by_an_in_flight_handshake(gated):
    """The safety property that matters most: FULL STOP's 'd' reaches the
    wire while a handshake ping is provably stuck holding `_lock`."""
    port, handle = gated(release_after=None)   # never releases on its own
    port.open()
    assert handle.ping_started.wait(1.0), "the handshake never reached its ping"
    assert port.state is ConnectionState.CONNECTING
    assert port.is_open, "a stop must not wait for the handshake to finish"

    started = time.monotonic()
    assert port.write(b"d", priority=True) is True
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, (
        f"a priority write waited {elapsed:.3f}s behind an in-flight connect")
    assert b"d" in handle.writes
    handle.release()
    assert port.wait_open(timeout=3.0)


def test_a_write_failure_during_connect_still_marks_the_link_lost(gated):
    """SERIAL-8's contract does not get a CONNECTING exemption."""
    port, handle = gated(release_after=None)
    port.open()
    assert handle.ping_started.wait(1.0)

    def _raise(_payload):
        raise OSError("unplugged mid-connect")

    handle.write = _raise
    with pytest.raises(TransportError):
        port.write(b"e", priority=True)
    assert port.state is ConnectionState.LOST

    handle.release()
    port.wait_open(timeout=3.0)
    assert port.state is ConnectionState.LOST, (
        "a stale handshake result resurrected a lost link")


def test_close_during_connect_is_not_overwritten_by_a_late_result(gated):
    port, handle = gated(release_after=0.3)
    port.open()
    assert handle.ping_started.wait(1.0)
    assert port.state is ConnectionState.CONNECTING

    port.close()
    assert port.state is ConnectionState.CLOSED

    port.wait_open(timeout=3.0)
    assert port.state is ConnectionState.CLOSED, (
        "a handshake result arriving after close() resurrected the link")


def test_reopening_after_a_close_starts_a_fresh_connect(gated):
    port, handle = gated(release_after=0.05)
    port.open()
    assert port.wait_open(timeout=3.0)
    port.close()
    assert port.state is ConnectionState.CLOSED

    handle.is_open = True          # the cable went back in
    port.open()
    assert port.wait_open(timeout=3.0)
    assert port.state is ConnectionState.UNVERIFIED


def test_open_is_idempotent_while_a_connect_is_in_flight(gated):
    port, handle = gated(release_after=0.3)
    port.open()
    assert handle.ping_started.wait(1.0)
    first = port._connect_thread
    port.open()
    assert port._connect_thread is first, "a second connect worker was started"
    handle.release()
    port.wait_open(timeout=3.0)
