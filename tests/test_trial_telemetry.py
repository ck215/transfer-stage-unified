"""`model.trial_telemetry`: every stream of a trial on one clock (REC-2).

The station is real where it can be: the SIM Stepper Probe and Temperature
Controller, and Red Percent over an injected capture (no screen), all in a
real Controller, so Red Percent's rows arrive through its real `subscribe`
hook on its real run thread. The rotator, a pad and the misbehaving models
are the core fakes' `FakeModel` with the attributes a reader looks for (the
SIM Rotator has no stage to read).
"""
import threading
import time
import uuid

import numpy
import pytest

from controller.controller import Controller
from devices.gamepad import NEUTRAL
from devices.screen import Screen
from events import events
from model.heater import Heater
from model.probe import StepperProbe
from model.red_monitor import RedMonitor
from model.trial_telemetry import TrialTelemetry
from test_core_fakes import FakeClock, FakeModel

#: Every row of these tests is stamped on this clock: far from both
#: `time.monotonic()` and `time.time()`, so a row stamped on either is caught.
OFFSET = 1.0e7


def offset_clock():
    return OFFSET + time.monotonic()


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _streams(rows):
    return {stream for _t, stream, _v in rows}


def _values(rows, stream):
    return [v for _t, s, v in rows if s == stream]


