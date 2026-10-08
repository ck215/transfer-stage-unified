import os
import sys
from unittest.mock import MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ---------------------------------------------------------------------------
# Harness carried over from the old suite's conftest (now
# legacy/tests/conftest.py). Before the relayout this suite lived under it as
# tests/station/ and ran with it as its parent conftest; these are the parts
# it depends on: the headless-Qt setup and qapp/qtbot auto-marking, and the
# tkinter stand-in `test_view_tk.py` is written against. Copied verbatim.
# ---------------------------------------------------------------------------

# Set headless Qt platform before any Qt fixtures initialize.
# Without this, pytest-qt's qapp fixture calls QApplication() which
# aborts immediately on macOS when no display server is available.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_qt_probe_ok = None  # not probed yet: the probe runs lazily, see _qt_available()


def _qt_available():
    """Whether a real QApplication survives in a throwaway subprocess.

    Lazy since the Qt view was retired (2026-10-07): the probe used to run at
    conftest import, so every pytest run (the fast gate included) spawned a
    PySide6 child. Now it runs only when a selected test needs Qt, and never
    imports PySide6 here (a find_spec, not an import).

    macOS marks pip-downloaded PySide6 .dylib files UF_HIDDEN, which makes
    Qt's plugin scanner skip them and QApplication() abort natively (SIGABRT),
    which would take the whole pytest session down. So construct one in a
    subprocess first (a crash there only ends the subprocess), retry once
    after clearing the flag, and mark Qt unavailable if it still fails.
    PySide6 absent: no known issue, never skip.
    """
    global _qt_probe_ok
    if _qt_probe_ok is not None:
        return _qt_probe_ok
    _qt_probe_ok = True
    if sys.platform != "darwin":
        return _qt_probe_ok
    import importlib.util
    import subprocess
    try:
        spec = importlib.util.find_spec("PySide6")
    except (ValueError, ImportError):
        spec = None
    if spec is None or not spec.submodule_search_locations:
        return _qt_probe_ok
    pyside6_dir = list(spec.submodule_search_locations)[0]

    def _probe_qapplication():
        try:
            result = subprocess.run(
                [sys.executable, "-c",
                 "from PySide6.QtWidgets import QApplication; QApplication([])"],
                env=os.environ.copy(), capture_output=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    subprocess.run(["chflags", "-R", "nohidden", pyside6_dir], check=False)
    if not _probe_qapplication():
        subprocess.run(["chflags", "-R", "nohidden", pyside6_dir], check=False)
        _qt_probe_ok = _probe_qapplication()
    return _qt_probe_ok


def _pyside6_installed():
    import importlib.util
    try:
        return importlib.util.find_spec("PySide6") is not None
    except (ValueError, ImportError):
        return False


#: The frozen Qt view's tests import PySide6 at module top. With PySide6 absent
#: (the default install since 2026-10-07: it is the optional `qt` extra) they
#: are not collected at all, instead of erroring the whole fast gate.
collect_ignore_glob = [] if _pyside6_installed() else ["test_view_qt*.py"]


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "window: maps a real Tk window on this Mac (a child process with the "
        "real tkinter). Skipped when STATION_NO_WINDOWS=1 is set, so the suite "
        "can run while someone is working at the display; the lead runs these "
        "afterwards. Applied automatically to any test that calls the real-Tk "
        "build helper, and explicitly where a test spawns its own window.")


#: The rule that keeps the suite off the operator's screen (owner, 2026-09-26:
#: "I need a way for you to work strictly in the background"). Every test that
#: maps a real Tk window carries `window`; with STATION_NO_WINDOWS=1 they skip.
NO_WINDOWS = bool(os.environ.get("STATION_NO_WINDOWS"))


def _maps_a_window(item):
    if item.get_closest_marker("window") is not None:
        return True
    function = getattr(item, "function", None)
    code = getattr(function, "__code__", None)
    names = set(getattr(code, "co_names", ()))
    # `_real_build` is test_view_tk.py's child-process real-Tk harness; a test
    # that names it maps a window whatever its own name is.
    return "_real_build" in names


