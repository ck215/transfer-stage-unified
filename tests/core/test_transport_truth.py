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

    def write_command(self, payload, priority=False):
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

    def write_command(self, payload, priority=False):
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

    def write_command(self, payload, priority=False):
        time.sleep(STALL)
        self.writes.append(payload)

    def send_autonomous_command(self, params):
        time.sleep(STALL)

    def disable(self):
        time.sleep(STALL)
        self.writes.append(b"d")


def test_emergency_stop_returns_within_100ms_against_a_stalled_transport():
    """HELD since S8. Until then emergency_stop did its hardware I/O on the
    calling thread, which is frequently the UI thread — so a wedged transport
    froze the window behind a FULL STOP button that appeared to do nothing.
    The write now goes out on a worker and the caller joins with a bound; if
    the bound expires the stop is still in flight, we simply stop waiting."""
    probe = _probe(StallingTransport())
    start = time.monotonic()
    probe.emergency_stop()
    assert time.monotonic() - start < 0.1


def test_emergency_stop_still_reaches_the_hardware_after_it_returns():
    """Returning early must not mean abandoning the stop."""
    transport = StallingTransport()
    probe = _probe(transport)
    probe.emergency_stop()
    assert probe.estop_latched
    # The worker is still running; give it room to land.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not transport.writes:
        time.sleep(0.02)
    assert transport.writes, "the hardware stop was dropped, not merely deferred"


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


# --------------------------------------------------------------------------
# RC-5 item 2 — FULL STOP fans out, and a stop outranks a poll for the lock.
# --------------------------------------------------------------------------


class SlowStopModel:
    """A ManagedModel whose stop takes a while, the way a wedged port does."""

    def __init__(self, delay=0.0, raises=None):
        self.delay, self.raises = delay, raises
        self.stops = 0

    def emergency_stop(self):
        self.stops += 1
        if self.delay:
            time.sleep(self.delay)
        if self.raises:
            raise self.raises

    def teardown(self):
        pass


def test_full_stop_does_not_queue_devices_behind_a_wedged_one():
    """Stops used to run one after another on the caller's thread, so a
    device with a wedged transport delayed every device behind it — in
    registration order, which has nothing to do with which axis is moving."""
    from model.system_manager import SystemManager

    manager = SystemManager()
    slow = SlowStopModel(delay=0.4)
    fast = SlowStopModel()
    manager.register("slow", slow)
    manager.register("fast", fast)

    start = time.monotonic()
    manager.full_stop_all()
    elapsed = time.monotonic() - start

    assert fast.stops == 1 and slow.stops == 1
    assert elapsed < 0.4 + 0.25, (
        f"FULL STOP took {elapsed:.2f}s for two devices; they ran in series")


def test_full_stop_reports_per_model_results():
    from model.system_manager import SystemManager

    manager = SystemManager()
    manager.register("good", SlowStopModel())
    manager.register("bad", SlowStopModel(raises=RuntimeError("port gone")))

    results = manager.full_stop_all()

    assert results["good"] is True
    assert results["bad"] is False, "a failed stop must be reported, not swallowed"


def test_full_stop_returns_even_if_a_model_never_finishes():
    from model.system_manager import SystemManager

    manager = SystemManager()
    manager.FULL_STOP_BUDGET = 0.2
    manager.register("hung", SlowStopModel(delay=5.0))
    manager.register("ok", SlowStopModel())

    start = time.monotonic()
    results = manager.full_stop_all()
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"FULL STOP hung for {elapsed:.2f}s"
    assert results["ok"] is True
    assert results["hung"] is False, "an unconfirmed stop must not report success"


def test_full_stop_on_an_empty_registry_is_harmless():
    from model.system_manager import SystemManager

    assert SystemManager().full_stop_all() == {}


def test_a_stop_forces_the_transport_lock_rather_than_waiting_behind_a_poll():
    """A poll holding the transport lock must not be able to delay 'd'/'k'."""
    import threading

    t = SerialTransport("SIM")
    t.SERIAL_PORT = "/dev/ttyUSB0"

    class FakeSer:
        is_open = True

        def __init__(self):
            self.written = []

        def write(self, data):
            self.written.append(data)

    t.ser = FakeSer()
    t.PRIORITY_LOCK_TIMEOUT = 0.05

    holder_may_release = threading.Event()
    holding = threading.Event()

    def hold_lock():
        with t._lock:
            holding.set()
            holder_may_release.wait(2.0)

    threading.Thread(target=hold_lock, daemon=True).start()
    assert holding.wait(1.0)

    try:
        start = time.monotonic()
        t.write_command(b"d", priority=True)
        elapsed = time.monotonic() - start
        assert elapsed < 0.3, f"the stop waited {elapsed:.2f}s behind the lock holder"
        assert b"d" in t.ser.written
    finally:
        holder_may_release.set()


def test_an_ordinary_write_still_waits_for_the_lock():
    """Forcing past the lock is the stop path's privilege, not everyone's."""
    import threading

    t = SerialTransport("SIM")
    t.SERIAL_PORT = "/dev/ttyUSB0"

    class FakeSer:
        is_open = True

        def __init__(self):
            self.written = []

        def write(self, data):
            self.written.append(data)

    t.ser = FakeSer()
    order = []
    release = threading.Event()

    def hold_lock():
        with t._lock:
            order.append("holder-in")
            release.wait(1.0)
            order.append("holder-out")

    threading.Thread(target=hold_lock, daemon=True).start()
    time.sleep(0.05)

    def ordinary():
        t.write_command(b"move")
        order.append("write")

    writer = threading.Thread(target=ordinary, daemon=True)
    writer.start()
    time.sleep(0.1)
    release.set()
    writer.join(2.0)

    assert order == ["holder-in", "holder-out", "write"], order


