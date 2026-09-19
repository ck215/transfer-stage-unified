import pytest
from unittest.mock import MagicMock
from views.tkinter.view import DashboardWindow
from model.system_manager import SystemManager

def test_tkinter_full_stop_binding():
    """Verify that clicking the FULL STOP label calls system_manager.full_stop_all()."""
    import tkinter as tk
    
    mgr = SystemManager()
    mgr.full_stop_all = MagicMock()
    
    # We mock tk.Label specifically so we can grab the bind callback
    with pytest.MonkeyPatch.context() as m:
        mock_label = MagicMock()
        m.setattr(tk, 'Label', MagicMock(return_value=mock_label))
        
        dash = DashboardWindow(MagicMock(), mgr)
        
        # Verify the bind was called with "<Button-1>"
        mock_label.bind.assert_any_call("<Button-1>", mock_label.bind.call_args[0][1])
        
        # Extract the lambda and call it
        bind_args = [args for args, kwargs in mock_label.bind.call_args_list if args[0] == "<Button-1>"]
        assert len(bind_args) > 0
        click_callback = bind_args[0][1]
        
        # Simulate the event
        click_callback(MagicMock())
        
        # Verify full_stop_all was called
        mgr.full_stop_all.assert_called_once()
