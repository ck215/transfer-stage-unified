"""`controller.controller` — construct/destruct on demand, and the global stop.

The old `SystemManager` tests land here: `tests/core/test_system_manager.py`
(register/unregister, `full_stop_all`, `shutdown_all`),
`tests/core/test_manager21_stop_confirmation.py` (a real stall, not a mock
that hand-returns False), `tests/core/test_manager25_shutdown_stops_all_first.py`
(every model is stopped before any is closed), and
`tests/core/test_lifecycle_exit.py`.
"""
import threading
import time

import pytest

from controller.controller import Controller
from result import Result

from test_core_fakes import EventRecorder, FakeDevice, FakeModel

pytestmark = pytest.mark.lifecycle


@pytest.fixture
def controller():
    made = Controller()
    yield made
    for model in list(made._models.values()):
        model.release()
    made.close()


# -- add ---------------------------------------------------------------------

def test_add_registers_and_opens(controller):
    model = FakeModel(devices=[FakeDevice("a")])
    assert controller.add("probe", model, {"port": "SIM"}) is model
    assert controller.model_names == ["probe"]
    assert model.start_calls == 1 and model.devices[0].is_open


def test_add_remembers_the_config_so_reopen_can_rebuild(controller):
    controller.add("probe", FakeModel(), {"port": "COM3"})
    assert controller.config("probe") == {"port": "COM3"}


def test_the_config_is_copied_not_aliased(controller):
    config = {"port": "COM3"}
    controller.add("probe", FakeModel(), config)
    config["port"] = "COM9"
    assert controller.config("probe") == {"port": "COM3"}


def test_adding_a_name_twice_is_refused_before_anything_opens(controller):
    first = FakeModel()
    controller.add("probe", first)
    second = FakeModel()
    with pytest.raises(ValueError, match="already open"):
        controller.add("probe", second)
    assert second.start_calls == 0, "the duplicate opened a second handle"


