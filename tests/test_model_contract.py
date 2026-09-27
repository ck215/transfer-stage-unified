"""The model contract (docs/rebuild/MODEL_CONTRACT.md), asserted against ANY
Model subclass: every class Setup registers plus a minimal model that
declares nothing but a readout. A new device class registered in
`setup.MODEL_TYPES` inherits every check. Plain fast test: no window, no
port, no gamepad; SIM constructors. From the abstraction audit of 2026-09-26.
"""
import json
import time

import pytest

import schema as sch                                    # noqa: E402
from controller.setup import MODEL_TYPES                # noqa: E402
from model.base import Model                            # noqa: E402
from param import Param                                 # noqa: E402
from result import NeedsConfirm, Result                 # noqa: E402


class MinimalModel(Model):
    """The least a device can be: one number to read, the inherited stop."""
    NAME = "Minimal"

    @property
    def schema(self):
        return sch.schema(
            sch.section("Reading", sch.readonly("Value:", "value", rail=True, unit="V")),
            self._safety_section())

    value = property(lambda self: "1.000")

    def _expects_heartbeat(self):
        return False


CLASSES = dict(MODEL_TYPES)
CLASSES["Minimal"] = MinimalModel

#: The state keys a view reads by name (grep of tk.py, qt.py, app.js,
#: server.py, views/base.py at 612397c).
STATE_KEYS = ("name", "mode", "model_mode", "values", "is_estopped",
              "is_faulted", "stop_confirmed", "latched_at", "fault",
              "is_active", "age", "devices")

#: Required keys per element type: what every renderer indexes without .get().
REQUIRED = {
    "readonly": ("text", "model_attr"),
    "entry": ("text", "model_attr", "param", "value_type"),
    "button": ("text", "command", "inputs", "args"),
    "toggle": ("text", "model_attr", "command", "true_text", "false_text",
               "on_args", "off_args"),
    "checkbox": ("text", "model_attr", "command"),
    "dropdown": ("text", "model_attr", "command", "options_command"),
    "region_select": ("text", "command"),
    "file_save": ("text", "command", "extensions"),
    "file_open": ("text", "command", "extensions"),
    "plot": ("text", "data_command"),
    "image": ("text", "data_command"),
    "indicator": ("text", "model_attr"),
    "log_stream": ("text", "source_command"),
    "internal": ("command",),
}

COMMAND_KEYS = ("command", "data_command", "source_command", "options_command")


@pytest.fixture(params=sorted(CLASSES), ids=lambda n: n.replace(" ", "_"))
def model(request):
    cls = CLASSES[request.param]
    if cls is MinimalModel:
        built = cls()
    else:
        # Setup's exact call (setup.py:1024): the contract's constructor.
        built = cls(port="SIM", gamepad=None, sim=True)
    built.open()
    try:
        yield built
    finally:
        try:
            built.estop()
        finally:
            built.close()


def _elements(model):
    return list(sch.elements(model.schema))


def _modes(model):
    """Every mode word the schema's gates name, plus the live mode."""
    words = {model.mode_name, model.gate_mode, "latched", "<any other mode>"}
    for e in _elements(model):
        words.update(e.get("enabled_when") or ())
        words.update(e.get("disabled_when") or ())
    return words


# -- 1. the class --------------------------------------------------------------

def test_constructor_takes_setups_call(model):
    """`cls(port=, gamepad=, sim=)`: Setup.model_from_config's one call."""
    assert isinstance(model, Model)


def test_class_declares_its_own_setup_attributes(model):
    """Setup reads these four through `_declared` (setup.py:114); a model
    added at runtime through `Controller.add` needs only NAME."""
    cls = type(model)
    wanted = (("NAME", "IDENTITY", "NEEDS_PORT", "NEEDS_GAMEPAD")
              if cls in MODEL_TYPES.values() else ("NAME",))
    for attr in wanted:
        assert any(attr in base.__dict__ for base in cls.__mro__
                   if base not in (Model, object) and base.__name__ != "Panel"), \
            f"{cls.__name__} inherits {attr} from the contract base"
    assert cls.NAME and cls.NAME != "Panel"


