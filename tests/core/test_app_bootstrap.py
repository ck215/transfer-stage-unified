import pytest
from unittest.mock import MagicMock, patch
import sys

from app_bootstrap import discover_ports, probe_device_at, build_models, validate_assignment

# --- Tests for discover_ports ---

def test_discover_ports_no_serial():
    with patch('app_bootstrap.discover_ports', __builtins__=__builtins__):
        # We need to simulate ImportError when importing serial.tools.list_ports
        import builtins
        real_import = builtins.__import__
        def mock_import(name, globals=None, locals=None, fromlist=(), level=0):
            if 'serial.tools.list_ports' in name:
                raise ImportError("No module named serial")
            return real_import(name, globals, locals, fromlist, level)

        with patch('builtins.__import__', side_effect=mock_import):
            ports = discover_ports()
            assert ports == ["Headless", "COM1", "COM2", "COM3", "COM4"]

def test_discover_ports_filtering_sorting():
    mock_port_1 = MagicMock()
    mock_port_1.device = "/dev/ttyUSB0"
    mock_port_2 = MagicMock()
    mock_port_2.device = "/dev/ttyS0"
    mock_port_3 = MagicMock()
    mock_port_3.device = "/dev/cu.Bluetooth-Incoming-Port"
    
    with patch("serial.tools.list_ports.comports", return_value=[mock_port_1, mock_port_2, mock_port_3]):
        with patch("sys.platform", "darwin"):
            ports = discover_ports()
            assert ports[0] == "Headless"
            assert "/dev/ttyUSB0" in ports
            assert "/dev/cu.Bluetooth-Incoming-Port" not in ports

def test_discover_ports_linux_ttys_filtering():
    mock_port_1 = MagicMock()
    mock_port_1.device = "/dev/ttyS0"
    mock_port_1.hwid = "n/a"
    mock_port_2 = MagicMock()
    mock_port_2.device = "/dev/ttyS1"
    mock_port_2.hwid = "some_hwid"
    
    with patch("serial.tools.list_ports.comports", return_value=[mock_port_1, mock_port_2]):
        with patch("sys.platform", "linux"):
            ports = discover_ports()
            assert ports[0] == "Headless"
            assert "/dev/ttyS0" not in ports
            assert "/dev/ttyS1" in ports

def test_discover_ports_fallback():
    mock_port_1 = MagicMock()
    mock_port_1.device = "/dev/ttyS0"
    mock_port_1.hwid = "n/a"
    
    with patch("serial.tools.list_ports.comports", return_value=[mock_port_1]):
        with patch("sys.platform", "linux"):
            ports = discover_ports()
            # Everything filtered out, should fallback to all ports
            assert ports[0] == "Headless"
            assert "/dev/ttyS0" in ports


# --- Tests for probe_device_at ---

class MockSerialContext:
    def __init__(self, responses, baudrate=None):
        self.responses = responses
        self.baudrate = baudrate
        self.in_waiting = 0
        self.read_idx = 0
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
        
    def reset_input_buffer(self):
        pass
        
    def reset_output_buffer(self):
        pass
        
    def write(self, data):
        if self.baudrate in self.responses:
            self.in_waiting = len(self.responses[self.baudrate])
            
    def read(self, size):
        data = self.responses[self.baudrate]
        self.in_waiting = 0
        return data
        
    def read_all(self):
        return self.responses.get(self.baudrate, b"")


def test_probe_device_at_500000():
    def mock_serial(port, baudrate, **kwargs):
        return MockSerialContext({500000: b"<s\n"}, baudrate=baudrate)

    with patch('serial.Serial', side_effect=mock_serial):
        device = probe_device_at("COM1")
        assert device == "Stepper Probe"

def test_probe_device_at_115200():
    def mock_serial(port, baudrate, **kwargs):
        # Respond only at 115200
        return MockSerialContext({115200: b"DEV: t"}, baudrate=baudrate)

    with patch('serial.Serial', side_effect=mock_serial):
        device = probe_device_at("COM2")
        assert device == "Temperature Controller"

def test_probe_device_at_57600_smc100():
    def mock_serial(port, baudrate, **kwargs):
        return MockSerialContext({57600: b"1ID1234"}, baudrate=baudrate)

    with patch('serial.Serial', side_effect=mock_serial):
        device = probe_device_at("COM3")
        assert device == "SMC100 Rotator"
        
def test_probe_device_at_no_response():
    def mock_serial(port, baudrate, **kwargs):
        return MockSerialContext({}, baudrate=baudrate)

    with patch('serial.Serial', side_effect=mock_serial):
        device = probe_device_at("COM4")
        assert device is None

# --- Tests for build_models ---

def test_build_models():
    configs = [
        {"device": "Stepper Probe", "port": "SIM", "controller": "None"},
        {"device": "Temperature Controller", "port": "SIM"},
        {"device": "Red Percent Window"}
    ]
    
    # We mock the imports inside build_models so it doesn't fail
    with patch("model.probes.StepperProbe") as MockStepper, \
         patch("model.temperature_system.TemperatureSystem") as MockTemp, \
         patch("model.redpercent_system.RedPercentSystem") as MockRed:
         
        MockStepper.return_value.__class__.__name__ = 'StepperProbe'
        MockTemp.return_value.__class__.__name__ = 'TemperatureSystem'
        MockRed.return_value.__class__.__name__ = 'RedPercentSystem'
        
        models = build_models(configs)
        
        assert "Stepper Probe" in models
        assert "Temperature Controller" in models
        assert "Red Percent Window" in models


