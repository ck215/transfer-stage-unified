"""Capture every byte the OLD code (`src/`) puts on the wire, as JSON.

Firmware is not touched by the rebuild, so the only definition of "the new
backend is correct on the wire" is: **identical bytes, in identical order,
for the same operator action**. This module drives the old models in
simulator mode, records the payloads, and writes them to the JSON files
beside it. `tests/station/test_wire_golden.py` then (a) re-runs this and
asserts the JSON is current, and (b) replays the same scenarios against the
new `station/` classes and compares byte for byte.

Regenerate with::

    python3 tests/station/golden/capture.py

from the repository root. It rewrites `probes.json`, `heater.json` and
`smc100.json` in place. **A diff in those files is a change to what the
hardware receives** — it is never a routine update, and a reviewer should
be able to read the `decode` field and say what moved.


What is captured, and from where
--------------------------------

* Probes — `model.probes.StepperProbe / DCProbe / ChuckPositioner`, built on
  port ``"SIM"``. The transport (`controller.serial.serial`) then installs a
  `SimulatedPort`, whose ``.writes`` list is every payload handed to
  ``pyserial.write()``, in order. That is the capture.
* Heater — `model.temperature_system.TemperatureSystem`, likewise on
  ``"SIM"``.
* SMC100 — `lib.smc100.SMC100` has no simulator mode and talks to pyserial
  directly, so `_RecordingSMCPort` below is substituted for
  ``smc100.serial.Serial``. It records writes and serves a **fixed, scripted**
  reply queue, so the read-driven paths (`wait_states`, `get_status`,
  `reset_and_configure`) take one determined route.


How capture is made deterministic
---------------------------------

Every source of nondeterminism is removed explicitly rather than by
sleeping and hoping:

1. **The models' background loops are stopped before anything is recorded.**
   `_quiesce_probe` replaces `start_loops` / `stop_loops` /
   `_start_interlock_watchdog` / `_stop_interlock_watchdog` with no-ops on
   the instance. Left alone, `_transition` starts a 50 Hz jog pump and a
   10 Hz sampler, and the jog pump writes a manual frame every 20 ms — those
   frames would interleave with the scenario's own writes at an arbitrary
   index. The idle interlock is worse: it can FULL STOP the probe mid-capture.
2. **The gamepad is removed.** `probe.poller = None` and `_gamepad_bound` is
   forced True. The real poller reads live SDL state, so a jog frame built
   from it is whatever the bench's controller happened to be doing. Every
   jog frame here is built from a literal level dict instead (`GAMEPAD_LEVELS`).
3. **The heater's reader thread is stopped and joined** right after
   construction. It never writes, but it holds the port and would keep the
   process alive.
4. **The SMC100 never sleeps**: `sleepfunc=lambda _: None`. Its
   `COMMAND_WAIT_TIME_SEC` pacing and `reset_and_configure`'s 3 s settle are
   pure wall-clock waits and do not touch the wire.
5. **Nothing captured carries a timestamp, a counter or a random value**, so
   two runs of this file produce byte-identical JSON. That is what makes
   test (a) in `test_wire_golden.py` a meaningful assertion rather than a
   tautology.

Handshake / ping traffic
------------------------

`controller.serial.serial._handshake` writes ``b"s\\n"`` up to once per
`PING_INTERVAL` until the board answers with a ``DEV:`` line. It is transport
liveness, not an operator action, so it must not appear in a golden frame
list. Two things keep it out:

* In simulator mode the handshake **never runs at all** — `serial.__init__`
  installs the `SimulatedPort` and returns before `_connect_thread` is
  created. So the captures below contain no ping traffic by construction.
* `_strip_handshake` filters ``b"s"`` / ``b"s\\n"`` anyway and records how
  many it dropped in each file's ``_meta.handshake_frames_filtered``. That
  number is **0** in every stored capture. It is asserted, not assumed: if a
  future capture is ever taken against a real port, the ping traffic is
  removed from the frame list and the count says so out loud instead of a
  stray ``s`` silently becoming part of the golden protocol.

The SMC100 has no equivalent host-side handshake inside `lib/smc100.py`
(`app_bootstrap` does its own identity probe, which is not this module's
subject). Its ``1TS?`` status polling *is* captured, because on that device a
status read is a scenario the rebuild has to reproduce — but only the polls
the scenario itself issues: `RotatorSystem` is deliberately never constructed
here, so its background `_sample_loop` poll cannot leak in.
"""
import json
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))


# --------------------------------------------------------------------------
# Fixed inputs. Every number below is a literal on purpose: a golden capture
# driven from a class default would silently change if the default changed.
# --------------------------------------------------------------------------

