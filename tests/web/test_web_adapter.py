import pytest
import time
from views.web.web_view import WebDashboardWindow, WebErrorManager
from views.web.web_server import WebAPIHandler
from error_routing import ErrorRouter

class DummyPoller:
    def __init__(self):
        self.log_updater = None

    def emit_log(self, msg):
        if self.log_updater:
            self.log_updater(msg)

class DummyModel:
    def __init__(self, with_poller=True):
        self.poller = DummyPoller() if with_poller else None

class MockSystemManager:
    def __init__(self):
        self.active_models = {
            "Stage_X": DummyModel(with_poller=True),
            "Sensor_Y": DummyModel(with_poller=False)
        }
        self.shutdown_called = False

    def shutdown_all(self):
        self.shutdown_called = True

    def get_active_models_snapshot(self):
        # The view reads models through the manager's thread-safe accessor
        # rather than reaching into active_models (I-1.5).
        return dict(self.active_models)


def test_web_error_manager_routing():
    WebAPIHandler.error_buffer.clear()
    WebErrorManager.initialize()

    # Verify report_error
    ErrorRouter.report_error("ConnectionFailed", "Device failed to respond", exception=Exception("TimeoutErr"))
    # Verify report_warning
    ErrorRouter.report_warning("LowPower", "Battery below 10%", exception=ValueError("Voltage low"))
    # Verify report_info
    ErrorRouter.report_info("HomingComplete", "Stage is calibrated")

    buf = WebAPIHandler.error_buffer
    assert len(buf) == 3
    assert buf[0]["type"] == "error"
    assert buf[0]["title"] == "ConnectionFailed"
    assert "TimeoutErr" in buf[0]["exception"]

    assert buf[1]["type"] == "warning"
    assert buf[1]["title"] == "LowPower"
    assert "Voltage low" in buf[1]["exception"]

    assert buf[2]["type"] == "info"
    assert buf[2]["title"] == "HomingComplete"
    assert buf[2]["exception"] is None

    WebAPIHandler.error_buffer.clear()


def test_web_dashboard_window_poller_binding():
    WebAPIHandler.log_buffer.clear()
    mgr = MockSystemManager()
    win = WebDashboardWindow(mgr, port=9200, open_browser=False)

    # Trigger log update via poller
    poller = mgr.active_models["Stage_X"].poller
    poller.emit_log("Step completed: 100")
    poller.emit_log("Step completed: 200")

    assert len(WebAPIHandler.log_buffer) == 2
    assert "[Stage_X] Step completed: 100" in WebAPIHandler.log_buffer
    assert "[Stage_X] Step completed: 200" in WebAPIHandler.log_buffer

    # Verify log buffer capped at 500 lines
    for i in range(550):
        poller.emit_log(f"Line {i}")
    assert len(WebAPIHandler.log_buffer) == 500
    assert "[Stage_X] Line 549" == WebAPIHandler.log_buffer[-1]

    WebAPIHandler.log_buffer.clear()


def test_web_dashboard_window_lifecycle():
    mgr = MockSystemManager()
    win = WebDashboardWindow(mgr, port=9205, open_browser=False)
    
    win.show()
    assert win.server.server is not None
    assert win.server.thread is not None
    assert win.server.thread.is_alive()

    win.close()
    assert mgr.shutdown_called is True
    assert win.server.server is None


def test_web_adapter_full_stop_all():
    from views.web.web_adapter import WebModelAdapter
    
    # Uninitialized SystemManager case
    adapter = WebModelAdapter()
    res = adapter.full_stop_all()
    assert res["status"] == "error"
    assert res["code"] == 500
    assert "SystemManager not initialized" in res["message"]
    
    # Success case
    class MockMgr:
        def __init__(self):
            self.called = False
        def full_stop_all(self):
            self.called = True
            
    mgr = MockMgr()
    adapter.set_system_manager(mgr)
    res2 = adapter.full_stop_all()
    assert res2["status"] == "ok"
    assert res2["code"] == 200
    assert mgr.called is True


