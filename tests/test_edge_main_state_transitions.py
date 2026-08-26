# Mock external dependencies
import sys
from unittest.mock import MagicMock

sys.modules['pygame'] = MagicMock()
sys.modules['serial'] = MagicMock()
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()
sys.modules['gcodeparser'] = MagicMock()
sys.modules['PIL'] = MagicMock()
sys.modules['cv2'] = MagicMock() # Just in case
sys.modules['mss'] = MagicMock()
sys.modules['numpy'] = MagicMock()

import os
import unittest

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import stepper_frame
import chuck_frame
import DC_frame

class TestStateTransitions(unittest.TestCase):
    def setUp(self):
        self.root_mock = MagicMock()
        self.gui_mock = MagicMock()
        self.controller_mock = MagicMock()
        self.serial_mock = MagicMock()
        self.active_claims = {}
        
        # Setup controller mock
        self.controller_mock.joystick = MagicMock()
        self.controller_mock.get_hat_edge.return_value = (0, 0)
        self.controller_mock.prev_axis_states = {}
        self.controller_mock.controller_binds = [0, 1, 2, 3, 4, 5]
        self.controller_mock.get_button_edge.return_value = 0
        
        # Setup GUI mock
        self.gui_mock.entry_x_step.get.return_value = "1"
        self.gui_mock.entry_y_step.get.return_value = "1"
        self.gui_mock.entry_z_step.get.return_value = "1"
        self.gui_mock.entry_man_full_speed.get.return_value = "100"
        
        # for chuck frame
        self.gui_mock.entry_rot_step.get.return_value = "1"
        
        # for DC frame
        self.gui_mock.entry_duration.get.return_value = "1"
        self.gui_mock.entry_voltage.get.return_value = "1"
        
    def test_rapid_toggling_stepper_frame(self):
        logic = stepper_frame.AppLogic(self.root_mock, self.gui_mock, self.controller_mock, self.serial_mock, self.active_claims, "Test")
        logic.system_enabled = True 
        
        # Enter manual mode
        logic.enter_manual_mode_button()
        
        # Rapid toggle
        logic.enter_autonomous_mode_button()
        logic.enter_manual_mode_button()
        
        # Collect all scheduled loops
        calls = self.root_mock.after.call_args_list
        self.root_mock.after.reset_mock()
        
        manual_loops = [call for call in calls if call[0][1] == logic._manual_mode_loop]
        
        for call in manual_loops:
            callback = call[0][1]
            callback()
            
        new_calls = self.root_mock.after.call_args_list
        new_manual_loops = [call for call in new_calls if call[0][1] == logic._manual_mode_loop]
        
        # Due to the bug, both pending loops continue because manualFlag is True
        self.assertEqual(len(new_manual_loops), 2, "Multiple manual loops run concurrently!")

    def test_rapid_toggling_chuck_frame(self):
        logic = chuck_frame.AppLogic(self.root_mock, self.gui_mock, self.controller_mock, self.serial_mock, self.active_claims, "Test")
        logic.system_enabled = True 
        
        logic.enter_manual_mode_button()
        logic.enter_autonomous_mode_button()
        logic.enter_manual_mode_button()
        
        calls = self.root_mock.after.call_args_list
        self.root_mock.after.reset_mock()
        manual_loops = [call for call in calls if call[0][1] == logic._manual_mode_loop]
        for call in manual_loops:
            call[0][1]()
            
        new_calls = self.root_mock.after.call_args_list
        new_manual_loops = [call for call in new_calls if call[0][1] == logic._manual_mode_loop]
        self.assertEqual(len(new_manual_loops), 2)

    def test_rapid_toggling_DC_frame(self):
        logic = DC_frame.AppLogic(self.root_mock, self.gui_mock, self.controller_mock, self.serial_mock, self.active_claims, "Test")
        logic.system_enabled = True 
        
        logic.enter_manual_mode_button()
        logic.enter_autonomous_mode_button()
        logic.enter_manual_mode_button()
        
        calls = self.root_mock.after.call_args_list
        self.root_mock.after.reset_mock()
        manual_loops = [call for call in calls if call[0][1] == logic._manual_mode_loop]
        for call in manual_loops:
            call[0][1]()
            
        new_calls = self.root_mock.after.call_args_list
        new_manual_loops = [call for call in new_calls if call[0][1] == logic._manual_mode_loop]
        self.assertEqual(len(new_manual_loops), 2)

if __name__ == '__main__':
    unittest.main()
