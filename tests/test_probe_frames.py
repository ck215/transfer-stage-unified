"""Golden frames: the rebuilt Probe puts the SAME bytes on the wire as `legacy/src/`.

Firmware is not being touched, so this is the test that has to hold before any
other probe test is worth reading. It does not hardcode the frames: it drives
the **old** classes (`model.probes`) in simulator mode, records what
`SimulatedPort` actually received, and asserts the new `model.probe`
produces byte-identical sequences for all three probe types.

The old side is reached through the byte-producing methods themselves
(`serial.enable`, `serial.disable`, `serial.send_autonomous_command`,
`BaseProbe.send_manual_mode_command`, `send_stop_command`, `power_down`) rather
than through `_transition`, because `_transition` starts the old model-owned
loop threads and a 50 Hz jog pump writing concurrently would make the capture
nondeterministic. The bytes are the same either way; that is what this file is
about.

Three per-class differences this covers by construction:

* step sizes: StepperProbe 1, ChuckPositioner 2, DCProbe 1;
* speeds: DCProbe declares 120 where the others declare 400, so its jog packet
  carries a different float in the last slot;
* `slow_speed` / `brake_distance`: literal ints (`0`) in the base frame, and
  *coerced floats* (`0.0`) in DCProbe's, which is a real byte difference in an
  ASCII frame.
"""
import json
import pathlib
import subprocess
import sys

import pytest

from model.probe import ChuckPositioner, DCProbe, StepperProbe

pytestmark = pytest.mark.transport

#: Deliberately asymmetric, and off-neutral on every axis the packet carries.
LEVELS = {
    "x_axisStatus": 0.5, "y_axisStatus": -0.25,
    "z_axisStatusL": 0.75, "z_axisStatusR": -1.0,
    "dpad_LR": 1, "dpad_UD": -1, "LBumper": 1, "RBumper": 0,
}

#: Strings on both sides: the old store held whatever the view typed, and the
#: coercion that turns it into a number is part of what must not change.
DISTANCES = {"x_dist": "5", "y_dist": "-3", "z_dist": "2"}

PAIRS = [("StepperProbe", StepperProbe), ("DCProbe", DCProbe),
         ("ChuckPositioner", ChuckPositioner)]
SEQUENCES = ("enable", "step", "jog", "zero", "disable", "halt")

OLD_SIDE = pathlib.Path(__file__).resolve().parent / "golden" / "old_frames.py"


# -- the old side -----------------------------------------------------------

def _run_old_side(request):
    """The old classes, in their own process (`golden/old_frames.py`): the old
    `legacy/src/` and the new `src/` share top-level package names."""
    completed = subprocess.run(
        [sys.executable, str(OLD_SIDE), json.dumps(request)],
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _old_sequences(name):
    """Every byte the old probe of this name writes, per operation."""
    answer = _run_old_side({"probe": name, "levels": LEVELS,
                            "distances": DISTANCES})
    return {op: [bytes.fromhex(w) for w in writes]
            for op, writes in answer.items()}


# -- the new side -----------------------------------------------------------

class _NewPort:
    """Records payloads the way SimulatedPort does, on the pinned interface."""

    status = "simulated"
    is_open = True

    def __init__(self):
        self.writes = []
        self.calls = []

    def open(self):
        pass

    def close(self):
        pass

    def write(self, payload, *, priority=False, abort_if=None):
        self.calls.append((payload, priority, abort_if))
        if abort_if is not None and abort_if():
            return False
        self.writes.append(payload)
        return True

    def read_line(self, timeout=None):
        return None


class _NewGamepad:
    status = "connected"
    is_bound = True
    is_gate_open = True
    name = "Pad0"
    log = ()

    def __init__(self, levels):
        self.levels = dict(levels)
        self.options = ["None", "Pad0"]

    def open(self):
        pass

    def close(self):
        pass

    def bind(self, name):
        return True

    def set_gate(self, is_open):
        self.is_gate_open = bool(is_open)


def _new_sequences(cls):
    port = _NewPort()
    probe = cls(port=port, gamepad=_NewGamepad(LEVELS))

    def capture(fn):
        port.writes.clear()
        fn()
        captured = list(port.writes)
        port.writes.clear()
        return captured

    for field, value in DISTANCES.items():
        setattr(probe, field, value)
    out = {"enable": capture(lambda: probe._energize("golden"))}

    probe.set_mode("autonomous")
    out["step"] = capture(probe._send_move)

    probe.set_mode("manual")
    out["jog"] = capture(lambda: probe._send_jog(LEVELS))

    out["zero"] = capture(lambda: probe._send_zero_frame("golden"))
    out["disable"] = capture(lambda: probe._deenergize("golden"))

    probe.set_mode("autonomous")
    out["halt"] = capture(probe._halt_hardware)
    return out


# -- the comparison ---------------------------------------------------------

@pytest.fixture(scope="module")
def sequences():
    return {name: (_old_sequences(name), _new_sequences(cls))
            for name, cls in PAIRS}


@pytest.mark.parametrize("name", [n for n, _ in PAIRS])
@pytest.mark.parametrize("operation", SEQUENCES)
def test_the_new_probe_writes_the_old_bytes(sequences, name, operation):
    old, new = sequences[name]
    assert new[operation] == old[operation], (
        f"{name}.{operation}: wire changed\n  old: {old[operation]!r}\n"
        f"  new: {new[operation]!r}")


@pytest.mark.parametrize("name", [n for n, _ in PAIRS])
def test_the_jog_packet_is_still_42_bytes(sequences, name):
    old, new = sequences[name]
    assert [len(w) for w in new["jog"]] == [42]
    assert new["jog"][0][:1] == b"\xaa"
    assert new["jog"] == old["jog"]


@pytest.mark.parametrize("name", [n for n, _ in PAIRS])
def test_the_stop_path_is_zero_frame_then_d_then_k(sequences, name):
    """The order is the safety property, not an accident of the capture."""
    _, new = sequences[name]
    halt = new["halt"]
    assert halt[0].startswith(b"0,0,0,")
    assert halt[1:] == [b"d", b"k\n"]


def test_the_dc_frame_carries_float_brake_fields_and_the_others_do_not(sequences):
    """A real byte difference the per-class frame has to keep.

    `BaseProbe.get_params` put literal ints in `slow_speed`/`brake_distance`;
    `DCProbe.get_params` put its own coerced floats there. `0` and `0.0` are
    different bytes in an ASCII frame.
    """
    stepper = sequences["StepperProbe"][1]["step"][0]
    dc = sequences["DCProbe"][1]["step"][0]
    assert stepper.split(b",")[5:7] == [b"0", b"0"]
    assert dc.split(b",")[5:7] == [b"0.0", b"0.0"]


def test_the_per_class_step_sizes_reach_the_wire(sequences):
    assert sequences["StepperProbe"][1]["step"][0].startswith(b"1,1,1,0,")
    assert sequences["ChuckPositioner"][1]["step"][0].startswith(b"2,2,2,0,")
    assert sequences["DCProbe"][1]["step"][0].startswith(b"1,1,1,0,")


def test_the_dc_probe_jogs_at_its_own_declared_speed(sequences):
    """RC-6: the fallback belongs to the class. A DC probe runs at 120, and the
    last float of its jog packet has to say so."""
    import struct
    dc = struct.unpack("<BBffffffffff", sequences["DCProbe"][1]["jog"][0])
    stepper = struct.unpack("<BBffffffffff", sequences["StepperProbe"][1]["jog"][0])
    assert dc[-1] == 120.0
    assert stepper[-1] == 400.0
