"""UX audit 2026-10-08 (docs/ux-audit-2026-10-08.md): what stayed open.

- #15: the Transfer Map's Tilt and Speed readings beside their entries.
- #17: the store prompt's Folder entry, too narrow for a path.
- Return in a photo-path entry (a `file_open` in a prompt) did nothing.
- #19: phone-width Settings rows and the take-over key.
- 2026-10-07 #5: status dots told apart by colour alone, at low contrast.

Headless Chrome through the harness in test_view_web_server.py (skipped
where node or puppeteer is absent).
"""
import pytest

import schema as sch
from controller.controller import Controller
from param import Param
from views.web.server import WebView

from test_view_web_procedure import FakeProc, _READY, _go
from test_view_web_server import FakeSetup, _browse, needs_browser


# ------------------------------------------ Return in a photo-path entry
class PhotoProc(FakeProc):
    """A prompt with a photo path beside the key that adds the record, like
    the Sample DB's New flake."""
    PARAMS = {"flake_id": Param("flake_id", "text", default="", label="Flake ID")}

    def __init__(self):
        super().__init__()
        self.flake_id = ""
        self.staged = []
        self.flakes = []

    @property
    def schema(self):
        return sch.schema(
            sch.section("Controls", sch.button("Ask", "go", args=("new_tip",))),
            sch.section("New tip",
                        sch.entry("Flake ID", "flake_id", self.PARAMS["flake_id"]),
                        sch.file_open("Add photo…", "stage_photo",
                                      extensions=("png",),
                                      placeholder="Path to a saved microscope image"),
                        sch.button("Add flake", "create_flake", role="go",
                                   inputs=("flake_id",)),
                        sch.button("Cancel", "cancel"),
                        phases=("new_tip",)))

    @property
    def state(self):
        snapshot = super().state
        snapshot["values"]["flake_id"] = self.flake_id
        return snapshot

    def stage_photo(self, path):
        self.staged.append(path)

    def create_flake(self):
        self.flakes.append(self.flake_id)
        self._phase = "setup"


@pytest.fixture
def photo_station():
    controller = Controller()
    model = PhotoProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, model
    finally:
        view.close()


