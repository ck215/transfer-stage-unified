import pytest
import json
import urllib.request
import urllib.parse
import urllib.error
import time
import threading
from unittest.mock import MagicMock, patch

from view.web_server import WebDashboardServer, WebAPIHandler
from view.web_adapter import WebModelAdapter
from model.system_manager import SystemManager


# ---------------------------------------------------------------------------
# Mock Devices for Testing
# ---------------------------------------------------------------------------

class MockSimulatedDevice:
    def __init__(self, name="SimStage"):
        self.name = name
        self.serial_port = "SIM"
        self.pos_x = 10.0
        self.polled_count = 0

    @property
    def ui_schema(self):
        return {
            "sections": [
                {
                    "title": "Controls",
                    "elements": [
                        {"type": "readonly", "text": "Pos:", "model_attr": "pos_x"}
                    ]
                }
            ]
        }

    def read_position(self):
        self.polled_count += 1
        return self.pos_x


class MockHardwareDevice:
    def __init__(self, name="RealStage", port="/dev/ttyUSB0"):
        self.name = name
        self.serial_port = port
        self.pos_x = 42.0
        self.connected = True
        self.polled_count = 0

    @property
    def ui_schema(self):
        return {
            "sections": [
                {
                    "title": "Controls",
                    "elements": [
                        {"type": "readonly", "text": "Pos:", "model_attr": "pos_x"}
                    ]
                }
            ]
        }

    def read_position(self):
        self.polled_count += 1
        return self.pos_x


