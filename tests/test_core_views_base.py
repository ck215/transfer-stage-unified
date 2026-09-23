"""`views.base` — the view logic written once for Tk and Qt.

No Tk, no Qt, no window: the toolkit half is a recording double, which is the
point of the split. Ports the intent of `tests/ui/test_edge_mvc_ui.py`,
`tests/ui/test_pyside4_discard_unsaved.py` (a refresh must not stamp on what
the operator is typing), `tests/pyside/test_pyside21_error_popup_queued.py`
and `tests/core/test_tkinter_teardown.py` (no modal after the close path
starts — it hung the Qt suite for three sessions).
"""
import pytest

import schema as sch
from controller.controller import Controller
from events import events
from views.base import Dashboard, PanelView

from test_core_fakes import (EventRecorder, FakeDashboard, FakeModel,
                             FakePanelView, FakeSetupPanel)

pytestmark = pytest.mark.schema


@pytest.fixture
def station():
    controller = Controller()
    model = FakeModel()
    controller.add("probe", model)
    yield controller, model
    model.release()
    controller.close()


@pytest.fixture
def view(station):
    controller, model = station
    built = FakePanelView(controller, "probe")
    built._build()
    return built


def _element(view, text):
    return next(e for e in view._elements if e.get("text") == text)


# -- construction: feature parity, enforced ---------------------------------

def test_a_view_that_cannot_render_an_element_type_cannot_be_built(station):
    controller, _ = station

    class Partial(FakePanelView):
        _make_plot = None            # a toolkit that forgot one element type

    with pytest.raises(TypeError, match="cannot render"):
        Partial(controller, "probe")


def test_the_error_names_every_missing_renderer(station):
    controller, _ = station

    class Partial(FakePanelView):
        _make_plot = None
        _make_image = "not callable either"

    with pytest.raises(TypeError) as raised:
        Partial(controller, "probe")
    assert "plot" in str(raised.value) and "image" in str(raised.value)


def test_a_complete_view_builds_every_element_the_schema_declares(view):
    declared = [e["type"] for e in sch.elements(view._schema())]
    assert [kind for kind, _ in view.built] == declared
    assert view.sections == ["Motion", "Modes", "Safety"]
    assert view.theme_calls == 1


def test_a_view_reaches_the_backend_only_through_schema_state_run(station):
    controller, _ = station
    built = FakePanelView(controller, "probe")
    assert built.controller is controller
    assert set(vars(PanelView)) >= {"_schema", "_state", "_call", "_options"}


def test_a_panel_the_controller_does_not_own_is_driven_directly(station):
    """Setup is not a Model, so a PanelView takes it explicitly."""
    controller, _ = station
    panel = FakeSetupPanel()
    built = FakePanelView(controller, "setup", panel=panel)
    built._build()
    assert built.sections == ["Setup"]
    assert built._call("build").is_ok


# -- every entry travels with every command (D-5) ---------------------------

def test_every_writable_entry_is_sent_with_every_command(view, station):
    _, model = station
    view.entry_text.update({"speed": "42.5", "steps": "9", "note": "hello"})
    result = view._run(_element(view, "Move"))
    assert result.is_ok
    assert (model.speed, model.steps, model.note) == (42.5, 9, "hello")


def test_a_read_only_element_is_not_sent_as_an_input(view):
    gathered = view._gather_inputs()
    assert set(gathered) == {"speed", "steps", "note"}
    assert "mode" not in gathered and "is_auto" not in gathered


def test_a_bad_entry_refuses_the_command_and_shows_why(view, station):
    _, model = station
    view.entry_text["speed"] = "banana"
    result = view._run(_element(view, "Move"))
    assert result.is_refused
    assert view.refusals[-1] and "Speed" in view.refusals[-1]
    assert model.moved == []


def test_a_successful_command_clears_the_status_line(view):
    view._run(_element(view, "Move"))
    assert view.refusals[-1] == ""


def test_a_failed_command_shows_no_status_line_because_it_is_already_a_popup(view):
    before = list(view.refusals)
    result = view._run(_element(view, "Boom"))
    assert result.is_failed
    assert view.refusals == before


# -- confirmation round trip ------------------------------------------------

def test_a_confirmed_command_is_re_run_with_args_plus_true(view, station):
    _, model = station
    view.confirm_answer = True
    result = view._run(_element(view, "Ask"))
    assert view.prompts == ["Really move?"]
    assert result.is_ok
    assert model.moved == [("confirmed", "north")]


def test_a_declined_confirmation_runs_nothing(view, station):
    _, model = station
    view.confirm_answer = False
    result = view._run(_element(view, "Ask"))
    assert result.needs_confirm
    assert model.moved == []


def test_the_confirmation_carries_its_own_inputs_not_the_widgets(view, station):
    _, model = station
    view.confirm_answer = True
    view._run(_element(view, "Ask"))
    assert model.speed == 12.0, (
        "the re-run must use the inputs the model attached to the question")


# -- toggles ----------------------------------------------------------------

