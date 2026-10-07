"""`model.transfer_map`: the Transfer Map model, its store and its guided trial.

Every test uses a temporary database (owner ruling 2026-09-27: tests never
touch the project database). Red Percent is the real model over an injected
capture factory (`tests/test_red_monitor.py`'s fake screen), so the samples
arrive through the real subscribe hook on the real run thread; the tilt and
speed sources are duck-typed stand-ins, plus the real SIM Rotator.
"""
import csv
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

import schema as sch
from controller.controller import Controller
from model import plot_data
from model import transfer_map as tm_module
from model.red_monitor import RedMonitor
from model.rotator import Rotator
from model.transfer_map import TransferMap
from events import events
from result import NeedsConfirm, Refused
from test_red_monitor import DesktopCapture, desktop_screen, fake_screen


@pytest.fixture(autouse=True)
def private_db(tmp_path, monkeypatch):
    """No test reaches the project database: the override points at tmp."""
    path = tmp_path / "db" / "transfer_map.sqlite"
    monkeypatch.setenv("STATION_MAP_DB", str(path))
    return path


@pytest.fixture(autouse=True)
def jpeg_recorder(request, monkeypatch):
    """The map tests record on the JPEG path: `devices.video`'s lazy import
    of the encoder answers None, so no test here starts an ffmpeg (the
    encoder itself is `tests/test_video.py`'s). A test that asks for the
    `real_encoder` fixture keeps the real MP4 path; there is ONE, the
    end-to-end `test_frames_flow_from_red_percent_into_the_trials_video`
    (grep `real_encoder`)."""
    if "real_encoder" in request.fixturenames:
        return
    from devices import video
    monkeypatch.setattr(video, "_encoder", lambda: None)


@pytest.fixture
def real_encoder():
    """Opt out of `jpeg_recorder`: this test records a real H.264 MP4."""
    return pytest.importorskip("imageio_ffmpeg")


class FakeRotator:
    """A tilt source, duck-typed as the Transfer Map reads one."""
    def __init__(self, angle=22.5):
        self.position_deg = angle


class FakeProbe:
    """A speed and Z source, duck-typed like a probe."""
    def __init__(self, mode="manual", man=300, full=500, z=1000.0):
        self.mode_name = mode
        self.man_full_speed = man
        self.full_speed = full
        self.position = (0.0, 0.0, z)
        self.position_time = time.monotonic()


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def red(tmp_path):
    model = RedMonitor(screen=desktop_screen())
    model.output_root = tmp_path / "runs"
    model.run_name = "C001"
    model.open()
    model.set_region(0, 0, 10, 10)
    yield model
    model.close()


@pytest.fixture
def station(red):
    """A Transfer Map beside a running Red Percent, a rotator and a probe."""
    rotator, probe = FakeRotator(), FakeProbe()
    model = TransferMap()
    model.open()
    for name, other in (("Red Percent", red), ("Rotator", rotator),
                        ("Stepper Probe", probe)):
        model.on_model_added(name, other)
    red.source_name = "Stepper Probe"
    red.start_run(confirmed=True)
    model.tip_id = "tip-A"
    yield model, red, rotator, probe
    model.close()


def _rows(path, sql, *args):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, args)]


def _rows_raw(path, sql, *args):
    with sqlite3.connect(path) as db:
        return [tuple(r) for r in db.execute(sql, args)]


def _confirmed(model, command, inputs=None):
    """Run `command` as a view does: answer its question, if it asks one,
    by re-running with `confirmed=True` and the inputs it names."""
    result = model.run(command, inputs)
    if result.needs_confirm:
        result = model.run(result.command, result.inputs, (*result.args, True))
    assert result.is_ok, result
    return result.value


def _arm(model, tip=None):
    return _confirmed(model, "arm_trial",
                      {"tip_id": tip if tip is not None else model.tip_id})


def _finish(model, note=""):
    return _confirmed(model, "finish_trial", {"note": note})


def _record(model, red, samples=20, mark=True, note=""):
    trial = _arm(model)
    start = len(model._trial.samples)
    assert _wait_for(lambda: len(model._trial.samples) >= start + samples)
    if mark:
        model.mark_force()
    assert _wait_for(lambda: len(model._trial.samples) >= start + samples + 5)
    _finish(model, note)
    return trial


# -- the class and the contract ----------------------------------------------

def test_the_class_declares_a_portless_model():
    assert TransferMap.NAME == "Transfer Map"
    assert TransferMap.IDENTITY is None
    assert TransferMap.RESOURCES == ()
    model = TransferMap(port="SIM", gamepad=None, sim=True)
    assert model.devices == [] and model._expects_heartbeat() is False
    assert model._halt_hardware() is True
    assert model.is_active is False


def test_the_database_defaults_to_the_projects_data_directory(monkeypatch):
    """Owner ruling 2026-09-27: local to the checkout, derived from the
    source tree, never a per-user location."""
    monkeypatch.delenv("STATION_MAP_DB", raising=False)
    root = Path(tm_module.__file__).resolve().parents[2]
    assert (root / "src").is_dir()
    assert TransferMap.default_db_path() == root / "data" / "transfer_map.sqlite"


def test_station_map_db_overrides_the_path(private_db):
    model = TransferMap()
    assert model.db_path == private_db
    assert model.output_root == private_db.parent
    assert model.state["values"]["db_path"] == str(private_db)
    assert model.state["output_root"] == str(private_db.parent)


def test_construction_creates_nothing(private_db):
    """Construction, state and the figure leave no file behind (the
    contract test builds every registered class); `open` is what creates it."""
    model = TransferMap(port="SIM", gamepad=None, sim=True)
    model.state
    model.figure
    assert not private_db.exists()
    assert not private_db.parent.exists()


# -- T4: the database at launch -------------------------------------------------

def _titled(title, since):
    return [e for e in events.since(since) if e.title == title]


def test_open_creates_the_database_and_says_where(private_db):
    """Bench 2026-09-27: "no prompt to create the db on startup". Opening
    the model creates the file and its schema and names it in the events."""
    since = events.latest_id
    model = TransferMap(port="SIM", gamepad=None, sim=True)
    model.open()
    try:
        assert private_db.is_file()
        tables = {r["name"] for r in _rows(private_db, "SELECT name FROM "
                                           "sqlite_master WHERE type='table'")}
        assert {"trials", "profile"} <= tables
        ready = _titled("Database Ready", since)
        assert len(ready) == 1
        assert ready[0].message == f"{private_db}: 0 trial(s)"
        assert ready[0].source == "Transfer Map"
    finally:
        model.close()


def test_open_on_an_existing_database_counts_and_keeps_its_trials(private_db):
    first = TransferMap()
    first._store.insert({"tip_id": "T1", "status": "recorded"})
    since = events.latest_id
    model = TransferMap()
    model.open()
    try:
        assert _titled("Database Ready", since)[0].message == \
            f"{private_db}: 1 trial(s)"
        assert model.trial_count == 1
        assert model._store.ensure() is False          # a no-op now
        assert _rows(private_db, "SELECT tip_id FROM trials") == [{"tip_id": "T1"}]
    finally:
        model.close()


def test_open_with_an_unwritable_folder_still_opens_and_says_so(tmp_path):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("a file where the database folder should be")
    since = events.latest_id
    model = TransferMap(db_path=blocker / "m.sqlite")
    model.open()                                   # never raises
    try:
        assert _titled("Database Not Ready", since)
        assert not _titled("Database Ready", since)
    finally:
        model.close()


def test_the_session_section_leads_tier_one():
    model = TransferMap()
    first = model.schema["sections"][0]
    assert first["title"] == "Session" and first.get("tier", 1) == 1
    keys = [e.get("model_attr") or e.get("command") for e in first["elements"]]
    assert keys == ["db_path", "trial_count", "new_database"]
    button = first["elements"][2]
    assert button["text"] == "New session database"
    assert button["confirm"] == ("Start a new database beside this one? The "
                                 "current one stays on disk.")
    diagnostics = next(s for s in model.schema["sections"]
                       if s["title"] == "Diagnostics")
    assert "db_path" not in [e.get("model_attr") for e in diagnostics["elements"]]


def test_new_database_starts_beside_the_old_one(station, private_db):
    model, red, *_ = station
    old_trial = _record(model, red)
    old_before = _rows(private_db, "SELECT before_full_path FROM trials")[0]["before_full_path"]
    old_png = Path(old_before).read_bytes()
    assert model.figure[:8] == b"\x89PNG\r\n\x1a\n"
    since = events.latest_id
    result = model.run("new_database")
    assert result.is_ok, result
    new_path = Path(result.value)
    assert new_path.parent == private_db.parent == model.output_root
    assert new_path.name.startswith("transfer_map_") and new_path.suffix == ".sqlite"
    assert model.db_path == new_path and new_path.is_file()
    assert model.state["values"]["db_path"] == str(new_path)
    assert model.trial_count == 0 and model.figure == b""
    assert _titled("New Database", since)
    # the old one stays on disk, whole
    assert [r["id"] for r in _rows(private_db, "SELECT id FROM trials")] == [old_trial]
    # a trial in the new database starts at 1 again and must not overwrite
    # the old database's trial 1 pictures
    trial = _record(model, red)
    assert trial == 1 == old_trial
    assert Path(old_before).read_bytes() == old_png
    new_before = _rows(new_path, "SELECT before_full_path FROM trials")[0]["before_full_path"]
    assert Path(new_before) != Path(old_before)
    assert Path(new_before).parent.parent.parent == model.output_root


def test_new_database_twice_in_one_second_gets_two_files(station):
    model = station[0]
    first = Path(model.new_database())
    second = Path(model.new_database())
    assert first != second and first.is_file() and second.is_file()


def test_new_database_refuses_while_armed(station, private_db):
    model = station[0]
    _arm(model)
    result = model.run("new_database")
    assert result.is_refused
    assert model.db_path == private_db
    with pytest.raises(Refused, match="Finish or abort"):
        model.new_database()


def test_the_rotator_reads_its_tilt_as_position_deg():
    rotator = Rotator(sim=True)
    assert rotator.position_deg is None
    rotator._position = 12.5
    assert rotator.position_deg == 12.5
    with pytest.raises(AttributeError):
        rotator.position_deg = 3.0


# -- arming ----------------------------------------------------------------------

def test_arm_refuses_without_red_percent():
    model = TransferMap()
    model.tip_id = "tip-A"
    with pytest.raises(Refused, match="Red Percent"):
        model.arm_trial()


def test_arm_refuses_without_a_capture_region(tmp_path, private_db):
    bare = RedMonitor(screen=fake_screen())
    bare.output_root = tmp_path / "runs"
    bare.open()
    try:
        model = TransferMap()
        model.on_model_added("Red Percent", bare)
        model.tip_id = "tip-A"
        with pytest.raises(Refused, match="capture region"):
            model.arm_trial(True)
        assert not bare.is_running and not model.is_armed
        assert model.trial_count == 0
    finally:
        bare.close()


def test_arm_refuses_without_a_tip_id(station):
    model = station[0]
    model.tip_id = "  "
    with pytest.raises(Refused, match="tip ID"):
        model.arm_trial()


def test_arm_refuses_while_latched(station):
    model = station[0]
    model.estop()
    result = model.run("arm_trial", {"tip_id": "tip-A"})
    assert result.is_refused


def test_arm_snapshots_tilt_speed_and_the_before_frame(station, private_db):
    model, red, rotator, probe = station
    trial = _arm(model)
    assert model.is_active and model.mode_name == "armed"
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "armed" and row["tip_id"] == "tip-A"
    assert row["tilt_deg"] == 22.5 and row["speed_steps_s"] == 300
    # V4: the region stills are gone; the whole screen at Arm stays
    assert row["before_path"] is None
    before = Path(row["before_full_path"])
    assert before == private_db.parent / "transfer_map" / str(trial) / "before_full.png"
    assert before.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(Refused, match="already armed"):
        model.arm_trial()


def test_speed_follows_the_probes_mode(station):
    model, red, rotator, probe = station
    probe.mode_name = "autonomous"
    assert model.speed_now == 500
    probe.mode_name = "idle"
    assert model.speed_now is None
    probe.live_speed = 812
    assert model.speed_now == 812


def test_tilt_is_none_until_a_rotator_reads_or_the_operator_types_one(red):
    """A SIM Rotator reads None; the trial records no tilt rather than a
    default 0 degrees (found in the headless launch, 2026-09-27)."""
    model = TransferMap()
    model.on_model_added("Red Percent", red)
    model.on_model_added("Rotator", Rotator(sim=True))
    assert model.tilt_now is None
    model.typed_tilt = "17"
    assert model._read_tilt() == (17.0, "typed")
    model.on_model_added("Rotator 2", FakeRotator(30.0))
    assert model.tilt_now == 17.0          # typed, so the reading waits
    model.typed_tilt = ""
    assert model.tilt_now == 30.0


def test_a_typed_tilt_wins_over_the_rotator_reading(station):
    """Bench 2026-09-28: the Rotator read 0.0 on two trials tilted by hand
    to 6.5 and 7 deg, and the typed tilt was ignored, so the tilt could not
    be set per trial. Typed wins; blank the entry and the rotator is the
    source again; the Arm prompt names which."""
    model, red, rotator, _ = station
    rotator.position_deg = 0.0
    model.typed_tilt = "6.5"
    assert model._read_tilt() == (6.5, "typed")
    assert model.tilt_now == 6.5
    asked = model.run("arm_trial", {"tip_id": "tip-A", "typed_tilt": "7"})
    assert asked.needs_confirm and " at 7 deg (typed)," in asked.reason
    trial = _arm(model)
    row = model._store.trial(trial)
    assert row["tilt_deg"] == 7.0 and row["tilt_source"] == "typed"
    _finish(model)
    model.typed_tilt = ""
    assert model._read_tilt() == (0.0, "Rotator")


def test_arm_refuses_a_typed_tilt_that_is_not_a_number(station):
    model = station[0]
    result = model.run("arm_trial", {"tip_id": "t", "typed_tilt": "steep"})
    assert result.is_refused and "Tilt" in result.reason


# -- recording -------------------------------------------------------------------

def test_a_recorded_trial_keeps_its_raw_profile_marks_and_frames(station, private_db):
    model, red, rotator, probe = station
    trial = _record(model, red, note="first cut")
    assert not model.is_active and red._subscribers == ()
    assert red._frame_subscribers == ()
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "recorded" and row["note"] == "first cut"
    assert row["mark_operator_t"] is not None and row["z_contact"] == 1000.0
    assert row["red_baseline"] is not None
    assert row["mark_auto_max_t"] is not None
    assert row["red_min"] <= row["red_max"]
    assert Path(row["video_path"]).exists() and row["video_frames"] > 0
    profile = _rows(private_db, "SELECT * FROM profile WHERE trial_id=? "
                    "ORDER BY t_s", trial)
    assert len(profile) >= 25
    assert all(p["z"] == 1000.0 for p in profile)     # read from the probe
    times = [p["t_s"] for p in profile]
    assert times == sorted(times) and times[0] >= 0


def test_the_profile_matches_what_red_percent_logged(station, private_db):
    model, red, *_ = station
    trial = _arm(model)
    assert _wait_for(lambda: len(model._trial.samples) >= 30)
    _finish(model)
    reds = [p["red"] for p in _rows(private_db, "SELECT red FROM profile "
                                    "WHERE trial_id=? ORDER BY rowid", trial)]
    logged = list(red._run.log.red_values)
    # the trial's reds are a contiguous run of Red Percent's own rows
    start = logged.index(reds[0])
    assert logged[start:start + len(reds)] == reds


