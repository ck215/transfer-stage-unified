"""`controller.device_log`: the station's local, verbose device log (owner
ruling 2026-10-08: "a separate db maintains a verbose log of such
information across ALL devices which is stored locally ... tied into the
existing events system").

One SQLite file under the data root's `logs/` (never on the cloud drive),
fed by every event the event log publishes and by a compact snapshot of
every open model's state once a second, written by ONE background thread
from a bounded queue. Publishers never wait: a full queue drops its oldest
entry and counts it; a writer stuck in the disk never holds an event, a
model or FULL STOP. Retention: 14 days or 200 MB, whichever comes first,
pruned at start and daily.
"""
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from controller import device_log as dl
from controller.controller import Controller
from events import EventLog, events
from model.heater import Heater

from test_heater_fakes import FakePort


class FakeModel:
    """What the device log reads of a model: `state` and `is_energized`."""

    def __init__(self, name="Stepper Probe", **values):
        self.NAME = name
        self.values = dict(values)
        self.mode = "idle"
        self.energized = False
        self.reads = 0
        self.block = None

    @property
    def state(self):
        self.reads += 1
        if self.block is not None:
            self.block.wait(10)
        return {"name": self.NAME, "mode": self.mode, "model_mode": self.mode,
                "phase": None, "values": dict(self.values),
                "is_estopped": False, "is_faulted": False, "fault": "",
                "is_active": False, "age": 0.1,
                "devices": {"SerialPort": "verified"}}

    @property
    def is_energized(self):
        return self.energized


class FakeController:
    def __init__(self, **models):
        self._models = dict(models)

    @property
    def models(self):
        return dict(self._models)


@pytest.fixture
def path(tmp_path):
    return tmp_path / "logs" / "device_log.sqlite"


@pytest.fixture
def log_of(path):
    """Build a DeviceLog on a private EventLog; every one is closed."""
    made = []

    def build(controller=None, **kwargs):
        kwargs.setdefault("event_log", EventLog())
        kwargs.setdefault("sample_hz", 0)          # tests sample by hand
        log = dl.DeviceLog(controller or FakeController(), path, **kwargs)
        made.append(log)
        return log
    yield build
    for log in made:
        log.close()


def _rows(path, sql, *args):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, args)]


# -- where it lives -------------------------------------------------------------

def test_the_default_file_is_under_the_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path / "root"))
    assert dl.default_path() == tmp_path / "root" / "logs" / "device_log.sqlite"


def test_without_a_data_root_it_is_under_the_home_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("TRANSFER_STAGE_DATA_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert dl.default_path() == (Path.home() / "transfer-stage-runs" / "logs"
                                 / "device_log.sqlite")


