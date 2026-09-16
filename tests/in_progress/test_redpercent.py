import pytest
import threading
import time
import os
import tempfile
import csv
from unittest.mock import Mock, patch, MagicMock, PropertyMock
import numpy as np

# Import the classes under test
from src.model.redpercent_system import RedPercentDataLog, RedPercentSystem


# --- Fixtures ---

@pytest.fixture
def mock_pil_and_mss():
    """
    Mock PIL and mss modules to avoid hardware dependencies.
    """
    mock_image = Mock()
    mock_mss = Mock()
    mock_sct = Mock()
    
    # Create a mock screenshot object
    mock_screenshot = Mock()
    mock_screenshot.size = (100, 100)
    # Create raw bgra data for a 100x100 image (4 bytes per pixel)
    # All pixels are black (0,0,0,0) initially
    mock_screenshot.bgra = b'\x00' * (100 * 100 * 4)
    
    mock_sct.grab.return_value = mock_screenshot
    
    mock_mss.mss.return_value.__enter__ = Mock(return_value=mock_sct)
    mock_mss.mss.return_value.__exit__ = Mock(return_value=None)
    
    with patch('color_test.Image', mock_image):
        with patch('color_test.mss', mock_mss):
            with patch('color_test.np', np):
                yield mock_image, mock_mss, mock_sct, mock_screenshot


@pytest.fixture
def data_log():
    return RedPercentDataLog(sync_dimensions=['X', 'Y'], probe_name="TestProbe", probe_tilt_angle="45")


@pytest.fixture
def system():
    sys = RedPercentSystem()
    sys.sync_dimensions = ['X', 'Y']
    return sys


# --- Tests for RedPercentDataLog ---

class TestRedPercentDataLog:
    
    def test_init_defaults(self):
        log = RedPercentDataLog()
        assert log.sync_dimensions == []
        assert log.probe_name == ""
        assert log.probe_tilt_angle == ""
        assert log.red_values == []
        assert log.loc_values == {}
        assert log.vel_values == {}
        assert isinstance(log.lock, type(threading.Lock()))

    def test_init_with_args(self):
        log = RedPercentDataLog(sync_dimensions=['X'], probe_name="P1", probe_tilt_angle="10")
        assert log.sync_dimensions == ['X']
        assert log.probe_name == "P1"
        assert log.probe_tilt_angle == "10"
        assert 'X' in log.loc_values
        assert 'X' in log.vel_values

    def test_add_entry_basic(self):
        log = RedPercentDataLog()
        log.add_entry(50.0)
        assert log.red_values == [50.0]
        assert log.loc_values == {}
        assert log.vel_values == {}

    def test_add_entry_with_sync_dimensions(self):
        log = RedPercentDataLog(sync_dimensions=['X'])
        log.add_entry(50.0, locs={'X': 10.0}, vels={'X': 1.0})
        assert log.red_values == [50.0]
        assert log.loc_values['X'] == [10.0]
        assert log.vel_values['X'] == [1.0]

    def test_add_entry_missing_sync_data_defaults_to_zero(self):
        log = RedPercentDataLog(sync_dimensions=['X', 'Y'])
        log.add_entry(50.0, locs={'X': 10.0}) # Y missing
        assert log.loc_values['X'] == [10.0]
        assert log.loc_values['Y'] == [0.0]
        assert log.vel_values['X'] == [0.0]
        assert log.vel_values['Y'] == [0.0]

    def test_add_entry_thread_safety(self):
        log = RedPercentDataLog(sync_dimensions=['X'])
        
        def add_entries():
            for i in range(100):
                log.add_entry(float(i), locs={'X': float(i)}, vels={'X': 0.0})
        
        threads = [threading.Thread(target=add_entries) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
            
        assert len(log.red_values) == 500
        assert len(log.loc_values['X']) == 500

    def test_save_to_csv_empty(self):
        log = RedPercentDataLog
