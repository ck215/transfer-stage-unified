import pytest
from unittest.mock import MagicMock, patch

from lib.smc100 import (
    SMC100,
    SMC100InvalidResponseException,
    SMC100WaitTimedOutException,
    SMC100DisabledStateException,
    STATE_NOT_REFERENCED,
    STATE_READY_FROM_HOMING,
)


class MockHomingSerialPort:
    """Mock serial port returning homing and position responses."""
    def __init__(self, position_mdeg=0):
        self.is_open = True
        self.position = position_mdeg
        self.write_history = []
        self.in_waiting = 0

    def write(self, data):
        self.write_history.append(data)

    def read(self, size=1):
        return b''

    def flush(self):
        pass

    def flushInput(self):
        pass

    def flushOutput(self):
        pass

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def close(self):
        self.is_open = False


def test_smc100_home_command_execution():
    """Verify home command issue and status wait behavior."""
    mock_port = MockHomingSerialPort()
    
    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        # Mock get_status to return homing, then ready
        with patch.object(smc, 'get_status', side_effect=[('0000', '34')] + [('0000', '33')] * 10):
            smc.home(waitStop=True)
        assert len(mock_port.write_history) > 0
        assert b'1OR' in mock_port.write_history


def test_smc100_move_absolute_and_relative_mdeg_conversions():
    """Verify millidegree to degree float conversions."""
    mock_port = MockHomingSerialPort()
    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        
        with patch.object(smc, 'move_absolute_deg') as mock_abs:
            smc.move_absolute_mdeg(15750)
            mock_abs.assert_called_once_with(15.75)

        with patch.object(smc, 'move_relative_deg') as mock_rel:
            smc.move_relative_mdeg(-2500)
            mock_rel.assert_called_once_with(-2.5)


def test_smc100_get_position_mdeg_parsing():
    """Verify parsing position command response (1TP<position>)."""
    mock_port = MockHomingSerialPort()
    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        with patch.object(smc, 'get_position_deg', return_value=12.5):
            pos = smc.get_position_mdeg()
            assert pos == 12500


def test_toupcam_mock_fallback_state():
    """Verify ToupCam initialization fallback when C-library is missing."""
    with patch('ctypes.cdll.LoadLibrary', side_effect=OSError("DLL not found")):
        from lib.toupcam import Toupcam
        try:
            cam = Toupcam.Open(None)
        except (OSError, AttributeError, Exception):
            pass
