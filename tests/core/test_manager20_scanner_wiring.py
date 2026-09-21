"""MANAGER-20 — the Qt setup window's scanner shutdown is actually wired up.

Both `SetupWindow` classes are defined *inside* launcher functions
(`run_legacy_app`, `run_pyside_app`), and the PySide one cannot be imported
without building a `QApplication` and entering `app.exec()`. So the
behavioural test for it is qt-marked and lives in
`tests/ui/test_manager20_setup_window_close.py`, where a native Qt abort
cannot take the rest of the session with it.

This file is the part that runs in the ordinary gate: it reads `app.py` with
`ast` and pins that the pieces exist and refer to each other. That is weaker
than exercising them — it cannot tell you the close actually joins — but it
is a real regression guard, because the failure mode being prevented is
silent. Deleting `closeEvent`, or forgetting to pass `should_abort` into
`probe_device_at`, reintroduces the defect with no test failing anywhere
else.
"""

import ast
import inspect
import pathlib

import pytest

import app
import app_bootstrap

APP_PY = pathlib.Path(app.__file__)


def _tree():
    return ast.parse(APP_PY.read_text(encoding="utf-8"))


def _function(name, tree=None):
    for node in ast.walk(tree or _tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in {APP_PY}")


def _class_in(function_name, class_name):
    """A class defined inside a launcher function, by name."""
    for node in ast.walk(_function(function_name)):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(
        f"class {class_name} not found inside {function_name}()")


def _method(cls_node, name):
    for node in cls_node.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{cls_node.name}.{name}() is missing")


def _names_used(node):
    used = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            used.add(child.attr)
        elif isinstance(child, ast.Name):
            used.add(child.id)
    return used


def _int_class_attr(cls_node, name):
    for node in cls_node.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name
                        for t in node.targets)
                and isinstance(node.value, ast.Constant)):
            return node.value.value
    raise AssertionError(f"{cls_node.name}.{name} is not a literal constant")


# --------------------------------------------------------------------------
# The window stops its scanner before it goes away.
# --------------------------------------------------------------------------


def test_the_qt_setup_window_defines_a_close_event():
    window = _class_in("run_pyside_app", "SetupWindow")
    _method(window, "closeEvent")


def test_close_event_stops_the_scanner():
    window = _class_in("run_pyside_app", "SetupWindow")
    assert "stop_scanner" in _names_used(_method(window, "closeEvent"))


def test_stop_scanner_requests_interruption_and_waits_with_a_bound():
    window = _class_in("run_pyside_app", "SetupWindow")
    used = _names_used(_method(window, "stop_scanner"))
    assert "requestInterruption" in used, "the thread is never told to stop"
    assert "wait" in used, "the close does not join the scanner"
    assert "SCANNER_SHUTDOWN_MS" in used, (
        "the wait has no bound; a stop that can hang the close is its own bug")


def test_the_shutdown_wait_is_bounded_and_short():
    """A ceiling on how long closing the window may appear to hang."""
    window = _class_in("run_pyside_app", "SetupWindow")
    budget_ms = _int_class_attr(window, "SCANNER_SHUTDOWN_MS")
    assert 0 < budget_ms <= 5000, budget_ms


def test_a_scanner_that_will_not_stop_is_parked_rather_than_destroyed():
    """A QThread wrapper collected while the thread runs aborts the process."""
    window = _class_in("run_pyside_app", "SetupWindow")
    assert "_orphaned_scanners" in _names_used(_method(window, "stop_scanner"))
    assert "_orphaned_scanners" in _names_used(_function("run_pyside_app"))


def test_starting_a_new_scan_stops_any_previous_one():
    """Replacing `self.scanner` while the old QThread runs is the same crash."""
    window = _class_in("run_pyside_app", "SetupWindow")
    assert "stop_scanner" in _names_used(_method(window, "start_autodetect"))


# --------------------------------------------------------------------------
# ...and the scanner, and the probe it blocks inside, both look.
# --------------------------------------------------------------------------


def test_the_scanner_thread_checks_for_interruption():
    scanner = _class_in("run_pyside_app", "ScannerThread")
    assert "isInterruptionRequested" in _names_used(_method(scanner, "run"))


