import pytest
import io
import os
from unittest.mock import MagicMock, patch

from model.probes import BaseProbe
from model.redpercent_system import RedPercentSystem, RedPercentDataLog

class TestRedPercentFeatures:

    def test_base_probe_velocity_casting(self):
        """Test Action 1: BaseProbe safely casts mapped state to velocity floats."""
        probe = BaseProbe("COM_TEST", "TEST_CTRL")
        
        # Test 1: Normal float values
        probe.poller = MagicMock()
        probe.poller.get_mapped_state.return_value = {
            "x_axisStatus": 1.5,
            "y_axisStatus": "2.5",
            "z_axisStatusR": 3.0,
            "z_axisStatusL": 1.0
        }
        assert probe.vel_x == 1.5
        assert probe.vel_y == 2.5
        assert probe.vel_z == 1.0  # (3.0 - 1.0) / 2.0
        
        # Test 2: Invalid values (None, string, empty)
        probe.poller.get_mapped_state.return_value = {
            "x_axisStatus": None,
            "y_axisStatus": "invalid_string",
            "z_axisStatusR": None,
            "z_axisStatusL": ""
        }
        assert probe.vel_x == 0.0
        assert probe.vel_y == 0.0
        assert probe.vel_z == 0.0

    def test_redpercent_velocity_logging(self):
        """Test Action 2: RedPercentSystem correctly fetches vel_x, vel_y, vel_z."""
        system = RedPercentSystem()
        
        mock_stepper = MagicMock()
        mock_stepper.pos_x = "10"
        mock_stepper.pos_y = "20"
        mock_stepper.pos_z = "30"
        mock_stepper.vel_x = 5.5
        mock_stepper.vel_y = 6.6
        mock_stepper.vel_z = 7.7
        
        system.stepper_model = mock_stepper
        system.sync_dimensions = ["X", "Y", "Z"]
        system.data_log = RedPercentDataLog(system.sync_dimensions)
        
        # We manually simulate the inside of the `_monitor_colors` loop
        # to ensure it extracts the velocities and adds them to data_log
        rounded_red = 45.0
        
        locs = {'X': float(mock_stepper.pos_x), 'Y': float(mock_stepper.pos_y), 'Z': float(mock_stepper.pos_z)}
        vels = {
            'X': getattr(system.stepper_model, 'vel_x', 0.0),
            'Y': getattr(system.stepper_model, 'vel_y', 0.0),
            'Z': getattr(system.stepper_model, 'vel_z', 0.0)
        }
        
        system.data_log.add_entry(rounded_red, locs, vels)
        
        # Check the logged entry
        assert len(system.data_log.red_values) == 1
        assert system.data_log.vel_values['X'][0] == 5.5
        assert system.data_log.vel_values['Y'][0] == 6.6
        assert system.data_log.vel_values['Z'][0] == 7.7

    @patch('builtins.open', new_callable=MagicMock)
    def test_csv_carries_no_metadata_block(self, mock_open):
        """REDPERCENT-22 re-authored this test rather than deleting it.

        It used to assert that `save_to_csv` PREPENDS a `# Metadata` block.
        That block was never a comment convention — it was four data rows
        written before the header, so a default `pandas.read_csv` took
        `# Metadata` as the column names and every real column name as data.

        The configuration moved to a sibling `<run_id>_station_meta.json`
        (`RedPercentSystem.station_meta`), and the CSV is now a plain
        rectangle. The assertion is inverted deliberately: this is the test
        that fails if the block is ever reintroduced.
        """
        log = RedPercentDataLog(["X"], "Test_Probe_1", 45.0)
        log.add_entry(50.0, {"X": 1.0}, {"X": 0.5})

        mock_file = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_file

        log.save_to_csv("dummy.csv")

        written_lines = [call.args[0] for call in mock_file.write.mock_calls]
        full_output = "".join(written_lines)

        assert "# Metadata" not in full_output
        assert "# Probe Name" not in full_output
        # The first thing written is the header, not a comment row.
        assert full_output.lstrip().startswith("Red Percent")
        assert "Stepper X Location" in full_output

    def test_detect_red_accuracy(self):
        import numpy as np
        system = RedPercentSystem()
        
        # None check
        assert system.detect_red(None) == 0.0
        
        # 10x10 image = 100 pixels
        image = np.zeros((10, 10, 3), dtype=np.uint8)
        
        # 10 pixels pure red (R=200, G=0, B=0)
        image[0, :] = [200, 0, 0]
        # 10 pixels pure blue (R=0, G=0, B=200)
        image[1, :] = [0, 0, 200]
        # 10 pixels borderline red (R=151, G=99, B=99)
        image[2, :] = [151, 99, 99]
        # 10 pixels barely not red (R=150, G=0, B=0) -> R > 150 is False
        image[3, :] = [150, 0, 0]
        
        # Should be exactly 20 pixels out of 100
        print(type(image)); assert system.detect_red(image) == 20.0

    @patch('model.redpercent_system.mss.mss')
    def test_monitoring_thread_lifecycle(self, mock_mss):
        """Re-authored for RC-11 item 1 (S13): `start_monitoring()` now
        refuses without a focus area (REDPERCENT-9) instead of starting a
        thread that spins forever with zero samples, and it returns a
        `CommandResult` rather than `None`. `monitoring` is a property
        derived from the run, not a bare boolean the test flips directly;
        `time.sleep` is no longer patched because the loop now waits on
        `run.stop_event`, which this test's own `stop_monitoring()` call
        wakes immediately instead of the test needing to out-wait a mock.
        """
        import time
        system = RedPercentSystem()
        system.stepper_model = MagicMock()
        system.set_focus_area(0, 0, 10, 10)

        # Prevent actual capturing blocking our mock thread
        mock_sct = MagicMock()
        mock_sct.grab.return_value = None  # Force detect_red to return 0.0 quickly
        mock_mss.return_value.__enter__.return_value = mock_sct

        assert not system.monitoring

        result = system.start_monitoring()
        assert result.ok, result.reason
        assert system.monitoring
        assert system._monitor_thread is not None
        assert system._monitor_thread.is_alive()

        # Let the thread spin once
        time.sleep(0.1)

        system.stop_monitoring()
        assert not system.monitoring
        system._monitor_thread.join(timeout=1.0)
        assert not system._monitor_thread.is_alive()

    def test_start_monitoring_refuses_without_a_focus_area(self):
        """REDPERCENT-9: today's code starts anyway, `capture_focus_area`
        returns `None` forever, and the loop spins with `monitoring` True
        and zero samples. It must refuse instead, and start nothing."""
        system = RedPercentSystem()
        assert system.focus_area is None

        result = system.start_monitoring()

        assert result.refused, result
        assert not system.monitoring
        assert system._monitor_thread is None

