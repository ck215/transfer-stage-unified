"""The heater's stall watch and the board's thermocouple-fault line
(2026-11-24: sp 80 held while the block fell 77 -> 48.5 C, never heating).

Nothing here opens a real port: a FakePort and a hand-driven clock.
"""
import pytest

import events as events_module
from events import events
from model.heater import Heater

from test_heater_fakes import EventRecorder, FakePort


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def quiet_event_log():
    events.clear()
    events._debug_seen.clear()
    yield


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def heater(clock):
    model = Heater(port=FakePort(), clock=clock)
    model._commanded_setpoint = 80.0        # as after an Apply of 80 °C
    yield model
    model._stop_threads()


def feed(heater, clock, temps, setpoint=80.0, step=0.58):
    for t in temps:
        heater._parse_line(f"{clock.now:.2f}, {t:.2f}, {setpoint:.2f}")
        clock.now += step


def falling(start, stop, n):
    return [start + (stop - start) * i / (n - 1) for i in range(n)]


def test_a_held_setpoint_the_block_falls_away_from_warns_once(heater, clock):
    with EventRecorder(events) as seen:
        # The 2026-11-24 shape: under the setpoint by 8+ and falling, 4 min.
        feed(heater, clock, falling(71.0, 60.0, 420))
    warned = seen.titled(events_module.HEATER_NOT_HEATING)
    assert len(warned) == 1
    assert warned[0].severity == "warning"
    assert "Apply the settings again" in warned[0].message


def test_a_gap_that_is_closing_does_not_warn(heater, clock):
    with EventRecorder(events) as seen:
        # a normal approach from below: rising 1 °C every ~30 s
        feed(heater, clock, falling(60.0, 72.0, 420))
    assert seen.titled(events_module.HEATER_NOT_HEATING) == []


def test_a_short_dip_does_not_warn(heater, clock):
    with EventRecorder(events) as seen:
        feed(heater, clock, falling(71.0, 69.0, 150))   # ~87 s < 120 s
        feed(heater, clock, [75.0] * 10)
    assert seen.titled(events_module.HEATER_NOT_HEATING) == []


def test_nothing_warns_with_the_heater_off(heater, clock):
    heater._commanded_setpoint = None
    with EventRecorder(events) as seen:
        feed(heater, clock, falling(71.0, 40.0, 420))
    assert seen.titled(events_module.HEATER_NOT_HEATING) == []


def test_heating_again_resolves_the_warning(heater, clock):
    with EventRecorder(events) as seen:
        feed(heater, clock, falling(71.0, 60.0, 420))
        feed(heater, clock, falling(60.0, 75.0, 60))
    again = seen.titled("Heater Heating Again")
    assert len(again) == 1
    assert again[0].resolves == events_module.HEATER_NOT_HEATING


def test_the_board_error_line_warns_once_and_is_not_a_reading(heater, clock):
    with EventRecorder(events) as seen:
        for _ in range(5):
            feed(heater, clock, [56.25], setpoint=0.0)
            heater._parse_line("ERR: thermocouple status 0x81\n")
    faults = seen.titled(events_module.THERMOCOUPLE_FAULT)
    assert len(faults) == 1 and "0x81" in faults[0].message
    assert len(heater.history[0]) == 5          # the ERR lines added nothing


def test_the_fault_clears_after_the_error_lines_stop(heater, clock):
    with EventRecorder(events) as seen:
        heater._parse_line("ERR: thermocouple status 0x81")
        clock.now += Heater.SENSOR_FAULT_CLEAR_SECONDS + 1
        feed(heater, clock, [25.0], setpoint=0.0)
    back = seen.titled("Thermocouple Reading Again")
    assert len(back) == 1 and back[0].resolves == events_module.THERMOCOUPLE_FAULT
