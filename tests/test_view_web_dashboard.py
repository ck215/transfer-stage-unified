"""The Web view's two items of the one-dashboard round (2026-09-28).

F: a hidden tab is not a gone browser. The page's heartbeat stopped the
moment the tab was hidden, so switching to the microscope window on a
single-screen bench PC FULL STOPped the station fifteen seconds later. The
heartbeat now runs in a dedicated Worker whose timer the page's hidden-tab
throttling does not reach; it stops on `pagehide` and at shutdown only.

D: Red Percent is drawn on the Transfer Map's page (`host` in state,
docs/rebuild/MODEL_CONTRACT.md step 1; handoff/brief-dashboard-contract.md):
no page or Overview entry of its own, a group after the host's tier 1,
its disclosures after the host's, every control bound to its own name.

Driven in headless Chrome through the harness in test_view_web_server.py
(skipped where node or puppeteer is absent), plus static reads of app.js.
"""
import re
import threading
import time

import pytest

import schema as sch
from controller.controller import Controller
from events import events
from panel import Panel
from views.web.server import WebView

from test_view_web_client import APP_JS, CODE, _body
from test_view_web_server import (FakeProbe, FakeSetup, _browse, _post,
                                  needs_browser, station)  # noqa: F401 (fixture)


# ==========================================================================
# F: the hidden-tab heartbeat
# ==========================================================================
def _count_beats(view):
    """Wrap `view.beat` so the test sees every check-in and what it said."""
    beats = []
    original = view.beat

    def counted(*args, **kwargs):
        beats.append((time.monotonic(), kwargs.get("hidden")))
        return original(*args, **kwargs)

    view.beat = counted
    return beats


#: Make the page believe it is hidden, the way a switch to another window
#: does, and say so the way the browser does.
_HIDE = r"""
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
"""

_SHOW = r"""
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
"""


@needs_browser
def test_f_a_hidden_tab_keeps_checking_in(station, tmp_path):
    """The owner's focus bug: hidden, the page used to send nothing, and the
    watchdog read that as a gone browser. Hidden for five seconds (two and a
    half heartbeat periods) the server must still hear from it, and hear
    that it is hidden."""
    view, controller, probe = station
    beats = _count_beats(view)

    out = _browse(view, _HIDE + r"""
      await sleep(5200);
      return true;
    """, tmp_path)
    assert out is True
    # The hide happened after the prelude's load; everything in the last
    # five seconds of the scenario was sent while hidden.
    hidden_beats = [b for b in beats if b[1] is True]
    assert len(hidden_beats) >= 2, (
        f"a hidden tab stopped its heartbeat: {beats}")
    assert view.heartbeat_age is not None and view.heartbeat_age < 3.0


@needs_browser
def test_f_a_hidden_tab_does_not_full_stop_an_energized_station(station, tmp_path):
    """The bug end to end, on shrunk thresholds (the rules are the real
    watchdog's): energized, the tab hidden for longer than STOP_SECONDS.
    Before the fix every model was latched; a hidden tab is not a gone
    browser."""
    view, controller, probe = station
    view.WARN_SECONDS, view.STOP_SECONDS = 2.8, 4.0
    probe.is_energized = True
    out = _browse(view, _HIDE + r"""
      await sleep(6500);
      return (await api('/api/state')).is_estopped;
    """, tmp_path)
    assert out is False and probe.is_estopped is False, (
        "switching to another window stopped the station")


@needs_browser
def test_f_the_page_says_when_it_is_shown_again(station, tmp_path):
    view, controller, probe = station
    beats = _count_beats(view)
    _browse(view, _HIDE + r"""
      await sleep(2600);
    """ + _SHOW + r"""
      await sleep(2600);
      return true;
    """, tmp_path)
    said = [b[1] for b in beats]
    assert True in said, said
    assert said[-1] is False, f"the last beat still said hidden: {said}"


