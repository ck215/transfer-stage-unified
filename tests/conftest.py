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

_qt_probe_ok = True  # non-macOS, or PySide6 absent: no known issue, never skip

if sys.platform == "darwin":
    # macOS marks pip-downloaded PySide6 .dylib files UF_HIDDEN, which makes
    # Qt's plugin scanner silently skip them — QApplication() then aborts
    # with a native SIGABRT (qt_check_pointer) instead of raising a Python
    # exception, which looks like pytest hanging/crashing. Same class of fix
    # as run_macos.sh's launcher self-heal — but unlike that script, the
    # chflags call alone is NOT self-verifying: confirmed via repeated
    # full-suite runs that qt_check_pointer still aborts non-deterministically
    # even with chflags already applied (two back-to-back runs of identical
    # code/suite produced one clean pass and one crash at the same qapp
    # fixture — not order- or composition-dependent). A crash here takes the
    # *entire* pytest session down with it, discarding every already-passed
    # test's result along with it, which is the actual problem worth fixing
    # regardless of the exact race underneath.
    #
    # So: verify in a throwaway *subprocess* (mirroring run_macos.sh's own
    # verify-before-use pattern) that constructing a real QApplication
    # actually survives, before the real session ever risks it. A crash in
    # that subprocess only ends the subprocess — pytest itself keeps running.
    # If it fails, retry chflags once (the flag may have been reasserted, or
    # raced the first pass) and probe again. If it still fails, mark Qt as
    # unavailable for this session so pytest_collection_modifyitems below can
    # skip qapp/qtbot-dependent tests gracefully instead of letting the real
    # fixture crash the whole run later.
    try:
        import subprocess
        import PySide6
        pyside6_dir = os.path.dirname(PySide6.__file__)

        def _probe_qapplication():
            result = subprocess.run(
                [sys.executable, "-c",
                 "from PySide6.QtWidgets import QApplication; QApplication([])"],
                env=os.environ.copy(), capture_output=True, timeout=30,
            )
            return result.returncode == 0

        subprocess.run(["chflags", "-R", "nohidden", pyside6_dir], check=False)
        if not _probe_qapplication():
            subprocess.run(["chflags", "-R", "nohidden", pyside6_dir], check=False)
            _qt_probe_ok = _probe_qapplication()
    except ImportError:
        pass


def pytest_collection_modifyitems(config, items):
    skip_qt = pytest.mark.skip(
        reason="QApplication() aborts natively in this environment even after "
               "the chflags self-heal + retry (see conftest.py's "
               "_probe_qapplication) — skipping Qt-dependent tests instead of "
               "crashing the whole session."
    )
    for item in items:
        needs_qt = "qapp" in item.fixturenames or "qtbot" in item.fixturenames
        if needs_qt:
            item.add_marker(pytest.mark.qt)
            if not _qt_probe_ok:
                item.add_marker(skip_qt)


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
