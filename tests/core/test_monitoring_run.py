"""S13 items 1-4 — `MonitoringRun` (RC-11).

The last real RC-11 repair. `RedPercentSystem` used to treat one monitoring
session as a set of live, independently-mutable attributes the thread read
directly on every frame — `sync_dimensions`, `data_log`, `monitoring`,
`current_red`/`red_change`, `last_logged_red` (via `hasattr`). Nothing froze
the configuration at Start, nothing stopped two threads existing at once,
and nothing published `current_red`/`red_change` as a pair.

These tests pin the fix: `MonitoringRun` owns a frozen configuration snapshot
and its own thread/stop-event/generation/data-log, `RedPercentSystem.
monitoring` is derived from it, and the loop can no longer alias state
across two runs. Findings: REDPERCENT-1, 2, 3, 4 (remainder), 5, 9, 16;
VIEW-TKINTER-15.

`tests/conftest.py` replaces `mss`, `PIL` and `matplotlib` with `MagicMock`
(numpy is real). A capture path driven through the raw mocks produces
`MagicMock` comparisons rather than real booleans/arrays, so most tests here
monkeypatch `capture_focus_area`/`detect_red` directly for deterministic
values — the same technique `tests/core/test_model_interactions.py` already
uses. A loop over/around a `MagicMock` yields nothing on its own; check what
each assertion is actually driven by.
"""
import threading
import time

import pytest
from unittest.mock import patch

import error_routing
from error_routing import EventBus
from model.redpercent_system import MonitoringRun, RedPercentDataLog, RedPercentSystem
from model.plot_data import parse_red_percent_csv
from results import CommandResult


# ---------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def isolated_bus(monkeypatch):
    """A fresh `EventBus` in place of the process one, for this test only —
    same technique as `test_redpercent4_velocity_reads.py`."""
    fresh = EventBus()
    monkeypatch.setattr(error_routing, "bus", fresh)
    return fresh


@pytest.fixture
def ready_system(tmp_path):
    """A system that `start_monitoring()` will actually accept: a focus
    area is set and artifacts land under `tmp_path`, never the CWD."""
    system = RedPercentSystem()
    system.output_root = tmp_path
    system.set_focus_area(0, 0, 10, 10)
    yield system
    # Belt-and-suspenders beyond tests/conftest.py's leaked-thread sweep:
    # a test that asserts partway through a real thread's life should not
    # depend on the sweep to keep the suite from accumulating live threads.
    system.teardown()


def _running(system, capture_value=object(), red_pct=10.0):
    """Start `system` with a controlled, deterministic capture path and
    return the `CommandResult`. `capture_focus_area`/`detect_red` are
    monkeypatched rather than driven through the mocked `mss`/`PIL`/numpy
    stack, so a sample is logged on (almost) every frame without depending
    on MagicMock arithmetic behaving like real comparisons.
    """
    system.capture_focus_area = lambda sct: capture_value
    system.detect_red = lambda image: red_pct
    with patch('model.redpercent_system.mss.mss'):
        return system.start_monitoring()


# ---------------------------------------------------------------------
# MonitoringRun: frozen configuration (REDPERCENT-1)
# ---------------------------------------------------------------------


def test_monitoring_run_freezes_sync_dimensions_as_a_tuple():
    """REDPERCENT-1: the run must hold `tuple(...)`, not the model's list —
    aliasing the list is the defect itself."""
    live = ["X", "Y"]
    run = MonitoringRun(sync_dimensions=live, focus_area=None,
                        probe_name="", probe_tilt_angle=0.0, run_id="r1",
                        output_root="/tmp", annotations={}, generation=1)
    assert run.sync_dimensions == ("X", "Y")
    assert isinstance(run.sync_dimensions, tuple)

    live.append("Z")
    live.pop(0)
    assert run.sync_dimensions == ("X", "Y"), (
        "mutating the caller's list reached the run's frozen copy")


