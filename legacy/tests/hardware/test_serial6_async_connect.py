"""SERIAL-6 -- the constructor no longer blocks the caller for the identity
handshake, and a connect in flight cannot delay or swallow a stop.

Before this fix, `serial.__init__` did `time.sleep(BOOTLOADER_WAIT)` (1.5s)
then looped the identity ping for up to `HANDSHAKE_TIMEOUT` (3.0s) --
synchronously, on whatever thread called the constructor. That is the GUI
thread building models at Tk/PySide launch (the window is withdrawn while
this runs) and an HTTP handler thread for the Web setup wizard. N devices
meant up to ~4.5s x N frozen.

`_connect_worker` moves the boot wait and handshake to a background daemon
thread; the constructor returns as soon as the port is open.
`connection_state` stays CONNECTING (already a real value -- the Web badge
already mapped it) until the worker finishes. Nothing that reaches the
hardware -- `enable()`, `disable()`, `write_command()` -- keys off
`connection_state`; they all key off `ser.is_open`, so a command sent while
still CONNECTING is not held up by it.

The one safety property that matters most: a stop (`disable()`, a priority
`write_command`) must not be delayed or swallowed by a handshake in flight.
`GatedPort` proves that directly by blocking the handshake's own ping
mid-write, on the same lock a priority write would otherwise contend for.
"""

import threading
import time

import pytest

import controller.serial as serial_mod
from controller.serial import ConnectionState, TransportError, serial as SerialTransport


class GatedPort:
    """A pyserial-shaped double whose *ping* write can be held open on
    command, while every other write goes straight through.

    This is deliberately narrower than a generic slow port: it isolates
    exactly the thing SERIAL-6 is about (the handshake's own I/O, running
    under `_lock`) from everything else, so a test can assert "the ping is
    provably stuck" and "a different write still landed" at the same time.
    """

    def __init__(self, release_after=None):
        self.is_open = True
        self.in_waiting = 0
        self.writes = []
        self.ping_write_started = threading.Event()
        self._release = threading.Event()
        self._release_after = release_after

    def write(self, payload):
        if payload == b"s\n" and not self._release.is_set():
            self.ping_write_started.set()
            if self._release_after is not None:
                # A timed release, not just a timed wait: without this, every
                # later ping re-blocks too, since `_release` was never
                # actually set.
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
def build(monkeypatch):
    """Build a transport over a `GatedPort`, with no real bootloader wait so
    the handshake reaches its first ping (and blocks on the gate) almost
    immediately.
    """
    def _build(release_after=None):
        port = GatedPort(release_after=release_after)

        class FakePySerial:
            SerialException = Exception
            SerialTimeoutException = Exception

            @staticmethod
            def Serial(*args, **kwargs):
                return port

        monkeypatch.setattr(serial_mod, "pyserial", FakePySerial)
        monkeypatch.setattr(SerialTransport, "BOOTLOADER_WAIT", 0.0)
        # GatedPort never sends a `DEV:` reply, so every one of these tests'
        # handshakes runs to its own timeout. Shrunk so the file stays fast
        # without changing what is being proven -- `release_after` values
        # below are chosen smaller than this.
        monkeypatch.setattr(SerialTransport, "HANDSHAKE_TIMEOUT", 0.5)
        return port

    return _build


# --------------------------------------------------------------------------
# Construction returns promptly; the handshake keeps running behind it.
# --------------------------------------------------------------------------


def test_construction_returns_before_the_handshake_finishes(build):
    """A handshake stuck on its first ping must not be on the return path
    of `__init__`. `release_after=0.4` bounds the stall instead of hanging
    the test outright if this regresses; the assertion is what proves the
    constructor did not wait for it (0.4s is generous headroom over any
    real construction cost)."""
    port = build(release_after=0.4)

    started = time.time()
    t = SerialTransport("COM9")
    elapsed = time.time() - started

    assert elapsed < 0.2, (
        f"construction took {elapsed:.3f}s -- the handshake is back on the "
        f"caller's thread (SERIAL-6 regression)")
    assert t.connection_state == ConnectionState.CONNECTING

    assert t.wait_connected(timeout=2.0), "handshake thread never finished"
    assert t.connection_state == ConnectionState.UNVERIFIED, (
        "GatedPort never answers DEV:, so an honest handshake ends UNVERIFIED")


def test_wait_connected_returns_true_immediately_for_sim():
    """SIM has no handshake to wait for; `wait_connected` must not invent
    one, per the same rule that keeps SIM a single code path (SERIAL-9)."""
    t = SerialTransport("SIM")
    assert t.wait_connected(timeout=0) is True