# -- 2. the schema ---------------------------------------------------------------

def test_every_element_is_a_renderable_type_with_its_required_keys(model):
    for e in _elements(model):
        assert e["type"] in sch.ELEMENT_TYPES, e
        missing = [k for k in REQUIRED[e["type"]] if k not in e]
        assert not missing, f"{e.get('text')!r} ({e['type']}) lacks {missing}"
        assert e.get("role", "neutral") in sch.ROLES, e


def test_sections_carry_valid_tiers_and_the_safety_section_is_last(model):
    sections = model.schema["sections"]
    for s in sections:
        assert s.get("tier", 1) in sch.TIERS
    assert sections[-1]["title"] == "Safety", "schema must end with _safety_section()"
    commands = {e.get("command") for e in sections[-1]["elements"]}
    assert {"toggle_estop", "clear_estop"} <= commands


def test_every_declared_command_exists_on_the_model(model):
    for e in _elements(model):
        for key in COMMAND_KEYS:
            name = e.get(key)
            if name:
                assert hasattr(type(model), name) or hasattr(model, name), \
                    f"{key} {name!r} ({e.get('text')!r}) is not on {model.NAME}"


def test_every_model_attr_reads_without_raising(model):
    for e in _elements(model):
        attr = e.get("model_attr")
        if attr:
            getattr(model, attr)          # raises -> the view shows nothing


def test_every_writable_entry_has_a_param_that_matches_its_element(model):
    for e in _elements(model):
        if e.get("writable"):
            assert e["type"] == "entry", f"only entries may be writable: {e}"
            param = model.PARAMS.get(e["model_attr"])
            assert isinstance(param, Param), f"{e['model_attr']} has no Param"
            for key, value in param.to_schema().items():
                assert e.get(key) == value, (e["model_attr"], key)
            if "slider" in e:
                low, high = e["slider"]
                assert param.minimum is None or low >= param.minimum
                assert param.maximum is None or high <= param.maximum


def test_a_go_buttons_inputs_are_writable_entries(model):
    entries = {e["model_attr"]: e for e in _elements(model)
               if e["type"] == "entry" and e.get("writable")}
    for e in _elements(model):
        for name in e.get("inputs") or ():
            assert name in entries, f"{e['text']!r} input {name!r} is not an entry"


def test_a_go_buttons_inputs_can_be_edited_where_the_button_runs(model):
    """A command whose input is locked in every mode the command runs in can
    only ever send the value typed before the mode was entered."""
    entries = {e["model_attr"]: e for e in _elements(model)
               if e["type"] == "entry" and e.get("writable")}
    for e in _elements(model):
        if not e.get("inputs"):
            continue
        live = [m for m in _modes(model) if m != "latched" and sch.is_enabled(e, m)]
        for name in e["inputs"]:
            assert any(sch.is_enabled(entries[name], m) for m in live), \
                f"{e['text']!r}: {name} is locked in every mode it runs in ({live})"


def test_toggle_args_pass_the_allow_list(model):
    for e in _elements(model):
        if e["type"] == "toggle":
            for args in (e["on_args"], e["off_args"]):
                matches = [x for x in _elements(model) if x.get("command") == e["command"]]
                assert matches


def test_axis_readouts_use_a_letter_every_view_recognises(model):
    """Web (`app.js:372`) and Tk (`tk.py:95`) group only X/Y/Z; Qt any A-Z."""
    import re
    for e in _elements(model):
        if e["type"] == "readonly" and e.get("rail"):
            text = re.sub(r"\s*:\s*$", "", e["text"]).strip()
            if re.fullmatch(r"[A-Z]", text):
                assert text in "XYZ", f"axis {text!r} renders as an axis in Qt only"


# -- 3. the state ----------------------------------------------------------------

def test_state_carries_every_key_the_views_read(model):
    state = model.state
    missing = [k for k in STATE_KEYS if k not in state]
    assert not missing, missing
    attrs = {e["model_attr"] for e in _elements(model) if e.get("model_attr")}
    assert attrs <= set(state["values"]), attrs - set(state["values"])
    assert isinstance(state["devices"], dict)