def test_monitoring_run_freezes_focus_area_as_a_copy():
    live = {"top": 1, "left": 2, "width": 3, "height": 4}
    run = MonitoringRun(sync_dimensions=[], focus_area=live, probe_name="",
                        probe_tilt_angle=0.0, run_id="r1", output_root="/tmp",
                        annotations={}, generation=1)
    live["top"] = 999
    assert run.focus_area["top"] == 1


def test_monitoring_run_gets_its_own_fresh_data_log():
    run = MonitoringRun(sync_dimensions=["X"], focus_area=None, probe_name="",
                        probe_tilt_angle=0.0, run_id="r1", output_root="/tmp",
                        annotations={}, generation=1)
    assert isinstance(run.data_log, RedPercentDataLog)
    assert run.data_log.red_values == []
    assert run.data_log.sync_dimensions == ("X",)


# ---------------------------------------------------------------------
# start_monitoring: refusals (REDPERCENT-9, REDPERCENT-4's mss=None half)
# ---------------------------------------------------------------------


def test_start_monitoring_refuses_without_a_focus_area():
    """REDPERCENT-9: today's code starts anyway — `capture_focus_area`
    returns `None` forever and the loop spins with `monitoring` True and
    zero samples. It must refuse, and start nothing."""
    system = RedPercentSystem()
    assert system.focus_area is None

    result = system.start_monitoring()

    assert isinstance(result, CommandResult)
    assert result.refused, result
    assert not system.monitoring
    assert system._monitor_thread is None


def test_start_monitoring_refuses_when_already_active(ready_system):
    first = _running(ready_system)
    assert first.ok, first.reason

    second = ready_system.start_monitoring()
    assert second.refused, second


@pytest.mark.parametrize("dep_name", ["mss", "np", "Image"])
def test_start_monitoring_refuses_when_a_dependency_is_missing(
        ready_system, monkeypatch, dep_name):
    """REDPERCENT-4's `mss=None` half: today `_monitor_colors` does
    `with mss.mss()` and raises `AttributeError` inside the thread the
    instant it starts, with `monitoring` already True. It must refuse
    before ever starting a thread."""
    monkeypatch.setattr(f"model.redpercent_system.{dep_name}", None)

    result = ready_system.start_monitoring()

    assert result.refused, result
    assert not ready_system.monitoring
    assert ready_system._monitor_thread is None


def test_start_monitoring_ok_returns_the_run(ready_system):
    result = _running(ready_system)
    assert result.ok, result.reason
    assert isinstance(result.value, MonitoringRun)
    assert ready_system.monitoring
    assert ready_system._monitor_thread is not None
    assert ready_system._monitor_thread.is_alive()


# ---------------------------------------------------------------------
# REDPERCENT-2: a fresh run gets a fresh log
# ---------------------------------------------------------------------


def test_a_fresh_run_gets_a_fresh_data_log(ready_system):
    """The defect: `start_monitoring` did `if not self.data_log:`, so a
    second run appended into the first run's log — runs concatenated,
    `has_unsaved_data` stayed sticky forever, and the new run's first
    sample was dedup-skipped against the old run's `last_logged_red`."""
    first = _running(ready_system)
    assert first.ok
    time.sleep(0.05)
    ready_system.stop_monitoring()
    first_log = ready_system.data_log
    assert first_log is first.value.data_log
    assert first_log.red_values, "the first run logged nothing to compare against"

    second = _running(ready_system)
    assert second.ok, second.reason
    try:
        second_log = ready_system.data_log
        assert second_log is not first_log, (
            "start_monitoring must not reuse the previous run's data_log "
            "(REDPERCENT-2's `if not self.data_log:`)")
        assert second_log is second.value.data_log
        # The second run's own thread is already running concurrently by
        # the time this assertion runs, so its log may already hold its own
        # first sample — a *fresh* object, freshly logging, is the point;
        # "still empty" is not guaranteed and is not what REDPERCENT-2 is
        # about (`test_last_logged_red_does_not_carry_over_between_runs`
        # below is the direct, non-racy proof of the dedup-carry-over half).
        assert first_log.red_values, "unaffected by starting the second run"
    finally:
        ready_system.stop_monitoring()