@needs_browser
def test_return_in_a_prompts_photo_path_presses_its_add_photo_key(photo_station, tmp_path):
    """The prompt's Return handler swallowed the path box's own Return and
    found no key for it, so nothing happened. Return now presses the key
    that takes the path (Add photo…); the prompt stays open for the rest."""
    view, model = photo_station
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(400);
      %s
      const open = () => page.evaluate(() => Boolean(document.querySelector('.card-dialog')));
      await page.focus('.card-dialog .file-open input.path-input');
      await page.keyboard.type('/tmp/flake_100x.png');
      await page.keyboard.press('Enter');
      await sleep(1500);
      return { open: await open() };
    """ % _go("new_tip"), tmp_path)
    assert model.staged == ["/tmp/flake_100x.png"], "Return in the photo path did nothing"
    assert model.flakes == [], "Return in the photo path added the flake"
    assert out["open"] is True, out


# ------------------------------------- #15 Tilt and Speed beside their entries
class TiltProc(FakeProc):
    """The Transfer Map's shape: Tilt read in every step but Setup; in Setup
    under the entry that overrides it."""
    PARAMS = {"typed_tilt": Param("typed_tilt", "text", default="",
                                  label="Tilt for this trial (deg)"),
              "tilt_now": Param("tilt_now", "float", default=0.0, decimals=2,
                                unit="deg", label="Tilt")}

    def __init__(self):
        super().__init__()
        self.typed_tilt = ""
        self.tilt_now = None

    @property
    def schema(self):
        P = self.PARAMS
        return sch.schema(
            sch.section("Trial", sch.phased(
                sch.readonly("Tilt", "tilt_now", rail=True, param=P["tilt_now"]),
                "live", "new_tip")),
            sch.section("Start",
                        sch.entry("Tilt for this trial (deg)", "typed_tilt", P["typed_tilt"]),
                        sch.readonly("Tilt now", "tilt_now", param=P["tilt_now"],
                                     secondary=True, lead="Now:"),
                        sch.button("To live", "go", args=("live",)),
                        phases=("setup",)))


@pytest.fixture
def tilt_station():
    controller = Controller()
    model = TiltProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, model
    finally:
        view.close()


_TILT = r"""() => {
  const card = Array.from(document.querySelectorAll('#cards > .card'))
    .find((c) => c.querySelector('.card-title') && c.querySelector('.card-title').textContent === 'Fake Proc');
  const shown = (n) => Boolean(n && n.getClientRects().length);
  const entry = card.querySelector('input[name="typed_tilt"]');
  const line = card.querySelector('.row.secondary.has-lead[data-attr="tilt_now"]');
  const reading = card.querySelector('.reading[data-attr="tilt_now"]');
  const under = shown(line) && shown(entry)
    && line.getBoundingClientRect().top >= entry.getBoundingClientRect().bottom - 1
    && entry.closest('.row').contains(line);
  return { line: shown(line) ? line.innerText.replace(/\s+/g, ' ').trim() : null,
           under, reading: shown(reading) };
}"""


@needs_browser
def test_in_setup_the_tilt_reading_is_a_now_line_under_its_entry(tilt_station, tmp_path):
    view, model = tilt_station
    out = _browse(view, _READY + r"""
      await sleep(600);
      const dashboard = await page.evaluate(%s);
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(600);
      const setup = await page.evaluate(%s);
      await api('/api/run', { name: 'Fake Proc', command: 'go', inputs: {}, args: ['live'] });
      await sleep(1700);
      const live = await page.evaluate(%s);
      return { dashboard, setup, live };
    """ % (_TILT, _TILT, _TILT), tmp_path)
    for where in ("dashboard", "setup"):
        got = out[where]
        assert got["line"] and got["line"].startswith("Now:"), out
        assert "--" in got["line"], out                 # unknown is said, not hidden
        assert got["under"] is True, out
        assert got["reading"] is False, out             # not a second Tilt in Setup
    assert out["live"]["reading"] is True and out["live"]["line"] is None, out


# ------------------------------------------------ #17 the Folder entry's width
LONG_FOLDER = "/home/transfer-stage-user/transfer-stage-runs/stores/op@lab.test"


class FolderProc(FakeProc):
    """The store prompt's shape: a sentence, a Folder, a Name, the keys."""
    PARAMS = {"store_dir": Param("store_dir", "text", default="", label="Folder"),
              "store_name": Param("store_name", "text", default="t", label="Name")}

    def __init__(self):
        super().__init__()
        self.store_dir = LONG_FOLDER
        self.store_name = "transfer_map"

    @property
    def schema(self):
        return sch.schema(
            sch.section("Controls", sch.button("Ask", "go", args=("new_tip",))),
            sch.section("Where to save the store",
                        sch.readonly("Store", "status_words", role="info"),
                        sch.entry("Folder", "store_dir", self.PARAMS["store_dir"]),
                        sch.entry("Name", "store_name", self.PARAMS["store_name"]),
                        sch.button("New store", "add", role="go",
                                   inputs=("store_dir", "store_name")),
                        sch.button("Cancel", "cancel"),
                        phases=("new_tip",)))

    @property
    def state(self):
        snapshot = super().state
        snapshot["values"].update({"store_dir": self.store_dir,
                                   "store_name": self.store_name,
                                   "status_words": "Not chosen."})
        return snapshot


@pytest.fixture
def folder_station():
    controller = Controller()
    model = FolderProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, model
    finally:
        view.close()


_FOLDER = r"""() => {
  const input = document.querySelector('.card-dialog input[name="store_dir"]');
  const box = input.closest('.dialog-box');
  const style = getComputedStyle(box);
  const inner = box.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  return { input: input.getBoundingClientRect().width, inner,
           fits: input.scrollWidth <= input.clientWidth + 1,
           sideways: document.documentElement.scrollWidth - window.innerWidth,
           value: input.value };
}"""


