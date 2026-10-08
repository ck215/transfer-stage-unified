"""The Transfer Map's tip-shade force (owner 2026-10-06): the live status
field, the columns a finished trial stores, the video index's shade column,
and the map's default definition.

Merged into the 2026-10-07 round: the region recorder and its picture
thread are retired, so the shade is fed by the trial's shade worker from
the analysis's settled region frames (`TransferMap._on_frame` ->
`_shade_frame`), the live status is the sheet's "Force estimate" (the shade
drives it once it reads; the red extrema are its fallback), and the per-frame
shade is in `telemetry.csv` (`transfer_map.shade`) instead of the region
video's index."""
import csv
import sqlite3

import numpy as np
import pytest

from model import transfer_map_analysis as an
from model import tip_shade as ts
from model.transfer_map import TransferMap
from test_transfer_map import (_arm, _finish, _rows, station, red, private_db,  # noqa: F401
                               jpeg_recorder, fake_display, FakeProbe,
                               FakeRotator, _wait_for)


@pytest.fixture(autouse=True)
def settled_analysis(monkeypatch):
    """The fake screen's settle-gate counters are not what these tests are
    about: the analysis reads settled, so the readout is the shade's."""
    monkeypatch.setattr(TransferMap, "analysis_health",
                        property(lambda self: "settled"))


def frame_with_green(value, size=(8, 12)):
    rgb = np.zeros((*size, 3), np.uint8)
    rgb[..., 0], rgb[..., 1] = 200, value
    return rgb


def arm_alone(model, red):
    """Arm, then stop the analysis's own frames reaching the trial (its
    worker drains what it has and leaves, and the tracker starts fresh), so
    a test writes the only frames it sees."""
    trial = _arm(model)
    red.unsubscribe_frames(model._on_frame)
    red.unsubscribe(model._on_sample)                 # the extrema fallback too
    model._trial.shade_done.set()
    model._trial.shade_worker.join(5)
    model._trial.shade = ts.ShadeTracker()
    model._trial.shades.clear()
    model._trial.live = an.LiveForce()
    return trial


def write(model, t, shade):
    """One frame `t` s after Arm, as the shade worker hands it on."""
    model._shade_frame(model._trial, t, frame_with_green(shade))


def feed(model, series, hz=15, start=0.0):
    """Write frames straight through the shade worker's body."""
    for i, shade in enumerate(series):
        write(model, start + i / hz, shade)


def word(model):
    """The class word of the sheet's Force estimate ("No contact", "Low")."""
    return model.force_estimate.split(" · ")[0]


def rising_falling(base=150.0, peak=1.5, n=330, hz=15):
    t = np.arange(n) / hz
    out = np.full(n, base)
    up = (t >= 4) & (t < 7)
    out[up] = base + (peak - 1) * base * (1 - np.cos(np.pi * (t[up] - 4) / 3)) / 2
    dn = t >= 7
    f = np.clip((t[dn] - 7) / 12, 0, 1)
    out[dn] = base + (peak - 1) * base * (1 + np.cos(np.pi * f)) / 2
    return out


def test_the_shade_estimate_is_blank_until_a_trial_is_armed(station):
    model, red, *_ = station
    assert model.force_estimate == ""
    arm_alone(model, red)
    feed(model, [150.0] * 30)
    assert model.force_estimate == "No contact"


def test_the_status_field_is_on_the_sheet_and_reads_the_trackers_words(station):
    model, red, *_ = station
    from schema import elements
    assert any(e.get("model_attr") == "force_estimate" for e in elements(model.schema))
    arm_alone(model, red)
    feed(model, [150.0] * 30)
    for status in ("Contact", "Low", "Medium", "High"):
        model._trial.shade.status, model._trial.shade.position = status, 0.5
        assert model.force_estimate == f"{ts.status_text(status)} · 0.50"


def test_frames_feed_the_tracker_and_the_telemetry_gains_the_shade(station):
    model, red, *_ = station
    trial = arm_alone(model, red)
    feed(model, [150.0] * 30)
    tracker = model._trial.shade
    assert tracker.baseline == pytest.approx(150.0)
    assert [s for _t, s in model._trial.shades][:3] == [150.0] * 3
    _finish(model)
    rows = list(csv.DictReader(open(model.pictures_root / str(trial) / "telemetry.csv")))
    shades = [r for r in rows if r["stream"] == "transfer_map.shade"]
    assert len(shades) == 30 and float(shades[5]["value"]) == 150.0


def test_the_analysis_frames_reach_the_tracker_through_the_worker(station):
    """No test feeding: the analysis's own settled frames, queued on its run
    thread and measured on the trial's shade worker."""
    model, red, *_ = station
    _arm(model)
    assert model._on_frame in red._frame_subscribers
    assert _wait_for(lambda: len(model._trial.shades) >= 5, timeout=5.0)
    assert model._trial.shade_worker.is_alive()
    worker = model._trial.shade_worker
    model.run("abort_trial")
    worker.join(5)
    assert not worker.is_alive() and model._on_frame not in red._frame_subscribers


def test_the_live_status_walks_from_no_contact_to_high(station):
    model, red, *_ = station
    arm_alone(model, red)
    seen = []
    for i, shade in enumerate(rising_falling()):
        write(model, i / 15, shade)
        if not seen or seen[-1] != word(model):
            seen.append(word(model))
    assert seen == ["No contact", "Contact", "Low force", "Medium force", "High force"]