def test_mark_force_is_refused_unless_armed(station):
    model = station[0]
    assert model.run("mark_force").is_refused
    _arm(model)
    assert model.run("mark_force").is_ok


def test_finish_with_no_samples_still_records(red, private_db):
    model = TransferMap()
    model.on_model_added("Red Percent", red)
    red.start_run(confirmed=True)
    model.tip_id = "t"
    red.unsubscribe  # the hook exists
    trial = _arm(model)
    model._trial.samples.clear()
    red.unsubscribe(model._on_sample)
    _finish(model)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "recorded" and row["mark_auto_max_t"] is None
    model.close()


# -- the stop --------------------------------------------------------------------

def test_estop_aborts_the_armed_trial_and_keeps_its_profile(station, private_db):
    model, red, *_ = station
    trial = _arm(model)
    assert _wait_for(lambda: len(model._trial.samples) >= 10)
    started = time.monotonic()
    assert model.estop() is True
    assert time.monotonic() - started < 0.5
    assert not model.is_active and red._subscribers == ()
    model.disable()                          # joins the persist
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "aborted"
    assert _rows(private_db, "SELECT COUNT(*) AS n FROM profile WHERE trial_id=?",
                 trial)[0]["n"] >= 10


def test_the_stop_never_waits_on_a_held_lock(station):
    """`_halt_hardware` must not wait on a lock without a timeout."""
    model = station[0]
    _arm(model)
    with model._lock:
        done = threading.Event()
        threading.Thread(target=lambda: (model._halt_hardware(), done.set()),
                         daemon=True).start()
        assert done.wait(1.0), "the stop blocked on the model's lock"


def test_the_station_stop_confirms_every_model_with_a_trial_armed(station):
    model, red, *_ = station
    controller = Controller()
    controller.add("Red Percent", red)
    controller.add("Transfer Map", model)
    _arm(model)
    results = controller.estop_all()
    assert results == {"Red Percent": True, "Transfer Map": True}
    assert not red.is_running and not model.is_active
    for name in ("Transfer Map", "Red Percent"):
        controller._models.pop(name)         # the fixtures close them


def test_abort_is_a_stop_that_ignores_bad_entry_text(station, private_db):
    model = station[0]
    button = next(e for e in sch.elements(model.schema)
                  if e.get("command") == "abort_trial")
    assert button.get("stop") is True
    trial = _arm(model)
    result = model.run("abort_trial", {"width_um": "not a number"})
    assert result.is_ok, result
    model.disable()
    assert _rows(private_db, "SELECT status FROM trials WHERE id=?",
                 trial)[0]["status"] == "aborted"


def test_close_with_an_armed_trial_saves_it_as_aborted(station, private_db):
    model = station[0]
    trial = _arm(model)
    model.close()
    assert _rows(private_db, "SELECT status FROM trials WHERE id=?",
                 trial)[0]["status"] == "aborted"


def test_a_late_sample_after_finish_is_ignored(station):
    model, *_ = station
    _arm(model)
    _finish(model)
    model._on_sample(9.0, 50.0, {})           # a row in flight at the swap
    assert model._trial is None


# -- after the trial -------------------------------------------------------------

def test_mark_broke_on_the_armed_then_the_last_trial(station, private_db):
    model, red, *_ = station
    _arm(model)
    model.mark_broke(True)
    assert model.is_broke is True
    trial = _finish(model)
    assert _rows(private_db, "SELECT broke FROM trials WHERE id=?", trial)[0]["broke"] == 1
    model.mark_broke(False)
    assert _rows(private_db, "SELECT broke FROM trials WHERE id=?", trial)[0]["broke"] == 0
    assert model.is_broke is False


def test_attach_afm_measures_a_trial(station, private_db):
    model, red, *_ = station
    trial = _record(model, red)
    result = model.run("attach_afm", {"afm_trial_id": str(trial), "width_um": "4.2",
                                      "width_sigma_um": "0.3",
                                      "thickness_nm": "12", "thickness_sigma_nm": "0"})
    assert result.is_ok, result
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "measured" and row["width_um"] == 4.2
    assert row["width_sigma_um"] == 0.3 and row["thickness_nm"] == 12
    assert row["thickness_sigma_nm"] is None          # 0 = not given


def test_attach_afm_refuses_an_unknown_trial_or_no_width(station):
    model, red, *_ = station
    trial = _record(model, red)
    assert "No trial 99" in model.run("attach_afm", {"afm_trial_id": "99",
                                                     "width_um": "3"}).reason
    assert model.run("attach_afm", {"afm_trial_id": str(trial),
                                    "width_um": "0"}).is_refused


def test_delete_asks_then_removes_the_trial_and_its_profile(station, private_db):
    model, red, *_ = station
    trial = _record(model, red)
    model.trial_pick = trial
    with pytest.raises(NeedsConfirm) as asked:
        model.delete_trial()
    assert asked.value.command == "delete_trial"
    model.delete_trial(True)
    assert _rows(private_db, "SELECT * FROM trials WHERE id=?", trial) == []
    assert _rows(private_db, "SELECT * FROM profile WHERE trial_id=?", trial) == []


# -- export and import -----------------------------------------------------------

def test_export_writes_both_tables_inside_the_output_root(station):
    model, red, *_ = station
    _record(model, red)
    trials_path = Path(model.export_csv())
    profile_path = Path(model.export_profile_csv())
    for path in (trials_path, profile_path):
        assert path.is_file()
        assert os.path.commonpath([path.resolve(), model.output_root.resolve()]) \
            == str(model.output_root.resolve())
    rows = list(csv.DictReader(trials_path.open()))
    assert rows[0]["status"] == "recorded"
    assert "force_shadow_vs_peak" in rows[0]
    assert list(csv.DictReader(profile_path.open()))[0]["trial_id"] == rows[0]["id"]


def test_export_with_no_trials_is_refused():
    with pytest.raises(Refused):
        TransferMap().export_csv()


def test_import_typed_trials_and_they_reach_the_map(tmp_path, private_db):
    typed = tmp_path / "typed.csv"
    typed.write_text("tilt_deg,speed_steps_s,force_index,width_um,tip_id\n"
                     "10,100,0.2,5.0,t1\n20,200,0.5,,t1\n,300,0.7,6,t1\n")
    model = TransferMap()
    assert model.import_csv(str(typed)) == {"imported": 2, "skipped": 1}
    rows = _rows(private_db, "SELECT * FROM trials ORDER BY id")
    assert [r["origin"] for r in rows] == ["imported", "imported"]
    assert [r["status"] for r in rows] == ["measured", "recorded"]
    assert "given" in model.force_definition_options
    model.set_force_definition("given")
    trials = model._map_rows()
    assert [t["force"]["given"] for t in trials] == [0.2, 0.5]


def test_an_export_imports_back(station, tmp_path):
    model, red, *_ = station
    _record(model, red)
    exported = model.export_csv()
    fresh = TransferMap(db_path=tmp_path / "other" / "m.sqlite")
    assert fresh.import_csv(exported)["imported"] == 1
    row = fresh._map_rows()[0]
    assert row["force"]["shadow_vs_peak"] is not None


