"""The Temperature Controller's readings, kept WITH the trial (owner ruling
2026-10-08: "the heater log should be stored with our db info for trials").

While a trial is live (its row exists: `live`, `marked`, up to End
recording), every reading the heater publishes (`Heater.subscribe_readings`)
is appended to the trial in memory - one list append on the heater's reader
thread, nothing else - and written with the profile, in the store's one
write transaction, at Finish or Abort: the `trial_heater` table, keyed by
`trial_id`, `t_s` on the profile's clock (seconds since the trial's time
zero). A `heater.csv` lands beside the trial's other files, and the export
has a heater file. No heater open: nothing recorded, nothing said.

The fixtures are `test_transfer_map`'s (a temporary store, the real Red
Percent over a fake screen, the fake display).
"""
import csv
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from controller.controller import Controller
from events import events
from model import transfer_map as tm_module
from model.heater import Heater
from model.transfer_map import TransferMap

from test_heater_fakes import FakePort
from test_transfer_map import (  # noqa: F401  (fixtures)
    _arm, _arm_only, _bench_v8_file, _finish, _record, _rows, _titled,
    _version, _version_one_file, _wait_for, fake_display, jpeg_recorder,
    private_db, red, station, wired)

HEATER = "Temperature Controller"


@pytest.fixture
def heater():
    """The real Heater over a fake port: readings arrive through its own
    `_parse_line`, as the reader thread delivers them."""
    model = Heater(port=FakePort())
    yield model
    model._stop_threads()


@pytest.fixture
def with_heater(station, heater):
    model = station[0]
    model.on_model_added(HEATER, heater)
    return (*station, heater)


def _reading(heater, temperature, setpoint=25.0, timer=[0.0]):
    timer[0] += 0.6
    heater._parse_line(f"{timer[0]:.2f},{temperature:.2f},{setpoint:.2f}")


def _heater_rows(path, trial_id=None):
    sql = "SELECT * FROM trial_heater"
    args = ()
    if trial_id is not None:
        sql += " WHERE trial_id = ?"
        args = (trial_id,)
    return _rows(path, sql + " ORDER BY trial_id, t_s, rowid", *args)


def _tables(path):
    with sqlite3.connect(path) as db:
        return {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}


# -- recorded only while the trial is live, keyed to it ------------------------

def test_readings_are_kept_only_while_the_trial_is_live(with_heater, private_db):
    model, red, _rot, _probe, heater = with_heater
    _reading(heater, 20.0)                      # setup: nothing armed
    _arm_only(model)
    _reading(heater, 20.5)                      # region: no row, no trial
    trial = model.run("set_region", None, (0, 0, 10, 10)).value
    assert model.phase == "live"
    for temperature in (21.0, 21.5, 22.0):
        _reading(heater, temperature)
    model.mark_force()
    _reading(heater, 22.5)                      # marked: still recording
    model.end_recording()
    _reading(heater, 23.0)                      # finish: the recording ended
    _finish(model)
    _reading(heater, 23.5)                      # back to setup
    rows = _heater_rows(private_db)
    assert [r["temp_c"] for r in rows] == [21.0, 21.5, 22.0, 22.5]
    assert {r["trial_id"] for r in rows} == {trial}
    times = [r["t_s"] for r in rows]
    assert times == sorted(times) and times[0] >= 0
    assert all(r["setpoint_c"] == 25.0 for r in rows)
    assert all(r["source"] == HEATER for r in rows)


def test_each_trial_keeps_its_own_readings(with_heater, private_db):
    model, red, _rot, _probe, heater = with_heater
    first = _arm(model)
    _reading(heater, 30.0)
    _finish(model)
    second = _arm(model)
    _reading(heater, 40.0)
    _reading(heater, 41.0)
    _finish(model)
    assert [r["temp_c"] for r in _heater_rows(private_db, first)] == [30.0]
    assert [r["temp_c"] for r in _heater_rows(private_db, second)] == [40.0, 41.0]


def test_the_row_carries_the_settings_in_force(with_heater, private_db):
    model, red, _rot, _probe, heater = with_heater
    heater.setpoint, heater.ramp_rate = 60.0, 10.0
    heater.p_term, heater.i_term, heater.d_term = 2.0, 0.5, 0.1
    heater.apply_settings()
    trial = _arm(model)
    _reading(heater, 24.0, setpoint=26.0)
    _finish(model)
    row = _heater_rows(private_db, trial)[0]
    assert row["setpoint_c"] == 26.0                 # the board's ramped one
    assert row["endpoint_c"] == 60.0 and row["ramp_s_per_c"] == 10.0
    assert (row["kp"], row["ki"], row["kd"]) == (2.0, 0.5, 0.1)
    assert row["heater_on"] == 1
    assert row["board_t_s"] is not None and row["wall_epoch_s"] > 0


def test_an_aborted_trial_keeps_its_readings(with_heater, private_db):
    model, red, _rot, _probe, heater = with_heater
    trial = _arm(model)
    _reading(heater, 50.0)
    started = time.monotonic()
    assert model.estop() is True
    assert time.monotonic() - started < 0.5
    _reading(heater, 51.0)                        # after the stop: not kept
    model.disable()                               # joins the abort writer
    assert [r["temp_c"] for r in _heater_rows(private_db, trial)] == [50.0]


