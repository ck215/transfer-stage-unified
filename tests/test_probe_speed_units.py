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

