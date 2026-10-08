"""The Web view's two items of the one-dashboard round (2026-09-28).

F: a hidden tab is not a gone browser. The page's heartbeat stopped the
moment the tab was hidden, so switching to the microscope window on a
single-screen bench PC FULL STOPped the station fifteen seconds later. The
heartbeat now runs in a dedicated Worker whose timer the page's hidden-tab
throttling does not reach; it stops on `pagehide` and at shutdown only.

D: Red Percent is drawn on the Transfer Map's page (`host` in state,
docs/rebuild/MODEL_CONTRACT.md step 1; docs/archive/handoff/brief-dashboard-contract.md):
no page or Overview entry of its own, a group after the host's tier 1,
its disclosures after the host's, every control bound to its own name.

Driven in headless Chrome through the harness in test_view_web_server.py
(skipped where node or puppeteer is absent), plus static reads of app.js.
"""
import re
import json
import urllib.parse
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
@pytest.mark.usefixtures("test_routes")
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
      page.on('request', (r) => { if (r.url().includes('/api/data')) asked.push(r.url()); });
      await openMap();
      await page.evaluate(() => {
        const titleOf = (c) => (c.querySelector('.card-title') || {}).textContent;
        const red = Array.from(document.querySelectorAll('#cards .card')).find((c) => titleOf(c) === 'Fake Red');
        Array.from(red.querySelectorAll('button')).find((b) => /region/i.test(b.textContent + (b.getAttribute('aria-label') || ''))).click();
      });
      await sleep(600);
      return asked;
    """, tmp_path)
    # WEB-2: the element declares data_command, so the picker asks /api/data
    # for THAT command, of the hosted model.
    assert out and all("name=Fake%20Red" in u and "command=screen_image" in u
                       for u in out), out


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
    Setup: the Map needs no port, so it always launches, with its guest."""
    monkeypatch.setenv("STATION_MAP_DB", str(tmp_path / "db" / "map.sqlite"))
    from controller.setup import Setup
    controller = Controller()
    setup = Setup(controller)
    # Past the sign-in screen (2026-10-07) signed in: a Guest's launch leaves
    # out the Transfer Map and its guest (owner 2026-10-07, "guest users
    # should have no access to transfer map"), and this is the pair.
    setup.users.create("sim@uci.edu", "correct-horse-4821", name="sim")
    signed = setup.run("sign_in", {"account_email": "sim@uci.edu",
                                   "account_password": "correct-horse-4821"})
    assert signed.is_ok, signed.reason
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
    assert set(controller.state()["models"]) >= {"Transfer Map", "RGB Analysis"}
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
        const red = Array.from(document.querySelectorAll('#cards .card')).find((c) => titleOf(c) === 'RGB Analysis');
        const discs = Array.from(map.querySelectorAll('.disclosure[data-tier="2"]')).map((d) => d.textContent.trim());
        return {
          links: Array.from(document.querySelectorAll('#model-nav [data-model]')).map((l) => l.dataset.model),
          nested: Boolean(map && red && map.contains(red)),
          shown: Boolean(red && red.getClientRects().length),
          discs,
        };
      });
    """, tmp_path)
    assert "RGB Analysis" not in out["links"] and "Transfer Map" in out["links"], out
    assert out["nested"] and out["shown"], out
    assert out["discs"][-1] == "RGB analysis details", out
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
@pytest.mark.usefixtures("test_routes")
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


# ==========================================================================
# rb-link-views V4: the link on the page
# ==========================================================================
class LinkedProbe(FakeProbe):
    """A probe that owns a port: `state["link"]` (the rb-link contract), a
    mode toggle, a go command and a Diagnostics section of its own."""

    def __init__(self, root=None):
        super().__init__(root)
        self.link = {"status": "verified", "losses": 0, "reconnects": 0,
                     "dropped": 0, "stalls": 0, "stalled": False, "last_loss": None}
        self.is_auto = False
        self.position_age = "6.0"

    @property
    def schema(self):
        schema = dict(super().schema)
        schema["sections"] = list(schema["sections"]) + [
            sch.section("Modes",
                        sch.toggle("Autonomous", "is_auto", "set_auto", "On", "Off",
                                   on_args=[True], off_args=[False]),
                        sch.button("Step", "step", role="go")),
            sch.section("Diagnostics",
                        sch.readonly("Position age (s):", "position_age"),
                        tier=3, disclosure="Diagnostics"),
        ]
        return schema

    @property
    def state(self):
        snapshot = super().state
        snapshot["link"] = dict(self.link)
        snapshot["devices"] = {"SerialPort": self.link["status"]}
        return snapshot

    def set_auto(self, flag):
        self.is_auto = bool(flag)

    def step(self):
        return "stepped"


@pytest.fixture
def linked(tmp_path):
    controller = Controller()
    probe = LinkedProbe(root=str(tmp_path))
    controller.add("Fake Probe", probe, {"root": str(tmp_path)})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, probe
    finally:
        view.close()


def test_v4_the_server_serves_the_link_words_and_the_counters_row(linked):
    """One implementation: /api/state carries `link_words` from views.base
    (the same functions Tk and Qt draw from) and /api/schema the counters
    row in the model's own Diagnostics; the model's schema is untouched."""
    from test_view_web_server import _get
    from views import base as view_base
    view, controller, probe = linked
    probe.link.update(status="reconnecting", losses=1, last_loss="12:41:07")
    status, state = _get(view, "/api/state")
    words = state["link_words"]["Fake Probe"]
    assert words == {"tier": "error", "line": "Link lost 12:41:07, reconnecting…",
                     "down": True, "reason": "Link lost: wait for it to reconnect",
                     "counters": "1 / 0 / 0 / 0; last loss 12:41:07",
                     "attr": view_base.LINK_ATTR}
    status, schema = _get(view, "/api/schema?name=Fake%20Probe")
    diagnostics = next(s for s in schema["sections"] if s["title"] == "Diagnostics")
    assert diagnostics["elements"][-1]["model_attr"] == view_base.LINK_ATTR
    assert all(e.get("model_attr") != view_base.LINK_ATTR
               for e in sch.elements(controller.schema("Fake Probe")))


def test_v4_a_model_without_a_port_has_no_link_words(station):
    from test_view_web_server import _get
    view, _, _ = station
    status, state = _get(view, "/api/state")
    assert state["link_words"] == {}


#: What the page shows about the probe's link, read in the page.
_LINK_READ = r"""
  return page.evaluate(() => {
    const card = Array.from(document.querySelectorAll('.card'))
      .find((c) => !c.classList.contains('setup-card'));
    const probe = (color) => {
      const s = document.createElement('span');
      s.style.color = getComputedStyle(document.documentElement).getPropertyValue(color).trim();
      document.body.appendChild(s);
      return getComputedStyle(s).color;
    };
    const button = (text) => Array.from(card.querySelectorAll('button'))
      .find((b) => b.textContent.trim().startsWith(text));
    const alert = card.querySelector('.card-alert');
    const mark = document.querySelector('.model-link[data-model="Fake Probe"] .nav-mark');
    return {
      cls: card.className, bar: getComputedStyle(card).borderTopColor,
      signal: probe('--signal'), ruleStrong: probe('--rule-strong'),
      alert: alert.hidden ? '' : alert.textContent,
      alertCls: alert.className,
      badge: card.querySelector('.stale-badge').hidden ? '' : card.querySelector('.stale-badge').textContent,
      auto: button('Off') ? button('Off').disabled : null,
      step: button('Step') ? button('Step').disabled : null,
      stop: button('Stop this model') ? button('Stop this model').disabled : null,
      rail: document.getElementById('rail-alert').hidden ? '' : document.getElementById('rail-alert').textContent,
      railCls: Array.from(document.querySelectorAll('#rail-alert .rail-alert-line'))
        .map((n) => n.className).join(' '),
      mark: mark ? mark.className : '', markTitle: mark ? mark.title : '',
      text: card.textContent,
    };
  });