def test_the_readings_survive_a_reload(with_heater, private_db):
    model, red, _rot, _probe, heater = with_heater
    trial = _arm(model)
    _reading(heater, 33.0)
    _reading(heater, 34.0)
    _finish(model)
    again = TransferMap()
    stored = again._store.heater(trial)
    assert [r["temp_c"] for r in stored] == [33.0, 34.0]
    assert again._store.heater(trial + 99) == []


def test_delete_removes_the_trials_readings(with_heater, private_db):
    model, red, _rot, _probe, heater = with_heater
    trial = _arm(model)
    _reading(heater, 33.0)
    _finish(model)
    model.trial_pick = trial
    model.delete_trial(True)
    assert _heater_rows(private_db, trial) == []


# -- the files ------------------------------------------------------------------

def test_a_heater_csv_lands_beside_the_trials_other_files(with_heater, private_db):
    model, red, _rot, _probe, heater = with_heater
    trial = _arm(model)
    _reading(heater, 27.0)
    _reading(heater, 27.5)
    _finish(model)
    path = model.pictures_root / str(trial) / tm_module.HEATER_NAME
    with open(path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [float(r["temp_c"]) for r in rows] == [27.0, 27.5]
    assert tuple(rows[0]) == tm_module.HEATER_COLUMNS


def test_the_export_has_a_heater_file(with_heater):
    model, red, _rot, _probe, heater = with_heater
    trial = _arm(model)
    _reading(heater, 28.0)
    _finish(model)
    path = Path(model.export_heater_csv())
    assert path.is_file() and path.parent == model.output_root / "exports"
    rows = list(csv.DictReader(path.open()))
    assert [(r["trial_id"], float(r["temp_c"])) for r in rows] == [(str(trial), 28.0)]
    # The other exports are where they were.
    assert Path(model.export_csv()).name.endswith("_trials.csv")
    assert Path(model.export_profile_csv()).name.endswith("_profile.csv")


def test_the_export_button_is_beside_the_others(station):
    model = station[0]
    from schema import elements
    saves = [e["command"] for e in elements(model.schema)
             if e.get("type") == "file_save"]
    assert "export_heater_csv" in saves


# -- no heater ------------------------------------------------------------------

def test_with_no_heater_nothing_is_recorded_and_nothing_complains(station, private_db):
    model, red, *_ = station
    since = events.latest_id
    trial = _record(model, red)
    assert _heater_rows(private_db) == []
    assert not (model.pictures_root / str(trial) / tm_module.HEATER_NAME).exists()
    said = [e for e in events.since(since)
            if e.severity in ("warning", "error") or "heater" in e.text.lower()]
    assert said == [], [e.text for e in said]


def test_a_removed_heater_is_let_go(with_heater):
    model, red, _rot, _probe, heater = with_heater
    assert heater._reading_subscribers
    model.on_model_removed(HEATER, heater)
    assert heater._reading_subscribers == ()


def test_the_controller_wires_the_heater_to_the_map(station, heater):
    model = station[0]
    controller = Controller()
    controller.add("Transfer Map", model)
    controller.add(HEATER, heater)
    try:
        assert heater._reading_subscribers
    finally:
        for name in ("Transfer Map", HEATER):
            controller._models.pop(name)          # the fixtures close them


# -- never blocking ---------------------------------------------------------------

def test_a_reading_never_waits_for_the_store(with_heater):
    """The hook is one append: a store write in progress (its lock held, as
    Finish holds it) never holds the heater's reader."""
    model, red, _rot, _probe, heater = with_heater
    _arm(model)
    with model._store._lock:
        done = threading.Event()
        threading.Thread(target=lambda: (_reading(heater, 22.0), done.set()),
                         daemon=True).start()
        assert done.wait(0.5), "the heater's reader waited on the store"
    assert len(model._trial.heater) == 1


def test_a_very_long_trial_counts_what_it_cannot_keep(with_heater, monkeypatch):
    model, red, _rot, _probe, heater = with_heater
    monkeypatch.setattr(tm_module, "MAX_HEATER_ROWS", 2)
    _arm(model)
    for t in (20.0, 21.0, 22.0, 23.0):
        _reading(heater, t)
    assert len(model._trial.heater) == 2 and model._trial.heater_dropped == 2


# -- old stores ---------------------------------------------------------------------

def test_a_version_one_store_gains_the_heater_table_and_keeps_its_trial(
        private_db):
    _version_one_file(private_db)
    model = TransferMap()
    model.open()
    try:
        assert "trial_heater" in _tables(private_db)
        assert len(_rows(private_db, "SELECT * FROM trials")) == 1
        assert len(_rows(private_db, "SELECT * FROM profile")) == 5
        assert model._store.heater(1) == []
    finally:
        model.close()


def test_a_bench_v8_store_gains_the_table_keeps_its_version_and_records(
        private_db, red, heater):
    _bench_v8_file(private_db)
    assert model_reads_without_the_table(private_db) == []
    model = TransferMap()
    model.open()
    try:
        assert "trial_heater" in _tables(private_db)
        assert _version(private_db) == tm_module.SCHEMA_VERSION
        assert len(_rows(private_db, "SELECT * FROM trials")) == 1
    finally:
        model.close()


def model_reads_without_the_table(path):
    """A read of a store that has no heater table answers empty and
    creates nothing (a read never migrates)."""
    rows = tm_module.TrialStore(path).heater(1)
    assert "trial_heater" not in _tables(path)
    return rows
