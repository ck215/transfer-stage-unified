"""TEMP-10's headline: with no port, the temperature reads "N/A" forever.

**This row has now been reported `closed` or fixed-on-a-different-clause
three times.** Each attempt fixed something real and adjacent, then wrote a
STATUS as though the headline were covered. So, clause by clause, the
finding says:

    the no-port branch backs off silently and never sets `Disconnected`,
    so SIM or no-port shows "N/A" forever

Two separate things have to be true for that symptom, and both are:

1. `__init__` starts the reader thread only inside
   `if self.serial_conn and self.serial_conn.is_open():`. With no port the
   reader **never starts at all**, so nothing is ever in a position to
   update `current_temp`.
2. Even when the reader does run, its no-port branch ("Port is closed/not
   open") only increments a counter and backs off. `current_temp` is set to
   `"Disconnected"` exclusively in the `except Exception` handler — and a
   closed port raises nothing, so that line is unreachable on this path.

`current_temp` therefore keeps its constructor default `"N/A"` for the life
of the process. "N/A" reads as *no reading yet*, which is a transient state;
the operator waits for a number that cannot arrive.

Note what is **not** the bug, because a previous attempt fixed this instead:
`send_settings()` and `stop()` being silent with no port is a real gap and is
now reported — but it is a *reporting* gap on a button press, and it leaves
`current_temp` reading "N/A" exactly as before. `connection_state` is also
not the bug: it correctly returns "CLOSED" via the transport. The bug is the
temperature field itself.

The Lane 1 PySide indicator half is separate and landed already.
"""
import threading

import pytest

from model.temperature_system import TemperatureSystem


@pytest.fixture
def no_port():
    ts = TemperatureSystem(port=None)
    yield ts
    ts.close()


def test_temp_10_no_port_does_not_report_a_pending_reading(no_port):
    """"N/A" claims a reading is coming. With no port, none ever is."""
    assert no_port.current_temp != "N/A", (
        'with no port the temperature still reads "N/A", which means "no '
        'reading yet" — a transient state that will never resolve, because '
        "the reader thread is not started when the port was never open. The "
        "operator waits indefinitely for a number that cannot arrive.")


def test_temp_10_no_port_names_the_disconnection(no_port):
    """It must say *disconnected*, not merely something non-"N/A"."""
    assert "disconnect" in str(no_port.current_temp).lower(), (
        f"current_temp is {no_port.current_temp!r}; TEMP-10 asks the no-port "
        f"state to be reported as a disconnection so the three renderers can "
        f"show it as one")


def test_temp_10_no_reader_thread_exists_with_no_port(no_port):
    """Pins clause 1: why nothing can update the field later.

    Not a defect in itself — there is no port to read — but it is the reason
    the state has to be set at construction rather than left to the reader.
    """
    reader = getattr(no_port, "serial_thread", None)
    assert reader is None or not reader.is_alive(), (
        "a reader thread is running with no port; if that changes, the "
        "no-port state may be settable from the loop instead")


def test_temp_10_the_reader_no_port_branch_also_sets_the_state():
    """Pins clause 2: a port that closes *later* must report it too.

    Drives one pass of the reader's no-port branch directly, with the loop
    stopped after a single iteration, and asserts the branch itself sets the
    state — rather than relying on the `except` handler, which a closed port
    never reaches because it raises nothing.
    """
    ts = TemperatureSystem(port=None)
    try:
        ts.current_temp = "21.5"          # a live reading, as if the port had been open
        ts.serial_conn = None             # ...and then went away
        ts.continue_reading = True

        def _stop_after_first_pass():
            ts.continue_reading = False
            ts._reader_wake.set()

        timer = threading.Timer(0.25, _stop_after_first_pass)
        timer.start()
        try:
            ts.read_serial_data()
        finally:
            timer.cancel()

        assert "disconnect" in str(ts.current_temp).lower(), (
            f"the reader's no-port branch left current_temp as "
            f"{ts.current_temp!r} — it backed off silently. A port that "
            f"drops mid-session goes on showing the last good reading as "
            f"though it were live.")
    finally:
        ts.close()