"""


@needs_browser
def test_v4_a_reconnecting_link_is_the_danger_tier_on_the_page(linked, tmp_path):
    view, _, probe = linked
    probe.link.update(status="reconnecting", losses=1, dropped=3,
                      last_loss="12:41:07")
    out = _browse(view, r"""
      await until(() => { const a = document.querySelector('.card-alert');
                          return a && !a.hidden; });
      await sleep(300);
    """ + _LINK_READ, tmp_path)
    assert out["alert"] == "Link lost 12:41:07, reconnecting…", out
    assert "severity-warning" not in out["alertCls"]
    assert "is-lost" in out["cls"] and out["bar"] == out["signal"], out
    assert out["badge"] == "Connection lost"
    assert out["auto"] is True and out["step"] is True, "modes held while down"
    assert out["stop"] is False, "the stop is never held"
    assert "Fake Probe: Link lost 12:41:07, reconnecting" in out["rail"]
    assert "is-link-lost" in out["mark"] and out["markTitle"].startswith("Link lost")
    assert "severity-warning" not in out["railCls"]
    assert "1 / 0 / 3 / 0; last loss 12:41:07" in out["text"], "the counters row"


@needs_browser
def test_v4_a_stalled_link_is_the_attention_tier_on_the_page(linked, tmp_path):
    view, _, probe = linked
    probe.link.update(stalled=True, stalls=1)
    out = _browse(view, r"""
      await until(() => { const a = document.querySelector('.card-alert');
                          return a && !a.hidden; });
      await sleep(300);
    """ + _LINK_READ, tmp_path)
    assert out["alert"] == "No position for 6 s; link up, check the board", out
    assert "severity-warning" in out["alertCls"]
    assert "is-lost" not in out["cls"] and out["bar"] != out["signal"], out
    assert out["auto"] is False and out["step"] is False
    assert "is-attention" in out["mark"] and "is-link-lost" not in out["mark"]
    assert "severity-warning" in out["railCls"], "the rail line is the attention tier"
    assert "0 / 0 / 0 / 1; last loss never" in out["text"]


@needs_browser
def test_v4_a_well_link_says_nothing(linked, tmp_path):
    view, _, probe = linked
    out = _browse(view, "await sleep(800);" + _LINK_READ, tmp_path)
    assert out["alert"] == "" and "is-lost" not in out["cls"]
    assert out["auto"] is False and out["step"] is False
    assert "is-attention" not in out["mark"] and "is-link-lost" not in out["mark"]


# ==========================================================================
# WEB-1: the page draws the procedure step (owner ruling 2026-10-07)
# ==========================================================================
class FakePhased(_Plain):
    """A two-step model, the shape of tests/test_model_contract.PhasedModel
    plus what the page needs: a row the step hides, a section whose every
    row is phased (its header must go with them), a `go`-style button whose
    note must stay silent while hidden."""
    NAME = "Fake Phased"
    PHASES = ("setup", "live")

    def __init__(self):
        super().__init__()
        self._phase = "setup"
        self.tip = "T-1"

    @property
    def phase(self):
        return self._phase

    @property
    def schema(self):
        return sch.schema(
            sch.section("Start", sch.readonly("Tip", "tip", rail=True),
                        sch.button("Begin", "begin"), phases=("setup",)),
            sch.section("Trial",
                        sch.readonly("Step", "phase_word"),
                        sch.phased(sch.button("Mark", "press"), "live"),
                        sch.phased(sch.readonly("Marked at", "tip"), "live")),
            sch.section("Review", sch.phased(sch.button("Keep", "press"), "live"),
                        sch.phased(sch.button("Drop", "press"), "setup")),
            sch.section("Details", sch.readonly("Tip", "tip"), tier=2,
                        disclosure="Configure Fake Phased", phases=("setup",)),
        )

    @property
    def state(self):
        snapshot = super().state
        snapshot["values"]["phase_word"] = self._phase
        return snapshot

    def begin(self):
        self._phase = "live"
        return "begun"


@pytest.fixture
def phased_station():
    controller = Controller()
    model = FakePhased()
    controller.add("Fake Phased", model, {"kind": "Fake Phased"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, model
    finally:
        view.close()


#: What the page draws right now, by visible text, and where focus is.
_PHASE_READ = r"""
  if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
    await page.click('#drawer-close');
    await sleep(300);
  }
  await until(() => document.querySelectorAll('#cards .card-title').length >= 1);
  await sleep(400);
  const drawn = () => page.evaluate(() => {
    const shown = (n) => Boolean(n && n.getClientRects().length);
    const card = document.querySelector('#cards .card');
    const a = document.activeElement;
    return {
      buttons: Array.from(card.querySelectorAll('.card-body button')).filter(shown)
        .map((b) => b.textContent.trim()),
      headers: Array.from(card.querySelectorAll('.section-title')).filter(shown)
        .map((h) => h.textContent.trim()),
      discs: Array.from(card.querySelectorAll('.disclosure')).filter(shown).length,
      phase: window.station.cards.get('Fake Phased').phase,
      focusIsDrawn: !a || a === document.body || shown(a),
      focusOnCard: Boolean(a) && a === card,
      focusTag: a ? a.tagName + ':' + (a.textContent || '').trim().slice(0, 20) : '',
    };
  });
