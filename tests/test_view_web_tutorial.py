"""Tutorials on the Web console (rb-tutorial, 2026-10-07).

TU-1: the runtime (tutorial.js), its page and its sidebar entry. Static
checks on the source, and headless Chrome (the harness in
test_view_web_server.py) against a fake procedure with tutorial files
served through request interception.
TU-2: the two shipped tutorials, their anchors against the real schemas, and
a walk of "Your first trial" on a real SIM Transfer Map.
"""
import json
import re
from pathlib import Path

import pytest

from views.web.server import WebView

from test_view_web_client import APP_JS, INDEX, MARKUP_SINKS, STYLES, STATIC
from test_view_web_procedure import FakeProc  # noqa: F401  (the fixture's model)
from test_view_web_procedure import proc_station  # noqa: F401
from test_view_web_server import _browse, needs_browser

TUTORIAL_JS = (STATIC / "tutorial.js").read_text()
TUTORIALS = STATIC / "tutorials"
CODE_JS = re.sub(r"^\s*//.*$", "", re.sub(r"/\*.*?\*/", "", TUTORIAL_JS, flags=re.S),
                 flags=re.M)


# ------------------------------------------------------------------ TU-1 static
@pytest.mark.parametrize("sink", MARKUP_SINKS)
def test_tutorial_js_assigns_no_markup(sink):
    assert sink not in CODE_JS, f"tutorial.js uses {sink}: every string must land in textContent"


def test_tutorial_js_never_runs_a_command():
    """The operator presses every control. The runtime asks for files and
    for /api/state, with GET, and never names the run route or a verb."""
    assert "/api/run" not in CODE_JS
    assert "apiPost" not in CODE_JS
    assert not re.search(r"method\s*:", CODE_JS), "a fetch with a method is not a GET"
    urls = re.findall(r"fetch\(\s*([^,)]+)", CODE_JS)
    assert urls, "the runtime reads /api/state with fetch"
    for url in urls:
        assert url.strip() in ("'/api/state'", "url"), f"unexpected request: {url}"
    # the only things it presses itself are a rail link and Setup's Close
    assert len(re.findall(r"\.click\(\)", CODE_JS)) == 2
    assert "link.click()" in CODE_JS and "away.click()" in CODE_JS


def test_tutorial_js_polls_no_faster_than_the_page():
    page = int(re.search(r"const STATE_POLL_MS = (\d+)", APP_JS).group(1))
    mine = int(re.search(r"const POLL_MS = (\d+)", TUTORIAL_JS).group(1))
    assert mine >= page


def test_tutorial_js_names_no_colour_and_keeps_to_the_dom():
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", CODE_JS)
    # app.js is not the runtime's to touch, and does not know about it
    assert "tutorial" not in APP_JS.lower()
    assert "STORE_PREFIX" in TUTORIAL_JS and "try {" in TUTORIAL_JS  # storage is wrapped


def test_tutorial_js_refuses_real_hardware_with_the_sentence():
    assert ("This tutorial runs on simulated hardware. "
            "Set every port to SIM in Setup first.") in re.sub(
                r"'\s*\+\s*'", "", re.sub(r"\s+", " ", TUTORIAL_JS).replace("' + '", ""))


def test_the_page_carries_one_script_and_one_entry():
    assert INDEX.count('src="/tutorial.js"') == 1
    assert INDEX.count('id="tutorials-link"') == 1
    assert INDEX.index('src="/app.js"') < INDEX.index('src="/tutorial.js"')
    assert re.search(r'id="tutorials-link"[^>]*>Tutorials<', INDEX)


def test_the_stylesheet_block_for_tutorials_is_last_and_uses_tokens():
    head = "/* -- tutorial "
    assert STYLES.count(head) == 1
    block = STYLES[STYLES.index(head):]
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", block)
    # every rule in the block is about the tutorial, but for the rail-foot wrap
    selectors = re.findall(r"^([^{}\n/][^{}]*)\{", block, flags=re.M)
    for sel in selectors:
        assert "tutorial" in sel or sel.strip() == ".rail-foot", sel


# ---------------------------------------------------------------- TU-1 headless
_FAKE = {
    "id": "fake-run", "title": "Fake run", "requires": "any",
    "steps": [
        {"page": "Fake Proc", "anchor": {"text": "To live"},
         "say": "Go live.", "wait": {"state": {"name": "Fake Proc", "key": "phase", "equals": "live"}}},
        {"page": "Fake Proc", "anchor": {"text": "To setup"},
         "say": "Back to setup.", "wait": {"click": True}},
        {"page": "Fake Proc", "anchor": {"selector": "#full-stop"},
         "say": "The stop is the stop.", "wait": None},
    ],
}
_SIM_ONLY = dict(_FAKE, id="fake-sim", title="Fake sim", requires="sim")

