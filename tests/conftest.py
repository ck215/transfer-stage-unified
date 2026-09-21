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


class ManagedStub:
    """A minimal real ManagedModel, for tests that register something.

    SystemManager.register() enforces `isinstance(model, ManagedModel)` at the
    boundary (RC-1), and under Python 3.12+ a runtime_checkable Protocol's
    isinstance check is stricter than hasattr — no Mock satisfies it however
    it is spec'd. That is correct: a Mock passing a lifecycle contract check
    was never evidence of anything. Tests that need a registrable model use
    this instead, and read `.stops` / `.teardowns` to assert on it.
    """

    def __init__(self, name="stub", stop_error=None, teardown_error=None):
        self.name = name
        self.stop_error, self.teardown_error = stop_error, teardown_error
        self.stops = self.teardowns = 0

    def emergency_stop(self):
        self.stops += 1
        if self.stop_error:
            raise self.stop_error

    def teardown(self):
        self.teardowns += 1
        if self.teardown_error:
            raise self.teardown_error


@pytest.fixture
def managed_stub():
    return ManagedStub


# ---------------------------------------------------------------------------
# Suite organization: concern markers, slow marking, known-bad quarantine.
# Policy and per-stage gate commands: docs/implementation/testing.md
# ---------------------------------------------------------------------------

# Concern markers per test file. Markers map to the repair stages in
# docs/implementation/plan.md, so each stage can gate on a targeted subset
# instead of the whole suite. Kept here, in one table, rather than as 39
# scattered `pytestmark` lines.
_FILE_MARKERS = {
    "architecture/test_invariants.py": ["invariants"],
    "core/test_app_bootstrap.py": ["bootstrap"],
    "core/test_composition_root.py": ["bootstrap", "lifecycle", "redpercent"],
    "core/test_edge_mvc_model.py": ["lifecycle", "mode"],
    "core/test_gamepad_interlock.py": ["loops", "mode"],
    "core/test_integration.py": ["integration"],
    "core/test_hide_show.py": ["lifecycle"],
    "core/test_lifecycle_teardown.py": ["lifecycle", "estop"],
    "core/test_lifecycle_exit.py": ["lifecycle", "estop", "bootstrap"],
    "core/test_transport_truth.py": ["transport", "estop"],
    "core/test_typed_params.py": ["params"],
    "core/test_model_owned_loops.py": ["loops", "mode", "estop"],
    "core/test_model_interactions.py": ["mode"],
    "core/test_model_round1.py": ["mode", "lifecycle"],
    "core/test_model_round2.py": ["mode"],
    "core/test_plot_data.py": ["redpercent"],
    "core/test_probe_mode.py": ["mode"],
    "core/test_probes.py": ["mode"],
    "core/test_redpercent_datalog.py": ["redpercent"],
    "core/test_redpercent_schema.py": ["redpercent", "schema"],
    "core/test_redpercent.py": ["redpercent"],
    "core/test_system_manager.py": ["lifecycle"],
    "core/test_temperature_subsystem.py": ["transport"],
    "core/test_temperature.py": ["transport"],
    "core/test_tkinter_full_stop.py": ["estop", "tk"],
    "core/test_tkinter_teardown.py": ["lifecycle", "tk"],
    "core/test_view_round1.py": ["schema", "qt"],
    "edge_cases/test_edge_mvc_boundary.py": ["params"],
    "edge_cases/test_edge_mvc_concurrency.py": ["loops"],
    "edge_cases/test_edge_mvc_disconnects.py": ["transport"],
    "edge_cases/test_edge_mvc_error_router.py": ["errors"],
    "edge_cases/test_edge_mvc_invalid_params.py": ["params"],
    "edge_cases/test_edge_mvc_state_transitions.py": ["mode"],
    "edge_cases/test_qa_round1.py": ["integration"],
    "hardware/test_controller_round1.py": ["loops"],
    "hardware/test_gamepad.py": ["loops"],
    "hardware/test_hal_round1.py": ["transport"],
    "hardware/test_hal_round2.py": ["transport"],
    "hardware/test_port_scanning.py": ["bootstrap"],
    "hardware/test_serial.py": ["transport"],
    "scripting/test_edge_mvc_parser.py": ["scripting"],
    "scripting/test_edge_mvc_scripting.py": ["scripting"],
    "ui/test_composites.py": ["schema"],
    "ui/test_edge_mvc_ui.py": ["schema", "qt"],
    "ui/test_schema_v2.py": ["schema"],
    "ui/test_ui_schema.py": ["schema"],
    "web/test_web_adapter.py": ["web"],
    "web/test_web_security.py": ["web"],
    "web/test_web_server.py": ["web"],
    "web/test_web_setup.py": ["web", "bootstrap"],
}

