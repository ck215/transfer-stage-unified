"""The data finalizer's rules (model/finalize.py): which trials still need
data, in what order, and what a typed estimate may be."""
import pytest

from model import finalize as fin

METHODS = ("capture_px", "reticle", "vendor_tool", "estimate")


def row(i, **kw):
    base = {"id": i, "sample_id": "S1", "chip_id": "1", "flake_id": "2",
            "cut_id": str(i), "status": "recorded", "invalid": 0,
            "width_um": None, "thickness_nm": None, "channel_height_nm": None,
            "trench_depth_nm": None, "width_optical_um": None}
    base.update(kw)
    return base


def test_missing_names_every_unfilled_property_in_order():
    assert fin.missing(row(1)) == ["width_um", "thickness_nm", "channel_height_nm",
                                   "trench_depth_nm", "width_optical_um"]
    full = row(1, width_um=1.0, thickness_nm=5, channel_height_nm=3,
               trench_depth_nm=2, width_optical_um=1.1)
    assert fin.missing(full) == []
    assert fin.missing(row(1, width_um=1.0)) == [
        "thickness_nm", "channel_height_nm", "trench_depth_nm", "width_optical_um"]


def test_queue_orders_by_sample_chip_flake_then_trial_and_skips_nothing_valid():
    rows = [row(3, sample_id="B"), row(1, sample_id="A", flake_id="9"),
            row(2, sample_id="A", flake_id="10"), row(4, sample_id="A", flake_id="9")]
    ids = [t["id"] for t in fin.queue(rows)]
    assert ids == [1, 4, 2, 3]       # A/flake 9, A/flake 10 (numeric), then B


def test_queue_hides_invalid_and_aborted_unless_asked():
    rows = [row(1), row(2, invalid=1), row(3, status="aborted")]
    assert [t["id"] for t in fin.queue(rows)] == [1]
    assert [t["id"] for t in fin.queue(rows, include_all=True)] == [1, 2, 3]


def test_queue_can_show_only_trials_with_something_missing():
    done = row(1, width_um=1, thickness_nm=1, channel_height_nm=1,
               trench_depth_nm=1, width_optical_um=1)
    rows = [done, row(2)]
    assert [t["id"] for t in fin.queue(rows, only_missing=True)] == [2]
    assert [t["id"] for t in fin.queue(rows)] == [1, 2]


def test_each_queue_entry_says_where_it_sits_in_its_sample():
    rows = [row(1), row(2), row(3, sample_id="Z")]
    queue = fin.queue(rows)
    assert [(t["sample_position"], t["sample_count"]) for t in queue] == [(1, 2), (2, 2), (1, 1)]


def test_parse_keeps_typed_numbers_and_drops_blanks():
    got = fin.parse_fields({"width_um": "1.8", "width_sigma_um": "", "thickness_nm": " 12 ",
                            "width_optical_um": "2.1", "width_optical_method": "reticle",
                            "note": "ok", "chip_id": "7"}, METHODS)
    assert got == {"width_um": 1.8, "thickness_nm": 12.0, "width_optical_um": 2.1,
                   "width_optical_method": "reticle", "note": "ok", "chip_id": "7"}


@pytest.mark.parametrize("bad", [{"width_um": "0"}, {"width_um": "-1"},
                                 {"width_um": "abc"}, {"width_sigma_um": "-0.1"},
                                 {"thickness_nm": "nan"}, {"width_optical_um": "inf"},
                                 {"width_optical_method": "ruler"},
                                 {"nonsense": "1"}, {"id": "9"}])
def test_parse_refuses_what_cannot_be_an_estimate(bad):
    with pytest.raises(ValueError):
        fin.parse_fields(bad, METHODS)


def test_a_sigma_without_its_value_is_refused():
    with pytest.raises(ValueError, match="uncertainty"):
        fin.parse_fields({"channel_height_sigma_nm": "0.5"}, METHODS)
    # unless the value is already on record
    assert fin.parse_fields({"channel_height_sigma_nm": "0.5"}, METHODS,
                            current=row(1, channel_height_nm=3)) == {
        "channel_height_sigma_nm": 0.5}


