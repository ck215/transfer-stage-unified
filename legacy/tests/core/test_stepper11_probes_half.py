"""STEPPER-11 (probes half): the Param table (RC-6) and the read-only mode
flags (RC-3) that this finding's "values part" and "flags part" close.

`docs/architecture/root-causes.md` already lists both parts as closed
(RC-3 "11(flags part)", RC-6 "STEPPER-11 (values part)"), and reading the
current `src/model/probes.py` bears that out: `PARAMS` is a typed table
per class, the schema publishes `value_type`/`min`/`max` from it, and
`auton_flag`/`manual_flag`/`system_enabled` are derived properties with no
setter. This file exercises both, through the real (unmodified) Web
adapter boundary the audit named as the failure site, to show the model's
own half of the fix actually holds end to end.

Not covered here, because it is not the probes half: STEPPER-11(a), the Web
JS committing an entry only on the Set button/Enter
(`src/views/web/static/js/app.js`) — a rendering/timing defect, not
something the model can enforce by itself.
"""
import pytest
from unittest.mock import MagicMock

from model import schema as sch
from model.probes import DCProbe, StepperProbe
from views.web.web_adapter import WebModelAdapter


def _adapter_with(probe, name="Probe"):
    adapter = WebModelAdapter()
    manager = MagicMock()
    manager.get_active_models_snapshot = MagicMock(return_value={name: probe})
    adapter.system_manager = manager
    return adapter


@pytest.mark.parametrize("flag", ["manual_flag", "auton_flag", "system_enabled"])
def test_mode_flags_are_not_writable_through_the_web_route(flag):
    """STEPPER-11(b): `/api/set_attr` used to be able to arm `manual_flag`
    directly, bypassing `enable()` and the gamepad check. RC-3 made the
    flags read-only derived properties; this is the Web boundary's own
    `descriptor.fset is None` check proving that actually refuses the
    write rather than merely rendering the control disabled.
    """
    probe = StepperProbe("SIM", None)
    try:
        adapter = _adapter_with(probe)
        result = adapter.set_device_attribute("Probe", flag, True)
        assert result["status"] == "error"
        assert result["code"] == 403
        assert getattr(probe, flag) is False
    finally:
        probe.teardown()


def test_a_dc_probes_speed_field_is_declared_with_its_own_type_and_bounds():
    """RC-6 item 2: the type and bounds are declared in the schema, not
    guessed by each view from the field's current contents.
    """
    probe = DCProbe("SIM", None)
    try:
        element = next(e for e in sch.elements(probe.ui_schema)
                        if e.get("model_attr") == "full_speed")
        assert element["value_type"] == "float"
        assert element["min"] == 1
    finally:
        probe.teardown()


def test_an_unparseable_speed_over_the_web_route_falls_back_to_this_classs_own_default():
    """STEPPER-11(c): the audit's exact failure was a DC probe's unparseable
    speed silently substituting the *stepper's* 400 instead of its own 120.
    RC-6's per-class `PARAMS` table is what makes the fallback correct, and
    this drives the write through the real Web `set_device_attribute` route
    the audit named as the call site.
    """
    probe = DCProbe("SIM", None)
    try:
        adapter = _adapter_with(probe)
        result = adapter.set_device_attribute("Probe", "full_speed", "not-a-number")
        assert result["status"] == "ok"
        assert probe.get_params()["full_speed"] == 120, (
            "an unparseable value must fall back to this class's own "
            "default, not another class's")
    finally:
        probe.teardown()


def test_the_defaults_table_has_no_class_agnostic_fallback():
    """RC-6 item 1: the fallback belongs to the class that declares it."""
    assert DCProbe.PARAMS["full_speed"].default == 120
    assert StepperProbe.PARAMS["full_speed"].default == 400
