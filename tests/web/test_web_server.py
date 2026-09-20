import pytest
import json
import urllib.request
import urllib.parse
import urllib.error
import time
import threading
from views.web.web_server import WebDashboardServer, WebAPIHandler

class MockDeviceModel:
    def __init__(self, name="TestStage"):
        self.name = name
        self.step_size = 10
        self.velocity = 5.5
        self.enabled = True
        self.label = "Sample X"
        self.pos_x = 100.0
        self.polled_count = 0
        self.position_read_count = 0
        self.executed_commands = []

    @property
    def ui_schema(self):
        return {
            "sections": [
                {
                    "title": "Motion Control",
                    "elements": [
                        {"type": "entry", "text": "Step:", "model_attr": "step_size"},
                        {"type": "entry", "text": "Speed:", "model_attr": "velocity"},
                        {"type": "entry", "text": "Active:", "model_attr": "enabled"},
                        {"type": "entry", "text": "Tag:", "model_attr": "label"},
                        {"type": "readonly", "text": "Pos:", "model_attr": "pos_x"},
                        {"type": "button", "text": "Home", "command": "home_axis"},
                        {"type": "button", "text": "Move", "command": "move_rel"},
                        {"type": "button", "text": "Single Arg", "command": "single_arg_cmd"},
                        {"type": "button", "text": "Dict Arg", "command": "dict_arg_cmd"},
                        {"type": "button", "text": "Slow Task", "command": "slow_task"},
                        {"type": "button", "text": "Fail", "command": "failing_command"},
                        {"type": "dropdown", "text": "Options:", "model_attr": "opt_val", "options_command": "get_opts"}
                    ]
                }
            ]
        }

    def get_opts(self):
        return ["A", "B", "C"]

    def home_axis(self):
        self.executed_commands.append(("home_axis", ()))
        return "homed_successfully"

    def move_rel(self, distance, speed=None):
        self.executed_commands.append(("move_rel", (distance, speed)))
        return f"moved_{distance}"

    def single_arg_cmd(self, value):
        self.executed_commands.append(("single_arg_cmd", value))
        return f"val_{value}"

    def dict_arg_cmd(self, x=0, y=0):
        self.executed_commands.append(("dict_arg_cmd", (x, y)))
        return f"coords_{x}_{y}"

    def slow_task(self, duration=0.2):
        time.sleep(duration)
        self.executed_commands.append(("slow_task", (duration,)))
        return "slow_done"

    def failing_command(self):
        raise RuntimeError("Hardware motor fault")

    def read_position(self):
        self.position_read_count += 1
        self.pos_x += 0.5

    def poll_status(self):
        self.polled_count += 1


class MockSystemManager:
    def __init__(self):
        self.active_models = {
            "Stage_A": MockDeviceModel("Stage_A"),
            "Stage_B": MockDeviceModel("Stage_B")
        }

    def get_active_models_snapshot(self):
        # The adapter reads models through the manager's thread-safe accessor
        # rather than reaching into active_models (I-1.5).
        return dict(self.active_models)


@pytest.fixture
def web_server_fixture():
    # Reset buffer state before tests
    WebAPIHandler.log_buffer.clear()
    WebAPIHandler.error_buffer.clear()
    mgr = MockSystemManager()
    server = WebDashboardServer(mgr, port=9100)
    server.start(background=True)
    yield server, mgr
    server.stop()
    WebAPIHandler.log_buffer.clear()
    WebAPIHandler.error_buffer.clear()


def make_request(url, method="GET", json_data=None, token=True, extra_headers=None):
    """Issue a request the way the dashboard does.

    The server requires a per-launch session token on every POST and on
    /api/screenshot (RC-10). Tests that exercise the boundary itself pass
    token=False to act as a cross-site caller would.
    """
    from views.web.web_server import SESSION_TOKEN, TOKEN_HEADER

    data = json.dumps(json_data).encode("utf-8") if json_data is not None else None
    headers = {"Content-Type": "application/json"} if json_data is not None else {}
    if token:
        headers[TOKEN_HEADER] = SESSION_TOKEN
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, resp.headers, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        return e.code, e.headers, body


# -----------------------------------------------------------------------------
# Static Asset Serving Tests
# -----------------------------------------------------------------------------

def test_static_asset_index(web_server_fixture):
    server, _ = web_server_fixture
    base = f"http://127.0.0.1:{server.port}"
    
    # Root route '/' should serve index.html
    status, headers, body = make_request(f"{base}/")
    assert status == 200
    assert "text/html" in headers.get("Content-Type", "")
    assert "<!DOCTYPE html>" in body or "Transfer Stage" in body

    # Explicit '/index.html'
    status, headers, body = make_request(f"{base}/index.html")
    assert status == 200
    assert "text/html" in headers.get("Content-Type", "")
    assert len(body) > 0


