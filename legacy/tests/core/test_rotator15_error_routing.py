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

    def test_the_dead_callback_hook_is_gone(self):
        """ROTATOR-15, the half that kept coming back `partly`.

        This slot used to hold `test_error_routing_to_both_callback_and_
        error_router`, which asserted that `error_callback` "should still be
        called for backward compatibility". Nothing in production ever
        assigned it -- not `app_bootstrap.py`, not any of the three views --
        so the only thing it was backward-compatible *with* was other tests.
        That assertion is what blocked the deletion the finding asks for,
        across three waves: an agent would correctly conclude the hook was
        dead, try to remove it, watch this test go red, and report `partly`.

        Reporting is unconditional through ErrorRouter/EventBus (RC-8, S11),
        which is what the rest of this file asserts.
        """
        rotator = create_test_rotator()
        assert not hasattr(rotator, "error_callback"), (
            "the error_callback hook is back. No shipped path assigns it, so "
            "it can only be a seam for tests -- and a test that sets it "
            "asserts code no operator ever reaches (ROTATOR-15)")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
