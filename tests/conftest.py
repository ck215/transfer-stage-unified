import sys
import os
import pytest

# Set headless Qt platform before any Qt fixtures initialize.
# Without this, pytest-qt's qapp fixture calls QApplication() which
# aborts immediately on macOS when no display server is available.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_qt_probe_ok = True  # non-macOS, or PySide6 absent: no known issue, never skip

if sys.platform == "darwin":
    # macOS marks pip-downloaded PySide6 .dylib files UF_HIDDEN, which makes
    # Qt's plugin scanner silently skip them — QApplication() then aborts
    # with a native SIGABRT (qt_check_pointer) instead of raising a Python
    # exception, which looks like pytest hanging/crashing. Same class of fix
    # as run_macos.sh's launcher self-heal — but unlike that script, the
    # chflags call alone is NOT self-verifying: confirmed via repeated
    # full-suite runs that qt_check_pointer still aborts non-deterministically
    # even with chflags already applied (two back-to-back runs of identical
    # code/suite produced one clean pass and one crash at the same qapp
    # fixture — not order- or composition-dependent). A crash here takes the
    # *entire* pytest session down with it, discarding every already-passed
    # test's result along with it, which is the actual problem worth fixing
    # regardless of the exact race underneath.
    #
    # So: verify in a throwaway *subprocess* (mirroring run_macos.sh's own
    # verify-before-use pattern) that constructing a real QApplication
    # actually survives, before the real session ever risks it. A crash in
    # that subprocess only ends the subprocess — pytest itself keeps running.
    # If it fails, retry chflags once (the flag may have been reasserted, or
    # raced the first pass) and probe again. If it still fails, mark Qt as
    # unavailable for this session so pytest_collection_modifyitems below can
    # skip qapp/qtbot-dependent tests gracefully instead of letting the real
    # fixture crash the whole run later.
    try:
        import subprocess
        import PySide6
        pyside6_dir = os.path.dirname(PySide6.__file__)

        def _probe_qapplication():
            result = subprocess.run(
                [sys.executable, "-c",
                 "from PySide6.QtWidgets import QApplication; QApplication([])"],
                env=os.environ.copy(), capture_output=True, timeout=30,
            )
            return result.returncode == 0

        subprocess.run(["chflags", "-R", "nohidden", pyside6_dir], check=False)
        if not _probe_qapplication():
            subprocess.run(["chflags", "-R", "nohidden", pyside6_dir], check=False)
            _qt_probe_ok = _probe_qapplication()
    except ImportError:
        pass


def pytest_collection_modifyitems(config, items):
    if _qt_probe_ok:
        return
    skip_qt = pytest.mark.skip(
        reason="QApplication() aborts natively in this environment even after "
               "the chflags self-heal + retry (see conftest.py's "
               "_probe_qapplication) — skipping Qt-dependent tests instead of "
               "crashing the whole session."
    )
    for item in items:
        if "qapp" in item.fixturenames or "qtbot" in item.fixturenames:
            item.add_marker(skip_qt)

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
    def columnconfigure(self, *args, **kwargs): pass
    def rowconfigure(self, *args, **kwargs): pass
    def grid(self, *args, **kwargs): pass
tkinter_mock.Toplevel = DummyTkWidget
tkinter_mock.Frame = DummyTkWidget
tkinter_mock.Tk = DummyTkWidget
sys.modules['tkinter'] = tkinter_mock
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()

# Add the src directory to the python path


import threading
import weakref

_live_tracked_instances = weakref.WeakSet()

# Three model classes each start an unmanaged daemon thread in __init__ or
# enable() that only stops via an explicit method call most unit tests never
# make (they construct the model directly, bypassing SystemManager, the only
# thing that normally tears models down). Each leaked thread holds a strong
# closure reference to its owning instance, so nothing it references is ever
# collected either — and for the two that poll a *mocked* serial connection
# at ~60-100Hz (TemperatureSystem, RedPercentSystem), every iteration also
# grows that MagicMock's permanent call-history bookkeeping. Confirmed via
# `sample` on a running full-suite process twice: first at 72GB physical
# footprint (BaseProbe's 5s-interval watchdog thread leaking, main thread
# stuck in gc_collect_main), then again at 55GB after fixing that alone —
# `Thread-N (read_serial_data)` threads (TemperatureSystem) were still
# leaking unaddressed. Stop-procedures below are the third and final
# confirmed leak source (RedPercentSystem's monitor thread) added
# preemptively, same pattern, before hitting it as a fourth surprise.
_STOP_LEAKED_THREAD_PROCEDURES = []  # populated by _install_background_thread_tracking


def _track_and_get_thread(instance, thread_attr):
    _live_tracked_instances.add(instance)
    return thread_attr


def _stop_base_probe(instance):
    stop_event = getattr(instance, "_interlock_stop", None)
    if stop_event is not None:
        stop_event.set()
    return getattr(instance, "_interlock_thread", None)


def _stop_temperature_system(instance):
    instance.continue_reading = False
    return getattr(instance, "serial_thread", None)


def _stop_redpercent_system(instance):
    instance.monitoring = False
    return getattr(instance, "_monitor_thread", None)


def _install_background_thread_tracking():
    """See _live_tracked_instances' comment above for why this exists.
    Wraps each tracked class's __init__ once, at collection time, to record
    every instance in a WeakSet (weak so the tracking itself can't leak),
    pairing each with its class-specific stop-procedure. An autouse fixture
    below sweeps the set and stops every leaked thread after every single
    test — mirrors _reset_global_error_routing below for the same reason:
    process-wide state that must not leak from one test into the rest of
    the session.
    """
    if _STOP_LEAKED_THREAD_PROCEDURES:
        return  # already installed this session

    from model.probes import BaseProbe
    from model.temperature_system import TemperatureSystem
    from model.redpercent_system import RedPercentSystem

    for cls, stop_fn in (
        (BaseProbe, _stop_base_probe),
        (TemperatureSystem, _stop_temperature_system),
        (RedPercentSystem, _stop_redpercent_system),
    ):
        original_init = cls.__init__

        def _tracking_init(self, *args, __original_init=original_init, **kwargs):
            __original_init(self, *args, **kwargs)
            _live_tracked_instances.add(self)

        cls.__init__ = _tracking_init
        _STOP_LEAKED_THREAD_PROCEDURES.append((cls, stop_fn))


@pytest.fixture(autouse=True)
def _stop_leaked_background_threads():
    """Teardown half of _install_background_thread_tracking(): see that
    function's docstring for why this exists."""
    _install_background_thread_tracking()
    yield
    threads_to_join = []
    for instance in list(_live_tracked_instances):
        for cls, stop_fn in _STOP_LEAKED_THREAD_PROCEDURES:
            if isinstance(instance, cls):
                threads_to_join.append(stop_fn(instance))
                break
    for thread in threads_to_join:
        if isinstance(thread, threading.Thread) and thread.is_alive():
            thread.join(timeout=1.0)


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
            
        def teardown(self):
            self.power_down()
            
    return DualShutdownModel()