"""


@needs_browser
def test_web1_a_step_draws_its_controls_and_hides_the_rest(phased_station, tmp_path):
    view, controller, model = phased_station
    out = _browse(view, _PHASE_READ + r"""
      const fetched = [];
      page.on('request', (r) => { if (r.url().includes('/api/schema')) fetched.push(r.url()); });
      await page.evaluate(() => window.station.showPage('Fake Phased'));
      await sleep(400);
      const setup = await drawn();
      await page.evaluate(() => Array.from(document.querySelectorAll('#cards button'))
        .find((b) => b.textContent.trim() === 'Begin').click());
      await sleep(1800);
      const live = await drawn();
      return { setup, live, schemaFetches: fetched.length };
    """, tmp_path)
    setup, live = out["setup"], out["live"]
    assert setup["phase"] == "setup", out
    # Section "Trial" keeps its unphased row; "Review" keeps its header
    # because one of its rows is drawn; "Mark" and "Keep" are not.
    assert "Begin" in setup["buttons"] and "Drop" in setup["buttons"], setup
    assert "Mark" not in setup["buttons"] and "Keep" not in setup["buttons"], setup
    assert setup["discs"] == 1, "the setup-only tier-2 disclosure is drawn in setup"
    assert live["phase"] == "live", out
    assert "Mark" in live["buttons"] and "Keep" in live["buttons"], live
    assert "Begin" not in live["buttons"] and "Drop" not in live["buttons"], live
    # A section with every row hidden has no header: "Start" is phased
    # whole; "Review" in setup showed Drop only.
    assert "Start" not in live["headers"], live
    assert live["discs"] == 0, "a tier-2 disclosure the step hides is still drawn"
    # The schema is read once, never again for a step.
    assert out["schemaFetches"] == 0, out


@needs_browser
def test_web1_focus_leaves_a_control_that_disappears(phased_station, tmp_path):
    view, controller, model = phased_station
    out = _browse(view, _PHASE_READ + r"""
      await page.evaluate(() => Array.from(document.querySelectorAll('#cards button'))
        .find((b) => b.textContent.trim() === 'Begin').focus());
      const before = await page.evaluate(() => document.activeElement.textContent.trim());
      await page.keyboard.press('Enter');
      await sleep(1800);
      return { before, after: await drawn() };
    """, tmp_path)
    assert out["before"] == "Begin", out
    after = out["after"]
    assert after["phase"] == "live", out
    assert after["focusIsDrawn"], f"focus stayed on a hidden control: {after}"
    assert after["focusOnCard"], f"focus did not go to the entry: {after}"


@needs_browser
def test_web1_the_overview_strip_shows_only_the_current_steps_rows(phased_station, tmp_path):
    view, controller, model = phased_station
    out = _browse(view, _PHASE_READ + r"""
      const onOverview = await page.evaluate(() => window.station.opened === null
        && document.getElementById('cards').classList.contains('is-overview'));
      const setup = await drawn();
      await api('/api/run', { name: 'Fake Phased', command: 'begin', inputs: {}, args: [] });
      await sleep(1800);
      return { onOverview, setup, live: await drawn() };
    """, tmp_path)
    assert out["onOverview"], out
    assert out["setup"]["buttons"] == ["Begin", "Drop"], out
    assert out["live"]["buttons"] == ["Mark", "Keep"], out


@needs_browser
def test_web1_a_hidden_go_command_says_nothing(phased_station, tmp_path):
    """sayWhyNotGo skips a phase-hidden row: no 'why not' caption is drawn
    for a command the step does not show."""
    view, controller, model = phased_station
    out = _browse(view, _PHASE_READ + r"""
      await sleep(600);
      return await page.evaluate(() => Array.from(document.querySelectorAll('#cards .note'))
        .filter((n) => n.getClientRects().length && n.textContent.trim())
        .map((n) => n.textContent.trim()));
    """, tmp_path)
    assert out == [], out


# ==========================================================================
# WEB-2: the region picker draws on the model's own still
# ==========================================================================
STILL_SIZE = (3584, 2746)


class FakeStill(_Plain):
    """The Transfer Map's picker as the contract declares it:
    `region_select(..., data_command="stage_still")` returning a
    full-resolution PNG, plus a plain `screen_image` model-free fallback is
    covered by the hosted tests above."""
    NAME = "Fake Still"

    def __init__(self):
        super().__init__()
        self.region = None
        self.asked = []

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Trial", sch.readonly("Step", "mode_word"),
            sch.region_select("Set capture region", "set_region",
                              model_attr="region", data_command="stage_still")))

    @property
    def state(self):
        snapshot = super().state
        snapshot["values"]["mode_word"] = "armed"
        snapshot["values"]["region"] = (
            sch.format_region(self.region) if self.region else "")
        return snapshot

    def stage_still(self):
        import io
        from PIL import Image
        picture = Image.new("RGB", STILL_SIZE, (30, 40, 50))
        out = io.BytesIO()
        picture.save(out, "PNG")
        return out.getvalue()

    def set_region(self, x, y, width, height):
        left, top = x, y
        self.asked.append([x, y, width, height])
        self.region = {"left": left, "top": top, "width": width, "height": height}
        return self.region


@pytest.fixture
def still_station():
    controller = Controller()
    model = FakeStill()
    controller.add("Fake Still", model, {"kind": "Fake Still"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, model
    finally:
        view.close()


@needs_browser
def test_web2_the_picker_draws_on_the_models_still_and_maps_to_source_pixels(still_station, tmp_path):
    view, controller, model = still_station
    out = _browse(view, _PHASE_READ.split("const drawn")[0] + r"""
      const asked = [];
      page.on('request', (r) => { if (/\/api\/(screen|data)/.test(r.url())) asked.push(r.url()); });
      const open = async () => {
        await page.evaluate(() => Array.from(document.querySelectorAll('#cards button'))
          .find((b) => /capture region/i.test(b.textContent)).click());
        // Drawn: the dialog drops aria-busy once the still is on the canvas.
        // The canvas keeps the first opening's width, so on the reopen its
        // width alone said nothing, and a 3584x2746 PNG made and sent in
        // over 300 ms (a loaded machine) left the fields still empty.
        await until(() => document.getElementById('region-canvas').width > 800
          && !document.querySelector('#region-picker .dialog').hasAttribute('aria-busy'));
        await sleep(300);
      };
      await open();
      const geom = await page.evaluate(() => {
        const c = document.getElementById('region-canvas');
        const b = c.getBoundingClientRect();
        return { w: c.width, h: c.height, left: b.left, top: b.top, bw: b.width, bh: b.height };
      });
      // Drag between two on-screen points; the region is what those points
      // are in the picture's own pixels.
      const a = [geom.left + geom.bw * 0.25, geom.top + geom.bh * 0.40];
      const z = [geom.left + geom.bw * 0.55, geom.top + geom.bh * 0.62];
      await page.mouse.move(a[0], a[1]);
      await page.mouse.down();
      await page.mouse.move((a[0] + z[0]) / 2, (a[1] + z[1]) / 2);
      await page.mouse.move(z[0], z[1]);
      await page.mouse.up();
      await sleep(700);
      const state = await api('/api/state');
      // Reopen: the model's value is outlined on the still and in the fields.
      await open();
      const again = await page.evaluate(() => ({
        fields: Array.from(document.querySelectorAll('#region-picker .region-fields input')).map((f) => f.value),
        help: document.getElementById('region-help').textContent,
      }));
      return { geom, a, z, asked, again, state, canvasBefore: 0 };
    """, tmp_path)
    geom = out["geom"]
    assert (geom["w"], geom["h"]) == STILL_SIZE, f"the still was downscaled: {geom}"
    assert geom["bw"] < geom["w"], "the picture should be displayed scaled to fit"
    assert any("/api/data" in u and "command=stage_still" in u for u in out["asked"]), out["asked"]
    assert not any("/api/screen" in u for u in out["asked"]), out["asked"]
    (region,) = model.asked
    assert all(isinstance(v, int) for v in region), region
    k = geom["w"] / geom["bw"]
    expect_left = round((out["a"][0] - geom["left"]) * k)
    expect_top = round((out["a"][1] - geom["top"]) * k)
    assert abs(region[0] - expect_left) <= 1 and abs(region[1] - expect_top) <= 1, (region, expect_left, expect_top)
    assert abs(region[2] - round((out["z"][0] - out["a"][0]) * k)) <= 1, region
    assert abs(region[3] - round((out["z"][1] - out["a"][1]) * k)) <= 1, region
    assert out["again"]["fields"] == [str(v) for v in region], out["again"]
    assert sch.format_region(model.region) in out["again"]["help"], out["again"]


def test_web2_the_picker_reads_the_elements_data_command_and_keeps_the_screen_fallback():
    opener = _method_of("openRegionPicker")
    assert "element.data_command" in opener
    assert "/api/screen" in opener, "without a data_command the screen grab is kept"
    assert "/api/data" in _method_of("loadStill")
    # Pure helpers the picker uses to read a value back and say it.
    drag = _method_of("bindRegionDrag")
    assert "current" in drag and "setLineDash" in drag


def _method_of(name):
    from test_view_web_client import _method
    return _method(name)


def test_web2_a_picture_with_geometry_is_served_as_a_png_with_headers(hosted_station):
    """`/api/data` for a command that returns `{"image": bytes, left, top,
    width, height}` (Red Percent's `screen_image`) serves the PNG itself;
    the area it was scaled from travels in headers, so the picker maps a
    drag on a bounded grab of a second monitor into that monitor's pixels."""
    from test_view_web_server import _request
    view, controller, host, guest = hosted_station
    status, headers, body = _request(
        view, "/api/data?name=Fake%20Red&command=screen_image")
    assert status == 200 and headers["Content-Type"] == "image/png"
    assert body.startswith(b"\x89PNG")
    assert [headers[f"X-Image-{k}"] for k in ("Left", "Top", "Width", "Height")] == [
        "0", "0", "100", "100"]
    # A bare PNG (the Transfer Map's stage still) carries no such headers.
    status, headers, body = _request(
        view, "/api/data?name=Fake%20Map&command=nothing")
    assert status == 403


# ==========================================================================
# WEB-3: the device boundary, as the browser computes it
# ==========================================================================
@needs_browser
def test_web3_each_overview_device_is_bounded_and_the_guest_stays_inside_its_host(hosted_station, tmp_path):
    view, controller, host, guest = hosted_station
    out = _browse(view, _HOSTED + r"""
      const probe = (width) => page.evaluate(() => {
        const edge = (() => {
          const p = document.createElement('span');
          p.style.color = getComputedStyle(document.documentElement).getPropertyValue('--edge').trim();
          document.body.appendChild(p);
          return getComputedStyle(p).color;
        })();
        const cards = Array.from(document.querySelectorAll('#cards > .card'));
        const css = (c) => getComputedStyle(c);
        return {
          count: cards.length,
          sides: cards.map((c) => [css(c).borderLeftWidth, css(c).borderRightWidth,
            css(c).borderBottomWidth, css(c).borderLeftColor === edge, css(c).borderTopWidth]),
          guestInside: Array.from(document.querySelectorAll('#cards .card.is-hosted'))
            .every((g) => g.parentElement.closest('.card') !== null && css(g).borderLeftWidth === '0px'),
        };
      });
      const wide = await probe();
      await page.setViewport({ width: 390, height: 844 });
      await sleep(400);
      return { wide, phone: await probe() };
    """, tmp_path)
    for shot in (out["wide"], out["phone"]):
        assert shot["count"] == 1
        left, right, bottom, is_edge, top = shot["sides"][0]
        # --line is 1.5 px; Chrome snaps a border to whole device pixels.
        assert left == right == bottom and left in ("1px", "1.5px") and is_edge, shot
        assert top == "2px", f"the head rule changed weight: {shot}"
        assert shot["guestInside"], shot


# ==========================================================================
# WEB-5: an image upload carries its inputs; thumbnails by relative path
# ==========================================================================
from param import Param  # noqa: E402


class FakeImages(_Plain):
    """The Sample DB's "Add image" as the contract allows it: a `file_open`
    whose `inputs` (instrument, magnification) travel with the command."""
    NAME = "Fake Images"
    PARAMS = {"instrument": Param("instrument", "text", default="", label="Instrument"),
              "magnification": Param("magnification", "float", default=1.0, label="Magnification")}

    def __init__(self, root):
        super().__init__()
        self.root = root
        self.instrument = ""
        self.magnification = 1.0
        self.added = []

    @property
    def schema(self):
        opener = sch.file_open("Add image", "add_image", extensions=("png",))
        opener["inputs"] = ["instrument", "magnification"]
        return sch.schema(sch.section(
            "Images",
            sch.entry("Instrument", "instrument", self.PARAMS["instrument"]),
            sch.entry("Magnification", "magnification", self.PARAMS["magnification"]),
            opener))

    @property
    def state(self):
        snapshot = super().state
        snapshot["output_root"] = self.root
        snapshot["values"].update({"instrument": self.instrument,
                                   "magnification": self.magnification})
        return snapshot

    def add_image(self, path):
        self.added.append((path, self.instrument, self.magnification))
        return {"path": path}


@pytest.fixture
def images_station(tmp_path):
    controller = Controller()
    model = FakeImages(str(tmp_path))
    controller.add("Fake Images", model, {"kind": "Fake Images"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, model
    finally:
        view.close()


@needs_browser
def test_web5_a_file_open_forwards_its_declared_inputs(images_station, tmp_path):
    view, controller, model = images_station
    chosen = tmp_path / "tip.png"
    chosen.write_bytes(b"\x89PNG\r\n\x1a\nnot really")
    out = _browse(view, _PHASE_READ.split("const drawn")[0] + r"""
      const inputs = await page.$$('#cards .card-body input.input:not(.path-input)');
      for (const [box, text] of [[inputs[0], 'SEM'], [inputs[1], '250']]) {
        await box.evaluate((el) => { el.focus(); el.select(); });
        await page.keyboard.press('Backspace');
        await box.type(text);
      }
      // Typed path, then an uploaded file: both run the command with the
      // entries, uncommitted as they are.
      await page.type('.path-input', '/data/typed.png');
      await page.evaluate(() => Array.from(document.querySelectorAll('.file-open button'))
        .find((b) => b.textContent === 'Add image').click());
      await sleep(700);
      const picker = await page.$('.file-picker');
      await picker.uploadFile(%s);
      await sleep(1500);
      return true;
    """ % json.dumps(str(chosen)), tmp_path)
    assert out is True
    assert [(a[1], a[2]) for a in model.added] == [("SEM", 250.0), ("SEM", 250.0)], model.added
    assert model.added[0][0] == "/data/typed.png"
    assert model.added[1][0].endswith("uploads/tip.png"), model.added


def test_web5_a_thumbnail_is_served_by_relative_path_under_the_output_root(images_station, tmp_path):
    from test_view_web_server import _request
    view, controller, model = images_station
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.png").write_bytes(b"\x89PNGa")
    outside = tmp_path.parent / "secret.png"
    outside.write_bytes(b"\x89PNGsecret")
    (tmp_path / "images" / "link.png").symlink_to(outside)
    (tmp_path / "notes.txt").write_text("x")

    def get(path):
        return _request(view, "/api/image?name=Fake%20Images&path=" + urllib.parse.quote(path))[::2]

    status, body = get("images/a.png")
    assert status == 200 and body == b"\x89PNGa"
    assert _request(view, "/api/image?name=Fake%20Images&path=images/a.png")[1]["Content-Type"] == "image/png"
    assert get("../secret.png")[0] == 403
    assert get(str(outside))[0] == 403, "an absolute path is never taken"
    assert get("images/link.png")[0] == 403, "a symlink out of the root is outside it"
    assert get("notes.txt")[0] == 403, "only image types"
    assert get("images/missing.png")[0] == 404
    status, _, _ = _request(view, "/api/image?name=Nobody&path=images/a.png")
    assert status == 404


# ==========================================================================
# SP-2 / SP-3 (2026-10-07): the secondary readout, and hosted_tier
# ==========================================================================
class FakeDial(_Plain):
    """A percent dial with its steps/s underneath, as a probe's Speeds."""

    NAME = "Fake Dial"
    PARAMS = {"speed_pct": Param("speed_pct", "int", default=13, minimum=0,
                                 maximum=100, label="Speed", unit="%")}

    def __init__(self):
        super().__init__()
        self.speed_pct = 13
        self.speed = 416
        self.x = 0

    @property
    def schema(self):
        return sch.schema(
            sch.section("Position", sch.readonly("X:", "x", rail=True, unit="steps")),
            sch.section(
                "Speeds",
                sch.entry("Speed:", "speed_pct", self.PARAMS["speed_pct"], slider=(0, 100)),
                sch.readonly("steps/s", "speed", secondary=True, unit="steps/s"),
            ),
        )


@pytest.fixture
def dial_station():
    controller = Controller()
    controller.factory = lambda config: FakeDial()
    controller.add("Fake Dial", FakeDial(), {})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open()
    try:
        yield view
    finally:
        view.close()


@needs_browser
def test_sp2_the_secondary_is_a_quiet_line_under_its_control_and_not_on_the_overview(dial_station, tmp_path):
    out = _browse(dial_station, r"""
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(300);
      }
      await until(() => document.querySelector('#cards .card'));
      await sleep(400);
      const overview = await page.evaluate(() => {
        const n = document.querySelector('#cards .row.secondary');
        return { drawn: Boolean(n && n.getClientRects().length) };
      });
      await page.click('#model-nav [data-model="Fake Dial"]');
      await sleep(500);
      const device = await page.evaluate(() => {
        const sec = document.querySelector('#cards .row.secondary');
        const row = sec.parentElement;
        const group = row.querySelector('.group').getBoundingClientRect();
        const box = sec.getBoundingClientRect();
        const value = sec.querySelector('.value');
        return {
          drawn: Boolean(sec.getClientRects().length),
          inRow: row.classList.contains('has-slider') && Boolean(row.querySelector('input[name="speed_pct"]')),
          below: box.top >= group.bottom - 1,
          label: Boolean(sec.querySelector('.label')),
          text: sec.textContent,
          size: parseFloat(getComputedStyle(value).fontSize),
          quiet: getComputedStyle(value).color === getComputedStyle(row.querySelector('.label')).color,
          readings: document.querySelectorAll('#cards .reading').length,
          secondaryReading: Boolean(sec.classList.contains('reading')),
        };
      });
      return { overview, device };
    """, tmp_path)
    assert not out["overview"]["drawn"], out
    d = out["device"]
    assert d["drawn"] and d["inRow"] and d["below"] and not d["label"], d
    assert "416" in d["text"] and "steps/s" in d["text"], d
    assert 24 <= d["size"] <= 26, d
    assert d["quiet"], d
    assert d["readings"] == 1 and not d["secondaryReading"], d   # the X reading only


class FakeLive(FakeRed):
    """A hosted model whose tier-1 Live group is placed at tier 2 on its
    host's page (the analysis's shape)."""

    NAME = "Fake Live"

    @property
    def schema(self):
        return sch.schema(
            sch.section("Live", sch.readonly("Red", "red", rail=True),
                        sch.button("Start live", "press"), hosted_tier=2),
            sch.section("Rows", sch.readonly("Rows", "rows"),
                        tier=2, disclosure="Fake Live details"),
            sch.section("Diagnostics", sch.readonly("Rows", "rows"), tier=3,
                        disclosure="Diagnostics"),
        )


@pytest.fixture
def live_station():
    controller = Controller()
    made = {"Fake Map": FakeMap, "Fake Live": FakeLive}
    controller.factory = lambda config: made[config["kind"]]()
    controller.add("Fake Map", FakeMap(), {"kind": "Fake Map"})
    controller.add("Fake Live", FakeLive(), {"kind": "Fake Live"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open()
    try:
        yield view
    finally:
        view.close()


_LIVE_WHERE = r"""
  const where = () => page.evaluate(() => {
    const live = Array.from(document.querySelectorAll('#cards .card'))
      .find((c) => (c.querySelector('.card-title') || {}).textContent === 'Fake Live');
    const btn = Array.from(document.querySelectorAll('#cards button'))
      .find((b) => b.textContent.trim() === 'Start live');
    const tiers = Array.from(document.querySelectorAll('#cards .tier-well'));
    return {
      exists: Boolean(btn),
      inBody: Boolean(btn && live.querySelector('.card-body').contains(btn)),
      inWell: Boolean(btn && tiers.some((w) => w.contains(btn))),
      wellHidden: Boolean(btn && tiers.find((w) => w.contains(btn)) && tiers.find((w) => w.contains(btn)).hidden),
      shown: Boolean(btn && btn.getClientRects().length),
    };
  });
"""


@needs_browser
def test_sp3_the_live_group_leaves_the_hosts_tier_one_and_is_behind_details(live_station, tmp_path):
    out = _browse(live_station, _LIVE_WHERE + r"""
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(300);
      }
      await until(() => document.querySelectorAll('#cards .card-title').length >= 2);
      await page.click('#model-nav [data-model="Fake Map"]');
      await sleep(500);
      const r = { shut: await where() };
      await page.evaluate(() => Array.from(document.querySelectorAll('#cards .disclosure'))
        .find((d) => d.textContent.trim() === 'Fake Live details').click());
      await sleep(300);
      r.open = await where();
      // The other tier-1 control of the group's model is still the host's own.
      r.hostSweep = await page.evaluate(() => Boolean(Array.from(document.querySelectorAll('#cards button'))
        .find((b) => b.textContent.trim() === 'Sweep' && b.getClientRects().length)));
      return r;
    """, tmp_path)
    assert out["shut"]["exists"] and out["shut"]["inWell"] and not out["shut"]["inBody"], out
    assert out["shut"]["wellHidden"] and not out["shut"]["shown"], out
    assert out["open"]["shown"] and out["open"]["inWell"] and not out["open"]["inBody"], out
    assert out["hostSweep"], out


@needs_browser
@pytest.mark.usefixtures("test_routes")
def test_sp3_the_live_group_is_tier_one_on_the_models_own_page_and_returns_there(live_station, tmp_path):
    out = _browse(live_station, _LIVE_WHERE + r"""
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(300);
      }
      await until(() => document.querySelectorAll('#cards .card-title').length >= 2);
      const r = {};
      await api('/api/close_model', { name: 'Fake Map' });
      await until(() => document.querySelector('#model-nav [data-model="Fake Live"]'));
      await sleep(400);
      await page.click('#model-nav [data-model="Fake Live"]');
      await sleep(400);
      r.alone = await where();
      await api('/api/open_model', { name: 'Fake Map' });
      await until(() => document.querySelector('#model-nav [data-model="Fake Map"]')
        && !document.querySelector('#model-nav [data-model="Fake Live"]'));
      await sleep(400);
      r.hosted = await where();
      return r;
    """, tmp_path)
    assert out["alone"]["inBody"] and out["alone"]["shown"] and not out["alone"]["inWell"], out
    assert out["hosted"]["inWell"] and not out["hosted"]["inBody"], out


@needs_browser
def test_sp3_the_real_analysis_live_group_is_behind_details_on_the_trial_page(map_station, tmp_path):
    """The Transfer Map page's tier 1 no longer carries the analysis's Live
    group (the procedure strip replaced it); Start run and Stop run are
    behind "RGB analysis details", and the Live group is tier 1 again on the
    analysis's own page."""
    view, controller, launched = map_station
    out = _browse(view, r"""
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(300);
      }
      await page.click('#model-nav [data-model="Transfer Map"]');
      await sleep(600);
      const where = (text) => page.evaluate((t) => {
        const red = Array.from(document.querySelectorAll('#cards .card'))
          .find((c) => (c.querySelector('.card-title') || {}).textContent === 'RGB Analysis');
        const btn = Array.from(document.querySelectorAll('#cards button'))
          .find((b) => b.textContent.trim() === t);
        const wells = Array.from(document.querySelectorAll('#cards .tier-well'));
        return { exists: Boolean(btn),
                 inBody: Boolean(btn && red.querySelector('.card-body').contains(btn)),
                 inWell: Boolean(btn && wells.some((w) => w.contains(btn))),
                 shown: Boolean(btn && btn.getClientRects().length) };
      }, text);
      const r = { shut: await where('Start run') };
      await page.evaluate(() => Array.from(document.querySelectorAll('#cards .disclosure'))
        .find((d) => d.textContent.trim() === 'RGB analysis details').click());
      await sleep(300);
      r.start = await where('Start run');
      r.stop = await where('Stop run');
      return r;
    """, tmp_path)
    for key in ("shut", "start", "stop"):
        assert out[key]["exists"] and out[key]["inWell"] and not out[key]["inBody"], out
    assert not out["shut"]["shown"] and out["start"]["shown"], out


# ==========================================================================
# Picture previews (owner 2026-10-08): a still keyed by `model_attr`
# ==========================================================================
class FakePreview(_Plain):
    """A model with a picture preview: the image is fetched when its key
    changes, not on every poll."""
    NAME = "Fake Preview"

    def __init__(self, png):
        super().__init__()
        self.png = png
        self.key = "1:a.png"
        self.fetched = 0

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Pictures",
            sch.image("Picture", "preview_picture", model_attr="preview_key",
                      empty="No picture", alt_attr="preview_alt"),
            sch.readonly("Shown", "preview_text")))

    @property
    def state(self):
        snapshot = super().state
        snapshot["values"]["preview_alt"] = "100x picture of flake 3 on chip 1, sample 15jul26"
        return snapshot

    @property
    def preview_key(self):
        return self.key

    @property
    def preview_text(self):
        return "3 picture(s) of Izzie's Gift 22April25 · Chip 1 · Flake 12"

    @property
    def preview_picture(self):
        self.fetched += 1
        return self.png


@needs_browser
def test_a_picture_preview_renders_scaled_and_is_fetched_once_per_key(tmp_path):
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (1600, 1200), (200, 90, 30)).save(out, "PNG")
    controller = Controller()
    model = FakePreview(out.getvalue())
    controller.add("Fake Preview", model, {"kind": "Fake Preview"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open()
    try:
        drawn = _browse(view, _PHASE_READ.split("const drawn")[0] + r"""
          await page.setViewport({ width: 390, height: 844 });
          await until(() => {
            const img = document.querySelector('#cards img.picture.is-preview');
            return img && img.complete && img.naturalWidth > 0;
          });
          await sleep(2500);
          return page.evaluate(() => {
            const img = document.querySelector('#cards img.picture.is-preview');
            const box = img.getBoundingClientRect();
            const shown = document.querySelector('#cards [data-attr="preview_text"] .value');
            return { natural: img.naturalWidth, width: box.width, height: box.height,
                     loading: img.loading, alt: img.alt,
                     page: document.documentElement.scrollWidth,
                     view: document.documentElement.clientWidth,
                     word: shown ? shown.classList.contains('is-word') : null };
          });
        """, tmp_path)
    finally:
        view.close()
    assert drawn["natural"] == 1600, drawn
    assert 0 < drawn["width"] <= 390 and drawn["height"] <= 844 * 0.4 + 1, drawn
    assert drawn["loading"] == "lazy"
    assert drawn["alt"] == "100x picture of flake 3 on chip 1, sample 15jul26", drawn
    assert drawn["page"] <= drawn["view"], "nothing spills past a phone"
    assert drawn["word"] is True, "a sentence opening with a count is words"
    # Fetched for its key, not once a poll (a handful of polls went by).
    assert model.fetched <= 2, model.fetched


def test_no_picture_is_the_caption_and_never_a_broken_image(tmp_path):
    """UX audit #19: with no picture the frame says "No picture"; the img
    is hidden, so no broken-image icon (or its alt) is drawn."""
    controller = Controller()
    model = FakePreview(b"")
    controller.add("Fake Preview", model, {"kind": "Fake Preview"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open()
    try:
        drawn = _browse(view, _PHASE_READ.split("const drawn")[0] + r"""
          await until(() => document.querySelector('#cards img.picture.is-preview'));
          await until(() => {
            const note = document.querySelector('#cards .plot-frame.is-preview .empty-note');
            return note && !note.hidden;
          });
          return page.evaluate(() => {
            const img = document.querySelector('#cards img.picture.is-preview');
            const note = document.querySelector('#cards .plot-frame.is-preview .empty-note');
            return { hidden: img.hidden, box: img.getBoundingClientRect().height,
                     note: note.textContent };
          });
        """, tmp_path)
    finally:
        view.close()
    assert drawn["hidden"] is True and drawn["box"] == 0, drawn
    assert "No picture" in drawn["note"], drawn
