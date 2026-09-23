"""`panel` — the allow-list, the atomic input set, and Result.

This is where the old `execute_command` / `apply_inputs` / `CommandResult`
tests land: `tests/core/test_system_manager.py`,
`tests/core/test_dc6_mode_gated_entries.py` (the mode gate must be enforced by
the model, not only rendered), and `tests/ui/test_schema_v2.py`'s D-5 rule
that a command carries the values typed a moment ago rather than reading one
edit behind.
"""
import pytest

import schema as sch
from panel import Panel
from param import Param
from result import Refused, Result

from test_core_fakes import EventRecorder, FakeModel, FakePanel

pytestmark = pytest.mark.params


# -- the allow-list ---------------------------------------------------------

def test_a_declared_command_runs_and_returns_ok_with_its_value():
    panel = FakePanel()
    result = panel.run("always")
    assert result.is_ok and result.value == "ok" and bool(result)


def test_a_command_the_schema_does_not_declare_is_refused_not_called():
    """The allow-list for every view, the Web API included."""
    panel = FakePanel()
    result = panel.run("secret")
    assert result.is_refused and "not a command of FakePanel" in result.reason
    assert panel.ran == [], "the body ran despite the refusal"


def test_an_attribute_that_is_not_a_command_at_all_is_refused_not_raised():
    result = FakePanel().run("NAME")
    assert result.is_refused


def test_a_command_that_does_not_exist_on_the_object_is_refused_first():
    result = FakePanel().run("not_a_method_anywhere")
    assert result.is_refused and not result.is_failed


def test_a_data_command_is_on_the_allow_list_too():
    model = FakeModel()
    assert model.run("trace_data").value == [1, 2, 3]
    assert model.run("log_lines").value == ["one line"]


# -- mode gating ------------------------------------------------------------

def test_a_gated_command_runs_while_idle():
    panel = FakePanel(mode="idle")
    assert panel.run("go").is_ok


def test_a_gated_command_is_refused_by_the_model_not_only_greyed_out():
    """DC-6: a request that goes straight at the model — `set_attr`, the Web
    route, a direct call — reached the control regardless of mode."""
    panel = FakePanel(mode="running")
    result = panel.run("go")
    assert result.is_refused
    assert "Go" in result.reason and "running" in result.reason
    assert panel.ran == []


def test_the_refusal_names_the_control_the_operator_clicked():
    panel = FakePanel(mode="running")
    assert panel.run("go").reason.startswith("Go is not available")


def test_two_toggles_sharing_one_command_are_gated_independently():
    """`set_mode` serves both toggles; only the second declares
    `disabled_when=("auto",)`. Picking the element by its args is the whole
    point of `on_args` / `off_args`."""
    model = FakeModel()
    model.mode = "auto"
    refused = model.run("set_mode", args=("running",))
    assert refused.is_refused and "Run" in refused.reason
    assert model.mode == "auto", "the refused command still ran"


def test_the_other_toggle_on_the_same_command_still_works_in_that_mode():
    model = FakeModel()
    model.mode = "auto"
    assert model.run("set_mode", args=("idle",)).is_ok
    assert model.mode == "idle"


def test_a_toggles_on_args_select_its_own_gate_not_the_first_match():
    model = FakeModel()
    model.mode = "idle"
    assert model.run("set_mode", args=("running",)).is_ok
    assert model.mode == "running"


class _SharedOffArgs(Panel):
    """Two mode toggles on one command, both switching OFF to the same mode.

    This is the probes' real shape: Autonomous and Manual both call
    `set_mode`, and both turn off to idle. Only Manual is gated, because you
    cannot go manual mid-run.
    """

    NAME = "Probe"
    PARAMS = {}

    def __init__(self):
        self.mode = "autonomous"
        super().__init__()

    @property
    def mode_name(self):
        return self.mode

    @property
    def is_manual(self):
        return self.mode == "manual"

    @property
    def is_auto(self):
        return self.mode == "autonomous"

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Modes",
            sch.toggle("Manual", "is_manual", "set_mode", "MANUAL", "off",
                       on_args=("manual",), off_args=("idle",),
                       disabled_when=("autonomous",)),
            sch.toggle("Autonomous", "is_auto", "set_mode", "AUTO", "off",
                       on_args=("autonomous",), off_args=("idle",)),
        ))

    def set_mode(self, mode):
        self.mode = mode
        return mode


def test_the_gated_toggle_is_still_refused_on_its_own_args():
    panel = _SharedOffArgs()
    assert panel.run("set_mode", args=("manual",)).is_refused
    assert panel.mode == "autonomous"


def test_leaving_a_mode_is_not_gated_by_a_sibling_toggles_declaration():
    panel = _SharedOffArgs()
    result = panel.run("set_mode", args=("idle",))
    assert result.is_ok, result.reason
    assert panel.mode == "idle"


