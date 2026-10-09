"""Web view rendering defects found by headless captures (rb-ui-render).

1. A numeric entry is as wide as the widest value its Param allows: nothing
   clips at 1280 or 1400.
2. A secondary readout after a rail number is a quiet line UNDER it, never
   beside it (MODEL_CONTRACT, "Secondary readouts").
3. The Dashboard's tiles flow in rail order with no hole: no tile sits lower
   than the highest free slot its column could have given it.

Driven in headless Chrome through the harness in test_view_web_server.py
(skipped where node or puppeteer is absent), with every model in SIM.
"""
import json

from test_view_web_server import _browse, needs_browser, sim_station  # noqa: F401

#: Past the drawer, on the Dashboard, at a given size.
_READY = r"""
  const ready = async (w, h) => {
    await page.setViewport({ width: w, height: h });
    if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
      await page.click('#drawer-close');
    }
    await page.waitForFunction(() => document.querySelector('#model-nav [data-page="overview"]')
      && document.querySelectorAll('#model-nav [data-model]').length >= 6, { polling: 100, timeout: 15000 });
    await sleep(700);
  };
  const open = async (name) => {
    await page.click('#model-nav [data-model="' + name + '"]');
    await sleep(900);
  };
"""


@needs_browser
def test_a_numeric_entry_is_wide_enough_for_its_widest_value(sim_station, tmp_path):
    """The Stepper Probe's speed (maximum 2000, three decimals) read
    "250.00(" in a 5.5 rem box. Every number box on the Stepper's page holds
    its Param's widest text, and "2000.000" fits, at 1400 and at 1280."""
    view, controller = sim_station
    out = _browse(view, _READY + r"""
      const r = {};
      for (const w of [1400, 1280]) {
        await ready(w, 800);
        await open('Stepper Probe');
        r[w] = await page.evaluate(() => {
          const rows = [];
          for (const input of document.querySelectorAll('.card.is-opened input[type="number"]')) {
            if (!input.getClientRects().length) continue;
            const wide = input.max ? input.max.replace('-', '') : '';
            const dec = (input.step.split('.')[1] || '').length;
            const widest = input.max ? String(Math.floor(Math.abs(Number(input.max)))) + (dec ? '.' + '8'.repeat(dec) : '') : '';
            const probe = document.createElement('span');
            const cs = getComputedStyle(input);
            probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font:' + cs.font
              + ';font-variant-numeric:tabular-nums;font-family:' + cs.fontFamily;
            probe.textContent = widest || input.value;
            document.body.appendChild(probe);
            const need = probe.getBoundingClientRect().width;
            probe.remove();
            const room = input.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
            rows.push({ name: input.name, value: input.value, max: input.max, scroll: input.scrollWidth,
                        client: input.clientWidth, need, room });
          }
          return rows;
        });
      }
      return r;
    """, tmp_path)
    for width, rows in out.items():
        assert rows, f"no number entries at {width}"
        speeds = [r for r in rows if r["max"] == "2000"]
        assert speeds, f"the speed entry is missing at {width}: {rows}"
        for row in rows:
            assert row["need"] <= row["room"] + 0.5, f"{row['name']} clips at {width}: {row}"
            assert row["scroll"] <= row["client"] + 1, f"{row['name']} overflows at {width}: {row}"


