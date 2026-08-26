import pytest
import sys
import os
import stat
from unittest.mock import MagicMock, patch
import tkinter as tk

# Mock toupcam module before importing camera_control
mock_toupcam = MagicMock()
sys.modules['lib.toupcam'] = mock_toupcam
sys.modules['lib'] = MagicMock()

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from camera_control import CameraPresetManager

@pytest.fixture
def root():
    root = tk.Tk()
    yield root
    root.destroy()

@pytest.fixture
def manager(root):
    # Mock Toupcam.Open
    mock_toupcam.Toupcam.Open.return_value = MagicMock()
    app = CameraPresetManager(root)
    return app

def test_load_corrupted_json(manager, tmp_path):
    # Create corrupted JSON file
    file_path = tmp_path / "corrupted.json"
    file_path.write_text("{ this is not valid json")
    
    with patch('camera_control.filedialog.askopenfilename', return_value=str(file_path)):
        with patch('camera_control.messagebox.showerror') as mock_showerror:
            manager.load_preset()
            # Assert error was shown
            mock_showerror.assert_called_once()
            args, _ = mock_showerror.call_args
            assert args[0] == "Error"
            assert "Could not load preset" in args[1]

def test_load_missing_file(manager):
    # Use a non-existent path
    file_path = "non_existent_file.json"
    
    with patch('camera_control.filedialog.askopenfilename', return_value=file_path):
        with patch('camera_control.messagebox.showerror') as mock_showerror:
            manager.load_preset()
            # Assert error was shown
            mock_showerror.assert_called_once()
            args, _ = mock_showerror.call_args
            assert args[0] == "Error"
            assert "Could not load preset" in args[1]

def test_save_readonly_file(manager, tmp_path):
    # Create a read-only file
    file_path = tmp_path / "readonly.json"
    file_path.write_text("{}")
    
    # Make file readonly
    os.chmod(str(file_path), stat.S_IREAD)
    
    try:
        with patch('camera_control.filedialog.asksaveasfilename', return_value=str(file_path)):
            with patch('camera_control.messagebox.showerror') as mock_showerror:
                manager.save_preset()
                # Assert error was shown
                mock_showerror.assert_called_once()
                args, _ = mock_showerror.call_args
                assert args[0] == "Error"
                assert "Could not save file" in args[1]
    finally:
        # Revert permissions so tmp_path cleanup doesn't fail
        os.chmod(str(file_path), stat.S_IWRITE)
