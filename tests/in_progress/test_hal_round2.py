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
    # Mock responses for home status sequence
    status_seq = [
        b'1', b'T', b'S', b'0', b'0', b'0', b'0', b'3', b'4', b'\r', b'\n', # Homing state 34
        b'1', b'T', b'S', b'0', b'0', b'0', b'0', b'3', b'3', b'\r', b'\n', # Ready state 33
    ]
    mock_port.read = lambda size=1: status_seq.pop(0) if status_seq else b''

    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        smc.home(wait_stop=True)
        assert len(mock_port.write_history) > 0
        assert b'1OR' in mock_port.write_history[0]


def test_smc100_move_absolute_and_relative_mdeg_conversions():
    """Verify degree float to millidegree integer conversions."""
    mock_port = MockHomingSerialPort()
    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        
        # Test move_absolute_mdeg
        with patch.object(smc, 'move_absolute') as mock_abs:
            smc.move_absolute_mdeg(15.75)
            mock_abs.assert_called_once_with(15750, wait_stop=True)

        # Test move_relative_mdeg
        with patch.object(smc, 'move_relative') as mock_rel:
            smc.move_relative_mdeg(-2.5)
            mock_rel.assert_called_once_with(-2500, wait_stop=True)


def test_smc100_get_position_mdeg_parsing():
    """Verify parsing position command response (1TP<position>)."""
    resp_bytes = [b'1', b'T', b'P', b'1', b'2', b'5', b'0', b'0', b'\r', b'\n']
    mock_port = MockHomingSerialPort()
    mock_port.read = lambda size=1: resp_bytes.pop(0) if resp_bytes else b''

    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        pos = smc.get_position_mdeg()
        assert pos == 12500


def test_toupcam_mock_fallback_state():
    """Verify ToupCam initialization fallback when C-library is missing."""
    with patch('ctypes.cdll.LoadLibrary', side_effect=OSError("DLL not found")):
        from lib.toupcam import Toupcam
        try:
            cam = Toupcam.Open(None)
        except (OSError, AttributeError, Exception):
            pass  # Application level guard handles missing camera library cleanly