def pytest_collection_modifyitems(config, items):
    skip_qt = pytest.mark.skip(
        reason="QApplication() aborts natively in this environment even after "
               "the chflags self-heal + retry (see conftest.py's "
               "_probe_qapplication) — skipping Qt-dependent tests instead of "
               "crashing the whole session."
    )
    skip_window = pytest.mark.skip(
        reason="maps a real Tk window; STATION_NO_WINDOWS=1 is set (someone is "
               "working at the display). Run without the variable to cover it.")
    # `-m "not qt"` (the fast gate) deselects every Qt test: no probe then.
    markexpr = (config.getoption("markexpr", "") or "").replace(" ", "")
    qt_wanted = "notqt" not in markexpr
    for item in items:
        needs_qt = "qapp" in item.fixturenames or "qtbot" in item.fixturenames
        if needs_qt:
            item.add_marker(pytest.mark.qt)
            if qt_wanted and not _qt_available():
                item.add_marker(skip_qt)
        if _maps_a_window(item):
            item.add_marker(pytest.mark.window)
            if NO_WINDOWS:
                item.add_marker(skip_window)


tkinter_mock = MagicMock()
class DummyTkWidget:
    def __init__(self, master=None, *args, **kwargs): self.master = master or __import__('unittest.mock').mock.MagicMock()
    def protocol(self, *args, **kwargs): pass
    def destroy(self): pass
    def deiconify(self): pass
    def configure(self, *args, **kwargs): pass
    def pack(self, *args, **kwargs): pass
    def bind(self, *args, **kwargs): pass
    def title(self, *args, **kwargs): pass
    def geometry(self, *args, **kwargs): pass
    def minsize(self, *args, **kwargs): pass
    def after(self, *args, **kwargs): pass
    def winfo_exists(self, *args, **kwargs): return True
    # DynamicView._poll_model (views/tkinter/view.py:514) calls focus_get()
    # as part of the 12e9d59 focus guard. Returning None is the truthful
    # headless answer ("no widget holds focus") and is what the guard's
    # else-branch expects; without it every Tk view test raises
    # AttributeError inside the poll loop.
    def focus_get(self, *args, **kwargs): return None
    def focus_set(self, *args, **kwargs): pass
    def columnconfigure(self, *args, **kwargs): pass
    def rowconfigure(self, *args, **kwargs): pass
    def grid(self, *args, **kwargs): pass
