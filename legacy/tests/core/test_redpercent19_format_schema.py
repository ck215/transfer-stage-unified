"""REDPERCENT-19 - the model half only.

Audit: raw floats reach the operator (`12.3456789012`, `-0.0`) where a
formatted number belongs, in PySide and Web.

Reading `views/pyside/view.py::_display` and `views/tkinter/view.py::_display`
(outside this file's write set, read only to confirm this) shows both
already render readonly numerics through `Param.format` (declared precision
via each parameter's `decimals`) - so that half of the audit's claim no
longer holds for Tk/PySide. The Web client's `pollState`
(`static/js/app.js`) still writes `String(val)` straight off `/api/state`
with no formatting step at all, and per-view rendering code for that is
explicitly out of scope for this finding (see the brief / handoff).

This pins the model-side declaration the audit's second proposed direction
asks for: `current_red`/`red_change` declare a `format` in `ui_schema`. No
renderer reads it yet (confirmed by grep across app.js, tkinter/view.py,
pyside/view.py: none references `element.get("format")` /
`el.format`/`.format` in that sense) - this closes REDPERCENT-19 **partly**;
see the handoff for the reasoning.
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


def test_current_red_and_red_change_declare_a_display_format():
    sysm = RedPercentSystem()
    schema = sysm.ui_schema

    current_red_el = _find(schema, "current_red")
    red_change_el = _find(schema, "red_change")
    assert current_red_el is not None and red_change_el is not None
    assert current_red_el.get("format"), (
        "current_red should declare a display format (REDPERCENT-19)")
    assert red_change_el.get("format"), (
        "red_change should declare a display format (REDPERCENT-19)")
