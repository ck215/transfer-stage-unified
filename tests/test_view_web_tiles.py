"""The Dashboard's tiles (owner 2026-10-08).

Standard sizes: an entry on the Dashboard is one grid column wide (three
across, two under 1000 px, one under 760) or two (Wide), and a whole number
of grid rows tall - the fewest that hold it. The viewer reorders the tiles by
dragging Move or with Move's arrow keys; the order and the Wide tiles are kept
in this browser (localStorage) and the page draws fine without it. No tile
ever covers the rail's Stop.

Driven in headless Chrome through the harness in test_view_web_server.py
(skipped where node or puppeteer is absent).
"""
import pytest

from controller.controller import Controller
from views.web.server import WebView

from test_view_web_server import FakeProbe, _browse, needs_browser
from test_view_web_sign_in import AccountSetup

NAMES = ["Probe A", "Probe B", "Probe C", "Probe D"]


@pytest.fixture
def launched():
    """A running station with four entries and no accounts."""
    controller = Controller()
    setup = AccountSetup(with_account=False)
    setup.controller = controller
    for name in NAMES:
        controller.add(name, FakeProbe(), {})
    setup.is_launched = True
    view = WebView(controller, setup, port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    yield view
    view.close()


#: Every tile's geometry against the grid it sits on.
_TILES = r"""() => {
  const sheet = document.getElementById('cards');
  const s = getComputedStyle(sheet);
  const cols = s.gridTemplateColumns.split(' ').map(parseFloat);
  const row = parseFloat(s.gridAutoRows), rowGap = parseFloat(s.rowGap), colGap = parseFloat(s.columnGap);
  const tiles = Array.from(sheet.querySelectorAll(':scope > .card')).map((c) => {
    const b = c.getBoundingClientRect();
    const rows = (b.height + rowGap) / (row + rowGap);
    const units = (b.width + colGap) / (cols[0] + colGap);
    return { name: c.querySelector('.card-title').textContent, wide: c.classList.contains('is-wide'),
             rows, units, fits: c.scrollHeight <= c.clientHeight + 1 };
  });
  const b = document.getElementById('full-stop').getBoundingClientRect();
  const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
  return { cols: cols.length, tiles, stop: Boolean(hit && hit.closest('#full-stop')),
           sideways: document.documentElement.scrollWidth > window.innerWidth };
}"""

_ORDER = r"""() => Array.from(document.querySelectorAll('#cards > .card .card-title')).map((n) => n.textContent)"""


def _snapped(state, cols, wide_units):
    assert state["cols"] == cols, state
    assert state["stop"], f"a tile covers the rail's Stop: {state}"
    assert not state["sideways"], state
    for tile in state["tiles"]:
        assert abs(tile["rows"] - round(tile["rows"])) < 0.02 and round(tile["rows"]) >= 1, tile
        want = wide_units if tile["wide"] else 1
        assert abs(tile["units"] - want) < 0.02, (tile, want)
        assert tile["fits"], f"the entry overflows its tile: {tile}"


@needs_browser
def test_tiles_snap_to_the_grid_simple_and_wide_at_every_width(launched, tmp_path):
    out = _browse(launched, r"""
      await until(() => document.querySelectorAll('#cards > .card').length === 4);
      await sleep(500);
      const r = {};
      for (const [w, h, size] of [[1400, 900, 'L'], [900, 900, 'M'], [390, 844, 'phone']]) {
        await page.setViewport({ width: w, height: h });
        await sleep(400);
        r[size + ' simple'] = await page.evaluate(%(tiles)s);
      }
      await page.setViewport({ width: 1400, height: 900 });
      await sleep(300);
      await page.click('#cards > .card:nth-child(1) .tile-wide');
      await sleep(400);
      r.pressed = await page.evaluate(() => document.querySelector('#cards > .card .tile-wide').getAttribute('aria-pressed'));
      for (const [w, h, size] of [[1400, 900, 'L'], [900, 900, 'M'], [390, 844, 'phone']]) {
        await page.setViewport({ width: w, height: h });
        await sleep(400);
        r[size + ' wide'] = await page.evaluate(%(tiles)s);
      }
      return r;
    """ % {"tiles": _TILES}, tmp_path)
    _snapped(out["L simple"], 3, 2)
    _snapped(out["M simple"], 2, 2)
    _snapped(out["phone simple"], 1, 1)
    assert out["pressed"] == "true"
    for size, cols, wide in (("L", 3, 2), ("M", 2, 2), ("phone", 1, 1)):
        state = out[size + " wide"]
        _snapped(state, cols, wide)
        assert [t["wide"] for t in state["tiles"]] == [True, False, False, False], state


@needs_browser
def test_tiles_reorder_by_keys_and_by_drag_and_the_order_survives_a_reload(launched, tmp_path):
    out = _browse(launched, r"""
      const order = %(order)s;
      await until(() => document.querySelectorAll('#cards > .card').length === 4);
      await sleep(400);
      const r = { start: await page.evaluate(order) };
      // Keys: Move on the first tile, Right twice - later by two.
      await page.focus('#cards > .card:nth-child(1) .tile-move');
      await page.keyboard.press('ArrowRight');
      await page.keyboard.press('ArrowRight');
      await sleep(200);
      r.keys = await page.evaluate(order);
      r.focus = await page.evaluate(() => document.activeElement.getAttribute('aria-label'));
      await page.keyboard.press('Home');
      await sleep(200);
      r.home = await page.evaluate(order);
      // Drag: the last tile's Move onto the first tile's left half (a tall
      // window, so both are on screen at once).
      await page.setViewport({ width: 1400, height: 1800 });
      await sleep(300);
      const from = await page.$eval('#cards > .card:last-child .tile-move', (n) => {
        const b = n.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; });
      const to = await page.$eval('#cards > .card:first-child', (n) => {
        const b = n.getBoundingClientRect(); return [b.left + 20, b.top + b.height / 2]; });
      await page.mouse.move(from[0], from[1]);
      await page.mouse.down();
      await page.mouse.move((from[0] + to[0]) / 2, (from[1] + to[1]) / 2, { steps: 4 });
      r.mid = await page.evaluate(() => [document.querySelectorAll('.is-dragging').length]);
      await page.mouse.move(to[0], to[1], { steps: 6 });
      await page.mouse.up();
      await sleep(300);
      r.dragged = await page.evaluate(order);
      await page.click('#cards > .card:nth-child(2) .tile-wide');
      await sleep(200);
      await page.reload({ waitUntil: 'load' });
      await until(() => document.querySelectorAll('#cards > .card').length === 4);
      await sleep(600);
      r.reloaded = await page.evaluate(order);
      r.wide = await page.evaluate(() => Array.from(document.querySelectorAll('#cards > .card'))
        .map((c) => c.classList.contains('is-wide')));
      return r;
    """ % {"order": _ORDER}, tmp_path)
    assert out["start"] == NAMES, out
    assert out["mid"][0] == 1, f"no tile is held mid-drag: {out['mid']}"
    assert out["keys"] == ["Probe B", "Probe C", "Probe A", "Probe D"], out
    assert out["focus"] == "Move Probe A", "focus left Move after a keyboard move"
    assert out["home"] == ["Probe A", "Probe B", "Probe C", "Probe D"], out
    assert out["dragged"] == ["Probe D", "Probe A", "Probe B", "Probe C"], out
    assert out["reloaded"] == out["dragged"], "the order did not survive a reload"
    assert out["wide"] == [False, True, False, False], out


@needs_browser
def test_tiles_draw_without_storage(launched, tmp_path):
    """Blocked storage (a private window, cleared site data): launch order,
    simple tiles, a move still works for the session, and nothing throws."""
    out = _browse(launched, r"""
      await page.evaluateOnNewDocument(() => {
        const no = () => { throw new Error('storage blocked'); };
        Object.defineProperty(window, 'localStorage', { get: no });
      });
      await page.reload({ waitUntil: 'load' });
      await until(() => document.querySelectorAll('#cards > .card').length === 4);
      await sleep(400);
      const start = await page.evaluate(%(order)s);
      await page.focus('#cards > .card:nth-child(1) .tile-move');
      await page.keyboard.press('End');
      await sleep(200);
      return { start, moved: await page.evaluate(%(order)s) };
    """ % {"order": _ORDER}, tmp_path)
    assert out["start"] == NAMES, out
    assert out["moved"] == ["Probe B", "Probe C", "Probe D", "Probe A"], out
