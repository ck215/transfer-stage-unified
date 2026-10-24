"""CR-2: the Chuck Positioner's step-resolution lever in the Web view.

The toggle and its "Now:" line are schema elements the client already draws
(a toggle, and a secondary readout flagged `dashboard`), so no client change
is needed; these tests prove it on the real page: the Dashboard's compact
tile carries the toggle and the line, and the device page carries both in
tier 1. Headless (puppeteer through test_view_web_server's harness; skipped
where node or puppeteer is absent), against a Chuck Positioner in SIM, whose
simulated board has the `res` capability.
"""
import json

import pytest

from controller.controller import Controller
from model.probe import ChuckPositioner
from views.web.server import WebView

from test_view_web_server import FakeSetup, _browse, needs_browser
from test_xyz_stage import wait_for

#: The toggle's face in the page: the client keeps what comes before the
#: parenthesis on the face ("(press for ...)" is the title and the label).
FAST = "Fast · ½ step"
FINE = "Fine · ⅛ step"


@pytest.fixture
def chuck_station():
    controller = Controller()
    probe = ChuckPositioner(port=None, gamepad=None, sim=True)
    controller.add("Chuck Positioner", probe)
    probe.open()
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, probe
    finally:
        view.close()
        probe.close()


def test_the_sim_chuck_is_a_res_board_that_answers_the_lever(chuck_station):
    view, controller, probe = chuck_station
    assert wait_for(lambda: probe.resolution == 2, 3.0), probe.state["values"]
    assert probe.port.identity == "c caps=res"
    assert probe.run("set_resolution", None, ("fine",)).is_ok
    assert probe.resolution == 8 and probe.port.simulator.microsteps == 8


#: The visible elements of the Chuck's card, as text.
_CARD = r"""() => {
  const card = Array.from(document.querySelectorAll('#cards .card')).find(
    (c) => /Chuck Positioner/.test(c.textContent));
  const shown = (n) => n.getClientRects().length > 0;
  const toggles = Array.from(card.querySelectorAll('button, [role=switch]')).filter(shown)
    .map((n) => n.textContent.trim()).filter((t) => /^(Fast|Fine) ·/.test(t));
  const lines = Array.from(card.querySelectorAll('.row.secondary')).filter(shown)
    .map((n) => n.textContent.trim().replace(/\s+/g, ' ')).filter((t) => /step per count|unknown until/.test(t));
  return { overview: document.getElementById('cards').classList.contains('is-overview'),
           toggles, lines,
           onDashboard: Array.from(card.querySelectorAll('.row.secondary.on-dashboard'))
             .filter(shown).length };
}"""


@needs_browser
def test_the_dashboard_tile_shows_the_toggle_and_the_now_line(chuck_station, tmp_path):
    view, controller, probe = chuck_station
    assert wait_for(lambda: probe.resolution == 2, 3.0)
    out = _browse(view, r"""
      await until(() => Boolean(document.querySelector('#cards .card')));
      await sleep(800);
      return await page.evaluate(%(card)s);
    """ % {"card": _CARD}, tmp_path)
    assert out["overview"] is True, out
    assert out["toggles"] == [FAST], out
    assert any("½ step per count · fastest" in line and line.startswith("Now:")
               for line in out["lines"]), out
    assert out["onDashboard"] >= 1, out


@needs_browser
def test_the_device_page_shows_both_in_tier_1_and_the_toggle_flips_the_board(
        chuck_station, tmp_path):
    view, controller, probe = chuck_station
    assert wait_for(lambda: probe.resolution == 2, 3.0)
    out = _browse(view, r"""
      await until(() => Boolean(document.querySelector('#cards .card')));
      await page.evaluate(() => {
        const card = Array.from(document.querySelectorAll('#cards .card')).find(
          (c) => /Chuck Positioner/.test(c.textContent));
        const open = card.querySelector('.card-open, .card-head');
        (card.querySelector('.card-open') || open).click();
      });
      await until(() => !document.getElementById('cards').classList.contains('is-overview'), 8000);
      await sleep(800);
      const before = await page.evaluate(%(card)s);
      const tier1 = await page.evaluate(() => {
        const toggle = Array.from(document.querySelectorAll('#cards button')).find(
          (b) => /^(Fast|Fine) ·/.test(b.textContent.trim()));
        return Boolean(toggle) && !toggle.closest('.tier-well, .card-tiers')
          && /Step resolution/.test(toggle.closest('.card-body').textContent);
      });
      await page.evaluate(() => Array.from(document.querySelectorAll('#cards button')).find(
        (b) => /^Fast ·/.test(b.textContent.trim())).click());
      await until(() => Array.from(document.querySelectorAll('#cards button')).some(
        (b) => /^Fine ·/.test(b.textContent.trim())), 8000);
      await sleep(400);
      const after = await page.evaluate(%(card)s);
      return { before, after, tier1 };
    """ % {"card": _CARD}, tmp_path)
    assert out["before"]["overview"] is False, out
    assert out["before"]["toggles"] == [FAST], out
    assert out["tier1"] is True, out
    assert any("½ step per count" in line for line in out["before"]["lines"]), out
    assert out["after"]["toggles"] == [FINE], out
    assert any("⅛ step per count" in line for line in out["after"]["lines"]), out
    assert probe.resolution == 8 and probe.is_fine


# -- what needs no browser: the page is served what it draws from -----------------------

def _get(view, route):
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{view.port}{route}", timeout=5) as got:
        return json.loads(got.read())


def test_the_served_schema_and_state_carry_what_the_tile_draws(chuck_station):
    """The client draws a toggle in a tile (its `is-overview` rules leave a
    toggle alone) and keeps a secondary readout flagged `dashboard`; these
    are the elements it is served, and the values it reads them from."""
    view, controller, probe = chuck_station
    assert wait_for(lambda: probe.resolution == 2, 3.0)
    schema = _get(view, "/api/schema?name=Chuck%20Positioner")
    sections = {s["title"]: s for s in schema["sections"]}
    toggle, line = sections["Step resolution"]["elements"]
    assert toggle["type"] == "toggle" and toggle["model_attr"] == "is_fine"
    assert line["secondary"] and line["dashboard"] and line["lead"] == "Now:"
    values = _get(view, "/api/state")["models"]["Chuck Positioner"]["values"]
    assert values["is_fine"] is False and values["resolution_ready"] is True
    assert values["resolution_text"] == "½ step per count · fastest"


def test_the_client_needs_no_change_for_a_dashboard_secondary_line():
    """The two client rules the lever leans on, already there for the speed
    units' other-units line: `dashboard` sets `on-dashboard`, and the tile's
    stylesheet shows exactly those secondary lines."""
    from test_view_web_client import APP_JS, STYLES
    assert "if (element.dashboard) node.classList.add('on-dashboard');" in APP_JS
    assert ".sheet.is-overview .row.secondary.on-dashboard { display: grid; }" in STYLES
