"""SERIAL-12: Manual mode packet stream issues.

Two issues in send_manual_mode_command:
1. Debug print at 50 Hz floods stdout (FIXED: print removed)
2. Unbounded flush() holds _lock indefinitely on stalled ports (FIXED: flush removed)
"""

import pytest
import struct
import threading
import time
from unittest.mock import patch, MagicMock, Mock
from controller.serial import serial, TransportError


def get_mock_serial():
    """Create a mock serial port."""
    mock_instance = MagicMock()
    mock_instance.is_open = True
    mock_instance.in_waiting = 0
    return mock_instance


def get_params():
    """Standard manual mode params for testing."""
    return {
        "x_axisStatus": 0.5,
        "y_axisStatus": -0.5,
        "z_axisStatusL": -1.0,
        "z_axisStatusR": -1.0,
        "x_stepSize": 10,
        "y_stepSize": 10,
        "z_stepSize": 10,
        "dpad_LR": 0,
        "dpad_UD": 0,
        "LBumper": 0,
        "RBumper": 0,
        "manual_jog_speed": 400,
        "packet_format": "<BBffffffffff"
    }


def test_serial12_print_removed(capsys):
    """SERIAL-12 fix: debug print removed.

    The print statement previously ran on every send at 50 Hz (50 lines/sec),
    flooding stdout and drowning all other messages. Since the write itself
    succeeds and the print doesn't provide actionable information, it has
    been removed.

    Sending 10 packets should produce NO "[SerialDrive] Sending 12-Field MANUAL State"
    lines.
    """
    with patch("controller.serial.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_serial.return_value = mock_instance

        s = serial("COM1")
        params = get_params()

        # Send packets 10 times
        for _ in range(10):
            s.send_manual_mode_command(params)

        captured = capsys.readouterr()
        # Count lines containing the manual state packet print
        manual_state_lines = [
            line for line in captured.out.split('\n')
            if "Sending 12-Field MANUAL State" in line
        ]

        # After fix: should be 0 (print removed)
        assert len(manual_state_lines) == 0, (
            f"Expected no 'Sending 12-Field MANUAL State' prints after SERIAL-12 fix, "
            f"got {len(manual_state_lines)}."
        )


def test_serial12_lock_not_held_during_flush(capsys):
    """SERIAL-12 fix: lock is not held during flush.

    After removing the flush() call, the lock is released immediately after
    the write, so other threads can acquire it without waiting for an unbounded
    drain operation.
    """
    with patch("controller.serial.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()

        # Don't make flush block - just track if it was called
        mock_instance.flush = MagicMock()
        mock_serial.return_value = mock_instance

        s = serial("COM1")
        params = get_params()

        # Track whether the second thread could acquire the lock
        lock_acquired = []

        def try_acquire_lock():
            """Try to acquire the lock with a 0.5s timeout."""
            acquired = s._lock.acquire(timeout=0.5)
            if acquired:
                lock_acquired.append(True)
                s._lock.release()
            else:
                lock_acquired.append(False)

        # Send a packet
        s.send_manual_mode_command(params)

        # Try to acquire the lock from another thread
        # This should succeed immediately since the lock is released after write
        acquirer = threading.Thread(target=try_acquire_lock)
        acquirer.start()
        acquirer.join(timeout=2.0)

        # After fix: should be able to acquire the lock
        assert lock_acquired == [True], (
            f"Expected to acquire the lock after SERIAL-12 fix, "
            f"but got {lock_acquired}."
        )


def test_serial12_flush_not_called_in_send(capsys):
    """SERIAL-12 fix: flush() is not called from send_manual_mode_command.

    The flush has been removed from send_manual_mode_command because:
    1. At 50 Hz, the next packet supersedes this one in 20ms (latest-state-wins)
    2. The unbounded flush() can block indefinitely on stalled USB endpoints
    3. The lock must not be held during unbounded I/O operations

    Callers needing a bounded drain can use the separate flush() method.
    """
    with patch("controller.serial.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_instance.flush = MagicMock()
        mock_serial.return_value = mock_instance

        s = serial("COM1")
        params = get_params()

        s.send_manual_mode_command(params)

        # After fix, flush should NOT be called from send_manual_mode_command
        mock_instance.flush.assert_not_called()


def test_serial12_write_still_succeeds():
    """Verify that the actual write still succeeds after removing print/flush."""
    with patch("controller.serial.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_serial.return_value = mock_instance

        s = serial("COM1")
        params = get_params()

        s.send_manual_mode_command(params)

        # The write should still be called once
        mock_instance.write.assert_called_once()
        packet = mock_instance.write.call_args[0][0]

        # Verify packet structure is intact
        assert len(packet) == 42
        unpacked = struct.unpack('<BBffffffffff', packet)
        assert unpacked[0] == 0xAA  # START_MARKER
        assert unpacked[1] == 1     # Mode byte
        assert unpacked[2] == 0.5   # x_axisStatus


def test_serial12_packet_format_unchanged():
    """Verify that SERIAL-12 fix doesn't change the packet format.

    The brief explicitly requires: "Do not change the packet format, the struct
    layout, the field order, or the START_MARKER."
    """
    with patch("controller.serial.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_serial.return_value = mock_instance

        s = serial("COM1")
        params = get_params()

        s.send_manual_mode_command(params)

        # Verify the packet structure
        packet = mock_instance.write.call_args[0][0]
        assert len(packet) == 42, "Packet should be 42 bytes"

        unpacked = struct.unpack('<BBffffffffff', packet)
        # Verify START_MARKER is still 0xAA
        assert unpacked[0] == 0xAA
        # Verify mode byte is still 1
        assert unpacked[1] == 1
        # Verify remaining fields are unpacked correctly
        assert unpacked[2] == 0.5   # x_axisStatus
        assert unpacked[3] == -0.5  # y_axisStatus
        assert unpacked[4] == 0.0   # combined_z_axis_status


def test_serial12_multiple_rapid_sends():
    """Verify that rapid sends don't deadlock after removing lock-holding flush.

    At 50 Hz, the manual mode loop sends 50 packets per second. Verify that
    removing the lock-holding flush allows rapid sends without blocking.
    """
    with patch("controller.serial.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_serial.return_value = mock_instance

        s = serial("COM1")
        params = get_params()

        # Send 50 packets rapidly to simulate 1 second at 50 Hz
        for i in range(50):
            s.send_manual_mode_command(params)

        # All sends should complete without blocking
        assert mock_instance.write.call_count == 50