def test_a_finished_trial_stores_the_shade_columns_at_the_mark(station, private_db):
    model, red, *_ = station
    trial = arm_alone(model, red)
    series = rising_falling()
    for i, shade in enumerate(series):
        t = i / 15
        if i == 11 * 15:                                  # the Mark, 4 s into the fall
            model._trial.operator_t = t
        write(model, t, shade)
    _finish(model)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["force_class"] in ("Low", "Medium")
    assert 0.1 <= row["force_position"] < 0.67
    assert row["shade_baseline"] == pytest.approx(150.0)
    assert row["shade_peak"] > 200 and row["shade_mark"] < row["shade_peak"]
    assert row["contact_lowered"] is not None


def test_a_trial_that_never_touched_stores_no_force(station, private_db):
    model, red, *_ = station
    trial = arm_alone(model, red)
    for i in range(120):
        t = i / 15
        if i == 90:
            model._trial.operator_t = t
        write(model, t, 150.0)
    _finish(model)
    row = _rows(private_db, "SELECT * FROM trials WHERE id=?", trial)[0]
    assert row["force_position"] is None and row["force_class"] is None
    assert row["shade_baseline"] == pytest.approx(150.0)


def test_the_maps_default_force_is_the_shade_position(station):
    model, *_ = station
    assert next(iter(an.FORCE_DEFINITIONS)) == "shade_position"
    assert model._definition == "shade_position"
    assert "shade_position" in model.force_definition_options


def test_the_map_reads_the_stored_position_not_the_red_trace(station, private_db):
    model, *_ = station
    trial = _arm(model)
    _finish(model)
    model._store.update(trial, {"force_position": 0.42, "force_class": "Medium"})
    model._indices.clear()
    row = model._store.trial(trial)
    assert model._force_of(row)["shade_position"] == pytest.approx(0.42)


def test_the_trials_log_names_the_force_class(station):
    model, *_ = station
    trial = _arm(model)
    _finish(model)
    model._store.update(trial, {"force_class": "High", "force_position": 0.9})
    assert any("force High" in line for line in model.trials_log)


# -- rebuilding the stored force from the footage -----------------------------

def _trial_with_footage(model, monkeypatch, **fields):
    """A recorded trial whose footage is on disk where the map keeps it."""
    from devices import video
    from test_shade_offline import record, series
    monkeypatch.setattr(video, "_encoder", lambda: None)
    base = {"started_at": "2026-10-06T10:00:00", "tip_id": "T", "tilt_deg": 7.0,
            "speed_steps_s": 200.0, "status": "recorded", "origin": "recorded",
            "mark_operator_t": 11.0}
    base.update(fields)
    trial = model._store.insert(base)
    folder = model.pictures_root / str(trial)
    path, index = record(folder, series(), jpeg=True)
    model._store.update(trial, {"video_path": path, "video_index_path": index,
                                "video_frames": 330})
    return trial


@pytest.fixture
def bare(tmp_path):
    model = TransferMap(db_path=tmp_path / "map.sqlite")
    model.open()
    yield model
    model.close()


def test_rebuild_force_is_a_declared_invisible_command(bare):
    from schema import elements
    found = [e for e in elements(bare.schema) if e.get("command") == "rebuild_force"]
    assert found and found[0]["type"] == "internal"


def test_rebuild_force_fills_the_columns_of_valid_recorded_trials(bare, monkeypatch):
    a = _trial_with_footage(bare, monkeypatch)
    b = _trial_with_footage(bare, monkeypatch, mark_operator_t=13.0)
    result = bare.run("rebuild_force")
    assert result.is_ok, result
    assert set(result.value) == {a, b}
    for trial in (a, b):
        row = bare._store.trial(trial)
        assert row["force_class"] in ("Low", "Medium", "High")
        assert row["shade_baseline"] == pytest.approx(150.0, abs=2.0)
        assert row["contact_lowered"] is not None
    assert bare._store.trial(b)["force_position"] > bare._store.trial(a)["force_position"]


def test_rebuild_force_leaves_invalid_and_aborted_trials_alone(bare, monkeypatch):
    ok = _trial_with_footage(bare, monkeypatch)
    bad = _trial_with_footage(bare, monkeypatch, invalid=1)
    gone = _trial_with_footage(bare, monkeypatch, status="aborted")
    assert set(bare.run("rebuild_force").value) == {ok}
    assert bare._store.trial(bad)["force_position"] is None
    assert bare._store.trial(gone)["force_position"] is None


def test_rebuild_force_can_name_one_trial_even_an_invalid_one(bare, monkeypatch):
    bad = _trial_with_footage(bare, monkeypatch, invalid=1)
    assert set(bare.run("rebuild_force", None, (bad,)).value) == {bad}
    assert bare._store.trial(bad)["force_class"] is not None
    assert bare.run("rebuild_force", None, (99,)).is_refused


def test_rebuild_force_skips_a_trial_whose_footage_is_gone(bare, monkeypatch):
    import shutil
    ok = _trial_with_footage(bare, monkeypatch)
    lost = _trial_with_footage(bare, monkeypatch)
    shutil.rmtree(bare.pictures_root / str(lost))
    result = bare.run("rebuild_force")
    assert result.is_ok and set(result.value) == {ok}


def test_rebuild_force_puts_the_trial_on_the_map(bare, monkeypatch):
    trial = _trial_with_footage(bare, monkeypatch)
    assert bare._map_rows()[0]["force"]["shade_position"] is None
    bare.run("rebuild_force")
    rows = bare._map_rows()
    assert rows[0]["id"] == trial and rows[0]["force"]["shade_position"] is not None