# Files whose tests are dominated by real sleeps. Excluding them with
# `-m "not slow"` is what makes per-stage gating usable.
#
# **Re-measured 2026-09-21 and six of the seven entries were removed.** The
# note that used to sit here said the sleeps were themselves findings
# (SERIAL-6, RC-4) and that "once construction moves off the calling thread
# in S5, most of this marking can go." That came true and nobody came back
# to collect it: SERIAL-6 closed on 2026-09-21, so construction now returns
# as soon as the port opens and does the 1.5 s bootloader wait plus the
# identity handshake on a background thread. Measured over the seven files:
# 17.99 s total, of which `core/test_app_bootstrap.py` alone is 17.2 s. The
# other six run 40 tests in **0.61 s**.
#
# Those 40 tests had been invisible to every working-gate run since S5 —
# including the whole of `edge_cases/test_edge_mvc_boundary.py`, which is
# where the NaN/infinity wire-format checks live. A stale quarantine is
# indistinguishable from deleted coverage, and it is worse, because the file
# is still there to reassure you.
#
# `core/test_app_bootstrap.py` stays: its cost is not construction but
# `test_probe_device_at_*`, which walk real baud-rate probe timeouts
# (9.25 s + 6.26 s + 1.71 s). That is inherent to what they test.
#
# Re-measure with `--durations=40` if construction cost changes again.
_SLOW_FILES = {
    "core/test_app_bootstrap.py",
}

# Known-bad tests: quarantined, never silently deleted. Each entry says why
# it fails and which stage re-authors it. strict=True on purpose — when the
# stage lands and the test starts passing, pytest reports XPASS as a
# failure, which forces the entry to be removed and the ledger updated. A
# quarantine that goes stale silently is how suites rot.
#
#   nodeid suffix -> (reason, owning stage)
# **Both S10 entries left this list in S10 and the quarantine is now empty.**
# `test_pyside_redpercent_sync_and_probe_controls` was re-authored against
# the schema toggles that replaced the hand-built checkbox row (D-6), and
# `test_pyside_dashboard_sidebar_dock_sync` XPASSed the moment schema v2
# made PYSIDE-7 inexpressible — `sch.dropdown()` refuses to build a dropdown
# with no command, so `getattr(self.model, None)` can no longer be reached.
# The five gamepad/mode entries retired in S7.
#
# An empty quarantine is the goal, not an invitation: add an entry rather
# than skip or delete a failing test, and give it an owning stage.
_KNOWN_BAD = {}


# Order-dependent tests: they PASS in isolation and FAIL in composition, so
# they are not known-bad (xfail would XPASS when run alone). They are honest
# tests sitting on a harness-isolation defect — process-wide state leaking
# between tests, plus wall-clock assertions that miss when the session is
# under load from other tests' threads. Excluded from targeted runs and from
# the default sweep; the final sweep runs them in their own pass.
#
# This is the same process-global-state theme the repair addresses (RC-8's
# ErrorRouter callbacks, RC-13's pygame and claims registry): once those stop
# being process-global, most of this set should dissolve. Do not "fix" one by
# adding a sleep or loosening a threshold.
_ORDER_DEPENDENT = {
    # `test_dashboard_window_teardown_ordering` was here. **Retired in S6, with
    # a proven cause.** It was not process-global product state, which is what
    # this file assumed for every entry. `sys.modules['tkinter.ttk']` was a
    # bare MagicMock, so `class DraggableClosableNotebook(ttk.Notebook)`
    # produced a MagicMock rather than a class — one whose `side_effect` is a
    # finite `tuple_iterator`. DashboardWindow was therefore constructible only
    # a bounded number of times per process, and the test that happened to be
    # last raised StopIteration from inside unittest.mock. `ttk.Notebook` is a
    # real stub class now (see DummyTkNotebook above).
    #
    # That is the second time a quarantine entry blamed the architecture and
    # turned out to be a harness wiring mistake. Read the actual exception.
    #
    # Wall-clock assertions (>= 0.28 s, ">= 5 reads per poller") that miss
    # when other tests' background threads are competing for the GIL.
    "web/test_web_server.py::test_thread_safety_concurrent_requests",
    "web/test_web_setup.py::test_thread_concurrency_setup_and_telemetry",
    #
    # The seven run_script tests that lived here are GONE as of S9, and the
    # cause was not what this file predicted. It was not ErrorRouter's
    # process-global callbacks and it was not STEPPER-8's untracked thread.
    # The test file installed its mock parser with
    # `sys.modules['gcodeparser'] = mock` at import time, which only works if
    # that file is what *first* imports model.probes — and several earlier
    # files import it, binding probes.gcodeparser to the real library.
    #
    # The failure did not look like a wiring mistake because the two parsers
    # disagree *subtly*: the real one yields Y as an int, so str(Y) is '20',
    # while the mock yields 20.0, so it is '20.0'. Same test, two parsers,
    # different answers, decided by import order.
    #
    # Patching `model.probes.gcodeparser` directly made all thirteen tests in
    # that file pass identically across repeated runs.
}


