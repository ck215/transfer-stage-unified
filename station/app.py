"""The only entry module: pick a view, build the one Controller, hand both
to the view.

Replaces `app.py` (948 lines, three ~390-line launchers each nesting its own
setup wizard), `app_bootstrap.py`, `lifecycle.py` and `model/devices.py`.
What is left is a table of three views and one `launch()`, because everything
the three launchers used to duplicate now lives in exactly one place:

    the wizard        -> `Setup`, a Panel every view renders from its schema
    the model list    -> `setup.MODEL_TYPES`, derived from the classes
    exit handling     -> `Controller._hook_exit()`
    exception hooks   -> `events.hook_exceptions()`
    cross-model wiring-> `Controller.add()` announces each model to the others

One Controller per process, created here and never replaced: a re-setup calls
`Controller.reset()` rather than swapping the object, so nothing can end up
stopping a manager that has already been replaced (which is what
`lifecycle.current_manager` existed to work around).
"""
import argparse
import importlib
import sys

from station.controller import Controller
from station.events import events
from station.setup import Setup
from station.views import theme

#: view name -> (module, attribute). Imported lazily: starting the Tk view
#: must not import PySide6, and none of the three may import the other two.
VIEWS = {
    "tk": ("station.views.tk", "TkDashboard"),
    "qt": ("station.views.qt", "QtDashboard"),
    "web": ("station.views.web.server", "WebView"),
}

#: The old spellings, still accepted. `--view legacy` and `--pyside` are what
#: `run.sh`, the lab notes and three years of muscle memory say.
ALIASES = {"legacy": "tk", "tkinter": "tk", "pyside": "qt", "pyside6": "qt"}

DEFAULT_PORT = 8080


def pick_view(requested, platform=None, pyside_available=None):
    """Resolve the view to launch. Pure, so the defaults are testable.
    was <app>.select_view ('select' is reserved for operator selections)

    `requested` is the parsed --view value, or None for "no flag given".
    """
    if requested is not None:
        return ALIASES.get(requested, requested)
    if platform is None:
        platform = sys.platform
    # Owner decision D-9: Tkinter is the macOS default until this codebase is
    # stabilized. The Web view stays available with --web, but it is not what
    # an unqualified launch on a lab Mac should start.
    if platform == "darwin":
        return "tk"
    if pyside_available is None:
        try:
            import PySide6.QtWidgets  # noqa: F401
            pyside_available = True
        except ImportError:
            pyside_available = False
    return "qt" if pyside_available else "web"


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

    setup = Setup(controller)
    module_name, attribute = VIEWS[view_name]
    view_class = getattr(importlib.import_module(module_name), attribute)
    # --port / --no-browser are the Web view's alone; the desktop views take
    # the controller and the setup panel and nothing else.
    view = (view_class(controller, setup, port=port, open_browser=open_browser)
            if view_name == "web" else view_class(controller, setup))
    events.info("View", f"{view_name} starting", source="app")
    view.open()
    return view


def main(argv=None):
    """was <app>.main"""
    parser = argparse.ArgumentParser(
        prog="station",
        description="Unified Stage Control Application",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Views:
  tk        Tkinter interface (the default on macOS, owner decision D-9)
  qt        Native Qt desktop GUI (PySide6)
  web       Browser-based dashboard, served on localhost

Examples:
  python3 -m station.app --web --port 8080 --no-browser
  python3 -m station.app --qt --font-size 14
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
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser for the web dashboard")
    parser.add_argument("--font-size", type=int,
                        help="base font size, in points (8-28)")

    # parse_args, not parse_known_args: an unrecognized flag must be an error.
    # Under parse_known_args a typo like `--pyside6` was silently dropped and
    # the platform default took over - on a lab Mac that started a
    # hardware-capable web server instead of the view the operator asked for
    # (MANAGER-14).
    args = parser.parse_args(argv)

    view_name = pick_view(args.view)
    if args.view is None:
        events.info("View", f"no view requested; defaulting to {view_name}",
                    source="app")
    if view_name != "web" and (args.port != DEFAULT_PORT or args.no_browser):
        parser.error("--port and --no-browser apply to the web view only")

    launch(view_name, port=args.port, open_browser=not args.no_browser,
           font_size=args.font_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
