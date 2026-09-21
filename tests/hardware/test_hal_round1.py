import sys
import pytest
from unittest.mock import MagicMock, patch

from lib.smc100 import (
    SMC100,
    SMC100ReadTimeOutException,
    SMC100WaitTimedOutException,
    SMC100DisabledStateException,
    SMC100RS232CorruptionException,
    SMC100InvalidResponseException,
    STATE_DISABLE_FROM_MOVING,
    STATE_READY_FROM_MOVING,
)

class MockSerialPort:
    def __init__(self, responses=None):
        self.is_open = True
        self.responses = responses or []
        self.write_history = []
        self.in_waiting = 0

    def write(self, data):
        self.write_history.append(data)

    def read(self, size=1):
        if self.responses:
            res = self.responses.pop(0)
            if isinstance(res, Exception):
                raise res
            return res
        return b''

    def flushInput(self):
        pass

    def flushOutput(self):
        pass

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def close(self):
        self.is_open = False

def test_smc100_read_timeout_exception():
    """Verify read timeout handling when serial port returns empty bytes."""
    mock_port = MockSerialPort(responses=[b''])
    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        with pytest.raises(SMC100ReadTimeOutException):
            smc._readline()

def test_smc100_rs232_corruption_handling():
    """Verify RS232 corruption byte exception formatting does not crash with TypeError."""
    mock_port = MockSerialPort(responses=[b'\xff'])
    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        try:
            smc._readline()
        except SMC100RS232CorruptionException as e:
            assert "RS232 corruption detected" in str(e)
        except TypeError as e:
            pytest.fail(f"TypeError during RS232 corruption exception creation: {e}")

def test_smc100_invalid_response_exception():
    """Verify mismatched prefix raises SMC100InvalidResponseException."""
    responses = [b'2', b'T', b'S', b'0', b'0', b'0', b'0', b'3', b'3', b'\r', b'\n']
    mock_port = MockSerialPort(responses=responses)
    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        with pytest.raises(SMC100InvalidResponseException):
            smc.sendcmd('TS', '?', expect_response=True, retry=False)

def test_smc100_retry_boolean_infinite_loop_prevention():
    """Verify retry parameter does not enter infinite loop when retry=True or int."""
    responses = [b'X', b'\r', b'\n'] * 20
    mock_port = MockSerialPort(responses=responses)
    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        with pytest.raises(SMC100InvalidResponseException):
            smc.sendcmd('TS', '?', expect_response=True, retry=3)

def test_smc100_no_retry_commands_override():
    """Verify movement commands like PR/OR override retry=True to False."""
    mock_port = MockSerialPort(responses=[b'X', b'\r', b'\n'])
    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        with pytest.raises(SMC100InvalidResponseException):
            smc.sendcmd('PR', '10', expect_response=True, retry=True)

def test_smc100_disabled_state_detection():
    """Verify state 3D (DISABLE_FROM_MOVING) raises SMC100DisabledStateException."""
    status_resp = [b'1', b'T', b'S', b'0', b'0', b'0', b'0', b'3', b'D', b'\r', b'\n']
    mock_port = MockSerialPort(responses=status_resp)
    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        with pytest.raises(SMC100DisabledStateException):
            smc.wait_states((STATE_READY_FROM_MOVING,))

def test_smc100_wait_states_timeout():
    """Verify wait_states raises SMC100WaitTimedOutException after max timeout.

    **Re-authored for ROTATOR-11.** This used to feed state 28 (MOVING) and
    assert the wait gave up anyway, which pinned the defect: the ceiling
    fired while the stage was still turning, and the exception does not stop
    the stage. The idle ceiling is now about a stage that is *not* making
    progress, so this feeds 0A (not referenced, and not moving).
    """
    status_resp = ([b'1', b'T', b'S', b'0', b'0', b'0', b'0', b'0', b'A', b'\r', b'\n']) * 100
    mock_port = MockSerialPort(responses=status_resp)
    with patch('serial.Serial', return_value=mock_port):
        with patch('lib.smc100.MAX_WAIT_TIME_SEC', 0.1):
            smc = SMC100(1, '/dev/ttyMock', silent=True)
            with pytest.raises(SMC100WaitTimedOutException):
                smc.wait_states((STATE_READY_FROM_MOVING,))


def _moving_port(count=4000):
    """A controller that answers TS with 28 (MOVING) for a long time."""
    return MockSerialPort(
        responses=([b'1', b'T', b'S', b'0', b'0', b'0', b'0', b'2', b'8',
                    b'\r', b'\n']) * count)


def test_smc100_wait_states_does_not_expire_while_the_stage_is_moving():
    """ROTATOR-11: a long move or a homing run must not raise while the
    controller is still reporting motion. The 12 s cap applied to the whole
    wait, so a 15 s move produced "Action failed: Wait timed out" — and the
    exception does not stop the stage, which went on to arrive."""
    import time as _time
    with patch('serial.Serial', return_value=_moving_port()):
        with patch('lib.smc100.MAX_WAIT_TIME_SEC', 0.05), \
             patch('lib.smc100.MAX_MOVING_WAIT_TIME_SEC', 0.8):
            smc = SMC100(1, '/dev/ttyMock', silent=True)
            started = _time.time()
            with pytest.raises(SMC100WaitTimedOutException):
                smc.wait_states((STATE_READY_FROM_MOVING,))
            elapsed = _time.time() - started

    assert elapsed >= 0.5, (
        f"the wait gave up after {elapsed:.2f}s while the stage was still "
        f"reporting MOVING")


def test_smc100_wait_states_still_has_an_absolute_ceiling():
    """Guard for the ROTATOR-11 fix: "wait while it is moving" must not
    become "wait forever" if the controller is stuck reporting 28."""
    import time as _time
    with patch('serial.Serial', return_value=_moving_port()):
        with patch('lib.smc100.MAX_WAIT_TIME_SEC', 0.05), \
             patch('lib.smc100.MAX_MOVING_WAIT_TIME_SEC', 0.3):
            smc = SMC100(1, '/dev/ttyMock', silent=True)
            started = _time.time()
            with pytest.raises(SMC100WaitTimedOutException):
                smc.wait_states((STATE_READY_FROM_MOVING,))
            assert _time.time() - started < 3.0


def test_a_wait_timeout_says_the_stage_was_not_stopped():
    """ROTATOR-11: the timeout is not an "it stopped" signal. Whatever reads
    this message must not conclude the motion ended — nothing in the timeout
    path sends ST."""
    with patch('serial.Serial', return_value=_moving_port()):
        with patch('lib.smc100.MAX_WAIT_TIME_SEC', 0.05), \
             patch('lib.smc100.MAX_MOVING_WAIT_TIME_SEC', 0.2):
            smc = SMC100(1, '/dev/ttyMock', silent=True)
            with pytest.raises(SMC100WaitTimedOutException) as excinfo:
                smc.wait_states((STATE_READY_FROM_MOVING,))

    message = str(excinfo.value)
    assert "not been stopped" in message
    assert "28" in message, "the timeout does not say what state it saw"

def test_smc100_disconnect_during_sendcmd():
    """Verify sudden SerialException during transmission is caught and propagated."""
    mock_port = MockSerialPort(responses=[Exception("Serial connection lost")])
    with patch('serial.Serial', return_value=mock_port):
        smc = SMC100(1, '/dev/ttyMock', silent=True)
        with pytest.raises(Exception):
            smc.sendcmd('TS', '?', expect_response=True)
