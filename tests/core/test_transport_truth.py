"""RC-2 / RC-5 — a failed write is never recorded as a success, and FULL STOP latches.

The bug these cover is not "an error was not displayed". It is that the
transport swallowed every write exception and returned normally, so the model
set `system_enabled = False` and the UI reported the system disabled **on the
strength of a command that never left the process**. With stepper coils that
is the difference between a safe bench and a hot one.

Note on what "confirmed" means here: full ACK confirmation needs firmware v2
(decision D-7, stage S16). Until then a successful *write* is the strongest
claim available, and these tests pin exactly that — no more.
"""

import threading
import time

import pytest

from controller.serial import TransportError, serial as SerialTransport
from model.probes import DCProbe


class FailingTransport:
    """A transport whose writes always fail, the way a yanked USB cable does."""

    def __init__(self):
        self.writes = []

    def write_command(self, payload):
        self.writes.append(payload)
        raise TransportError("port is gone")

    def enable(self):
        self.write_command(b"e")

    def disable(self):
        self.write_command(b"d")

    def send_autonomous_command(self, params):
        self.write_command(b"auton")

    def send_manual_mode_command(self, params):
        self.write_command(b"manual")

    def read_position(self):
        # The model samples on its own thread now (RC-4); the double has to
        # answer, even if it has nothing to report.
        return None

    def close(self):
        pass


class RecordingTransport:
    def __init__(self):
        self.writes = []

    def write_command(self, payload):
        self.writes.append(payload)

    def enable(self):
        self.write_command(b"e")

    def disable(self):
        self.write_command(b"d")

    def send_autonomous_command(self, params):
        self.writes.append(("auton", params))

    def send_manual_mode_command(self, params):
        self.writes.append(("manual", params))

    def read_position(self):
        # The model samples on its own thread now (RC-4); the double has to
        # answer, even if it has nothing to report.
        return None

    def close(self):
        pass


def _probe(transport):
    probe = DCProbe("SIM", None)
    probe.serial_comm = transport
    return probe


# --------------------------------------------------------------------------
# I-2.1 — the transport tells the truth about whether a write happened.
# --------------------------------------------------------------------------


def test_write_command_raises_when_the_port_is_closed():
    t = SerialTransport("SIM")
    t.SERIAL_PORT = "/dev/ttyUSB0"  # pretend it is a real port
    t.ser = None
    with pytest.raises(TransportError):
        t.write_command(b"e")


def test_write_command_is_a_successful_no_op_in_simulator_mode():
    """SIM has nothing to write to; that is success, not failure."""
    SerialTransport("SIM").write_command(b"e")


def test_write_command_encodes_a_string_payload():
    class FakeSer:
        is_open = True

        def __init__(self):
            self.written = []

        def write(self, data):
            self.written.append(data)

    t = SerialTransport("SIM")
    t.SERIAL_PORT = "/dev/ttyUSB0"
    t.ser = FakeSer()
    t.write_command("<0,1,2>")
    assert t.ser.written == [b"<0,1,2>"]


# --------------------------------------------------------------------------
# RC-2 — the model must not claim a state it did not achieve.
# --------------------------------------------------------------------------


def test_enable_reports_failure_and_leaves_the_flag_alone():
    probe = _probe(FailingTransport())
    assert probe.enable() is False
    assert probe.system_enabled is False


def test_enable_sets_the_flag_only_on_a_successful_write():
    probe = _probe(RecordingTransport())
    assert probe.enable() is True
    assert probe.system_enabled is True


def test_a_failed_disable_faults_instead_of_claiming_the_system_is_off():
    """The core RC-2 case: coils may still be energized, so do not say 'off'."""
    probe = _probe(RecordingTransport())
    probe.enable()
    assert probe.system_enabled is True

    probe.serial_comm = FailingTransport()
    probe.disable()

    assert probe.in_fault, "a disable that never reached the board must fault"
    assert "coils may be energized" in probe.fault_reason
    assert probe.system_enabled is True, (
        "system_enabled must not be cleared by a disable that failed — that is "
        "the UI reporting the system safe on the strength of a failed command")


def test_a_successful_disable_clears_the_fault():
    probe = _probe(RecordingTransport())
    probe.enable()
    probe.serial_comm = FailingTransport()
    probe.disable()
    assert probe.in_fault

    probe.serial_comm = RecordingTransport()
    probe.disable()
    assert not probe.in_fault
    assert probe.system_enabled is False


# --------------------------------------------------------------------------
# I-5.1 — FULL STOP latches.
# --------------------------------------------------------------------------


def test_emergency_stop_latches():
    probe = _probe(RecordingTransport())
    probe.emergency_stop()
    assert probe.estop_latched


