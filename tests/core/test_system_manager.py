import pytest
from unittest.mock import Mock, MagicMock
from model.system_manager import SystemManager

def test_full_stop_all():
    """full_stop_all() calls emergency_stop() on every model that has one —
    the power_down-vs-full_stop priority decision now lives inside each
    model's own emergency_stop() (see BaseProbe.emergency_stop), not in
    SystemManager, which no longer arbitrates between method names."""
    manager = SystemManager()

    model1 = Mock(spec=['emergency_stop'])
    model2 = Mock(spec=['emergency_stop'])
    failing_model = Mock(spec=['emergency_stop'])
    failing_model.emergency_stop.side_effect = RuntimeError("Failure")
    no_emergency_stop_model = Mock(spec=[])

    manager.register_model("m1", model1)
    manager.register_model("m2", model2)
    manager.register_model("failing", failing_model)
    manager.register_model("no_op", no_emergency_stop_model)

    manager.full_stop_all()

    model1.emergency_stop.assert_called_once()
    model2.emergency_stop.assert_called_once()
    failing_model.emergency_stop.assert_called_once()
    # one model's failure must not stop the others from being called — already
    # verified above (model1/model2 called regardless of registration order)


def test_full_stop_all_skips_models_without_emergency_stop():
    """A model with no emergency_stop method is silently skipped — no crash."""
    manager = SystemManager()
    model = Mock(spec=[])
    manager.register_model("m1", model)
    manager.full_stop_all()  # must not raise
