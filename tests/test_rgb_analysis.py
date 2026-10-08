"""`model.rgb_analysis` — RunLog, MonitorRun and the RgbAnalysis model
("RGB Analysis"; `model.red_monitor` / `RedMonitor` / "Red Percent" until RG-3,
2026-10-07). It also holds RG-2's factor tests: the columns this model
publishes are what `transfer_map_analysis` reads as a factor.

Ported from `tests/core/test_monitoring_run.py`,
`tests/core/test_redpercent_run_artifacts.py`,
`tests/core/test_redpercent_datalog.py`, `tests/core/test_redpercent.py`,
`tests/core/test_redpercent4_velocity_reads.py` and
`tests/hardware/test_redpercent_dead_fields.py`.

Nothing here opens a display: a `Screen` is built with an injected capture
factory that hands back real numpy frames, so the detector, the loop and the
device seam are all exercised for real while the OS is not. `numpy` is the
genuine article under `tests/conftest.py`; `mss` and matplotlib are not, which
is exactly why the factory is injected.
"""
import csv
import json
import threading
import time

import numpy
import pytest

from devices.screen import Screen
from model import plot_data
from model.rgb_analysis import MonitorRun, RgbAnalysis, RunLog
from result import Refused, NeedsConfirm


# ---------------------------------------------------------------------
# fixtures and fakes
# ---------------------------------------------------------------------

def red_frame(red_rows=2, height=10, width=10):
    """A `height x width` RGB frame with `red_rows` rows of pure red."""
    frame = numpy.zeros((height, width, 3), dtype=numpy.uint8)
    frame[:red_rows, :] = [200, 0, 0]
    return frame


class FakeCapture:
    """Frames are served in order and the last one repeats. With `varying`
    (the default), each repeat carries a different number of red rows, so
    the red percent changes on EVERY picture and change-triggered sampling
    logs a row per frame.

    Each picture is held for HOLD reads, as a screen holds what it shows
    (CAP-1, 2026-10-07): the run loop takes a sample only when two reads
    agree, and a picture that changed on every read would be exactly the
    repaint transient it now refuses."""

    HOLD = 2

    def __init__(self, frames, delay, varying=True):
        self._frames, self._delay, self._varying = frames, delay, varying
        self.grabs = 0

    def grab(self, region):
        self.grabs += 1
        if self._delay:
            time.sleep(self._delay)
        shown = (self.grabs - 1) // self.HOLD + 1      # the picture on screen
        index = min(shown - 1, len(self._frames) - 1)
        frame = self._frames[index]
        repeats = shown - len(self._frames)
        if self._varying and repeats > 0:
            frame = frame.copy()
            rows = 1 + repeats % max(1, frame.shape[0] - 1)
            frame[:, :] = 0
            frame[:rows, :] = [200, 0, 0]
        return frame

    def close(self):
        pass


def fake_screen(frames=None, delay=0.001, varying=True):
    frames = frames if frames is not None else [red_frame()]
    return Screen(factory=lambda: FakeCapture(frames, delay, varying))


class FakeProbe:
    """A position source, duck-typed exactly as `RgbAnalysis` asks for one."""

    def __init__(self, position=(0.0, 0.0, 0.0), position_time=None):
        self.position = position
        self.position_time = position_time

    @property
    def position_age(self):
        if self.position_time is None:
            return None
        return max(0.0, time.monotonic() - self.position_time)

    def report(self, position, position_time):
        self.position = position
        self.position_time = position_time


@pytest.fixture
def monitor(tmp_path):
    model = RgbAnalysis(screen=fake_screen())
    model.output_root = tmp_path / "runs"
    model.run_name = "C001"
    model.open()
    yield model
    model.close()


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _started(model, **kwargs):
    for name, value in kwargs.items():
        setattr(model, name, value)
    model.set_region(0, 0, 10, 10)
    return model.start_run(confirmed=True)


# ---------------------------------------------------------------------
# RunLog
# ---------------------------------------------------------------------

def test_run_log_headers_carry_units_and_never_say_stepper():
    log = RunLog(["X", "Y"])
    header = log.headers

    assert header[0] == plot_data.TIME_COLUMN
    assert header[1] == plot_data.RED_COLUMN
    assert "x_position_steps" in header
    assert "x_velocity_steps_per_s" in header
    assert header.count(plot_data.AGE_COLUMN) == 1, "one age column per row"
    assert not any("Stepper" in name for name in header)


def test_run_log_writes_an_unmeasured_value_as_an_empty_cell_never_zero(tmp_path):
    """ERRORS-7: `0.0` is a position the stage can actually be at, so a failed
    read must not be written as one."""
    log = RunLog(["X"])
    log.add(0.0, 10.0, {"X": 1.0}, {"X": None}, 0.01)
    log.add(0.1, 20.0, {"X": None}, {"X": None}, None)
    path = log.save(tmp_path / "run.csv")

    rows = list(csv.reader(path.read_text().splitlines()))
    assert rows[0][0] == plot_data.TIME_COLUMN
    # RG-1: five channel cells close every row; a sample added without its
    # six numbers leaves them empty, never zero.
    assert rows[1] == ["0.0", "10.0", "1.0", "", "0.01", "", "", "", "", ""]
    assert rows[2] == ["0.1", "20.0", "", "", "", "", "", "", "", ""]
    assert "0.0" not in rows[2][2:], "an unmeasured cell became a zero"


def test_run_log_does_not_keep_the_dictionaries_it_is_handed():
    """The run loop reuses one pair of dictionaries every frame; a log that
    kept a reference would report every row as the newest one."""
    log = RunLog(["X"])
    positions, velocities = {"X": 1.0}, {"X": None}
    log.add(0.0, 10.0, positions, velocities, 0.0)
    positions["X"] = 99.0
    log.add(0.1, 11.0, positions, velocities, 0.0)

    assert log.positions["X"] == [1.0, 99.0]


def test_run_log_append_is_thread_safe():
    log = RunLog(["X"])

    def add_many():
        for index in range(100):
            log.add(float(index), float(index), {"X": float(index)}, {}, 0.0)

    threads = [threading.Thread(target=add_many) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(log) == 500
    assert len(log.positions["X"]) == 500


def test_a_run_log_round_trips_through_the_parser(tmp_path):
    log = RunLog(["X"])
    log.add(0.0, 10.0, {"X": 1.0}, {"X": None}, 0.02)
    log.add(0.5, 20.0, {"X": 3.0}, {"X": 4.0}, 0.01)
    path = log.save(tmp_path / "run.csv")

    result = plot_data.parse_run_csv(path.read_text())
    assert result["red_percents"] == [10.0, 20.0]
    assert result["times"] == [0.0, 0.5]
    assert result["dim_data"]["X"] == [1.0, 3.0]
    assert result["dim_velocity"]["X"] == [None, 4.0]
    assert result["position_ages"] == [0.02, 0.01]


# ---------------------------------------------------------------------
# MonitorRun: a frozen configuration (REDPERCENT-1, -2)
# ---------------------------------------------------------------------

def _run(**overrides):
    config = dict(axes=["X"], region={"top": 1, "left": 2, "width": 3,
                                      "height": 4},
                  probe_name="", probe_tilt_angle=0.0, run_id="r1",
                  output_root="/tmp", annotations={}, source_name=None,
                  baseline_red=None, sample_mode="change",
                  sample_interval_s=0.0,
                  red_threshold={"r_min": 150, "g_max": 100, "b_max": 100})
    config.update(overrides)
    return MonitorRun(**config)


def test_a_run_freezes_its_axes_as_a_tuple():
    live = ["X", "Y"]
    run = _run(axes=live)
    live.append("Z")
    live.pop(0)

    assert run.axes == ("X", "Y")
    assert isinstance(run.axes, tuple)


def test_a_run_freezes_its_region_and_threshold_as_copies():
    region = {"top": 1, "left": 2, "width": 3, "height": 4}
    threshold = {"r_min": 150, "g_max": 100, "b_max": 100}
    run = _run(region=region, red_threshold=threshold)
    region["top"], threshold["r_min"] = 999, 0

    assert run.region["top"] == 1
    assert run.red_threshold["r_min"] == 150


def test_a_run_owns_a_fresh_log_and_its_own_stop_event():
    first, second = _run(), _run()
    assert first.log is not second.log
    assert first.stop_event is not second.stop_event
    assert first.log.red_values == []


def test_last_logged_red_does_not_carry_over_between_runs():
    """REDPERCENT-2: a `hasattr` check inside the thread let the previous
    run's last value decide this run's first dedup."""
    assert _run().last_logged_red is None


def test_end_is_idempotent_and_stamps_once():
    run = _run()
    assert run.is_active
    run.end()
    stopped = run.stopped_at
    run.end()

    assert not run.is_active
    assert run.stopped_at == stopped


def test_a_failed_run_is_not_active():
    run = _run()
    run.failure = RuntimeError("boom")
    assert not run.is_active


def test_frame_intervals_are_summarised():
    run = _run()
    run.note_frame(None)
    run.note_frame(0.010)
    run.note_frame(0.030)

    assert run.frames == 3
    assert run.mean_frame_interval == pytest.approx(0.020)
    assert run.max_frame_interval == pytest.approx(0.030)
    assert run.frame_rate == pytest.approx(50.0)


# ---------------------------------------------------------------------
# construction, output root, identity (REDPERCENT-21)
# ---------------------------------------------------------------------

def test_the_output_root_does_not_follow_the_process_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("TRANSFER_STAGE_DATA_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    before = RgbAnalysis(screen=fake_screen()).output_root
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    after = RgbAnalysis(screen=fake_screen()).output_root

    assert before.is_absolute()
    assert before == after, "output_root moved when the CWD moved"


def test_the_data_root_environment_variable_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path))
    assert RgbAnalysis(screen=fake_screen()).output_root == tmp_path.resolve()


