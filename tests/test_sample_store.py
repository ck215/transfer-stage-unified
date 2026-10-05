"""The Sample Map's store, `data/sample_map.sqlite` (flake-coords Phase 1).

Owner rulings 2026-10-04: a separate file following the Transfer Map store's
conventions, joined to it by `sample_id` / `flake_uid`; quality 1-5 and the
six defect tags, rateable before an extent exists; approximate and AFM
thickness kept apart, with no red-percent estimate (Q16); the additive
`flake-coords/1` fields accepted (Q11): a UTC offset on every timestamp,
`registration_uid`, `sample_uid`, `storage_location`, `tags`, `deleted_at`.
Synthetic stores only: nothing here opens a real one.
"""
import csv
import datetime
import json
import sqlite3
import uuid

import pytest

from model import sample_store as ss


@pytest.fixture
def store(tmp_path):
    return ss.SampleStore(tmp_path / "data" / "sample_map.sqlite")


def _registration(store, sample_id="S1", **extra):
    fields = {"sample_id": sample_id, "frame_source": "stage:Chuck Positioner",
              "position_epoch": 3, "k_x_um": 0.625, "k_y_um": 0.625,
              "fit_kind": "rigid", "origin_stage_x": 1000.0,
              "origin_stage_y": 2000.0, "theta_rad": 0.1, "handedness": 1,
              "quality": "good"}
    fields.update(extra)
    corners = [{"label": "A", "stage_x": 1000.0, "stage_y": 2000.0,
                "method": "crosshair"},
               {"label": "B", "stage_x": 9000.0, "stage_y": 2800.0,
                "method": "crosshair"}]
    return store.add_registration(fields, corners)


def _aware(stamp):
    parsed = datetime.datetime.fromisoformat(stamp)
    return parsed.tzinfo is not None


# -- the file -------------------------------------------------------------------

def test_construction_creates_nothing_and_reads_answer_empty(store):
    assert not store.exists
    assert store.samples() == [] and store.flakes() == []
    assert not store.exists


def test_a_fresh_store_is_version_one_with_an_identity(store):
    assert store.ensure() is True
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == ss.SCHEMA_VERSION == 1
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"samples", "registrations", "corners", "flakes", "meta"} <= tables
    meta = store.meta()
    assert uuid.UUID(meta["store_uuid"]).version == 4
    assert store.ensure() is False and store.meta() == meta


def test_the_default_path_and_its_override(monkeypatch, tmp_path):
    monkeypatch.delenv("STATION_SAMPLE_DB", raising=False)
    assert ss.default_path().name == "sample_map.sqlite"
    assert ss.default_path().parent.name == "data"
    monkeypatch.setenv("STATION_SAMPLE_DB", str(tmp_path / "x.sqlite"))
    assert ss.default_path() == tmp_path / "x.sqlite"


# -- samples ----------------------------------------------------------------------

def test_a_sample_keeps_its_uid_and_its_timestamps_carry_the_offset(store):
    store.put_sample({"sample_id": "S1", "material": "graphene",
                      "substrate": "SiO2 285 nm / Si", "shape": "rectangle",
                      "storage_location": "box 3"})
    first = store.sample("S1")
    assert uuid.UUID(first["uid"]).version == 4
    assert _aware(first["created_at"]) and _aware(first["updated_at"])
    store.put_sample({"sample_id": "S1", "note": "remounted"})
    second = store.sample("S1")
    assert second["uid"] == first["uid"] and second["note"] == "remounted"
    assert second["material"] == "graphene"            # an update keeps the rest
    assert second["created_at"] == first["created_at"]


def test_sample_vocabularies_are_refused_in_words(store):
    with pytest.raises(ss.StoreRefused, match="shape"):
        store.put_sample({"sample_id": "S1", "shape": "circle"})
    with pytest.raises(ss.StoreRefused, match="status"):
        store.put_sample({"sample_id": "S1", "status": "lost"})
    with pytest.raises(ss.StoreRefused, match="sample ID"):
        store.put_sample({"sample_id": "  "})


