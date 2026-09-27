"""`model.base` — the one latch, the bounded stop, the ordered close.

The four assertions of `docs/architecture/safety-pattern.md` are pinned here
for `Model.estop`, which every subsystem now inherits instead of
re-implementing:

1. it returns within ~100 ms against a hanging `_halt_hardware`;
2. the stop still reaches the hardware;
3. a command issued after the stop is refused by `_guard`;
4. only an explicit operator action clears the latch (RC-5).

Ported from `tests/core/test_manager21_stop_confirmation.py` (the shape no
existing test covered: a stop that returns successfully but unconfirmed),
`tests/core/test_manager22_clear_estop_is_reachable.py`, and the lifecycle
half of `tests/core/test_lifecycle_teardown.py`.
"""
import threading
import time

import pytest

import schema as sch
from events import events
from model.base import Model
from result import NeedsConfirm, Refused

from test_core_fakes import EventRecorder, FakeDevice, FakeModel

pytestmark = pytest.mark.estop


# -- 1. latch first, and it cannot fail -------------------------------------

def test_the_latch_is_set_before_the_hardware_is_touched():
    """"The latch is what actually orders a stop against work already in
    flight — the hardware write is not, because it can be queued behind a
    transaction.\""""
    model = FakeModel()
    model.estop()
    assert model.halt_latched_at_call == [True], (
        "the stop reached the hardware before the latch was set")


def test_the_latch_is_set_even_when_the_stop_never_confirms():
    """False means 'I could not confirm this', never 'I did not try'."""
    model = FakeModel(halt_blocks=True)
    try:
        assert model.estop() is False
        assert model.is_estopped is True
    finally:
        model.release()


def test_the_latch_is_set_even_when_the_stop_raises():
    model = FakeModel(halt_error=OSError("device not configured"))
    assert model.estop() is False
    assert model.is_estopped is True


# -- 2. never block the caller ---------------------------------------------

def test_estop_returns_inside_its_budget_against_a_hanging_halt():
    """I-5.2. The caller is frequently the UI thread; a wedged transport must
    not be able to hold the operator's stop button."""
    model = FakeModel(halt_blocks=True)
    try:
        started = time.monotonic()
        model.estop()
        elapsed = time.monotonic() - started
        assert elapsed < model.ESTOP_BUDGET + 0.1, (
            f"estop took {elapsed * 1000:.0f} ms against a hanging stop; "
            f"the budget is {model.ESTOP_BUDGET * 1000:.0f} ms")
    finally:
        model.release()


def test_the_stop_still_reaches_the_hardware_after_the_caller_gives_up():
    """Safety-pattern assertion 2: unconfirmed is not un-attempted."""
    model = FakeModel(halt_blocks=True)
    model.estop()
    assert model.halt_latched_at_call, "the worker never sent the stop"
    model.release()
    for _ in range(200):
        if model.close_order.count("halt"):
            break
        time.sleep(0.005)
    assert "halt" in model.close_order


def test_the_stop_thread_is_a_daemon_so_a_wedged_board_cannot_hold_the_exit():
    model = FakeModel(halt_blocks=True)
    try:
        before = {t.name for t in threading.enumerate()}
        model.estop()
        workers = [t for t in threading.enumerate()
                   if t.name not in before and t.name.startswith("estop-")]
        assert workers and all(t.daemon for t in workers)
    finally:
        model.release()


# -- the confirmation contract (MANAGER-21) ---------------------------------

def test_a_healthy_stop_reports_confirmed():
    assert FakeModel().estop() is True


def test_a_stop_still_in_flight_reports_unconfirmed():
    model = FakeModel(halt_blocks=True)
    try:
        assert model.estop() is False, (
            "estop returned True while its hardware write was still blocked; "
            "this is the value the Web client renders as 'FULL STOP "
            "confirmed for all devices'")
    finally:
        model.release()


def test_a_stop_whose_hardware_says_no_reports_unconfirmed():
    assert FakeModel(halt_result=False).estop() is False


def test_a_slow_but_in_time_stop_still_reports_confirmed():
    assert FakeModel(halt_delay=0.01).estop() is True


