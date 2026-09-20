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