def test_static_asset_css(web_server_fixture):
    server, _ = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/css/styles.css"
    status, headers, body = make_request(url)
    assert status == 200
    assert "text/css" in headers.get("Content-Type", "")
    assert len(body) > 0


def test_static_asset_js(web_server_fixture):
    server, _ = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/js/app.js"
    status, headers, body = make_request(url)
    assert status == 200
    # Standard mime type can be text/javascript or application/javascript
    ct = headers.get("Content-Type", "")
    assert "javascript" in ct
    assert len(body) > 0


def test_static_asset_not_found(web_server_fixture):
    server, _ = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/non_existent_file.xyz"
    status, headers, body = make_request(url)
    assert status == 404


def test_static_asset_traversal_prevention(web_server_fixture):
    server, _ = web_server_fixture
    # Attempt directory traversal attack
    url = f"http://127.0.0.1:{server.port}/../../web_server.py"
    status, headers, body = make_request(url)
    assert status in (400, 404)


# -----------------------------------------------------------------------------
# REST API Endpoints Tests
# -----------------------------------------------------------------------------

def test_api_devices(web_server_fixture):
    server, mgr = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/devices"
    status, headers, body = make_request(url)
    assert status == 200
    data = json.loads(body)
    assert "Stage_A" in data
    assert "Stage_B" in data
    assert data["Stage_A"]["sections"][0]["title"] == "Motion Control"


def test_api_state(web_server_fixture):
    """/api/state reports the model's cached values and samples nothing.

    Re-authored in S5 (RC-4). This used to assert the opposite — that the
    request incremented polled_count and position_read_count, i.e. that
    reading the API drove the hardware. Two problems with that: the sampling
    rate became whatever the browser happened to poll at, and a stalled read
    blocked the HTTP handler thread. The model samples on its own thread now
    (invariant I-4.1), so a read is a read.
    """
    server, mgr = web_server_fixture
    stage_a = mgr.active_models["Stage_A"]
    assert stage_a.polled_count == 0
    assert stage_a.position_read_count == 0

    url = f"http://127.0.0.1:{server.port}/api/state"
    status, headers, body = make_request(url)
    assert status == 200
    data = json.loads(body)
    
    assert "Stage_A" in data
    assert data["Stage_A"]["step_size"] == 10
    assert data["Stage_A"]["velocity"] == 5.5
    assert data["Stage_A"]["enabled"] is True
    assert data["Stage_A"]["label"] == "Sample X"
    assert data["Stage_A"]["pos_x"] == 100.0, "the cached value, unmodified by the read"
    assert stage_a.polled_count == 0, "/api/state polled the hardware"
    assert stage_a.position_read_count == 0, "/api/state read the hardware"


def test_api_command_success(web_server_fixture):
    server, mgr = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/command"
    payload = {
        "device": "Stage_A",
        "command": "home_axis"
    }
    status, headers, body = make_request(url, method="POST", json_data=payload)
    assert status == 200
    data = json.loads(body)
    assert data["status"] == "ok"
    assert data["result"] == "homed_successfully"
    assert ("home_axis", ()) in mgr.active_models["Stage_A"].executed_commands


def test_api_command_with_args(web_server_fixture):
    server, mgr = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/command"
    payload = {
        "device": "Stage_A",
        "command": "move_rel",
        "args": [25, 10.0]
    }
    status, headers, body = make_request(url, method="POST", json_data=payload)
    assert status == 200
    data = json.loads(body)
    assert data["status"] == "ok"
    assert data["result"] == "moved_25"
    assert ("move_rel", (25, 10.0)) in mgr.active_models["Stage_A"].executed_commands


def test_api_command_with_dict_and_scalar_args(web_server_fixture):
    server, mgr = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/command"

    # Dict args
    payload_dict = {
        "device": "Stage_A",
        "command": "dict_arg_cmd",
        "args": {"x": 10, "y": 20}
    }
    status, _, body = make_request(url, method="POST", json_data=payload_dict)
    assert status == 200
    assert json.loads(body)["result"] == "coords_10_20"

    # Scalar arg
    payload_scalar = {
        "device": "Stage_A",
        "command": "single_arg_cmd",
        "args": 42
    }
    status, _, body = make_request(url, method="POST", json_data=payload_scalar)
    assert status == 200
    assert json.loads(body)["result"] == "val_42"


def test_api_unknown_post_endpoint(web_server_fixture):
    server, _ = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/unknown_post_route"
    status, _, _ = make_request(url, method="POST", json_data={"foo": "bar"})
    assert status == 404


