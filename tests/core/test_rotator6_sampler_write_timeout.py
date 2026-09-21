"""SMC100-WRITE-TIMEOUT: the priority stop's own write was unbounded.

Filed mid-task by the coordinator, separate from ROTATOR-6, but in the same
write set (`src/lib/smc100.py`).

`stop(priority=True)` (smc100.py) bounds only the `_serial_lock`
acquisition via `PRIORITY_LOCK_TIMEOUT`. The `port.write()` calls that
follow it were not bounded at all: the port is opened with `xonxoff=True`,
and pyserial's own default is `write_timeout=None` -- block until every
byte is accepted, forever. A controller withholding XON is exactly what
software flow control does when the device is busy or faulted, which is
exactly the condition under which someone is pressing FULL STOP. So the
worker thread `RotatorSystem.emergency_stop` spawns to carry the stop could
hang forever inside `port.write`, with the operator-visible side (the
latch, the 0.08s join) reporting nothing wrong.

`tests/core/test_rotator6_sampler_extras.py` and the lead's seam pin cover
ROTATOR-6 (the sampler). This file is standalone and does not depend on
either.
"""
import threading
import time
from unittest.mock import patch

import serial   # the conftest-mocked module; SerialTimeoutException = Exception there

from lib.smc100 import SMC100


class FakeSerialPort:
    """Stands in for pyserial's `serial.Serial`, replicating exactly the
    one behavior this finding is about.

    Real pyserial: with `write_timeout` set, `write()` blocks up to that
    many seconds waiting for the OS to accept the bytes (which a controller
    withholding XON prevents) and then raises `SerialTimeoutException`.
    With `write_timeout=None` -- pyserial's own default, and what
    `smc100.py` left in place pre-fix -- `write()` blocks forever instead.
    This fake reproduces both, so the *same* test hangs against the
    unpatched constructor and returns bounded against the patched one; the
    only thing that differs between runs is which kwargs `SMC100.__init__`
    actually passed to `serial.Serial(...)`.
    """

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.write_timeout = kwargs.get("write_timeout")
        self._release = threading.Event()   # never set: models XON withheld forever
        self.writes = []

    def write(self, data):
        self.writes.append(data)
        if self.write_timeout is None:
            self._release.wait()   # no timeout argument -- blocks forever
        elif not self._release.wait(self.write_timeout):
            raise serial.SerialTimeoutException(
                "simulated: controller withholding XON")
        return len(data)

    def flush(self):
        pass

    def read(self, size=1):
        return b""

    def close(self):
        pass


def _built_smc(captured_ports):
    """Construct a real `SMC100` whose `_port` is a `FakeSerialPort`,
    through the real `__init__` -- so whatever kwargs `smc100.py` actually
    passes to `serial.Serial(...)` are the ones the fake receives, rather
    than a value the test asserts past.
    """
    def factory(*args, **kwargs):
        port = FakeSerialPort(*args, **kwargs)
        captured_ports.append(port)
        return port

    with patch("lib.smc100.serial.Serial", side_effect=factory):
        smc = SMC100(smcID=1, port="SIM-PORT", silent=True, sleepfunc=lambda s: None)
    return smc


def test_smc100_opens_the_port_with_a_write_timeout():
    """The constructor must actually pass `write_timeout`, not leave
    pyserial's `None` default in place. This is the fix in one assertion.
    """
    captured = []
    _built_smc(captured)
    assert len(captured) == 1, "test setup: SMC100.__init__ did not open a port"
    write_timeout = captured[0].kwargs.get("write_timeout")
    assert write_timeout is not None, (
        "serial.Serial(...) was opened with no write_timeout -- pyserial's "
        "default blocks a write forever, which is exactly what "
        "xonxoff=True lets the controller do when it is busy or faulted")
    assert write_timeout == SMC100.WRITE_TIMEOUT_SEC, (
        f"write_timeout should be the named constant "
        f"SMC100.WRITE_TIMEOUT_SEC ({SMC100.WRITE_TIMEOUT_SEC}), got "
        f"{write_timeout} -- a literal buried in the constructor call "
        f"instead of the documented, referenceable value")


def test_priority_stop_does_not_hang_when_the_write_blocks():
    """The actual defect: a wedged write must not hang the FULL STOP path.

    Run on a background thread with a bounded join rather than calling
    `stop()` directly on the test thread -- so that if this ever regresses,
    the test *fails* instead of hanging the whole pytest run.
    """
    captured = []
    smc = _built_smc(captured)

    result = {}

    def _call():
        try:
            smc.stop(priority=True)
        except Exception as e:
            result["exception"] = e
        else:
            result["exception"] = None

    worker = threading.Thread(target=_call, daemon=True)
    started = time.time()
    worker.start()
    # A fixed, generous bound -- deliberately not derived from
    # `SMC100.WRITE_TIMEOUT_SEC`, so this test still exercises (and fails
    # cleanly on, rather than erroring on a missing attribute) a pre-fix
    # `SMC100` that has no such constant at all. Pre-fix, the fake blocks
    # forever and this join times out with the thread still alive -- the
    # deadlock the coordinator's finding describes, reproduced without real
    # hardware.
    bound = 3.0
    worker.join(bound)
    elapsed = time.time() - started

    assert not worker.is_alive(), (
        f"stop(priority=True) did not return within {bound}s of a blocked "
        f"write -- the write is unbounded again (SMC100-WRITE-TIMEOUT "
        f"regression); the worker thread RotatorSystem.emergency_stop "
        f"spawns to carry this call would hang forever while the stage "
        f"kept turning")
    assert elapsed < bound, f"stop(priority=True) took {elapsed:.2f}s"
    assert result.get("exception") is not None, (
        "a write that timed out must raise, not return as if the stop "
        "landed -- a silent return here is indistinguishable from a "
        "successful stop to every caller above this")


def test_rotator_system_reports_a_write_timeout_instead_of_swallowing_it():
    """Requirement 2 of the finding: the failure must not be silent.

    `RotatorSystem.stop` already wraps `smc.stop()` and routes any
    exception through `ErrorRouter.report_error` (pre-existing code, not
    part of this fix) -- this pins that a write-timeout-shaped exception
    from `smc100.py` actually reaches it, rather than being swallowed
    somewhere in between.
    """
    from model.rotator_system import RotatorSystem

    class RaisingSMC:
        def stop(self, priority=False):
            raise serial.SerialTimeoutException(
                "simulated: controller withholding XON")

    rotator = RotatorSystem.__new__(RotatorSystem)
    rotator._lock = threading.Lock()
    rotator.smc = RaisingSMC()

    with patch("error_routing.ErrorRouter.report_error") as mock_report:
        rotator.stop(priority=True)   # must not raise out of this call

    assert mock_report.called, (
        "smc.stop() raised on a write timeout and nothing reported it -- "
        "a stop that failed to land must be visible to the operator, not "
        "silently dropped")
    title = mock_report.call_args[0][0]
    assert "stop" in title.lower() or "rotator" in title.lower()