#: Motion parameters written before a step, while the probe is still IDLE.
#: They cannot be written later — `_mode_gated_param` refuses a write to a
#: motion parameter while AUTONOMOUS or MANUAL (DC-6), which is itself part
#: of the behaviour being preserved.
STEP_INPUTS = {
    "x_step": "4",
    "y_step": "8",
    "z_step": "16",
    "x_dist": "10",
    "y_dist": "20",
    "z_dist": "-5",
    "full_speed": "250",
    "man_full_speed": "175",
}

#: DC-only extras: `DCProbe.get_params` adds these two fields to the frame.
DC_STEP_INPUTS = {"slow_speed": "40", "brake_distance": "12"}

#: The gamepad level dicts jog frames are built from. Keys are the poller's
#: mapped-state keys. Triggers idle at -1.0 and are remapped to [0, 1] by
#: `serial.send_manual_mode_command`; sticks and d-pad are already signed.
GAMEPAD_LEVELS = {
    "neutral": {
        "x_axisStatus": 0.0, "y_axisStatus": 0.0,
        "z_axisStatusL": -1.0, "z_axisStatusR": -1.0,
        "dpad_LR": 0, "dpad_UD": 0, "LBumper": 0, "RBumper": 0,
    },
    "x_full_positive": {
        "x_axisStatus": 1.0, "y_axisStatus": 0.0,
        "z_axisStatusL": -1.0, "z_axisStatusR": -1.0,
        "dpad_LR": 0, "dpad_UD": 0, "LBumper": 0, "RBumper": 0,
    },
    "x_full_negative": {
        "x_axisStatus": -1.0, "y_axisStatus": 0.0,
        "z_axisStatusL": -1.0, "z_axisStatusR": -1.0,
        "dpad_LR": 0, "dpad_UD": 0, "LBumper": 0, "RBumper": 0,
    },
    "y_full_positive": {
        "x_axisStatus": 0.0, "y_axisStatus": 1.0,
        "z_axisStatusL": -1.0, "z_axisStatusR": -1.0,
        "dpad_LR": 0, "dpad_UD": 0, "LBumper": 0, "RBumper": 0,
    },
    "y_full_negative": {
        "x_axisStatus": 0.0, "y_axisStatus": -1.0,
        "z_axisStatusL": -1.0, "z_axisStatusR": -1.0,
        "dpad_LR": 0, "dpad_UD": 0, "LBumper": 0, "RBumper": 0,
    },
    "z_trigger_left_full": {
        "x_axisStatus": 0.0, "y_axisStatus": 0.0,
        "z_axisStatusL": 1.0, "z_axisStatusR": -1.0,
        "dpad_LR": 0, "dpad_UD": 0, "LBumper": 0, "RBumper": 0,
    },
    "z_trigger_right_full": {
        "x_axisStatus": 0.0, "y_axisStatus": 0.0,
        "z_axisStatusL": -1.0, "z_axisStatusR": 1.0,
        "dpad_LR": 0, "dpad_UD": 0, "LBumper": 0, "RBumper": 0,
    },
    "dpad_left_right": {
        "x_axisStatus": 0.0, "y_axisStatus": 0.0,
        "z_axisStatusL": -1.0, "z_axisStatusR": -1.0,
        "dpad_LR": 1, "dpad_UD": 0, "LBumper": 0, "RBumper": 0,
    },
    "dpad_up_down": {
        "x_axisStatus": 0.0, "y_axisStatus": 0.0,
        "z_axisStatusL": -1.0, "z_axisStatusR": -1.0,
        "dpad_LR": 0, "dpad_UD": -1, "LBumper": 0, "RBumper": 0,
    },
    "bumper_left": {
        "x_axisStatus": 0.0, "y_axisStatus": 0.0,
        "z_axisStatusL": -1.0, "z_axisStatusR": -1.0,
        "dpad_LR": 0, "dpad_UD": 0, "LBumper": 1, "RBumper": 0,
    },
    "bumper_right": {
        "x_axisStatus": 0.0, "y_axisStatus": 0.0,
        "z_axisStatusL": -1.0, "z_axisStatusR": -1.0,
        "dpad_LR": 0, "dpad_UD": 0, "LBumper": 0, "RBumper": 1,
    },
    "all_deflected": {
        "x_axisStatus": 1.0, "y_axisStatus": -1.0,
        "z_axisStatusL": 1.0, "z_axisStatusR": -1.0,
        "dpad_LR": 1, "dpad_UD": -1, "LBumper": 1, "RBumper": 0,
    },
}