def test_an_unset_run_name_still_produces_an_identity(monitor):
    monitor.run_name = ""
    assert monitor.run_id, "an unset run id must fall back, not be empty"
    assert monitor.run_dir.parent == monitor.output_root


def test_the_operator_run_name_becomes_the_run_id(monitor):
    assert monitor.run_id == "C001"
    assert monitor.run_dir == monitor.output_root / "C001"


def test_probe_tilt_angle_is_the_float_its_param_declares(monitor):
    assert isinstance(monitor.probe_tilt_angle, float)


def test_the_derived_readouts_are_not_seeded_as_plain_attributes(monitor):
    """`current_red` and friends are Params so the schema carries their type
    and precision — not so `Panel.__init__` can overwrite the property."""
    assert monitor.current_red == 0.0
    assert monitor.is_running is False
    assert monitor.frame_rate == 0.0
    assert type(monitor).current_red.__class__ is property


# ---------------------------------------------------------------------
# the red detector
# ---------------------------------------------------------------------

def test_the_detector_counts_only_pixels_inside_the_threshold(monitor):
    image = numpy.zeros((10, 10, 3), dtype=numpy.uint8)
    image[0, :] = [200, 0, 0]        # red
    image[1, :] = [0, 0, 200]        # blue
    image[2, :] = [151, 99, 99]      # just inside
    image[3, :] = [150, 0, 0]        # just outside: r > 150 is False

    assert monitor._measure_red(image) == pytest.approx(20.0)


def test_the_detector_reads_a_bgra_screenshot_without_a_conversion(monitor):
    class Shot:
        width, height = 4, 2
        # BGRA: two red pixels, six black
        bgra = bytes([0, 0, 200, 255] * 2 + [0, 0, 0, 255] * 6)

    assert monitor._measure_red(Shot()) == pytest.approx(25.0)


def test_no_frame_is_zero_percent_not_an_exception(monitor):
    assert monitor._measure_red(None) == 0.0


def test_the_threshold_comes_from_the_params_and_reaches_the_detector(monitor):
    image = numpy.zeros((10, 10, 3), dtype=numpy.uint8)
    image[0, :] = [160, 0, 0]

    assert monitor._measure_red(image) == pytest.approx(10.0)
    monitor.red_min = 200
    assert monitor._measure_red(image) == pytest.approx(0.0)
    assert monitor.red_threshold == {"r_min": 200, "g_max": 100, "b_max": 100}


# ---------------------------------------------------------------------
# REDPERCENT-5: current_red / red_change published as one pair
# ---------------------------------------------------------------------

def test_publish_red_reads_the_baseline_exactly_once(monitor):
    reads = {"count": 0}

    class CountingBaseline:
        def __get__(self, obj, objtype=None):
            reads["count"] += 1
            return 4.0

        def __set__(self, obj, value):
            pass

    del monitor.baseline_red
    RgbAnalysis.baseline_red = CountingBaseline()
    try:
        monitor._publish_red(8.0)
    finally:
        del RgbAnalysis.baseline_red

    assert reads["count"] == 1, (
        f"baseline_red was read {reads['count']} times computing one sample")


def test_the_pair_published_together_stays_together(monitor):
    monitor.baseline_red = 10.0
    monitor._publish_red(20.0)
    assert monitor.current_red == 20.0
    assert monitor.red_change == pytest.approx(100.0)

    monitor.baseline_red = 5.0        # a "concurrent" reset
    assert monitor.current_red == 20.0
    assert monitor.red_change == pytest.approx(100.0)


def test_reset_baseline_takes_the_current_reading(monitor):
    monitor._publish_red(33.0)
    monitor.reset_baseline()
    assert monitor.baseline_red == pytest.approx(33.0)


# ---------------------------------------------------------------------
# start_run refusals and confirmations (REDPERCENT-9)
# ---------------------------------------------------------------------

def test_start_refuses_without_a_region(monitor):
    """REDPERCENT-9: the old code started anyway, captured `None` forever and
    spun with `monitoring` True and zero samples."""
    with pytest.raises(Refused) as refusal:
        monitor.start_run()

    assert "region" in str(refusal.value)
    assert not monitor.is_running


def test_start_refuses_a_region_with_no_area(monitor):
    with pytest.raises(Refused):
        monitor.set_region(0, 0, 0, 10)


def test_start_asks_before_running_without_a_position_source(monitor):
    monitor.set_region(0, 0, 10, 10)

    with pytest.raises(NeedsConfirm) as ask:
        monitor.start_run()

    assert "position source" in ask.value.prompt
    assert ask.value.command == "start_run"
    assert not monitor.is_running


def test_start_asks_before_running_with_no_synced_axis(monitor):
    monitor.on_model_added("Probe", FakeProbe())
    monitor.set_region(0, 0, 10, 10)

    with pytest.raises(NeedsConfirm) as ask:
        monitor.start_run()

    assert "no axis is synced" in ask.value.prompt


def test_a_fully_configured_start_needs_no_confirmation(monitor):
    monitor.on_model_added("Probe", FakeProbe())
    monitor.set_sync("X")
    monitor.set_region(0, 0, 10, 10)

    assert monitor.start_run() == "C001"
    assert monitor.is_running
    monitor.end_run()


def test_start_refuses_while_a_run_is_active(monitor):
    _started(monitor)
    try:
        with pytest.raises(Refused):
            monitor.start_run(confirmed=True)
    finally:
        monitor.end_run()


def test_start_refuses_while_the_stop_latch_is_set(monitor):
    monitor.set_region(0, 0, 10, 10)
    monitor.estop()

    with pytest.raises(Refused) as refusal:
        monitor.start_run(confirmed=True)
    assert "is stopped" in str(refusal.value)   # F20 vocabulary


def test_start_refuses_when_screen_capture_is_unavailable(tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "mss", None)
    model = RgbAnalysis()
    model.output_root = tmp_path
    model.open()
    model.set_region(0, 0, 10, 10)
    try:
        with pytest.raises(Refused) as refusal:
            model.start_run(confirmed=True)
        assert "mss" in str(refusal.value)
        assert not model.is_running
    finally:
        model.close()


# ---------------------------------------------------------------------
# the run itself
# ---------------------------------------------------------------------



def test_change_mode_writes_a_row_only_when_the_red_percent_moves(monitor):
    frames = [red_frame(2), red_frame(2), red_frame(8), red_frame(8)]
    monitor.screen = fake_screen(frames=frames, delay=0.002, varying=False)
    monitor.screen.open()
    _started(monitor)
    try:
        assert _wait_for(lambda: monitor.frames_captured >= 6)
        monitor.end_run()
        assert monitor.rows_written == 2, "one row per distinct red percent"
    finally:
        monitor.end_run()




def test_the_first_frame_of_a_run_sets_the_baseline(monitor):
    _started(monitor)
    try:
        assert _wait_for(lambda: monitor.rows_written >= 1)
        assert monitor.baseline_red == pytest.approx(20.0)
        assert monitor._run.baseline_red == pytest.approx(20.0)
    finally:
        monitor.end_run()


def test_a_second_run_gets_a_fresh_log(monitor):
    """REDPERCENT-2: `if not self.data_log:` made a second run append into the
    first run's log — runs concatenated and the new run's first sample was
    dedup-skipped against the old run's last value."""
    _started(monitor)
    assert _wait_for(lambda: monitor.rows_written >= 2)
    monitor.end_run()
    first_log = monitor._run.log
    monitor.save()

    monitor.start_run(confirmed=True)
    try:
        assert monitor._run.log is not first_log
        assert first_log.red_values, "the first run's log was not left alone"
    finally:
        monitor.end_run()


def test_a_run_reads_only_its_own_frozen_configuration(monitor):
    """REDPERCENT-1: the loop must not read the model's live attributes, so a
    mid-run edit cannot reach the thread."""
    monitor.on_model_added("Probe", FakeProbe())
    monitor.set_sync("X")
    _started(monitor)
    try:
        run = monitor._run
        assert run.axes == ("X",)
        with pytest.raises(Refused):
            monitor.set_sync("Y")
        assert run.axes == ("X",), "a refused toggle still reached the run"
        with pytest.raises(Refused):
            monitor.set_region(5, 5, 5, 5)
        assert run.region["width"] == 10
    finally:
        monitor.end_run()


def test_a_failure_in_the_loop_ends_the_run_and_reports_once(monitor, capsys):
    """REDPERCENT-4: the loop used to have no handler at all, so a failure
    left `monitoring` True with a dead thread forever."""
    monitor.set_region(0, 0, 10, 10)
    monitor._measure_rgb = lambda frame, threshold=None: 1 / 0
    monitor.start_run(confirmed=True)
    run = monitor._run

    assert _wait_for(lambda: not run.thread.is_alive())
    assert not monitor.is_running
    assert run.failure is not None
    assert isinstance(run.failure, ZeroDivisionError)
    assert "RGB Analysis Run Failed" in capsys.readouterr().err


