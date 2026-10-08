"""P1, reshaped (owner ruling 2026-10-08): every heater reading is a RECORD
handed to subscribers, not a per-session CSV.

The owner: "the heater log should be stored with our db info for trials".
So the heater writes no file of its own any more. Each parsed reading
becomes one record (`Heater.READING_FIELDS`): what the board reported (its
timer, the temperature, its ramped setpoint) AND what was in force when it
was read - the endpoint, ramp, gains and offset of the last frame that
actually reached the wire, not what is typed in a box. The Transfer Map
subscribes (`subscribe_readings`) and keeps the records of an armed trial
in its store; the station's device log reads `last_reading` once a second.

Observation only: no byte on the port (the golden wire captures pin that),
nothing written to disk, and a subscriber that raises never stops the
reader.
"""
import pytest

from events import events
from model.heater import Heater

from test_heater_fakes import EventRecorder, FakePort


@pytest.fixture(autouse=True)
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path))
    for reset in (events.clear, events._debug_seen.clear):
        reset()
    return tmp_path


@pytest.fixture
def port():
    return FakePort()


@pytest.fixture
def heater(port):
    model = Heater(port=port)
    yield model
    model._stop_threads()


@pytest.fixture
def seen(heater):
    records = []
    heater.subscribe_readings(records.append)
    return records


def test_a_reading_reaches_a_subscriber_as_one_record(heater, seen):
    heater._parse_line("12.5 , 24.75 , 25.00\n")
    assert len(seen) == 1
    record = seen[0]
    assert set(record) == set(Heater.READING_FIELDS)
    assert record["board_timer_s"] == 12.5
    assert record["temperature_c"] == 24.75
    assert record["setpoint_c"] == 25.0
    assert record["wall_epoch_s"] > 0 and record["monotonic_s"] > 0


def test_last_reading_is_the_latest_record(heater, seen):
    assert heater.last_reading is None
    heater._parse_line("1.0,20.00,20.0")
    heater._parse_line("1.6,20.25,20.0")
    assert heater.last_reading == seen[-1]
    assert heater.last_reading["temperature_c"] == 20.25


def test_the_heater_writes_no_file_of_its_own(heater, seen, data_root):
    """The per-session CSV of the merged branch is gone (2026-10-08)."""
    for i in range(5):
        heater._parse_line(f"{i}.0,20.0,20.0")
    assert list(data_root.rglob("*")) == []
    assert not hasattr(heater, "reading_log_path")


def test_a_line_that_is_not_a_reading_reaches_no_subscriber(heater, seen):
    heater._parse_line("DEV: t")
    heater._parse_line("not,a,number")
    assert seen == []
    heater._parse_line("1.0,20.0,20.0")
    assert len(seen) == 1


def test_the_settings_in_force_are_those_of_the_last_frame_on_the_wire(
        heater, seen):
    heater.setpoint, heater.ramp_rate = 30.0, 10.0
    heater.p_term, heater.i_term, heater.d_term, heater.offset = 2.0, 0.5, 0.1, 0.0
    heater.apply_settings()
    # A box edited but never sent is NOT in force.
    heater.p_term = 99.0
    heater._parse_line("3.0,22.00,23.0")
    record = seen[-1]
    assert record["endpoint_c"] == 30.0
    assert record["ramp_s_per_c"] == 10.0
    assert (record["kp"], record["ki"], record["kd"]) == (2.0, 0.5, 0.1)
    assert record["offset_c"] == 0.0
    assert record["heater_on"] == 1


def test_after_a_stop_the_record_shows_the_heater_off_frame(heater, seen):
    heater.setpoint, heater.ramp_rate = 30.0, 10.0
    heater.apply_settings()
    heater.halt()
    heater._parse_line("9.0,29.00,21.0")
    record = seen[-1]
    assert record["endpoint_c"] == 0.0
    assert (record["kp"], record["ki"], record["kd"]) == (0, 0, 0)
    assert record["heater_on"] == 0


def test_the_power_on_reset_frame_counts_as_in_force(heater, seen):
    heater._send_reset_once()
    heater._parse_line("0.5,21.0,21.0")
    record = seen[-1]
    assert record["endpoint_c"] == 0.0 and record["ramp_s_per_c"] == 6.0
    assert record["heater_on"] == 0


def test_before_any_frame_the_settings_are_none(heater, seen):
    heater._parse_line("0.5,21.0,21.0")
    record = seen[-1]
    assert record["endpoint_c"] is None and record["kp"] is None
    assert record["heater_on"] is None


def test_a_superseded_settings_frame_is_not_recorded_as_in_force(
        heater, port, seen):
    heater.setpoint = 30.0
    original_write = port.write

    def stop_lands_first(payload, *, priority=False, abort_if=None):
        if payload.startswith(b"<30"):
            heater.halt()     # a stop arrives while the settings wait
        return original_write(payload, priority=priority, abort_if=abort_if)

    port.write = stop_lands_first
    with pytest.raises(Exception):
        heater.apply_settings()
    heater._parse_line("1.0,21.0,21.0")
    record = seen[-1]
    assert record["endpoint_c"] == 0.0 and record["heater_on"] == 0


def test_the_records_write_nothing_to_the_port(heater, port, seen):
    heater.setpoint = 30.0
    heater.apply_settings()
    before = list(port.frames)
    for i in range(20):
        heater._parse_line(f"{i * 0.58:.2f},{21 + i * 0.1:.2f},22.0")
    assert port.frames == before
    assert len(seen) == 20


def test_a_subscriber_that_raises_never_stops_the_reader(heater):
    def broken(record):
        raise RuntimeError("a subscriber's bug")

    heater.subscribe_readings(broken)
    with EventRecorder(events) as log:
        for i in range(10):
            heater._parse_line(f"{i}.0,20.0,20.0")
    times, _temperatures, _ = heater.history
    assert len(times) == 10 and heater.temperature == "20.00 °C"
    # Diagnostic only: no tray line, no popup.
    assert [e for e in log.seen if e.severity != "debug"] == []


def test_unsubscribe_ends_delivery_and_a_subscriber_is_kept_once(heater):
    records = []
    heater.subscribe_readings(records.append)
    heater.subscribe_readings(records.append)
    heater._parse_line("1.0,20.0,20.0")
    assert len(records) == 1
    heater.unsubscribe_readings(records.append)
    heater._parse_line("2.0,20.0,20.0")
    assert len(records) == 1
    heater.unsubscribe_readings(records.append)     # again: no error


def test_a_reading_announces_nothing(heater, seen):
    """The CSV announced its path once; a record is silent."""
    with EventRecorder(events) as log:
        for i in range(3):
            heater._parse_line(f"{i}.0,20.0,20.0")
    assert [e for e in log.seen if e.severity != "debug"] == []