def test_import_refuses_a_file_without_tilt_and_speed(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    with pytest.raises(Refused, match="tilt"):
        TransferMap().import_csv(str(bad))


# -- the figure and the schema ---------------------------------------------------

def test_the_figure_is_empty_until_there_is_a_trial_then_a_png(station):
    model, red, *_ = station
    assert model.figure == b""
    _record(model, red)
    for label in model.figure_type_options:
        assert model.run("set_figure_type", None, (label,)).is_ok
        assert model.figure[:8] == b"\x89PNG\r\n\x1a\n", label


def test_the_figure_is_cached_until_something_changes(station):
    model, red, *_ = station
    _record(model, red)
    model.set_figure_type(model.figure_type_options[-1])
    first = model.figure
    assert model.figure is first
    model.set_force_definition(model.force_definition_options[1])
    assert model.figure is not first


def test_dropdowns_refuse_what_they_do_not_offer():
    model = TransferMap()
    for command in ("set_figure_type", "set_force_definition"):
        assert model.run(command, None, ("nonsense",)).is_refused


def test_the_detector_numbers_and_the_force_indices_are_published(station):
    model, red, *_ = station
    _record(model, red)
    text = model.last_trial_numbers
    assert "peak" in text and "shadow_vs_peak" in text


def test_tier_one_holds_the_trial_keys_and_tier_two_the_configuration():
    model = TransferMap()
    tiers = {}
    for section in model.schema["sections"]:
        for element in section["elements"]:
            key = element.get("command") or element.get("data_command") \
                or element.get("source_command") or element.get("model_attr")
            tiers[key] = section.get("tier", 1)
    for key in ("arm_trial", "mark_force", "finish_trial", "abort_trial",
                "figure", "tip_id", "tilt_now", "speed_now", "red_now",
                "trial_count", "trial_status", "db_path", "new_database",
                "first_frame_image", "mark_frame_image", "video_status",
                "tip_status"):
        assert tiers[key] == 1, key
    for key in ("set_figure_type", "set_force_definition", "attach_afm",
                "export_csv", "import_csv", "before_full_image",
                "export_tips_csv", "retire_tip", "unretire_tip",
                "set_tip_note", "tip_note"):
        assert tiers[key] == 2, key
    for key in ("trials_log", "tips_log", "delete_trial", "last_trial_numbers",
                "video_encoder"):
        assert tiers[key] == 3, key
    for gone in ("before_image", "mark_image", "after_image", "mark_full_image",
                 "after_full_image"):
        assert gone not in tiers, gone
    disclosures = {s.get("disclosure") for s in model.schema["sections"]
                   if s.get("tier") == 2}
    assert disclosures == {"Configure Transfer Map"}


def test_the_trials_log_lists_one_line_per_trial(station):
    model, red, *_ = station
    _record(model, red)
    lines = model.trials_log
    assert len(lines) == 1 and "recorded" in lines[0] and "tip-A" in lines[0]


# -- T5: trials on this tip -----------------------------------------------------

def test_trials_on_this_tip_counts_the_typed_tip(station):
    """Bench 2026-09-27: "I need to see how many trials have been done on a
    given tip ID"."""
    model, red, *_ = station
    values = lambda: model.state["values"]            # noqa: E731
    model.tip_id = ""
    assert values()["tip_trial_count"] == ""          # blank entry: nothing
    model.tip_id = "tip-A"
    assert values()["tip_trial_count"] == "0"         # a new tip
    _record(model, red)
    _record(model, red)
    model.tip_id = "  tip-A "
    assert values()["tip_trial_count"] == "2"         # stripped
    model.tip_id = "tip-B"
    assert values()["tip_trial_count"] == "0"
    _record(model, red)
    assert values()["tip_trial_count"] == "1"
    assert model._store.count_for_tip("tip-A") == 2
    assert model._store.count_for_tip("tip-A", up_to=1) == 1


def test_trials_on_this_tip_sits_under_the_tip_id_entry():
    model = TransferMap()
    trial = next(s for s in model.schema["sections"] if s["title"] == "Trial")
    keys = [e.get("model_attr") or e.get("command") for e in trial["elements"]]
    at = keys.index("tip_id")
    assert keys[at + 3] == "tip_trial_count"     # after Known tips and New tip
    element = trial["elements"][at + 3]
    assert element["type"] == "readonly" and element["text"] == "Trials on this tip"


@pytest.mark.parametrize("n, word", [(1, "1st"), (2, "2nd"), (3, "3rd"),
                                     (4, "4th"), (11, "11th"), (12, "12th"),
                                     (13, "13th"), (21, "21st"), (22, "22nd"),
                                     (101, "101st"), (111, "111th")])
def test_ordinals_read_as_the_operator_says_them(n, word):
    assert tm_module._ordinal(n) == word


def test_arm_and_finish_name_the_trials_place_on_its_tip(station):
    model, red, *_ = station
    _record(model, red)
    model.tip_id = "T7"
    since = events.latest_id
    trial = _record(model, red)
    armed = _titled("Trial Armed", since)[0].message
    assert armed.startswith(f"Trial {trial} armed, the 1st on tip T7.")
    since = events.latest_id
    trial = _record(model, red)
    assert _titled("Trial Armed", since)[0].message.startswith(
        f"Trial {trial} armed, the 2nd on tip T7.")
    recorded = _titled("Trial Recorded", since)[0].message
    assert recorded.startswith(f"Trial {trial} recorded, the 2nd on tip T7:")


# -- T1: Red Percent's controls, on the trial sheet ----------------------------

def _element(model, key):
    return next(e for e in sch.elements(model.schema)
                if key in (e.get("command"), e.get("model_attr"),
                           e.get("data_command")))


def test_the_capture_region_is_red_percents_set_from_the_sheet(red):
    """Bench 2026-09-27: "the red percent and transfer map are decoupled?
    They should be unified". The region is set on the trial sheet."""
    model = TransferMap()
    assert model.region is None and model.state["values"]["region"] == ""
    assert model.state["has_region"] is False
    refused = model.run("set_region", None, (1, 2, 30, 40))
    assert refused.is_refused and "Open Red Percent" in refused.reason
    model.on_model_added("Red Percent", red)
    assert model.region == red.region and model.state["has_region"] is True
    result = model.run("set_region", None, (1, 2, 30, 40))
    assert result.is_ok, result
    assert red.region == {"top": 2, "left": 1, "width": 30, "height": 40}
    assert model.region == red.region
    assert model.state["values"]["region"] == sch.format_region(red.region)
    assert model.run("set_region", None, (0, 0, 0, 5)).is_refused   # red's own check


def test_the_region_is_fixed_while_a_trial_is_armed(station):
    model, red, *_ = station
    _arm(model)
    before = dict(red.region)
    result = model.run("set_region", None, (5, 5, 20, 20))
    assert result.is_refused and "fixed" in result.reason
    assert red.region == before


def test_the_region_picker_reads_red_percents_screen(red, monkeypatch):
    model = TransferMap()
    assert model.screen_image is None
    assert model.run("screen_image").is_ok            # a declared data source
    model.on_model_added("Red Percent", red)
    bounds = {"left": 0, "top": 0, "width": 8, "height": 6}
    monkeypatch.setattr(red.screen, "screenshot_png", lambda **kw: (b"PNG!", bounds))
    assert model.screen_image == {"image": b"PNG!", **bounds}
    element = _element(model, "set_region")
    assert element["type"] == "region_select"
    assert element["text"] == "Set capture region"
    assert element["model_attr"] == "region"
    assert element["data_command"] == "screen_image"


def test_the_next_step_walks_the_operator_through_a_trial(red):
    model = TransferMap()
    step = lambda: model.state["values"]["next_step"]  # noqa: E731
    assert _element(model, "next_step")["role"] == "info"
    assert step() == "Open Red Percent"
    bare = RedMonitor(screen=fake_screen())
    model.on_model_added("Red Percent", bare)
    # M3: with neither set, the line says the polling is the sheet's to start.
    assert step() == "Set the capture region and a tip ID"
    model.tip_id = "tip-A"
    assert step() == "Set the capture region"
    model.tip_id = ""
    model.on_model_removed("Red Percent", bare)
    model.on_model_added("Red Percent", red)
    model.tip_id = " "
    assert step() == "Type a tip ID"
    model.tip_id = "tip-A"
    assert step() == "Type the tilt for this trial"
    model.typed_tilt = "6.5"
    assert step() == "Type the sample, chip, flake, cut IDs"
    model.sample_id, model.chip_id, model.flake_id, model.cut_id = "S1", "C1", "F2", "3"
    assert step() == "Press Arm trial"
    if not red.is_running:
        red.start_run(confirmed=True)       # before T2, Arm needs a run
    _arm(model)
    assert step() == "Lower the tip; press Mark force when the force is right"
    model.mark_force()
    assert step() == "Press Finish trial"
    _finish(model)
    assert step() == "Type the cut ID"       # cut and go: the next cut's own ID
    model.cut_id = "4"
    assert step() == "Press Arm trial"
    model.estop()
    assert step() == ""                      # latched: the stop says what to do
    model.close()


# -- T2: Arm owns the run --------------------------------------------------------
# The stop path first: every way a trial ends must end the run the map
# started, and only that run, including when Red Percent is gone or stopped.

@pytest.fixture
def idle_station(red):
    """A Transfer Map beside a Red Percent with a region and no run."""
    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", red)
    model.on_model_added("Stepper Probe", FakeProbe())
    model.tip_id = "tip-A"
    assert not red.is_running
    yield model, red
    model.close()


def test_estop_ends_the_run_the_map_started(idle_station, private_db):
    model, red = idle_station
    trial = _arm(model)
    assert red.is_running
    assert _wait_for(lambda: len(model._trial.samples) >= 5)
    started = time.monotonic()
    assert model.estop() is True
    assert time.monotonic() - started < 0.5
    assert not red.is_running and not model.is_armed
    model.disable()
    assert _rows(private_db, "SELECT status FROM trials WHERE id=?",
                 trial)[0]["status"] == "aborted"


def test_abort_ends_the_run_the_map_started(idle_station):
    model, red = idle_station
    _arm(model)
    assert model.run("abort_trial").is_ok
    assert not red.is_running


def test_finish_ends_the_run_the_map_started(idle_station, private_db):
    model, red = idle_station
    trial = _arm(model)
    assert _wait_for(lambda: len(model._trial.samples) >= 5)
    _finish(model)
    assert not red.is_running
    assert _rows(private_db, "SELECT status FROM trials WHERE id=?",
                 trial)[0]["status"] == "recorded"


def test_a_run_the_operator_started_is_left_running(station):
    """The station fixture starts the run on the Red Percent page."""
    model, red, *_ = station
    run = red.run_id
    _record(model, red)
    assert red.is_running and red.run_id == run
    _arm(model)
    assert model.run("abort_trial").is_ok and red.is_running
    _arm(model)
    assert model.estop() is True
    assert red.is_running and red.run_id == run


def test_a_later_run_the_operator_started_is_not_the_maps_to_end(idle_station):
    """The map started a run; the operator ended it on Red Percent and
    started their own. Finishing the trial leaves the operator's run alone."""
    model, red = idle_station
    _arm(model)
    red.end_run()
    red.start_run(confirmed=True)
    theirs = red.run_id
    assert model.run("abort_trial").is_ok
    assert red.is_running and red.run_id == theirs


def test_the_stop_works_when_red_percent_is_gone(idle_station):
    model, red = idle_station
    _arm(model)
    model.on_model_removed("Red Percent", red)
    assert model.estop() is True
    assert not model.is_armed
    red.end_run()                        # the removed model's own close


def test_abort_works_when_red_percent_already_stopped(idle_station):
    model, red = idle_station
    _arm(model)
    red.end_run()
    assert model.run("abort_trial").is_ok and not model.is_armed
    assert model.estop() is True


def test_arm_starts_red_percents_run_when_none_is_running(idle_station):
    """A trial is a red-only run by definition: the start's doubts (no
    position source, no synced axis) are the map's to accept."""
    model, red = idle_station
    assert red._source is None and not red.sync_axes_list
    trial = _arm(model)
    assert red.is_running and model.is_armed
    assert model._trial.run is red.run_token and trial == model._trial.id


def test_arm_that_cannot_start_the_run_writes_nothing(idle_station, private_db):
    model, red = idle_station
    red.estop()
    result = model.run("arm_trial", {"tip_id": "tip-A"}, (True,))
    assert result.is_refused and "Red Percent" in result.reason
    assert not model.is_armed and model.trial_count == 0


# -- T3: the picture prompts -------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n"


def test_arm_asks_to_frame_the_sample_before_anything_is_written(idle_station):
    """Bench 2026-09-27: "No prompt for a before or after image"."""
    model, red = idle_station
    result = model.run("arm_trial", {"tip_id": "T7", "typed_tilt": ""})
    assert result.needs_confirm, result
    assert result.reason == ("Is the sample vacuum ON? Check it now.\n\n"
                             "Frame the sample now. Continue takes the "
                             "whole-screen picture, starts the video and arms "
                             "trial 1 on tip T7 (NO sample, chip, flake or cut ID), with "
                             "NO tilt recorded, 300 steps/s (Stepper Probe).")
    assert result.command == "arm_trial"
    assert result.inputs == {"tip_id": "T7", "sample_id": "", "chip_id": "",
                             "flake_id": "", "cut_id": "", "typed_tilt": ""}
    assert not model.is_armed and model.trial_count == 0
    assert not red.is_running                 # nothing started either
    again = model.run(result.command, result.inputs, (*result.args, True))
    assert again.is_ok and again.value == 1 and model.is_armed


def test_the_arm_prompt_names_the_number_the_trial_will_get(station):
    model, red, *_ = station
    _record(model, red)
    second = _record(model, red)
    model.trial_pick = second
    model.delete_trial(True)
    result = model.run("arm_trial", {"tip_id": "tip-A"})
    assert "arms trial 3 on tip tip-A" in result.reason
    assert _confirmed(model, "arm_trial", {"tip_id": "tip-A"}) == 3


def test_arm_refuses_before_it_asks(station):
    model = station[0]
    result = model.run("arm_trial", {"tip_id": "  "})
    assert result.is_refused and "tip ID" in result.reason


def test_finish_asks_before_the_after_picture(station, private_db):
    model, red, *_ = station
    trial = _arm(model)
    result = model.run("finish_trial", {"note": "clean cut"})
    assert result.needs_confirm, result
    assert result.reason == f"Continue ends trial {trial} and closes its video."
    assert result.command == "finish_trial"
    assert result.inputs == {"note": "clean cut"}
    assert model.is_armed
    again = model.run(result.command, result.inputs, (*result.args, True))
    assert again.is_ok and not model.is_armed
    row = _rows(private_db, "SELECT note, status FROM trials WHERE id=?", trial)[0]
    assert row == {"note": "clean cut", "status": "recorded"}


def test_abort_does_not_ask(station):
    model = station[0]
    _arm(model)
    result = model.run("abort_trial")
    assert result.is_ok and not model.is_armed


def test_arm_without_a_before_picture_is_refused_and_writes_nothing(
        idle_station, monkeypatch):
    model, red = idle_station
    monkeypatch.setattr(red, "grab_frame", lambda: None)
    since = events.latest_id
    result = model.run("arm_trial", {"tip_id": "tip-A"}, (True,))
    assert result.is_refused
    assert result.reason == ("No picture of the capture region: the capture "
                             "region is not set or the screen is not open.")
    assert not model.is_armed and model.trial_count == 0
    assert not red.is_running                 # the run it started is ended
    assert not _titled("No Picture", since)   # a refusal, not a tray warning


def test_arm_without_a_picture_leaves_the_operators_run_running(station,
                                                                monkeypatch):
    model, red, *_ = station
    monkeypatch.setattr(red, "grab_frame", lambda: None)
    assert model.run("arm_trial", {"tip_id": "tip-A"}, (True,)).is_refused
    assert red.is_running


def test_finish_needs_no_picture_of_its_own(
        station, monkeypatch, private_db):
    """V4: Finish takes no picture of its own any more, so a screen that
    cannot be captured at Finish no longer holds the trial armed."""
    model, red, *_ = station
    trial = _arm(model)
    monkeypatch.setattr(red, "grab_frame", lambda: None)
    monkeypatch.setattr(red, "grab_screen", lambda: None)
    since = events.latest_id
    result = model.run("finish_trial", {"note": ""}, (True,))
    assert result.is_ok, result
    assert not model.is_armed
    assert not _titled("No Full Picture", since)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "recorded"
    assert row["after_path"] is None and row["after_full_path"] is None


def test_the_pictures_show_on_the_sheet(station):
    """Tier 1's two pictures come from the recording: its first frame and
    its frame at the Mark, both labelled; the armed trial's, else the last
    trial's, never an earlier trial's while one is armed."""
    model, red, *_ = station
    assert model.first_frame_image == b"" and model.mark_frame_image == b""
    first = _arm(model)
    folder = model.pictures_root / str(first)
    assert _wait_for(lambda: model.first_frame_image != b"")
    assert model.first_frame_image == (folder / "first_frame.png").read_bytes()
    assert model.first_frame_image[:8] == PNG
    assert model.mark_frame_image == b""       # not marked yet
    assert _marked(model).is_ok
    assert _wait_for(lambda: model.mark_frame_image != b"")
    assert model.mark_frame_image == (folder / "mark_frame.png").read_bytes()
    _finish(model)
    assert model.mark_frame_image == (folder / "mark_frame.png").read_bytes()
    second = _arm(model)
    assert model.mark_frame_image == b""       # not the last trial's
    assert _wait_for(lambda: model.first_frame_image == (
        model.pictures_root / str(second) / "first_frame.png").read_bytes()
        if (model.pictures_root / str(second) / "first_frame.png").is_file()
        else False)
    model.run("abort_trial")
    model.disable()
    model.new_database()
    assert model.first_frame_image == b"" and model.mark_frame_image == b""
    for command in ("first_frame_image", "mark_frame_image"):
        assert model.run(command).is_ok       # declared data sources


def test_the_picture_elements_say_when_they_are_taken():
    model = TransferMap()
    first, mark = _element(model, "first_frame_image"), _element(model, "mark_frame_image")
    assert (first["type"], first["text"]) == ("image", "First frame")
    assert (mark["type"], mark["text"]) == ("image", "Mark frame")
    assert "arm" in first["empty"] and "Mark force" in mark["empty"]
    status = _element(model, "video_status")
    assert (status["type"], status["text"]) == ("readonly", "Video")


# -- T6: the order of the sheet ------------------------------------------------

def test_the_sheet_reads_in_the_order_a_trial_is_run():
    model = TransferMap()
    sections = model.schema["sections"]
    tier_one = [s["title"] for s in sections if s.get("tier", 1) == 1
                and s["title"] != "Safety"]
    # The finalizer button sits last, at the foot of the tab (owner 2026-10-06).
    assert tier_one == ["Session", "Trial", "Finalize data"]
    trial = next(s for s in sections if s["title"] == "Trial")
    keys = [e.get("command") if e["type"] in ("button", "region_select")
            else e.get("model_attr") or e.get("command") or e.get("data_command")
            for e in trial["elements"]]
    assert keys == ["next_step", "set_region", "tilt_now", "speed_now",
                    "red_now", "tip_id", "tip_pick", "new_tip",
                    "tip_trial_count", "tip_status", "sample_id", "chip_id",
                    "flake_id", "cut_id", "typed_tilt", "typed_speed",
                    "arm_trial", "mark_force", "note", "finish_trial",
                    "abort_trial", "is_broke", "trial_status",
                    "first_frame_image", "mark_frame_image", "video_status",
                    "force_status", "live_series", "figure"]
    later = [(s["title"], s.get("tier")) for s in sections
             if s.get("tier", 1) != 1]
    assert later == [("Context", 2), ("Tip", 2), ("Figure", 2),
                     ("AFM measurement", 2), ("Optical measurement", 2),
                     ("Data", 2),
                     ("Diagnostics", 3), ("Safety", 3)]
    diagnostics = next(s for s in sections if s["title"] == "Diagnostics")
    assert [e.get("model_attr") or e.get("source_command") or e.get("command")
            for e in diagnostics["elements"]] == [
        "last_trial_numbers", "width_gradient", "video_encoder",
        "force_position_text", "trials_log", "tips_log", "delete_trial"]


# -- full pictures: the whole screen at Arm and at Finish (2026-09-28) ----------
# "I need additional full image captures included." The region pictures stay
# the gate; the whole-screen pictures are the record, so a missing one warns.

def _size(png):
    import io
    from PIL import Image
    return Image.open(io.BytesIO(png)).size


DESKTOP_SIZE = (DesktopCapture.DESKTOP["width"], DesktopCapture.DESKTOP["height"])


def test_arm_and_finish_keep_whole_screen_pictures(station, private_db):
    """V4: the whole screen at Arm is the one still left (context); Finish
    takes none."""
    model, red, *_ = station
    trial = _arm(model)
    folder = model.pictures_root / str(trial)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert Path(row["before_full_path"]) == folder / "before_full.png"
    _finish(model)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["after_full_path"] is None and row["after_path"] is None
    assert row["mark_path"] is None and row["mark_full_path"] is None
    names = sorted(p.name for p in folder.iterdir())
    assert "before_full.png" in names
    assert not {"before.png", "after.png", "after_full.png", "mark.png",
                "mark_full.png"} & set(names)
    png = (folder / "before_full.png").read_bytes()
    assert png[:8] == PNG and _size(png) == DESKTOP_SIZE


def test_a_missing_full_picture_warns_and_the_trial_is_recorded(
        station, private_db, monkeypatch):
    model, red, *_ = station
    monkeypatch.setattr(red, "grab_screen", lambda: None)
    events.forget("No Full Picture")    # a new dedupe episode
    since = events.latest_id
    trial = _arm(model)
    assert model.is_armed
    warned = _titled("No Full Picture", since)
    assert len(warned) == 1 and warned[0].severity == "warning"
    assert f"Trial {trial}" in warned[0].message
    _finish(model)
    assert len(_titled("No Full Picture", since)) == 1     # Finish takes none
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "recorded"
    assert row["before_full_path"] is None
    folder = model.pictures_root / str(trial)
    assert "before_full.png" not in {p.name for p in folder.iterdir()}


def test_a_full_grab_that_raises_is_a_missing_picture(station, monkeypatch):
    model, red, *_ = station

    def broken():
        raise RuntimeError("display went away")

    monkeypatch.setattr(red, "grab_screen", broken)
    events.forget("No Full Picture")    # a new dedupe episode
    since = events.latest_id
    _arm(model)
    assert model.is_armed and len(_titled("No Full Picture", since)) == 1


def test_a_red_percent_without_grab_screen_still_arms(tmp_path, private_db):
    """Duck typing: the map finds Red Percent by `subscribe` and
    `grab_frame`; `grab_screen` is optional."""
    class Plain:
        region = {"top": 0, "left": 0, "width": 10, "height": 10}
        is_running = True
        run_token = None
        def subscribe(self, fn): pass
        def unsubscribe(self, fn): pass
        def grab_frame(self): return PNG + b"region"

    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", Plain())
    model.tip_id = "t"
    events.forget("No Full Picture")    # a new dedupe episode
    since = events.latest_id
    trial = _arm(model)
    assert model.is_armed and len(_titled("No Full Picture", since)) == 1
    model.run("abort_trial")
    model.close()
    assert _rows(private_db, "SELECT before_full_path FROM trials WHERE id=?",
                 trial)[0]["before_full_path"] is None


def test_a_stop_during_the_arm_pictures_arms_nothing(idle_station, private_db,
                                                     monkeypatch):
    """The whole-screen grab widens the time between Arm's guard and the
    trial being armed; a stop inside it must win."""
    model, red = idle_station
    real = red.grab_screen

    def grab_then_stop():
        png = real()
        model.estop()
        return png

    monkeypatch.setattr(red, "grab_screen", grab_then_stop)
    result = model.run("arm_trial", {"tip_id": "tip-A"}, (True,))
    assert result.is_refused and "stopped" in result.reason
    assert not model.is_armed and model.trial_count == 0
    assert not red.is_running                    # the run it started is ended


def test_the_full_pictures_sit_under_configure(station):
    model, red, *_ = station
    sections = model.schema["sections"]
    context = next(s for s in sections if s["title"] == "Context")
    assert context.get("tier") == 2
    assert context.get("disclosure") == "Configure Transfer Map"
    assert [(e["type"], e["text"], e["data_command"]) for e in context["elements"]] \
        == [("image", "Arm, whole screen", "before_full_image")]
    assert all(e["empty"] for e in context["elements"])
    assert model.run("before_full_image").is_ok       # a declared data source
    assert not any(s["title"] == "Full pictures" for s in sections)


def test_the_full_pictures_show_the_armed_trial_else_the_last(station):
    model, red, *_ = station
    assert model.before_full_image == b""
    first = _arm(model)
    folder = model.pictures_root / str(first)
    assert model.before_full_image == (folder / "before_full.png").read_bytes()
    _finish(model)
    assert model.before_full_image == (folder / "before_full.png").read_bytes()
    assert _size(model.before_full_image) == DESKTOP_SIZE
    second = _arm(model)
    assert model.before_full_image == (model.pictures_root / str(second)
                                       / "before_full.png").read_bytes()
    model.run("abort_trial")
    model.disable()
    model.new_database()
    assert model.before_full_image == b""


def test_export_carries_the_full_picture_paths(station):
    """The old picture columns stay in the table and the export (old rows
    carry them); a new trial leaves them blank and fills the video's."""
    model, red, *_ = station
    trial = _record(model, red)
    rows = list(csv.DictReader(Path(model.export_csv()).open()))
    folder = model.pictures_root / str(trial)
    assert rows[0]["before_full_path"] == str(folder / "before_full.png")
    for old in ("before_path", "after_path", "after_full_path", "mark_path",
                "mark_full_path"):
        assert old in rows[0] and rows[0][old] == "", old
    assert rows[0]["video_path"] and Path(rows[0]["video_path"]).exists()
    assert rows[0]["video_index_path"] == str(folder / "video_index.csv")
    assert int(rows[0]["video_frames"]) > 0 and rows[0]["video_dropped"] != ""


# -- the store migrates: version 1 or 2 -> 3 -------------------------------------

V2_COLUMNS = ("before_full_path", "after_full_path")
V3_COLUMNS = ("mark_path", "mark_full_path")
V5_COLUMNS = ("video_path", "video_index_path", "video_frames",
              "video_dropped")
#: Version 6 (2026-10-04, both proposals' one migration): the trial names
#: its chip and flake and operator, and the cut descriptors.
V6_COLUMNS = ("sample_id", "flake_uid", "operator_id", "operator_auth",
              "camera_profile_id",
              "channel_height_nm", "channel_height_sigma_nm",
              "trench_depth_nm", "trench_depth_sigma_nm",
              "width_optical_um", "width_optical_sigma_um",
              "width_optical_method")
#: Version 7 (bench 2026-09-28 and 2026-10-04): the sample's chip, flake and
#: cut IDs, and the invalid flag.
V7_COLUMNS = ("chip_id", "flake_id", "cut_id", "invalid")
#: Version 8 (owner 2026-10-06): the tip-shade force columns.
V8_COLUMNS = ("contact_lowered", "force_position", "force_class", "shade_baseline",
              "shade_peak", "shade_mark")


def _version(path):
    with sqlite3.connect(path) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def _columns(path):
    with sqlite3.connect(path) as db:
        return [r[1] for r in db.execute("PRAGMA table_info(trials)")]


def _version_one_file(path, drop=V2_COLUMNS + V3_COLUMNS + V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS,
                      version=1):
    """A database as an earlier round wrote it: the version-1 trials table
    (no whole-screen columns; with `version=2` and `drop=V3_COLUMNS +
    V5_COLUMNS`, the version-2 table; with `version=4` and
    `drop=V5_COLUMNS + V6_COLUMNS`, the version-4 table, no video columns;
    with `version=5` and `drop=V6_COLUMNS`, the version-5 table), no tips
    table before version 3, one measured trial on tip T7 with a profile,
    `user_version = version`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [(n, k) for n, k in tm_module.TRIAL_COLUMNS if n not in drop]
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE trials (" + ", ".join(f"{n} {k}" for n, k in columns)
               + ")")
    db.execute("CREATE TABLE profile (trial_id INTEGER NOT NULL REFERENCES "
               "trials(id), t_s REAL NOT NULL, red REAL, z REAL, x REAL, y REAL)")
    db.execute("CREATE INDEX profile_trial ON profile(trial_id)")
    if version >= 3:
        db.execute("CREATE TABLE tips (" + ", ".join(
            f"{n} {k}" for n, k in tm_module.TIP_COLUMNS) + ")")
        db.execute("INSERT INTO tips (tip_id, created_at, first_trial_id, "
                   "last_trial_id, last_used_at) VALUES ('T7', "
                   "'2026-09-27T15:00:00', 1, 1, '2026-09-27T15:00:00')")
    db.execute("INSERT INTO trials (started_at, tip_id, tilt_deg, speed_steps_s, "
               "width_um, before_path, after_path, status, note) VALUES "
               "('2026-09-27T15:00:00', 'T7', 12.5, 300, 4.2, '/b.png', "
               "'/a.png', 'measured', 'bench')")
    db.executemany("INSERT INTO profile VALUES (1, ?, ?, NULL, NULL, NULL)",
                   [(0.1 * i, 10.0 + i) for i in range(5)])
    db.execute(f"PRAGMA user_version = {int(version)}")
    db.commit()
    db.close()


def _tables(path):
    with sqlite3.connect(path) as db:
        return {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}


def test_a_fresh_database_is_version_three_with_the_picture_columns_and_tips(
        private_db):
    assert tm_module.SCHEMA_VERSION == 8
    model = TransferMap()
    model.open()
    model.close()
    assert _version(private_db) == 8
    assert set(V2_COLUMNS + V3_COLUMNS + V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) <= set(_columns(private_db))
    assert "tips" in _tables(private_db)


def test_a_version_one_database_gains_the_columns_and_keeps_its_trial(
        private_db):
    """The owner's bench database already holds trials."""
    _version_one_file(private_db)
    before = _rows(private_db, "SELECT * FROM trials")
    assert not set(V2_COLUMNS) & set(_columns(private_db))
    events.forget("No Full Picture")    # a new dedupe episode
    since = events.latest_id
    model = TransferMap()
    model.open()
    try:
        assert _version(private_db) == 8
        assert set(V2_COLUMNS + V3_COLUMNS + V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) <= set(_columns(private_db))
        after = _rows(private_db, "SELECT * FROM trials")
        assert len(after) == 1
        assert {k: after[0][k] for k in before[0]} == before[0]   # untouched
        assert after[0]["before_full_path"] is None
        assert after[0]["after_full_path"] is None
        assert len(_rows(private_db, "SELECT * FROM profile")) == 5
        upgraded = _titled("Database Upgraded", since)
        assert len(upgraded) == 1 and str(private_db) in upgraded[0].message
        assert _titled("Database Ready", since)[0].message == \
            f"{private_db}: 1 trial(s)"
        assert model.run("before_full_image").is_ok
        assert model.before_full_image == b""  # the stored paths are gone files
    finally:
        model.close()


def test_a_migrated_database_records_a_trial_with_its_full_pictures(
        red, private_db):
    _version_one_file(private_db)
    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", red)
    model.tip_id = "T7"
    try:
        trial = _arm(model)
        assert trial == 2
        _finish(model)
        row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
        assert row["before_full_path"] and row["video_path"]
        assert model.tip_trial_count == 2
    finally:
        model.close()


def test_the_first_write_migrates_too(private_db):
    """Not only `open`: a store written before anything opened it (an
    import from a script, a view reading an old file) upgrades on write."""
    _version_one_file(private_db)
    store = tm_module.TrialStore(private_db)
    store.insert({"tip_id": "T8", "status": "recorded",
                  "before_full_path": "/x.png", "mark_path": "/m.png",
                  "video_path": "/v.mp4"})
    assert _version(private_db) == 8
    assert [r["tip_id"] for r in store.trials()] == ["T7", "T8"]


def test_a_half_done_upgrade_finishes(private_db):
    """A version-1 file that already has some of the new columns (an upgrade
    cut short between the ALTERs) gets the rest and the current version."""
    _version_one_file(private_db, drop=("after_full_path",) + V3_COLUMNS
                      + V5_COLUMNS + V6_COLUMNS[3:] + V7_COLUMNS + V8_COLUMNS)
    assert _version(private_db) == 1
    assert tm_module.TrialStore(private_db).ensure() is False
    assert _version(private_db) == 8
    assert set(V2_COLUMNS + V3_COLUMNS + V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) <= set(_columns(private_db))


def test_a_version_three_database_is_left_alone(private_db):
    tm_module.TrialStore(private_db).ensure()
    with sqlite3.connect(private_db) as db:
        schema = db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    events.forget("No Full Picture")    # a new dedupe episode
    since = events.latest_id
    model = TransferMap()
    model.open()
    model.close()
    with sqlite3.connect(private_db) as db:
        assert db.execute("SELECT sql FROM sqlite_master ORDER BY name"
                          ).fetchall() == schema
    assert _version(private_db) == 8
    assert not _titled("Database Upgraded", since)


def test_a_version_two_database_gains_the_mark_columns_and_its_tips(private_db):
    """M1/M2: the owner's bench file is version 2 and holds trials. It gains
    the Mark columns and a tip record per tip its trials name; every trial
    and profile row is kept."""
    _version_one_file(private_db, drop=V3_COLUMNS + V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS,
                      version=2)
    before = _rows(private_db, "SELECT * FROM trials")
    assert _version(private_db) == 2 and "tips" not in _tables(private_db)
    assert set(V2_COLUMNS) <= set(_columns(private_db))
    assert not set(V3_COLUMNS) & set(_columns(private_db))
    events.forget("Database Upgraded")  # a new dedupe episode
    since = events.latest_id
    model = TransferMap()
    model.open()
    try:
        assert _version(private_db) == 8
        assert set(V3_COLUMNS + V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) <= set(_columns(private_db))
        after = _rows(private_db, "SELECT * FROM trials")
        assert len(after) == 1
        assert {k: after[0][k] for k in before[0]} == before[0]    # untouched
        assert after[0]["mark_path"] is None and after[0]["mark_full_path"] is None
        assert len(_rows(private_db, "SELECT * FROM profile")) == 5
        tips = _rows(private_db, "SELECT * FROM tips")
        assert tips == [{"tip_id": "T7", "created_at": "2026-09-27T15:00:00",
                         "first_trial_id": 1, "last_trial_id": 1,
                         "last_used_at": "2026-09-27T15:00:00",
                         "broke_trial_id": None, "retired_at": None,
                         "note": None}]
        upgraded = _titled("Database Upgraded", since)
        assert len(upgraded) == 1
        assert (", ".join(V3_COLUMNS + V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) + ", tips (version 8)"
                in upgraded[0].message), upgraded[0].message
        model.tip_id = "T7"
        assert model.tip_status == "in use since trial 1"
        assert model.run("mark_frame_image").is_ok and model.mark_frame_image == b""
    finally:
        model.close()


def test_a_migrated_version_two_database_records_a_marked_trial(red, private_db):
    _version_one_file(private_db, drop=V3_COLUMNS + V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS, version=2)
    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", red)
    model.tip_id = "T7"
    events.forget("Tip Created")        # a new dedupe episode
    since = events.latest_id
    try:
        trial = _record(model, red)
        assert trial == 2
        row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
        assert row["mark_operator_t"] is not None and row["video_path"]
        assert not _titled("Tip Created", since)            # T7 had its record
        assert model._store.tip("T7")["trials"] == [1, 2]
        assert model._store.tip("T7")["last_trial_id"] == 2
    finally:
        model.close()


def test_a_version_two_file_with_a_broken_tip_backfills_it(private_db):
    _version_one_file(private_db, drop=V3_COLUMNS + V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS, version=2)
    with sqlite3.connect(private_db) as db:
        db.execute("INSERT INTO trials (tip_id, broke, status) VALUES "
                   "('T7', 1, 'recorded'), ('T7', 1, 'recorded'), "
                   "('T9', 0, 'recorded'), ('', 0, 'recorded')")
    tm_module.TrialStore(private_db).ensure()
    tips = {r["tip_id"]: r for r in _rows(private_db, "SELECT * FROM tips")}
    assert set(tips) == {"T7", "T9"}                  # a blank tip is no tip
    assert (tips["T7"]["first_trial_id"], tips["T7"]["last_trial_id"],
            tips["T7"]["broke_trial_id"]) == (1, 3, 2)
    assert tips["T9"]["broke_trial_id"] is None


# -- M1: the Mark picture ----------------------------------------------------------
# "A photo when that mark is taken of how the visuals are at that moment."
# The Mark is the measurement: it is stamped first, a missing picture warns.

def _marked(model, samples=5):
    assert _wait_for(lambda: len(model._trial.samples) >= samples)
    return model.run("mark_force")


def test_mark_keeps_a_region_and_a_whole_screen_picture(station, private_db):
    """V4: the Mark takes no still of its own; its picture is the
    recording's frame at the Mark, labelled MARK."""
    model, red, *_ = station
    trial = _arm(model)
    folder = model.pictures_root / str(trial)
    assert _marked(model).is_ok
    assert _wait_for(lambda: (folder / "mark_frame.png").is_file())
    _finish(model)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["mark_path"] is None and row["mark_full_path"] is None
    names = {p.name for p in folder.iterdir()}
    assert "mark_frame.png" in names
    assert not {"mark.png", "mark_full.png"} & names


def test_the_mark_is_stamped_before_its_picture_is_taken(station, monkeypatch):
    """The Mark grabs nothing: it stamps and returns."""
    model, red, *_ = station
    _arm(model)
    grabs = []
    monkeypatch.setattr(red, "grab_frame", lambda: grabs.append(1))
    monkeypatch.setattr(red, "grab_screen", lambda: grabs.append(1))
    marked = _marked(model)
    assert marked.is_ok and grabs == []
    assert model._trial.operator_t is not None and model._trial.z_mark == 1000.0
    assert round(model._trial.operator_t, 3) == marked.value


def test_the_mark_pictures_show_the_armed_trial_else_the_last(station):
    model, red, *_ = station
    first = _arm(model)
    assert _marked(model).is_ok
    folder = model.pictures_root / str(first)
    assert _wait_for(lambda: (folder / "mark_frame.png").is_file())
    _finish(model)
    assert model.mark_frame_image == (folder / "mark_frame.png").read_bytes()
    assert model.run("mark_frame_image").is_ok     # a declared data source


def test_the_mark_picture_says_when_it_is_taken():
    model = TransferMap()
    mark = _element(model, "mark_frame_image")
    assert (mark["type"], mark["text"]) == ("image", "Mark frame")
    assert mark["empty"] == "The video's frame at Mark force, labelled MARK."


def test_many_marks_share_one_picture_worker_and_close_ends_it(station):
    """One worker thread writes the video, whatever the number of Marks."""
    model, red, *_ = station
    _arm(model)
    for _ in range(3):
        assert _marked(model).is_ok
    workers = [t for t in threading.enumerate()
               if t.name.startswith("transfer-map-pictures") and t.is_alive()]
    assert len(workers) == 1
    _finish(model)
    model.close()
    assert _wait_for(lambda: not workers[0].is_alive())


# -- M2: tips as records ---------------------------------------------------------
# "Tip objects that can be created on demand and tracked in the db for
# usage, and on which trials."

def test_the_first_arm_on_a_new_tip_creates_its_record(station, private_db):
    model, red, *_ = station
    model.tip_id = "T7"
    events.forget("Tip Created")        # a new dedupe episode
    since = events.latest_id
    first = _arm(model)
    created = _titled("Tip Created", since)
    assert [e.message for e in created] == ["Tip T7 created."]
    record = model._store.tip("T7")
    assert (record["first_trial_id"], record["last_trial_id"]) == (first, first)
    assert record["created_at"] and record["last_used_at"]
    assert record["broke_trial_id"] is None and record["retired_at"] is None
    _finish(model)
    events.forget("Tip Created")
    since = events.latest_id
    second = _record(model, red)
    assert not _titled("Tip Created", since)
    record = model._store.tip("T7")
    assert (record["first_trial_id"], record["last_trial_id"]) == (first, second)
    assert record["trials"] == [first, second] and record["count"] == 2
    assert record["broke"] is False
    assert model._store.tip("nobody") is None


def test_tip_broke_is_kept_on_the_tips_record(station):
    model, red, *_ = station
    first = _record(model, red)
    trial = _arm(model)
    assert model.run("mark_broke", None, (True,)).is_ok       # the armed trial
    assert model._store.tip("tip-A")["broke_trial_id"] == trial
    assert model.run("mark_broke", None, (False,)).is_ok
    assert model._store.tip("tip-A")["broke_trial_id"] is None
    assert model.run("mark_broke", None, (True,)).is_ok
    _finish(model)
    record = model._store.tip("tip-A")
    assert record["broke_trial_id"] == trial and record["broke"] is True
    assert model.run("mark_broke", None, (False,)).is_ok      # the last trial
    assert model._store.tip("tip-A")["broke_trial_id"] is None
    assert first < trial


def test_tip_status_reads_under_trials_on_this_tip(station):
    model, red, *_ = station
    values = lambda: model.state["values"]            # noqa: E731
    model.tip_id = ""
    assert values()["tip_status"] == ""
    model.tip_id = "T7"
    assert values()["tip_status"] == "new"
    first = _record(model, red)
    assert values()["tip_status"] == f"in use since trial {first}"
    second = _record(model, red)
    model.mark_broke(True)
    assert values()["tip_status"] == f"broke on trial {second}"
    trial = next(s for s in model.schema["sections"] if s["title"] == "Trial")
    keys = [e.get("model_attr") or e.get("command") for e in trial["elements"]]
    assert keys[keys.index("tip_trial_count") + 1] == "tip_status"
    assert _element(model, "tip_status")["text"] == "Tip"


def test_arming_on_a_broken_tip_asks_once(station):
    model, red, *_ = station
    model.tip_id = "T7"
    broke = _record(model, red)
    model.mark_broke(True)
    result = model.run("arm_trial", {"tip_id": "T7"})
    assert result.needs_confirm
    assert result.reason == (
        "Is the sample vacuum ON? Check it now.\n\n"
        f"Tip T7 broke on trial {broke}. Arm on it anyway?\n\nFrame the sample "
        "now. Continue takes the whole-screen picture, starts the video and "
        f"arms trial {broke + 1} on tip T7 (NO sample, chip, flake or cut ID) at "
        "22.5 deg (Rotator), 300 steps/s (Stepper Probe).")
    again = model.run(result.command, result.inputs, (*result.args, True))
    assert again.is_ok and model.is_armed         # one Continue, not two


VACUUM = "Is the sample vacuum ON? Check it now."


def test_every_arm_asks_that_the_sample_vacuum_is_on(station):
    """Bench 2026-10-04: two trials were cut with the sample vacuum off. The
    station cannot sense it, so every Arm asks, first, in the one prompt it
    already raises: still one question and one Continue, and nothing is
    armed until the operator answers. A broken tip's question stays."""
    model, red, *_ = station
    model.tip_id = "T7"
    for _ in range(2):                            # every Arm, not the first
        result = model.run("arm_trial", {"tip_id": "T7"})
        assert result.needs_confirm and result.command == "arm_trial"
        assert result.reason.startswith(VACUUM + "\n\nFrame the sample now.")
        assert result.reason.count(VACUUM) == 1
        assert not model.is_armed
        _record(model, red)
    model.mark_broke(True)
    result = model.run("arm_trial", {"tip_id": "T7"})
    assert result.needs_confirm and not model.is_armed
    reason = result.reason
    assert reason.startswith(VACUUM + "\n\nTip T7 broke on trial ")
    assert reason.index("Arm on it anyway?") < reason.index("Frame the sample now.")
    again = model.run(result.command, result.inputs, (*result.args, True))
    assert again.is_ok and model.is_armed         # one Continue, not two


def test_arming_on_a_retired_tip_asks_once(station):
    model, red, *_ = station
    model.tip_id = "T7"
    _record(model, red)
    assert _confirmed(model, "retire_tip", {"tip_id": "T7"}) == "T7"
    result = model.run("arm_trial", {"tip_id": "T7"})
    assert result.needs_confirm
    assert result.reason.startswith("Is the sample vacuum ON? Check it now.\n\n"
                                    "Tip T7 is retired. Arm on it anyway?\n\n"
                                    "Frame the sample now.")
    again = model.run(result.command, result.inputs, (*result.args, True))
    assert again.is_ok and model.is_armed


def test_retire_asks_and_unretire_returns_the_tip(station):
    model, red, *_ = station
    model.tip_id = "T7"
    first = _record(model, red)
    asked = model.run("retire_tip", {"tip_id": "T7"})
    assert asked.needs_confirm and asked.reason.startswith("Retire tip T7? Its 1 trial(s)")
    assert asked.inputs == {"tip_id": "T7"}
    assert model._store.tip("T7")["retired_at"] is None       # nothing yet
    assert model.run(asked.command, asked.inputs, (*asked.args, True)).is_ok
    assert model._store.tip("T7")["retired_at"] and model.tip_status == "retired"
    again = model.run("retire_tip", {"tip_id": "T7"}, (True,))
    assert again.is_refused and "already retired" in again.reason
    assert model.run("unretire_tip", {"tip_id": "T7"}).is_ok
    assert model.tip_status == f"in use since trial {first}"
    assert model.run("unretire_tip", {"tip_id": "T7"}).is_refused


def test_the_tip_commands_refuse_a_blank_an_unknown_or_an_armed_tip(station):
    model, red, *_ = station
    for command in ("retire_tip", "unretire_tip", "set_tip_note"):
        blank = model.run(command, {"tip_id": " "})
        assert blank.is_refused and "tip ID" in blank.reason, command
    for command in ("retire_tip", "unretire_tip"):
        unknown = model.run(command, {"tip_id": "T99"})
        assert unknown.is_refused and "no record yet" in unknown.reason, command
    # A note on an unknown tip creates its record (owner call 2026-09-28).
    assert model.run("set_tip_note", {"tip_id": "T98", "tip_note": "n"}).is_ok
    _arm(model, "tip-A")
    armed = model.run("retire_tip", {"tip_id": "tip-A"}, (True,))
    assert armed.is_refused and "armed on tip tip-A" in armed.reason


def test_a_tip_note_is_saved_on_its_record(station):
    model, red, *_ = station
    _record(model, red)
    result = model.run("set_tip_note", {"tip_id": "tip-A",
                                        "tip_note": " box B, 2 um "})
    assert result.is_ok, result
    assert model._store.tip("tip-A")["note"] == "box B, 2 um"


def test_the_tips_log_has_one_line_per_tip(station):
    model, red, *_ = station
    first = _record(model, red)
    second = _record(model, red)
    model.mark_broke(True)
    model.tip_id = "T8"
    third = _record(model, red)
    assert _confirmed(model, "retire_tip", {"tip_id": "T8"}) == "T8"
    model.run("set_tip_note", {"tip_id": "T8", "tip_note": "chipped"})
    assert model.tips_log == [
        f"tip-A  2 trial(s), trials {first}-{second}  broke on trial {second}",
        f"T8  1 trial(s), trial {third}  retired  chipped"]
    assert model.run("tips_log").is_ok            # a declared source


def test_export_writes_a_tips_file_with_one_row_per_tip(station):
    model, red, *_ = station
    first = _record(model, red)
    second = _record(model, red)
    path = Path(model.export_tips_csv())
    assert path.parent == model.output_root / "exports"
    assert path.name.startswith("transfer_map_") and path.name.endswith("_tips.csv")
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == 1
    assert rows[0]["tip_id"] == "tip-A"
    assert rows[0]["trial_count"] == "2"
    assert rows[0]["trial_ids"] == f"{first} {second}"
    assert rows[0]["first_trial_id"] == str(first)
    assert list(rows[0])[:8] == [n for n, _k in tm_module.TIP_COLUMNS]
    element = _element(model, "export_tips_csv")
    assert element["type"] == "file_save" and element["text"] == "Export tips"


def test_an_import_creates_the_tips_its_trials_name(tmp_path, private_db):
    source = tmp_path / "typed.csv"
    source.write_text("tilt_deg,speed_steps_s,tip_id,broke\n"
                      "10,200, T7 ,0\n12,300,T7,1\n14,400,,0\n16,500,T9,0\n")
    model = TransferMap()
    events.forget("Map Imported")
    since = events.latest_id
    assert model.import_csv(str(source)) == {"imported": 4, "skipped": 0}
    assert model._store.tip("T7")["trials"] == [1, 2]
    assert model._store.tip("T7")["broke_trial_id"] == 2
    assert model._store.tip("T9")["trials"] == [4]
    assert [t["tip_id"] for t in model._store.tips()] == ["T7", "T9"]
    assert "tip(s) created: T7, T9" in _titled("Map Imported", since)[0].message


def test_deleting_a_trial_keeps_its_tips_record_current(station):
    model, red, *_ = station
    first = _record(model, red)
    second = _record(model, red)
    model.mark_broke(True)
    model.trial_pick = second
    model.delete_trial(True)
    record = model._store.tip("tip-A")
    assert (record["first_trial_id"], record["last_trial_id"]) == (first, first)
    assert record["broke_trial_id"] is None and record["trials"] == [first]


def test_the_tip_section_sits_under_configure():
    model = TransferMap()
    section = next(s for s in model.schema["sections"] if s["title"] == "Tip")
    assert section.get("tier") == 2
    assert section.get("disclosure") == "Configure Transfer Map"
    assert [(e["type"], e.get("command") or e.get("model_attr"),
             tuple(e.get("inputs") or ())) for e in section["elements"]] == [
        ("entry", "tip_note", ()),
        ("button", "set_tip_note", ("tip_id", "tip_note")),
        ("button", "retire_tip", ("tip_id",)),
        ("button", "unretire_tip", ("tip_id",))]


# -- M3: polling starts itself ----------------------------------------------------
# "Once capture region and tip details are set, the red percent polling
# baseline is reset and polling begins." The stop path first: every way a
# trial or the map stops ends the run the map started, and only that one.

@pytest.fixture
def sheet(red):
    """A Transfer Map beside a Red Percent with a region, no run, no tip."""
    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", red)
    model.on_model_added("Stepper Probe", FakeProbe())
    assert not red.is_running and not model.tip_id
    yield model, red
    model.close()


def _commit_tip(model, tip):
    result = model.set_value("tip_id", tip)          # what a view's entry sends
    assert result.is_ok, result
    return result


def test_the_maps_stop_ends_the_polling_it_started(sheet):
    model, red = sheet
    _commit_tip(model, "T7")
    assert red.is_running
    started = time.monotonic()
    assert model.estop() is True
    assert time.monotonic() - started < 0.5
    assert not red.is_running and model._auto_run is None


def test_the_maps_stop_leaves_a_run_the_operator_started(sheet):
    model, red = sheet
    red.start_run(confirmed=True)
    _commit_tip(model, "T7")
    assert model._auto_run is None
    assert model.estop() is True and red.is_running


def test_arm_takes_over_the_polling_and_finish_ends_it(sheet, private_db):
    model, red = sheet
    _commit_tip(model, "T7")
    token = red.run_token
    trial = _arm(model)
    assert red.run_token is token                  # the same run, not a second
    assert model._trial.run is token and model._auto_run is None
    assert _wait_for(lambda: len(model._trial.samples) >= 5)
    _finish(model)
    assert not red.is_running


@pytest.mark.parametrize("end", ["abort", "estop"])
def test_abort_and_the_stop_end_the_polling_a_trial_took_over(sheet, end):
    model, red = sheet
    _commit_tip(model, "T7")
    _arm(model)
    if end == "abort":
        assert model.run("abort_trial").is_ok
    else:
        assert model.estop() is True
    assert not red.is_running


def test_committing_a_tip_id_starts_polling_from_a_fresh_baseline(sheet):
    model, red = sheet
    red.baseline_red = 99.0                        # a stale baseline
    step = lambda: model.state["values"]["next_step"]  # noqa: E731
    assert step() == "Type a tip ID"
    events.forget("Polling Started")    # a new dedupe episode
    since = events.latest_id
    model.typed_tilt = "7"
    model.sample_id, model.chip_id, model.flake_id, model.cut_id = "S1", "C1", "F2", "3"
    _commit_tip(model, "T7")
    assert red.is_running and model._auto_run is red.run_token
    assert step() == "Press Arm trial"
    started = _titled("Polling Started", since)
    assert len(started) == 1 and "tip T7" in started[0].message
    assert _wait_for(lambda: red._run.frames >= 3)
    assert red._run.baseline_red is not None and red._run.baseline_red != 99.0
    assert red.baseline_red == red._run.baseline_red
    reds = set()
    assert _wait_for(lambda: reds.add(model.state["values"]["red_now"])
                     or len(reds) >= 2)            # the sheet's Red moves


def test_setting_the_region_with_a_tip_starts_polling(tmp_path):
    red = RedMonitor(screen=desktop_screen())
    red.output_root = tmp_path / "runs"
    red.open()
    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", red)
    try:
        _commit_tip(model, "T7")
        assert not red.is_running                  # no region yet
        assert model.state["values"]["next_step"] == "Set the capture region"
        assert model.run("set_region", None, (0, 0, 10, 10)).is_ok
        assert red.is_running and model._auto_run is red.run_token
    finally:
        model.close()
        red.close()


def test_a_region_without_a_tip_does_not_start_polling(tmp_path):
    red = RedMonitor(screen=desktop_screen())
    red.output_root = tmp_path / "runs"
    red.open()
    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", red)
    try:
        assert model.state["values"]["next_step"] == (
            "Set the capture region and a tip ID")
        assert model.run("set_region", None, (0, 0, 10, 10)).is_ok
        assert not red.is_running
        assert model.state["values"]["next_step"] == "Type a tip ID"
    finally:
        model.close()
        red.close()


def test_clearing_the_tip_or_the_region_does_not_stop_polling(sheet):
    model, red = sheet
    _commit_tip(model, "T7")
    _commit_tip(model, "")
    assert red.is_running
    red.region = None                              # as if cleared on Red Percent
    model._commit()
    assert red.is_running


def test_a_run_the_operator_ended_stays_ended_until_the_tip_changes(sheet):
    model, red = sheet
    _commit_tip(model, "T7")
    red.end_run()                                  # the operator's stop
    assert model.set_value("note", "anything").is_ok
    _commit_tip(model, "T7")                       # the same tip again
    assert not red.is_running
    _commit_tip(model, "T8")                       # a new tip: a new start
    assert red.is_running


def test_no_polling_while_the_map_is_stopped(sheet):
    model, red = sheet
    model.estop()
    _commit_tip(model, "T7")
    assert not red.is_running


def test_no_polling_while_a_trial_is_armed(idle_station):
    model, red = idle_station
    _arm(model)
    red.end_run()                                  # the operator's stop
    _commit_tip(model, "T9")
    assert not red.is_running and model.is_armed


def test_a_refused_start_warns_once_and_next_step_says_what_to_fix(sheet,
                                                                   monkeypatch):
    model, red = sheet
    real = red.start_run

    def refuse(confirmed=False):
        raise Refused("Screen capture is unavailable in this environment.")

    monkeypatch.setattr(red, "start_run", refuse)
    events.forget("Polling Not Started")
    since = events.latest_id
    for tip in ("T7", "T7", "T8"):
        model.typed_tilt = "5"
        _commit_tip(model, tip)                    # a commit never fails
    warned = _titled("Polling Not Started", since)
    assert len(warned) == 1 and warned[0].severity == "warning"
    assert "Screen capture is unavailable" in warned[0].message
    assert model.state["values"]["next_step"] == (
        "Polling did not start: Screen capture is unavailable in this "
        "environment. Fix that, then press Arm trial")
    monkeypatch.setattr(red, "start_run", real)
    _arm(model)                                    # Arm's own start: the fallback
    assert red.is_running and model._trial.run is red.run_token
    assert model.state["values"]["next_step"].startswith("Lower the tip")


def test_a_commit_through_the_controller_starts_polling(red):
    """The Web and Tk entry commit is `Controller.set_value` -> `_commit`."""
    controller = Controller()
    model = TransferMap()
    controller.add("Transfer Map", model, {})
    model.on_model_added("Red Percent", red)
    try:
        result = controller.set_value("Transfer Map", "tip_id", "T7")
        assert result.is_ok, result
        assert red.is_running and model._auto_run is red.run_token
    finally:
        model.close()


# -- Known tips and New tip (bench 2026-09-28) -------------------------------

def _map_with_tip(tmp_path, tip="T7"):
    from model.transfer_map import TransferMap
    tm = TransferMap(db_path=tmp_path / "map.sqlite")
    tm.tip_id = tip
    assert tm.run("new_tip", inputs={"tip_id": tip}).is_ok
    return tm


def test_new_tip_creates_a_record_before_any_trial(tmp_path):
    tm = _map_with_tip(tmp_path)
    assert tm.tip_options == [tm.NO_TIP, "T7"]
    assert tm.tip_status == "new"
    assert tm.tip_pick == "T7"


def test_new_tip_refuses_a_blank_or_known_id(tmp_path):
    tm = _map_with_tip(tmp_path)
    again = tm.run("new_tip", inputs={"tip_id": "T7"})
    assert again.status == "refused" and "already on record" in again.reason
    blank = tm.run("new_tip", inputs={"tip_id": ""})
    assert blank.status == "refused"


def test_picking_a_known_tip_fills_the_entry(tmp_path):
    tm = _map_with_tip(tmp_path)
    tm.tip_id = ""
    assert tm.tip_pick == tm.NO_TIP
    assert tm.run("pick_tip", args=("T7",)).is_ok
    assert tm.tip_id == "T7"
    assert tm.run("pick_tip", args=(tm.NO_TIP,)).is_ok and tm.tip_id == "T7"
    assert tm.run("pick_tip", args=("T99",)).status == "refused"


def test_a_note_creates_the_tip_record_when_there_is_none(tmp_path):
    from model.transfer_map import TransferMap
    tm = TransferMap(db_path=tmp_path / "map.sqlite")
    tm.tip_id, tm.tip_note = "T8", "fresh"
    assert tm.run("set_tip_note", inputs={"tip_id": "T8", "tip_note": "fresh"}).is_ok
    assert tm._store.tip("T8")["note"] == "fresh"


# -- the tilt is asked per trial (bench 2026-09-28) --------------------------

def test_the_tilt_entry_sits_in_tier_one_before_arm(tmp_path):
    from model.transfer_map import TransferMap
    tm = TransferMap(db_path=tmp_path / "map.sqlite")
    trial = [s for s in tm.schema["sections"] if s["title"] == "Trial"][0]
    keys = [e.get("model_attr") or e.get("command") for e in trial["elements"]]
    assert keys.index("typed_tilt") < keys.index("arm_trial")
    assert keys.index("typed_tilt") > keys.index("tip_id")
    figure = [s for s in tm.schema["sections"] if s["title"] == "Figure"][0]
    assert "typed_tilt" not in [e.get("model_attr") for e in figure["elements"]]


def test_set_tilt_for_trial_corrects_a_recorded_trial(station):
    model, red, *_ = station
    model.typed_tilt = ""
    _arm(model, "tip-A")
    _finish(model)
    trial_id = model._store.last()["id"]
    assert model._store.trial(trial_id)["tilt_deg"] == 22.5      # the SIM rotator
    model.afm_trial_id, model.typed_tilt = trial_id, "6.5"
    assert model.run("set_trial_tilt", {"afm_trial_id": trial_id,
                                       "typed_tilt": "6.5"}).is_ok
    row = model._store.trial(trial_id)
    assert row["tilt_deg"] == 6.5 and row["tilt_source"] == "typed later"
    bad = model.run("set_trial_tilt", {"afm_trial_id": 999, "typed_tilt": "7"})
    assert bad.is_refused


# -- the speed is typed per trial and the cut's speed is measured (2026-09-28)

def test_cut_speed_is_the_fast_segment_after_the_mark():
    from model import transfer_map_analysis as analysis
    t = [i * 0.1 for i in range(40)]
    z = [-(i * 1.0) for i in range(20)] + [-20 - (i * 8.0) for i in range(20)]
    assert analysis.cut_speed(t, z) == pytest.approx(80.0)
    assert analysis.cut_speed(t, z, after_t=2.0) == pytest.approx(80.0)
    assert analysis.cut_speed(t[:2], z[:2]) is None
    assert analysis.cut_speed(t, [0.0] * 40) is None


def test_a_typed_speed_wins_over_the_probe_setting(station):
    model, red, *_ = station
    model.typed_speed = "150"
    assert model._read_speed() == (150.0, "typed")
    model.typed_speed = ""
    speed, source = model._read_speed()
    assert source != "typed"


def test_set_speed_for_trial_corrects_a_recorded_trial(station):
    model, red, *_ = station
    _arm(model, "tip-A")
    _finish(model)
    trial_id = model._store.last()["id"]
    assert model.run("set_trial_speed", {"afm_trial_id": trial_id,
                                        "typed_speed": "220"}).is_ok
    row = model._store.trial(trial_id)
    assert row["speed_steps_s"] == 220.0 and row["speed_source"] == "typed later"


# -- an invalid trial is kept but left off the map (bench 2026-10-04: "vacuum
# was off, data is invalid"; "a flag ... that will allow me to drop invalid
# trials") ------------------------------------------------------------------

def test_mark_trial_invalid_drops_it_from_the_map_and_keeps_it_on_record(
        station):
    model, red, *_ = station
    _arm(model, "tip-A")
    _finish(model)
    trial_id = model._store.last()["id"]
    assert trial_id in [r["id"] for r in model._map_rows()]
    assert model.run("set_trial_invalid", {"afm_trial_id": trial_id},
                     args=(True,)).is_ok
    assert model._store.trial(trial_id)["invalid"] == 1
    assert trial_id not in [r["id"] for r in model._map_rows()]
    assert model._store.trial(trial_id) is not None          # kept
    assert "invalid" in [line for line in model.trials_log
                         if line.lstrip().startswith(str(trial_id))][0]
    assert model.run("set_trial_invalid", {"afm_trial_id": trial_id},
                     args=(False,)).is_ok
    assert model._store.trial(trial_id)["invalid"] == 0
    assert trial_id in [r["id"] for r in model._map_rows()]
    assert model.run("set_trial_invalid", {"afm_trial_id": 999},
                     args=(True,)).is_refused


def test_the_invalid_flag_is_a_column_and_reaches_the_export(station):
    model, red, *_ = station
    _arm(model, "tip-A")
    _finish(model)
    trial_id = model._store.last()["id"]
    model.run("set_trial_invalid", {"afm_trial_id": trial_id}, args=(True,))
    with open(model.export_csv(), newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["invalid"] for r in rows if r["id"] == str(trial_id)] == ["1"]


# -- V3-V5: a labelled video per trial (owner, 2026-09-28) -------------------------
# "Rather than like 7 pictures, a video where timestamps label the footage
# for analysis afterward." Frames reach the map through Red Percent's frame
# hook (the frame it measured, no second grab); the picture thread writes
# at most VIDEO_FPS of them a second; the index says what each one shows.

def _index(model, trial):
    path = model.pictures_root / str(trial) / "video_index.csv"
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def _mp4_frames(path):
    ffmpeg = pytest.importorskip("imageio_ffmpeg")
    return ffmpeg.count_frames_and_secs(str(path))[0]


def _video_frames(path):
    """Frames in a trial's video, either path: the MP4's, or the JPEGs."""
    path = Path(path)
    if path.is_dir():
        return len(list(path.glob("frame_*.jpg")))
    return _mp4_frames(path)


def _row(private_db, trial):
    return _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]


