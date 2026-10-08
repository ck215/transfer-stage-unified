"""The Web frontend. `WebView(controller, setup)` is the whole entry point."""
from views.web.server import ApiHandler, SETUP_NAME, WebView

__all__ = ["ApiHandler", "SETUP_NAME", "WebView"]
