import pytest
from unittest.mock import Mock, MagicMock
from model.system_manager import SystemManager
from conftest import ManagedStub

def test_full_stop_all():
    """full_stop_all() calls emergency_stop() on every registered model.

    The power_down-vs-full_stop priority decision lives inside each model's
    own emergency_stop() (see BaseProbe.emergency_stop), not in
    SystemManager, which no longer arbitrates between method names.
    """
    manager = SystemManager()

    model1, model2 = ManagedStub("m1"), ManagedStub("m2")
    failing = ManagedStub("failing", stop_error=RuntimeError("Failure"))

    manager.register("m1", model1)
    manager.register("m2", model2)
    manager.register("failing", failing)

    manager.full_stop_all()

    assert failing.stops == 1
    # One model's failure must not stop the others. Registration order puts
    # the failing model last, so assert it the other way round too.
    assert model1.stops == 1
    assert model2.stops == 1


def test_full_stop_all_continues_past_a_failing_model():
    """E-stop is all-or-nothing in intent: one bad model cannot veto the rest."""
    manager = SystemManager()
    failing = ManagedStub("failing", stop_error=RuntimeError("Failure"))
    survivor = ManagedStub("survivor")
    manager.register("failing", failing)
    manager.register("survivor", survivor)

    manager.full_stop_all()

    assert survivor.stops == 1


def test_a_model_without_emergency_stop_never_reaches_the_registry():
    """This used to be "silently skipped at full stop", which is the bug.

    SystemManager arbitrated over duck-typed names, so a model missing
    emergency_stop was quietly passed over by the global FULL STOP. The
    contract is now enforced at registration instead, where it is visible.
    """
    class NoStop:
        def teardown(self):
            pass

    manager = SystemManager()
    with pytest.raises(TypeError):
        manager.register("m1", NoStop())
