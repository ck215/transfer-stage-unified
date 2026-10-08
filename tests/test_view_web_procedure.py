"""WEB round 2 (2026-10-07): the procedure strip (W2-1), prompt phases as a
card-level dialog (W2-2), cascading dropdowns that stay fresh (W2-3), and no
model-name or frame-count literal in the client (W2-4).

Fake models only; the real ones land in parallel. Headless Chrome through the
harness in test_view_web_server.py (skipped where node or puppeteer is absent).
"""
import json
import re

import pytest

import schema as sch
from controller.controller import Controller
from param import Param
from views.web.server import WebView

from test_view_web_client import (APP_JS, CODE, INDEX, MARKUP_SINKS, NODE,
                                  STYLES, _method, _node_value)
from test_view_web_dashboard import _PHASE_READ, _Plain
from test_view_web_server import FakeSetup, _browse, needs_browser


class FakeProc(_Plain):
    """A procedure with a prompt step, a cascading Sample -> Chip pair whose
    options depend on the pick above (and the Sample's on the step), and the
    next-step text and analysis health a real model publishes."""
    NAME = "Fake Proc"
    PHASES = ("setup", "live", "new_tip")
    PARAMS = {"tip_name": Param("tip_name", "text", default="", label="Name")}
    CHIPS = {"A": ["A1", "A2"], "B": ["B1"]}

    def __init__(self):
        super().__init__()
        self._phase = "setup"
        self.tip_name = ""
        self.sample = "A"
        self.chip = ""
        self.step_text = "Set a region."
        self.health = ""
        self.added = []
        self.cancelled = 0

    @property
    def phase(self):
        return self._phase

    def sample_options(self):
        return ["A", "B"] + (["P"] if self._phase == "live" else [])

    def chip_options(self):
        return self.CHIPS.get(self.sample, [])

    @property
    def schema(self):
        return sch.schema(
            sch.section("Controls",
                        sch.button("To setup", "go", args=("setup",)),
                        sch.button("To live", "go", args=("live",)),
                        sch.button("Ask", "go", args=("new_tip",))),
            sch.section("Pick",
                        sch.dropdown("Sample", "sample", "pick_sample", "sample_options"),
                        sch.dropdown("Chip", "chip", "pick_chip", "chip_options")),
            sch.section("New tip",
                        sch.entry("Name", "tip_name", self.PARAMS["tip_name"]),
                        sch.button("Add", "add"), sch.button("Cancel", "cancel"),
                        phases=("new_tip",)))

    @property
    def state(self):
        snapshot = super().state
        snapshot["values"].update({"sample": self.sample, "chip": self.chip,
                                   "tip_name": self.tip_name})
        snapshot["step_text"] = self.step_text
        snapshot["analysis_health"] = self.health
        return snapshot

    def go(self, phase):
        self._phase = phase
        return phase

    def pick_sample(self, value):
        self.sample = value

    def pick_chip(self, value):
        self.chip = value

    def add(self):
        self.added.append(self.tip_name)
        self._phase = "setup"

    def cancel(self):
        self.cancelled += 1
        self._phase = "setup"


@pytest.fixture
def proc_station():
    controller = Controller()
    model = FakeProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, model
    finally:
        view.close()


_READY = _PHASE_READ.split("const drawn")[0]
_RUN = "api('/api/run', { name: 'Fake Proc', command: %s, inputs: {}, args: [%s] })"


def _go(phase):
    return "await " + _RUN % ("'go'", json.dumps(phase)) + ";\n await sleep(1700);"


# ---------------------------------------------------------------- W2-1 static
@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("expression, wanted", [
    ("stepWord('new_tip')", "New tip"), ("stepWord('live')", "Live"),
    ("isPromptStep('new_sample')", True), ("isPromptStep('setup')", False),
    ("litStep(['a','b','c'], 'b')", 1), ("litStep(['a','b'], '')", -1),
    ("litStep(['a','b'], 'zzz')", 0), ("litStep([], 'a')", -1),
    ("healthIsQuiet('settled')", True), ("healthIsQuiet('Stalled')", False),
    ("healthIsQuiet('no region')", False),
])
def test_w21_strip_rules(expression, wanted):
    assert _node_value(expression) == wanted


def test_w21_strip_is_drawn_from_state_with_no_fetch_and_no_markup_sink():
    for name in ("buildProcedure", "applyProcedure"):
        body = _method(name)
        assert "apiGet" not in body and "apiPost" not in body and "fetch(" not in body
        assert "/api/schema" not in body
        for sink in MARKUP_SINKS:
            assert sink not in body
    refresh = _method("refresh")
    assert "applyProcedure(state)" in refresh
    apply = _method("applyProcedure")
    for key in ("state.phases", "state.phase", "state.step_text", "state.analysis_health"):
        assert key in apply
    # Written only on a change.
    assert apply.count("seen.") >= 8 and "!==" in apply


