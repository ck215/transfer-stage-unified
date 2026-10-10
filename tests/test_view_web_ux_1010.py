"""UX round 2026-10-10 (docs/ux-audit-2026-10-07.md and -08.md): the rail on
short and phone screens, the device page measure, the ghost Hard reset keys
and the tutorial card, the status-dot legend, Setup's Update and guest rows.

Headless Chrome through the harness in test_view_web_server.py (skipped where
node or puppeteer is absent).
"""
import json

import pytest

import schema as sch
from controller.controller import Controller
from views.web.server import WebView

from test_view_web_procedure import FakeProc, _READY
from test_view_web_procedure import proc_station  # noqa: F401
from test_view_web_server import FakeSetup, _browse, needs_browser
from test_view_web_tutorial import _FAKE, _run


class XyzSetup(FakeSetup):
    """Setup's real row shapes before the launch: four cells, and the XYZ
    Stage's six (Port, Hard reset, Port Y, Port Z, Gamepad, Status)."""

    def __init__(self):
        super().__init__()
        self.a_port = self.x_port = self.x_port_y = self.x_port_z = "SIM"
        self.a_gamepad = self.x_gamepad = "None"
        self.a_status = self.x_status = "Not connected"

    @property
    def schema(self):
        return sch.schema(
            sch.section("Devices", sch.button("Refresh", "scan")),
            sch.section("Stepper Probe",
                        sch.dropdown("Port", "a_port", "set_port", "port_options"),
                        sch.button("Hard reset", "hard_reset_a"),
                        sch.dropdown("Gamepad", "a_gamepad", "set_port", "pad_options"),
                        sch.readonly("Status", "a_status"), layout="row"),
            sch.section("XYZ Stage",
                        sch.dropdown("Port", "x_port", "set_port", "port_options"),
                        sch.button("Hard reset", "hard_reset_x"),
                        sch.dropdown("Port Y", "x_port_y", "set_port", "port_options"),
                        sch.dropdown("Port Z", "x_port_z", "set_port", "port_options"),
                        sch.dropdown("Gamepad", "x_gamepad", "set_port", "pad_options"),
                        sch.readonly("Status", "x_status"), layout="row"))

    def pad_options(self):
        return ["None"]

    def hard_reset_a(self):
        return None

    hard_reset_x = hard_reset_a

    @property
    def state(self):
        snapshot = super().state
        snapshot.update({"is_launched": False, "rows": [
            {"key": "a", "name": "Stepper Probe"}, {"key": "x", "name": "XYZ Stage"}]})
        return snapshot


