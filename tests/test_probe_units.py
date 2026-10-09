"""Stepper Probe in physical units (owner ruling 2026-10-09).

The operator reads and types um, um/s; the stored Params stay whole counts
(and counts/s) and the wire is unchanged. 0.625 um per count is the
repo's `sample_frame.UM_PER_COUNT["stepper"]`, flagged "lead unmeasured" once.
"""
import struct

import pytest

import schema as sch
from model.probe import ChuckPositioner, DCProbe, StepperProbe
from model.sample_frame import UM_PER_COUNT
from result import Refused

from tests.test_probe import make_probe

UM = UM_PER_COUNT["stepper"]
AXES = "xyz"


@pytest.fixture
def stepper():
    p, port, pad = make_probe(StepperProbe)
    yield p
    p._stop_threads()


def element(p, attr):
    return next(e for e in sch.elements(p.schema) if e.get("model_attr") == attr)


def after(p, attr):
    """The element drawn right after `attr` in its section."""
    for section in p.schema["sections"]:
        names = [e.get("model_attr") for e in section["elements"]]
        if attr in names:
            return section["elements"][names.index(attr) + 1]
    raise AssertionError(attr)


# -- conversions -----------------------------------------------------------
@pytest.mark.params
@pytest.mark.parametrize("um, counts", [
    (0, 0), (0.625, 1), (100, 160), (100.2, 160), (100.4, 161),
    (0.9375, 2),            # exactly 1.5 counts: halves round up
    (0.3125, 1),            # exactly 0.5 counts
    (0.3, 0), (-0.9375, -2), (-100, -160), (1000, 1600),
])
def test_a_typed_distance_becomes_whole_counts(stepper, um, counts):
    stepper.x_dist_um = um
    assert stepper.x_dist == counts and isinstance(stepper.x_dist, int)
    # the readout is the ACHIEVED value after rounding, not what was typed
    assert stepper.x_dist_um == pytest.approx(counts * UM)


@pytest.mark.params
def test_the_um_view_round_trips_through_counts(stepper):
    for axis in AXES:
        setattr(stepper, f"{axis}_dist_um", "250")
        assert getattr(stepper, f"{axis}_dist") == 400
        assert getattr(stepper, f"{axis}_dist_um") == 250.0
        setattr(stepper, f"{axis}_dist_um", getattr(stepper, f"{axis}_dist_um"))
        assert getattr(stepper, f"{axis}_dist") == 400   # re-sending changes nothing


@pytest.mark.params
def test_step_sizes_and_speeds_are_views_over_the_stored_counts(stepper):
    assert (stepper.x_step, stepper.full_speed, stepper.man_full_speed) == (1, 400, 400)
    assert stepper.x_step_um == pytest.approx(0.625)
    assert stepper.full_speed_um_s == pytest.approx(250.0)
    stepper.y_step_um = 10
    stepper.full_speed_um_s = 500
    stepper.man_full_speed_um_s = "125.2"
    assert (stepper.y_step, stepper.full_speed, stepper.man_full_speed) == (16, 800, 200)
    assert stepper.man_full_speed_um_s == pytest.approx(125.0)


@pytest.mark.params
def test_bounds_are_the_counts_bounds_in_um(stepper):
    for attr in ("x_step_um", "full_speed_um_s", "man_full_speed_um_s"):
        with pytest.raises(Refused):
            setattr(stepper, attr, 0.3)          # below one count
    with pytest.raises(Refused):
        stepper.full_speed_um_s = UM * StepperProbe.MAX_SPEED + 1
    stepper.full_speed_um_s = UM * StepperProbe.MAX_SPEED
    assert stepper.full_speed == StepperProbe.MAX_SPEED
    with pytest.raises(Refused):
        stepper.x_dist_um = "abc"
    assert stepper.x_dist == 0


# -- what is displayed -----------------------------------------------------
@pytest.mark.schema
def test_position_reads_in_um_with_counts_beneath(stepper):
    stepper._position = (1600, -800, 3)
    values = stepper.state["values"]
    assert values["position_x_um"] == "1000.000"
    assert values["position_y_um"] == "-500.000"
    assert values["position_z_um"] == "1.875"
    assert (values["position_x"], values["position_z"]) == ("1600", "3")
    for axis in AXES:
        main = element(stepper, f"position_{axis}_um")
        small = after(stepper, f"position_{axis}_um")
        assert main["unit"] == "um" and main.get("rail")
        assert small["model_attr"] == f"position_{axis}"
        assert small["secondary"] and small["unit"] == "steps"


