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
from results import NeedsConfirmation

from controller.serial import TransportError, serial as SerialTransport
from model.probes import DCProbe


class FailingTransport:
    """A transport whose writes always fail, the way a yanked USB cable does."""

    def __init__(self):
        self.writes = []

    def write_command(self, payload, priority=False, abort_if=None):
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

    def write_command(self, payload, priority=False, abort_if=None):
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
    """Strengthened by MANAGER-22, not relaxed by it.

    `clear_estop()` used to release the latch on a single call, so this test
    could only ever check that *something* cleared it. Now the unconfirmed
    call asks first and clears nothing, which is what "explicit operator
    action" in this test's own name actually means — so the assertion the
    name promised is finally available to make.
    """
    probe = _probe(RecordingTransport())
    probe.emergency_stop()

    asked = probe.clear_estop()
    assert isinstance(asked, NeedsConfirmation)
    assert probe.estop_latched, "an unconfirmed clear released the latch"

    probe.clear_estop(True)
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

    def write_command(self, payload, priority=False, abort_if=None):
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

    temp.clear_estop(True)
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
        """Honours the MANAGER-21 contract: returns whether the stop confirmed.

        This double used to return `None` implicitly, which is exactly how
        the defect survived — `full_stop_all` read "did not raise" as
        success, and every test here handed it a double that could only
        succeed or raise. The real shape, a model that returns normally
        *without* having confirmed, was not reachable through this class and
        so was never tested. It is covered directly now, against real models
        and a real stalled transport, in
        tests/core/test_manager21_stop_confirmation.py.
        """
        self.stops += 1
        if self.delay:
            time.sleep(self.delay)
        if self.raises:
            raise self.raises
        return True

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
    was indistinguishable from a working one (SERIAL-7).

    **Updated for SERIAL-6.** The handshake now runs on a worker thread, so
    the verdict is no longer in hand the instant `__init__` returns — the
    state passes through CONNECTING first. The contract this test defends is
    unchanged and is if anything asserted harder below: a board that never
    answers must *never* be called VERIFIED, at any point. What changed is
    only *when* the final answer is readable, so the test waits for the
    worker via the `wait_connected` seam SERIAL-6 added for exactly this.

    `wait_connected` is test-only by design; calling it from a view or model
    would put the 1.5–4.5 s stall straight back on the GUI thread, which is
    the whole of SERIAL-6. The timing constants are shrunk here so waiting
    for a board that never answers costs milliseconds instead of 4.5 s.
    """
    from unittest.mock import MagicMock, patch

    from controller.serial import ConnectionState

    with patch("controller.serial.pyserial.Serial") as mock_serial, \
         patch.object(SerialTransport, "BOOTLOADER_WAIT", 0.01), \
         patch.object(SerialTransport, "HANDSHAKE_TIMEOUT", 0.05), \
         patch.object(SerialTransport, "PING_INTERVAL", 0.01):
        inst = MagicMock()
        inst.is_open = True
        inst.in_waiting = 0          # nothing ever answers
        mock_serial.return_value = inst
        t = SerialTransport("COM9")

        # The point SERIAL-7 exists for, now checked during the window that
        # SERIAL-6 opened as well as after it: "not yet known" is an honest
        # answer, "verified" is not.
        assert t.connection_state != ConnectionState.VERIFIED, (
            "a port that has answered nothing was called VERIFIED while the "
            "handshake was still in flight")

        assert t.wait_connected(timeout=5.0), "handshake worker never finished"

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


# --------------------------------------------------------------------------
# ROTATOR-8 — the same contract, on the device S8's test did not cover.
#
# S8 was recorded as closing I-5.2 for every device. Its test builds a probe,
# so the rotator kept the original defect: `emergency_stop` called `stop()`
# called `smc.stop()` called `sendcmd('ST')`, which takes the SMC100's
# `_serial_lock` — held for whole poll transactions, up to about half a second
# each with retry=10. FULL STOP queued behind the poller while the stage
# turned.
# --------------------------------------------------------------------------


class StallingSMC:
    """An SMC100 whose serial lock is held by a poll that has stopped answering."""

    def __init__(self):
        self.stops = []
        self._serial_lock = threading.Lock()

    def stop(self, priority=False):
        # The real priority path refuses to wait on the lock; the ordinary one
        # blocks on it. Model exactly that difference.
        if priority:
            self.stops.append("ST")
            return
        with self._serial_lock:
            self.stops.append("ST")

    def move_relative_deg(self, step):
        time.sleep(STALL)

    def move_absolute_deg(self, target):
        time.sleep(STALL)


def _rotator(smc, position="0"):
    from model.rotator_system import RotatorSystem

    rotator = RotatorSystem(default_port=None)
    rotator.smc = smc
    rotator.is_connected = True
    # A freshly built rotator has never been polled, so its position is
    # unknown and ROTATOR-4 makes every relative move ask first. These tests
    # are about the FULL STOP latch, so give them a known stage.
    rotator.position = position
    return rotator


def test_rotator_emergency_stop_returns_within_100ms_behind_a_held_serial_lock():
    """ROTATOR-8. The lock stands in for an in-flight poll transaction."""
    smc = StallingSMC()
    rotator = _rotator(smc)
    smc._serial_lock.acquire()
    try:
        start = time.monotonic()
        rotator.emergency_stop()
        assert time.monotonic() - start < 0.1
    finally:
        smc._serial_lock.release()


def test_rotator_emergency_stop_still_reaches_the_hardware():
    """Returning early must not mean abandoning the stop."""
    smc = StallingSMC()
    rotator = _rotator(smc)
    rotator.emergency_stop()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not smc.stops:
        time.sleep(0.02)
    assert smc.stops, "the rotator stop was dropped, not merely deferred"


def test_rotator_latches_so_a_queued_move_cannot_land_after_the_stop():
    """The ST-then-PA race: a move click spawns a worker, FULL STOP is pressed,
    and the worker then reaches sendcmd and turns the stage anyway."""
    smc = StallingSMC()
    rotator = _rotator(smc)
    rotator.emergency_stop()
    assert rotator.estop_latched
    rotator.step_deg = "5"
    assert rotator.move_relative_positive() is False


def test_only_an_explicit_operator_action_clears_the_rotator_latch():
    rotator = _rotator(StallingSMC())
    rotator.emergency_stop()

    # Unconfirmed clears nothing (MANAGER-22) — see the probe test above.
    assert isinstance(rotator.clear_estop(), NeedsConfirmation)
    assert rotator.estop_latched

    rotator.clear_estop(True)
    assert not rotator.estop_latched
    rotator.step_deg = "5"
    assert rotator.move_relative_positive() is True


def test_the_rotator_stop_path_takes_the_priority_write():
    """A stop that waits on the serial lock is the whole defect. Pin the flag."""
    calls = []

    class Recorder(StallingSMC):
        def stop(self, priority=False):
            calls.append(priority)

    _rotator(Recorder()).emergency_stop()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not calls:
        time.sleep(0.02)
    assert calls == [True], f"emergency_stop used priority={calls}"


# --------------------------------------------------------------------------
# DC-18 — the kill-coils write goes through the locked transport, not around it.
#
# `power_down` used to call `serial_comm.ser.write(b'k\n')` directly, which
# bypasses the RLock every other write takes, so a 'k' from a Web request or
# the interlock watchdog could interleave with a multi-byte manual packet and
# corrupt a frame. The audit's second half — a watchdog flipping mode
# booleans while the GUI thread read them — went away with the four booleans
# themselves in S7; the mode is now one value behind `_mode_lock`.
# --------------------------------------------------------------------------


def test_dc_18_kill_coils_goes_through_the_locked_priority_path():
    class Recorder(RecordingTransport):
        def __init__(self):
            super().__init__()
            self.priorities = []

        def write_command(self, payload, priority=False, abort_if=None):
            self.priorities.append((payload, priority))
            self.writes.append(payload)

    transport = Recorder()
    probe = _probe(transport)
    probe.power_down()
    assert (b"k\n", True) in transport.priorities, (
        "'k' must go through write_command on the priority path, never "
        f"around the lock via ser.write: {transport.priorities}")


def test_dc_18_no_model_writes_around_the_transport_lock():
    """The structural half: nothing under src/model reaches `.ser.write`."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "model"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\.ser\s*\.\s*write\b", code):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "a model wrote to the raw port, bypassing the transport lock "
        "(DC-18):\n" + "\n".join(offenders))