def test_a_toggle_sends_on_args_when_off_and_off_args_when_on(view, station):
    _, model = station
    auto = _element(view, "Auto")
    view._run_toggle(auto)
    assert model.mode == "auto"
    view._run_toggle(auto)
    assert model.mode == "idle"


def test_a_toggle_reads_its_state_from_the_model_not_from_the_widget(view, station):
    _, model = station
    model.mode = "auto"
    view._run_toggle(_element(view, "Auto"))
    assert model.mode == "idle"


def test_the_full_stop_toggle_latches_through_the_view(view, station):
    _, model = station
    view._run_toggle(_element(view, "FULL STOP"))
    assert model.is_estopped


# -- refresh ----------------------------------------------------------------

def test_refresh_does_not_overwrite_an_entry_the_operator_is_typing_in(view, station):
    """PYSIDE-4 / the discard-unsaved family: a 100 ms redraw that stamps on a
    half-typed number is how an operator loses a setpoint."""
    _, model = station
    view.entry_text["speed"] = "37"
    view.dirty_entries.add("speed")
    model.speed = 400.0
    view._refresh()
    assert view.entry_text["speed"] == "37"


def test_refresh_updates_an_entry_nobody_is_editing(view, station):
    _, model = station
    model.speed = 400.0
    view._refresh()
    assert view.entry_text["speed"] == "400.0"


def test_refresh_renders_readonly_and_dropdown_values(view, station):
    _, model = station
    model.mode = "auto"
    model.port_name = "COM2"
    view._refresh()
    assert view.texts["mode"] == "auto"
    assert view.texts["port_name"] == "COM2"


def test_an_unset_dropdown_shows_nothing_not_the_word_none(view):
    view._refresh()
    assert view.texts["port_name"] == ""


def test_refresh_lights_toggles_and_indicators_from_state(view, station):
    _, model = station
    model.estop()
    model._fault("coil open")
    view._refresh()
    assert view.on_states["is_estopped"] is True
    assert view.on_states["is_faulted"] is True
    assert view.on_states["is_auto"] is False


def test_refresh_pulls_plot_image_and_log_data_through_run(view, station):
    _, model = station
    view._refresh()
    assert view.data["Trace"] == [1, 2, 3]
    assert view.data["Log"] == ["one line"]
    assert model.data_reads >= 1


def test_refresh_gates_every_element_including_the_entries(view, station):
    """"gating is part of every refresh, entries included" — the Web
    `set_attr` hole was a gate that lived in one renderer only."""
    _, model = station
    model.mode = "running"
    view._refresh()
    assert view.enabled["speed"] is False
    assert view.enabled["Move"] is False
    assert view.enabled["note"] is True
    assert view.enabled["is_estopped"] is True, (
        "FULL STOP must never be greyed out")


def test_gating_reopens_when_the_mode_leaves(view, station):
    _, model = station
    model.mode = "running"
    view._refresh()
    model.mode = "idle"
    view._refresh()
    assert view.enabled["speed"] is True and view.enabled["Move"] is True


def test_sync_gates_is_the_same_pass_as_refresh():
    assert PanelView._sync_gates is PanelView._refresh


def test_a_stale_model_is_flagged_so_the_operator_can_see_it(view, station):
    _, model = station
    view._refresh()
    assert view.stale is False
    model._updated_at -= 10
    view._refresh()
    assert view.stale is True


def test_running_a_command_refreshes_afterwards(view, station):
    _, model = station
    view._run(_element(view, "Move"))
    assert view.texts["mode"] == "idle"


def test_close_drops_the_elements_so_a_late_refresh_draws_nothing(view):
    view.close()
    assert view._elements == []
    view._refresh()


# -- Dashboard: the popup policy --------------------------------------------

def test_open_subscribes_to_both_event_sources(station):
    controller, _ = station
    dashboard = FakeDashboard(controller)
    dashboard.open()
    try:
        assert dashboard._on_event in events._subscribers
        assert dashboard._on_models_changed in controller._subscribers
    finally:
        events.unsubscribe(dashboard._on_event)
        controller.unsubscribe(dashboard._on_models_changed)


def test_an_acknowledged_event_becomes_a_popup(station):
    controller, _ = station
    dashboard = FakeDashboard(controller)
    dashboard.open()
    try:
        events.clear()
        events.error("Fault", "coil open", source="Probe")
        assert len(dashboard.popups) == 1
        assert len(dashboard.shown) == 1
    finally:
        events.unsubscribe(dashboard._on_event)
        controller.unsubscribe(dashboard._on_models_changed)


def test_a_quiet_event_reaches_the_log_panel_and_raises_no_popup(station):
    controller, _ = station
    dashboard = FakeDashboard(controller)
    dashboard.open()
    try:
        events.clear()
        events.info("Connected", "COM3")
        events.warn("Slow", "poll late")
        assert len(dashboard.shown) == 2 and dashboard.popups == []
    finally:
        events.unsubscribe(dashboard._on_event)
        controller.unsubscribe(dashboard._on_models_changed)