# --- Tests for validate_assignment ---

def test_validate_assignment_port_collision():
    configs = [
        {"device": "Stepper Probe", "port": "/dev/ttyUSB0", "controller": "None"},
        {"device": "DC Probe", "port": "/dev/ttyUSB0", "controller": "None"}
    ]
    errors = validate_assignment(configs)
    assert any("Port collision" in e for e in errors)

def test_validate_assignment_controller_collision():
    configs = [
        {"device": "Stepper Probe", "port": "SIM", "controller": "ID 0: Gamepad"},
        {"device": "DC Probe", "port": "SIM", "controller": "ID 0: Gamepad"}
    ]
    errors = validate_assignment(configs)
    assert any("Controller collision" in e for e in errors)

def test_validate_assignment_sim_ports_do_not_collide():
    configs = [
        {"device": "Stepper Probe", "port": "SIM", "controller": "None"},
        {"device": "DC Probe", "port": "SIM", "controller": "None"},
        {"device": "Temperature Controller", "port": "Headless"}
    ]
    errors = validate_assignment(configs)
    assert len(errors) == 0

def test_validate_assignment_red_percent_window_exempt():
    configs = [
        {"device": "Stepper Probe", "port": "/dev/ttyUSB0", "controller": "ID 0: Gamepad"},
        {"device": "Red Percent Window", "port": "/dev/ttyUSB0", "controller": "ID 0: Gamepad"}
    ]
    errors = validate_assignment(configs)
    assert len(errors) == 0

def test_validate_assignment_two_devices_cannot_share_one_controller():
    """Re-authored in S12 (RC-9).

    What this used to assert: that a controller name containing "Virtual" was
    exempt from the collision check. Those names came from one place — the
    web wizard invented two of them whenever its subprocess enumeration found
    nothing — and a device assigned one received no input at all. Nothing
    produces them now, so the exemption did nothing except let two devices
    claim one controller without complaint.

    "None" and "N/A" stay exempt: they mean *no* controller, and any number
    of devices can have no controller.
    """
    errors = validate_assignment([
        {"device": "Stepper Probe", "port": "SIM", "controller": "ID 0: Pad"},
        {"device": "DC Probe", "port": "SIM", "controller": "ID 0: Pad"},
    ])
    assert len(errors) == 1 and "Controller collision" in errors[0]

    errors = validate_assignment([
        {"device": "Stepper Probe", "port": "SIM", "controller": "None"},
        {"device": "DC Probe", "port": "SIM", "controller": "None"},
        {"device": "Chuck Positioner", "port": "SIM", "controller": "N/A"},
    ])
    assert errors == []


# --- Rollback (MANAGER-5) ---

class _RecordingModel:
    """A real ManagedModel; register() enforces the contract at the boundary."""

    def __init__(self, *a, **kw):
        self.torn_down = 0

    def teardown(self):
        self.torn_down += 1

    def emergency_stop(self):
        pass


def test_build_models_tears_down_partial_work_when_a_later_device_fails():
    """A failed launch used to strand every already-built model holding its
    port open, with no reference to it anywhere — only a restart recovered."""
    from model.system_manager import SystemManager

    configs = [
        {"device": "Stepper Probe", "port": "SIM", "controller": "None"},
        {"device": "Temperature Controller", "port": "SIM"},
    ]
    built = _RecordingModel()

    with patch("model.probes.StepperProbe", return_value=built), \
         patch("model.temperature_system.TemperatureSystem",
               side_effect=RuntimeError("port busy")):
        with pytest.raises(RuntimeError):
            build_models(configs)

    assert built.torn_down == 1, "the first model must not be left holding its port"


def test_build_models_registers_as_it_goes_and_releases_on_failure():
    from model.system_manager import SystemManager

    manager = SystemManager()
    configs = [
        {"device": "Stepper Probe", "port": "SIM", "controller": "None"},
        {"device": "Temperature Controller", "port": "SIM"},
    ]
    built = _RecordingModel()

    with patch("model.probes.StepperProbe", return_value=built), \
         patch("model.temperature_system.TemperatureSystem",
               side_effect=RuntimeError("port busy")):
        with pytest.raises(RuntimeError):
            build_models(configs, manager)

    assert manager.get_model("Stepper Probe") is None
    assert built.torn_down == 1


def test_build_models_registers_every_device_on_success():
    from model.system_manager import SystemManager

    manager = SystemManager()
    configs = [
        {"device": "Stepper Probe", "port": "SIM", "controller": "None"},
        {"device": "Red Percent Window"},
    ]
    with patch("model.probes.StepperProbe", return_value=_RecordingModel()), \
         patch("model.redpercent_system.RedPercentSystem", return_value=_RecordingModel()):
        models = build_models(configs, manager)

    assert set(models) == {"Stepper Probe", "Red Percent Window"}
    assert manager.get_model("Stepper Probe") is not None
    assert manager.get_model("Red Percent Window") is not None
    # The config travels with the model, so nothing has to re-derive it later.
    assert manager.get_config("Stepper Probe")["port"] == "SIM"
