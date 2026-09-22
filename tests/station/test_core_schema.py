"""`station.schema` — RC-7, the one element contract three renderers read.

Ports the conformance intent of `tests/ui/test_ui_schema.py` and
`tests/ui/test_schema_v2.py`: every builder must emit an element every
renderer can draw, writability must be opt-in (STEPPER-11 / DC-11), gating
must have exactly one implementation (`is_enabled`), and "nothing is
selected" must look the same in all three views (`current_text`).
"""
import pytest

from station import schema as sch
from station.param import Param

pytestmark = pytest.mark.schema


BUILT = {
    "readonly": sch.readonly("Position:", "position"),
    "entry": sch.entry("Speed", "speed", Param("speed", "float")),
    "button": sch.button("Go", "go"),
    "toggle": sch.toggle("Auto", "is_auto", "set_mode", "ON", "OFF"),
    "dropdown": sch.dropdown("Port", "port", "set_port", "port_options"),
    "region_select": sch.region_select("Region", "pick_region"),
    "file_save": sch.file_save("Save", "save_csv"),
    "file_open": sch.file_open("Load", "load_csv"),
    "plot": sch.plot("Trace", "trace_data"),
    "image": sch.image("Frame", "frame_data"),
    "indicator": sch.indicator("Fault", "is_faulted"),
    "log_stream": sch.log_stream("Log", "log_lines"),
}


# -- conformance -----------------------------------------------------------

@pytest.mark.parametrize("name,element", sorted(BUILT.items()))
def test_every_builder_emits_a_declared_element_type(name, element):
    assert element["type"] == name
    assert element["type"] in sch.ELEMENT_TYPES


@pytest.mark.parametrize("name,element", sorted(BUILT.items()))
def test_every_element_declares_a_role_a_renderer_can_map(name, element):
    assert element["role"] in sch.ROLES, f"{name} role {element['role']!r}"


@pytest.mark.parametrize("name,element", sorted(BUILT.items()))
def test_every_element_declares_writability_explicitly(name, element):
    assert "writable" in element, f"{name} left writability to the renderer"
    assert isinstance(element["writable"], bool)


@pytest.mark.parametrize("name,element", sorted(BUILT.items()))
def test_only_an_entry_is_writable(name, element):
    """STEPPER-11 / DC-11: `/api/set_attr` accepted a write to anything the
    schema mentioned, mode flags included. Writability has to be asked for."""
    assert element["writable"] is (name == "entry")


@pytest.mark.parametrize("name,element", sorted(BUILT.items()))
def test_no_element_carries_a_colour_literal(name, element):
    """Colour is the renderer's job; the schema names the meaning."""
    assert not {"bg", "fg", "background", "foreground"} & set(element)
    assert not any(isinstance(v, str) and v.startswith("#")
                   for v in element.values())


def test_every_declared_element_type_except_internal_has_a_builder():
    """`internal` is the one type with no builder — it is the escape hatch a
    renderer must still implement, so nothing falls through silently."""
    assert set(BUILT) | {"internal"} == set(sch.ELEMENT_TYPES)


# -- structure -------------------------------------------------------------

def test_schema_is_version_2_and_serialisable_in_shape():
    built = sch.schema(sch.section("A", sch.button("Go", "go")))
    assert built["version"] == 2
    assert built["sections"][0]["title"] == "A"


def test_a_section_drops_none_so_a_model_can_omit_a_control_conditionally():
    built = sch.section("A", sch.button("Go", "go"), None, sch.button("B", "b"))
    assert len(built["elements"]) == 2


def test_elements_flattens_every_section_in_order():
    built = sch.schema(sch.section("A", sch.button("1", "one")),
                       sch.section("B", sch.button("2", "two"),
                                   sch.button("3", "three")))
    assert [e["text"] for e in sch.elements(built)] == ["1", "2", "3"]


def test_elements_of_an_empty_schema_yields_nothing():
    assert list(sch.elements({})) == []


# -- the builders that carry rules -----------------------------------------

def test_a_dropdown_without_a_command_cannot_be_expressed():
    """PYSIDE-7: a dropdown with `model_attr` and no command reached
    `getattr(self.model, None)` and raised TypeError. Make the shape
    unrepresentable and it cannot recur."""
    with pytest.raises(ValueError, match="needs a command"):
        sch.dropdown("Port", "port", "", "port_options")


def test_a_button_carries_the_inputs_that_travel_with_it():
    element = sch.button("Move", "move", inputs=("speed", "steps"))
    assert element["inputs"] == ["speed", "steps"], "D-5: a list, so it serialises"


