"""VIEW-TKINTER-18: Minor Tk behaviors - right/middle-click mapping, log-window handling, stdout spam.

These are three independent issues that need separate investigation and fixes.
"""

import pytest
import sys
from io import StringIO


def test_view_tkinter_18_button_2_still_present():
    """VIEW-TKINTER-18 sub-issue: Button-2 closes tab on right-click on macOS.

    On macOS Aqua:
    - Button-1 = left click
    - Button-2 = right click
    - Button-3 = middle click (if present)

    The current code binds to Button-2, which means right-click closes the tab
    instead of opening a context menu. This needs to be fixed to use Button-3
    on macOS or to use a confirmation dialog.
    """
    with open('src/views/tkinter/view.py', 'r') as f:
        view_source = f.read()

    # Verify Button-2 is still bound (this is what we need to fix)
    assert '<Button-2>' in view_source or '<ButtonPress-2>' in view_source


def test_view_tkinter_18_button_2_binding_location():
    """Verify the Button-2 binding is in the notebook tab close logic."""
    with open('src/views/tkinter/view.py', 'r') as f:
        lines = f.readlines()

    # Find the Button-2 binding
    button2_line = None
    for i, line in enumerate(lines):
        if '<Button-2>' in line or '<ButtonPress-2>' in line:
            button2_line = i
            break

    # It should exist
    assert button2_line is not None, "Button-2 binding not found"

    # It should be near the notebook class or close handler
    context = ''.join(lines[max(0, button2_line - 10):button2_line + 10])
    assert 'notebook' in context.lower() or 'close' in context.lower()


def test_view_tkinter_18_log_window_never_opened():
    """VIEW-TKINTER-18 sub-issue: Log window never opened on Tk.

    The audit documented that the old code opened a ControllerLogWindow via
    the view, and passed `log_updater=print` to the poller, causing stdout spam.
    The fix moved polling to the model, so the view should not be opening any
    log windows anymore.
    """
    with open('src/views/tkinter/view.py', 'r') as f:
        view_source = f.read()

    # ControllerLogWindow class should still exist (for reference)
    assert 'class ControllerLogWindow' in view_source

    # But it should NOT be instantiated in DynamicView or anywhere in the view
    # (except in its own class definition)
    lines = view_source.split('\n')
    instantiation_lines = []
    in_class_def = False
    for i, line in enumerate(lines):
        if 'class ControllerLogWindow' in line:
            in_class_def = True
        elif in_class_def and (line.startswith('class ') or (line and not line[0].isspace())):
            in_class_def = False

        if 'ControllerLogWindow(' in line and not in_class_def:
            instantiation_lines.append((i + 1, line))

    # There should be NO instantiation of ControllerLogWindow outside the class
    assert len(instantiation_lines) == 0, f"ControllerLogWindow instantiated at: {instantiation_lines}"


def test_view_tkinter_18_no_log_updater_print():
    """VIEW-TKINTER-18 sub-issue: stdout spam from `log_updater=print`.

    The polling has been moved to the model (RC-4), so the view should not be
    passing `log_updater=print` to any gamepad poller. This eliminates the
    ~200 Hz stdout spam mentioned in the audit.
    """
    with open('src/views/tkinter/view.py', 'r') as f:
        view_source = f.read()

    # Should NOT see log_updater=print in the view
    # (It should only appear in ControllerLogWindow.on_close where it's resetting)
    lines = view_source.split('\n')
    problematic_lines = []
    for i, line in enumerate(lines, 1):
        if 'log_updater=print' in line:
            # Check if it's not in a comment or in ControllerLogWindow.on_close
            if not line.strip().startswith('#'):
                # Check context - it should only be in ControllerLogWindow reset code
                if 'ControllerLogWindow' not in view_source[max(0, view_source.find(line) - 500):view_source.find(line)]:
                    problematic_lines.append((i, line))

    # There should be no log_updater=print calls in active code
    assert len(problematic_lines) == 0, f"Found log_updater=print in: {problematic_lines}"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