def test_a_grab_failure_does_not_end_the_run(monitor):
    class BrokenCapture:
        def grab(self, region):
            raise RuntimeError("display went away")

        def close(self):
            pass

    monitor.screen = Screen(factory=BrokenCapture)
    monitor.screen.open()
    _started(monitor)
    try:
        assert _wait_for(lambda: monitor._run.grab_failures >= 2)
        assert monitor.is_running, "a transient grab failure ended the run"
        assert monitor.rows_written == 0
    finally:
        monitor.end_run()


def test_end_run_never_blocks_on_a_slow_grab(monitor):
    """The stop path must latch and return — never wait on a screen grab.
    A loop stuck in a slow capture must not be able to hold the stop."""
    release = threading.Event()

    class SlowCapture:
        def grab(self, region):
            release.wait(timeout=5.0)
            return red_frame()

        def close(self):
            pass

    monitor.screen = Screen(factory=SlowCapture)
    monitor.screen.open()
    _started(monitor)
    time.sleep(0.05)

    started = time.monotonic()
    monitor.end_run()
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 0.1, f"end_run blocked for {elapsed:.3f}s"
    assert not monitor.is_running


def test_estop_stops_the_run_and_confirms(monitor):
    _started(monitor)
    assert monitor.estop() is True
    assert not monitor.is_running
    assert monitor.is_estopped


def test_halt_reports_that_it_landed(monitor):
    _started(monitor)
    assert monitor.halt() is True
    assert not monitor.is_running


# ---------------------------------------------------------------------
# position, velocity and age (REDPERCENT-16, reopened)
# ---------------------------------------------------------------------

def test_velocity_is_measured_only_between_distinct_position_samples(monitor):
    """The firmware streams position at a fixed 10 Hz. At `every_frame` rates
    most rows see the same position sample twice; differencing across them
    would report a velocity of zero for a moving stage."""
    run = _run(axes=["X"])
    probe = FakeProbe((0.0, 0.0, 0.0), 100.0)
    monitor._source = probe

    value, velocity = monitor._read_dim(run, "X", probe.position, 100.0, True)
    assert value == 0.0
    assert velocity is None, "the first sample has nothing to difference against"
    run.last_position_time = 100.0

    # The same 10 Hz sample, seen again on the next captured frame.
    value, velocity = monitor._read_dim(run, "X", (0.0, 0.0, 0.0), 100.0, False)
    assert value == 0.0
    assert velocity is None, "a repeated position sample produced a velocity"

    value, velocity = monitor._read_dim(run, "X", (2.0, 0.0, 0.0), 100.1, True)
    assert value == 2.0
    assert velocity == pytest.approx(20.0), "2 steps over 0.1 s is 20 steps/s"


def test_an_unreadable_position_is_none_never_zero(monitor):
    run = _run(axes=["X"])
    value, velocity = monitor._read_dim(run, "X", ("not-a-number", 0, 0), 1.0,
                                        True)
    assert value is None and velocity is None


def test_a_missing_position_source_leaves_every_cell_empty(monitor):
    run = _run(axes=["X", "Y"])
    positions, velocities = {"X": 1.0, "Y": 2.0}, {"X": 3.0, "Y": 4.0}
    monitor._source = None

    age = monitor._read_position(run, ("X", "Y"), positions, velocities)

    assert age is None
    assert positions == {"X": None, "Y": None}
    assert velocities == {"X": None, "Y": None}


def test_one_position_age_column_serves_every_axis(monitor):
    run = _run(axes=["X", "Y"])
    probe = FakeProbe((1.0, 2.0, 3.0), time.monotonic())
    monitor._source = probe
    positions, velocities = {"X": None, "Y": None}, {"X": None, "Y": None}

    age = monitor._read_position(run, ("X", "Y"), positions, velocities)

    assert positions == {"X": 1.0, "Y": 2.0}
    assert age is not None and age >= 0.0
    assert len(RunLog(["X", "Y"]).headers) == 2 + 2 * 2 + 1 + 5   # + RG-1


def test_a_run_records_positions_from_the_selected_source(monitor):
    probe = FakeProbe((5.0, 0.0, 0.0), time.monotonic())
    monitor.on_model_added("Probe", probe)
    monitor.set_sync("X")
    _started(monitor)
    try:
        assert _wait_for(lambda: monitor.rows_written >= 2)
        monitor.end_run()
        assert monitor._run.log.positions["X"][0] == pytest.approx(5.0)
    finally:
        monitor.end_run()


# ---------------------------------------------------------------------
# the position source list (REDPERCENT-11, PYSIDE-3, STEPPER-13)
# ---------------------------------------------------------------------

def test_a_model_without_a_position_is_not_a_source(monitor):
    monitor.on_model_added("Heater", object())
    assert monitor.source_options == []


def test_the_first_source_to_appear_is_selected(monitor):
    probe = FakeProbe()
    monitor.on_model_added("Probe A", probe)

    assert monitor.source_options == ["Probe A"]
    assert monitor.source_name == "Probe A"
    assert monitor._source is probe


def test_a_released_source_is_dropped_and_another_is_selected(monitor):
    first, second = FakeProbe(), FakeProbe()
    monitor.on_model_added("Probe A", first)
    monitor.on_model_added("Probe B", second)
    assert monitor.source_name == "Probe A"

    monitor.on_model_removed("Probe A", first)

    assert monitor.source_options == ["Probe B"]
    assert monitor.source_name == "Probe B"
    assert monitor._source is second


def test_the_last_source_going_away_leaves_nothing_selected(monitor):
    probe = FakeProbe()
    monitor.on_model_added("Probe A", probe)
    monitor.on_model_removed("Probe A", probe)

    assert monitor.source_options == []
    assert monitor.source_name is None
    assert monitor._source is None


def test_set_source_refuses_a_name_that_is_not_there(monitor):
    with pytest.raises(Refused):
        monitor.set_source("Nothing")


def test_source_options_is_reachable_as_a_dropdown_options_command(monitor):
    monitor.on_model_added("Probe A", FakeProbe())
    assert monitor.options("source_options") == ["Probe A"]


# ---------------------------------------------------------------------
# synced axes: nine members become one command
# ---------------------------------------------------------------------

def test_set_sync_toggles_one_axis_and_keeps_axis_order(monitor):
    monitor.set_sync("Z")
    monitor.set_sync("X")
    assert monitor.sync_axes == "X,Z"
    assert monitor.sync_axes_list == ["X", "Z"]
    assert monitor.is_sync_x and monitor.is_sync_z and not monitor.is_sync_y

    monitor.set_sync("X")
    assert monitor.sync_axes == "Z"


def test_set_sync_refuses_a_name_that_is_not_an_axis(monitor):
    with pytest.raises(Refused):
        monitor.set_sync("Q")


def test_set_sync_refuses_mid_run_rather_than_ignoring_the_click(monitor):
    """VIEW-TKINTER-15: the rendered gate only stops a rendered widget. The
    Web client reached `sync_x` by attribute; silently ignoring an operator's
    click is its own defect."""
    _started(monitor)
    try:
        before = monitor.sync_axes
        with pytest.raises(Refused):
            monitor.set_sync("X")
        assert monitor.sync_axes == before
    finally:
        monitor.end_run()




# ---------------------------------------------------------------------
# artifacts (REDPERCENT-21, -22, -23)
# ---------------------------------------------------------------------

@pytest.fixture
def logged(monitor):
    """A monitor holding a finished run's worth of data."""
    monitor.probe_name = "tip-3"
    monitor.probe_tilt_angle = 12.5
    probe = FakeProbe((1.0, 2.0, 3.0), time.monotonic())
    monitor.on_model_added("Probe", probe)
    monitor.set_sync("X")
    monitor.set_sync("Y")
    _started(monitor)
    assert _wait_for(lambda: monitor.rows_written >= 3)
    monitor.end_run()
    # `end_run` latches and returns — it deliberately never joins, so the
    # thread can still be mid-frame. A test reasoning about exact row counts
    # has to wait for it the way `close()` does.
    monitor._run.thread.join(timeout=2.0)
    return monitor


def test_save_returns_an_absolute_path_under_the_output_root(logged):
    written = logged.save()

    assert written.startswith(str(logged.output_root))
    assert written.endswith("C001_position.csv")


def test_every_artifact_of_a_run_carries_the_run_id(logged):
    logged.save()
    produced = sorted(path.name for path in logged.run_dir.iterdir())

    assert produced
    for name in produced:
        assert name.startswith("C001_"), f"{name} does not carry its run id"


def test_the_csv_is_a_rectangle_a_default_reader_opens(logged):
    """REDPERCENT-22: `# Metadata` and friends were four DATA rows written
    before the header, so a default `read_csv` took `# Metadata` as the
    column names."""
    logged.save()
    with open(logged.run_dir / "C001_position.csv", newline="") as handle:
        rows = list(csv.reader(handle))

    header, body = rows[0], rows[1:]
    assert header[0] == plot_data.TIME_COLUMN
    assert not any(cell.startswith("#") for row in rows for cell in row)
    assert all(len(row) == len(header) for row in body), "ragged rows"
    assert body


