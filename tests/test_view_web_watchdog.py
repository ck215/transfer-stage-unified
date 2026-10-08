"""The browser-liveness watchdog (D-8 / D-8a, WEB-19 / WEB-23) and the
server's own lifecycle.

Driven on a fake clock, so the thresholds are exercised in milliseconds and
nothing here sleeps. Ported from `tests/core/test_web19_client_liveness_
watchdog.py` and `tests/core/test_web23_heater_rotator_liveness.py`, which
tested three private copies of this gate - one in `BaseProbe`, one mixed into
the heater and one into the rotator. There is one now, and it belongs to the
view that has the browser, not to any model.
"""
import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.request

import pytest

from events import events
from views.web.server import ApiHandler, WebView


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

    def state(self):
        return {"models": {}, "is_estopped": False}


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


# --------------------------------------------------------------------------
# G1: a browser that goes away is not a traceback on the terminal
# --------------------------------------------------------------------------
@pytest.fixture
def debug_titles(monkeypatch):
    """`events.debug` goes to the log file only; record the titles here."""
    seen = []
    real = events.debug

    def record(title, message, **kwargs):
        seen.append(title)
        return real(title, message, **kwargs)

    monkeypatch.setattr(events, "debug", record)
    return seen


def _until(predicate, seconds=2.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_a_reset_connection_is_not_a_traceback(capfd, debug_titles):
    """Closing the tab resets every keep-alive connection the browser held.
    Each one parked a handler thread in readline, and the stdlib's
    `BaseServer.handle_error` printed a ConnectionResetError traceback per
    thread to stderr (G1). It is a debug line in the log file now."""
    view = WebView(FakeController(), object(), port=0, open_browser=False)
    assert view.open()
    try:
        client = socket.create_connection(("127.0.0.1", view.port), timeout=5)
        client.sendall(b"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        reply = b""
        while b"\r\n\r\n" not in reply:
            reply += client.recv(4096)
        assert reply.startswith(b"HTTP/1.1 200"), reply[:80]
        time.sleep(0.1)     # the handler is back in readline, keep-alive
        # SO_LINGER 0: close() sends an RST - what a closing browser sends.
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()
        assert _until(lambda: "Client Went Away" in debug_titles), debug_titles
        time.sleep(0.1)
    finally:
        view.close()
    err = capfd.readouterr().err
    assert "Traceback" not in err and "ConnectionResetError" not in err, err
    assert err == "", err


def test_a_handler_crash_is_an_error_event(capfd, captured, monkeypatch):
    """Anything else that escapes a handler is a real fault: an error event
    (the log file with its traceback, and the tray) - never a traceback on
    the terminal, and never an acknowledgement modal from a connection
    thread."""
    def explode(self):
        raise RuntimeError("the handler blew up")

    monkeypatch.setattr(ApiHandler, "do_GET", explode)
    view = WebView(FakeController(), object(), port=0, open_browser=False)
    assert view.open()
    try:
        url = f"http://127.0.0.1:{view.port}/api/state"
        with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):
            urllib.request.urlopen(url, timeout=5).read()
        assert _until(lambda: any(e.severity == "error" for e in captured))
    finally:
        view.close()
    errors = [e for e in captured if e.severity == "error"]
    assert len(errors) == 1, errors
    assert isinstance(errors[0].exception, RuntimeError)
    assert "the handler blew up" in errors[0].message
    assert errors[0].needs_ack is False, "a pop-up from a connection thread"
    err = capfd.readouterr().err
    assert "Traceback" not in err, err


def test_an_idle_keep_alive_connection_retires_quietly(capfd, monkeypatch):
    """G1, second half: without a timeout an idle keep-alive thread lived
    until the browser dropped the socket. `handle_one_request` already turns
    a socket timeout into close-connection; the handler just never had one.
    The station's own tab polls every 250 ms, so a connection it is using
    never idles this long."""
    assert ApiHandler.timeout == 120
    monkeypatch.setattr(ApiHandler, "timeout", 0.3)
    view = WebView(FakeController(), object(), port=0, open_browser=False)
    assert view.open()
    try:
        client = socket.create_connection(("127.0.0.1", view.port), timeout=5)
        client.sendall(b"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        reply = b""
        while b"\r\n\r\n" not in reply:
            reply += client.recv(4096)
        body = reply.split(b"\r\n\r\n", 1)[1]
        length = int(next(line.split(b":")[1] for line in reply.split(b"\r\n")
                          if line.lower().startswith(b"content-length")))
        while len(body) < length:
            body += client.recv(4096)
        client.settimeout(3)
        assert client.recv(4096) == b"", "the idle connection was not closed"
        client.close()
    finally:
        view.close()
    assert capfd.readouterr().err == ""


# --------------------------------------------------------------------------
# G2: Quit from the console
# --------------------------------------------------------------------------
def _post_quit(view, method="POST"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{view.port}/api/quit",
        data=b"{}" if method == "POST" else None, method=method,
        headers={"Content-Type": "application/json",
                 "Origin": f"http://127.0.0.1:{view.port}"} if method == "POST" else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_quit_answers_then_releases_wait():
    """POST /api/quit answers ok first, then releases the launcher's
    `wait()`, which closes the server and the Controller on ITS thread - the
    handler never calls close() itself (G2)."""
    controller = FakeController()
    view = WebView(controller, object(), port=0, open_browser=False)
    assert view.open()
    waiter = threading.Thread(target=view.wait, name="launcher", daemon=True)
    waiter.start()
    try:
        time.sleep(0.2)
        assert waiter.is_alive(), "wait() returned before anyone asked to quit"
        # Updated (O5): the answer carries the stop Quit ran first.
        assert _post_quit(view) == (200, {"status": "ok", "stopped": {"Probe": True},
                                          "unconfirmed": []})
        assert controller.estop_calls == 1, "Quit did not stop before answering"
        waiter.join(timeout=1.0)
        assert not waiter.is_alive(), "wait() was not released within 1 s"
        assert controller.closed == 1, "the Controller was not closed"
        assert not view.is_serving
    finally:
        view.close()


def test_a_second_quit_is_ok_not_an_error():
    controller = FakeController()
    view = WebView(controller, object(), port=0, open_browser=False)
    assert view.open()
    try:
        # Updated (O5): the same answer twice, and one stop, not two.
        first = _post_quit(view)
        assert first == (200, {"status": "ok", "stopped": {"Probe": True},
                               "unconfirmed": []})
        assert _post_quit(view) == first and controller.estop_calls == 1
        assert controller.closed == 0, "a handler thread closed the Controller"
        waiter = threading.Thread(target=view.wait, daemon=True)
        waiter.start()
        waiter.join(timeout=1.0)
        assert not waiter.is_alive() and controller.closed == 1
    finally:
        view.close()


def test_quit_is_a_post_not_a_get():
    """Like every other command route: a GET is a 404, and quits nothing."""
    controller = FakeController()
    view = WebView(controller, object(), port=0, open_browser=False)
    assert view.open()
    try:
        status, data = _post_quit(view, method="GET")
        assert status == 404 and data["status"] == "error"
        waiter = threading.Thread(target=view.wait, daemon=True)
        waiter.start()
        waiter.join(timeout=0.8)
        assert waiter.is_alive(), "a GET released wait()"
        assert controller.closed == 0
    finally:
        view.close()
        waiter.join(timeout=2.0)


def test_silence_while_energized_but_not_active_still_stops(watched, captured):
    """Round 8 (IMP8-7): a probe held in autonomous mode is energized but not
    active; a closed tab left it powered. The watchdog keys on energized."""
    view, controller, clock = watched
    controller.is_active = False
    controller.is_energized = True
    view.beat()
    clock.now += view.STOP_SECONDS + 0.1
    view._check_heartbeat()
    assert controller.estop_calls == 1


# --------------------------------------------------------------------------
# V4 (rb-link-views): the warning says why a healthy tab goes silent
# --------------------------------------------------------------------------
def test_v4_browser_silent_tells_the_operator_how_to_recover(watched, captured):
    """A background tab is throttled or slept by the browser: the station
    is fine, the page is not checking in. The warning says so and says what
    to do - keep the station in its own window - before the stop fires."""
    view, controller, clock = watched
    controller.is_active = True
    view.beat()
    clock.advance(view.WARN_SECONDS + 0.2)
    view._check_heartbeat()
    warning = next(e for e in captured if e.severity == "warning")
    assert warning.title == events.BROWSER_SILENT
    message = warning.message.lower()
    assert "throttl" in message and "sleep" in message
    assert "keep the station in its own window" in message
    assert f"at {view.STOP_SECONDS:.0f} s" in warning.message


# --------------------------------------------------------------------------
# Closing the last tab quits the station (owner 2026-10-07): "make sure
# closing the browser/tab also closes the server". A page's pagehide posts
# /api/leave; no page checking in within the grace quits by the Quit
# control's own path (every model stopped, then wait() released, which runs
# close()). A reload or another tab checks in inside the grace. The backstop
# quits after a longer silence once a page had checked in.
# --------------------------------------------------------------------------
def _titles(captured):
    return [e.title for e in captured]


def test_a_closed_last_tab_quits_after_the_grace(watched, captured):
    view, controller, clock = watched
    view.beat(page="a")
    assert view.leave(page="a") == view.leave_grace
    clock.advance(view.leave_grace - 0.1)
    view._check_heartbeat()
    assert controller.estop_calls == 0 and not view._halt.is_set(), (
        "quit before the grace was over: a reload would have been cut off")
    clock.advance(0.2)
    view._check_heartbeat()
    assert controller.estop_calls == 1, "the quit did not stop every model first"
    assert view._halt.is_set(), "wait() was not released"
    titles = _titles(captured)
    assert "Tab Closed" in titles and "No Browser Left" in titles and "Quit" in titles
    said = next(e for e in captured if e.title == "No Browser Left").message
    assert "no page has checked in" in said and "grace" in said
    view._check_heartbeat()
    assert controller.estop_calls == 1, "quit twice"


def test_an_idle_station_quits_too(watched):
    """Idle or energized alike: the owner's ask is the server, not the stop."""
    view, controller, clock = watched
    controller.is_active = False
    view.beat(page="a")
    view.leave(page="a")
    clock.advance(view.leave_grace + 0.1)
    view._check_heartbeat()
    assert view._halt.is_set()


def test_a_reload_checks_in_within_the_grace_and_cancels_the_quit(watched, captured):
    view, controller, clock = watched
    view.beat(page="old")
    view.leave(page="old")
    clock.advance(1.5)
    view.beat(page="new")                 # the reloaded page, under a new id
    assert "Quit Cancelled" in _titles(captured)
    for _ in range(10):
        clock.advance(2.0)
        view.beat(page="new")
        view._check_heartbeat()
    assert controller.estop_calls == 0 and not view._halt.is_set()


def test_a_beat_in_flight_from_the_page_that_left_does_not_cancel(watched):
    """The worker's last beat can land after the leave; it is the old page,
    not a page coming back."""
    view, controller, clock = watched
    view.beat(page="a")
    view.leave(page="a")
    clock.advance(0.3)
    view.beat(page="a")
    clock.advance(view.leave_grace)
    view._check_heartbeat()
    assert view._halt.is_set(), "a late beat from the closed tab kept the station up"


def test_with_two_tabs_only_the_last_one_closing_quits(watched):
    view, controller, clock = watched
    view.beat(page="a")
    view.beat(page="b")
    view.leave(page="a")
    for _ in range(15):                   # tab b keeps checking in, 30 s
        clock.advance(2.0)
        view.beat(page="b")
        view._check_heartbeat()
    assert not view._halt.is_set() and controller.estop_calls == 0
    view.leave(page="b")
    clock.advance(view.leave_grace + 0.1)
    view._check_heartbeat()
    assert view._halt.is_set()


def test_the_backstop_quits_after_a_long_silence_without_a_leave(watched, captured):
    """A crashed or killed browser sends no leave."""
    view, controller, clock = watched
    view.beat()
    clock.advance(view.quit_after_silence - 0.5)
    view._check_heartbeat()
    assert not view._halt.is_set()
    clock.advance(1.0)
    view._check_heartbeat()
    assert view._halt.is_set() and controller.estop_calls == 1
    said = next(e for e in captured if e.title == "No Browser Left").message
    assert "backstop" in said


def test_the_backstop_never_fires_before_any_page_checked_in(watched):
    view, controller, clock = watched
    clock.advance(view.quit_after_silence * 10)
    view._check_heartbeat()
    assert not view._halt.is_set() and controller.estop_calls == 0


def test_while_energized_the_full_stop_still_comes_first_then_the_backstop(watched):
    """The 15 s FULL STOP is unchanged; the quit comes after it."""
    view, controller, clock = watched
    controller.is_active = True
    view.beat()
    clock.advance(view.STOP_SECONDS + 1)
    view._check_heartbeat()
    assert controller.estop_calls == 1 and not view._halt.is_set()
    clock.advance(view.quit_after_silence)
    view._check_heartbeat()
    assert view._halt.is_set() and controller.estop_calls == 2


def test_the_grace_and_the_backstop_are_set_from_the_environment(monkeypatch):
    assert WebView.LEAVE_GRACE_SECONDS == 8.0
    assert WebView.QUIT_AFTER_SILENCE_SECONDS == 60.0
    assert (WebView.LEAVE_GRACE_SECONDS < WebView.STOP_SECONDS
            < WebView.QUIT_AFTER_SILENCE_SECONDS)
    monkeypatch.setenv("STATION_LEAVE_GRACE_SECONDS", "0.25")
    monkeypatch.setenv("STATION_QUIT_AFTER_SILENCE_SECONDS", "3")
    view = WebView(FakeController(), object(), port=0, open_browser=False)
    assert (view.leave_grace, view.quit_after_silence) == (0.25, 3.0)
    monkeypatch.setenv("STATION_LEAVE_GRACE_SECONDS", "nonsense")
    monkeypatch.setenv("STATION_QUIT_AFTER_SILENCE_SECONDS", "-1")
    view = WebView(FakeController(), object(), port=0, open_browser=False)
    assert (view.leave_grace, view.quit_after_silence) == (8.0, 60.0)


def _post(view, route, body, origin=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{view.port}{route}", data=json.dumps(body).encode(),
        method="POST", headers={"Content-Type": "application/json",
                                "Origin": origin or f"http://127.0.0.1:{view.port}"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


@pytest.fixture
def served(monkeypatch):
    """A real server on an ephemeral port, its launcher's wait() on a thread,
    with a short grace so the real watchdog thread decides in under a second."""
    monkeypatch.setenv("STATION_LEAVE_GRACE_SECONDS", "0.6")
    controller = FakeController()
    view = WebView(controller, object(), port=0, open_browser=False)
    assert view.open()
    waiter = threading.Thread(target=view.wait, name="launcher", daemon=True)
    waiter.start()
    try:
        yield view, controller, waiter
    finally:
        view.close()
        waiter.join(timeout=2.0)


def test_the_leave_route_quits_the_served_station_the_quit_way(served):
    view, controller, waiter = served
    assert _post(view, "/api/heartbeat", {"page": "a"})[0] == 200
    status, answer = _post(view, "/api/leave", {"page": "a"})
    assert (status, answer) == (200, {"status": "ok", "quit_in": 0.6})
    waiter.join(timeout=3.0)
    assert not waiter.is_alive(), "the last tab closed and the station kept running"
    assert controller.estop_calls == 1 and controller.closed == 1
    assert not view.is_serving


def test_the_leave_route_then_a_reload_keeps_the_station_up(served):
    view, controller, waiter = served
    _post(view, "/api/heartbeat", {"page": "a"})
    _post(view, "/api/leave", {"page": "a"})
    _post(view, "/api/heartbeat", {"page": "b"})
    deadline = time.monotonic() + 1.8     # three graces
    while time.monotonic() < deadline:
        _post(view, "/api/heartbeat", {"page": "b"})
        time.sleep(0.2)
    assert waiter.is_alive() and controller.estop_calls == 0 and view.is_serving


def test_the_leave_route_is_guarded_like_every_other_post(served):
    """A page on another origin cannot quit the station."""
    view, controller, waiter = served
    _post(view, "/api/heartbeat", {"page": "a"})
    status, _ = _post(view, "/api/leave", {"page": "a"}, origin="http://evil.example")
    assert status == 403
    time.sleep(1.2)
    assert waiter.is_alive() and controller.estop_calls == 0
