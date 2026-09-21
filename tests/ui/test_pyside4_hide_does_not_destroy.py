"""D-1 still holds for the Red Percent dock after PYSIDE-4.

PYSIDE-4's fix installs a `confirm_discard` hook so D-10's autosave can ask
the operator first. The wave-4 agent installed the hook correctly and then
called `model.teardown()` from `cleanup()` to make the hook fire — but
`cleanup()` runs on `close_device_view`, which is the **hide** path. D-1 says
closing a view hides the device; the model, its connection and its config
persist. `teardown()` unbinds the registry and ends the run, so that made a
dock close destroy its device again: the RC-1 defect S2 removed (DC-2, DC-9)
and S6 replaced with real hide/show semantics.

Nothing caught it. `close_device_view` is exercised only by
`test_pyside16_layout_intent.py`, and only against "DC Probe" — no test
pinned the invariant for the view that had just been changed.

These tests are the missing pin. They assert on the source rather than on a
live QApplication so they run in the ordinary gate, where a regression of a
closed root cause actually gets noticed.
"""
import ast
import inspect
from pathlib import Path

import pytest

_VIEW = Path(__file__).resolve().parents[2] / "src" / "views" / "pyside" / "view.py"


def _method_source(class_name, method_name):
    tree = ast.parse(_VIEW.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == method_name:
                    return ast.get_source_segment(_VIEW.read_text(), sub)
    pytest.fail(f"{class_name}.{method_name} is gone")


def _calls(src):
    return {n.func.attr for n in ast.walk(ast.parse(src.strip()))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


def test_the_red_percent_dock_cleanup_never_tears_the_model_down():
    """The hide path may stop a run. It may not end the device."""
    src = _method_source("RedPercentDynamicView", "cleanup")
    assert "teardown" not in _calls(src), (
        "RedPercentDynamicView.cleanup() calls teardown(), but cleanup() runs "
        "on close_device_view — the D-1 hide path. That unbinds the registry "
        "and destroys the model on a dock close, which is RC-1 (DC-2, DC-9)")


def test_the_cleanup_path_still_stops_the_run():
    """Hiding stops monitoring; it is only the teardown that is forbidden."""
    src = _method_source("RedPercentDynamicView", "cleanup")
    assert "stop_monitoring" in _calls(src), (
        "hiding the dock no longer stops the run, so a hidden Red Percent "
        "view keeps grabbing the screen with nothing rendering it")


def test_the_discard_hook_is_still_installed_for_shutdown():
    """The hook is the whole point of PYSIDE-4 — it must survive the fix."""
    src = _method_source("RedPercentDynamicView", "cleanup")
    assert "confirm_discard" in src, (
        "the D-10 discard hook is gone, so shutdown_all() autosaves without "
        "ever asking the operator")