def test_api_command_missing_fields(web_server_fixture):
    server, _ = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/command"

    # Missing command
    status, _, body = make_request(url, method="POST", json_data={"device": "Stage_A"})
    assert status == 400
    assert "Missing device or command" in json.loads(body)["message"]

    # Missing device
    status, _, body = make_request(url, method="POST", json_data={"command": "home_axis"})
    assert status == 400
    assert "Missing device or command" in json.loads(body)["message"]


def test_api_command_device_not_found(web_server_fixture):
    server, _ = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/command"
    payload = {"device": "UnknownStage", "command": "home_axis"}
    status, _, body = make_request(url, method="POST", json_data=payload)
    assert status == 404
    assert "not found" in json.loads(body)["message"]


def test_api_command_invalid_command(web_server_fixture):
    server, _ = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/command"
    payload = {"device": "Stage_A", "command": "nonexistent_action"}
    status, _, body = make_request(url, method="POST", json_data=payload)
    assert status == 400
    assert "Command nonexistent_action not found" in json.loads(body)["message"]


def test_api_command_execution_error(web_server_fixture):
    server, _ = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/command"
    payload = {"device": "Stage_A", "command": "failing_command"}
    status, _, body = make_request(url, method="POST", json_data=payload)
    assert status == 500
    data = json.loads(body)
    assert data["status"] == "error"
    assert "Hardware motor fault" in data["message"]
    # Tracebacks are logged server-side (print), not returned to the client
    # — leaking stack traces over an unauthenticated LAN API is an info-disclosure risk.
    assert "traceback" not in data


def test_api_set_attr_type_casting(web_server_fixture):
    server, mgr = web_server_fixture
    stage_a = mgr.active_models["Stage_A"]
    url = f"http://127.0.0.1:{server.port}/api/set_attr"

    # Int casting
    status, _, body = make_request(url, method="POST", json_data={
        "device": "Stage_A", "attr": "step_size", "value": "128"
    })
    assert status == 200
    assert stage_a.step_size == 128
    assert isinstance(stage_a.step_size, int)

    # Float casting
    status, _, body = make_request(url, method="POST", json_data={
        "device": "Stage_A", "attr": "velocity", "value": "12.75"
    })
    assert status == 200
    assert stage_a.velocity == 12.75
    assert isinstance(stage_a.velocity, float)

    # Bool casting
    status, _, body = make_request(url, method="POST", json_data={
        "device": "Stage_A", "attr": "enabled", "value": "false"
    })
    assert status == 200
    assert stage_a.enabled is False

    # String attr
    status, _, body = make_request(url, method="POST", json_data={
        "device": "Stage_A", "attr": "label", "value": "Rotated_Z"
    })
    assert status == 200
    assert stage_a.label == "Rotated_Z"


def test_api_set_attr_invalid_device(web_server_fixture):
    server, _ = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/set_attr"
    status, _, body = make_request(url, method="POST", json_data={
        "device": "GhostDevice", "attr": "step_size", "value": "50"
    })
    assert status == 404


def test_api_set_attr_invalid_type_conversion(web_server_fixture):
    server, _ = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/set_attr"
    # Setting an int attribute to a non-int string
    status, _, body = make_request(url, method="POST", json_data={
        "device": "Stage_A", "attr": "step_size", "value": "invalid_number"
    })
    assert status == 500
    data = json.loads(body)
    assert data["status"] == "error"


def test_api_logs_and_errors(web_server_fixture):
    """`/api/errors?since=<id>`, non-destructively (RC-8 item 3).

    This test used to append straight into `error_buffer` and then assert
    that a **second** read came back empty, because the route called
    `pop_errors()` and cleared as it read. That is ERRORS-2 / WEB-17 stated
    as a requirement: with two browser tabs open, whichever polled first
    consumed the error and the other never saw it. The assertion is
    inverted here, and the error is published the way one really arrives.
    """
    from error_routing import ErrorRouter

    server, _ = web_server_fixture
    WebAPIHandler.log_buffer.append("[Stage_A] Initialization complete")
    ErrorRouter.report_warning("LimitReached", "Soft limit", source="Stage_A")

    status, _, body = make_request(f"http://127.0.0.1:{server.port}/api/logs")
    assert status == 200
    logs_data = json.loads(body)
    assert "[Stage_A] Initialization complete" in logs_data["logs"]

    status, _, body = make_request(f"http://127.0.0.1:{server.port}/api/errors")
    assert status == 200
    errors_data = json.loads(body)
    assert len(errors_data["errors"]) == 1
    assert errors_data["errors"][0]["title"] == "LimitReached"
    assert errors_data["errors"][0]["severity"] == "warning"
    latest = errors_data["latest_id"]
    assert latest == errors_data["errors"][0]["id"]

    # A second client, with its own cursor at 0, sees the same error. Under
    # `pop_errors` this was the read that came back empty.
    status, _, body2 = make_request(f"http://127.0.0.1:{server.port}/api/errors")
    assert status == 200
    assert len(json.loads(body2)["errors"]) == 1

    # The first client, having advanced its cursor, sees nothing new.
    status, _, body3 = make_request(
        f"http://127.0.0.1:{server.port}/api/errors?since={latest}")
    assert status == 200
    assert json.loads(body3)["errors"] == []


