import pytest
import tkinter as tk
import sys
import os

import sys
from unittest.mock import MagicMock
sys.modules['pygame'] = MagicMock()
sys.modules['serial'] = MagicMock()
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()
sys.modules['PIL'] = MagicMock()
sys.modules['gcodeparser'] = MagicMock()
sys.modules['color_test_new'] = MagicMock()
sys.modules['mss'] = MagicMock()
sys.modules['cv2'] = MagicMock()
sys.modules['numpy'] = MagicMock()

# Adjust path to import src modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from stepper_frame import StepperFrame, AppLogic
import serialDrive

class MockSerialArduino:
    def __init__(self, port='SIM', baud_rate=500000):
        self.ser = type('MockSer', (), {'is_open': True})()
        self.SERIAL_PORT = port
        
    def close(self):
        pass
        
    def read_position(self):
        return (0, 0, 0)
        
    def disable(self):
        pass
        
    def enable(self):
        pass
        
    def send_autonomous_command(self, params):
        pass

class MockController:
    def __init__(self):
        self.joystick = None
        self.prev_axis_states = {}
        self.controller_binds = [0, 1, 2, 3, 4, 5]
        
    def stop_polling(self):
        pass
        
    def close(self):
        pass
        
    def get_physical_controllers(self):
        return []
        
    def change_controller(self, controllerID):
        return True

def test_overlapping_serial_polling(monkeypatch):
    """
    Test that triggering 'serial_reconnect_button' multiple times
    does not create overlapping/duplicate _poll_position loops.
    """
    monkeypatch.setattr('stepper_frame.serialDrive.SerialArduino', MockSerialArduino)
    
    root = tk.Tk()
    gui = StepperFrame(root)
    gui.entry_serial_port.delete(0, tk.END)
    gui.entry_serial_port.insert(0, "MOCK_PORT")
    
    mock_serial = MockSerialArduino()
    mock_controller = MockController()
    
    # AppLogic starts _poll_position loop in __init__
    app = AppLogic(root, gui, mock_controller, mock_serial, {}, "TestProcess")
    
    # Wait for pending events so Tkinter registers the after callbacks
    root.update()
    
    initial_after_ids = root.tk.call('after', 'info')
    
    # Trigger serial_reconnect multiple times
    for _ in range(5):
        app.serial_reconnect_button()
        root.update()
        
    final_after_ids = root.tk.call('after', 'info')
    
    # If overlapping loops were created, final_after_ids will be much larger
    # than initial_after_ids (by at least 5)
    assert len(final_after_ids) <= len(initial_after_ids) + 1, (
        f"Concurrency Bug: Expected around {len(initial_after_ids)} polling loops, "
        f"but found {len(final_after_ids)}. Overlapping polling loops created!"
    )
    
    try:
        app._on_closing() # cleanup
    except Exception:
        pass
