import pytest
import sys
import os

from model.probes import BaseProbe
from unittest.mock import patch, MagicMock

def test_baseprobe_constructed_with_string_id():
    with patch("controller.gamepad.pygame") as mock_pygame:
        mock_pygame.joystick.get_count.return_value = 1
        mock_js = MagicMock()
        mock_js.get_name.return_value = "Xbox"
        mock_pygame.joystick.Joystick.return_value = mock_js

        claims = {}
        with patch("controller.gamepad.ControllerPoller._is_os_connected", return_value=True):
            probe = BaseProbe("COM1", "ID 0: Xbox", claims)
            assert probe.poller.controller_index == 0
