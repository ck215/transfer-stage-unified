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


# ---------------------------------------------------------------------------
# Suite organization: concern markers, slow marking, known-bad quarantine.
# Policy and per-stage gate commands: docs/implementation/testing.md
# ---------------------------------------------------------------------------

# Concern markers per test file. Markers map to the repair stages in
# docs/implementation/plan.md, so each stage can gate on a targeted subset
# instead of the whole suite. Kept here, in one table, rather than as 39
# scattered `pytestmark` lines.
_FILE_MARKERS = {
    "core/test_app_bootstrap.py": ["bootstrap"],
    "core/test_edge_mvc_model.py": ["lifecycle", "mode"],
    "core/test_gamepad_interlock.py": ["loops", "mode"],
    "core/test_integration.py": ["integration"],
    "core/test_model_interactions.py": ["mode"],
    "core/test_model_round1.py": ["mode", "lifecycle"],
    "core/test_model_round2.py": ["mode"],
    "core/test_plot_data.py": ["redpercent"],
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
    "ui/test_edge_mvc_ui.py": ["schema", "qt"],
    "ui/test_ui_schema.py": ["schema"],
    "web/test_web_adapter.py": ["web"],
    "web/test_web_server.py": ["web"],
    "web/test_web_setup.py": ["web", "bootstrap"],
}

# Files whose tests are dominated by real sleeps during model construction
# (serial.py's 1.5 s bootloader wait, probes.py:168's sleep(1)) — measured
# 2026-09-19 at ~4.5 s per test. Excluding them with `-m "not slow"` takes
# the suite from ~3 min to ~20 s, which is what makes per-stage gating
# usable. Re-measure with `--durations=40` if construction cost changes.
# These sleeps are themselves findings (SERIAL-6, RC-4): once construction
# moves off the calling thread in S5, most of this marking can go.
_SLOW_FILES = {
    "core/test_app_bootstrap.py",
    "core/test_model_interactions.py",
    "edge_cases/test_edge_mvc_boundary.py",
    "edge_cases/test_edge_mvc_disconnects.py",
    "edge_cases/test_edge_mvc_state_transitions.py",
    "hardware/test_serial.py",
    "scripting/test_edge_mvc_scripting.py",
}

# Known-bad tests: quarantined, never silently deleted. Each entry says why
# it fails and which stage re-authors it. strict=True on purpose — when the
# stage lands and the test starts passing, pytest reports XPASS as a
# failure, which forces the entry to be removed and the ledger updated. A
# quarantine that goes stale silently is how suites rot.
#
#   nodeid suffix -> (reason, owning stage)
_KNOWN_BAD = {
    # Stale: assert pre-046533f manual-mode semantics. enter_manual() now
    # refuses without a bound gamepad (that fix is why manual mode no longer
    # energizes coils with no pad attached), so manual_flag stays False.
    # RC-3 redefines the mode contract; re-author against ProbeMode then.
    "core/test_model_round1.py::test_base_probe_disarming_interlocks":
        ("asserts manual_flag True after enter_manual with no gamepad; "
         "046533f made that refuse. Re-author against ProbeMode.", "S7"),
    "core/test_model_round1.py::test_stepper_probe_mutual_exclusion_auton_manual":
        ("same stale manual-mode assumption as the disarming-interlocks "
         "test.", "S7"),
    "core/test_model_interactions.py::test_stepper_probe_enter_manual":
        ("same stale manual-mode assumption.", "S7"),
    "edge_cases/test_edge_mvc_state_transitions.py::test_auton_manual_toggle_mutual_exclusivity":
        ("same stale manual-mode assumption; auton_flag stays True because "
         "the manual transition is refused.", "S7"),
    "core/test_edge_mvc_model.py::test_mutating_state_out_of_order":
        ("same stale manual-mode assumption; auton_flag is not cleared "
         "because entering manual is refused without a gamepad.", "S7"),
    # Stale: asserts a system that was deliberately deleted.
    "core/test_view_round1.py::test_pyside_redpercent_sync_and_probe_controls":
        ("asserts view.sync_cbs, the duplicate hand-built QCheckBox row "
         "deleted in 046533f as known-issues #5. The test guards the "
         "redundancy, not the behavior. Re-author against the schema "
         "toggles.", "S10"),
    # Real product bugs. These SHOULD fail; the stage fixes the product.
    "core/test_view_round1.py::test_pyside_dashboard_sidebar_dock_sync":
        ("real bug PYSIDE-7: a dropdown with model_attr and no command "
         "reaches getattr(self.model, None) and raises TypeError "
         "(pyside/view.py:315). Schema v2 makes command required.", "S10"),
    "scripting/test_edge_mvc_scripting.py::test_run_script_gcode_execution_path":
        ("real bug RC-6: params are untyped strings, so '20' is sent where "
         "'20.0' is expected. Typed params fix the formatting.", "S9"),
    "scripting/test_edge_mvc_scripting.py::test_run_script_malformed_gcode":
        ("real gap RC-8: malformed G-code is swallowed instead of reported; "
         "no result channel exists yet to carry the refusal.", "S11"),
    "scripting/test_edge_mvc_scripting.py::test_run_script_unrecognized_actions":
        ("expects a raw .ser.write passthrough — one of the 11 transport "
         "bypasses S3 deletes. It currently writes nothing at all; that "
         "cause is NOT yet diagnosed. Re-author against write_command and "
         "diagnose the silence then.", "S3"),
}


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
    # Passes running tests/core alone; fails in a full-suite composition.
    "core/test_tkinter_teardown.py::test_dashboard_window_teardown_ordering",
    # Wall-clock assertions (>= 0.28 s, ">= 5 reads per poller") that miss
    # when other tests' background threads are competing for the GIL.
    "web/test_web_server.py::test_thread_safety_concurrent_requests",
    "web/test_web_setup.py::test_thread_concurrency_setup_and_telemetry",
    # The run_script family. All four pass alone and per-directory, and fail
    # nondeterministically in a full composition: across four identical
    # sweeps, missing_file and no_serial_port_sim failed once each, then
    # macro_halting and non_utf8 failed once each.
    #
    # Two process-global mechanisms, both of them findings:
    #   - run_script spawns an untracked thread with no run token or
    #     generation (STEPPER-8, RC-5), so a previous test's script thread
    #     can still be executing during the next test's assertions.
    #   - ErrorRouter's callbacks are class-level process state that the
    #     _reset_global_error_routing fixture clears after every test
    #     (RC-8), so whether a report lands depends on what ran before.
    # Expect this set to dissolve when S3/S8 give run_script a generation
    # token and S11 makes the router an instance-scoped bus.
    "scripting/test_edge_mvc_scripting.py::test_run_script_missing_file",
    "scripting/test_edge_mvc_scripting.py::test_run_script_no_serial_port_sim",
    "scripting/test_edge_mvc_scripting.py::test_run_script_macro_halting",
    "scripting/test_edge_mvc_scripting.py::test_run_script_non_utf8",
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
