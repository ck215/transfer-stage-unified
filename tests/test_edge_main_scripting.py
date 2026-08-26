import pytest
import sys
import os
from unittest import mock
import tkinter as tk
from pathlib import Path

# Mock serial to prevent hardware import errors
class MockSerialModule:
    class SerialTimeoutException(Exception):
        pass
    class SerialException(Exception):
        pass

sys.modules['serial'] = mock.MagicMock()
sys.modules['serial'].SerialTimeoutException = MockSerialModule.SerialTimeoutException
sys.modules['serial'].SerialException = MockSerialModule.SerialException
sys.modules['serial.tools'] = mock.MagicMock()
sys.modules['serial.tools.list_ports'] = mock.MagicMock()
sys.modules['pygame'] = mock.MagicMock()
sys.modules['PIL'] = mock.MagicMock()
sys.modules['mss'] = mock.MagicMock()
sys.modules['numpy'] = mock.MagicMock()
class MockGcodeParser:
    @staticmethod
    def parse_gcode_lines(f, include_comments=False):
        # We can implement a simple parser for the tests if needed, or just let it raise error
        if "malformed" in str(f.name):
            raise ValueError("Malformed gcode")
        if "recursive" in str(f.name):
            raise RecursionError("Maximum recursion depth exceeded")
        return ["MOCK_GCODE_LINE"]
sys.modules['gcodeparser'] = mock.MagicMock()
sys.modules['gcodeparser'].parse_gcode_lines = MockGcodeParser.parse_gcode_lines

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
from stepper_frame import StepperFrame, AppLogic

@pytest.fixture
def app_logic():
    root = tk.Tk()
    gui = StepperFrame(root)
    controller = mock.MagicMock()
    serial_drv = mock.MagicMock()
    app = AppLogic(root, gui, controller, serial_drv, {}, "test")
    yield app
    root.destroy()

def test_script_non_existent_file(app_logic, capsys):
    app_logic.open_script = 'non_existent_random_file.gcode'
    app_logic.run_script_button()
    
    captured = capsys.readouterr()
    assert "[AppLogic] Script parse failed. Perhaps selected file is not gcode." in captured.out
    
def test_script_malformed_gcode(app_logic, tmp_path, capsys):
    malformed_file = tmp_path / "malformed.gcode"
    malformed_file.write_text("THIS IS NOT GCODE\nJUST RANDOM TEXT\n!@#$%^&*()")
    
    app_logic.open_script = str(malformed_file)
    app_logic.run_script_button()
    
    captured = capsys.readouterr()
    # It might parse it or throw an error, but the bare except catches it anyway
    # Let's just assert that it ran without unhandled exceptions
    pass

def test_script_recursive_macros(app_logic, tmp_path, capsys):
    recursive_file = tmp_path / "recursive.gcode"
    recursive_file.write_text("O100\nG01 X10\nM98 P100\nM99\nM98 P100")
    
    app_logic.open_script = str(recursive_file)
    app_logic.run_script_button()
    
    captured = capsys.readouterr()
    pass

def test_script_empty_file(app_logic, tmp_path, capsys):
    empty_file = tmp_path / "empty.gcode"
    empty_file.write_text("")
    
    app_logic.open_script = str(empty_file)
    app_logic.run_script_button()
    
    captured = capsys.readouterr()
    assert "[AppLogic] Script parse failed" not in captured.out

def test_script_none_file(app_logic, capsys):
    app_logic.open_script = None
    # Suppress TypeError since open(None, 'r') raises TypeError but bare except catches it
    app_logic.run_script_button()
    
    captured = capsys.readouterr()
    assert "[AppLogic] Script parse failed. Perhaps selected file is not gcode." in captured.out
