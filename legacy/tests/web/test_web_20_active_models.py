"""Test for WEB-20: Web reads SystemManager.active_models directly (unlocked)

Verify that resolve_options, dispatch_command, and set_device_attribute use
get_active_models_snapshot() instead of direct dict access to avoid stale
references when the manager is swapped during re-setup.
"""

import pytest
import threading
import time
from unittest.mock import MagicMock, patch, Mock
from views.web.web_adapter import WebModelAdapter
from model.system_manager import SystemManager


class TestWEB20ActiveModelsSnapshot:
    """Verify WEB-20 fix: all three methods use get_active_models_snapshot()"""

    def test_resolve_options_uses_snapshot(self):
        """resolve_options should use get_active_models_snapshot not live dict"""
        adapter = WebModelAdapter()
        
        # Mock the system manager and model
        mock_manager = MagicMock(spec=SystemManager)
        mock_model = MagicMock()
        mock_model.ui_schema = {"sections": []}
        mock_model.get_available_controllers = MagicMock(return_value=["Controller1"])
        
        # Track if get_active_models_snapshot was called
        snapshot_called = []
        def track_snapshot():
            snapshot_called.append(True)
            return {"test_device": mock_model}
        
        mock_manager.get_active_models_snapshot = track_snapshot
        adapter.system_manager = mock_manager
        
        # Call resolve_options
        result = adapter.resolve_options("test_device", "get_available_controllers")
        
        # Verify get_active_models_snapshot was called
        assert snapshot_called, "resolve_options should call get_active_models_snapshot()"

    def test_dispatch_command_uses_snapshot(self):
        """dispatch_command should use get_active_models_snapshot not live dict"""
        adapter = WebModelAdapter()
        
        # Mock the system manager and model
        mock_manager = MagicMock(spec=SystemManager)
        mock_model = MagicMock()
        mock_model.ui_schema = {"sections": []}
        
        # Track if get_active_models_snapshot was called
        snapshot_called = []
        def track_snapshot():
            snapshot_called.append(True)
            return {"test_device": mock_model}
        
        mock_manager.get_active_models_snapshot = track_snapshot
        adapter.system_manager = mock_manager
        
        # Mock model command
        mock_model.execute_command = MagicMock(return_value="ok")
        
        # Call dispatch_command
        result = adapter.dispatch_command("test_device", "stop")
        
        # Verify get_active_models_snapshot was called
        assert snapshot_called, "dispatch_command should call get_active_models_snapshot()"

    def test_set_device_attribute_uses_snapshot(self):
        """set_device_attribute should use get_active_models_snapshot not live dict"""
        adapter = WebModelAdapter()
        
        # Mock the system manager and model
        mock_manager = MagicMock(spec=SystemManager)
        mock_model = MagicMock()
        mock_model.ui_schema = {"sections": []}
        
        # Track if get_active_models_snapshot was called
        snapshot_called = []
        def track_snapshot():
            snapshot_called.append(True)
            return {"test_device": mock_model}
        
        mock_manager.get_active_models_snapshot = track_snapshot
        adapter.system_manager = mock_manager
        
        # Mock model attribute
        mock_model.test_attr = 10
        
        # Call set_device_attribute
        result = adapter.set_device_attribute("test_device", "test_attr", 20)
        
        # Verify get_active_models_snapshot was called
        assert snapshot_called, "set_device_attribute should call get_active_models_snapshot()"