class _SlowOpen(FakeModel):
    """A model whose open takes a while (a port handshake does)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.opening = threading.Event()
        self.open_gate = threading.Event()

    def _start_threads(self):
        self.opening.set()
        self.open_gate.wait(5)
        super()._start_threads()


def _add_in_thread(controller, name, model):
    errors = []

    def _add():
        try:
            controller.add(name, model)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=_add, daemon=True)
    thread.start()
    return thread, errors


def test_a_second_add_of_a_name_that_is_still_opening_is_refused(controller):
    """Audit 2026-10-08 item 1: `add` checked the name, opened with the lock
    released, then inserted. Two concurrent adds (the Web server runs Setup
    commands concurrently) both opened, the second replaced the first in the
    table, and the first kept its port and threads where no FULL STOP
    reached them."""
    first = _SlowOpen()
    thread, errors = _add_in_thread(controller, "probe", first)
    try:
        assert first.opening.wait(2)
        second = FakeModel()
        with pytest.raises(ValueError):
            controller.add("probe", second)
        assert second.start_calls == 0, "the duplicate opened a second handle"
    finally:
        first.open_gate.set()
        thread.join(5)
    assert errors == []
    assert controller.models == {"probe": first}


def test_a_full_stop_during_an_open_latches_the_model_before_it_is_published(
        controller):
    """FULL STOP never waits behind an opening port, and a model whose open
    was under way when it was pressed is stopped before anyone can use it."""
    model = _SlowOpen()
    thread, errors = _add_in_thread(controller, "probe", model)
    try:
        assert model.opening.wait(2)
        started = time.monotonic()
        controller.estop_all()
        assert time.monotonic() - started < controller.ESTOP_ALL_BUDGET + 0.5, \
            "FULL STOP waited behind an opening port"
    finally:
        model.open_gate.set()
        thread.join(5)
    assert errors == []
    assert controller.model_names == ["probe"]
    assert model.is_estopped, "a model opened across a FULL STOP came up unlatched"
    assert "halt" in model.close_order


def test_a_failed_open_frees_the_name(controller):
    model = FakeModel()
    model.start_error = OSError("handshake timed out")
    with pytest.raises(OSError):
        controller.add("probe", model)
    again = FakeModel()
    controller.add("probe", again)
    assert controller.models == {"probe": again}


def test_a_model_that_fails_to_open_is_closed_and_not_kept(controller):
    """One port must never be left half-open and unowned."""
    device = FakeDevice("a")
    model = FakeModel(devices=[device])
    model.start_error = OSError("handshake timed out")
    with pytest.raises(OSError):
        controller.add("probe", model)
    assert controller.model_names == []
    assert device.close_calls == 1, "the device stayed open with no owner"
    assert model.stop_calls == 1 and model.disable_calls == 1


def test_a_model_whose_device_fails_to_open_is_still_closed(controller):
    good = FakeDevice("a")
    bad = FakeDevice("b", open_error=OSError("no such port"))
    model = FakeModel(devices=[good, bad])
    with pytest.raises(OSError):
        controller.add("probe", model)
    assert good.close_calls == 1, "the device that did open was orphaned"


def test_a_failed_add_leaves_nothing_behind_for_reopen(controller):
    model = FakeModel()
    model.start_error = OSError("x")
    with pytest.raises(OSError):
        controller.add("probe", model)
    assert controller.closed_names == []


# -- peer fan-out ------------------------------------------------------------

def test_every_existing_model_hears_about_a_new_one(controller):
    first, second = FakeModel(), FakeModel()
    controller.add("one", first)
    controller.add("two", second)
    assert first.added_seen == ["two"]


def test_a_new_model_hears_about_every_existing_one(controller):
    first, second, third = FakeModel(), FakeModel(), FakeModel()
    controller.add("one", first)
    controller.add("two", second)
    controller.add("three", third)
    assert sorted(third.added_seen) == ["one", "two"]
    assert first.added_seen == ["two", "three"]


def test_a_model_is_not_told_about_itself(controller):
    model = FakeModel()
    controller.add("one", model)
    assert model.added_seen == []


def test_every_surviving_model_hears_about_a_removal(controller):
    first, second = FakeModel(), FakeModel()
    controller.add("one", first)
    controller.add("two", second)
    controller.remove("two")
    assert first.removed_seen == ["two"]


# -- remove / reopen / reset -------------------------------------------------

def test_remove_stops_the_model_before_it_closes_it(controller):
    """MANAGER-25: a model that is closed before it is stopped can be left
    energized by a close step that raises."""
    model = FakeModel()
    controller.add("probe", model)
    assert controller.remove("probe") is True
    assert model.is_estopped, "remove closed a model it had not latched"
    assert model.close_order.index("halt") < model.close_order.index("disable")
    assert model.stop_calls == 1


def test_a_full_stop_during_a_slow_remove_still_reaches_the_model(controller):
    """Audit 2026-10-08 item 9: `remove` dropped the model from the table
    before it stopped and closed it, so a FULL STOP pressed during the
    heater's 0.6-4.5 s close did not include it."""
    model = _SlowDisable()
    controller.add("heater", model)
    remover = threading.Thread(target=controller.remove, args=("heater",))
    remover.start()
    try:
        assert model.entered.wait(2), "remove never reached the model's close"
        halts_before = model.close_order.count("halt")
        results = controller.estop_all()
        assert "heater" in results, "FULL STOP missed a model that was still closing"
        assert model.close_order.count("halt") > halts_before
    finally:
        model.gate.set()
        remover.join(5)
    assert controller.model_names == []
    assert "heater" in controller.closed_names


def test_a_closing_model_takes_no_new_commands_and_its_name_is_not_reused(controller):
    model = _SlowDisable()
    controller.add("heater", model)
    remover = threading.Thread(target=controller.remove, args=("heater",))
    remover.start()
    try:
        assert model.entered.wait(2)
        assert controller.run("heater", "move").is_refused
        second = FakeModel()
        with pytest.raises(ValueError):
            controller.add("heater", second)
        assert second.start_calls == 0, "a second handle opened on a closing port"
    finally:
        model.gate.set()
        remover.join(5)