def test_frames_flow_from_red_percent_into_the_trials_video(station, private_db,
                                                           real_encoder):
    """The one map test on the real MP4 path (`real_encoder`)."""
    model, red, *_ = station
    assert model.video_encoder.startswith("H.264 MP4 via imageio-ffmpeg")
    trial = _arm(model)
    assert red._frame_subscribers == (model._on_frame,)
    rec = model._trial.recording
    assert _wait_for(lambda: rec.frames >= 8)
    _finish(model)
    assert red._frame_subscribers == ()
    row = _row(private_db, trial)
    folder = model.pictures_root / str(trial)
    assert row["video_path"] == str(folder / "trial.mp4")
    assert row["video_index_path"] == str(folder / "video_index.csv")
    index = _index(model, trial)
    assert list(index[0]) == ["frame", "t_s", "red", "z", "marked", "shade"]
    assert [int(r["frame"]) for r in index] == list(range(1, len(index) + 1))
    assert row["video_frames"] == len(index) >= 8
    assert _mp4_frames(row["video_path"]) == len(index)
    times = [float(r["t_s"]) for r in index]
    assert times == sorted(times) and times[0] >= 0
    assert all(float(r["z"]) == 1000.0 for r in index)      # the probe's Z
    assert all(0.0 <= float(r["red"]) <= 100.0 for r in index)


