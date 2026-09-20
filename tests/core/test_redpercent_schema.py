import pytest
from model.redpercent_system import RedPercentSystem, RedPercentDataLog
from model.probes import StepperProbe
from model.temperature_system import TemperatureSystem
from model.rotator_system import RotatorSystem

def check_schema(instance):
    schema = instance.ui_schema
    for section in schema.get("sections", []):
        for el in section.get("elements", []):
            if el["type"] == "toggle":
                cmd = el.get("command")
                assert cmd is not None, f"Toggle {el} is missing 'command' key"
                assert hasattr(instance, cmd), f"Toggle command '{cmd}' missing on {instance.__class__.__name__}"
                assert callable(getattr(instance, cmd)), f"Toggle command '{cmd}' not callable on {instance.__class__.__name__}"
            elif el["type"] == "dropdown":
                opts = el.get("options")
                opts_cmd = el.get("options_command")
                assert opts is not None or opts_cmd is not None, f"Dropdown {el} must have 'options' or 'options_command'"
                if opts_cmd is not None:
                    assert hasattr(instance, opts_cmd), f"Dropdown options_command '{opts_cmd}' missing on {instance.__class__.__name__}"
                    assert callable(getattr(instance, opts_cmd)), f"Dropdown options_command '{opts_cmd}' not callable on {instance.__class__.__name__}"

def test_all_ui_schemas():
    rp = RedPercentSystem()
    sp = StepperProbe("SIM", None)
    ts = TemperatureSystem("SIM")
    rs = RotatorSystem("SIM")
    
    for instance in [rp, sp, ts, rs]:
        check_schema(instance)

def test_redpercent_toggle_methods():
    rp = RedPercentSystem()
    
    assert 'X' not in rp.sync_dimensions
    rp.toggle_sync_x()
    assert 'X' in rp.sync_dimensions
    rp.toggle_sync_x()
    assert 'X' not in rp.sync_dimensions
    
    assert 'Y' not in rp.sync_dimensions
    rp.toggle_sync_y()
    assert 'Y' in rp.sync_dimensions
    rp.toggle_sync_y()
    assert 'Y' not in rp.sync_dimensions
    
    assert 'Z' not in rp.sync_dimensions
    rp.toggle_sync_z()
    assert 'Z' in rp.sync_dimensions
    rp.toggle_sync_z()
    assert 'Z' not in rp.sync_dimensions

def test_redpercent_get_available_probe_names():
    """Re-authored in S12 (RC-9).

    What this used to assert: that `get_available_probe_names` filtered out
    probes carrying `_disabled_in_setup`. That filter never excluded anything
    in practice — only the web path ever set the flag, and it set it from a
    key normalization had already dropped, so it was False for every model
    ever built (WEB-4, MANAGER-12). A disabled device is not constructed now,
    so it is never registered and the list cannot contain it; the flag and
    the filter are both gone.

    The list is also no longer assigned from outside. It is maintained from
    the manager's `registered`/`released` events (I-9.1), which is what this
    test now drives.
    """
    from app_bootstrap import link_models
    from model.system_manager import SystemManager

    class MockProbe:
        pos_x = 0.0

        def teardown(self):
            pass

        def emergency_stop(self):
            pass

    manager = SystemManager()
    rp = RedPercentSystem()
    manager.register("Red Percent Window", rp)
    link_models(manager)
    assert rp.get_available_probe_names() == []

    manager.register("Stepper Probe", MockProbe())
    manager.register("DC Probe", MockProbe())
    assert set(rp.get_available_probe_names()) == {"Stepper Probe", "DC Probe"}

    manager.release("DC Probe")
    assert rp.get_available_probe_names() == ["Stepper Probe"]
def test_redpercent_has_unsaved_data():
    rp = RedPercentSystem()
    assert rp.has_unsaved_data is False
    
    rp.data_log = RedPercentDataLog(["X"])
    assert rp.has_unsaved_data is False
    
    rp.data_log.add_entry(45.0, {"X": 10}, {"X": 0})
    assert rp.has_unsaved_data is True

