"""The browser-liveness watchdog (D-8 / D-8a, WEB-19 / WEB-23) and the
server's own lifecycle.

Driven on a fake clock, so the thresholds are exercised in milliseconds and
nothing here sleeps. Ported from `tests/core/test_web19_client_liveness_
watchdog.py` and `tests/core/test_web23_heater_rotator_liveness.py`, which
tested three private copies of this gate - one in `BaseProbe`, one mixed into
the heater and one into the rotator. There is one now, and it belongs to the
view that has the browser, not to any model.
"""
import socket
import threading

import pytest

from station.events import events
from station.views.web.server import WebView


class FakeController:
    """Only what the watchdog and `close()` touch."""

    def __init__(self, is_active=False):
        self.is_active = is_active
        self.estop_calls = 0
        self.closed = 0

    def estop_all(self):
        self.estop_calls += 1
        return {"Probe": True}

    def close(self):
        self.closed += 1


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def watched():
    clock = Clock()
    controller = FakeController()
    view = WebView(controller, object(), port=0, open_browser=False, clock=clock)
    return view, controller, clock


@pytest.fixture
def captured():
    """Every event published during the test, newest last."""
    seen = []
    # A repeat inside DEDUPE_SECONDS updates a count instead of notifying, so
    # two tests that publish the identical sentence would hide each other.
    events.clear()
    events.subscribe(seen.append)
    try:
        yield seen
    finally:
        events.unsubscribe(seen.append)


# --------------------------------------------------------------------------
# no gate until a browser has checked in
# --------------------------------------------------------------------------
def test_silence_before_any_client_never_stops_anything(watched):
    """A Tk or Qt session never calls beat(), so it can never be stopped by
    this - which is why the check is `is None`, not `age > threshold`."""
    view, controller, clock = watched
    controller.is_active = True
    clock.advance(10 * view.STOP_SECONDS)
    view._check_heartbeat()
    assert controller.estop_calls == 0
    assert view.heartbeat_age is None


def test_a_heartbeat_arms_the_gate_and_reports_its_age(watched):
    view, controller, clock = watched
    view.beat()
    assert view.heartbeat_age == 0.0
    clock.advance(2.0)
    assert view.heartbeat_age == 2.0


# --------------------------------------------------------------------------
# never while idle
# --------------------------------------------------------------------------
def test_silence_while_idle_never_stops_anything(watched):
    view, controller, clock = watched
    controller.is_active = False
    view.beat()
    clock.advance(view.STOP_SECONDS * 3)
    view._check_heartbeat()
    assert controller.estop_calls == 0, (
        "an idle station was FULL STOPped for a browser that went away")


# --------------------------------------------------------------------------
# warn, then stop, once
# --------------------------------------------------------------------------
def test_silence_while_active_warns_once_then_stops_once(watched, captured):
    view, controller, clock = watched
    controller.is_active = True
    view.beat()

    clock.advance(view.WARN_SECONDS / 2)
    view._check_heartbeat()
    assert [e for e in captured if e.severity == "warning"] == []

    clock.advance(view.WARN_SECONDS)          # past WARN, short of STOP
    view._check_heartbeat()
    view._check_heartbeat()
    warnings = [e for e in captured if e.severity == "warning"]
    assert len(warnings) == 1, "the warning repeated every tick"
    assert "checked in" in warnings[0].message
    assert controller.estop_calls == 0

    clock.advance(view.STOP_SECONDS)          # now past STOP
    view._check_heartbeat()
    assert controller.estop_calls == 1
    view._check_heartbeat()
    view._check_heartbeat()
    assert controller.estop_calls == 1, (
        "the watchdog re-stopped a station whose stop frame never landed")
    assert any(e.severity == "error" for e in captured)


def test_a_fresh_heartbeat_rearms_the_gate(watched, captured):
    view, controller, clock = watched
    controller.is_active = True
    view.beat()
    clock.advance(view.STOP_SECONDS + 1)
    view._check_heartbeat()
    assert controller.estop_calls == 1

    view.beat()                      # the operator's tab came back
    clock.advance(view.STOP_SECONDS + 1)
    view._check_heartbeat()
    assert controller.estop_calls == 2, (
        "a client that came back and went away again was never gated")


def test_the_latch_lands_before_the_popup(watched, captured):
    """Latch first, then report: safety-pattern.md rule 1."""
    view, controller, clock = watched
    order = []

    def _estop_all():
        order.append("estop")
        return {}

    def _watch(event):
        if event.severity == "error":
            order.append("error")

    controller.estop_all = _estop_all
    events.subscribe(_watch)
    try:
        controller.is_active = True
        view.beat()
        clock.advance(view.STOP_SECONDS + 1)
        view._check_heartbeat()
    finally:
        events.unsubscribe(_watch)
    assert order == ["estop", "error"]


def test_the_thresholds_are_class_attributes_a_bench_session_can_change():
    """D-8a: PROVISIONAL until measured. They are one pair on the view now,
    not three pairs spread across three models."""
    assert WebView.WARN_SECONDS == 5.0
    assert WebView.STOP_SECONDS == 15.0
    assert WebView.WARN_SECONDS < WebView.STOP_SECONDS


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------
def _watchdog_threads():
    return [t for t in threading.enumerate() if t.name == "web-watchdog"]


def test_open_starts_the_watchdog_and_close_stops_it(watched):
    view, controller, _ = watched
    before = len(_watchdog_threads())
    assert view.open()
    assert len(_watchdog_threads()) == before + 1
    view.close()
    assert _watchdog_threads() == [t for t in _watchdog_threads() if t.is_alive() is False] \
        or len(_watchdog_threads()) == before
    assert controller.closed == 1, "close() did not close the Controller"


def test_close_is_safe_to_call_twice(watched):
    view, controller, _ = watched
    view.open()
    view.close()
    view.close()
    assert controller.closed == 2  # Controller.close() is itself idempotent


def test_a_busy_port_is_reported_as_an_event_not_a_traceback(captured):
    """WEB-16: only EADDRINUSE walks up the range, and running out of ports
    says so instead of raising an AttributeError out of serve_forever."""
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    busy = holder.getsockname()[1]
    try:
        view = WebView(FakeController(), object(), port=busy,
                       open_browser=False)
        view.PORT_ATTEMPTS = 1
        assert view.open() == "", "it claimed to be serving on a busy port"
        assert any(e.severity == "error" and "Web Server" in e.title
                   for e in captured)
    finally:
        holder.close()


def test_a_busy_port_walks_up_to_the_next_one(captured):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    busy = holder.getsockname()[1]
    view = WebView(FakeController(), object(), port=busy, open_browser=False)
    try:
        url = view.open()
        assert url and view.port != busy
    finally:
        view.close()
        holder.close()