def test_a_gated_command_does_not_commit_its_inputs():
    """The allow-list runs before the input set, so a refused click cannot
    leave half an edit behind."""
    panel = FakePanel(mode="running")
    result = panel.run("go", inputs={"speed": "250.0"})
    assert result.is_refused and "not available" in result.reason
    assert panel.speed == 100.0


# -- inputs: atomic, and the refusal names the field ------------------------

def test_a_commands_inputs_are_applied_before_the_body_runs():
    """D-5: without this the model reads whatever it happens to hold, which is
    one edit behind what was just typed. Tk hid it by forcing focus away — a
    named anti-fix, because it only ever worked in Tk."""
    panel = FakePanel()
    result = panel.run("go", inputs={"speed": "42.5", "steps": "9"})
    assert result.is_ok
    assert panel.ran == [("go", 42.5, 9)]


def test_an_input_set_is_all_or_nothing():
    panel = FakePanel()
    panel.speed, panel.steps = 100.0, 4
    result = panel.run("go", inputs={"speed": "42.5", "steps": "not a number"})
    assert result.is_refused
    assert (panel.speed, panel.steps) == (100.0, 4), (
        "a partially applied input set leaves the model in a state the "
        "operator never asked for")
    assert panel.ran == []


def test_the_refusal_names_the_field_that_failed():
    result = FakePanel().run("go", inputs={"steps": "banana"})
    assert result.is_refused
    assert "Steps" in result.reason and "banana" in result.reason


def test_an_out_of_range_input_is_refused_rather_than_clamped():
    panel = FakePanel()
    result = panel.run("go", inputs={"speed": "9000"})
    assert result.is_refused and "at most 500.0" in result.reason
    assert panel.speed == 100.0


def test_an_empty_box_is_refused_and_says_which_one():
    result = FakePanel().run("go", inputs={"speed": ""})
    assert result.is_refused and "Speed is empty" in result.reason


def test_a_field_the_schema_does_not_declare_writable_is_refused():
    """The Web `/api/set_attr` hole: it accepted a write to anything the
    schema mentioned, mode flags included."""
    panel = FakePanel()
    result = panel.run("always", inputs={"mode": "running"})
    assert result.is_refused and "not an editable field" in result.reason
    assert panel.mode == "idle"


def test_a_field_with_no_param_declaration_is_refused():
    result = FakePanel().run("always", inputs={"nonsense": "1"})
    assert result.is_refused and "not an editable field" in result.reason


def test_an_input_typed_into_a_gated_entry_is_refused_and_names_the_mode():
    panel = FakePanel(mode="running")
    panel.speed = 100.0
    result = panel.run("always", inputs={"speed": "250.0"})
    assert result.is_refused
    assert "Speed" in result.reason and "running" in result.reason
    assert panel.speed == 100.0


def test_an_unchanged_value_of_a_gated_entry_is_not_an_edit():
    """Every view sends every entry with every command, so a gated field's
    current text arrives on every click. Refusing that would make every
    command in `running` mode impossible."""
    panel = FakePanel(mode="running")
    panel.speed = 100.0
    current = panel.PARAMS["speed"].format(panel.speed)
    result = panel.run("always", inputs={"speed": current, "note": "hi"})
    assert result.is_ok, result.reason
    assert panel.speed == 100.0 and panel.note == "hi"


def test_an_ungated_entry_still_commits_while_another_is_gated():
    panel = FakePanel(mode="running")
    result = panel.run("always", inputs={
        "speed": panel.PARAMS["speed"].format(panel.speed), "note": "ok"})
    assert result.is_ok and panel.note == "ok"


def test_no_inputs_is_not_an_error():
    assert FakePanel().run("always", inputs={}).is_ok
    assert FakePanel().run("always", inputs=None).is_ok


def test_set_value_commits_one_field_through_the_same_validation():
    panel = FakePanel()
    assert panel.set_value("speed", "12.5").is_ok
    assert panel.speed == 12.5
    assert panel.set_value("speed", "nope").is_refused
    assert panel.speed == 12.5


def test_set_value_cannot_reach_a_read_only_attribute():
    panel = FakePanel()
    assert panel.set_value("mode", "running").is_refused
    assert panel.mode == "idle"


# -- the three ways a command ends -------------------------------------------

def test_a_refused_command_is_an_info_event_and_never_a_popup():
    model = FakeModel()
    with EventRecorder() as log:
        result = model.run("nope")
    assert result.is_refused and result.reason == "not while the lid is open"
    assert [e.severity for e in log.seen] == ["info"]
    assert log.acknowledged == [], "a refusal is not a fault"


def test_an_exception_becomes_failed_and_exactly_one_acknowledged_event():
    model = FakeModel()
    with EventRecorder() as log:
        result = model.run("boom")
    assert result.is_failed
    assert "boom failed" in result.reason and "the board said no" in result.reason
    assert isinstance(result.exception, RuntimeError)
    assert len(log.acknowledged) == 1, (
        f"a failed command raised {len(log.acknowledged)} popups")
    assert log.acknowledged[0].title == "Command Failed"


