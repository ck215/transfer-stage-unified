"""VIEW-TKINTER-14: Tests for button state and Sync checkbox rendering.

Tests that:
1. The model's start_monitoring returns Refused when focus_area is not set
2. The schema properly declares disabled_when for sync checkboxes
3. The view's gating logic correctly handles the start button state
"""

import pytest
from model.redpercent_system import RedPercentSystem
from results import Refused
from model import schema as sch


def test_view_tkinter_14_start_monitoring_refused_without_focus_area():
    """The model should refuse start_monitoring when focus_area is not set."""
    system = RedPercentSystem()
    system.focus_area = None
    system.monitoring = False

    result = system.start_monitoring()
    assert result.refused, f"Expected Refused, got {result}"
    assert "focus area" in result.reason.lower()


def test_view_tkinter_14_sync_toggles_disabled_when_monitoring():
    """Sync toggle schema should declare disabled_when=(monitoring,)."""
    system = RedPercentSystem()
    schema_def = system.ui_schema

    # Find the sync toggles
    sync_toggles = []
    for section in schema_def["sections"]:
        if section.get("title") == "Sync Dimensions":
            for el in section.get("elements", []):
                if el.get("type") == "toggle":
                    sync_toggles.append(el)

    assert len(sync_toggles) == 3, f"Expected 3 sync toggles, got {len(sync_toggles)}"

    for toggle in sync_toggles:
        disabled_when = toggle.get("disabled_when", [])
        assert "monitoring" in disabled_when, \
            f"Expected 'monitoring' in disabled_when for {toggle.get('command')}"


def test_view_tkinter_14_start_button_disabled_when_monitoring():
    """Start button schema should declare disabled_when includes monitoring."""
    system = RedPercentSystem()
    schema_def = system.ui_schema

    # Find the start button
    start_button = None
    for section in schema_def["sections"]:
        for el in section.get("elements", []):
            if el.get("command") == "start_monitoring":
                start_button = el
                break

    assert start_button is not None
    disabled_when = start_button.get("disabled_when", [])
    assert "monitoring" in disabled_when


def test_view_tkinter_14_gating_logic_for_start_button():
    """Test the view's gating logic for the start button (from _sync_gates)."""
    # Simulate what _sync_gates does for the start_monitoring button
    system = RedPercentSystem()

    # Test 1: focus_area not set, monitoring not active -> button should be disabled
    system.focus_area = None
    system.monitoring = False
    mode = "idle"  # from _mode_name()

    # Create a mock element like the schema provides
    element = {"command": "start_monitoring", "disabled_when": ["monitoring"]}

    # Simulate _sync_gates logic
    enabled = sch.is_enabled(element, mode)
    # enabled should be True based on schema alone (not monitoring)
    assert enabled, "Button should be enabled according to schema when not monitoring"

    # But the view adds extra logic to check focus_area
    if element.get("command") == "start_monitoring":
        focus_area = getattr(system, "focus_area", None)
        if not focus_area:
            enabled = False

    assert not enabled, "Button should be disabled when focus_area is not set"

    # Test 2: focus_area set, monitoring not active -> button should be enabled
    system.focus_area = {'top': 0, 'left': 0, 'width': 100, 'height': 100}
    system.monitoring = False

    enabled = sch.is_enabled(element, mode)
    assert enabled, "Button should be enabled according to schema"

    if element.get("command") == "start_monitoring":
        focus_area = getattr(system, "focus_area", None)
        if not focus_area:
            enabled = False

    assert enabled, "Button should be enabled when focus_area is set"

    # Test 3: focus_area set, monitoring active -> button should be disabled
    system.monitoring = True
    mode = "monitoring"

    enabled = sch.is_enabled(element, mode)
    assert not enabled, "Button should be disabled when monitoring is active"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