# ---------------------------------------------------------------- W2-1 browser
@needs_browser
def test_w21_strip_segments_lit_step_text_and_health(proc_station, tmp_path):
    view, controller, model = proc_station
    model.health = "settled"
    out = _browse(view, _READY + r"""
      const fetched = [];
      page.on('request', (r) => { if (r.url().includes('/api/schema')) fetched.push(1); });
      const read = () => page.evaluate(() => {
        const card = document.querySelector('#cards .card');
        const steps = Array.from(card.querySelectorAll('.proc-step'));
        const pill = card.querySelector('.proc-health');
        return {
          steps: steps.map((s) => s.textContent),
          lit: steps.filter((s) => s.classList.contains('is-current')).map((s) => s.dataset.step),
          prompt: steps.filter((s) => s.classList.contains('is-prompt')).map((s) => s.dataset.step),
          text: card.querySelector('.proc-text').textContent,
          pill: pill.hidden ? '' : pill.textContent, warn: pill.classList.contains('is-warn'),
          litFill: getComputedStyle(card.querySelector('.proc-step.is-current')).backgroundColor,
          quietFill: getComputedStyle(steps[1]).backgroundColor,
          promptStyle: getComputedStyle(steps[2]).borderTopStyle,
          overview: document.getElementById('cards').classList.contains('is-overview'),
          size: parseFloat(getComputedStyle(steps[0]).fontSize),
          numbered: getComputedStyle(steps[0], '::before').content,
        };
      });
      const overview = await read();
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(500);
      const page1 = await read();
      %s
      const live = await read();
      return { overview, page1, live, fetched: fetched.length };
    """ % _go("live"), tmp_path)
    assert out["fetched"] == 0
    one = out["page1"]
    assert one["steps"] == ["Setup", "Live", "New tip"] or len(one["steps"]) == 3, one
    assert one["lit"] == ["setup"] and one["prompt"] == ["new_tip"], one
    assert one["litFill"] != one["quietFill"]
    assert one["promptStyle"] == "dotted"
    assert one["text"] == "Set a region." and one["pill"] == "Settled" and not one["warn"]
    assert out["live"]["lit"] == ["live"]
    # Overview: the compact form, same strip.
    ov = out["overview"]
    assert ov["overview"] and ov["lit"] == ["setup"] and len(ov["steps"]) == 3
    assert ov["size"] < one["size"] and ov["numbered"] in ("none", "normal")


@needs_browser
def test_w21_text_and_health_update_and_unsettled_is_a_warning(proc_station, tmp_path):
    view, controller, model = proc_station
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(400);
      const read = () => page.evaluate(() => {
        const c = document.querySelector('#cards .card');
        const p = c.querySelector('.proc-health');
        return { text: c.querySelector('.proc-text').textContent, hidden: p.hidden,
                 word: p.textContent, warn: p.classList.contains('is-warn') };
      });
      const before = await read();
      await api('/api/run', { name: 'Fake Proc', command: 'go', inputs: {}, args: ['live'] });
      return { before };
    """, tmp_path)
    assert out["before"]["hidden"] is True
    model.step_text, model.health = "Press Mark.", "stalled"
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(1800);
      return await page.evaluate(() => {
        const c = document.querySelector('#cards .card');
        const p = c.querySelector('.proc-health');
        return { text: c.querySelector('.proc-text').textContent, hidden: p.hidden,
                 word: p.textContent, warn: p.classList.contains('is-warn') };
      });
    """, tmp_path)
    assert out == {"text": "Press Mark.", "hidden": False, "word": "Stalled", "warn": True}


# ---------------------------------------------------------------- W2-2
def test_w22_dialog_rules_are_static():
    apply_phase = _method("applyPhase")
    assert "isPromptStep(phase)" in apply_phase and "openDialog(" in apply_phase
    assert "closeDialog()" in apply_phase
    key = _method("dialogKey")
    assert "'Escape'" in key and "'cancel'" in key and "'Enter'" in key and "'add'" in key
    for sink in MARKUP_SINKS:
        assert sink not in _method("openDialog")
    assert re.search(r"\.card-dialog\s*\{[^}]*position:\s*absolute", STYLES)
    assert "apiGet" not in _method("openDialog")