def test_views_hear_removed_only_once_the_model_is_closed(controller):
    model = _SlowDisable()
    controller.add("heater", model)
    heard = []
    controller.subscribe(lambda event, name: heard.append(
        (event, name, model.closed.is_set())))
    model.gate.set()
    controller.remove("heater")
    assert heard == [("removed", "heater", True)]


def test_remove_drops_the_model_but_remembers_its_config(controller):
    controller.add("probe", FakeModel(), {"port": "COM3"})
    controller.remove("probe")
    assert controller.model_names == []
    assert controller.closed_names == ["probe"]
    assert controller.config("probe") == {"port": "COM3"}


def test_removing_something_that_is_not_open_reports_false(controller):
    assert controller.remove("nope") is False


def test_reopen_rebuilds_from_the_factory_and_the_remembered_config(controller):
    built = []

    def factory(config):
        built.append(config)
        return FakeModel()

    controller.factory = factory
    controller.add("probe", FakeModel(), {"port": "COM3"})
    controller.remove("probe")
    model = controller.reopen("probe")
    assert built == [{"port": "COM3"}]
    assert controller.model_names == ["probe"] and model.start_calls == 1
    assert controller.closed_names == [], "reopen left a ghost in the closed list"


def test_reopen_of_something_never_configured_is_an_error(controller):
    controller.factory = lambda config: FakeModel()
    with pytest.raises(ValueError, match="never configured"):
        controller.reopen("probe")


def test_reopen_without_a_factory_is_an_error_not_a_silent_no_op(controller):
    controller.add("probe", FakeModel(), {})
    controller.remove("probe")
    with pytest.raises(ValueError):
        controller.reopen("probe")


def test_reset_destructs_everything_and_forgets_the_configs(controller):
    """Setup calls this before building again, so one port never has two
    handles."""
    device = FakeDevice("a")
    model = FakeModel(devices=[device])
    controller.add("probe", model, {"port": "COM3"})
    controller.reset()
    assert controller.model_names == [] and controller.closed_names == []
    assert device.close_calls == 1 and model.is_estopped


def test_reset_leaves_the_controller_usable(controller):
    controller.add("probe", FakeModel())
    controller.reset()
    controller.add("probe", FakeModel())
    assert controller.model_names == ["probe"]


# -- close -------------------------------------------------------------------

def test_close_stops_and_closes_every_model(controller):
    first, second = FakeModel(), FakeModel()
    controller.add("one", first)
    controller.add("two", second)
    controller.close()
    assert first.is_estopped and second.is_estopped
    assert first.disable_calls == second.disable_calls == 1
    assert controller.model_names == []


def test_close_stops_every_model_before_it_closes_any(controller):
    """MANAGER-25, the ordering that matters when one close hangs."""
    first, second = FakeModel(), FakeModel()
    controller.add("one", first)
    controller.add("two", second)
    controller.close()
    assert first.halt_latched_at_call and second.halt_latched_at_call
    assert first.halt_latched_at_call[0] is True


def test_close_is_idempotent(controller):
    model = FakeModel()
    controller.add("probe", model)
    controller.close()
    controller.close()
    assert model.disable_calls == 1, "the second close ran the teardown again"


def test_close_never_requests_an_acknowledged_popup(controller):
    """A modal raised from inside the exit path blocks the exit — it hung the
    Qt suite for three sessions."""
    model = FakeModel(halt_blocks=True)
    controller.add("probe", model)
    try:
        with EventRecorder() as log:
            controller.close()
        assert log.acknowledged == []
        assert [e.title for e in log.seen] == ["Stop Not Confirmed"]
        assert "Treat them as live" in log.seen[0].message
    finally:
        model.release()


def test_a_model_whose_close_raises_does_not_block_the_others(controller):
    first = FakeModel()
    first.disable_error = RuntimeError("wedged")
    second = FakeModel()
    controller.add("one", first)
    controller.add("two", second)
    controller.close()
    assert second.disable_calls == 1


def test_closing_an_empty_controller_publishes_nothing(controller):
    with EventRecorder() as log:
        controller.close()
    assert log.seen == []