def test_the_sidecar_carries_what_the_csv_cannot(logged):
    logged.save()
    meta = json.loads((logged.run_dir / "C001_station_meta.json").read_text())

    for key in ("run_id", "probe_name", "probe_tilt_angle", "sync_axes",
                "region", "baseline_red", "red_threshold", "sample_mode",
                "position_rate_hz", "frames_captured", "rows_written",
                "mean_frame_interval_s", "max_frame_interval_s",
                "achieved_rate_hz", "duration_s", "started_at", "stopped_at",
                "annotations", "source_name"):
        assert key in meta, f"the sidecar is missing {key!r}"

    assert meta["run_id"] == "C001"
    assert meta["probe_name"] == "tip-3"
    assert meta["probe_tilt_angle"] == pytest.approx(12.5)
    assert meta["sync_axes"] == ["X", "Y"]
    assert meta["region"]["width"] == 10
    assert meta["red_threshold"] == {"r_min": 150, "g_max": 100, "b_max": 100}
    assert meta["sample_mode"] == "change"
    assert meta["position_rate_hz"] == 10.0
    assert meta["rows_written"] >= 3
    assert meta["achieved_rate_hz"] > 0


def test_operator_annotations_reach_the_sidecar_and_stay_apart_from_actuals(logged):
    """REDPERCENT-23: the annotation block holds what the operator INTENDED;
    the meta block holds what the station DID. Merging them loses exactly the
    comparison the experiment exists to make."""
    logged.specimen_id = "hBN-04"
    logged.note = "second cut of the block"
    logged._run.annotations.update({"specimen_id": "hBN-04",
                                    "note": "second cut of the block"})
    logged.save()

    meta = json.loads((logged.run_dir / "C001_station_meta.json").read_text())
    assert meta["annotations"]["specimen_id"] == "hBN-04"
    assert meta["annotations"]["note"] == "second cut of the block"
    assert "probe_tilt_angle" in meta
    assert meta["probe_tilt_angle"] == pytest.approx(12.5)


def test_the_annotation_set_is_a_table_not_hardcoded_attributes():
    names = [field.name for field in RgbAnalysis.ANNOTATION_FIELDS]
    assert {"specimen_id", "consumable_id", "note"} <= set(names)
    assert len(set(names)) == len(names)


def test_annotations_are_rendered_by_the_schema_not_per_view(monitor):
    flat = repr(monitor.schema)
    for name in ("specimen_id", "consumable_id", "note"):
        assert name in flat


def test_saving_with_nothing_recorded_is_a_refusal_not_a_silent_nothing(monitor):
    with pytest.raises(Refused) as refusal:
        monitor.save()
    assert "nothing to write" in str(refusal.value)


def test_save_takes_no_path_from_any_caller():
    """Finding 9: `save_log(file_path)` took a path from whichever view raised
    a file dialog, which on the Web client meant the browser handing the
    server a path on the server's own filesystem."""
    import inspect
    parameters = list(inspect.signature(RgbAnalysis.save).parameters)
    assert parameters == ["self"]


def test_has_unsaved_data_clears_on_save_and_returns_after_more_rows(logged):
    assert logged.has_unsaved_data
    logged.save()
    assert not logged.has_unsaved_data


def test_close_autosaves_unsaved_data(logged):
    run_dir = logged.run_dir
    logged.close()

    produced = list(run_dir.iterdir()) if run_dir.exists() else []
    assert any(path.name.endswith("_position.csv") for path in produced), (
        "close() must autosave pending data, not discard it")


def test_close_writes_nothing_when_everything_is_saved(logged):
    logged.save()
    written = sorted(path.stat().st_mtime_ns for path in logged.run_dir.iterdir())
    logged.close()
    assert sorted(path.stat().st_mtime_ns
                  for path in logged.run_dir.iterdir()) == written


def test_starting_a_new_run_autosaves_the_previous_one(logged):
    logged.start_run(confirmed=True)
    try:
        assert (logged.output_root / "C001" / "C001_position.csv").exists()
    finally:
        logged.end_run()


def test_a_saved_run_round_trips_through_load_run(logged):
    path = logged.save()
    result = plot_data.load_run(path)

    assert result["red_percents"]
    assert result["metadata"]["probe_name"] == "tip-3"
    assert result["dims"] == ["X", "Y"]


# ---------------------------------------------------------------------
# the analysis plot, for all three views
# ---------------------------------------------------------------------

def test_load_run_reports_what_it_found(logged):
    path = logged.save()
    summary = logged.load_run(path)

    assert summary["samples"] == len(logged._run.log)
    assert summary["dims"] == ["X", "Y"]
    assert summary["metadata"]["run_id"] == "C001"
    assert logged.loaded_run == "C001_position.csv"


