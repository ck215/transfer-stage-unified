"""WEB-13: the web "Stop Monitoring" flow did not offer to save unsaved
monitoring data. RC-11 (2513c32) landed `RedPercentSystem.pending_run_data()`
/ `has_unsaved_data` and wired PySide's view to consult
`self.model.has_unsaved_data` in-process before dispatching stop_monitoring
(views/pyside/view.py) - but nothing published that information to the web
client, which cannot read the model object directly, and nothing in app.js
consulted it either.

Two pieces, both owned here:

  - `WebModelAdapter.get_state()` now publishes `pending_run_data()`'s dict
    for any model that defines it (duck-typed, not RedPercentSystem-specific)
    alongside the schema-declared attrs, the same way it already publishes
    `connection_status`.
  - app.js's `dispatchCommand` special-cases `stop_monitoring`: if the
    device's last-known `pending_run_data.has_data` is true, it offers to
    run `save_log` first. Covered by a Node harness
    (js_web13_stop_monitoring_save_check.js), since that half is pure
    client-side logic.

redpercent_system.py is read-only in this worktree; these tests exercise
the adapter/JS seam that reads it, not the model itself.
"""
import os
import shutil
import subprocess

import pytest
from unittest.mock import MagicMock

from views.web.web_adapter import WebModelAdapter
from model.system_manager import SystemManager


def _manager_with_model(model, name="Red Percent Window"):
    manager = MagicMock(spec=SystemManager)
    manager.get_active_models_snapshot = MagicMock(return_value={name: model})
    return manager


class TestGetStatePublishesPendingRunData:
    def test_get_state_includes_pending_run_data_when_model_defines_it(self):
        model = MagicMock()
        model.ui_schema = {"sections": []}
        model.pending_run_data = MagicMock(
            return_value={"has_data": True, "sample_count": 7, "run_id": "r1"}
        )
        # _determine_connection_status walks several getattr fallbacks;
        # give it nothing it recognizes so it falls through harmlessly.
        model.connection_status = "hardware"

        adapter = WebModelAdapter()
        adapter.system_manager = _manager_with_model(model)

        state = adapter.get_state()

        assert state["Red Percent Window"]["pending_run_data"] == {
            "has_data": True, "sample_count": 7, "run_id": "r1",
        }

    def test_get_state_omits_pending_run_data_for_models_without_it(self):
        """A model with no pending_run_data() (every non-RedPercent model
        today) must not gain a bogus key or raise."""
        model = MagicMock(spec=["ui_schema", "connection_status"])
        model.ui_schema = {"sections": []}
        model.connection_status = "hardware"

        adapter = WebModelAdapter()
        adapter.system_manager = _manager_with_model(model, name="Rotator")

        state = adapter.get_state()

        assert "pending_run_data" not in state["Rotator"]


NODE = shutil.which("node")
HERE = os.path.dirname(__file__)
APP_JS = os.path.join(HERE, "..", "..", "src", "views", "web", "static", "js", "app.js")
HARNESS = os.path.join(HERE, "js_web13_stop_monitoring_save_check.js")


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_stop_monitoring_offers_to_save_unsaved_data():
    """dispatchCommand('device', 'stop_monitoring') must offer save_log
    first when the device's published pending_run_data says there is
    unsaved data, skip the prompt when there is none, and never block
    stop_monitoring itself from going out either way."""
    result = subprocess.run(
        [NODE, HARNESS, APP_JS],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}")
    assert "OK" in result.stdout
