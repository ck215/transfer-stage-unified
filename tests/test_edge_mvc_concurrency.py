import pytest
import time
import threading
from unittest.mock import MagicMock, patch
from controller.seiral import serial

from controller.gamepad import ControllerPoller
from error_routing import ErrorRouter

def test_serial_write_blocks_polling():
    """
    Test that a slow serial write blocking the main thread will cause the
    ControllerPoller's _poll_loop to be delayed or blocked.
    """
    gui_mock = MagicMock()
    poller = ControllerPoller(0, {}, "test_process")
    poller.is_polling = True
    poller.log_updater = None
    poller.gui_root = gui_mock
    poller._is_os_connected = MagicMock(return_value=True)
    poller.gamepad = MagicMock()
    poller.gamepad.joystick.get_numaxes.return_value = 1
    poller.gamepad.joystick.get_axis.return_value = 0.5
    poller.gamepad.prev_axis_states = {0: 0.0}
    poller.gamepad.joystick.get_numbuttons.return_value = 0
    poller.gamepad.joystick.get_numhats.return_value = 0
    
    with patch("controller.gamepad.pygame") as mock_pygame:
        mock_pygame.event.get.return_value = []
        class FakePygameError(Exception): pass
        mock_pygame.error = FakePygameError
        
        def mock_activity():
            time.sleep(0.1) # Simulate blocking serial write
            
        poller.activity_callback = mock_activity
        
        start = time.time()
        poller._poll_loop()
        end = time.time()
        
        assert end - start >= 0.1, f"Polling should have been blocked for at least 0.1s by synchronous serial writes, but took {end - start}s"

def test_reentrant_ui_events_on_axis_change():
    """
    Test that if an activity callback (like a UI update on axis change)
    processes UI events (e.g., QApplication.processEvents() or tk.update()),
    the _poll_loop can be re-entered by the scheduler, causing state corruption
    or duplicate processing.
    """
    gui_mock = MagicMock()
    poller = ControllerPoller(0, {}, "test_process")
    poller.is_polling = True
    poller.gui_root = gui_mock
    poller.log_updater = None
    poller._is_os_connected = MagicMock(return_value=True)
    poller.gamepad = MagicMock()
    poller.gamepad.joystick.get_numaxes.return_value = 1
    # Axis changes from 0.0 to 0.5 to trigger activity_callback
    poller.gamepad.joystick.get_axis.return_value = 0.5
    poller.gamepad.prev_axis_states = {0: 0.0}
    poller.gamepad.joystick.get_numbuttons.return_value = 0
    poller.gamepad.joystick.get_numhats.return_value = 0
    
    poll_loop_entry_count = 0
    max_reentry = 3
    
    def simulate_ui_event_loop_processing():
        nonlocal poll_loop_entry_count
        # Simulate that processing UI events caused another scheduled _poll_loop to fire
        if poll_loop_entry_count < max_reentry:
            poller._poll_loop()
            
    with patch("controller.gamepad.pygame") as mock_pygame:
        class FakePygameError(Exception): pass
        mock_pygame.error = FakePygameError
        
        # We wrap _poll_loop to trace its entry
        original_poll_loop = poller._poll_loop
        def traced_poll_loop(*args, **kwargs):
            nonlocal poll_loop_entry_count
            poll_loop_entry_count += 1
            original_poll_loop(*args, **kwargs)
            
        poller._poll_loop = traced_poll_loop
        poller.activity_callback = simulate_ui_event_loop_processing
        
        # Start the chain
        poller._poll_loop()
        
        assert poll_loop_entry_count == max_reentry, f"Polling loop re-entered {poll_loop_entry_count} times, exposing missing re-entrancy guards."


def test_serial_drive_thread_unsafety():
    """
    Test that the serial controller does not have internal locks,
    which can lead to interleaved writes if called concurrently 
    by gamepad polling and autonomous scripts.
    """
    drive = serial("COM_MOCK")
    drive.ser = MagicMock()
    
    # Simulate a delay in the write operation
    def slow_write(*args, **kwargs):
        time.sleep(0.05)
        
    drive.ser.write.side_effect = slow_write
    drive.ser.is_open = True
    
    # We will write from two threads simultaneously
    def write_manual():
        params = {
            "x_axisStatus": 0, "y_axisStatus": 0, "z_axisStatusL": -1, "z_axisStatusR": -1,
            "x_stepSize": 1, "y_stepSize": 1, "z_stepSize": 1,
            "dpad_LR": 0, "dpad_UD": 0, "LBumper": 0, "RBumper": 0,
            "manual_jog_speed": 100, "packet_format": "<BBffffffffff"
        }
        drive.send_manual_mode_command(params)
        
    def write_auton():
        params = {
            "command_code_auton": 1, "command_code_manual": 0,
            "x_step_size": 1, "y_step_size": 1, "z_step_size": 1,
            "full_speed": 100, "slow_speed": 50, "brake_distance": 10,
            "x_dist": 100, "y_dist": 100, "z_dist": 100
        }
        drive.send_autonomous_command(params)
        
    t1 = threading.Thread(target=write_manual)
    t2 = threading.Thread(target=write_auton)
    
    # If there are no locks, both threads will enter write() concurrently
    # and call ser.write() overlapping. (MagicMock tracks calls concurrently).
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    assert drive.ser.write.call_count == 2
    assert hasattr(drive, '_lock') or hasattr(drive, 'lock'), "Lock should exist in SerialDrive to prevent concurrent write clashing!"