def test_the_latch_is_set_before_any_io():
    """A command in flight on another thread must not be able to land after
    the stop returns, so the latch cannot wait for the I/O to finish."""
    observed = []

    class ObservingTransport(RecordingTransport):
        def __init__(self, probe_ref):
            super().__init__()
            self.probe_ref = probe_ref

        def write_command(self, payload):
            observed.append(self.probe_ref[0].estop_latched)
            super().write_command(payload)

        def send_autonomous_command(self, params):
            observed.append(self.probe_ref[0].estop_latched)

    ref = [None]
    probe = _probe(ObservingTransport(ref))
    ref[0] = probe
    probe.emergency_stop()
    assert observed, "emergency_stop did no I/O at all"
    assert all(observed), "the latch must already be set at the first write"


def test_a_latched_probe_refuses_motion_commands():
    probe = _probe(RecordingTransport())
    probe.emergency_stop()
    probe.serial_comm = RecordingTransport()  # fresh recorder

    probe.send_autonomous_command()
    probe.send_manual_mode_command({"x_axisStatus": 1.0})

    assert probe.serial_comm.writes == [], "motion was sent while FULL STOP was latched"


def test_a_latched_probe_refuses_to_enable():
    probe = _probe(RecordingTransport())
    probe.emergency_stop()
    assert probe.enable() is False


def test_the_latch_does_not_clear_itself():
    """A latch that clears itself is not a latch."""
    probe = _probe(RecordingTransport())
    probe.emergency_stop()
    probe.disable()
    probe.touch_activity()
    assert probe.estop_latched


def test_only_an_explicit_operator_action_clears_the_latch():
    probe = _probe(RecordingTransport())
    probe.emergency_stop()
    probe.clear_estop()
    assert not probe.estop_latched
    assert probe.enable() is True


def test_a_script_already_running_stops_writing_once_the_latch_is_set():
    """STEPPER-8's untracked script thread is exactly why the latch is checked
    before each write rather than only at the start of the run."""
    probe = _probe(RecordingTransport())
    probe.emergency_stop()
    assert probe._refuse_if_estopped("script motion") is True


# --------------------------------------------------------------------------
# I-5.2 — emergency_stop must return promptly against a stalled transport.
# --------------------------------------------------------------------------


# Long enough to be unambiguously over the 100 ms budget, short enough that
# these stay in the fast gate. emergency_stop makes three transport calls.
STALL = 0.4


class StallingTransport(RecordingTransport):
    """A transport that has stopped answering — a live port with a wedged board."""

    def write_command(self, payload):
        time.sleep(STALL)

    def send_autonomous_command(self, params):
        time.sleep(STALL)

    def disable(self):
        time.sleep(STALL)


@pytest.mark.xfail(
    strict=True,
    reason="[invariant I-5.2, owned by S8] emergency_stop does its hardware I/O "
    "on the calling thread, so a stalled transport blocks it for as long as the "
    "write takes. The latch is already set first (I-5.1), so no *new* motion "
    "can be issued meanwhile — but the caller, which may be the UI thread, is "
    "held. RC-5's worker/timeout work is S8.",
)
def test_emergency_stop_returns_within_100ms_against_a_stalled_transport():
    probe = _probe(StallingTransport())
    start = time.monotonic()
    probe.emergency_stop()
    assert time.monotonic() - start < 0.1


def test_the_latch_is_set_immediately_even_when_the_transport_stalls():
    """The part that is true today, and the part that actually protects the
    bench: no further motion can be issued while the stop is in flight."""
    probe = _probe(StallingTransport())
    done = threading.Event()

    def stop():
        probe.emergency_stop()
        done.set()

    threading.Thread(target=stop, daemon=True).start()
    deadline = time.monotonic() + 1.0
    while not probe.estop_latched and time.monotonic() < deadline:
        time.sleep(0.005)

    assert probe.estop_latched, "the latch must not wait for the stalled write"
    assert probe._refuse_if_estopped("motion") is True


# --------------------------------------------------------------------------
# SERIAL-9 — simulator mode is a working configuration, not a broken one.
# --------------------------------------------------------------------------


def test_simulator_probes_can_arm_and_disarm():
    """`_verify_serial` returns False for SIM because there is no port object,
    which made enable() raise and left every SIM probe permanently
    un-armable — the whole point of SIM being to exercise the bench without
    hardware attached."""
    t = SerialTransport("SIM")
    t.enable()
    t.disable()

    probe = DCProbe("SIM", None)
    assert probe.enable() is True
    assert probe.system_enabled is True
    assert not probe.in_fault


# --------------------------------------------------------------------------
# The heater latches too (RC-5).
# --------------------------------------------------------------------------


def test_temperature_emergency_stop_latches_and_refuses_new_setpoints():
    from model.temperature_system import TemperatureSystem

    temp = TemperatureSystem()
    transport = RecordingTransport()
    temp.serial_conn = transport
    transport.is_open = lambda: True

    temp.emergency_stop()
    assert temp.estop_latched

    transport.writes.clear()
    temp.setpoint = "200"
    temp.send_settings()
    assert transport.writes == [], "a new setpoint was accepted after FULL STOP"

    temp.clear_estop()
    temp.send_settings()
    assert transport.writes, "an explicit clear must restore normal operation"
