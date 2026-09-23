"""Seam note: these were written against `touch_client_heartbeat`, the name
this half chose before it could see the model half, which implemented
`touch_client_liveness`. The lead joined the two on merge; the names here
follow the joined seam. See tests/web/test_web19_seam.py.

WEB-19 (client half of D-8): the browser side of a client-liveness
watchdog. Agent A is building the model side in probes.py - a warn-at-N-s,
FULL-STOP-at-M-s gate folded into the existing interlock watchdog rather
than run as a second timer - in a sibling worktree this test cannot see.

This is a deliberate split (see wave-4 brief). The seam this half exposes:

  - `POST /api/client/heartbeat` -> `WebModelAdapter.record_client_heartbeat()`,
    called from app.js on a bounded interval while the tab is visible, and
    not called while hidden/closed/wedged (js_web19_client_heartbeat_check.js
    covers the visibility half; a wedged request is simply bounded by the
    same shared fetch-timeout wrapper WEB-22 already applies to every
    same-origin fetch).
  - `record_client_heartbeat()` forwards a duck-typed call to
    `model.touch_client_liveness()` (the name is
    `WebModelAdapter.CLIENT_HEARTBEAT_HOOK`) on every active model that
    defines it, and is a no-op on every model that does not - which is
    every model today, since that hook lives in probes.py and is not this
    worktree's file to add. Until agent A's half lands and defines it,
    this half is exercised end-to-end except for the actual fold into the
    interlock watchdog.
"""
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request

import pytest
from unittest.mock import MagicMock

from views.web.web_adapter import WebModelAdapter
from views.web.web_server import WebDashboardServer
from model.system_manager import SystemManager


class TestRecordClientHeartbeatUnit:
    def test_records_last_seen_and_age(self):
        adapter = WebModelAdapter()
        assert adapter.client_heartbeat_age_seconds() is None

        before = time.time()
        result = adapter.record_client_heartbeat()
        after = time.time()

        assert result["status"] == "ok"
        assert before <= result["last_seen"] <= after
        age = adapter.client_heartbeat_age_seconds()
        assert age is not None
        assert 0 <= age < 2.0

    def test_forwards_to_models_that_define_the_hook(self):
        touched = MagicMock()
        model_with_hook = MagicMock()
        model_with_hook.touch_client_liveness = touched

        manager = MagicMock(spec=SystemManager)
        manager.get_active_models_snapshot = MagicMock(
            return_value={"Stepper Probe": model_with_hook})

        adapter = WebModelAdapter(system_manager=manager)
        result = adapter.record_client_heartbeat()

        touched.assert_called_once()
        assert result["touched"] == ["Stepper Probe"]

    def test_does_not_touch_models_without_the_hook(self):
        """Every model today. Also proves this never calls touch_activity
        instead - conflating the two would silently defeat the idle-disable
        timer (RC-3 item 5 / STEPPER-7) for a tab that is merely open."""
        model_without_hook = MagicMock(spec=["ui_schema"])
        model_without_hook.ui_schema = {"sections": []}

        manager = MagicMock(spec=SystemManager)
        manager.get_active_models_snapshot = MagicMock(
            return_value={"Stepper Probe": model_without_hook})

        adapter = WebModelAdapter(system_manager=manager)
        result = adapter.record_client_heartbeat()

        assert result["touched"] == []
        assert not hasattr(model_without_hook, "touch_activity")

    def test_a_raising_hook_does_not_fail_the_heartbeat(self):
        broken_model = MagicMock()
        broken_model.touch_client_liveness = MagicMock(side_effect=RuntimeError("boom"))

        manager = MagicMock(spec=SystemManager)
        manager.get_active_models_snapshot = MagicMock(
            return_value={"Broken": broken_model})

        adapter = WebModelAdapter(system_manager=manager)
        result = adapter.record_client_heartbeat()

        assert result["status"] == "ok"
        assert result["touched"] == []

    def test_no_manager_is_still_a_successful_heartbeat(self):
        adapter = WebModelAdapter(system_manager=None)
        result = adapter.record_client_heartbeat()
        assert result["status"] == "ok"
        assert result["touched"] == []


@pytest.fixture
def web_heartbeat_server():
    adapter = WebModelAdapter(system_manager=None, mode="setup")
    server = WebDashboardServer(host="127.0.0.1", port=18081, adapter=adapter)
    server.start(background=True)
    base_url = f"http://{server.host}:{server.port}"
    time.sleep(0.05)
    yield server, base_url, adapter
    server.stop()


def _post(url, data):
    from views.web.web_server import SESSION_TOKEN, TOKEN_HEADER
    body = json.dumps(data).encode("utf-8")
    headers = {"Content-Type": "application/json", TOKEN_HEADER: SESSION_TOKEN}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_post_client_heartbeat_endpoint(web_heartbeat_server):
    server, base_url, adapter = web_heartbeat_server
    assert adapter.client_heartbeat_age_seconds() is None

    status, data = _post(f"{base_url}/api/client/heartbeat", {})

    assert status == 200
    assert data["status"] == "ok"
    assert adapter.client_heartbeat_age_seconds() is not None


NODE = shutil.which("node")
HERE = os.path.dirname(__file__)
APP_JS = os.path.join(HERE, "..", "..", "src", "views", "web", "static", "js", "app.js")
HARNESS = os.path.join(HERE, "js_web19_client_heartbeat_check.js")


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_heartbeat_stops_when_tab_hidden_and_resumes_when_visible():
    """The client-side half: startClientHeartbeat/stopClientHeartbeat must
    track document visibility, not merely run on a fixed interval - a
    backgrounded tab must not keep sending heartbeats, and a
    foregrounded one must resume without a page reload."""
    result = subprocess.run(
        [NODE, HARNESS, APP_JS],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}")
    assert "OK" in result.stdout
