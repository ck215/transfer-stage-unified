"""`model.transfer_map`: the Transfer Map model, its store and its guided trial.

Every test uses a temporary database (owner ruling 2026-09-27: tests never
touch the project database). Red Percent is the real model over an injected
capture factory (`tests/test_red_monitor.py`'s fake screen), so the samples
arrive through the real subscribe hook on the real run thread; the tilt and
speed sources are duck-typed stand-ins, plus the real SIM Rotator. The
stage still is taken by the real `ScreenRecorder` over an injected frame
source (`fake_display`): no test grabs the real screen.

The trial is a procedure (owner ruling 2026-10-07): setup -> Arm ->
region (the still is taken; the region is picked on it) -> live -> Mark
force -> marked -> End recording -> finish -> Finish -> setup. `_arm`,
`_finish` and `_record` walk it through the Panel, as a view does.
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
from model import plot_data
from model import transfer_map as tm_module
from model.red_monitor import RedMonitor
from model.rotator import Rotator
from model.transfer_map import TransferMap
from events import events
from result import NeedsConfirm, Refused
from test_red_monitor import desktop_screen, fake_screen


@pytest.fixture(autouse=True)
def private_db(tmp_path, monkeypatch):
    """No test reaches the project database: the override points at tmp."""
    path = tmp_path / "db" / "transfer_map.sqlite"
    monkeypatch.setenv("STATION_MAP_DB", str(path))
    return path


@pytest.fixture(autouse=True)
def jpeg_recorder(request, monkeypatch):
    """No encoder: `devices.video`'s lazy import of it answers None, so
    `ScreenRecorder.start()` raises and a trial records without a video
    (TM-4: never refused); no test here starts an ffmpeg unless it asks.
    The `wired` tests inject a fake recorder; ONE test asks for
    `real_encoder` and records a real MP4 of the fake display,
    `test_the_whole_display_is_recorded_end_to_end` (grep `real_encoder`)."""
    if "real_encoder" in request.fixturenames:
        return
    from devices import video
    monkeypatch.setattr(video, "_encoder", lambda: None)


@pytest.fixture
def real_encoder():
    """Opt out of `jpeg_recorder`: this test records a real H.264 MP4."""
    return pytest.importorskip("imageio_ffmpeg")


#: The display the fake source shows: (width, height) of the stage still.
STAGE_SIZE = (96, 64)


def display_frame():
    """One BGRA frame of the fake display: dark, a red block at its centre."""
    import numpy
    width, height = STAGE_SIZE
    frame = numpy.zeros((height, width, 4), dtype=numpy.uint8)
    frame[:, :, 3] = 255
    frame[16:48, 32:64, 2] = 220                  # BGRA: red
    return frame


def display_source():
    """A `ScreenRecorder` frame source: a new frame per call, and its time."""
    return display_frame(), time.monotonic()


class FakeRecorder:
    """A `ScreenRecorder` for the map's tests: logs each call (and the
    thread a stop runs on); its stills are the fake display's PNG."""

    def __init__(self, out_dir, fps, monitor, log, fail=None, stop_delay=0.0,
                 still_delay=0.0, stop_fail=None):
        self.out_dir, self.fps, self.monitor = Path(out_dir), fps, monitor
        self.log, self.fail, self.stop_fail = log, fail, stop_fail
        self.stop_delay, self.still_delay = stop_delay, still_delay

    def start(self):
        self.log.append(("start", self.out_dir, self.fps, self.monitor))
        if self.fail is not None:
            raise self.fail
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "screen.mp4").write_bytes(b"not really an mp4")
        (self.out_dir / "frames.csv").write_text("frame,t_monotonic,t_wall,marked\n")

    @property
    def stats(self):
        return {"frames": 12, "dropped_full": 1}

    def mark(self, label):
        self.log.append(("mark", label))

    def stop(self):
        self.log.append(("stop", threading.current_thread().name))
        time.sleep(self.stop_delay)
        if self.stop_fail is not None:
            raise self.stop_fail
        from devices.screen_recorder import RecorderResult
        return RecorderResult(self.out_dir / "screen.mp4",
                              self.out_dir / "frames.csv", 42, 2)

    def capture_still(self, path):
        from devices.screen_recorder import ScreenRecorder
        name = Path(path).name
        if name.startswith("mark"):
            time.sleep(self.still_delay)
        ScreenRecorder(Path(path).parent, 15, 1,
                       frame_source=display_source).capture_still(path)
        self.log.append(("still", name))
        return path


class FakeTelemetry:
    """A `TrialTelemetry` for the map's tests: logs start and stop and
    hands back two rows on the monotonic clock."""

    def __init__(self, controller, log, fail=None):
        self.controller, self.log, self.fail = controller, log, fail

    def start(self, trial_id):
        self.log.append(("telemetry.start", trial_id))
        if self.fail is not None:
            raise self.fail

    def stop(self):
        self.log.append(("telemetry.stop", threading.current_thread().name))
        return [(1.5, "stepper_probe.z", 1000.0), (1.6, "events.info", "Run Started")]


def _wire(model, recorder=None, telemetry=None):
    """Give `model` the fake recorder and telemetry factories. -> (log,
    made): every call, in order; the last recorder and telemetry built."""
    log, made = [], {}

    def recorders(out_dir, fps, monitor):
        made["recorder"] = FakeRecorder(out_dir, fps, monitor, log,
                                        **(recorder or {}))
        return made["recorder"]

    def telemetries(controller):
        made["telemetry"] = FakeTelemetry(controller, log, **(telemetry or {}))
        return made["telemetry"]

    model._recorder_factory = recorders
    model._telemetry_factory = telemetries
    return log, made


@pytest.fixture(autouse=True)
def fake_display(monkeypatch):
    """The map's default recorder (the stage still's device) over the fake
    display: the real `ScreenRecorder`, never the real screen."""
    from devices.screen_recorder import ScreenRecorder
    made = []

    def factory(out_dir, fps, monitor):
        made.append((Path(out_dir), fps, monitor))
        return ScreenRecorder(out_dir, fps, monitor, frame_source=display_source)

    monkeypatch.setattr(tm_module, "_make_recorder", factory)
    return made


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
    """A Transfer Map beside Red Percent (a region, no run: the trial
    starts its own), a rotator and a probe."""
    rotator, probe = FakeRotator(), FakeProbe()
    model = TransferMap()
    model.open()
    for name, other in (("Red Percent", red), ("Rotator", rotator),
                        ("Stepper Probe", probe)):
        model.on_model_added(name, other)
    red.source_name = "Stepper Probe"
    model.tip_id = "tip-A"
    yield model, red, rotator, probe
    model.close()


@pytest.fixture
def wired(station):
    """The station, its map given the fake recorder and telemetry."""
    model, red, rotator, probe = station
    log, made = _wire(model)
    return model, red, log, made


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


#: The capture region every trial picks on its stage still.
REGION = (0, 0, 10, 10)


def _arm_only(model, tip=None):
    """Arm, as a view does: answer its question. -> the `region` step."""
    _confirmed(model, "arm_trial",
               {"tip_id": tip if tip is not None else model.tip_id})
    assert model.phase == "region", model.phase


def _arm(model, tip=None, region=REGION):
    """Arm and pick the region: the trial is `live`. -> its id."""
    _arm_only(model, tip)
    result = model.run("set_region", None, region)
    assert result.is_ok, result
    assert model.phase == "live", model.phase
    return result.value


def _finish(model, note=""):
    """End the recording (from `marked` as the sheet does; from `live`
    through the model, for a trial with no Mark), then keep it."""
    if model.phase == "marked":
        assert model.run("end_recording").is_ok
    elif model.phase == "live":
        model.end_recording()
    assert model.phase == "finish", model.phase
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


# -- A3: the trial store is chosen, remembered, and never inside the install --

@pytest.fixture
def no_store(tmp_path, monkeypatch):
    """No STATION_MAP_DB, a private operator-choices file, and an install
    root in tmp_path (so a store left in the real checkout is not seen)."""
    from controller import user_config
    monkeypatch.delenv("STATION_MAP_DB", raising=False)
    monkeypatch.setenv("STATION_CONFIG", str(tmp_path / "choices" / "station.json"))
    user_config.forget()
    monkeypatch.setattr(TransferMap, "choices", user_config)
    install = tmp_path / "install"
    (install / "src").mkdir(parents=True)
    monkeypatch.setattr(tm_module, "_install_root", lambda: install)
    yield install
    user_config.forget()


def _store_elements(model):
    [section] = [s for s in model.schema["sections"] if s["title"] == "Store"]
    return [(e["type"], e.get("command") or e.get("model_attr"))
            for e in section["elements"]]


def test_with_nothing_chosen_the_map_has_no_store_and_asks(no_store):
    """Owner decision 4 (2026-09-30): no default. The map comes up without
    a store, says so in its state, and its schema asks."""
    assert TransferMap.default_db_path() is None
    model = TransferMap()
    assert model.db_path is None and model.output_root is None
    assert model.state["store"] == {"path": None, "chosen": False}
    assert _store_elements(model) == [
        ("readonly", "store_status"),
        ("entry", "store_path"), ("button", "open_store"),
        ("entry", "store_dir"), ("entry", "store_name"), ("button", "new_store")]
    assert model.state["values"]["store_status"].startswith("Not chosen")


def test_opening_without_a_store_warns_and_creates_nothing(no_store, tmp_path):
    seen = []
    events.subscribe(seen.append)
    try:
        model = TransferMap()
        model.open()
        model.close()
    finally:
        events.unsubscribe(seen.append)
    [ask] = [e for e in seen if e.title == "Trial Store Not Chosen"]
    assert "Transfer Map, Store" in ask.message
    assert not list(tmp_path.rglob("*.sqlite"))