#: Serve the fake files, and count every POST to the run route.
_INTERCEPT = r"""
  const served = %(served)s;
  const posts = [];
  await page.setRequestInterception(true);
  page.on('request', (req) => {
    const url = new URL(req.url());
    if (req.method() === 'POST' && url.pathname === '/api/run') posts.push(req.postData());
    if (url.pathname === '/tutorials/index.json') {
      return req.respond({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ tutorials: Object.keys(served) }) });
    }
    if (url.pathname.startsWith('/tutorials/')) {
      const name = decodeURIComponent(url.pathname.slice('/tutorials/'.length));
      if (served[name]) return req.respond({ status: 200, contentType: 'application/json',
        body: JSON.stringify(served[name]) });
    }
    return req.continue();
  });
  await page.reload({ waitUntil: 'load' });
  await card();
  await sleep(600);
  const openPanel = async () => {
    await page.click('#tutorials-link');
    await until(() => !document.getElementById('tutorial-panel').hidden);
  };
  const startRow = async (title) => page.evaluate((t) => {
    const row = Array.from(document.querySelectorAll('.tutorial-row'))
      .find((r) => r.querySelector('.tutorial-row-title').textContent === t);
    Array.from(row.querySelectorAll('button')).find((b) => b.textContent === 'Start').click();
  }, title);
  const cardNow = () => page.evaluate(() => {
    const c = document.getElementById('tutorial-card');
    if (!c || c.hidden) return null;
    const r = document.querySelector('.tutorial-ring');
    const box = (n) => { const b = n.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
    return { count: c.querySelector('.tutorial-count').textContent,
             say: c.querySelector('.tutorial-say').textContent,
             box: box(c), ring: r.hidden ? null : box(r), stop: box(document.getElementById('full-stop')),
             next: c.querySelector('.tutorial-next').textContent,
             backOff: c.querySelector('.tutorial-back').disabled };
  });
"""


def _run(view, body, tmp_path, served):
    return _browse(view, (_INTERCEPT % {"served": json.dumps(served)}) + body, tmp_path)


def _overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


@needs_browser
def test_a_tutorial_walks_by_state_and_by_click_and_runs_nothing(proc_station, tmp_path):
    view, controller, model = proc_station
    out = _run(view, r"""
      await openPanel(); await startRow('Fake run');
      await until(() => document.getElementById('tutorial-card') && !document.getElementById('tutorial-card').hidden);
      await sleep(400);
      const one = await cardNow();
      // the operator (this test) presses the model into live
      await api('/api/run', { name: 'Fake Proc', command: 'go', inputs: {}, args: ['live'] });
      await until(() => document.querySelector('.tutorial-count').textContent === 'Step 2 of 3', 4000);
      const two = await cardNow();
      // a press on the anchored control advances; it is the page's own press
      await page.evaluate(() => Array.from(document.querySelectorAll('#cards button'))
        .find((b) => b.textContent.trim() === 'To setup').click());
      await until(() => document.querySelector('.tutorial-count').textContent === 'Step 3 of 3', 4000);
      const three = await cardNow();
      await page.click('.tutorial-back');
      await sleep(300);
      const back = await cardNow();
      await page.keyboard.press('Escape');
      await sleep(300);
      const gone = await cardNow();
      const saved = await page.evaluate(() => localStorage.getItem('station.tutorial.fake-run'));
      return { one, two, three, back, gone, saved, posts };
    """, tmp_path, {"a.json": _FAKE})
    assert out["one"]["count"] == "Step 1 of 3" and out["one"]["backOff"] is True
    assert out["two"]["count"] == "Step 2 of 3"
    assert out["three"]["count"] == "Step 3 of 3" and out["three"]["next"] == "Done"
    assert out["back"]["count"] == "Step 2 of 3", "Back must not be undone by a held wait"
    assert out["gone"] is None, "Escape stops the tutorial"
    assert json.loads(out["saved"]) == {"step": 1}
    # exactly two run posts: the test's go, and the page's own press of To setup
    assert len(out["posts"]) == 2, out["posts"]
    for step in ("one", "two", "three"):
        shown = out[step]
        assert not _overlap(shown["box"], shown["stop"]), f"{step}: the card covers the stop"
    assert out["one"]["ring"] is not None


@needs_browser
@pytest.mark.parametrize("status, refused", [("verified", True), ("unverified", True),
                                             ("simulated", False)])
def test_a_sim_tutorial_refuses_real_hardware_only(proc_station, tmp_path, status, refused):
    """`hardware_devices` names every link a model owns, SIM ones included;
    the device's status says whether it is real."""
    view, controller, model = proc_station
    original = type(model).state.fget

    def with_hardware(self):
        snapshot = original(self)
        snapshot["hardware_devices"] = ["SerialDevice"]
        snapshot["devices"] = {"SerialDevice": status}
        return snapshot
    type(model).state = property(with_hardware)
    try:
        out = _run(view, r"""
          await openPanel(); await startRow('Fake sim');
          await sleep(900);
          return { note: await page.evaluate(() => document.querySelector('.tutorial-note').textContent),
                   card: await cardNow() };
        """, tmp_path, {"a.json": _SIM_ONLY})
    finally:
        type(model).state = property(original)
    if refused:
        assert out["note"] == ("This tutorial runs on simulated hardware. "
                               "Set every port to SIM in Setup first.")
        assert out["card"] is None
    else:
        assert out["note"] == "" and out["card"]["count"] == "Step 1 of 3"


@needs_browser
def test_a_sim_tutorial_starts_without_hardware(proc_station, tmp_path):
    view, controller, model = proc_station
    out = _run(view, r"""
      await openPanel(); await startRow('Fake sim');
      await sleep(900);
      return { card: await cardNow(), panelHidden: await page.evaluate(
        () => document.getElementById('tutorial-panel').hidden) };
    """, tmp_path, {"a.json": _SIM_ONLY})
    assert out["card"]["count"] == "Step 1 of 3"
    assert out["panelHidden"] is True