def test_the_rate_cap_writes_at_most_video_fps_and_counts_what_it_drops(
        station, private_db, monkeypatch):
    """Red Percent grabs far faster than VIDEO_FPS: the map writes at most
    VIDEO_FPS frames a second of it. A recorder that falls behind fills the
    bounded queue and the rest are dropped and counted, never waited for."""
    model, red, *_ = station
    trial = _arm(model)
    rec = model._trial.recording
    grabbed = red._run.frames
    started = time.monotonic()
    time.sleep(1.0)
    elapsed = time.monotonic() - started
    assert red._run.frames - grabbed > 3 * tm_module.VIDEO_FPS * elapsed
    accepted = rec.frames + rec.queue.qsize()
    assert accepted <= tm_module.VIDEO_FPS * (elapsed + 0.3) + 2
    assert accepted >= 0.5 * tm_module.VIDEO_FPS * elapsed
    real = tm_module.TrialRecorder.write

    def slow(self, frame, lines):
        time.sleep(0.3)
        return real(self, frame, lines)

    monkeypatch.setattr(tm_module.TrialRecorder, "write", slow)
    assert _wait_for(lambda: rec.dropped > 0, timeout=6.0)
    monkeypatch.setattr(tm_module.TrialRecorder, "write", real)   # the tail drains
    _finish(model)
    assert rec.closed.wait(5.0)
    row = _row(private_db, trial)
    assert row["video_dropped"] == rec.dropped > 0
    assert row["video_frames"] == len(_index(model, trial))


