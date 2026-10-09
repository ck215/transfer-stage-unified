"""RAMP-QUICK (owner, 2026-10-08): "have ramp rate as a quick access setting.
Bind to a min value of 1 and a max value of 20."

Pins the bounds, the tier-1 placement right after the Setpoint, what each path
that sets `ramp_rate` does with an out-of-range value, and the tutorial order.
"""
import json
from pathlib import Path

import pytest

from model.heater import Heater
from result import Result

from test_heater_fakes import FakePort

TUTORIAL = (Path(__file__).resolve().parent.parent / "src" / "views" / "web"
            / "static" / "tutorials" / "temperature-controller.json")


@pytest.fixture
def port():
    return FakePort()


@pytest.fixture
def heater(port, tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path))
    model = Heater(port=port)
    yield model
    model._stop_threads()


def _entries(section):
    return [e["model_attr"] for e in section["elements"] if e["type"] == "entry"]


# -- bounds ----------------------------------------------------------------------

def test_ramp_rate_is_bound_to_1_through_20_and_keeps_its_unit_and_default():
    param = Heater.PARAMS["ramp_rate"]
    assert (param.minimum, param.maximum) == (1, 20)
    assert (param.unit, param.decimals, param.default) == ("s/°C", 2, 10)


@pytest.mark.parametrize("raw", ["1", "20", "7.25", "1.00"])
def test_ramp_rate_accepts_the_bounds_and_inside(raw):
    assert Heater.PARAMS["ramp_rate"].parse(raw)[0] is True


@pytest.mark.parametrize("raw", ["0.99", "20.01", "0", "0.5", "25", "3600", "-1"])
def test_ramp_rate_refuses_outside_the_bounds(raw):
    ok, reason = Heater.PARAMS["ramp_rate"].parse(raw)
    assert ok is False and "Ramp Rate" in reason


@pytest.mark.parametrize("raw", ["0", "0.5", "25", "3600"])
def test_enter_settings_refuses_a_typed_ramp_outside_the_bounds(heater, port, raw):
    result = heater.run("apply_settings", inputs={"setpoint": "30", "ramp_rate": raw})
    assert result.status == Result.REFUSED
    assert "Ramp Rate" in result.reason and port.frames == []


@pytest.mark.parametrize("raw", ["1", "20"])
def test_enter_settings_sends_the_bounds(heater, port, raw):
    assert heater.run("apply_settings", inputs={"setpoint": "30", "ramp_rate": raw}).is_ok
    assert port.frames and port.frames[-1].startswith(b"<30,")


@pytest.mark.parametrize("stored", [0, 0.5, 25, 3600])
def test_a_stored_attribute_out_of_range_never_reaches_the_board(heater, port, stored):
    heater.setpoint, heater.ramp_rate = 30.0, stored
    assert heater.run("apply_settings").status == Result.REFUSED
    assert port.frames == []


# -- stored defaults (user / station config) --------------------------------------

@pytest.mark.parametrize("stored", [0, 0.5, 25, 3600, "3600", "abc"])
def test_a_stored_out_of_range_default_is_refused_and_the_default_kept(heater, stored):
    """`Panel.apply_defaults` parses with the Param: refused, not clamped; the
    model keeps its current value (the Param default, 10, at load)."""
    refused = heater.apply_defaults({"ramp_rate": stored, "p_term": 3.0})
    assert "ramp_rate" in refused and "Ramp Rate" in refused["ramp_rate"]
    assert heater.ramp_rate == 10
    assert heater.p_term == 3.0, "the rest of the load is not lost"


def test_a_stored_in_range_default_loads(heater):
    assert heater.apply_defaults({"ramp_rate": 4.5}) == {}
    assert heater.ramp_rate == 4.5


@pytest.mark.parametrize("scope", ["station", "user"])
@pytest.mark.parametrize("value", [10, 3600])
def test_ramp_rate_is_not_a_stored_preference_at_all(scope, value):
    """Neither profile scope can store it (it is in neither USER_PARAMS nor
    STATION_PARAMS), so config files have no path to it; if that ever
    changes, the Param bounds still apply (the test above)."""
    import model.profile as pf
    clean, problems = pf.validate_model_params(
        scope, {Heater.NAME: {"ramp_rate": value}}, lambda name: Heater.PARAMS)
    assert clean == {} and problems


# -- tier placement ----------------------------------------------------------------

def test_ramp_rate_sits_in_tier_one_right_after_the_setpoint(heater):
    sections = heater.schema["sections"]
    tier_one = next(s for s in sections if s["title"] == "Temperature")
    assert tier_one.get("tier", 1) == 1
    entries = _entries(tier_one)
    assert entries[:2] == ["setpoint", "ramp_rate"]
    attrs = [e.get("model_attr") for e in tier_one["elements"]]
    assert attrs.index("ramp_rate") == attrs.index("setpoint") + 1


def test_ramp_rate_is_absent_from_tier_two_and_appears_once(heater):
    sections = heater.schema["sections"]
    tier_two = [s for s in sections if s.get("tier") == 2]
    assert tier_two
    assert all("ramp_rate" not in _entries(s) for s in tier_two)
    every = [e["model_attr"] for s in sections for e in s["elements"]
             if e["type"] == "entry"]
    assert every.count("ramp_rate") == 1
    assert set(every) == set(Heater.FRAME_FIELDS)


def test_the_frame_and_enter_settings_inputs_are_unchanged(heater):
    assert Heater.FRAME_FIELDS == ("setpoint", "ramp_rate", "p_term", "i_term",
                                   "d_term", "offset")
    button = next(e for s in heater.schema["sections"] for e in s["elements"]
                  if e.get("command") == "apply_settings")
    assert tuple(button["inputs"]) == Heater.FRAME_FIELDS


def test_the_entry_schema_carries_the_new_bounds(heater):
    entry = next(e for s in heater.schema["sections"] for e in s["elements"]
                 if e.get("model_attr") == "ramp_rate")
    assert (entry["min"], entry["max"]) == (1, 20)


# -- tutorial ------------------------------------------------------------------------

def _steps():
    return json.loads(TUTORIAL.read_text())["steps"]


def test_the_tutorial_teaches_the_ramp_right_after_the_setpoint():
    anchors = [s["anchor"].get("text") for s in _steps()]
    assert anchors.index("Ramp rate") == anchors.index("Setpoint") + 1
    assert anchors.index("Configure Temperature Controller") == anchors.index("Ramp rate") + 1
    assert anchors.count("Ramp rate") == 1


def test_the_ramp_step_states_the_range_and_configure_no_longer_claims_it():
    steps = {s["anchor"].get("text"): s for s in _steps()}
    assert "1 to 20 seconds per degree" in steps["Ramp rate"]["say"]
    configure = steps["Configure Temperature Controller"]["say"].lower()
    assert "ramp" not in configure
    assert steps["Ramp rate"]["wait"] is None