#: The frame `_input_loop` sends once on leaving manual mode (I-4.2): an
#: empty dict, which the transport fills from its own defaults. Kept separate
#: from `GAMEPAD_LEVELS["neutral"]` because it is a *different call*, and the
#: two producing the same bytes is the property worth pinning.
JOG_EXIT_LEVELS = {}

#: Heater settings frame inputs.
HEATER_INPUTS = {
    "setpoint": "120.5",
    "ramp_rate": "7.25",
    "p_term": "2.5",
    "i_term": "0.75",
    "d_term": "0.125",
    "offset": "-1.5",
}

#: Fixed SMC100 controller id, as `RotatorSystem` uses (`self.smc_id = 1`).
SMC_ID = 1

#: Payloads that are transport liveness, not operator actions. See the module
#: docstring. Never present in a simulator-mode capture; filtered regardless.
HANDSHAKE_PAYLOADS = (b"s", b"s\n")


# --------------------------------------------------------------------------
# Recording and decoding
# --------------------------------------------------------------------------

def _strip_handshake(writes):
    """(frames, dropped_count) with handshake pings removed."""
    kept = [payload for payload in writes if payload not in HANDSHAKE_PAYLOADS]
    return kept, len(writes) - len(kept)


_AUTON_FIELDS = (
    "x_step_size", "y_step_size", "z_step_size", "target_steps_unused",
    "full_speed", "slow_speed", "brake_distance",
    "x_dist", "y_dist", "z_dist",
    "command_code_manual", "command_code_auton",
)

_JOG_FIELDS = (
    "start_marker", "packet_type",
    "x_axis", "y_axis", "z_axis_combined",
    "x_step_size", "y_step_size", "z_step_size",
    "dpad_LR", "dpad_UD", "bumpers_combined", "manual_jog_speed",
)

_CONTROL_BYTES = {
    b"e": "control byte 'e' (0x65) - energize. Handled by stepper/chuck "
          "firmware; falls through to text on the DC board (SERIAL-10).",
    b"d": "control byte 'd' (0x64) - de-energize, TOFF=0 on all three "
          "TMC2209 drivers. The DC board has no handler; it reads as text "
          "and produces a halt, not a de-energize (SERIAL-10).",
    b"k\n": "control byte 'k' (0x6B) + LF - coil kill. **No firmware on any "
            "board has a handler for this.** It is sent unchanged because "
            "this is a stop path; making it real is owner decision D-7.",
}


def _decode_probe(payload):
    """A human-readable reading of one probe payload, or None."""
    if payload in _CONTROL_BYTES:
        return {"kind": "control_byte", "text": _CONTROL_BYTES[payload]}
    if payload[:1] == b"\xaa":
        values = struct.unpack("<BBffffffffff", payload)
        return {
            "kind": "jog_packet",
            "format": "<BBffffffffff (42 bytes)",
            "fields": dict(zip(_JOG_FIELDS, list(values))),
        }
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError:
        return None
    if text.endswith("\n") and text.count(",") == 11:
        return {
            "kind": "autonomous_frame",
            "format": "12 comma-separated fields, LF-terminated",
            "text": text.strip(),
            "fields": dict(zip(_AUTON_FIELDS, text.strip().split(","))),
        }
    return {"kind": "ascii", "text": text}


_HEATER_FIELDS = ("setpoint", "spdelay_s_per_C", "p", "i", "d", "offset")


def _decode_heater(payload):
    text = payload.decode("ascii")
    body = text.strip("<>")
    return {
        "kind": "heater_frame",
        "format": "<setpoint,spdelay,P,I,D,offset>",
        "text": text,
        "fields": dict(zip(_HEATER_FIELDS, body.split(","))),
    }


def _decode_smc(payload):
    text = payload.decode("ascii")
    if payload == b"\r\n":
        return {"kind": "terminator", "text": "CRLF"}
    command = text[len(str(SMC_ID)):]
    return {
        "kind": "command",
        "format": "<ID><command><argument>, CRLF sent as a separate write",
        "text": text,
        "controller_id": str(SMC_ID),
        "command": command[:2],
        "argument": command[2:],
    }


def _frames(writes, decoder):
    out = []
    for payload in writes:
        entry = {"hex": payload.hex(), "length": len(payload)}
        decoded = decoder(payload)
        if decoded is not None:
            entry["decode"] = decoded
        out.append(entry)
    return out


