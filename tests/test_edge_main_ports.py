import pytest
import sys
import os
from unittest.mock import MagicMock, patch
import builtins

# Mock serial module before importing src modules
mock_serial = MagicMock()
mock_serial_tools = MagicMock()
mock_list_ports = MagicMock()
mock_serial_tools.list_ports = mock_list_ports
mock_serial.tools = mock_serial_tools

sys.modules['serial'] = mock_serial
sys.modules['serial.tools'] = mock_serial_tools
sys.modules['serial.tools.list_ports'] = mock_list_ports
sys.modules['pygame'] = MagicMock()
sys.modules['gcodeparser'] = MagicMock()
sys.modules['color_test_new'] = MagicMock()

# Add src to sys.path to import modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from stepper_frame import get_arduino_port

class MockPort:
    def __init__(self, device, description="", hwid="", vid=None, pid=None):
        self.device = device
        self.description = description
        self.hwid = hwid
        self.vid = vid
        self.pid = pid

def test_missing_ports(monkeypatch):
    """Test when no ports are found."""
    mock_list_ports.comports.return_value = []
    
    # Mock input to return a manual port since fallback calls input()
    monkeypatch.setattr('builtins.input', lambda prompt: 'COM99')
    
    port = get_arduino_port()
    assert port == 'COM99'

def test_malformed_ports(monkeypatch):
    """Test with deeply malformed port objects (missing attributes or None)."""
    class MissingAttrsPort:
        def __init__(self, device):
            self.device = device
            # No description, hwid, vid, pid attributes at all

    class MissingVidPidPort:
        def __init__(self, device):
            self.device = device
            self.description = "Some device"
            self.hwid = "Some HWID"
            # No vid, pid attributes

    malformed_port1 = MockPort(device="COM1", description=None, hwid=None, vid=None, pid=None)
    malformed_port3 = MissingVidPidPort(device="COM3")
    # A valid port at the end
    valid_port = MockPort(device="COM4", description="Arduino Mega", vid=0x2341, pid=0x0042)
    
    mock_list_ports.comports.return_value = [malformed_port1, malformed_port3, valid_port]
    
    # Should catch AttributeError if it tries to access missing attributes, or skip gracefully.
    try:
        port = get_arduino_port()
    except AttributeError as e:
        pytest.fail(f"Implementation error missing vid: {e}")
        
    assert port == 'COM4'

def test_malformed_ports_missing_desc():
    class MissingAttrsPort:
        def __init__(self, device):
            self.device = device
            # No description, hwid, vid, pid attributes at all
            
    mock_list_ports.comports.return_value = [MissingAttrsPort(device="COM2")]
    
    try:
        get_arduino_port()
    except AttributeError as e:
        pytest.fail(f"Implementation error missing description: {e}")

def test_duplicated_ports():
    """Test when multiple valid ports are available, should pick the first one."""
    port1 = MockPort(device="COM4", description="CH340", vid=0x1A86, pid=0x7523)
    port2 = MockPort(device="COM5", description="Arduino Mega", vid=0x2341, pid=0x0042)
    
    mock_list_ports.comports.return_value = [port1, port2]
    
    port = get_arduino_port()
    assert port == 'COM4'

def test_fallback_hwid_string_matching(monkeypatch):
    """Test string matching fallback logic."""
    # Description has mega
    port1 = MockPort(device="COM6", description="some mega clone")
    mock_list_ports.comports.return_value = [port1]
    
    port = get_arduino_port()
    assert port == 'COM6'

def test_manual_input_fallback(monkeypatch):
    """Test that if ports exist but none match, it falls back to input()."""
    port1 = MockPort(device="COM1", description="Bluetooth", vid=0x1234, pid=0x5678)
    mock_list_ports.comports.return_value = [port1]
    
    monkeypatch.setattr('builtins.input', lambda prompt: '/dev/ttyUSB0')
    port = get_arduino_port()
    assert port == '/dev/ttyUSB0'