def test_dc_18_the_probe_mode_has_one_writer_under_one_lock():
    """The flag race is gone because the flags are gone: four booleans set from
    seven places became one `_mode` written only by `_transition`."""
    probe = _probe(RecordingTransport())
    assert hasattr(probe, "_mode_lock")
    assert not hasattr(probe, "auton_flag_lock")
    for gone in ("_stop_and_disarm",):
        assert not hasattr(probe, gone), f"{gone} came back"


# --------------------------------------------------------------------------
# ROTATOR-4 — the +/-30 degree tubing guard cannot be walked past.
#
# `move_relative` computed its target from `self.position`, which is whatever
# the last poll wrote. That is None until the first poll and None again after
# a reconnect, and the old code turned None into 0.0 — so an unknown position
# became a known one at the origin. It also could not see a move already in
# flight, so a stack of clicks each looked safe on its own.
# --------------------------------------------------------------------------


def test_rotator_4_an_unknown_position_is_not_the_origin():
    """Failure scenario A: the stage is really at 25, the model has never
    polled, and a +10 step computes to 10 against a default of 0."""
    from model.rotator_system import RotatorSystem

    rotator = RotatorSystem(default_port=None)
    rotator.smc = StallingSMC()
    rotator.step_deg = "10"
    result = rotator.move_relative_positive()
    assert result is not True, "an unpolled stage moved without confirmation"
    assert "unknown" in str(result).lower()


