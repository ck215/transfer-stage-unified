"""P1 (Tier P): every heater reading goes to a live per-session CSV.

Anyone, an agent included, must be able to watch a run that another process
is driving. The reader appends one row per reading to
`<data root>/heater/heater-<stamp>.csv` and flushes it, so a second process
tailing the file sees each reading as it lands. The row carries what the
board reported (its timer, the temperature, its ramped setpoint) AND what was
in force when it was read: the endpoint, ramp, gains and offset of the last
frame that actually reached the wire, not what is typed in a box.

The log is observation only: it never writes to the port (the golden wire
captures pin that), and a log that cannot be written never stops the reader.
"""
import csv
import os

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


def _rows(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _log_files(root):
    folder = root / "heater"
    return sorted(folder.glob("heater-*.csv")) if folder.exists() else []


def test_a_reading_lands_in_a_csv_under_the_data_root(heater, data_root):
    heater._parse_line("12.5 , 24.75 , 25.00\n")
    files = _log_files(data_root)
    assert len(files) == 1
    assert heater.reading_log_path == str(files[0])
    rows = _rows(files[0])
    assert len(rows) == 1
    row = rows[0]
    assert float(row["board_timer_s"]) == 12.5
    assert float(row["temperature_c"]) == 24.75
    assert float(row["setpoint_c"]) == 25.0
    assert row["wall_time"] and float(row["wall_epoch_s"]) > 0
    assert float(row["elapsed_s"]) >= 0


def test_each_row_is_readable_by_another_process_before_the_log_closes(
        heater, data_root):
    """Flushed per line: a tail/reader must not wait for a buffer to fill."""
    heater._parse_line("1.0,20.00,20.0")
    heater._parse_line("1.6,20.25,20.0")
    path = heater.reading_log_path
    # The model still holds the file open; a second reader sees both rows.
    assert [r["temperature_c"] for r in _rows(path)] == ["20.0", "20.25"]


def test_nothing_is_written_before_the_first_reading(heater, data_root):
    assert heater.reading_log_path == ""
    assert _log_files(data_root) == []


def test_a_line_that_is_not_a_reading_writes_no_row(heater, data_root):
    heater._parse_line("DEV: t")
    heater._parse_line("not,a,number")
    assert _log_files(data_root) == []
    heater._parse_line("1.0,20.0,20.0")
    assert len(_rows(heater.reading_log_path)) == 1


def test_the_settings_in_force_are_those_of_the_last_frame_on_the_wire(
        heater, data_root):
    heater.setpoint, heater.ramp_rate = 30.0, 10.0
    heater.p_term, heater.i_term, heater.d_term, heater.offset = 2.0, 0.5, 0.1, 0.0
    heater.apply_settings()
    # A box edited but never sent is NOT in force.
    heater.p_term = 99.0
    heater._parse_line("3.0,22.00,23.0")
    row = _rows(heater.reading_log_path)[-1]
    assert float(row["endpoint_c"]) == 30.0
    assert float(row["ramp_s_per_c"]) == 10.0
    assert (float(row["kp"]), float(row["ki"]), float(row["kd"])) == (2.0, 0.5, 0.1)
    assert float(row["offset_c"]) == 0.0
    assert row["heater_on"] == "1"


def test_after_a_stop_the_row_shows_the_heater_off_frame(heater, data_root):
    heater.setpoint, heater.ramp_rate = 30.0, 10.0
    heater.apply_settings()
    heater.halt()
    heater._parse_line("9.0,29.00,21.0")
    row = _rows(heater.reading_log_path)[-1]
    assert float(row["endpoint_c"]) == 0.0
    assert (float(row["kp"]), float(row["ki"]), float(row["kd"])) == (0, 0, 0)
    assert row["heater_on"] == "0"


def test_the_power_on_reset_frame_counts_as_in_force(heater, port, data_root):
    heater._send_reset_once()
    heater._parse_line("0.5,21.0,21.0")
    row = _rows(heater.reading_log_path)[-1]
    assert float(row["endpoint_c"]) == 0.0 and float(row["ramp_s_per_c"]) == 6.0
    assert row["heater_on"] == "0"


def test_before_any_frame_the_settings_columns_are_blank(heater, data_root):
    heater._parse_line("0.5,21.0,21.0")
    row = _rows(heater.reading_log_path)[-1]
    assert row["endpoint_c"] == "" and row["kp"] == "" and row["heater_on"] == ""


def test_a_superseded_settings_frame_is_not_recorded_as_in_force(
        heater, port, data_root):
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
    row = _rows(heater.reading_log_path)[-1]
    assert float(row["endpoint_c"]) == 0.0 and row["heater_on"] == "0"


def test_the_log_writes_nothing_to_the_port(heater, port, data_root):
    heater.setpoint = 30.0
    heater.apply_settings()
    before = list(port.frames)
    for i in range(20):
        heater._parse_line(f"{i * 0.58:.2f},{21 + i * 0.1:.2f},22.0")
    assert port.frames == before


def test_one_file_per_session_and_it_keeps_growing(heater, data_root):
    for i in range(5):
        heater._parse_line(f"{i}.0,20.0,20.0")
    assert len(_log_files(data_root)) == 1
    assert len(_rows(heater.reading_log_path)) == 5


def test_a_log_that_cannot_be_written_never_stops_the_reader(
        port, data_root, monkeypatch):
    blocker = data_root / "blocked"
    blocker.write_text("a file where the folder should be")
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(blocker))
    model = Heater(port=port)
    try:
        with EventRecorder(events) as log:
            for i in range(10):
                model._parse_line(f"{i}.0,20.0,20.0")
        times, temperatures, _ = model.history
        assert len(times) == 10 and model.temperature == "20.00 °C"
        warnings = [e for e in log.seen if e.severity == "warning"]
        assert len(warnings) == 1, [e.text for e in log.seen]
        assert model.reading_log_path == ""
    finally:
        model._stop_threads()


def test_close_closes_the_log_file(heater, data_root):
    heater._parse_line("1.0,20.0,20.0")
    handle = heater._reading_log
    assert handle is not None and not handle.closed
    heater.close()
    assert handle.closed
    # The rows are still there for the plot tool.
    assert len(_rows(heater.reading_log_path)) == 1


def test_the_log_folder_is_announced_once(heater, data_root):
    with EventRecorder(events) as log:
        for i in range(3):
            heater._parse_line(f"{i}.0,20.0,20.0")
    announced = [e for e in log.seen if "heater-" in e.text]
    assert len(announced) == 1
    assert os.path.basename(heater.reading_log_path) in announced[0].text
