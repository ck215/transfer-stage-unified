"""`model.estimators` (the bank of per-frame estimators over crops of the
capture region, owner ruling 2026-10-07) and `dev/estimators_offline.py`.

Every estimator is checked against a frame whose answer is known by
construction; the bank is checked against the registry it is a fast path of,
and against `RgbAnalysis._measure_rgb` for the numbers the run already logs.
"""
import threading
import time

import numpy
import pytest

from model import estimators as E
from model.rgb_analysis import RgbAnalysis


def solid(rgb, height=8, width=12):
    frame = numpy.empty((height, width, 3), dtype=numpy.uint8)
    frame[:, :] = rgb
    return frame


def random_frame(shape=(204, 460), seed=1):
    rng = numpy.random.default_rng(seed)
    return rng.integers(0, 256, (*shape, 3), dtype=numpy.uint8)


def agree(a, b):
    return (numpy.isnan(a) and numpy.isnan(b)) or abs(a - b) < 1e-9


# ---------------------------------------------------------------------
# estimators on frames with known answers
# ---------------------------------------------------------------------

def test_the_registries_name_every_crop_and_estimator():
    assert set(E.CROPS) == set(E.CROP_NAMES)
    assert set(E.ESTIMATORS) == set(E.ESTIMATOR_NAMES)
    assert len(E.KEYS) == len(E.CROP_NAMES) * len(E.ESTIMATOR_NAMES)
    assert "full.red_share" in E.KEYS and "custom.luma_mean" in E.KEYS


@pytest.mark.parametrize("colour,expected", [
    ((200, 0, 0), {"red_share": 100.0, "green_share": 0.0, "blue_share": 0.0}),
    ((0, 200, 0), {"red_share": 0.0, "green_share": 100.0, "blue_share": 0.0}),
    ((0, 0, 200), {"red_share": 0.0, "green_share": 0.0, "blue_share": 100.0}),
    ((120, 140, 60), {"red_share": 0.0, "green_share": 0.0, "blue_share": 0.0}),
])
def test_the_shares_of_a_solid_frame(colour, expected):
    frame = solid(colour)
    for name, value in expected.items():
        assert E.ESTIMATORS[name](frame) == pytest.approx(value)


def test_a_share_counts_the_pixels_its_mask_passes():
    frame = solid((120, 140, 60), height=10, width=10)
    frame[:3, :] = (200, 0, 0)               # three red rows of ten: 30 %
    frame[9, :5] = (0, 200, 0)               # five green pixels: 5 %
    frame[8, :2] = (0, 0, 200)               # two blue pixels: 2 %
    assert E.red_share(frame) == pytest.approx(30.0)
    assert E.green_share(frame) == pytest.approx(5.0)
    assert E.blue_share(frame) == pytest.approx(2.0)


def test_the_masks_are_strict_at_the_thresholds():
    r_min = RgbAnalysis.PARAMS["red_min"].default
    at = solid((r_min, 0, 0))
    over = solid((r_min + 1, 0, 0))
    assert E.red_share(at) == 0.0 and E.red_share(over) == 100.0
    g_at = solid((0, RgbAnalysis.GREEN_MIN, 0))
    assert E.green_share(g_at) == 0.0


def test_the_limits_are_the_models_not_copies():
    limits = E.default_limits()
    assert (limits.r_min, limits.g_max, limits.b_max) == (
        RgbAnalysis.PARAMS["red_min"].default, RgbAnalysis.GREEN_MAX,
        RgbAnalysis.BLUE_MAX)
    assert (limits.g_min, limits.b_min, limits.r_max) == (
        RgbAnalysis.GREEN_MIN, RgbAnalysis.BLUE_MIN, RgbAnalysis.RED_MAX)


def test_the_means_luma_and_red_minus_green():
    frame = solid((200, 100, 50))
    assert E.r_mean(frame) == 200.0
    assert E.g_mean(frame) == 100.0
    assert E.b_mean(frame) == 50.0
    assert E.luma_mean(frame) == pytest.approx(0.299 * 200 + 0.587 * 100
                                               + 0.114 * 50)
    assert E.red_minus_green_mean(frame) == 100.0


def test_a_mean_is_over_every_pixel():
    frame = solid((0, 0, 0), height=2, width=2)
    frame[0, 0] = (255, 40, 0)
    assert E.r_mean(frame) == pytest.approx(255 / 4)
    assert E.g_mean(frame) == pytest.approx(10.0)