@pytest.mark.parametrize("command, inputs", [
    ("arm_trial", {"tip_id": "tip-A"}),
    ("new_tip", {"tip_id": "tip-A"}),
    ("set_tip_note", {"tip_id": "tip-A", "tip_note": "sharp"}),
    ("retire_tip", {"tip_id": "tip-A"}),
    ("unretire_tip", {"tip_id": "tip-A"}),
    ("attach_afm", {"afm_trial_id": "1", "width_um": "2"}),
    ("set_trial_tilt", {"afm_trial_id": "1", "typed_tilt": "3"}),
    ("set_trial_speed", {"afm_trial_id": "1", "typed_speed": "3"}),
    ("delete_trial", {"trial_pick": "1"}),
    ("new_database", None),
    ("export_csv", None),
])
def test_every_recording_command_is_refused_without_a_store(no_store, tmp_path,
                                                            command, inputs):
    model = TransferMap()
    model.open()
    try:
        result = model.run(command, inputs)
        assert result.status == "refused"
        assert result.reason == "Choose a trial store first (Transfer Map, Store)."
    finally:
        model.close()
    assert not list(tmp_path.rglob("*.sqlite"))


def test_import_is_refused_without_a_store(no_store, tmp_path):
    csv_path = tmp_path / "in.csv"
    csv_path.write_text("tilt_deg,speed_steps_s\n10,100\n")
    model = TransferMap()
    with pytest.raises(Refused) as refused:
        model.import_csv(str(csv_path))
    assert refused.value.reason == "Choose a trial store first (Transfer Map, Store)."


def test_a_new_store_is_created_where_the_operator_says_and_remembered(no_store, tmp_path):
    folder = tmp_path / "lab data"
    model = TransferMap()
    result = model.run("new_store", {"store_dir": str(folder), "store_name": "october"})
    assert result.is_ok, result
    path = folder / "october.sqlite"
    assert path.is_file()
    assert model.db_path == path and model.output_root == folder
    assert model.state["store"] == {"path": str(path), "chosen": True}
    assert tm_module.TransferMap.choices.read("map_store") == str(path)
    # Recording works now.
    assert model.run("new_tip", {"tip_id": "tip-A"}).is_ok
    # A new session remembers it: no question.
    again = TransferMap()
    assert again.db_path == path and again.state["store"]["chosen"] is True


def test_a_new_store_never_overwrites_an_existing_file(no_store, tmp_path):
    folder = tmp_path / "d"
    folder.mkdir()
    (folder / "old.sqlite").write_text("keep me")
    result = TransferMap().run("new_store", {"store_dir": str(folder), "store_name": "old"})
    assert result.status == "refused" and "Open store" in result.reason
    assert (folder / "old.sqlite").read_text() == "keep me"


def test_an_existing_store_is_opened_and_remembered(no_store, tmp_path):
    path = tmp_path / "kept" / "trials.sqlite"
    tm_module.TrialStore(path).ensure()
    model = TransferMap()
    assert model.run("open_store", {"store_path": str(path)}).is_ok
    assert model.db_path == path
    assert TransferMap().db_path == path


@pytest.mark.parametrize("make, why", [
    (lambda p: None, "no file"),
    (lambda p: p.write_text("not a database"), "not a Transfer Map store"),
])
def test_opening_what_is_not_a_store_is_refused(no_store, tmp_path, make, why):
    path = tmp_path / "x.sqlite"
    make(path)
    result = TransferMap().run("open_store", {"store_path": str(path)})
    assert result.status == "refused" and why in result.reason
    assert tm_module.TransferMap.choices.read("map_store") is None


def test_a_store_inside_the_install_is_refused(no_store):
    """Updates replace the install folder: a store there would go with it."""
    inside = no_store / "data"
    tm_module.TrialStore(inside / "transfer_map.sqlite").ensure()
    model = TransferMap()
    reason = ("the store cannot live inside the station's own folder; updates "
              "replace that folder")
    opened = model.run("open_store", {"store_path": str(inside / "transfer_map.sqlite")})
    made = model.run("new_store", {"store_dir": str(no_store / "mine"), "store_name": "x"})
    for result in (opened, made):
        assert result.status == "refused" and reason in result.reason
    assert not (no_store / "mine").exists()
    assert tm_module.TransferMap.choices.read("map_store") is None


def test_a_store_left_in_the_install_is_offered_never_taken(no_store):
    """Migration by choice: the path field is pre-filled with the store a
    previous build left inside the install; nothing is chosen for the
    operator."""
    left = no_store / "data" / "transfer_map.sqlite"
    tm_module.TrialStore(left).ensure()
    model = TransferMap()
    assert model.store_path == str(left)
    assert model.state["store"]["chosen"] is False


def test_the_environment_overrides_the_remembered_choice(no_store, tmp_path, monkeypatch):
    TransferMap.choices.write("map_store", str(tmp_path / "remembered.sqlite"))
    override = tmp_path / "override.sqlite"
    monkeypatch.setenv("STATION_MAP_DB", str(override))
    model = TransferMap()
    assert model.db_path == override
    assert model.state["store"] == {"path": str(override), "chosen": True}
    assert "STATION_MAP_DB" in model.state["values"]["store_status"]


def test_a_chosen_store_is_refused_while_a_trial_is_armed(no_store, tmp_path):
    model = TransferMap(db_path=tmp_path / "a.sqlite")
    model.store_dir, model.store_name = str(tmp_path / "b"), "b"
    # 2026-10-07: armed from the region step on; the Store section is the
    # start screen's, so the Panel refuses it by the step, the model by
    # the trial.
    for armed in ("_trial", "_pending"):
        setattr(model, armed, tm_module._Trial(1, None, None, "t")
                if armed == "_trial" else tm_module._Pending("t", None, (1, 1), None))
        with pytest.raises(Refused, match="armed"):
            model.new_store()
        result = model.run("new_store", {"store_dir": str(tmp_path / "b"),
                                         "store_name": "b"})
        assert result.status == "refused" and "step" in result.reason
        setattr(model, armed, None)
    assert not (tmp_path / "b").exists()


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
    # 2026-10-07: the start screen's only (a step shows its own controls).
    assert first["phases"] == ["setup"]
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


def test_arm_needs_no_capture_region_and_starts_nothing(tmp_path, private_db):
    """2026-10-07 (owner ruling: the region is picked after Arm, on the
    stage still): Arm without a region is the `region` step, not a
    refusal; nothing runs and nothing is written until the region lands."""
    bare = RedMonitor(screen=fake_screen())
    bare.output_root = tmp_path / "runs"
    bare.open()
    try:
        model = TransferMap()
        model.on_model_added("Red Percent", bare)
        model.tip_id = "tip-A"
        assert model.arm_trial(True) is None
        assert model.phase == "region" and model.mode_name == "armed"
        assert not bare.is_running and not model.is_armed
        assert model.trial_count == 0
        model.abort_trial()
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
    assert red._frame_subscribers == ()
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "recorded" and row["note"] == "first cut"
    assert row["mark_operator_t"] is not None and row["z_contact"] == 1000.0
    assert row["red_baseline"] is not None
    assert row["mark_auto_max_t"] is not None
    assert row["red_min"] <= row["red_max"]
    # TM-4: no encoder in these tests, so no video (never a refusal); the
    # `wired` tests record one through the fake recorder.
    assert row["video_frames"] is None and row["video_path"] is None
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
    assert not model.is_active and model._on_sample not in red._subscribers
    model.disable()                          # joins the persist
    # TM-4: the telemetry's own row hook goes with its stop, on the writer.
    assert red._subscribers == ()
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
    # TM-3 (2026-10-07): a row without a tilt is imported (tilt collected,
    # never demanded); only a row without a speed would be skipped.
    assert model.import_csv(str(typed)) == {"imported": 3, "skipped": 0}
    rows = _rows(private_db, "SELECT * FROM trials ORDER BY id")
    assert [r["origin"] for r in rows] == ["imported"] * 3
    assert [r["status"] for r in rows] == ["measured", "recorded", "measured"]
    assert [r["tilt_deg"] for r in rows] == [10, 20, None]
    assert "given" in model.force_definition_options
    model.set_force_definition("given")
    trials = model._map_rows()
    assert [t["force"]["given"] for t in trials] == [0.2, 0.5, 0.7]


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
    with pytest.raises(Refused, match="speed_steps_s"):     # TM-3: speed only
        TransferMap().import_csv(str(bad))


