"""WEB-22 (remainder, per the wave-4 brief): the shared fetch wrapper's
abort-on-timeout is already covered by test_web_ui_js.py. Per-device
staleness marking (_markDeviceSeen/_markDeviceMissedCycle/_setDeviceStale,
STALE_AFTER_CYCLES) and dropdown refresh-on-focus
(refreshDropdownOptions + bindCardInteractiveEvents' focus listener) were
implemented but never exercised by any test - no DOM harness existed for
either.

This is coverage, not a fix: both behaviors were already correct in the
app.js this worktree started from. js_web22_staleness_and_dropdown_refresh_check.js
was sanity-checked against two deliberately sabotaged copies of app.js
(threshold changed, focus listener removed) during development and caught
both; that proof is not repeated here since there is no real pre-fix state
to check this test against.
"""
import os
import shutil
import subprocess

import pytest

NODE = shutil.which("node")

HERE = os.path.dirname(__file__)
APP_JS = os.path.join(HERE, "..", "..", "src", "views", "web", "static", "js", "app.js")
HARNESS = os.path.join(HERE, "js_web22_staleness_and_dropdown_refresh_check.js")


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_staleness_marking_and_dropdown_refresh_on_focus():
    """_markDeviceMissedCycle must gray out and disable a card's controls
    only once STALE_AFTER_CYCLES consecutive polls have missed it, and
    _markDeviceSeen must clear that state; refreshDropdownOptions must
    populate a select from /api/options and preserve a still-valid
    selection, and bindCardInteractiveEvents must wire a focus listener
    that re-runs it exactly once per focus rather than only at first
    render."""
    result = subprocess.run(
        [NODE, HARNESS, APP_JS],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}")
    assert "OK" in result.stdout