def test_a_raising_stop_is_a_warning_not_a_popup():
    with EventRecorder() as log:
        FakeModel(halt_error=OSError("bad")).estop()
    assert [e.severity for e in log.seen] == ["warning"]
    assert log.acknowledged == []


# -- 3. a command after the stop is refused by _guard -----------------------

def test_a_guarded_command_is_refused_while_latched():
    model = FakeModel()
    model.estop()
    result = model.run("move")
    assert result.is_refused
    # F19/F20: one vocabulary ("stopped", "Clear the stop") and a sentence
    # that says what to do; the command name goes to the file log.
    assert "Fake is stopped" in result.reason
    assert "Clear the stop" in result.reason
    assert model.moved == [], "a move landed after the FULL STOP"


def test_the_guard_names_the_model_so_an_operator_knows_which_one():
    model = FakeModel()
    model.estop()
    with pytest.raises(Refused, match="Fake"):
        model._guard("Move")


def test_the_guard_is_silent_while_clear():
    FakeModel()._guard("Move")          # must not raise


def test_a_move_already_queued_behind_the_stop_does_not_land():
    """Safety-pattern assertion 3, at the model level: the latch is checked
    when the command actually runs, not when it was requested."""
    model = FakeModel()
    started, release = threading.Event(), threading.Event()
    outcome = []

    def late_move():
        started.set()
        release.wait(5)
        outcome.append(model.run("move"))

    worker = threading.Thread(target=late_move, daemon=True)
    worker.start()
    started.wait(2)
    model.estop()
    release.set()
    worker.join(5)
    assert outcome and outcome[0].is_refused
    assert model.moved == []


# -- 4. only an operator action clears the latch (RC-5) ---------------------

def test_clear_estop_without_confirmation_asks_instead_of_clearing():
    model = FakeModel()
    model.estop()
    with pytest.raises(NeedsConfirm) as raised:
        model.clear_estop()
    # F20: one vocabulary ("Clear the stop"), and one sentence saying what
    # clearing does.
    assert raised.value.prompt.startswith("Clear the stop on the Fake?")
    assert "nothing restarts" in raised.value.prompt
    assert model.is_estopped, "the latch released on an unconfirmed request"


def test_clear_estop_with_confirmation_releases_the_latch_and_says_so():
    model = FakeModel()
    model.estop()
    with EventRecorder() as log:
        model.clear_estop(confirmed=True)
    assert model.is_estopped is False
    assert [e.title for e in log.seen] == ["Stop Cleared"]   # F20 vocabulary
    assert log.acknowledged == [], "clearing a latch is not a fault"


def test_clearing_the_latch_does_not_restart_anything():
    model = FakeModel()
    model.mode = "running"
    model.estop()
    model.clear_estop(confirmed=True)
    assert model.enable_calls == 0
    assert model.moved == []


def test_a_guarded_command_works_again_once_the_operator_clears():
    model = FakeModel()
    model.estop()
    model.clear_estop(confirmed=True)
    assert model.run("move").is_ok


def test_nothing_in_the_model_clears_the_latch_on_its_own():
    """RC-5: not a reconnect, not a mode change, not a retry."""
    model = FakeModel()
    model.estop()
    model.run("set_mode", args=("idle",))
    model.halt()
    model.open()
    model.enable()
    assert model.is_estopped, "something other than the operator cleared it"


# -- toggle_estop, which is what the schema actually wires ------------------

def test_toggle_estop_latches_when_clear():
    model = FakeModel()
    assert model.run("toggle_estop").is_ok
    assert model.is_estopped


def test_an_unconfirmed_stop_is_the_one_thing_that_earns_an_error_popup():
    model = FakeModel(halt_blocks=True)
    try:
        with EventRecorder() as log:
            model.run("toggle_estop")
        acked = log.acknowledged
        assert len(acked) == 1 and acked[0].title == "Stop Not Confirmed"
        assert "Treat it as live" in acked[0].message
    finally:
        model.release()


def test_a_confirmed_stop_raises_no_popup():
    model = FakeModel()
    with EventRecorder() as log:
        model.run("toggle_estop")
    assert log.acknowledged == []


def test_toggle_estop_while_latched_asks_before_clearing():
    model = FakeModel()
    model.estop()
    result = model.run("toggle_estop")
    assert result.needs_confirm
    assert model.is_estopped