def test_a_slow_recorder_never_slows_red_percents_loop(station, monkeypatch):
    """Measured: Red Percent's frame rate with the map's recorder taking
    200 ms a frame stays within reach of its rate with no trial armed."""
    model, red, *_ = station

    def rate(seconds=0.6):
        frames, start = red._run.frames, time.monotonic()
        time.sleep(seconds)
        return (red._run.frames - frames) / (time.monotonic() - start)

    alone = rate()
    real = tm_module.TrialRecorder.write
    monkeypatch.setattr(tm_module.TrialRecorder, "write",
                        lambda self, f, l: (time.sleep(0.2), real(self, f, l))[1])
    _arm(model)
    loaded = rate()
    assert loaded >= 0.6 * alone, (alone, loaded)
    monkeypatch.setattr(tm_module.TrialRecorder, "write", real)
    model.run("abort_trial")


def test_mark_appears_in_the_index_and_the_label_from_the_mark_on(
        station, private_db, monkeypatch):
    model, red, *_ = station
    labels = []
    real = tm_module.TrialRecorder.write

    def spy(self, frame, lines):
        labels.append(list(lines))
        return real(self, frame, lines)

    monkeypatch.setattr(tm_module.TrialRecorder, "write", spy)
    trial = _arm(model)
    rec = model._trial.recording
    assert _wait_for(lambda: rec.frames >= 4)
    mark_t = model.mark_force()
    first_marked = rec.frames
    assert _wait_for(lambda: rec.frames >= first_marked + 4)
    _finish(model)
    index = _index(model, trial)
    flags = [int(r["marked"]) for r in index]
    assert flags[0] == 0 and flags[-1] == 1
    assert flags == sorted(flags), "MARK stays once pressed"
    for row, flag in zip(index, flags):
        assert flag == (float(row["t_s"]) >= mark_t - 1e-3), row
    # The band's first line is the measurement (MARK at its end from the
    # Mark on); the second names the trial (chip, flake, cut, 2026-09-28).
    assert all("MARK" not in l[0] for l, f in zip(labels, flags) if not f)
    assert all(l[0].endswith("MARK") for l, f in zip(labels, flags) if f)
    assert all(l[1].startswith(f"trial {trial}  tip ") for l in labels)
    assert labels[0][0].startswith("t=0.") and "red " in labels[0][0]
    assert " z 1000" in labels[0][0]
    folder = model.pictures_root / str(trial)
    assert (folder / "mark_frame.png").is_file()
    assert (folder / "first_frame.png").is_file()


