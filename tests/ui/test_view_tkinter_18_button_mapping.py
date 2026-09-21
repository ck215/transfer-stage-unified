"""VIEW-TKINTER-18: Tk button mapping on macOS - confirmation of current code.

Audit claim: On macOS Aqua, Button-2 is the RIGHT button, so a right-click
intended to open a context menu closes the tab instead.

Current code finding: DraggableClosableNotebook binds <ButtonPress-2> to
on_middle_press() which closes the tab. No platform-specific handling exists.

Status: This needs verification on macOS hardware. The code as-is:
1. Binds Button-2 to close tabs (all platforms, no platform detection)
2. Does not bind Button-3 (would normally be right-click context menu)
3. If macOS Aqua has Button-2=right, this would cause right-click to close tabs

The brief states: "Two agents have now declined to change this without executing
it on macOS, and the lead judged both of them right." This finding requires
bench execution to confirm the button mapping on actual macOS hardware.
"""

import ast
import inspect
from pathlib import Path
import pytest


def test_view_tkinter_18_button_2_binding_exists():
    """Verify Button-2 binding is present in the notebook code."""
    from views.tkinter.view import DraggableClosableNotebook

    # Check that the source code has the binding
    source = inspect.getsource(DraggableClosableNotebook.__init__)

    # The binding string should be present
    assert '<ButtonPress-2>' in source, "Button-2 binding not found in source"
    assert 'on_middle_press' in source, "on_middle_press handler not found"


def test_view_tkinter_18_no_platform_detection():
    """Verify there's no platform-specific button binding code."""
    from views.tkinter.view import DraggableClosableNotebook

    source = inspect.getsource(DraggableClosableNotebook.__init__)

    # Check that there's no platform-specific logic for buttons
    assert 'sys.platform' not in source
    assert 'darwin' not in source.lower()
    assert 'aqua' not in source.lower()
    assert 'Button-3' not in source  # No special handling for Button-3

    # This confirms the finding: same binding for all platforms


def test_view_tkinter_18_button_3_not_bound():
    """Note that Button-3 is not bound in the notebook."""
    from views.tkinter.view import DraggableClosableNotebook

    source = inspect.getsource(DraggableClosableNotebook)

    # Verify Button-3 is not used anywhere in the class
    # (it would normally be right-click for context menu)
    assert 'ButtonPress-3' not in source, "Button-3 binding found (unexpected)"
    assert 'Button-3' not in source, "Button-3 reference found"


def test_view_tkinter_18_confirmation():
    """Summary of findings for VIEW-TKINTER-18.

    The code currently binds:
    - Button-1: drag to reorder tabs
    - Button-2: close tab
    - Button-3: (not bound)

    On macOS Aqua, the button-to-physical-action mapping may differ.
    This needs verification on actual macOS hardware to confirm whether
    Button-2 maps to right-click (which would cause right-click to close tabs
    instead of showing context menu).

    The audit says this needs bench execution and both previous agents correctly
    declined to fix it without running on macOS.
    """
    pass  # This is a documentation test