class RedCapture:
    """Red Percent's capture instance: a 10x10 RGB frame whose red rows
    change every HOLD grabs. Each picture is held for two reads, as a screen
    holds what it shows: the run loop takes a sample only when two reads at
    least 5 ms apart agree (CAP-1, 2026-10-07). Paced, not a spin."""
    HOLD = 4

    def __init__(self):
        self.grabs = 0

    def grab(self, region):
        self.grabs += 1
        time.sleep(0.005)
        frame = numpy.zeros((10, 10, 3), dtype=numpy.uint8)
        frame[:1 + (self.grabs // self.HOLD) % 9, :] = [200, 0, 0]
        return frame

    def close(self):
        pass


class Tilt(FakeModel):
    """A rotator as the readers see one."""

    def __init__(self, angle=1.5):
        super().__init__()
        self.angle = angle

    @property
    def position_deg(self):
        return self.angle

    @property
    def motion_state(self):
        return "Ready"


class BrokenTilt(FakeModel):
    reads = 0

    @property
    def position_deg(self):
        BrokenTilt.reads += 1
        raise RuntimeError("link lost")


class HangingTilt(FakeModel):
    """Reading the angle blocks (a wedged property) once `armed`."""

    def __init__(self):
        super().__init__()
        self.armed = False
        self.entered = threading.Event()
        self.release = threading.Event()

    @property
    def position_deg(self):
        if self.armed:
            self.entered.set()
            self.release.wait(30)
        return 0.0


class Pad:
    """The Gamepad surface the readers use."""

    def __init__(self, levels=None, bound=True):
        self.is_bound = bound
        self.levels = dict(NEUTRAL, **(levels or {}))


class PadModel(FakeModel):
    def __init__(self, pad):
        super().__init__()
        self.gamepad = pad

    @property
    def gamepad_name(self):
        return "Pad0"


class TextHeater(FakeModel):
    """A heater whose readings are the live model's text."""

    def __init__(self):
        super().__init__()
        self.temperature = "23.40 °C"
        self.heating_to = "45.0 °C"
        self.setpoint = 45
        self.connection = "verified"
        self.is_heating = True

    @property
    def is_active(self):
        return self.is_heating


@pytest.fixture
def station(tmp_path):
    """A SIM probe and heater, Red Percent mid-run and a tilt, in one
    Controller."""
    controller = Controller()
    probe = StepperProbe(port=None, gamepad=None, sim=True)
    heater = Heater(port=None, sim=True)
    red = RedMonitor(screen=Screen(factory=RedCapture))
    red.output_root = tmp_path / "runs"
    tilt = Tilt()
    for name, model in (("Stepper Probe", probe), ("Temperature Controller", heater),
                        ("Red Percent", red), ("Rotator", tilt)):
        controller.add(name, model)
    red.set_region(0, 0, 10, 10)
    red.start_run(confirmed=True)
    yield controller, probe, heater, red, tilt
    if red.is_running:
        red.end_run()
    controller.close()


def test_every_stream_lands_on_the_one_clock(station):
    controller, probe, heater, red, tilt = station
    telemetry = TrialTelemetry(controller, clock=offset_clock, poll_hz=50)
    t_start = offset_clock()
    telemetry.start("T1")
    assert _wait_for(lambda: 1.5 in _values(telemetry.rows(), "rotator.angle"))
    probe._note_position((10, 20, 30))        # one POS line, as the sample loop does
    tilt.angle = 3.0
    assert _wait_for(lambda: "stepper_probe.x" in _streams(telemetry.rows())), \
        f"no probe stream: {sorted(_streams(telemetry.rows()))}"
    assert _wait_for(lambda: 3.0 in _values(telemetry.rows(), "rotator.angle")), \
        f"no rotator change: {_values(telemetry.rows(), 'rotator.angle')}"
    # Red Percent rows arrive at the source rate now (CAP-1), not per grab:
    # wait for the second one before stopping, so the "row per row" check
    # below has two to count.
    assert _wait_for(lambda: len(_values(telemetry.rows(), "red_percent.row_red")) >= 2), \
        f"fewer than two red rows: {sorted(_streams(telemetry.rows()))}"
    text = f"telemetry {uuid.uuid4().hex}"
    t_before = offset_clock()
    events.info("Telemetry Test", text, source="test")
    t_after = offset_clock()
    rows = telemetry.stop()
    t_stop = offset_clock()

    streams = _streams(rows)
    expected = {
        "telemetry.trial",
        "stepper_probe.x", "stepper_probe.y", "stepper_probe.z",
        "stepper_probe.vx", "stepper_probe.position_time", "stepper_probe.mode",
        "stepper_probe.full_speed", "stepper_probe.man_full_speed",
        "stepper_probe.gamepad_connected", "stepper_probe.gamepad_held",
        "temperature_controller.temperature", "temperature_controller.setpoint",
        "temperature_controller.heating_to", "temperature_controller.heating",
        "rotator.angle", "rotator.motion",
        "red_percent.red", "red_percent.row_red",
        "events.info",
    }
    assert expected <= streams, expected - streams
    assert all(t_start <= t <= t_stop for t, _s, _v in rows), "one clock, every row"
    assert [t for t, _s, _v in rows] == sorted(t for t, _s, _v in rows)
    assert all(isinstance(v, (float, str)) for _t, _s, v in rows)
    assert ("telemetry.trial", "T1") in [(s, v) for _t, s, v in rows]
    assert _values(rows, "stepper_probe.x")[-1] == 10.0
    assert _values(rows, "stepper_probe.mode") == ["disabled"]
    assert _values(rows, "rotator.angle") == [1.5, 3.0]
    line = [(t, v) for t, s, v in rows if s == "events.info" and text in v]
    assert len(line) == 1 and t_before <= line[0][0] <= t_after
    assert len(_values(rows, "red_percent.row_red")) >= 2, "a row per Red Percent row"


def test_event_log_lines_are_captured_with_their_timestamp():
    clock = FakeClock(500.0)
    telemetry = TrialTelemetry(Controller(), clock=clock, poll_hz=50)
    telemetry.start(7)
    clock.now = 612.25
    text = f"line {uuid.uuid4().hex}"
    events.warn("Telemetry Probe", text, source="test")
    rows = telemetry.stop()
    events.warn("Telemetry Probe", f"after stop {text}", source="test")
    assert (612.25, "events.warning", f"[test] Telemetry Probe: {text}") in rows
    assert telemetry.rows() == rows
    assert not any("after stop" in str(v) for _t, _s, v in telemetry.rows())
    assert ("telemetry.trial", "7") in [(s, v) for _t, s, v in rows]


def test_a_raising_model_is_counted_and_skipped():
    controller = Controller()
    controller.add("Broken", BrokenTilt())
    controller.add("Rotator", Tilt(angle=2.0))
    telemetry = TrialTelemetry(controller, clock=offset_clock, poll_hz=100)
    try:
        BrokenTilt.reads = 0
        telemetry.start("T2")
        assert _wait_for(lambda: telemetry.failures >= 5)
        assert telemetry.is_recording, "the poll thread lives on"
        rows = telemetry.stop()
    finally:
        controller.close()
    assert "broken.angle" not in _streams(rows)
    assert _values(rows, "rotator.angle") == [2.0], "the healthy model still polls"
    assert telemetry.failure_counts["broken.tilt"] == BrokenTilt.reads >= 5
    assert telemetry.failures == sum(telemetry.failure_counts.values())


def test_a_hook_handed_garbage_never_raises_into_the_caller():
    """The EventLog and Red Percent call these on their own threads: a bad
    argument is a counted failure, never an exception into their loop."""
    telemetry = TrialTelemetry(Controller(), clock=offset_clock)
    telemetry.start("T3")
    try:
        telemetry._on_event(object())              # no severity, no text
        telemetry._red_row_hook("red_percent", telemetry._generation)()
        assert telemetry.failures == 2
    finally:
        telemetry.stop()


def test_stop_is_bounded_when_a_model_read_hangs():
    controller = Controller()
    hanging = HangingTilt()
    controller.add("Rotator", hanging)
    telemetry = TrialTelemetry(controller, clock=offset_clock, poll_hz=50)
    telemetry.STOP_TIMEOUT = 0.3
    telemetry.start("T4")
    try:
        assert _wait_for(lambda: "rotator.angle" in _streams(telemetry.rows()))
        hanging.armed = True
        assert hanging.entered.wait(5)
        started = time.monotonic()
        rows = telemetry.stop()
        assert time.monotonic() - started < 1.0
        assert telemetry.stop_timeouts == 1
        assert not telemetry.is_recording
    finally:
        hanging.release.set()
        controller.close()
    time.sleep(0.1)                                # the poll thread wakes up late
    assert telemetry.rows() == rows, "nothing lands after stop"


def test_polling_never_holds_up_the_station_stop(station):
    controller, *_ = station
    telemetry = TrialTelemetry(controller, clock=offset_clock, poll_hz=200)
    telemetry.start("T5")
    try:
        assert _wait_for(lambda: len(telemetry.rows()) > 10)
        started = time.monotonic()
        results = controller.estop_all()
        elapsed = time.monotonic() - started
    finally:
        telemetry.stop()
    assert all(results.values()), results
    assert elapsed < controller.ESTOP_ALL_BUDGET + 0.5, elapsed


def test_a_polled_stream_is_recorded_when_it_changes():
    controller = Controller()
    tilt = Tilt(angle=1.0)
    controller.add("Rotator", tilt)
    telemetry = TrialTelemetry(controller, clock=offset_clock, poll_hz=100)
    try:
        telemetry.start("T6")
        time.sleep(0.15)                           # ~15 polls of one value
        tilt.angle = 4.0
        assert _wait_for(lambda: 4.0 in _values(telemetry.rows(), "rotator.angle"))
        rows = telemetry.stop()
    finally:
        controller.close()
    assert _values(rows, "rotator.angle") == [1.0, 4.0]
    assert _values(rows, "rotator.motion") == ["Ready"]


def test_gamepad_connected_and_held():
    controller = Controller()
    pad = Pad({"axis_x": 0.6})
    controller.add("Chuck Positioner", PadModel(pad))
    telemetry = TrialTelemetry(controller, clock=offset_clock, poll_hz=100)
    try:
        telemetry.start("T7")
        assert _wait_for(lambda: 1.0 in _values(telemetry.rows(),
                                                 "chuck_positioner.gamepad_held"))
        pad.levels = dict(NEUTRAL)
        pad.is_bound = False
        assert _wait_for(lambda: 0.0 in _values(telemetry.rows(),
                                                 "chuck_positioner.gamepad_held"))
        rows = telemetry.stop()
    finally:
        controller.close()
    assert _values(rows, "chuck_positioner.gamepad_connected") == [1.0, 0.0]
    assert _values(rows, "chuck_positioner.gamepad_held") == [1.0, 0.0]


def test_heater_text_readings_become_numbers():
    controller = Controller()
    heater = TextHeater()
    controller.add("Temperature Controller", heater)
    telemetry = TrialTelemetry(controller, clock=offset_clock, poll_hz=100)
    try:
        telemetry.start("T8")
        assert _wait_for(lambda: "temperature_controller.heating"
                         in _streams(telemetry.rows()))
        heater.temperature = "Disconnected"
        heater.heating_to = ""
        heater.is_heating = False
        assert _wait_for(lambda: 0.0 in _values(telemetry.rows(),
                                                 "temperature_controller.heating"))
        rows = telemetry.stop()
    finally:
        controller.close()
    assert _values(rows, "temperature_controller.temperature") == [23.4, "Disconnected"]
    assert _values(rows, "temperature_controller.heating_to") == [45.0, ""]
    assert _values(rows, "temperature_controller.setpoint") == [45.0]
    assert _values(rows, "temperature_controller.heating") == [1.0, 0.0]


def test_red_percent_rows_stop_arriving_after_stop(station):
    controller, probe, heater, red, tilt = station
    telemetry = TrialTelemetry(controller, clock=offset_clock, poll_hz=50)
    telemetry.start("T9")
    assert _wait_for(lambda: "red_percent.row_red" in _streams(telemetry.rows()))
    rows = telemetry.stop()
    assert red.is_running
    assert red._subscribers == (), "the row hook is unsubscribed"
    time.sleep(0.1)
    assert telemetry.rows() == rows


def test_start_twice_refuses_stop_is_idempotent_and_a_new_trial_starts_clean():
    controller = Controller()
    controller.add("Rotator", Tilt(angle=1.0))
    telemetry = TrialTelemetry(controller, clock=offset_clock, poll_hz=100)
    try:
        assert telemetry.stop() == []               # before any start
        telemetry.start("A")
        with pytest.raises(RuntimeError):
            telemetry.start("B")
        assert _wait_for(lambda: "rotator.angle" in _streams(telemetry.rows()))
        first = telemetry.stop()
        assert telemetry.stop() == first
        telemetry.start("B")
        assert _wait_for(lambda: "rotator.angle" in _streams(telemetry.rows()))
        second = telemetry.stop()
    finally:
        controller.close()
    assert ("telemetry.trial", "A") in [(s, v) for _t, s, v in first]
    assert ("telemetry.trial", "A") not in [(s, v) for _t, s, v in second]
    assert _values(second, "rotator.angle") == [1.0], "a new trial records afresh"
    assert telemetry.trial_id == "B"
