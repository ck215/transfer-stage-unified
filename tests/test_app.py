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
    monkeypatch.setattr(Controller, "hook_signals",
                        lambda self: hooks.append(("signals", self)))
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


def test_every_platform_defaults_to_qt():
    """Owner ruling 2026-09-28: the unqualified launch is Qt everywhere (it
    was Tk from D-9 amended 2026-09-25). Still one answer on every OS: no UI
    behaviour is platform-specific."""
    for platform in ("darwin", "linux", "win32"):
        for pyside in (True, False, None):
            assert app.pick_view(None, platform, pyside_available=pyside) == "qt"
    assert app.DEFAULT_VIEW == "qt"


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
    assert [kind for kind, *_ in isolated_launch] == ["exit", "exc", "signals", "scan"]
    assert len(FakeView.built) == 1


def test_launch_re_arms_the_signal_handlers_after_the_view_is_built(
        fake_views, isolated_launch):
    """The toolkit may replace the process's handlers when its first window is
    created (Tk 9 on Aqua, SIGTERM). The re-arm must come AFTER the view is
    built and BEFORE anything can move (the scan)."""
    app.launch("tk")
    kinds = [kind for kind, *_ in isolated_launch]
    assert kinds.index("signals") > kinds.index("exit")
    assert kinds.index("signals") < kinds.index("scan")
    assert FakeView.built, "the view was built before the re-arm"


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
    assert picked == ["qt"]


def test_sample_db_sets_the_sample_maps_store(fake_views, monkeypatch, tmp_path):
    """flake-coords section 10 item 5: `--sample-db PATH`, like `--map-db`."""
    monkeypatch.setattr(app, "launch", lambda name, **kwargs: None)
    monkeypatch.delenv("STATION_SAMPLE_DB", raising=False)
    target = tmp_path / "s.sqlite"
    app.main(["--web", "--sample-db", str(target)])
    import os
    assert os.environ["STATION_SAMPLE_DB"] == str(target)


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


# -- the signal handlers survive the toolkit (packaging smoke, 2026-09-25) ----

import os as _os
SRC = _os.path.dirname(_os.path.abspath(app.__file__))

