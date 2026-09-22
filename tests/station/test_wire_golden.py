"""The firmware-compatibility gate.

Firmware is not touched by the rebuild, so the contract with the hardware is
absolute: **for the same operator action, the new backend must put exactly
the same bytes on the wire, in the same order, as the old one.** Nothing else
in this suite checks that. A refactor can keep every test green, keep every
mode machine honest, and still hand the stepper board a frame with its fields
in a different order.

Two halves:

(a) `test_golden_files_are_current` re-runs `golden/capture.py` against `src/`
    and asserts the stored JSON still matches. It fails when someone changes
    what the *old* code sends without regenerating - which means the golden
    files stopped describing the firmware's actual input, and every
    comparison below is measuring against the wrong thing.

(b) Everything else replays the captured scenarios against the new `station/`
    classes through a recording fake port and compares payload lists byte for
    byte. Those classes are stubs today, so they are `xfail(strict=False)`:
    they need no edit to go green once the real implementation lands.

`_build_probe`, `_build_heater` and `_build_smc` are the only places that
touch the new construction API. If the implementing agent's constructor
differs from the pinned interface, those three functions are the whole of
what has to change.
"""
import importlib
import json
import os

import pytest

from station.result import Refused

from tests.station.golden import capture as golden_capture

GOLDEN_DIR = os.path.dirname(os.path.abspath(golden_capture.__file__))

#: Reasons, by the file whose implementation each family is waiting on. Used
#: verbatim as the xfail reason so a run says which stub is still a stub.
AWAITING = {
    "probe": "awaiting station/models/probe.py implementation",
    "heater": "awaiting station/models/heater.py implementation",
    "smc100": "awaiting station/devices/smc100.py and "
              "station/models/rotator.py implementation",
}


def _load(filename):
    with open(os.path.join(GOLDEN_DIR, filename), encoding="utf-8") as handle:
        return json.load(handle)


def _scenarios(filename):
    return _load(filename)["scenarios"]


PROBE_SCENARIOS = _scenarios("probes.json")
HEATER_SCENARIOS = _scenarios("heater.json")
SMC_SCENARIOS = _scenarios("smc100.json")


def _ids(scenarios):
    return [scenario["id"] for scenario in scenarios]


def _expected(scenario):
    """The golden payloads for one scenario, as a list of bytes."""
    return [bytes.fromhex(frame["hex"]) for frame in scenario["frames"]]


# ==========================================================================
# (a) the golden files describe what src/ sends today
# ==========================================================================

@pytest.mark.transport
def test_golden_files_are_current():
    """Re-capture from `src/` and assert nothing drifted.

    A failure here is not a test problem. Either the old code's wire format
    changed - in which case the firmware's input changed and that is the
    thing to look at - or the capture is nondeterministic, which would make
    every comparison below worthless.
    """
    stale = []
    for filename in golden_capture.FILES:
        recaptured = golden_capture.capture_file(filename)
        stored = _load(filename)
        if recaptured != stored:
            for fresh, old in zip(recaptured["scenarios"],
                                  stored["scenarios"]):
                if fresh != old:
                    stale.append(
                        f"{filename}:{old['id']}\n"
                        f"  stored:     {[f['hex'] for f in old['frames']]}\n"
                        f"  recaptured: {[f['hex'] for f in fresh['frames']]}")
            if len(recaptured["scenarios"]) != len(stored["scenarios"]):
                stale.append(
                    f"{filename}: scenario count "
                    f"{len(stored['scenarios'])} -> "
                    f"{len(recaptured['scenarios'])}")
    assert not stale, (
        "golden wire captures are out of date; the bytes src/ sends have "
        "changed. Regenerate with `python3 tests/station/golden/capture.py` "
        "and review the diff as a firmware-facing change:\n"
        + "\n".join(stale))


@pytest.mark.transport
@pytest.mark.parametrize(
    "filename", sorted(golden_capture.FILES), ids=sorted(golden_capture.FILES))
def test_no_handshake_traffic_in_golden_bytes(filename):
    """Ping traffic is not part of any operator action.

    `serial._handshake` writes `b"s\\n"` until the board answers. Simulator
    mode never runs it, so the filter in `capture.py` should have nothing to
    do - and if it ever does, the count says so rather than a stray `s`
    becoming part of the golden protocol.
    """
    document = _load(filename)
    assert document["_meta"]["handshake_frames_filtered"] == 0
    for scenario in document["scenarios"]:
        for frame in scenario["frames"]:
            payload = bytes.fromhex(frame["hex"])
            assert payload not in golden_capture.HANDSHAKE_PAYLOADS, (
                f"{scenario['id']} contains a handshake ping")


