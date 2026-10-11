"""The Web data finalizer (C-11, 2026-10-10): the Qt window's six behaviours
(tests/test_view_qt_finalizer.py, frozen with that view) driven against a real
Transfer Map through a real Controller, then the same walk in headless Chrome
on the Transfer Map's page. The rules themselves stay tested in
test_finalize.py / test_finalizer_model.py."""
import json

import pytest

from controller.controller import Controller
from model.finalizer import Finalizer
from model.transfer_map import TransferMap
from views.web.server import WebView

from test_view_web_server import _browse, needs_browser
from test_view_web_file_open import _Setup


@pytest.fixture
def station(tmp_path):
    controller = Controller()
    tm = TransferMap(db_path=tmp_path / "map.sqlite")
    ids = []
    for sample, flake in (("S1", "1"), ("S1", "2"), ("S2", "1")):
        ids.append(tm._store.insert({
            "started_at": "2026-10-06T10:00:00", "tip_id": "T", "tilt_deg": 6.0,
            "speed_steps_s": 250.0, "status": "recorded", "origin": "recorded",
            "sample_id": sample, "chip_id": "1", "flake_id": flake, "cut_id": "1"}))
    fin = Finalizer(sim=True)
    controller.add("Transfer Map", tm)
    controller.add(Finalizer.NAME, fin)
    fin.ids, fin.tm, fin.controller = ids, tm, controller
    yield fin
    controller.close()


def type_in(fin, **boxes):
    for name, text in boxes.items():
        setattr(fin, name, text)


def shown(fin):
    return fin._queue[fin._index]["id"]


def test_it_opens_on_the_first_trial_of_the_first_sample(station):
    assert shown(station) == station.ids[0]
    assert "Sample S1" in station.title_text and "1 of 2" in station.title_text


def test_it_is_hosted_on_the_transfer_map_and_refuses_without_one():
    assert Finalizer.HOST == "Transfer Map"
    lone = Finalizer(sim=True)
    for command in ("next", "previous", "next_sample", "save", "save_and_next", "reload"):
        assert lone.run(command).is_refused, command
    assert "Open the Transfer Map" in lone.title_text


def test_next_sample_jumps_past_the_rest_of_the_sample(station):
    assert station.run("next_sample").is_ok
    assert shown(station) == station.ids[2]
    station.run("next_sample")
    assert "last sample" in station.status_text


def test_save_and_next_writes_afm_and_optical_then_moves_on(station):
    type_in(station, f_width_um="1.8", f_width_optical_um="2.0")
    result = station.run("save_and_next", {"f_width_um": "1.8", "f_width_optical_um": "2.0"})
    assert result.is_ok, result
    row = station.tm._store.trial(station.ids[0])
    assert row["width_um"] == 1.8 and row["width_optical_um"] == 2.0
    assert row["status"] == "measured"
    assert row["width_optical_method"] == "estimate"      # typed by eye
    assert shown(station) == station.ids[1]


def test_a_bad_box_is_refused_in_the_words_of_the_rules_and_nothing_is_written(station):
    result = station.run("save", {"f_width_um": "-3"})
    assert result.is_refused and "width_um" in result.reason, result
    assert station.tm._store.trial(station.ids[0])["width_um"] is None


def test_skipping_changes_nothing(station):
    assert station.run("next").is_ok
    assert shown(station) == station.ids[1]
    assert all(station.tm._store.trial(i)["width_um"] is None for i in station.ids)


def test_a_filled_trial_leaves_the_unfilled_list(station):
    boxes = {"f_width_um": "1", "f_thickness_nm": "1", "f_channel_height_nm": "1",
             "f_trench_depth_nm": "1", "f_width_optical_um": "1"}
    assert station.run("save", boxes).is_ok
    assert station.ids[0] not in [t["id"] for t in station._queue]


def test_the_unfilled_filter_can_be_turned_off(station):
    boxes = {"f_width_um": "1", "f_thickness_nm": "1", "f_channel_height_nm": "1",
             "f_trench_depth_nm": "1", "f_width_optical_um": "1"}
    station.run("save", boxes)
    assert station.run("set_only_missing", None, (False,)).is_ok
    assert station.ids[0] in [t["id"] for t in station._queue]


def test_an_estimate_method_is_checked_and_saved(station):
    assert station.run("set_method", None, ("by guess",)).is_refused
    assert station.run("set_method", None, ("reticle",)).is_ok
    assert station.run("save", {"f_width_optical_um": "2"}).is_ok
    assert station.tm._store.trial(station.ids[0])["width_optical_method"] == "reticle"


def test_the_form_loads_the_trials_own_values(station):
    station.run("save", {"f_width_um": "1.5", "f_note": "clean cut"})
    row = station.tm._store.trial(station.ids[0])
    assert row["note"] == "clean cut"
    station.run("set_only_missing", None, (False,))
    assert station.f_width_um == "1.5" and station.f_note == "clean cut"
    station.run("next")
    assert station.f_width_um == "" and station.f_sample_id == "S1"


def test_the_schema_is_a_form_with_one_save_that_declares_every_box(station):
    import schema as sch
    elements = list(sch.elements(station.schema))
    save = next(e for e in elements if e.get("command") == "save")
    entries = [e["model_attr"] for e in elements if e["type"] == "entry"]
    assert sorted(save["inputs"]) == sorted(entries)
    images = [e["data_command"] for e in elements if e["type"] == "image"]
    assert images == ["first_frame_png", "mark_frame_png", "before_full_png",
                      "outcome_figure"]


@needs_browser
def test_the_transfer_map_page_walks_the_trials_in_the_browser(station, tmp_path):
    view = WebView(station.controller, _Setup(), port=0, open_browser=False)
    assert view.open()
    try:
        out = _browse(view, r"""
          await page.evaluate(() => {
            const link = document.querySelector('.model-link[data-model="Transfer Map"]');
            if (link) link.click();
          });
          await sleep(1500);
          const find = (name) => page.evaluate((n) => {
            const input = document.querySelector('input[name="' + n + '"]');
            return Boolean(input);
          }, name);
          const present = await find('f_width_um');
          const set = (name, value) => page.evaluate((n, v) => {
            const input = document.querySelector('input[name="' + n + '"]');
            input.value = v;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
          }, name, value);
          await set('f_width_um', '1.8');
          const press = (text) => page.evaluate((t) => {
            const b = Array.from(document.querySelectorAll('button'))
              .find((x) => x.textContent.trim().toLowerCase() === t);
            if (b) b.click();
            return Boolean(b);
          }, text);
          const pressed = await press('save and next');
          await sleep(1500);
          return { present, pressed };
        """, tmp_path)
    finally:
        view.close()
    assert out == {"present": True, "pressed": True}, out
    row = station.tm._store.trial(station.ids[0])
    assert row["width_um"] == 1.8 and row["status"] == "measured"
