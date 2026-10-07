"""The only entry module: pick a view, build the one Controller, hand both
to the view.

Replaces `app.py` (948 lines, three ~390-line launchers each nesting its own
setup wizard), `app_bootstrap.py`, `lifecycle.py` and `model/devices.py`.
What is left is a table of three views and one `launch()`, because everything
the three launchers used to duplicate now lives in exactly one place:

    the wizard        -> `Setup`, a Panel every view renders from its schema
    the model list    -> `setup.MODEL_TYPES`, derived from the classes
    exit handling     -> `Controller._hook_exit()`, re-armed after the view
                         is built by `Controller.hook_signals()`
    exception hooks   -> `events.hook_exceptions()`
    cross-model wiring-> `Controller.add()` announces each model to the others

One Controller per process, created here and never replaced: a re-setup calls
`Controller.reset()` rather than swapping the object, so nothing can end up
stopping a manager that has already been replaced (which is what
`lifecycle.current_manager` existed to work around).
"""
import argparse
import os
import atexit
import importlib
import subprocess
import sys
import threading
import time

from controller import updater
from controller.controller import Controller
from events import events
from controller.setup import Setup
from views import theme

#: view name -> (module, attribute). Imported lazily: starting the Tk view
#: must not import PySide6, and none of the three may import the other two.
VIEWS = {
    "tk": ("views.tk", "TkDashboard"),
    "qt": ("views.qt", "QtDashboard"),
    "web": ("views.web.server", "WebView"),
}

#: The old spellings, still accepted. `--view legacy` and `--pyside` are what
#: `run.sh`, the lab notes and three years of muscle memory say.
ALIASES = {"legacy": "tk", "tkinter": "tk", "pyside": "qt", "pyside6": "qt"}

DEFAULT_PORT = 8080

#: The checkout this file runs from: a restart re-executes from here, so a
#: relative path in argv or a data default means what it meant at launch.
CHECKOUT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Seconds between the answer to Restart and the exec: long enough for the
#: Web's answer to reach the page, short enough to feel immediate.
RESTART_DELAY = 0.5


#: Owner decision D-9, amended 2026-09-25: an unqualified launch opens the
#: Tkinter view on EVERY platform ("simple and lightweight and local"). It
#: used to be Tk on macOS and Qt-or-Web elsewhere, which made the operator's
#: first screen depend on the OS (audit P8) against the no-platform-specific-UI
#: ruling. `--web` / `--qt` / `--tk` still choose explicitly.
DEFAULT_VIEW = "qt"      # owner ruling 2026-09-28: Qt is the default view (was Tk, D-9 amended 2026-09-25)


def pick_view(requested, platform=None, pyside_available=None):
    """Resolve the view to launch. Pure, so the default is testable.
    was <app>.select_view ('select' is reserved for operator selections)

    `requested` is the parsed --view value, or None for "no flag given".
    `platform` and `pyside_available` are accepted for the callers and tests
    that pass them; neither changes the answer any more.
    """
    if requested is not None:
        return ALIASES.get(requested, requested)
    return DEFAULT_VIEW


def restart_argv(args=None, extra_args=()):
    """The command line a restart runs: this interpreter, this script (made
    absolute, since the restart runs from `CHECKOUT_ROOT`), `args` (default:
    this run's flags) and `extra_args`. A frozen bundle's `sys.executable`
    IS its launcher, so it is re-executed on its own."""
    flags = list(sys.argv[1:] if args is None else args) + list(extra_args)
    if getattr(sys, "frozen", False):
        return [sys.executable, *flags]
    script = sys.argv[0] if sys.argv else ""
    if script and os.path.exists(script):
        script = os.path.abspath(script)
    return [sys.executable, script, *flags]