def test_close_tells_the_views_each_panel_is_gone(controller):
    seen = []
    controller.add("one", FakeModel())
    controller.subscribe(lambda change, name: seen.append((change, name)))
    controller.close()
    assert ("removed", "one") in seen


# -- what views call ---------------------------------------------------------

def test_run_is_the_only_way_a_command_reaches_a_model(controller):
    model = FakeModel()
    controller.add("probe", model)
    assert controller.run("probe", "move").is_ok
    assert model.moved == [(100.0, 4)]


def test_running_against_a_model_that_is_not_open_is_refused_not_raised(controller):
    result = controller.run("ghost", "move")
    assert isinstance(result, Result) and result.is_refused
    assert "ghost is not open" in result.reason


def test_schema_and_state_come_straight_from_the_model(controller):
    model = FakeModel()
    controller.add("probe", model)
    assert controller.schema("probe")["version"] == 2
    assert controller.state("probe")["name"] == "Fake"


@pytest.mark.parametrize("call", ["schema", "state"])
def test_reading_a_model_that_just_closed_does_not_raise_on_the_ui_thread(
        controller, call):
    """`PanelView._refresh` has no way to catch this: it is scheduled by the
    toolkit, not by the caller who removed the model."""
    controller.add("probe", FakeModel())
    controller.remove("probe")
    getattr(controller, call)("probe")


def test_listing_options_of_a_model_that_just_closed_does_not_raise(controller):
    controller.add("probe", FakeModel())
    controller.remove("probe")
    controller.options("probe", "port_options")


def test_run_already_guards_that_race(controller):
    """The shape the other three should match."""
    controller.add("probe", FakeModel())
    controller.remove("probe")
    assert controller.run("probe", "move").is_refused


def test_the_whole_station_state_is_one_snapshot(controller):
    controller.add("one", FakeModel())
    controller.add("two", FakeModel())
    controller.remove("two")
    snapshot = controller.state()
    assert set(snapshot["models"]) == {"one"}
    assert snapshot["closed"] == ["two"]
    assert snapshot["is_estopped"] is False and snapshot["is_active"] is False
    assert isinstance(snapshot["latest_event"], int)


def test_the_station_is_estopped_when_any_one_model_is(controller):
    first, second = FakeModel(), FakeModel()
    controller.add("one", first)
    controller.add("two", second)
    assert controller.is_estopped is False
    second.estop()
    assert controller.is_estopped is True


def test_the_station_is_active_when_any_one_model_is(controller):
    model = FakeModel()
    controller.add("one", model)
    model.mode = "running"
    assert controller.is_active is True


def test_options_and_set_value_route_to_the_named_model(controller):
    model = FakeModel()
    controller.add("probe", model)
    assert controller.options("probe", "port_options") == ["COM1", "COM2"]
    assert controller.set_value("probe", "speed", "33.5").is_ok
    assert model.speed == 33.5


def test_window_focus_gates_every_gamepad_and_never_stops_anything(controller):
    """D-4: gate input, never stop."""
    class Pad:
        def __init__(self):
            self.gates = []

        def set_gate(self, is_open):
            self.gates.append(is_open)

    model = FakeModel()
    model.gamepad = Pad()
    padless = FakeModel()
    controller.add("probe", model)
    controller.add("heater", padless)
    controller.set_input_focus(False)
    controller.set_input_focus(True)
    assert model.gamepad.gates == [False, True]
    assert model.is_estopped is False


# -- serialisation and the stop lane -----------------------------------------