@needs_browser
def test_f_pagehide_and_stop_still_silence_the_heartbeat(station, tmp_path):
    """A closed tab is a gone browser: pagehide ends the heartbeat, and the
    watchdog's rules then apply unchanged."""
    view, controller, probe = station
    beats = _count_beats(view)
    out = _browse(view, r"""
      await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
      await sleep(300);
      const t = Date.now();
      await sleep(4500);
      return t;
    """, tmp_path)
    assert out
    # Allow for one beat in flight at the pagehide.
    late = [b for b in beats if b[0] > time.monotonic() - 3.5]
    assert late == [], f"the heartbeat outlived pagehide: {beats}"


@needs_browser
def test_f_quit_ends_the_worker_heartbeat(station, tmp_path):
    """After Quit the page asserts nothing, heartbeat included (G2). Counted
    at the server: the worker's requests are its own thread's, which a
    count of the page's requests may not see."""
    view, controller, probe = station
    beats = _count_beats(view)
    marks = {}

    def mark_quit(original=view.request_quit):
        marks["quit"] = time.monotonic()
        return original()

    view.request_quit = mark_quit
    _browse(view, r"""
      await page.click('#quit-link');
      await until(() => !document.getElementById('confirm-modal').hidden);
      await page.click('#confirm-yes');
      await until(() => document.getElementById('connection').textContent.startsWith('The station has shut down'));
      await sleep(4500);
      return true;
    """, tmp_path)
    assert "quit" in marks
    after = [b for b in beats if b[0] > marks["quit"] + 0.5]
    assert beats and after == [], f"the heartbeat outlived the Quit: {beats}"


def test_f_watch_visibility_no_longer_stops_the_heartbeat():
    watch = _body(r"watchVisibility\(\) \{(.*?)\n  \}")
    assert "visibilitychange" in watch
    assert "stopHeartbeat" not in watch.split("pagehide")[0], (
        "a hidden tab is not a gone browser")
    assert "pagehide" in watch
    send = _body(r"sendHeartbeat\(\) \{(.*?)\n  \}")
    assert "document.hidden) return" not in send


def test_f_the_heartbeat_worker_does_its_own_bounded_fetch():
    block = re.search(r"const HEARTBEAT_WORKER_SOURCE = \[(.*?)\]\.join", APP_JS, re.S)
    assert block, "app.js has no inline heartbeat worker"
    source = block.group(1)
    assert "fetch(" in source and "'POST'" in source
    assert "setInterval(" in source
    assert "AbortController" in source and ".abort()" in source, (
        "the worker's fetch must be bounded like every other (WEB-22)")
    assert "hidden" in source
    assert "new Worker(" in CODE and "new Blob(" in CODE


def test_f_the_server_logs_hidden_and_shown_once_per_change(monkeypatch):
    lines = []
    real = events.debug

    def debug(title, message, **kwargs):
        lines.append(title)
        return real(title, message, **kwargs)

    monkeypatch.setattr(events, "debug", debug)
    view = WebView(Controller(), object(), port=0, open_browser=False)
    view.beat(hidden=False)
    view.beat(hidden=True)
    view.beat(hidden=True)
    view.beat()                    # an old client says nothing about it
    view.beat(hidden=False)
    view.beat(hidden=False)
    said = [t for t in lines if t in ("Browser Hidden", "Browser Shown")]
    assert said == ["Browser Shown", "Browser Hidden", "Browser Shown"], said


def test_f_the_heartbeat_route_passes_hidden_through(station):
    view, _, _ = station
    beats = _count_beats(view)
    assert _post(view, "/api/heartbeat", {"hidden": True})[1]["status"] == "ok"
    assert _post(view, "/api/heartbeat", {})[1]["status"] == "ok"
    assert _post(view, "/api/heartbeat", {"hidden": "yes"})[1]["status"] == "ok"
    assert [b[1] for b in beats] == [True, None, None]


# ==========================================================================
# D: a hosted model drawn on its host's page
# ==========================================================================
from result import Refused  # noqa: E402


