"""Speed units (owner ruling 2026-10-12): the Web view's side.

SU-1: an int box must let the operator edit it (Backspace, Delete, arrows).
SU-4: the unit choice on each speed entry (see the later tests).

The browser tests run headless (puppeteer through test_view_web_server's
harness, skipped where node or puppeteer is absent), against a real probe
in SIM.
"""
import json

import pytest

from controller.controller import Controller
from model.probe import ChuckPositioner, DCProbe, StepperProbe
from views.web.server import WebView

from test_view_web_client import NODE, _node_value
from test_view_web_server import FakeSetup, _browse, needs_browser


def _station(cls, name):
    controller = Controller()
    probe = cls(port=None, gamepad=None, sim=True)
    controller.add(name, probe)
    probe.open()
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    return view, controller, probe


@pytest.fixture
def chuck_station():
    view, controller, probe = _station(ChuckPositioner, "Chuck Positioner")
    try:
        yield view, controller, probe
    finally:
        view.close()
        probe.close()


@pytest.fixture
def stepper_station():
    view, controller, probe = _station(StepperProbe, "Stepper Probe")
    try:
        yield view, controller, probe
    finally:
        view.close()
        probe.close()


# ==========================================================================
# SU-1: an int box swallowed its own editing keys
# ==========================================================================
@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_su1_an_int_box_refuses_only_typed_characters_not_named_keys():
    named = ["Backspace", "Delete", "Enter", "Escape", "Home", "End",
             "ArrowLeft", "ArrowRight", "Tab"]
    refused = _node_value(
        "%s.map((key) => rejectsIntKey({ key }))" % str(named).replace("'", '"'))
    assert refused == [False] * len(named), dict(zip(named, refused))
    assert _node_value("['.', ',', 'e', 'E'].map((key) => rejectsIntKey({ key }))") == [
        True, True, True, True]
    assert _node_value("['7', '-', '0'].map((key) => rejectsIntKey({ key }))") == [
        False, False, False]
    # A chord is a shortcut, not a typed character (Ctrl+E is not an "e").
    assert _node_value("rejectsIntKey({ key: 'e', ctrlKey: true })") is False


@needs_browser
def test_su1_backspace_and_delete_edit_an_int_entry(chuck_station, tmp_path):
    view, controller, probe = chuck_station
    out = _browse(view, r"""
      const sel = '#cards .card-body input.input[name="x_dist"]';
      await until(() => Boolean(document.querySelector('#cards .card-body input.input[name="x_dist"]')));
      const value = () => page.$eval(sel, (el) => el.value);
      await page.focus(sel);
      await page.$eval(sel, (el) => el.select());
      await page.keyboard.press('Backspace');
      const cleared = await value();
      await page.keyboard.type('123');
      await page.keyboard.press('Backspace');
      const afterBackspace = await value();
      await page.keyboard.press('ArrowLeft');
      await page.keyboard.press('Delete');
      const afterDelete = await value();
      await page.keyboard.type('.');
      await page.keyboard.type('e');
      return { cleared, afterBackspace, afterDelete, final: await value() };
    """, tmp_path)
    assert out == {"cleared": "", "afterBackspace": "12", "afterDelete": "1",
                   "final": "1"}, out


# ==========================================================================
# SU-4: the unit choice on each speed entry
# ==========================================================================
_UNIT = ('#cards .choice[aria-label^="%s"] .choice-seg[data-option="%s"]')
_ENTRY = '#cards .card-body input.input[name="%s"]'

#: Which of a speed's entries are drawn right now.
_DRAWN = r"""(names) => names.filter((n) => {
  const box = document.querySelector('#cards .card-body input.input[name="' + n + '"]');
  return Boolean(box && box.getClientRects().length);
})"""


@needs_browser
def test_su4_switching_to_steps_shows_that_entry_hides_percent_and_step_carries_only_it(
        stepper_station, tmp_path):
    view, controller, probe = stepper_station
    out = _browse(view, r"""
      const names = ['full_speed_pct', 'full_speed', 'full_speed_um_s'];
      const drawn = () => page.evaluate(%(drawn)s, names);
      await until(() => Boolean(document.querySelector('#cards .choice')));
      const before = await drawn();
      await page.click(%(steps)s);
      await until(() => document.querySelector(%(steps)s).getAttribute('aria-checked') === 'true');
      await sleep(500);
      const after = await drawn();
      const box = %(box)s;
      await page.$eval(box, (el) => { el.focus(); el.select(); });
      await page.keyboard.type('800');
      let sent = null;
      page.on('request', (r) => {
        const body = r.postData() || '';
        if (r.url().includes('/api/run') && body.includes('"step"')) sent = JSON.parse(body);
      });
      await page.evaluate(() => Array.from(document.querySelectorAll('#cards .card-body button'))
        .find((b) => b.textContent.trim().startsWith('Step')).click());
      await when(async () => sent !== null, 8000);
      await sleep(600);
      return { before, after, inputs: sent && Object.keys(sent.inputs).sort() };
    """ % {"drawn": _DRAWN, "steps": json.dumps(_UNIT % ("Autonomous", "steps/s")),
           "box": json.dumps(_ENTRY % "full_speed")}, tmp_path)
    assert out["before"] == ["full_speed_pct"], out
    assert out["after"] == ["full_speed"], out
    speed_inputs = [k for k in out["inputs"] if "full_speed" in k]
    assert speed_inputs == ["full_speed"], out
    assert probe.full_speed == 800 and probe.full_speed_unit == "steps/s"


@needs_browser
def test_su4_the_other_units_line_shows_on_the_dashboard_and_the_choice_survives_a_refresh(
        stepper_station, tmp_path):
    view, controller, probe = stepper_station
    out = _browse(view, r"""
      await until(() => Boolean(document.querySelector('#cards .choice')));
      const lines = () => page.evaluate(() => Array.from(document.querySelectorAll(
        '#cards .row.secondary.on-dashboard')).filter((n) => n.getClientRects().length)
        .map((n) => n.textContent.trim()));
      const overview = await page.evaluate(() => document.getElementById('cards').classList.contains('is-overview'));
      const first = await lines();
      await page.click(%(um)s);
      await until(() => document.querySelector(%(um)s).getAttribute('aria-checked') === 'true');
      await sleep(600);
      const second = await lines();
      await page.reload({ waitUntil: 'load' });
      await until(() => Boolean(document.querySelector('#cards .choice')));
      await sleep(600);
      const kept = await page.$eval(%(um)s, (el) => el.getAttribute('aria-checked'));
      const tabs = await page.evaluate(() => Array.from(document.querySelectorAll(
        '#cards .choice[aria-label^="Autonomous"] .choice-seg')).map((s) => s.tabIndex));
      return { overview, first, second, kept, tabs };
    """ % {"um": json.dumps(_UNIT % ("Autonomous", "µm/s"))}, tmp_path)
    assert out["overview"] is True, out
    assert "400 steps/s · 250 µm/s" in out["first"], out
    assert "12.5 % · 400 steps/s" in out["second"], out
    assert out["kept"] == "true" and out["tabs"] == [-1, -1, 0], out
