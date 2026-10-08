"""The finalizer window (Qt), driven against a real Transfer Map through a
real Controller, offscreen."""
import os
import pytest

pytest.importorskip("PySide6")
pytestmark = pytest.mark.qt


@pytest.fixture
def window(tmp_path, qapp):
    from controller.controller import Controller
    from model.transfer_map import TransferMap
    from views.qt_finalizer import FinalizerWindow
    controller = Controller()
    model = controller.add(TransferMap.NAME, TransferMap(db_path=tmp_path / "map.sqlite"))
    ids = []
    for sample, flake in (("S1", "1"), ("S1", "2"), ("S2", "1")):
        ids.append(model._store.insert({
            "started_at": "2026-10-06T10:00:00", "tip_id": "T", "tilt_deg": 6.0,
            "speed_steps_s": 250.0, "status": "recorded", "origin": "recorded",
            "sample_id": sample, "chip_id": "1", "flake_id": flake, "cut_id": "1"}))
    win = FinalizerWindow(controller, model.NAME)
    win.ids, win.model = ids, model
    yield win
    win.close()
    controller.close()


def test_it_opens_on_the_first_trial_of_the_first_sample(window):
    assert window._queue[window._index]["id"] == window.ids[0]
    assert "Sample S1" in window.title.text() and "1 of 2" in window.title.text()


def test_next_sample_jumps_past_the_rest_of_the_sample(window):
    window.next_sample()
    assert window._queue[window._index]["id"] == window.ids[2]
    window.next_sample()
    assert "last sample" in window.status.text()


def test_save_and_next_writes_afm_and_optical_then_moves_on(window):
    window._fields["width_um"].setText("1.8")
    window._fields["width_optical_um"].setText("2.0")
    window.save_and_next()
    row = window.model._store.trial(window.ids[0])
    assert row["width_um"] == 1.8 and row["width_optical_um"] == 2.0
    assert row["status"] == "measured"
    assert window._queue[window._index]["id"] == window.ids[1]


def test_a_bad_box_is_refused_in_the_window_and_nothing_is_written(window):
    window._fields["width_um"].setText("-3")
    assert window.save() is False
    assert "width_um" in window.status.text()
    assert window.model._store.trial(window.ids[0])["width_um"] is None


def test_skipping_changes_nothing(window):
    window.next()
    assert window._queue[window._index]["id"] == window.ids[1]
    assert all(window.model._store.trial(i)["width_um"] is None for i in window.ids)


def test_a_filled_trial_leaves_the_unfilled_list(window):
    for name in ("width_um", "thickness_nm", "channel_height_nm",
                 "trench_depth_nm", "width_optical_um"):
        window._fields[name].setText("1")
    window.save()
    assert window.ids[0] not in [t["id"] for t in window._queue]
