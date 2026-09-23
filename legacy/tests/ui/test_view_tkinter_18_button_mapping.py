"""VIEW-TKINTER-18: where the tab-close binding lives. Deliberately thin.

The audit claims that on macOS Aqua, Button-2 is the *right* button, so a
right-click meant to open a context menu closes the tab instead.

**Three agents have now declined to change this without running it on macOS,
and all three were right.** This file exists to make the binding easy to find
when someone finally has the hardware. It does not pin the current mapping as
correct.

That distinction cost a review. The first version of this file also asserted
that `sys.platform` does *not* appear in the binding code and that Button-3
is *not* bound — i.e. it pinned the defect. Those tests would have gone red
the moment the owner applied the actual fix (platform detection, and a
Button-3 binding for the context menu), so the fix would have arrived with
two failing tests that had to be deleted to proceed. A test that must be
removed before a bug can be fixed is worse than no test.

It also carried a `test_..._confirmation` whose body was `pass`. It asserted
nothing, could never fail, and inflated the count by one. Its docstring is
now this module docstring, which is where prose belongs.

The verification itself is in `docs/implementation/bench-checklist.md`.
"""
import inspect


def test_view_tkinter_18_button_2_binding_exists():
    """Locator, not a verdict: find the binding this finding is about.

    Survives the real fix on purpose. A platform-conditional binding still
    mentions `<ButtonPress-2>` and still routes to `on_middle_press`, so this
    stays green whether or not macOS gets its own branch — it only fails if
    the tab-close binding is renamed or removed out from under the finding.
    """
    from views.tkinter.view import DraggableClosableNotebook

    source = inspect.getsource(DraggableClosableNotebook.__init__)
    assert "<ButtonPress-2>" in source, (
        "the Button-2 tab-close binding VIEW-TKINTER-18 is about has moved "
        "or been renamed; re-locate it before trusting the audit entry")
    assert "on_middle_press" in source, (
        "the Button-2 binding no longer routes to on_middle_press; "
        "VIEW-TKINTER-18's audit text describes a handler that is gone")
