"""Hotfix regression tests: raw Tk Entry text must never drop a manual packet.

The manual-mode packet is written every 5 ms while an operator drives a stage.
The firmware has no host-liveness timeout, so a packet that is never written
leaves the last commanded velocity in force -- the "drift" the operator saw.
These tests pin the coercion of raw Entry text and the fail-safe stop packet.
No hardware: SerialArduino is built in 'SIM' mode and handed a fake port.
"""

import os
import struct
import sys

import pytest
from unittest.mock import MagicMock


class MockSerialModule(MagicMock):
    pass


mock_serial = MockSerialModule()
mock_serial.SerialTimeoutException = Exception

# Mock serial module before importing src modules (same convention as the
# other edge-case suites in tests/)
sys.modules['serial'] = mock_serial
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()
sys.modules['pygame'] = MagicMock()
sys.modules['gcodeparser'] = MagicMock()
sys.modules['color_test_new'] = MagicMock()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from serialDrive import SerialArduino, PACKET_FORMAT, START_MARKER  # noqa: E402

PACKET_SIZE = struct.calcsize('<BBfffhhhhhhh')


class FakeSerial:
    """Minimal stand-in for a pyserial Serial object."""

    def __init__(self, fail_writes=0):
        self.is_open = True
        self.in_waiting = 0
        self.writes = []
        self._fail_writes = fail_writes
        self.write_calls = 0

    def write(self, data):
        self.write_calls += 1
        if self.write_calls <= self._fail_writes:
            raise RuntimeError("simulated serial write failure")
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.is_open = False


def make_arduino(fail_writes=0):
    ard = SerialArduino(port='SIM')
    ard.ser = FakeSerial(fail_writes=fail_writes)
    return ard


def manual_params(**overrides):
    params = {
        'x_axisStatus': 0.0,
        'y_axisStatus': 0.0,
        'z_axisStatusL': -1.0,
        'z_axisStatusR': -1.0,
        'x_stepSize': '100',
        'y_stepSize': '100',
        'z_stepSize': '100',
        'dpad_LR': 0,
        'dpad_UD': 0,
        'LBumper': 0,
        'RBumper': 0,
        'manual_jog_speed': '400',
    }
    params.update(overrides)
    return params


def auton_params(**overrides):
    params = {
        'command_code_auton': 3,
        'command_code_manual': 0,
        'x_step_size': '100',
        'y_step_size': '100',
        'z_step_size': '100',
        'full_speed': '400',
        'slow_speed': '50',
        'brake_distance': '10',
        'x_dist': '5',
        'y_dist': '5',
        'z_dist': '5',
    }
    params.update(overrides)
    return params


def unpack_only_packet(ard):
    assert len(ard.ser.writes) == 1, (
        "expected exactly one packet to be written, got "
        f"{len(ard.ser.writes)} -- a dropped packet leaves the firmware at its "
        "last commanded velocity (drift)"
    )
    packet = ard.ser.writes[0]
    assert len(packet) == PACKET_SIZE
    return struct.unpack(PACKET_FORMAT, packet)


# --------------------------------------------------------------------------
# manual jog speed -- the field the operator reported as "decimal does nothing"
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("0.5", 0),
    ("", 0),
    ("   ", 0),
    (" 400 ", 400),
    ("1e3", 1000),
    ("400", 400),
    ("-250.9", -250),
    ("abc", 0),
    (None, 0),
])
def test_manual_jog_speed_text_is_coerced_not_dropped(raw, expected):
    ard = make_arduino()
    ard.send_manual_mode_command(manual_params(manual_jog_speed=raw))
    fields = unpack_only_packet(ard)
    assert fields[11] == expected


@pytest.mark.parametrize("raw,expected", [
    ("0.5", 0),
    ("", 0),
    (" 400 ", 400),
    ("1e3", 1000),
    ("25.9", 25),
])
def test_manual_step_sizes_text_is_coerced_not_dropped(raw, expected):
    ard = make_arduino()
    ard.send_manual_mode_command(
        manual_params(x_stepSize=raw, y_stepSize=raw, z_stepSize=raw)
    )
    fields = unpack_only_packet(ard)
    assert (fields[5], fields[6], fields[7]) == (expected, expected, expected)


