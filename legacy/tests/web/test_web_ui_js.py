"""Exercises the small amount of app.js logic that is meaningfully testable
without a browser: the shared fetch wrapper's WEB-22 timeout.

This project has no JS test harness (no jsdom/playwright/node package.json)
and none is introduced here beyond a single throwaway Node script - Node
itself is not otherwise a project dependency, so this test skips cleanly
where it is unavailable rather than failing the gate.
"""
import os
import shutil
import subprocess

import pytest

NODE = shutil.which("node")

HERE = os.path.dirname(__file__)
APP_JS = os.path.join(HERE, "..", "..", "src", "views", "web", "static", "js", "app.js")
HARNESS = os.path.join(HERE, "js_fetch_timeout_check.js")


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_shared_fetch_wrapper_aborts_a_hung_request_after_its_timeout():
    """WEB-22: every same-origin fetch is wrapped once, in app.js's
    attachSessionToken IIFE, to attach a bounded AbortController timeout.
    Before this, a stalled request (a wedged server, a dropped connection)
    hung forever - inside the poll cycle specifically, that left
    `isPolling` stuck true and froze every future poll behind the one that
    never returned.

    Runs the real wrapper, loaded out of the actual app.js, against a real
    TCP server that accepts the connection and never responds.
    window.__FETCH_TIMEOUT_MS_OVERRIDE__ shrinks the wrapper's timeout so
    this does not have to wait out the real 20-second default.
    """
    result = subprocess.run(
        [NODE, HARNESS, APP_JS],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}")
    assert "OK" in result.stdout
