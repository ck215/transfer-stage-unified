"""SERIAL-10 (host half) — the model stops claiming a power-down it cannot observe.

The finding: the DC probe's firmware (`firmware/high_polling_rate/`) has no
branch for `'e'`, `'d'` or `'k'` at all — only `0xAA` and `0x73` (`'s'`) — so
every other byte falls through to `parseSerialAuto()` and is read as text.
`'k'` has no branch in *any* .ino. The host nevertheless logged
"Sent Power Down (Kill Coils) command 'k'" on every board and reported plain
success, which is a safety verb asserting an outcome the firmware never
produced.

**Scope.** What the firmware *should* do about `'e'`/`'d'`/`'k'` is owner
decision **D-7** (plan.md S16 item 2: "implement or delete `k` (no firmware
handles it today)"), which is open and needs every board reflashed at the
bench. These tests therefore pin two things and nothing else:

1. the bytes on the wire are **unchanged** — this is a stop path, and the
   first test here is the one that says so;
2. the model reports `unsupported` rather than silent success on a board
   whose firmware has no coil-kill handler.

Nothing here asserts what `'k'` ought to do. That is D-7's to answer.
"""

import pathlib

import pytest

from error_routing import ErrorRouter
from model.probes import BaseProbe, ChuckPositioner, DCProbe, StepperProbe

FIRMWARE = pathlib.Path(__file__).resolve().parents[2] / "firmware"


def _writes(probe):
    """Every byte string this probe's simulated port actually received."""
    return list(probe.serial_comm.ser.writes)


# --------------------------------------------------------------------------
# Safety first: the stop path's bytes are not allowed to change.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [StepperProbe, DCProbe, ChuckPositioner])
def test_power_down_still_sends_the_stop_frame_disable_and_kill_byte(cls):
    """The wire is untouched by the truthfulness fix (standing safety rule).

    A host that has decided it cannot *claim* the coils are dead must still
    send everything it sent before. Zeroed auton frame, then `d`, then `k\\n`,
    in that order, on every board.
    """
    probe = cls("SIM", None)
    probe.power_down()

    writes = _writes(probe)
    assert b"d" in writes, f"{cls.__name__}: the disable byte was not sent"
    assert b"k\n" in writes, f"{cls.__name__}: the kill-coils byte was not sent"

    zero_frame = next(
        (w for w in writes if w.startswith(b"0,0,0,")), None)
    assert zero_frame is not None, f"{cls.__name__}: no zeroed stop frame"
    assert writes.index(zero_frame) < writes.index(b"d") < writes.index(b"k\n")


# --------------------------------------------------------------------------
# The host half of SERIAL-10: report what happened, not what was hoped for.
# --------------------------------------------------------------------------


def test_dc_power_down_reports_unsupported_rather_than_success():
    probe = DCProbe("SIM", None)
    assert probe.power_down_status is None, "status before any power down"

    probe.power_down()

    assert probe.power_down_status == BaseProbe.POWER_DOWN_UNSUPPORTED


@pytest.mark.parametrize("cls", [StepperProbe, ChuckPositioner])
def test_stepper_and_chuck_power_down_report_sent(cls):
    """`'d'` (TOFF=0) has a real handler on these boards, so "sent" is honest.

    "sent" and not "confirmed": no firmware ACKs anything today, so a
    successful write is still the strongest claim available (D-7).
    """
    probe = cls("SIM", None)
    probe.power_down()
    assert probe.power_down_status == BaseProbe.POWER_DOWN_SENT


def test_dc_power_down_tells_the_operator_it_is_not_supported():
    probe = DCProbe("SIM", None)
    seen = []
    ErrorRouter.subscribe(seen.append)
    try:
        probe.power_down()
    finally:
        ErrorRouter.unsubscribe(seen.append)

    warnings = [e for e in seen if e.severity == "warning"]
    assert warnings, "a DC power down reported nothing at all to the operator"
    assert any("power down" in e.title.lower()
               or "power down" in e.message.lower() for e in warnings)