# -- registrations and corners -------------------------------------------------------

def test_a_registration_keeps_its_corners_and_its_own_uid(store):
    store.put_sample({"sample_id": "S1"})
    rid = _registration(store)
    reg = store.registration(rid)
    assert uuid.UUID(reg["registration_uid"]).version == 4
    assert reg["invalidated_at"] is None and _aware(reg["registered_at"])
    assert [c["label"] for c in store.corners(rid)] == ["A", "B"]
    store.invalidate_registration(rid, "the probe reconnected")
    reg = store.registration(rid)
    assert reg["invalidated_reason"] == "the probe reconnected" and reg["invalidated_at"]


def test_a_legacy_registration_is_invalid_from_the_start(store):
    store.put_sample({"sample_id": "S1", "legacy_ref": "bench:trial 12"})
    rid = store.add_registration({"sample_id": "S1", "frame_source": "legacy",
                                  "quality": "unchecked"}, [])
    reg = store.registration(rid)
    assert reg["invalidated_at"] and "legacy import" in reg["invalidated_reason"]


def test_a_registration_names_a_known_frame_source_and_fit(store):
    store.put_sample({"sample_id": "S1"})
    with pytest.raises(ss.StoreRefused, match="frame source"):
        _registration(store, frame_source="probe")
    with pytest.raises(ss.StoreRefused, match="fit"):
        _registration(store, fit_kind="spline")


# -- flakes -----------------------------------------------------------------------

def test_flakes_are_labelled_per_sample(store):
    for sid in ("S1", "S2"):
        store.put_sample({"sample_id": sid})
    a = store.add_flake({"sample_id": "S1"})
    b = store.add_flake({"sample_id": "S1"})
    c = store.add_flake({"sample_id": "S2"})
    assert [store.flake(u)["label"] for u in (a, b, c)] == ["F01", "F02", "F01"]
    flake = store.flake(a)
    assert flake["sample_uid"] == store.sample("S1")["uid"]       # Q11, C7
    assert flake["status"] == "candidate"


def test_a_flake_needs_a_known_sample(store):
    with pytest.raises(ss.StoreRefused, match="No sample S9"):
        store.add_flake({"sample_id": "S9"})


def test_quality_is_one_to_five_and_rateable_before_an_extent(store):
    """Q18: accepted scale; a flake may be rated before it has an extent."""
    store.put_sample({"sample_id": "S1"})
    uid = store.add_flake({"sample_id": "S1", "quality": 4})
    assert store.flake(uid)["quality"] == 4 and store.flake(uid)["extent_kind"] == "none"
    for bad in (0, 6, "great", 3.5):
        with pytest.raises(ss.StoreRefused, match="Quality"):
            store.update_flake(uid, {"quality": bad})
    store.update_flake(uid, {"quality": None})
    assert store.flake(uid)["quality"] is None


def test_defects_are_the_six_tags_and_empty_means_inspected(store):
    store.put_sample({"sample_id": "S1"})
    uid = store.add_flake({"sample_id": "S1", "defects": ["bubbles", "folds"]})
    assert store.flake(uid)["defects"] == ["bubbles", "folds"]
    store.update_flake(uid, {"defects": []})
    assert store.flake(uid)["defects"] == []                 # inspected, clean
    store.update_flake(uid, {"defects": None})
    assert store.flake(uid)["defects"] is None               # not inspected
    with pytest.raises(ss.StoreRefused, match="scratches"):
        store.update_flake(uid, {"defects": ["scratches"]})
    assert ss.DEFECTS == ("cracks", "bubbles", "residue", "folds", "wrinkles", "tears")


