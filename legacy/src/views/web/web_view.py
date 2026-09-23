import os
import sys
import webbrowser
import time
from error_routing import ErrorRouter
from .web_server import WebDashboardServer

class WebErrorManager:
    """The web view's three report_* shims onto `ErrorRouter`.

    **It no longer mirrors the bus** (ERRORS-12). It used to subscribe and
    copy every event into `WebAPIHandler.error_buffer`, because
    `/api/errors` once read that buffer. S11 moved the route onto the bus
    itself (`errors_since`/`latest_error_id`), which left the mirror as a
    second account of the same events — one that lived on whichever
    `WebModelAdapter` was current, and that the proxy holding it would
    lazily *construct* if there were none. The buffer, the proxy and the
    subscription are all gone; `initialize()`/`shutdown()` went with them,
    since subscribing was the whole of what they did.

    The three report_* names are kept: a handful of call sites and tests
    reach the web manager directly rather than through ErrorRouter.
    """

    @classmethod
    def report_error(cls, title, message, exception=None):
        # ERRORS-3: Print to console even when subscribers exist.
        # EventBus only prints when no subscribers are registered, so we
        # always print here to ensure errors reach the console.
        print(f"[ERROR] web/{title}: {message}")
        if exception is not None:
            import traceback
            traceback.print_exception(
                type(exception), exception, exception.__traceback__)
        ErrorRouter.report_error(title, message, exception, source="web")

    @classmethod
    def report_warning(cls, title, message, exception=None):
        # ERRORS-3: Print to console even when subscribers exist.
        print(f"[WARNING] web/{title}: {message}")
        if exception is not None:
            import traceback
            traceback.print_exception(
                type(exception), exception, exception.__traceback__)
        ErrorRouter.report_warning(title, message, exception, source="web")

    @classmethod
    def report_info(cls, title, message):
        # ERRORS-3: Print to console even when subscribers exist.
        print(f"[INFO] web/{title}: {message}")
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

        # Nothing to connect for errors: `ErrorRouter` publishes to the
        # bus and `/api/errors` reads the bus (ERRORS-12).

        # Connect poller logs for any models already present at construction
        # time (WEB-5). This only ever covers a manager built before the
        # window - in practice that is an empty manager in `run_web_app`,
        # since real models are built later by the setup wizard, which is
        # why `WebModelAdapter._initialize_setup_locked` also wires this for
        # every model it builds. It logs to **this server's** adapter: the
        # `WebAPIHandler.log_buffer` proxy it used to write through forwarded
        # to whichever adapter the handler *class* held, which is a different
        # object until `show()` re-points it, so a log emitted before then
        # went somewhere `/api/logs` never looks (ERRORS-12). `append_log`
        # caps the buffer at 500 under its own lock.
        models = (self.system_manager.get_active_models_snapshot()
                  if self.system_manager else {})
        adapter = self.server.adapter
        for name, model in models.items():
            if hasattr(model, "poller") and model.poller:
                def make_logger(p_name):
                    def append_log(msg):
                        adapter.append_log(f"[{p_name}] {msg}")
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
