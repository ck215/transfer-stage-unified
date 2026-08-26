import pytest
import serial
from unittest.mock import MagicMock, patch, PropertyMock
import pygame
from src.serialDrive import SerialArduino
from src.controllerDrive import ControllerPoller

class MockJoystick:
    def get_numaxes(self): return 1
    def get_numbuttons(self): return 1
    def get_numhats(self): return 1
    def get_axis(self, i): return 0.0
    def get_button(self, i): return 0
    def get_hat(self, i): return (0, 0)
    def get_name(self): return "Generic Controller"
    def init(self): pass

def test_controller_disconnect_mid_poll():
    active_claims = {"test": "ID 0: Controller"}
    
    with patch('src.controllerDrive.pygame.joystick.Joystick', return_value=MockJoystick()), \
         patch('src.controllerDrive.pygame.init'), \
         patch('src.controllerDrive.pygame.event.get', return_value=[]), \
         patch('src.controllerDrive.pygame.joystick.init'):
        
        # Initialize
        poller = ControllerPoller("ID 0: Controller", active_claims, "test")
        
        # Fake OS connected true to enter poll
        poller._is_os_connected = MagicMock(return_value=True)
        poller.is_polling = True
        poller.joystick = MockJoystick()
        
        # Mock get_axis to raise pygame.error to simulate disconnect mid-poll
        poller.joystick.get_axis = MagicMock(side_effect=pygame.error("Joystick disconnected"))
        
        try:
            poller._poll_loop()
        except Exception as e:
            pytest.fail(f"_poll_loop crashed on joystick error: {e}")
            
        assert poller.joystick is None, "Disconnect was not handled, joystick should be None"

def test_serial_disconnect_read_position():
    with patch('src.serialDrive.serial.Serial') as MockSerial:
        mock_ser = MagicMock()
        MockSerial.return_value = mock_ser
        mock_ser.is_open = True
        
        # Simulate in_waiting throwing an error when device is disconnected
        type(mock_ser).in_waiting = PropertyMock(side_effect=serial.SerialException("Device disconnected"))
        
        arduino = SerialArduino(port='COM3', baud_rate=9600)
        arduino.ser = mock_ser
        
        try:
            res = arduino.read_position()
            assert res is None, "Expected None on error"
        except Exception as e:
            pytest.fail(f"read_position crashed on serial exception: {e}")

def test_serial_disconnect_enable():
    with patch('src.serialDrive.serial.Serial') as MockSerial:
        mock_ser = MagicMock()
        MockSerial.return_value = mock_ser
        mock_ser.is_open = True
        
        # Simulate write throwing an error when device is disconnected
        mock_ser.write.side_effect = serial.SerialException("Device disconnected")
        
        arduino = SerialArduino(port='COM3', baud_rate=9600)
        arduino.ser = mock_ser
        
        # This will crash because enable() doesn't catch exceptions!
        with pytest.raises(serial.SerialException):
            arduino.enable()

def test_serial_disconnect_disable():
    with patch('src.serialDrive.serial.Serial') as MockSerial:
        mock_ser = MagicMock()
        MockSerial.return_value = mock_ser
        mock_ser.is_open = True
        
        # Simulate write throwing an error when device is disconnected
        mock_ser.write.side_effect = serial.SerialException("Device disconnected")
        
        arduino = SerialArduino(port='COM3', baud_rate=9600)
        arduino.ser = mock_ser
        
        # This will crash because disable() doesn't catch exceptions!
        with pytest.raises(serial.SerialException):
            arduino.disable()
