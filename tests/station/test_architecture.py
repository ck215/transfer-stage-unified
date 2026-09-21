"""The hierarchy, enforced. These replace the Protocol and mixin 'contract'
classes: the rules live in the import graph, not in a type."""
import ast
import pathlib

import pytest

STATION = pathlib.Path(__file__).resolve().parents[2] / "station"
FILES = sorted(p for p in STATION.rglob("*.py"))


def _imports(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _rel(path):
    return path.relative_to(STATION).as_posix()


@pytest.mark.parametrize("path", [p for p in FILES if "views/" in _rel(p)], ids=_rel)
def test_views_never_import_a_model_or_device(path):
    bad = [m for m in _imports(path)
           if m.startswith(("station.models", "station.devices", "station.model", "src"))]
    assert not bad, f"{_rel(path)} imports {bad}"


@pytest.mark.parametrize("path", [p for p in FILES if _rel(p).startswith(("models/", "devices/"))
                                  or _rel(p) in ("model.py", "panel.py")], ids=_rel)
def test_backend_never_imports_a_view_or_the_controller(path):
    bad = [m for m in _imports(path) if m.startswith(("station.views", "station.controller"))]
    assert not bad, f"{_rel(path)} imports {bad}"


@pytest.mark.parametrize("library,owner", [("serial", "devices/serial_port.py"),
                                           ("pygame", "devices/gamepad.py"),
                                           ("mss", "devices/screen.py")])
def test_one_owner_per_hardware_library(library, owner):
    users = [_rel(p) for p in FILES
             if any(m == library or m.startswith(library + ".") for m in _imports(p))]
    assert set(users) <= {owner}, f"{library} imported by {users}"


@pytest.mark.parametrize("path", FILES, ids=_rel)
def test_no_print_and_no_get_prefix(path):
    tree = ast.parse(path.read_text())
    prints = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name) and n.func.id == "print"]
    if _rel(path) != "events.py":
        assert not prints, f"print() at lines {prints}: use station.events"
    getters = [n.name for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name.startswith("get_")
               and _rel(path) != "devices/smc100.py"]
    assert not getters, f"get_ prefix: {getters}"
