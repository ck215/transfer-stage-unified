"""TEMP-10, PySide half: the readonly renderer applies the declared role.

Tk already styles `connection_state` by its schema role; PySide rendered every
readonly field in the same flat colour, so the two desktop views disagreed
about a field whose whole job is to be noticed.

**Why these assert the way they do.** The original version of this file built
a `MagicMock(spec=QtDynamicView)`, assigned `view.ROLE_STYLES`, and then never
touched `view` again — it looped over the schema dict and asserted that the
role string was a key of `ROLE_STYLES`. That passes against a view that
renders nothing at all, which is the exact failure the `parallel-stage` skill
records from the previous wave (a test asserting on a `matplotlib` that
`conftest` had replaced with a MagicMock). It proved the *schema*, and was
named as though it proved the *view*.

Building the widget for real needs a live QApplication, which puts the test in
the qt pass where agents cannot run it. So the view half is pinned at the
source level instead — the same technique as the D-1 hide/show pin — and it
runs in the ordinary fast gate.

TEMP-10 does **not** close on this file. Its model half (the no-port branch
never setting `Disconnected`) is Lane 3's and closes separately.
"""
import ast
import inspect
from pathlib import Path

import pytest

from model.temperature_system import TemperatureSystem

_QT = Path(__file__).resolve().parents[2] / "src" / "views" / "pyside" / "view.py"


def _readonly_branch_source():
    """The source of the method that builds readonly value widgets."""
    from views.pyside.view import QtDynamicView
    for name in ("_build_section", "_build", "build_ui", "_build_ui", "__init__"):
        method = getattr(QtDynamicView, name, None)
        if method is None:
            continue
        try:
            src = inspect.getsource(method)
        except (OSError, TypeError):
            continue
        if "readonly" in src and "valueLabel" in src:
            return src
    pytest.fail("could not locate the QtDynamicView method that builds "
                "readonly value widgets; TEMP-10's pin needs re-anchoring")


def test_temp_10_schema_declares_connection_state_with_a_role():
    """The model side of the contract: the field carries a role to style by."""
    schema = TemperatureSystem(port=None).ui_schema
    field = next(
        (el for section in schema["sections"]
         for el in section.get("elements", [])
         if el.get("model_attr") == "connection_state"),
        None)
    assert field is not None, (
        "the temperature schema no longer declares connection_state, so "
        "there is nothing for either desktop view to render")
    assert field.get("type") == "readonly"
    assert field.get("role"), (
        "connection_state has no role, so PySide has nothing to style by and "
        "the staleness indicator renders flat again")


def test_temp_10_pyside_readonly_widgets_are_styled_by_their_role():
    """The view side: the readonly branch must consult the role and apply it.

    Asserted on the source because the alternative needs a QApplication. It
    fails if the role lookup or the stylesheet call is removed — which is the
    regression this pins — rather than passing on a schema key alone.
    """
    src = _readonly_branch_source()
    assert 'get("role"' in src or "get('role'" in src, (
        "the PySide readonly branch no longer reads the element's role, so "
        "connection_state renders in the same flat colour as every other "
        "readonly field and TEMP-10's PySide half has regressed")
    assert "ROLE_STYLES" in src and "setStyleSheet" in src, (
        "the PySide readonly branch reads a role but never applies it; the "
        "role lookup without the setStyleSheet call is dead code")


def test_temp_10_every_declared_role_has_a_style_to_apply():
    """A role the view cannot style is a silent no-op, not an indicator."""
    from views.pyside.view import QtDynamicView

    schema = TemperatureSystem(port=None).ui_schema
    for section in schema["sections"]:
        for el in section.get("elements", []):
            role = el.get("role")
            if el.get("type") == "readonly" and role:
                assert role in QtDynamicView.ROLE_STYLES, (
                    f"schema declares role {role!r} for "
                    f"{el.get('model_attr')!r}, but QtDynamicView.ROLE_STYLES "
                    f"has no entry for it, so the field renders unstyled")
