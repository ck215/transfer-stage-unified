import pytest
from unittest.mock import MagicMock, patch
from model.system_manager import SystemManager
from conftest import ManagedStub
from controller.gamepad import ControllerPoller

def test_system_manager_shutdown_all():
    mgr = SystemManager()

    model1, model2 = ManagedStub("Model 1"), ManagedStub("Model 2")
    mgr.register("Model 1", model1)
    mgr.register("Model 2", model2)

    mgr.shutdown_all()

    assert model1.teardowns == 1
    assert model2.teardowns == 1
    assert len(mgr.active_models) == 0

def test_system_manager_reconfigure_replaces_the_model_set():
    """reboot_model's replacement (RC-1 item 3).

    reboot_model built the new model before releasing the old one and slept
    1 s on the caller thread to "simulate hardware reboot delay" — on the UI
    thread that was a visible freeze, and the overlap meant two live handles
    on one port (I-1.4). reconfigure tears down first.
    """
    mgr = SystemManager()
    old = ManagedStub("Model 1")
    mgr.register("Model 1", old)

    new = ManagedStub("Model 1 v2")
    def builder(manager):
        assert old.teardowns == 1, "old model must be gone before the new one is built"
        manager.register("Model 1", new)

    mgr.reconfigure(builder)

    assert old.teardowns == 1
    assert mgr.get_model("Model 1") is new