def test_a_failed_command_keeps_the_exception_for_the_log_file():
    result = FakeModel().run("boom")
    assert result.exception is not None
    assert result.exception.__traceback__ is not None


def test_needs_confirm_round_trips_with_its_args_and_inputs():
    model = FakeModel()
    ask = model.run("ask")
    assert ask.needs_confirm and not ask.is_ok
    assert ask.reason == "Really move?"
    assert ask.command == "ask"
    assert ask.inputs == {"speed": "12.0"}
    assert ask.args == ("north",)

    confirmed = model.run(ask.command, ask.inputs, (*ask.args, True))
    assert confirmed.is_ok and confirmed.value == "asked"
    assert model.moved == [("confirmed", "north")]
    assert model.speed == 12.0, "the confirmation's inputs must travel with it"


def test_needs_confirm_raises_no_event_at_all():
    with EventRecorder() as log:
        FakeModel().run("ask")
    assert log.seen == [], "a question is not a fault and not a refusal"


def test_a_command_run_returns_a_result_and_never_raises():
    model = FakeModel()
    for command in ("boom", "nope", "ask", "move", "secret_nonsense"):
        assert isinstance(model.run(command), Result)


# -- the quiet channel -------------------------------------------------------

def test_a_data_command_is_not_logged_on_every_refresh(tmp_path):
    """Plot, image and log sources are polled every 100 ms. One debug line per
    poll would be ~36 000 lines an hour."""
    from events import events
    model = FakeModel()
    path = events.open_file(str(tmp_path))
    try:
        for _ in range(10):
            model.run("trace_data")
        model.run("move")
    finally:
        events.close_file()
    text = open(path).read()
    assert "trace_data" not in text
    assert "move" in text, "an ordinary command should still be timed"


def test_commit_is_quiet_too(tmp_path):
    from events import events
    model = FakeModel()
    path = events.open_file(str(tmp_path))
    try:
        model.set_value("note", "hello")
    finally:
        events.close_file()
    assert "_commit()" not in open(path).read()


# -- options -----------------------------------------------------------------

def test_options_come_from_the_declared_options_command():
    assert FakeModel().options("port_options") == ["COM1", "COM2"]


def test_an_undeclared_options_command_is_refused():
    with pytest.raises(Refused, match="not an options source"):
        FakeModel().options("port_name")


def test_options_with_no_command_is_refused_rather_than_crashing():
    with pytest.raises(Refused):
        FakeModel().options(None)


# -- the state snapshot ------------------------------------------------------

def test_state_reports_every_model_attr_the_schema_mentions():
    model = FakeModel()
    values = model.state["values"]
    for attr in ("speed", "steps", "note", "mode", "port_name",
                 "is_auto", "is_running", "is_estopped", "is_faulted", "fault"):
        assert attr in values, f"{attr} is in the schema but not in state"


def test_state_renders_a_number_at_its_declared_precision():
    model = FakeModel()
    model.speed = 12.3456
    assert model.state["values"]["speed"] == "12.3"


def test_state_leaves_a_boolean_as_a_boolean_for_the_toggle_renderers():
    model = FakeModel()
    assert model.state["values"]["is_auto"] is False
    model.mode = "auto"
    assert model.state["values"]["is_auto"] is True


def test_an_unset_value_is_empty_text_not_the_string_none():
    assert FakeModel().state["values"]["port_name"] == ""


def test_state_carries_the_name_and_the_mode():
    model = FakeModel()
    model.mode = "auto"
    assert model.state["name"] == "Fake" and model.state["mode"] == "auto"


def test_a_region_value_is_rendered_with_the_one_shared_wording():
    class Regional(Panel):
        NAME = "Regional"
        PARAMS = {}

        def __init__(self):
            self.region = {"width": 8, "height": 4, "left": 1, "top": 2}
            super().__init__()

        @property
        def schema(self):
            return sch.schema(sch.section("R", sch.region_select(
                "Region", "pick", model_attr="region")))

    assert Regional().state["values"]["region"] == "8x4 at (1, 2)"


def test_defaults_come_from_the_param_table_at_construction():
    panel = FakePanel()
    assert (panel.speed, panel.steps, panel.note) == (100.0, 4, "")


def test_a_subclass_extends_params_rather_than_replacing_them():
    class Extended(FakePanel):
        PARAMS = {**FakePanel.PARAMS, "extra": Param("extra", "int", default=7)}

    panel = Extended()
    assert panel.extra == 7 and panel.speed == 100.0


def test_the_base_panel_has_no_schema_of_its_own():
    with pytest.raises(NotImplementedError):
        Panel().schema


def test_the_base_panels_mode_is_the_empty_string():
    assert Panel().mode_name == ""