@pytest.mark.parametrize("end", ["finish", "abort", "estop", "close"])
def test_finish_abort_and_the_stop_close_the_video_and_fill_the_columns(
        station, private_db, end):
    model, red, *_ = station
    trial = _arm(model)
    rec = model._trial.recording
    assert _wait_for(lambda: rec.frames >= 5)
    if end == "finish":
        _finish(model)
    elif end == "abort":
        assert model.run("abort_trial").is_ok
    elif end == "estop":
        started = time.monotonic()
        assert model.estop() is True
        assert time.monotonic() - started < 0.5
        model.disable()                      # joins the abort writer
    else:
        model.close()
    assert rec.closed.is_set()
    assert red._frame_subscribers == ()
    row = _row(private_db, trial)
    assert row["status"] == ("recorded" if end == "finish" else "aborted")
    assert row["video_frames"] == len(_index(model, trial)) >= 5
    assert row["video_dropped"] is not None
    assert _video_frames(row["video_path"]) == row["video_frames"]


def test_the_stop_never_closes_the_video_on_its_own_thread(station, private_db,
                                                           monkeypatch):
    """V5: the stop sets the recording closing and returns; the picture
    thread closes the file (bounded join by the abort writer)."""
    model, red, *_ = station
    closed_on = []
    real = tm_module.TrialRecorder.close

    def slow_close(self):
        closed_on.append(threading.current_thread().name)
        time.sleep(0.4)
        return real(self)

    monkeypatch.setattr(tm_module.TrialRecorder, "close", slow_close)
    trial = _arm(model)
    assert _wait_for(lambda: model._trial.recording.frames >= 3)
    started = time.monotonic()
    assert model.estop() is True
    assert time.monotonic() - started < 0.3
    model.disable()
    assert len(closed_on) == 1 and closed_on[0].startswith("transfer-map-pictures")
    assert _row(private_db, trial)["video_frames"] >= 3


def test_a_recorder_that_fails_mid_trial_warns_once_and_the_trial_goes_on(
        station, private_db, monkeypatch):
    model, red, *_ = station
    real = tm_module.TrialRecorder.write

    def failing(self, frame, lines):
        if self.frames >= 3:
            raise OSError("disk full")
        return real(self, frame, lines)

    monkeypatch.setattr(tm_module.TrialRecorder, "write", failing)
    events.forget("Video Stopped")
    since = events.latest_id
    trial = _arm(model)
    rec = model._trial.recording
    assert _wait_for(lambda: rec.failed)
    time.sleep(0.3)                           # more frames arrive; no more warnings
    assert model.is_armed
    assert "stopped" in model.video_status
    _finish(model)
    warned = _titled("Video Stopped", since)
    assert len(warned) == 1 and warned[0].severity == "warning"
    assert f"Trial {trial}" in warned[0].message
    row = _row(private_db, trial)
    assert row["status"] == "recorded" and row["video_frames"] == 3
    assert _video_frames(row["video_path"]) == 3
    assert len(_rows(private_db, "SELECT * FROM profile WHERE trial_id=?",
                     trial)) > 0          # the measurement is untouched


def test_without_an_encoder_the_trial_records_labelled_jpeg_frames(
        station, private_db, monkeypatch):
    model, red, *_ = station
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    trial = _arm(model)
    rec = model._trial.recording
    assert _wait_for(lambda: rec.frames >= 3)
    assert "no encoder: JPEG frames" in model.video_status
    _finish(model)
    folder = model.pictures_root / str(trial)
    row = _row(private_db, trial)
    assert row["video_path"] == str(folder / "frames")
    assert not (folder / "trial.mp4").exists()
    jpegs = sorted((folder / "frames").iterdir())
    assert len(jpegs) == row["video_frames"] == len(_index(model, trial))
    assert model.video_status.startswith("no encoder: JPEG frames")


def test_the_video_status_says_what_is_being_recorded_then_what_was(
        station, private_db):
    model, red, *_ = station
    assert model.video_status == "No video yet."
    _arm(model)
    rec = model._trial.recording
    assert _wait_for(lambda: rec.frames >= 2)
    import re
    assert re.match(r"recording, \d+ frames", model.video_status), model.video_status
    _finish(model)
    row = model._store.last()
    assert model.video_status == (f"no encoder: JPEG frames, "
                                  f"{row['video_frames']} frames, "
                                  f"{row['video_dropped']} dropped")


def test_the_video_encoder_is_in_diagnostics():
    """What the probe says without the encoder (the MP4 wording is checked
    in the one `real_encoder` test)."""
    assert TransferMap().video_encoder.startswith("imageio-ffmpeg is not installed")


def test_a_red_percent_without_the_frame_hook_arms_without_a_video(
        tmp_path, private_db):
    class Plain:
        region = {"top": 0, "left": 0, "width": 10, "height": 10}
        is_running = True
        run_token = None
        def subscribe(self, fn): pass
        def unsubscribe(self, fn): pass
        def grab_frame(self): return PNG + b"region"

    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", Plain())
    model.tip_id = "t"
    trial = _arm(model)
    assert model._trial.recording is None
    assert model.video_status == "no video: Red Percent has no frame hook"
    _finish(model)
    model.close()
    row = _row(private_db, trial)
    assert row["status"] == "recorded" and row["video_path"] is None


def test_a_version_four_database_gains_the_video_columns_and_keeps_its_trial(
        red, private_db):
    """The owner's bench file after the tips round is version 4 and holds
    trials: it gains the four video columns, keeps every trial, profile row
    and tip, and records a trial with its video."""
    _version_one_file(private_db, drop=V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS, version=4)
    before = _rows(private_db, "SELECT * FROM trials")
    tips_before = _rows(private_db, "SELECT * FROM tips")
    assert _version(private_db) == 4
    assert not set(V5_COLUMNS) & set(_columns(private_db))
    events.forget("Database Upgraded")
    since = events.latest_id
    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", red)
    model.tip_id = "T7"
    try:
        assert _version(private_db) == 8
        assert set(V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) <= set(_columns(private_db))
        after = _rows(private_db, "SELECT * FROM trials")
        assert len(after) == 1
        assert {k: after[0][k] for k in before[0]} == before[0]    # untouched
        assert all(after[0][c] is None for c in V5_COLUMNS)
        assert len(_rows(private_db, "SELECT * FROM profile")) == 5
        assert _rows(private_db, "SELECT * FROM tips") == tips_before
        upgraded = _titled("Database Upgraded", since)
        assert len(upgraded) == 1
        assert ("now has " + ", ".join(V5_COLUMNS + V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) + " (version 8)"
                in upgraded[0].message), \
            upgraded[0].message
        assert model.video_status == "No video for this trial."
        trial = _record(model, red)
        assert trial == 2
        row = _row(private_db, trial)
        assert row["video_frames"] > 0 and Path(row["video_path"]).exists()
        assert model._store.tip("T7")["trials"] == [1, 2]
    finally:
        model.close()


# -- V8: the map subscribes before it starts a run ------------------------------
# When the map starts Red Percent's run itself (Arm's fallback start, or the
# polling that starts itself), its first row used to land before the map
# subscribed, so the trial's profile lost the run's first sample (its
# baseline frame). Subscribed first; let go on every path that ends or
# refuses the run.

def _spy_start(monkeypatch, model, red, fail=None):
    seen = []
    real = red.start_run

    def spy(*args, **kwargs):
        seen.append((model._on_sample in red._subscribers,
                     model._on_frame in red._frame_subscribers))
        if fail is not None:
            raise fail
        return real(*args, **kwargs)

    monkeypatch.setattr(red, "start_run", spy)
    return seen


def test_an_arm_that_starts_the_run_keeps_its_first_row(idle_station, private_db):
    model, red = idle_station
    trial = _arm(model)
    assert _wait_for(lambda: len(model._trial.samples) >= 5)
    rec = model._trial.recording
    assert _wait_for(lambda: rec.frames >= 2)
    _finish(model)
    reds = [p["red"] for p in _rows(private_db, "SELECT red FROM profile "
                                    "WHERE trial_id=? ORDER BY rowid", trial)]
    logged = list(red._run.log.red_values)
    assert reds[0] == logged[0], "the run's first row is the trial's first sample"
    assert reds == logged[:len(reds)]
    times = [p["t_s"] for p in _rows(private_db, "SELECT t_s FROM profile "
                                     "WHERE trial_id=? ORDER BY rowid", trial)]
    assert times[0] >= 0
    # the video's first frame is the run's first frame too (always a row)
    assert float(_index(model, trial)[0]["red"]) == pytest.approx(logged[0], abs=1e-3)


def test_an_arm_subscribes_before_it_starts_the_run(idle_station, monkeypatch):
    model, red = idle_station
    seen = _spy_start(monkeypatch, model, red)
    _arm(model)
    assert seen == [(True, True)]
    model.run("abort_trial")
    assert red._subscribers == () and red._frame_subscribers == ()


def test_an_arm_whose_start_is_refused_lets_go(idle_station, monkeypatch):
    model, red = idle_station
    seen = _spy_start(monkeypatch, model, red, fail=Refused("no screen"))
    result = model.run("arm_trial", {"tip_id": "tip-A"}, (True,))
    assert result.is_refused and seen == [(True, True)]
    assert red._subscribers == () and red._frame_subscribers == ()
    assert model._arming is None and not model.is_armed


def test_an_arm_that_fails_after_starting_the_run_lets_go(idle_station,
                                                          monkeypatch):
    model, red = idle_station
    monkeypatch.setattr(red, "grab_frame", lambda: None)
    assert model.run("arm_trial", {"tip_id": "tip-A"}, (True,)).is_refused
    assert not red.is_running
    assert red._subscribers == () and red._frame_subscribers == ()


def test_the_self_started_polling_subscribes_before_its_run_starts(
        sheet, monkeypatch):
    model, red = sheet
    seen = _spy_start(monkeypatch, model, red)
    _commit_tip(model, "T7")
    assert red.is_running and seen == [(True, True)]
    assert model._trial is None           # nothing is kept before Arm
    model.estop()                         # ends the polling the map started
    assert not red.is_running
    assert red._subscribers == () and red._frame_subscribers == ()


def test_a_refused_polling_start_lets_go(sheet, monkeypatch):
    model, red = sheet
    seen = _spy_start(monkeypatch, model, red, fail=Refused("no screen"))
    _commit_tip(model, "T7")
    assert seen == [(True, True)] and not red.is_running
    assert red._subscribers == () and red._frame_subscribers == ()


def test_arm_taking_over_the_polling_keeps_the_rows_from_arm_on(sheet,
                                                                 private_db):
    """The polling the map started is the trial's once armed: its rows before
    Arm are not the trial's (time zero is Arm), every row after is."""
    model, red = sheet
    _commit_tip(model, "T7")
    assert _wait_for(lambda: red.rows_written >= 5)
    trial = _arm(model)
    assert _wait_for(lambda: len(model._trial.samples) >= 5)
    _finish(model)
    times = [p["t_s"] for p in _rows(private_db, "SELECT t_s FROM profile "
                                     "WHERE trial_id=? ORDER BY rowid", trial)]
    assert times and times[0] >= 0


# -- chip, flake and cut IDs (bench 2026-09-28) -------------------------------------
# "The runs no longer have labels. Each trial has a chip, flake, and cut ID
# for better sorting later." Typed on the sheet under the tip; on the row
# and in the trials export; in the video's band; the Red Percent run the
# map records through is named after the trial.

def _identify(model, sample="S1", chip="C1", flake="F2", cut="3"):
    model.sample_id, model.chip_id, model.flake_id, model.cut_id = sample, chip, flake, cut


def test_the_ids_are_tier_one_entries_that_travel_with_arm(tmp_path):
    model = TransferMap(db_path=tmp_path / "t.sqlite")
    tier1 = [e for e in sch.elements(model.schema) if e.get("tier", 1) == 1]
    attrs = [e.get("model_attr") for e in tier1]
    assert attrs.index("tip_id") < attrs.index("sample_id") < attrs.index("chip_id") \
        < attrs.index("flake_id") < attrs.index("cut_id") < attrs.index("typed_tilt")
    arm = next(e for e in tier1 if e.get("command") == "arm_trial")
    assert set(arm["inputs"]) >= {"tip_id", "sample_id", "chip_id", "flake_id", "cut_id"}


def test_next_step_asks_for_the_ids_after_the_tilt_and_never_refuses(idle_station):
    model, red = idle_station
    model.typed_tilt = ""
    step = lambda: model.state["values"]["next_step"]   # noqa: E731
    assert step() == "Type the tilt for this trial"
    model.typed_tilt = "7"
    assert step() == "Type the sample, chip, flake, cut IDs"
    model.sample_id, model.chip_id = "7/27/26", "C1"
    assert step() == "Type the flake, cut IDs"
    model.flake_id = "F2"
    assert step() == "Type the cut ID"
    model.cut_id = "3"
    assert step() == "Press Arm trial"
    assert model.ids_status == "sample 7/27/26  chip C1  flake F2  cut 3"
    model.cut_id = ""
    assert _arm(model) == 1                        # asked for, not required
    model.run("abort_trial")


