"""I-4.2 / I-4.3 — the loops belong to the models, so every frontend matches.

This is the Web-parity proof. Manual mode used to be implemented once per
frontend: Tk pumped the gamepad every 50 ms from `_route_input`, PySide every
20 ms from `input_timer`, and the Web dashboard **not at all** — it had no
event loop to hang a timer on, so entering manual mode energized the coils
and then nothing happened. Three implementations, two rates, one missing.

There is no GUI anywhere in this file. That is the point: if manual mode
works here, it works in every frontend, because none of them own it any more.
"""

import time

import pytest

from model.probes import DCProbe


class RecordingTransport:
    def __init__(self):
        self.manual = []
        self.auton = []

    def write_command(self, payload):
        pass

    def enable(self):
        pass

    def disable(self):
        pass

    def send_manual_mode_command(self, params):
        self.manual.append(params)

    def send_autonomous_command(self, params):
        self.auton.append(params)

    def read_position(self):
        return None

    def close(self):
        pass


class FakePoller:
    """A gamepad that is present and reports a constant deflection."""

    def __init__(self, state=None):
        self.gamepad = object()
        self.state = state or {"x_axisStatus": 0.5, "y_axisStatus": 0.0,
                               "dpad_LR": 0, "dpad_UD": 0,
                               "LBumper": 0, "RBumper": 0}
        self.started = False

    def start_polling(self, gui=None, log_updater=None, activity_callback=None):
        self.started = True

    def get_mapped_state(self):
        return dict(self.state)

    def get_physical_controllers(self):
        return []

    def stop_polling(self):
        pass

    def close(self):
        pass

    def flush_neutral(self):
        pass


def _probe():
    probe = DCProbe("SIM", None)
    probe.serial_comm = RecordingTransport()
    probe.poller = FakePoller()
    return probe