def test_import_takes_a_file_with_no_tilt_column(tmp_path, private_db):
    """TM-3: the tilt is collected when a file has it, never demanded."""
    typed = tmp_path / "speeds.csv"
    typed.write_text("speed_steps_s,width_um\n100,2.0\n,3.0\n300,4.0\n")
    model = TransferMap()
    assert model.import_csv(str(typed)) == {"imported": 2, "skipped": 1}
    assert [r["tilt_deg"] for r in _rows(private_db, "SELECT tilt_deg FROM "
                                          "trials ORDER BY id")] == [None, None]


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
    # 2026-10-07: + the region step's still and the End recording step.
    for key in ("arm_trial", "mark_force", "finish_trial", "abort_trial",
                "figure", "tip_id", "tilt_now", "speed_now", "red_now",
                "trial_count", "trial_status", "db_path", "new_database",
                "mark_full_image", "video_status",
                "tip_status", "stage_still", "end_recording", "trial_figure"):
        assert tiers[key] == 1, key
    for key in ("set_figure_type", "set_force_definition", "attach_afm",
                "export_csv", "import_csv", "before_full_image",
                "export_tips_csv", "retire_tip", "unretire_tip",
                "set_tip_note", "tip_note"):
        assert tiers[key] == 2, key
    for key in ("trials_log", "tips_log", "delete_trial", "last_trial_numbers",
                "video_encoder"):
        assert tiers[key] == 3, key
    # TM-2: + the live plot ("Red % since Arm"). TM-4: + the region video's
    # labelled frames; the display at the Mark is back (its v3 column).
    for gone in ("before_image", "mark_image", "after_image",
                 "after_full_image", "live_series", "first_frame_image",
                 "mark_frame_image"):
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
    # 2026-10-07: the tip's entries are the setup step's "Start" section.
    trial = next(s for s in model.schema["sections"] if s["title"] == "Start")
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
    They should be unified". The region is set on the trial sheet; since
    2026-10-07 in the `region` step, after Arm, on the stage still."""
    model = TransferMap()
    model.open()
    try:
        assert model.region is None and model.state["values"]["region"] == ""
        assert model.state["has_region"] is False
        with pytest.raises(Refused, match="Open Red Percent"):
            model.set_region(1, 2, 30, 40)
        model.on_model_added("Red Percent", red)
        model.tip_id = "tip-A"
        assert model.region == red.region and model.state["has_region"] is True
        early = model.run("set_region", None, (1, 2, 30, 40))
        assert early.is_refused and "setup step" in early.reason
        with pytest.raises(Refused, match="Press Arm trial first"):
            model.set_region(1, 2, 30, 40)
        _arm_only(model)
        bad = model.run("set_region", None, (0, 0, 0, 5))   # red's own check
        assert bad.is_refused and model.phase == "region"
        result = model.run("set_region", None, (1, 2, 30, 40))
        assert result.is_ok, result
        assert red.region == {"top": 2, "left": 1, "width": 30, "height": 40}
        assert model.region == red.region
        assert model.state["values"]["region"] == sch.format_region(red.region)
        assert model.phase == "live" and result.value == model._trial.id
        model.abort_trial()
    finally:
        model.close()


def test_the_region_is_fixed_while_a_trial_is_armed(station):
    model, red, *_ = station
    _arm(model)
    before = dict(red.region)
    result = model.run("set_region", None, (5, 5, 20, 20))
    assert result.is_refused and "live step" in result.reason
    with pytest.raises(Refused, match="fixed"):
        model.set_region(5, 5, 20, 20)
    assert red.region == before


def test_the_region_picker_draws_on_the_stage_still(station):
    """Owner ruling 2026-10-07: the region is picked ON the still Arm
    took (the picker's picture is `stage_still`, no live screenshot)."""
    model, red, *_ = station
    element = _element(model, "set_region")
    assert element["type"] == "region_select"
    assert element["text"] == "Capture region"
    assert element["model_attr"] == "region"
    assert element["data_command"] == "stage_still"
    region_section = next(s for s in model.schema["sections"]
                          if element in s["elements"])
    assert region_section["phases"] == ["region"]
    assert model.stage_still == b""                    # nothing before Arm
    _arm_only(model)
    still = model.stage_still
    assert still[:8] == PNG and _size(still) == STAGE_SIZE
    assert model._pending.still.read_bytes() == still
    assert model.run("stage_still").value == still    # the picker's data
    trial = model.run("set_region", None, REGION).value
    kept = model.pictures_root / str(trial) / "before_full.png"
    assert kept.read_bytes() == still == model.stage_still
    _finish(model)
    assert model.stage_still == b""


def test_the_next_step_walks_the_operator_through_a_trial(red):
    model = TransferMap()
    model.open()
    step = lambda: model.state["values"]["next_step"]  # noqa: E731
    assert _element(model, "next_step")["role"] == "info"
    assert step() == "Open Red Percent"
    model.on_model_added("Red Percent", red)
    model.tip_id = " "
    assert step() == "Type a tip ID"
    model.tip_id = "tip-A"
    assert step() == "Press Arm trial"            # TM-3: no tilt demanded
    model.typed_tilt = "6.5"
    assert step() == "Press Arm trial"
    red.start_run(confirmed=True)                 # a run of the operator's own
    assert step() == "Stop Red Percent's run, then press Arm trial"
    red.end_run()
    assert step() == "Press Arm trial"
    _arm_only(model)
    assert step() == "Drag the capture region on the picture of the stage"
    assert model.run("set_region", None, REGION).is_ok
    assert step() == "Lower the tip; press Mark force when the force is right"
    assert model.run("mark_force").is_ok
    assert step() == "Press End recording when the cut is done"
    assert model.run("end_recording").is_ok
    assert step() == "Review the trial, then press Finish trial to keep it"
    _confirmed(model, "finish_trial", {"note": ""})
    assert step() == "Press Arm trial"
    model.estop()
    assert step() == ""                      # latched: the stop says what to do
    model.close()


# -- TM-2: no live plot; the trace is drawn once, for the review ---------------

def test_the_sheet_declares_no_plot_and_finish_still_writes_the_profile(
        station, private_db):
    """The live plots left the live view (CAP-5): neither the map nor Red
    Percent (drawn on its page) declares one. The profile is the
    measurement, not the plot's: Finish and Abort still write it."""
    model, red, *_ = station
    for panel in (model, red):
        plots = [e for e in sch.elements(panel.schema) if e["type"] == "plot"]
        assert plots == [], (panel.NAME, plots)
    assert model.run("live_series").is_refused     # no longer a data source
    trial = _record(model, red)
    samples = _rows(private_db, "SELECT COUNT(*) AS n FROM profile WHERE "
                    "trial_id=?", trial)[0]["n"]
    assert samples >= 25
    aborted = _arm(model)
    assert _wait_for(lambda: len(model._trial.samples) >= 5)
    assert model.run("abort_trial").is_ok
    model.disable()
    assert _rows(private_db, "SELECT COUNT(*) AS n FROM profile WHERE "
                 "trial_id=?", aborted)[0]["n"] >= 5


def test_the_review_shows_the_trial_just_recorded(station):
    """The finish step's figure: this trial's profile, from memory, before
    Finish writes it; nothing in any other step."""
    model, red, *_ = station
    assert model.trial_figure == b""
    _arm(model)
    assert _marked(model, samples=10).is_ok
    assert model.trial_figure == b""                 # recording: no plot
    assert model.run("trial_figure").is_refused      # not the marked step's
    assert model.run("end_recording").is_ok
    shown = model.run("trial_figure")
    assert shown.is_ok and shown.value[:8] == PNG
    assert model.trial_figure is shown.value         # drawn once
    _confirmed(model, "finish_trial", {"note": ""})
    assert model.trial_figure == b""


# -- TM-3: the tilt is collected, never demanded or drawn ----------------------

def test_a_trial_without_a_tilt_is_armed_recorded_and_mapped(idle_station,
                                                             private_db):
    """No rotator and no typed tilt: nothing asks for one, the trial records
    a NULL tilt (never a default 0), and it still reaches the map rows."""
    model, red = idle_station
    assert model.tilt_now is None
    assert model.next_step == "Press Arm trial"
    asked = model.run("arm_trial", {"tip_id": "tip-A", "typed_tilt": ""})
    assert asked.needs_confirm and "tilt" not in asked.reason.lower()
    trial = _arm(model)
    assert _wait_for(lambda: len(model._trial.samples) >= 5)
    _finish(model)
    row = _rows(private_db, "SELECT tilt_deg, tilt_source FROM trials "
                "WHERE id=?", trial)[0]
    assert row == {"tilt_deg": None, "tilt_source": None}
    assert [r["tilt"] for r in model._map_rows()] == [None]


def test_a_typed_tilt_is_still_collected(station, private_db):
    model, red, rotator, probe = station
    model.on_model_removed("Rotator")
    model.typed_tilt = "7.5"
    trial = _record(model, red)
    row = _rows(private_db, "SELECT tilt_deg, tilt_source FROM trials "
                "WHERE id=?", trial)[0]
    assert row == {"tilt_deg": 7.5, "tilt_source": "typed"}


def test_the_map_rows_carry_the_tilt_and_the_force_class(station, private_db):
    """`plot_data` draws speed by force class: the rows carry the bench's
    `force_class` (read with .get: this store's v6 has no such column) and
    the tilt, which nothing draws."""
    model, red, *_ = station
    trial = _record(model, red)
    [row] = model._map_rows()
    assert row["tilt"] == 22.5 and row["force_class"] is None
    with sqlite3.connect(private_db) as db:       # a bench file has the column
        db.execute("ALTER TABLE trials ADD COLUMN force_class TEXT")
        db.execute("UPDATE trials SET force_class='High' WHERE id=?", (trial,))
    [row] = model._map_rows()
    assert row["force_class"] == "High" and row["tilt"] == 22.5
    assert plot_data.force_class(row) == "High"


def test_the_width_gradient_is_over_speed_only(tmp_path, private_db):
    """TM-3: with no tilt axis the gradient is d(width)/d(speed); trials
    without a tilt count."""
    typed = tmp_path / "w.csv"
    typed.write_text("speed_steps_s,width_um\n100,1.0\n200,2.0\n300,3.0\n"
                     "400,4.0\n")
    model = TransferMap()
    assert model.width_gradient == ""
    model.import_csv(str(typed))
    words = model.width_gradient
    assert words.startswith("At the centre of the speeds: +")
    assert words.endswith("um per step/s") and "deg" not in words
    slope = float(words.split(": ")[1].split(" ")[0])
    assert 0.002 < slope < 0.02                   # ~0.01 um per step/s


# -- the procedure (owner ruling 2026-10-07) ---------------------------------
# setup -> Arm -> region (the stage still; nothing records) -> the region
# lands -> live -> Mark force -> marked -> End recording -> finish (review)
# -> Finish -> setup. Abort from region on; the stop overrides everything.

def _to_step(model, step):
    """Drive the procedure to `step` through the Panel, as a view does."""
    if step == "setup":
        return
    _arm_only(model)
    if step == "region":
        return
    assert model.run("set_region", None, REGION).is_ok
    if step == "live":
        return
    assert _marked(model).is_ok
    if step == "marked":
        return
    assert model.run("end_recording").is_ok


def _staged(model):
    staging = model.pictures_root / tm_module.STAGING
    return list(staging.iterdir()) if staging.exists() else []


def test_the_procedure_runs_through_its_steps_on_real_commands(station,
                                                               private_db):
    model, red, *_ = station
    assert TransferMap.PHASES == ("setup", "region", "live", "marked", "finish")
    seen = []

    def step():
        state = model.state
        assert state["phase"] == model.phase
        assert state["mode"] == state["model_mode"] == model.mode_name
        seen.append((model.phase, model.mode_name))

    step()
    assert model.run("arm_trial", {"tip_id": "tip-A"}, (True,)).is_ok
    step()
    assert model.trial_count == 0 and not red.is_running
    trial = model.run("set_region", None, REGION).value
    step()
    assert _wait_for(lambda: len(model._trial.samples) >= 5)
    assert model.run("mark_force").is_ok
    step()
    first_mark = model._trial.operator_t
    assert model.run("mark_force").is_ok              # again: the Mark moves
    step()
    assert model._trial.operator_t > first_mark
    assert model.run("end_recording").is_ok
    step()
    assert model.run("finish_trial", {"note": "walked"}, (True,)).is_ok
    step()
    assert seen == [("setup", "ready"), ("region", "armed"), ("live", "armed"),
                    ("marked", "armed"), ("marked", "armed"),
                    ("finish", "armed"), ("setup", "ready")]
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "recorded" and row["note"] == "walked"
    assert row["mark_operator_t"] == pytest.approx(model._store.trial(trial)
                                                   ["mark_operator_t"])
    assert _rows(private_db, "SELECT COUNT(*) AS n FROM profile WHERE "
                 "trial_id=?", trial)[0]["n"] >= 5


@pytest.mark.parametrize("step", TransferMap.PHASES)
def test_the_phase_and_the_next_step_agree_in_every_step(station, step):
    model, red, *_ = station
    _to_step(model, step)
    assert model.phase == model.state["phase"] == step
    words = model.state["values"]["next_step"]
    if step == "setup":
        assert words == "Press Arm trial" and model.mode_name == "ready"
    else:
        assert words == TransferMap.STEP_WORDS[step]
        assert model.mode_name == "armed"
    others = {w for s, w in TransferMap.STEP_WORDS.items() if s != step}
    assert words not in others
    model.estop()
    assert model.phase == "setup" and model.next_step == ""   # latched


#: Per step: commands whose controls the step does not show.
HIDDEN = {
    "setup": ("mark_force", "end_recording", "finish_trial", "set_region",
              "stage_still"),
    "region": ("arm_trial", "mark_force", "end_recording", "finish_trial",
               "new_database", "new_tip"),
    "live": ("arm_trial", "set_region", "end_recording", "finish_trial",
             "new_tip"),
    "marked": ("arm_trial", "set_region", "finish_trial", "new_tip"),
    "finish": ("arm_trial", "set_region", "mark_force", "end_recording",
               "new_tip"),
}


@pytest.mark.parametrize("step", TransferMap.PHASES)
def test_a_hidden_command_is_refused_in_the_wrong_step(station, step):
    """What the step does not show cannot be called through the Panel
    (the Web API included): refused, and the step does not move."""
    model, red, *_ = station
    _to_step(model, step)
    for command in HIDDEN[step]:
        result = model.run(command, {"tip_id": "tip-A"} if command == "new_tip"
                           else None, REGION if command == "set_region" else ())
        assert result.is_refused, (step, command)
        assert f"{step} step" in result.reason, (step, command, result.reason)
        assert model.phase == step
    if step != "setup":
        model.abort_trial()


@pytest.mark.parametrize("step", TransferMap.PHASES)
def test_abort_from_every_step(station, private_db, step):
    model, red, *_ = station
    _to_step(model, step)
    trial = model._trial.id if model._trial is not None else None
    result = model.run("abort_trial", {"width_um": "not a number"})
    if step == "setup":
        assert result.is_refused and "no trial is armed" in result.reason
        return
    assert result.is_ok, result
    assert model.phase == "setup" and model.mode_name == "ready"
    assert not red.is_running and red._subscribers == ()
    model.disable()
    if step == "region":
        assert result.value is None and model.trial_count == 0
        assert _staged(model) == []                # the still is deleted
    else:
        row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
        assert row["status"] == "aborted"


@pytest.mark.parametrize("step", TransferMap.PHASES)
def test_the_stop_from_every_step(station, private_db, step):
    model, red, *_ = station
    _to_step(model, step)
    trial = model._trial.id if model._trial is not None else None
    started = time.monotonic()
    assert model.estop() is True
    assert time.monotonic() - started < 0.5
    assert model.phase == "setup" and model.gate_mode == "latched"
    assert not red.is_running
    model.disable()
    if trial is None:
        assert model.trial_count == 0
        assert _wait_for(lambda: _staged(model) == [])
    else:
        row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
        assert row["status"] == "aborted"
    assert model.run("toggle_estop", None, (True,)).is_ok   # clear: setup again
    assert model.phase == "setup" and model.mode_name == "ready"


def test_the_region_step_writes_no_trial_row(station, private_db):
    model, red, *_ = station
    _arm_only(model)
    assert model.trial_count == 0
    assert _rows(private_db, "SELECT * FROM trials") == []
    numbered = [p for p in model.pictures_root.iterdir()
                if p.name != tm_module.STAGING]
    assert numbered == []                          # only the staged still
    assert len(_staged(model)) == 1
    assert not red.is_running and red._subscribers == ()
    assert not model.is_armed and model.is_active is False
    assert model.video_status == "No video yet."


def test_the_still_is_taken_through_the_recorder_on_its_display(
        station, fake_display):
    """The still comes through the recorder factory, for the map's display,
    and carries that display's place on the desktop, so the picker maps
    its drag to desktop coordinates: the region lands as it is sent."""
    model, red, *_ = station
    model.monitor = 0                       # the fake desktop: 1700 x 40
    _arm_only(model)
    folder, fps, monitor = fake_display[-1]
    assert folder == model.pictures_root / tm_module.STAGING
    assert (fps, monitor) == (tm_module.VIDEO_FPS, 0)
    still = model.run("stage_still").value
    assert set(still) == {"image", "left", "top", "width", "height"}
    assert still["image"][:8] == PNG and _size(still["image"]) == STAGE_SIZE
    assert (still["left"], still["top"], still["width"], still["height"]) == (
        0, 0, 1700, 40)
    assert model.run("set_region", None, (850, 20, 850, 20)).is_ok
    assert red.region == {"left": 850, "top": 20, "width": 850, "height": 20}
    assert model.stage_still["width"] == 1700         # the trial keeps them
    model.abort_trial()
    model.monitor = {"left": 100, "top": 50, "width": 48, "height": 32}
    _arm_only(model)                        # a display given as its bounds
    assert model.stage_still["left"] == 100
    assert model.run("set_region", None, (105, 60, 20, 15)).is_ok
    assert red.region == {"left": 105, "top": 60, "width": 20, "height": 15}
    model.abort_trial()


def test_without_the_displays_bounds_the_stills_pixels_are_the_desktops(
        station):
    model, red, *_ = station
    model.monitor = 7                       # no such display on this screen
    _arm_only(model)
    assert model._pending.bounds is None
    assert isinstance(model.stage_still, bytes)       # a plain PNG
    assert model.run("set_region", None, (3, 4, 20, 10)).is_ok
    assert red.region == {"left": 3, "top": 4, "width": 20, "height": 10}
    model.abort_trial()


def test_end_recording_stops_everything_and_keeps_the_trial_open(wired,
                                                                  private_db):
    model, red, log, made = wired
    trial = _arm(model)
    assert _marked(model).is_ok
    assert model.run("end_recording").is_ok
    kept = len(model._trial.samples)
    time.sleep(0.2)
    assert len(model._trial.samples) == kept       # no rows after the end
    assert not red.is_running and red._subscribers == ()
    names = [e[0] for e in log]
    assert "stop" in names and "telemetry.stop" in names
    assert model.phase == "finish"
    assert model.is_armed and _rows(private_db, "SELECT status FROM trials "
                                    "WHERE id=?", trial)[0]["status"] == "armed"
    with pytest.raises(Refused, match="already ended"):
        model.end_recording()
    with pytest.raises(Refused, match="nothing left to mark"):
        model.mark_force()
    _confirmed(model, "finish_trial", {"note": ""})
    assert _rows(private_db, "SELECT COUNT(*) AS n FROM profile WHERE "
                 "trial_id=?", trial)[0]["n"] == kept


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


def test_arm_refuses_while_red_percent_runs_a_run_of_its_own(station):
    """2026-10-07: the trial's run starts on the region picked after Arm,
    so a run the operator started on Red Percent (on another region, which
    Red Percent cannot change mid-run) is never joined: Arm says so and
    leaves that run running."""
    model, red, *_ = station
    red.start_run(confirmed=True)
    run = red.run_token
    result = model.run("arm_trial", {"tip_id": "tip-A"}, (True,))
    assert result.is_refused and "run of its own" in result.reason
    assert model.phase == "setup" and red.run_token is run
    red.end_run()
    _record(model, red)                      # once it is stopped, Arm works


def test_a_run_the_operator_starts_in_the_region_step_is_left_running(station,
                                                                    private_db):
    model, red, *_ = station
    _arm_only(model)
    red.start_run(confirmed=True)
    run = red.run_token
    result = model.run("set_region", None, REGION)
    assert result.is_refused and "run of its own" in result.reason
    assert model.phase == "region" and red.run_token is run
    assert model.trial_count == 0
    red.end_run()
    assert model.run("set_region", None, REGION).is_ok      # then it starts
    assert model.phase == "live"


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
    """The run starts when the region lands (2026-10-07): a Red Percent
    that refuses it leaves the trial in the region step, with no row."""
    model, red = idle_station
    red.estop()
    _arm_only(model)
    result = model.run("set_region", None, REGION)
    assert result.is_refused and "Red Percent" in result.reason
    assert not model.is_armed and model.trial_count == 0
    assert model.phase == "region"


# -- T3: the picture prompts -------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n"


def test_arm_asks_to_frame_the_sample_before_anything_is_written(idle_station):
    """Bench 2026-09-27: "No prompt for a before or after image"."""
    model, red = idle_station
    result = model.run("arm_trial", {"tip_id": "T7", "typed_tilt": ""})
    assert result.needs_confirm, result
    # 2026-10-07: Continue takes the stage still; the region comes next.
    # TM-3: without a tilt the prompt says nothing of one (never demanded).
    assert result.reason == ("Frame the sample now. Continue takes the "
                             "picture of the stage for trial 1 on tip T7, "
                             "300 steps/s (Stepper Probe); you then pick the "
                             "capture region on it, and the recording starts.")
    assert result.command == "arm_trial"
    assert result.inputs == {"tip_id": "T7", "typed_tilt": ""}
    assert not model.is_armed and model.trial_count == 0
    assert model.phase == "setup" and model.stage_still == b""
    assert not red.is_running                 # nothing started either
    again = model.run(result.command, result.inputs, (*result.args, True))
    assert again.is_ok and model.phase == "region"
    assert not model.is_armed and model.trial_count == 0   # still no row
    assert model.run("set_region", None, REGION).value == 1 and model.is_armed


def test_the_arm_prompt_names_the_number_the_trial_will_get(station):
    model, red, *_ = station
    _record(model, red)
    second = _record(model, red)
    model.trial_pick = second
    model.delete_trial(True)
    result = model.run("arm_trial", {"tip_id": "tip-A"})
    assert "for trial 3 on tip tip-A" in result.reason
    assert _arm(model, "tip-A") == 3


def test_arm_refuses_before_it_asks(station):
    model = station[0]
    result = model.run("arm_trial", {"tip_id": "  "})
    assert result.is_refused and "tip ID" in result.reason


def test_finish_asks_before_the_after_picture(station, private_db):
    model, red, *_ = station
    trial = _arm(model)
    assert model.run("mark_force").is_ok
    assert model.run("end_recording").is_ok          # the finish step
    result = model.run("finish_trial", {"note": "clean cut"})
    assert result.needs_confirm, result
    assert result.reason == f"Continue keeps trial {trial}."
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
    """The capture gate is the region step's since 2026-10-07: the region
    lands, its picture cannot be taken, the trial is not started."""
    model, red = idle_station
    monkeypatch.setattr(red, "grab_frame", lambda: None)
    since = events.latest_id
    _arm_only(model)
    result = model.run("set_region", None, REGION)
    assert result.is_refused
    assert result.reason == ("No picture of the capture region: the capture "
                             "region is not set or the screen is not open.")
    assert not model.is_armed and model.trial_count == 0
    assert model.phase == "region"
    assert not red.is_running                 # the run it started is ended
    assert not _titled("No Picture", since)   # a refusal, not a tray warning


def test_finish_needs_no_picture_of_its_own(
        station, monkeypatch, private_db):
    """V4: Finish takes no picture of its own any more, so a screen that
    cannot be captured at Finish no longer holds the trial armed."""
    model, red, *_ = station
    trial = _arm(model)
    monkeypatch.setattr(red, "grab_frame", lambda: None)
    monkeypatch.setattr(red, "grab_screen", lambda: None)
    model.end_recording()
    since = events.latest_id
    result = model.run("finish_trial", {"note": ""}, (True,))
    assert result.is_ok, result
    assert not model.is_armed
    assert not _titled("No Full Picture", since)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "recorded"
    assert row["after_path"] is None and row["after_full_path"] is None


def test_the_pictures_show_on_the_sheet(station):
    """TM-4: the review's two pictures are the whole display, at Arm (the
    stage still) and at the Mark; the armed trial's, else the last trial's
    (the Mark's), never an earlier trial's while one is armed."""
    model, red, *_ = station
    assert model.stage_still == b"" and model.mark_full_image == b""
    first = _arm(model)
    folder = model.pictures_root / str(first)
    assert model.stage_still == (folder / "before_full.png").read_bytes()
    assert model.mark_full_image == b""        # not marked yet
    assert _marked(model).is_ok
    assert _wait_for(lambda: model.mark_full_image != b"")
    assert model.mark_full_image == (folder / "mark_full.png").read_bytes()
    assert _size(model.mark_full_image) == STAGE_SIZE
    _finish(model)
    assert model.mark_full_image == (folder / "mark_full.png").read_bytes()
    second = _arm(model)
    assert model.mark_full_image == b""        # not the last trial's
    model.run("abort_trial")
    model.disable()
    model.new_database()
    assert model.stage_still == b"" and model.mark_full_image == b""
    for command in ("stage_still", "mark_full_image"):
        # Declared data sources of the region and review steps: the start
        # screen does not show them, so it refuses them (2026-10-07).
        early = model.run(command)
        assert early.is_refused and "setup step" in early.reason
    assert second == first + 1


def test_the_picture_elements_say_when_they_are_taken():
    model = TransferMap()
    stage, mark = _element(model, "stage_still"), _element(model, "mark_full_image")
    assert (mark["type"], mark["text"]) == ("image", "At Mark force")
    assert "Mark force" in mark["empty"]
    assert stage["type"] == "region_select"            # the picker draws on it
    review = next(e for e in sch.elements(model.schema)
                  if e["type"] == "image" and e["data_command"] == "stage_still")
    assert review["text"] == "Stage" and "Arm" in review["empty"]
    status = _element(model, "video_status")
    assert (status["type"], status["text"]) == ("readonly", "Video")


# -- T6: the order of the sheet ------------------------------------------------

def _keys(section):
    return [e.get("command") if e["type"] in ("button", "region_select")
            else e.get("model_attr") or e.get("command") or e.get("data_command")
            for e in section["elements"]]


def test_the_sheet_reads_in_the_order_a_trial_is_run():
    """2026-10-07 (owner ruling: each step shows only its controls): the one
    Trial section is split into the procedure's groups, in its order; what
    every step needs (Next step, the readouts, Status, Abort) is unphased."""
    model = TransferMap()
    sections = model.schema["sections"]
    tier_one = [(s["title"], s.get("phases")) for s in sections
                if s.get("tier", 1) == 1 and s["title"] != "Safety"]
    # A3: the Store section, where the operator chooses the trial store,
    # sits between the session and the trial it would refuse without one;
    # it is the start screen's (2026-10-07), like the session.
    assert tier_one == [("Session", ["setup"]), ("Store", ["setup"]),
                        ("Trial", None),
                        ("Start", ["setup"]), ("Capture region", ["region"]),
                        ("Recording", ["live", "marked"]),
                        ("Review", ["finish"]), ("This trial", None),
                        ("Map", ["setup"])]
    by_title = {s["title"]: s for s in sections}
    assert _keys(by_title["Trial"]) == ["next_step", "tilt_now", "speed_now"]
    assert _keys(by_title["Start"]) == [
        "tip_id", "tip_pick", "new_tip", "tip_trial_count", "tip_status",
        "typed_tilt", "typed_speed", "arm_trial"]
    assert _keys(by_title["Capture region"]) == ["set_region"]
    # TM-2: no live plot in the recording steps; the trace is the review's.
    # TM-4: no labelled region frames; the review shows the stage still and
    # the whole display at the Mark.
    assert _keys(by_title["Recording"]) == [
        "red_now", "mark_force", "end_recording", "video_status"]
    assert _keys(by_title["Review"]) == [
        "stage_still", "mark_full_image", "video_status", "trial_figure",
        "note", "finish_trial"]
    assert _keys(by_title["This trial"]) == ["trial_status", "is_broke",
                                             "abort_trial"]
    assert _keys(by_title["Map"]) == ["figure"]
    phased = {(e.get("command") or e.get("data_command") or e.get("model_attr")):
              e.get("phases") for s in sections for e in s["elements"]
              if e.get("phases")}
    assert phased == {"end_recording": ["marked"],
                      "mark_broke": ["marked", "finish"]}
    later = [(s["title"], s.get("tier")) for s in sections
             if s.get("tier", 1) != 1]
    assert later == [("Context", 2), ("Tip", 2), ("Figure", 2),
                     ("AFM measurement", 2), ("Optical measurement", 2),
                     ("Data", 2),
                     ("Diagnostics", 3), ("Safety", 3)]
    assert not any(s.get("phases") for s in sections if s.get("tier", 1) != 1)
    diagnostics = next(s for s in sections if s["title"] == "Diagnostics")
    assert [e.get("model_attr") or e.get("source_command") or e.get("command")
            for e in diagnostics["elements"]] == [
        "last_trial_numbers", "width_gradient", "video_encoder", "trials_log",
        "tips_log", "delete_trial"]


# -- full pictures: the whole screen at Arm and at Finish (2026-09-28) ----------
# "I need additional full image captures included." The region pictures stay
# the gate; the whole-screen pictures are the record, so a missing one warns.

def _size(png):
    import io
    from PIL import Image
    return Image.open(io.BytesIO(png)).size


def test_arm_and_finish_keep_whole_screen_pictures(station, private_db):
    """V4: the whole screen at Arm is the one still left (context); Finish
    takes none. Since 2026-10-07 it is the stage still the region was
    picked on, taken through the recorder's device."""
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
    assert png[:8] == PNG and _size(png) == STAGE_SIZE
    assert not (model.pictures_root / tm_module.STAGING).exists() or \
        not list((model.pictures_root / tm_module.STAGING).iterdir())


def test_a_missing_full_picture_warns_and_the_trial_is_recorded(
        station, private_db):
    """A stage still that cannot be kept (here: gone from the staging
    folder) is the old "No Full Picture" warning; the trial records."""
    model, red, *_ = station
    events.forget("No Full Picture")    # a new dedupe episode
    since = events.latest_id
    _arm_only(model)
    model._pending.still.unlink()
    trial = model.run("set_region", None, REGION).value
    assert model.is_armed
    warned = _titled("No Full Picture", since)
    assert len(warned) == 1 and warned[0].severity == "warning"
    assert f"Trial {trial}" in warned[0].message
    # A settled sample takes >= 5 ms (CAP-1): Finish waits for one.
    assert _wait_for(lambda: len(model._trial.samples) >= 1)
    _finish(model)
    assert len(_titled("No Full Picture", since)) == 1     # Finish takes none
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["status"] == "recorded"
    assert row["before_full_path"] is None
    folder = model.pictures_root / str(trial)
    assert "before_full.png" not in {p.name for p in folder.iterdir()}


class _Recorder:
    """A recorder factory's product for the still: `capture_still` runs
    `then` (a stop, a failure) around writing the fake display's PNG."""

    def __init__(self, then=None, fail=None):
        self.then, self.fail = then, fail

    def capture_still(self, path):
        if self.fail is not None:
            raise self.fail
        from devices.screen_recorder import ScreenRecorder
        ScreenRecorder(Path(path).parent, 15, 1,
                       frame_source=display_source).capture_still(path)
        if self.then is not None:
            self.then()
        return path


def test_a_still_that_cannot_be_taken_refuses_arm(station, private_db):
    """The region is picked on the still: without one there is nothing to
    pick on, so Arm is refused and nothing is left behind."""
    model, red, *_ = station
    model._recorder_factory = lambda *a: _Recorder(
        fail=RuntimeError("the screen could not be grabbed for a still"))
    result = model.run("arm_trial", {"tip_id": "tip-A"}, (True,))
    assert result.is_refused and "No picture of the stage" in result.reason
    assert "could not be grabbed" in result.reason
    assert model.phase == "setup" and model.trial_count == 0
    staging = model.pictures_root / tm_module.STAGING
    assert not staging.exists() or not list(staging.iterdir())


def test_a_red_percent_without_grab_screen_still_arms(tmp_path, private_db):
    """Duck typing: the map finds Red Percent by `subscribe` and
    `grab_frame`; its screen, `grab_screen` and the frame hook are
    optional (the stage still is the recorder's)."""
    class Plain:
        region = None
        is_running = False
        run_token = None
        def subscribe(self, fn): pass
        def unsubscribe(self, fn): pass
        def grab_frame(self): return PNG + b"region"
        def set_region(self, x, y, w, h):
            self.region = {"left": x, "top": y, "width": w, "height": h}
        def start_run(self, confirmed=False):
            self.is_running, self.run_token = True, object()
        def end_run(self):
            self.is_running, self.run_token = False, None

    model = TransferMap()
    model.open()
    plain = Plain()
    model.on_model_added("Red Percent", plain)
    model.tip_id = "t"
    events.forget("No Full Picture")    # a new dedupe episode
    since = events.latest_id
    trial = _arm(model)
    assert model.is_armed and not _titled("No Full Picture", since)
    assert plain.region == {"left": 0, "top": 0, "width": 10, "height": 10}
    model.run("abort_trial")
    model.close()
    assert not plain.is_running
    assert _rows(private_db, "SELECT before_full_path FROM trials WHERE id=?",
                 trial)[0]["before_full_path"]


def test_a_stop_during_the_arm_pictures_arms_nothing(idle_station, private_db):
    """The still widens the time between Arm's guard and the step; a stop
    inside it must win, and leave no still behind."""
    model, red = idle_station
    model._recorder_factory = lambda *a: _Recorder(then=model.estop)
    result = model.run("arm_trial", {"tip_id": "tip-A"}, (True,))
    assert result.is_refused and "stopped" in result.reason
    assert model.phase == "setup" and model.trial_count == 0
    assert not red.is_running
    staging = model.pictures_root / tm_module.STAGING
    assert not list(staging.iterdir())


def test_a_stop_while_the_region_lands_starts_nothing(idle_station, private_db,
                                                       monkeypatch):
    """The stop inside the region step's start (here: its capture gate)
    wins: no row, no run, nothing armed."""
    model, red = idle_station
    _arm_only(model)
    real = red.grab_frame

    def grab_then_stop():
        png = real()
        model.estop()
        return png

    monkeypatch.setattr(red, "grab_frame", grab_then_stop)
    result = model.run("set_region", None, REGION)
    assert result.is_refused and "stopped" in result.reason
    assert model.phase == "setup" and model.trial_count == 0
    assert not model.is_armed and not red.is_running


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
    assert _size(model.before_full_image) == STAGE_SIZE
    second = _arm(model)
    assert model.before_full_image == (model.pictures_root / str(second)
                                       / "before_full.png").read_bytes()
    model.run("abort_trial")
    model.disable()
    model.new_database()
    assert model.before_full_image == b""


def test_export_carries_the_full_picture_paths(wired):
    """The old picture columns stay in the table and the export (old rows
    carry them); a new trial leaves them blank and fills the video's, and
    (TM-4) the whole display at its Mark."""
    model, red, log, made = wired
    trial = _record(model, red)
    rows = list(csv.DictReader(Path(model.export_csv()).open()))
    folder = model.pictures_root / str(trial)
    assert rows[0]["before_full_path"] == str(folder / "before_full.png")
    for old in ("before_path", "after_path", "after_full_path", "mark_path"):
        assert old in rows[0] and rows[0][old] == "", old
    assert rows[0]["mark_full_path"] == str(folder / "mark_full.png")
    assert rows[0]["video_path"] == str(folder / "screen.mp4")
    assert rows[0]["video_index_path"] == str(folder / "frames.csv")
    assert (rows[0]["video_frames"], rows[0]["video_dropped"]) == ("42", "2")


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


def _version(path):
    with sqlite3.connect(path) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def _columns(path):
    with sqlite3.connect(path) as db:
        return [r[1] for r in db.execute("PRAGMA table_info(trials)")]


def _version_one_file(path, drop=V2_COLUMNS + V3_COLUMNS + V5_COLUMNS + V6_COLUMNS,
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
    assert tm_module.SCHEMA_VERSION == 6
    model = TransferMap()
    model.open()
    model.close()
    assert _version(private_db) == 6
    assert set(V2_COLUMNS + V3_COLUMNS + V5_COLUMNS + V6_COLUMNS) <= set(_columns(private_db))
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
        assert _version(private_db) == 6
        assert set(V2_COLUMNS + V3_COLUMNS + V5_COLUMNS + V6_COLUMNS) <= set(_columns(private_db))
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
    _wire(model)
    try:
        trial = _arm(model)
        assert trial == 2
        # A settled sample takes >= 5 ms (CAP-1): Finish waits for one.
        assert _wait_for(lambda: len(model._trial.samples) >= 1)
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
    assert _version(private_db) == 6
    assert [r["tip_id"] for r in store.trials()] == ["T7", "T8"]


def test_a_half_done_upgrade_finishes(private_db):
    """A version-1 file that already has some of the new columns (an upgrade
    cut short between the ALTERs) gets the rest and the current version."""
    _version_one_file(private_db, drop=("after_full_path",) + V3_COLUMNS
                      + V5_COLUMNS + V6_COLUMNS[3:])
    assert _version(private_db) == 1
    assert tm_module.TrialStore(private_db).ensure() is False
    assert _version(private_db) == 6
    assert set(V2_COLUMNS + V3_COLUMNS + V5_COLUMNS + V6_COLUMNS) <= set(_columns(private_db))


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
    assert _version(private_db) == 6
    assert not _titled("Database Upgraded", since)


def test_a_version_two_database_gains_the_mark_columns_and_its_tips(private_db):
    """M1/M2: the owner's bench file is version 2 and holds trials. It gains
    the Mark columns and a tip record per tip its trials name; every trial
    and profile row is kept."""
    _version_one_file(private_db, drop=V3_COLUMNS + V5_COLUMNS + V6_COLUMNS,
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
        assert _version(private_db) == 6
        assert set(V3_COLUMNS + V5_COLUMNS + V6_COLUMNS) <= set(_columns(private_db))
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
        assert (", ".join(V3_COLUMNS + V5_COLUMNS + V6_COLUMNS) + ", tips (version 6)"
                in upgraded[0].message), upgraded[0].message
        model.tip_id = "T7"
        assert model.tip_status == "in use since trial 1"
        # (the property: the sheet shows it in the review)
        assert model.mark_full_image == b""
    finally:
        model.close()


def test_a_migrated_version_two_database_records_a_marked_trial(red, private_db):
    _version_one_file(private_db, drop=V3_COLUMNS + V5_COLUMNS + V6_COLUMNS, version=2)
    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", red)
    model.tip_id = "T7"
    _wire(model)
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
    _version_one_file(private_db, drop=V3_COLUMNS + V5_COLUMNS + V6_COLUMNS, version=2)
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
    """TM-4: the Mark keeps the whole display as it is then (`mark_full.png`,
    its v3 column), through the recorder's device, on a worker; no region
    still (the region is in the display)."""
    model, red, *_ = station
    trial = _arm(model)
    folder = model.pictures_root / str(trial)
    assert _marked(model).is_ok
    assert _wait_for(lambda: (folder / "mark_full.png").is_file())
    _finish(model)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["mark_path"] is None
    assert row["mark_full_path"] == str(folder / "mark_full.png")
    names = {p.name for p in folder.iterdir()}
    assert "mark_full.png" in names
    assert not {"mark.png", "mark_full.part.png", "mark_frame.png"} & names


def test_the_mark_is_stamped_before_its_picture_is_taken(wired):
    """The Mark grabs nothing on its own thread: it stamps and returns; the
    display's still is taken on a worker, however slow."""
    model, red, log, made = wired
    _arm(model)
    made["recorder"].still_delay = 0.5           # a slow full-display grab
    grabs = []
    red.grab_frame = lambda: grabs.append(1)
    red.grab_screen = lambda: grabs.append(1)
    started = time.monotonic()
    marked = _marked(model, samples=0)
    assert time.monotonic() - started < 0.3
    assert marked.is_ok and grabs == []
    assert model._trial.operator_t is not None and model._trial.z_mark == 1000.0
    assert round(model._trial.operator_t, 3) == marked.value
    assert _wait_for(lambda: ("still", "mark_full.part.png") in log)
    model.abort_trial()


def test_the_mark_pictures_show_the_armed_trial_else_the_last(station):
    model, red, *_ = station
    first = _arm(model)
    assert _marked(model).is_ok
    folder = model.pictures_root / str(first)
    assert _wait_for(lambda: (folder / "mark_full.png").is_file())
    _finish(model)
    assert model.mark_full_image == (folder / "mark_full.png").read_bytes()
    # A declared data source of the review step.
    second = _arm(model)
    assert _marked(model).is_ok
    assert model.run("end_recording").is_ok
    assert model.run("mark_full_image").is_ok
    model.abort_trial()
    assert second == first + 1


def test_the_mark_picture_says_when_it_is_taken():
    model = TransferMap()
    mark = _element(model, "mark_full_image")
    assert (mark["type"], mark["text"]) == ("image", "At Mark force")
    assert mark["empty"] == "The whole display at Mark force."


def test_many_marks_share_one_picture_worker_and_close_ends_it(wired):
    """One still at a time, whatever the number of Marks: a Mark while one
    is being taken asks for one more, never a second worker."""
    model, red, log, made = wired
    _arm(model)
    made["recorder"].still_delay = 0.2
    for _ in range(3):
        assert _marked(model, samples=0).is_ok
    workers = [t for t in threading.enumerate()
               if t.name == "transfer-map-mark-still" and t.is_alive()]
    assert len(workers) == 1
    model.close()
    assert _wait_for(lambda: not workers[0].is_alive())
    stills = [e for e in log if e == ("still", "mark_full.part.png")]
    assert 1 <= len(stills) <= 2               # the first, and the one asked meanwhile


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
    # 2026-10-07: the tip's lines are the setup step's "Start" section.
    trial = next(s for s in model.schema["sections"] if s["title"] == "Start")
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
    # (2026-10-07: Continue takes the stage still; the region comes next)
    assert result.reason == (
        f"Tip T7 broke on trial {broke}. Arm on it anyway?\n\nFrame the sample "
        "now. Continue takes the picture of the stage for trial "
        f"{broke + 1} on tip T7 at 22.5 deg (Rotator), 300 steps/s (Stepper "
        "Probe); you then pick the capture region on it, and the recording "
        "starts.")
    again = model.run(result.command, result.inputs, (*result.args, True))
    assert again.is_ok and model.phase == "region"   # one Continue, not two
    model.abort_trial()


def test_arming_on_a_retired_tip_asks_once(station):
    model, red, *_ = station
    model.tip_id = "T7"
    _record(model, red)
    assert _confirmed(model, "retire_tip", {"tip_id": "T7"}) == "T7"
    result = model.run("arm_trial", {"tip_id": "T7"})
    assert result.needs_confirm
    assert result.reason.startswith("Tip T7 is retired. Arm on it anyway?\n\n"
                                    "Frame the sample now.")
    again = model.run(result.command, result.inputs, (*result.args, True))
    assert again.is_ok and model.phase == "region"
    model.abort_trial()


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
        ("button", "unretire_tip", ("tip_id",)),
        # 2026-10-07: the sheet's Tip broke is the marked and finish steps';
        # this one marks the last trial after the fact.
        ("toggle", "mark_broke", ())]


# -- M3 retired (2026-10-07): nothing polls before the trial ------------------
# M3 started Red Percent's run once a region and a tip were set. The region
# is now picked after Arm, on the stage still, and the trial's run starts
# when it lands, from a fresh baseline; a run before that would measure a
# stale region and spend the loop (CAP-5) for nothing on the sheet.

def _commit_tip(model, tip):
    result = model.set_value("tip_id", tip)          # what a view's entry sends
    assert result.is_ok, result
    return result


def test_nothing_polls_before_the_region_is_picked(idle_station):
    """A tip committed, picked or created, with Red Percent holding a
    region from before: no run starts until the trial's region lands."""
    model, red = idle_station
    controller = Controller()
    controller.add("Transfer Map", model, {})
    try:
        assert controller.set_value("Transfer Map", "tip_id", "T7").is_ok
        assert model.run("new_tip", {"tip_id": "T7"}).is_ok
        assert model.run("pick_tip", None, ("T7",)).is_ok
        model.typed_tilt = "5"
        assert not red.is_running and red._subscribers == ()
        assert model.state["values"]["next_step"] == "Press Arm trial"
        _arm_only(model)
        assert not red.is_running                      # nor in the region step
    finally:
        controller._models.pop("Transfer Map")         # the fixture closes it


def test_the_trials_run_starts_from_a_fresh_baseline_when_the_region_lands(
        idle_station):
    model, red = idle_station
    red.baseline_red = 99.0                        # a stale baseline
    _arm(model)
    assert red.is_running and model._trial.run is red.run_token
    assert _wait_for(lambda: red._run.frames >= 3)
    assert red._run.baseline_red is not None and red._run.baseline_red != 99.0
    assert red.baseline_red == red._run.baseline_red
    reds = set()
    assert _wait_for(lambda: reds.add(model.state["values"]["red_now"])
                     or len(reds) >= 2)            # the sheet's Red moves
    model.abort_trial()


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
    # 2026-10-07: the setup step's "Start" section holds the tilt entry.
    trial = [s for s in tm.schema["sections"] if s["title"] == "Start"][0]
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


# -- TM-4: the whole display and the telemetry, from the region to the end ----
# Owner ruling 2026-10-07: record everything possible, trim in analysis. One
# ScreenRecorder of the still's display and one TrialTelemetry over the
# map's peers per trial, through injected factories, started when the
# region lands; stopped by End recording, Finish, Abort or the stop (never
# on the stop's own thread). The region recorder of 2026-09-28 is retired.

def _mp4_frames(path):
    ffmpeg = pytest.importorskip("imageio_ffmpeg")
    return ffmpeg.count_frames_and_secs(str(path))[0]


def _row(private_db, trial):
    return _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]


def _telemetry_rows(model, trial):
    path = model.pictures_root / str(trial) / tm_module.TELEMETRY_NAME
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def test_the_recorder_and_the_telemetry_start_mark_and_stop_in_order(
        wired, private_db):
    model, red, log, made = wired
    trial = _arm(model)
    folder = model.pictures_root / str(trial)
    assert log[0][0] == "still" and log[0][1].startswith("stage_")  # Arm's
    assert [e for e in log if e[0] in ("start", "telemetry.start")] == [
        ("start", folder, tm_module.VIDEO_FPS, model.monitor),
        ("telemetry.start", trial)]
    assert _marked(model).is_ok
    assert model.run("end_recording").is_ok
    order = [e[0] for e in log if e[0] in ("start", "telemetry.start", "mark",
                                            "stop", "telemetry.stop")]
    assert order == ["start", "telemetry.start", "mark", "stop",
                     "telemetry.stop"]
    assert ("mark", "mark") in log
    row = _row(private_db, trial)            # kept at End recording
    assert row["status"] == "armed"
    assert (row["video_path"], row["video_index_path"], row["video_frames"],
            row["video_dropped"]) == (str(folder / "screen.mp4"),
                                      str(folder / "frames.csv"), 42, 2)
    _confirmed(model, "finish_trial", {"note": ""})
    names = [e[0] for e in log]
    assert names.count("stop") == 1 and names.count("telemetry.stop") == 1
    assert _row(private_db, trial)["status"] == "recorded"
    assert model.video_status == "screen.mp4, 42 frames, 2 dropped"


def test_the_telemetry_sidecar_is_written_beside_the_video(wired, private_db):
    """telemetry.csv (t, stream, value), on the monotonic clock of the
    video's frames.csv, with the map's own time zero and Mark, so the
    profile's t_s, the footage and the stage line up."""
    model, red, log, made = wired
    trial = _arm(model)
    assert _marked(model).is_ok
    _finish(model)
    rows = _telemetry_rows(model, trial)
    assert list(rows[0]) == list(tm_module.TELEMETRY_COLUMNS) == ["t", "stream", "value"]
    streams = {r["stream"] for r in rows}
    assert {"stepper_probe.z", "events.info", "transfer_map.armed",
            "transfer_map.mark"} <= streams
    times = [float(r["t"]) for r in rows]
    assert times == sorted(times)
    armed = next(r for r in rows if r["stream"] == "transfer_map.armed")
    mark = next(r for r in rows if r["stream"] == "transfer_map.mark")
    assert armed["value"] == str(trial)
    assert float(mark["t"]) - float(armed["t"]) == pytest.approx(
        float(mark["value"]), abs=1e-5)
    assert float(mark["value"]) == pytest.approx(
        _row(private_db, trial)["mark_operator_t"], abs=1e-5)


def test_the_telemetry_records_the_maps_peers(wired):
    model, red, log, made = wired
    _arm(model)
    peers = made["telemetry"].controller.models
    assert set(peers) == {"Red Percent", "Rotator", "Stepper Probe"}
    assert peers["Red Percent"] is red and model not in peers.values()
    model.on_model_removed("Rotator")
    assert "Rotator" not in made["telemetry"].controller.models
    model.abort_trial()


def test_a_recorder_that_cannot_start_is_an_event_and_the_trial_records(
        station, private_db):
    model, red, *_ = station
    log, made = _wire(model, recorder={"fail": RuntimeError("no ffmpeg here")})
    events.forget("No Video")
    since = events.latest_id
    trial = _arm(model)
    assert model.phase == "live"                     # never refused
    warned = _titled("No Video", since)
    assert len(warned) == 1 and warned[0].severity == "warning"
    assert "no ffmpeg here" in warned[0].message
    assert model.video_status == "no video: no ffmpeg here"
    assert _marked(model).is_ok                      # the still still comes
    _finish(model)
    row = _row(private_db, trial)
    assert row["status"] == "recorded"
    assert row["video_path"] is None and row["video_frames"] is None
    assert row["mark_full_path"]
    assert "stop" not in [e[0] for e in log]
    assert _telemetry_rows(model, trial)             # the telemetry still ran


def test_without_an_encoder_the_trial_records_without_a_video(station,
                                                              private_db):
    """The real ScreenRecorder with no imageio-ffmpeg (`jpeg_recorder`):
    its start raises, the trial records, Diagnostics says why."""
    model, red, *_ = station
    assert model.video_encoder == ("No encoder (imageio-ffmpeg is not "
                                   "installed): trials record without a video")
    events.forget("No Video")
    since = events.latest_id
    trial = _record(model, red)
    assert "imageio-ffmpeg is not installed" in _titled("No Video", since)[0].message
    row = _row(private_db, trial)
    assert row["status"] == "recorded" and row["video_frames"] is None
    assert model.video_status == "No video for this trial."


def test_a_telemetry_that_cannot_start_is_an_event_and_the_trial_records(
        station, private_db):
    model, red, *_ = station
    _wire(model, telemetry={"fail": RuntimeError("no clock")})
    events.forget("No Telemetry")
    since = events.latest_id
    trial = _record(model, red)
    assert len(_titled("No Telemetry", since)) == 1
    assert _row(private_db, trial)["status"] == "recorded"


@pytest.mark.parametrize("end", ["abort", "estop", "close"])
def test_abort_and_the_stop_stop_both_and_keep_what_they_left(wired, private_db,
                                                              end):
    model, red, log, made = wired
    trial = _arm(model)
    assert _wait_for(lambda: len(model._trial.samples) >= 3)
    if end == "abort":
        assert model.run("abort_trial").is_ok
    elif end == "estop":
        started = time.monotonic()
        assert model.estop() is True
        assert time.monotonic() - started < 0.5
        model.disable()                      # joins the abort writer
    else:
        model.close()
    names = [e[0] for e in log]
    assert names.count("stop") == 1 and names.count("telemetry.stop") == 1
    row = _row(private_db, trial)
    assert row["status"] == "aborted" and row["video_frames"] == 42
    assert _telemetry_rows(model, trial)


def test_the_stop_never_waits_on_the_recorder(station, private_db):
    """The stop returns at once; the recorder's and the telemetry's bounded
    stops run on the abort writer."""
    model, red, *_ = station
    log, made = _wire(model, recorder={"stop_delay": 0.6})
    trial = _arm(model)
    started = time.monotonic()
    assert model.estop() is True
    assert time.monotonic() - started < 0.3
    model.disable()
    assert [e[1] for e in log if e[0] == "stop"] == ["transfer-map-abort"]
    assert [e[1] for e in log if e[0] == "telemetry.stop"] == ["transfer-map-abort"]
    assert _row(private_db, trial)["video_frames"] == 42


def test_a_stop_while_the_recording_starts_still_stops_it(station, private_db):
    """A stop landing inside the recorder's start: its writer waits for the
    start to finish, then stops both; nothing is left recording."""
    model, red, *_ = station
    log, made = _wire(model)
    real_factory = model._recorder_factory

    def factory(out_dir, fps, monitor):
        recorder = real_factory(out_dir, fps, monitor)
        if Path(out_dir).name != tm_module.STAGING:
            real_start = recorder.start
            recorder.start = lambda: (real_start(), model.estop())
        return recorder

    model._recorder_factory = factory
    _arm_only(model)
    trial = model.run("set_region", None, REGION).value
    model.disable()
    names = [e[0] for e in log]
    assert names.count("stop") == 1 and names.count("telemetry.stop") == 1
    assert names.index("stop") > names.index("telemetry.start")
    assert _row(private_db, trial)["status"] == "aborted"


def test_a_recorder_that_fails_to_stop_warns_and_the_trial_records(
        station, private_db):
    model, red, *_ = station
    _wire(model, recorder={"stop_fail": OSError("disk full")})
    events.forget("Video Not Closed")
    since = events.latest_id
    trial = _record(model, red)
    assert len(_titled("Video Not Closed", since)) == 1
    row = _row(private_db, trial)
    assert row["status"] == "recorded" and row["video_frames"] is None
    assert len(_rows(private_db, "SELECT * FROM profile WHERE trial_id=?",
                     trial)) > 0          # the measurement is untouched


def test_the_video_status_says_what_is_being_recorded_then_what_was(wired):
    model, red, log, made = wired
    assert model.video_status == "No video yet."
    _arm(model)
    assert model.video_status == "recording, 12 frames, 1 dropped"
    assert _marked(model).is_ok
    assert model.run("end_recording").is_ok
    assert model.video_status == "screen.mp4, 42 frames, 2 dropped"
    _confirmed(model, "finish_trial", {"note": ""})
    assert model.video_status == "screen.mp4, 42 frames, 2 dropped"


def test_the_whole_display_is_recorded_end_to_end(station, private_db,
                                                 real_encoder):
    """The one map test on the real encoder (`real_encoder`): the real
    ScreenRecorder over the fake display writes a playable MP4 whose
    frames.csv has a row per frame and flags the Mark's frame."""
    model, red, *_ = station
    assert model.video_encoder.startswith("H.264 MP4 of the whole display via")
    trial = _arm(model)
    recorder = model._trial.recorder
    assert _wait_for(lambda: recorder.stats["frames"] >= 6, timeout=10.0)
    assert _marked(model, samples=0).is_ok
    assert _wait_for(lambda: recorder.stats["frames"] >= 12, timeout=10.0)
    _finish(model)
    row = _row(private_db, trial)
    folder = model.pictures_root / str(trial)
    assert row["video_path"] == str(folder / "screen.mp4")
    assert row["video_index_path"] == str(folder / "frames.csv")
    with open(folder / "frames.csv", newline="") as handle:
        index = list(csv.DictReader(handle))
    assert row["video_frames"] == len(index) >= 12
    assert _mp4_frames(row["video_path"]) == len(index)
    assert [r["marked"] for r in index if r["marked"]] == ["mark"]
    mark = next(r for r in _telemetry_rows(model, trial)
                if r["stream"] == "transfer_map.mark")
    flagged = next(r for r in index if r["marked"])
    assert float(flagged["t_monotonic"]) >= float(mark["t"]) - 1e-3


def test_the_video_encoder_is_in_diagnostics():
    """What Diagnostics says without the encoder (the MP4 wording is checked
    in the one `real_encoder` test)."""
    assert TransferMap().video_encoder.startswith("No encoder (")


def test_a_red_percent_without_the_frame_hook_still_gets_the_displays_video(
        tmp_path, private_db):
    """TM-4: the video is the display's, not Red Percent's frames: a Red
    Percent without `subscribe_frames` loses nothing."""
    class Plain:
        region = None
        is_running = False
        run_token = None
        def subscribe(self, fn): pass
        def unsubscribe(self, fn): pass
        def grab_frame(self): return PNG + b"region"
        def set_region(self, x, y, w, h):
            self.region = {"left": x, "top": y, "width": w, "height": h}
        def start_run(self, confirmed=False):
            self.is_running, self.run_token = True, object()
        def end_run(self):
            self.is_running, self.run_token = False, None

    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", Plain())
    model.tip_id = "t"
    _wire(model)
    trial = _arm(model)
    assert model.video_status == "recording, 12 frames, 1 dropped"
    _finish(model)
    model.close()
    row = _row(private_db, trial)
    assert row["status"] == "recorded" and row["video_frames"] == 42


def test_a_version_four_database_gains_the_video_columns_and_keeps_its_trial(
        red, private_db):
    """The owner's bench file after the tips round is version 4 and holds
    trials: it gains the four video columns, keeps every trial, profile row
    and tip, and records a trial with its video."""
    _version_one_file(private_db, drop=V5_COLUMNS + V6_COLUMNS, version=4)
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
    _wire(model)
    try:
        assert _version(private_db) == 6
        assert set(V5_COLUMNS + V6_COLUMNS) <= set(_columns(private_db))
        after = _rows(private_db, "SELECT * FROM trials")
        assert len(after) == 1
        assert {k: after[0][k] for k in before[0]} == before[0]    # untouched
        assert all(after[0][c] is None for c in V5_COLUMNS)
        assert len(_rows(private_db, "SELECT * FROM profile")) == 5
        assert _rows(private_db, "SELECT * FROM tips") == tips_before
        upgraded = _titled("Database Upgraded", since)
        assert len(upgraded) == 1
        assert ("now has " + ", ".join(V5_COLUMNS + V6_COLUMNS) + " (version 6)"
                in upgraded[0].message), \
            upgraded[0].message
        assert model.video_status == "No video for this trial."
        trial = _record(model, red)
        assert trial == 2
        row = _row(private_db, trial)
        assert row["video_frames"] == 42 and Path(row["video_path"]).exists()
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
                     red._frame_subscribers == ()))     # TM-4: no frame hook
        if fail is not None:
            raise fail
        return real(*args, **kwargs)

    monkeypatch.setattr(red, "start_run", spy)
    return seen