def test_a_negative_depth_or_height_is_allowed_to_be_zero_but_not_below_for_widths():
    assert fin.parse_fields({"trench_depth_nm": "0"}, METHODS) == {"trench_depth_nm": 0.0}
    assert fin.parse_fields({"channel_height_nm": "-2"}, METHODS) == {"channel_height_nm": -2.0}


def test_status_after_saving_follows_the_afm_width_alone():
    assert fin.status_after(row(1), {"width_um": 1.0}) == "measured"
    assert fin.status_after(row(1, width_um=1.0), {"note": "x"}) == "measured"
    assert fin.status_after(row(1), {"width_optical_um": 2.0}) == "recorded"
    assert fin.status_after(row(1, status="aborted"), {"width_um": 1.0}) == "aborted"


def test_queue_entries_carry_readable_labels_for_what_is_missing():
    entry = fin.queue([row(1, width_um=1.0)])[0]
    assert entry["missing_labels"][0] == "Sample thickness"
    assert len(entry["missing_labels"]) == len(entry["missing"]) == 4


def test_the_experiment_filter_keeps_only_the_7_degree_speed_by_force_series():
    """Temporary (owner, Ian, 2026-10-13): the finalizer walks only the trials
    of the 7° speed x force map; an import, another tilt or an off-plan speed
    is left out."""
    rows = [row(1, tilt_deg=7.0, speed_steps_s=100.0, origin="recorded"),
            row(2, tilt_deg=7.0, speed_steps_s=300.0, origin="recorded"),
            row(3, tilt_deg=6.0, speed_steps_s=250.0, origin="recorded"),
            row(4, tilt_deg=7.0, speed_steps_s=20.0, origin="recorded"),
            row(5, tilt_deg=7.0, speed_steps_s=200.0, origin="imported"),
            row(6, tilt_deg=7.0, speed_steps_s=400.0, origin="recorded")]
    assert [t["id"] for t in fin.queue(rows, experiment=fin.EXPERIMENT)] == [1, 2, 6]
    assert [t["id"] for t in fin.queue(rows)] == [1, 2, 3, 4, 5, 6]     # no filter: as before
    assert fin.EXPERIMENT["label"] == "Ian, 10/13/26"


def test_widths_and_the_channel_height_take_a_min_and_a_max():
    """Owner 2026-10-13: a variable channel is recorded as a range."""
    out = fin.parse_fields({"width_optical_um": "2.0", "width_optical_min_um": "1.5",
                            "width_optical_max_um": "2.6", "width_min_um": "1.2",
                            "width_max_um": "2.1", "channel_height_min_nm": "-1",
                            "channel_height_max_nm": "4"}, METHODS)
    assert out["width_optical_min_um"] == 1.5 and out["width_optical_max_um"] == 2.6
    assert out["channel_height_min_nm"] == -1.0      # a height may read below the substrate


@pytest.mark.parametrize("raw, current", [
    ({"width_optical_min_um": "3", "width_optical_max_um": "2"}, {}),
    ({"width_max_um": "1.0"}, {"width_min_um": 1.5}),
    ({"width_optical_um": "5", "width_optical_max_um": "4"}, {}),
    ({"channel_height_nm": "1"}, {"channel_height_min_nm": 2.0, "channel_height_max_nm": 3.0}),
    ({"width_min_um": "0"}, {}),
])
def test_a_range_that_does_not_hold_together_is_refused(raw, current):
    with pytest.raises(ValueError):
        fin.parse_fields(raw, METHODS, current=current)


def test_the_walk_goes_by_chip_then_flake_then_cut_never_by_trial_number():
    """Owner 2026-10-13: walk the trials so the sample is swapped and searched
    as little as possible: one sample, then each chip, then each flake on it,
    and the cuts on a flake in cut order, whatever order they were recorded."""
    rows = [row(10, chip_id="2", flake_id="1", cut_id="1"),
            row(11, chip_id="1", flake_id="3", cut_id="2"),
            row(12, chip_id="1", flake_id="3", cut_id="1"),
            row(13, chip_id="1", flake_id="10", cut_id="1"),
            row(14, chip_id="1", flake_id="3", cut_id="10")]
    assert [t["id"] for t in fin.queue(rows)] == [12, 11, 14, 13, 10]