# --------------------------------------------------------------------------
# RC-5 item 3 — ConnectionState is what the link actually is.
# --------------------------------------------------------------------------


def test_simulator_mode_is_its_own_state_not_a_failure():
    from controller.serial import ConnectionState

    assert SerialTransport("SIM").connection_state == ConnectionState.SIMULATED


def test_an_opened_port_that_never_answered_is_unverified_not_connected():
    """Opening a port proves nothing. The old code logged "Operating blind"
    and then treated the link as good, so a cable into a powered-off board
    was indistinguishable from a working one (SERIAL-7)."""
    from unittest.mock import MagicMock, patch

    from controller.serial import ConnectionState

    with patch("controller.serial.pyserial.Serial") as mock_serial:
        inst = MagicMock()
        inst.is_open = True
        inst.in_waiting = 0          # nothing ever answers
        mock_serial.return_value = inst
        t = SerialTransport("COM9")

    assert t.connection_state == ConnectionState.UNVERIFIED
    assert t.connection_state != ConnectionState.VERIFIED


def test_the_first_write_failure_moves_the_link_to_lost_and_closes_it():
    from unittest.mock import MagicMock, patch

    from controller.serial import ConnectionState

    with patch("controller.serial.pyserial.Serial") as mock_serial:
        inst = MagicMock()
        inst.is_open = True
        inst.in_waiting = 0
        mock_serial.return_value = inst
        t = SerialTransport("COM9")

    inst.write.side_effect = OSError("unplugged")
    with pytest.raises(TransportError):
        t.write_command(b"x")

    assert t.connection_state == ConnectionState.LOST
    assert t.ser is None, "the handle must be released, not left dangling"
    inst.close.assert_called_once()


def test_loss_is_reported_once_not_on_every_subsequent_command():
    """Port loss used to be popup spam on a 5 s dedupe while the reported
    state never changed at all."""
    from unittest.mock import MagicMock, patch

    from controller.serial import ConnectionState

    with patch("controller.serial.pyserial.Serial") as mock_serial:
        inst = MagicMock()
        inst.is_open = True
        inst.in_waiting = 0
        mock_serial.return_value = inst
        t = SerialTransport("COM9")

    inst.write.side_effect = OSError("unplugged")
    with patch("controller.serial.ErrorPopupManager.report_error") as report:
        with pytest.raises(TransportError):
            t.write_command(b"x")
        for _ in range(3):
            with pytest.raises(TransportError):
                t.write_command(b"x")
        assert report.call_count == 1, (
            f"the loss was reported {report.call_count} times")
    assert t.connection_state == ConnectionState.LOST


def test_closing_deliberately_is_not_recorded_as_loss():
    from unittest.mock import MagicMock, patch

    from controller.serial import ConnectionState

    with patch("controller.serial.pyserial.Serial") as mock_serial:
        inst = MagicMock()
        inst.is_open = True
        inst.in_waiting = 0
        mock_serial.return_value = inst
        t = SerialTransport("COM9")

    t.close()
    assert t.connection_state == ConnectionState.CLOSED


def test_simulator_stays_simulated_across_close():
    from controller.serial import ConnectionState

    t = SerialTransport("SIM")
    t.close()
    assert t.connection_state == ConnectionState.SIMULATED


# --------------------------------------------------------------------------
# RC-5 item 1 / STEPPER-8 — a run that has been superseded stops.
# --------------------------------------------------------------------------


def test_stopping_invalidates_a_script_still_in_flight():
    """run_script used to spawn an untracked thread with no way to tell it to
    stop and no way to know it had. Halting relied on the thread noticing that
    is_stepping/auton_flag had been flipped — flags any other caller could
    flip back, and which said nothing about *which* run they belonged to."""
    probe = _probe(RecordingTransport())
    generation = probe._new_run_generation()
    assert probe._generation_is_current(generation)

    probe.full_stop()
    assert not probe._generation_is_current(generation), (
        "a stopped run still believed it was the current one")


def test_a_second_run_supersedes_the_first():
    probe = _probe(RecordingTransport())
    first = probe._new_run_generation()
    second = probe._new_run_generation()
    assert first != second
    assert not probe._generation_is_current(first)
    assert probe._generation_is_current(second)


def test_cancel_running_script_is_explicit_and_idempotent():
    probe = _probe(RecordingTransport())
    generation = probe._new_run_generation()
    probe.cancel_running_script()
    probe.cancel_running_script()
    assert not probe._generation_is_current(generation)


# --------------------------------------------------------------------------
# RC-2 item 4 — SIM is a device, not a different code path.
# --------------------------------------------------------------------------


def test_simulator_mode_acknowledges_writes_instead_of_short_circuiting():
    """SIM used to be `ser = None` plus an early exit in every method, so the
    paths exercised headlessly were not the paths run with hardware attached
    — and each new method had to remember its own early exit, which is
    exactly how SERIAL-9 happened."""
    t = SerialTransport("SIM")
    assert t.is_open()
    t.write_command(b"hello")
    assert t.ser.writes == [b"hello"], "the simulated port did not see the write"


def test_simulator_and_hardware_take_the_same_route_through_the_transport():
    """The point of item 4: one code path, two devices."""
    sim = SerialTransport("SIM")
    sim.enable()
    sim.disable()
    sim.write_command("a string payload")
    assert sim.ser.writes == [b"e", b"d", b"a string payload"], sim.ser.writes


def test_simulator_reads_return_nothing_rather_than_raising():
    t = SerialTransport("SIM")
    assert t.read_line() == b""


def test_a_closed_simulator_refuses_writes_like_a_closed_port():
    t = SerialTransport("SIM")
    t.close()
    with pytest.raises(TransportError):
        t.write_command(b"x")
