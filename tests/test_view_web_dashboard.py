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