_TK_SIGTERM_CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
import app
from controller.setup import Setup
Setup.start = lambda self: None          # never scan real ports from a test
app.launch("tk")
"""


@pytest.mark.window
@pytest.mark.skipif(sys.platform != "darwin" and not _os.environ.get("DISPLAY"),
                    reason="needs a display for a real Tk window")
def test_a_sigterm_under_tk_still_runs_the_close_path(tmp_path):
    """Tk 9 on Aqua installs its own C-level SIGTERM handler when the first
    window is created, replacing the Controller's. A SIGTERM then ended the
    process with exit 1, past `close()` and past atexit: every model live,
    every port open (found by the packaging smoke test). `launch()` now puts
    the Controller's handlers back after the view is built, so the process
    dies BY the signal (-15) after `close()` ran."""
    import signal
    import subprocess
    import time
    env = dict(_os.environ, TRANSFER_STAGE_DATA_ROOT=str(tmp_path))
    child = subprocess.Popen(
        [sys.executable, "-c", _TK_SIGTERM_CHILD, str(SRC),
         "-ApplePersistenceIgnoreState", "YES"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(6.0)                       # the window and its mainloop are up
        child.send_signal(signal.SIGTERM)
        out, _ = child.communicate(timeout=20)
    finally:
        if child.poll() is None:
            child.kill()
    assert child.returncode == -signal.SIGTERM, \
        f"exit {child.returncode}: the toolkit's handler won\\n{out[-2000:]}"


# -- rb-restart R3: restart_process ------------------------------------------

@pytest.fixture
def exec_calls(monkeypatch):
    """`os.execv`, `os.chdir`, `subprocess.Popen` and `os._exit` recorded:
    nothing here ever replaces or ends the test process."""
    calls = []
    monkeypatch.setattr(app.os, "execv", lambda path, argv: calls.append(
        ("execv", path, list(argv))))
    monkeypatch.setattr(app.os, "chdir", lambda path: calls.append(("chdir", path)))
    monkeypatch.setattr(app.os, "_exit", lambda code: calls.append(("_exit", code)))
    monkeypatch.setattr(app.subprocess, "Popen", lambda argv, **kw: calls.append(
        ("Popen", list(argv), kw.get("cwd"))))
    monkeypatch.setattr(app.sys, "platform", "darwin")
    return calls


def test_restart_process_re_executes_the_same_interpreter_and_argv_from_the_root(
        monkeypatch, tmp_path, request):
    script = tmp_path / "app.py"
    script.write_text("")
    monkeypatch.chdir(tmp_path)          # the real chdir, before it is recorded
    exec_calls = request.getfixturevalue("exec_calls")
    monkeypatch.setattr(app.sys, "argv", ["app.py", "--qt", "--font-size", "14"])
    app.restart_process()
    assert exec_calls == [
        ("chdir", app.CHECKOUT_ROOT),
        # The script is made absolute before the working directory moves.
        ("execv", sys.executable, [sys.executable, str(script), "--qt",
                                   "--font-size", "14"])]


def test_restart_process_adds_the_extra_flags(exec_calls, monkeypatch):
    monkeypatch.setattr(app.sys, "argv", ["/abs/src/app.py", "--web"])
    app.restart_process(extra_args=("--no-browser",))
    assert exec_calls[-1] == ("execv", sys.executable,
                              [sys.executable, "/abs/src/app.py", "--web", "--no-browser"])


def test_a_frozen_bundle_re_executes_its_own_launcher_where_it_stands(
        exec_calls, monkeypatch):
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app.sys, "argv", ["/Apps/station-web", "--port", "8100"])
    app.restart_process()
    assert exec_calls == [("execv", sys.executable,
                           [sys.executable, "--port", "8100"])]


def test_on_windows_the_restart_starts_a_new_process_and_ends_this_one(
        exec_calls, monkeypatch):
    """The one platform branch, and it is here: `execv` on Windows leaves
    the console to the parent shell."""
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(app.sys, "argv", ["C:/station/src/app.py", "--tk"])
    app.restart_process()
    assert exec_calls == [
        ("chdir", app.CHECKOUT_ROOT),
        ("Popen", [sys.executable, "C:/station/src/app.py", "--tk"], app.CHECKOUT_ROOT),
        ("_exit", 0)]


def test_the_log_is_closed_before_the_exec(exec_calls, monkeypatch):
    order = []
    monkeypatch.setattr(events, "close_file", lambda: order.append("closed"))
    monkeypatch.setattr(app.os, "execv", lambda path, argv: order.append("exec"))
    monkeypatch.setattr(app.sys, "argv", ["/abs/src/app.py", "--tk"])
    app.restart_process()
    assert order == ["closed", "exec"]


def test_a_failed_exec_reopens_the_log_and_raises(exec_calls, monkeypatch):
    opened = []
    monkeypatch.setattr(events, "open_file", lambda *a: opened.append(True) or "x")

    def broken(path, argv):
        raise OSError("exec format error")
    monkeypatch.setattr(app.os, "execv", broken)
    monkeypatch.setattr(app.sys, "argv", ["/abs/src/app.py", "--tk"])
    with pytest.raises(OSError):
        app.restart_process()
    assert opened


def test_a_delayed_restart_runs_on_its_own_thread(exec_calls, monkeypatch):
    """The Web's answer to Restart must reach the page before the process
    goes: `delay` puts the exec on a thread and returns at once."""
    monkeypatch.setattr(app.sys, "argv", ["/abs/src/app.py", "--web"])
    thread = app.restart_process(delay=0.05)
    assert thread is not None and thread.daemon
    thread.join(5.0)
    assert exec_calls[-1][0] == "execv"


def test_a_delayed_restart_that_fails_is_an_error_event(exec_calls, monkeypatch):
    seen = []
    monkeypatch.setattr(events, "open_file", lambda *a: "x")

    def broken(path, argv):
        raise OSError("no such file")
    monkeypatch.setattr(app.os, "execv", broken)
    monkeypatch.setattr(app.sys, "argv", ["/abs/src/app.py", "--web"])
    events.subscribe(seen.append)
    try:
        app.restart_process(delay=0.01).join(5.0)
    finally:
        events.unsubscribe(seen.append)
    assert [e for e in seen if e.title == "Restart Failed" and e.severity == "error"]


def test_launch_hands_setup_a_restart_that_keeps_the_web_port(
        fake_views, monkeypatch):
    asked = []
    monkeypatch.setattr(app, "restart_process",
                        lambda args=None, extra_args=(), delay=0.0: asked.append(
                            (args, tuple(extra_args), delay)))
    monkeypatch.setattr(app.sys, "argv", ["/abs/src/app.py", "--web", "--port",
                                          "8100", "--no-browser"])
    web = app.launch("web", port=8100, open_browser=True)
    web.setup._restart()
    args, extra, delay = asked[-1]
    # The page reloads itself: no second browser tab, the same port, and the
    # flags are replaced rather than piled up restart after restart.
    assert args == ["--web"]
    assert extra == ("--no-browser", "--port", "8100")
    assert delay == app.RESTART_DELAY > 0
    desktop = app.launch("tk")
    desktop.setup._restart()
    assert asked[-1] == (None, (), app.RESTART_DELAY)


def test_the_web_restart_flags_replace_the_old_ones():
    flags = ["--web", "--port", "8080", "--no-browser", "--port=9000",
             "--font-size", "12"]
    assert app.web_restart_flags(flags) == ["--web", "--font-size", "12"]


def test_restart_process_takes_the_flags_it_is_given(exec_calls, monkeypatch):
    monkeypatch.setattr(app.sys, "argv", ["/abs/src/app.py", "--web", "--port", "1"])
    app.restart_process(args=["--web"], extra_args=("--port", "8100"))
    assert exec_calls[-1] == ("execv", sys.executable,
                              [sys.executable, "/abs/src/app.py", "--web",
                               "--port", "8100"])


# -- brief-bundle-update follow-up 3: the Windows swap on restart ------------

@pytest.fixture
def frozen_install(tmp_path, monkeypatch, exec_calls):
    """A frozen launcher in tmp_path/station with an update staged beside it."""
    install = tmp_path / "station"
    staged = tmp_path / "station.next"
    for folder, tag in ((install, "v1.2.0"), (staged, "v1.3.0")):
        folder.mkdir()
        (folder / "VERSION").write_text(f"{tag}\nsha\n2026-09-28T00:00:00Z\n")
    (install / "UPDATE_PENDING").write_text(str(install.resolve()) + "\n")
    launcher = install / "station-tk.exe"
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app.sys, "executable", str(launcher))
    monkeypatch.setattr(app.sys, "argv", [str(launcher), "--tk"])
    return install


def test_windows_with_an_update_pending_hands_the_swap_to_a_script_and_exits(
        frozen_install, exec_calls, monkeypatch):
    monkeypatch.setattr(app.sys, "platform", "win32")
    app.restart_process()
    script = frozen_install.parent / "station-update.cmd"
    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert f'PID eq {_os.getpid()}' in text
    assert f'ren "{frozen_install.resolve()}.next" "station"' in text
    kinds = [c[0] for c in exec_calls]
    assert kinds == ["Popen", "_exit"]
    assert exec_calls[0][1] == ["cmd", "/c", str(script)]
    assert exec_calls[1] == ("_exit", 0)
    # the restart itself swaps nothing: the script does, after this exits
    assert (frozen_install / "VERSION").read_text().startswith("v1.2.0")


def test_windows_without_a_pending_update_restarts_as_before(
        frozen_install, exec_calls, monkeypatch):
    (frozen_install / "UPDATE_PENDING").unlink()
    monkeypatch.setattr(app.sys, "platform", "win32")
    app.restart_process()
    assert exec_calls == [("Popen", [app.sys.executable, "--tk"], None), ("_exit", 0)]
    assert not (frozen_install.parent / "station-update.cmd").exists()


def test_elsewhere_a_pending_update_is_swapped_in_before_the_exec(
        frozen_install, exec_calls):
    app.restart_process()                       # sys.platform is "darwin" here
    assert (frozen_install / "VERSION").read_text().startswith("v1.3.0")
    assert exec_calls == [("execv", app.sys.executable, [app.sys.executable, "--tk"])]
    assert not (frozen_install.parent / "station-update.cmd").exists()


# -- A2: the startup checks wait for the view to listen ----------------------

class ListeningView(FakeView):
    """A desktop view: `open()` subscribes to the event log (as
    `Dashboard.open` does), then runs its loop."""

    order = []

    def open(self):
        ListeningView.order.append(("subscribing", self.setup._startup_listening))
        events.subscribe(self._on_event)
        ListeningView.order.append(("subscribed", self.setup._startup_listening))
        events.unsubscribe(self._on_event)

    def _on_event(self, event):
        pass


@pytest.fixture
def listening_views(monkeypatch):
    module = types.ModuleType("views.listening")
    module.ListeningView = ListeningView
    ListeningView.order = []
    monkeypatch.setitem(sys.modules, "views.listening", module)
    monkeypatch.setattr(app, "VIEWS", {
        "tk": ("views.listening", "ListeningView"),
        "qt": ("views.listening", "ListeningView"),
        "web": ("views.listening", "ListeningView"),
    })


@pytest.mark.parametrize("view", ["tk", "qt"])
def test_a_desktop_views_subscription_starts_the_startup_offer(listening_views, view):
    """OP-4: the offer reaches a view only once it has subscribed; the app
    tells Setup at that moment, never before."""
    app.launch(view)
    assert ListeningView.order == [("subscribing", False), ("subscribed", True)]
    assert "subscribe" not in vars(events), "the one-shot hook is gone"


def test_the_web_offers_at_the_pages_first_read(listening_views, monkeypatch):
    calls = []
    monkeypatch.setattr(app.Setup, "startup_checks",
                        lambda self, on_next_read=False: calls.append(on_next_read))
    app.launch("web")
    assert calls == [True]
    assert "subscribe" not in vars(events)


def test_a_view_that_never_subscribes_leaves_the_event_log_as_it_was(fake_views):
    app.launch("tk")
    assert "subscribe" not in vars(events)


# -- A4: Switch to stable ends the station through the app -------------------

def test_launch_hands_setup_the_way_to_end_the_station(fake_views):
    app.launch("tk")
    assert FakeView.built[0].setup._exit_app is app.exit_process


def test_exit_process_closes_the_log_then_exits(monkeypatch):
    order = []
    monkeypatch.setattr(events, "close_file", lambda: order.append("closed"))
    monkeypatch.setattr(app.os, "_exit", lambda code: order.append(("_exit", code)))
    app.exit_process()
    assert order == ["closed", ("_exit", 0)]


# -- A5: an update staged for the restart is swapped in at the next start -----

def test_a_pending_update_is_swapped_in_before_anything_opens(
        frozen_install, exec_calls, fake_views):
    """The operator quit instead of pressing Restart: the next start swaps
    the staged version in and runs it, before any view, model or log opens."""
    assert app.main(["--tk"]) == 0
    assert FakeView.built == [], "nothing opened on the old version"
    assert (frozen_install / "VERSION").read_text().startswith("v1.3.0")
    assert not (frozen_install / "UPDATE_PENDING").exists()
    assert exec_calls == [("execv", app.sys.executable, [app.sys.executable, "--tk"])]


def test_on_windows_the_startup_swap_goes_to_the_script(
        frozen_install, exec_calls, fake_views, monkeypatch):
    monkeypatch.setattr(app.sys, "platform", "win32")
    assert app.main(["--tk"]) == 0
    assert FakeView.built == []
    assert [c[0] for c in exec_calls] == ["Popen", "_exit"]
    assert (frozen_install.parent / "station-update.cmd").exists()


def test_without_a_pending_update_the_start_is_as_before(
        frozen_install, exec_calls, fake_views):
    (frozen_install / "UPDATE_PENDING").unlink()
    assert app.main(["--tk"]) == 0
    assert exec_calls == [] and len(FakeView.built) == 1


def test_a_checkout_never_looks_for_a_pending_update(exec_calls, fake_views, monkeypatch):
    monkeypatch.setattr(app.updater, "pending_update",
                        lambda install: pytest.fail("looked in a checkout"))
    assert app.main(["--tk"]) == 0
    assert len(FakeView.built) == 1