def test_last_logged_red_does_not_carry_over_between_runs(ready_system):
    """`last_logged_red` belongs on the run, initialised in its
    constructor — not a `hasattr` check inside the thread that let it
    default from whatever the previous run last wrote."""
    run = MonitoringRun(sync_dimensions=[], focus_area=None, probe_name="",
                        probe_tilt_angle=0.0, run_id="r1", output_root="/tmp",
                        annotations={}, generation=1)
    assert run.last_logged_red == -1000.0


# ---------------------------------------------------------------------
# REDPERCENT-1 / VIEW-TKINTER-15 (RC-11 item 2): sync toggles refuse mid-run
# ---------------------------------------------------------------------


def test_toggle_sync_refuses_while_a_run_is_active(ready_system):
    ready_system.sync_dimensions = []
    result = _running(ready_system)
    assert result.ok
    try:
        before = list(ready_system.sync_dimensions)
        outcome = ready_system.toggle_sync_x()
        assert outcome is not None and outcome.refused, outcome
        assert ready_system.sync_dimensions == before, (
            "a refused toggle must not still mutate sync_dimensions")
    finally:
        ready_system.stop_monitoring()


def test_direct_setattr_on_sync_x_is_ignored_mid_run(ready_system):
    """The web client reaches `sync_x` through `set_device_attribute`,
    which calls `setattr` directly rather than `toggle_sync_x()` — a path
    a rendered, disabled widget never protected (VIEW-TKINTER-15's Tk
    twin). The property setter itself must refuse."""
    ready_system.sync_dimensions = []
    result = _running(ready_system)
    assert result.ok
    try:
        ready_system.sync_x = True
        assert ready_system.sync_dimensions == [], (
            "a direct attribute write slipped past the guard mid-run")
    finally:
        ready_system.stop_monitoring()


def test_toggle_sync_still_works_when_no_run_is_active():
    """The guard must not become a blanket refusal — only mid-run."""
    system = RedPercentSystem()
    assert not system.monitoring
    system.toggle_sync_x()
    assert 'X' in system.sync_dimensions


# ---------------------------------------------------------------------
# REDPERCENT-3: the generation-token pattern from probes.py
# ---------------------------------------------------------------------


def test_generation_token_invalidates_a_superseded_generation():
    system = RedPercentSystem()
    gen1 = system._new_monitor_generation()
    assert system._monitor_generation_is_current(gen1)

    gen2 = system._new_monitor_generation()
    assert not system._monitor_generation_is_current(gen1)
    assert system._monitor_generation_is_current(gen2)


def test_a_stale_generation_stops_the_loop_without_the_stop_event(ready_system):
    """The exact race REDPERCENT-3 names: a Stop -> Start race can leave an
    old thread alive with its own `stop_event` still unset at the instant a
    new generation exists. The loop condition checks the generation
    independently of `stop_event`, so the thread must still exit — and must
    not write again after its generation goes stale.

    `detect_red` returns a strictly increasing value so the dedup threshold
    (`abs(rounded_red - last_logged_red) >= 0.1`) never itself explains an
    absence of new samples — only the generation check can.
    """
    counter = {"n": 0}

    def _increasing_red(image):
        counter["n"] += 1
        return float(counter["n"])

    ready_system.capture_focus_area = lambda sct: object()
    ready_system.detect_red = _increasing_red
    with patch('model.redpercent_system.mss.mss'):
        result = ready_system.start_monitoring()
    assert result.ok
    run = result.value

    # Let it log a few samples.
    for _ in range(200):
        if len(run.data_log.red_values) >= 3:
            break
        time.sleep(0.01)
    assert len(run.data_log.red_values) >= 3, (
        "the run never logged enough samples to race against")

    # Simulate the race directly: a new generation exists, but nobody set
    # *this* run's stop_event (unlike a normal stop_monitoring() call).
    ready_system._new_monitor_generation()
    assert not ready_system._monitor_generation_is_current(run.generation)

    run.thread.join(timeout=1.0)
    assert not run.thread.is_alive(), (
        "a thread whose generation went stale must still exit")

    count_after_join = len(run.data_log.red_values)
    time.sleep(0.1)
    assert len(run.data_log.red_values) == count_after_join, (
        "a stale-generation thread wrote after it should have exited")


