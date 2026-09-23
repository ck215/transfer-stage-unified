"""REDPERCENT-20 (model half): dead fields, duplicate thread handles, and
noisy prints removed from `RedPercentSystem`.

The `web_adapter.py` half of this finding belongs to a different agent's
write set and is out of scope here.

- `red_percent`, `is_monitoring`, `stop_event`, `thread`, `monitor_thread`
  and `baseline` were assigned in `__init__` and never read anywhere else
  in the tree (grep-verified against all of `src/` and `tests/`, not just
  the audit's cited line range). Their live equivalents are `current_red`,
  `monitoring`, `_monitor_thread` and `baseline_red`.
- `__del__`'s unconditional `print` and `set_focus_area`'s per-call `print`
  are removed; the many other prints in this file (monitoring start/stop,
  baseline reset, save-to-csv status, etc.) are untouched — they are not
  named by this finding.
- `plot_data_ui` / `set_focus_area_ui` do not exist anywhere in this file
  under those names; the audit's claim that they are no-op stubs here is
  stale (they never made it into this commit, or were already renamed to
  the live `set_focus_area` schema command below). Nothing to leave alone
  because there is nothing there to touch.
"""
import io
import sys

import pytest

from model.redpercent_system import RedPercentSystem


def test_construction_has_no_dead_fields():
    """The six never-read fields must not survive construction."""
    system = RedPercentSystem()
    for name in ("red_percent", "is_monitoring", "stop_event", "thread",
                 "monitor_thread", "baseline"):
        assert not hasattr(system, name), (
            f"RedPercentSystem still assigns dead field '{name}' in __init__")


def test_construction_keeps_the_live_equivalents():
    """The fields that actually back monitoring/baseline behaviour survive."""
    system = RedPercentSystem()
    assert hasattr(system, "monitoring")
    assert hasattr(system, "current_red")
    assert hasattr(system, "baseline_red")
    assert hasattr(system, "_monitor_thread")


def test_del_prints_nothing():
    """`__del__` used to unconditionally print 'Destructor called'. Deleting
    the instance must not write anything to stdout.
    """
    system = RedPercentSystem()
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        del system
    finally:
        sys.stdout = old_stdout
    assert captured.getvalue() == ""


def test_set_focus_area_does_not_print(capsys):
    """`set_focus_area` printed the new focus area on every ROI commit.
    It still has to set `self.focus_area` and return True — only the print
    is gone.
    """
    system = RedPercentSystem()
    result = system.set_focus_area(1, 2, 3, 4)
    assert result is True
    assert system.focus_area == {"top": 2, "left": 1, "width": 3, "height": 4}
    out = capsys.readouterr().out
    assert out == ""


def test_no_plot_data_ui_or_set_focus_area_ui_stub_exists():
    """These names do not exist on the model; the audit's citation of them
    as no-op stubs here is stale. Documented as a passing assertion so a
    future reintroduction of dead stubs under these names is caught.
    """
    system = RedPercentSystem()
    assert not hasattr(system, "plot_data_ui")
    assert not hasattr(system, "set_focus_area_ui")