def pytest_collection_modifyitems(config, items):
    skip_qt = pytest.mark.skip(
        reason="QApplication() aborts natively in this environment even after "
               "the chflags self-heal + retry (see conftest.py's "
               "_probe_qapplication) — skipping Qt-dependent tests instead of "
               "crashing the whole session."
    )
    for item in items:
        rel = item.nodeid.split("::")[0]

        for marker in _FILE_MARKERS.get(rel, []):
            item.add_marker(getattr(pytest.mark, marker))
        if rel in _SLOW_FILES:
            item.add_marker(pytest.mark.slow)

        if any(item.nodeid.endswith(s) for s in _ORDER_DEPENDENT):
            item.add_marker(pytest.mark.order_dependent)

        for suffix, (reason, stage) in _KNOWN_BAD.items():
            if item.nodeid.endswith(suffix):
                item.add_marker(pytest.mark.known_bad)
                item.add_marker(pytest.mark.xfail(
                    reason=f"[known-bad, owned by {stage}] {reason}",
                    strict=True,
                ))
                break

        # Auto-mark anything that constructs a real QApplication. Marking by
        # fixture is exact, where the _FILE_MARKERS "qt" entries are only a
        # hint. This matters: a Qt abort is a *native* SIGABRT that kills the
        # session and discards every already-passed result, so the final
        # sweep runs `-m "not qt"` first and Qt in its own pass afterwards.
        needs_qt = "qapp" in item.fixturenames or "qtbot" in item.fixturenames
        if needs_qt:
            item.add_marker(pytest.mark.qt)
            if not _qt_probe_ok:
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
    # DynamicView._poll_model (views/tkinter/view.py:514) calls focus_get()
    # as part of the 12e9d59 focus guard. Returning None is the truthful
    # headless answer ("no widget holds focus") and is what the guard's
    # else-branch expects; without it every Tk view test raises
    # AttributeError inside the poll loop.
    def focus_get(self, *args, **kwargs): return None
    def focus_set(self, *args, **kwargs): pass
    def columnconfigure(self, *args, **kwargs): pass
    def rowconfigure(self, *args, **kwargs): pass
    def grid(self, *args, **kwargs): pass