def _scenario(scenario_id, device, description, prelude, old_calls,
              new_calls, inputs, writes, decoder):
    frames, dropped = _strip_handshake(writes)
    return {
        "id": scenario_id,
        "device": device,
        "description": description,
        "prelude": prelude,
        "old_calls": old_calls,
        "new_calls": new_calls,
        "inputs": inputs,
        "handshake_frames_filtered": dropped,
        "frames": _frames(frames, decoder),
    }


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------

PROBE_CLASSES = ("StepperProbe", "DCProbe", "ChuckPositioner")


def _quiesce_probe(probe):
    """Stop everything that writes to the port except the scenario itself.

    See "How capture is made deterministic" in the module docstring. This is
    applied before a single byte is recorded, so a captured frame is always
    the frame the call under capture produced.
    """
    probe.poller = None
    probe.start_loops = lambda *a, **k: None
    probe.stop_loops = lambda *a, **k: None
    probe._start_interlock_watchdog = lambda *a, **k: None
    probe._stop_interlock_watchdog = lambda *a, **k: None
    # MANUAL refuses to arm without a bound gamepad (I-3.2). That check is
    # real behaviour and is tested elsewhere; here it would simply prevent
    # the jog scenarios from existing, so it is satisfied rather than removed.
    probe._gamepad_bound = lambda: True
    return probe


def _new_probe(name):
    from model.probes import StepperProbe, DCProbe, ChuckPositioner
    cls = {"StepperProbe": StepperProbe, "DCProbe": DCProbe,
           "ChuckPositioner": ChuckPositioner}[name]
    return _quiesce_probe(cls("SIM", None))


def _writes_of(probe):
    return probe.serial_comm.ser.writes


def _apply_step_inputs(probe, name):
    values = dict(STEP_INPUTS)
    if name == "DCProbe":
        values.update(DC_STEP_INPUTS)
    for key, value in values.items():
        setattr(probe, key, value)
    return values


