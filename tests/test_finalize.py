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
