import os
import sys
import webbrowser
import time
from error_routing import ErrorRouter
from .web_server import WebDashboardServer, WebAPIHandler

class WebErrorManager:
    """Mirrors bus events into the adapter buffer (RC-8 item 3).

    The route serves `/api/errors?since=<id>` from the bus itself, so this
    exists only to keep `error_buffer` — which the shutdown drain and a
    number of tests read — populated. It subscribes rather than seizing
    `ErrorRouter`'s three global callback slots, so starting the web view
    alongside a desktop view no longer silences the desktop one.
    """

    _subscribed = False

    @classmethod
    def initialize(cls):
        if not cls._subscribed:
            ErrorRouter.subscribe(cls._on_event)
            cls._subscribed = True

    @classmethod
    def shutdown(cls):
        ErrorRouter.unsubscribe(cls._on_event)
        cls._subscribed = False

    @classmethod
    def _on_event(cls, event):
        WebAPIHandler.error_buffer.append(event.to_dict())

    # The three report_* names are kept: a handful of call sites and tests
    # reach the web manager directly rather than through ErrorRouter.
    @classmethod
    def report_error(cls, title, message, exception=None):
        ErrorRouter.report_error(title, message, exception, source="web")

    @classmethod
    def report_warning(cls, title, message, exception=None):
        ErrorRouter.report_warning(title, message, exception, source="web")

    @classmethod
    def report_info(cls, title, message):
        ErrorRouter.report_info(title, message, source="web")


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

        # Connect poller logs for any models already present at construction
        # time (WEB-5). This only ever covers a manager built before the
        # window - in practice that is an empty manager in `run_web_app`,
        # since real models are built later by the setup wizard, which is
        # why `WebModelAdapter._initialize_setup_locked` also wires this for
        # every model it builds. `WebAPIHandler.log_buffer` is a
        # backward-compatible proxy over `adapter.append_log`, which already
        # caps the buffer at 500 under its own lock - it has no `pop`, so
        # this used to raise AttributeError the moment its own manual size
        # check ever tripped.
        models = (self.system_manager.get_active_models_snapshot()
                  if self.system_manager else {})
        for name, model in models.items():
            if hasattr(model, "poller") and model.poller:
                def make_logger(p_name):
                    def append_log(msg):
                        WebAPIHandler.log_buffer.append(f"[{p_name}] {msg}")
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
