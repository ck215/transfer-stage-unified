"""Speed units (owner ruling 2026-10-12): the model side.

SU-2 the percent is read and typed at 0.1 %, parsed strictly;
SU-3 the unit each speed entry is shown in (a per-account preference).
The stored speeds stay whole steps/s and the wire is unchanged.
"""
import pytest

from model.probe import ChuckPositioner, DCProbe, StepperProbe
from result import Refused

from tests.test_probe import make_probe


@pytest.fixture
def stepper():
    p, port, pad = make_probe(StepperProbe)
    yield p
    p._stop_threads()


@pytest.fixture
def chuck():
    p, port, pad = make_probe(ChuckPositioner)
    yield p
    p._stop_threads()


# ==========================================================================
# SU-2: percent at 0.1 %, strict parse
# ==========================================================================
@pytest.mark.params
@pytest.mark.parametrize("cls, steps, shown", [
    (StepperProbe, 400, 12.5),       # the default, no longer "13"
    (StepperProbe, 3, 0.1),          # 0.094 %
    (StepperProbe, 1600, 50.0),
    (StepperProbe, 3200, 100.0),
    (ChuckPositioner, 1, 0.2),       # 0.1667 %
    (ChuckPositioner, 400, 66.7),
    (ChuckPositioner, 600, 100.0),
    (DCProbe, 120, 3.8),             # exactly 3.75 %: halves round up
])
def test_su2_the_percent_shows_the_nearest_tenth(cls, steps, shown):
    assert cls.steps_to_pct(steps) == shown


@pytest.mark.params
def test_su2_the_default_dial_reads_12_5_and_the_params_are_tenths():
    for cls in (StepperProbe, ChuckPositioner, DCProbe):
        for name in ("full_speed_pct", "man_full_speed_pct"):
            param = cls.PARAMS[name]
            assert (param.type, param.decimals) == ("float", 1), (cls, name)
            assert (param.minimum, param.maximum, param.default) == (0, 100, 12.5)
    p, _, _ = make_probe(DCProbe)
    try:
        assert (p.full_speed_pct, p.man_full_speed_pct) == (3.8, 3.8)   # 120 of 3200
        p.full_speed = 400
        assert p.state["values"]["full_speed_pct"] == "12.5"
    finally:
        p._stop_threads()


@pytest.mark.params
def test_su2_a_tenth_of_a_percent_is_a_step_on_the_stepper(stepper):
    stepper.full_speed_pct = "0.1"
    assert stepper.full_speed == 3 and stepper.full_speed_pct == 0.1
    stepper.full_speed_pct = "12.5"
    assert stepper.full_speed == 400 and stepper.full_speed_pct == 12.5


@pytest.mark.params
def test_su2_a_tenth_of_a_percent_on_the_chuck_is_the_slowest_it_can_be(chuck):
    chuck.full_speed_pct = "0.1"          # 0.6 steps/s: never below 1
    assert chuck.full_speed == 1 and chuck.full_speed_pct == 0.2


@pytest.mark.params
def test_su2_writing_the_displayed_percent_changes_nothing(stepper, chuck):
    for p, steps in ((stepper, 1000), (chuck, 333)):
        p.full_speed = steps
        shown = p.full_speed_pct
        p.full_speed_pct = shown            # a Step re-sends the readout
        p.full_speed_pct = str(shown)
        assert p.full_speed == steps, (p.NAME, p.full_speed)
    # float noise on the displayed value is still the displayed value
    stepper.full_speed = 1000
    stepper.full_speed_pct = stepper.full_speed_pct + 1e-12
    assert stepper.full_speed == 1000


@pytest.mark.params
@pytest.mark.parametrize("bad", ["", "  ", None, "abc", "nan", "101", "-1"])
def test_su2_a_bad_percent_is_refused_with_the_params_sentence(stepper, bad):
    before = stepper.full_speed
    with pytest.raises(Refused) as why:
        stepper.full_speed_pct = bad
    assert "Autonomous Speed" in str(why.value)
    assert stepper.full_speed == before


@pytest.mark.params
def test_su2_an_empty_percent_box_through_the_commit_path_is_refused(chuck):
    result = chuck.run("_commit", inputs={"man_full_speed_pct": ""})
    assert result.is_refused and "Manual Speed" in result.reason, result
    assert chuck.man_full_speed == 400