def test_full_stop_reports_per_device_results():
    """WEB-18: the adapter must surface SystemManager.full_stop_all's
    per-device {name: ok} dict rather than discarding it and always
    reporting status "ok"."""
    from views.web.web_adapter import WebModelAdapter

    class MockMgrAllConfirmed:
        def full_stop_all(self):
            return {"Stage_A": True, "Stage_B": True}

    adapter = WebModelAdapter()
    adapter.set_system_manager(MockMgrAllConfirmed())
    res = adapter.full_stop_all()
    assert res["status"] == "ok"
    assert res["results"] == {"Stage_A": True, "Stage_B": True}

    class MockMgrOneFailed:
        def full_stop_all(self):
            return {"Stage_A": True, "Stage_B": False}

    adapter2 = WebModelAdapter()
    adapter2.set_system_manager(MockMgrOneFailed())
    res2 = adapter2.full_stop_all()
    assert res2["status"] == "error"
    assert res2["results"] == {"Stage_A": True, "Stage_B": False}
    assert "Stage_B" in res2["message"]


def test_get_state_isolates_a_raising_device():
    """WEB-14: one device's cache read must not take the whole /api/state
    response down with it. A property that raises used to propagate out of
    get_state() and empty the response for every device, not just the
    broken one."""
    from views.web.web_adapter import WebModelAdapter

    class HealthyModel:
        def __init__(self):
            self.pos = 1.0

        @property
        def ui_schema(self):
            return {"sections": [{"elements": [
                {"type": "readonly", "model_attr": "pos"}]}]}

    class BrokenModel:
        @property
        def ui_schema(self):
            raise RuntimeError("board fell off the bus")

    class MockMgr:
        def __init__(self):
            self.active_models = {"Good": HealthyModel(), "Bad": BrokenModel()}

        def get_active_models_snapshot(self):
            return dict(self.active_models)

    adapter = WebModelAdapter()
    adapter.set_system_manager(MockMgr())
    state = adapter.get_state()

    assert state["Good"]["pos"] == 1.0
    assert "connection_status" in state["Good"]
    assert "_error" in state["Bad"]
    assert "board fell off the bus" in state["Bad"]["_error"]


def test_hardware_scan_runs_async_and_is_polled_to_completion(monkeypatch):
    """WEB-15 residue: scan_hardware()'s own docstring says device-type
    probing needs an async scan (background thread + poll), not a loop
    bolted onto a fast GET, because probe_device_at blocks for seconds per
    port. start_hardware_scan must return immediately (not block on the
    probes), and get_scan_status must observe "running" then "done" with
    the per-port results filled in."""
    import app_bootstrap
    import threading
    from views.web.web_adapter import WebModelAdapter

    release_probe = threading.Event()

    def fake_discover_ports():
        return ["SIM", "/dev/fake0", "/dev/fake1"]

    def fake_probe_device_at(port):
        release_probe.wait(timeout=2)
        return {"/dev/fake0": "stepper", "/dev/fake1": "chuck"}.get(port)

    monkeypatch.setattr(app_bootstrap, "discover_ports", fake_discover_ports)
    monkeypatch.setattr(app_bootstrap, "probe_device_at", fake_probe_device_at)

    adapter = WebModelAdapter()

    assert adapter.get_scan_status()["status"] == "not_started"

    start_result = adapter.start_hardware_scan()
    assert start_result["status"] == "ok"

    # The probes are still blocked on release_probe, so this must observe
    # "running" rather than having waited for them - proving the call above
    # did not block the caller.
    status = adapter.get_scan_status()
    assert status["status"] == "running"

    release_probe.set()
    deadline = time.time() + 2
    while time.time() < deadline:
        status = adapter.get_scan_status()
        if status["status"] != "running":
            break
        time.sleep(0.01)

    assert status["status"] == "done"
    assert status["results"] == {"/dev/fake0": "stepper", "/dev/fake1": "chuck"}
    # SIM is never probed - it isn't a real port to open.
    assert "SIM" not in status["results"]


