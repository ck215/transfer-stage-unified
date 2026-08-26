import pytest
import sys
import sys
from unittest.mock import MagicMock
sys.modules["PIL"] = MagicMock()
sys.modules["PIL.ImageTk"] = MagicMock()
sys.modules["PIL.Image"] = MagicMock()

import os
import tkinter as tk
from unittest.mock import patch, MagicMock

# Mock pygame before importing mainGUI to avoid macOS crash
sys.modules['pygame'] = MagicMock()

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import mainGUI

def test_destroy_multiple_times():
    app = mainGUI.SetupWindow()
    app.update()
    
    print("Before first destroy")
    app.destroy()
    print("After first destroy")
    
    # Second destroy
    try:
        print("Before second destroy")
        app.destroy()
        print("After second destroy")
    except Exception as e:
        pytest.fail(f"Second destroy raised an exception: {e}")

def test_multiple_subwindows():
    app1 = mainGUI.SetupWindow()
    app2 = mainGUI.SetupWindow()
    
    app1.update()
    app2.update()
    
    app1.destroy()
    app2.destroy()

def test_launch_modules_closes_properly():
    app = mainGUI.SetupWindow()
    app.update()
    
    # Mock multiprocessing.Process and threading so we don't actually spawn things
    with patch('multiprocessing.Process') as mock_process:
        with patch('threading.Thread') as mock_thread:
            # We must select a device to avoid early return
            app.device_vars["Red Percent Window"].set(True)
            app.launch_modules()
            
            # Since threading.Thread is mocked, the closing_thread isn't run, 
            # let's manually call close()
            app.close()
            
            # Note: launch_modules also calls self.destroy() at the end.
            
    # And we call destroy again, which replicates destroying multiple times
    try:
        app.destroy()
    except Exception as e:
        # We might expect an error here or we want to capture it
        pass

if __name__ == '__main__':
    test_destroy_multiple_times()