@needs_browser
def test_w22_prompt_phase_is_a_dialog_over_the_card(proc_station, tmp_path):
    view, controller, model = proc_station
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(400);
      %s
      const look = () => page.evaluate(() => {
        const card = document.querySelector('#cards .card');
        const d = card.querySelector('.card-dialog');
        if (!d) return { open: false, home: Boolean(card.querySelector('.card-body .section-title')) };
        const r = d.getBoundingClientRect(), c = card.getBoundingClientRect();
        const s = document.getElementById('full-stop').getBoundingClientRect();
        const hit = document.elementFromPoint(s.left + s.width / 2, s.top + s.height / 2);
        const a = document.activeElement;
        return { open: true, covers: Math.abs(r.width - c.width) < 6 && Math.abs(r.height - c.height) < 6,
                 title: d.querySelector('.section-title').textContent,
                 stopFree: Boolean(hit && hit.closest('#full-stop')),
                 focus: a.tagName + ':' + a.name, buttons: Array.from(d.querySelectorAll('button')).map((b) => b.textContent.trim()) };
      });
      const open = await look();
      await page.keyboard.type('T9');
      await page.keyboard.press('Enter');
      await sleep(1800);
      const afterAdd = await look();
      %s
      const again = await look();
      await page.keyboard.press('Escape');
      await sleep(1800);
      return { open, afterAdd, again, closed: await look() };
    """ % (_go("new_tip"), _go("new_tip")), tmp_path)
    o = out["open"]
    assert o["open"] and o["covers"] and o["stopFree"], o
    assert o["title"] == "New tip" and o["focus"] == "INPUT:tip_name", o
    assert "Add" in o["buttons"] and "Cancel" in o["buttons"]
    assert out["afterAdd"]["open"] is False and model.added == ["T9"]
    assert out["again"]["open"] is True
    assert out["closed"]["open"] is False and model.cancelled == 1


@needs_browser
def test_w22_a_prompt_tile_keeps_its_size_and_shows_its_keys(proc_station, tmp_path):
    """UX audit 2026-10-08: on the Dashboard a tile with a prompt open grew a
    grid row per frame without end (sizeTiles measured the scrim stretched
    over the tile), and on a device page a prompt taller than 22rem hid its
    last keys inside the card. The tile settles, and the prompt's box shows
    every key without scrolling, on both pages."""
    view, controller, model = proc_station
    out = _browse(view, _READY + r"""
      %s
      // A tall prompt: a long note in the dialog, like the store prompts.
      await page.evaluate(() => {
        const box = document.querySelector('.dialog-box');
        const p = document.createElement('p');
        p.style.height = '30rem';
        box.querySelector('.section').prepend(p);
      });
      const read = () => page.evaluate(() => {
        const card = document.querySelector('#cards > .card');
        const box = card.querySelector('.dialog-box');
        return { rows: card.style.getPropertyValue('--tile-rows'),
                 height: Math.round(card.getBoundingClientRect().height),
                 fits: box.scrollHeight <= box.clientHeight + 1 };
      });
      await sleep(600);
      const first = await read();
      await sleep(1500);
      const later = await read();
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(600);
      return { first, later, page: await read() };
    """ % _go("new_tip"), tmp_path)
    assert out["first"]["rows"] == out["later"]["rows"], f"the prompt tile keeps growing: {out}"
    assert out["first"]["fits"] and out["later"]["fits"], out
    assert out["page"]["fits"], f"the prompt's keys are cut off on the device page: {out}"


# ---------------------------------------------------------------- W2-3
def test_w23_trigger_rule_is_static():
    watch = _method("watchOptions")
    assert "group.seen" in watch and "queueOptions(" in watch
    assert "setTimeout" in _method("queueOptions") and "OPTIONS_DEBOUNCE_MS" in CODE
    assert "queueOptions(" in _method("applyPhase")
    assert "optionsTurn" in _method("loadOptions")
    # No timer polls options.
    assert "setInterval" not in _method("watchOptions") + _method("queueOptions")


@needs_browser
def test_w23_cascading_dropdowns_refetch_once_per_change(proc_station, tmp_path):
    view, controller, model = proc_station
    model.chip = "A2"
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(1500);
      const asked = [];
      page.on('request', (r) => { if (r.url().includes('/api/options')) asked.push(JSON.parse(r.postData()).command); });
      const read = () => page.evaluate(() => {
        const c = document.querySelector('#cards .card');
        const sel = (n) => c.querySelector('select[name=' + n + ']');
        return { chips: Array.from(sel('chip').options).map((o) => o.value),
                 chip: sel('chip').value, samples: Array.from(sel('sample').options).map((o) => o.value) };
      });
      const start = await read();
      await api('/api/run', { name: 'Fake Proc', command: 'pick_sample', inputs: {}, args: ['B'] });
      await sleep(1800);
      const picked = await read();
      const afterPick = asked.slice();
      await sleep(1500);
      const quiet = asked.length === afterPick.length;
      %s
      return { start, picked, afterPick, quiet, live: await read(), all: asked };
    """ % _go("live"), tmp_path)
    assert out["start"]["chip"] == "A2" and "A1" in out["start"]["chips"]
    # Sample changed: Chip re-read once; its held value A2 is not among [B1].
    assert out["picked"]["chips"] == ["", "B1"] and out["picked"]["chip"] == ""
    assert out["afterPick"].count("chip_options") == 1, out
    assert out["quiet"], "options are polled"
    # The step changed: Sample re-read and now offers P.
    assert "P" in out["live"]["samples"]


# ---------------------------------------------------------------- W2-4
@pytest.mark.parametrize("text", [APP_JS, STYLES, INDEX])
def test_w24_no_model_name_or_frame_count_literal(text):
    low = text.lower()
    assert "red percent" not in low and "red_percent" not in low and "red %" not in low
    assert not re.search(r"n_frames|frame_count|frames?\s+count|\bframes\b", low)