@pytest.mark.transport
def test_every_scenario_names_its_new_class_call():
    """Each golden scenario documents what it maps to in `station/`.

    This is the porting instruction for whoever implements the model: the
    scenario says which new call is supposed to produce these bytes. A
    scenario without one is a frame nobody has been told to reproduce.
    """
    for scenario in PROBE_SCENARIOS + HEATER_SCENARIOS + SMC_SCENARIOS:
        assert scenario["new_calls"], f"{scenario['id']} has no new_calls"
        assert scenario["frames"], f"{scenario['id']} captured no bytes"


# ==========================================================================
# the recording fake port
# ==========================================================================

class RecordingPort:
    """A `SerialPort` stand-in that records payloads instead of sending them.

    Implements the pinned transport surface from the rebuild brief:
    `write(payload, *, priority=False, abort_if=None) -> bool`, returning
    False when `abort_if()` is true *inside the lock* and True when the
    payload was written. Nothing here raises `TransportError`: a golden
    comparison is about the bytes of a successful path, and the failure paths
    belong to the transport's own tests.
    """

    NAME = "RecordingPort"

    def __init__(self, identity=None):
        self.writes = []
        self.priorities = []
        self.is_open = True
        self.identity = identity
        self.status = "simulated"
        self.state = "simulated"
        self.replies = []

    # -- the write path under test ----------------------------------------
    def write(self, payload, *, priority=False, abort_if=None):
        if abort_if is not None and abort_if():
            return False
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self.writes.append(bytes(payload))
        self.priorities.append(bool(priority))
        return True

    # -- the rest of the surface, so a model can hold one ------------------
    def open(self):
        self.is_open = True

    def wait_open(self, timeout=None):
        return True

    def read_line(self, timeout=None):
        return self.replies.pop(0) if self.replies else None

    def flush(self, timeout=None):
        return True

    def close(self):
        self.is_open = False


def _payloads(port):
    return list(port.writes)


# ==========================================================================
# building the new classes (the only place that knows their constructors)
# ==========================================================================

_PROBE_CLASS_NAMES = {
    "StepperProbe": "StepperProbe",
    "DCProbe": "DCProbe",
    "ChuckPositioner": "ChuckPositioner",
}


def _build_probe(device_name, port):
    """A new `station.models.probe` instance wired to `port`.

    Per the brief every Model takes `(port=None, gamepad=None, sim=False)`
    and owns its Devices, so the port is handed in and then replaced with the
    recorder - the model must not have opened a real one. `_gamepad_bound`
    has no new-side equivalent to stub: the Gamepad is injected, and a fake
    that reports itself bound is what MANUAL needs.
    """
    module = importlib.import_module("station.models.probe")
    cls = getattr(module, _PROBE_CLASS_NAMES[device_name])
    probe = cls(port=None, gamepad=None, sim=True)
    probe.port = port
    probe.gamepad = _BoundGamepad()
    return probe, module


class _BoundGamepad:
    """Minimum Gamepad surface a probe needs to enter MANUAL."""

    is_bound = True
    is_gate_open = True
    log = []
    options = ["None"]
    levels = {}

    def open(self):
        pass

    def close(self):
        pass

    def bind(self, name=None):
        return True

    def drain_edges(self):
        return {}

    def flush_neutral(self):
        pass

    def set_gate(self, is_open):
        pass


def _build_heater(port):
    module = importlib.import_module("station.models.heater")
    heater = module.Heater(port=None, gamepad=None, sim=True)
    heater.port = port
    return heater


def _build_smc(port, replies=()):
    module = importlib.import_module("station.devices.smc100")
    device = module.SMC100(SMC_ID_FROM_CAPTURE, port)
    device.port = port
    port.replies = list(replies)
    return device


SMC_ID_FROM_CAPTURE = golden_capture.SMC_ID


# ==========================================================================
# (b) the new classes put the same bytes on the wire
# ==========================================================================

def _scenario(scenario_id):
    for group in (PROBE_SCENARIOS, HEATER_SCENARIOS, SMC_SCENARIOS):
        for scenario in group:
            if scenario["id"] == scenario_id:
                return scenario
    raise KeyError(scenario_id)


def _motion_inputs(scenario):
    """The scenario's motion parameters as `run(..., inputs=...)` would take
    them. Param names are carried over unchanged (design.rules: `PARAMS` is a
    plain dict literal on the new class)."""
    # A jog scenario's `inputs` are gamepad levels; its motion parameters are
    # the capture's fixed STEP_INPUTS (the prelude says "<motion parameters>").
    source = scenario["inputs"]
    if "x_axisStatus" in source or not source:
        source = dict(golden_capture.STEP_INPUTS)
        if scenario["device"] == "DCProbe":
            source.update(golden_capture.DC_STEP_INPUTS)
    return {name: value for name, value in source.items()
            if name in golden_capture.STEP_INPUTS
            or name in golden_capture.DC_STEP_INPUTS}