@pytest.mark.params
def test_su2_manual_speed_stays_strict_and_live_in_manual(chuck):
    chuck.set_mode("manual")
    assert chuck.run("_commit", inputs={"man_full_speed_pct": "25.5"}).is_ok
    assert chuck.man_full_speed == 153 and chuck.man_full_speed_pct == 25.5
    for bad in ("", "x", "100.1"):
        assert chuck.run("_commit", inputs={"man_full_speed_pct": bad}).is_refused
        assert chuck.man_full_speed == 153
    # the autonomous speed is still locked in manual
    assert chuck.run("_commit", inputs={"full_speed_pct": "20"}).is_refused


# ==========================================================================
# SU-3: the unit each speed entry is shown in
# ==========================================================================
import schema as sch                                  # noqa: E402
from model import profile as pf                      # noqa: E402
from model.probe import Probe                        # noqa: E402

UNITS_OF = {StepperProbe: ("%", "steps/s", "µm/s"),
            DCProbe: ("%", "steps/s"), ChuckPositioner: ("%", "steps/s")}
BOTH = [(c, b) for c in UNITS_OF for b in ("full_speed", "man_full_speed")]


def _id(value):
    return value.__name__ if isinstance(value, type) else value


@pytest.mark.params
def test_su3_each_class_declares_the_units_it_offers():
    assert Probe.SPEED_UNITS == ("%", "steps/s")
    for cls, units in UNITS_OF.items():
        assert cls.SPEED_UNITS == units, cls
        for base in ("full_speed", "man_full_speed"):
            param = cls.PARAMS[base + "_unit"]
            assert (param.type, param.default) == ("text", "%")


@pytest.mark.params
@pytest.mark.parametrize("cls, base", BOTH, ids=_id)
def test_su3_the_default_unit_is_percent_and_each_unit_is_accepted_by_its_class(cls, base):
    p, _, _ = make_probe(cls)
    try:
        assert getattr(p, base + "_unit") == "%"
        for unit in cls.SPEED_UNITS:
            setattr(p, base + "_unit", unit)
            assert getattr(p, base + "_unit") == unit
    finally:
        p._stop_threads()


@pytest.mark.params
@pytest.mark.parametrize("cls", [DCProbe, ChuckPositioner])
def test_su3_a_unit_the_class_does_not_offer_is_refused_in_words(cls):
    p, _, _ = make_probe(cls)
    try:
        with pytest.raises(Refused) as why:
            p.full_speed_unit = "µm/s"
        assert "Autonomous Speed" in str(why.value) and "steps/s" in str(why.value)
        with pytest.raises(Refused):
            p.man_full_speed_unit = "furlongs"
        assert (p.full_speed_unit, p.man_full_speed_unit) == ("%", "%")
    finally:
        p._stop_threads()


@pytest.mark.params
@pytest.mark.parametrize("mode", ["autonomous", "manual"])
def test_su3_the_unit_is_a_display_choice_allowed_in_every_mode(mode, chuck):
    chuck.set_mode(mode)
    chuck.full_speed_unit = "steps/s"
    chuck.man_full_speed_unit = "steps/s"
    assert (chuck.full_speed_unit, chuck.man_full_speed_unit) == ("steps/s", "steps/s")
    # the speed entry itself keeps its gate
    with pytest.raises(Refused):
        chuck.full_speed_pct = 50


@pytest.mark.params
def test_su3_the_two_speeds_choose_their_units_separately(stepper):
    stepper.full_speed_unit = "µm/s"
    assert (stepper.full_speed_unit, stepper.man_full_speed_unit) == ("µm/s", "%")


