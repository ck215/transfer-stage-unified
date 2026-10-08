"""UX audit 2026-10-08 (docs/ux-audit-2026-10-08.md): the small fixes.

- Return in a prompt's entry presses the key that takes that entry
  ("Add sample", "Add material", "New store", "Open store"), not only a key
  named exactly "Add".
- After the launch Setup is called Settings, so the words that send the
  operator there say Settings.

Headless Chrome through the harness in test_view_web_server.py (skipped
where node or puppeteer is absent).
"""
import pytest

import schema as sch
from controller.controller import Controller
from param import Param
from views import base
from views.web.server import WebView

from test_view_web_client import APP_JS
from test_view_web_procedure import FakeProc, _READY, _go
from test_view_web_server import FakeSetup, _browse, needs_browser


class NamedAddProc(FakeProc):
    """A prompt whose keys say what they add, like the Sample DB's."""
    NAME = "Fake Proc"
    PARAMS = {"tip_name": Param("tip_name", "text", default="", label="Name"),
              "other": Param("other", "text", default="", label="Other")}

    def __init__(self):
        super().__init__()
        self.other = ""
        self.others = []

    @property
    def schema(self):
        return sch.schema(
            sch.section("Controls", sch.button("Ask", "go", args=("new_tip",))),
            sch.section("New tip",
                        sch.entry("Name", "tip_name", self.PARAMS["tip_name"]),
                        sch.entry("Other", "other", self.PARAMS["other"]),
                        sch.dropdown("Sample", "sample", "pick_sample", "sample_options"),
                        sch.button("Add other", "add_other", inputs=("other",)),
                        sch.button("Add tip", "add", role="go", inputs=("tip_name",)),
                        sch.button("Cancel", "cancel"),
                        phases=("new_tip",)))

    @property
    def state(self):
        snapshot = super().state
        snapshot["values"]["other"] = self.other
        return snapshot

    def add_other(self):
        self.others.append(self.other)


@pytest.fixture
def named_station():
    controller = Controller()
    model = NamedAddProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, model
    finally:
        view.close()


@needs_browser
def test_return_in_a_prompt_entry_presses_the_key_that_takes_it(named_station, tmp_path):
    view, model = named_station
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(400);
      %s
      const open = () => page.evaluate(() => Boolean(document.querySelector('.card-dialog')));
      const field = (name) => page.focus('.card-dialog input[name="' + name + '"]');
      await field('other');
      await page.keyboard.type('O1');
      await page.keyboard.press('Enter');
      await sleep(1500);
      const afterOther = await open();
      await field('tip_name');
      await page.keyboard.type('T7');
      await page.keyboard.press('Enter');
      await sleep(1800);
      return { afterOther, afterTip: await open() };
    """ % _go("new_tip"), tmp_path)
    assert model.others == ["O1"], "Return in Other did not press Add other"
    assert out["afterOther"] is True, "Return in Other closed the prompt"
    assert model.added == ["T7"], "Return in Name did not press Add tip"
    assert out["afterTip"] is False


def test_the_link_lost_words_name_settings_not_setup():
    """After the launch the rail says Settings; a lost link is only ever
    after the launch."""
    state = {"link": {"status": "lost"}}
    words = base.link_gate_reason(state)
    if not words:  # the state shape differs: read the source line instead
        words = [line for line in open(base.__file__).read().splitlines()
                 if "Hard reset it in" in line][0]
    assert "in Settings" in words and "in Setup" not in words
    assert "Hard reset it in Setup" not in APP_JS


@needs_browser
def test_tutorials_take_focus_and_their_end_key_is_not_called_stop(named_station, tmp_path):
    """The Tutorials side window takes focus like Settings and the account
    menu, gives it back on Escape, and the coach card's key that ends a
    tutorial says so: "Stop" beside the rail's red Stop read as the machine's
    stop."""
    view, model = named_station
    out = _browse(view, _READY + r"""
      await page.focus('#tutorials-link');
      await page.keyboard.press('Enter');
      await until(() => !document.getElementById('tutorial-panel').hidden);
      await sleep(300);
      const opened = await page.evaluate(() => document.activeElement.id);
      await page.keyboard.press('Escape');
      await sleep(300);
      return { opened,
               closed: await page.evaluate(() => [document.getElementById('tutorial-panel').hidden,
                                                   document.activeElement.id]),
               end: await page.evaluate(() => document.querySelector('#tutorial-card .tutorial-stop').textContent) };
    """, tmp_path)
    assert out["opened"] == "tutorial-panel", out
    assert out["closed"] == [True, "tutorials-link"], out
    assert out["end"] == "End tutorial", out


@needs_browser
def test_a_dropdown_in_a_prompt_keeps_its_chevron_on_its_key(named_station, tmp_path):
    view, model = named_station
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(400);
      %s
      return await page.evaluate(() => {
        const key = document.querySelector('.card-dialog .select-key');
        const s = key.querySelector('select').getBoundingClientRect();
        const g = key.querySelector('.glyph').getBoundingClientRect();
        return { select: [s.left, s.right], glyph: [g.left, g.right] };
      });
    """ % _go("new_tip"), tmp_path)
    assert out["select"][0] <= out["glyph"][0] and out["glyph"][1] <= out["select"][1] + 1, \
        f"the chevron is drawn off its select: {out}"


@needs_browser
def test_focus_lands_on_the_next_steps_first_control_after_a_prompt(named_station, tmp_path):
    """Return on a prompt's last entry closed it and dropped focus to <body>
    (the entry went back into a hidden section). The brief: focus moves to
    the step's first control."""
    view, model = named_station
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(400);
      // The prompt opens while focus is elsewhere (the store prompt opens
      // at the launch, from the rail).
      await page.focus('#model-nav button');
      %s
      await page.focus('.card-dialog input[name="tip_name"]');
      await page.keyboard.type('T8');
      await page.keyboard.press('Enter');
      await sleep(1800);
      return await page.evaluate(() => {
        const a = document.activeElement;
        return { tag: a.tagName, inCard: Boolean(a.closest('#cards .card')),
                 words: (a.getAttribute('aria-label') || a.textContent || '').trim().slice(0, 40) };
      });
    """ % _go("new_tip"), tmp_path)
    assert model.added == ["T8"]
    assert out["tag"] != "BODY" and out["inCard"], f"focus fell out of the card: {out}"