def test_the_confirmed_clear_round_trip_actually_clears_the_latch():
    """MANAGER-22, rebuilt: a latched FULL STOP has to be clearable from the
    UI. `views/base.PanelView._run` re-runs `result.command` with
    `(*result.args, True)` — exactly what this does."""
    model = FakeModel()
    model.estop()
    ask = model.run("toggle_estop")
    assert ask.needs_confirm

    confirmed = model.run(ask.command, ask.inputs, (*ask.args, True))
    assert confirmed.is_ok, confirmed.reason
    assert model.is_estopped is False


def test_the_safety_toggle_is_reachable_through_the_allow_list():
    model = FakeModel()
    commands = {e.get("command") for e in sch.elements(model.schema)}
    assert "toggle_estop" in commands


def test_every_models_schema_ends_with_the_safety_section():
    sections = FakeModel().schema["sections"]
    assert sections[-1]["title"] == "Safety"
    types = [e["type"] for e in sections[-1]["elements"]]
    assert types == ["toggle", "indicator", "readonly", "internal"]


def test_the_safety_toggle_is_danger_in_both_states():
    toggle = FakeModel().schema["sections"][-1]["elements"][0]
    assert toggle["on_role"] == "danger" and toggle["off_role"] == "danger"


def test_the_safety_toggle_is_not_writable_from_the_web_set_attr_route():
    toggle = FakeModel().schema["sections"][-1]["elements"][0]
    assert toggle["writable"] is False


# -- halt: the same stop, without the latch ---------------------------------

def test_halt_stops_the_hardware_and_does_not_latch():
    model = FakeModel()
    assert model.halt() is True
    assert model.is_estopped is False


def test_halt_reports_a_hardware_refusal():
    assert FakeModel(halt_result=False).halt() is False


def test_the_base_models_halt_is_a_no_op_that_reports_success():
    class Bare(Model):
        NAME = "Bare"

        @property
        def schema(self):
            return sch.schema(self._safety_section())

    assert Bare().halt() is True


# -- lifecycle ---------------------------------------------------------------

def test_open_opens_every_device_then_starts_the_threads():
    first, second = FakeDevice("a"), FakeDevice("b")
    model = FakeModel(devices=[first, second])
    model.open()
    assert first.open_calls == second.open_calls == 1
    assert model.start_calls == 1
    assert model.close_order == ["_start_threads"], (
        "threads started before the devices were open")


def test_close_runs_stop_halt_disable_in_that_order():
    model = FakeModel()
    model.close()
    assert model.close_order == ["_stop_threads", "halt", "disable"]


def test_close_runs_every_step_when_an_earlier_one_raises():
    """"The hardware steps are never skipped because an earlier step
    raised."\""""
    model = FakeModel()
    model.stop_error = RuntimeError("thread would not join")
    with EventRecorder() as log:
        model.close()
    assert model.close_order == ["_stop_threads", "halt", "disable"]
    assert model.disable_calls == 1, "de-energize was skipped"
    assert [e.title for e in log.seen] == ["Close Step Failed"]
    assert log.acknowledged == [], "a close path must not raise a popup"


def test_a_raising_halt_does_not_stop_the_close():
    model = FakeModel(halt_error=OSError("port gone"))
    model.close()
    assert model.disable_calls == 1


def test_a_raising_disable_does_not_stop_the_devices_being_closed():
    device = FakeDevice("a")
    model = FakeModel(devices=[device])
    model.disable_error = RuntimeError("no")
    model.close()
    assert device.close_calls == 1


def test_close_closes_every_device_even_when_one_raises():
    first = FakeDevice("a", close_error=OSError("already gone"))
    second = FakeDevice("b")
    model = FakeModel(devices=[first, second])
    with EventRecorder() as log:
        model.close()
    assert first.close_calls == second.close_calls == 1, (
        "one device's close failure orphaned the next device's handle")
    assert [e.title for e in log.seen] == ["Device Close Failed"]


def test_close_never_raises():
    model = FakeModel(devices=[FakeDevice("a", close_error=OSError("x"))])
    model.stop_error = RuntimeError("x")
    model.disable_error = RuntimeError("x")
    model.halt_error = RuntimeError("x")
    model.close()


