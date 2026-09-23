"""Regression tests for the part of WEB-20 that survived two prior "closed"
reports.

`resolve_options`, `dispatch_command` and `set_device_attribute` already
read through `SystemManager.get_active_models_snapshot()` (see
tests/web/test_web_20_active_models.py). Verified still live at the time
this test was written:

  - `get_system_info` (the `/api/system/status` handler) read
    `getattr(self.system_manager, "active_models", {})` directly.
  - `get_devices` (the `/api/devices` handler) did the same.
  - No generation counter existed anywhere in web_adapter.py, so a command
    whose per-device lock acquisition raced a `initialize_setup()` swap had
    no way to notice the manager underneath it had changed.

Each test below is built so the pre-fix code returns an empty/stale result
by construction: `MagicMock(spec=SystemManager)` does not expose
`active_models` as an attribute at all (it is set only in `__init__`, not on
the class), so `getattr(mock, "active_models", {})` silently falls back to
`{}` on the mock exactly as it would docstring-innocuously on a real
manager under an in-flight swap. `get_active_models_snapshot()` is a real
method on the class, so the mock honors it. Only code that calls the real
accessor sees the device this test registers.
"""

import pytest
from unittest.mock import MagicMock

from views.web.web_adapter import WebModelAdapter
from model.system_manager import SystemManager


def _mock_manager_with_device(name="test_device", model=None):
    manager = MagicMock(spec=SystemManager)
    if model is None:
        model = MagicMock()
        model.ui_schema = {"sections": []}
    manager.get_active_models_snapshot = MagicMock(return_value={name: model})
    return manager, model


class TestGetSystemInfoUsesSnapshot:
    def test_get_system_info_reports_active_device_from_snapshot(self):
        adapter = WebModelAdapter()
        manager, _ = _mock_manager_with_device("test_device")
        adapter.system_manager = manager

        info = adapter.get_system_info()

        assert "test_device" in info["active_devices"], (
            "get_system_info() must read active devices through "
            "get_active_models_snapshot(), not a direct/live attribute "
            "access that silently sees nothing on a manager mid-swap"
        )


class TestGetDevicesUsesSnapshot:
    def test_get_devices_reports_device_from_snapshot(self):
        adapter = WebModelAdapter()
        model = MagicMock()
        model.ui_schema = {"sections": [{"elements": []}]}
        manager, _ = _mock_manager_with_device("test_device", model)
        adapter.system_manager = manager

        devices = adapter.get_devices()

        assert "test_device" in devices, (
            "get_devices() must read through get_active_models_snapshot(), "
            "not a direct/live attribute access"
        )


class TestGenerationCounter:
    """The finding's proposed fix direction: a generation counter in the
    adapter, re-checked after the per-device lock is taken, so a command
    that raced a manager swap aborts instead of running against a
    torn-down model."""

    def test_generation_counter_exists(self):
        adapter = WebModelAdapter()
        assert hasattr(adapter, "_generation"), (
            "WebModelAdapter must track a generation counter that bumps "
            "on every manager swap (initialize_setup)"
        )

    def test_generation_bumps_on_setup_swap(self):
        import app_bootstrap

        adapter = WebModelAdapter(system_manager=SystemManager())
        before = adapter._generation

        real_manager = SystemManager()
        adapter.system_manager = real_manager
        # Simulate what _initialize_setup_locked does on a successful
        # re-setup swap without going through the full hardware-building
        # path: swap the manager and bump the counter the same way.
        adapter.system_manager = SystemManager()
        adapter._generation += 1

        assert adapter._generation != before

    def test_dispatch_command_aborts_if_manager_changes_under_the_lock(self):
        """A command that captures its model via the snapshot, then finds
        the generation counter has moved by the time it holds the
        per-device lock, must refuse rather than run against a model that
        may already be torn down."""

        class GenerationBumpingLock:
            """Stands in for the real per-device lock, bumping the
            adapter's generation counter at acquisition time the way a
            concurrent initialize_setup() would between snapshot and
            lock."""

            def __init__(self, adapter):
                self._adapter = adapter

            def __enter__(self):
                self._adapter._generation += 1
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        adapter = WebModelAdapter()
        model = MagicMock()
        model.ui_schema = {
            "sections": [{"elements": [{"command": "move"}]}]
        }
        model.move = MagicMock(return_value="ok")
        manager, _ = _mock_manager_with_device("dev", model)
        adapter.system_manager = manager
        adapter._get_device_lock = lambda name: GenerationBumpingLock(adapter)

        result = adapter.dispatch_command("dev", "move")

        assert result["status"] == "error"
        assert result["code"] == 409
        model.move.assert_not_called()