class _Plain(Panel):
    """What the Controller and the page need of a model, and no more."""

    def __init__(self):
        super().__init__()
        self.is_estopped = False
        self.is_active = False
        self.stop_confirmed = None
        self.mode = "idle"
        self.pressed = []
        self.refuse = ""

    def open(self):
        pass

    def close(self):
        pass

    def estop(self):
        self.is_estopped = True
        self.stop_confirmed = True
        return True

    def clear_estop(self, confirmed=False):
        self.is_estopped = False
        self.stop_confirmed = None

    def on_model_added(self, name, model):
        pass

    def on_model_removed(self, name, model):
        pass

    @property
    def mode_name(self):
        return self.mode

    @property
    def state(self):
        snapshot = super().state
        snapshot.update({"age": 0.0, "is_estopped": self.is_estopped,
                         "is_active": False, "devices": {},
                         "stop_confirmed": self.stop_confirmed})
        # What a real model's stop switch (model_attr is_estopped) publishes.
        snapshot["values"]["is_estopped"] = self.is_estopped
        return snapshot

    def press(self):
        if self.refuse:
            raise Refused(self.refuse)
        self.pressed.append(self.NAME)
        return "pressed"


class FakeMap(_Plain):
    NAME = "Fake Map"

    def __init__(self):
        super().__init__()
        self.cells = "12"
        self.link = "ok"

    @property
    def schema(self):
        return sch.schema(
            sch.section("Map", sch.readonly("Cells", "cells", rail=True),
                        sch.button("Sweep", "press")),
            sch.section("Session", sch.readonly("Link", "link"),
                        tier=2, disclosure="Configure Fake Map"),
            sch.section("Diagnostics", sch.readonly("Link", "link"), tier=3,
                        disclosure="Diagnostics"),
        )


class FakeRed(_Plain):
    NAME = "Fake Red"
    HOST = "Fake Map"

    def __init__(self):
        super().__init__()
        self.red = "4.5"
        self.rows = "7"
        self.region = None

    @staticmethod
    def _region():
        region = sch.region_select("Region", "set_region", model_attr="region")
        region["data_command"] = "screen_image"
        return region

    def set_region(self, region=None):
        self.region = region
        return region

    def screen_image(self):
        return {"image": b"\x89PNG\r\n\x1a\nscreen", "width": 100,
                "height": 100, "left": 0, "top": 0}

    @property
    def schema(self):
        return sch.schema(
            sch.section("Red", sch.readonly("Red", "red", rail=True),
                        sch.button("Poke", "press"), self._region()),
            sch.section("Rows", sch.readonly("Rows", "rows"),
                        tier=2, disclosure="Fake Red details"),
            sch.section("Diagnostics", sch.readonly("Rows", "rows"), tier=3,
                        disclosure="Diagnostics"),
        )