# ---------------------------------------------------------------------
# REDPERCENT-5: current_red / red_change published as a pair
# ---------------------------------------------------------------------


def test_publish_red_reads_baseline_exactly_once():
    """The defect: `red_change` computed
    `((red_pct - self.baseline_red) / max(self.baseline_red, 0.1)) * 100`
    — two reads of `self.baseline_red` in one expression, so a concurrent
    `reset_baseline()` between them mixed a value from two different
    baselines."""
    system = RedPercentSystem()
    reads = {"count": 0}

    class _CountingBaseline:
        def __get__(self, obj, objtype=None):
            reads["count"] += 1
            return 4.0

        def __set__(self, obj, value):
            # A no-op *data* descriptor: only a data descriptor (one
            # defining __set__ as well as __get__) takes priority over the
            # instance's own __dict__, which `__init__` already populated
            # with a plain `baseline_red = 0.0`.
            pass

    del system.baseline_red  # drop the instance shadow so the class
                              # descriptor below is actually consulted
    RedPercentSystem.baseline_red = _CountingBaseline()
    try:
        system._publish_red(8.0)
    finally:
        del RedPercentSystem.baseline_red

    assert reads["count"] == 1, (
        f"baseline_red was read {reads['count']} times computing one sample")


def test_current_red_and_red_change_always_agree():
    system = RedPercentSystem()
    system.baseline_red = 10.0
    system._publish_red(20.0)
    assert system.current_red == 20.0
    assert system.red_change == pytest.approx(100.0)

    system.baseline_red = 5.0  # a "concurrent" reset, from the pair's view
    # Reading current_red/red_change now must not re-derive red_change from
    # the *new* baseline — the pair published together stays together.
    assert system.current_red == 20.0
    assert system.red_change == pytest.approx(100.0)


# ---------------------------------------------------------------------
# REDPERCENT-4 remainder: exception isolation in the loop
# ---------------------------------------------------------------------


def test_an_exception_in_the_loop_clears_monitoring_and_is_reported(
        ready_system, isolated_bus):
    """Before this, the monitor loop body had no exception handling: any
    failure left `monitoring` True with a dead thread and the UI showing
    "monitoring" forever."""
    def _boom(image):
        raise RuntimeError("detector exploded")

    ready_system.capture_focus_area = lambda sct: object()
    ready_system.detect_red = _boom
    with patch('model.redpercent_system.mss.mss'):
        result = ready_system.start_monitoring()
    assert result.ok
    run = result.value

    run.thread.join(timeout=1.0)
    assert not run.thread.is_alive()
    assert not ready_system.monitoring
    assert run.failure is not None
    assert "detector exploded" in str(run.failure)

    events = isolated_bus.since(0)
    assert events, "the loop's failure was never reported"
    assert any("detector exploded" in e.message for e in events)


# ---------------------------------------------------------------------
# REDPERCENT-16: velocity from position deltas, and honest invalid samples
# ---------------------------------------------------------------------


class _FakeStepper:
    def __init__(self, x):
        self.pos_x = x
        # A gamepad-deflection value that must NOT end up in the CSV as
        # "velocity" any more (REDPERCENT-16) — if `_read_dim` ever
        # regresses to reading this, these tests catch it because it never
        # matches the position-delta math below.
        self.vel_x = 999.0


def test_velocity_is_derived_from_position_deltas_not_vel_x(ready_system):
    stepper = _FakeStepper(0.0)
    ready_system.stepper_model = stepper
    ready_system.sync_dimensions = ["X"]
    run = MonitoringRun(sync_dimensions=["X"], focus_area=None, probe_name="",
                        probe_tilt_angle=0.0, run_id="r1", output_root="/tmp",
                        annotations={}, generation=1)

    loc1, vel1 = ready_system._read_dim(run, "X", 100.0)
    assert loc1 == 0.0
    assert vel1 is None, "the first sample has no prior point to difference against"

    stepper.pos_x = 2.0
    loc2, vel2 = ready_system._read_dim(run, "X", 101.0)
    assert loc2 == 2.0
    assert vel2 == pytest.approx(2.0), "2.0 units over 1.0 s must be 2.0, not 999.0 (vel_x)"


