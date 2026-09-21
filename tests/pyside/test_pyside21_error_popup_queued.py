"""PYSIDE-21: a `requires_ack` error must never open its modal inside the
publisher's own call stack.

`QtErrorPopupManager._publish` (the bus callback, run on whichever thread
published) hands the event to `_on_event` (which may call
`QMessageBox.critical`) through a Qt signal. Before the fix, that signal
used the default `AutoConnection`, which resolves to a **direct**,
synchronous call whenever the publisher is already on the GUI thread — the
same thread the `QtErrorPopupManager` lives on. A `requires_ack=True` error
raised from inside `closeEvent -> shutdown_all()` (e.g. a device's `close()`
reporting "Heater Off Not Delivered") therefore opened a *nested* event loop
mid-teardown, in the middle of `shutdown_all()`'s own call stack.

This needs a real `QApplication` (the `qapp` fixture), so it is qt-marked
and not run by the agent that wrote it — see tests/ui/test_manager20_*.py
for the same constraint. Nothing here asserts on matplotlib (mocked to a
MagicMock in conftest.py); `QMessageBox.critical` is patched directly so a
real modal is never shown (nobody would be there to click it).
"""
from unittest.mock import patch

import pytest

from error_routing import ErrorRouter, reset_bus

pytestmark = pytest.mark.qt


@pytest.fixture
def clean_bus():
    reset_bus()
    yield
    reset_bus()


def test_requires_ack_modal_does_not_run_before_publish_returns(
        qapp, clean_bus):
    """The synchronous-emit regression, pinned directly.

    Pre-fix, `ErrorRouter.report_error(..., requires_ack=True)` called on
    the GUI thread reaches `QMessageBox.critical` before `report_error`
    itself returns, because the signal's `AutoConnection` resolves direct
    same-thread. Post-fix (`Qt.QueuedConnection`), the call is queued and
    only runs once the event loop is pumped.
    """
    from views.pyside.view import QtErrorPopupManager

    # Guard against a stale subscriber from a prior test/module leaving
    # `_instance` set; initialize() would otherwise silently reuse it.
    QtErrorPopupManager.shutdown()
    manager = QtErrorPopupManager.initialize()

    calls = []
    try:
        with patch("PySide6.QtWidgets.QMessageBox.critical",
                   side_effect=lambda *a, **k: calls.append((a, k))):
            ErrorRouter.report_error(
                "Heater Off Not Delivered", "boom", requires_ack=True)

            assert calls == [], (
                "QMessageBox.critical ran inside report_error()'s own call "
                "stack -- a requires_ack error raised from closeEvent -> "
                "shutdown_all() would open a nested event loop mid-teardown")

            qapp.processEvents()

            assert len(calls) == 1, (
                "the modal never ran even after the event loop was pumped "
                "-- the queued signal was never delivered")
    finally:
        QtErrorPopupManager.shutdown()


def test_requires_ack_modal_still_runs_from_a_worker_thread(
        qapp, clean_bus):
    """The fix must not lose events published off the GUI thread.

    `AutoConnection` already queued these (a worker thread is never the
    receiver's thread), so this only pins that forcing `QueuedConnection`
    explicitly did not change that path.
    """
    import threading

    from views.pyside.view import QtErrorPopupManager

    QtErrorPopupManager.shutdown()
    manager = QtErrorPopupManager.initialize()

    calls = []
    try:
        with patch("PySide6.QtWidgets.QMessageBox.critical",
                   side_effect=lambda *a, **k: calls.append((a, k))):
            t = threading.Thread(
                target=ErrorRouter.report_error,
                args=("Worker Fault", "boom"),
                kwargs={"requires_ack": True},
            )
            t.start()
            t.join(timeout=5.0)
            assert not t.is_alive(), "publisher thread never finished"

            assert calls == [], (
                "the modal ran off the GUI thread -- Qt must never touch a "
                "widget outside it")

            qapp.processEvents()

            assert len(calls) == 1, (
                "an error published from a worker thread never reached the "
                "modal after the event loop ran")
    finally:
        QtErrorPopupManager.shutdown()
