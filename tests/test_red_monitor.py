"""`model.red_monitor` — RunLog, MonitorRun and the RedMonitor model.

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
from model.red_monitor import MonitorRun, RedMonitor, RunLog
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
    the red percent changes on EVERY grab and change-triggered sampling
    logs a row per frame."""

    def __init__(self, frames, delay, varying=True):
        self._frames, self._delay, self._varying = frames, delay, varying
        self.grabs = 0

    def grab(self, region):
        self.grabs += 1
        if self._delay:
            time.sleep(self._delay)
        index = min(self.grabs - 1, len(self._frames) - 1)
        frame = self._frames[index]
        repeats = self.grabs - len(self._frames)
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
    """A position source, duck-typed exactly as `RedMonitor` asks for one."""

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
    model = RedMonitor(screen=fake_screen())
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
    assert rows[1] == ["0.0", "10.0", "1.0", "", "0.01"]
    assert rows[2] == ["0.1", "20.0", "", "", ""]
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
    before = RedMonitor(screen=fake_screen()).output_root
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    after = RedMonitor(screen=fake_screen()).output_root

    assert before.is_absolute()
    assert before == after, "output_root moved when the CWD moved"


def test_the_data_root_environment_variable_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path))
    assert RedMonitor(screen=fake_screen()).output_root == tmp_path.resolve()


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
    RedMonitor.baseline_red = CountingBaseline()
    try:
        monitor._publish_red(8.0)
    finally:
        del RedMonitor.baseline_red

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
    model = RedMonitor()
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
    monitor._measure_red = lambda frame, threshold=None: 1 / 0
    monitor.start_run(confirmed=True)
    run = monitor._run

    assert _wait_for(lambda: not run.thread.is_alive())
    assert not monitor.is_running
    assert run.failure is not None
    assert isinstance(run.failure, ZeroDivisionError)
    assert "Red Percent Run Failed" in capsys.readouterr().err


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
    assert len(RunLog(["X", "Y"]).headers) == 2 + 2 * 2 + 1


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
    names = [field.name for field in RedMonitor.ANNOTATION_FIELDS]
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
    parameters = list(inspect.signature(RedMonitor.save).parameters)
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
    assert {"region_select", "file_save", "file_open", "image", "plot",
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
