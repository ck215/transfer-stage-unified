"""PROPOSED 2026-11-27 (fix/heater-safety-followup): the station's side of
the board's new cut-offs - its `ERR:` lines for the 260 C hard limit, a
clamped endpoint and lost mains zero-cross, the `BOOT: reset=watchdog`
line, and a board that stops reporting while its port is open.

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
    model._commanded_setpoint = 200.0
    yield model
    model._stop_threads()


def reading(heater, clock, temp, setpoint=200.0, step=0.58):
    heater._parse_line(f"{clock.now:.2f}, {temp:.2f}, {setpoint:.2f}")
    clock.now += step


# -- (b) the hard limit ------------------------------------------------------
def test_over_temperature_line_warns_once_with_ack_and_is_not_a_reading(heater, clock):
    with EventRecorder(events) as seen:
        for _ in range(6):
            reading(heater, clock, 261.0)
            heater._parse_line("ERR: over-temperature 261.00 C, limit 260.00 C\r\n")
    warns = seen.titled(events_module.HEATER_OVER_TEMPERATURE)
    assert len(warns) == 1 and "261.00" in warns[0].message
    assert events_module.HEATER_OVER_TEMPERATURE in events_module.ATTENTION
    assert len(heater.history[0]) == 6              # only the readings


def test_over_temperature_resolves_once_its_lines_stop(heater, clock):
    with EventRecorder(events) as seen:
        heater._parse_line("ERR: over-temperature 260.25 C, limit 260.00 C")
        reading(heater, clock, 255.0)
        assert not seen.titled("Heater Below Temperature Limit")
        clock.now += Heater.SENSOR_FAULT_CLEAR_SECONDS + 1
        reading(heater, clock, 249.0)
        reading(heater, clock, 248.0)
    back = seen.titled("Heater Below Temperature Limit")
    assert len(back) == 1
    assert back[0].resolves == events_module.HEATER_OVER_TEMPERATURE


def test_a_clamped_endpoint_warns_once(heater, clock):
    with EventRecorder(events) as seen:
        for _ in range(4):
            reading(heater, clock, 100.0, setpoint=101.0)
            heater._parse_line("ERR: endpoint clamped to 260.00 C")
    assert len(seen.titled(events_module.HEATER_SETPOINT_LIMITED)) == 1


def test_each_error_kind_warns_separately(heater, clock):
    with EventRecorder(events) as seen:
        heater._parse_line("ERR: thermocouple status 0x81")
        heater._parse_line("ERR: zero-cross lost")
        heater._parse_line("ERR: over-temperature 262.00 C, limit 260.00 C")
        heater._parse_line("ERR: something new")
    for title in (events_module.THERMOCOUPLE_FAULT,
                  events_module.HEATER_ZERO_CROSS_LOST,
                  events_module.HEATER_OVER_TEMPERATURE,
                  events_module.HEATER_BOARD_ERROR):
        assert len(seen.titled(title)) == 1, title


# -- (c) zero-cross loss -----------------------------------------------------
def test_zero_cross_lost_warns_while_readings_continue_and_resolves(heater, clock):
    with EventRecorder(events) as seen:
        for _ in range(10):                         # the board's millis() fallback
            reading(heater, clock, 150.0)
            heater._parse_line("ERR: zero-cross lost")
        assert len(seen.titled(events_module.HEATER_ZERO_CROSS_LOST)) == 1
        assert len(heater.history[0]) == 10
        for _ in range(12):                         # pulses back: no more lines
            reading(heater, clock, 150.0)
    back = seen.titled("Heater Zero-Cross Restored")
    assert len(back) == 1
    assert back[0].resolves == events_module.HEATER_ZERO_CROSS_LOST


# -- the watchdog's boot line -------------------------------------------------
def test_a_watchdog_reset_line_warns_once_per_session(heater, clock):
    with EventRecorder(events) as seen:
        for _ in range(3):
            heater._parse_line("BOOT: reset=watchdog")
        heater._parse_line("BOOT: reset=power-on")
    assert len(seen.titled(events_module.HEATER_WATCHDOG_RESET)) == 1
    assert len(heater.history[0]) == 0


def test_a_power_on_boot_line_says_nothing(heater, clock):
    with EventRecorder(events) as seen:
        heater._parse_line("BOOT: reset=power-on")
    assert not seen.titled(events_module.HEATER_WATCHDOG_RESET)


# -- (e) a silent board -------------------------------------------------------
def test_a_board_silent_for_5_s_warns_once_and_a_reading_resolves(heater, clock):
    with EventRecorder(events) as seen:
        reading(heater, clock, 80.0)
        for _ in range(40):                         # the reader's passes, 0.2 s apart
            heater._watch_silence()
            clock.now += 0.2
        warns = seen.titled(events_module.TEMPERATURE_BOARD_SILENT)
        assert len(warns) == 1
        assert events_module.TEMPERATURE_BOARD_SILENT in events_module.ATTENTION
        reading(heater, clock, 80.0)
    back = seen.titled("Temperature Board Reporting Again")
    assert len(back) == 1
    assert back[0].resolves == events_module.TEMPERATURE_BOARD_SILENT


def test_a_board_reporting_normally_never_warns(heater, clock):
    with EventRecorder(events) as seen:
        for _ in range(100):
            reading(heater, clock, 80.0)
            heater._watch_silence()
    assert not seen.titled(events_module.TEMPERATURE_BOARD_SILENT)


def test_a_closed_port_is_not_a_silent_board(heater, clock):
    with EventRecorder(events) as seen:
        reading(heater, clock, 80.0)
        heater.port._is_open = False
        for _ in range(40):
            heater._watch_silence()
            clock.now += 0.2
    assert not seen.titled(events_module.TEMPERATURE_BOARD_SILENT)


def test_the_watch_starts_afresh_after_a_reconnect(heater, clock):
    with EventRecorder(events) as seen:
        reading(heater, clock, 80.0)
        heater.port._is_open = False
        heater._watch_silence()
        clock.now += 30.0                           # down for 30 s
        heater.port._is_open = True
        heater._watch_silence()                     # first look after it
        clock.now += 2.0
        heater._watch_silence()
        assert not seen.titled(events_module.TEMPERATURE_BOARD_SILENT)
        clock.now += 4.0
        heater._watch_silence()
    assert len(seen.titled(events_module.TEMPERATURE_BOARD_SILENT)) == 1


# -- the station's cap matches the board's hard limit -------------------------
def test_the_station_refuses_a_setpoint_the_board_would_clamp(heater):
    assert Heater.MAX_SETPOINT == 260.0
    assert Heater.PARAMS["setpoint"].maximum == 260.0
    inputs = {name: getattr(heater, name) for name in Heater.FRAME_FIELDS}
    assert heater.run("apply_settings", inputs=dict(inputs, setpoint=260.0)).is_ok
    refused = heater.run("apply_settings", inputs=dict(inputs, setpoint=260.5))
    assert refused.is_refused and "260" in refused.reason