def test_an_arm_that_starts_the_run_keeps_its_first_row(idle_station, private_db):
    model, red = idle_station
    trial = _arm(model)
    assert _wait_for(lambda: len(model._trial.samples) >= 5)
    _finish(model)
    reds = [p["red"] for p in _rows(private_db, "SELECT red FROM profile "
                                    "WHERE trial_id=? ORDER BY rowid", trial)]
    logged = list(red._run.log.red_values)
    assert reds[0] == logged[0], "the run's first row is the trial's first sample"
    assert reds == logged[:len(reds)]
    times = [p["t_s"] for p in _rows(private_db, "SELECT t_s FROM profile "
                                     "WHERE trial_id=? ORDER BY rowid", trial)]
    assert times[0] >= 0


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
    _arm_only(model)
    assert seen == []                     # Arm alone starts nothing (2026-10-07)
    result = model.run("set_region", None, REGION)
    assert result.is_refused and seen == [(True, True)]
    assert red._subscribers == () and red._frame_subscribers == ()
    assert model._arming is None and not model.is_armed
    assert model.phase == "region"


def test_an_arm_that_fails_after_starting_the_run_lets_go(idle_station,
                                                          monkeypatch):
    model, red = idle_station
    monkeypatch.setattr(red, "grab_frame", lambda: None)
    _arm_only(model)
    assert model.run("set_region", None, REGION).is_refused
    assert not red.is_running
    assert red._subscribers == () and red._frame_subscribers == ()


