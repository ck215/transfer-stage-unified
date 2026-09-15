import pytest
from unittest.mock import MagicMock, patch
from model.system_manager import SystemManager
from controller.gamepad import ControllerPoller

def test_system_manager_shutdown_all():
    mgr = SystemManager()
    
    mock_model1 = MagicMock()
    mock_model2 = MagicMock()
    
    mgr.register_model("Model 1", mock_model1)
    mgr.register_model("Model 2", mock_model2)
    
    mgr.shutdown_all()
    
    mock_model1.poller.stop_polling.assert_called_once()
    mock_model1.poller.close.assert_called_once()
    mock_model1.disconnect.assert_called_once()
    mock_model1.stop.assert_called_once()
    
    # Active models cleared
    assert len(mgr.active_models) == 0

def test_system_manager_reboot_model():
    mgr = SystemManager()
    
    mock_model = MagicMock()
    mgr.register_model("Model 1", mock_model)
    
    constructor_called = False
    def mock_constructor():
        nonlocal constructor_called
        constructor_called = True
        return "New Model"
        
    with patch('time.sleep'):
        new = mgr.reboot_model("Model 1", mock_constructor)
        
    mock_model.poller.stop_polling.assert_called_once()
    mock_model.disconnect.assert_called_once()
    mock_model.stop.assert_called_once()
    
    assert constructor_called is True
    assert mgr.get_model("Model 1") == "New Model"

def test_gamepad_change_controller():
    # Setup initial
    claims = {}
    with patch('controller.gamepad.pygame'):
        driver = ControllerPoller(0, claims, "Test Process")
        driver.start_polling = MagicMock()
        
        # Change controller to None
        driver.change_controller("None")
        
        # Claims should be dropped/updated
        assert claims["Test Process"] == "None Detected"
        assert driver.gamepad is None