def test_approximate_and_afm_thickness_never_mix(store):
    """4.1a and Q16: the AFM fields fill the AFM columns only; there is no
    red-percent estimate, so `red_percent` is not an approximate method."""
    store.put_sample({"sample_id": "S1"})
    uid = store.add_flake({"sample_id": "S1", "layers_estimate": 2,
                           "thickness_approx_nm": 0.7,
                           "thickness_approx_method": "optical_contrast",
                           "red_percent": 12.5})
    store.update_flake(uid, {"thickness_afm_nm": 1.1, "thickness_afm_sigma_nm": 0.1,
                             "afm_by": "ian"})
    flake = store.flake(uid)
    assert (flake["thickness_approx_nm"], flake["thickness_afm_nm"]) == (0.7, 1.1)
    assert flake["red_percent"] == 12.5 and flake["layers_estimate"] == 2
    with pytest.raises(ss.StoreRefused, match="red_percent"):
        store.update_flake(uid, {"thickness_approx_method": "red_percent"})


def test_a_flake_status_is_from_the_vocabulary(store):
    store.put_sample({"sample_id": "S1"})
    uid = store.add_flake({"sample_id": "S1"})
    store.update_flake(uid, {"status": "selected"})
    with pytest.raises(ss.StoreRefused, match="status"):
        store.update_flake(uid, {"status": "reserved"})        # C5: server-derived


def test_extent_points_and_tags_round_trip_as_lists(store):
    store.put_sample({"sample_id": "S1"})
    uid = store.add_flake({"sample_id": "S1", "extent_kind": "bbox",
                           "extent_source": "stage_corners",
                           "extent_points_um": [[1, 2], [3, 2], [3, 4], [1, 4]],
                           "tags": ["hBN-capped"], "trial_ids": [12]})
    flake = store.flake(uid)
    assert flake["extent_points_um"] == [[1, 2], [3, 2], [3, 4], [1, 4]]
    assert flake["tags"] == ["hBN-capped"] and flake["trial_ids"] == [12]


def test_delete_is_soft_and_the_export_carries_it(store):
    store.put_sample({"sample_id": "S1"})
    uid = store.add_flake({"sample_id": "S1"})
    store.delete_flake(uid)
    assert store.flakes() == []
    assert store.flakes(include_deleted=True)[0]["deleted_at"]
    doc = store.export_document("bench", "test")
    assert doc["flakes"][0]["deleted_at"]


def test_updating_an_unknown_flake_is_refused(store):
    store.ensure()
    with pytest.raises(ss.StoreRefused, match="No flake"):
        store.update_flake(str(uuid.uuid4()), {"note": "x"})


# -- the flake-coords/1 document -----------------------------------------------------

def _populated(store, tmp_path):
    store.put_sample({"sample_id": "S1", "material": "WSe2"})
    rid = _registration(store)
    picture = tmp_path / "f.png"
    picture.write_bytes(b"\x89PNG fake")
    uid = store.add_flake({"sample_id": "S1", "registration_id": rid,
                           "sample_x_um": 120.0, "sample_y_um": 340.0,
                           "extent_kind": "bbox",
                           "extent_points_um": [[0, 0], [10, 0], [10, 5], [0, 5]],
                           "image_path": str(picture), "quality": 5, "defects": []})
    return rid, uid


def test_the_export_document_follows_the_contract(store, tmp_path):
    rid, uid = _populated(store, tmp_path)
    doc = store.export_document("bench-pc", "1.2.3")
    assert doc["schema"] == "flake-coords/1"
    assert _aware(doc["exported_at"])
    assert doc["station"] == {"name": "bench-pc", "software_version": "1.2.3",
                              "store_uuid": store.meta()["store_uuid"]}
    (reg,) = doc["registrations"]
    assert [c["label"] for c in reg["corners"]] == ["A", "B"]
    (flake,) = doc["flakes"]
    assert flake["registration_uid"] == reg["registration_uid"]
    assert flake["extent_points_um"] == [[0, 0], [10, 0], [10, 5], [0, 5]]
    assert flake["observations"] == []
    for derived in ("area_um2", "lateral_um", "aspect_ratio"):
        assert derived not in flake                          # computed, never sent
    (image,) = doc["images"]
    assert image["flake_uid"] == uid and len(image["sha256"]) == 64
    json.dumps(doc)                                          # plain JSON


