
import pytest
import math
import time
import copy
from unittest.mock import MagicMock, patch, PropertyMock, mock_open
import threading

# Mock the external dependencies before importing the module under test
@pytest.fixture(autouse=True)
def mock_dependencies():
    """
    Mocks serial, ErrorRouter, ControllerPoller, and gcodeparser to isolate the unit tests.
    """
    with patch('model.probes.serial') as mock_serial_class, \
         patch('model.probes.ErrorPopupManager') as mock_error_router, \
         patch('controller.gamepad.ControllerPoller') as mock_poller_class, \
         patch('gcodeparser.GcodeParser') as mock_gcode_parser, \
         patch('gcodeparser.parse_gcode_lines', return_value=[]) as mock_parse_lines:
        
        # Configure Serial Mock
        mock_serial_instance = MagicMock()
        mock_serial_instance.ser = MagicMock()
        mock_serial_class.return_value = mock_serial_instance
        
        # Configure Poller Mock
        mock_poller_instance = MagicMock()
        mock_poller_instance.get_mapped_state.return_value = {}
        mock_poller_instance.get_physical_controllers.return_value = ["Controller1"]
        mock_poller_class.return_value = mock_poller_instance
        
        yield {
            'serial_class': mock_serial_class,
            'serial_instance': mock_serial_instance,
            'error_router': mock_error_router,
            'poller_class': mock_poller_class,
            'poller_instance': mock_poller_instance,
            'gcode_parser': mock_gcode_parser,
            'parse_lines': mock_parse_lines
        }


# Import the module under test after mocking
from model.probes import BaseProbe, StepperProbe, DCProbe, ChuckPositioner, _num


class TestNumHelper:
    def test_valid_float(self):
        assert _num("10.5", 0) == 10.5

    def test_valid_integer(self):
        assert _num("10", 0, integer=True) == 10

    def test_invalid_value_fallback(self):
        assert _num("abc", 5) == 5.0

    def test_nan_fallback(self):
        assert _num(float('nan'), 5) == 5.0

    def test_inf_fallback(self):
        assert _num(float('inf'), 5) == 5.0

    def test_minimum_clamp(self):
        assert _num("0", 10, minimum=5) == 5.0

    def test_minimum_no_clamp(self):
        assert _num("10", 5, minimum=5) == 10.0


class TestBaseProbeInit:
    def test_init_with_valid_port(self, mock_dependencies):
        probe = BaseProbe("COM1", "CTRL1")
        assert probe.serial_port == "COM1"
        assert probe.controller_var == "CTRL1"
        assert probe.serial_comm is not None
        mock_dependencies['serial_class'].assert_called_once_with("COM1")

    def test_init_with_none_port(self, mock_dependencies):
        probe = BaseProbe("None", "CTRL1")
        assert probe.serial_comm is None
        mock_dependencies['serial_class'].assert_not_called()

    def test_init_with_null_port(self, mock_dependencies):
        probe = BaseProbe(None, "CTRL1")
        assert probe.serial_comm is None
        mock_dependencies['serial_class'].assert_not_called()

    def test_init_poller_creation(self, mock_dependencies):
        probe = BaseProbe("COM1", "CTRL1")
        mock_dependencies['poller_class'].assert_called_once_with("CTRL1", {}, "BaseProbe")

    def test_init_poller_failure(self, mock_dependencies):
        mock_dependencies['poller_class'].side_effect = Exception("No gamepad")
        probe = BaseProbe("COM1", "CTRL1")
        assert probe.poller is None

    def test_default_values(self, mock_dependencies):
        probe = BaseProbe("COM1", "CTRL1")
        assert probe.pos_x == "0"
        assert probe.x_step == "16"
        assert probe.full_speed == "400"
        assert probe.system_enabled is False
        assert probe.auton_flag is False
        assert probe.manual_flag is False


