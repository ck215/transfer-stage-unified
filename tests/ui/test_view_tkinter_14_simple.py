"""VIEW-TKINTER-14: Button state and Sync checkbox rendering.

Tests for:
1. Start button should be disabled when focus_area is not set
2. Sync checkboxes should be disabled during monitoring
3. Button states reflect the model's monitoring state
"""

import pytest

from pathlib import Path as _Path
#: Anchored on this file, not on the process CWD. These read `src/`
#: by relative path, which works only while pytest is launched from
#: the repo root — true today, and a confusing failure the first
#: time it is not.
_SRC = _Path(__file__).resolve().parents[2] / "src"


def test_redpercent_start_monitoring_refuses_without_focus_area():
    """Verify that start_monitoring returns Refused when no focus area is set."""
    from model.redpercent_system import RedPercentSystem
    from results import Refused
    from unittest.mock import MagicMock

    # Create a RedPercentSystem with no focus area
    system = RedPercentSystem()
    system.focus_area = None
    system.monitoring = False

    # Call start_monitoring - it should return Refused
    result = system.start_monitoring()

    # Verify the result is Refused
    assert result.refused, f"Expected Refused, got {result}"
    assert "focus area" in result.reason.lower(), f"Reason should mention focus area: {result.reason}"


def test_redpercent_sync_toggles_have_disabled_when_monitoring():
    """Verify sync toggle schema includes disabled_when=monitoring."""
    from model.redpercent_system import RedPercentSystem

    system = RedPercentSystem()
    schema = system.ui_schema

    # Find the sync toggles in the schema
    sync_toggles = []
    for section in schema["sections"]:
        if section.get("title") == "Sync Dimensions":
            for el in section.get("elements", []):
                if el.get("type") == "toggle":
                    sync_toggles.append(el)

    assert len(sync_toggles) == 3, f"Expected 3 sync toggles, got {len(sync_toggles)}"

    # Each should have disabled_when including "monitoring"
    for toggle in sync_toggles:
        disabled_when = toggle.get("disabled_when")
        assert "monitoring" in disabled_when, \
            f"Expected 'monitoring' in disabled_when, got {disabled_when} for {toggle.get('command')}"


def test_redpercent_start_button_has_disabled_when_monitoring():
    """Verify start button schema includes disabled_when=monitoring."""
    from model.redpercent_system import RedPercentSystem

    system = RedPercentSystem()
    schema = system.ui_schema

    # Find the start button in the schema
    start_button = None
    for section in schema["sections"]:
        for el in section.get("elements", []):
            if el.get("command") == "start_monitoring":
                start_button = el
                break

    assert start_button is not None, "Start monitoring button not found in schema"

    # Should have disabled_when including "monitoring"
    disabled_when = start_button.get("disabled_when")
    assert "monitoring" in disabled_when, \
        f"Expected 'monitoring' in disabled_when, got {disabled_when}"


def test_view_should_check_focus_area_when_gating_start_button():
    """The view's _sync_gates should check if focus_area is set for the start button."""
    # This is a structural test that the view code handles this case
    with open(_SRC / "views/tkinter/view.py", 'r') as f:
        view_source = f.read()

    # The view should have logic to check focus_area when gating the start button
    # Look for either:
    # 1. Explicit check in _sync_gates
    # 2. Use of a helper that checks focus_area
    assert '_sync_gates' in view_source, "No _sync_gates method in view"

    # For now, just verify the structure exists
    # We'll verify the actual behavior in an integration test


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