def test_rotator_4_stacked_clicks_accumulate_toward_the_guard():
    """Failure scenario B: at 20 with step 4, five clicks land at 40 because
    each one recomputes from a position that has not moved yet."""
    smc = StallingSMC()
    rotator = _rotator(smc, position="20")
    rotator.step_deg = "4"

    accepted, confirmations = 0, 0
    for _ in range(5):
        result = rotator.move_relative_positive()
        if result is True:
            accepted += 1
        else:
            confirmations += 1
    assert confirmations, (
        f"five +4 clicks from 20 reach 40 and none asked ({accepted} accepted)")


def test_rotator_4_the_commanded_target_tracks_accepted_moves():
    rotator = _rotator(StallingSMC(), position="0")
    rotator.step_deg = "10"
    assert rotator.move_relative_positive() is True
    assert rotator._commanded_target == 10.0
    assert rotator.move_relative_positive() is True
    assert rotator._commanded_target == 20.0
    # 30 is the limit, so the third click is the one that must ask.
    assert rotator.move_relative_positive() is True   # -> 30, still within
    assert rotator.move_relative_positive() is not True


def test_rotator_4_a_full_stop_makes_the_position_unknown_again():
    """The stage halts wherever it was, not at the commanded target."""
    rotator = _rotator(StallingSMC(), position="0")
    rotator.step_deg = "5"
    assert rotator.move_relative_positive() is True
    rotator.emergency_stop()
    assert rotator._commanded_target is None


def test_rotator_4_an_absolute_move_past_the_limit_still_asks():
    """The case that already worked. Pin it so the ROTATOR-4 fix did not
    trade one hole for another."""
    rotator = _rotator(StallingSMC(), position="0")
    rotator.target_deg = "40"
    assert rotator.move_absolute() is not True
    assert rotator.move_absolute(confirmed=True) is True


# ---------------------------------------------------------------------------
# TEMP-7 — the heater's FULL STOP is a latch, not a hope
#
# `send_settings` checked `_estop` at the top and then did ~40 lines of work
# before writing. A FULL STOP landing in that window was overwritten by the
# frame that was already being built: the operator hit stop, the heater went
# back to its setpoint, and nothing in the logs said so.
# ---------------------------------------------------------------------------


class StallingHeaterTransport:
    """A transport whose ordinary writes block until released.

    `is_open()` is the hook the tests use to land a FULL STOP *after*
    `send_settings` has passed its top-of-method check — that call sits
    between the check and the write lock.
    """

    def __init__(self, on_is_open=None):
        self.writes = []
        self.gate = threading.Event()
        self._on_is_open = on_is_open

    def is_open(self):
        if self._on_is_open is not None:
            self._on_is_open()
        return True

    def write_command(self, payload, priority=False, abort_if=None):
        self.writes.append((payload, priority))
        if not priority:
            self.gate.wait(2.0)


def _heater(transport):
    from model.temperature_system import TemperatureSystem
    heater = TemperatureSystem(None)
    heater.continue_reading = False
    heater.serial_conn = transport
    return heater


def test_temp_7_a_stop_landing_mid_build_is_not_overwritten():
    """The check-then-act: latch the stop between the top check and the write."""
    heater = _heater(None)
    transport = StallingHeaterTransport(on_is_open=lambda: heater._estop.set())
    heater.serial_conn = transport
    heater.setpoint = "300"

    heater.send_settings()

    assert transport.writes == [], (
        "a settings frame was written after FULL STOP latched: %r"
        % (transport.writes,))


def test_temp_7_emergency_stop_returns_promptly_behind_a_held_write_lock():
    transport = StallingHeaterTransport()
    heater = _heater(transport)

    holder = threading.Thread(target=heater.send_settings, daemon=True)
    holder.start()
    time.sleep(0.05)          # let it reach the blocking write inside the lock

    start = time.monotonic()
    heater.emergency_stop()
    elapsed = time.monotonic() - start

    transport.gate.set()
    holder.join(2.0)

    assert elapsed < 0.5, f"emergency_stop blocked the caller for {elapsed:.3f}s"
    assert heater._estop.is_set()


def test_temp_7_the_stop_frame_forces_through_a_busy_write_lock():
    transport = StallingHeaterTransport()
    heater = _heater(transport)

    holder = threading.Thread(target=heater.send_settings, daemon=True)
    holder.start()
    time.sleep(0.05)

    heater.emergency_stop()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if any(priority for _payload, priority in transport.writes):
            break
        time.sleep(0.01)

    transport.gate.set()
    holder.join(2.0)

    priority_writes = [p for p, priority in transport.writes if priority]
    assert priority_writes, (
        "the stop frame never reached the transport: %r" % (transport.writes,))
    assert priority_writes[-1].startswith("<0,"), priority_writes[-1]


def test_temp_7_an_ordinary_send_takes_the_lock_without_a_timeout():
    """Only the stop path forces. A normal Enter Settings must still queue."""
    transport = StallingHeaterTransport()
    heater = _heater(transport)
    transport.gate.set()
    heater.setpoint = "20"
    heater.send_settings()
    assert len(transport.writes) == 1, transport.writes
    payload, priority = transport.writes[0]
    assert priority is False, "an ordinary send must not take the priority path"
    assert payload.startswith("<20,"), payload
