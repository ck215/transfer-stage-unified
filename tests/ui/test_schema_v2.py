"""S10 / RC-7: schema v2 conformance.

Every device's schema is checked against the model it describes. This is the
test the codebase most needed and did not have: the schema is the contract
three renderers work from, and nothing verified that the contract referred to
anything real. `PYSIDE-7` is what that costs — a dropdown carrying
`model_attr` and no `command`, which reached `getattr(self.model, None)` and
raised `TypeError` in one view while rendering harmlessly in another.

The checks run over *every* model, so a schema added later is covered without
anyone remembering to extend this file.
"""
from unittest.mock import MagicMock, patch

import pytest

from model import schema as sch
from model.probes import BaseProbe, StepperProbe, DCProbe, ChuckPositioner
from model.redpercent_system import RedPercentSystem
from model.rotator_system import RotatorSystem
from model.temperature_system import TemperatureSystem


def _build(cls):
    with patch('model.probes.serial'), \
         patch('controller.serial.serial'), \
         patch('model.probes.ErrorPopupManager'), \
         patch('controller.gamepad.ControllerPoller'):
        if issubclass(cls, BaseProbe):
            return cls("SIM", "None")
        if cls is TemperatureSystem:
            return cls(port=None)
        return cls()


ALL_MODELS = [StepperProbe, DCProbe, ChuckPositioner, RedPercentSystem,
              RotatorSystem, TemperatureSystem]


@pytest.fixture(params=ALL_MODELS, ids=lambda c: c.__name__)
def model(request):
    instance = _build(request.param)
    yield instance
    for stop in ("teardown", "shutdown"):
        fn = getattr(instance, stop, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass


def test_the_harness_covers_every_schema_bearing_model():
    """Vacuity guard. Every check below passes on an empty model list."""
    assert len(ALL_MODELS) >= 6
    for cls in ALL_MODELS:
        assert isinstance(getattr(cls, "ui_schema", None), property), (
            f"{cls.__name__} publishes no ui_schema")


def test_every_schema_is_version_2(model):
    assert model.ui_schema.get("version") == 2


def test_every_schema_has_at_least_one_element(model):
    assert list(sch.elements(model.ui_schema)), "an empty schema renders nothing"


def test_every_element_type_is_one_a_renderer_implements(model):
    for element in sch.elements(model.ui_schema):
        assert element["type"] in sch.ELEMENT_TYPES, (
            f"{element['type']!r} is not a type any renderer implements")


def test_every_role_is_one_the_renderers_map(model):
    """`role` replaced raw bg/fg hex, which only Tk could honour."""
    for element in sch.elements(model.ui_schema):
        assert element.get("role", "neutral") in sch.ROLES


def test_no_element_carries_a_raw_colour(model):
    """Colour is a renderer's decision, made from `role`."""
    for element in sch.elements(model.ui_schema):
        assert "bg" not in element and "fg" not in element, (
            f"{element.get('text')} still carries a raw colour")


def test_every_command_exists_on_the_model(model):
    """The check PYSIDE-7 needed and nothing performed."""
    for element in sch.elements(model.ui_schema):
        for key in ("command", "options_command", "data_command",
                    "source_command"):
            name = element.get(key)
            if name is None:
                continue
            assert callable(getattr(model, name, None)), (
                f"{key} {name!r} on {element.get('text')!r} is not a method of "
                f"{type(model).__name__}")


def test_every_model_attr_exists_on_the_model(model):
    for element in sch.elements(model.ui_schema):
        attr = element.get("model_attr")
        if attr is None:
            continue
        assert hasattr(model, attr), (
            f"model_attr {attr!r} on {element.get('text')!r} does not exist")


def test_every_declared_param_exists_in_the_table(model):
    for element in sch.elements(model.ui_schema):
        name = element.get("param")
        if name is None:
            continue
        assert name in model.PARAMS, (
            f"param {name!r} is referenced by the schema and not declared")


def test_every_command_input_is_a_declared_param(model):
    """D-5: a command cannot ask for a field that has no type."""
    for element in sch.elements(model.ui_schema):
        for name in element.get("inputs", []):
            assert name in model.PARAMS, (
                f"{element['command']} declares input {name!r}, which is not "
                "a declared parameter")


def test_an_interactive_dropdown_always_has_a_command(model):
    """PYSIDE-7 stated as an invariant rather than a fixed instance."""
    for element in sch.elements(model.ui_schema):
        if element["type"] == "dropdown":
            assert element.get("command"), (
                f"dropdown {element.get('model_attr')!r} has no command; "
                "PySide would reach getattr(model, None)")


# -- I-7.2: writability is declared, and defaults closed -------------------

def test_readonly_and_toggle_elements_are_never_writable(model):
    """I-3.4 expressed in the schema.

    A toggle that published itself writable is how `/api/set_attr` came to be
    able to arm manual mode without a gamepad or a hardware enable.
    """
    for element in sch.elements(model.ui_schema):
        if element["type"] in ("readonly", "toggle", "button", "dropdown"):
            assert element.get("writable") is False, (
                f"{element.get('text')!r} claims to be writable")


def test_only_entry_elements_are_writable(model):
    for element in sch.elements(model.ui_schema):
        if element.get("writable"):
            assert element["type"] == "entry"


def test_every_entry_declares_a_type(model):
    """The guessing this replaces: `float(value)` on the current contents."""
    for element in sch.elements(model.ui_schema):
        if element["type"] == "entry":
            assert element.get("value_type") in ("int", "float", "text"), (
                f"{element.get('text')!r} has no declared value_type")


# -- enabled_when / disabled_when ------------------------------------------

def test_mode_gates_are_evaluated_the_same_way_for_every_view():
    element = {"type": "button", "disabled_when": ["autonomous", "manual"]}
    assert sch.is_enabled(element, "enabled_idle") is True
    assert sch.is_enabled(element, "autonomous") is False

    element = {"type": "button", "enabled_when": ["monitoring"]}
    assert sch.is_enabled(element, "monitoring") is True
    assert sch.is_enabled(element, "disabled") is False

    assert sch.is_enabled({"type": "button"}, "anything") is True


def test_a_dropdown_cannot_be_declared_without_a_command():
    """The broken shape is not expressible, which is why it cannot recur."""
    with pytest.raises(ValueError):
        sch.dropdown("Source:", "selected", command=None,
                     options_command="options")