@pytest.mark.schema
def test_every_physical_entry_has_its_count_line_directly_under_it(stepper):
    pairs = {
        "x_dist_um": ("x_dist", "um", "steps"), "y_dist_um": ("y_dist", "um", "steps"),
        "z_dist_um": ("z_dist", "um", "steps"),
        "x_step_um": ("x_step", "um", "steps"), "y_step_um": ("y_step", "um", "steps"),
        "z_step_um": ("z_step", "um", "steps"),
        "full_speed_um_s": ("full_speed", "um/s", "steps/s"),
        "man_full_speed_um_s": ("man_full_speed", "um/s", "steps/s"),
    }
    for attr, (counts, unit, small_unit) in pairs.items():
        main, small = element(stepper, attr), after(stepper, attr)
        assert main["type"] == "entry" and main["writable"] and main["unit"] == unit
        assert small["type"] == "readonly" and small["secondary"]
        assert small["model_attr"] == counts and small["unit"] == small_unit
    state = stepper.state["values"]
    assert state["x_dist_um"] == "0.000" and state["x_dist"] == "0"
    assert state["full_speed_um_s"] == "250.000" and state["full_speed"] == "400"
    # no count entry and no percent dial is drawn any more
    entries = {e["model_attr"] for e in sch.elements(stepper.schema)
               if e["type"] == "entry"}
    assert entries == set(pairs) | {e for e in entries if e not in pairs}
    assert not entries & {"x_dist", "x_step", "full_speed_pct", "man_full_speed_pct"}
    assert sorted(entries - {"gamepad_name"}) == sorted(
        n for n in StepperProbe.ENTRY_PARAMS)


@pytest.mark.schema
def test_lead_unmeasured_is_flagged_once(stepper):
    notes = [e for e in sch.elements(stepper.schema)
             if "unmeasured" in str(stepper.state["values"].get(e.get("model_attr"), ""))]
    assert len(notes) == 1
    text = stepper.state["values"][notes[0]["model_attr"]]
    assert "0.625" in text and "lead unmeasured" in text
    assert "unmeasured" not in " ".join(
        str(e.get("text", "")) for e in sch.elements(stepper.schema))


@pytest.mark.schema
def test_the_other_probes_are_untouched():
    for cls in (DCProbe, ChuckPositioner):
        p, _, _ = make_probe(cls)
        attrs = {e.get("model_attr") for e in sch.elements(p.schema)}
        assert {"x_dist", "x_step", "full_speed_pct", "position_x"} <= attrs
        assert not any(a and a.endswith(("_um", "_um_s")) for a in attrs)
        p._stop_threads()


@pytest.mark.schema
def test_gates_are_those_of_the_count_fields(stepper):
    for attr in ("x_dist_um", "x_step_um", "full_speed_um_s"):
        assert element(stepper, attr)["disabled_when"] == ["autonomous", "manual"]
    assert element(stepper, "man_full_speed_um_s")["disabled_when"] == ["autonomous"]
    stepper.set_mode("autonomous")
    with pytest.raises(Refused):
        stepper.x_dist_um = 100
    assert stepper.x_dist == 0
    assert stepper.run("_commit", inputs={"x_step_um": "5"}).is_refused
    # the value already held is not an edit
    assert stepper.run("_commit", inputs={"x_dist_um": "0"}).is_ok
    with pytest.raises(Refused):
        stepper.man_full_speed_um_s = 500
    stepper.set_mode("disabled")
    stepper.set_mode("manual")
    stepper.man_full_speed_um_s = 500          # live while jogging
    assert stepper.man_full_speed == 800


@pytest.mark.params
def test_a_physical_input_travels_with_the_step_command(stepper):
    result = stepper.run("step", inputs={"x_dist_um": "100", "y_dist_um": "-50",
                                         "z_dist_um": "0", "full_speed_um_s": "500"})
    assert result.is_ok, result
    assert (stepper.x_dist, stepper.y_dist, stepper.full_speed) == (160, -80, 800)


# -- the wire does not change ------------------------------------------------
@pytest.mark.params
def test_the_move_frame_is_byte_identical_to_the_count_route():
    a, port_a, _ = make_probe(StepperProbe)
    b, port_b, _ = make_probe(StepperProbe)
    a.x_dist_um, a.y_dist_um, a.z_dist_um = 100, -50, 0.625
    a.x_step_um, a.full_speed_um_s = 10, 500
    b.x_dist, b.y_dist, b.z_dist = 160, -80, 1
    b.x_step, b.full_speed = 16, 800
    assert a._frame_bytes(a._frame()) == b._frame_bytes(b._frame())
    assert a._frame_bytes(a._frame()) == b"16,1,1,0,800.0,0,0,160.0,-80.0,1.0,0,0\n"
    for p in (a, b):
        p._stop_threads()


@pytest.mark.params
def test_the_jog_packet_is_byte_identical_to_the_count_route():
    a, _, _ = make_probe(StepperProbe)
    b, _, _ = make_probe(StepperProbe)
    a.x_step_um, a.y_step_um, a.z_step_um = 10, 5, 2.5
    a.man_full_speed_um_s = 125
    b.x_step, b.y_step, b.z_step, b.man_full_speed = 16, 8, 4, 200
    levels = {"axis_x": 0.5, "hat_x": 1}
    assert a._jog_bytes(levels) == b._jog_bytes(levels)
    assert struct.unpack("<BBffffffffff", a._jog_bytes(levels))[5:8] == (16.0, 8.0, 4.0)
    assert a.man_full_speed == 200
    for p in (a, b):
        p._stop_threads()


@pytest.mark.params
def test_stored_params_and_their_names_are_unchanged(stepper):
    for name in ("x_step", "y_step", "z_step", "x_dist", "y_dist", "z_dist",
                 "full_speed", "man_full_speed", "slow_speed", "brake_distance"):
        assert name in stepper.PARAMS
    assert StepperProbe.PARAMS["full_speed"].unit == "steps/s"
    assert stepper._defaults().get("x_dist") == 0
    assert not {n for n in stepper._defaults() if n.endswith(("_um", "_um_s"))}