class DummyTkNotebook(DummyTkWidget):
    """A real class, because `ttk.Notebook` gets **subclassed**.

    `sys.modules['tkinter.ttk']` used to be a bare MagicMock, so
    `class DraggableClosableNotebook(ttk.Notebook)` did not produce a class at
    all: the MagicMock base went through `__mro_entries__` and the result was
    another MagicMock, whose `side_effect` is a finite `tuple_iterator`.

    That made `DraggableClosableNotebook` **callable only a bounded number of
    times per process** — the next construction raised `StopIteration` from
    inside `unittest.mock`, with a traceback pointing at the view. It is the
    real cause of `test_dashboard_window_teardown_ordering` sitting in
    `_ORDER_DEPENDENT`: that test passes alone and fails in composition, not
    because of process-global product state as this file long assumed, but
    because some earlier test had already spent the iterator.

    Tab bookkeeping is modelled for real here, because S6's hide/show is
    exactly a question of whether a hidden tab can be added back.
    """
    def __init__(self, master=None, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self._tabs = []
        self._hidden = set()
        self._selected = None

    def add(self, child, **kwargs):
        if child not in self._tabs:
            self._tabs.append(child)
        self._hidden.discard(child)

    def hide(self, child):
        self._hidden.add(self._resolve(child))

    def forget(self, child):
        child = self._resolve(child)
        if child in self._tabs:
            self._tabs.remove(child)
        self._hidden.discard(child)

    def select(self, child=None):
        if child is None:
            return self._selected
        self._selected = self._resolve(child)

    def index(self, spec):
        return 0

    def insert(self, position, child):
        pass

    def tabs(self):
        return [str(t) for t in self._tabs if t not in self._hidden]

    def _resolve(self, child):
        if isinstance(child, int):
            visible = [t for t in self._tabs if t not in self._hidden]
            return visible[child] if child < len(visible) else child
        return child


ttk_mock = MagicMock()
ttk_mock.Notebook = DummyTkNotebook
ttk_mock.Frame = DummyTkWidget

tkinter_mock.Toplevel = DummyTkWidget
tkinter_mock.Frame = DummyTkWidget
tkinter_mock.Tk = DummyTkWidget
# Both bindings are needed, and only one of them is obvious. `import
# tkinter.ttk` consults sys.modules; `from tkinter import ttk` — which is what
# the views actually write — reads the *attribute* off the tkinter module
# object, and on a MagicMock that auto-creates an unrelated child mock. Setting
# only sys.modules leaves the views holding the auto-created one.
tkinter_mock.ttk = ttk_mock
sys.modules['tkinter'] = tkinter_mock
sys.modules['tkinter.ttk'] = ttk_mock
sys.modules['tkinter.filedialog'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()


@pytest.fixture(autouse=True)
def _transfer_map_db_in_tmp(monkeypatch, tmp_path):
    """The Transfer Map makes its database ready at `open()` (bench
    2026-09-27), so every test that opens the seven rows would otherwise
    leave `data/transfer_map.sqlite` (and a session file per "New session
    database" press) in the checkout: on the lab PC, junk beside the real
    store. Every test's store lives under its own tmp_path instead. A test
    that sets `STATION_MAP_DB` itself still wins: it runs after this."""
    monkeypatch.setenv("STATION_MAP_DB", str(tmp_path / "map" / "transfer_map.sqlite"))
    # The Sample DB's store likewise (flake-coords, 2026-10-04).
    monkeypatch.setenv("STATION_SAMPLE_DB", str(tmp_path / "map" / "sample_map.sqlite"))
    # And the profiles (user-system Phase 1): never the operator's real ones.
    monkeypatch.setenv("STATION_PROFILES_DIR", str(tmp_path / "profiles"))
    # And the stores' backup (2026-10-07): off, so no test ever writes to
    # ~/QMDL_Drive; a test of the backup sets its own folder.
    monkeypatch.setenv("STATION_BACKUP_DIR", "off")
    # And the station's choices file (a Guest's store choices): never the
    # operator's ~/transfer-stage-runs/station.json.
    monkeypatch.setenv("STATION_CONFIG", str(tmp_path / "choices" / "station.json"))


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch):
    """Setup checks GitHub for an update on a thread at construction (owner,
    2026-09-28). No test may fetch: every Setup built in the suite starts with
    the check off. A test of the check itself deletes the variable and hands
    Setup a fake Updater."""
    monkeypatch.setenv("STATION_NO_UPDATE_CHECK", "1")


@pytest.fixture(autouse=True)
def _no_firmware_check(monkeypatch, tmp_path):
    """Setup works out each board's firmware status on a thread at
    construction (owner, 2026-09-28: the check that was `run_swap.sh`'s, now `dev/swap_branch.sh`).
    Every Setup in the suite starts with it off, and the stamp file it and
    `firmware/flash_firmware.py` read is this test's own, never the bench's
    `~/transfer-stage-runs/flashed.json`. A test of the check deletes the
    variable and hands Setup a fake FirmwareCheck."""
    monkeypatch.setenv("STATION_NO_FIRMWARE_CHECK", "1")
    monkeypatch.setenv("STATION_FLASH_STAMP", str(tmp_path / "flash" / "flashed.json"))



@pytest.fixture
def profiles_on(monkeypatch):
    """The user profiles are off by default (owner 2026-10-06; the work waits
    on branch `feature/sample-map-profiles`). A test of the held feature asks
    for it on; `Setup` reads the flag when it builds its schema."""
    from controller import setup as station_setup
    monkeypatch.setattr(station_setup, "PROFILES_ENABLED", True)


@pytest.fixture
def sample_map_on(monkeypatch):
    """The Sample DB is off by default (owner 2026-10-06; branch
    `feature/sample-map-profiles`). A test that builds one through Setup asks
    for it registered, in a private copy of the registry."""
    from controller import setup as station_setup
    from model.sample_map import SampleMap
    monkeypatch.setattr(station_setup, "MODEL_TYPES", dict(station_setup.MODEL_TYPES))
    station_setup.register(SampleMap)