@needs_browser
def test_a_rail_secondary_line_sits_under_its_value(sim_station, tmp_path):
    """"0 steps" under the Stepper's "X 0.00 um" read as an exponent: raised
    beside the number's top right. It is a line under the value, left
    aligned with it, and the number itself did not move."""
    view, controller = sim_station
    out = _browse(view, _READY + r"""
      await ready(1400, 900);
      const r = {};
      for (const name of ['Stepper Probe', 'XYZ Stage']) {
        await open(name);
        r[name] = await page.evaluate(() => Array.from(
          document.querySelectorAll('.card.is-opened .reading-axis')).map((axis) => {
            const value = axis.querySelector(':scope > .value').getBoundingClientRect();
            const second = axis.querySelector(':scope > .row.secondary');
            if (!second) return null;
            const s = second.querySelector('.value').getBoundingClientRect();
            return { valueBottom: value.bottom, valueLeft: value.left, secondTop: s.top,
                     secondLeft: s.left, secondText: second.textContent };
          }));
      }
      return r;
    """, tmp_path)
    for name, axes in out.items():
        found = [a for a in axes if a]
        assert found, f"{name} shows no rail secondary line: {axes}"
        for a in found:
            assert a["secondTop"] >= a["valueBottom"] - 1, f"{name}: secondary beside its value: {a}"
            assert abs(a["secondLeft"] - a["valueLeft"]) <= 1.5, f"{name}: secondary not left aligned: {a}"


@needs_browser
def test_the_dashboard_cards_flow_in_rail_order_with_no_hole(sim_station, tmp_path):
    """Tiles are whole grid rows tall and heights differ; with one auto
    placement cursor a tall tile left a free slot that a later tile then
    skipped. Each tile sits in the column that frees up first, in rail
    order: no column has a hole in it, and the tiles run in order. Checked as laid out, and with each of the first three
    tiles made Wide (a Wide tile spans two columns)."""
    view, controller = sim_station
    out = _browse(view, _READY + r"""
      const r = {};
      const measure = () => page.evaluate(() => {
        const sheet = document.getElementById('cards');
        const gap = parseFloat(getComputedStyle(sheet).rowGap) || 0;
        const tiles = Array.from(sheet.querySelectorAll(':scope > .card')).filter((c) => c.getClientRects().length)
          .map((c) => { const b = c.getBoundingClientRect();
            return { name: (c.querySelector('.card-title') || {}).textContent, left: b.left, right: b.right,
                     top: b.top + scrollY, bottom: b.bottom + scrollY }; });
        const lefts = Array.from(new Set(tiles.map((t) => Math.round(t.left)))).sort((a, b) => a - b);
        return { gap, tiles, lefts };
      });
      const toggle = (i) => page.evaluate((i) => {
        const tile = document.querySelectorAll('#cards > .card:not(.setup-card)')[i];
        tile.querySelector('.tile-wide').click();
      }, i);
      for (const [w, h] of [[1400, 900], [1280, 720]]) {
        await ready(w, h);
        await page.evaluate(() => document.querySelector('#model-nav [data-page="overview"]').click());
        await sleep(900);
        r[w + ' plain'] = await measure();
        for (const i of [0, 1, 2]) {
          await toggle(i); await sleep(900);
          r[w + ' wide ' + i] = await measure();
          await toggle(i); await sleep(900);
        }
      }
      return r;
    """, tmp_path)
    for state, got in out.items():
        tiles, gap = got["tiles"], got["gap"]
        assert len(tiles) >= 6, (state, tiles)
        # No hole: down every column the tiles follow one another with at
        # most the grid's gap between them, from the first tile's top.
        for c in got["lefts"]:
            inside = sorted((t for t in tiles if t["left"] - 2 <= c < t["right"] - 2),
                            key=lambda t: t["top"])
            edge = tiles[0]["top"]
            for tile in inside:
                # A Wide tile waits for both its columns, so under one a
                # column may rest between tiles; it never starts late.
                slack = gap + 1.5 if "wide" not in state or tile is inside[0] else float("inf")
                assert tile["top"] <= edge + slack, \
                    f"{state}: a hole above {tile['name']} in the column at {c}: {json.dumps(tiles)}"
                edge = tile["bottom"]
        # Rail order across the sheet when no tile is Wide (a Wide tile may
        # leave a slot that a later, narrow one fills).
        if "wide" not in state:
            tops = [t["top"] for t in tiles]
            assert tops == sorted(tops), f"{state}: the cards are out of rail order: {tiles}"