@pytest.fixture
def hosted_station():
    controller = Controller()
    made = {"Fake Map": FakeMap, "Fake Red": FakeRed}
    controller.factory = lambda config: made[config["kind"]]()
    host, guest = FakeMap(), FakeRed()
    controller.add("Fake Map", host, {"kind": "Fake Map"})
    controller.add("Fake Red", guest, {"kind": "Fake Red"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, host, guest
    finally:
        view.close()


#: The page with the drawer shut, and the reads every D scenario makes.
_HOSTED = r"""
  if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
    await page.click('#drawer-close');
    await sleep(300);
  }
  await until(() => document.querySelectorAll('#cards .card-title').length >= 2);
  await sleep(400);
  const titleOf = (c) => (c.querySelector('.card-title') || {}).textContent;
  const read = () => page.evaluate(() => {
    const titleOf = (c) => (c.querySelector('.card-title') || {}).textContent;
    const top = Array.from(document.querySelectorAll('#cards > .card'));
    const all = Array.from(document.querySelectorAll('#cards .card'));
    const map = top.find((c) => titleOf(c) === 'Fake Map');
    const red = all.find((c) => titleOf(c) === 'Fake Red');
    const shown = (n) => Boolean(n && n.getClientRects().length);
    const order = (a, b) => Boolean(a && b
      && (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING));
    const disc = (text) => Array.from(document.querySelectorAll('#cards .disclosure'))
      .find((d) => d.textContent.trim() === text);
    return {
      links: Array.from(document.querySelectorAll('#model-nav .model-link[data-model]'))
        .map((l) => l.dataset.model),
      top: top.map(titleOf),
      nested: Boolean(map && red && map !== red && map.contains(red)),
      redShown: shown(red),
      redBodyShown: shown(red && red.querySelector('.card-body')),
      opened: window.station.opened,
      // The contract's order: host tier 1, the group, host tier 2, guest tier 2.
      ordered: Boolean(map && red) && order(map.querySelector('.card-body'), red)
        && order(red, disc('Configure Fake Map')) && order(disc('Configure Fake Map'), disc('Fake Red details')),
      hostDisc: shown(disc('Configure Fake Map')),
      guestDisc: shown(disc('Fake Red details')),
      heading: red ? (red.querySelector('.card-title') || {}).tagName : null,
      mapMark: (document.querySelector('#model-nav [data-model="Fake Map"] .nav-mark') || {}).textContent || '',
      mapUnconfirmed: Boolean(map && map.classList.contains('is-unconfirmed')),
      mapWord: map ? map.querySelector(':scope > .card-head .card-state').textContent : '',
    };
  });
  const openMap = async () => {
    await page.click('#model-nav [data-model="Fake Map"]');
    await sleep(400);
  };
"""


@needs_browser
def test_d_a_hosted_model_has_no_link_and_no_overview_entry(hosted_station, tmp_path):
    view, controller, host, guest = hosted_station
    out = _browse(view, _HOSTED + r"""
      return await read();
    """, tmp_path)
    assert out["links"] == ["Fake Map"], out
    assert out["top"] == ["Fake Map"], out
    assert out["nested"], out
    assert not out["redShown"], "the hosted model is drawn on the Overview"


@needs_browser
def test_d_the_host_page_holds_the_group_in_the_contracts_order(hosted_station, tmp_path):
    view, controller, host, guest = hosted_station
    out = _browse(view, _HOSTED + r"""
      await openMap();
      const r = await read();
      // Tab order: the host's last tier-1 control, then the guest's.
      await page.evaluate(() => Array.from(document.querySelectorAll('#cards button'))
        .find((b) => b.textContent.trim() === 'Sweep').focus());
      await page.keyboard.press('Tab');
      r.afterSweep = await page.evaluate(() => document.activeElement.textContent.trim());
      return r;
    """, tmp_path)
    assert out["opened"] == "Fake Map", out
    assert out["redShown"] and out["redBodyShown"], out
    assert out["ordered"], out
    assert out["hostDisc"] and out["guestDisc"], out
    assert out["afterSweep"] == "Poke", out


@needs_browser
def test_d_a_command_in_the_group_runs_against_the_hosted_model(hosted_station, tmp_path):
    view, controller, host, guest = hosted_station
    out = _browse(view, _HOSTED + r"""
      await openMap();
      const press = () => page.evaluate(() => Array.from(document.querySelectorAll('#cards button'))
        .find((b) => b.textContent.trim() === 'Poke').click());
      await press();
      await sleep(500);
      return true;
    """, tmp_path)
    assert guest.pressed == ["Fake Red"] and host.pressed == [], (guest.pressed, host.pressed)


@needs_browser
def test_d_a_refusal_in_the_group_is_said_in_the_group(hosted_station, tmp_path):
    view, controller, host, guest = hosted_station
    guest.refuse = "Pick a region first."
    out = _browse(view, _HOSTED + r"""
      await openMap();
      await page.evaluate(() => Array.from(document.querySelectorAll('#cards button'))
        .find((b) => b.textContent.trim() === 'Poke').click());
      await until(() => Array.from(document.querySelectorAll('#cards .status'))
        .some((s) => !s.hidden && s.textContent.includes('Pick a region')));
      return page.evaluate(() => {
        const titleOf = (c) => (c.querySelector('.card-title') || {}).textContent;
        const red = Array.from(document.querySelectorAll('#cards .card')).find((c) => titleOf(c) === 'Fake Red');
        const line = Array.from(document.querySelectorAll('#cards .status'))
          .find((s) => !s.hidden && s.textContent.includes('Pick a region'));
        return { inGroup: red.contains(line), shown: Boolean(line.getClientRects().length) };
      });
    """, tmp_path)
    assert out == {"inGroup": True, "shown": True}, out


@needs_browser
def test_d_closing_the_host_gives_the_hosted_model_its_page_back(hosted_station, tmp_path):
    view, controller, host, guest = hosted_station
    out = _browse(view, _HOSTED + r"""
      const r = {};
      await api('/api/close_model', { name: 'Fake Map' });
      await until(() => document.querySelectorAll('#model-nav [data-model]').length === 1
        && document.querySelector('#model-nav [data-model="Fake Red"]'));
      await sleep(400);
      r.alone = await read();
      await page.click('#model-nav [data-model="Fake Red"]');
      await sleep(400);
      r.alonePage = await read();
      await api('/api/open_model', { name: 'Fake Map' });
      await until(() => document.querySelector('#model-nav [data-model="Fake Map"]')
        && !document.querySelector('#model-nav [data-model="Fake Red"]'));
      await sleep(400);
      r.back = await read();
      await api('/api/close_model', { name: 'Fake Red' });
      await until(() => !Array.from(document.querySelectorAll('#cards .card-title'))
        .some((t) => t.textContent === 'Fake Red'));
      await sleep(300);
      await openMap();
      r.guestGone = await read();
      r.guestDiscs = await page.evaluate(() => Array.from(document.querySelectorAll('#cards .disclosure'))
        .map((d) => d.textContent.trim()));
      return r;
    """, tmp_path)
    assert out["alone"]["links"] == ["Fake Red"] and out["alone"]["top"] == ["Fake Red"], out["alone"]
    assert out["alone"]["redShown"], out["alone"]
    assert out["alonePage"]["opened"] == "Fake Red" and out["alonePage"]["guestDisc"], out["alonePage"]
    assert out["back"]["links"] == ["Fake Map"] and out["back"]["nested"], out["back"]
    assert out["guestGone"]["links"] == ["Fake Map"] and out["guestGone"]["hostDisc"], out["guestGone"]
    assert "Fake Red details" not in out["guestDiscs"], out["guestDiscs"]


@needs_browser
def test_d_going_to_the_hosted_model_by_name_lands_on_the_hosts_page(hosted_station, tmp_path):
    view, controller, host, guest = hosted_station
    out = _browse(view, _HOSTED + r"""
      await page.setViewport({ width: 1400, height: 500 });
      await page.evaluate(() => window.station.showPage('Fake Red'));
      await sleep(600);
      const r = await read();
      r.inView = await page.evaluate(() => {
        const titleOf = (c) => (c.querySelector('.card-title') || {}).textContent;
        const red = Array.from(document.querySelectorAll('#cards .card')).find((c) => titleOf(c) === 'Fake Red');
        const t = red.querySelector('.card-title').getBoundingClientRect();
        const hit = document.elementFromPoint(t.left + 4, t.top + t.height / 2);
        // In the window, and not under the rail or the host's pinned tier 1.
        return { top: Math.round(t.top), onTop: Boolean(hit && red.contains(hit)),
                 focused: document.activeElement === red };
      });
      return r;
    """, tmp_path)
    assert out["opened"] == "Fake Map" and out["redShown"], out
    assert out["inView"]["onTop"] and out["inView"]["focused"], out


@needs_browser
def test_d_the_hosted_models_stop_state_folds_into_the_hosts_link_and_entry(hosted_station, tmp_path):
    view, controller, host, guest = hosted_station
    guest.is_estopped, guest.stop_confirmed = True, False
    out = _browse(view, _HOSTED + r"""
      const r = { overview: await read() };
      await openMap();
      r.page = await read();
      return r;
    """, tmp_path)
    over = out["overview"]
    assert over["mapMark"] == "did not confirm", over
    assert over["mapUnconfirmed"] and over["mapWord"] == "Stopped", over
    # On the host's page the guest's own head says it; the host's head is
    # the host's again.
    assert out["page"]["mapMark"] == "did not confirm", out["page"]
    assert not out["page"]["mapUnconfirmed"] and out["page"]["mapWord"] == "", out["page"]
    assert host.is_estopped is False


@needs_browser
def test_d_the_region_picker_in_the_group_asks_the_hosted_model(hosted_station, tmp_path):
    view, controller, host, guest = hosted_station
    out = _browse(view, _HOSTED + r"""
      const asked = [];
      page.on('request', (r) => { if (r.url().includes('/api/screen')) asked.push(r.url()); });
      await openMap();
      await page.evaluate(() => {
        const titleOf = (c) => (c.querySelector('.card-title') || {}).textContent;
        const red = Array.from(document.querySelectorAll('#cards .card')).find((c) => titleOf(c) === 'Fake Red');
        Array.from(red.querySelectorAll('button')).find((b) => /region/i.test(b.textContent + (b.getAttribute('aria-label') || ''))).click();
      });
      await sleep(600);
      return asked;
    """, tmp_path)
    assert out and all("name=Fake%20Red" in u for u in out), out


#: The colour a value is drawn in, against the theme's muted ink.
_COLOURS = r"""
  const colours = () => page.evaluate(() => {
    const titleOf = (c) => (c.querySelector('.card-title') || {}).textContent;
    const all = Array.from(document.querySelectorAll('#cards .card'));
    const map = all.find((c) => titleOf(c) === 'Fake Map');
    const red = all.find((c) => titleOf(c) === 'Fake Red');
    const probe = document.createElement('span');
    probe.style.color = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
    document.body.appendChild(probe);
    const muted = getComputedStyle(probe).color;
    const isMuted = (n) => Boolean(n) && getComputedStyle(n).color === muted;
    const tiers = document.querySelector('#cards .card-tiers');
    return {
      map: isMuted(map.querySelector(':scope > .card-body .value')),
      red: isMuted(red.querySelector('.card-body .value')),
      redTier: isMuted(tiers && tiers.querySelector('.tier-well .value')),
    };
  });
"""


@needs_browser
def test_d_a_latched_host_does_not_mute_its_guest_and_a_latched_guest_mutes_its_tiers(hosted_station, tmp_path):
    """Each model's latch is its own: the host latched freezes the host's
    numbers only; the guest latched freezes the guest's, its tier-2 well
    (drawn apart from its entry, after the host's) included."""
    view, controller, host, guest = hosted_station
    out = _browse(view, _HOSTED + _COLOURS + r"""
      await openMap();
      await page.evaluate(() => {
        const d = Array.from(document.querySelectorAll('#cards .disclosure'))
          .find((n) => n.textContent.trim() === 'Fake Red details');
        d.click();
      });
      await sleep(300);
      const r = { live: await colours() };
      return r;
    """, tmp_path)
    assert out["live"] == {"map": False, "red": False, "redTier": False}, out
    host.is_estopped = True
    out = _browse(view, _HOSTED + _COLOURS + r"""
      await openMap();
      await page.evaluate(() => {
        const d = Array.from(document.querySelectorAll('#cards .disclosure'))
          .find((n) => n.textContent.trim() === 'Fake Red details');
        if (d.getAttribute('aria-expanded') !== 'true') d.click();
      });
      await sleep(300);
      return colours();
    """, tmp_path)
    assert out == {"map": True, "red": False, "redTier": False}, out
    host.is_estopped, guest.is_estopped = False, True
    out = _browse(view, _HOSTED + _COLOURS + r"""
      await openMap();
      await page.evaluate(() => {
        const d = Array.from(document.querySelectorAll('#cards .disclosure'))
          .find((n) => n.textContent.trim() === 'Fake Red details');
        if (d.getAttribute('aria-expanded') !== 'true') d.click();
      });
      await sleep(300);
      return colours();
    """, tmp_path)
    assert out == {"map": False, "red": True, "redTier": True}, out


def test_d_the_view_reads_host_from_state_never_a_class_name():
    assert "Red Percent" not in CODE and "Transfer Map" not in CODE
    assert ".host" in CODE


@pytest.fixture
def map_station(tmp_path, monkeypatch):
    """The real pair, Transfer Map and Red Percent, in SIM behind a real
    Setup: ticking the Map ticks Red Percent (Setup._enable)."""
    monkeypatch.setenv("STATION_MAP_DB", str(tmp_path / "db" / "map.sqlite"))
    from controller.setup import Setup
    controller = Controller()
    setup = Setup(controller)
    key = next(k for k, row in setup._rows.items() if row["name"] == "Transfer Map")
    getattr(setup, f"set_{key}_enabled")(True)
    launched = setup.launch()
    view = WebView(controller, setup, port=0, open_browser=False)
    assert view.open()
    try:
        yield view, controller, launched
    finally:
        view.close()


@needs_browser
def test_d_the_real_pair_in_sim_is_one_dashboard(map_station, tmp_path):
    view, controller, launched = map_station
    assert set(controller.state()["models"]) >= {"Transfer Map", "Red Percent"}
    out = _browse(view, r"""
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close');
        await sleep(300);
      }
      await page.click('#model-nav [data-model="Transfer Map"]');
      await sleep(600);
      return page.evaluate(() => {
        const titleOf = (c) => (c.querySelector('.card-title') || {}).textContent;
        const map = Array.from(document.querySelectorAll('#cards > .card')).find((c) => titleOf(c) === 'Transfer Map');
        const red = Array.from(document.querySelectorAll('#cards .card')).find((c) => titleOf(c) === 'Red Percent');
        const discs = Array.from(map.querySelectorAll('.disclosure[data-tier="2"]')).map((d) => d.textContent.trim());
        return {
          links: Array.from(document.querySelectorAll('#model-nav [data-model]')).map((l) => l.dataset.model),
          nested: Boolean(map && red && map.contains(red)),
          shown: Boolean(red && red.getClientRects().length),
          discs,
        };
      });
    """, tmp_path)
    assert "Red Percent" not in out["links"] and "Transfer Map" in out["links"], out
    assert out["nested"] and out["shown"], out
    assert out["discs"][-1] == "Red Percent details", out
    assert len(out["discs"]) == 2, out


# ==========================================================================
# W1: a host's page is not pinned (brief-web-polish.md)
# ==========================================================================
#: The Transfer Map's page at the bench's 1440x900: pinned or not, and where
#: the host's head and the guest's group sit before and after a scroll.
_HOST_SCROLL = r"""
  await page.setViewport({ width: 1440, height: 900 });
  await openMap();
  const where = () => page.evaluate(() => {
    const map = Array.from(document.querySelectorAll('#cards > .card'))
      .find((c) => (c.querySelector('.card-title') || {}).textContent === 'Fake Map');
    const red = Array.from(map.querySelectorAll('.card'))
      .find((c) => (c.querySelector('.card-title') || {}).textContent === 'Fake Red');
    return { pinned: map.classList.contains('is-pinned'),
             sticky: getComputedStyle(map.querySelector(':scope > .card-head')).position,
             head: map.querySelector(':scope > .card-head').getBoundingClientRect().top,
             red: red ? red.getBoundingClientRect().top : null,
             scroll: window.scrollY };
  });
  // Room to scroll, whatever the fakes' height: the host's entry grows at
  // its foot, where its details would be (a pin holds inside its entry).
  await page.evaluate(() => {
    const map = Array.from(document.querySelectorAll('#cards > .card'))
      .find((c) => (c.querySelector('.card-title') || {}).textContent === 'Fake Map');
    const spacer = document.createElement('div');
    spacer.style.height = '2000px';
    map.appendChild(spacer);
  });
  await page.evaluate(() => window.dispatchEvent(new Event('resize')));
  await sleep(200);
  const before = await where();
  await page.evaluate(() => window.scrollBy(0, 200));
  await sleep(300);
  const after = await where();
"""


@needs_browser
def test_w1_a_host_page_is_not_pinned_the_whole_page_scrolls(hosted_station, tmp_path):
    """The Transfer Map's tier 1 was pinned, so Red Percent's group scrolled
    under it with ~400 px left at 1440x900. A page whose model hosts another
    is not pinned: the host's head scrolls away with the page."""
    view, controller, host, guest = hosted_station
    out = _browse(view, _HOSTED + _HOST_SCROLL + r"""
      return { before, after };
    """, tmp_path)
    before, after = out["before"], out["after"]
    assert after["scroll"] > 150, out
    assert before["pinned"] is False and after["pinned"] is False, out
    assert after["sticky"] != "sticky", out
    # Unpinned: the head moved up with the page, by the page's scroll.
    assert after["head"] < before["head"] - 150, out
    assert after["red"] is not None and after["red"] < before["red"] - 150, out


@needs_browser
def test_w1_the_same_page_without_a_guest_keeps_todays_pin(hosted_station, tmp_path):
    """Every other page keeps O15's rule: the same host with its guest
    closed is a page of its own, and its short tier 1 is pinned again."""
    view, controller, host, guest = hosted_station
    out = _browse(view, _HOSTED + r"""
      await api('/api/close_model', { name: 'Fake Red' });
      await until(() => !Array.from(document.querySelectorAll('#cards .card-title'))
        .some((t) => t.textContent === 'Fake Red'));
      await sleep(300);
    """ + _HOST_SCROLL + r"""
      return { before, after };
    """, tmp_path)
    before, after = out["before"], out["after"]
    assert after["scroll"] > 150, out
    assert before["pinned"] and after["pinned"], out
    assert after["sticky"] == "sticky", out
    # Pinned (O15's own check): the head holds at the top of the view while
    # the page moved 200 px under it.
    assert -0.5 <= after["head"] <= before["head"] + 0.5, out


def test_w1_pin_opened_skips_a_card_with_guests():
    """Static read: the pin decision asks whether the card hosts a guest."""
    body = _body(r"\n  pinOpened\(\) \{(.*?)\n  \}\n")
    assert "this.guestsOf(card.name).length" in body, body



# ==========================================================================
# W2: a value is shown as the model gives it (brief-web-polish.md)
# ==========================================================================
class _VersionSetup(FakeSetup):
    """FakeSetup with the real Setup's Update row: a version sha and the
    incoming commits (values, lower case), and a row status (a status line,
    L17, lower case)."""

    def __init__(self):
        super().__init__()
        self.station_version = "d66c462"
        self.update_log = "d66c462 fix the map"
        self.probe_status = "simulated"

    @property
    def schema(self):
        base = super().schema
        base["sections"].insert(0, sch.section(
            "Update",
            sch.readonly("Station", "station_version"),
            sch.readonly("Coming", "update_log"),
            layout="row"))
        base["sections"][-1]["elements"].append(sch.readonly("Status:", "probe_status"))
        return base


@pytest.fixture
def version_station():
    controller = Controller()
    controller.factory = lambda config: FakeProbe()
    probe = FakeProbe()
    controller.add("Fake Probe", probe, {"kind": "Fake Probe"})
    view = WebView(controller, _VersionSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, probe
    finally:
        view.close()


@needs_browser
def test_w2_a_lowercase_value_is_not_sentence_cased(version_station, tmp_path):
    """The version sha read "D66c462". A value's text is the model's; the
    caption beside it and a Setup status line keep their sentence case."""
    view, controller, probe = version_station
    out = _browse(view, r"""
      if (!(await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open')))) {
        await page.click('#setup-link'); await sleep(500);
      }
      await until(() => document.querySelector('#setup-drawer [data-attr="station_version"] .value')
        && document.querySelector('#setup-drawer [data-attr="station_version"] .value').textContent !== '--');
      await sleep(300);
      return page.evaluate(() => {
        const at = (attr) => document.querySelector('#setup-drawer [data-attr="' + attr + '"]');
        const value = (attr) => at(attr).querySelector('.value').textContent;
        return { version: value('station_version'), log: value('update_log'),
                 status: value('probe_status'),
                 caption: at('station_version').textContent.replace(value('station_version'), '').trim() };
      });
    """, tmp_path)
    assert out["version"] == "d66c462", out
    assert out["log"] == "d66c462 fix the map", out
    assert out["status"] == "Simulated", out
    assert out["caption"].startswith("Station"), out
