import pytest
from unittest.mock import MagicMock
from model.probes import BaseProbe, ProbeMode


def _probe(gamepad):
    """A probe in MANUAL, reached the only way MANUAL can be reached.

    These tests used to assign `probe.manual_flag = True`. That is exactly the
    write RC-3 removed: it armed a mode with no gamepad check and no hardware
    enable. Going through `enter_manual()` is the point — it is now the only
    door.
    """
    probe = BaseProbe(port="SIM", controller_id="None")
    probe.serial_comm = MagicMock()
    probe.poller = MagicMock()
    probe.poller.gamepad = gamepad
    return probe


def test_manual_mode_blocked_without_gamepad():
    """A pad that vanishes mid-session takes manual mode down with it.

    The old assertion was `manual_flag is False` — the flag alone. Clearing it
    left `system_enabled` True, so the coils stayed energized in a mode
    nothing was driving (STEPPER-5). The transition de-energizes now.
    """
    probe = _probe(MagicMock())
    assert probe.enter_manual() is True
    probe.poller.gamepad = None

    probe.send_manual_mode_command({"x_axisStatus": 1.0})

    assert probe.manual_flag is False
    assert probe.mode is ProbeMode.DISABLED
    assert probe.system_enabled is False
    probe.serial_comm.send_manual_mode_command.assert_called_once()
    args, _kwargs = probe.serial_comm.send_manual_mode_command.call_args
    assert args[0]["x_axisStatus"] == 0.0


def test_manual_mode_never_starts_without_a_gamepad():
    """The refusal comes before the hardware enable, so no coils energize."""
    probe = _probe(None)
    assert probe.enter_manual() is False
    assert probe.mode is ProbeMode.DISABLED
    probe.serial_comm.enable.assert_not_called()


def test_manual_mode_allowed_with_gamepad():
    probe = _probe(MagicMock())
    assert probe.enter_manual() is True
    probe.serial_comm.send_manual_mode_command.reset_mock()

    probe.send_manual_mode_command({"x_axisStatus": 1.0})

    assert probe.manual_flag is True
    probe.serial_comm.send_manual_mode_command.assert_called_once()
    args, _kwargs = probe.serial_comm.send_manual_mode_command.call_args
    assert args[0]["x_axisStatus"] == 1.0
