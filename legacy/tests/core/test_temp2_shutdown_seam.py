"""TEMP-2, the seam: a reader in backoff must not outlive close().

Neither half of this could be caught where it was written. The
`fix-thermal-rotator` worktree gave the reader a real exponential backoff,
which is the correct repair of TEMP-2 — the old code gave up after five
failures and broke out of the loop entirely. But the backoff it wrote was a
plain `time.sleep(backoff)`, and `continue_reading` is only tested at the top
of the loop, so the longest backoff (2.0 s) is longer than
`READER_JOIN_TIMEOUT` (1.5 s), which lives in a different method and was not
part of that finding.

The consequence is not a slow shutdown. `close()` gives up waiting, prints
"closing anyway", and shuts the port — and the reader then wakes and touches
a port that has been closed underneath it. That is the use-after-close shape
S3 and S8 spent two stages removing, reintroduced through the back door of an
unrelated fix.

These tests fail against the merged worktree and pass once the backoff waits
on an event that `close()` sets.
"""
import time

import pytest
from unittest.mock import MagicMock, patch

from model.temperature_system import TemperatureSystem


def _always_failing_conn():
    conn = MagicMock()
    conn.is_open.return_value = True
    conn.read_line.side_effect = Exception("link down")
    return conn


def _system_parked_at_the_backoff_ceiling():
    """A system whose reader has failed its way up to the 2.0 s backoff.

    The wait is not padding. The backoff doubles 0.1, 0.2, 0.4, 0.8, 1.6,
    then pins at 2.0, so the reader has only spent 3.1 s of it by the time it
    first enters a wait longer than READER_JOIN_TIMEOUT. A shorter setup
    leaves the reader in a 0.4 s backoff, which close() survives easily — and
    a test built that way passes against the broken code, which is how the
    first draft of this file fooled its author.
    """
    with patch("model.temperature_system.serial") as serial_cls:
        serial_cls.return_value = _always_failing_conn()
        ts = TemperatureSystem("COM4")
    time.sleep(3.4)
    return ts


@pytest.mark.slow
def test_a_reader_at_the_backoff_ceiling_still_leaves_before_close_returns():
    """Both halves of the contract, in one run, because the setup costs 3.4 s.

    `close()` must return inside READER_JOIN_TIMEOUT *and* the reader must
    genuinely be gone. Either one alone is satisfiable by the broken code:
    a close() that gives up waiting also returns fast, and it is the one that
    then shuts the port underneath a live thread.
    """
    ts = _system_parked_at_the_backoff_ceiling()
    reader = getattr(ts, "serial_thread", None)
    assert reader is not None and reader.is_alive(), "no reader to test against"

    started = time.monotonic()
    ts.close()
    elapsed = time.monotonic() - started

    assert not reader.is_alive(), (
        "the reader is still alive after close() returned, so the port was "
        "closed underneath a thread that is about to read it")
    assert elapsed < ts.READER_JOIN_TIMEOUT, (
        f"close() took {elapsed:.2f}s, at or past the {ts.READER_JOIN_TIMEOUT}s "
        f"join timeout — the reader slept through shutdown instead of leaving")


def test_the_reader_stops_even_with_no_port_to_close():
    """The no-port branch backs off too, and has no `conn` to gate on.

    `close()` only joins the reader inside `if conn and conn.is_open()`. A
    system built with no port still starts no reader, but one whose transport
    reports closed does back off in the else branch — this pins that the stop
    flag alone ends that loop promptly.
    """
    with patch("model.temperature_system.serial") as serial_cls:
        conn = MagicMock()
        conn.is_open.return_value = False
        serial_cls.return_value = conn
        ts = TemperatureSystem("COM4")
    time.sleep(0.5)
    reader = getattr(ts, "serial_thread", None)
    if reader is None or not reader.is_alive():
        return  # no reader was started; nothing to strand
    ts.continue_reading = False
    ts._reader_wake.set()
    reader.join(timeout=1.0)
    assert not reader.is_alive(), (
        "a reader on a closed transport slept through its stop flag")
