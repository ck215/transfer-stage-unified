"""The Sample DB's store, `data/sample_map.sqlite` (flake-coords Phase 1).

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
    assert store.samples() == [] and store.coord_flakes() == []
    assert not store.exists


def test_a_fresh_store_is_version_four_with_an_identity(store):
    assert store.ensure() is True
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == ss.SCHEMA_VERSION == 4   # v4: the hierarchy tables
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"samples", "registrations", "corners", "flakes", "meta", "sample_images"} <= tables
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
    a = store.add_coord_flake({"sample_id": "S1"})
    b = store.add_coord_flake({"sample_id": "S1"})
    c = store.add_coord_flake({"sample_id": "S2"})
    assert [store.coord_flake(u)["label"] for u in (a, b, c)] == ["F01", "F02", "F01"]
    flake = store.coord_flake(a)
    assert flake["sample_uid"] == store.sample("S1")["uid"]       # Q11, C7
    assert flake["status"] == "candidate"


def test_a_flake_needs_a_known_sample(store):
    with pytest.raises(ss.StoreRefused, match="No sample S9"):
        store.add_coord_flake({"sample_id": "S9"})


def test_quality_is_one_to_five_and_rateable_before_an_extent(store):
    """Q18: accepted scale; a flake may be rated before it has an extent."""
    store.put_sample({"sample_id": "S1"})
    uid = store.add_coord_flake({"sample_id": "S1", "quality": 4})
    assert store.coord_flake(uid)["quality"] == 4 and store.coord_flake(uid)["extent_kind"] == "none"
    for bad in (0, 6, "great", 3.5):
        with pytest.raises(ss.StoreRefused, match="Quality"):
            store.update_coord_flake(uid, {"quality": bad})
    store.update_coord_flake(uid, {"quality": None})
    assert store.coord_flake(uid)["quality"] is None


def test_defects_are_the_six_tags_and_empty_means_inspected(store):
    store.put_sample({"sample_id": "S1"})
    uid = store.add_coord_flake({"sample_id": "S1", "defects": ["bubbles", "folds"]})
    assert store.coord_flake(uid)["defects"] == ["bubbles", "folds"]
    store.update_coord_flake(uid, {"defects": []})
    assert store.coord_flake(uid)["defects"] == []                 # inspected, clean
    store.update_coord_flake(uid, {"defects": None})
    assert store.coord_flake(uid)["defects"] is None               # not inspected
    with pytest.raises(ss.StoreRefused, match="scratches"):
        store.update_coord_flake(uid, {"defects": ["scratches"]})
    assert ss.DEFECTS == ("cracks", "bubbles", "residue", "folds", "wrinkles", "tears")


def test_approximate_and_afm_thickness_never_mix(store):
    """4.1a and Q16: the AFM fields fill the AFM columns only; there is no
    red-percent estimate, so `red_percent` is not an approximate method."""
    store.put_sample({"sample_id": "S1"})
    uid = store.add_coord_flake({"sample_id": "S1", "layers_estimate": 2,
                           "thickness_approx_nm": 0.7,
                           "thickness_approx_method": "optical_contrast",
                           "red_percent": 12.5})
    store.update_coord_flake(uid, {"thickness_afm_nm": 1.1, "thickness_afm_sigma_nm": 0.1,
                             "afm_by": "ian"})
    flake = store.coord_flake(uid)
    assert (flake["thickness_approx_nm"], flake["thickness_afm_nm"]) == (0.7, 1.1)
    assert flake["red_percent"] == 12.5 and flake["layers_estimate"] == 2
    with pytest.raises(ss.StoreRefused, match="red_percent"):
        store.update_coord_flake(uid, {"thickness_approx_method": "red_percent"})


def test_a_flake_status_is_from_the_vocabulary(store):
    store.put_sample({"sample_id": "S1"})
    uid = store.add_coord_flake({"sample_id": "S1"})
    store.update_coord_flake(uid, {"status": "selected"})
    with pytest.raises(ss.StoreRefused, match="status"):
        store.update_coord_flake(uid, {"status": "reserved"})        # C5: server-derived


def test_extent_points_and_tags_round_trip_as_lists(store):
    store.put_sample({"sample_id": "S1"})
    uid = store.add_coord_flake({"sample_id": "S1", "extent_kind": "bbox",
                           "extent_source": "stage_corners",
                           "extent_points_um": [[1, 2], [3, 2], [3, 4], [1, 4]],
                           "tags": ["hBN-capped"], "trial_ids": [12]})
    flake = store.coord_flake(uid)
    assert flake["extent_points_um"] == [[1, 2], [3, 2], [3, 4], [1, 4]]
    assert flake["tags"] == ["hBN-capped"] and flake["trial_ids"] == [12]


def test_delete_is_soft_and_the_export_carries_it(store):
    store.put_sample({"sample_id": "S1"})
    uid = store.add_coord_flake({"sample_id": "S1"})
    store.delete_coord_flake(uid)
    assert store.coord_flakes() == []
    assert store.coord_flakes(include_deleted=True)[0]["deleted_at"]
    doc = store.export_document("bench", "test")
    assert doc["flakes"][0]["deleted_at"]


def test_updating_an_unknown_flake_is_refused(store):
    store.ensure()
    with pytest.raises(ss.StoreRefused, match="No flake"):
        store.update_coord_flake(str(uuid.uuid4()), {"note": "x"})


# -- the flake-coords/1 document -----------------------------------------------------

def _populated(store, tmp_path):
    store.put_sample({"sample_id": "S1", "material": "WSe2"})
    rid = _registration(store)
    picture = tmp_path / "f.png"
    picture.write_bytes(b"\x89PNG fake")
    uid = store.add_coord_flake({"sample_id": "S1", "registration_id": rid,
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
    flake = other.coord_flakes()[0]
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
    assert other.coord_flake(uid)["note"] == "edited on the other rig"
    older = json.loads(json.dumps(doc))
    older["flakes"][0]["note"] = "stale"
    older["flakes"][0]["updated_at"] = "2000-01-01T00:00:00+00:00"
    other.import_document(older)
    assert other.coord_flake(uid)["note"] == "edited on the other rig"


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


# -- the Sample DB's incremental writes (each mark is its own transaction) ------

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


# -- version 2: the Rotator (owner 2026-10-04) --------------------------------------

def test_a_version_one_file_gains_the_rotator_columns_and_table(tmp_path):
    import sqlite3
    path = tmp_path / "v1.sqlite"
    db = sqlite3.connect(str(path))
    v1 = [c for c in ss.REGISTRATION_COLUMNS if not c[0].startswith("rotator_")]
    db.execute("CREATE TABLE registrations (" + ", ".join(n + " " + k for n, k in v1) + ")")
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    store = ss.SampleStore(path)
    store.ensure()
    db = sqlite3.connect(str(path))
    try:
        # a v1 file is walked all the way to the current version (now 4)
        assert db.execute("PRAGMA user_version").fetchone()[0] == ss.SCHEMA_VERSION == 4
        have = {r[1] for r in db.execute("PRAGMA table_info(registrations)")}
        assert {"rotator_name", "rotator_phi0_deg", "rotator_calibration_uid",
                "rotator_closure_um", "rotator_quality"} <= have
        assert db.execute("SELECT name FROM sqlite_master WHERE name = "
                          "'rotator_calibrations'").fetchone()
    finally:
        db.close()


def test_a_rotator_calibration_round_trips_and_can_be_ended(store):
    uid = store.add_rotator_calibration({
        "rotator_name": "Rotator", "frame_source": "stage:Stepper Probe",
        "position_epoch": 3, "centre_x": 1.5, "centre_y": -2.0, "sense": -1,
        "n_points": 3, "method": "circle", "residual_um": 0.4, "quality": "good",
        "points": [[1.0, 2.0, 0.0], [3.0, 4.0, 10.0], [5.0, 6.0, 20.0]],
        "k_um": 0.625})
    cal = store.rotator_calibration(uid)
    assert cal["points"] == [[1.0, 2.0, 0.0], [3.0, 4.0, 10.0], [5.0, 6.0, 20.0]]
    assert cal["sense"] == -1 and cal["calibrated_at"]
    assert [c["calibration_uid"] for c in store.rotator_calibrations()] == [uid]
    store.invalidate_rotator_calibration(uid, "the probe reconnected")
    assert store.rotator_calibration(uid)["invalidated_reason"] == "the probe reconnected"
    with pytest.raises(ss.StoreRefused):
        store.add_rotator_calibration({"frame_source": "elsewhere", "sense": 1,
                                       "centre_x": 0, "centre_y": 0})
    with pytest.raises(ss.StoreRefused):
        store.add_rotator_calibration({"frame_source": "stage:X", "sense": 2,
                                       "centre_x": 0, "centre_y": 0})


# -- version 3: the sample images (owner 2026-10-07) --------------------------------

def _picture(tmp_path, name="shot.png", payload=b"\x89PNG original bytes"):
    path = tmp_path / "incoming" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(payload)
    return path


def test_add_image_copies_the_original_unmodified_and_records_it(store, tmp_path):
    import hashlib
    source = _picture(tmp_path)
    row = store.add_image("4oct26", source, "microscope", 20, note="edge")
    assert row["sample_id"] == "4oct26" and row["instrument"] == "microscope"
    assert row["magnification"] == 20 and row["note"] == "edge"
    assert not row["path"].startswith("/") and row["path"].startswith("images/")
    assert row["path"].endswith("_microscope_20x.png")
    copy = store.image_file(row)
    assert copy.read_bytes() == source.read_bytes()
    assert copy.is_relative_to(store.path.parent) and copy != source
    assert row["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert _aware(row["captured_at"])
    assert source.exists()                                   # the original stays
    assert store.images("4oct26") == [row] and store.images("other") == []


def test_a_name_collision_gets_a_suffix_and_nothing_is_overwritten(store, tmp_path):
    first = store.add_image("S1", _picture(tmp_path, payload=b"one"), "microscope", 10)
    second = store.add_image("S1", _picture(tmp_path, payload=b"two"), "microscope", 10)
    third = store.add_image("S1", _picture(tmp_path, payload=b"three"), "microscope", 10)
    paths = {first["path"], second["path"], third["path"]}
    assert len(paths) == 3
    assert [store.image_file(r).read_bytes() for r in (first, second, third)] == \
        [b"one", b"two", b"three"]


def test_a_free_text_label_gets_a_safe_folder(store, tmp_path):
    row = store.add_image("Riki's Gift 8March26", _picture(tmp_path), "transfer_stage", 50)
    folder = row["path"].split("/")[1]
    assert "/" not in folder and " " not in folder and "'" not in folder
    assert store.images("Riki's Gift 8March26") == [row]
    assert store.add_image("7/27/26", _picture(tmp_path), "microscope", 100)["path"].count("/") == 2


@pytest.mark.parametrize("instrument,mag,word", [
    ("sem", 20, "sem"), ("microscope", "40x", "40x"), ("microscope", 5, "5"),
    ("microscope", "big", "big"), (None, 20, "instrument")])
def test_image_vocabulary_is_refused_in_words(store, tmp_path, instrument, mag, word):
    with pytest.raises(ss.StoreRefused) as refusal:
        store.add_image("S1", _picture(tmp_path), instrument, mag)
    assert word in str(refusal.value)
    assert store.images() == []
    assert not (store.path.parent / "images").exists() or \
        not list((store.path.parent / "images").rglob("*.png"))


def test_a_magnification_may_be_typed_with_its_x(store, tmp_path):
    assert store.add_image("S1", _picture(tmp_path), "microscope", "50x")["magnification"] == 50


def test_a_missing_source_or_blank_sample_is_refused(store, tmp_path):
    with pytest.raises(ss.StoreRefused):
        store.add_image("S1", tmp_path / "nope.png", "microscope", 10)
    with pytest.raises(ss.StoreRefused):
        store.add_image("  ", _picture(tmp_path), "microscope", 10)


def test_the_table_itself_enforces_the_vocabulary(store):
    store.ensure()
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO sample_images (sample_id, instrument, magnification,"
                       " path, sha256, captured_at) VALUES ('s','sem',20,'p','h','t')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO sample_images (sample_id, instrument, magnification,"
                       " path, sha256, captured_at) VALUES ('s','microscope',40,'p','h','t')")


def test_delete_image_removes_the_row_and_the_file(store, tmp_path):
    keep = store.add_image("S1", _picture(tmp_path), "microscope", 10)
    gone = store.add_image("S1", _picture(tmp_path, payload=b"x"), "microscope", 20)
    path = store.image_file(gone)
    assert path.exists()
    store.delete_image(gone["id"])
    assert not path.exists() and store.images("S1") == [keep]
    with pytest.raises(ss.StoreRefused):
        store.delete_image(gone["id"])


def test_delete_image_never_unlinks_outside_the_images_folder(store, tmp_path):
    store.ensure()
    outside = tmp_path / "precious.png"
    outside.write_bytes(b"keep me")
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO sample_images (sample_id, instrument, magnification, path,"
                   " sha256, captured_at) VALUES ('S1','microscope',10,?,'h','t')",
                   (str(outside),))
    (row,) = store.images("S1")
    store.delete_image(row["id"])
    assert outside.exists() and store.images("S1") == []


def _v2_file(path):
    """A version-2 store as the repo wrote it before the image table."""
    db = sqlite3.connect(str(path))
    for table in ("samples", "registrations", "corners", "flakes", "rotator_calibrations"):
        cols = dict(ss._TABLES)[table]
        extra = ", PRIMARY KEY (registration_id, label)" if table == "corners" else ""
        db.execute("CREATE TABLE " + table + " (" + ", ".join(n + " " + k for n, k in cols)
                   + extra + ")")
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta VALUES ('store_uuid', 'u-1')")
    db.execute("INSERT INTO samples (sample_id, uid, status) VALUES ('S1', 'uid-1', 'active')")
    db.execute("INSERT INTO registrations (registration_uid, sample_id, frame_source)"
               " VALUES ('r-1', 'S1', 'legacy')")
    db.execute("INSERT INTO corners (registration_id, label, image_path)"
               " VALUES (1, 'A', '/abs/corner.png')")
    db.execute("INSERT INTO flakes (flake_uid, label, sample_id, image_path)"
               " VALUES ('f-1', 'F01', 'S1', '/abs/flake.png')")
    db.execute("PRAGMA user_version = 2")
    db.commit()
    db.close()


def test_a_version_two_file_keeps_every_row_and_gains_the_image_table(tmp_path):
    path = tmp_path / "v2.sqlite"
    _v2_file(path)
    store = ss.SampleStore(path)
    assert store.images() == []                              # a read changes nothing
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    store.ensure()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert db.execute("SELECT name FROM sqlite_master WHERE name = 'sample_images_sample'"
                          ).fetchone()
    assert [s["sample_id"] for s in store.samples()] == ["S1"]
    assert len(store.registrations()) == 1 and len(store.coord_flakes()) == 1
    assert store.corners(1)[0]["image_path"] == "/abs/corner.png"
    assert store.meta()["store_uuid"] == "u-1"               # identity untouched
    store.add_image("S1", _picture(tmp_path), "microscope", 10)
    assert len(store.images("S1")) == 1


def test_old_absolute_paths_are_left_alone_and_counted(tmp_path):
    path = tmp_path / "v2.sqlite"
    _v2_file(path)
    store = ss.SampleStore(path)
    store.ensure()
    assert store.absolute_image_paths() == 2
    assert store.coord_flakes()[0]["image_path"] == "/abs/flake.png"
    store.add_image("S1", _picture(tmp_path), "microscope", 10)
    assert store.absolute_image_paths() == 2                 # new rows are relative


def test_the_export_lists_the_sample_images_by_relative_path(store, tmp_path):
    row = store.add_image("S1", _picture(tmp_path), "microscope", 100, note="n")
    doc = store.export_document("bench-pc", "1")
    assert doc["schema"] == "flake-coords/1"
    (entry,) = doc["images"]
    assert entry == {"sample_id": "S1", "path": row["path"], "sha256": row["sha256"],
                     "instrument": "microscope", "magnification": 100,
                     "captured_at": row["captured_at"], "note": "n"}
    assert not entry["path"].startswith("/")
    json.dumps(doc)


# -- the hierarchy: sample > chip > flake (v4, 2026-10-07) -------------------------

def _tree(store):
    store.add_sample("4oct26", "hBN", note="first")
    store.add_chip("4oct26", "2")
    store.add_flake("4oct26", "2", "F1", note="thin")
    return store


def test_materials_are_seeded_on_first_write_and_may_grow(store):
    assert store.materials() == ["hBN", "graphite", "MoS2"]  # a missing file: the seed
    assert not store.exists                                  # and the read created nothing
    store.add_sample("S1", "hBN")
    assert store.materials() == ["hBN", "graphite", "MoS2"]
    assert store.add_material("WSe2") == "WSe2"
    assert store.add_material("wse2") == "WSe2"              # idempotent, one spelling
    assert store.materials()[-1] == "WSe2" and len(store.materials()) == 4
    store.add_sample("S2", "wse2")                           # stored with the canonical spelling
    assert store.sample("S2")["material"] == "WSe2"
    with pytest.raises(ss.StoreRefused):
        store.add_material("  ")


def test_a_sample_row_has_material_note_created_and_photo_count(store, tmp_path):
    _tree(store)
    (row,) = store.samples()
    assert (row["sample_id"], row["material"], row["note"], row["photo_count"]) == \
        ("4oct26", "hBN", "first", 0)
    assert _aware(row["created_at"])
    store.add_image("4oct26", _picture(tmp_path), "microscope", 10)
    store.add_image("4oct26", _picture(tmp_path, payload=b"c"), "microscope", 10,
                    chip_id="2")
    assert store.samples()[0]["photo_count"] == 1            # the sample's OWN pictures


@pytest.mark.parametrize("call,word", [
    (lambda s: s.add_sample("", "hBN"), "sample ID"),
    (lambda s: s.add_sample("  ", "hBN"), "sample ID"),
    (lambda s: s.add_sample("4oct26", "hBN"), "already"),
    (lambda s: s.add_sample("4OCT26", "hBN"), "already"),
    (lambda s: s.add_sample("S9", "unobtainium"), "unobtainium"),
    (lambda s: s.add_sample("S9", ""), "material"),
    (lambda s: s.add_chip("4oct26", "", ), "chip ID"),
    (lambda s: s.add_chip("4oct26", "2"), "already"),
    (lambda s: s.add_chip("nope", "1"), "no sample"),
    (lambda s: s.add_flake("4oct26", "2", ""), "flake ID"),
    (lambda s: s.add_flake("4oct26", "2", "f1"), "already"),
    (lambda s: s.add_flake("4oct26", "9", "F2"), "no chip"),
    (lambda s: s.add_flake("nope", "2", "F2"), "no sample"),
])
def test_the_hierarchy_refuses_in_words(store, call, word):
    _tree(store)
    with pytest.raises(ss.StoreRefused) as refusal:
        call(store)
    assert word in str(refusal.value)
    assert [s["sample_id"] for s in store.samples()] == ["4oct26"]
    assert len(store.chips("4oct26")) == 1 and len(store.flakes("4oct26", "2")) == 1


def test_the_same_id_is_fine_under_another_parent(store):
    _tree(store)
    store.add_sample("S2", "graphite")
    store.add_chip("S2", "2")                                 # chip 2 of another sample
    store.add_chip("4oct26", "3")
    store.add_flake("4oct26", "3", "F1")                      # F1 of another chip
    assert [c["chip_id"] for c in store.chips("4oct26")] == ["2", "3"]
    assert [c["chip_id"] for c in store.chips("S2")] == ["2"]
    assert len(store.flakes("4oct26", "2")) == len(store.flakes("4oct26", "3")) == 1


def test_chips_and_flakes_carry_their_counts(store, tmp_path):
    _tree(store)
    store.add_flake("4oct26", "2", "F2")
    store.add_image("4oct26", _picture(tmp_path), "microscope", 20, chip_id="2")
    store.add_image("4oct26", _picture(tmp_path, payload=b"f"), "microscope", 50,
                    chip_id="2", flake_id="F1")
    (chip,) = store.chips("4oct26")
    assert (chip["chip_id"], chip["flake_count"], chip["photo_count"]) == ("2", 2, 1)
    assert set(chip) == {"chip_id", "note", "created_at", "photo_count", "flake_count"}
    f1, f2 = store.flakes("4oct26", "2")
    assert (f1["flake_id"], f1["note"], f1["photo_count"], f2["photo_count"]) == \
        ("F1", "thin", 1, 0)
    assert set(f1) == {"flake_id", "note", "created_at", "photo_count"}
    assert store.chips("nope") == [] and store.flakes("4oct26", "9") == []


def test_images_belong_to_one_level_and_land_in_its_folder(store, tmp_path):
    _tree(store)
    s = store.add_image("4oct26", _picture(tmp_path), "microscope", 10)
    c = store.add_image("4oct26", _picture(tmp_path, payload=b"c"), "microscope", 10,
                        chip_id="2")
    f = store.add_image("4oct26", _picture(tmp_path, payload=b"f"), "microscope", 10,
                        chip_id="2", flake_id="F1")
    assert (s["chip_id"], s["flake_id"]) == (None, None)
    assert (c["chip_id"], c["flake_id"]) == ("2", None)
    assert (f["chip_id"], f["flake_id"]) == ("2", "F1")
    assert s["path"].count("/") == 2 and c["path"].count("/") == 3 and f["path"].count("/") == 4
    assert store.images("4oct26") == [s]                      # that level's own
    assert store.images("4oct26", "2") == [c]
    assert store.images("4oct26", "2", "F1") == [f]
    assert store.images("4oct26", any=True) == [s, c, f]
    assert store.images("4oct26", "2", any=True) == [c, f]
    assert store.images() == [s, c, f]


def test_a_picture_of_a_missing_chip_or_flake_copies_nothing(store, tmp_path):
    _tree(store)
    for kw in ({"chip_id": "9"}, {"chip_id": "2", "flake_id": "F9"}, {"flake_id": "F1"}):
        with pytest.raises(ss.StoreRefused):
            store.add_image("4oct26", _picture(tmp_path), "microscope", 10, **kw)
    assert store.images() == []
    assert not list((store.path.parent / "images").rglob("*.png"))


def test_open_readonly_reads_and_never_writes(store, tmp_path):
    _tree(store)
    ro = ss.SampleStore.open_readonly(store.path)
    assert [s["sample_id"] for s in ro.samples()] == ["4oct26"]
    assert ro.chips("4oct26")[0]["flake_count"] == 1 and ro.flakes("4oct26", "2")
    assert ro.materials() == ["hBN", "graphite", "MoS2"]
    for call in (lambda: ro.add_sample("X", "hBN"), lambda: ro.add_material("x"),
                 lambda: ro.ensure()):
        with pytest.raises(ss.StoreRefused):
            call()
    with pytest.raises(ss.StoreRefused) as missing:
        ss.SampleStore.open_readonly(tmp_path / "nope.sqlite")
    assert "nope.sqlite" in str(missing.value) and not (tmp_path / "nope.sqlite").exists()
    db = ro._connect()
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM samples")
    finally:
        db.close()


def _v3_file(path):
    """A version-3 store as the repo wrote it: sample_images without the
    level columns, no hierarchy tables."""
    db = sqlite3.connect(str(path))
    for table in ("samples", "registrations", "corners", "flakes",
                  "rotator_calibrations", "sample_images"):
        cols = dict(ss._TABLES)[table]
        if table == "sample_images":
            cols = tuple(c for c in cols if c[0] not in ("chip_id", "flake_id"))
        extra = ", PRIMARY KEY (registration_id, label)" if table == "corners" else ""
        db.execute("CREATE TABLE " + table + " (" + ", ".join(n + " " + k for n, k in cols)
                   + extra + ")")
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta VALUES ('store_uuid', 'u-3')")
    db.execute("INSERT INTO samples (sample_id, uid, material, status, note) VALUES "
               "('S1', 'uid-1', 'WSe2', 'active', 'old')")
    db.execute("INSERT INTO flakes (flake_uid, label, sample_id) VALUES ('f-1','F01','S1')")
    db.execute("INSERT INTO sample_images (sample_id, instrument, magnification, path,"
               " sha256, captured_at) VALUES ('S1','microscope',10,'images/S1/a.png','h','t')")
    db.execute("PRAGMA user_version = 3")
    db.commit()
    db.close()


def test_a_version_three_file_reads_unchanged_and_migrates_on_first_write(tmp_path):
    path = tmp_path / "v3.sqlite"
    _v3_file(path)
    ro = ss.SampleStore.open_readonly(path)                  # an un-migrated file
    (row,) = ro.samples()
    assert (row["sample_id"], row["material"], row["photo_count"]) == ("S1", "WSe2", 1)
    assert ro.chips("S1") == [] and ro.flakes("S1", "1") == []
    assert len(ro.images("S1")) == 1 and ro.images("S1", "1") == []
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3   # a read migrated nothing
    store = ss.SampleStore(path)
    store.add_chip("S1", "1")
    store.add_flake("S1", "1", "F1")
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == ss.SCHEMA_VERSION == 4
        have = {r[1] for r in db.execute("PRAGMA table_info(sample_images)")}
    assert {"chip_id", "flake_id"} <= have
    assert store.materials() == ["hBN", "graphite", "MoS2"]
    # Every v3 row survived, the old picture is still the sample's own.
    (old,) = store.images("S1")
    assert old["path"] == "images/S1/a.png" and (old["chip_id"], old["flake_id"]) == (None, None)
    assert store.sample("S1")["note"] == "old" and store.coord_flake("f-1")["label"] == "F01"
    assert store.meta()["store_uuid"] == "u-3"
    assert store.samples()[0]["photo_count"] == 1


def test_the_export_stays_additive_and_carries_the_hierarchy(store, tmp_path):
    _tree(store)
    row = store.add_image("4oct26", _picture(tmp_path), "microscope", 10, chip_id="2",
                          flake_id="F1")
    doc = store.export_document("bench-pc", "1")
    assert doc["schema"] == "flake-coords/1"
    assert doc["materials"] == ["hBN", "graphite", "MoS2"]
    assert [c["chip_id"] for c in doc["chips"]] == ["2"]
    assert [f["flake_id"] for f in doc["sample_flakes"]] == ["F1"]
    assert doc["flakes"] == []                               # the dormant records, apart
    (entry,) = doc["images"]
    assert (entry["chip_id"], entry["flake_id"], entry["path"]) == ("2", "F1", row["path"])
    json.dumps(doc)
    other = ss.SampleStore(tmp_path / "other" / "s.sqlite")
    other.import_document(doc)
    assert other.chips("4oct26")[0]["chip_id"] == "2" and other.flakes("4oct26", "2")


# -- the picture preview's pick (owner 2026-10-08) -------------------------------------
def _pic(id_, mag, when="2026-10-07T10:00:00-07:00"):
    return {"id": id_, "magnification": mag, "captured_at": when,
            "path": f"images/s/{id_}_{mag}x.png"}


def test_the_preview_prefers_100x_then_50x_then_the_next_lower():
    assert ss.pick_preview([_pic(1, 10), _pic(2, 100), _pic(3, 50)])[0]["id"] == 2
    assert ss.pick_preview([_pic(1, 10), _pic(3, 50), _pic(4, 20)])[0]["id"] == 3
    assert ss.pick_preview([_pic(1, 10), _pic(4, 20), _pic(5, 5)])[0]["id"] == 4
    assert ss.pick_preview([_pic(5, 5)])[0]["id"] == 5
    # The order on offer: 100, 50, then lower highest first, then the rest.
    assert ss.preview_order([5, 10, 20, 50, 100, 60, 150]) == [100, 50, 20, 10, 5, 150, 60]


def test_the_newest_picture_wins_at_one_magnification():
    rows = [_pic(1, 100, "2026-10-07T10:00:00-07:00"),
            _pic(2, 100, "2026-10-08T09:00:00-07:00"),
            _pic(3, 100, "2026-10-07T12:00:00-07:00"),
            _pic(4, 10, "2026-10-09T00:00:00-07:00")]
    assert ss.pick_preview(rows)[0]["id"] == 2
    # Same timestamp: the later row.
    assert ss.pick_preview([_pic(7, 50), _pic(8, 50)])[0]["id"] == 8


def test_no_pictures_is_no_preview_and_a_choice_is_honoured_only_when_there():
    assert ss.pick_preview([]) == (None, [])
    rows = [_pic(1, 10), _pic(2, 100)]
    assert ss.pick_preview(rows, "10x")[0]["id"] == 1
    assert ss.pick_preview(rows, "50x")[0]["id"] == 2
    preview = ss.PicturePreview()
    level = ("S", "1", "F")
    assert preview.text(level, []) == "No picture"
    assert preview.png(None, level, []) == b""
    assert preview.choose(level, rows, "10x") == "10x"
    assert preview.magnification(level, rows) == "10x"
    # A new pick starts again at the default.
    assert preview.magnification(("S", "1", "G"), rows) == "100x"
    with pytest.raises(ss.StoreRefused, match="No picture at 50x"):
        preview.choose(level, rows, "50x")


def test_the_preview_reads_only_a_file_inside_the_store(tmp_path):
    import io
    from PIL import Image
    store = ss.SampleStore(tmp_path / "store" / "sample_map.sqlite")
    (tmp_path / "store" / "images").mkdir(parents=True)
    Image.new("RGB", (1200, 900), (200, 80, 20)).save(tmp_path / "store" / "images" / "a.png")
    Image.new("RGB", (10, 10)).save(tmp_path / "outside.png")
    inside = dict(_pic(1, 100), path="images/a.png")
    outside = dict(_pic(2, 100), path="../outside.png")
    assert ss.preview_file(store, inside) is not None
    assert ss.preview_file(store, outside) is None
    png = ss.PicturePreview().png(store, ("S", None, None), [inside])
    small = Image.open(io.BytesIO(png))
    assert max(small.size) == ss.PREVIEW_PX and small.size[0] > small.size[1]
    assert ss.PicturePreview().png(store, ("S", None, None), [outside]) == b""
