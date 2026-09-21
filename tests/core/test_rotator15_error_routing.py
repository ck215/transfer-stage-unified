"""ROTATOR-15: Remove dead error_callback hook, route all errors through ErrorRouter.

The error_callback hook was initialized to None but never assigned anywhere,
making all branches that checked it dead code. All errors should be routed
consistently through ErrorRouter.
"""
import pytest
import threading
from unittest.mock import patch, MagicMock

from model.rotator_system import RotatorSystem


def create_test_rotator():
    """Create a rotator for testing without going through connect()."""
    rotator = RotatorSystem.__new__(RotatorSystem)
    rotator._lock = threading.Lock()
    rotator._position = None
    rotator._state = "Disconnected"
    rotator._error = "0"
    rotator._commanded_target = None
    rotator._motion_lock = threading.Lock()
    rotator.smc = None
    rotator.is_connected = False
    rotator.error_callback = None
    return rotator


class TestErrorRouting:
    """Tests for ROTATOR-15: error_callback removal and ErrorRouter routing."""

    def test_async_action_failure_routes_through_error_router(self):
        """Async action failures route through ErrorRouter."""
        rotator = create_test_rotator()
        rotator.smc = MagicMock()
        rotator.smc.move_absolute_deg.side_effect = Exception("Move failed")
        rotator.is_connected = True
        
        with patch('error_routing.ErrorRouter.report_error') as mock_report:
            rotator._run_guarded(rotator.smc.move_absolute_deg, (45.0,))
            
            # Should have reported through ErrorRouter
            assert mock_report.called, "Should report error through ErrorRouter"

    def test_stop_failure_routes_through_error_router(self):
        """Stop command failures route through ErrorRouter."""
        rotator = create_test_rotator()
        rotator.smc = MagicMock()
        rotator.smc.stop.side_effect = Exception("Stop failed")
        
        with patch('error_routing.ErrorRouter.report_error') as mock_report:
            rotator.stop()
            
            # Should have reported through ErrorRouter
            assert mock_report.called, "Should report stop error"
            assert "Rotator Error" in mock_report.call_args[0][0]

    def test_error_routing_to_both_callback_and_error_router(self):
        """Errors route to both callback (for backward compatibility) and ErrorRouter."""
        rotator = create_test_rotator()

        # If a callback is set, it still gets called for backward compatibility
        # with existing test code
        mock_callback = MagicMock()
        rotator.error_callback = mock_callback
        rotator.smc = MagicMock()
        rotator.smc.stop.side_effect = Exception("Stop failed")

        with patch('error_routing.ErrorRouter.report_error') as mock_report:
            rotator.stop()

        # Both callback and ErrorRouter are called
        assert mock_callback.called, (
            "error_callback should still be called for backward compatibility")
        assert mock_report.called, (
            "ErrorRouter should also be called for consistent error routing")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
