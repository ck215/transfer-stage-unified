"""The Sample Map's frame math (flake-coords Phase 0, section 3).

Pure functions on synthetic corners: the rigid frame from A and B, the
handedness from D, the degenerate refusals in the operator's words, the
rectangularity and closure checks, the similarity and affine fits for
typed dimensions, and the extent metrics (Q16: `lateral_um` is the
longest chord). No model, no store, no hardware.
"""
import math

import numpy
import pytest

from model import sample_frame as sf


def _stage(q, theta, origin, k, flip=False):
    """Where a sample-frame point (um) sits in stage counts, for a chip
    rotated by `theta` with corner A at `origin` (counts)."""
    x, y = q
    if flip:
        y = -y
    c, s = math.cos(theta), math.sin(theta)
    return (origin[0] + (c * x - s * y) / k[0], origin[1] + (s * x + c * y) / k[1])


THETA = math.radians(17.0)
ORIGIN = (12_000.0, -3_400.0)
K = (0.625, 0.625)
W, H = 5_000.0, 4_000.0          # a 5 x 4 mm chip, um


def _corners(theta=THETA, origin=ORIGIN, k=K, flip=False, w=W, h=H):
    ideal = {"A": (0, 0), "B": (w, 0), "C": (w, h), "D": (0, h)}
    return {name: _stage(q, theta, origin, k, flip) for name, q in ideal.items()}


# -- 3.2 the rigid frame ------------------------------------------------------

def test_the_rigid_frame_puts_a_at_the_origin_and_b_on_x():
    c = _corners()
    frame = sf.rigid_frame(c["A"], c["B"], K, d=c["D"])
    assert frame.handedness == 1
    assert frame.theta == pytest.approx(THETA)
    for name, ideal in (("A", (0, 0)), ("B", (W, 0)), ("C", (W, H)), ("D", (0, H))):
        assert frame.to_sample(c[name]) == pytest.approx(ideal, abs=1e-6), name


def test_sample_and_stage_round_trip():
    c = _corners()
    frame = sf.rigid_frame(c["A"], c["B"], K, d=c["D"])
    for q in ((123.4, 567.8), (-50.0, 2.5), (W / 2, H / 2)):
        assert frame.to_sample(frame.to_stage(q)) == pytest.approx(q, abs=1e-6)


def test_a_mirrored_stage_flips_y_and_says_so():
    """D on the other side of A->B: the stage frame is left-handed relative
    to the operator's corner order; S keeps +y toward the interior."""
    c = _corners(flip=True)
    frame = sf.rigid_frame(c["A"], c["B"], K, d=c["D"])
    assert frame.handedness == -1
    assert frame.to_sample(c["D"]) == pytest.approx((0, H), abs=1e-6)
    assert frame.to_sample(c["C"]) == pytest.approx((W, H), abs=1e-6)


def test_unequal_um_per_count_per_axis():
    k = (0.625, 1.25)
    c = _corners(k=k)
    frame = sf.rigid_frame(c["A"], c["B"], k, d=c["D"])
    assert frame.to_sample(c["C"]) == pytest.approx((W, H), abs=1e-6)


def test_the_derived_dimensions_and_the_angle_at_a():
    c = _corners()
    dims = sf.dimensions(c["A"], c["B"], c["D"], K)
    assert dims["width_um"] == pytest.approx(W)
    assert dims["height_um"] == pytest.approx(H)
    assert dims["angle_at_a_deg"] == pytest.approx(90.0)


# -- 3.4 degenerate cases, refused in the operator's words ----------------------

def test_a_and_b_too_close_is_refused():
    with pytest.raises(sf.FrameRefused, match="Corners A and B are the same point"):
        sf.rigid_frame((100, 100), (130, 120), K)                 # 22 um
    # The rule is a distance on the chip: 5 mm of typed micrometer reading
    # is far apart, though the numbers differ by 5.
    assert sf.rigid_frame((10.0, 20.0), (15.0, 20.0), (1000.0, 1000.0)).theta == 0


def test_d_nearly_in_line_with_a_and_b_is_refused():
    for angle in (10.0, 170.0):
        a, b = (0.0, 0.0), (8_000.0, 0.0)
        d = (8_000 * math.cos(math.radians(angle)), 8_000 * math.sin(math.radians(angle)))
        with pytest.raises(sf.FrameRefused, match="nearly in line with A: not a corner"):
            sf.rigid_frame(a, b, K, d=d)


def test_the_frame_without_d_is_right_handed_by_default():
    c = _corners()
    frame = sf.rigid_frame(c["A"], c["B"], K)
    assert frame.handedness == 1
    assert frame.to_sample(c["B"]) == pytest.approx((W, 0), abs=1e-6)


# -- 3.2 the checks: rectangularity and closure ------------------------------------

def test_a_true_rectangle_has_no_rectangularity_residual():
    c = _corners()
    assert sf.rectangularity(c, K) == pytest.approx(0.0, abs=1e-3)


def test_a_bent_corner_shows_as_rectangularity():
    c = _corners()
    x, y = c["C"]
    c["C"] = (x + 40.0, y)          # 40 counts = 25 um off
    rms = sf.rectangularity(c, K)
    assert 5.0 < rms < 25.0


def test_rectangularity_needs_four_corners():
    c = _corners()
    del c["C"]
    with pytest.raises(sf.FrameRefused, match="four corners"):
        sf.rectangularity(c, K)


def test_closure_and_its_words():
    assert sf.closure_um((0, 0), (8, 0), K) == pytest.approx(5.0)
    assert sf.closure_word(5.0) == "good"
    assert sf.closure_word(10.0) == "check"
    assert sf.closure_word(29.9) == "check"
    assert sf.closure_word(30.0) == "poor"
    assert sf.rectangularity_asks_quad(sf.RECTANGULARITY_ASK_UM + 1)
    assert not sf.rectangularity_asks_quad(sf.RECTANGULARITY_ASK_UM - 1)