def test_manual_packet_is_exact_size_and_unpacks_to_expected_values():
    ard = make_arduino()
    ard.send_manual_mode_command(manual_params(
        x_axisStatus=0.5,
        y_axisStatus=-0.25,
        z_axisStatusL=1.0,    # full up
        z_axisStatusR=-1.0,   # idle
        x_stepSize=' 400 ',
        y_stepSize='0.5',
        z_stepSize='',
        dpad_LR=1,
        dpad_UD=-1,
        LBumper=1,
        RBumper=0,
        manual_jog_speed='1e3',
    ))
    assert len(ard.ser.writes) == 1
    packet = ard.ser.writes[0]
    assert len(packet) == PACKET_SIZE
    fields = struct.unpack(PACKET_FORMAT, packet)
    assert fields[0] == START_MARKER
    assert fields[1] == 1                      # manual mode byte
    # These are exactly representable in float32, so compare directly.
    # (pytest.approx is deliberately avoided: it probes sys.modules['numpy'],
    #  which sibling suites replace with a MagicMock.)
    assert fields[2] == 0.5
    assert fields[3] == -0.25
    assert fields[4] == 1.0                    # (1+1)/2 - (-1+1)/2
    assert fields[5] == 400
    assert fields[6] == 0
    assert fields[7] == 0
    assert fields[8] == 1
    assert fields[9] == -1
    assert fields[10] == 1                     # LBumper - RBumper
    assert fields[11] == 1000


def test_manual_never_raises_on_garbage_params():
    ard = make_arduino()
    # Missing keys must still not propagate an exception to the 5 ms caller.
    ard.send_manual_mode_command({})


# --------------------------------------------------------------------------
# fail-safe: a write failure must still attempt an explicit STOP
# --------------------------------------------------------------------------

def test_write_failure_attempts_mode_zero_stop_packet():
    ard = make_arduino(fail_writes=1)
    ard.send_manual_mode_command(manual_params())

    assert ard.ser.write_calls == 2, (
        "after a failed manual write the driver must make one best-effort "
        "STOP write; the firmware otherwise holds the last velocity"
    )
    assert len(ard.ser.writes) == 1
    stop = ard.ser.writes[0]
    assert len(stop) == PACKET_SIZE
    fields = struct.unpack(PACKET_FORMAT, stop)
    assert fields[0] == START_MARKER
    assert fields[1] == 0                       # handleAllStop
    assert fields[2:] == (0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0)


def test_stop_packet_failure_is_swallowed():
    ard = make_arduino(fail_writes=2)
    ard.send_manual_mode_command(manual_params())
    assert ard.ser.write_calls == 2
    assert ard.ser.writes == []


def test_manual_does_nothing_when_port_closed():
    ard = make_arduino()
    ard.ser.is_open = False
    ard.send_manual_mode_command(manual_params())
    assert ard.ser.write_calls == 0


# --------------------------------------------------------------------------
# autonomous text protocol -- firmware parses these with Arduino toInt()
# --------------------------------------------------------------------------

def decode_auton(ard):
    assert len(ard.ser.writes) == 1
    return ard.ser.writes[0].decode('utf-8').strip().split(',')


def test_auton_decimal_and_empty_numeric_fields_become_int_text():
    ard = make_arduino()
    ard.send_autonomous_command(auton_params(
        x_step_size='0.5',
        y_step_size='',
        z_step_size=' 400 ',
        full_speed='0.5',
        x_dist='',
        y_dist='1e3',
        z_dist='-2.9',
    ))
    parts = decode_auton(ard)
    assert len(parts) == 12
    assert parts[0] == '0'      # x_step_size "0.5"
    assert parts[1] == '0'      # y_step_size ""
    assert parts[2] == '400'    # z_step_size " 400 "
    assert parts[3] == '0'      # empty_data placeholder
    assert parts[4] == '0'      # full_speed "0.5"
    assert parts[7] == '0'      # x_dist ""
    assert parts[8] == '1000'   # y_dist "1e3"
    assert parts[9] == '-2'     # z_dist "-2.9"


def test_auton_command_codes_are_untouched():
    ard = make_arduino()
    ard.send_autonomous_command(auton_params(
        command_code_manual=0, command_code_auton=3
    ))
    parts = decode_auton(ard)
    assert parts[10] == '0'
    assert parts[11] == '3'
