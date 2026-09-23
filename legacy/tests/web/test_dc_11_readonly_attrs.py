"""Test for DC-11: Web API write allowlist should only include entry elements

The web adapter's set_device_attribute should refuse to write readonly
attributes, toggles, and dropdowns - those should only be changed through
their corresponding commands.
"""

import pytest
from unittest.mock import MagicMock, Mock
from views.web.web_adapter import WebModelAdapter


def test_dc_11_readonly_not_writable():
    """Readonly schema elements should not be writable via set_device_attribute"""
    adapter = WebModelAdapter()
    
    # Create a mock model with readonly pos_x
    mock_model = MagicMock()
    mock_model.pos_x = 10.5
    
    # Build a schema with readonly element
    mock_model.ui_schema = {
        "sections": [
            {
                "elements": [
                    {"type": "readonly", "model_attr": "pos_x"}
                ]
            }
        ]
    }
    
    # Create a mock manager
    mock_manager = MagicMock()
    mock_manager.get_active_models_snapshot = MagicMock(
        return_value={"TestProbe": mock_model}
    )
    adapter.system_manager = mock_manager
    
    # Attempt to write readonly attribute should fail
    result = adapter.set_device_attribute("TestProbe", "pos_x", 20)
    
    assert result["status"] == "error"
    assert result["code"] == 403
    assert "not exposed" in result["message"]


def test_dc_11_toggle_not_writable():
    """Toggle schema elements should not be writable via set_device_attribute"""
    adapter = WebModelAdapter()
    
    # Create a mock model with toggle auton_flag
    mock_model = MagicMock()
    mock_model.auton_flag = False
    
    # Build a schema with toggle element
    mock_model.ui_schema = {
        "sections": [
            {
                "elements": [
                    {"type": "toggle", "model_attr": "auton_flag", "command": "toggle_auton"}
                ]
            }
        ]
    }
    
    # Create a mock manager
    mock_manager = MagicMock()
    mock_manager.get_active_models_snapshot = MagicMock(
        return_value={"TestProbe": mock_model}
    )
    adapter.system_manager = mock_manager
    
    # Attempt to write toggle attribute should fail
    result = adapter.set_device_attribute("TestProbe", "auton_flag", True)
    
    assert result["status"] == "error"
    assert result["code"] == 403
    assert "not exposed" in result["message"]


def test_dc_11_dropdown_not_writable():
    """Dropdown schema elements should not be writable via set_device_attribute"""
    adapter = WebModelAdapter()
    
    # Create a mock model with dropdown controller_var
    mock_model = MagicMock()
    mock_model.controller_var = "Controller1"
    
    # Build a schema with dropdown element
    mock_model.ui_schema = {
        "sections": [
            {
                "elements": [
                    {
                        "type": "dropdown",
                        "model_attr": "controller_var",
                        "command": "set_controller",
                        "options_command": "get_available_controllers"
                    }
                ]
            }
        ]
    }
    
    # Create a mock manager
    mock_manager = MagicMock()
    mock_manager.get_active_models_snapshot = MagicMock(
        return_value={"TestProbe": mock_model}
    )
    adapter.system_manager = mock_manager
    
    # Attempt to write dropdown attribute should fail
    result = adapter.set_device_attribute("TestProbe", "controller_var", "Controller2")
    
    assert result["status"] == "error"
    assert result["code"] == 403
    assert "not exposed" in result["message"]


def test_dc_11_entry_is_writable():
    """Entry schema elements should still be writable via set_device_attribute"""
    adapter = WebModelAdapter()
    
    # Create a mock model with entry full_speed
    mock_model = MagicMock()
    mock_model.full_speed = 100
    type(mock_model).full_speed = MagicMock()  # Make sure it's not a read-only property
    
    # Build a schema with entry element
    mock_model.ui_schema = {
        "sections": [
            {
                "elements": [
                    {"type": "entry", "model_attr": "full_speed"}
                ]
            }
        ]
    }
    
    # Create a mock manager
    mock_manager = MagicMock()
    mock_manager.get_active_models_snapshot = MagicMock(
        return_value={"TestProbe": mock_model}
    )
    adapter.system_manager = mock_manager
    
    # Attempt to write entry attribute should succeed
    result = adapter.set_device_attribute("TestProbe", "full_speed", 120)
    
    assert result["status"] == "ok"
    assert result["code"] == 200