def test_the_shade_is_the_median_green_as_the_lab_takes_it():
    frame = random_frame((31, 47))
    for crop in (frame, E.crop_right_half(frame)):
        assert E.shade_g_median(crop) == float(numpy.median(crop[:, :, 1]))
    even = solid((0, 0, 0), height=2, width=2)
    even[:, :, 1] = [[10, 20], [30, 250]]
    assert E.shade_g_median(even) == 25.0          # the two middle values
    odd = solid((0, 0, 0), height=1, width=3)
    odd[0, :, 1] = [5, 90, 7]
    assert E.shade_g_median(odd) == 7.0


def test_the_shade_ignores_a_few_hot_pixels():
    frame = solid((0, 100, 0), height=20, width=20)
    frame[0, :3, 1] = 255
    assert E.shade_g_median(frame) == 100.0


def test_an_empty_crop_is_nan_for_every_estimator():
    empty = numpy.zeros((0, 5, 3), dtype=numpy.uint8)
    for fn in E.ESTIMATORS.values():
        assert numpy.isnan(fn(empty))


def test_the_red_share_and_means_are_the_ones_the_run_logs():
    """The bank's full-frame numbers are `_measure_rgb`'s: the red share to
    the bit, the green and blue shares and the means beside it."""
    frame = random_frame((40, 60), seed=3)
    frame[:10, :20] = (220, 20, 20)
    model = RgbAnalysis.__new__(RgbAnalysis)      # the measure reads class constants only
    logged = RgbAnalysis._measure_rgb(model, frame,
                                      {"r_min": 150, "g_max": 100, "b_max": 100})
    bank = E.EstimatorBank()
    bank.update(0.0, frame)
    row = bank.latest()
    names = ("red_share", "green_share", "blue_share", "r_mean", "g_mean",
             "b_mean")
    for number, name in zip(logged, names):
        assert row[f"full.{name}"] == pytest.approx(number, abs=1e-9)


def test_a_different_red_min_reaches_the_red_share_only():
    frame = solid((180, 0, 0))
    bank = E.EstimatorBank()
    bank.update(0.0, frame)
    bank.update(1.0, frame, red_min=200)
    default, strict = bank.series(["full.red_share", "full.r_mean"],
                                  normalise=None)["full.red_share"]
    assert (default, strict) == (100.0, 0.0)


# ---------------------------------------------------------------------
# crops
# ---------------------------------------------------------------------

def test_the_crops_are_the_stated_rectangles():
    frame = random_frame((10, 21))
    assert E.crop_full(frame) is frame
    assert numpy.array_equal(E.crop_right_half(frame), frame[:, 10:])
    assert numpy.array_equal(E.crop_left_half(frame), frame[:, :10])
    centre = E.crop_centre(frame)
    assert numpy.array_equal(centre, frame[2:8, 5:16])
    # the middle half in both axes
    big = random_frame((204, 460))
    assert E.crop_centre(big).shape[:2] == (102, 230)
    assert E.CROPS["right_half"](big).shape[1] == 230


def test_the_custom_crop_is_the_clipped_rectangle_or_none():
    frame = random_frame((10, 20))
    assert E.crop_custom(frame) is None
    assert E.crop_custom(frame, (2, 3, 5, 4)).shape[:2] == (4, 5)
    assert numpy.array_equal(E.crop_custom(frame, (2, 3, 5, 4)),
                             frame[3:7, 2:7])
    assert E.crop_custom(frame, (15, 8, 50, 50)).shape[:2] == (2, 5)  # clipped
    assert E.crop_custom(frame, (30, 0, 5, 5)) is None      # outside
    assert E.crop_custom(frame, (0, 0, 0, 5)) is None       # no width


# ---------------------------------------------------------------------
# the bank
# ---------------------------------------------------------------------

@pytest.mark.parametrize("shape", [(204, 460), (7, 9), (5, 5), (3, 3), (1, 1),
                                   (4, 8)])
def test_the_bank_is_the_registry_on_every_crop(shape):
    frame = random_frame(shape, seed=sum(shape))
    rect = (1, 1, 3, 2)
    bank = E.EstimatorBank(custom=rect)
    assert bank.update(0.0, frame)
    row = bank.latest()
    for crop in E.CROP_NAMES:
        piece = E.crop_custom(frame, rect) if crop == "custom" else E.CROPS[crop](frame)
        for name, fn in E.ESTIMATORS.items():
            expected = float("nan") if piece is None else fn(piece)
            assert agree(row[f"{crop}.{name}"], expected), (shape, crop, name)


def test_an_unused_custom_crop_is_none_in_the_series():
    bank = E.EstimatorBank()
    bank.update(0.0, solid((10, 20, 30)))
    out = bank.series(["custom.r_mean", "full.r_mean"], normalise=None)
    assert out["custom.r_mean"] == [None]
    assert out["full.r_mean"] == [10.0]