def test_an_export_imports_into_another_store(store, tmp_path):
    _populated(store, tmp_path)
    doc = store.export_document("bench-pc", "1")
    other = ss.SampleStore(tmp_path / "other.sqlite")
    counts = other.import_document(doc)
    assert counts == {"added": 3, "updated": 0, "kept": 0}
    flake = other.flakes()[0]
    reg = other.registrations()[0]
    assert flake["registration_id"] == reg["registration_id"]
    assert reg["registration_uid"] == doc["registrations"][0]["registration_uid"]
    assert [c["label"] for c in other.corners(reg["registration_id"])] == ["A", "B"]
    assert other.import_document(doc) == {"added": 0, "updated": 0, "kept": 3}


def test_the_newer_record_wins_a_merge(store, tmp_path):
    _, uid = _populated(store, tmp_path)
    doc = store.export_document("bench-pc", "1")
    other = ss.SampleStore(tmp_path / "other.sqlite")
    other.import_document(doc)
    newer = json.loads(json.dumps(doc))
    newer["flakes"][0]["note"] = "edited on the other rig"
    newer["flakes"][0]["updated_at"] = "2099-01-01T00:00:00+00:00"
    assert other.import_document(newer)["updated"] == 1
    assert other.flake(uid)["note"] == "edited on the other rig"
    older = json.loads(json.dumps(doc))
    older["flakes"][0]["note"] = "stale"
    older["flakes"][0]["updated_at"] = "2000-01-01T00:00:00+00:00"
    other.import_document(older)
    assert other.flake(uid)["note"] == "edited on the other rig"


def test_an_import_refuses_another_schema(store):
    with pytest.raises(ss.StoreRefused, match="flake-coords/1"):
        store.import_document({"schema": "flake-coords/2"})


def test_the_csv_export_writes_the_four_tables(store, tmp_path):
    _populated(store, tmp_path)
    paths = store.export_csv(tmp_path / "exports", "20261004-1540")
    assert sorted(p.name for p in paths) == [
        "sample_map_20261004-1540_corners.csv", "sample_map_20261004-1540_flakes.csv",
        "sample_map_20261004-1540_registrations.csv",
        "sample_map_20261004-1540_samples.csv"]
    flakes = next(p for p in paths if p.name.endswith("_flakes.csv"))
    with open(flakes, newline="") as handle:
        (row,) = list(csv.DictReader(handle))
    assert json.loads(row["extent_points_um"]) == [[0, 0], [10, 0], [10, 5], [0, 5]]
    assert row["quality"] == "5"


# -- the Sample Map's incremental writes (each mark is its own transaction) ------

def test_a_corner_is_added_or_replaced_on_a_registration(store):
    store.put_sample({"sample_id": "S1"})
    rid = _registration(store)
    store.put_corner(rid, {"label": "D", "stage_x": 1.0, "stage_y": 9.0,
                           "method": "crosshair"})
    store.put_corner(rid, {"label": "A", "stage_x": 5.0, "stage_y": 5.0,
                           "method": "crosshair"})
    corners = {c["label"]: c for c in store.corners(rid)}
    assert sorted(corners) == ["A", "B", "D"]
    assert (corners["A"]["stage_x"], corners["A"]["stage_y"]) == (5.0, 5.0)
    with pytest.raises(ss.StoreRefused, match="corner method"):
        store.put_corner(rid, {"label": "C", "method": "guess"})


def test_a_registrations_fit_is_updated_in_place(store):
    store.put_sample({"sample_id": "S1"})
    rid = _registration(store, quality="unchecked")
    before = store.registration(rid)
    store.update_registration(rid, {"theta_rad": 0.2, "quality": "check",
                                    "closure_um": 12.0})
    after = store.registration(rid)
    assert (after["theta_rad"], after["quality"], after["closure_um"]) == (0.2, "check", 12.0)
    assert after["registration_uid"] == before["registration_uid"]
    with pytest.raises(ss.StoreRefused, match="registration quality"):
        store.update_registration(rid, {"quality": "great"})
    with pytest.raises(ss.StoreRefused, match="No registration"):
        store.update_registration(999, {"quality": "good"})
