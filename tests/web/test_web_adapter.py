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
