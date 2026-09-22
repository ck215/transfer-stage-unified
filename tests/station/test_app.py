"""The entry module: view selection, strict CLI, and one launch path.

`pick_view` is the ported `select_view` (tests/architecture/test_invariants.py
carried its four cases); the strict-argparse case is MANAGER-14, where a typo
like `--pyside6` was silently dropped and a lab Mac started a hardware-capable
web server instead of the view the operator asked for.

Nothing here opens a window: the view table is replaced with a recording
stand-in, and the real table is only checked for importability.
"""
import sys
import types

import pytest

from station import app
from station.controller import Controller
from station.events import events
from station.views import theme


class FakeView:
    """What `launch` is contracted to build: View(controller, setup).open()."""

    built = []

    def __init__(self, controller, setup, **kwargs):
        self.controller, self.setup, self.kwargs = controller, setup, kwargs
        self.is_open = False
        FakeView.built.append(self)

    def open(self):
        self.is_open = True


@pytest.fixture(autouse=True)
def isolated_launch(monkeypatch, tmp_path):
    """No exit hooks, no excepthooks and no log file outside the tmp dir."""
    FakeView.built = []
    hooks = []
    monkeypatch.setattr(Controller, "_hook_exit",
                        lambda self: hooks.append(("exit", self)))
    monkeypatch.setattr(events, "hook_exceptions", lambda: hooks.append(("exc",)))
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(theme, "FONT_SIZE", theme.FONT_SIZE)
    yield hooks
    events.close_file()


@pytest.fixture
def fake_views(monkeypatch):
    module = types.ModuleType("station.views.fake")
    module.FakeView = FakeView
    monkeypatch.setitem(sys.modules, "station.views.fake", module)
    monkeypatch.setattr(app, "VIEWS", {
        "tk": ("station.views.fake", "FakeView"),
        "qt": ("station.views.fake", "FakeView"),
        "web": ("station.views.fake", "FakeView"),
    })
    return module


# -- pick_view (was select_view) ------------------------------------------

def test_an_explicit_request_always_wins():
    assert app.pick_view("web", "darwin") == "web"
    assert app.pick_view("qt", "darwin") == "qt"


def test_macos_defaults_to_tkinter():
    """Owner decision D-9: Tkinter is the macOS default until this codebase
    is stabilized."""
    assert app.pick_view(None, "darwin") == "tk"


def test_elsewhere_qt_when_pyside_is_installed_and_web_when_it_is_not():
    assert app.pick_view(None, "linux", pyside_available=True) == "qt"
    assert app.pick_view(None, "linux", pyside_available=False) == "web"


def test_the_old_view_names_still_resolve():
    assert app.pick_view("legacy") == "tk"
    assert app.pick_view("pyside") == "qt"


def test_the_view_table_has_exactly_three_entries():
    assert set(app.VIEWS) == {"tk", "qt", "web"}


def test_every_view_in_the_table_is_importable_and_named_correctly():
    import importlib
    for module_name, attribute in app.VIEWS.values():
        assert hasattr(importlib.import_module(module_name), attribute)


# -- launch ----------------------------------------------------------------

def test_launch_builds_one_controller_hooks_the_exit_and_opens_the_view(
        fake_views, isolated_launch):
    view = app.launch("tk")
    assert isinstance(view, FakeView) and view.is_open
    assert isinstance(view.controller, Controller)
    assert view.setup.controller is view.controller
    assert [kind for kind, *_ in isolated_launch] == ["exit", "exc"]
    assert len(FakeView.built) == 1


def test_launch_opens_the_log_file_and_says_where_it_is(
        fake_views, isolated_launch, tmp_path):
    seen = []
    events.subscribe(seen.append)
    try:
        app.launch("tk")
    finally:
        events.unsubscribe(seen.append)
    paths = [e.message for e in seen if e.title == "Log File"]
    assert paths and str(tmp_path) in paths[0]


def test_port_and_no_browser_reach_the_web_view_only(fake_views):
    web = app.launch("web", port=9001, open_browser=False)
    assert web.kwargs == {"port": 9001, "open_browser": False}
    desktop = app.launch("tk", port=9001, open_browser=False)
    assert desktop.kwargs == {}


def test_launch_applies_the_font_size(fake_views):
    app.launch("tk", font_size=17)
    assert theme.FONT_SIZE == 17


def test_launch_refuses_a_view_that_is_not_in_the_table(fake_views):
    with pytest.raises(ValueError):
        app.launch("curses")


def test_the_other_views_are_never_imported(monkeypatch, fake_views):
    """Lazily, from a 3-entry table: starting Tk must not import PySide6."""
    monkeypatch.setattr(app, "VIEWS", dict(
        app.VIEWS, qt=("station.views.never", "Nope")))
    app.launch("tk")
    assert "station.views.never" not in sys.modules


# -- main ------------------------------------------------------------------

def test_an_unrecognized_flag_is_an_error(fake_views):
    """MANAGER-14: `parse_args`, not `parse_known_args`."""
    with pytest.raises(SystemExit) as exit_code:
        app.main(["--pyside6-typo"])
    assert exit_code.value.code == 2
    assert FakeView.built == []


def test_main_launches_the_requested_view(fake_views, monkeypatch):
    picked = []
    monkeypatch.setattr(app, "launch",
                        lambda name, **kwargs: picked.append((name, kwargs)))
    assert app.main(["--web", "--port", "9100", "--no-browser"]) == 0
    assert picked == [("web", {"port": 9100, "open_browser": False,
                               "font_size": None})]


def test_main_defaults_the_view_from_the_platform(fake_views, monkeypatch):
    picked = []
    monkeypatch.setattr(app, "launch",
                        lambda name, **kwargs: picked.append(name))
    monkeypatch.setattr(app.sys, "platform", "darwin")
    app.main([])
    assert picked == ["tk"]


def test_the_view_flags_are_mutually_exclusive(fake_views):
    with pytest.raises(SystemExit):
        app.main(["--web", "--qt"])


def test_port_and_no_browser_are_refused_for_a_desktop_view(fake_views):
    with pytest.raises(SystemExit) as exit_code:
        app.main(["--tk", "--port", "9100"])
    assert exit_code.value.code == 2


def test_font_size_travels_to_launch(fake_views, monkeypatch):
    picked = []
    monkeypatch.setattr(app, "launch",
                        lambda name, **kwargs: picked.append(kwargs["font_size"]))
    app.main(["--tk", "--font-size", "14"])
    assert picked == [14]