def test_a_new_custom_rectangle_drops_the_old_rectangles_values():
    bank = E.EstimatorBank(custom=(0, 0, 4, 4))
    bank.update(0.0, solid((10, 20, 30)))
    assert bank.series(["custom.r_mean"], normalise=None)["custom.r_mean"] == [10.0]
    gen = bank.generation
    bank.set_custom((1, 1, 2, 2))
    assert bank.generation > gen
    assert bank.series(["custom.r_mean"], normalise=None)["custom.r_mean"] == [None]
    bank.update(1.0, solid((40, 20, 30)))
    assert bank.series(["custom.r_mean"], normalise=None)["custom.r_mean"] == [None, 40.0]
    bank.set_custom(None)
    assert bank.custom is None


def test_latest_is_none_before_a_frame_and_the_last_row_after():
    bank = E.EstimatorBank()
    assert bank.latest() is None
    bank.update(1.5, solid((10, 0, 0)))
    bank.update(2.5, solid((20, 0, 0)))
    row = bank.latest()
    assert row["t"] == 2.5 and row["full.r_mean"] == 20.0
    assert set(row) == {"t", *E.KEYS}


def test_a_frame_that_is_not_a_picture_stores_nothing():
    bank = E.EstimatorBank()
    for bad in (None, numpy.zeros((4, 4)), numpy.zeros((0, 4, 3), numpy.uint8),
                "frame"):
        assert bank.update(0.0, bad) is False
    assert len(bank) == 0


def test_the_ring_never_exceeds_its_capacity():
    bank = E.EstimatorBank(seconds=2.0)
    assert bank.capacity == 40                         # 2 s x 20 Hz
    ring = bank._rows
    frame = solid((10, 20, 30), height=4, width=4)
    for i in range(1000):
        bank.update(i * 0.05, frame)
        assert len(bank) <= bank.capacity
    assert len(bank) == bank.capacity
    assert bank._rows is ring and len(ring) == bank.capacity   # never grew
    out = bank.series(["full.r_mean"], seconds=1e9, normalise=None)
    assert len(out["t"]) == bank.capacity
    assert out["t"][-1] == pytest.approx(49.95)         # newest kept
    assert out["t"][0] == pytest.approx(49.95 - 39 * 0.05)  # oldest dropped


def test_the_default_capacity_is_a_minute_at_twenty_hertz():
    assert E.EstimatorBank().capacity == 1200


def test_reset_forgets_every_row():
    bank = E.EstimatorBank()
    bank.update(0.0, solid((1, 2, 3)))
    bank.reset()
    assert len(bank) == 0 and bank.latest() is None
    assert bank.series(["full.r_mean"])["t"] == []


def ramp_bank(values, step=0.1):
    """A bank fed solid frames whose green is `values[i]` at `i * step`."""
    bank = E.EstimatorBank(seconds=60.0)
    for i, value in enumerate(values):
        bank.update(i * step, solid((10, value, 10), height=4, width=4))
    return bank


def test_raw_series_are_the_measured_values():
    bank = ramp_bank([100, 110, 120])
    out = bank.series(["full.g_mean", "full.shade_g_median"], normalise=None)
    assert out["t"] == [0.0, 0.1, 0.2]
    assert out["full.g_mean"] == [100.0, 110.0, 120.0]
    assert out["full.shade_g_median"] == [100.0, 110.0, 120.0]


def test_a_normalised_series_starts_at_zero_and_scales_to_its_own_span():
    # 1 s of baseline at 100, then 2 s climbing to 200 (a peak held for many
    # frames, so the 95th percentile of the departure is the peak itself)
    values = [100] * 11 + [100 + (i + 1) * 10 for i in range(10)] + [200] * 40
    bank = ramp_bank(values)
    out = bank.series(["full.g_mean"], normalise="baseline")["full.g_mean"]
    assert out[0] == 0.0 and out[10] == 0.0
    assert out[-1] == pytest.approx(1.0)
    assert max(out) == pytest.approx(1.0)
    assert min(out) == 0.0


def test_normalising_puts_different_units_on_one_axis():
    bank = E.EstimatorBank()
    for i in range(50):
        level = 0.0 if i < 12 else 1.0                 # a step after 1.2 s
        frame = solid((10, int(100 + 100 * level), 10), height=4, width=4)
        frame[:2] = (200, 99, 0) if level else (10, 100, 10)
        bank.update(i * 0.1, frame)
    out = bank.series(["full.g_mean", "full.red_share"])
    for key in out:
        if key != "t":
            assert min(out[key]) >= -1.0 and max(out[key]) == pytest.approx(1.0)
            assert out[key][0] == 0.0