# -- fault -------------------------------------------------------------------

def test_a_fault_is_reported_once_however_often_the_loop_sees_it():
    """Nothing in a loop may publish per-iteration."""
    model = FakeModel()
    with EventRecorder() as log:
        for _ in range(100):
            model._fault("position read timed out")
    assert len(log.seen) == 1 and log.seen[0].needs_ack is True
    assert model.is_faulted and model.fault == "position read timed out"


def test_a_different_fault_is_reported_again():
    model = FakeModel()
    with EventRecorder() as log:
        model._fault("first")
        model._fault("second")
    assert len(log.seen) == 2


def test_the_same_fault_after_a_clear_is_published_again():
    """`_fault` re-publishes; whether the operator sees a *second* popup is
    then the EventLog's dedupe window's call, which is why this asserts the
    repeat count rather than the number of delivered events."""
    model = FakeModel()
    with EventRecorder() as log:
        model._fault("position read timed out")
        model._clear_fault()
        model._fault("position read timed out")
    assert len(log.seen) == 1 and log.seen[0].count == 2
    assert model.is_faulted


def test_a_cleared_fault_leaves_no_reason_behind():
    model = FakeModel()
    model._fault("x")
    model._clear_fault()
    assert model.fault == "" and model.is_faulted is False


# -- state -------------------------------------------------------------------

def test_state_carries_the_safety_flags_a_view_needs():
    model = FakeModel(devices=[FakeDevice("a")])
    model.open()
    model.estop()
    model._fault("coil open")
    snapshot = model.state
    assert snapshot["is_estopped"] is True
    assert snapshot["is_faulted"] is True and snapshot["fault"] == "coil open"
    assert snapshot["is_active"] is False
    assert snapshot["devices"] == {"FakeDevice": "verified"}


def test_state_ages_so_a_view_can_show_staleness():
    model = FakeModel()
    assert model.state["age"] < 1.0
    model._updated_at -= 5
    assert model.state["age"] >= 5.0
    model._touch()
    assert model.state["age"] < 1.0


def test_a_closed_device_reports_closed_not_missing():
    model = FakeModel(devices=[FakeDevice("a")])
    assert model.state["devices"] == {"FakeDevice": "closed"}


def test_is_active_reflects_the_model_not_the_latch():
    model = FakeModel()
    model.mode = "running"
    assert model.state["is_active"] is True


def test_the_model_declares_what_setup_needs_to_build_it():
    assert FakeModel.IDENTITY == "F"
    assert FakeModel.NEEDS_PORT is False and FakeModel.NEEDS_GAMEPAD is False
    assert Model.NEEDS_PORT is False and Model.NEEDS_GAMEPAD is False


def test_the_base_model_owns_no_devices():
    class Bare(Model):
        NAME = "Bare"

        @property
        def schema(self):
            return sch.schema(self._safety_section())

    assert Bare().devices == []


def test_a_fault_with_no_reason_still_says_something():
    """F19: an empty-reason fault came up blank in every view."""
    model = FakeModel()
    with EventRecorder() as log:
        model._fault("")
    assert model.is_faulted
    assert model.fault.startswith("Fake reported a fault without a reason.")
    errors = [e for e in log.seen if e.severity == "error"]
    assert errors and errors[0].message == model.fault


def test_a_model_remembers_whether_its_last_stop_confirmed():
    """Round 7: the views mark an unconfirmed stop from state, not from the
    event log. None while clear; True/False after a stop; None again once
    cleared."""
    model = FakeModel()
    assert model.stop_confirmed is None
    assert model.estop() is True
    assert model.stop_confirmed is True and model.state["stop_confirmed"] is True
    model.clear_estop(confirmed=True)
    assert model.stop_confirmed is None
    stalled = FakeModel(halt_blocks=True)
    try:
        assert stalled.estop() is False
        assert stalled.stop_confirmed is False
    finally:
        stalled.release()