def _wait_for(predicate, timeout=1.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_manual_mode_drives_hardware_with_no_gui_at_all():
    """The Web parity proof (I-4.2)."""
    probe = _probe()
    try:
        probe.enter_manual()
        assert probe.manual_flag is True
        assert _wait_for(lambda: len(probe.serial_comm.manual) >= 3), (
            "no manual commands were sent — this is exactly the state the Web "
            "dashboard was permanently in")
    finally:
        probe.teardown()


def test_the_model_starts_the_poller_itself():
    """The views used to do this, handing in their own event loop."""
    probe = _probe()
    try:
        probe.enable()
        assert probe.poller.started, "the model did not start its own poller"
    finally:
        probe.teardown()


def test_leaving_manual_mode_sends_one_neutral_frame():
    """Neutral on exit (I-4.2). Without it the last non-zero command stands
    and the axis keeps moving."""
    probe = _probe()
    try:
        probe.enter_manual()
        assert _wait_for(lambda: len(probe.serial_comm.manual) >= 2)

        # Leaving manual mode goes through the transition now; assigning the
        # flag is refused (I-3.4). `disable()` is the exit the operator has.
        probe.disable()
        probe.serial_comm.manual.clear()
        assert _wait_for(lambda: len(probe.serial_comm.manual) >= 1), (
            "leaving manual mode sent nothing")
        neutral = probe.serial_comm.manual[0]
        assert neutral.get("x_axisStatus", 0.0) == 0.0
    finally:
        probe.teardown()


def test_manual_commands_stop_at_once_when_full_stop_latches():
    probe = _probe()
    try:
        probe.enter_manual()
        assert _wait_for(lambda: len(probe.serial_comm.manual) >= 2)

        probe.emergency_stop()
        time.sleep(0.1)
        probe.serial_comm.manual.clear()
        time.sleep(0.2)
        assert probe.serial_comm.manual == [], (
            "manual commands continued after FULL STOP latched")
    finally:
        probe.teardown()


def test_the_model_samples_position_on_its_own_thread():
    """Views and /api/state read the cache; nobody samples on a render tick."""
    probe = _probe()
    reads = []

    class CountingTransport(RecordingTransport):
        def read_position(self):
            reads.append(time.monotonic())
            return None

    probe.serial_comm = CountingTransport()
    try:
        probe.enable()
        assert _wait_for(lambda: len(reads) >= 2, timeout=1.0), (
            "the model never sampled position by itself")
        assert probe.last_sample_time > 0
    finally:
        probe.teardown()


def test_a_stalled_transport_does_not_block_full_stop(monkeypatch):
    """I-4.3 — a slow read must not hold up the stop path."""
    probe = _probe()

    class StallingTransport(RecordingTransport):
        def read_position(self):
            time.sleep(1.0)
            return None

    probe.serial_comm = StallingTransport()
    try:
        probe.enable()
        time.sleep(0.15)  # let the sampler get stuck inside a read
        start = time.monotonic()
        probe.emergency_stop()
        elapsed = time.monotonic() - start
        assert probe.estop_latched
        assert elapsed < 0.5, f"FULL STOP waited {elapsed:.2f}s on a stalled sampler"
    finally:
        probe.stop_loops()


def test_teardown_stops_the_model_owned_loops():
    probe = _probe()
    probe.enable()
    assert _wait_for(lambda: probe._input_thread is not None)
    probe.teardown()
    assert _wait_for(lambda: not probe._input_thread.is_alive()), (
        "the input pump outlived the model")


# -- D-4: the input gate ----------------------------------------------------

def test_d4_a_closed_gate_stops_the_manual_pump_without_stopping_the_mode():
    """Gating, never stopping. Losing focus must not halt the bench.

    `flush_neutral` was the old answer and could not work: it zeroed the
    cached axis state, and the poll loop — four times faster than the pump —
    read the physical stick again and refilled it before the next send
    (GAMEPAD-8, PYSIDE-14, VIEW-TKINTER-9). Gating the *send* is what holds
    the axis.
    """
    probe = _probe()
    try:
        probe.enter_manual()
        assert _wait_for(lambda: len(probe.serial_comm.manual) >= 2)

        probe.set_input_gate(False)
        assert probe.input_gate_open is False
        # The mode is untouched: no stop packet, still in manual.
        assert probe.manual_flag is True
        assert probe.system_enabled is True

        # One neutral frame on the closing edge, then silence.
        time.sleep(probe.MANUAL_COMMAND_INTERVAL * 6)
        probe.serial_comm.manual.clear()
        time.sleep(probe.MANUAL_COMMAND_INTERVAL * 6)
        assert probe.serial_comm.manual == [], (
            "a closed gate must not keep writing motion")
    finally:
        probe.teardown()


def test_d4_the_closing_edge_sends_one_neutral_frame():
    """Otherwise the last non-zero command stands and the axis keeps moving."""
    probe = _probe()
    try:
        probe.enter_manual()
        assert _wait_for(lambda: len(probe.serial_comm.manual) >= 2)

        probe.serial_comm.manual.clear()
        probe.set_input_gate(False)
        assert _wait_for(lambda: len(probe.serial_comm.manual) >= 1), (
            "closing the gate sent nothing")
        assert probe.serial_comm.manual[0].get("x_axisStatus", 0.0) == 0.0
    finally:
        probe.teardown()


def test_d4_reopening_the_gate_resumes_the_pump():
    probe = _probe()
    try:
        probe.enter_manual()
        probe.set_input_gate(False)
        time.sleep(probe.MANUAL_COMMAND_INTERVAL * 6)
        probe.serial_comm.manual.clear()

        probe.set_input_gate(True)
        assert _wait_for(lambda: len(probe.serial_comm.manual) >= 1), (
            "reopening the gate did not resume manual input")
    finally:
        probe.teardown()


def test_d4_the_gate_is_open_by_default():
    probe = _probe()
    try:
        assert probe.input_gate_open is True
    finally:
        probe.teardown()