def test_hardware_scan_is_single_flight(monkeypatch):
    """A second start while one is already running must not spin up a
    second thread walking the same ports; it gets a 409-shaped refusal."""
    import app_bootstrap
    import threading
    from views.web.web_adapter import WebModelAdapter

    block_forever = threading.Event()

    def fake_discover_ports():
        return ["/dev/fake0"]

    def fake_probe_device_at(port):
        block_forever.wait(timeout=2)
        return None

    monkeypatch.setattr(app_bootstrap, "discover_ports", fake_discover_ports)
    monkeypatch.setattr(app_bootstrap, "probe_device_at", fake_probe_device_at)

    adapter = WebModelAdapter()
    first = adapter.start_hardware_scan()
    assert first["status"] == "ok"

    second = adapter.start_hardware_scan()
    assert second["status"] == "error"
    assert second["code"] == 409

    block_forever.set()


# --- ROTATOR-7: the web STOP must not queue behind another request -------
#
# The finding: `dispatch_command` took the per-device lock for *every*
# command, so the per-device STOP button waited for whatever request was
# already holding it, and `get_state` took the same lock, so one slow
# command stalled state polling for that device. The safety contract
# (docs/architecture/safety-pattern.md) is that a stop never queues: "A
# stop that cannot get the lock is worse than an unsynchronised one."

class _GatedDevice:
    """A model with one command that blocks until released, plus a stop."""

    def __init__(self):
        import threading as _t
        self.release = _t.Event()
        self.in_home = _t.Event()
        self.stopped = _t.Event()
        self.estopped = _t.Event()
        self.position = 0.0

    @property
    def ui_schema(self):
        return {"sections": [{"elements": [
            {"type": "readonly", "model_attr": "position"},
            {"type": "button", "command": "home"},
            {"type": "button", "command": "stop"},
            {"type": "button", "command": "emergency_stop"},
        ]}]}

    def home(self):
        self.in_home.set()
        # Bounded so a regression cannot wedge the suite forever.
        self.release.wait(10)
        return True

    def stop(self):
        self.stopped.set()
        return True

    def emergency_stop(self):
        self.estopped.set()
        return True


class _GatedMgr:
    def __init__(self, model):
        self.active_models = {"Rotator": model}

    def get_active_models_snapshot(self):
        return dict(self.active_models)


def _adapter_with_gated_device():
    from views.web.web_adapter import WebModelAdapter
    model = _GatedDevice()
    adapter = WebModelAdapter()
    adapter.set_system_manager(_GatedMgr(model))
    return adapter, model


def test_stop_command_is_not_serialized_behind_a_slow_command():
    """ROTATOR-7: with a slow command holding the device lock, a STOP
    dispatched from another request must still reach the model promptly."""
    import threading
    adapter, model = _adapter_with_gated_device()

    slow = threading.Thread(
        target=adapter.dispatch_command, args=("Rotator", "home"), daemon=True)
    slow.start()
    assert model.in_home.wait(2), "the slow command never started"

    result = {}

    def _stop():
        result["res"] = adapter.dispatch_command("Rotator", "stop")

    stopper = threading.Thread(target=_stop, daemon=True)
    stopper.start()
    try:
        # The stop must land while `home` is still in flight.
        assert model.stopped.wait(1.0), (
            "STOP queued behind the in-flight command on the device lock")
        stopper.join(1.0)
        assert not stopper.is_alive(), "STOP did not return to its caller"
        assert result["res"]["status"] == "ok"
        assert not model.release.is_set()
    finally:
        model.release.set()
        slow.join(5)


def test_emergency_stop_command_is_not_serialized_behind_a_slow_command():
    """ROTATOR-7: the same for `emergency_stop`, which the safety pattern
    requires never block its caller."""
    import threading
    adapter, model = _adapter_with_gated_device()

    slow = threading.Thread(
        target=adapter.dispatch_command, args=("Rotator", "home"), daemon=True)
    slow.start()
    assert model.in_home.wait(2), "the slow command never started"

    estopper = threading.Thread(
        target=adapter.dispatch_command,
        args=("Rotator", "emergency_stop"), daemon=True)
    estopper.start()
    try:
        assert model.estopped.wait(1.0), (
            "emergency_stop queued behind the in-flight command")
        estopper.join(1.0)
        assert not estopper.is_alive()
    finally:
        model.release.set()
        slow.join(5)