def test_a_series_that_never_moves_normalises_to_zeros():
    bank = ramp_bank([100] * 20)
    assert bank.series(["full.g_mean"])["full.g_mean"] == [0.0] * 20


def test_the_baseline_is_the_median_of_the_first_second_in_the_window():
    # 1 s of 100 then a long 200. In a window that starts after the step the
    # baseline is the 200 and the series is flat; before it, it is 100.
    bank = ramp_bank([100] * 11 + [200] * 200)
    whole = bank.series(["full.g_mean"], seconds=1e9)["full.g_mean"]
    assert whole[0] == 0.0 and whole[-1] == pytest.approx(1.0)
    late = bank.series(["full.g_mean"], seconds=5.0)["full.g_mean"]
    assert set(late) == {0.0}


def test_a_single_bad_frame_does_not_set_the_scale():
    values = [100] * 11 + [150] * 200
    values[100] = 0                                     # one glitch frame
    out = ramp_bank(values).series(["full.g_mean"])["full.g_mean"]
    assert out[-1] == pytest.approx(1.0, abs=0.05)      # the plateau stays ~1
    assert min(out) < -1.0                              # the glitch overshoots


def test_the_window_is_the_last_seconds_before_the_newest_row():
    bank = ramp_bank(list(range(100, 160)))
    out = bank.series(["full.g_mean"], seconds=1.0, normalise=None)
    assert out["t"][0] >= 5.9 - 1.0 - 1e-9 and out["t"][-1] == pytest.approx(5.9)
    assert len(out["t"]) == 11


def test_max_points_thins_every_series_evenly_and_keeps_the_newest():
    bank = E.EstimatorBank(seconds=60.0)
    for i in range(1000):
        bank.update(i * 0.05, solid((10, i % 256, 10), height=2, width=2))
    out = bank.series(["full.g_mean", "full.r_mean"], seconds=1e9,
                      max_points=100)
    assert 50 < len(out["t"]) <= 100
    assert len(out["full.g_mean"]) == len(out["full.r_mean"]) == len(out["t"])
    assert out["t"][-1] == pytest.approx(49.95)
    assert out["t"] == sorted(out["t"])


def test_series_rejects_an_unknown_key_or_mode():
    bank = ramp_bank([1, 2, 3])
    with pytest.raises(ValueError):
        bank.series(["full.nonsense"])
    with pytest.raises(ValueError):
        bank.series(["nowhere.r_mean"])
    with pytest.raises(ValueError):
        bank.series(["full.r_mean"], normalise="zscore")


def test_the_bank_is_safe_to_read_while_it_is_written():
    bank = E.EstimatorBank(seconds=5.0)
    frame = random_frame((20, 30))
    stop = threading.Event()
    errors = []

    def writer():
        i = 0
        while not stop.is_set():
            bank.update(i * 0.01, frame)
            i += 1

    def reader():
        try:
            for _ in range(200):
                out = bank.series(["full.g_mean", "centre.red_share"])
                assert len(out["t"]) == len(out["full.g_mean"])
                bank.latest()
        except Exception as exc:                          # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader),
               threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads[1:]:
        t.join()
    stop.set()
    threads[0].join()
    assert errors == []
    assert len(bank) <= bank.capacity


# ---------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------

def test_the_whole_bank_costs_about_a_millisecond_on_a_region_frame():
    frame = random_frame((204, 460))
    bank = E.EstimatorBank(custom=(10, 10, 100, 50))
    bank.update(0.0, frame)
    started = time.perf_counter()
    count = 200
    for i in range(count):
        bank.update(i * 0.05, frame)
    each = (time.perf_counter() - started) / count
    print(f"bank update (4 crops + custom, 460x204): {each * 1e3:.3f} ms")
    # Measured ~0.6 ms; the bound is loose enough for a busy machine, tight
    # enough to catch a per-crop median or a Python pixel loop (> 10 ms).
    assert each < 0.005


# ---------------------------------------------------------------------
# the picture
# ---------------------------------------------------------------------

def test_render_png_draws_the_curves_or_nothing():
    assert E.render_png({"t": []}) == b""
    assert E.render_png({"t": [0.0, 1.0]}) == b""          # no curve
    bank = ramp_bank([100] * 11 + [200] * 20)
    series = bank.series(["full.g_mean", "centre.shade_g_median"])
    png = E.render_png(series, title="t")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