# -- 3.3 typed dimensions: similarity and affine fits ---------------------------

def _ideal(w=W, h=H):
    return [(0, 0), (w, 0), (w, h), (0, h)]


def test_the_affine_fit_recovers_a_skewed_unequal_stage_exactly():
    """The manual rig: unequal, non-orthogonal axes."""
    matrix = numpy.array([[1.62, 0.07], [-0.11, 0.81]])     # counts per um
    shift = numpy.array([500.0, -200.0])
    stage = [tuple(matrix @ q + shift) for q in numpy.array(_ideal(), float)]
    fit = sf.affine_fit(stage, _ideal())
    assert fit.rms_um == pytest.approx(0.0, abs=1e-6)
    assert fit.to_sample(stage[2]) == pytest.approx((W, H), abs=1e-6)
    assert fit.to_stage((W / 2, H / 2)) == pytest.approx(
        tuple(matrix @ numpy.array([W / 2, H / 2]) + shift), abs=1e-6)


def test_the_affine_fit_works_from_three_corners_and_refuses_collinear_ones():
    stage = [_stage(q, THETA, ORIGIN, K) for q in _ideal()[:3]]
    fit = sf.affine_fit(stage, _ideal()[:3])
    assert fit.to_sample(stage[1]) == pytest.approx((W, 0), abs=1e-6)
    with pytest.raises(sf.FrameRefused, match="in a line"):
        sf.affine_fit([(0, 0), (100, 100), (200, 200)], [(0, 0), (1, 0), (2, 0)])
    with pytest.raises(sf.FrameRefused, match="three corners"):
        sf.affine_fit(stage[:2], _ideal()[:2])


def test_the_affine_residual_is_meaningful_with_four_noisy_corners():
    stage = [_stage(q, THETA, ORIGIN, K) for q in _ideal()]
    stage[3] = (stage[3][0] + 16.0, stage[3][1])           # 10 um off
    fit = sf.affine_fit(stage, _ideal())
    assert 1.0 < fit.rms_um < 10.0


def test_the_similarity_fit_finds_the_scale_and_keeps_axes_square():
    stage = [_stage(q, THETA, ORIGIN, (0.5, 0.5)) for q in _ideal()]
    fit = sf.similarity_fit(stage, _ideal())
    assert fit.rms_um == pytest.approx(0.0, abs=1e-6)
    assert fit.um_per_count == pytest.approx(0.5)
    assert fit.to_sample(stage[2]) == pytest.approx((W, H), abs=1e-6)


def test_the_fit_kind_defaults():
    assert sf.default_fit(dimensions_typed=False) == "rigid"
    assert sf.default_fit(dimensions_typed=True) == "affine"


# -- 3.5 extent -------------------------------------------------------------------

def test_the_stage_bounding_box_extent_in_the_sample_frame():
    c = _corners()
    frame = sf.rigid_frame(c["A"], c["B"], K, d=c["D"])
    p1 = frame.to_stage((100.0, 200.0))
    p2 = frame.to_stage((160.0, 240.0))
    box = sf.bbox_extent(frame, p1, p2)
    flat = [v for point in box["polygon_um"] for v in point]
    assert flat == pytest.approx([100.0, 200.0, 160.0, 200.0, 160.0, 240.0,
                                  100.0, 240.0], abs=1e-6)
    assert box["source"] == "stage_corners"      # the record vocabulary (4.1)
    metrics = sf.extent_metrics(box["polygon_um"])
    assert metrics["area_um2"] == pytest.approx(60.0 * 40.0)
    assert metrics["lateral_um"] == pytest.approx(math.hypot(60.0, 40.0))
    assert metrics["aspect_ratio"] == pytest.approx(1.5)


def test_lateral_um_is_the_longest_chord_of_an_irregular_polygon():
    """Q16 (owner 2026-10-04): the dimension people quote is the longest
    chord, not the bounding box."""
    poly = [(0, 0), (10, 0), (12, 5), (3, 9), (-2, 4)]
    metrics = sf.extent_metrics(poly)
    chords = [math.dist(p, q) for p in poly for q in poly]
    assert metrics["lateral_um"] == pytest.approx(max(chords))
    assert metrics["area_um2"] == pytest.approx(abs(sf.shoelace(poly)))
    assert metrics["aspect_ratio"] >= 1.0


def test_extent_metrics_refuse_fewer_than_three_points():
    with pytest.raises(sf.FrameRefused, match="three points"):
        sf.extent_metrics([(0, 0), (1, 1)])


# -- bench facts are explicit placeholders that fail loud --------------------------

def test_the_stepper_scale_is_known_and_the_others_fail_loud():
    assert sf.um_per_count("stepper") == 0.625
    for kind in ("chuck", "dc"):
        with pytest.raises(sf.BenchFactMissing, match="um per count"):
            sf.um_per_count(kind)
    with pytest.raises(sf.BenchFactMissing):
        sf.um_per_count("nonsense")
    assert sf.um_per_count("chuck", typed=0.4) == 0.4    # an operator's typed value


def test_the_quality_thresholds_are_module_constants():
    assert (sf.CLOSURE_GOOD_UM, sf.CLOSURE_CHECK_UM) == (10.0, 30.0)
    assert sf.RECTANGULARITY_ASK_UM == 50.0
    assert sf.MIN_AB_UM == 30.0
    assert (sf.MIN_CORNER_ANGLE_DEG, sf.MAX_CORNER_ANGLE_DEG) == (20.0, 160.0)