@needs_browser
def test_a_prompts_folder_entry_runs_the_box_and_shows_a_whole_path(folder_station, tmp_path):
    """The store prompt's Folder showed "/home/transfer-sta" in a 9rem box.
    A text entry in a prompt runs the prompt's width, which is wide enough
    for a store path; at phone width it still fits the screen."""
    view, _model = folder_station
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(400);
      %s
      await until(() => Boolean(document.querySelector('.card-dialog input[name="store_dir"]')));
      await sleep(600);
      const wide = await page.evaluate(%s);
      await page.setViewport({ width: 390, height: 844 });
      await sleep(800);
      const phone = await page.evaluate(%s);
      return { wide, phone };
    """ % (_go("new_tip"), _FOLDER, _FOLDER), tmp_path)
    wide, phone = out["wide"], out["phone"]
    assert wide["value"] == LONG_FOLDER, out
    assert wide["fits"], out                       # 144 px and cut before
    assert wide["input"] >= wide["inner"] - 2, out
    assert phone["input"] >= phone["inner"] - 2, out
    assert phone["sideways"] <= 0, out


# ------------------------------------------------ #19 phone-width leftovers
LONG_PORT = "Stepper Probe — ACM90"


class RowsSetup(FakeSetup):
    """A launched Settings row in the real Setup's shape: Port, Hard reset,
    Gamepad, Status."""

    def __init__(self):
        super().__init__()
        self.ports = ["SIM", LONG_PORT]
        self.alpha_port = LONG_PORT
        self.alpha_gamepad = "None"
        self.alpha_status = "Detected: Stepper Probe"

    @property
    def schema(self):
        return sch.schema(
            sch.section("Devices", sch.button("Refresh", "scan")),
            sch.section(
                "Stepper Probe",
                sch.dropdown("Port", "alpha_port", "set_port", "port_options"),
                sch.button("Hard reset", "hard_reset_alpha"),
                sch.dropdown("Gamepad", "alpha_gamepad", "set_port", "pad_options"),
                sch.readonly("Status", "alpha_status"),
                layout="row"))

    def pad_options(self):
        return ["None"]

    def hard_reset_alpha(self):
        return None

    @property
    def state(self):
        snapshot = super().state
        snapshot.update({"is_launched": True,
                         "rows": [{"key": "alpha", "name": "Stepper Probe"}]})
        return snapshot


@pytest.fixture
def rows_station():
    controller = Controller()
    model = FakeProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, RowsSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view
    finally:
        view.close()


#: Each Port and Gamepad key in the drawer: its width, and whether the
#: choice it shows fits in it (the text measured in the select's own font).
_KEYS = r"""() => {
  const ctx = document.createElement('canvas').getContext('2d');
  return Array.from(document.querySelectorAll('#drawer-body select'))
    .filter((s) => s.getClientRects().length)
    .map((s) => {
      const style = getComputedStyle(s);
      ctx.font = style.fontWeight + ' ' + style.fontSize + ' ' + style.fontFamily;
      const text = (s.options[s.selectedIndex] || {}).text || '';
      const room = s.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const key = s.closest('.select-key') || s;
      return { name: s.getAttribute('aria-label'), text, fits: ctx.measureText(text).width <= room + 1,
               height: Math.round(key.getBoundingClientRect().height) };
    });
}"""


@needs_browser
def test_a_settings_row_at_phone_width_shows_its_whole_port(rows_station, tmp_path):
    """At 390 px the Port key shared its line with Hard reset and its
    caption and showed "Stepper Probe — AC" (or "Stepper")."""
    out = _browse(rows_station, _READY + r"""
      await page.setViewport({ width: 390, height: 844 });
      await sleep(500);
      if (!(await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open')))) {
        await page.evaluate(() => document.getElementById('setup-link').click());
      }
      await until(() => document.querySelectorAll('#drawer-body select').length >= 2);
      await sleep(900);
      return { keys: await page.evaluate(%s),
               reset: await page.evaluate(() => {
                 const b = Array.from(document.querySelectorAll('#drawer-body button'))
                   .find((x) => /hard reset/i.test(x.textContent) && x.getClientRects().length);
                 return b ? Math.round(b.getBoundingClientRect().height) : null; }),
               sideways: await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
               drawer: await page.evaluate(() => { const d = document.getElementById('drawer-body');
                                                   return d.scrollWidth - d.clientWidth; }) };
    """ % _KEYS, tmp_path)
    port = [k for k in out["keys"] if k["name"] and k["name"].startswith("Port")]
    assert port and port[0]["text"] == LONG_PORT, out
    assert port[0]["fits"], out
    assert all(k["height"] >= 36 for k in out["keys"]), out
    assert out["reset"] is not None and out["reset"] >= 36, out
    assert out["sideways"] <= 0 and out["drawer"] <= 0, out


@pytest.fixture
def two_window_station():
    controller = Controller()
    model = FakeProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view
    finally:
        view.close()


@needs_browser
def test_the_take_over_key_is_as_wide_as_its_words(two_window_station, tmp_path):
    """The notice's "Take over here" was stretched across the notice (450 px
    on a desktop, the screen's width on a phone); every other key is as
    wide as its words. And on a phone the Stop is above, not "on the left"."""
    out = _browse(two_window_station, r"""
      await sleep(2500);
      const second = await browser.newPage();
      const read = () => second.evaluate(() => {
        const gate = document.getElementById('elsewhere-gate');
        const key = document.getElementById('take-over');
        if (!gate || gate.hidden || !key) return null;
        const r = key.getBoundingClientRect();
        const ink = document.createRange();
        ink.selectNodeContents(key);
        return { width: Math.round(r.width), height: Math.round(r.height),
                 words: Math.round(ink.getBoundingClientRect().width),
                 sideways: document.documentElement.scrollWidth - window.innerWidth,
                 text: gate.innerText };
      });
      await second.setViewport({ width: 390, height: 844 });
      await second.goto(BASE + '/', { waitUntil: 'load' });
      const phone = await when(read, 6000);
      await second.setViewport({ width: 1400, height: 900 });
      await sleep(400);
      return { phone, wide: await read() };
    """, tmp_path)
    for size in ("phone", "wide"):
        got = out[size]
        assert got, out
        assert got["width"] <= got["words"] + 80, out    # 358 / 448 before
        assert got["height"] >= 36, out
        assert got["sideways"] <= 0, out
    assert "on the left" not in out["phone"]["text"], out


# ------------------------------------- 2026-10-07 #5 the rail's status dots
#: Every rail dot: its fill, the background it sits on (its ancestors'
#: backgrounds composited), their WCAG contrast, and its shape.
_DOT_READ = r"""() => {
  const rgba = (text) => {
    const m = String(text).match(/rgba?\(([^)]+)\)/);
    if (!m) return [0, 0, 0, 0];
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const over = (top, under) => {
    const a = top[3];
    return [0, 1, 2].map((i) => top[i] * a + under[i] * (1 - a)).concat([1]);
  };
  const backdrop = (node) => {
    const layers = [];
    for (let n = node.parentElement; n; n = n.parentElement) {
      const c = rgba(getComputedStyle(n).backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] >= 1) break;
    }
    let colour = [255, 255, 255, 1];
    for (const layer of layers.reverse()) colour = over(layer, colour);
    return colour;
  };
  const lum = (c) => {
    const ch = c.slice(0, 3).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  return Array.from(document.querySelectorAll('#model-nav .model-link[data-model] .nav-dot')).map((d) => {
    const s = getComputedStyle(d);
    const fill = rgba(s.backgroundColor);
    return { name: d.parentNode.dataset.model, label: d.getAttribute('aria-label'),
             error: d.classList.contains('is-error'), on: d.classList.contains('is-on'),
             contrast: Math.round(ratio(fill, backdrop(d)) * 100) / 100,
             shape: [s.borderRadius, s.transform, s.clipPath].join('|') };
  });
}"""


@needs_browser
def test_the_rail_dots_meet_3_to_1_and_an_error_differs_by_shape(two_window_station, tmp_path):
    """The dots differed by hue alone (grey/blue 1.13:1, grey/red 1.20:1):
    every one now clears 3:1 against what it sits on (the rail, the current
    entry's highlight), and an error dot is a different shape, so it reads
    without colour."""
    out = _browse(two_window_station, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(900);
      const idle = await page.evaluate(%s);
      await page.click('#full-stop');
      await until(() => document.querySelector('#model-nav .nav-dot.is-error'));
      await sleep(400);
      const stopped = await page.evaluate(%s);
      return { idle, stopped };
    """ % (_DOT_READ, _DOT_READ), tmp_path)
    idle, stopped = out["idle"], out["stopped"]
    assert idle and stopped, out
    assert not any(d["error"] for d in idle), out
    assert all(d["error"] for d in stopped), out
    for dot in idle + stopped:
        assert dot["contrast"] >= 3.0, dot
    assert {d["shape"] for d in idle}.isdisjoint({d["shape"] for d in stopped}), out