def test_an_invalid_position_read_is_none_not_zero(ready_system):
    """ERRORS-7: the old `except: locs['X'] = 0.0` wrote a real position of
    zero and a failed read as the same number."""
    class _BrokenStepper:
        pos_x = "not-a-number"

    ready_system.stepper_model = _BrokenStepper()
    run = MonitoringRun(sync_dimensions=["X"], focus_area=None, probe_name="",
                        probe_tilt_angle=0.0, run_id="r1", output_root="/tmp",
                        annotations={}, generation=1)

    loc, vel = ready_system._read_dim(run, "X", 100.0)
    assert loc is None
    assert vel is None


def test_invalid_sample_is_never_written_as_zero_in_add_entry():
    log = RedPercentDataLog(["X"])
    log.add_entry(50.0, locs={"X": None}, vels={"X": None})
    assert log.loc_values["X"] == [None]
    assert log.vel_values["X"] == [None]


# ---------------------------------------------------------------------
# REDPERCENT-16: a timestamp column, and the sentinel round-trips honestly
# ---------------------------------------------------------------------


def test_csv_carries_a_timestamp_column(tmp_path):
    log = RedPercentDataLog(["X"])
    log.add_entry(50.0, {"X": 1.0}, {"X": 0.5}, timestamp=1234.5)
    csv_path = tmp_path / "run.csv"
    log.save_to_csv(str(csv_path))

    text = csv_path.read_text()
    header = text.splitlines()[0].split(",")
    assert "Timestamp" in header

    result = parse_red_percent_csv(text)
    assert result["timestamps"] == [1234.5]
    assert result["red_percents"] == [50.0]


def test_an_invalid_position_survives_as_an_empty_cell_not_a_zero(tmp_path):
    """`plot_data.parse_red_percent_csv` already drops a row it cannot
    parse cleanly (`test_redpercent_22_...` and `test_plot_data.py` pin
    this for a malformed cell); an explicit `None` sentinel takes the same
    path rather than silently becoming a `0.0` sample. What matters here is
    that it is a *dropped row*, not a *corrupted* one — a valid row before
    and after it must still parse.
    """
    log = RedPercentDataLog(["X"])
    log.add_entry(10.0, {"X": 1.0}, {"X": 0.1}, timestamp=1.0)
    log.add_entry(20.0, {"X": None}, {"X": None}, timestamp=2.0)  # a failed read
    log.add_entry(30.0, {"X": 3.0}, {"X": 0.1}, timestamp=3.0)
    csv_path = tmp_path / "run.csv"
    log.save_to_csv(str(csv_path))

    raw = csv_path.read_text()
    assert "20.0,2.0,," in raw, (
        f"the invalid sample's location/velocity cells were not left "
        f"empty (written as a fabricated 0.0 instead?):\n{raw}")
    result = parse_red_percent_csv(raw)

    assert 20.0 not in result["red_percents"], (
        "the invalid sample's row must not silently reappear as if valid")
    assert result["red_percents"] == [10.0, 30.0]
    assert result["dim_data"]["X"] == [1.0, 3.0]


# ---------------------------------------------------------------------
# RC-11 item 3: pending_run_data / has_unsaved_data / teardown autosave
# ---------------------------------------------------------------------


def test_pending_run_data_and_has_unsaved_data_share_one_source():
    system = RedPercentSystem()
    assert system.pending_run_data() == {"has_data": False, "sample_count": 0,
                                         "run_id": None}
    assert system.has_unsaved_data is False

    system.data_log = RedPercentDataLog(["X"])
    system.data_log.add_entry(45.0, {"X": 1.0}, {"X": 0.0})

    pending = system.pending_run_data()
    assert pending["has_data"] is True
    assert pending["sample_count"] == 1
    assert system.has_unsaved_data is True