def capture_probes():
    scenarios = []
    for name in PROBE_CLASSES:
        prefix = name.lower().replace("probe", "").replace("positioner", "") or name
        prefix = {"stepper": "stepper", "dc": "dc", "chuck": "chuck"}[prefix]

        # --- enable -----------------------------------------------------
        probe = _new_probe(name)
        _writes_of(probe).clear()
        probe.enable()
        scenarios.append(_scenario(
            f"{prefix}.enable", name,
            "Operator arms the probe. `_arm` sends the energize byte, then "
            "`_transition`'s quiesce sends a zeroed motion frame so the "
            "board starts from rest.",
            prelude=["<construct on port 'SIM'>"],
            old_calls=["probe.enable()"],
            new_calls=['probe.run("set_mode", args=(ProbeMode.IDLE,))'],
            inputs={}, writes=list(_writes_of(probe)),
            decoder=_decode_probe))

        # --- autonomous + one step --------------------------------------
        probe = _new_probe(name)
        probe.enable()
        values = _apply_step_inputs(probe, name)
        _writes_of(probe).clear()
        probe.enter_auton()
        probe.macro_start_auton()
        scenarios.append(_scenario(
            f"{prefix}.auto_and_step", name,
            "Enter AUTONOMOUS (one zeroed frame from the quiesce), then one "
            "step. `macro_start_auton` transitions with quiesce=False, so the "
            "move frame is the only write it adds - no stop-then-go hiccup at "
            "the board.",
            prelude=["probe.enable()",
                     "<motion parameters written while IDLE; a write to one "
                     "is refused once AUTONOMOUS or MANUAL (DC-6)>"],
            old_calls=["probe.enter_auton()", "probe.macro_start_auton()"],
            new_calls=['probe.run("set_mode", args=(ProbeMode.AUTO,))',
                       'probe.run("step", inputs=<motion parameters>)'],
            inputs=values, writes=list(_writes_of(probe)),
            decoder=_decode_probe))

        # --- repeated step while already AUTO (DC-6 / review finding 5) --
        probe = _new_probe(name)
        probe.enable()
        _apply_step_inputs(probe, name)
        probe.enter_auton()
        probe.macro_start_auton()
        _writes_of(probe).clear()
        probe.macro_start_auton()
        scenarios.append(_scenario(
            f"{prefix}.step_again_while_auto", name,
            "A second step while already AUTONOMOUS. One move frame and "
            "nothing else: step stays available while AUTO so repeated "
            "stepping works (DC-6, review finding 5).",
            prelude=["probe.enable()", "<motion parameters>",
                     "probe.enter_auton()", "probe.macro_start_auton()"],
            old_calls=["probe.macro_start_auton()"],
            new_calls=['probe.run("step", inputs=<motion parameters>)'],
            inputs=values, writes=list(_writes_of(probe)),
            decoder=_decode_probe))

        # --- manual entry ------------------------------------------------
        probe = _new_probe(name)
        probe.enable()
        _writes_of(probe).clear()
        probe.enter_manual()
        scenarios.append(_scenario(
            f"{prefix}.manual_enter", name,
            "Enter MANUAL from IDLE. Already armed, so no energize byte; the "
            "quiesce frame is the whole of it.",
            prelude=["probe.enable()"],
            old_calls=["probe.enter_manual()"],
            new_calls=['probe.run("set_mode", args=(ProbeMode.MANUAL,))'],
            inputs={}, writes=list(_writes_of(probe)),
            decoder=_decode_probe))

        # --- jog frames ---------------------------------------------------
        for levels_name, levels in GAMEPAD_LEVELS.items():
            probe = _new_probe(name)
            probe.enable()
            _apply_step_inputs(probe, name)
            probe.enter_manual()
            _writes_of(probe).clear()
            probe.send_manual_mode_command(dict(levels))
            scenarios.append(_scenario(
                f"{prefix}.jog.{levels_name}", name,
                f"One 42-byte jog packet for the '{levels_name}' gamepad "
                f"state. Triggers are remapped from [-1, 1] to [0, 1] and "
                f"combined as (L - R); bumpers combine as (L - R).",
                prelude=["probe.enable()", "<motion parameters>",
                         "probe.enter_manual()"],
                old_calls=[f"probe.send_manual_mode_command({levels_name!r} "
                           f"levels)"],
                new_calls=["probe._send_jog(<levels>)  # driven by "
                           "Probe._jog_loop, was BaseProbe._input_loop"],
                inputs=dict(levels), writes=list(_writes_of(probe)),
                decoder=_decode_probe))

        # --- the neutral-on-exit frame -----------------------------------
        probe = _new_probe(name)
        probe.enable()
        _apply_step_inputs(probe, name)
        probe.enter_manual()
        _writes_of(probe).clear()
        probe.send_manual_mode_command(dict(JOG_EXIT_LEVELS))
        scenarios.append(_scenario(
            f"{prefix}.jog.zero_frame_on_exit", name,
            "The single zeroed jog frame `_input_loop` sends when manual mode "
            "ends or the input gate closes (I-4.2). Called with an empty "
            "dict, so every field comes from the transport's own defaults - "
            "the bytes must match the 'neutral' levels frame exactly.",
            prelude=["probe.enable()", "<motion parameters>",
                     "probe.enter_manual()"],
            old_calls=["probe.send_manual_mode_command({})"],
            new_calls=["probe._send_jog({})"],
            inputs={}, writes=list(_writes_of(probe)),
            decoder=_decode_probe))

        # --- disable ------------------------------------------------------
        probe = _new_probe(name)
        probe.enable()
        _writes_of(probe).clear()
        probe.disable()
        scenarios.append(_scenario(
            f"{prefix}.disable", name,
            "Stop motion, then de-energize. The zeroed frame goes first and "
            "the 'd' is sent even if it raised - de-energizing is never "
            "skipped because an earlier step failed.",
            prelude=["probe.enable()"],
            old_calls=["probe.disable()"],
            new_calls=['probe.run("set_mode", args=(ProbeMode.DISABLED,))'],
            inputs={}, writes=list(_writes_of(probe)),
            decoder=_decode_probe))

        # --- FULL STOP ----------------------------------------------------
        probe = _new_probe(name)
        probe.enable()
        _writes_of(probe).clear()
        probe.power_down()
        scenarios.append(_scenario(
            f"{prefix}.full_stop", name,
            "FULL STOP: zeroed motion frame, 'd', then 'k\\n' - in that "
            "order, all on the priority lane. `emergency_stop()` latches and "
            "then runs exactly this on a worker, so the byte sequence is the "
            "same; it is captured synchronously here so the capture is "
            "deterministic.",
            prelude=["probe.enable()"],
            old_calls=["probe.power_down()",
                       "# probe.emergency_stop() sends the same three frames"],
            new_calls=["probe._halt_hardware()",
                       '# probe.run("estop") -> Model.estop() -> '
                       '_halt_hardware() on a worker'],
            inputs={}, writes=list(_writes_of(probe)),
            decoder=_decode_probe))

    return scenarios


# --------------------------------------------------------------------------
# Heater
# --------------------------------------------------------------------------

def _new_heater():
    from model.temperature_system import TemperatureSystem
    heater = TemperatureSystem("SIM")
    # The reader never writes, but it holds the port and keeps the process
    # alive. Stopped and joined here so a capture run terminates cleanly.
    heater.continue_reading = False
    heater._reader_wake.set()
    thread = getattr(heater, "serial_thread", None)
    if thread is not None:
        thread.join(timeout=2.0)
    return heater