# -- store version 6 (owner, 2026-10-04): trials name their flake; the cut
# descriptors (two AFM heights, an optical width); the store's identity ------

def test_a_fresh_database_is_version_six_with_an_identity(private_db):
    model = TransferMap()
    model.open()
    model.close()
    assert _version(private_db) == 6
    assert set(V6_COLUMNS) <= set(_columns(private_db))
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
    _version_one_file(private_db, drop=V6_COLUMNS, version=5)
    before = _rows(private_db, "SELECT * FROM trials")
    assert "meta" not in _tables(private_db)
    events.forget("Database Upgraded")
    since = events.latest_id
    model = TransferMap()
    model.open()
    model.on_model_added("Red Percent", red)
    model.tip_id = "T7"
    try:
        assert _version(private_db) == 6
        after = _rows(private_db, "SELECT * FROM trials")
        assert len(after) == 1
        assert {k: after[0][k] for k in before[0]} == before[0]    # untouched
        assert all(after[0][c] is None for c in V6_COLUMNS)
        assert len(_rows(private_db, "SELECT * FROM profile")) == 5
        assert "map_db_uuid" in tm_module.TrialStore(private_db).meta()
        upgraded = _titled("Database Upgraded", since)
        assert len(upgraded) == 1
        assert ("now has " + ", ".join(V6_COLUMNS) + " (version 6)"
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
    model.set_figure_type("Slice at a force band")
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