def test_wait_connected_returns_true_immediately_when_the_port_never_opened():
    """The pyserial.Serial() call itself failing means there was never a
    handshake thread to start; `wait_connected` must not hang waiting on
    one that does not exist."""
    class FailingPySerial:
        class SerialException(Exception):
            pass
        SerialTimeoutException = Exception

        @staticmethod
        def Serial(*args, **kwargs):
            raise FailingPySerial.SerialException("no such device")

    import controller.serial as mod
    real_pyserial = mod.pyserial
    mod.pyserial = FailingPySerial
    try:
        t = SerialTransport("COM9")
    finally:
        mod.pyserial = real_pyserial

    assert t.connection_state == ConnectionState.LOST
    assert t.wait_connected(timeout=0) is True


# --------------------------------------------------------------------------
# The core safety property: a stop is not delayed or swallowed by a connect
# in flight (safety-pattern.md; the rotator's serial-lock-blocked
# emergency_stop is the named shape this must not repeat).
# --------------------------------------------------------------------------


def test_a_priority_write_is_not_delayed_by_an_in_flight_handshake(build):
    """FULL STOP's 'd' must reach the wire while a handshake ping is
    provably stuck holding `_lock` for its own write -- proving the
    priority path's `PRIORITY_LOCK_TIMEOUT` forced-through write actually
    forces through, rather than merely being untested."""
    port = build(release_after=None)  # never releases on its own

    t = SerialTransport("COM9")
    assert port.ping_write_started.wait(1.0), (
        "the handshake never reached its ping -- test setup is broken")
    assert t.connection_state == ConnectionState.CONNECTING

    started = time.time()
    t.write_command(b"d", priority=True)
    elapsed = time.time() - started

    assert elapsed < 0.5, (
        f"a priority write waited {elapsed:.3f}s behind an in-flight "
        f"connect -- a stop must never be delayed by a handshake")
    assert b"d" in port.writes, "the priority write must still have landed"

    port.release()
    assert t.wait_connected(timeout=2.0)


def test_disable_reaches_the_hardware_while_still_connecting(build):
    """`disable()` -- the model-level path FULL STOP actually calls -- must
    work the same way: it only checks `is_open()`, never `connection_state`,
    so it is not gated on the handshake finishing."""
    port = build(release_after=None)

    t = SerialTransport("COM9")
    assert port.ping_write_started.wait(1.0)
    assert t.connection_state == ConnectionState.CONNECTING

    t.disable()  # must not raise, must not block

    assert b"d" in port.writes
    port.release()
    assert t.wait_connected(timeout=2.0)


def test_a_write_failure_during_connect_still_marks_the_link_lost(build):
    """A command sent while CONNECTING that fails to write must still take
    the transport to LOST -- SERIAL-8's contract does not get a CONNECTING
    exemption."""
    port = build(release_after=None)
    t = SerialTransport("COM9")
    assert port.ping_write_started.wait(1.0)

    def _raise(_payload):
        raise OSError("unplugged mid-connect")
    port.write = _raise

    with pytest.raises(TransportError):
        t.write_command(b"e", priority=True)
    assert t.connection_state == ConnectionState.LOST

    # The handshake thread is still out there holding nothing now (its
    # blocked write already returned False from `_handshake`'s except
    # clause the next time it touches the now-`_raise`d port); closing state
    # must stay LOST, not be clobbered back to UNVERIFIED/VERIFIED by a
    # stale result.
    port.release()
    t.wait_connected(timeout=2.0)
    assert t.connection_state == ConnectionState.LOST


# --------------------------------------------------------------------------
# close() during CONNECTING wins over a handshake result that arrives late.
# --------------------------------------------------------------------------


def test_close_during_connect_is_not_overwritten_by_a_late_handshake_result(build):
    """`close()` takes the plain (non-priority) lock, so it is bounded by
    how long the handshake's own in-flight write can hold it -- in real
    pyserial that is `write_timeout` (1s), which `release_after` stands in
    for here. `close()` is not the stop path itself (that is
    `write_command(priority=True)`, proven above); it is the teardown step
    right after, and it still must not let a result that arrives *after*
    it clobber CLOSED back to VERIFIED/UNVERIFIED."""
    port = build(release_after=0.3)
    t = SerialTransport("COM9")
    assert port.ping_write_started.wait(1.0)
    assert t.connection_state == ConnectionState.CONNECTING

    t.close()
    assert t.connection_state == ConnectionState.CLOSED

    t.wait_connected(timeout=2.0)
    assert t.connection_state == ConnectionState.CLOSED, (
        "a handshake result that arrives after close() must not resurrect "
        "the connection state")
