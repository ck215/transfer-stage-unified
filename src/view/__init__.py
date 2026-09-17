# Web-view layer exports
from .web_view import WebDashboardWindow, WebErrorManager
from .web_server import WebDashboardServer, WebAPIHandler
from .web_adapter import WebModelAdapter

# Legacy view.py compatibility shim — keeps 'from view import ErrorPopupManager'
# and 'from view import DashboardWindow/DynamicView' working now that the view/
# package shadows the sibling view.py module.
import importlib.util as _ilu, os as _os

def _import_legacy():
    import os as _os, importlib.util as _ilu
    _here = _os.path.dirname(_os.path.abspath(__file__))           # .../src/view/
    _src  = _os.path.dirname(_here)                                # .../src/
    _spec = _ilu.spec_from_file_location("_view_legacy", _os.path.join(_src, "view.py"))
    _mod  = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod

_legacy = _import_legacy()

ErrorPopupManager = _legacy.ErrorPopupManager
DashboardWindow   = _legacy.DashboardWindow
DynamicView       = _legacy.DynamicView
messagebox        = getattr(_legacy, "messagebox", None)

import sys as _sys
class _ViewModule(_sys.modules[__name__].__class__):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if "_legacy" in self.__dict__ and hasattr(_legacy, name):
            setattr(_legacy, name, value)

_sys.modules[__name__].__class__ = _ViewModule

__all__ = [
    # Web layer
    "WebDashboardWindow",
    "WebErrorManager",
    "WebDashboardServer",
    "WebAPIHandler",
    "WebModelAdapter",
    # Legacy layer
    "ErrorPopupManager",
    "DashboardWindow",
    "DynamicView",
    "messagebox",
]
