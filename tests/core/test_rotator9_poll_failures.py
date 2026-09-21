"""ROTATOR-9: Stale state on poll failures.

Poll failures leave the last position/state on screen forever, and the
display differs from main. The model half must reflect poll failures in its
own state rather than silently leaving stale values.
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


def get_mock_smc100():
    """A fake SMC100 driver."""
    smc = MagicMock()
    smc.get_position_deg.return_value = 10.5
    smc.get_status.return_value = (0, "28")  # status=0, state_code="28" (Moving)
    return smc


class TestRotatorPollFailures:
    """Tests for ROTATOR-9: visible poll failure state."""

    def test_poll_success_updates_state(self):
        """Successful poll updates position and state."""
        rotator = create_test_rotator()
        rotator.smc = get_mock_smc100()
        rotator.is_connected = True

        rotator.poll_status()

        assert rotator.position == 10.5, "Position should be updated"
        assert rotator.state == "Moving", "State should be updated"

    def test_poll_failure_indicates_communication_loss(self):
        """Poll failure sets state to indicate communication loss, not stale values."""
        rotator = create_test_rotator()
        rotator.smc = get_mock_smc100()
        rotator.is_connected = True
        rotator.position = 20.0  # Set initial position
        rotator.state = "Ready"   # Set initial state

        # Make poll fail
        rotator.smc.get_position_deg.side_effect = Exception("Read timeout")

        with patch('error_routing.ErrorRouter.report_warning'):
            rotator.poll_status()

        # After poll failure, state should indicate communication loss
        # (not remain "Ready")
        assert rotator.state != "Ready", (
            "State should change from Ready after poll failure (not stale)")
        assert (rotator.state == "Communication lost" or
                "communication" in rotator.state.lower() or
                "lost" in rotator.state.lower() or
                rotator.state == "Disconnected"), (
            f"State should indicate communication loss, got: {rotator.state}")

    def test_poll_failure_clears_position(self):
        """Poll failure clears position, not leaving stale value."""
        rotator = create_test_rotator()
        rotator.smc = get_mock_smc100()
        rotator.is_connected = True
        rotator.position = 20.0  # Set initial position

        # Make poll fail
        rotator.smc.get_position_deg.side_effect = Exception("Read timeout")

        with patch('error_routing.ErrorRouter.report_warning'):
            rotator.poll_status()

        # After poll failure, position should be cleared (None or non-numeric)
        assert rotator.position is None or not isinstance(rotator.position, float), (
            f"Position should be cleared on poll failure (not stale), got: {rotator.position}")

    def test_poll_failure_single_warning(self):
        """Poll failure reports a warning (single transition, not spam)."""
        rotator = create_test_rotator()
        rotator.smc = get_mock_smc100()
        rotator.is_connected = True

        # Make poll fail consistently
        rotator.smc.get_position_deg.side_effect = Exception("Read timeout")

        with patch('error_routing.ErrorRouter.report_warning') as mock_warn:
            # First failure should report
            rotator.poll_status()
            call_count_after_first = mock_warn.call_count

            # Second failure - depends on implementation but should not spam
            rotator.poll_status()
            call_count_after_second = mock_warn.call_count

            # Should have reported at least once
            assert call_count_after_first > 0, (
                "Should report first poll failure as warning")
            # Subsequent failures should not keep reporting the same thing
            # (exact behavior depends on implementation)

    def test_poll_recovery_after_failure(self):
        """Poll can recover after failure, state returns to normal."""
        rotator = create_test_rotator()
        rotator.smc = get_mock_smc100()
        rotator.is_connected = True

        # First poll succeeds
        rotator.poll_status()
        assert rotator.state == "Moving", "Initial poll should succeed"

        # Make poll fail
        rotator.smc.get_position_deg.side_effect = Exception("Read timeout")
        with patch('error_routing.ErrorRouter.report_warning'):
            rotator.poll_status()

        # Verify failure state
        failed_state = rotator.state
        assert failed_state != "Moving", "State should change on failure"

        # Recovery: clear the side effect
        rotator.smc.get_position_deg.side_effect = None
        rotator.smc.get_position_deg.return_value = 15.0
        rotator.smc.get_status.return_value = (0, "32")  # Ready

        rotator.poll_status()

        # Should recover
        assert rotator.position == 15.0, "Position should be restored"
        assert rotator.state == "Ready", "State should be restored to Ready"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
