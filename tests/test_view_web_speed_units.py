"""Speed units (owner ruling 2026-10-12): the Web view's side.

SU-1: an int box must let the operator edit it (Backspace, Delete, arrows).
SU-4: the unit choice on each speed entry (see the later tests).

The browser tests run headless (puppeteer through test_view_web_server's
harness, skipped where node or puppeteer is absent), against a real probe
in SIM.
"""
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
