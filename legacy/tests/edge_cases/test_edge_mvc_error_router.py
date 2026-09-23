"""Robustness of the reporting path against inputs that should not reach it.

Every test here predates S11 and asserted the same properties against the
old `ErrorPopupManager._display_popup(error_data_dict)`. The properties are
unchanged — a formatter that raises while formatting an error destroys the
only report of that error — so they were re-pointed at the bus API rather
than deleted.
"""

import pytest
import sys
from unittest.mock import patch, MagicMock

sys.path.append("src")
from error_routing import Event, ErrorRouter, bus, install_exception_hooks
from views.tkinter.view import ErrorPopupManager


def _event(title="Title", message="Message", exception=None, severity="error"):
    return Event(1, severity, "test", title, message,
                 exception=exception, requires_ack=True)


def test_formatting_survives_a_none_message():
    """`publish` coerces with `str()`, but `_format` is also reached directly
    by the replay path, so it may not assume a string arrived."""
    event = _event(title=None, message=None, exception=Exception("Test"))
    text = ErrorPopupManager._format(event)
    assert "Test" in text


def test_formatting_survives_an_exception_whose_str_raises():
    class CorruptedStrException(Exception):
        def __str__(self):
            raise RuntimeError("Corrupted string representation")

    event = _event(exception=CorruptedStrException("Bad"))
    text = ErrorPopupManager._format(event)
    assert "Unprintable Exception" in text


def test_a_huge_message_is_truncated_not_dropped():
    large = "A" * (10 ** 7)
    event = _event(title=large, message=large, exception=Exception(large))
    text = ErrorPopupManager._format(event)
    assert len(text) < 6000
    assert text.endswith("[TRUNCATED]")


def test_a_modal_is_still_raised_for_an_acknowledged_error():
    """The truncation above must not cost the operator the dialog."""
    with patch("views.tkinter.view.messagebox") as mock_mb:
        ErrorPopupManager._root = MagicMock()
        ErrorPopupManager._handle(_event(message="A" * (10 ** 7)))
        assert mock_mb.showerror.called


def test_a_quiet_event_raises_no_modal():
    """TEMP-12: "Temperature Send" opened a dialog on every send. Only an
    error carrying `requires_ack` may, and the bus refuses `requires_ack`
    on anything quieter."""
    with patch("views.tkinter.view.messagebox") as mock_mb:
        ErrorPopupManager._root = MagicMock()
        ErrorPopupManager._handle(
            Event(1, "info", "temperature", "Temperature Send", "sent"))
        ErrorPopupManager._handle(
            Event(2, "warning", "stage", "Slow", "taking a while"))
        assert not mock_mb.showerror.called

    with pytest.raises(ValueError):
        bus.publish("info", "temperature", "Send", "sent", requires_ack=True)


def test_excepthook_survives_a_corrupted_traceback():
    install_exception_hooks()
    seen = []
    ErrorRouter.subscribe(seen.append)
    try:
        # exc_type is a class that is not an exception, value is None and the
        # traceback is a string. `traceback.print_exception` raises on this.
        sys.excepthook(str, None, "not a real traceback")
    except Exception as e:
        pytest.fail(f"excepthook crashed with corrupted traceback: {e}")
    finally:
        ErrorRouter.unsubscribe(seen.append)
    assert len(seen) == 1
    assert seen[0].requires_ack, "an unhandled exception must be acknowledged"


def test_a_thread_exception_reaches_the_bus():
    """I-8.3. `threading.excepthook` was installed by the web launcher only,
    so an exception in a poller thread printed to stderr and the operator
    never learned the loop had died (ERRORS-4, PYSIDE-15)."""
    import threading

    install_exception_hooks()
    seen = []
    ErrorRouter.subscribe(seen.append)
    try:
        def boom():
            raise RuntimeError("poller died")

        t = threading.Thread(target=boom, name="poller")
        t.start()
        t.join(timeout=2.0)
    finally:
        ErrorRouter.unsubscribe(seen.append)

    assert len(seen) == 1, f"expected one event, got {seen}"
    assert "poller died" in seen[0].message
    assert seen[0].source == "thread:poller"


def test_an_unhashable_message_does_not_crash_the_router():
    """The old dedup used the message as a dict key, so a dict message
    raised `TypeError` inside the reporter. The rate limit keys on
    `(severity, source, title)` now, and the message is coerced with
    `str()`."""
    try:
        ErrorRouter.report_error("Title", {"unhashable": "dict"})
    except TypeError as e:
        pytest.fail(f"ErrorRouter crashed on unhashable message: {e}")
