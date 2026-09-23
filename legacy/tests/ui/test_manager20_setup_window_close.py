"""MANAGER-20 — closing the Qt setup window mid-scan, against a real QThread.

**qt-marked, and NOT run by the agent that wrote it.** A native Qt `SIGABRT`
kills the whole pytest session and discards every already-passed result, so
these are left for the Qt pass. Everything about this finding that can be
checked without a real `QThread` is already covered, and *is* run, in
`tests/core/test_manager20_scanner_wiring.py` (the same two classes lifted
from `app.py` against stand-in base classes) and
`tests/core/test_manager20_scan_abort.py` (`probe_device_at`'s abort hook).

What is left here, and the only reason this file exists, is the part a
stand-in cannot honestly model: that `QThread.requestInterruption()` ->
`isInterruptionRequested()` -> `wait()` really does bring a running scan
down, promptly, through the real Qt machinery.

`ScannerThread` and `SetupWindow` are defined *inside* `run_pyside_app()`,
after it has already built a `QApplication`, so neither can be imported.
They are lifted out of `app.py`'s syntax tree and defined in a namespace
holding just the names their class statements need, so what runs is the real
code in `src/app.py` and not a replica of it.
"""

import ast
import pathlib
import threading
import time

import pytest

import app

pytestmark = pytest.mark.qt

APP_PY = pathlib.Path(app.__file__)


def _lift(class_names, namespace):
    """Define the named classes from `run_pyside_app` into `namespace`."""
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))
    launcher = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "run_pyside_app")
    wanted = [n for n in ast.walk(launcher)
              if isinstance(n, ast.ClassDef) and n.name in class_names]
    assert {n.name for n in wanted} == set(class_names), (
        f"missing from app.py: {set(class_names) - {n.name for n in wanted}}")
    wanted.sort(key=lambda n: n.lineno)
    module = ast.Module(body=wanted, type_ignores=[])
    ast.fix_missing_locations(module)
    # Compiled from app.py's own syntax tree — nothing here is user input.
    eval(compile(module, str(APP_PY), "exec"), namespace)
    return namespace


@pytest.fixture
def qt_scope(qapp):
    """The real ScannerThread and SetupWindow, over real Qt base classes."""
    from PySide6.QtCore import QThread, Signal
    from PySide6.QtWidgets import QMainWindow

    namespace = {
        "__name__": "app_pyside_scope",
        "QThread": QThread,
        "Signal": Signal,
        "QMainWindow": QMainWindow,
        "SERIAL_AVAILABLE": True,
        "_orphaned_scanners": [],
    }
    return _lift(["ScannerThread", "SetupWindow"], namespace)


class _Window:
    """Just enough of a SetupWindow for `stop_scanner`: a scanner and a budget.

    `stop_scanner` does not use the zero-argument `super()`, so it can be
    called unbound on any object carrying those two attributes. That keeps
    this test away from `SetupWindow.__init__`, which builds the whole setup
    grid and runs a port scan of its own.
    """

    def __init__(self, scanner, budget_ms):
        self.scanner = scanner
        self.SCANNER_SHUTDOWN_MS = budget_ms


def _blocking_probe(entered, released):
    """A `probe_device_at` that sits in a polling loop until interrupted.

    Faithful to the real one in the way that matters: it takes seconds, and
    it consults `should_abort` while it does.
    """
    def probe(port, should_abort=None):
        entered.set()
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if should_abort is not None and should_abort():
                released.set()
                return None
            time.sleep(0.01)
        released.set()
        return None
    return probe


def test_a_running_scanner_thread_stops_when_interruption_is_requested(
        qt_scope, monkeypatch):
    import app_bootstrap

    entered, released = threading.Event(), threading.Event()
    monkeypatch.setattr(app_bootstrap, "probe_device_at",
                        _blocking_probe(entered, released))

    completed = []
    scanner = qt_scope["ScannerThread"](["Stepper Probe"], ["/dev/ttyFAKE0"])
    scanner.complete.connect(lambda: completed.append(True))
    scanner.start()
    try:
        assert entered.wait(5.0), "the scan never reached the probe"
        scanner.requestInterruption()
        assert scanner.wait(5000), "the scanner ignored the interruption"
        assert released.is_set(), "probe_device_at never saw the abort hook"
        assert completed == [], (
            "an interrupted scan emitted complete(), which would drive the "
            "progress UI of a window that is going away")
    finally:
        scanner.requestInterruption()
        scanner.wait(5000)


def test_stop_scanner_brings_a_real_running_scan_down_promptly(
        qt_scope, monkeypatch):
    """The path `closeEvent` takes, against a real QThread mid-probe."""
    import app_bootstrap

    entered, released = threading.Event(), threading.Event()
    monkeypatch.setattr(app_bootstrap, "probe_device_at",
                        _blocking_probe(entered, released))

    scanner = qt_scope["ScannerThread"](["Stepper Probe"], ["/dev/ttyFAKE0"])
    scanner.start()
    try:
        assert entered.wait(5.0)
        started = time.time()
        stopped = qt_scope["SetupWindow"].stop_scanner(_Window(scanner, 5000))
        elapsed = time.time() - started

        assert stopped is True, "stop_scanner gave up on a scan that can stop"
        assert not scanner.isRunning()
        assert elapsed < 3.0, (
            f"the close waited {elapsed:.1f}s; the abort hook is not being "
            f"seen inside the probe's polling loop")
    finally:
        scanner.requestInterruption()
        scanner.wait(5000)
