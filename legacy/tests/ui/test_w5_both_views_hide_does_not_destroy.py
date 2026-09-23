"""D-1 hide/show, pinned for BOTH desktop views. Lead-owned; wave 5 gate.

`test_pyside4_hide_does_not_destroy.py` pins the invariant for exactly one
widget in one view — `RedPercentDynamicView.cleanup`. That was enough while
no write set could reach both frontends at once. Wave 5's Lane 1 deliberately
owns `src/views/tkinter/view.py` *and* `src/views/pyside/view.py`, so a
single agent can now regress both desktop hide paths in one commit. That is
precisely how the D-1 teardown slipped in on 2026-09-20.

So this file pins the whole D-1 contract on both sides, at the source level
rather than against a live toolkit, so it runs in the ordinary fast gate
where a regression is noticed in ~70 s instead of in the qt pass.

The contract, in one line: **closing a device view hides the widget and asks
the manager to hide the device. It never ends the device.** Lifetime belongs
to `SystemManager` (I-1.5); visibility is all a view gets to decide.

This file belongs to the lead. Lane 1 owns the rest of `tests/ui/**` but not
this — if it goes red, the fix is in the view, not here.
"""
import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "views"
_TK = _SRC / "tkinter" / "view.py"
_QT = _SRC / "pyside" / "view.py"

# Ending a device, in any spelling a view might reach for.
_TERMINAL = {"teardown", "release", "shutdown_all", "close_device", "destroy_model"}


def _method_source(path, class_name, method_name):
    text = path.read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == method_name:
                    return ast.get_source_segment(text, sub)
    pytest.fail(f"{path.name}: {class_name}.{method_name} is gone")


def _calls(src):
    """Attribute-call names made in this method's own body."""
    return {n.func.attr for n in ast.walk(ast.parse(src.strip()))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


# --- Tkinter -------------------------------------------------------------

def test_tk_hide_device_never_ends_the_device():
    """The tab goes away, the device does not."""
    src = _method_source(_TK, "DashboardWindow", "hide_device")
    found = _TERMINAL & _calls(src)
    assert not found, (
        f"DashboardWindow.hide_device() calls {sorted(found)}. Hiding a tab "
        "must not end the device: D-1 says the model, its connection and its "
        "config persist. Ending it is the manager's job, via release()/"
        "shutdown_all(), never a view's.")


def test_tk_hide_device_delegates_the_hardware_to_the_manager():
    """The view owns the widget; the manager owns what hiding means to a coil."""
    assert "hide" in _calls(_method_source(_TK, "DashboardWindow", "hide_device")), (
        "DashboardWindow.hide_device() no longer calls system_manager.hide(), "
        "so a hidden device is never brought to a safe stop and de-energized "
        "(D-2). The tab disappears while the axis keeps its coils live.")


def test_tk_show_device_re_adds_the_existing_tab():
    """S6's re-add path. Showing must never build a second view or model."""
    calls = _calls(_method_source(_TK, "DashboardWindow", "show_device"))
    assert "show" in calls, (
        "DashboardWindow.show_device() no longer calls system_manager.show(), "
        "so re-showing a hidden device cannot reattach it to the registry.")
    assert "add" in calls, (
        "DashboardWindow.show_device() no longer re-adds the existing frame. "
        "S6 gave Tk a real re-add path precisely so show/hide would stop "
        "rebuilding widgets against a model that never went away.")


# --- PySide --------------------------------------------------------------

def test_pyside_close_device_view_never_ends_the_device():
    """D-1's other half. This is the exact line that regressed on 2026-09-20."""
    src = _method_source(_QT, "DashboardWindow", "close_device_view")
    found = _TERMINAL & _calls(src)
    assert not found, (
        f"DashboardWindow.close_device_view() calls {sorted(found)}. Closing "
        "a dock is a *hide* (D-1) — it may stop a run, but it may not unbind "
        "the registry or end the device. That is RC-1 returning (DC-2, DC-9).")


def test_pyside_close_device_view_delegates_the_hardware_to_the_manager():
    assert "hide" in _calls(_method_source(_QT, "DashboardWindow", "close_device_view")), (
        "DashboardWindow.close_device_view() no longer calls "
        "system_manager.hide(), so a closed dock leaves the device energized.")


def test_pyside_base_view_cleanup_never_ends_the_device():
    """`cleanup()` runs on the hide path, so it inherits the hide contract.

    The existing pin covers `RedPercentDynamicView.cleanup` only. This covers
    the base every other device view uses — the wider blast radius.
    """
    src = _method_source(_QT, "QtDynamicView", "cleanup")
    found = _TERMINAL & _calls(src)
    assert not found, (
        f"QtDynamicView.cleanup() calls {sorted(found)}. cleanup() is invoked "
        "from close_device_view, which is the hide path — ending the device "
        "there destroys it on a dock close for every device type at once.")


def test_pyside_base_view_cleanup_does_not_close_the_gamepad_poller():
    """Named regression: `ControllerPoller.close()` is terminal and unresettable."""
    src = _method_source(_QT, "QtDynamicView", "cleanup")
    assert "close_controller" not in src and "stop_polling" not in src, (
        "QtDynamicView.cleanup() touches the model's controller again. "
        "ControllerPoller.close() is terminal — `_closed` is never cleared "
        "and start_polling does not reset it — so a single dock close leaves "
        "manual mode unable to arm for the rest of the session.")
