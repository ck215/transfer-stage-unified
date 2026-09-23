"""RC-10 item 2 — the web dashboard's security boundary.

This server drives physical hardware from an unauthenticated localhost port.
Before this, any page the operator's browser happened to visit could issue a
cross-site form POST to `/api/command` and move the stage, and could GET
`/api/screenshot` to read the operator's screen. Browsers send such "simple"
requests with no preflight and no prompt.

These tests act as that cross-site caller.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from model.system_manager import SystemManager
from views.web.web_server import SESSION_TOKEN, TOKEN_HEADER, WebDashboardServer


@pytest.fixture
def server():
    srv = WebDashboardServer(SystemManager(), host="127.0.0.1", port=0)
    srv.start(background=True)
    yield srv
    srv.stop()


def _request(server, path, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode("utf-8") if e.fp else "")


def _authed(extra=None):
    h = {"Content-Type": "application/json", TOKEN_HEADER: SESSION_TOKEN}
    h.update(extra or {})
    return h


# --------------------------------------------------------------------------
# The hole: a cross-site POST could drive the stage.
# --------------------------------------------------------------------------


def test_a_post_without_the_token_cannot_command_the_stage(server):
    status, _ = _request(
        server, "/api/command", "POST",
        {"device": "Stepper Probe", "command": "macro_start_auton"},
        headers={"Content-Type": "application/json"})
    assert status == 403


def test_a_cross_site_form_post_is_refused_on_content_type(server):
    """A cross-site <form> can only send text/plain, form-urlencoded or
    multipart. Requiring JSON forces a preflight the browser will refuse, so
    this check alone defeats the classic no-JS CSRF."""
    status, _ = _request(
        server, "/api/command", "POST",
        {"device": "Stepper Probe", "command": "macro_start_auton"},
        headers={"Content-Type": "text/plain"})
    assert status == 415


def test_a_post_from_another_origin_is_refused_even_with_json(server):
    status, _ = _request(
        server, "/api/command", "POST", {"device": "x", "command": "y"},
        headers=_authed({"Origin": "https://evil.example"}))
    assert status == 403


def test_the_dashboards_own_origin_is_accepted(server):
    status, _ = _request(
        server, "/api/command", "POST", {"device": "nope", "command": "nope"},
        headers=_authed({"Origin": f"http://127.0.0.1:{server.port}"}))
    assert status != 403, "the dashboard's own requests must not be refused"


def test_a_wrong_token_is_refused(server):
    status, _ = _request(
        server, "/api/command", "POST", {"device": "x", "command": "y"},
        headers={"Content-Type": "application/json", TOKEN_HEADER: "not-the-token"})
    assert status == 403


# --------------------------------------------------------------------------
# The screenshot endpoint serves the operator's actual screen.
# --------------------------------------------------------------------------


def test_screenshot_requires_the_token(server):
    status, _ = _request(server, "/api/screenshot")
    assert status == 403, "an unauthenticated caller could read the operator's screen"


def test_screenshot_refuses_a_foreign_origin(server):
    status, _ = _request(
        server, "/api/screenshot",
        headers={TOKEN_HEADER: SESSION_TOKEN, "Origin": "https://evil.example"})
    assert status == 403


# --------------------------------------------------------------------------
# How the dashboard gets the token in the first place.
# --------------------------------------------------------------------------


def test_the_served_html_carries_the_token(server):
    """A cross-site page cannot read this, because it cannot read our HTML.
    That same-origin restriction is the whole mechanism."""
    status, body = _request(server, "/index.html")
    assert status == 200
    assert f'<meta name="stage-token" content="{SESSION_TOKEN}">' in body


def test_reads_stay_open_so_the_dashboard_can_boot(server):
    """/api/state and friends are reads. Locking them down would break the
    first paint before the page has run any script; the boundary that matters
    is the one on commands and on the screen grab."""
    status, _ = _request(server, "/api/state")
    assert status == 200


# --------------------------------------------------------------------------
# RC-10 item 1 — one manager, read through, never copied.
# --------------------------------------------------------------------------


def test_window_and_server_read_the_live_manager_not_a_stored_copy(server):
    """Five objects used to hold a manager and only the adapter's stayed live,
    because re-setup replaces it. The window's stale copy is why close() shut
    down the original, empty manager on Ctrl-C and left the real models
    running (MANAGER-1, TEMP-1, ROTATOR-2)."""
    from views.web.web_view import WebDashboardWindow

    first = SystemManager()
    window = WebDashboardWindow(first, port=0, open_browser=False)
    assert window.system_manager is first
    assert window.server.system_manager is first

    replacement = SystemManager()
    window.server.adapter.set_system_manager(replacement)

    assert window.system_manager is replacement, "the window held a stale manager"
    assert window.server.system_manager is replacement, "the server held a stale manager"


def test_concurrent_re_setup_is_refused_rather_than_raced():
    """Two rebuilds would each tear down and re-open the same ports, and the
    loser would leave orphaned models holding them."""
    from views.web.web_adapter import WebModelAdapter

    adapter = WebModelAdapter(SystemManager())
    adapter._reconfiguring.acquire()
    try:
        result = adapter.initialize_setup([{"device": "Stepper Probe", "port": "SIM"}])
    finally:
        adapter._reconfiguring.release()

    assert result["code"] == 409
    assert "already in progress" in result["message"]


def test_re_setup_releases_the_single_flight_lock_on_failure():
    """A failed rebuild must not wedge the endpoint for the rest of the session."""
    from views.web.web_adapter import WebModelAdapter

    adapter = WebModelAdapter(SystemManager())
    adapter.initialize_setup([])          # rejected: empty configs
    second = adapter.initialize_setup([])  # must be rejected for the same reason
    assert second["code"] != 409


# --------------------------------------------------------------------------
# DC-13 — the badge tells the truth about the link.
# --------------------------------------------------------------------------


def test_the_badge_cannot_be_faked_by_typing_SIM_into_the_port_field():
    """The badge used to be inferred from the editable serial_port field, so
    typing "SIM" into a hardware probe's port box relabelled it SIMULATED
    while it went on driving real hardware (DC-13). It now asks the
    transport, which knows."""
    from controller.serial import ConnectionState
    from views.web.web_adapter import WebModelAdapter

    class FakeTransport:
        connection_state = ConnectionState.VERIFIED

    class Probe:
        ui_schema = {"sections": []}
        serial_port = "SIM"          # the operator typed this
        serial_comm = FakeTransport()  # the hardware link is real

    assert WebModelAdapter()._determine_connection_status(Probe()) == "hardware"


def test_an_unverified_link_is_badged_distinctly_from_a_working_one():
    """Opening a port is not the same as the board answering."""
    from controller.serial import ConnectionState
    from views.web.web_adapter import WebModelAdapter

    class Probe:
        ui_schema = {"sections": []}

        class serial_comm:
            connection_state = ConnectionState.UNVERIFIED

    badge = WebModelAdapter()._determine_connection_status(Probe())
    assert badge == "unverified"
    assert badge != "hardware"


def test_a_lost_link_badges_as_disconnected():
    from controller.serial import ConnectionState
    from views.web.web_adapter import WebModelAdapter

    class Probe:
        ui_schema = {"sections": []}

        class serial_comm:
            connection_state = ConnectionState.LOST

    assert WebModelAdapter()._determine_connection_status(Probe()) == "disconnected"
