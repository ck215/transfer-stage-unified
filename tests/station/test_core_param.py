"""`station.param` — RC-6, ported from `tests/core/test_typed_params.py`.

The old defect was a *speed hazard*, not a formatting one: `_num(self.
full_speed, 400)` hardcoded BaseProbe's fallback at every call site, so a
DCProbe with an empty box sent the firmware 400 instead of its own 120. The
declaration now owns the fallback, and `parse` refuses instead of
substituting.
"""
import math

import pytest

from station.param import Param

pytestmark = pytest.mark.params


def test_an_unknown_type_is_rejected_at_declaration_time():
    with pytest.raises(ValueError, match="unknown param type"):
        Param("speed", type="number")


def test_int_params_never_render_decimals_whatever_was_asked_for():
    assert Param("n", "int", decimals=4).decimals == 0


def test_the_label_falls_back_to_the_name():
    assert Param("full_speed").label == "full_speed"
    assert Param("full_speed", label="Full speed").label == "Full speed"


@pytest.mark.parametrize("type_name,expected", [("int", True), ("float", True),
                                                ("text", False)])
def test_is_numeric_is_declared_not_inferred_from_the_current_value(type_name, expected):
    assert Param("p", type_name).is_numeric is expected


# -- coerce: lenient, and the fallback is the class's own ------------------

@pytest.mark.parametrize("bad", ["", "  ", "abc", None, "nan", "inf", "-inf", object()])
def test_an_unparseable_value_falls_back_to_this_parameters_own_default(bad):
    """The DCProbe-got-the-stepper's-400 defect, in one assertion."""
    dc_speed = Param("full_speed", "float", default=120.0, maximum=500.0)
    assert dc_speed.coerce(bad) == 120.0


def test_coerce_clamps_into_the_declared_bounds():
    speed = Param("speed", "float", default=100.0, minimum=10.0, maximum=400.0)
    assert speed.coerce(9000) == 400.0
    assert speed.coerce(-5) == 10.0


def test_coerce_returns_an_int_for_an_int_param():
    steps = Param("steps", "int", default=4, minimum=1, maximum=16)
    value = steps.coerce("7.9")
    assert value == 7 and isinstance(value, int)


def test_coerce_never_raises_on_anything_a_widget_can_hold():
    speed = Param("speed", "float", default=1.0)
    for raw in ("", None, [], {}, "1e", float("nan")):
        assert isinstance(speed.coerce(raw), float)


def test_coerce_on_a_text_param_stringifies_and_maps_none_to_empty():
    note = Param("note", "text")
    assert note.coerce(None) == "" and note.coerce(5) == "5"


# -- parse: strict, and the refusal names the field -----------------------

def test_parse_accepts_a_good_value_and_types_it():
    ok, value = Param("steps", "int", default=1, minimum=1).parse(" 6 ")
    assert ok and value == 6 and isinstance(value, int)


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_an_empty_box_is_refused_and_the_refusal_names_the_field(raw):
    ok, reason = Param("full_speed", "float", label="Full speed").parse(raw)
    assert ok is False
    assert "Full speed" in reason and "empty" in reason


def test_a_non_number_is_refused_and_quotes_what_was_typed():
    ok, reason = Param("speed", "float", label="Speed").parse("fast")
    assert ok is False and "Speed" in reason and "'fast'" in reason


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_a_non_finite_number_is_refused_rather_than_sent_to_the_firmware(raw):
    ok, reason = Param("speed", "float", label="Speed").parse(raw)
    assert ok is False and "finite" in reason


def test_parse_refuses_out_of_bounds_instead_of_clamping():
    speed = Param("speed", "float", minimum=10.0, maximum=400.0, label="Speed")
    low_ok, low_reason = speed.parse("5")
    high_ok, high_reason = speed.parse("900")
    assert (low_ok, high_ok) == (False, False)
    assert "at least 10.0" in low_reason and "at most 400.0" in high_reason


def test_parse_on_text_accepts_anything_including_empty():
    ok, value = Param("note", "text").parse("")
    assert ok and value == ""


def test_parse_and_coerce_disagree_on_purpose():
    """`coerce` is for building a frame from what is stored; `parse` is for
    accepting operator input. Substituting silently is the RC-6 defect."""
    speed = Param("speed", "float", default=120.0, minimum=10.0)
    assert speed.coerce("abc") == 120.0
    assert speed.parse("abc")[0] is False


# -- render / to_schema ----------------------------------------------------

def test_rendering_uses_the_declared_precision():
    assert Param("speed", "float", decimals=1).format(3.14159) == "3.1"
    assert Param("speed", "float", decimals=3).format(3.14159) == "3.142"
    assert Param("steps", "int").format(6.9) == "6"


def test_rendering_an_unparseable_stored_value_shows_the_default():
    assert Param("speed", "float", default=120.0, decimals=1).format("") == "120.0"


def test_rendering_round_trips_through_parse_so_a_redraw_is_not_an_edit():
    """`Panel._apply_inputs` compares the rendered stored value with the widget
    text to decide whether a gated field was really edited. If rendering did
    not round-trip, a refresh would look like an edit."""
    speed = Param("speed", "float", default=100.0, decimals=1)
    text = speed.format(100.0)
    ok, value = speed.parse(text)
    assert ok and speed.format(value) == text


def test_to_schema_is_flat_and_carries_what_a_renderer_needs():
    payload = Param("speed", "float", minimum=0.0, maximum=9.0, decimals=2,
                    unit="mm/s").to_schema()
    assert payload == {"param": "speed", "value_type": "float", "min": 0.0,
                       "max": 9.0, "decimals": 2, "unit": "mm/s"}


def test_to_schema_of_an_unbounded_param_says_none_not_zero():
    payload = Param("note", "text").to_schema()
    assert payload["min"] is None and payload["max"] is None


def test_repr_names_the_param():
    assert "speed" in repr(Param("speed"))


def test_a_nan_default_still_produces_a_number_rather_than_raising():
    """Defensive: the fallback path calls `float(self.default)`."""
    value = Param("p", "float", default=float("nan")).coerce("x")
    assert isinstance(value, float) and math.isnan(value)