class MockDisconnectedDevice:
    def __init__(self, name="OfflineStage"):
        self.name = name
        self.serial_port = None
        self.connected = False
        self.pos_x = 0.0

    @property
    def ui_schema(self):
        return {
            "sections": [
                {
                    "title": "Controls",
                    "elements": [
                        {"type": "readonly", "text": "Pos:", "model_attr": "pos_x"}
                    ]
                }
            ]
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def web_setup_server():
    """Starts a WebDashboardServer in setup mode with a fresh adapter."""
    adapter = WebModelAdapter(system_manager=None, mode="setup")
    server = WebDashboardServer(host="127.0.0.1", port=18080, adapter=adapter)
    server.start(background=True)
    base_url = f"http://{server.host}:{server.port}"
    time.sleep(0.05)

    yield server, base_url, adapter

    server.stop()


def _get(url):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _post(url, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = json.loads(e.read().decode("utf-8"))
        return e.code, err_body


# ---------------------------------------------------------------------------
# Tests: GET /api/setup/scan
# ---------------------------------------------------------------------------

def test_setup_scan_enumeration(web_setup_server):
    """Verifies that GET /api/setup/scan enumerates serial ports and controllers."""
    server, base_url, adapter = web_setup_server

    status, data = _get(f"{base_url}/api/setup/scan")
    assert status == 200
    assert data["status"] == "ok"
    assert "ports" in data
    assert "controllers" in data
    assert isinstance(data["ports"], list)
    assert isinstance(data["controllers"], list)
    # "SIM" must always be in available ports
    assert "SIM" in data["ports"]
    # "None" must always be in controllers
    assert "None" in data["controllers"]


def test_setup_scan_with_mocked_hardware(web_setup_server):
    """Verifies hardware enumeration when serial ports and gamepads are detected."""
    server, base_url, adapter = web_setup_server

    mock_port_1 = MagicMock()
    mock_port_1.device = "/dev/ttyUSB_STAGE"
    mock_port_2 = MagicMock()
    mock_port_2.device = "/dev/cu.usbserial-1420"
    mock_bluetooth = MagicMock()
    mock_bluetooth.device = "/dev/cu.Bluetooth-Incoming-Port"

    with patch("serial.tools.list_ports.comports", return_value=[mock_port_1, mock_port_2, mock_bluetooth]):
        status, data = _get(f"{base_url}/api/setup/scan")
        assert status == 200
        ports = data["ports"]
        assert "SIM" in ports
        assert "/dev/ttyUSB_STAGE" in ports
        assert "/dev/cu.usbserial-1420" in ports
        # Bluetooth incoming ports should be filtered out
        assert "/dev/cu.Bluetooth-Incoming-Port" not in ports


# ---------------------------------------------------------------------------
# Tests: POST /api/setup/initialize
# ---------------------------------------------------------------------------

def test_setup_initialize_valid_configuration(web_setup_server):
    """Verifies valid device configuration mapping with simulated devices and mode transition."""
    server, base_url, adapter = web_setup_server

    assert adapter.mode == "setup"
    assert adapter.system_manager is None

    configs = [
        {"device": "Stepper Probe", "port": "SIM", "controller": "None"},
        {"device": "Chuck Positioner", "port": "SIM", "controller": "None"},
        {"device": "Temperature Controller", "port": "SIM"}
    ]

    status, resp = _post(f"{base_url}/api/setup/initialize", {"configs": configs})
    assert status == 200
    assert resp["status"] == "ok"
    assert resp["mode"] == "running"
    assert "Stepper Probe" in resp["initialized_devices"]
    assert "Chuck Positioner" in resp["initialized_devices"]
    assert "Temperature Controller" in resp["initialized_devices"]

    # Verify adapter state and manager transition
    assert adapter.mode == "running"
    assert adapter.system_manager is not None
    assert "Stepper Probe" in adapter.system_manager.active_models


def test_setup_initialize_headless_alias(web_setup_server):
    """Verifies that 'Headless' port is automatically mapped to 'SIM'."""
    server, base_url, adapter = web_setup_server

    configs = [
        {"device": "Stepper Probe", "port": "Headless", "controller": "None"}
    ]

    status, resp = _post(f"{base_url}/api/setup/initialize", {"configs": configs})
    assert status == 200
    assert resp["status"] == "ok"
    assert adapter.mode == "running"
    stepper = adapter.system_manager.active_models["Stepper Probe"]
    assert getattr(stepper, "serial_port", None) == "SIM"


def test_setup_initialize_invalid_payload(web_setup_server):
    """Verifies error handling on empty or invalid payload."""
    server, base_url, adapter = web_setup_server

    # Empty configs list
    status, resp = _post(f"{base_url}/api/setup/initialize", {"configs": []})
    assert status == 400
    assert resp["status"] == "error"

    # Missing device or port in config
    status, resp = _post(f"{base_url}/api/setup/initialize", {"configs": [{"device": "Stepper Probe"}]})
    assert status == 400
    assert "Device name and port are required" in resp["message"]


def test_setup_initialize_conflicting_ports(web_setup_server):
    """Verifies error handling when conflicting COM ports are assigned to different devices."""
    server, base_url, adapter = web_setup_server

    configs = [
        {"device": "Stepper Probe", "port": "/dev/ttyUSB0", "controller": "None"},
        {"device": "DC Probe", "port": "/dev/ttyUSB0", "controller": "None"}
    ]

    status, resp = _post(f"{base_url}/api/setup/initialize", {"configs": configs})
    assert status == 400
    assert resp["status"] == "error"
    assert "Port collision" in resp["message"]
    assert adapter.mode == "setup"


def test_setup_initialize_conflicting_controllers(web_setup_server):
    """Verifies error handling when conflicting physical controllers are assigned."""
    server, base_url, adapter = web_setup_server

    configs = [
        {"device": "Stepper Probe", "port": "SIM", "controller": "ID 0: Xbox Controller"},
        {"device": "DC Probe", "port": "SIM", "controller": "ID 0: Xbox Controller"}
    ]

    status, resp = _post(f"{base_url}/api/setup/initialize", {"configs": configs})
    assert status == 400
    assert resp["status"] == "error"
    assert "Controller collision" in resp["message"]
    assert adapter.mode == "setup"


def test_setup_initialize_sim_ports_do_not_collide(web_setup_server):
    """Verifies that multiple devices using SIM port do NOT trigger port collisions."""
    server, base_url, adapter = web_setup_server

    configs = [
        {"device": "Stepper Probe", "port": "SIM", "controller": "None"},
        {"device": "DC Probe", "port": "SIM", "controller": "None"},
        {"device": "Temperature Controller", "port": "SIM"}
    ]

    status, resp = _post(f"{base_url}/api/setup/initialize", {"configs": configs})
    assert status == 200
    assert resp["status"] == "ok"
    assert adapter.mode == "running"


# ---------------------------------------------------------------------------
# Tests: Connection Status Badging in GET /api/state
# ---------------------------------------------------------------------------

def test_connection_status_badging(web_setup_server):
    """
    Verifies that GET /api/state correctly attributes connection_status:
    - simulated
    - hardware
    - disconnected
    """
    server, base_url, adapter = web_setup_server

    mgr = SystemManager()
    mgr.register_model("SimDevice", MockSimulatedDevice("SimDevice"))
    mgr.register_model("RealDevice", MockHardwareDevice("RealDevice", port="/dev/ttyUSB0"))
    mgr.register_model("OfflineDevice", MockDisconnectedDevice("OfflineDevice"))

    adapter.set_system_manager(mgr)

    status, state = _get(f"{base_url}/api/state")
    assert status == 200

    assert "SimDevice" in state
    assert state["SimDevice"]["connection_status"] == "simulated"

    assert "RealDevice" in state
    assert state["RealDevice"]["connection_status"] == "hardware"

    assert "OfflineDevice" in state
    assert state["OfflineDevice"]["connection_status"] == "disconnected"


def test_connection_status_explicit_attribute_precedence():
    """Verifies that explicit connection_status on a model takes precedence."""
    adapter = WebModelAdapter()

    class CustomModel:
        connection_status = "hardware"
        ui_schema = {"sections": []}

    mgr = SystemManager()
    mgr.register_model("CustomDevice", CustomModel())
    adapter.set_system_manager(mgr)

    state = adapter.get_state()
    assert state["CustomDevice"]["connection_status"] == "hardware"

    CustomModel.connection_status = "simulated"
    state = adapter.get_state()
    assert state["CustomDevice"]["connection_status"] == "simulated"


# ---------------------------------------------------------------------------
# Tests: Concurrency Verification (Setup Initialization + Telemetry Polling)
# ---------------------------------------------------------------------------

def test_thread_concurrency_setup_and_telemetry(web_setup_server):
    """
    Spawns concurrent client threads continuously reading telemetry (GET /api/state)
    while another thread performs setup initialization and device re-configuration.
    Verifies zero deadlocks, race conditions, or unhandled exceptions.
    """
    server, base_url, adapter = web_setup_server

    # Pre-populate manager with simulated devices
    mgr = SystemManager()
    mgr.register_model("StageA", MockSimulatedDevice("StageA"))
    mgr.register_model("StageB", MockSimulatedDevice("StageB"))
    adapter.set_system_manager(mgr)

    errors = []
    stop_event = threading.Event()
    poll_counts = [0, 0, 0, 0]

    def telemetry_reader(worker_id):
        while not stop_event.is_set():
            try:
                status, state = _get(f"{base_url}/api/state")
                if status != 200:
                    errors.append(f"Worker {worker_id}: unexpected status {status}")
                if not isinstance(state, dict):
                    errors.append(f"Worker {worker_id}: non-dict state response")
                poll_counts[worker_id] += 1
            except Exception as e:
                errors.append(f"Worker {worker_id} exception: {e}")
            time.sleep(0.01)

    # Launch 4 reader threads
    threads = []
    for i in range(4):
        t = threading.Thread(target=telemetry_reader, args=(i,))
        t.daemon = True
        t.start()
        threads.append(t)

    # Concurrent setup initialization / reconfiguration
    time.sleep(0.05)
    reconfig_configs = [
        {"device": "Stepper Probe", "port": "SIM", "controller": "None"},
        {"device": "DC Probe", "port": "SIM", "controller": "None"}
    ]
    status, resp = _post(f"{base_url}/api/setup/initialize", {"configs": reconfig_configs})
    assert status == 200
    assert resp["status"] == "ok"

    # Allow polling to continue after reconfiguration
    time.sleep(0.15)
    stop_event.set()

    for t in threads:
        t.join(timeout=2.0)

    assert not errors, f"Concurrency errors encountered: {errors}"
    assert all(c > 5 for c in poll_counts), f"Expected each poller to read at least 5 times, got {poll_counts}"

    # Verify state after reconfiguration
    status, final_state = _get(f"{base_url}/api/state")
    assert status == 200
    assert "Stepper Probe" in final_state
    assert "DC Probe" in final_state
    assert final_state["Stepper Probe"]["connection_status"] == "simulated"