class DummyTkNotebook(DummyTkWidget):
    """A real class, because `ttk.Notebook` gets **subclassed**.

    `sys.modules['tkinter.ttk']` used to be a bare MagicMock, so
    `class DraggableClosableNotebook(ttk.Notebook)` did not produce a class at
    all: the MagicMock base went through `__mro_entries__` and the result was
    another MagicMock, whose `side_effect` is a finite `tuple_iterator`.

    That made `DraggableClosableNotebook` **callable only a bounded number of
    times per process** — the next construction raised `StopIteration` from
    inside `unittest.mock`, with a traceback pointing at the view. It is the
    real cause of `test_dashboard_window_teardown_ordering` sitting in
    `_ORDER_DEPENDENT`: that test passes alone and fails in composition, not
    because of process-global product state as this file long assumed, but
    because some earlier test had already spent the iterator.

    Tab bookkeeping is modelled for real here, because S6's hide/show is
    exactly a question of whether a hidden tab can be added back.
    """
    def __init__(self, master=None, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self._tabs = []
        self._hidden = set()
        self._selected = None

    def add(self, child, **kwargs):
        if child not in self._tabs:
            self._tabs.append(child)
        self._hidden.discard(child)

    def hide(self, child):
        self._hidden.add(self._resolve(child))

    def forget(self, child):
        child = self._resolve(child)
        if child in self._tabs:
            self._tabs.remove(child)
        self._hidden.discard(child)

    def select(self, child=None):
        if child is None:
            return self._selected
        self._selected = self._resolve(child)

    def index(self, spec):
        return 0

    def insert(self, position, child):
        pass

    def tabs(self):
        return [str(t) for t in self._tabs if t not in self._hidden]

    def _resolve(self, child):
        if isinstance(child, int):
            visible = [t for t in self._tabs if t not in self._hidden]
            return visible[child] if child < len(visible) else child
        return child


ttk_mock = MagicMock()
ttk_mock.Notebook = DummyTkNotebook
ttk_mock.Frame = DummyTkWidget

tkinter_mock.Toplevel = DummyTkWidget
tkinter_mock.Frame = DummyTkWidget
tkinter_mock.Tk = DummyTkWidget
# Both bindings are needed, and only one of them is obvious. `import
# tkinter.ttk` consults sys.modules; `from tkinter import ttk` — which is what
# the views actually write — reads the *attribute* off the tkinter module
# object, and on a MagicMock that auto-creates an unrelated child mock. Setting
# only sys.modules leaves the views holding the auto-created one.
tkinter_mock.ttk = ttk_mock
sys.modules['tkinter'] = tkinter_mock
sys.modules['tkinter.ttk'] = ttk_mock
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
    """Give every test a clean event bus and no leftover subscribers.

    The original reason still holds, restated for S11. `QtErrorPopupManager`
    is a process-wide singleton: once any test called `.initialize()`, it
    rewired `ErrorRouter`'s three global callbacks to real, blocking
    `QMessageBox` popups for the rest of the pytest process — not just its
    own test. Under the offscreen Qt platform nobody clicks the dialog, so
    the next unrelated test that reported anything hung in `QDialog::exec()`
    forever (confirmed by a native stack sample). That is the same hang S10
    finally diagnosed in `tests/ui`.

    S11 replaced the callback slots with subscribers, which removes the
    *silencing* half of the problem but not the leak: a subscriber left
    installed still receives another test's events, and the bus's event
    history and monotonic ids are process-global by design. Resetting per
    test is what keeps `since()` assertions meaningful — without it
    `test_api_logs_and_errors` reads two dozen events it did not publish.
    """
    from error_routing import bus
    bus.clear()
    yield
    bus.clear()
    try:
        from views.pyside.view import QtErrorPopupManager
        QtErrorPopupManager._instance = None
    except ImportError:
        pass
    try:
        from views.tkinter.view import ErrorPopupManager
        ErrorPopupManager._root = None
        ErrorPopupManager._is_polling = False
        ErrorPopupManager._panel = None
        ErrorPopupManager._log = []
    except ImportError:
        pass
    try:
        from views.web.web_view import WebErrorManager
        WebErrorManager._subscribed = False
    except ImportError:
        pass




# ---------------------------------------------------------------------------
# Everything above this line existed **twice** in this file until S11: lines
# 1-543 and 544-1011 were byte-identical, so every fixture, every helper
# class and `pytest_collection_modifyitems` itself were each defined twice
# and only the *second* copy was ever live. Editing the first half — which
# is the half you reach by reading from the top — silently did nothing.
#
# Found by an S11 conftest change that had no effect: a `bus.clear()` added
# to `_reset_global_error_routing` at line 501 never ran, because the
# definition at line 988 shadowed it. The two halves were diffed before the
# duplicate was removed; they differed only by that edit.
# ---------------------------------------------------------------------------


@pytest.fixture
def gate_model():
    """A managed model that records the D-4 input gate and its stop calls."""
    class GateModel:
        def __init__(self):
            self.gate_open = True
            self.power_down_calls = 0
            self.disable_calls = 0

        def set_input_gate(self, is_open):
            self.gate_open = is_open

        def power_down(self):
            self.power_down_calls += 1

        def disable(self):
            self.disable_calls += 1

        def teardown(self):
            self.power_down()

        def emergency_stop(self):
            self.power_down()

    return GateModel()


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

        def emergency_stop(self):
            # register() enforces the ManagedModel contract at the boundary
            # (RC-1), and shutdown_all() stops before it tears down, so this
            # has to be the strongest stop the model has.
            self.power_down()

    return DualShutdownModel()