def test_a_button_with_no_inputs_still_declares_the_key():
    assert sch.button("Go", "go")["inputs"] == []


def test_a_toggle_carries_the_args_that_let_one_command_serve_many_toggles():
    element = sch.toggle("Auto", "is_auto", "set_mode", "ON", "OFF",
                         on_args=("auto",), off_args=("idle",))
    assert element["on_args"] == ["auto"] and element["off_args"] == ["idle"]
    assert element["command"] == "set_mode"


def test_a_toggles_resting_role_is_its_off_role():
    element = sch.toggle("Stop", "is_estopped", "toggle_estop", "LATCHED", "STOP",
                         on_role="danger", off_role="danger")
    assert element["on_role"] == element["off_role"] == "danger"
    assert element["role"] == "danger", (
        "a latched estop is not green because it happens to be 'on'")


def test_an_entry_inherits_its_params_declaration():
    element = sch.entry("Speed", "speed",
                        Param("speed", "float", minimum=1.0, maximum=2.0, decimals=1))
    assert element["value_type"] == "float"
    assert (element["min"], element["max"], element["decimals"]) == (1.0, 2.0, 1)


def test_a_readonly_may_borrow_a_params_formatting():
    element = sch.readonly("Speed:", "speed", param=Param("speed", "float", decimals=2))
    assert element["decimals"] == 2 and element["writable"] is False


def test_gates_are_lists_and_absent_when_not_asked_for():
    plain = sch.button("Go", "go")
    gated = sch.button("Go", "go", enabled_when=("idle",), disabled_when=("running",))
    assert "enabled_when" not in plain and "disabled_when" not in plain
    assert gated["enabled_when"] == ["idle"] and gated["disabled_when"] == ["running"]


# -- is_enabled: one implementation, three views ---------------------------

@pytest.mark.parametrize("element,mode,expected", [
    ({}, "running", True),
    ({"disabled_when": ["running"]}, "running", False),
    ({"disabled_when": ["running"]}, "idle", True),
    ({"enabled_when": ["idle"]}, "idle", True),
    ({"enabled_when": ["idle"]}, "running", False),
    ({"enabled_when": ["idle"], "disabled_when": ["idle"]}, "idle", False),
    ({"disabled_when": []}, "idle", True),
    ({"enabled_when": []}, "idle", True),
])
def test_is_enabled_matrix(element, mode, expected):
    assert sch.is_enabled(element, mode) is expected


def test_disabled_when_wins_over_enabled_when():
    element = {"enabled_when": ["idle", "running"], "disabled_when": ["running"]}
    assert sch.is_enabled(element, "running") is False
    assert sch.is_enabled(element, "idle") is True


def test_an_enabled_when_gate_closes_against_a_panel_with_no_mode():
    """Setup-style panels report `mode_name == ""`. `enabled_when` therefore
    gates them off entirely, which is a trap worth pinning rather than
    discovering at the bench."""
    assert sch.is_enabled({"enabled_when": ["idle"]}, "") is False
    assert sch.is_enabled({"disabled_when": ["idle"]}, "") is True


# -- current_text: absent stays absent -------------------------------------

def test_an_unset_value_renders_empty_not_the_string_none():
    """The defect: `str(getattr(model, attr))` gave `"None"`, which both
    desktop dropdowns then offered as a selectable probe."""
    class Model:
        port = None

    assert sch.current_text(Model(), {"model_attr": "port"}) == ""


def test_a_missing_attribute_also_renders_empty():
    assert sch.current_text(object(), {"model_attr": "nope"}) == ""


def test_a_present_value_renders_as_text():
    class Model:
        port = "COM3"

    assert sch.current_text(Model(), {"model_attr": "port"}) == "COM3"


def test_a_false_value_is_not_treated_as_absent():
    class Model:
        flag = False

    assert sch.current_text(Model(), {"model_attr": "flag"}) == "False"


# -- format_region: one wording, three views -------------------------------

def test_an_unset_region_reads_as_not_set():
    assert sch.format_region(None) == "not set"
    assert sch.format_region({}) == "not set"


def test_a_region_reads_as_size_at_origin():
    assert sch.format_region({"width": 640, "height": 480, "left": 10, "top": 20}) \
        == "640x480 at (10, 20)"


def test_a_malformed_region_degrades_instead_of_raising():
    assert sch.format_region({"w": 1}) == "{'w': 1}"
    assert sch.format_region(7) == "7"
