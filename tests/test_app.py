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

import app
from controller.controller import Controller
from events import events
from views import theme


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
    """No exit hooks, no excepthooks, no log file outside the tmp dir - and no
    real hardware scan: `launch()` starts one, and a test must never open a
    port."""
    FakeView.built = []
    hooks = []
    monkeypatch.setattr(Controller, "_hook_exit",
                        lambda self: hooks.append(("exit", self)))
    monkeypatch.setattr(events, "hook_exceptions", lambda: hooks.append(("exc",)))
    monkeypatch.setattr(app.Setup, "start",
                        lambda self: hooks.append(("scan", self)))
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(theme, "FONT_SIZE", theme.FONT_SIZE)
    yield hooks
    events.close_file()


@pytest.fixture
def fake_views(monkeypatch):
    module = types.ModuleType("views.fake")
    module.FakeView = FakeView
    monkeypatch.setitem(sys.modules, "views.fake", module)
    monkeypatch.setattr(app, "VIEWS", {
        "tk": ("views.fake", "FakeView"),
        "qt": ("views.fake", "FakeView"),
        "web": ("views.fake", "FakeView"),
    })
    return module


# -- pick_view (was select_view) ------------------------------------------

def test_an_explicit_request_always_wins():
    assert app.pick_view("web", "darwin") == "web"
    assert app.pick_view("qt", "darwin") == "qt"


def test_every_platform_defaults_to_tkinter():
    """Owner decision D-9, amended 2026-09-25: the unqualified launch is Tk
    everywhere. It was Tk on macOS and Qt-or-Web elsewhere, which made the
    first screen depend on the OS (audit P8) against the ruling that no UI
    behaviour is platform-specific."""
    for platform in ("darwin", "linux", "win32"):
        for pyside in (True, False, None):
            assert app.pick_view(None, platform, pyside_available=pyside) == "tk"
    assert app.DEFAULT_VIEW == "tk"


def test_the_packaged_entry_points_fix_the_view_and_forward_the_flags(
        fake_views, monkeypatch):
    """PACKAGING_PLAN P2: `station-web` can start nothing but the Web view."""
    picked = []
    monkeypatch.setattr(app, "launch",
                        lambda name, **kwargs: picked.append((name, kwargs)))
    assert app.main_tk([]) == 0
    assert app.main_qt(["--font-size", "14"]) == 0
    assert app.main_web(["--port", "8090", "--no-browser"]) == 0
    assert [p[0] for p in picked] == ["tk", "qt", "web"]
    assert picked[1][1]["font_size"] == 14
    assert picked[2][1]["port"] == 8090 and picked[2][1]["open_browser"] is False
    with pytest.raises(SystemExit):          # a second view flag is refused
        app.main_web(["--qt"])


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
    assert [kind for kind, *_ in isolated_launch] == ["exit", "exc", "scan"]
    assert len(FakeView.built) == 1


def test_launch_starts_the_hardware_scan_before_the_view_opens(
        fake_views, monkeypatch):
    """Addendum 2: Setup scans automatically at start. It runs on its own
    thread and the view polls `setup.state`, so the window is never waiting
    on the handshake budget."""
    order = []
    monkeypatch.setattr(app.Setup, "start", lambda self: order.append("scan"))
    monkeypatch.setattr(FakeView, "open", lambda self: order.append("open"))
    app.launch("tk")
    assert order == ["scan", "open"]


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
        app.VIEWS, qt=("views.never", "Nope")))
    app.launch("tk")
    assert "views.never" not in sys.modules


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
    monkeypatch.setattr(app.sys, "platform", "linux")
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