def capture_heater():
    scenarios = []

    heater = _new_heater()
    scenarios.append(_scenario(
        "heater.open", "TemperatureSystem",
        "Construction writes the heater-off frame immediately, so a board "
        "left at a setpoint by a previous session is brought to zero before "
        "anything else happens.",
        prelude=[],
        old_calls=["TemperatureSystem('SIM')"],
        new_calls=["Heater(port='SIM').open()"],
        inputs={}, writes=list(heater.serial_conn.ser.writes),
        decoder=_decode_heater))

    heater = _new_heater()
    for key, value in HEATER_INPUTS.items():
        setattr(heater, key, value)
    heater.serial_conn.ser.writes.clear()
    heater.send_settings()
    scenarios.append(_scenario(
        "heater.apply_settings", "TemperatureSystem",
        "The settings frame. `ramp_rate` is spdelay (seconds per 1 C step) "
        "in the firmware's own unit, formatted to 2 decimals; every other "
        "field is interpolated as entered. A field that is not a number is "
        "refused outright rather than sent - strtok would shift every field "
        "after a blank one left (RC-6 item 4).",
        prelude=["TemperatureSystem('SIM')"],
        old_calls=["heater.send_settings()"],
        new_calls=['heater.run("apply_settings", inputs=<settings>)'],
        inputs=dict(HEATER_INPUTS),
        writes=list(heater.serial_conn.ser.writes),
        decoder=_decode_heater))

    heater = _new_heater()
    for key, value in HEATER_INPUTS.items():
        setattr(heater, key, value)
    heater.serial_conn.ser.writes.clear()
    heater.stop()
    scenarios.append(_scenario(
        "heater.stop", "TemperatureSystem",
        "Heater off: setpoint 0 and zeroed gains, keeping the operator's "
        "ramp rate (1 decimal here, not 2 - `stop` formats it differently "
        "from `send_settings` and that difference is on the wire) and "
        "offset. Also what `disable()` sends, and what FULL STOP sends with "
        "priority=True.",
        prelude=["TemperatureSystem('SIM')", "<settings written>"],
        old_calls=["heater.stop()",
                   "# heater.disable() and emergency_stop() send the same "
                   "frame"],
        new_calls=["heater._halt_hardware()",
                   "# Model.halt() / Model.estop() / Heater.disable()"],
        inputs=dict(HEATER_INPUTS),
        writes=list(heater.serial_conn.ser.writes),
        decoder=_decode_heater))

    heater = _new_heater()
    for key, value in HEATER_INPUTS.items():
        setattr(heater, key, value)
    heater.serial_conn.ser.writes.clear()
    heater.close()
    scenarios.append(_scenario(
        "heater.close", "TemperatureSystem",
        "Shutdown. The heater-off frame goes out on the priority lane and is "
        "drained before the port is released - the firmware has no watchdog, "
        "so a frame discarded by close() leaves the heater at its last "
        "setpoint (TEMP-11). Note it is the fixed literal frame, not the "
        "operator's ramp rate.",
        prelude=["TemperatureSystem('SIM')", "<settings written>"],
        old_calls=["heater.close()"],
        new_calls=["heater.close()  # Model.close(); was close/disconnect/"
                   "teardown"],
        inputs={}, writes=list(heater.serial_conn.ser.writes),
        decoder=_decode_heater))

    return scenarios


# --------------------------------------------------------------------------
# SMC100
# --------------------------------------------------------------------------

class _RecordingSMCPort:
    """A pyserial stand-in for `SMC100`, recording writes and serving replies.

    `lib/smc100.py` talks to pyserial directly, so there is no simulator mode
    to capture from; this is the equivalent of `SimulatedPort` for that
    device. Replies are a fixed FIFO queue, handed out one per `?` query, so
    `wait_states` takes one determined route instead of polling until a real
    controller happens to answer.
    """

    def __init__(self, replies=()):
        self.writes = []
        self.replies = list(replies)
        self.is_open = True
        self._buffer = b""

    def write(self, payload):
        payload = bytes(payload)
        self.writes.append(payload)
        if payload.endswith(b"?") and self.replies:
            self._buffer += self.replies.pop(0).encode("ascii") + b"\r\n"
        return len(payload)

    def read(self, _size=1):
        if not self._buffer:
            return b""
        char, self._buffer = self._buffer[:1], self._buffer[1:]
        return char

    def flush(self):
        pass

    def flushInput(self):
        self._buffer = b""

    def flushOutput(self):
        pass

    def close(self):
        self.is_open = False