def _drive_probe(scenario, probe, module, port):
    """Run one golden probe scenario against the new class.

    The mapping is the one recorded in each scenario's `new_calls`, which
    comes from `docs/rebuild/design.rules`:

        enable / enter_auton / enter_manual / disable / full_stop
                                          -> set_mode(<ProbeMode>)
        macro_start_auton                 -> step(...)  [run("step", inputs=)]
        send_manual_mode_command(levels)  -> _send_jog(levels)
        power_down / emergency_stop       -> _halt_hardware()
    """
    mode = module.ProbeMode
    kind = scenario["id"].split(".", 1)[1]
    inputs = _motion_inputs(scenario)

    if kind == "enable":
        probe.set_mode(mode.IDLE)
        return

    if kind == "auto_and_step":
        probe.set_mode(mode.IDLE)
        _apply(probe, inputs)
        port.writes.clear()
        probe.set_mode(mode.AUTO)
        probe.step()
        return

    if kind == "step_again_while_auto":
        probe.set_mode(mode.IDLE)
        _apply(probe, inputs)
        probe.set_mode(mode.AUTO)
        probe.step()
        port.writes.clear()
        # The new Probe refuses a step while the previous one is still
        # settling (a guard against stacked moves that the old code lacked).
        # The scenario is "a second step while still AUTO", so let it settle.
        probe._moving_deadline = 0.0
        probe.step()
        return

    if kind == "manual_enter":
        probe.set_mode(mode.IDLE)
        port.writes.clear()
        probe.set_mode(mode.MANUAL)
        return

    if kind.startswith("jog."):
        probe.set_mode(mode.IDLE)
        _apply(probe, inputs)
        probe.set_mode(mode.MANUAL)
        port.writes.clear()
        levels = ({} if kind.endswith("zero_frame_on_exit")
                  else dict(scenario["inputs"]))
        probe._send_jog(levels)
        return

    if kind == "disable":
        probe.set_mode(mode.IDLE)
        port.writes.clear()
        probe.set_mode(mode.DISABLED)
        return

    if kind == "full_stop":
        probe.set_mode(mode.IDLE)
        port.writes.clear()
        probe._halt_hardware()
        return

    raise AssertionError(f"no new-class mapping for scenario {scenario['id']}")


def _apply(model, inputs):
    for name, value in inputs.items():
        setattr(model, name, value)


@pytest.mark.transport
@pytest.mark.xfail(strict=False, reason=AWAITING["probe"])
@pytest.mark.parametrize("scenario", PROBE_SCENARIOS, ids=_ids(PROBE_SCENARIOS))
def test_new_probe_is_byte_identical(scenario):
    port = RecordingPort()
    probe, module = _build_probe(scenario["device"], port)
    _drive_probe(scenario, probe, module, port)
    assert _payloads(port) == _expected(scenario), (
        f"{scenario['id']}: the new {scenario['device']} did not send the "
        f"bytes the firmware expects")


@pytest.mark.transport
@pytest.mark.xfail(strict=False, reason=AWAITING["heater"])
@pytest.mark.parametrize("scenario", HEATER_SCENARIOS,
                         ids=_ids(HEATER_SCENARIOS))
def test_new_heater_is_byte_identical(scenario):
    """`open` -> the construction frame, `apply_settings` -> the settings
    frame, `_halt_hardware` -> the off frame, `close` -> the shutdown frame.
    """
    port = RecordingPort()
    heater = _build_heater(port)
    kind = scenario["id"].split(".", 1)[1]

    if kind == "open":
        heater.open()
    elif kind == "apply_settings":
        heater.open()
        _apply(heater, scenario["inputs"])
        port.writes.clear()
        heater.apply_settings()
    elif kind == "stop":
        heater.open()
        _apply(heater, scenario["inputs"])
        port.writes.clear()
        heater._halt_hardware()
    elif kind == "close":
        heater.open()
        _apply(heater, golden_capture.HEATER_INPUTS)
        port.writes.clear()
        heater.close()
    else:
        raise AssertionError(f"no mapping for {scenario['id']}")

    if kind == "close":
        # The old `close()` alone sent only the shutdown frame; the app's real
        # exit path was `emergency_stop()` (the off frame) THEN `close()`. The
        # new `Model.close()` is that whole path, so it sends the off frame
        # first: assert the shutdown frame is last and the only extra frame
        # before it is the off frame the `stop` scenario already pins.
        payloads = _payloads(port)
        off_frame = _expected(_scenario("heater.stop"))
        assert payloads[-len(_expected(scenario)):] == _expected(scenario), (
            "heater.close: the shutdown frame is not the last thing on the wire")
        assert payloads[:-len(_expected(scenario))] in ([], off_frame), (
            f"heater.close: unexpected frames before shutdown: {payloads}")
        return
    assert _payloads(port) == _expected(scenario), (
        f"{scenario['id']}: the new Heater did not send the bytes the "
        f"firmware expects")