def test_never_on_the_cloud_drive(log_of, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    drive = tmp_path / "QMDL_Drive" / "logs" / "device_log.sqlite"
    seen = EventLog()
    said = []
    seen.subscribe(said.append)
    log = dl.DeviceLog(FakeController(), drive, event_log=seen, sample_hz=0)
    assert log.start() is False
    assert not drive.exists() and not drive.parent.exists()
    assert [e.title for e in said] == ["Device Log Off"]
    log.close()


def test_nothing_is_created_until_it_starts(log_of, path):
    log_of()
    assert not path.exists()


# -- events -------------------------------------------------------------------------

def test_every_published_event_is_recorded(log_of, path):
    bus = EventLog()
    log = log_of(event_log=bus)
    assert log.start()
    bus.info("Port Opened", "COM3 at 115200", source="Stepper Probe")
    bus.warn("Temperature Read Error", "retrying", source="Temperature Controller")
    try:
        raise OSError("the cable")
    except OSError as exc:
        bus.error("Write Failed", "lost", source="Chuck", exception=exc, ack=False)
    assert log.flush(2.0)
    rows = _rows(path, "SELECT * FROM events WHERE source != ? ORDER BY id",
                 dl.SOURCE)
    assert [(r["severity"], r["source"], r["title"]) for r in rows] == [
        ("info", "Stepper Probe", "Port Opened"),
        ("warning", "Temperature Controller", "Temperature Read Error"),
        ("error", "Chuck", "Write Failed")]
    assert rows[0]["message"] == "COM3 at 115200"
    assert rows[0]["text"] == "[Stepper Probe] Port Opened: COM3 at 115200"
    assert "OSError('the cable')" in rows[2]["exception"]
    assert all(r["t"] > 0 and r["iso"] for r in rows)


def test_it_subscribes_to_the_stations_one_event_log_by_default(path):
    log = dl.DeviceLog(FakeController(), path, sample_hz=0)
    try:
        assert log.start()
        events.info("Device Log Probe", "hello", source="test")
        assert log.flush(2.0)
        assert _rows(path, "SELECT title FROM events WHERE title = ?",
                     "Device Log Probe")
    finally:
        log.close()
    events.info("Device Log Probe", "after close", source="test2")
    assert not _rows(path, "SELECT * FROM events WHERE source = 'test2'")


# -- model snapshots --------------------------------------------------------------

def test_a_snapshot_records_each_models_state(log_of, path):
    probe = FakeModel("Stepper Probe", position="(1, 2, 3)", speed="300")
    log = log_of(FakeController(**{"Stepper Probe": probe}))
    assert log.start()
    log.sample_once()
    assert log.flush(2.0)
    got = {r["key"]: r for r in _rows(
        path, "SELECT * FROM readings WHERE model = 'Stepper Probe'")}
    assert got["mode"]["value"] == "idle"
    assert got["values.position"]["value"] == "(1, 2, 3)"
    assert got["values.speed"]["num"] == 300.0
    assert got["is_energized"]["num"] == 0.0
    assert got["device.SerialPort"]["value"] == "verified"
    assert "age" not in got                # changes every poll; never logged


def test_only_what_changed_is_written_until_the_keyframe(log_of, path):
    probe = FakeModel(position="(1, 2, 3)")
    clock = [1000.0]
    log = log_of(FakeController(**{"Stepper Probe": probe}),
                 clock=lambda: clock[0])
    assert log.start()
    log.sample_once()
    first = log.flush(2.0) and len(_rows(path, "SELECT * FROM readings"))
    clock[0] += 1
    log.sample_once()                      # nothing changed
    assert log.flush(2.0)
    assert len(_rows(path, "SELECT * FROM readings")) == first
    clock[0] += 1
    probe.values["position"] = "(1, 2, 4)"
    probe.energized = True
    log.sample_once()
    assert log.flush(2.0)
    new = _rows(path, "SELECT key, value FROM readings WHERE t = ?", clock[0])
    assert sorted(r["key"] for r in new) == ["is_energized", "values.position"]
    clock[0] += dl.KEYFRAME_S
    log.sample_once()                      # the keyframe: every key again
    assert log.flush(2.0)
    assert len(_rows(path, "SELECT * FROM readings WHERE t = ?", clock[0])) == first


def test_a_model_that_raises_is_skipped_not_fatal(log_of, path):
    class Broken(FakeModel):
        @property
        def state(self):
            raise RuntimeError("a property's bug")

    good = FakeModel("Chuck", x="1")
    log = log_of(FakeController(Broken=Broken("Broken"), Chuck=good))
    assert log.start()
    log.sample_once()
    assert log.flush(2.0)
    assert _rows(path, "SELECT * FROM readings WHERE model = 'Chuck'")
    assert log.failures == 1


def test_a_heaters_reading_is_logged_by_name(log_of, path):
    heater = Heater(port=FakePort())
    try:
        heater._parse_line("1.0,24.50,25.00")
        log = log_of(FakeController(**{"Temperature Controller": heater}))
        assert log.start()
        log.sample_once()
        assert log.flush(2.0)
        got = {r["key"]: r["num"] for r in _rows(
            path, "SELECT key, num FROM readings WHERE model = ?",
            "Temperature Controller")}
        assert got["reading.temperature_c"] == 24.5
        assert got["reading.setpoint_c"] == 25.0
        # The clocks change every reading: never logged as readings.
        assert "reading.wall_epoch_s" not in got
        assert "reading.monotonic_s" not in got
    finally:
        heater._stop_threads()


def test_the_sampler_runs_by_itself_at_its_rate(log_of, path):
    probe = FakeModel()
    log = log_of(FakeController(**{"Stepper Probe": probe}), sample_hz=50)
    assert log.start()
    deadline = time.monotonic() + 2.0
    while probe.reads < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert probe.reads >= 3


def test_a_model_opened_and_closed_is_marked(log_of, path):
    controller = FakeController(**{"Stepper Probe": FakeModel()})
    log = log_of(controller)
    assert log.start()
    log.sample_once()
    controller._models.clear()
    log.sample_once()
    assert log.flush(2.0)
    opened = [r["num"] for r in _rows(
        path, "SELECT num FROM readings WHERE model = 'Stepper Probe' AND "
        "key = 'open' ORDER BY t, rowid")]
    assert opened == [1.0, 0.0]


# -- never blocking ------------------------------------------------------------------

@pytest.fixture
def stuck(monkeypatch):
    """The writer's disk write blocks until released."""
    gate = threading.Event()
    entered = threading.Event()
    real = dl.DeviceLog._write_batch

    def blocking(self, db, batch):
        entered.set()
        gate.wait(20)
        return real(self, db, batch)

    monkeypatch.setattr(dl.DeviceLog, "_write_batch", blocking)
    yield gate, entered
    gate.set()


def test_a_stuck_writer_never_holds_a_publisher(log_of, stuck):
    gate, entered = stuck
    bus = EventLog()
    log = log_of(event_log=bus, queue_max=50)
    assert log.start()
    bus.info("First", "goes to the writer", source="x")
    assert entered.wait(2.0)
    started = time.monotonic()
    for i in range(500):
        bus.info("Flood", f"event {i}", source="x")
    assert time.monotonic() - started < 1.0
    assert log.dropped >= 400
    assert log.queued <= 50


def test_a_stuck_writer_never_holds_full_stop(stuck, path):
    gate, entered = stuck
    controller = Controller()
    model = Heater(port=FakePort())
    controller.add("Temperature Controller", model)
    log = dl.DeviceLog(controller, path, sample_hz=0, queue_max=10)
    try:
        assert log.start()
        events.info("Wedge", "the writer takes this and sticks", source="x")
        assert entered.wait(2.0)
        started = time.monotonic()
        results = controller.estop_all()
        assert time.monotonic() - started < controller.ESTOP_ALL_BUDGET + 0.5
        assert results == {"Temperature Controller": True}
    finally:
        started = time.monotonic()
        log.close(timeout=0.3)
        assert time.monotonic() - started < 1.0       # bounded, stuck or not
        controller._models.pop("Temperature Controller", None)
        model._stop_threads()


def test_overflow_is_written_down_when_the_writer_catches_up(log_of, stuck, path):
    gate, entered = stuck
    bus = EventLog()
    log = log_of(event_log=bus, queue_max=5)
    assert log.start()
    bus.info("First", "x", source="x")
    assert entered.wait(2.0)
    for i in range(20):
        bus.info("Flood", f"{i}", source="x")
    gate.set()
    assert log.flush(2.0)
    lost = _rows(path, "SELECT * FROM events WHERE title = 'Device Log Overflow'")
    assert lost and "15" in lost[0]["message"]


def test_close_is_bounded_and_idempotent(log_of):
    log = log_of()
    assert log.start()
    started = time.monotonic()
    assert log.close() is True
    assert log.close() is True
    assert time.monotonic() - started < dl.CLOSE_BUDGET_S


def test_close_writes_what_is_queued(log_of, path):
    bus = EventLog()
    log = log_of(event_log=bus)
    assert log.start()
    for i in range(100):
        bus.info("Burst", f"{i}", source="x")
    assert log.close()
    assert len(_rows(path, "SELECT * FROM events WHERE title = 'Burst'")) == 100


# -- retention -------------------------------------------------------------------------

def _old_rows(path, now, ages_days):
    db = sqlite3.connect(path)
    for age in ages_days:
        t = now - age * 86400
        db.execute("INSERT INTO events (t, iso, severity, source, title, "
                   "message, text) VALUES (?, '', 'info', 'old', 'Old', ?, '')",
                   (t, str(age)))
        db.execute("INSERT INTO readings (t, model, key, value, num) VALUES "
                   "(?, 'old', 'k', ?, ?)", (t, str(age), age))
    db.commit()
    db.close()


def test_rows_older_than_fourteen_days_are_pruned_at_start(log_of, path):
    now = time.time()
    log = log_of()
    assert log.start() and log.close()
    _old_rows(path, now, [20, 15, 13, 1])
    log = log_of()
    assert log.start() and log.flush(2.0)
    kept = sorted(r["num"] for r in _rows(
        path, "SELECT num FROM readings WHERE model = 'old'"))
    assert kept == [1, 13]
    assert sorted(r["message"] for r in _rows(
        path, "SELECT message FROM events WHERE source = 'old'")) == ["1", "13"]


def test_the_file_is_pruned_to_its_size_cap_oldest_first(log_of, path):
    now = time.time()
    log = log_of()
    assert log.start() and log.close()
    db = sqlite3.connect(path)
    filler = "x" * 900
    db.executemany("INSERT INTO readings (t, model, key, value, num) VALUES "
                   "(?, 'bulk', 'k', ?, ?)",
                   [(now - 3600 + i, filler, i) for i in range(3000)])
    db.commit()
    db.close()
    assert path.stat().st_size > 2_000_000
    log = log_of(max_bytes=1_000_000)
    assert log.start() and log.flush(2.0)
    assert log.close()
    assert dl.logical_size(path) <= 1_000_000
    kept = [r["num"] for r in _rows(
        path, "SELECT num FROM readings WHERE model = 'bulk' ORDER BY t")]
    assert kept and kept[-1] == 2999 and kept[0] > 0     # the newest survive


def test_it_prunes_again_once_a_day(log_of, path):
    clock = [time.time()]
    log = log_of(clock=lambda: clock[0])
    assert log.start() and log.flush(2.0)
    assert log.prunes == 1
    clock[0] += dl.PRUNE_EVERY_S + 1
    log._wake()
    deadline = time.monotonic() + 2.0
    while log.prunes < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert log.prunes == 2


def test_the_retention_defaults_are_the_owners():
    assert dl.MAX_AGE_S == 14 * 86400
    assert dl.MAX_BYTES == 200 * 1024 * 1024
    assert dl.SAMPLE_HZ == 1.0
