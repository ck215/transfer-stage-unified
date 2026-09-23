"""VIEW-TKINTER-17: View knows model internals and hard-coded names (MVC strain), dead/misleading widgets.

This test verifies that the view no longer knows model internals via hard-coded names and instead
uses the schema-driven approach (VIEW_HINT from the model).
"""

import pytest

from pathlib import Path as _Path
#: Anchored on this file, not on the process CWD. These read `src/`
#: by relative path, which works only while pytest is launched from
#: the repo root — true today, and a confusing failure the first
#: time it is not.
_SRC = _Path(__file__).resolve().parents[2] / "src"


def test_view_tkinter_17_no_hardcoded_device_names():
    """Verify that DashboardWindow does not hard-code device names like 'SMC100 Rotator'."""
    with open(_SRC / "views/tkinter/view.py", 'r') as f:
        view_source = f.read()

    # The old code had hard-coded device name matching:
    # if device_name == "SMC100 Rotator": ...
    # if device_name == "Red Percent Window": ...
    # This should not appear in the current code

    # However, these might appear in comments documenting the old behavior,
    # so we need to be more careful. Let's check if they appear outside comments.

    lines = view_source.split('\n')
    problematic_lines = []
    for i, line in enumerate(lines, 1):
        # Skip comments
        if line.strip().startswith('#'):
            continue
        if 'SMC100 Rotator' in line or 'Red Percent Window' in line:
            # Check if this is in actual code (not a comment or string in a docstring)
            if 'device_name ==' in line or 'if ' in line:
                problematic_lines.append((i, line))

    # We expect no problematic lines with device name checks
    assert len(problematic_lines) == 0, f"Found hard-coded device name checks: {problematic_lines}"


def test_view_tkinter_17_uses_view_hint():
    """Verify that tab routing uses model.VIEW_HINT instead of hard-coded device names."""
    from views.tkinter.view import VIEW_CLASSES, DashboardWindow

    # Verify VIEW_CLASSES dict exists and is used as the model hint resolver
    assert isinstance(VIEW_CLASSES, dict)

    # The router should use VIEW_HINT from the model
    # Read the source to verify this pattern is in place
    with open(_SRC / "views/tkinter/view.py", 'r') as f:
        view_source = f.read()

    # Should see getattr(model, "VIEW_HINT", None) pattern
    assert 'VIEW_HINT' in view_source
    assert 'VIEW_CLASSES' in view_source


def test_view_tkinter_17_no_serial_port_field_checks():
    """Verify that the view doesn't special-case the serial_port field."""
    with open(_SRC / "views/tkinter/view.py", 'r') as f:
        view_source = f.read()

    # The old code had: attr != "serial_port" check to skip serial_port entries
    # This should be gone now that the schema handles it
    assert 'attr != "serial_port"' not in view_source


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