def test_dc_power_down_does_not_nag_on_every_stop():
    """Once per model, not once per FULL STOP.

    `power_down()` is on the teardown and emergency-stop paths; a popup every
    time would train the operator to dismiss the one message that matters.
    """
    probe = DCProbe("SIM", None)
    seen = []
    ErrorRouter.subscribe(seen.append)
    try:
        for _ in range(3):
            probe.power_down()
    finally:
        ErrorRouter.unsubscribe(seen.append)

    unsupported = [e for e in seen
                   if e.severity == "warning" and "power down" in e.title.lower()]
    assert len(unsupported) <= 1


def test_a_failed_kill_write_is_not_reported_as_sent():
    class DeadTransport:
        connection_state = "LOST"

        def write_command(self, payload, priority=False):
            raise RuntimeError("port is gone")

        def enable(self):
            self.write_command(b"e")

        def disable(self):
            self.write_command(b"d")

        def send_autonomous_command(self, params):
            self.write_command(b"auton")

        def send_manual_mode_command(self, params):
            self.write_command(b"manual")

        def read_position(self):
            return None

        def close(self):
            pass

    probe = StepperProbe("SIM", None)
    probe.serial_comm = DeadTransport()
    assert probe.power_down() is False
    assert probe.power_down_status == BaseProbe.POWER_DOWN_FAILED


# --------------------------------------------------------------------------
# The declared record is kept honest against the .ino files it came from.
# --------------------------------------------------------------------------


def _ino(name):
    matches = sorted((FIRMWARE / name).glob("*.ino"))
    assert matches, f"no .ino under firmware/{name}"
    return matches[0].read_text(encoding="utf-8", errors="ignore")


def test_no_firmware_handles_the_kill_coils_byte():
    """The audit's grep, encoded. `'k'` is 0x6B and has no branch anywhere.

    This is a record of fact, not a decision — if D-7 lands and a board grows
    a real `'k'` handler, this test is what makes someone update the model's
    declaration instead of leaving it silently wrong.
    """
    for board in ("stepper_firmware", "chuck_firmware", "high_polling_rate"):
        source = _ino(board)
        assert "0x6B" not in source and "0x6b" not in source, board
        assert "== 'k'" not in source, board

    for cls in (BaseProbe, StepperProbe, DCProbe, ChuckPositioner):
        assert b"k" not in cls.FIRMWARE_CONTROL_BYTES, cls.__name__


def test_declared_control_bytes_match_the_ino_files():
    stepper = _ino("stepper_firmware")
    chuck = _ino("chuck_firmware")
    dc = _ino("high_polling_rate")

    for source, board in ((stepper, "stepper"), (chuck, "chuck")):
        assert "0x64" in source, f"{board}: no 'd' branch"
        assert "0x65" in source, f"{board}: no 'e' branch"
        assert "0x73" in source, f"{board}: no 's' branch"

    assert "0x73" in dc, "DC: no 's' branch"
    assert "0x64" not in dc, "DC firmware grew a 'd' branch — D-7 may have landed"
    assert "0x65" not in dc, "DC firmware grew an 'e' branch — D-7 may have landed"

    assert StepperProbe.FIRMWARE_CONTROL_BYTES == frozenset({b"d", b"e", b"s"})
    assert ChuckPositioner.FIRMWARE_CONTROL_BYTES == frozenset({b"d", b"e", b"s"})
    assert DCProbe.FIRMWARE_CONTROL_BYTES == frozenset({b"s"})


def test_supports_coil_kill_follows_the_declared_bytes():
    assert StepperProbe("SIM", None).supports_coil_kill is True
    assert ChuckPositioner("SIM", None).supports_coil_kill is True
    assert DCProbe("SIM", None).supports_coil_kill is False
