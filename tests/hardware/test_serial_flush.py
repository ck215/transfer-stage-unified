"""A bounded `flush()` on the transport, so a shutdown frame is not lost.

Routed here from TEMP-11 (fix-web): `TemperatureSystem.close()` sends the
heater-off frame and then closes the port. On POSIX a `close()` does not
guarantee the bytes already handed to the OS have reached the wire, so the
off-frame can be discarded by the close that follows it — and the heater
stays on. The model cannot fix that on its own: reaching for `.ser.flush()`
violates I-2.3 ("no code outside the transport touches `.ser`"), which is a
grep invariant, and rightly so. The primitive has to live here.

Two things it must do, and the second is why this is not simply
`self.ser.flush()`:

* **Block until the OS buffer is actually drained.** That is the point.
* **Not forever.** pyserial's `flush()` is `tcdrain` on POSIX, which is
  unbounded — SERIAL-12 names it as a hazard on the GUI thread. A dead or
  flow-controlled port would hang the shutdown that called it. A drain that
  cannot finish inside its budget reports failure; it does not wait.

`flush()` is a *query*, not a state transition: it answers "did the bytes
get out?" and deliberately does not move the connection to LOST. The caller
here is a teardown path, and a "Connection Lost" popup raised while the
application is closing is noise, not information.
"""

import threading
import time

import pytest

from controller.serial import SimulatedPort, serial as SerialTransport

PORT = "/dev/ttyFAKE0"


def _real_port_transport(handle):
    """A transport that believes it has a real port, holding `handle`."""
    t = SerialTransport("SIM")
    t.SERIAL_PORT = PORT
    t.ser = handle
    return t


class GoodHandle:
    is_open = True

    def __init__(self):
        self.written = []
        self.flushes = 0
        self.closed = False

    def write(self, payload):
        self.written.append(payload)
        return len(payload)

    def flush(self):
        self.flushes += 1

    def close(self):
        self.closed = True


class WedgedHandle:
    """A port whose `tcdrain` never returns — flow control, or a dead cable."""

    is_open = True

    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Event()

    def write(self, payload):
        return len(payload)

    def flush(self):
        self.entered.set()
        self.release.wait(30.0)

    def close(self):
        pass


class RaisingHandle:
    is_open = True

    def write(self, payload):
        return len(payload)

    def flush(self):
        raise OSError("device not configured")

    def close(self):
        pass


# --------------------------------------------------------------------------
# The safety case: the frame gets out before the port goes away.
# --------------------------------------------------------------------------


def test_flush_drains_the_port_and_says_so():
    handle = GoodHandle()
    t = _real_port_transport(handle)
    t.write_command(b"<0,0,0,0,0,0>")

    assert t.flush() is True
    assert handle.flushes == 1
    assert handle.written == [b"<0,0,0,0,0,0>"]


def test_a_frame_can_be_forced_out_before_close():
    """The TEMP-11 shape: write the off-frame, drain it, then close."""
    handle = GoodHandle()
    t = _real_port_transport(handle)

    t.write_command(b"<0,0,0,0,0,0>")
    assert t.flush() is True
    t.close()

    assert handle.written == [b"<0,0,0,0,0,0>"]
    assert handle.flushes == 1
    assert handle.closed is True


# --------------------------------------------------------------------------
# ...but a dead port cannot hang the shutdown.
# --------------------------------------------------------------------------


def test_a_wedged_drain_gives_up_inside_its_budget():
    handle = WedgedHandle()
    t = _real_port_transport(handle)
    try:
        started = time.time()
        result = t.flush(timeout=0.2)
        elapsed = time.time() - started

        assert result is False, "a drain that never finished reported success"
        assert elapsed < 2.0, f"the caller was held for {elapsed:.1f}s"
        assert handle.entered.is_set()
    finally:
        handle.release.set()


def test_the_default_budget_is_bounded_and_short():
    assert 0 < SerialTransport.FLUSH_TIMEOUT <= 2.0


def test_a_wedged_drain_does_not_leave_a_non_daemon_thread_behind():
    """A shutdown blocked on a lingering flush thread is the same bug again."""
    handle = WedgedHandle()
    t = _real_port_transport(handle)
    try:
        t.flush(timeout=0.1)
        assert handle.entered.wait(2.0)
        workers = [th for th in threading.enumerate()
                   if th.name.startswith("flush-")]
        assert workers, "the drain did not run on its own thread"
        assert all(th.daemon for th in workers)
    finally:
        handle.release.set()


# --------------------------------------------------------------------------
# Failure is reported, never guessed at.
# --------------------------------------------------------------------------


def test_a_drain_that_raises_reports_failure():
    t = _real_port_transport(RaisingHandle())
    assert t.flush() is False


def test_flushing_a_closed_port_reports_failure():
    t = _real_port_transport(GoodHandle())
    t.ser = None
    assert t.flush() is False


def test_a_failed_drain_does_not_move_the_connection_state():
    """A teardown-path query must not raise "Connection Lost" on the way out."""
    from controller.serial import ConnectionState

    t = _real_port_transport(RaisingHandle())
    t.connection_state = ConnectionState.VERIFIED
    t.flush()
    assert t.connection_state == ConnectionState.VERIFIED


# --------------------------------------------------------------------------
# Simulator mode is a working configuration, not a different code path.
# --------------------------------------------------------------------------


def test_flush_in_simulator_mode_is_a_successful_no_op():
    assert SerialTransport("SIM").flush() is True


def test_the_simulated_port_answers_flush():
    """RC-2 item 4: SIM ACKs everything the transport may ask a port to do.

    It did not answer `flush`, and `send_manual_mode_command` calls it on
    every frame — so *every* manual-mode frame in simulator mode raised
    AttributeError inside the write path and was reported to the operator as
    a "Serial Write Error". Exactly the class of gap SERIAL-9 was: a method
    the simulated port forgot to implement.
    """
    assert hasattr(SimulatedPort(), "flush")
    SimulatedPort().flush()


def test_a_manual_frame_in_simulator_mode_reports_no_error():
    import error_routing
    from error_routing import EventBus

    bus = EventBus()
    original, error_routing.bus = error_routing.bus, bus
    try:
        t = SerialTransport("SIM")
        t.send_manual_mode_command({
            "x_axisStatus": 0.0, "y_axisStatus": 0.0,
            "x_stepSize": 1, "y_stepSize": 1, "z_stepSize": 1,
            "dpad_LR": 0, "dpad_UD": 0, "manual_jog_speed": 10,
        })
    finally:
        error_routing.bus = original

    errors = [e for e in bus.since(0) if e.severity == "error"]
    assert errors == [], [(e.title, e.message) for e in errors]


# --------------------------------------------------------------------------
# The API guard that makes I-2.3 keepable.
# --------------------------------------------------------------------------


def test_the_transport_exposes_flush_beside_write_and_close():
    """A model that needs a drain must have somewhere to ask for one.

    Without this, the only way to force a shutdown frame out is
    `self.serial_conn.ser.flush()`, which is precisely what invariant I-2.3
    forbids — and the model would have been right to want it.
    """
    for name in ("write_command", "flush", "close"):
        assert callable(getattr(SerialTransport, name, None)), name
