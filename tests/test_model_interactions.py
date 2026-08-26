import pytest
from unittest.mock import MagicMock, patch
from model.probes import StepperProbe, DCProbe

def get_mock_serial():
    mock_instance = MagicMock()
    mock_instance.is_open = True
    mock_instance.in_waiting = 0
    return mock_instance

def test_stepper_probe_enter_auton():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")
        probe.enter_auton()
        assert probe.auton_flag is True, "auton_flag should be True after enter_auton"
        assert probe.manual_flag is False

def test_stepper_probe_enter_manual():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")
        probe.enter_manual()
        assert probe.manual_flag is True, "manual_flag should be True after enter_manual"
        assert probe.auton_flag is False

def test_macro_start_auton_command_flags():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")
        probe.serial_comm = MagicMock()
        probe.macro_start_auton()
        probe.serial_comm.send_autonomous_command.assert_called_once()
        args, kwargs = probe.serial_comm.send_autonomous_command.call_args
        params = args[0]
        assert params["command_code_auton"] == 1, "Command code auton should be 1"

def test_send_manual_mode_command():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = DCProbe("COM1", "Virtual Controller A")
        probe.serial_comm = MagicMock()
        controller_params = {
            "x_axisStatus": 0.5,
            "y_axisStatus": -0.5,
            "z_axisStatusL": 1.0,
            "z_axisStatusR": -1.0,
            "dpad_LR": 1,
            "dpad_UD": -1,
            "LBumper": 1,
            "RBumper": 0
        }
        probe.send_manual_mode_command(controller_params)
        probe.serial_comm.send_manual_mode_command.assert_called_once()
        args, kwargs = probe.serial_comm.send_manual_mode_command.call_args
        sent_params = args[0]
        assert sent_params["x_axisStatus"] == 0.5
        assert sent_params["manual_jog_speed"] == "120"

def test_temperature_system_baud_rate():
    with patch("model.temperature_system.serial") as mock_serial_cls:
        from model.temperature_system import TemperatureSystem
        mock_instance = MagicMock()
        mock_instance.ser = None
        mock_serial_cls.return_value = mock_instance
        
        ts = TemperatureSystem("COM5")
        mock_serial_cls.assert_called_once_with("COM5", baud_rate=115200)

def test_rotator_system_auto_connect():
    from model.rotator_system import RotatorSystem
    with patch.object(RotatorSystem, "connect") as mock_connect:
        rot = RotatorSystem("COM3")
        mock_connect.assert_called_once_with("COM3", 1)

    with patch.object(RotatorSystem, "connect") as mock_connect:
        rot_sim = RotatorSystem("SIM")
        mock_connect.assert_not_called()

    with patch.object(RotatorSystem, "connect") as mock_connect:
        rot_none_str = RotatorSystem("None")
        mock_connect.assert_not_called()

    with patch.object(RotatorSystem, "connect") as mock_connect:
        rot_none = RotatorSystem(None)
        mock_connect.assert_not_called()

def test_qt_dynamic_view_poll_model(qtbot):
    from view_pyside import QtDynamicView
    probe_model = MagicMock()
    probe_model.ui_schema = {"sections": []}
    probe_model.read_position = MagicMock()
    
    view = QtDynamicView(probe_model)
    qtbot.addWidget(view)
    view._poll_model()
    probe_model.read_position.assert_called()

    rot_model = MagicMock()
    rot_model.ui_schema = {"sections": []}
    rot_model.poll_status = MagicMock()
    
    rot_view = QtDynamicView(rot_model)
    qtbot.addWidget(rot_view)
    rot_view._poll_model()
    rot_model.poll_status.assert_called()
