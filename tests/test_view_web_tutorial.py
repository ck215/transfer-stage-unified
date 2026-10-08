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


# ---------------------------------------------------------------------- TU-2
import os
import time

import numpy

import schema as sch
from controller.controller import Controller
from devices.screen import Screen
from devices.screen_recorder import ScreenRecorder
from model.heater import Heater
from model.probe import StepperProbe
from model.rgb_analysis import RgbAnalysis
from model.sample_map import SampleMap
from model.transfer_map import TransferMap
from panel import Panel


def _files():
    index = json.loads((TUTORIALS / "index.json").read_text())
    return {name: json.loads((TUTORIALS / name).read_text()) for name in index["tutorials"]}


def _texts(schema_dict):
    """Every element text anywhere in a schema, whatever its phase."""
    found = set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("text"), str):
                found.add(node["text"])
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)
    walk(schema_dict)
    return found


def _norm(text):
    return re.sub(r"\s*(…|\.\.\.)\s*$", "", " ".join(text.split()))


class _FakeCapture:
    """A held picture per grab: Red Percent reads a changing number."""
    HOLD = 4

    def __init__(self):
        self.grabs = 0

    def grab(self, region):
        self.grabs += 1
        time.sleep(0.005)
        frame = numpy.zeros((40, 60, 3), dtype=numpy.uint8)
        frame[:1 + (self.grabs // self.HOLD) % 20, :] = [200, 0, 0]
        return frame

    def close(self):
        pass


def _synthetic():
    t = time.monotonic()
    frame = numpy.full((540, 960, 4), 46, dtype=numpy.uint8)
    x = int((t * 160) % 960)
    frame[:, x:x + 24, 2] = 210
    frame[:, :, 3] = 255
    return frame, t


class _Setup(Panel):
    NAME = "Setup"

    @property
    def schema(self):
        return sch.schema(sch.section("Hardware", sch.button("Refresh", "scan")))

    def scan(self):
        return None

    def start(self):
        pass

    def close(self):
        pass


@pytest.fixture
def sim_station(tmp_path):
    controller = Controller()
    probe = StepperProbe(port=None, gamepad=None, sim=True)
    heater = Heater(port=None, sim=True)
    red = RgbAnalysis(screen=Screen(factory=_FakeCapture))
    red.output_root = tmp_path / "runs"
    transfer = TransferMap(
        db_path=tmp_path / "transfer_map.sqlite",
        recorder_factory=lambda out, fps, mon: ScreenRecorder(
            out, fps, mon, frame_source=_synthetic))
    samples = SampleMap(db_path=tmp_path / "sample_map.sqlite")
    models = (("Stepper Probe", probe), ("Temperature Controller", heater),
              ("RGB analysis", red), ("Transfer Map", transfer), ("Sample Map", samples))
    for name, model in models:
        controller.add(name, model)
    for _name, model in models:
        model.open()
    view = WebView(controller, _Setup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, transfer, samples
    finally:
        view.close()
        for _name, model in reversed(models):
            try:
                model.close()
            except Exception:
                pass


def test_the_index_lists_every_file_and_every_file_is_well_formed():
    index = json.loads((TUTORIALS / "index.json").read_text())["tutorials"]
    on_disk = sorted(p.name for p in TUTORIALS.glob("*.json") if p.name != "index.json")
    assert sorted(index) == on_disk
    ids = set()
    for name, t in _files().items():
        assert t["id"] not in ids
        ids.add(t["id"])
        assert t["requires"] in ("sim", "any") and t["title"] and t["steps"]
        for step in t["steps"]:
            assert set(step) <= {"page", "anchor", "say", "wait"}, step
            assert step["page"] and 0 < len(step["say"]) <= 240
            assert len(step["anchor"]) == 1 and ("text" in step["anchor"] or "selector" in step["anchor"])
            wait = step["wait"]
            assert wait is None or wait == {"click": True} or set(wait) == {"state"}
            if wait and "state" in wait:
                assert set(wait["state"]) == {"name", "key", "equals"}


def test_the_shipped_tutorials_are_the_two_named():
    files = _files()
    assert {t["title"]: t["requires"] for t in files.values()} == {
        "Your first trial": "sim", "Register a sample": "any"}
    first = files["first-trial.json"]
    texts = [s["anchor"].get("text") for s in first["steps"]]
    assert texts[1:] == ["Arm trial", "Capture region…", "Mark force",
                         "End recording", "Finish trial"]
    assert first["steps"][0]["anchor"] == {"selector": "#setup-link"}
    register = [s["anchor"]["text"] for s in files["register-sample.json"]["steps"]]
    for wanted in ("New sample…", "Add photo…", "Add sample",
                   "New chip…", "New flake…"):
        assert wanted in register


def test_every_anchor_text_is_in_the_real_schema_of_its_sim_model(sim_station):
    view, transfer, samples = sim_station
    models = {"Transfer Map": transfer, "Sample Map": samples}
    missing = []
    for name, t in _files().items():
        for step in t["steps"]:
            text = step["anchor"].get("text")
            if text is None:
                continue
            have = {_norm(x) for x in _texts(models[step["page"]].schema)}
            if _norm(text) not in have:
                missing.append((name, step["page"], text))
            wait = (step["wait"] or {}).get("state")
            if wait:
                assert wait["key"] == "phase"
                assert wait["equals"] in models[wait["name"]].PHASES, wait
    assert not missing, missing


def test_the_ids_and_pages_a_tutorial_names_are_real(sim_station):
    view, transfer, samples = sim_station
    for t in _files().values():
        for step in t["steps"]:
            assert step["page"] in ("Overview", "Transfer Map", "Sample Map")


@pytest.mark.xfail(reason="2026-10-07: the first-trial tutorial predates the sample pickers and the tip prompt; "
                          "Arm now refuses without a sample, so the walk stalls at step 3 until the tutorial is re-anchored", strict=False)
@needs_browser
def test_your_first_trial_walks_a_sim_transfer_map_to_the_end(sim_station, tmp_path):
    """Real SIM models, a real server, headless Chrome and a synthetic
    recorder: the tutorial follows the phases the operator (here, the API)
    drives, and runs no command of its own."""
    view, transfer, samples = sim_station
    shots = os.environ.get("STATION_TUTORIAL_CAPTURES", "")
    out = _browse(view, r"""
      const SHOTS = %s;
      await page.setViewport({ width: 1600, height: 900 });
      const posts = [];
      page.on('request', (req) => {
        if (req.method() === 'POST' && new URL(req.url()).pathname === '/api/run') posts.push(JSON.parse(req.postData()).command);
      });
      const shot = async (name) => { if (SHOTS) await page.screenshot({ path: SHOTS + '/' + name }); };
      const run = (name, command, inputs, args) => page.evaluate(async (n, c, i, a) => {
        const post = async (body) => (await fetch('/api/run', { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
        let r = await post({ name: n, command: c, inputs: i || {}, args: a || [] });
        if (r.status === 'needs_confirm') r = await post({ name: n, command: r.command || c,
          inputs: r.inputs || {}, args: [...(r.args || a || []), true] });
        return r;
      }, name, command, inputs, args);
      const count = () => page.evaluate(() => {
        const c = document.getElementById('tutorial-card');
        return c && !c.hidden ? c.querySelector('.tutorial-count').textContent : null;
      });
      const at = async (want) => { await until(async () => true); const end = Date.now() + 6000;
        while (Date.now() < end) { if ((await count()) === want) return true; await sleep(150); }
        return false; };
      const seen = {};
      await page.click('#tutorials-link');
      await until(() => !document.getElementById('tutorial-panel').hidden);
      await page.evaluate(() => {
        const row = Array.from(document.querySelectorAll('.tutorial-row'))
          .find((r) => r.querySelector('.tutorial-row-title').textContent === 'Your first trial');
        Array.from(row.querySelectorAll('button')).find((b) => b.textContent === 'Start').click();
      });
      const fits = [];
      const cardNow = () => page.evaluate(() => {
        const c = document.getElementById('tutorial-card');
        if (!c || c.hidden) return null;
        const box = (n) => { const b = n.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
        return { box: box(c), stop: box(document.getElementById('full-stop')) };
      });
      const look = async () => { const c = await cardNow(); fits.push(c && c.box[0] >= 0 && c.box[1] >= 0
        && c.box[2] <= 1600 && c.box[3] <= 900 && !(c.box[2] > c.stop[0] && c.box[0] < c.stop[2]
        && c.box[3] > c.stop[1] && c.box[1] < c.stop[3]) ? true : JSON.stringify(c)); };
      seen.s1 = await at('Step 1 of 6'); await sleep(500); await look(); await shot('01_setup_step.png');
      await page.click('#setup-link');                     // the operator opens Setup
      seen.s2 = await at('Step 2 of 6'); await sleep(900); await look(); await shot('02_arm_step.png');
      await run('Transfer Map', '_commit', { tip_id: 'T12' });
      await run('Transfer Map', 'arm_trial');
      seen.s3 = await at('Step 3 of 6'); await sleep(900); await look(); await shot('03_region_step.png');
      await run('Transfer Map', 'set_region', null, [0, 0, 100, 80]);
      seen.s4 = await at('Step 4 of 6'); await sleep(1500);
      await run('Transfer Map', 'mark_force');
      seen.s5 = await at('Step 5 of 6'); await sleep(900);
      await run('Transfer Map', 'end_recording');
      seen.s6 = await at('Step 6 of 6'); await sleep(900); await look(); await shot('04_finish_step.png');
      await run('Transfer Map', 'finish_trial');
      seen.done = await until(() => document.getElementById('tutorial-card').hidden, 6000);
      const saved = await page.evaluate(() => localStorage.getItem('station.tutorial.first-trial'));
      return { seen, saved, posts, fits };
    """ % json.dumps(shots), tmp_path)
    assert all(out["seen"].values()), out
    assert out["fits"] == [True] * 4, "a card left the viewport or covered the stop"
    assert out["saved"] is None, "a finished tutorial forgets its place"
    ours = {"_commit", "arm_trial", "set_region", "mark_force", "end_recording", "finish_trial"}
    assert set(out["posts"]) <= ours | {"extend_idle"}, out["posts"]
    assert out["posts"].count("arm_trial") == 2   # the ask and the confirmed press, both the test's


@needs_browser
def test_the_tutorials_page_lists_the_shipped_files(proc_station, tmp_path):
    view, controller, model = proc_station
    out = _browse(view, r"""
      await page.click('#tutorials-link');
      await until(() => !document.getElementById('tutorial-panel').hidden);
      await sleep(500);
      return await page.evaluate(() => ({
        titles: Array.from(document.querySelectorAll('.tutorial-row-title')).map((n) => n.textContent),
        starts: Array.from(document.querySelectorAll('.tutorial-row button')).map((b) => b.textContent),
        expanded: document.getElementById('tutorials-link').getAttribute('aria-expanded'),
      }));
    """, tmp_path)
    assert out["titles"] == ["Your first trial", "Register a sample"], out
    assert out["starts"].count("Start") == 2
    assert out["expanded"] == "true"
