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
import threading
import time
from pathlib import Path

import pytest

import schema as sch
from controller.controller import Controller
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
    old_before = _rows(private_db, "SELECT before_path FROM trials")[0]["before_path"]
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
    new_before = _rows(new_path, "SELECT before_path FROM trials")[0]["before_path"]
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
    before = Path(row["before_path"])
    assert before == private_db.parent / "transfer_map" / str(trial) / "before.png"
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
    assert model.tilt_now == 30.0


def test_arm_refuses_a_typed_tilt_that_is_not_a_number(station):
    model = station[0]
    result = model.run("arm_trial", {"tip_id": "t", "typed_tilt": "steep"})
    assert result.is_refused and "Tilt" in result.reason


# -- recording -------------------------------------------------------------------

def test_a_recorded_trial_keeps_its_raw_profile_marks_and_frames(station, private_db):
    model, red, rotator, probe = station
    trial = _record(model, red, note="first cut")
    assert not model.is_active and red._subscribers == ()
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "recorded" and row["note"] == "first cut"
    assert row["mark_operator_t"] is not None and row["z_contact"] == 1000.0
    assert row["red_baseline"] is not None
    assert row["mark_auto_max_t"] is not None
    assert row["red_min"] <= row["red_max"]
    assert Path(row["after_path"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
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
    for command in ("set_figure_type", "set_force_definition", "set_force_band"):
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
                "before_image", "after_image"):
        assert tiers[key] == 1, key
    for key in ("set_figure_type", "set_force_definition", "attach_afm",
                "export_csv", "import_csv", "before_full_image",
                "after_full_image"):
        assert tiers[key] == 2, key
    for key in ("trials_log", "delete_trial", "last_trial_numbers"):
        assert tiers[key] == 3, key
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
    assert keys[at + 1] == "tip_trial_count"
    element = trial["elements"][at + 1]
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
    monkeypatch.setattr(red.screen, "screenshot_png", lambda: (b"PNG!", bounds))
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
    assert step() == "Set the capture region"
    model.on_model_removed("Red Percent", bare)
    model.on_model_added("Red Percent", red)
    model.tip_id = " "
    assert step() == "Type a tip ID"
    model.tip_id = "tip-A"
    assert step() == "Press Arm trial"
    if not red.is_running:
        red.start_run(confirmed=True)       # before T2, Arm needs a run
    _arm(model)
    assert step() == "Lower the tip; press Mark force when the force is right"
    model.mark_force()
    assert step() == "Press Finish trial"
    _finish(model)
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
    assert result.reason == ("Frame the sample now. Continue takes the before "
                             "picture and arms trial 1 on tip T7.")
    assert result.command == "arm_trial"
    assert result.inputs == {"tip_id": "T7", "typed_tilt": ""}
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
    assert result.reason == f"Continue takes the after picture and ends trial {trial}."
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
    assert result.reason == ("No before picture: the capture region is not "
                             "set or the screen is not open.")
    assert not model.is_armed and model.trial_count == 0
    assert not red.is_running                 # the run it started is ended
    assert not _titled("No Picture", since)   # a refusal, not a tray warning


def test_arm_without_a_picture_leaves_the_operators_run_running(station,
                                                                monkeypatch):
    model, red, *_ = station
    monkeypatch.setattr(red, "grab_frame", lambda: None)
    assert model.run("arm_trial", {"tip_id": "tip-A"}, (True,)).is_refused
    assert red.is_running


def test_finish_without_an_after_picture_is_refused_and_stays_armed(
        station, monkeypatch, private_db):
    model, red, *_ = station
    trial = _arm(model)
    monkeypatch.setattr(red, "grab_frame", lambda: None)
    result = model.run("finish_trial", {"note": ""}, (True,))
    assert result.is_refused and result.reason.startswith(
        "No after picture: the capture region is not set or the screen is "
        "not open.")
    assert model.is_armed
    assert model.run("abort_trial").is_ok     # the way out still works
    model.disable()
    assert _rows(private_db, "SELECT status FROM trials WHERE id=?",
                 trial)[0]["status"] == "aborted"


def test_the_pictures_show_on_the_sheet(station):
    model, red, *_ = station
    assert model.before_image == b"" and model.after_image == b""
    first = _arm(model)
    folder = model.pictures_root / str(first)
    assert model.before_image == (folder / "before.png").read_bytes()
    assert model.before_image[:8] == PNG
    assert model.after_image == b""           # not the last trial's
    _finish(model)
    assert model.after_image == (folder / "after.png").read_bytes()
    assert model.after_image[:8] == PNG
    second = _arm(model)
    assert model.before_image == (model.pictures_root / str(second)
                                  / "before.png").read_bytes()
    assert model.after_image == b""
    model.run("abort_trial")
    model.disable()
    model.new_database()
    assert model.before_image == b"" and model.after_image == b""
    for command in ("before_image", "after_image"):
        assert model.run(command).is_ok       # declared data sources


def test_the_picture_elements_say_when_they_are_taken():
    model = TransferMap()
    before, after = _element(model, "before_image"), _element(model, "after_image")
    assert (before["type"], before["text"], before["empty"]) == \
        ("image", "Before picture", "Taken when you arm.")
    assert (after["type"], after["text"], after["empty"]) == \
        ("image", "After picture", "Taken when you finish.")


# -- T6: the order of the sheet ------------------------------------------------

def test_the_sheet_reads_in_the_order_a_trial_is_run():
    model = TransferMap()
    sections = model.schema["sections"]
    tier_one = [s["title"] for s in sections if s.get("tier", 1) == 1
                and s["title"] != "Safety"]
    assert tier_one == ["Session", "Trial"]
    trial = next(s for s in sections if s["title"] == "Trial")
    keys = [e.get("command") if e["type"] in ("button", "region_select")
            else e.get("model_attr") or e.get("command") or e.get("data_command")
            for e in trial["elements"]]
    assert keys == ["next_step", "set_region", "tilt_now", "speed_now",
                    "red_now", "tip_id", "tip_trial_count", "arm_trial",
                    "mark_force", "note", "finish_trial", "abort_trial",
                    "is_broke", "trial_status", "before_image",
                    "after_image", "live_series", "figure"]
    later = [(s["title"], s.get("tier")) for s in sections
             if s.get("tier", 1) != 1]
    assert later == [("Full pictures", 2), ("Figure", 2),
                     ("AFM measurement", 2), ("Data", 2),
                     ("Diagnostics", 3), ("Safety", 3)]
    diagnostics = next(s for s in sections if s["title"] == "Diagnostics")
    assert [e.get("model_attr") or e.get("source_command") or e.get("command")
            for e in diagnostics["elements"]] == [
        "last_trial_numbers", "width_gradient", "trials_log", "delete_trial"]


# -- full pictures: the whole screen at Arm and at Finish (2026-09-28) ----------
# "I need additional full image captures included." The region pictures stay
# the gate; the whole-screen pictures are the record, so a missing one warns.

def _size(png):
    import io
    from PIL import Image
    return Image.open(io.BytesIO(png)).size


DESKTOP_SIZE = (DesktopCapture.DESKTOP["width"], DesktopCapture.DESKTOP["height"])


def test_arm_and_finish_keep_whole_screen_pictures(station, private_db):
    model, red, *_ = station
    trial = _arm(model)
    folder = model.pictures_root / str(trial)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert Path(row["before_full_path"]) == folder / "before_full.png"
    assert row["after_full_path"] is None
    _finish(model)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert Path(row["after_full_path"]) == folder / "after_full.png"
    assert sorted(p.name for p in folder.iterdir()) == [
        "after.png", "after_full.png", "before.png", "before_full.png"]
    for name in ("before_full.png", "after_full.png"):
        png = (folder / name).read_bytes()
        assert png[:8] == PNG and _size(png) == DESKTOP_SIZE, name
    for name in ("before.png", "after.png"):          # the region, unchanged
        assert _size((folder / name).read_bytes()) == (10, 10), name


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
    assert len(_titled("No Full Picture", since)) == 2
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "recorded"
    assert row["before_full_path"] is None and row["after_full_path"] is None
    assert row["before_path"] and row["after_path"]
    folder = model.pictures_root / str(trial)
    assert sorted(p.name for p in folder.iterdir()) == ["after.png", "before.png"]


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
    full = next(s for s in sections if s["title"] == "Full pictures")
    assert full.get("tier") == 2
    assert full.get("disclosure") == "Configure Transfer Map"
    assert [(e["type"], e["text"], e["data_command"]) for e in full["elements"]] \
        == [("image", "Before, whole screen", "before_full_image"),
            ("image", "After, whole screen", "after_full_image")]
    assert all(e["empty"] for e in full["elements"])
    for command in ("before_full_image", "after_full_image"):
        assert model.run(command).is_ok       # declared data sources


def test_the_full_pictures_show_the_armed_trial_else_the_last(station):
    model, red, *_ = station
    assert model.before_full_image == b"" and model.after_full_image == b""
    first = _arm(model)
    folder = model.pictures_root / str(first)
    assert model.before_full_image == (folder / "before_full.png").read_bytes()
    assert model.after_full_image == b""
    _finish(model)
    assert model.after_full_image == (folder / "after_full.png").read_bytes()
    assert _size(model.after_full_image) == DESKTOP_SIZE
    second = _arm(model)
    assert model.before_full_image == (model.pictures_root / str(second)
                                       / "before_full.png").read_bytes()
    assert model.after_full_image == b""      # not the last trial's
    model.run("abort_trial")
    model.disable()
    model.new_database()
    assert model.before_full_image == b"" and model.after_full_image == b""


def test_export_carries_the_full_picture_paths(station):
    model, red, *_ = station
    trial = _record(model, red)
    rows = list(csv.DictReader(Path(model.export_csv()).open()))
    folder = model.pictures_root / str(trial)
    assert rows[0]["before_full_path"] == str(folder / "before_full.png")
    assert rows[0]["after_full_path"] == str(folder / "after_full.png")
    header = list(rows[0])
    assert header.index("before_full_path") == header.index("after_path") + 1
    assert header.index("after_full_path") == header.index("after_path") + 2


# -- the store migrates: version 1 -> 2 ------------------------------------------

V2_COLUMNS = ("before_full_path", "after_full_path")


def _version(path):
    with sqlite3.connect(path) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def _columns(path):
    with sqlite3.connect(path) as db:
        return [r[1] for r in db.execute("PRAGMA table_info(trials)")]


def _version_one_file(path, drop=V2_COLUMNS):
    """A database as the round before this one wrote it: the version-1
    trials table (no whole-screen columns), one recorded trial with a
    profile, `user_version = 1`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [(n, k) for n, k in tm_module.TRIAL_COLUMNS if n not in drop]
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE trials (" + ", ".join(f"{n} {k}" for n, k in columns)
               + ")")
    db.execute("CREATE TABLE profile (trial_id INTEGER NOT NULL REFERENCES "
               "trials(id), t_s REAL NOT NULL, red REAL, z REAL, x REAL, y REAL)")
    db.execute("CREATE INDEX profile_trial ON profile(trial_id)")
    db.execute("INSERT INTO trials (started_at, tip_id, tilt_deg, speed_steps_s, "
               "width_um, before_path, after_path, status, note) VALUES "
               "('2026-09-27T15:00:00', 'T7', 12.5, 300, 4.2, '/b.png', "
               "'/a.png', 'measured', 'bench')")
    db.executemany("INSERT INTO profile VALUES (1, ?, ?, NULL, NULL, NULL)",
                   [(0.1 * i, 10.0 + i) for i in range(5)])
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()


def test_a_fresh_database_is_version_two_with_the_full_picture_columns(
        private_db):
    assert tm_module.SCHEMA_VERSION == 2
    model = TransferMap()
    model.open()
    model.close()
    assert _version(private_db) == 2
    assert set(V2_COLUMNS) <= set(_columns(private_db))


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
        assert _version(private_db) == 2
        assert set(V2_COLUMNS) <= set(_columns(private_db))
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
        assert model.before_image == b""      # the stored paths are gone files
        assert model.run("before_full_image").is_ok
        assert model.before_full_image == b""
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
        assert row["before_full_path"] and row["after_full_path"]
        assert model.tip_trial_count == 2
    finally:
        model.close()


def test_the_first_write_migrates_too(private_db):
    """Not only `open`: a store written before anything opened it (an
    import from a script, a view reading an old file) upgrades on write."""
    _version_one_file(private_db)
    store = tm_module.TrialStore(private_db)
    store.insert({"tip_id": "T8", "status": "recorded",
                  "before_full_path": "/x.png"})
    assert _version(private_db) == 2
    assert [r["tip_id"] for r in store.trials()] == ["T7", "T8"]


def test_a_half_done_upgrade_finishes(private_db):
    """A version-1 file that already has one of the two columns (an upgrade
    cut short between the two ALTERs) gets the other and version 2."""
    _version_one_file(private_db, drop=("after_full_path",))
    assert _version(private_db) == 1
    assert tm_module.TrialStore(private_db).ensure() is False
    assert _version(private_db) == 2
    assert set(V2_COLUMNS) <= set(_columns(private_db))


def test_a_version_two_database_is_left_alone(private_db):
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
    assert _version(private_db) == 2
    assert not _titled("Database Upgraded", since)
