"""The Transfer Map's finalizer commands: the queue, a trial's media, saving
estimates. Driven through `run`, the way a view reaches them."""
import pytest

from model.transfer_map import TransferMap


@pytest.fixture
def tm(tmp_path):
    model = TransferMap(db_path=tmp_path / "map.sqlite")
    model.open()
    store = model._store

    def add(**kw):
        base = {"started_at": "2026-10-06T10:00:00", "tip_id": "T1", "tilt_deg": 6.0,
                "speed_steps_s": 250.0, "status": "recorded", "origin": "recorded",
                "sample_id": "S1", "chip_id": "1", "flake_id": "2", "cut_id": "1"}
        base.update(kw)
        return store.insert(base)
    model.add = add
    yield model
    model.close()


def test_the_finalizer_commands_are_declared_and_invisible(tm):
    from schema import elements
    declared = {e.get("command"): e for e in elements(tm.schema)}
    for name in ("open_finalizer", "finalize_queue", "finalize_media", "finalize_save"):
        assert name in declared, name
    for name in ("finalize_queue", "finalize_media", "finalize_save"):
        assert declared[name]["type"] == "internal"
    assert declared["open_finalizer"]["type"] == "button"


def test_open_finalizer_asks_the_view_to_open_its_window(tm):
    assert tm.run("open_finalizer").value == "open:finalizer"


def test_the_queue_lists_unfilled_trials_in_walk_order(tm):
    a, b = tm.add(cut_id="1"), tm.add(cut_id="2", flake_id="1")
    queue = tm.run("finalize_queue").value
    assert [t["id"] for t in queue] == [b, a]            # flake 1 before flake 2
    assert queue[0]["missing"][0] == "width_um"
    only = tm.run("finalize_queue", None, (True,)).value
    assert [t["id"] for t in only] == [b, a]


def test_save_writes_afm_and_optical_estimates_and_keeps_the_row(tm):
    t = tm.add()
    result = tm.run("finalize_save", None, (t, {
        "width_um": "1.8", "width_sigma_um": "0.1", "thickness_nm": "12",
        "channel_height_nm": "3", "trench_depth_nm": "2.5",
        "width_optical_um": "2.0", "width_optical_method": "reticle"}))
    assert result.is_ok, result.reason
    row = tm._store.trial(t)
    assert (row["width_um"], row["width_sigma_um"], row["thickness_nm"]) == (1.8, 0.1, 12.0)
    assert (row["channel_height_nm"], row["trench_depth_nm"]) == (3.0, 2.5)
    assert (row["width_optical_um"], row["width_optical_method"]) == (2.0, "reticle")
    assert row["status"] == "measured"
    assert row["tilt_deg"] == 6.0 and row["cut_id"] == "1"       # untouched
    assert result.value["missing"] == []


def test_save_an_optical_estimate_alone_leaves_the_trial_recorded(tm):
    t = tm.add()
    assert tm.run("finalize_save", None, (t, {"width_optical_um": "2.2"})).is_ok
    row = tm._store.trial(t)
    assert row["status"] == "recorded" and row["width_um"] is None
    assert row["width_optical_um"] == 2.2 and row["width_optical_method"] == "estimate"


def test_save_with_nothing_typed_changes_nothing_and_is_not_an_error(tm):
    t = tm.add()
    before = tm._store.trial(t)
    assert tm.run("finalize_save", None, (t, {"width_um": ""})).is_ok
    assert tm._store.trial(t) == before


def test_save_refuses_bad_input_without_writing_anything(tm):
    t = tm.add()
    result = tm.run("finalize_save", None, (t, {"thickness_nm": "5", "width_um": "-1"}))
    assert result.is_refused and "width_um" in result.reason
    assert tm._store.trial(t)["thickness_nm"] is None          # all or nothing


def test_save_refuses_an_unknown_or_armed_trial(tm):
    assert tm.run("finalize_save", None, (99, {"width_um": "1"})).is_refused
    t = tm.add(status="armed")
    assert "armed" in tm.run("finalize_save", None, (t, {"width_um": "1"})).reason


def test_an_aborted_trial_stays_aborted(tm):
    t = tm.add(status="aborted")
    assert tm.run("finalize_save", None, (t, {"width_um": "1"})).is_ok
    assert tm._store.trial(t)["status"] == "aborted"


def test_media_names_the_stills_and_the_video(tm, tmp_path):
    t = tm.add()
    folder = tm.pictures_root / str(t)
    folder.mkdir(parents=True)
    (folder / "first_frame.png").write_bytes(b"\x89PNGfirst")
    (folder / "trial.mp4").write_bytes(b"mp4")
    tm._store.update(t, {"video_path": str(folder / "trial.mp4"), "video_frames": 10})
    media = tm.run("finalize_media", None, (t,)).value
    assert media["first_frame"] == b"\x89PNGfirst"
    assert media["mark_frame"] == b"" and media["before_full"] == b""
    assert media["video_path"] == str(folder / "trial.mp4")
    assert media["video_kind"] == "file" and media["frames"] == 10


def test_media_of_a_frames_folder_lists_its_jpegs_in_order(tm):
    t = tm.add()
    folder = tm.pictures_root / str(t) / "frames"
    folder.mkdir(parents=True)
    for n in (2, 0, 1):
        (folder / f"{n:06d}.jpg").write_bytes(b"j")
    tm._store.update(t, {"video_path": str(folder), "video_frames": 3})
    media = tm.run("finalize_media", None, (t,)).value
    assert media["video_kind"] == "frames"
    assert [p.rsplit("/", 1)[1] for p in media["frame_paths"]] == [
        "000000.jpg", "000001.jpg", "000002.jpg"]


def test_media_of_a_trial_with_no_video_says_so(tm):
    t = tm.add()
    media = tm.run("finalize_media", None, (t,)).value
    assert media["video_kind"] == "none" and media["video_path"] == ""
    assert tm.run("finalize_media", None, (99,)).is_refused