def test_the_scanner_passes_an_abort_hook_into_probe_device_at():
    """`requestInterruption()` sets a flag; something has to read it.

    A port costs ~1.5 s + 3 s per baud, so checking only between ports leaves
    `wait()` blocked for most of that. The hook has to go *into* the probe.
    """
    scanner = _class_in("run_pyside_app", "ScannerThread")
    for node in ast.walk(_method(scanner, "run")):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "probe_device_at"):
            kwargs = {kw.arg for kw in node.keywords}
            assert "should_abort" in kwargs, (
                "probe_device_at is called without an abort hook, so an "
                "interruption request is not noticed until the port's full "
                "4.5 s per baud has elapsed")
            return
    raise AssertionError("ScannerThread.run() never calls probe_device_at")


def test_probe_device_at_accepts_the_abort_hook():
    signature = inspect.signature(app_bootstrap.probe_device_at)
    assert "should_abort" in signature.parameters
    assert signature.parameters["should_abort"].default is None


# --------------------------------------------------------------------------
# The Tk half is deliberately untouched.
# --------------------------------------------------------------------------


def test_the_tk_setup_window_is_left_alone():
    """Tk's scan runs on a daemon thread and is harmless at exit.

    Recorded so that "why does only one of the two have this?" has an answer
    in the suite rather than only in a commit message.
    """
    tk_window = _class_in("run_legacy_app", "SetupWindow")
    method_names = {n.name for n in tk_window.body
                    if isinstance(n, ast.FunctionDef)}
    assert "closeEvent" not in method_names


# --------------------------------------------------------------------------
# ...and, since neither method actually needs Qt, they are *exercised* here.
# --------------------------------------------------------------------------
#
# `ScannerThread` and `SetupWindow` are lifted out of `app.py`'s syntax tree
# and defined against stand-in base classes. What runs is the real code in
# `src/app.py`, not a replica of it — a replica would pass happily while the
# shipped version stayed broken, which is how MANAGER-20 got written in the
# first place. Only the base class is fake, and neither `run()`'s
# interruption checks nor `stop_scanner()`'s request-then-wait touches
# anything Qt-specific beyond `isInterruptionRequested` / `requestInterruption`
# / `wait`, all three of which a stand-in can provide faithfully.
#
# The same two classes against a *real* `QThread` are in
# tests/ui/test_manager20_setup_window_close.py, which is qt-marked.


class _Signal:
    """A Qt signal stand-in that records what was emitted."""

    def __init__(self, *types):
        self.emissions = []

    def emit(self, *args):
        self.emissions.append(args)

    def connect(self, fn):
        pass


class _MainWindow:
    """A QMainWindow stand-in, so `super().closeEvent(event)` resolves."""

    def __init__(self, *args, **kwargs):
        self.closed = []

    def closeEvent(self, event):
        self.closed.append(event)


class _Thread:
    """A QThread stand-in: interruption flag, and a `wait` the test controls."""

    def __init__(self, *args, **kwargs):
        self._interrupted = False
        self.stops_on_wait = True
        self.running = False
        self.waits = []

    def isInterruptionRequested(self):
        return self._interrupted

    def requestInterruption(self):
        self._interrupted = True

    def isRunning(self):
        return self.running

    def wait(self, timeout_ms=None):
        self.waits.append(timeout_ms)
        if self.stops_on_wait:
            self.running = False
            return True
        return False


def _lift(class_names):
    """Define the named classes from `run_pyside_app` into a fresh namespace."""
    wanted = [_class_in("run_pyside_app", name) for name in class_names]
    wanted.sort(key=lambda n: n.lineno)
    module = ast.Module(body=wanted, type_ignores=[])
    ast.fix_missing_locations(module)
    code = compile(module, str(APP_PY), "exec")
    namespace = {
        "__name__": "app_pyside_scope",
        "QThread": _Thread,
        "QMainWindow": _MainWindow,
        "Signal": _Signal,
        "SERIAL_AVAILABLE": True,
        "_orphaned_scanners": [],
    }
    eval(code, namespace)
    return namespace


class _Window:
    """Just enough of a SetupWindow for `stop_scanner`: a scanner and a budget."""

    def __init__(self, scanner, budget_ms=3000):
        self.scanner = scanner
        self.SCANNER_SHUTDOWN_MS = budget_ms