def test_a_malformed_since_is_treated_as_zero(web_server_fixture):
    """A bad cursor must not 500 the poll loop the whole UI depends on."""
    server, _ = web_server_fixture
    from error_routing import ErrorRouter
    ErrorRouter.report_error("Boom", "something broke")

    for bad in ("abc", "", "-1", "9e99"):
        status, _, body = make_request(
            f"http://127.0.0.1:{server.port}/api/errors?since={bad}")
        assert status == 200, f"since={bad!r} returned {status}"


def test_invalid_json_handling(web_server_fixture):
    server, _ = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/command"
    from views.web.web_server import SESSION_TOKEN, TOKEN_HEADER
    req = urllib.request.Request(
        url, data=b"{corrupted_json: true",
        headers={"Content-Type": "application/json", TOKEN_HEADER: SESSION_TOKEN},
        method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 400
    except urllib.error.HTTPError as e:
        assert e.code == 400
        body = json.loads(e.read().decode("utf-8"))
        assert body["status"] == "error"
        assert body["message"] == "Invalid JSON"


# -----------------------------------------------------------------------------
# Thread Safety and Non-Blocking Operation Tests
# -----------------------------------------------------------------------------

def test_thread_safety_concurrent_requests(web_server_fixture):
    server, mgr = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/command"

    # Execute a slow command that sleeps for 0.3s
    slow_payload = {"device": "Stage_A", "command": "slow_task", "args": [0.3]}
    # Concurrently execute a fast command
    fast_payload = {"device": "Stage_B", "command": "home_axis"}

    results = []

    def run_slow():
        t0 = time.time()
        status, _, body = make_request(url, method="POST", json_data=slow_payload)
        elapsed = time.time() - t0
        results.append(("slow", status, elapsed))

    def run_fast():
        # slight delay to ensure slow is initiated
        time.sleep(0.05)
        t0 = time.time()
        status, _, body = make_request(url, method="POST", json_data=fast_payload)
        elapsed = time.time() - t0
        results.append(("fast", status, elapsed))

    t_slow = threading.Thread(target=run_slow)
    t_fast = threading.Thread(target=run_fast)

    t_slow.start()
    t_fast.start()

    t_fast.join()
    t_slow.join()

    # Fast request should complete well before slow request finishes,
    # demonstrating ThreadingHTTPServer daemon threads are non-blocking across clients.
    fast_res = next(r for r in results if r[0] == "fast")
    slow_res = next(r for r in results if r[0] == "slow")

    assert fast_res[1] == 200
    assert slow_res[1] == 200
    assert fast_res[2] < 0.25  # Fast finished promptly
    assert slow_res[2] >= 0.28  # Slow took its full time


def test_api_full_stop_all(web_server_fixture):
    server, mgr = web_server_fixture
    mgr.full_stop_called = False
    def mock_full_stop_all():
        mgr.full_stop_called = True
    mgr.full_stop_all = mock_full_stop_all
    
    url = f"http://127.0.0.1:{server.port}/api/system/full_stop"
    status, _, body = make_request(url, method="POST", json_data={})
    assert status == 200
    data = json.loads(body)
    assert data["status"] == "ok"
    assert mgr.full_stop_called is True

def test_api_options_success(web_server_fixture):
    server, mgr = web_server_fixture
    url = f"http://127.0.0.1:{server.port}/api/options?device=Stage_A&command=get_opts"
    status, _, body = make_request(url)
    assert status == 200
    data = json.loads(body)
    assert data["status"] == "ok"
    assert data["options"] == ["A", "B", "C"]

def test_api_options_security(web_server_fixture):
    server, mgr = web_server_fixture
    # home_axis is a valid method, but NOT an options_command
    url = f"http://127.0.0.1:{server.port}/api/options?device=Stage_A&command=home_axis"
    status, _, body = make_request(url)
    assert status == 400
    data = json.loads(body)
    assert "not an exposed options_command" in data["message"]
