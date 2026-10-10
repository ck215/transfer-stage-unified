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
