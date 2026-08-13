import unittest
from unittest.mock import MagicMock, call
import struct

from src.hardware.serial_comm import SerialArduino, PACKET_FORMAT, START_MARKER
from src.domain_models.probes import StepperProbe, DCProbe, ChuckPositioner
from src.domain_models.temperature_system import TemperatureSystem

class TestDomainInteractions(unittest.TestCase):
    def setUp(self):
        self.mock_serial = MagicMock()
        self.mock_serial.is_open = True
        self.mock_serial.in_waiting = 0
        
        # We initialize with SIM so it doesn't try to open real serial port
        self.serial_arduino = SerialArduino(port='SIM')
        self.serial_arduino.ser = self.mock_serial
        # Note: port='SIM' bypasses serial connection and leaves self.ser = None
        # But we force it to self.mock_serial right after.
        # Wait, if we use port='SIM', it returns early and self.ser is None, which is fine as we override it.
        # However, _verify_serial relies on self.ser and self.ser.is_open, which is True for MagicMock.

    def test_stepper_probe_autonomous_command(self):
        probe = StepperProbe(self.serial_arduino)
        probe.x_step = "16"
        probe.y_step = "16"
        probe.z_step = "16"
        probe.full_speed = "400"
        probe.x_dist = "100"
        probe.y_dist = "-50"
        probe.z_dist = "0"
        probe.auton_flag = True
        probe.manual_flag = False

        probe.send_autonomous_command()
        
        expected_command = b"16,16,16,0,400,0,0,100,-50,0,0,1\n"
        self.mock_serial.write.assert_called_once_with(expected_command)
        
    def test_dc_probe_autonomous_command(self):
        probe = DCProbe(self.serial_arduino)
        probe.x_step = "1"
        probe.y_step = "1"
        probe.z_step = "1"
        probe.full_speed = "120"
        probe.slow_speed = "50"
        probe.brake_distance = "10"
        probe.x_dist = "-200"
        probe.y_dist = "0"
        probe.z_dist = "20"
        probe.auton_flag = True
        probe.manual_flag = False

        probe.send_autonomous_command()
        
        expected_command = b"1,1,1,0,120,50,10,-200,0,20,0,1\n"
        self.mock_serial.write.assert_called_once_with(expected_command)
        
    def test_chuck_positioner_manual_command(self):
        probe = ChuckPositioner(self.serial_arduino)
        probe.x_step = "2"
        probe.y_step = "2"
        probe.z_step = "2"
        probe.man_full_speed = "200"
        
        controller_params = {
            "x_axisStatus": -0.5,
            "y_axisStatus": 1.0,
            "z_axisStatusR": 0.5, # Down trigger (partially pressed) -> 0.75 value
            "z_axisStatusL": -1.0, # Up trigger (not pressed) -> 0 value
            # combined_z = 0 - 0.75 = -0.75
            "dpad_LR": -1,
            "dpad_UD": 1,
            "LBumper": 1,
            "RBumper": 0
            # combined_bumpers = 1 - 0 = 1
        }
        
        probe.send_manual_mode_command(controller_params)
        
        z_up_value = (-1.0 + 1.0) / 2.0
        z_down_value = (0.5 + 1.0) / 2.0
        combined_z_axis_status = z_up_value - z_down_value
        
        expected_packet = struct.pack(
            PACKET_FORMAT,
            START_MARKER,
            1,
            float(-0.5),
            float(1.0),
            float(combined_z_axis_status),
            int(2),
            int(2),
            int(2),
            int(-1),
            int(1),
            int(1),
            int(200),
        )
        self.mock_serial.write.assert_called_once_with(expected_packet)
        self.mock_serial.flush.assert_called_once()
        
    def test_temperature_system(self):
        sys = TemperatureSystem(serial_conn=self.mock_serial)
        sys.send_settings(setpoint=100.5, ramp_rate=15, p_term=1.5, i_term=0.2, d_term=0.05, offset=-1.2)
        expected_command = b"<100.5,15,1.5,0.2,0.05,-1.2>"
        self.mock_serial.write.assert_called_with(expected_command)
        
    def test_temperature_system_process_data(self):
        sys = TemperatureSystem(serial_conn=self.mock_serial)
        sys.process_raw_data("1.0, 25.4, 100.0\n")
        self.assertEqual(sys.tempC[0], 25.4)
        self.assertEqual(sys.time[0], 1.0)
        self.assertEqual(sys.sp[0], 100.0)
        self.assertEqual(sys.current_temp, "25.40 °C")
        
        sys.stop()
        self.mock_serial.write.assert_called_with(b"<0,10,2.0,0.5,.1,0>")
        self.mock_serial.close.assert_called_once()
        
    def test_edge_cases_and_disconnected(self):
        # Disconnected serial should not crash
        disconnected_serial_arduino = SerialArduino(port='SIM')
        # Since it's SIM, ser is None.
        
        probe = StepperProbe(disconnected_serial_arduino)
        try:
            probe.send_autonomous_command()
            probe.send_manual_mode_command({})
        except Exception as e:
            self.fail(f"Disconnected serial crashed with: {e}")
            
        # Zero speeds, negative coordinates
        probe = StepperProbe(self.serial_arduino)
        probe.x_dist = "-9999"
        probe.full_speed = "0"
        probe.send_autonomous_command()
        expected_command = b"16,16,16,0,0,0,0,-9999,0,0,0,0\n"
        self.mock_serial.write.assert_called_with(expected_command)

if __name__ == '__main__':
    unittest.main()
