import os
import sys
import webbrowser
import time
from error_routing import ErrorRouter
from .web_server import WebDashboardServer, WebAPIHandler

class WebErrorManager:
    """Binds ErrorRouter to the web server error queue."""
    @classmethod
    def initialize(cls):
        ErrorRouter.set_callbacks(cls.report_error, cls.report_warning, cls.report_info)

    @classmethod
    def report_error(cls, title, message, exception=None):
        WebAPIHandler.error_buffer.append({
            "type": "error",
            "title": title,
            "message": message,
            "exception": str(exception) if exception else None
        })

    @classmethod
    def report_warning(cls, title, message, exception=None):
        WebAPIHandler.error_buffer.append({
            "type": "warning",
            "title": title,
            "message": message,
            "exception": str(exception) if exception else None
        })

    @classmethod
    def report_info(cls, title, message):
        WebAPIHandler.error_buffer.append({
            "type": "info",
            "title": title,
            "message": message,
            "exception": None
        })

class WebDashboardWindow:
    """
    MVC View adapter providing a local browser-based dashboard interface.
    Adheres strictly to the separation principles of the MVC architecture.
    """
    def __init__(self, system_manager, port=8080, open_browser=True):
        self.port = port
        self.server = WebDashboardServer(system_manager, port=self.port)
        self.open_browser = open_browser

        # Connect ErrorRouter to web reporting
        WebErrorManager.initialize()

        # Connect poller logs if present
        models = (self.system_manager.get_active_models_snapshot()
                  if self.system_manager else {})
        for name, model in models.items():
            if hasattr(model, "poller") and model.poller:
                def make_logger(p_name):
                    def append_log(msg):
                        WebAPIHandler.log_buffer.append(f"[{p_name}] {msg}")
                        if len(WebAPIHandler.log_buffer) > 500:
                            WebAPIHandler.log_buffer.pop(0)
                    return append_log
                model.poller.log_updater = make_logger(name)

    @property
    def system_manager(self):
        """Read through to the adapter — never a stored copy (RC-10 item 1).

        Five objects used to hold a manager and only the adapter's stayed
        live, because `reconfigure` replaces it. The window's stale copy is
        why `close()` shut down the *original, empty* manager on Ctrl-C and
        left the real models running (MANAGER-1, TEMP-1, ROTATOR-2).
        """
        return self.server.system_manager

    def show(self):
        self.server.start(background=True)
        url = f"http://127.0.0.1:{self.server.port}"
        print(f"[WebDashboard] Server running at {url}")
        if self.open_browser:
            webbrowser.open(url)

    def close(self):
        if self.server:
            self.server.stop()
        if self.system_manager and hasattr(self.system_manager, "shutdown_all"):
            self.system_manager.shutdown_all()