def test_an_unconfirmed_stop_after_a_clear_is_a_new_event_not_a_count():
    """Round 7: toggle_estop on a stalled model, clear, toggle again inside
    the dedupe window: two "Stop Not Confirmed" events, two notifications."""
    from events import events
    from test_core_fakes import EventRecorder
    stalled = FakeModel(halt_blocks=True)
    try:
        with EventRecorder() as log:
            stalled.toggle_estop()
            stalled.clear_estop(confirmed=True)
            stalled.toggle_estop()
        ids = [e.id for e in log.titled("Stop Not Confirmed")]
        assert len(ids) == 2 and ids[0] != ids[1]
    finally:
        stalled.release()


def test_a_faulted_model_gates_as_fault_from_the_base():
    """CON-9: any model's fault greys what lists "fault", without the model
    mapping it through its own mode_name."""
    model = FakeModel()
    assert model.gate_mode != "fault"
    model._fault("coil open") if hasattr(model, "_fault") else setattr(model, "_fault_reason", "coil open")
    if model.is_faulted:
        assert model.gate_mode == "fault"
    model.estop()
    assert model.gate_mode == "latched", "the latch outranks the fault"


# -- MOD-2: one loop helper ---------------------------------------------------
#
# `Model._spawn(name, target)` starts a daemon thread and records it;
# `Model._threads_stop` is the one stop flag; the base `_stop_threads` sets it
# and joins every spawned thread within `THREAD_JOIN_TIMEOUT`. No subclass
# builds its own stop Event or its own join any more.

class _Looping(Model):
    NAME = "Looping"

    def __init__(self):
        super().__init__()
        self.ticks = 0

    @property
    def schema(self):
        return sch.schema(self._safety_section())

    def _start_threads(self):
        self._spawn("tick", self._loop)

    def _loop(self):
        while not self._threads_stop.wait(0.005):
            self.ticks += 1


def test_spawn_starts_a_named_daemon_thread_and_records_it():
    model = _Looping()
    model.open()
    try:
        thread = model._thread("tick")
        assert thread is not None and thread.is_alive() and thread.daemon
        assert "tick" in thread.name and model.NAME in thread.name
    finally:
        model.close()


def test_the_base_stop_threads_sets_the_flag_and_joins_every_spawned_thread():
    model = _Looping()
    model.open()
    deadline = time.monotonic() + 1.0
    while model.ticks == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    thread = model._thread("tick")
    model.close()
    assert model._threads_stop.is_set()
    assert not thread.is_alive(), "close() returned with the loop still running"


def test_spawning_again_after_a_stop_restarts_the_loop():
    model = _Looping()
    model._start_threads()
    model._stop_threads()
    first = model._thread("tick")
    model._start_threads()
    try:
        second = model._thread("tick")
        assert second is not first and second.is_alive()
        assert not model._threads_stop.is_set()
    finally:
        model._stop_threads()


def test_a_live_loop_is_not_spawned_twice():
    model = _Looping()
    model._start_threads()
    try:
        first = model._thread("tick")
        model._start_threads()
        assert model._thread("tick") is first
        assert sum(t.is_alive() for t in model._spawned_threads()) == 1
    finally:
        model._stop_threads()


def test_the_join_is_bounded_by_one_timeout_and_a_stuck_loop_is_reported(monkeypatch):
    release = threading.Event()

    class Stuck(_Looping):
        def _loop(self):
            release.wait(5.0)          # ignores the stop flag

    model = Stuck()
    monkeypatch.setattr(Stuck, "THREAD_JOIN_TIMEOUT", 0.05)
    model._start_threads()
    try:
        with EventRecorder() as log:
            started = time.monotonic()
            model._stop_threads()
            elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"the join waited {elapsed:.2f} s"
        assert log.titled("Thread Still Running"), "a stuck loop went unreported"
    finally:
        release.set()


def test_a_loop_with_its_own_stop_event_is_stopped_by_the_base_too():
    """A per-arming Event (the idle interlock's) is set by `_stop_threads` as
    well, so a loop parked on it leaves at close instead of outliving the
    join by a whole poll interval."""
    model = _Looping()
    own = threading.Event()

    def _park():
        own.wait(30.0)

    model._spawn("parked", _park, stop=own)
    model._stop_threads()
    assert own.is_set()
    assert not model._thread("parked").is_alive()


def test_the_join_timeout_is_one_constant_on_the_base():
    assert Model.THREAD_JOIN_TIMEOUT == 2.0