@pytest.mark.transport
@pytest.mark.xfail(strict=False, reason=AWAITING["smc100"])
@pytest.mark.parametrize("scenario", SMC_SCENARIOS, ids=_ids(SMC_SCENARIOS))
def test_new_smc100_is_byte_identical(scenario):
    """The SMC100 keeps its vendor protocol method names, so the mapping is
    one-to-one. Only the transport underneath it changes: raw port I/O moves
    onto the shared `SerialPort`, which is what `RecordingPort` stands in for.

    Note the captured frames keep `sendcmd`'s two-write split (command, then
    CRLF). If the new implementation writes one payload instead, this fails -
    deliberately. The bytes on the wire are the same either way, but write
    granularity is the kind of thing that should be changed on purpose and
    reviewed, not discovered at the bench.
    """
    replies = scenario["inputs"].get("scripted_replies", [])
    port = RecordingPort()
    device = _build_smc(port, replies)
    kind = scenario["id"].split(".", 1)[1]

    if kind == "home":
        device.home(waitStop=True)
    elif kind == "home_no_wait":
        device.home(waitStop=False)
    elif kind == "move_absolute":
        device.move_absolute_deg(scenario["inputs"]["position_deg"],
                                 waitStop=False)
    elif kind in ("move_relative_positive", "move_relative_negative"):
        device.move_relative_deg(scenario["inputs"]["dist_deg"],
                                 waitStop=False)
    elif kind == "stop":
        device.stop()
    elif kind == "stop_priority":
        device.stop(priority=True)
    elif kind == "status":
        device.get_status(silent=True)
    elif kind == "position":
        device.get_position_deg()
    elif kind == "reset_and_configure":
        device.reset_and_configure()
    else:
        raise AssertionError(f"no mapping for {scenario['id']}")

    assert _payloads(port) == _expected(scenario), (
        f"{scenario['id']}: the new SMC100 did not send the bytes the "
        f"controller expects")


@pytest.mark.transport
@pytest.mark.xfail(strict=False, reason=AWAITING["probe"])
def test_full_stop_uses_the_priority_lane():
    """The stop bytes are not enough on their own.

    `_halt_hardware` must reach the wire on the priority lane, or a poll
    holding the transaction lock can delay it (RC-5 item 2). Byte equality
    would pass with `priority=False`, so the lane is asserted separately.
    """
    port = RecordingPort()
    probe, module = _build_probe("StepperProbe", port)
    probe.set_mode(module.ProbeMode.IDLE)
    port.writes.clear()
    port.priorities.clear()
    probe._halt_hardware()
    assert port.priorities and all(port.priorities), (
        "every FULL STOP payload must be written with priority=True")


@pytest.mark.transport
@pytest.mark.xfail(strict=False, reason=AWAITING["probe"])
def test_motion_writes_pass_the_estop_as_abort_if():
    """A latched FULL STOP must abort a motion write *inside the lock*.

    Checking the latch before calling `write` is a check-then-act: the write
    can then wait on the transport lock for as long as a reader's readline
    while the stop forces its own frame past it, and the motion frame lands
    afterwards (TEMP-17, and finding 3 in design.rules). `abort_if` is what
    orders the two, so a motion write that ignores it is wrong even though
    its bytes are right.
    """
    port = RecordingPort()
    probe, module = _build_probe("StepperProbe", port)
    probe.set_mode(module.ProbeMode.IDLE)
    _apply(probe, golden_capture.STEP_INPUTS)
    probe.set_mode(module.ProbeMode.AUTO)
    port.writes.clear()
    seen = []
    real_write = port.write

    def write_with_latch_landing_inside(payload, *, priority=False, abort_if=None):
        # The stop lands while this write holds the lock: the pre-check has
        # already passed, so only `abort_if` can stop the frame.
        probe._estop.set()
        seen.append(abort_if)
        if abort_if is not None and abort_if():
            return False
        return real_write(payload, priority=priority, abort_if=abort_if)

    port.write = write_with_latch_landing_inside
    with pytest.raises(Refused):     # aborted inside the lock, and said so
        probe.step()
    assert seen and seen[0] is not None, "the motion write passed no abort_if"
    assert _payloads(port) == [], (
        "a motion frame was written although FULL STOP latched inside the lock")
