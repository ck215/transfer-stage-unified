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
from test_red_monitor import fake_screen


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
    model = RedMonitor(screen=fake_screen())
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


def test_arm_refuses_while_red_percent_is_not_running(red):
    model = TransferMap()
    model.on_model_added("Red Percent", red)
    model.tip_id = "tip-A"
    with pytest.raises(Refused, match="Start a Red Percent run"):
        model.arm_trial()


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
                "trial_count", "trial_status", "db_path", "new_database"):
        assert tiers[key] == 1, key
    for key in ("set_figure_type", "set_force_definition", "attach_afm",
                "export_csv", "import_csv"):
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