@pytest.fixture
def xyz_station():
    controller = Controller()
    controller.add("Fake Proc", FakeProc(), {"kind": "Fake Proc"})
    view = WebView(controller, XyzSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view
    finally:
        view.close()


@needs_browser
def test_setup_before_launch_has_four_columns_and_no_ghost_hard_reset(xyz_station, tmp_path):
    """A row with Port Y and Port Z made a six-column table in a five-track
    grid: the captions headed the wrong cells and the rows wrapped. The
    ports stack under Port, with their captions, and no Hard reset key is
    drawn before there is anything to reset."""
    out = _browse(xyz_station, _READY + r"""
      await page.setViewport({ width: 1366, height: 768 });
      if (!(await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open')))) {
        await page.evaluate(() => document.getElementById('setup-link').click());
      }
      await until(() => document.querySelectorAll('#drawer-body select').length >= 5);
      await sleep(900);
      return await page.evaluate(() => {
        const box = (n) => { const b = n.getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; };
        const rows = Array.from(document.querySelectorAll('#drawer-body .section-row:not(.table-head)'));
        const row = (t) => rows.find((r) => r.dataset.section === t);
        const status = (t) => box(row(t).querySelector('[data-attr$="_status"]'));
        const sel = (t) => Array.from(row(t).querySelectorAll('select')).map((s) => box(s));
        const reset = Array.from(document.querySelectorAll('#drawer-body button'))
          .filter((b) => /hard reset/i.test(b.textContent))
          .map((b) => { const r = b.getBoundingClientRect(); return r.width > 2 && getComputedStyle(b).visibility !== 'hidden'; });
        return { heads: Array.from(document.querySelectorAll('.table-head .head-cell')).map((c) => c.textContent),
                 xs: status('XYZ Stage'), as: status('Stepper Probe'), xsel: sel('XYZ Stage'),
                 asel: sel('Stepper Probe'), reset,
                 labels: Array.from(row('XYZ Stage').querySelectorAll('label.label')).map((l) => [l.textContent, l.getBoundingClientRect().width > 8]) };
      });
    """, tmp_path)
    assert out["heads"] == ["Port", "Hard reset", "Gamepad", "Status"], out
    assert not any(out["reset"]), out
    assert abs(out["xs"][0] - out["as"][0]) < 2, out            # one Status column
    assert abs(out["xsel"][0][0] - out["xsel"][1][0]) < 2, out    # ports stacked
    assert out["xsel"][1][1] > out["xsel"][0][1] and out["xsel"][2][1] > out["xsel"][1][1], out
    assert abs(out["xsel"][3][0] - out["asel"][1][0]) < 2, out    # one Gamepad column
    assert [l for l in out["labels"] if l[0].startswith("Port") and not l[1]] == [], out


@needs_browser
def test_a_tutorial_step_with_no_anchor_sits_under_the_head_not_over_its_state(proc_station, tmp_path):
    """The card went to the top right, where a device page's entry head says
    its state ("Connection lost")."""
    view, _controller, _model = proc_station
    free = dict(_FAKE, steps=[{"page": "Fake Proc", "say": "Look at the page.", "wait": {"click": True}}])
    out = _run(view, r"""
      await openPanel(); await startRow('Fake run');
      await until(() => document.getElementById('tutorial-card') && !document.getElementById('tutorial-card').hidden);
      await sleep(600);
      return await page.evaluate(() => {
        const box = (n) => { const b = n.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
        const head = document.querySelector('.sheet .card.is-opened > .card-head');
        return { card: box(document.getElementById('tutorial-card')), head: head && box(head) };
      });
    """, tmp_path, {"a.json": free})
    assert out["head"], out
    assert out["card"][1] >= out["head"][3], out


# ------------------------------------------- Setup's Update row and guest rows
class GuestSetup(FakeSetup):
    """A guest's Setup: the Transfer Map row says "sign in to use" and the
    Update row carries the check-off sentence with its variable name."""

    def __init__(self):
        super().__init__()
        self.tm_port = "On"
        self.tm_status = "sign in to use"
        self.update_status = ("The update check is off for this run "
                              "(STATION_NO_UPDATE_CHECK). Press Check again to check now.")
        self.guest = True

    @property
    def schema(self):
        return sch.schema(
            sch.section("Devices", sch.button("Refresh", "scan")),
            sch.section("Transfer Map",
                        sch.dropdown("Run", "tm_port", "set_port", "tm_options"),
                        sch.readonly("Status", "tm_status"), layout="row"),
            sch.section("Update", sch.readonly("Updates", "update_status")))

    def tm_options(self):
        return ["On", "SIM"]

    @property
    def state(self):
        snapshot = super().state
        status = "sign in to use" if self.guest else "on"
        snapshot.update({"is_launched": False, "rows": [
            {"key": "tm", "name": "Transfer Map", "status": status, "needs_port": False}]})
        snapshot["values"]["tm_status"] = status
        return snapshot


@pytest.fixture
def guest_station():
    controller = Controller()
    controller.add("Fake Proc", FakeProc(), {"kind": "Fake Proc"})
    setup = GuestSetup()
    view = WebView(controller, setup, port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, setup
    finally:
        view.close()


@needs_browser
def test_a_guests_locked_row_offers_no_key_and_update_names_no_variable(guest_station, tmp_path):
    view, setup = guest_station
    out = _browse(view, _READY + r"""
      if (!(await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open')))) {
        await page.evaluate(() => document.getElementById('setup-link').click());
      }
      await until(() => document.querySelectorAll('#drawer-body select').length >= 1);
      await sleep(900);
      const read = () => page.evaluate(() => {
        const s = document.querySelector('#drawer-body select[name="tm_port"]');
        const u = document.querySelector('#drawer-body [data-attr="update_status"] .value');
        return { disabled: s.disabled, title: s.title, update: u && u.textContent };
      });
      const locked = await read();
      return { locked };
    """, tmp_path)
    assert out["locked"]["disabled"] is True, out
    assert "Sign in" in out["locked"]["title"], out
    assert "STATION_" not in out["locked"]["update"], out
    assert out["locked"]["update"].startswith("The update check is off for this run"), out


# ------------------------------------------------------------- status-dot legend
@needs_browser
def test_the_rail_says_what_its_dots_mean(xyz_station, tmp_path):
    """Enabled, Disabled and Error: the legend's marks are the dots' own
    classes and its words are the dots' accessible names."""
    out = _browse(xyz_station, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(500);
      return await page.evaluate(() => {
        const legend = document.getElementById('nav-legend');
        const dots = Array.from(document.querySelectorAll('#model-nav .nav-dot'))
          .map((d) => d.getAttribute('aria-label'));
        return { shown: legend.getClientRects().length > 0,
                 words: Array.from(legend.querySelectorAll('.nav-legend-item')).map((i) => i.textContent.trim()),
                 marks: Array.from(legend.querySelectorAll('.nav-dot')).map((d) => d.className.replace('nav-dot', '').trim()),
                 dots, top: legend.getBoundingClientRect().top,
                 navBottom: document.getElementById('model-nav').getBoundingClientRect().bottom };
      });
    """, tmp_path)
    assert out["shown"], out
    assert out["words"] == ["Enabled", "Disabled", "Error"], out
    assert out["marks"] == ["is-on", "", "is-error"], out
    assert out["top"] >= out["navBottom"] - 1, out
    assert out["dots"] and out["dots"][0] in ("Enabled", "Disabled") or out["dots"][0].startswith("Error"), out


# ----------------------------------------------- the rail on short and phone screens
_RAIL = r"""() => {
  const box = (n) => { const b = n.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, h: b.height, w: b.width }; };
  const rail = document.querySelector('.rail');
  const visible = (n) => n.getClientRects().length > 0;
  const foot = Array.from(document.querySelectorAll('.rail-foot .rail-control')).map((n) => [n.id, box(n)]);
  return { vh: window.innerHeight, rail: box(rail), scrolls: rail.scrollHeight > rail.clientHeight + 1,
           foot, stop: box(document.getElementById('full-stop')),
           toggle: visible(document.getElementById('nav-toggle')),
           nav: visible(document.getElementById('model-nav')),
           toggleWords: document.getElementById('nav-toggle').textContent.trim(),
           expanded: document.getElementById('nav-toggle').getAttribute('aria-expanded') };
}"""


@needs_browser
def test_the_rail_keeps_its_foot_in_view_on_a_1366_by_768_screen(xyz_station, tmp_path):
    """Stop line, a lost device and the caution on the rail pushed Tutorials,
    Setup and Quit below the fold; the rail scrolled. Tutorials and Setup now
    share a line with Quit under them, on a tighter plate."""
    out = _browse(xyz_station, _READY + r"""
      await page.setViewport({ width: 1366, height: 768 });
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(500);
      await page.evaluate(() => {
        for (const id of ['rail-latched', 'energized-line']) {
          const n = document.getElementById(id);
          n.textContent = 'Stepper Probe, DC Probe and Chuck Positioner did not confirm the stop.';
          n.hidden = false;
        }
        const a = document.getElementById('rail-alert');
        const line = document.createElement('div'); line.className = 'rail-alert-line';
        line.textContent = 'The Rotator did not answer. Check the cable.';
        a.appendChild(line); a.hidden = false;
      });
      await sleep(500);
      return { short: await page.evaluate(%s),
               ids: await page.evaluate(() => Array.from(document.querySelectorAll('.rail-foot .rail-control')).map((n) => n.getBoundingClientRect().left)) };
    """ % _RAIL, tmp_path)["short"]
    foot = dict(out["foot"])
    assert foot["tutorials-link"]["top"] == foot["setup-link"]["top"], out        # one line
    assert foot["quit-link"]["top"] > foot["setup-link"]["bottom"] - 1, out        # Quit under
    assert max(f[1]["bottom"] for f in out["foot"]) <= out["vh"], out
    assert not out["scrolls"], out
    assert out["stop"]["bottom"] <= out["vh"], out


@needs_browser
def test_a_phones_page_list_folds_behind_one_key_and_the_bar_stays_short(xyz_station, tmp_path):
    out = _browse(xyz_station, _READY + r"""
      await page.setViewport({ width: 390, height: 844 });
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(600);
      const closed = await page.evaluate(%s);
      await page.click('#nav-toggle');
      await sleep(300);
      const open = await page.evaluate(%s);
      await page.evaluate(() => document.querySelector('#model-nav .overview-link').click());
      await sleep(500);
      const after = await page.evaluate(%s);
      return { closed, open, after };
    """ % (_RAIL, _RAIL, _RAIL), tmp_path)
    closed, opened, after = out["closed"], out["open"], out["after"]
    assert closed["toggle"] and not closed["nav"], out
    assert closed["toggleWords"] == "Pages: Fake Proc", out
    assert closed["rail"]["h"] <= closed["vh"] / 3 + 4, out                        # about a third
    assert closed["expanded"] == "false" and opened["expanded"] == "true", out
    assert opened["nav"] and opened["rail"]["h"] > closed["rail"]["h"], out
    assert not after["nav"] and after["toggleWords"] == "Pages: Dashboard", out    # choosing closes it


@needs_browser
def test_the_page_list_toggle_is_not_drawn_on_a_desktop(xyz_station, tmp_path):
    out = _browse(xyz_station, _READY + r"""
      await page.setViewport({ width: 1400, height: 900 });
      await sleep(400);
      return await page.evaluate(%s);
    """ % _RAIL, tmp_path)
    assert not out["toggle"] and out["nav"], out


@needs_browser
def test_a_device_page_keeps_a_measure_of_75rem_on_a_wide_screen(xyz_station, tmp_path):
    out = _browse(xyz_station, _READY + r"""
      await page.setViewport({ width: 1920, height: 1080 });
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(600);
      return await page.evaluate(() => {
        const c = document.querySelector('.sheet.is-device > .card.is-opened');
        return { w: c.getBoundingClientRect().width, rem: parseFloat(getComputedStyle(document.documentElement).fontSize) };
      });
    """, tmp_path)
    assert out["w"] <= 75 * out["rem"] + 1, out
    assert out["w"] >= 60 * out["rem"], out
