"""`model.transfer_map_analysis`: the detector, the force definitions and the
Gaussian process, on synthetic lowering profiles. Pure functions; no model,
no display, no hardware.

A lowering, as the owner described it (2026-09-27): hover at a baseline, the
reflection brightens to a maximum as the tip approaches, then a shadow
overcasts it; pushed further the tip flexes, and eventually breaks.
"""
import math
import random

import numpy
import pytest

from model import transfer_map_analysis as tma


def lowering(n=400, rate=100.0, baseline=20.0, peak=35.0, dip=8.0,
             rise_at=1.5, peak_at=2.0, dip_at=3.0, noise=0.0, seed=1,
             break_at=None, after_break=20.0):
    """A profile: `baseline` until `rise_at`, a linear rise to `peak` at
    `peak_at`, a linear fall to `dip` at `dip_at`, flat after. A break sends
    the red back to `after_break` (the tip leaves the field)."""
    rng = random.Random(seed)
    t, red = [], []
    for i in range(n):
        s = i / rate
        if s < rise_at:
            r = baseline
        elif s < peak_at:
            r = baseline + (peak - baseline) * (s - rise_at) / (peak_at - rise_at)
        elif s < dip_at:
            r = peak + (dip - peak) * (s - peak_at) / (dip_at - peak_at)
        else:
            r = dip
        if break_at is not None and s >= break_at:
            r = after_break
        t.append(s)
        red.append(r + (rng.gauss(0, noise) if noise else 0.0))
    return {"t": t, "red": red}


# -- smoothing and normalising -------------------------------------------------

def test_median5_removes_a_single_sample_spike_and_keeps_length():
    values = [1.0] * 10
    values[5] = 100.0
    out = tma.median5(values)
    assert len(out) == 10 and max(out) == 1.0


def test_median5_of_a_short_profile_is_the_profile():
    assert list(tma.median5([3.0, 1.0])) == [3.0, 1.0]


def test_normalise_maps_min_to_0_and_max_to_1():
    n, low, high = tma.normalise([2.0, 4.0, 6.0])
    assert (low, high) == (2.0, 6.0)
    assert list(n) == [0.0, 0.5, 1.0]


def test_a_flat_profile_cannot_be_normalised():
    n, low, high = tma.normalise([5.0, 5.0, 5.0])
    assert n is None and low == high == 5.0


# -- the detector ---------------------------------------------------------------

def test_detector_finds_the_peak_and_the_dip_of_a_clean_lowering():
    found = tma.detect(lowering())
    assert found["max_t"] == pytest.approx(2.0, abs=0.03)
    assert found["min_t"] >= 3.0 - 0.03
    assert found["red_max"] == pytest.approx(35.0, abs=0.5)   # a 5-sample median rounds the apex
    assert found["red_min"] == pytest.approx(8.0, abs=0.2)
    assert found["baseline"] == pytest.approx(20.0)


def test_detector_ignores_single_sample_spikes_in_noise():
    profile = lowering(noise=0.3)
    profile["red"][50] = 99.0           # one glint while hovering
    profile["red"][320] = -40.0         # one dropped frame in the dip
    found = tma.detect(profile)
    assert found["max_t"] == pytest.approx(2.0, abs=0.05)
    assert found["red_max"] < 40.0 and found["red_min"] > 0.0


def test_the_maximum_is_taken_before_the_operator_mark():
    """A later, higher glint after the Mark is not the approach peak."""
    profile = lowering(n=600)
    for i in range(450, 470):
        profile["red"][i] = 60.0
    found = tma.detect(profile, operator_t=3.5)
    assert found["max_t"] == pytest.approx(2.0, abs=0.03)
    assert found["min_t"] > found["max_t"]


def test_the_minimum_is_after_the_maximum_even_when_the_hover_was_darker():
    profile = lowering(baseline=2.0)
    found = tma.detect(profile)
    assert found["min_t"] > found["max_t"]
    assert found["red_min"] == pytest.approx(8.0, abs=0.2)


def test_a_break_does_not_move_the_peak_and_the_dip_stays_before_the_recovery():
    profile = lowering(n=500, break_at=4.0, after_break=25.0)
    found = tma.detect(profile)
    assert found["max_t"] == pytest.approx(2.0, abs=0.03)
    assert 3.0 - 0.03 <= found["min_t"] < 4.0


def test_the_baseline_is_the_median_of_the_first_second():
    profile = lowering(baseline=20.0)
    profile["red"][10] = 90.0
    assert tma.detect(profile)["baseline"] == pytest.approx(20.0)


def test_detector_answers_none_for_an_empty_or_flat_profile():
    assert tma.detect({"t": [], "red": []}) is None
    flat = tma.detect({"t": [0.0, 0.1, 0.2], "red": [5.0, 5.0, 5.0]})
    assert flat["max_t"] is None and flat["min_t"] is None
    assert flat["baseline"] == 5.0


# -- the force definitions --------------------------------------------------------

def test_the_registry_is_an_open_dict_of_name_to_function():
    assert set(tma.FORCE_DEFINITIONS) >= {
        "shadow_vs_baseline", "shadow_vs_peak", "at_operator_mark",
        "dip_area", "fall_slope"}
    assert all(callable(f) for f in tma.FORCE_DEFINITIONS.values())