def test_two_commands_on_one_model_do_not_interleave(controller):
    model = FakeModel()
    controller.add("probe", model)

    threads = [threading.Thread(
        target=lambda: controller.run("probe", "slow_move", args=(0.05,)))
        for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert model.moved == ["enter", "exit", "enter", "exit"], (
        f"a second view interleaved a command on one device: {model.moved}")


def test_a_stop_never_queues_behind_an_ordinary_command(controller):
    """The per-model lock serialises commands; the stop must bypass it, or a
    long move holds the operator's stop button."""
    model = FakeModel()
    controller.add("probe", model)
    running = threading.Event()

    def long_move():
        running.set()
        controller.run("probe", "slow_move", args=(0.6,))

    worker = threading.Thread(target=long_move, daemon=True)
    worker.start()
    running.wait(2)
    time.sleep(0.05)

    started = time.monotonic()
    result = controller.run("probe", "toggle_estop")
    elapsed = time.monotonic() - started

    assert result.is_ok, result.reason
    assert model.is_estopped
    assert elapsed < 0.3, (
        f"the stop waited {elapsed:.2f}s behind an ordinary command")
    worker.join(5)


def test_two_models_run_commands_in_parallel(controller):
    controller.add("one", FakeModel())
    controller.add("two", FakeModel())
    started = time.monotonic()
    threads = [threading.Thread(
        target=lambda n=n: controller.run(n, "slow_move", args=(0.15,)))
        for n in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert time.monotonic() - started < 0.28, "the lock is global, not per model"


# -- estop_all ---------------------------------------------------------------

def test_estop_all_latches_every_model_and_reports_each(controller):
    controller.add("one", FakeModel())
    controller.add("two", FakeModel())
    results = controller.estop_all()
    assert results == {"one": True, "two": True}
    assert all(m.is_estopped for m in controller._models.values())


def test_estop_all_reports_a_real_unconfirmed_device(controller):
    """MANAGER-21 end to end, with a real stall rather than a mock that
    hand-returns False — which is why this survived every prior wave."""
    healthy, stalled = FakeModel(), FakeModel(halt_blocks=True)
    controller.add("healthy", healthy)
    controller.add("stalled", stalled)
    try:
        results = controller.estop_all()
        assert results["stalled"] is False
        assert results["healthy"] is True, "the signal is useless without this"
        assert stalled.is_estopped, "False must mean unconfirmed, not untried"
    finally:
        stalled.release()


def test_an_unconfirmed_device_is_reported_to_every_frontend(controller):
    """MANAGER-23: the information reaches all three views through the event
    log, not by return value."""
    stalled = FakeModel(halt_blocks=True)
    controller.add("stalled", stalled)
    try:
        with EventRecorder() as log:
            controller.estop_all()
        titles = [e.title for e in log.seen]
        assert "Stop Not Confirmed" in titles
        assert "stalled" in log.titled("Stop Not Confirmed")[0].message
        assert log.acknowledged, "an unconfirmed stop is the one popup case"
    finally:
        stalled.release()


def test_a_confirmed_stop_of_everything_logs_but_never_pops_up(controller):
    controller.add("one", FakeModel())
    with EventRecorder() as log:
        controller.estop_all()
    # The log line is the operator's confirmation (the tray shows it); a
    # modal on every successful FULL STOP is one operators learn to dismiss
    # without reading, so nothing here may ask for an acknowledgement.
    assert [e.severity for e in log.seen] == ["info"]
    assert "one" in log.seen[0].message
    assert not any(e.needs_ack for e in log.seen)


def test_estop_all_is_bounded_even_with_a_hanging_model(controller):
    stalled = FakeModel(halt_blocks=True)
    controller.add("one", FakeModel())
    controller.add("stalled", stalled)
    try:
        started = time.monotonic()
        controller.estop_all()
        elapsed = time.monotonic() - started
        assert elapsed < controller.ESTOP_ALL_BUDGET + 0.3, (
            f"estop_all took {elapsed:.2f}s; the budget is "
            f"{controller.ESTOP_ALL_BUDGET}s")
    finally:
        stalled.release()


def test_estop_all_runs_the_models_concurrently_not_one_after_another(controller):
    """Five models each taking the full per-model budget must still land
    inside the total budget."""
    stalled = []
    for n in range(5):
        model = FakeModel(halt_blocks=True)
        stalled.append(model)
        controller.add(f"m{n}", model)
    try:
        started = time.monotonic()
        results = controller.estop_all()
        elapsed = time.monotonic() - started
        assert set(results.values()) == {False}
        assert all(m.is_estopped for m in stalled)
        assert elapsed < 0.6, (
            f"five stalled models took {elapsed:.2f}s: the stops are serial, "
            f"not concurrent")
    finally:
        for model in stalled:
            model.release()


def test_a_model_whose_estop_raises_is_reported_unconfirmed(controller):
    class Angry(FakeModel):
        def estop(self):
            raise RuntimeError("wedged")

    controller.add("angry", Angry())
    with EventRecorder() as log:
        results = controller.estop_all()
    assert results == {"angry": False}
    assert "Stop Raised" in [e.title for e in log.seen]


def test_estop_all_of_an_empty_station_is_an_empty_map(controller):
    assert controller.estop_all() == {}


# -- clear_estop_all ---------------------------------------------------------

def test_clearing_nothing_is_ok_and_asks_no_question(controller):
    controller.add("one", FakeModel())
    assert controller.clear_estop_all().is_ok


def test_clearing_asks_first_and_names_the_latched_models(controller):
    first, second = FakeModel(), FakeModel()
    controller.add("one", first)
    controller.add("two", second)
    controller.estop_all()
    result = controller.clear_estop_all()
    assert result.needs_confirm
    assert "one" in result.reason and "two" in result.reason
    assert result.command == "clear_estop_all"
    assert first.is_estopped and second.is_estopped


def test_a_confirmed_clear_releases_every_latch(controller):
    first, second = FakeModel(), FakeModel()
    controller.add("one", first)
    controller.add("two", second)
    controller.estop_all()
    assert controller.clear_estop_all(confirmed=True).is_ok
    assert not first.is_estopped and not second.is_estopped


def test_a_clear_does_not_touch_a_model_that_was_not_latched(controller):
    first, second = FakeModel(), FakeModel()
    controller.add("one", first)
    controller.add("two", second)
    first.estop()
    controller.clear_estop_all(confirmed=True)
    assert not first.is_estopped and not second.is_estopped


# -- subscribers -------------------------------------------------------------

def test_subscribers_hear_about_added_and_removed(controller):
    seen = []
    controller.subscribe(lambda change, name: seen.append((change, name)))
    controller.add("one", FakeModel())
    controller.remove("one")
    assert seen == [("added", "one"), ("removed", "one")]


def test_subscribe_is_idempotent(controller):
    seen = []

    def on_change(change, name):
        seen.append(name)

    controller.subscribe(on_change)
    controller.subscribe(on_change)
    controller.add("one", FakeModel())
    assert seen == ["one"]


def test_unsubscribe_stops_delivery(controller):
    seen = []

    def on_change(change, name):
        seen.append(name)

    controller.subscribe(on_change)
    controller.unsubscribe(on_change)
    controller.add("one", FakeModel())
    assert seen == []


def test_a_broken_subscriber_cannot_break_add(controller):
    seen = []

    def angry(change, name):
        raise RuntimeError("view exploded")

    controller.subscribe(angry)
    controller.subscribe(lambda change, name: seen.append(name))
    with EventRecorder() as log:
        controller.add("one", FakeModel())
    assert controller.model_names == ["one"]
    assert seen == ["one"]
    assert "Subscriber Failed" in [e.title for e in log.seen]
    assert log.acknowledged == []


# -- exit hooks --------------------------------------------------------------

def test_hooking_exit_is_idempotent(controller):
    controller._hook_exit()
    controller._hook_exit()
    import atexit
    atexit.unregister(controller.close)


def test_the_sigterm_handler_stops_and_closes_then_re_raises(controller, monkeypatch):
    """The handler `hook_signals` installs, called in-process (gate G1).

    Port of legacy `test_lifecycle_exit.py::test_sigterm_tears_down_before_the_process_dies`.
    `signal.signal` and `signal.raise_signal` are stubbed so no real handler
    is installed and the re-raise does not take the pytest session down; the
    re-raise being recorded is the evidence the signal is not swallowed.
    """
    import signal

    device = FakeDevice("port")
    model = FakeModel(devices=[device])
    controller.add("probe", model, {"port": "SIM"})

    installs = []
    reraised = []
    monkeypatch.setattr(signal, "signal", lambda sig, handler: installs.append((sig, handler)))
    monkeypatch.setattr(signal, "raise_signal", lambda sig: reraised.append(sig))

    controller.hook_signals()
    handlers = {sig: handler for sig, handler in installs}
    assert signal.SIGTERM in handlers and callable(handlers[signal.SIGTERM])
    handler = handlers[signal.SIGTERM]
    installs.clear()

    handler(signal.SIGTERM, None)

    assert model.is_estopped, "the handler must stop the hardware"
    assert model.disable_calls == 1 and not device.is_open, "the handler must close the model"
    assert controller.model_names == []
    assert installs == [(signal.SIGTERM, signal.SIG_DFL)], "the default action is restored"
    assert reraised == [signal.SIGTERM], "the signal must be re-raised, not swallowed"


class _SlowDisable(FakeModel):
    """A model whose teardown takes a while (the heater's read-back does)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.closed = threading.Event()
        self.on_disable = None

    def disable(self):
        self.entered.set()
        if self.on_disable is not None:
            self.on_disable()
        self.gate.wait(5)
        super().disable()
        self.closed.set()


def test_a_second_close_waits_for_the_first_to_finish(controller):
    """Quit is closing on one thread when SIGTERM (or atexit) calls close on
    another. Before 2026-10-07 the second call returned at once, the handler
    re-raised and the process died with the heater's teardown half done."""
    model = _SlowDisable()
    controller.add("heater", model)
    first = threading.Thread(target=controller.close)
    first.start()
    assert model.entered.wait(2)
    second_done = threading.Event()

    def _second():
        controller.close()
        second_done.set()

    threading.Thread(target=_second, daemon=True).start()
    time.sleep(0.2)
    assert not second_done.is_set(), "the second close returned before the models closed"
    model.gate.set()
    assert second_done.wait(5)
    assert model.closed.is_set()
    first.join(5)


def test_a_signal_landing_inside_this_threads_own_close_waits_for_it(
        controller, monkeypatch):
    """The handler runs on the main thread, which may be the one already
    closing (Quit, then the terminal is closed: SIGHUP). It must not re-raise
    (which kills the process) until that close has finished."""
    import signal

    model = _SlowDisable()
    model.gate.set()
    controller.add("heater", model)
    installs, reraised = [], []
    monkeypatch.setattr(signal, "signal", lambda sig, handler: installs.append((sig, handler)))
    monkeypatch.setattr(signal, "raise_signal",
                        lambda sig: reraised.append((sig, model.closed.is_set())))
    controller.hook_signals()
    handler = dict(installs)[signal.SIGHUP]
    model.on_disable = lambda: handler(signal.SIGHUP, None)
    controller.close()
    assert model.closed.is_set()
    assert reraised == [(signal.SIGHUP, True)], reraised


# -- the station's stop state (audit round 7, IMP7-1/2, TK7-1) --------------
#
# `is_estopped` is "any model latched" and stays so (the watchdog and the
# close path use it). What a view SAYS about the stop needs three facts:
# which models are latched, which did not confirm, and whether that is every
# model. One model's own switch must never read as "every model is stopped".

def test_stop_state_is_empty_while_nothing_is_latched(controller):
    controller.add("one", FakeModel())
    assert controller.stop_state == {"latched": [], "unconfirmed": [], "every": False,
                                     "since": None}


def test_one_models_own_stop_is_a_partial_stop_not_every(controller):
    first, second = FakeModel(), FakeModel()
    controller.add("one", first)
    controller.add("two", second)
    controller.run("two", "toggle_estop")
    state = controller.stop_state
    assert state["latched"] == ["two"] and state["every"] is False
    assert state["unconfirmed"] == []
    assert controller.state()["stop"] == state


def test_the_stop_state_lists_latched_models_in_station_order(controller):
    for name in ("zeta", "alpha", "mid"):
        controller.add(name, FakeModel())
    controller.estop_all()
    assert controller.stop_state["latched"] == ["zeta", "alpha", "mid"]
    assert controller.stop_state["every"] is True


def test_the_stop_state_names_the_model_that_did_not_confirm(controller):
    healthy, stalled = FakeModel(), FakeModel(halt_blocks=True)
    controller.add("healthy", healthy)
    controller.add("stalled", stalled)
    try:
        controller.estop_all()
        state = controller.stop_state
        assert state["every"] is True
        assert state["unconfirmed"] == ["stalled"]
    finally:
        stalled.release()


def test_clearing_a_model_drops_it_from_the_stop_state(controller):
    controller.add("one", FakeModel())
    controller.add("two", FakeModel())
    controller.estop_all()
    controller.run("one", "clear_estop", args=(True,))
    assert controller.stop_state["latched"] == ["two"]
    assert controller.stop_state["every"] is False


def test_an_empty_station_is_not_every_model_stopped(controller):
    assert controller.stop_state["every"] is False


def test_the_clear_confirmation_names_the_unconfirmed_model_in_plain_words(controller):
    """IMP7-4 / TK7-10: the question an operator answers before releasing the
    latch says which model never confirmed, in sentence case, station order."""
    healthy, stalled = FakeModel(), FakeModel(halt_blocks=True)
    controller.add("healthy", healthy)
    controller.add("stalled", stalled)
    try:
        controller.estop_all()
        result = controller.clear_estop_all()
        assert result.status == Result.CONFIRM
        assert "FULL STOP" not in result.reason
        assert result.reason.startswith("Clear the stop on healthy, stalled?")
        assert "stalled did not confirm its stop. Treat it as live" in result.reason
    finally:
        stalled.release()


def test_the_stop_state_carries_the_time_the_newest_latch_closed(controller):
    """Round 7 (Web CCR 1): a view reloaded mid-latch shows only the events
    newer than the latch, so it needs the latch's wall time."""
    import time
    controller.add("one", FakeModel())
    controller.add("two", FakeModel())
    assert controller.stop_state["since"] is None
    before = time.time()
    controller.run("one", "toggle_estop")
    first = controller.stop_state["since"]
    assert before <= first <= time.time()
    time.sleep(0.01)
    controller.run("two", "toggle_estop")
    assert controller.stop_state["since"] > first
    controller.run("two", "clear_estop", args=(True,))
    assert controller.stop_state["since"] == first


def test_the_state_names_the_models_that_are_energized(controller):
    """Tier N: a view warns before the operator walks away from energized
    hardware; `energized` is wider than `is_active`."""
    quiet, busy = FakeModel(), FakeModel()
    controller.add("quiet", quiet)
    controller.add("busy", busy)
    assert controller.state()["energized"] == []
    busy.mode = "running"
    assert controller.state()["energized"] == ["busy"]


def test_window_focus_gates_every_device_that_has_a_gate(controller):
    """CON-3: a manual input source under any attribute name is gated when
    the window loses focus, not only one called `gamepad`."""
    class Knob(FakeDevice):
        def __init__(self):
            super().__init__("knob")
            self.gates = []
        def set_gate(self, on): self.gates.append(bool(on))
    knob = Knob()
    model = FakeModel(devices=[knob])
    controller.add("piezo", model)
    controller.set_input_focus(False)
    controller.set_input_focus(True)
    assert knob.gates == [False, True]


# -- host: a model drawn on another model's page (Model.HOST, 2026-09-28) ----

def test_state_names_the_host_only_while_the_host_is_launched(controller):
    class Hosted(FakeModel):
        HOST = "map"
    hosted = Hosted()
    controller.add("red", hosted, {})
    assert controller.state()["models"]["red"]["host"] is None
    controller.add("map", FakeModel(), {})
    assert controller.state()["models"]["red"]["host"] == "map"
    assert controller.state()["models"]["map"]["host"] is None


def test_a_model_never_hosts_itself(controller):
    class Loop(FakeModel):
        HOST = "loop"
    controller.add("loop", Loop(), {})
    assert controller.state()["models"]["loop"]["host"] is None
