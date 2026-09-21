"""REDPERCENT-13 - the model-side half.

The Web live plotter mixed `current_red` and `red_change` into one series,
sampled while idle, and its baseline/reset were client-only so they never
reached the model. The exact-attribute-match half was already fixed
(`test_web_7_plotter.py`, WEB-7): reading `pollState` at HEAD shows
`attr === 'current_red'`, not `attr.toLowerCase().includes('red')`.

Two defects from the same audit entry were still live:

  1. `pollState` pushed a plotter sample on every poll regardless of whether
     a run was active, because the Web client had no way to know -
     `WebModelAdapter.get_state` (`views/web/web_adapter.py`, outside this
     file's write set, read only to confirm this) only ever publishes what a
     model's own `ui_schema` declares with a `model_attr`, and `monitoring`
     was not declared anywhere.
  2. The plotter modal's "Reset" button only ever touched client-side state,
     never the model's own `reset_baseline`.

This file pins the model side of (1): `monitoring` must be a `readonly`
schema element so `/api/state` can publish it. (2), and the Web-side
consumption of (1), are `app.js` changes with no Python-visible surface;
they are verified separately by a Node harness
(`tests/web/test_redpercent13_web_plotter.py`), since app.js has no test
runner in this repo.
"""
import pytest

from model.redpercent_system import RedPercentSystem

pytestmark = pytest.mark.redpercent


def _find(schema, model_attr):
    for section in schema.get("sections", []):
        for el in section["elements"]:
            if el.get("model_attr") == model_attr:
                return el
    return None


def test_monitoring_is_published_as_a_readonly_schema_attr():
    """Without this, `/api/state` has no way to know a run is active, so
    the Web client cannot gate the plotter on it."""
    sysm = RedPercentSystem()
    el = _find(sysm.ui_schema, "monitoring")
    assert el is not None, (
        "ui_schema must expose 'monitoring' so /api/state can publish it")
    assert el["type"] == "readonly"
    assert el["writable"] is False

    # And it actually reflects the model's own `monitoring` property.
    assert getattr(sysm, "monitoring") is False
    sysm._run = None
    sysm.monitoring = True
    assert getattr(sysm, "monitoring") is True
