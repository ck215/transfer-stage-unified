import pytest
from unittest.mock import MagicMock, patch
from model.probes import StepperProbe, DCProbe
from model.redpercent_system import RedPercentSystem

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

def test_is_stepping_starts_false_and_unaffected_by_plain_mode_entry():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")

    assert probe.is_stepping is False

    probe.enter_auton()
    assert probe.is_stepping is False, "enter_auton should not mark the probe as stepping"

    probe.enter_manual()
    assert probe.is_stepping is False, "enter_manual should not mark the probe as stepping"

def test_macro_start_auton_sets_is_stepping_and_full_stop_clears_it():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")
        probe.serial_comm = MagicMock()

        probe.macro_start_auton()
        assert probe.is_stepping is True, "macro_start_auton should mark the probe as actively stepping"

        probe.full_stop()
        assert probe.is_stepping is False, "full_stop should clear is_stepping"
        assert probe.auton_flag is False
        assert probe.manual_flag is False

def test_send_stop_command_emits_zeros():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")
        probe.serial_comm = MagicMock()
        
        probe.x_dist = "10"
        probe.full_speed = "500"
        probe.manual_flag = True
        
        probe.send_stop_command()
        probe.serial_comm.send_autonomous_command.assert_called_once()
        args, _ = probe.serial_comm.send_autonomous_command.call_args
        params = args[0]
        
        assert params["x_step_size"] == 0
        assert params["full_speed"] == 0
        assert params["x_dist"] == 0
        assert params["command_code_manual"] == 0
        assert params["command_code_auton"] == 0

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
        assert sent_params["manual_jog_speed"] == 120.0

def test_redpercent_syncs_to_non_stepper_probe():
    """
    RedPercentSystem must be linkable to any position-tracking probe, not just
    StepperProbe, so rigs using a DC Probe / Chuck Positioner for XYZ still get
    location data tied to red-percent readings (app.py's fallback linkage).
    """
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        dc_probe = DCProbe("COM1", "Virtual Controller A")

    assert hasattr(dc_probe, 'pos_x') and hasattr(dc_probe, 'pos_y') and hasattr(dc_probe, 'pos_z')

    rp = RedPercentSystem()
    rp.stepper_model = dc_probe
    rp.sync_dimensions = ['X', 'Y', 'Z']
    dc_probe.pos_x, dc_probe.pos_y, dc_probe.pos_z = "1.5", "2.5", "3.5"
    rp.capture_focus_area = lambda sct: object()
    rp.detect_red = lambda img: 25.0

    rp.start_monitoring()
    import time
    time.sleep(0.2)
    rp.stop_monitoring()
    time.sleep(0.1)

    assert rp.data_log.red_values == [25.0]
    assert rp.data_log.loc_values == {'X': [1.5], 'Y': [2.5], 'Z': [3.5]}

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

def test_watchdog_do_disable_manual_mode(qtbot):
    from view_pyside import QtDynamicView
    probe_model = MagicMock()
    probe_model.ui_schema = {"sections": []}
    probe_model.poller = MagicMock()
    probe_model.system_enabled = True
    probe_model.is_stepping = False
    probe_model.manual_flag = True
    probe_model.disable = MagicMock()

    view = QtDynamicView(probe_model)
    qtbot.addWidget(view)
    
    # Get the activity_callback passed to start_polling
    args, kwargs = probe_model.poller.start_polling.call_args
    activity_callback = kwargs.get("activity_callback")
    
    # Initialize the disable timer
    activity_callback()
    
    # Trigger the timeout
    view.disable_timer.timeout.emit()
    
    # Probe disable should NOT be called because manual_flag is True
    probe_model.disable.assert_not_called()
    assert view.disable_timer.isActive(), "disable_timer should be restarted"