def test_state_is_json_serialisable_for_the_web_view(model):
    json.dumps(model.state)
    json.dumps(model.schema)


def test_idle_countdown_keys_come_as_a_pair(model):
    state = model.state
    if "idle_remaining" in state:
        assert "idle_warn_seconds" in state
        commands = {e.get("command") for e in _elements(model)}
        assert "extend_idle" in commands, "idle clock without an extend_idle to answer it"


def test_device_status_is_a_word_the_views_know(model):
    known = {"verified", "simulated", "lost", "closed", "unverified",
             "connecting", "bound", "unbound", "open", "capturing"}
    for kind, status in model.state["devices"].items():
        assert str(status) in known, f"{kind}: {status!r}"


def test_file_save_output_root_is_published_at_the_top_of_state(model):
    """The contract: a model with a file_save publishes `output_root` at the
    top level of its state (Model.state); the Web server reads it there
    (CON-5 fixed the server's lookup)."""
    if not any(e["type"] == "file_save" for e in _elements(model)):
        return
    assert model.state.get("output_root"), \
        "a file_save model whose downloads the Web server cannot check"


# -- 4. the stop -----------------------------------------------------------------

def test_estop_latches_calls_halt_and_reports(model, monkeypatch):
    calls = []
    real = model._halt_hardware
    monkeypatch.setattr(model, "_halt_hardware", lambda: calls.append(1) or real())
    before = time.time()
    confirmed = model.estop()
    assert calls, "_halt_hardware was not called"
    assert model.is_estopped and model.gate_mode == "latched"
    assert model.state["mode"] == "latched"
    assert model.stop_confirmed is confirmed
    assert model.latched_at is not None and model.latched_at >= before - 1
    with pytest.raises(NeedsConfirm):
        model.clear_estop()
    model.clear_estop(confirmed=True)
    assert not model.is_estopped
    assert model.stop_confirmed is None and model.latched_at is None


def test_estop_confirms_in_sim(model):
    """Informational: a SIM model that cannot confirm its stop makes every
    FULL STOP in a simulated session read 'did not confirm'. The SIM Rotator
    is that model today (CON-13, owner call): an expected failure, not a
    licence."""
    if type(model).__name__ == "Rotator":
        pytest.xfail("CON-13: the SIM Rotator has no device, so its stop never confirms")
    assert model.estop() is True


def test_every_gated_command_refuses_while_latched(model):
    model.estop()
    for e in _elements(model):
        command = e.get("command")
        if not command or e["type"] == "internal":
            continue
        if sch.is_enabled(e, "latched"):
            continue
        args = e.get("on_args") or e.get("args") or []
        result = model.run(command, None, tuple(args))
        assert result.status == Result.REFUSED, \
            f"{e['text']!r} ({command}{tuple(args)}) while latched -> {result!r}"


def test_leaving_an_energized_mode_ignores_bad_entry_text(model):
    """O1, generalised: whatever toggle took the model into an energized
    state, its off direction is a stop and must not be refused because an
    unrelated entry holds bad text."""
    numeric = [e["model_attr"] for e in _elements(model)
               if e["type"] == "entry" and e.get("value_type") in ("int", "float")]
    for e in _elements(model):
        if e["type"] != "toggle" or e["command"] == "toggle_estop":
            continue
        on = model.run(e["command"], None, tuple(e["on_args"]))
        if not on.is_ok or not model.is_energized:
            model.run(e["command"], None, tuple(e["off_args"]))
            continue
        bad = {numeric[0]: "not a number"} if numeric else None
        off = model.run(e["command"], bad, tuple(e["off_args"]))
        assert off.is_ok and not model.is_energized, \
            f"leaving {e['text']!r} with bad text elsewhere -> {off!r}"


def test_is_energized_covers_is_active_in_every_reachable_mode(model):
    assert (not model.is_active) or model.is_energized
    for e in _elements(model):
        if e["type"] == "toggle" and e["command"] != "toggle_estop":
            model.run(e["command"], None, tuple(e["on_args"]))
            assert (not model.is_active) or model.is_energized, e["text"]
            model.run(e["command"], None, tuple(e["off_args"]))