def test_load_run_refuses_a_file_with_no_samples(monitor, tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("t_s,red_percent\n")
    with pytest.raises(Refused):
        monitor.load_run(empty)


def test_load_run_refuses_a_file_that_is_not_there(monitor, tmp_path):
    with pytest.raises(Refused):
        monitor.load_run(tmp_path / "nope.csv")


def test_the_dimension_options_are_only_the_plots_the_data_can_produce(logged):
    logged.load_run(logged.save())

    options = logged.plot_dim_options
    assert options[0] == "0D"
    assert "1D: X" in options and "1D: Y" in options
    assert "2D: X+Y" in options
    assert not any(option.startswith("3D") for option in options), (
        "a 3D option for a two-axis run is REDPERCENT-17's blank plot")


def test_set_plot_dims_accepts_an_option_and_explicit_arguments(logged):
    logged.load_run(logged.save())

    assert logged.set_plot_dims("2D: X+Y") == "2D: X+Y"
    assert logged._plot_type == "2D"
    assert logged._plot_dims == ("X", "Y", None)

    assert logged.set_plot_dims("1D", "Y") == "1D: Y"
    assert logged._plot_dims == ("Y", None, None)


def test_set_plot_dims_refuses_a_selection_that_is_not_a_plot(logged):
    with pytest.raises(Refused):
        logged.set_plot_dims("sideways")


def test_figure_is_png_bytes_before_and_after_a_run_is_loaded(logged):
    assert isinstance(logged.figure, bytes)
    logged.load_run(logged.save())
    assert isinstance(logged.figure, bytes)


def test_the_live_series_is_time_against_red(monitor):
    _started(monitor)
    try:
        assert _wait_for(lambda: monitor.rows_written >= 3)
        series = monitor.series
        assert len(series["x"]) == len(series["y"]) >= 3
        assert series["x"] == sorted(series["x"])
        assert all(0.0 <= value <= 100.0 for value in series["y"])
    finally:
        monitor.end_run()


def test_the_series_of_a_model_with_no_run_is_empty(monitor):
    assert monitor.series == {"x": [], "y": []}


def test_rgb_analysis_declares_no_live_plot(monitor):
    """TM-2 (2026-10-07): the live plots left the live view (RGB analysis is
    drawn on the Transfer Map's page; redrawing a whole run each refresh
    slowed it, CAP-5). `series` stays a property; nothing polls it."""
    import schema as sch
    assert not [e for e in sch.elements(monitor.schema) if e["type"] == "plot"]
    assert monitor.run("series").is_refused


# ---------------------------------------------------------------------
# the schema every view renders
# ---------------------------------------------------------------------

def test_every_command_the_schema_declares_exists(monitor):
    import schema as sch
    for element in sch.elements(monitor.schema):
        for key in ("command", "data_command", "source_command",
                    "options_command"):
            name = element.get(key)
            if name:
                assert hasattr(monitor, name), f"{name} is declared but absent"


def test_the_schema_ends_with_the_safety_section(monitor):
    assert monitor.schema["sections"][-1]["title"] == "Safety"


def test_every_editable_field_travels_with_start(monitor):
    import schema as sch
    entries = {element["model_attr"] for element in sch.elements(monitor.schema)
               if element["type"] == "entry"}
    start = next(element for element in sch.elements(monitor.schema)
                 if element.get("command") == "start_run")

    assert entries <= set(start["inputs"]), (
        f"not carried by Start: {sorted(entries - set(start['inputs']))}")


def test_start_is_gated_off_and_stop_gated_on_while_running(monitor):
    import schema as sch
    elements = {element.get("command"): element
                for element in sch.elements(monitor.schema)}

    assert not sch.is_enabled(elements["start_run"], "running")
    assert sch.is_enabled(elements["start_run"], "idle")
    assert sch.is_enabled(elements["end_run"], "running")
    assert not sch.is_enabled(elements["end_run"], "idle")


def test_the_schema_carries_a_region_select_a_file_save_and_a_file_open(monitor):
    import schema as sch
    types = {element["type"] for element in sch.elements(monitor.schema)}
    # TM-2 (2026-10-07): "plot" left with the live plot.
    assert {"region_select", "file_save", "file_open", "image",
            "dropdown", "toggle"} <= types


def test_state_renders_the_region_in_one_wording(monitor):
    monitor.set_region(20, 10, 640, 480)
    assert monitor.state["values"]["region"] == "640x480 at (20, 10)"


def test_state_reports_the_run_without_a_view_reaching_into_the_log(monitor):
    _started(monitor)
    try:
        assert _wait_for(lambda: monitor.rows_written >= 2)
        snapshot = monitor.state["run"]
        assert snapshot["is_running"] is True
        assert snapshot["run_id"] == "C001"
        assert snapshot["rows"] >= 2
        assert snapshot["rate_hz"] > 0
        assert snapshot["run_dir"].endswith("C001")
    finally:
        monitor.end_run()


def test_a_command_the_schema_does_not_declare_is_refused(monitor):
    result = monitor.run("_autosave")
    assert result.is_refused


def test_a_readonly_field_cannot_be_written_through_set_value(monitor):
    assert monitor.set_value("current_red", "99").is_refused
    assert monitor.set_value("sync_axes", "X,Y").is_refused


def test_an_entry_commits_through_the_panel(monitor):
    assert monitor.set_value("probe_tilt_angle", "12.5").is_ok
    assert monitor.probe_tilt_angle == pytest.approx(12.5)


def test_an_unparseable_entry_is_refused_by_name(monitor):
    result = monitor.set_value("probe_tilt_angle", "")
    assert result.is_refused
    assert "Probe Tilt Angle" in result.reason


def test_the_mode_name_is_what_the_gates_are_matched_against(monitor):
    assert monitor.mode_name == "idle"
    _started(monitor)
    try:
        assert monitor.mode_name == "running"
        assert monitor.is_active
    finally:
        monitor.end_run()


# ---------------------------------------------------------------------
# F11: Start run is greyed out while latched or with no capture region
# ---------------------------------------------------------------------

def _start_element(model):
    import schema as sch
    return next(e for e in sch.elements(model.schema)
                if e.get("command") == "start_run")


def test_start_run_is_greyed_out_until_a_capture_region_is_set(monitor):
    import schema as sch
    assert monitor.region is None
    assert monitor.state["mode"] == "no_region"
    assert sch.is_enabled(_start_element(monitor), "no_region") is False
    refused = monitor.run("start_run")
    assert refused.is_refused and "capture region" in refused.reason
    monitor.set_region(0, 0, 10, 10)
    assert monitor.state["mode"] == "idle"
    assert sch.is_enabled(_start_element(monitor), "idle") is True


def test_start_run_is_greyed_out_while_latched(monitor):
    import schema as sch
    monitor.set_region(0, 0, 10, 10)
    monitor.estop()
    assert monitor.state["mode"] == "latched"
    assert sch.is_enabled(_start_element(monitor), "latched") is False


# ---------------------------------------------------------------------
# F16: the analysis figure is cached until the data or settings change
# ---------------------------------------------------------------------

def test_the_figure_is_rendered_once_until_something_changes(logged, monkeypatch):
    """UXPM-13 measured 214 ms per render, every second, for an unchanged
    image."""
    logged.load_run(logged.save())
    calls = []
    real = plot_data.render_figure

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(plot_data, "render_figure", counting)
    first = logged.figure
    assert logged.figure == first and logged.run("figure").value == first
    assert len(calls) == 1
    logged.set_plot_dims("1D: X")
    changed = logged.figure
    assert len(calls) == 2 and changed != first
    assert logged.figure == changed and len(calls) == 2
    logged.load_run(logged.save())
    logged.figure
    assert len(calls) == 3, "a new load must render afresh"


def test_the_tier_two_disclosure_names_the_device(monitor):
    """Tier K (2026-09-26): RGB analysis's second tier is statistics and
    annotations, not configuration, so its disclosure reads "<name> details";
    every tier-2 section says the same words (the views draw the first)."""
    tier_two = [s for s in monitor.schema["sections"] if s.get("tier") == 2]
    assert tier_two
    # RG-3: "RGB analysis details", sentence case, not "<NAME> details".
    assert {s["disclosure"] for s in tier_two} == {"RGB analysis details"}
    assert monitor.DISCLOSURE == "RGB analysis details"


def test_the_next_step_line_says_what_unblocks_start_run(monitor):
    """L3: Start run is greyed from launch until a region exists; the reason
    is a tier-1 readonly beside it, quiet (empty) once there is a region."""
    import schema as sch
    line = next(e for e in sch.elements(monitor.schema) if e.get("model_attr") == "next_step")
    section = next(s for s in monitor.schema["sections"] if line in s["elements"])
    assert section.get("tier", 1) == 1
    assert monitor.region is None or not monitor.region
    assert monitor.next_step.startswith("Set a capture region")
    monitor.region = (0, 0, 10, 10)
    assert monitor.next_step == ""


def test_the_analysis_image_is_empty_until_a_run_is_loaded(monitor):
    """L15: no full-size "No samples" picture; empty bytes and the schema's
    `empty` sentence, which a view draws as one caption line."""
    import schema as sch
    assert monitor.figure == b""
    image = next(e for e in sch.elements(monitor.schema) if e["type"] == "image")
    assert image["empty"].startswith("No analysis yet")


def test_stop_run_is_never_refused_by_a_bad_box(monitor):
    """CON-4: the one model whose stop is not called halt. `end_run` is
    declared `stop=True`, so a bad "Red at least" in the box cannot keep a
    run recording."""
    import schema as sch
    button = next(e for e in sch.elements(monitor.schema) if e.get("command") == "end_run")
    assert button.get("stop") is True
    assert monitor._takes_hardware_down("end_run")
    result = monitor.run("end_run", inputs={"red_min": "abc"})
    assert not result.is_refused or "not a number" not in (result.reason or "")


# ---------------------------------------------------------------------
# MAP-2 (2026-09-27): the additive hooks the Transfer Map reads through
# ---------------------------------------------------------------------

def test_a_subscriber_receives_every_logged_row(monitor):
    """`subscribe(fn)`: fn(t_s, red, positions) once per row the run log
    appends, in order, from the run thread."""
    seen = []
    monitor.subscribe(lambda t, red, positions: seen.append((t, red, dict(positions))))
    _started(monitor, sync_axes="Z")
    assert _wait_for(lambda: monitor.rows_written >= 5)
    monitor.end_run()
    monitor._run.thread.join(2)
    log = monitor._run.log
    assert len(seen) == len(log) == monitor.rows_written
    assert [s[1] for s in seen] == log.red_values
    assert [s[0] for s in seen] == log.times
    # The row dict: the synced axis, and the five RG-1 numbers.
    assert all(set(s[2]) == {"Z", *RgbAnalysis.CHANNEL_KEYS} for s in seen)


def test_a_subscriber_gets_its_own_copy_of_the_positions(monitor):
    """The loop reuses one dict per run; a subscriber that keeps it must not
    see it change under it."""
    kept = []
    monitor.subscribe(lambda t, red, positions: kept.append(positions))
    _started(monitor, sync_axes="X")
    assert _wait_for(lambda: len(kept) >= 2)
    monitor.end_run()
    assert kept[0] is not kept[1]


def test_a_failing_subscriber_never_ends_the_run(monitor):
    def broken(t, red, positions):
        raise RuntimeError("subscriber bug")
    monitor.subscribe(broken)
    _started(monitor)
    assert _wait_for(lambda: monitor.rows_written >= 10)
    assert monitor.is_running and monitor._run.failure is None
    monitor.end_run()


def test_unsubscribe_stops_the_calls_and_is_idempotent(monitor):
    seen = []
    fn = lambda t, red, positions: seen.append(red)   # noqa: E731
    monitor.subscribe(fn)
    monitor.subscribe(fn)                  # once, not twice
    monitor.unsubscribe(fn)
    monitor.unsubscribe(fn)
    _started(monitor)
    assert _wait_for(lambda: monitor.rows_written >= 5)
    monitor.end_run()
    assert seen == []


def test_the_estop_path_is_unchanged_with_a_subscriber_attached(monitor):
    """A subscriber is on the run thread, never on the stop path: estop ends
    the run and confirms inside its budget."""
    import threading
    gate = threading.Event()
    monitor.subscribe(lambda t, red, positions: gate.wait(0.5))   # a slow one
    _started(monitor)
    assert _wait_for(lambda: monitor.rows_written >= 1)
    assert monitor.estop() is True
    assert not monitor.is_running
    gate.set()


def test_grab_frame_returns_the_capture_region_as_png(monitor):
    monitor.set_region(0, 0, 10, 10)
    png = monitor.grab_frame()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image
    import io
    image = Image.open(io.BytesIO(png))
    assert image.size == (10, 10)
    assert image.getpixel((0, 0))[:3] == (200, 0, 0)     # the red rows, RGB


def test_grab_frame_is_none_without_a_region_or_a_frame(monitor):
    assert monitor.grab_frame() is None
    closed = RgbAnalysis(screen=fake_screen())
    closed.region = {"top": 0, "left": 0, "width": 10, "height": 10}
    assert closed.grab_frame() is None                   # screen never opened


# -- trial sheet T2: a run's identity that no later run shares (additive) ------

def test_run_token_names_this_run_and_no_later_one(monitor):
    """`run_id` repeats whenever the operator has typed a Run / Cut ID, so
    the Transfer Map cannot tell the run it started from the operator's next
    one by it. `run_token` can: None when no run is active."""
    monitor.set_region(0, 0, 10, 10)
    assert monitor.run_token is None
    monitor.start_run(confirmed=True)
    first = monitor.run_token
    assert first is not None
    assert monitor.run_token is first                   # stable while it runs
    monitor.end_run()
    assert monitor.run_token is None
    monitor.start_run(confirmed=True)
    assert monitor.run_id == "C001"                    # the same name again
    assert monitor.run_token is not None and monitor.run_token is not first


# -- full pictures: the whole desktop at full size (additive, 2026-09-28) ------

class _DesktopShot:
    """An mss-shaped grab: `size` and a BGRA buffer (pure blue here, so a
    whole-screen picture is told from a region picture at a glance)."""

    def __init__(self, width, height):
        self.size = (width, height)
        self.bgra = bytes([200, 0, 0, 255]) * (width * height)


class DesktopCapture(FakeCapture):
    """`FakeCapture` with a virtual desktop wider than the picker's 1600:
    a grab of `monitors[0]` is the whole desktop; any other region is a
    frame, as before."""

    DESKTOP = {"left": 0, "top": 0, "width": 1700, "height": 40}

    def __init__(self, frames, delay, varying=True):
        super().__init__(frames, delay, varying)
        self.monitors = [dict(self.DESKTOP)]

    def grab(self, region):
        if dict(region) == self.DESKTOP:
            return _DesktopShot(region["width"], region["height"])
        return super().grab(region)


def desktop_screen(frames=None, delay=0.001, varying=True):
    frames = frames if frames is not None else [red_frame()]
    return Screen(factory=lambda: DesktopCapture(frames, delay, varying))


def test_grab_screen_is_the_whole_desktop_at_full_size(tmp_path):
    from PIL import Image
    import io
    model = RgbAnalysis(screen=desktop_screen())
    model.output_root = tmp_path / "runs"
    model.open()
    try:
        png = model.grab_screen()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        image = Image.open(io.BytesIO(png))
        assert image.size == (1700, 40)                 # not downscaled
        assert image.getpixel((0, 0))[:3] == (0, 0, 200)
        # The picker's picture is the desktop at full size (2026-09-28).
        picker = model.screen_image
        assert Image.open(io.BytesIO(picker["image"])).size == (1700, 40)
        assert picker["width"] == 1700
    finally:
        model.close()


def test_grab_screen_is_none_when_the_screen_is_closed_or_fails(monitor):
    closed = RgbAnalysis(screen=desktop_screen())
    assert closed.grab_screen() is None                 # never opened
    assert monitor.grab_screen() is None                # a capture with no desktop


# -- capture handles (2026-09-28): the loop keeps one, nothing else does -----

def test_the_run_loop_keeps_one_handle_and_other_grabs_keep_none(tmp_path):
    """A Web request is a new thread each time; a frame or screen grab from
    one must leave no capture handle open (on X11, a display connection).
    The run loop keeps exactly one for its life and drops it on leaving."""
    model = RgbAnalysis(screen=desktop_screen())
    model.output_root = tmp_path / "runs"
    model.open()
    try:
        model.set_region(0, 0, 10, 10)
        model.start_run(confirmed=True)
        run = model._run
        deadline = time.monotonic() + 3.0
        while run.frames < 5 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert run.frames >= 5
        assert model.screen.kept_handles == 1
        for grab in (model.grab_frame, model.grab_screen) * 3:
            out = []
            worker = threading.Thread(target=lambda: out.append(grab()))
            worker.start()
            worker.join()
            assert out[0] and out[0][:8] == b"\x89PNG\r\n\x1a\n"
        assert model.screen.kept_handles == 1      # still only the loop's
        model.end_run()
        run.thread.join(2.0)
        assert not run.thread.is_alive()
        assert model.screen.kept_handles == 0      # dropped with the loop
    finally:
        model.close()


# ---------------------------------------------------------------------
# V3 (2026-09-28): the frame hook the Transfer Map's video reads through
# ---------------------------------------------------------------------

def test_a_frame_subscriber_is_called_for_every_grabbed_frame(monitor):
    """`subscribe_frames(fn)`: fn(t_s, frame, red) once per frame the loop
    grabs and measures, the same frame, whether or not a row is logged."""
    seen = []
    monitor.subscribe_frames(lambda t, frame, red: seen.append((t, frame, red)))
    _started(monitor)
    assert _wait_for(lambda: len(seen) >= 20)
    monitor.end_run()
    monitor._run.thread.join(2)
    run = monitor._run
    assert len(seen) == run.frames
    assert [s[0] for s in seen] == sorted(s[0] for s in seen)
    for t, frame, red in seen[:5]:
        assert isinstance(frame, numpy.ndarray) and frame.shape == (10, 10, 3)
        assert red == pytest.approx(monitor._measure_red(frame))
    # the hook's red is the run's own: every logged row is one of the frames'
    assert set(run.log.red_values) <= {s[2] for s in seen}


def test_a_failing_frame_subscriber_never_ends_the_run(monitor):
    def broken(t, frame, red):
        raise RuntimeError("subscriber bug")
    monitor.subscribe_frames(broken)
    _started(monitor)
    assert _wait_for(lambda: monitor.frames_captured >= 20)
    assert monitor.is_running and monitor._run.failure is None
    monitor.end_run()


def test_unsubscribe_frames_stops_the_calls_and_is_idempotent(monitor):
    seen = []
    fn = lambda t, frame, red: seen.append(red)   # noqa: E731
    monitor.subscribe_frames(fn)
    monitor.subscribe_frames(fn)
    monitor.unsubscribe_frames(fn)
    monitor.unsubscribe_frames(fn)
    _started(monitor)
    assert _wait_for(lambda: monitor.frames_captured >= 10)
    monitor.end_run()
    assert seen == []


def _loop_rate(model, seconds=0.6):
    start_frames, start = model._run.frames, time.monotonic()
    time.sleep(seconds)
    return (model._run.frames - start_frames) / (time.monotonic() - start)


def test_a_slow_frame_consumer_never_slows_the_loop(tmp_path):
    """Measured: the loop's frame rate with a subscriber that hands frames to
    a bounded queue drained by a consumer taking 100 ms a frame (the
    Transfer Map's pattern) stays within reach of the rate with none."""
    import queue as queue_module
    model = RgbAnalysis(screen=fake_screen(delay=0.004))
    model.output_root = tmp_path / "runs"
    model.open()
    try:
        _started(model)
        assert _wait_for(lambda: model.frames_captured >= 10)
        alone = _loop_rate(model)
        box = queue_module.Queue(maxsize=4)
        dropped = []

        def hand_off(t, frame, red):
            try:
                box.put_nowait(frame)
            except queue_module.Full:
                dropped.append(t)

        def consume():
            while model.is_running:
                try:
                    box.get(timeout=0.05)
                except queue_module.Empty:
                    continue
                time.sleep(0.1)

        threading.Thread(target=consume, daemon=True).start()
        model.subscribe_frames(hand_off)
        loaded = _loop_rate(model)
        model.end_run()
        assert alone > 20, alone
        assert loaded >= 0.6 * alone, (alone, loaded)
        assert dropped, "the consumer fell behind, so frames were dropped"
    finally:
        model.close()


# ---------------------------------------------------------------------
# RG-1 (2026-10-07): six numbers per settled sample
# ---------------------------------------------------------------------

def channel_frame():
    """A 10x10 RGB frame with known channels: 20 red pixels, 30 green, 10
    blue and 40 of the bench's yellow-green field, which no mask passes.
    Shares: red 20 %, green 30 %, blue 10 %. Means: red (20*200 + 40*120)/100
    = 88, green (30*200 + 40*140)/100 = 116, blue (10*200 + 40*60)/100 = 44."""
    frame = numpy.empty((10, 10, 3), dtype=numpy.uint8)
    frame[0:2] = [200, 0, 0]
    frame[2:5] = [0, 200, 0]
    frame[5:6] = [0, 0, 200]
    frame[6:10] = [120, 140, 60]
    return frame


CHANNEL_SIX = (20.0, 30.0, 10.0, 88.0, 116.0, 44.0)


def test_the_six_numbers_of_a_frame_with_known_channels(monitor):
    assert monitor._measure_rgb(channel_frame()) == pytest.approx(CHANNEL_SIX)
    assert RgbAnalysis.RGB_KEYS == ("red", "green", "blue",
                                   "r_mean", "g_mean", "b_mean")


def test_the_green_and_blue_masks_have_the_red_masks_structure(monitor):
    """A channel above its threshold and the other two below their caps;
    the boundaries are strict, as the red mask's are."""
    image = numpy.zeros((10, 10, 3), dtype=numpy.uint8)
    image[0, :] = [99, 151, 99]      # green, just inside
    image[1, :] = [0, 150, 0]        # green, just outside: g > 150 is False
    image[2, :] = [100, 200, 0]      # green, outside: r < 100 is False
    image[3, :] = [99, 99, 151]      # blue, just inside
    image[4, :] = [0, 0, 150]        # blue, just outside
    image[5, :] = [0, 100, 200]      # blue, outside: g < 100 is False
    red, green, blue = monitor._measure_rgb(image)[:3]
    assert (red, green, blue) == (0.0, pytest.approx(10.0), pytest.approx(10.0))
    assert (RgbAnalysis.GREEN_MIN, RgbAnalysis.BLUE_MIN, RgbAnalysis.RED_MAX) == \
        (150, 150, 100)


def test_the_six_numbers_read_a_bgra_screenshot_in_its_own_order(monitor):
    class Shot:
        width, height = 4, 2
        # BGRA: one red, two green, one blue pixel, four black
        bgra = bytes([0, 0, 200, 255] + [0, 200, 0, 255] * 2
                     + [200, 0, 0, 255] + [0, 0, 0, 255] * 4)

    assert monitor._measure_rgb(Shot()) == pytest.approx(
        (12.5, 25.0, 12.5, 25.0, 50.0, 25.0))


def test_the_red_share_is_the_red_detectors_to_the_bit(monitor):
    """The red share beside the five new numbers is `_measure_red`'s, so no
    bench number moves: the same comparisons over the same pixels."""
    rng = numpy.random.default_rng(7)
    for red_min in (0, 120, 150, 254):
        monitor.red_min = red_min
        for _ in range(5):
            frame = rng.integers(0, 256, size=(23, 37, 3), dtype=numpy.uint8)
            assert monitor._measure_rgb(frame)[0] == monitor._measure_red(frame)


def test_no_frame_is_six_zeros_not_an_exception(monitor):
    assert monitor._measure_rgb(None) == (0.0,) * 6


def _channel_run(monitor, **kwargs):
    monitor.screen = fake_screen(frames=[channel_frame()], varying=False)
    monitor.screen.open()
    return _started(monitor, **kwargs)


def test_the_five_new_keys_reach_every_subscriber_with_the_row(monitor):
    """The row dict a subscriber gets carries the positions and the five
    new numbers, the subscriber's own copy, read with `.get`."""
    seen = []
    monitor.subscribe(lambda t, red, row: seen.append((red, row)))
    _channel_run(monitor, sync_axes="Z")
    assert _wait_for(lambda: len(seen) >= 1)
    monitor.end_run()
    monitor._run.thread.join(2)
    red, row = seen[0]
    assert set(row) == {"Z", "green", "blue", "r_mean", "g_mean", "b_mean"}
    assert red == pytest.approx(20.0)
    assert [row[k] for k in RgbAnalysis.CHANNEL_KEYS] == \
        pytest.approx(CHANNEL_SIX[1:])


def test_the_run_log_and_its_csv_carry_the_five_columns(monitor):
    from model.rgb_analysis import CHANNEL_COLUMNS
    _channel_run(monitor, sync_axes="X")
    assert _wait_for(lambda: monitor.rows_written >= 1)
    monitor.end_run()
    monitor._run.thread.join(2)
    path = monitor.save()
    with open(path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    for column, value in zip(CHANNEL_COLUMNS, CHANNEL_SIX[1:]):
        assert float(rows[0][column]) == pytest.approx(value), column
    assert float(rows[0][plot_data.RED_COLUMN]) == pytest.approx(20.0)
    # the parser reads by header name, so a run with the new columns loads
    assert plot_data.load_run(path)["red_percents"][0] == pytest.approx(20.0)


def test_state_run_carries_the_latest_six(monitor):
    assert monitor.state["run"]["latest"] == dict.fromkeys(RgbAnalysis.RGB_KEYS)
    _channel_run(monitor)
    assert _wait_for(lambda: monitor.frames_captured >= 1)
    monitor.end_run()
    latest = monitor.state["run"]["latest"]
    assert list(latest) == list(RgbAnalysis.RGB_KEYS)
    assert [latest[k] for k in RgbAnalysis.RGB_KEYS] == pytest.approx(CHANNEL_SIX)


def test_the_five_readouts_are_under_details_and_red_stays_in_tier_one(monitor):
    _channel_run(monitor)
    assert _wait_for(lambda: monitor.frames_captured >= 1)
    monitor.end_run()
    attrs = ("current_green", "current_blue", "mean_red", "mean_green",
             "mean_blue")
    sections = monitor.schema["sections"]
    where = {e.get("model_attr"): s for s in sections for e in s["elements"]}
    assert where["current_red"]["tier"] == 1
    tier_two = {s["disclosure"] for s in sections if s.get("tier") == 2}
    for attr in attrs:
        assert where[attr]["tier"] == 2, attr
        assert where[attr]["disclosure"] in tier_two
    values = monitor.state["values"]
    assert [float(values[a]) for a in attrs] == pytest.approx(CHANNEL_SIX[1:])
    for attr in attrs:
        assert monitor.set_value(attr, "5").is_refused       # read-only


def test_the_sidecar_records_the_channel_masks(logged):
    logged.save()
    meta = json.loads((logged.run_dir / "C001_station_meta.json").read_text())
    assert meta["channel_thresholds"] == {
        "green": {"g_min": 150, "r_max": 100, "b_max": 100},
        "blue": {"b_min": 150, "r_max": 100, "g_max": 100}}


# ---------------------------------------------------------------------
# RG-2 (2026-10-07): the factor is an analysis setting. The numbers this
# model publishes are profile columns; `transfer_map_analysis` reads any of
# them (or a ratio of two) as the column that drives the extrema.
# ---------------------------------------------------------------------

def _lowering(t, base, peak, dip):
    """A hover at `base`, a rise to `peak` at 2 s, a fall to `dip` at 3 s,
    flat after: the owner's picture of a lowering, one column of it."""
    out = []
    for s in t:
        if s < 1.5:
            out.append(base)
        elif s < 2.0:
            out.append(base + (peak - base) * (s - 1.5) / 0.5)
        elif s < 3.0:
            out.append(peak + (dip - peak) * (s - 2.0))
        else:
            out.append(dip)
    return out


def factor_profile():
    """Red and green with different shapes: red peaks at 2.0 s and dips at
    3.0 s; green is red shifted 0.5 s later and scaled, so its extrema are
    elsewhere and its values are not red's."""
    t = [i / 100.0 for i in range(500)]
    red = _lowering(t, 20.0, 35.0, 8.0)
    green = _lowering([s - 0.5 for s in t], 5.0, 9.0, 2.0)
    return {"t": t, "red": red, "green": green,
            "blue": [1.0] * len(t), "r_mean": [s + 100.0 for s in red],
            "g_mean": [0.0] * len(t), "b_mean": [50.0] * len(t)}


def test_the_factor_parser_names_a_column_or_a_ratio():
    from model import transfer_map_analysis as tma
    assert tma.DEFAULT_FACTOR == "red"
    assert tma.FACTOR_COLUMNS == ("red", "green", "blue", "r_mean", "g_mean",
                                  "b_mean")
    assert tma.parse_factor("red") == ("red", None)
    assert tma.parse_factor("b_mean") == ("b_mean", None)
    assert tma.parse_factor("red/green") == ("red", "green")
    assert tma.parse_factor(" Red / G_Mean ") == ("red", "g_mean")
    for bad in ("purple", "red/", "/green", "red/green/blue", "", "red//green",
                None, 3):
        with pytest.raises(ValueError):
            tma.parse_factor(bad)
    with pytest.raises(ValueError, match="purple"):
        tma.parse_factor("red/purple")


def test_a_ratio_factor_divides_and_a_zero_denominator_is_not_a_number():
    from model import transfer_map_analysis as tma
    profile = {"t": [0.0, 0.1, 0.2], "red": [4.0, 6.0, 8.0],
               "green": [2.0, 0.0, None]}
    values = tma.factor_values(profile, "red/green")
    assert values[0] == pytest.approx(2.0)
    assert numpy.isnan(values[1]) and numpy.isnan(values[2])
    assert list(tma.factor_values(profile, "red")) == [4.0, 6.0, 8.0]


def test_a_profile_lacking_the_factors_column_is_refused_by_name():
    from model import transfer_map_analysis as tma
    old = {"t": [0.0, 0.1], "red": [1.0, 2.0]}            # a red-only profile
    with pytest.raises(ValueError, match="green"):
        tma.detect(old, factor="green")
    with pytest.raises(ValueError, match="b_mean"):
        tma.force_indices(old, {}, factor="red/b_mean")
    with pytest.raises(ValueError, match="blue"):
        tma.detect({**old, "blue": [None, None]}, factor="blue")


def test_red_is_the_default_factor_and_nothing_moves():
    from model import transfer_map_analysis as tma
    profile = factor_profile()
    red_only = {"t": profile["t"], "red": profile["red"]}
    default, named = tma.detect(profile, 3.5), tma.detect(profile, 3.5, factor="red")
    alone = tma.detect(red_only, 3.5)
    for key in ("max_t", "min_t", "max_i", "min_i", "red_max", "red_min",
                "baseline", "masked_share"):
        assert default[key] == named[key] == alone[key], key
    assert tma.force_indices(profile, {"operator_t": 3.5}) == \
        tma.force_indices(red_only, {"operator_t": 3.5}) == \
        tma.force_indices(profile, {"operator_t": 3.5}, factor="red")


def test_two_factors_find_their_own_extrema_on_one_profile():
    from model import transfer_map_analysis as tma
    profile = factor_profile()
    red = tma.detect(profile, 4.0)
    green = tma.detect(profile, 4.0, factor="green")
    assert red["max_t"] == pytest.approx(2.0, abs=0.03)
    assert red["min_t"] == pytest.approx(3.0, abs=0.03)
    assert green["max_t"] == pytest.approx(2.5, abs=0.03)
    assert green["min_t"] == pytest.approx(3.5, abs=0.03)
    # (the 5-sample median takes a little off a sharp peak)
    assert (green["red_max"], green["red_min"], green["baseline"]) == \
        pytest.approx((9.0, 2.0, 5.0), abs=0.1)
    # the green factor reads exactly as a profile whose red WAS the green
    as_red = tma.detect({"t": profile["t"], "red": profile["green"]}, 4.0)
    for key in ("max_t", "min_t", "red_max", "red_min", "baseline"):
        assert green[key] == as_red[key], key
    forces = tma.force_indices(profile, {"operator_t": 4.0}, factor="green")
    assert forces == tma.force_indices(
        {"t": profile["t"], "red": profile["green"]}, {"operator_t": 4.0})
    assert forces["shadow_vs_peak"] == pytest.approx((9.0 - 2.0) / 9.0, abs=0.01)
    assert forces != tma.force_indices(profile, {"operator_t": 4.0})
    # a ratio is one more column: r_mean / b_mean = (red + 100) / 50
    ratio = tma.detect(profile, 4.0, factor="r_mean/b_mean")
    assert ratio["red_max"] == pytest.approx(135.0 / 50.0, abs=0.02)
    assert ratio["max_t"] == red["max_t"]
    assert tma.baseline_of(profile, factor="green") == pytest.approx(5.0)


def test_the_rows_a_factor_is_read_over_are_the_red_masks():
    """A glitch is a property of the grab, not of a column: a black grab
    (red 0.0) is masked for every factor, while a green share of 0.0 (the
    bench scene has none) is a reading, not a black grab."""
    from model import transfer_map_analysis as tma
    profile = factor_profile()
    profile["red"][120] = 0.0                       # a black grab
    green = tma.detect(profile, 4.0, factor="g_mean")   # g_mean is all 0.0
    assert not green["settled_mask"][120]
    assert green["settled_mask"].sum() == len(profile["t"]) - 1
    assert green["red_max"] == 0.0 and green["max_t"] is None   # flat, not masked


# -- dev/reanalyse_trials.py --factor -------------------------------------------

def _reanalyse_tool():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / "dev" / "reanalyse_trials.py"
    spec = importlib.util.spec_from_file_location("reanalyse_trials_rgb", path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def _trial_db(path, columns=()):
    """A store with two trials over `factor_profile()`: the profile table
    carries `red` plus `columns`; trial 2's channel cells are NULL (recorded
    before the columns existed, as an old row of a migrated table is)."""
    import sqlite3
    profile = factor_profile()
    conn = sqlite3.connect(path)
    extra = "".join(f", {c} REAL" for c in columns)
    conn.execute("CREATE TABLE trials (id INTEGER PRIMARY KEY, "
                 "mark_operator_t REAL, red_min REAL, red_max REAL, "
                 "red_baseline REAL)")
    conn.execute(f"CREATE TABLE profile (trial_id INTEGER, t_s REAL, red REAL, "
                 f"z REAL{extra})")
    for trial in (1, 2):
        conn.execute("INSERT INTO trials VALUES (?, 4.0, 1.0, 2.0, 3.0)", (trial,))
        for i, t in enumerate(profile["t"]):
            cells = [profile[c][i] if trial == 1 else None for c in columns]
            conn.execute(f"INSERT INTO profile VALUES (?, ?, ?, ?"
                         f"{', ?' * len(columns)})",
                         (trial, t, profile["red"][i], None, *cells))
    conn.commit()
    conn.close()


def test_reanalyse_takes_a_factor_and_refuses_an_unknown_one(capsys):
    tool = _reanalyse_tool()
    assert tool.parse_args(["x.sqlite"]).factor == "red"
    assert tool.parse_args(["x.sqlite", "--factor", "red/green"]).factor == "red/green"
    with pytest.raises(SystemExit):
        tool.parse_args(["x.sqlite", "--factor", "purple"])
    assert "purple" in capsys.readouterr().err


def test_reanalyse_with_a_factor_reports_its_extrema_and_the_red_only_trials(tmp_path):
    tool = _reanalyse_tool()
    db = str(tmp_path / "map.sqlite")
    _trial_db(db, columns=("green", "blue", "r_mean", "g_mean", "b_mean"))
    assert tool.main([db, "--out", str(tmp_path / "out"), "--factor", "green"]) == 0
    md = (tmp_path / "out").glob("reanalysis_*_green.md")
    text = next(md).read_text()
    assert "factor: green" in text
    assert "red only" in text
    rows = list(csv.DictReader(open(next((tmp_path / "out").glob("reanalysis_*_green.csv")))))
    first, second = rows
    assert first["factor"] == "green" and first["note"] == ""
    assert float(first["new_red_max"]) == pytest.approx(9.0, abs=0.1)
    assert float(first["new_red_min"]) == pytest.approx(2.0, abs=0.1)
    assert second["new_red_max"] == "" and "red only" in second["note"]


def test_reanalyse_says_so_when_no_profile_carries_the_column(tmp_path):
    tool = _reanalyse_tool()
    db = str(tmp_path / "old.sqlite")
    _trial_db(db)                                       # red only, as the bench's
    tool.main([db, "--out", str(tmp_path / "out"), "--factor", "red/green"])
    text = next((tmp_path / "out").glob("reanalysis_*_red-green.md")).read_text()
    assert "factor: red/green" in text and "no green column" in text
    rows = list(csv.DictReader(open(next((tmp_path / "out").glob("reanalysis_*_red-green.csv")))))
    assert len(rows) == 2 and all(r["new_red_max"] == "" for r in rows)


def test_reanalyse_red_is_unchanged_and_write_needs_the_red_factor(tmp_path):
    tool = _reanalyse_tool()
    db = str(tmp_path / "map.sqlite")
    _trial_db(db, columns=("green",))
    tool.main([db, "--out", str(tmp_path / "out")])
    rows = list(csv.DictReader(open(next((tmp_path / "out").glob("reanalysis_????-??-??.csv")))))
    assert float(rows[0]["new_red_max"]) == pytest.approx(35.0, abs=0.5)
    with pytest.raises(SystemExit, match="red"):
        tool.main([db, "--out", str(tmp_path / "out"), "--factor", "green",
                   "--write"])
    assert not (tmp_path / "map.sqlite.pre-reanalysis.bak").exists()


# ---------------------------------------------------------------------
# RG-3 (2026-10-07): Red Percent is RGB analysis wherever the registry or
# an operator sees it. The stores are untouched (the profile column is red).
# ---------------------------------------------------------------------

def test_the_registry_builds_rgb_analysis_under_its_new_name_on_the_map(
        tmp_path, monkeypatch):
    """Setup's registry keys the class by its NAME; ticking the Transfer
    Map's row launches it, hosted (`HOST` resolves in the Controller's
    state), under "RGB Analysis" and never "Red Percent"."""
    import model.rgb_analysis as module
    from controller import setup as station_setup
    from controller.controller import Controller
    monkeypatch.setenv("STATION_CONFIG", str(tmp_path / "station.json"))
    monkeypatch.setattr(module, "Screen", lambda: fake_screen())   # no display
    assert station_setup.MODEL_TYPES["RGB Analysis"] is RgbAnalysis
    assert "Red Percent" not in station_setup.MODEL_TYPES
    controller = Controller()
    setup = station_setup.Setup(controller)
    try:
        assert not any(row["name"] == "RGB Analysis" for row in setup._rows.values()), \
            "a hosted model has no Setup row"
        key = next(k for k, row in setup._rows.items()
                   if row["name"] == "Transfer Map")
        getattr(setup, f"set_{key}_enabled")(True)
        assert "RGB Analysis" in [c["model"] for c in setup.configs]
        setup.launch()
        models = controller.state()["models"]
        assert "Red Percent" not in models
        assert models["RGB Analysis"]["host"] == "Transfer Map"
        assert models["RGB Analysis"]["name"] == "RGB Analysis"
        assert isinstance(controller.models["RGB Analysis"], RgbAnalysis)
    finally:
        controller.reset()


def test_the_operator_sees_rgb_analysis_in_the_stop_and_the_events(
        monitor, monkeypatch):
    import schema as sch
    from events import events
    stop = next(e for e in sch.elements(monitor.schema)
                if e.get("command") == "toggle_estop")
    assert stop["tooltip"] == "Stop the RGB Analysis"
    seen = []
    real = events.info

    def spy(title, message, **kwargs):
        seen.append((title, kwargs.get("source")))
        return real(title, message, **kwargs)

    monkeypatch.setattr(events, "info", spy)
    _started(monitor)
    monitor.end_run()
    sources = {source for title, source in seen
               if title in ("Run Started", "Run Ended")}
    assert sources == {"RGB Analysis"}
    assert "Red Percent" not in repr(monitor.schema)


def test_the_telemetry_stream_slug_is_rgb_analysis():
    from model import trial_telemetry
    assert trial_telemetry._slug(RgbAnalysis.NAME) == "rgb_analysis"


# -- label_run (the Transfer Map names the run it records through) -----------------

def test_label_run_renames_the_active_run_until_something_is_saved(tmp_path):
    model = RgbAnalysis(screen=fake_screen())
    model.output_root = tmp_path
    model.open()
    try:
        assert model.label_run("trial001") is None          # no run yet
        model.set_region(0, 0, 10, 10)
        model.start_run(confirmed=True)
        assert model.run_id.startswith("run_")
        token = model.run_token
        assert model.label_run("trial001_tip-T7", {"consumable_id": "T7"}) == "trial001_tip-T7"
        assert model.run_token is token and model.run_id == "trial001_tip-T7"
        assert model.run_dir == tmp_path / "trial001_tip-T7"
        assert token.annotations["consumable_id"] == "T7"
        assert model.label_run("") == "trial001_tip-T7"     # blank: no change
        model._saved_rows = 1                               # rows are on disk
        assert model.label_run("other") is None
        assert model.run_id == "trial001_tip-T7"
        model.end_run()
        assert model.label_run("later") is None
    finally:
        model.close()
