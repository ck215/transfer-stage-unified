"""TEMP-17 (host half) and SERIAL-23 (host side): write ordering on the wire.

Two defects with one root: the transport's priority path, after waiting
PRIORITY_LOCK_TIMEOUT for `_lock`, writes *unsynchronised*.

1. **Byte interleaving.** `_lock` is held for whole transactions - including
   the heater reader's blocking `readline()` - so the priority path forces
   through routinely, and can then overlap an ordinary write that is in
   `ser.write` at that moment. For the heater that can produce a short
   `<...>` frame, which `temp_controller.ino::parseData` feeds to
   `atof(NULL)`; for the binary boards a `'d'` inside a manual packet is
   swallowed as payload (SERIAL-23).

2. **Order inversion (found while fixing 1).** An Enter Settings that
   passed its FULL STOP check waits inside `write_command` for `_lock`
   while the reader holds it. A FULL STOP forces its zero-setpoint frame
   through meanwhile; when the reader lets go, the stale heating frame is
   written *after* the stop, and the heater re-arms.

Pinned: a priority write never overlaps an in-flight write (bounded, so a
wedged writer still cannot hold a stop), and a settings frame superseded by
a stop is never written.
"""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from controller.serial import serial
from model.temperature_system import TemperatureSystem



class _WireRecorder:
    """A pyserial stand-in that records writes and detects overlap."""

    def __init__(self, write_delay=0.0):
        self.is_open = True
        self.in_waiting = 0
        self.write_delay = write_delay
        self.frames = []
        self.overlaps = 0
        self._inside = 0
        self._guard = threading.Lock()

    def write(self, data):
        with self._guard:
            self._inside += 1
            if self._inside > 1:
                self.overlaps += 1
        try:
            if self.write_delay and data.startswith(b"<"):
                time.sleep(self.write_delay)
            self.frames.append(bytes(data))
        finally:
            with self._guard:
                self._inside -= 1
        return len(data)

    def readline(self):
        return b""

    def close(self):
        self.is_open = False


def _transport(wire):
    with patch("controller.serial.pyserial.Serial", return_value=wire):
        s = serial("COM_TEST")
    # Let the identity handshake's first ping go out; its pings are filtered.
    time.sleep(0.05)
    return s


def _frames(wire):
    return [f for f in wire.frames if not f.startswith(b"s\n")]


def _hold_lock(s, seconds):
    """Hold the transport lock the way the heater's reader does in readline."""
    ready = threading.Event()

    def _run():
        with s._lock:
            ready.set()
            time.sleep(seconds)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    ready.wait(1)
    return t


# -- transport ---------------------------------------------------------------

def test_temp17_a_forced_priority_write_waits_out_an_in_flight_write():
    wire = _WireRecorder(write_delay=0.12)
    s = _transport(wire)
    slow = threading.Thread(
        target=s.write_command, args=(b"<80,6.0,2,0.5,0.1,0>",), daemon=True)
    slow.start()
    time.sleep(0.02)  # the slow frame is now inside ser.write
    s.write_command(b"d", priority=True)
    slow.join(2)
    assert wire.overlaps == 0, "priority write overlapped an in-flight write"
    assert _frames(wire) == [b"<80,6.0,2,0.5,0.1,0>", b"d"]


def test_temp17_a_wedged_writer_still_cannot_hold_a_stop_for_long():
    wire = _WireRecorder(write_delay=1.0)
    s = _transport(wire)
    wedged = threading.Thread(
        target=s.write_command, args=(b"<80,6.0,2,0.5,0.1,0>",), daemon=True)
    wedged.start()
    time.sleep(0.02)
    t0 = time.monotonic()
    s.write_command(b"d", priority=True)
    elapsed = time.monotonic() - t0
    wedged.join(3)
    bound = s.PRIORITY_LOCK_TIMEOUT + s.WRITE_IO_LOCK_TIMEOUT + 0.15
    assert elapsed < bound, f"priority write waited {elapsed:.2f}s (bound {bound:.2f}s)"


def test_temp17_abort_if_is_checked_at_the_write_and_skips_it():
    wire = _WireRecorder()
    s = _transport(wire)
    assert s.write_command(b"<1>", abort_if=lambda: True) is False
    assert s.write_command(b"<2>", abort_if=lambda: False) is True
    assert _frames(wire) == [b"<2>"]


def test_serial23_a_priority_disable_cannot_land_inside_a_manual_packet():
    """SERIAL-23, host side. Both binary firmwares `readBytes` a fixed-size
    packet after 0xAA, so a 'd' written mid-packet is swallowed as payload.
    The disable must go out after the packet, not inside it."""
    wire = _WireRecorder()
    s = _transport(wire)
    params = {
        "x_axisStatus": 0.5, "y_axisStatus": -0.5,
        "z_axisStatusL": -1.0, "z_axisStatusR": -1.0,
        "x_stepSize": 10, "y_stepSize": 10, "z_stepSize": 10,
        "dpad_LR": 0, "dpad_UD": 0, "LBumper": 0, "RBumper": 0,
        "manual_jog_speed": 400, "packet_format": "<BBffffffffff",
    }
    real_write = wire.write

    def slow_binary(data):
        if data[:1] == b"\xaa":
            try:
                with wire._guard:
                    wire._inside += 1
                    if wire._inside > 1:
                        wire.overlaps += 1
                time.sleep(0.12)
                wire.frames.append(bytes(data))
            finally:
                with wire._guard:
                    wire._inside -= 1
            return len(data)
        return real_write(data)

    wire.write = slow_binary
    s._verify_serial = lambda verbose=False: True
    jog = threading.Thread(target=s.send_manual_mode_command, args=(params,),
                           daemon=True)
    jog.start()
    time.sleep(0.02)
    s.write_command(b"d", priority=True)
    jog.join(2)
    frames = _frames(wire)
    assert wire.overlaps == 0, "the disable overlapped the manual packet"
    assert frames[-1] == b"d" and frames[0][:1] == b"\xaa", frames


# -- heater end to end ---------------------------------------------------------

def _heater_on(wire):
    h = TemperatureSystem(port=None)
    h.serial_conn = _transport(wire)
    h.setpoint = "80"
    return h


@pytest.mark.parametrize("stopper", ["emergency_stop", "stop"])
def test_temp17_a_settings_frame_waiting_behind_the_reader_cannot_land_after_a_stop(stopper):
    wire = _WireRecorder()
    h = _heater_on(wire)
    try:
        reader = _hold_lock(h.serial_conn, 0.4)
        sender = threading.Thread(target=h.send_settings, daemon=True)
        sender.start()
        time.sleep(0.1)  # send_settings passed its latch check; blocked on _lock
        if stopper == "emergency_stop":
            h.emergency_stop()
        else:
            h.stop(priority=True)
        reader.join(2)
        sender.join(2)
        frames = _frames(wire)
        stops = [i for i, f in enumerate(frames) if f.startswith(b"<0,")]
        heats = [i for i, f in enumerate(frames) if f.startswith(b"<80")]
        assert stops, frames
        assert not [i for i in heats if i > stops[-1]], (
            f"a heating frame was written after the stop: {frames}")
        assert h._commanded_setpoint is None
    finally:
        h.stop_client_liveness_watchdog()


def test_temp17_an_ordinary_settings_send_still_goes_out():
    wire = _WireRecorder()
    h = _heater_on(wire)
    try:
        h.send_settings()
        assert any(f.startswith(b"<80") for f in _frames(wire))
        assert h._commanded_setpoint == 80.0
    finally:
        h.stop_client_liveness_watchdog()
