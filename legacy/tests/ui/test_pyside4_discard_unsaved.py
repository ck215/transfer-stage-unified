"""PYSIDE-4: Closing the Red Percent dock prompts to save unsaved data.

When a monitoring run has unsaved data and the dock is closed (or the sidebar
item unchecked or the app closed), the operator should be prompted whether to
save or discard before data is lost.

The model seam (D-10) already exists: pending_run_data(), has_unsaved_data
derived from it, and a confirm_discard hook that teardown() consults.
This test verifies the view installs a PySide prompt on that seam.
"""

import pytest
from unittest.mock import patch, MagicMock
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox


def test_pyside4_cleanup_installs_the_discard_hook(qtbot):
    """Rewritten on merge, with the finding re-read against D-1.

    The original asserted the prompt appears *during* `cleanup()`, which it
    achieved by making `cleanup()` call `model.teardown()`. That is a D-1
    violation: `cleanup()` runs on `close_device_view`, the hide path, and
    tearing the model down there destroys the device on a dock close — the
    RC-1 defect S2 and S6 removed.

    Under D-1 a hidden dock keeps its model, so its data is not discarded and
    there is nothing to prompt about yet. What `cleanup()` must do is *install*
    the hook, so that the real teardown — `shutdown_all()` at application exit
    — asks before D-10's autosave decides for the operator.
    """
    from model.redpercent_system import RedPercentSystem, RedPercentDataLog
    from views.pyside.view import RedPercentDynamicView

    model = RedPercentSystem()
    model.data_log = RedPercentDataLog()
    model.data_log.red_values.append(50.0)
    assert model.has_unsaved_data, "test setup: model should have unsaved data"

    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)

    with patch.object(model, "unbind_registry"):
        view.cleanup()

    assert callable(getattr(model, "confirm_discard", None)), (
        "cleanup() did not install the discard hook, so shutdown_all() will "
        "autosave without ever asking the operator")


@pytest.mark.parametrize("answer,expect_discard", [
    (QMessageBox.No, True),     # No = discard  -> hook True  -> skip autosave
    (QMessageBox.Yes, False),   # Yes = save    -> hook False -> autosave runs
])
def test_pyside4_the_hook_reports_the_operators_choice(qtbot, answer, expect_discard):
    """The polarity is the whole safety property.

    `teardown()` autosaves *unless* the hook returns True. An inverted hook
    therefore does not merely annoy — it discards the run the operator asked
    to keep, silently, which is the defect PYSIDE-4 names.
    """
    from model.redpercent_system import RedPercentSystem, RedPercentDataLog
    from views.pyside.view import RedPercentDynamicView

    model = RedPercentSystem()
    model.data_log = RedPercentDataLog()
    model.data_log.red_values.append(50.0)

    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)
    with patch.object(model, "unbind_registry"):
        view.cleanup()

    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=answer):
        assert model.confirm_discard() is expect_discard


def test_pyside4_teardown_consults_the_hook_and_autosaves_on_save(qtbot):
    """End to end on the path that actually runs at application exit."""
    from model.redpercent_system import RedPercentSystem, RedPercentDataLog
    from views.pyside.view import RedPercentDynamicView

    model = RedPercentSystem()
    model.data_log = RedPercentDataLog()
    model.data_log.red_values.append(50.0)

    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)
    with patch.object(model, "unbind_registry"):
        view.cleanup()

    with patch("PySide6.QtWidgets.QMessageBox.question",
               return_value=QMessageBox.Yes), \
         patch.object(model, "autosave_log") as autosave, \
         patch.object(model, "unbind_registry"):
        model.teardown()

    autosave.assert_called(), "the operator chose Save and the run was dropped"


def test_pyside4_cleanup_no_prompt_without_data(qtbot):
    """Cleanup with no unsaved data should not prompt."""
    from model.redpercent_system import RedPercentSystem
    from views.pyside.view import RedPercentDynamicView

    model = RedPercentSystem()
    # No data added
    assert not model.has_unsaved_data, "Test setup: model should have no unsaved data"

    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)

    with patch("PySide6.QtWidgets.QMessageBox.question") as mock_question, \
         patch.object(model, "unbind_registry"):
        view.cleanup()

        # No prompt should be shown
        mock_question.assert_not_called()