def test_get_state_is_not_stalled_by_an_in_flight_command():
    """ROTATOR-7, second half: `/api/state` reads the model's cache and does
    no I/O (I-4.1), so it must not wait on the per-device lock a long command
    is holding. A blocking command used to stall every state poll for that
    device, which is what the audit recorded for `reconnect`."""
    import threading
    import time as _time
    adapter, model = _adapter_with_gated_device()

    slow = threading.Thread(
        target=adapter.dispatch_command, args=("Rotator", "home"), daemon=True)
    slow.start()
    assert model.in_home.wait(2), "the slow command never started"

    try:
        started = _time.monotonic()
        state = adapter.get_state()
        elapsed = _time.monotonic() - started
        assert elapsed < 0.5, (
            f"/api/state blocked {elapsed:.2f}s on the device lock")
        assert state["Rotator"]["position"] == 0.0
    finally:
        model.release.set()
        slow.join(5)


def test_ordinary_commands_still_serialize_on_the_device_lock():
    """Regression guard for the ROTATOR-7 fix: only the stop paths skip the
    per-device lock. Two ordinary commands must still not overlap, or the
    fix has traded a queued STOP for concurrent motion on one device."""
    import threading
    adapter, model = _adapter_with_gated_device()

    slow = threading.Thread(
        target=adapter.dispatch_command, args=("Rotator", "home"), daemon=True)
    slow.start()
    assert model.in_home.wait(2), "the slow command never started"

    model.in_home.clear()
    second = threading.Thread(
        target=adapter.dispatch_command, args=("Rotator", "home"), daemon=True)
    second.start()
    try:
        assert not model.in_home.wait(0.3), (
            "a second ordinary command ran while the first held the device")
    finally:
        model.release.set()
        slow.join(5)
        second.join(5)


# --- ROTATOR-13, web half: the badge must not claim a simulator ----------

def test_a_sim_port_rotator_is_not_badged_as_simulated():
    """ROTATOR-13: `_determine_connection_status` guessed "simulated" from
    the port string, so a rotator configured for SIM rendered a SIMULATED
    badge next to state "Disconnected" — a claimed capability that does not
    exist. The model's own `connection_status` is consulted first."""
    from views.web.web_adapter import WebModelAdapter
    from model.rotator_system import RotatorSystem

    rotator = RotatorSystem(default_port="SIM")

    class MockMgr:
        def __init__(self):
            self.active_models = {"Rotator": rotator}

        def get_active_models_snapshot(self):
            return dict(self.active_models)

    adapter = WebModelAdapter()
    adapter.set_system_manager(MockMgr())
    assert adapter._determine_connection_status(rotator) == "disconnected"
    assert adapter.get_state()["Rotator"]["connection_status"] == "disconnected"


def test_dispatch_surfaces_a_refused_command_as_an_error():
    """ROTATOR-13 residue: a `Refused` CommandResult is not a success. The
    adapter only special-cased a bare `False`, so a command that refused
    *with a reason* came back `{"status": "ok"}` and the dashboard toasted
    "executed" — which is the same class of silence the refusal was added
    to break."""
    from views.web.web_adapter import WebModelAdapter
    from results import Refused, Failed

    class RefusingModel:
        @property
        def ui_schema(self):
            return {"sections": [{"elements": [
                {"type": "button", "command": "home"},
                {"type": "button", "command": "boom"},
            ]}]}

        def home(self):
            return Refused("The rotator is not connected.")

        def boom(self):
            return Failed(RuntimeError("stage fell over"))

    class MockMgr:
        def __init__(self):
            self.active_models = {"Rotator": RefusingModel()}

        def get_active_models_snapshot(self):
            return dict(self.active_models)

    adapter = WebModelAdapter()
    adapter.set_system_manager(MockMgr())

    res = adapter.dispatch_command("Rotator", "home")
    assert res["status"] == "error"
    assert "not connected" in res["message"]

    res2 = adapter.dispatch_command("Rotator", "boom")
    assert res2["status"] == "error"
    assert "stage fell over" in res2["message"]
