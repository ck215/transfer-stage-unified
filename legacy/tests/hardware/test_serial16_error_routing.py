"""SERIAL-16 -- per-site audit of which serial.py conditions report through
ErrorRouter/EventBus (RC-8) versus print. This is a judgment pass, not a
find-and-replace; most sites were already correct:

* Routine status (`"Establishing Serial Connection..."`, `"Sending ...
  Command"`, a successful `"Serial Connection Verified!"`) is print-only on
  purpose -- error-routing.md already documents connect *success* as fully
  silent, and reporting every successful write would flood the bus (RC-8
  item 2 exists to collapse repeats, not to be relied on for routine
  traffic).
* `flush()`'s drain timeout/failure is print-only on purpose, by its own
  docstring: a "Connection Lost" popup raised while the app is already
  closing the device is noise, not information. `close()` below is the same
  shape but was missing the print *and* the report both.
* A handshake write/read failure inside `_handshake` returns False with no
  report of its own, but `_connect_worker` always reports the outcome
  ("Serial Connection Warning: ... Operating blind") once the worker
  finishes, whether the cause was silence or a write error -- there is
  exactly one user-visible symptom either way, which is the audit's own
  description of the site, not a gap.
* A malformed `POS:` line in `read_position` is dropped silently by design:
  it is per-sample telemetry at up to ~10 Hz, and reporting each one would
  be the popup flood RC-8 exists to prevent. `_mark_lost`'s own two
  `except Exception: pass` blocks (closing the dead handle, and the
  `ErrorRouter` call itself) are deliberately silent -- there is nowhere
  left to report a failure to report to.

The one real gap: `close()` had no failure handling at all. An exception
from `ser.close()` propagated straight out of `close()` -- not even a
print, let alone a report -- into whatever called it (`BaseProbe.teardown`,
`TemperatureSystem.teardown`, `SystemManager.shutdown_all`'s per-model
catch). That is the one behavior this file changes and tests.
"""

from controller.serial import serial as SerialTransport

PORT = "/dev/ttyFAKE0"


def _real_port_transport(handle):
    """A transport that believes it has a real (non-SIM) port, without
    going through the identity handshake -- same technique as
    test_serial_flush.py."""
    t = SerialTransport("SIM")
    t.SERIAL_PORT = PORT
    t.ser = handle
    return t


class GoodHandle:
    def __init__(self):
        self.is_open = True
        self.closed = False

    def close(self):
        self.closed = True
        self.is_open = False


class ExplodingCloseHandle:
    """A handle whose `close()` fails, the way a device that vanished out
    from under the OS sometimes does."""

    def __init__(self):
        self.is_open = True

    def close(self):
        raise OSError("device or resource busy")


def test_close_does_not_raise_when_the_handle_fails_to_close(monkeypatch):
    """The core SERIAL-16 fix: a failing `ser.close()` must not propagate
    out of `close()` uncaught."""
    reports = []
    import controller.serial as serial_mod
    monkeypatch.setattr(
        serial_mod.ErrorPopupManager, "report_warning",
        staticmethod(lambda title, msg, exc=None, source="app": reports.append((title, msg, exc))))

    t = _real_port_transport(ExplodingCloseHandle())

    t.close()  # must not raise

    assert len(reports) == 1
    title, msg, exc = reports[0]
    assert title == "Serial Close Error"
    assert isinstance(exc, OSError)
    assert PORT in msg


def test_close_still_moves_to_closed_even_when_the_handle_fails(monkeypatch):
    from controller.serial import ConnectionState
    import controller.serial as serial_mod
    monkeypatch.setattr(
        serial_mod.ErrorPopupManager, "report_warning",
        staticmethod(lambda *a, **k: None))

    t = _real_port_transport(ExplodingCloseHandle())
    t.connection_state = ConnectionState.VERIFIED
    t.close()

    assert t.connection_state == ConnectionState.CLOSED, (
        "the intent to close must still be recorded even when the OS call "
        "that carries it out fails")


def test_close_reports_nothing_on_the_ordinary_successful_path():
    """Regression guard: routine close stays print-only, matching every
    other success path in this file (error-routing.md: connect success is
    fully silent)."""
    import controller.serial as serial_mod
    reports = []
    real = serial_mod.ErrorPopupManager.report_warning
    try:
        serial_mod.ErrorPopupManager.report_warning = staticmethod(
            lambda *a, **k: reports.append(a))
        t = _real_port_transport(GoodHandle())
        t.close()
    finally:
        serial_mod.ErrorPopupManager.report_warning = real

    assert reports == []
