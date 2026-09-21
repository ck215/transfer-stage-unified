"""SERIAL-17 — the identity handshake stops flooding, guessing and littering.

`serial.__init__` pinged the board with `s\\n` on **every pass** of a 50 ms
loop — roughly twenty per second for up to three seconds — and kept pinging
after the board had already answered, so a freshly verified link began life
with a queue of unread `DEV:` replies that `read_position` then skipped past
for the rest of the session. It broke on `"DEV:" in buffer`, which is true
the instant those four bytes land, so `device_type` was parsed off a line
that had not finished arriving and came out `""` or truncated. And
`reset_input_buffer()` ran *before* the 1.5 s bootloader wait rather than
after the match, so boot-time garbage was tolerated only by luck.

The clock and the port here are both fakes, so these run in milliseconds
rather than the four-and-a-half real seconds a construction costs. The fake
clock is what makes "how many pings in one second of waiting" an assertion
rather than a stopwatch race.
"""

import pytest

import controller.serial as serial_mod
from controller.serial import ConnectionState, serial as SerialTransport

PORT = "/dev/ttyFAKE0"


class Clock:
    """Virtual monotonic time. `sleep` is the only thing that advances it."""

    def __init__(self, start=1000.0):
        self.now = start

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, float(seconds))


class FakePort:
    """A pyserial handle that delivers scripted bytes at scripted times."""

    def __init__(self, clock, deliveries=()):
        self.clock = clock
        # [(absolute_virtual_time, payload_bytes), ...]
        self.pending = sorted(deliveries)
        self.is_open = True
        self.writes = []           # [(when, payload)]
        self.input_resets = []     # [when]
        self._buf = b""

    def _pump(self):
        while self.pending and self.pending[0][0] <= self.clock.now:
            self._buf += self.pending.pop(0)[1]

    @property
    def in_waiting(self):
        self._pump()
        return len(self._buf)

    def read(self, size=1):
        self._pump()
        out, self._buf = self._buf[:size], self._buf[size:]
        return out

    def write(self, payload):
        self.writes.append((self.clock.now, payload))
        return len(payload)

    def reset_input_buffer(self):
        self.input_resets.append(self.clock.now)
        self._buf = b""

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.is_open = False


@pytest.fixture
def harness(monkeypatch):
    """Build a transport over a fake port and a fake clock.

    `controller.serial` holds `time` as a module attribute and uses it only
    in the handshake, so replacing that one name leaves the real clock alone
    for everything else in the session.
    """
    def build(deliveries=(), start=1000.0):
        clock = Clock(start)
        port = FakePort(clock, deliveries)

        class FakePySerial:
            SerialException = Exception
            SerialTimeoutException = Exception

            @staticmethod
            def Serial(*args, **kwargs):
                return port

        monkeypatch.setattr(serial_mod, "time", clock)
        monkeypatch.setattr(serial_mod, "pyserial", FakePySerial)
        t = SerialTransport(PORT)
        # SERIAL-6: the handshake now runs on a background thread, so the
        # virtual clock advancing instantly no longer means the handshake
        # itself has finished by the time this returns -- only that it
        # *would* finish quickly once scheduled. Join explicitly instead of
        # relying on the real thread happening to win the race before the
        # caller's next line runs. Real seconds, deliberately: `Thread.join`
        # does not know about the fake clock, and the work behind it is a
        # handful of Python-level iterations either way.
        assert t.wait_connected(timeout=5.0), "handshake thread never finished"
        return t, port, clock

    return build


def _pings(port):
    return [when for when, payload in port.writes if payload == b"s\n"]


# --------------------------------------------------------------------------
# A truncated `DEV:` must not be read as an identity.
# --------------------------------------------------------------------------


def test_a_dev_prefix_split_across_reads_is_not_parsed_as_a_device(harness):
    """`DEV:` arrives, then ` s\\n` 400 ms later. There is one device, not two.

    The old break condition fired on the first four bytes and parsed
    `"DEV:".split("DEV:")[1].strip()` — the empty string — as the device id.
    """
    t, port, _ = harness([(1001.6, b"DEV:"), (1002.0, b" s\n")])
    assert t.device_type == "s"
    assert t.connection_state == ConnectionState.VERIFIED


def test_bootloader_garbage_before_the_reply_is_ignored(harness):
    t, port, _ = harness([
        (1001.6, b"\x00\xff\xfe rubbish from the bootloader"),
        (1002.0, b"\r\nDEV: s\r\n"),
    ])
    assert t.device_type == "s"


def test_a_complete_reply_in_one_read_still_works(harness):
    t, port, _ = harness([(1001.7, b"DEV: t\r\n")])
    assert t.device_type == "t"
    assert t.connection_state == ConnectionState.VERIFIED


# --------------------------------------------------------------------------
# The ping is a handshake, not a flood.
# --------------------------------------------------------------------------


def test_the_ping_does_not_flood_while_waiting_for_a_slow_board(harness):
    """One second of waiting is a handful of pings, not twenty.

    Every ping is answered with its own `DEV:` line, so twenty of them leave
    nineteen replies queued in the input buffer behind the one that was read.
    """
    t, port, _ = harness([(1002.5, b"DEV: s\r\n")])
    assert t.connection_state == ConnectionState.VERIFIED
    assert len(_pings(port)) <= 8, (
        f"{len(_pings(port))} pings sent while waiting one second for a reply")


def test_the_ping_does_not_flood_a_board_that_never_answers(harness):
    t, port, _ = harness([])
    assert t.connection_state == ConnectionState.UNVERIFIED
    assert len(_pings(port)) <= 16, (
        f"{len(_pings(port))} pings sent over the full handshake window")


def test_pings_are_spaced_by_at_least_the_declared_interval(harness):
    t, port, _ = harness([])
    times = _pings(port)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(gap >= SerialTransport.PING_INTERVAL - 1e-9 for gap in gaps), gaps


# --------------------------------------------------------------------------
# Nothing is left in the buffer for read_position to trip over.
# --------------------------------------------------------------------------


def test_the_input_buffer_is_drained_after_the_match(harness):
    t, port, _ = harness([(1002.0, b"DEV: s\r\nDEV: s\r\nDEV: s\r\n")])
    assert t.device_type == "s"
    assert any(when >= 1002.0 for when in port.input_resets), (
        "the queued replies were left in the buffer; the only reset was the "
        "one before the bootloader wait")


# --------------------------------------------------------------------------
# Regression guards.
# --------------------------------------------------------------------------


def test_a_silent_board_is_unverified_not_verified(harness):
    t, _, _ = harness([])
    assert t.device_type is None
    assert t.connection_state == ConnectionState.UNVERIFIED


def test_the_handshake_gives_up_within_its_declared_window(harness):
    t, _, clock = harness([])
    elapsed = clock.now - 1000.0
    budget = (SerialTransport.BOOTLOADER_WAIT
              + SerialTransport.HANDSHAKE_TIMEOUT + 0.5)
    assert elapsed <= budget, f"handshake ran for {elapsed}s"