def test_a_trial_records_its_chip_flake_and_cut_and_the_prompt_names_them(
        station, private_db):
    model, red, *_ = station
    _identify(model)
    asked = model.run("arm_trial", {"tip_id": "tip-A", "sample_id": "S1",
                                    "chip_id": "C1", "flake_id": "F2", "cut_id": "3"})
    assert asked.needs_confirm
    assert "arms trial 1 on tip tip-A (sample S1, chip C1, flake F2, cut 3) at " in asked.reason
    assert asked.inputs["chip_id"] == "C1" and asked.inputs["cut_id"] == "3"
    trial = _record(model, red)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert (row["sample_id"], row["chip_id"], row["flake_id"], row["cut_id"]) == ("S1", "C1", "F2", "3")
    assert "sample S1  chip C1  flake F2  cut 3" in model.trials_log[-1]
    trials_path, _profile, _tips = model._export()
    with open(trials_path, newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert (exported[-1]["sample_id"], exported[-1]["chip_id"], exported[-1]["flake_id"],
            exported[-1]["cut_id"]) == ("S1", "C1", "F2", "3")


def test_the_video_band_carries_the_trials_identity(station):
    model, red, *_ = station
    _identify(model)
    _arm(model)
    text, _z, marked = model._label(model._trial, 1.5, 42.0)
    assert text[0].startswith("t=1.50 s  red 42.0 %") and not marked
    assert text[1] == f"trial {model._trial.id}  tip tip-A  sample S1  chip C1  flake F2  cut 3"
    model.run("abort_trial")


def test_arm_names_the_polling_run_after_the_trial(sheet):
    """The run the map started for polling is the trial's once armed: its
    folder under ~/transfer-stage-runs/ says which trial, tip, chip, flake
    and cut, not `run_<timestamp>`."""
    model, red = sheet
    _identify(model, sample="7/27/26", chip="C1", flake="F2", cut="3")
    _commit_tip(model, "9/27/26 Tip1")
    token = red.run_token
    assert red.run_id == "C001"          # the Run / Cut ID typed on Red Percent's page
    trial = _arm(model)
    assert red.run_token is token                   # taken over, not restarted
    assert red.run_id == f"trial{trial:03d}_tip-9-27-26-Tip1_sample-7-27-26_chip-C1_flake-F2_cut-3"
    assert red.run_dir.name == red.run_id
    assert token.annotations["consumable_id"] == "9/27/26 Tip1"
    assert token.annotations["specimen_id"] == "sample 7/27/26 chip C1 flake F2"
    assert token.annotations["note"] == f"trial {trial}  sample 7/27/26  chip C1  flake F2  cut 3"
    _finish(model)
    assert not red.is_running


def test_arm_names_the_run_it_starts_itself_and_blank_ids_are_left_out(idle_station):
    model, red = idle_station
    model.chip_id = "C1"
    trial = _arm(model)
    assert red.is_running and red.run_id == f"trial{trial:03d}_tip-tip-A_chip-C1"
    model.run("abort_trial")


def test_the_operators_run_keeps_its_name(station):
    """The station fixture's run was started on the Red Percent page, named
    C001 there: it is theirs, and Arm leaves the name alone."""
    model, red, *_ = station
    _identify(model)
    _arm(model)
    assert red.run_id == "C001"
    model.run("abort_trial")


def test_run_label_is_folder_safe():
    label = TransferMap.run_label(TransferMap, 12, "9/27/26 Tip1", "", "chip A/B",
                                  "", "cut #4")
    assert label == "trial012_tip-9-27-26-Tip1_chip-chip-A-B_cut-cut-4"
    assert "/" not in label and " " not in label


def test_import_keeps_the_ids(tmp_path, private_db):
    path = tmp_path / "in.csv"
    path.write_text("tip_id,sample_id,chip_id,flake_id,cut_id,tilt_deg,speed_steps_s\n"
                    "T9,S9,C7,F1,2,10,300\n")
    model = TransferMap()
    model.open()
    try:
        assert model.run("import_csv", args=(str(path),)).is_ok
        row = model._store.trials()[-1]
        assert (row["sample_id"], row["chip_id"], row["flake_id"], row["cut_id"]) == ("S9", "C7", "F1", "2")
    finally:
        model.close()


# -- store version 6 (owner, 2026-10-04): trials name their flake; the cut
# descriptors (two AFM heights, an optical width); the store's identity ------

def test_a_fresh_database_is_version_six_with_an_identity(private_db):
    model = TransferMap()
    model.open()
    model.close()
    assert _version(private_db) == 8
    assert set(V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) <= set(_columns(private_db))
    meta = dict(_rows_raw(private_db, "SELECT key, value FROM meta"))
    assert set(meta) == {"map_db_uuid", "created_at"}
    import uuid
    assert uuid.UUID(meta["map_db_uuid"]).version == 4
    assert tm_module.TrialStore(private_db).meta() == meta
    tm_module.TrialStore(private_db).ensure()                # written once
    assert dict(_rows_raw(private_db, "SELECT key, value FROM meta")) == meta


def test_a_version_five_database_gains_the_v6_columns_and_an_identity(
        red, private_db):
    """The owner's bench file is version 5 and holds trials: it gains the
    v6 columns (NULL = not measured, nothing backfilled) and a store id, and
    keeps every trial, profile row and tip."""
    _version_one_file(private_db, drop=V6_COLUMNS + V7_COLUMNS + V8_COLUMNS, version=5)
    before = _rows(private_db, "SELECT * FROM trials")
    assert "meta" not in _tables(private_db)
    events.forget("Database Upgraded")
    since = events.latest_id
    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", red)
    model.tip_id = "T7"
    try:
        assert _version(private_db) == 8
        after = _rows(private_db, "SELECT * FROM trials")
        assert len(after) == 1
        assert {k: after[0][k] for k in before[0]} == before[0]    # untouched
        assert all(after[0][c] is None for c in V6_COLUMNS + V7_COLUMNS + V8_COLUMNS
                   if c != "invalid")
        assert after[0]["invalid"] == 0                  # valid unless flagged
        assert len(_rows(private_db, "SELECT * FROM profile")) == 5
        assert "map_db_uuid" in tm_module.TrialStore(private_db).meta()
        upgraded = _titled("Database Upgraded", since)
        assert len(upgraded) == 1
        assert ("now has " + ", ".join(V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) + " (version 8)"
                in upgraded[0].message), upgraded[0].message
        trial = _record(model, red)
        row = _row(private_db, trial)
        assert (row["operator_id"], row["operator_auth"]) == ("station", "station")
    finally:
        model.close()


def test_attach_afm_takes_the_two_heights_and_names_what_it_attached(
        station, private_db):
    """Q17: the AFM step from substrate to channel top (positive up) and the
    depth the tip cut into the flake (positive down), two nullable columns."""
    model, red, *_ = station
    trial = _record(model, red)
    since = events.latest_id
    result = model.run("attach_afm", {
        "afm_trial_id": str(trial), "width_um": "1.8", "width_sigma_um": "0.1",
        "channel_height_nm": "12", "channel_height_sigma_nm": "0.5",
        "trench_depth_nm": "3.5", "trench_depth_sigma_nm": "0"})
    assert result.is_ok, result
    row = _row(private_db, trial)
    assert row["status"] == "measured" and row["width_um"] == 1.8
    assert row["channel_height_nm"] == 12 and row["channel_height_sigma_nm"] == 0.5
    assert row["trench_depth_nm"] == 3.5 and row["trench_depth_sigma_nm"] is None
    attached = _titled("AFM Attached", since)[0].message
    assert attached == (f"Trial {trial}: width 1.8 um, channel height 12 nm, "
                        "trench depth 3.5 nm attached."), attached


def test_attach_afm_without_heights_leaves_them_unmeasured(station, private_db):
    model, red, *_ = station
    trial = _record(model, red)
    assert model.run("attach_afm", {"afm_trial_id": str(trial),
                                    "width_um": "2"}).is_ok
    row = _row(private_db, trial)
    assert row["channel_height_nm"] is None and row["trench_depth_nm"] is None


def test_attach_optical_writes_the_three_columns_and_keeps_the_status(
        station, private_db):
    """Q19: pixels on the capture-region picture times the Sample Map's
    um_per_px (`capture_px`); an optical width never makes a trial
    `measured` (that stays "an AFM width exists")."""
    model, red, *_ = station
    trial = _record(model, red)
    assert model.width_optical_method == "capture_px"
    assert model.width_optical_method_options == list(tm_module.WIDTH_OPTICAL_METHODS)
    since = events.latest_id
    result = model.run("attach_optical", {
        "afm_trial_id": str(trial), "width_optical_um": "2.1",
        "width_optical_sigma_um": "0.4"})
    assert result.is_ok, result
    row = _row(private_db, trial)
    assert row["width_optical_um"] == 2.1 and row["width_optical_sigma_um"] == 0.4
    assert row["width_optical_method"] == "capture_px"
    assert row["status"] == "recorded" and row["width_um"] is None
    assert _titled("Optical Width Attached", since)[0].message == \
        f"Trial {trial}: optical width 2.1 um (capture_px) attached."
    assert model.run("set_width_optical_method", None, ("reticle",)).is_ok
    assert model.run("set_width_optical_method", None, ("ruler",)).is_refused


def test_attach_optical_refuses_an_unknown_trial_no_width_or_an_armed_one(
        station):
    model, red, *_ = station
    trial = _record(model, red)
    assert "No trial 99" in model.run("attach_optical", {
        "afm_trial_id": "99", "width_optical_um": "3"}).reason
    assert model.run("attach_optical", {"afm_trial_id": str(trial),
                                        "width_optical_um": "0"}).is_refused
    armed = _arm(model)
    try:
        assert "armed" in model.run("attach_optical", {
            "afm_trial_id": str(armed), "width_optical_um": "3"}).reason
    finally:
        model.abort_trial()


def test_the_trials_log_names_each_widths_source(station, private_db):
    model, red, *_ = station
    afm, optical, both, none = (_record(model, red) for _ in range(4))
    store = model._store
    store.update(afm, {"width_um": 1.8, "status": "measured",
                       "channel_height_nm": 12.0})
    store.update(optical, {"width_optical_um": 2.1})
    store.update(both, {"width_um": 1.5, "width_optical_um": 2.0,
                        "status": "measured", "trench_depth_nm": 3.0})
    lines = {int(line.split()[0]): line for line in model.trials_log}
    assert "1.8 um (AFM)" in lines[afm] and "height 12 nm" in lines[afm]
    assert "~2.1 um (optical)" in lines[optical]
    assert "1.5 um (AFM), optical 2 um" in lines[both]
    assert "trench 3 nm" in lines[both]
    assert "no width" in lines[none]


def test_an_import_reads_the_v6_columns_and_only_afm_makes_measured(
        tmp_path, private_db):
    typed = tmp_path / "typed.csv"
    typed.write_text(
        "tilt_deg,speed_steps_s,force_index,width_um,width_optical_um,"
        "width_optical_sigma_um,width_optical_method,channel_height_nm,"
        "trench_depth_nm,sample_id,flake_uid,operator_id\n"
        "10,100,0.2,,2.5,0.3,reticle,11,2,S1,F1,ian\n"
        "20,200,0.4,1.9,,,,,,,,\n")
    model = TransferMap()
    assert model.import_csv(str(typed))["imported"] == 2
    first, second = _rows(private_db, "SELECT * FROM trials ORDER BY id")
    assert first["status"] == "recorded" and first["width_optical_um"] == 2.5
    assert first["width_optical_method"] == "reticle"
    assert (first["channel_height_nm"], first["trench_depth_nm"]) == (11, 2)
    assert (first["sample_id"], first["flake_uid"], first["operator_id"]) == \
        ("S1", "F1", "ian")
    assert second["status"] == "measured" and second["width_optical_um"] is None


def test_the_map_rows_carry_both_widths_and_the_chosen_one(station):
    model, red, *_ = station
    afm, optical = _record(model, red), _record(model, red)
    model._store.update(afm, {"width_um": 1.8, "width_sigma_um": 0.1,
                              "width_optical_um": 2.4, "status": "measured"})
    model._store.update(optical, {"width_optical_um": 2.1})
    rows = {r["id"]: r for r in model._map_rows()}
    assert (rows[afm]["width"], rows[afm]["width_sigma"],
            rows[afm]["width_source"]) == (1.8, 0.1, "afm")
    assert rows[afm]["width_optical"] == 2.4 and rows[afm]["width_afm"] == 1.8
    assert (rows[optical]["width"], rows[optical]["width_source"]) == (2.1, "optical")


def test_the_width_source_dropdown_reaches_the_figure(station):
    model, red, *_ = station
    _record(model, red)
    assert model.width_source == "AFM only"
    assert model.width_source_options == list(plot_data.WIDTH_SOURCES)
    model.set_figure_type("Heatmap")
    first = model.figure
    assert model.run("set_width_source", None, ("AFM, else optical",)).is_ok
    assert model.figure is not first
    assert model.run("set_width_source", None, ("optical only",)).is_refused


def test_the_v6_controls_are_tier_two():
    model = TransferMap()
    tiers = {}
    for section in model.schema["sections"]:
        for element in section["elements"]:
            key = element.get("model_attr") or element.get("command")
            tiers[key] = (section.get("tier", 1), section["title"])
    for key in ("channel_height_nm", "trench_depth_nm"):
        assert tiers[key] == (2, "AFM measurement"), key
    for key in ("width_optical_um", "width_optical_sigma_um", "attach_optical",
                "width_optical_method"):
        assert tiers[key] == (2, "Optical measurement"), key
    assert tiers["width_source"][0] == 2
    afm = next(s for s in model.schema["sections"] if s["title"] == "AFM measurement")
    button = next(e for e in afm["elements"] if e.get("command") == "attach_afm")
    assert {"channel_height_nm", "channel_height_sigma_nm", "trench_depth_nm",
            "trench_depth_sigma_nm"} <= set(button["inputs"])
    labels = [e.get("text") for e in afm["elements"]]
    assert "Channel width (AFM)" in labels


def test_a_version_five_database_gains_the_id_columns(private_db):
    _version_one_file(private_db, drop=V6_COLUMNS + V7_COLUMNS + V8_COLUMNS, version=5)
    assert not set(V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) & set(_columns(private_db))
    model = TransferMap()
    model.open()
    try:
        assert _version(private_db) == 8
        assert set(V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) <= set(_columns(private_db))
        after = _rows(private_db, "SELECT * FROM trials")
        assert len(after) == 1 and all(after[0][c] is None for c in V6_COLUMNS + V7_COLUMNS + V8_COLUMNS if c != "invalid") and after[0]["invalid"] == 0
        assert model.trials_log[-1].startswith("   1  measured T7  12.5 deg")
    finally:
        model.close()


def test_a_version_seven_database_gains_the_invalid_flag_all_valid(private_db):
    _version_one_file(private_db, drop=("invalid",) + V8_COLUMNS, version=7)
    assert "invalid" not in _columns(private_db)
    model = TransferMap()
    model.open()
    try:
        assert _version(private_db) == 8
        after = _rows(private_db, "SELECT * FROM trials")
        assert len(after) == 1 and after[0]["invalid"] == 0
        assert [r["id"] for r in model._map_rows()] == [1]
    finally:
        model.close()


def test_finish_clears_the_cut_id_and_keeps_chip_and_flake(station):
    """Cut and go: the next cut on the same flake is asked for its own ID."""
    model, red, *_ = station
    _identify(model)
    _record(model, red)
    assert (model.sample_id, model.chip_id, model.flake_id, model.cut_id) == ("S1", "C1", "F2", "")
    assert model.state["values"]["next_step"] == "Type the cut ID"


# -- a file numbered ahead of its columns (the owner's own earlier numbering) --

@pytest.mark.parametrize("ahead", [7, 8, 9])
def test_a_file_numbered_ahead_but_missing_columns_is_completed(private_db, ahead):
    """The bench file was once numbered 8 with chip/flake/cut/sample/invalid
    and none of the later version-6 columns; the version number must not
    hide the missing columns. Its trials, profile and tips are kept."""
    _version_one_file(private_db, drop=V6_COLUMNS, version=ahead)
    before = _rows(private_db, "SELECT * FROM trials")
    assert tm_module.TrialStore(private_db).ensure() is False
    assert _version(private_db) == max(ahead, tm_module.SCHEMA_VERSION)
    assert set(V6_COLUMNS + V7_COLUMNS + V8_COLUMNS) <= set(_columns(private_db))
    after = _rows(private_db, "SELECT * FROM trials")
    assert {k: after[0][k] for k in before[0]} == before[0]
    assert len(_rows(private_db, "SELECT * FROM profile")) == 5
    assert "map_db_uuid" in tm_module.TrialStore(private_db).meta()


def test_the_invalid_flag_survives_a_completing_migration(private_db):
    _version_one_file(private_db, drop=V6_COLUMNS, version=8)
    with sqlite3.connect(private_db) as db:
        db.execute("UPDATE trials SET invalid = 1")
    tm_module.TrialStore(private_db).ensure()
    assert _rows(private_db, "SELECT invalid FROM trials")[0]["invalid"] == 1
