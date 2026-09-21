"""ROTATOR-13 (web half): a device reporting connection_status
"disconnected" left every control in its card clickable and silently
inert - most acutely the SMC100 rotator, which has no simulator to fall
back on for a SIM/None port. The model half is done elsewhere
(RotatorSystem.connection_status never reports "simulated", and refused
commands return `Refused(...)` which dispatch_command surfaces as an
error). What was still missing here: the client never disabled the card's
own controls, and said nothing beyond an easy-to-miss OFFLINE badge shared
with several other states.

Like WEB-22's fetch-timeout test, this project has no JS test harness
(no jsdom/playwright) and none is introduced here - a single throwaway
Node script drives the real app.js source in a vm sandbox, the same
technique as js_fetch_timeout_check.js.
"""
import os
import shutil
import subprocess

import pytest

NODE = shutil.which("node")

HERE = os.path.dirname(__file__)
APP_JS = os.path.join(HERE, "..", "..", "src", "views", "web", "static", "js", "app.js")
HARNESS = os.path.join(HERE, "js_rotator13_connection_gate_check.js")


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_disconnected_device_controls_are_disabled_and_noted():
    """TransferStageApp._applyConnectionGate, called every poll cycle for
    every device card, must force-disable every button/input/select in a
    disconnected card's body and show a visible note explaining why -
    and must leave a connected/simulated/hardware card alone."""
    result = subprocess.run(
        [NODE, HARNESS, APP_JS],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}")
    assert "OK" in result.stdout
