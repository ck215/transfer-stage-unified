import pytest
import sys
from unittest.mock import patch, MagicMock

sys.path.append("src")
from view import ErrorPopupManager

def test_error_popup_manager_none_types():
    with patch("view.messagebox") as mock_mb:
        # Test None for message with an exception
        # This will likely crash because None += str
        error_data = {
            'title': None,
            'message': None,
            'exception': Exception("Test"),
            'type': 'error'
        }
        ErrorPopupManager._root = MagicMock()
        ErrorPopupManager._display_popup(error_data)


def test_error_popup_manager_corrupted_exception():
    class CorruptedStrException(Exception):
        def __str__(self):
            raise RuntimeError("Corrupted stack trace or string representation")
    
    with patch("view.messagebox") as mock_mb:
        error_data = {
            'title': "Title",
            'message': "Message",
            'exception': CorruptedStrException("Bad"),
            'type': 'error'
        }
        ErrorPopupManager._root = MagicMock()
        ErrorPopupManager._display_popup(error_data)


def test_error_popup_manager_large_strings():
    with patch("view.messagebox") as mock_mb:
        large_string = "A" * (10**7)
        error_data = {
            'title': large_string,
            'message': large_string,
            'exception': Exception(large_string),
            'type': 'error'
        }
        ErrorPopupManager._root = MagicMock()
        ErrorPopupManager._display_popup(error_data)
        
        # Verify it was called (might not be if it crashed)
        assert mock_mb.showerror.called

def test_setup_excepthook_corrupted_traceback():
    ErrorPopupManager.setup_excepthook()
    # Trigger sys.excepthook with None types and strings
    with patch("view.ErrorPopupManager.report_error") as mock_report:
        # Corrupted stack trace, exc_type is a string, exc_value is None, traceback is string
        # This tests if traceback.print_exception crashes
        try:
            sys.excepthook(str, None, "not a real traceback")
        except Exception as e:
            pytest.fail(f"excepthook crashed with corrupted traceback: {e}")
            
        mock_report.assert_called_once()

def test_error_router_unhashable_message():
    from error_routing import ErrorRouter
    # Should not crash if message is unhashable
    try:
        ErrorRouter.report_error("Title", {"unhashable": "dict"})
    except TypeError as e:
        pytest.fail(f"ErrorRouter crashed on unhashable message: {e}")