def test_no_popup_reaches_the_screen_once_close_has_begun(station):
    """The modal that hung the Qt suite: an event lands, `_marshal` queues it
    on the UI thread, and by the time it runs the window is tearing down."""
    controller, _ = station
    dashboard = FakeDashboard(controller)
    dashboard.open()
    dashboard.defer = True
    events.clear()
    events.error("Fault", "coil open", source="Probe")
    assert dashboard.popups == [], "not marshalled yet"

    dashboard.close()
    dashboard.flush()
    assert dashboard.popups == [], "a modal opened from inside the close path"


def test_close_unsubscribes_so_a_later_event_reaches_nothing(station):
    controller, _ = station
    dashboard = FakeDashboard(controller)
    dashboard.open()
    dashboard.close()
    events.clear()
    events.error("Fault", "coil open")
    assert dashboard.shown == [] and dashboard.popups == []
    assert dashboard._on_event not in events._subscribers


def test_close_closes_the_controller(station):
    controller, model = station
    dashboard = FakeDashboard(controller)
    dashboard.open()
    dashboard.close()
    assert controller.model_names == [] and model.is_estopped


def test_closing_twice_is_safe(station):
    controller, _ = station
    dashboard = FakeDashboard(controller)
    dashboard.open()
    dashboard.close()
    dashboard.close()


def test_a_close_path_publishes_nothing_that_wants_acknowledging(station):
    controller, model = station
    model.halt_blocks = True
    dashboard = FakeDashboard(controller)
    dashboard.open()
    try:
        with EventRecorder() as log:
            dashboard.close()
        assert log.acknowledged == []
    finally:
        model.release()


# -- Dashboard: models coming and going -------------------------------------

def test_a_model_added_becomes_a_panel(station):
    controller, _ = station
    dashboard = FakeDashboard(controller)
    dashboard.open()
    try:
        controller.add("second", FakeModel())
        assert dashboard.added_panels == ["second"]
    finally:
        dashboard.close()


def test_a_model_removed_drops_its_panel(station):
    controller, _ = station
    dashboard = FakeDashboard(controller)
    dashboard.open()
    try:
        controller.remove("probe")
        assert dashboard.removed_panels == ["probe"]
    finally:
        dashboard.close()


def test_opening_and_closing_a_model_goes_through_the_controller(station):
    controller, _ = station
    controller.factory = lambda config: FakeModel()
    dashboard = FakeDashboard(controller)
    dashboard.open()
    try:
        dashboard.close_model("probe")
        assert controller.model_names == []
        dashboard.open_model("probe")
        assert controller.model_names == ["probe"]
    finally:
        dashboard.close()


def test_window_focus_gates_input_rather_than_stopping_anything(station):
    controller, model = station

    class Pad:
        def __init__(self):
            self.gates = []

        def set_gate(self, is_open):
            self.gates.append(is_open)

    model.gamepad = Pad()
    dashboard = FakeDashboard(controller)
    dashboard._on_focus_change(False)
    assert model.gamepad.gates == [False] and model.is_estopped is False


# -- Dashboard: the global stop ---------------------------------------------

def test_the_global_stop_latches_every_model(station):
    controller, model = station
    dashboard = FakeDashboard(controller)
    assert dashboard.toggle_estop_all() == {"probe": True}
    assert model.is_estopped


def test_the_global_stop_asks_before_clearing_and_clears_on_yes(station):
    controller, model = station
    dashboard = FakeDashboard(controller)
    dashboard.toggle_estop_all()
    dashboard.confirm_answer = True
    result = dashboard.toggle_estop_all()
    assert dashboard.prompts and "probe" in dashboard.prompts[0]
    assert result.is_ok and model.is_estopped is False


def test_declining_the_clear_leaves_the_latch_alone(station):
    controller, model = station
    dashboard = FakeDashboard(controller)
    dashboard.toggle_estop_all()
    dashboard.confirm_answer = False
    result = dashboard.toggle_estop_all()
    assert result.needs_confirm and model.is_estopped


# -- the toolkit contract ---------------------------------------------------

@pytest.mark.parametrize("name", [
    "_make_section", "_read_entry", "_entry_is_dirty", "_set_text", "_set_on",
    "_set_data", "_set_enabled", "_set_stale", "_confirm", "_show_refused",
    "_apply_theme"])
def test_the_base_panel_view_refuses_to_guess_at_a_toolkit(name):
    with pytest.raises(NotImplementedError):
        getattr(PanelView, name)(object(), *([None] * (
            getattr(PanelView, name).__code__.co_argcount - 1)))


@pytest.mark.parametrize("name", ["_marshal", "_show_event", "_show_popup",
                                  "_confirm", "_add_panel", "_remove_panel"])
def test_the_base_dashboard_refuses_to_guess_at_a_toolkit(name):
    with pytest.raises(NotImplementedError):
        getattr(Dashboard, name)(object(), None)