@pytest.fixture
def lifted():
    return _lift(["ScannerThread", "SetupWindow"])


def test_stop_scanner_is_a_no_op_when_no_scan_is_running(lifted):
    stop = lifted["SetupWindow"].stop_scanner
    assert stop(_Window(None)) is True

    idle = _Thread()
    idle.running = False
    assert stop(_Window(idle)) is True
    assert idle.waits == [], "an idle scanner was joined for no reason"


def test_stop_scanner_requests_interruption_then_joins(lifted):
    scanner = _Thread()
    scanner.running = True
    window = _Window(scanner, budget_ms=3000)

    assert lifted["SetupWindow"].stop_scanner(window) is True
    assert scanner.isInterruptionRequested(), "the thread was never told to stop"
    assert scanner.waits == [3000], (
        f"expected one bounded wait of 3000 ms, got {scanner.waits}")


def test_a_scanner_that_ignores_the_request_is_parked_not_destroyed(lifted):
    """The bounded wait expires: the window still closes, the process lives.

    A QThread whose Python wrapper is collected while the thread is still
    running is the "QThread: Destroyed while thread is still running" abort
    this finding names, so the reference is kept rather than dropped.
    """
    scanner = _Thread()
    scanner.running = True
    scanner.stops_on_wait = False
    window = _Window(scanner)

    assert lifted["SetupWindow"].stop_scanner(window) is False
    assert scanner in lifted["_orphaned_scanners"]
    assert window.scanner is None, (
        "the window kept the only reference to a running thread it is about "
        "to be destroyed with")


def test_close_event_joins_the_scanner_and_clears_the_scanning_flag(lifted):
    """A real `SetupWindow`, built without its widget-heavy `__init__`.

    `closeEvent` uses the zero-argument `super()`, so `self` has to be an
    actual instance of the lifted class — `object.__new__` gives one with no
    `QApplication` and no widgets in sight.
    """
    scanner = _Thread()
    scanner.running = True

    window = object.__new__(lifted["SetupWindow"])
    window.closed = []
    window.scanner = scanner
    window.is_scanning = True

    event = object()
    window.closeEvent(event)

    assert scanner.isInterruptionRequested(), "the close did not stop the scan"
    assert scanner.waits == [lifted["SetupWindow"].SCANNER_SHUTDOWN_MS]
    assert window.is_scanning is False
    assert window.closed == [event], "the base class close never ran"


def test_the_scanner_run_loop_returns_on_an_interruption_request(
        lifted, monkeypatch):
    probed = []

    def fake_probe(port, should_abort=None):
        probed.append(port)
        return None

    monkeypatch.setattr(app_bootstrap, "probe_device_at", fake_probe)

    scanner = lifted["ScannerThread"](
        ["Stepper Probe"], ["/dev/ttyFAKE0", "/dev/ttyFAKE1"])
    scanner.requestInterruption()
    scanner.run()

    assert probed == [], "the scan kept probing after being interrupted"
    assert scanner.complete.emissions == [], (
        "an interrupted scan emitted complete(), driving the progress UI of "
        "a window that is going away")


def test_the_scanner_run_loop_hands_its_interruption_flag_to_the_probe(
        lifted, monkeypatch):
    hooks = []

    def fake_probe(port, should_abort=None):
        hooks.append(should_abort)
        return None

    monkeypatch.setattr(app_bootstrap, "probe_device_at", fake_probe)

    scanner = lifted["ScannerThread"](["Stepper Probe"], ["/dev/ttyFAKE0"])
    scanner.run()

    assert len(hooks) == 1
    assert callable(hooks[0])
    assert hooks[0]() is False
    scanner.requestInterruption()
    assert hooks[0]() is True, (
        "the hook handed to probe_device_at is not this thread's "
        "interruption flag")


def test_an_uninterrupted_scan_still_completes(lifted, monkeypatch):
    monkeypatch.setattr(app_bootstrap, "probe_device_at",
                        lambda port, should_abort=None: "Stepper Probe")

    scanner = lifted["ScannerThread"](["Stepper Probe"], ["/dev/ttyFAKE0"])
    scanner.run()

    assert scanner.found.emissions == [("Stepper Probe", "/dev/ttyFAKE0")]
    assert scanner.complete.emissions == [()]
