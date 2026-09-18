import pytest
from unittest.mock import Mock
from model.system_manager import SystemManager

def test_full_stop_all():
    manager = SystemManager()

    model1 = Mock()
    del model1.stop_monitoring
    del model1.stop
    model1.full_stop = Mock()

    model2 = Mock()
    del model2.full_stop
    del model2.stop
    model2.stop_monitoring = Mock()

    model3 = Mock()
    del model3.full_stop
    del model3.stop_monitoring
    model3.stop = Mock()

    model4 = Mock()
    del model4.stop_monitoring
    del model4.stop
    model4.full_stop = Mock(side_effect=RuntimeError("Failure"))

    manager.register_model("m1", model1)
    manager.register_model("m2", model2)
    manager.register_model("m4", model4) 
    manager.register_model("m3", model3)

    manager.full_stop_all()

    model1.full_stop.assert_called_once()
    model2.stop_monitoring.assert_called_once()
    model4.full_stop.assert_called_once()
    model3.stop.assert_called_once()
