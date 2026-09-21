"""A DC-6 refusal reaches the web client as a refusal, not as a crash.

The model half of DC-6 (wave 4, `probes.py`) made every `PARAMS` attribute a
mode-gated property whose setter raises `ValueError` when the schema's own
`disabled_when` forbids the write. A property setter cannot return a
`Refused`, so raising is the only channel available to it.

The web half was built in a different worktree, against a tree where that
setter did not raise. Its `except Exception` therefore caught the refusal and
returned **500**. A working interlock was being reported to the operator as a
server fault — the mirror image of ERRORS-1, where a refusal was reported as
success. STEPPER-11 already established 403 as this repo's code for a refused
write.

Neither agent could have found this: the model half had no web route in its
write set, the web half had no raising setter in its tree.
"""
from unittest.mock import MagicMock, patch

import pytest

from model.probes import ModeRefused, ProbeMode
from views.web.web_adapter import WebModelAdapter


def _adapter_with(model):
    """The real method, with only the collaborators it actually reaches."""
    import threading
    adapter = WebModelAdapter.__new__(WebModelAdapter)
    adapter._state_lock = threading.RLock()
    adapter._device_locks = {}
    adapter._generation = 0
    adapter.system_manager = MagicMock()
    adapter.system_manager.get_active_models_snapshot.return_value = {
        "DC Probe": model}
    return adapter


def test_a_mode_gated_write_is_refused_with_403_not_500():
    model = MagicMock()
    type(model).x_step = property(
        lambda self: "1",
        lambda self, v: (_ for _ in ()).throw(
            ModeRefused("X Step Size cannot be changed while autonomous")))

    adapter = _adapter_with(model)
    with patch.object(WebModelAdapter, "_schema_attrs", return_value={"x_step"}):
        result = adapter.set_device_attribute("DC Probe", "x_step", "4")

    assert result["code"] == 403, (
        f"a mode-gated refusal came back as {result['code']}; a working "
        f"interlock must not be reported to the operator as a server fault")
    assert result["status"] == "refused"
    assert "autonomous" in result["message"]


def test_a_genuine_fault_is_still_500():
    """The 403 branch must not swallow real errors."""
    model = MagicMock()
    type(model).x_step = property(
        lambda self: "1",
        lambda self, v: (_ for _ in ()).throw(RuntimeError("transport is gone")))

    adapter = _adapter_with(model)
    with patch.object(WebModelAdapter, "_schema_attrs", return_value={"x_step"}):
        result = adapter.set_device_attribute("DC Probe", "x_step", "4")

    assert result["code"] == 500, "a real fault was downgraded to a refusal"