def _new_smc(replies=()):
    from lib import smc100 as smc_module
    port = _RecordingSMCPort(replies)

    class _FakePySerial:
        Serial = staticmethod(lambda **kwargs: port)

    original = smc_module.serial
    smc_module.serial = _FakePySerial
    try:
        device = smc_module.SMC100(SMC_ID, "RECORDING",
                                   sleepfunc=lambda _seconds: None)
    finally:
        smc_module.serial = original
    return port, device


def capture_smc100():
    scenarios = []

    port, device = _new_smc(["1TS000032"])
    port.writes.clear()
    device.home(waitStop=True)
    scenarios.append(_scenario(
        "smc100.home", "SMC100",
        "Home and wait. OR, then TS? until the controller reports a target "
        "state. The scripted reply is '32' (READY from HOMING), which is a "
        "target state, so no follow-up absolute move is issued. Note that "
        "`sendcmd` writes the command and the CRLF as two separate calls - "
        "the wire sees one frame either way, and the split is preserved so a "
        "reimplementation cannot quietly change the write granularity.",
        prelude=["SMC100(1, port)", "<scripted reply: 1TS000032>"],
        old_calls=["smc.home(waitStop=True)"],
        new_calls=['rotator.run("home")  -> SMC100.home()'],
        inputs={"waitStop": True, "scripted_replies": ["1TS000032"]},
        writes=list(port.writes), decoder=_decode_smc))

    port, device = _new_smc()
    port.writes.clear()
    device.home(waitStop=False)
    scenarios.append(_scenario(
        "smc100.home_no_wait", "SMC100",
        "Home without waiting: OR followed immediately by an absolute move "
        "to 0, so the stage ends at the origin whether or not it was already "
        "homed.",
        prelude=["SMC100(1, port)"],
        old_calls=["smc.home(waitStop=False)"],
        new_calls=["SMC100.home(waitStop=False)"],
        inputs={"waitStop": False}, writes=list(port.writes),
        decoder=_decode_smc))

    port, device = _new_smc()
    port.writes.clear()
    device.move_absolute_deg(45.0, waitStop=False)
    scenarios.append(_scenario(
        "smc100.move_absolute", "SMC100",
        "Absolute move to 45.0 deg. PA carries the position as Python's own "
        "str() of the float, which is what reaches the controller.",
        prelude=["SMC100(1, port)"],
        old_calls=["smc.move_absolute_deg(45.0, waitStop=False)"],
        new_calls=['rotator.run("move_to", inputs={"target": 45.0}) -> '
                   "SMC100.move_absolute_deg"],
        inputs={"position_deg": 45.0, "waitStop": False},
        writes=list(port.writes), decoder=_decode_smc))

    port, device = _new_smc()
    port.writes.clear()
    device.move_relative_deg(1.5, waitStop=False)
    scenarios.append(_scenario(
        "smc100.move_relative_positive", "SMC100",
        "Relative move +1.5 deg. PR is on the no-retry list, so it is never "
        "resent automatically - a retried relative move is a second move.",
        prelude=["SMC100(1, port)"],
        old_calls=["smc.move_relative_deg(1.5, waitStop=False)"],
        new_calls=['rotator.run("move_by", inputs={"step": 1.5}) -> '
                   "SMC100.move_relative_deg"],
        inputs={"dist_deg": 1.5, "waitStop": False},
        writes=list(port.writes), decoder=_decode_smc))

    port, device = _new_smc()
    port.writes.clear()
    device.move_relative_deg(-1.5, waitStop=False)
    scenarios.append(_scenario(
        "smc100.move_relative_negative", "SMC100",
        "Relative move -1.5 deg. The old model had two commands "
        "(move_relative_positive / move_relative_negative); the new one has "
        "one signed step, and this is the negative half of it.",
        prelude=["SMC100(1, port)"],
        old_calls=["smc.move_relative_deg(-1.5, waitStop=False)"],
        new_calls=['rotator.run("move_by", inputs={"step": -1.5})'],
        inputs={"dist_deg": -1.5, "waitStop": False},
        writes=list(port.writes), decoder=_decode_smc))

    port, device = _new_smc()
    port.writes.clear()
    device.stop()
    scenarios.append(_scenario(
        "smc100.stop", "SMC100",
        "ST on the ordinary path, through `sendcmd` and its lock.",
        prelude=["SMC100(1, port)"],
        old_calls=["smc.stop()"],
        new_calls=["rotator.halt() -> SMC100.stop()"],
        inputs={"priority": False}, writes=list(port.writes),
        decoder=_decode_smc))

    port, device = _new_smc()
    port.writes.clear()
    device.stop(priority=True)
    scenarios.append(_scenario(
        "smc100.stop_priority", "SMC100",
        "ST on the priority path, which bypasses `sendcmd` entirely and "
        "writes through a bounded lock acquisition (ROTATOR-8). **The bytes "
        "are identical to the ordinary path** - that is the point: a stop "
        "that forces its way past a held lock must not be a different "
        "command.",
        prelude=["SMC100(1, port)"],
        old_calls=["smc.stop(priority=True)"],
        new_calls=["rotator._halt_hardware() -> SMC100.stop(priority=True)"],
        inputs={"priority": True}, writes=list(port.writes),
        decoder=_decode_smc))

    port, device = _new_smc(["1TS000033"])
    port.writes.clear()
    device.get_status(silent=True)
    scenarios.append(_scenario(
        "smc100.status", "SMC100",
        "TS? - the status query the rotator's sample loop issues. Four hex "
        "error digits then a two-character state code; '33' is READY from "
        "MOVING.",
        prelude=["SMC100(1, port)", "<scripted reply: 1TS000033>"],
        old_calls=["smc.get_status(silent=True)"],
        new_calls=["rotator._poll() -> SMC100.get_status(silent=True)"],
        inputs={"scripted_replies": ["1TS000033"]},
        writes=list(port.writes), decoder=_decode_smc))

    port, device = _new_smc(["1TP12.345"])
    port.writes.clear()
    device.get_position_deg()
    scenarios.append(_scenario(
        "smc100.position", "SMC100",
        "TP? - the position query, issued by the same sample loop as the "
        "status query and captured with it because the pair is what the "
        "controller actually sees at 1 Hz.",
        prelude=["SMC100(1, port)", "<scripted reply: 1TP12.345>"],
        old_calls=["smc.get_position_deg()"],
        new_calls=["rotator._poll() -> SMC100.get_position_deg()"],
        inputs={"scripted_replies": ["1TP12.345"]},
        writes=list(port.writes), decoder=_decode_smc))

    replies = ["1TS00000A", "1ID TRB25CC", "1TS000014", "1TS00000C"]
    port, device = _new_smc(replies)
    port.writes.clear()
    device.reset_and_configure()
    scenarios.append(_scenario(
        "smc100.reset_and_configure", "SMC100",
        "Full reset and reconfigure: RS twice, wait for NOT REFERENCED from "
        "reset, read the stage id, enter config mode (PW1), load stage "
        "parameters (ZX1), enable the stage id check (ZX2), leave config "
        "mode (PW0), wait for NOT REFERENCED from configuration. Ten "
        "commands in a fixed order; the scripted replies put the controller "
        "in the expected state at each wait so exactly one TS? is needed "
        "per wait.",
        prelude=["SMC100(1, port)", f"<scripted replies: {replies}>"],
        old_calls=["smc.reset_and_configure()"],
        new_calls=['rotator.run("configure") -> SMC100.reset_and_configure()'],
        inputs={"scripted_replies": replies},
        writes=list(port.writes), decoder=_decode_smc))

    return scenarios


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

