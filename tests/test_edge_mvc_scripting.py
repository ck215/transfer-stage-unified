import sys
from unittest.mock import MagicMock, patch
import pytest
import os
import tempfile

# Create mock gcodeparser
mock_gcodeparser = MagicMock()
class MockGcodeParser:
    def __init__(self, gcode):
        if "malformed" in gcode.lower() or "invalid" in gcode.lower():
            raise ValueError("Malformed gcode")
        # For our tests, we don't need real lines
        self.lines = []
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
        import time; time.sleep(0.5)
        mock_err.assert_called_once()
        title, message, exc = mock_err.call_args[0]
        assert title == "Script Execution Error"
        assert isinstance(exc, FileNotFoundError)

def test_run_script_malformed_gcode(probe, tmp_path):
    file_path = tmp_path / "malformed.gcode"
    file_path.write_text("malformed gcode content")
    
    with patch.object(ErrorRouter, 'report_error') as mock_err:
        probe.run_script(str(file_path))
        import time; time.sleep(0.5)
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
        import time; time.sleep(0.5)
        mock_err.assert_called_once()
        title, message, exc = mock_err.call_args[0]
        assert title == "Script Execution Error"
        assert isinstance(exc, UnicodeDecodeError)

def test_run_script_no_serial_port_sim(probe, tmp_path):
    # In SIM mode, serial_comm might exist but serial_comm.ser is None
    probe.serial_comm.ser = None
    file_path = tmp_path / "valid.gcode"
    file_path.write_text("G0 X10")
    
    # We expect it to catch the AttributeError and report it
    with patch.object(ErrorRouter, 'report_error') as mock_err:
        probe.run_script(str(file_path))
        import time; time.sleep(0.5)
        mock_err.assert_called_once()
        title, message, exc = mock_err.call_args[0]
        assert title == "Script Execution Error"
        assert isinstance(exc, AttributeError)