def web_restart_flags(flags):
    """`flags` without `--port` and `--no-browser`: the Web restart puts back
    the port it is serving on and `--no-browser` (the page reloads itself),
    so a flag is replaced, never piled up restart after restart."""
    kept, skip = [], False
    for flag in flags:
        if skip:
            skip = False
            continue
        if flag == "--port":
            skip = True
            continue
        if flag == "--no-browser" or flag.startswith("--port="):
            continue
        kept.append(flag)
    return kept


def restart_process(args=None, extra_args=(), delay=0.0):
    """Replace this process with a fresh run of the same command line
    (rb-restart R3): `restart_argv`, from `CHECKOUT_ROOT`. The caller has
    closed every model; this closes the log file (an exec runs no exit path)
    and reopens it if the exec fails.

    With `delay`, the exec runs on a daemon thread after that many seconds
    and the thread is returned at once (a failure there is an error event);
    without, it runs here and a failure raises.

    The one platform branch for a restart lives here and nowhere else: on
    Windows `execv` hands the console back to the parent shell while the new
    process runs detached, so the new run is started and this one ends."""
    if delay:
        def later():
            time.sleep(delay)
            try:
                restart_process(args, extra_args)
            except Exception as exc:
                events.error("Restart Failed", f"The station did not restart "
                             f"({exc}). Quit and start it again by hand.",
                             source="app", exception=exc)
        thread = threading.Thread(target=later, name="station-restart", daemon=True)
        thread.start()
        return thread
    argv = restart_argv(args, extra_args)
    events.info("Restart", " ".join(argv), source="app")
    # A bundle's update that could not swap while it ran (Windows locks a
    # running launcher's folder) waits in <install>.next for this restart.
    frozen = getattr(sys, "frozen", False)
    pending = updater.pending_update(os.path.dirname(sys.executable)) if frozen else None
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    events.close_file()
    try:
        if not frozen:
            os.chdir(CHECKOUT_ROOT)
        if sys.platform == "win32":
            if pending is not None:
                # The swap cannot run in this process: a script beside the
                # install waits for it to exit, swaps, starts the new launcher.
                script = os.path.join(os.path.dirname(pending), updater.SWAP_SCRIPT)
                with open(script, "w", encoding="utf-8", newline="") as f:
                    f.write(updater.swap_script(pending, os.getpid(), argv))
                subprocess.Popen(["cmd", "/c", script], cwd=os.path.dirname(pending),
                                 creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            else:
                subprocess.Popen(argv, cwd=CHECKOUT_ROOT if not frozen else None)
            # os._exit, not sys.exit: a delayed restart runs on a daemon
            # thread, where sys.exit would end only that thread. The models
            # are closed and the log flushed before this is ever called.
            os._exit(0)
        else:
            if pending is not None:
                refusal = updater.finish_pending(pending)
                if refusal:
                    events.warn("Update Not Installed", refusal, source="app")
            os.execv(sys.executable, argv)
    except OSError:
        events.open_file()
        raise
    return None


def _swap_in_pending_update():
    """True when a pending update was handed to `restart_process`: this
    start has then been replaced (or, where `execv` returns in a test,
    must not go on). A checkout never has one."""
    if not getattr(sys, "frozen", False):
        return False
    if updater.pending_update(os.path.dirname(sys.executable)) is None:
        return False
    restart_process()
    return True


def exit_process():
    """End the station now: Setup's Switch to stable has closed every model
    and started the stable app. `os._exit`, not `sys.exit`: it is called from
    Setup's worker thread, where `sys.exit` would end only that thread."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    events.close_file()
    os._exit(0)


def launch(view_name, port=DEFAULT_PORT, open_browser=True, font_size=None):
    """Build the one Controller and the one Setup, then open the view.
    was <app>.run_legacy_app, <app>.run_pyside_app, <app>.run_web_app

    The three launchers this replaces each had their own copy of the setup
    wizard, their own exit handling (Tk had none until VIEW-TKINTER-8) and
    their own exception hooks (only the Web one covered threads).
    """
    view_name = ALIASES.get(view_name, view_name)
    if view_name not in VIEWS:
        raise ValueError(f"{view_name!r} is not a view: "
                         f"{', '.join(sorted(VIEWS))}")

    # SDL must be initialised on the MAIN thread, before any view or request
    # thread builds a model: initialised anywhere else it traps the process
    # (SIGTRAP) at exit on macOS. Its teardown is registered FIRST so that
    # atexit (last in, first out) runs it after the Controller has closed
    # every model and stopped every poll loop; quitting SDL under a live
    # poller is a bus error.
    from devices.gamepad import hub
    hub.open()
    atexit.register(hub.close)

    controller = Controller()
    controller._hook_exit()
    events.hook_exceptions()
    path = events.open_file()
    events.info("Log File", path, source="app")
    events.debug("Launch", f"view={view_name} port={port} "
                 f"open_browser={open_browser} font_size={font_size} "
                 f"platform={sys.platform} python={sys.version.split()[0]}",
                 source="app")

    if font_size is not None:
        theme.set_font_size(font_size)

    built = {}

    def restart():
        """Setup's Restart (rb-restart R3): the same view and flags. The Web
        keeps the port it is serving on (it may have walked up from
        `port`), and opens no second tab: the page reloads itself."""
        if view_name != "web":
            return restart_process(delay=RESTART_DELAY)
        serving = getattr(built.get("view"), "port", None) or port
        return restart_process(args=web_restart_flags(sys.argv[1:]),
                               extra_args=("--no-browser", "--port", str(serving)),
                               delay=RESTART_DELAY)

    setup = Setup(controller, restart=restart, exit_app=exit_process)
    module_name, attribute = VIEWS[view_name]
    view_class = getattr(importlib.import_module(module_name), attribute)
    # --port / --no-browser are the Web view's alone; the desktop views take
    # the controller and the setup panel and nothing else.
    ensure_application = getattr(view_class, "ensure_application", None)
    if ensure_application is not None:      # Qt: one QApplication, main thread
        ensure_application()
    view = (view_class(controller, setup, port=port, open_browser=open_browser)
            if view_name == "web" else view_class(controller, setup))
    built["view"] = view
    # A toolkit may have replaced the process's signal handlers while the
    # view was built (Tk 9 on Aqua does, for SIGTERM): put the Controller's
    # back, so a SIGTERM still runs close() before the process ends.
    controller.hook_signals()
    # The hardware scan starts BEFORE the window does (Addendum 2): the
    # operator finds it already running instead of being shown a Scan button
    # and asked to press it. It runs on its own thread and the view polls
    # `setup.state`, so this never delays the window by the handshake budget.
    setup.start()
    events.info("View", f"{view_name} starting", source="app")
    # A2 (OP-4): Setup's startup dialogs go out once the view listens, not
    # at construction. A desktop view subscribes to the event log inside
    # open(), just before its loop: the first subscription is the moment.
    # The Web page subscribes by polling; Setup offers at its first read.
    listening = None
    if view_name != "web":
        listening = _after_first_subscriber(events, setup.startup_checks)
    try:
        view.open()
        if view_name == "web":
            setup.startup_checks(on_next_read=True)
        # A desktop view's open() runs its event loop and returns at close;
        # the Web view serves on a thread and waits here for the same reason.
        wait = getattr(view, "wait", None)
        if wait is not None:
            wait()
    finally:
        if listening is not None:
            listening()
    return view


def _after_first_subscriber(log, then):
    """Run `then()` once, right after the first `log.subscribe(fn)` - the
    moment a view starts to listen. The hook is on this one instance and
    goes away at the first subscription; the returned function removes it
    if nothing ever subscribed (a view that failed to open)."""
    original = log.subscribe

    def remove():
        if vars(log).get("subscribe") is subscribe:
            del log.subscribe

    def subscribe(fn):
        remove()
        original(fn)
        try:
            then()
        except Exception as exc:        # never fail the view's own open()
            events.debug("Startup Checks Failed", repr(exc), source="app",
                         exception=exc)

    log.subscribe = subscribe
    return remove


def main(argv=None):
    """was <app>.main"""
    parser = argparse.ArgumentParser(
        prog="station",
        description="Unified Stage Control Application",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Views:
  tk        Tkinter interface
  qt        Native Qt desktop GUI (PySide6): the default on every platform (owner ruling 2026-09-28)
  web       Browser-based dashboard, served on localhost

Examples:
  python3 src/app.py --web --port 8080 --no-browser
  python3 src/app.py --qt --font-size 14
""")
    views = parser.add_mutually_exclusive_group()
    views.add_argument("--view", choices=sorted(set(VIEWS) | set(ALIASES)),
                       help="which view to launch")
    views.add_argument("--tk", action="store_const", dest="view", const="tk",
                       help="launch the Tkinter view")
    views.add_argument("--tkinter", action="store_const", dest="view",
                       const="tk", help=argparse.SUPPRESS)
    views.add_argument("--qt", action="store_const", dest="view", const="qt",
                       help="launch the PySide6 view")
    views.add_argument("--pyside", action="store_const", dest="view",
                       const="qt", help=argparse.SUPPRESS)
    views.add_argument("--web", action="store_const", dest="view", const="web",
                       help="launch the web dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"web dashboard port (default: {DEFAULT_PORT})")
    parser.add_argument("--no-motion", action="store_true",
                        help="disable the one motion the views have (the latch pulse); same as STATION_NO_MOTION=1")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser for the web dashboard")
    parser.add_argument("--font-size", type=int,
                        help="base font size, in points (8-28)")
    parser.add_argument("--map-db", metavar="PATH",
                        help="the Transfer Map's SQLite file, overriding the store the "
                             "operator chose (same as STATION_MAP_DB)")
    parser.add_argument("--sample-db", metavar="PATH",
                        help="the Sample Map's SQLite file (default: data/sample_map.sqlite "
                             "in this checkout; same as STATION_SAMPLE_DB)")

    # parse_args, not parse_known_args: an unrecognized flag must be an error.
    # Under parse_known_args a typo like `--pyside6` was silently dropped and
    # the platform default took over - on a lab Mac that started a
    # hardware-capable web server instead of the view the operator asked for
    # (MANAGER-14).
    args = parser.parse_args(argv)
    # A5: an update staged for the restart (Windows locks a running
    # install, so it waits in <install>.next) is swapped in now, before any
    # view, model or log file opens - also when the operator quit instead
    # of pressing Restart. The swap re-executes the new version (or, on
    # Windows, hands over to the swap script) and this start ends here.
    if _swap_in_pending_update():
        return 0
    if args.no_motion:
        os.environ["STATION_NO_MOTION"] = "1"
    if args.map_db:
        os.environ["STATION_MAP_DB"] = args.map_db
    if args.sample_db:
        os.environ["STATION_SAMPLE_DB"] = args.sample_db

    view_name = pick_view(args.view)
    if args.view is None:
        events.info("View", f"no view requested; defaulting to {view_name}",
                    source="app")
    if view_name != "web" and (args.port != DEFAULT_PORT or args.no_browser):
        parser.error("--port and --no-browser apply to the web view only")

    launch(view_name, port=args.port, open_browser=not args.no_browser,
           font_size=args.font_size)
    return 0


# -- packaged entry points (PACKAGING_PLAN P2) ------------------------------
# One function per view for `[project.scripts]` and the PyInstaller EXEs, so
# a bundle's `station-web` cannot start anything but the Web view. Each one
# forwards the remaining command-line flags (--port, --no-browser,
# --font-size, --no-motion) and refuses a second view flag the way `main`
# does.

def main_tk(argv=None):
    return main(["--tk", *(sys.argv[1:] if argv is None else argv)])


def main_qt(argv=None):
    return main(["--qt", *(sys.argv[1:] if argv is None else argv)])


def main_web(argv=None):
    return main(["--web", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    sys.exit(main())
