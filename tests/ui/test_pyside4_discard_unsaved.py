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


def test_pyside4_cleanup_prompts_unsaved_data_save(qtbot):
    """Closing a Red Percent view with unsaved data prompts the user.

    When user chooses 'Yes' (Save), autosave should be called.
    """
    from model.redpercent_system import RedPercentSystem, RedPercentDataLog
    from views.pyside.view import RedPercentDynamicView

    model = RedPercentSystem()
    # Manually add one sample to make has_unsaved_data True
    model.data_log = RedPercentDataLog()
    model.data_log.red_values.append(50.0)

    assert model.has_unsaved_data, "Test setup: model should have unsaved data"

    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)

    # Mock QMessageBox.question and autosave to verify the flow
    with patch("PySide6.QtWidgets.QMessageBox.question") as mock_question, \
         patch.object(model, "autosave_log") as mock_autosave, \
         patch.object(model, "unbind_registry"):
        mock_question.return_value = QMessageBox.Yes  # User chose Save

        # Call cleanup - should prompt and set up the hook
        view.cleanup()

        # The prompt should have been shown
        mock_question.assert_called_once()
        # Autosave should be called because the hook returned False (save)
        mock_autosave.assert_called()


def test_pyside4_cleanup_discard_no_autosave(qtbot):
    """If user chooses 'No' (Discard), cleanup should not autosave."""
    from model.redpercent_system import RedPercentSystem, RedPercentDataLog
    from views.pyside.view import RedPercentDynamicView

    model = RedPercentSystem()
    model.data_log = RedPercentDataLog()
    model.data_log.red_values.append(50.0)

    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)

    with patch("PySide6.QtWidgets.QMessageBox.question") as mock_question, \
         patch.object(model, "autosave_log") as mock_autosave, \
         patch.object(model, "unbind_registry"):
        mock_question.return_value = QMessageBox.No  # User chose Discard

        view.cleanup()

        # Prompt should have been shown
        mock_question.assert_called_once()
        # Autosave should NOT be called because the hook returned True
        mock_autosave.assert_not_called()


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
