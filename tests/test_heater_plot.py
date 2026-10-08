"""P1, reshaped 2026-10-08: `tools/heater_plot.py` renders heater readings.

The tool lives outside `src/` on purpose: matplotlib is a display library,
and nothing in the app imports it. Since the owner's ruling of 2026-10-08
the heater writes no CSV of its own; the tool reads the readings where they
are kept now:

- a trial's `trial_heater` rows in its Transfer Map store
  (`--trial N --store PATH`), or its `heater.csv` / the export's heater file;
- the station's device log (`--log PATH --from/--to`; default: the data
  root's `logs/device_log.sqlite`, the last two hours), the heater's
  `reading.*` keys of the Temperature Controller;
- a CSV by its header (the merged branch's logs still plot).

It must cope with a source being written while it reads.
"""
import csv
import importlib.util
import pathlib

import pytest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "heater_plot.py"
COLUMNS = ("wall_time", "wall_epoch_s", "elapsed_s", "board_timer_s",
           "temperature_c", "setpoint_c", "endpoint_c", "ramp_s_per_c",
           "kp", "ki", "kd", "offset_c", "heater_on")


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("heater_plot", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path, readings, *, endpoint=30.0, tail=""):
    """`readings`: (elapsed, temperature, setpoint)."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for elapsed, temperature, setpoint in readings:
            writer.writerow(["2026-09-26T12:00:00", 1.0 + elapsed, elapsed,
                             elapsed, temperature, setpoint, endpoint, 10,
                             2, 0.5, 0.1, 0, 1 if endpoint else 0])
        handle.write(tail)


def _step_response(peak=31.2, final=30.0):
    """Rise from 22 to `peak` at t=300, then settle to `final` by t=600."""
    out = []
    for i in range(0, 1200):
        t = i * 0.58
        if t < 300:
            temp = 22 + (peak - 22) * t / 300
        elif t < 600:
            temp = peak + (final - peak) * (t - 300) / 300
        else:
            temp = final
        out.append((t, round(temp, 2), min(30.0, 22 + t / 10)))
    return out


def test_the_tool_exists_outside_src():
    assert TOOL.exists()
    assert "src" not in TOOL.relative_to(TOOL.parents[1]).parts


def test_it_reads_the_log_by_header(tool, tmp_path):
    path = tmp_path / "heater-1.csv"
    _write(path, [(0, 22.0, 22.0), (0.58, 22.25, 22.0)])
    rows = tool.load(path)
    assert [r["temperature_c"] for r in rows] == [22.0, 22.25]
    assert rows[0]["endpoint_c"] == 30.0


def test_a_half_written_last_line_is_skipped_not_fatal(tool, tmp_path):
    path = tmp_path / "heater-1.csv"
    _write(path, [(0, 22.0, 22.0), (0.58, 22.25, 22.0)],
           tail="2026-09-26T12:00:01,2.1,1.1")
    assert len(tool.load(path)) == 2


def test_the_analysis_measures_overshoot_above_the_endpoint(tool, tmp_path):
    path = tmp_path / "heater-1.csv"
    _write(path, _step_response(peak=31.2))
    stats = tool.analyse(tool.load(path))
    assert stats["endpoint_c"] == 30.0
    assert stats["overshoot_c"] == pytest.approx(1.2, abs=0.01)
    assert stats["peak_c"] == pytest.approx(31.2, abs=0.01)
    assert stats["peak_s"] == pytest.approx(300, abs=1)
    assert stats["start_c"] == pytest.approx(22.0)
    # First reaching 30 on the way up: 22 + 9.2*t/300 = 30 -> t ~ 261.
    assert stats["reach_s"] == pytest.approx(261, abs=2)
    assert stats["settle_s"] is not None and stats["settle_s"] > 300
    assert stats["sse_c"] == pytest.approx(0.0, abs=0.01)


def test_no_overshoot_reads_as_zero(tool, tmp_path):
    path = tmp_path / "heater-1.csv"
    _write(path, [(i * 0.58, min(29.9, 22 + i * 0.02), 30.0)
                  for i in range(800)])
    stats = tool.analyse(tool.load(path))
    assert stats["overshoot_c"] == 0.0
    assert stats["reach_s"] is None


def test_it_renders_a_png(tool, tmp_path):
    path = tmp_path / "heater-1.csv"
    _write(path, _step_response())
    out = tmp_path / "out.png"
    assert tool.render(path, out) == out
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_an_empty_log_still_renders(tool, tmp_path):
    path = tmp_path / "heater-1.csv"
    _write(path, [])
    out = tool.render(path, tmp_path / "out.png")
    assert out.exists()


def test_the_cli_writes_the_png_beside_the_csv(tool, tmp_path):
    path = tmp_path / "heater-1.csv"
    _write(path, _step_response())
    assert tool.main([str(path)]) == 0
    assert (tmp_path / "heater-1.png").exists()


# -- the trial store and the device log (2026-10-08) ------------------------------

import sqlite3  # noqa: E402
import time  # noqa: E402

from controller import device_log as dl  # noqa: E402
from model import transfer_map as tm  # noqa: E402
from model.heater import Heater  # noqa: E402

from test_heater_fakes import FakePort  # noqa: E402


def _store_with_trial(path, readings, *, endpoint=30.0):
    """A trial store with one trial and its heater rows. -> trial id."""
    store = tm.TrialStore(path)
    trial = store.insert({"status": "recorded", "tip_id": "T1"})
    store.update(trial, {}, heater=[
        (t, 1e9 + t, t, temperature, setpoint, endpoint, 10.0, 2.0, 0.5, 0.1,
         0.0, 1, "Temperature Controller")
        for t, temperature, setpoint in readings])
    other = store.insert({"status": "recorded", "tip_id": "T1"})
    store.update(other, {}, heater=[(0.0, 1e9, 0.0, 99.0, 99.0, 99.0, 1, 1, 1,
                                     1, 0, 1, "Temperature Controller")])
    return trial


def test_it_reads_a_trials_heater_rows_from_its_store(tool, tmp_path):
    path = tmp_path / "map.sqlite"
    trial = _store_with_trial(path, _step_response())
    rows = tool.load_trial(path, trial)
    assert len(rows) == 1200
    assert rows[0]["temperature_c"] == 22.0 and rows[0]["endpoint_c"] == 30.0
    stats = tool.analyse(rows)
    assert stats["overshoot_c"] == pytest.approx(1.2, abs=0.01)
    assert tool.load_trial(path, trial + 50) == []


def test_it_reads_a_trials_heater_csv_by_its_header(tool, tmp_path):
    path = tmp_path / "heater.csv"
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(tm.HEATER_COLUMNS)
        for t, temperature in ((0.0, 22.0), (0.6, 22.5)):
            writer.writerow([3, t, 1e9 + t, t, temperature, 25.0, 30.0, 10, 2,
                             0.5, 0.1, 0, 1, "Temperature Controller"])
    rows = tool.load(path)
    assert [(r["elapsed_s"], r["temperature_c"]) for r in rows] == [
        (0.0, 22.0), (0.6, 22.5)]
    assert rows[0]["endpoint_c"] == 30.0


def _device_log_with_heater(path, temperatures, start=1_000_000.0):
    """A device log the station's own way: a real Heater's readings,
    sampled once a second by the DeviceLog. -> the sample times."""
    heater = Heater(port=FakePort())
    clock = [start]

    class Station:
        models = {"Temperature Controller": heater}

    log = dl.DeviceLog(Station(), path, clock=lambda: clock[0], sample_hz=0)
    assert log.start()
    times = []
    try:
        heater.setpoint, heater.ramp_rate = 30.0, 10.0
        heater.apply_settings()
        for i, temperature in enumerate(temperatures):
            heater._parse_line(f"{i:.1f},{temperature:.2f},{min(30, 22 + i):.2f}")
            log.sample_once()
            times.append(clock[0])
            clock[0] += 1.0
    finally:
        assert log.close()
        heater._stop_threads()
    return times


def test_it_reads_the_heater_from_the_device_log(tool, tmp_path):
    path = tmp_path / "device_log.sqlite"
    times = _device_log_with_heater(path, [22.0, 22.0, 23.5, 25.0])
    rows = tool.load_log(path)
    assert [r["temperature_c"] for r in rows] == [22.0, 22.0, 23.5, 25.0]
    assert [r["elapsed_s"] for r in rows] == [0.0, 1.0, 2.0, 3.0]
    # A key written only when it changed is carried forward.
    assert [r["setpoint_c"] for r in rows] == [22.0, 23.0, 24.0, 25.0]
    assert all(r["endpoint_c"] == 30.0 and r["heater_on"] == 1 for r in rows)
    assert rows[0]["wall_epoch_s"] == times[0]


def test_the_device_log_window_is_from_to(tool, tmp_path):
    path = tmp_path / "device_log.sqlite"
    times = _device_log_with_heater(path, [20.0, 21.0, 22.0, 23.0, 24.0])
    rows = tool.load_log(path, start=times[1], end=times[3])
    assert [r["temperature_c"] for r in rows] == [21.0, 22.0, 23.0]
    # The endpoint was last written before the window: still known.
    assert rows[0]["endpoint_c"] == 30.0


def test_a_time_is_read_as_iso_or_seconds(tool):
    assert tool.parse_time("1000000.5") == 1000000.5
    stamp = tool.parse_time("2026-10-08T12:30:00")
    assert time.localtime(stamp)[:6] == (2026, 10, 8, 12, 30, 0)


def test_the_cli_plots_a_trial(tool, tmp_path):
    path = tmp_path / "map.sqlite"
    trial = _store_with_trial(path, _step_response())
    out = tmp_path / "trial.png"
    assert tool.main(["--trial", str(trial), "--store", str(path),
                      "-o", str(out)]) == 0
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_cli_plots_a_window_of_the_device_log(tool, tmp_path, capsys):
    path = tmp_path / "device_log.sqlite"
    times = _device_log_with_heater(path, [20.0, 21.0, 22.0])
    out = tmp_path / "log.png"
    assert tool.main(["--log", str(path), "--from", str(times[0]),
                      "--to", str(times[-1]), "-o", str(out)]) == 0
    assert out.exists()
    assert tool.main(["--log", str(path), "--from", str(times[0]),
                      "--stats"]) == 0
    assert '"readings": 3' in capsys.readouterr().out


def test_the_default_source_is_the_device_log_under_the_data_root(
        tool, tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path))
    assert tool.default_log() == tmp_path / "logs" / "device_log.sqlite"
    assert tool.main(["--stats"]) == 1          # no log there yet: says so


def test_a_missing_trial_or_store_is_one_message_not_a_traceback(
        tool, tmp_path, capsys):
    assert tool.main(["--trial", "3", "--store", str(tmp_path / "no.sqlite")]) == 1
    assert "no.sqlite" in capsys.readouterr().err