@pytest.mark.params
def test_su3_the_readout_names_the_units_not_chosen(stepper, chuck):
    assert stepper.full_speed_alt_text == "400 steps/s · 250 µm/s"
    stepper.full_speed_unit = "steps/s"
    assert stepper.full_speed_alt_text == "12.5 % · 250 µm/s"
    stepper.full_speed_unit = "µm/s"
    assert stepper.full_speed_alt_text == "12.5 % · 400 steps/s"
    assert chuck.full_speed_alt_text == "400 steps/s"
    chuck.full_speed_unit = "steps/s"
    assert chuck.full_speed_alt_text == "66.7 %"
    # µm/s is trimmed: 0.625 stays, 250.0 is 250
    stepper.full_speed = 1
    assert stepper.full_speed_alt_text == "0.0 % · 1 steps/s"
    stepper.man_full_speed_unit = "steps/s"
    stepper.man_full_speed = 1
    assert stepper.man_full_speed_alt_text == "0.0 % · 0.625 µm/s"


@pytest.mark.params
def test_su3_two_representations_of_one_speed_in_one_input_set_are_refused(stepper, chuck):
    result = stepper.run("_commit", inputs={"full_speed_pct": "20", "full_speed_um_s": "100"})
    assert result.is_refused and "Autonomous Speed" in result.reason, result
    assert stepper.full_speed == 400
    result = chuck.run("_commit", inputs={"man_full_speed": "100", "man_full_speed_pct": "20"})
    assert result.is_refused and "Manual Speed" in result.reason, result
    assert chuck.man_full_speed == 400
    # one of each speed is fine
    assert chuck.run("_commit", inputs={"full_speed": "300",
                                        "man_full_speed_pct": "50"}).is_ok
    assert (chuck.full_speed, chuck.man_full_speed) == (300, 300)


@pytest.mark.params
def test_su3_the_unit_choice_round_trips_through_a_profile(stepper, chuck):
    assert {"full_speed_unit", "man_full_speed_unit"} <= pf.USER_PARAMS
    stepper.full_speed_unit = "µm/s"
    stepper.man_full_speed_unit = "steps/s"
    kept = pf.current_params(stepper, pf.USER_PARAMS)
    assert kept["full_speed_unit"] == "µm/s" and kept["man_full_speed_unit"] == "steps/s"
    stepper.full_speed_unit = stepper.man_full_speed_unit = "%"
    assert stepper.apply_defaults(kept) == {}
    assert (stepper.full_speed_unit, stepper.man_full_speed_unit) == ("µm/s", "steps/s")
    # a unit this class does not offer is reported refused, not applied
    refused = chuck.apply_defaults(kept)
    assert set(refused) == {"full_speed_unit"}, refused
    assert (chuck.full_speed_unit, chuck.man_full_speed_unit) == ("%", "steps/s")
    # and the rebuild-to-defaults a sign-out does brings it back to %
    assert chuck.apply_defaults(pf.param_defaults(chuck, pf.USER_PARAMS)) == {}
    assert chuck.man_full_speed_unit == "%"


@pytest.mark.params
@pytest.mark.parametrize("mode", ["disabled", "autonomous", "manual"])
def test_su4_the_unit_choice_commits_through_the_ordinary_path_in_every_mode(mode, chuck):
    chuck.set_mode(mode)
    assert chuck.set_value("full_speed_unit", "steps/s").is_ok
    assert chuck.run("_commit", inputs={"man_full_speed_unit": "steps/s"}).is_ok
    assert (chuck.full_speed_unit, chuck.man_full_speed_unit) == ("steps/s", "steps/s")
    assert chuck.state["values"]["full_speed_unit"] == "steps/s"
    # a unit the class does not offer is refused in words, nothing changes
    bad = chuck.run("_commit", inputs={"full_speed_unit": "µm/s"})
    assert bad.is_refused and "Autonomous Speed" in bad.reason
    assert chuck.full_speed_unit == "steps/s"


@pytest.mark.schema
def test_su4_every_unit_has_an_entry_and_the_choice_offers_exactly_those_units():
    for cls, units in UNITS_OF.items():
        p, _, _ = make_probe(cls)
        try:
            for base in ("full_speed", "man_full_speed"):
                choice = next(e for e in sch.elements(p.schema)
                              if e.get("model_attr") == base + "_unit")
                assert choice["options"] == list(units)
                for unit in units:
                    entry = next(e for e in sch.elements(p.schema)
                                 if e["type"] == "entry"
                                 and e["model_attr"] == p.speed_attr(base, unit))
                    assert entry["shown_when"]["values"] == [unit]
        finally:
            p._stop_threads()