def test_force_indices_returns_every_registered_definition():
    out = tma.force_indices(lowering(), {"operator_t": 3.5})
    assert set(out) == set(tma.FORCE_DEFINITIONS)


def test_the_force_formulas_on_a_known_profile():
    profile = lowering(baseline=20.0, peak=35.0, dip=8.0)
    out = tma.force_indices(profile, {"operator_t": 2.5})
    # (baseline - min) / (max - min) = (20 - 8) / (35 - 8)
    assert out["shadow_vs_baseline"] == pytest.approx(12 / 27, abs=0.01)
    # (max - min) / max = 27 / 35
    assert out["shadow_vs_peak"] == pytest.approx(27 / 35, abs=0.01)
    # halfway down the fall: red 21.5, depth (35 - 21.5) / 27 = 0.5
    assert out["at_operator_mark"] == pytest.approx(0.5, abs=0.02)
    # full normalised fall (1.0) over 1 s
    assert out["fall_slope"] == pytest.approx(1.0, rel=0.05)
    # the curve sits below the baseline (12/27) from t=2.44 s to the end at
    # 3.99 s; area = triangle + rectangle, in normalised units x seconds
    nb = 12 / 27
    t_cross = 2.0 + (35 - 20) / 27
    expected = 0.5 * (3.0 - t_cross) * nb + (3.99 - 3.0) * nb
    assert out["dip_area"] == pytest.approx(expected, rel=0.05)


def test_a_deeper_shadow_scores_higher_on_every_definition():
    shallow = tma.force_indices(lowering(dip=18.0), {"operator_t": 3.5})
    deep = tma.force_indices(lowering(dip=4.0), {"operator_t": 3.5})
    for name in ("shadow_vs_baseline", "shadow_vs_peak", "dip_area"):
        assert deep[name] > shallow[name], name


def test_at_operator_mark_is_none_without_a_mark():
    assert tma.force_indices(lowering(), {})["at_operator_mark"] is None


def test_a_flat_profile_gives_no_index_rather_than_a_number():
    flat = {"t": [0.0, 0.1, 0.2, 0.3], "red": [5.0] * 4}
    out = tma.force_indices(flat, {"operator_t": 0.2})
    assert all(value is None for value in out.values()), out


def test_a_definition_that_raises_is_reported_as_none_not_raised():
    registry = dict(tma.FORCE_DEFINITIONS)
    registry["broken"] = lambda ctx: 1 / 0
    out = tma.force_indices(lowering(), {}, definitions=registry)
    assert out["broken"] is None and out["shadow_vs_peak"] is not None


def test_z_past_peak_uses_the_z_column_when_it_was_recorded():
    profile = lowering()
    profile["z"] = [1000 - 10 * i for i in range(len(profile["t"]))]
    out = tma.force_indices(profile, {"operator_t": 2.5})
    # z at 2.0 s is 1000 - 2000 = -1000, at 2.5 s -1500: 500 steps lowered
    assert out["z_past_peak"] == pytest.approx(500, abs=20)
    assert tma.force_indices(lowering(), {"operator_t": 2.5})["z_past_peak"] is None


def test_given_marks_override_the_detector():
    profile = lowering()
    out = tma.force_indices(profile, {"auto_max_t": 2.0, "auto_min_t": 2.5})
    # (35 - 21.5) / 35 with the given dip at 2.5 s
    assert out["shadow_vs_peak"] == pytest.approx(13.5 / 35, abs=0.02)


# -- the Gaussian process -----------------------------------------------------

def test_gp_recovers_a_known_smooth_function_and_its_uncertainty_shrinks_near_data():
    rng = numpy.random.default_rng(3)
    x = rng.uniform(0, 1, size=(40, 2))
    y = numpy.sin(3 * x[:, 0]) + x[:, 1] ** 2
    grid = numpy.array([[0.5, 0.5], [0.2, 0.8], [5.0, 5.0]])
    mean, var = tma.gp_predict(x, y, grid, length=0.3, noise=1e-3)
    truth = numpy.sin(3 * grid[:2, 0]) + grid[:2, 1] ** 2
    assert numpy.allclose(mean[:2], truth, atol=0.05)
    assert var[0] < 0.01 and var[2] > 10 * var[0]


def test_gp_gradient_matches_the_function_and_carries_a_variance():
    rng = numpy.random.default_rng(4)
    x = rng.uniform(0, 1, size=(60, 2))
    y = 2.0 * x[:, 0] - 1.0 * x[:, 1]
    grad, grad_var = tma.gp_gradient(x, y, numpy.array([[0.5, 0.5]]),
                                     length=0.5, noise=1e-4)
    assert grad[0] == pytest.approx([2.0, -1.0], abs=0.15)
    assert numpy.all(grad_var >= 0)


def test_gp_with_one_point_or_none_answers_prior():
    mean, var = tma.gp_predict(numpy.zeros((0, 2)), numpy.zeros(0),
                               numpy.array([[0.0, 0.0]]))
    assert var[0] > 0
