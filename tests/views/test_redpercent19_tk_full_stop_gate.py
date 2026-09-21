"""REDPERCENT-19 (remainder, part 2): the Tk Red Percent monitor button
follows FULL STOP.

The audit's claim was that Tk's Red Percent monitor button state does not
follow FULL STOP. `DynamicView._sync_gates` (src/views/tkinter/view.py)
computes a mode string via `_mode_name()` -- which, for a model with no
`mode` attribute (RedPercentSystem has none), falls back to
`"monitoring" if self.model.monitoring else "idle"` -- and feeds it to
`schema.is_enabled(element, mode)`.  `system_manager.full_stop_all()`
calls each model's `emergency_stop()`, and `RedPercentSystem.emergency_stop`
is `stop_monitoring()`, which calls `MonitoringRun.mark_stopped()`
synchronously (sets `stop_event`, never blocks).  `RedPercentSystem.
monitoring` is derived live from `run.active`, so if that wiring is intact,
`monitoring` -- and therefore the button gate -- flips the instant
`emergency_stop()` returns, with no polling delay needed for the *model*
side (the Tk view's 50 ms `_poll_model` tick is what carries the flip to
the widget, which is exercised by the existing VIEW-TKINTER-14 tests; this
test pins the model+schema half that gate actually depends on).

This exercises the exact same `schema.is_enabled` call `_sync_gates` makes
(not just `RedPercentSystem.monitoring`, which
`test_emergency_stop_returns_immediately_even_if_the_loop_is_slow` in
tests/core/test_monitoring_run.py already pins) so a regression that kept
`monitoring` correct but broke the schema gate wiring would still be
caught.
"""
from unittest.mock import patch

import pytest

import model.schema as sch
from model.redpercent_system import RedPercentSystem


def _mode_name(model):
    """The same fallback `DynamicView._mode_name` uses for a model with no
    `mode` attribute (src/views/tkinter/view.py)."""
    if getattr(model, "mode", None) is not None:
        mode = getattr(model, "mode")
        return getattr(mode, "value", str(mode))
    if getattr(model, "monitoring", False):
        return "monitoring"
    return "idle"


def _find_button(schema, command):
    for section in schema["sections"]:
        for el in section.get("elements", []):
            if el.get("command") == command:
                return el
    raise AssertionError(f"no {command!r} button in schema")


@pytest.fixture
def ready_system(tmp_path):
    system = RedPercentSystem()
    system.output_root = tmp_path
    system.set_focus_area(0, 0, 10, 10)
    yield system
    system.teardown()


def test_full_stop_flips_the_start_stop_gate(ready_system):
    schema = ready_system.ui_schema
    start_el = _find_button(schema, "start_monitoring")
    stop_el = _find_button(schema, "stop_monitoring")

    # Idle: Start enabled, Stop disabled.
    mode = _mode_name(ready_system)
    assert mode == "idle"
    assert sch.is_enabled(start_el, mode) is True
    assert sch.is_enabled(stop_el, mode) is False

    ready_system.capture_focus_area = lambda sct: object()
    ready_system.detect_red = lambda image: 10.0
    with patch("model.redpercent_system.mss.mss"):
        result = ready_system.start_monitoring()
    assert result.ok

    # Running: Start disabled, Stop enabled.
    mode = _mode_name(ready_system)
    assert mode == "monitoring"
    assert sch.is_enabled(start_el, mode) is False
    assert sch.is_enabled(stop_el, mode) is True

    # FULL STOP -> system_manager.full_stop_all() calls emergency_stop().
    ready_system.emergency_stop()

    assert ready_system.monitoring is False, (
        "emergency_stop() must make `monitoring` False synchronously -- "
        "the Tk gate has nothing else to poll"
    )
    mode = _mode_name(ready_system)
    assert mode == "idle", f"expected idle after FULL STOP, got {mode!r}"
    assert sch.is_enabled(start_el, mode) is True, (
        "Start Monitoring must re-enable after FULL STOP"
    )
    assert sch.is_enabled(stop_el, mode) is False, (
        "Stop Monitoring must disable after FULL STOP -- a live Stop "
        "button after FULL STOP implies a run the operator can still "
        "\"stop\" a second time"
    )

    ready_system._monitor_thread.join(timeout=2.0)
