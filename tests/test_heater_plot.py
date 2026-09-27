"""P1 (Tier P): `tools/heater_plot.py` renders a heater reading log.

The tool lives outside `src/` on purpose: matplotlib is a display library,
and nothing in the app imports it. It reads the CSV by its header, so it
needs nothing from the model, and it must cope with the file being written
while it reads (a half-written last line).
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


def test_the_default_log_is_the_newest_under_the_data_root(
        tool, tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path))
    folder = tmp_path / "heater"
    folder.mkdir()
    old, new = folder / "heater-20260926-100000.csv", folder / "heater-20260926-110000.csv"
    _write(old, [])
    _write(new, [])
    import os
    os.utime(old, (1, 1))
    assert tool.newest_log() == new


def test_the_cli_writes_the_png_beside_the_csv(tool, tmp_path):
    path = tmp_path / "heater-1.csv"
    _write(path, _step_response())
    assert tool.main([str(path)]) == 0
    assert (tmp_path / "heater-1.png").exists()