FILES = {
    "probes.json": ("probe wire frames: StepperProbe, DCProbe, "
                    "ChuckPositioner", capture_probes),
    "heater.json": ("heater (TemperatureSystem) wire frames", capture_heater),
    "smc100.json": ("SMC100 rotator controller wire frames", capture_smc100),
}


def capture_file(filename):
    """The full JSON document for one golden file."""
    description, capture = FILES[filename]
    scenarios = capture()
    return {
        "_meta": {
            "description": description,
            "captured_from": "src/ (the old code), driven in simulator mode",
            "regenerate_with": "python3 tests/station/golden/capture.py",
            "handshake_frames_filtered": sum(
                scenario["handshake_frames_filtered"]
                for scenario in scenarios),
            "handshake_note":
                "Transport ping traffic (b's\\n') is filtered out of every "
                "frame list and counted here. Simulator mode never runs the "
                "handshake at all, so this is 0; a non-zero value means a "
                "capture was taken against a live port and the pings were "
                "removed from the golden bytes.",
            "scenario_count": len(scenarios),
        },
        "scenarios": scenarios,
    }


def capture_all():
    return {filename: capture_file(filename) for filename in FILES}


def main():
    for filename, document in capture_all().items():
        path = os.path.join(_HERE, filename)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print(f"wrote {path} "
              f"({document['_meta']['scenario_count']} scenarios)")


if __name__ == "__main__":
    main()