def test_teardown_autosaves_unsaved_data_d10(ready_system):
    """D-10: autosave to a timestamped file. `teardown()` is the path every
    frontend's close/release/FULL STOP eventually reaches; it must not
    silently drop a run's only copy of its data."""
    result = _running(ready_system)
    assert result.ok
    for _ in range(50):
        if ready_system.data_log.red_values:
            break
        time.sleep(0.01)
    assert ready_system.data_log.red_values

    run_dir = ready_system.run_dir()
    ready_system.teardown()

    produced = list(run_dir.iterdir()) if run_dir.exists() else []
    assert produced, "teardown() must autosave pending data, not discard it"
    assert any(p.name.endswith("_position.csv") for p in produced)


def test_confirm_discard_seam_exists_and_defaults_to_none():
    """RC-11 item 3's D-10 seam. `SystemManager.release`/`shutdown_all`
    already reach `teardown()` with no change needed in `system_manager.py`
    (outside this file's write set) — what this pins is that the model
    exposes a settable, callable seam, and that `teardown()` consults it."""
    system = RedPercentSystem()
    assert system.confirm_discard is None

    called = []
    system.confirm_discard = lambda: called.append(True) or True
    assert callable(system.confirm_discard)
    assert system.confirm_discard() is True
    assert called == [True]


def test_teardown_honors_an_explicit_discard(ready_system, tmp_path):
    """A view that prompted the operator and got "discard" must not have
    `teardown()` autosave anyway — that would silently keep data the
    operator explicitly declined to keep."""
    result = _running(ready_system)
    assert result.ok
    for _ in range(50):
        if ready_system.data_log.red_values:
            break
        time.sleep(0.01)
    assert ready_system.data_log.red_values

    ready_system.confirm_discard = lambda: True
    run_dir = ready_system.run_dir()
    ready_system.teardown()

    assert not run_dir.exists(), (
        "teardown() autosaved despite an explicit operator discard")


def test_teardown_autosaves_when_the_discard_hook_raises(ready_system, isolated_bus):
    """A prompt that failed to show is not consent to lose the run — the
    safe D-10 default (autosave) must still apply."""
    result = _running(ready_system)
    assert result.ok
    for _ in range(50):
        if ready_system.data_log.red_values:
            break
        time.sleep(0.01)
    assert ready_system.data_log.red_values

    def _broken_prompt():
        raise RuntimeError("no display")

    ready_system.confirm_discard = _broken_prompt
    run_dir = ready_system.run_dir()
    ready_system.teardown()

    assert run_dir.exists() and any(
        p.name.endswith("_position.csv") for p in run_dir.iterdir()), (
        "a raising confirm_discard must fall through to autosave, not "
        "silently drop the run")


# ---------------------------------------------------------------------
# Safety: emergency_stop never blocks (docs/architecture/safety-pattern.md)
# ---------------------------------------------------------------------


def test_emergency_stop_returns_immediately_even_if_the_loop_is_slow(ready_system):
    """`emergency_stop` must latch and return — never join, never wait on
    a screen grab. A monitor loop stuck in a slow `capture_focus_area` must
    not be able to hold the operator's stop button."""
    release_capture = threading.Event()

    def _slow_capture(sct):
        release_capture.wait(timeout=5.0)
        return object()

    ready_system.capture_focus_area = _slow_capture
    ready_system.detect_red = lambda image: 10.0
    with patch('model.redpercent_system.mss.mss'):
        result = ready_system.start_monitoring()
    assert result.ok

    # Give the thread a chance to actually enter the slow capture.
    time.sleep(0.05)

    started = time.monotonic()
    ready_system.emergency_stop()
    elapsed = time.monotonic() - started

    assert elapsed < 0.1, f"emergency_stop() blocked for {elapsed:.3f}s"
    assert not ready_system.monitoring

    release_capture.set()  # let the thread finish so teardown()'s join is fast
    ready_system._monitor_thread.join(timeout=2.0)
