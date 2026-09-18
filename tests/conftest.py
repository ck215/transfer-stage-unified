import sys
import os
import pytest

# Set headless Qt platform before any Qt fixtures initialize.
# Without this, pytest-qt's qapp fixture calls QApplication() which
# aborts immediately on macOS when no display server is available.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if sys.platform == "darwin":
    # macOS marks pip-downloaded PySide6 .dylib files UF_HIDDEN, which makes
    # Qt's plugin scanner silently skip them — QApplication() then aborts
    # with a native SIGABRT (qt_check_pointer) instead of raising a Python
    # exception, which looks like pytest hanging/crashing. Same fix as
    # run_macos.sh's launcher self-heal; must run before any PySide6 import.
    try:
        import subprocess
        import PySide6
        pyside6_dir = os.path.dirname(PySide6.__file__)
        subprocess.run(["chflags", "-R", "nohidden", pyside6_dir], check=False)
    except ImportError:
        pass

from unittest.mock import MagicMock

# Mock libraries that might not be installed
sys.modules['pygame'] = MagicMock()
sys.modules['pygame'].error = Exception

mock_serial_lib = MagicMock()
mock_serial_lib.SerialException = Exception
mock_serial_lib.SerialTimeoutException = Exception
mock_serial_tools = MagicMock()
mock_serial_list_ports = MagicMock()
mock_serial_tools.list_ports = mock_serial_list_ports
mock_serial_lib.tools = mock_serial_tools

sys.modules['serial'] = mock_serial_lib
sys.modules['serial.tools'] = mock_serial_tools
sys.modules['serial.tools.list_ports'] = mock_serial_list_ports

sys.modules['PIL'] = MagicMock()
sys.modules['mss'] = MagicMock()
sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.figure'] = MagicMock()
sys.modules['matplotlib.backends'] = MagicMock()
sys.modules["matplotlib.backends.backend_qtagg"] = MagicMock()
sys.modules["matplotlib.figure"] = MagicMock()

sys.modules['matplotlib.backends.backend_tkagg'] = MagicMock()
sys.modules['matplotlib.colors'] = MagicMock()
tkinter_mock = MagicMock()
class DummyTkWidget:
    def __init__(self, master=None, *args, **kwargs): self.master = master or __import__('unittest.mock').mock.MagicMock()
    def protocol(self, *args, **kwargs): pass
    def destroy(self): pass
    def deiconify(self): pass
    def configure(self, *args, **kwargs): pass
    def pack(self, *args, **kwargs): pass
    def bind(self, *args, **kwargs): pass
    def title(self, *args, **kwargs): pass
    def geometry(self, *args, **kwargs): pass
    def minsize(self, *args, **kwargs): pass
    def after(self, *args, **kwargs): pass
    def winfo_exists(self, *args, **kwargs): return True
tkinter_mock.Toplevel = DummyTkWidget
tkinter_mock.Frame = DummyTkWidget
tkinter_mock.Tk = DummyTkWidget
sys.modules['tkinter'] = tkinter_mock
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()

# Add the src directory to the python path


@pytest.fixture(autouse=True)
def _reset_global_error_routing():
    """QtErrorPopupManager (views/pyside/view.py) is a process-wide class-level
    singleton: once any test calls .initialize(), it globally rewires
    ErrorRouter's callbacks (also process-wide class state) to real,
    blocking QMessageBox popups for the rest of the pytest process — not
    just its own test. Under the offscreen Qt platform there is no user to
    click the dialog, so the next unrelated test anywhere in the session
    that triggers ErrorRouter.report_error/warning/info hits QDialog.exec()
    and hangs forever (confirmed via a native stack sample: the hang sits
    in QDialog::exec() -> qt_safe_poll, reached only through this signal
    chain). Reset both pieces of global state after every test so Qt-popup
    routing never leaks into a later, unrelated test.
    """
    yield
    from error_routing import ErrorRouter
    ErrorRouter._error_cb = None
    ErrorRouter._warning_cb = None
    ErrorRouter._info_cb = None
    try:
        from views.pyside.view import QtErrorPopupManager
        QtErrorPopupManager._instance = None
    except ImportError:
        pass

@pytest.fixture
def dual_shutdown_model():
    class DualShutdownModel:
        def __init__(self):
            self.power_down_calls = 0
            self.disable_calls = 0
            
        def power_down(self):
            self.power_down_calls += 1
            
        def disable(self):
            self.disable_calls += 1
            
    return DualShutdownModel()
