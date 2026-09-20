import sys
import time
from unittest.mock import MagicMock, patch
import pytest
import os
import tempfile


def _wait_until(condition, timeout=2.0, interval=0.01):
    """Poll `condition` (a zero-arg callable) until truthy or `timeout`
    elapses. run_script() executes on a background daemon thread; a fixed
    sleep-then-assert races real thread scheduling under full-suite load
    (confirmed: reliably fails after ~230 other tests, passes in isolation)
    — polling is both faster in the common case and robust under load,
    instead of gambling on a fixed duration being "enough".
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()

# Create mock gcodeparser
mock_gcodeparser = MagicMock()
class MockLine:
    def __init__(self, command, params, gcode_str):
        self.command = command
        self.params = params
        self.gcode_str = gcode_str

class MockGcodeParser:
    def __init__(self, gcode):
        if "malformed" in gcode.lower() or "invalid" in gcode.lower():
            raise ValueError("Malformed gcode")
        
        self.lines = []
        if "Y0" in gcode:
            self.lines.extend([
                MockLine(("G", 0), {"X": 10}, "G0 X10"),
                MockLine(("G", 0), {"X": 20}, "G0 X20"),
                MockLine(("G", 0), {"X": 30}, "G0 X30")
            ])
        elif "G0" in gcode:
            self.lines.append(MockLine(("G", 0), {"X": 10.5, "Y": 20.0, "F": 100}, "G0 X10.5 Y20 F100"))
        elif "M104" in gcode:
            self.lines.append(MockLine(("M", 104), {"S": 200}, "M104 S200"))
        elif "raw," in gcode:
            self.lines.append(MockLine(None, {}, "raw,command,str"))
        else:
            self.lines.append(MockLine(("G", 0), {}, "G0"))

mock_gcodeparser.GcodeParser = MockGcodeParser
sys.modules['gcodeparser'] = mock_gcodeparser

from model.probes import BaseProbe
from error_routing import ErrorRouter

@pytest.fixture
def probe():
    p = BaseProbe(port="COM1", controller_id="None")
    p.serial_comm = MagicMock()
    p.serial_comm.ser = MagicMock()
    return p

def test_run_script_missing_file(probe):
    with patch.object(ErrorRouter, 'report_error') as mock_err:
        probe.run_script("this_file_does_not_exist_at_all.gcode")
        _wait_until(lambda: mock_err.called)
        mock_err.assert_called_once()
        title, message, exc = mock_err.call_args[0]
        assert title == "Script Execution Error"
        assert isinstance(exc, FileNotFoundError)

def test_run_script_malformed_gcode(probe, tmp_path):
    file_path = tmp_path / "malformed.gcode"
    file_path.write_text("malformed gcode content")
    
    with patch.object(ErrorRouter, 'report_error') as mock_err:
        probe.run_script(str(file_path))
        _wait_until(lambda: mock_err.called)
        mock_err.assert_called_once()
        title, message, exc = mock_err.call_args[0]
        assert title == "Script Execution Error"
        assert isinstance(exc, ValueError)

def test_run_script_non_utf8(probe, tmp_path):
    file_path = tmp_path / "non_utf8.gcode"
    # Write invalid UTF-8 bytes
    file_path.write_bytes(b"\x80\x81\x82")
    
    with patch.object(ErrorRouter, 'report_error') as mock_err:
        probe.run_script(str(file_path))
        _wait_until(lambda: mock_err.called)
        mock_err.assert_called_once()
        title, message, exc = mock_err.call_args[0]
        assert title == "Script Execution Error"
        assert isinstance(exc, UnicodeDecodeError)

def test_run_script_no_serial_port_sim(probe, tmp_path):
    # A transport that exists but has no usable link. This used to be
    # expressed as `serial_comm.ser = None`, reaching past the transport to
    # the pyserial handle; the model asks is_open() now (invariant I-2.3).
    probe.serial_comm.is_open.return_value = False
    file_path = tmp_path / "valid.gcode"
    file_path.write_text("G0 X10")
    
    # We expect it to catch the AttributeError and report it
    with patch.object(ErrorRouter, 'report_error') as mock_err:
        probe.run_script(str(file_path))
        _wait_until(lambda: mock_err.called)
        mock_err.assert_called_once()
        title, message, exc = mock_err.call_args[0]
        assert title == "Script Execution Error"
        assert isinstance(exc, AttributeError)

def test_run_script_gcode_execution_path(probe, tmp_path):
    file_path = tmp_path / "valid.gcode"
    file_path.write_text("G0 X10.5 Y20 F100")
    
    probe.serial_comm.send_autonomous_command.reset_mock()
    probe.run_script(str(file_path))
    _wait_until(lambda: probe.serial_comm.send_autonomous_command.call_count >= 2)

    # Called by enter_auton() stop command + actual execution
    assert probe.serial_comm.send_autonomous_command.call_count == 2
    args = probe.serial_comm.send_autonomous_command.call_args_list[-1][0][0]
    assert args["x_dist"] == "10.5"
    assert args["y_dist"] == "20.0"
    assert args["full_speed"] == "100"

def test_run_script_macro_halting(probe, tmp_path):
    file_path = tmp_path / "valid.gcode"
    file_path.write_text("G0 X10.5 Y20 F100\nG0 X0 Y0 F100") # Triggers 3 lines in Mock
    
    probe.serial_comm.send_autonomous_command.reset_mock()
    with patch('model.probes.time.sleep') as mock_sleep:
        probe.run_script(str(file_path))
        import time; time.sleep(0.1)
        
        probe.full_stop()
        time.sleep(0.2)
        
        # enter_auton sends stop (1), then executes line 1 (2), then full_stop sends stop (3)
        # Should be strictly less than the total (1 + 3 + 1 = 5) if it halted properly
        assert probe.serial_comm.send_autonomous_command.call_count < 4

def test_run_script_unrecognized_actions(probe, tmp_path):
    """Non-G-code lines go out through the transport, not around it.

    Re-authored in S8, and the diagnosis the quarantine note asked for is
    this: the test was watching `serial_comm.ser.write`, one of the 11 raw
    transport bypasses S3 deleted. The model writes through
    `write_command()` now, so `.ser.write` is never called and the assertion
    saw silence. The behaviour was correct the whole time; the test was
    watching the wrong object.
    """
    # Test fallback path for comma separated strings
    file_path = tmp_path / "raw.gcode"
    file_path.write_text("raw,command,str")

    probe.serial_comm.write_command.reset_mock()
    probe.run_script(str(file_path))
    _wait_until(lambda: probe.serial_comm.write_command.called)

    probe.serial_comm.write_command.assert_called_once_with("raw,command,str\n")

    # Test M-codes fallback
    probe.serial_comm.write_command.reset_mock()
    file_path2 = tmp_path / "mcode.gcode"
    file_path2.write_text("M104 S200")

    probe.run_script(str(file_path2))
    _wait_until(lambda: probe.serial_comm.write_command.called)

    probe.serial_comm.write_command.assert_called_once_with("M104 S200\n")