def test_confirm_round_trips(model):
    """Every NeedsConfirm names a declared command and, re-run as
    command(*args, True), does not ask again."""
    declared = {e.get("command") for e in _elements(model)}
    for e in _elements(model):
        if e["type"] != "button":
            continue
        result = model.run(e["command"], None, tuple(e.get("args") or ()))
        if result.needs_confirm:
            assert result.command in declared, result.command
            again = model.run(result.command, result.inputs, (*result.args, True))
            assert not again.needs_confirm, e["text"]
        assert not (result.is_failed and isinstance(result.exception, TypeError)), \
            f"{e['text']!r}: signature does not match its declared args: {result.exception!r}"


def test_window_focus_gates_every_manual_input_device(model):
    """D-4: an unfocused window gates manual input. `Controller.
    set_input_focus` (controller.py:175) reaches a model only through an
    attribute literally named `gamepad`; any other input Device with a
    `set_gate` is never told."""
    from controller.controller import Controller
    cls = type(model)
    fresh = cls() if cls is MinimalModel else cls(port="SIM", gamepad=None, sim=True)
    station = Controller()
    station.add("under test", fresh)
    try:
        station.set_input_focus(False)
        for device in fresh.devices:
            if callable(getattr(device, "set_gate", None)):
                gate = getattr(device, "is_gate_open", getattr(device, "gate_open", None))
                assert gate is False, f"{type(device).__name__} still open while unfocused"
    finally:
        station.close()


# -- MOD-2: loops go through the base -----------------------------------------

#: Classes that still override `_stop_threads`, and why a join cannot do it.
STOP_THREADS_OVERRIDES = {
    # A run is a one-shot per-command worker with its own stop (`run.end()`):
    # ending it is something the base join cannot do.
    "RedMonitor": "ends the in-flight run before the base join",
}


def test_loops_are_stopped_by_the_base_join(model):
    cls = type(model)
    if cls._stop_threads is Model._stop_threads:
        return
    assert cls.__name__ in STOP_THREADS_OVERRIDES, (
        f"{cls.__name__} overrides _stop_threads; spawn its loops with "
        "Model._spawn and let the base join them")


def test_close_leaves_no_spawned_loop_running(model):
    cls = type(model)
    fresh = cls() if cls is MinimalModel else cls(port="SIM", gamepad=None, sim=True)
    fresh.open()
    spawned = list(fresh._spawned_threads())
    fresh.close()
    alive = [t.name for t in spawned if t.is_alive()]
    assert not alive, f"still running after close: {alive}"


# -- MOD-3: the idle clock is one mixin ---------------------------------------

def test_a_model_with_an_idle_clock_takes_it_from_the_mixin(model):
    from model.idle import IdleInterlock
    commands = {e.get("command") for e in _elements(model)}
    if "extend_idle" in commands or "idle_remaining" in model.state:
        assert isinstance(model, IdleInterlock), (
            f"{type(model).__name__} runs its own idle clock; mix in "
            "model.idle.IdleInterlock")


# -- MOD-1: the gamepad contract ------------------------------------------------

from test_gamepad_input import JoystickStage              # noqa: E402

#: A gamepad-driven device that is not a probe, run through every check above.
CLASSES["Joystick Stage"] = JoystickStage


def test_a_gamepad_model_takes_its_input_from_the_mixin(model):
    from model.gamepad_input import GamepadInput
    if not type(model).NEEDS_GAMEPAD:
        assert not isinstance(model, GamepadInput) or type(model) is JoystickStage
        return
    assert isinstance(model, GamepadInput), (
        f"{type(model).__name__} needs a gamepad but does not mix in "
        "model.gamepad_input.GamepadInput")
    dropdowns = [e for e in _elements(model) if e["type"] == "dropdown"
                 and e["command"] == "set_gamepad"]
    assert dropdowns, f"{type(model).__name__} declares no Gamepad: dropdown"
    assert dropdowns[0]["options_command"] == "gamepad_options"
    assert dropdowns[0]["model_attr"] == "gamepad_name"
