"""The OLD side of `test_probe_frames.py` and `test_heater_frames.py`, run
in its own process.

Those two tests drive the old classes (`legacy/src/`) and the new ones
(`src/`) through the same operations and compare the bytes. The two trees
share top-level package names (`model`, `controller`, `views`), so they can
never be imported into one process; this module is the old half, moved here
verbatim from the two test files, and each test runs it with
`sys.executable` and reads the payloads back as hex::

    python3 tests/golden/old_frames.py '{"probe": "DCProbe", "levels": {...}, "distances": {...}}'
    python3 tests/golden/old_frames.py '{"heater": ["45.5", "12", ...]}'
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LEGACY_SRC = os.path.abspath(os.path.join(_HERE, "..", "..", "legacy", "src"))

FIELDS = ("setpoint", "ramp_rate", "p_term", "i_term", "d_term", "offset")


# -- probes (was test_probe_frames.py's old side) ---------------------------

class _OldPoller:
    """Stands in for ControllerPoller: bound, with a fixed mapped state."""

    def __init__(self, levels):
        self.gamepad = object()
        self.controllerID = "Pad0"
        self._levels = dict(levels)

    def get_mapped_state(self):
        return dict(self._levels)

    def get_physical_controllers(self):
        return ["Pad0"]

    def start_polling(self, *args, **kwargs):
        pass

    def stop_polling(self):
        pass

    def close(self):
        pass


def _old_sequences(name, LEVELS, DISTANCES):
    """Every byte the old probe of this name writes, per operation."""
    from model.probes import ProbeMode as OldMode
    from model import probes as old

    probe = getattr(old, name)("SIM", None)
    probe.stop_loops()
    real_poller, probe.poller = probe.poller, _OldPoller(LEVELS)
    port = probe.serial_comm.ser

    def capture(fn):
        port.writes.clear()
        fn()
        captured = list(port.writes)
        port.writes.clear()
        return captured

    try:
        for field, value in DISTANCES.items():
            setattr(probe, field, value)
        out = {"enable": capture(probe.serial_comm.enable)}

        probe._mode = OldMode.AUTONOMOUS
        out["step"] = capture(
            lambda: probe.serial_comm.send_autonomous_command(probe.get_params()))

        probe._mode = OldMode.MANUAL
        out["jog"] = capture(lambda: probe.send_manual_mode_command(LEVELS))

        out["zero"] = capture(probe.send_stop_command)
        out["disable"] = capture(
            lambda: (probe.send_stop_command(), probe.serial_comm.disable()))

        probe._mode = OldMode.AUTONOMOUS
        out["halt"] = capture(probe.power_down)
        return out
    finally:
        probe.stop_loops()
        for candidate in (real_poller,):
            try:
                candidate.close()
            except Exception:
                pass


# -- heater (was test_heater_frames.py's old side) --------------------------

def _old_frames(values):
    """`(reset, settings, heater_off)` as TemperatureSystem writes them."""
    from model.temperature_system import TemperatureSystem
    old = TemperatureSystem(port="SIM")
    # Park the reader immediately: it has nothing to read from a simulated
    # port and we are only interested in what gets written.
    old.continue_reading = False
    old._reader_wake.set()
    wire = old.serial_conn.ser
    reset = list(wire.writes)
    assert len(reset) == 1, f"the old constructor wrote {reset}"

    for name, text in zip(FIELDS, values):
        setattr(old, name, text)

    wire.writes.clear()
    old.send_settings()
    settings = list(wire.writes)
    assert len(settings) == 1, f"old send_settings wrote {settings}"

    wire.writes.clear()
    old.stop()
    heater_off = list(wire.writes)
    assert len(heater_off) == 1, f"old stop wrote {heater_off}"
    return reset[0], settings[0], heater_off[0]


def run(request):
    """One request in, the old side's payloads out, every bytes as hex."""
    if "probe" in request:
        sequences = _old_sequences(request["probe"], request["levels"],
                                   request["distances"])
        return {op: [w.hex() for w in writes]
                for op, writes in sequences.items()}
    return [frame.hex() for frame in _old_frames(tuple(request["heater"]))]


def main(argv):
    sys.path.insert(0, _LEGACY_SRC)
    # The old code prints its own chatter (destructors included, at exit);
    # stdout carries the JSON answer and nothing else.
    real_stdout, sys.stdout = sys.stdout, sys.stderr
    answer = run(json.loads(argv[1]))
    json.dump(answer, real_stdout)
    real_stdout.write("\n")
    real_stdout.flush()


if __name__ == "__main__":
    main(sys.argv)
