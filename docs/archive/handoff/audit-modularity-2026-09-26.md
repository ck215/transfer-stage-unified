# Modularity audit — 2026-09-26 (HEAD 3ba87bd, branch mvc-refactor)

Question asked: can a new device be wired by writing its Model alone, with
the views, the Controller, Setup and the input paths (serial, gamepad)
adapting without edits?

Read-only. Builds on `audit-model-contract-2026-09-26.md` (a `PiezoStage`
driven through all three views unedited) and `audit-architecture-2026-09-26.md`
(layering intact). This audit measures the seams those two did not: the
gamepad path, the per-model loop scaffolding, Setup's constructor contract
and the Device base.

## Verdict

**Yes for the UI half; no for the input half.** A device that is driven
from schema controls only (buttons, entries, toggles, readouts, plots) is
already plug-in: write the Model, register it, and Tk, Qt and Web render
it, gate it, stop it and name it. A device that is driven from a gamepad
must copy roughly 200 lines of probe-private plumbing, because the gamepad
has no contract. That is the one seam that still contradicts the goal.

## What is already modular (verified in code)

| Seam | Where | Evidence |
|---|---|---|
| Control ↔ widget | `schema.py` 14 element types; `views/base.py:128` refuses a view missing any `_make_<type>` | Every element type is rendered by all three views; `inputs`, `args`, `on_args`, `slider`, `unit`, `rail`, `tier`, `disclosure`, `stop` honoured |
| Command dispatch | `panel.py:run/_allows/_apply_inputs` | One allow-list for every view including the Web API; the schema is the only way to call a model |
| Stop / latch / fault / close | `model/base.py` | Subclass writes `_halt_hardware` only; estop, clear, fault gating, close order inherited |
| View ↔ model | `controller.py` `schema/state/run` | No view imports a model or a device (`test_architecture.py`); the 32/26/22 grep hits for device names in tk/qt/app.js are comments plus two tables (`DEVICE_WORDS`, `LINK_DEVICES`, the latter is CON-6) |
| Contract enforcement | `tests/test_model_contract.py` | 23 checks × every registered class |

## What is not modular yet, ranked

### MOD-1 — The gamepad has no contract [S2, the blocker for the stated goal]

- **Observed.** `devices/gamepad.py` publishes a fixed, probe-shaped
  vocabulary: `x_axisStatus`, `y_axisStatus`, `z_axisStatusL`,
  `z_axisStatusR`, `dpad_LR`, `dpad_UD`, `LBumper`, `RBumper`
  (`gamepad.py:370-373,494,1040-1043`). The probe owns everything above
  the device: build (`probe.py:204`), bind on open (`:216`), the dropdown,
  options and log commands (`:876-896`), the bound / gate-open checks
  (`:868-873`), the lost-pad transition (`:640`), the 50 Hz pump
  (`_jog_loop:693`), the activity touch, and the level→packet map
  (`_jog_bytes:662`). Heater, Rotator and Red Percent accept `gamepad=`
  and drop it (`heater.py:103`).
- **Consequence.** A second gamepad-driven device (a piezo, a second
  chuck) re-implements bind, gate, lost, pump and the dropdown, or it
  subclasses the probe and inherits a stepper's mode machine.
- **Proposal.** A manual-input contract in three parts:
  1. The device speaks channels, not probe fields: `axis:x`, `axis:y`,
     `axis:z` in [-1, 1], plus named edges (`edge:step_x`, …). The
     `LAYOUTS` table already maps physical pads to logical names; only
     the logical names change.
  2. A base mixin (`model/manual_input.py`, or on `Model`) owns the
     dropdown / options / log commands, bind-on-open, the gate, the
     lost-input transition and the pump loop. The model declares
     `INPUT_RATE_HZ` and writes one method, `_on_input(levels, edges)`,
     which for the probe is today's `_send_jog`.
  3. Schema gets one builder, `sch.manual_input("Gamepad:", …)`, so the
     dropdown, the log stream and the "Manual" toggle come from one
     declaration; a device that declares it gets the Setup gamepad
     column for free (Setup already keys the column on `NEEDS_GAMEPAD`).
- **Proof of no regression.** The 42-byte jog packet is pinned by
  `tests/test_wire_golden.py`; the refactor changes who calls
  `_jog_bytes`, not what it packs.
- **Serial is not the same problem.** Each model packs its own frames
  (`struct.pack` in the probe, ASCII in the heater and the SMC100). That
  is correct: the firmware protocols differ and the golden gate pins the
  bytes. The "consistent command structure" the goal asks for is the
  schema command plus its args, which is already one shape for every
  input source. Do not add a shared wire codec.

### MOD-2 — Loop scaffolding is written four times [S3]

- **Observed.** Every model builds its own stop `Event`, thread(s) and
  join-with-timeout: probe `:178-253` (sampler, jog, interlock), heater
  `:143-178`, rotator `:93-134`, red_monitor `:173,701-713`. Four
  different timeout constants. The probe's interlock thread is started
  outside `_start_threads` (`:1065`).
- **Proposal.** `Model._spawn(name, body, period_s)` and a base
  `_stop_threads` that joins every spawned thread with one
  `THREAD_JOIN_TIMEOUT`. Subclasses stop overriding `_start_threads` /
  `_stop_threads`; they call `_spawn` from `open`.

### MOD-3 — The idle interlock is probe code (CON-11, open) [S3]

Falls out of MOD-2: the interlock is a `_spawn`ed loop plus two state keys
and `extend_idle`. Lift it onto the same mixin as MOD-1 or beside it.

### MOD-4 — Setup's constructor and registry are closed (CON-7, open) [S3]

- **Observed.** `MODEL_TYPES` is a tuple (`setup.py:132-143`); the SMC100
  handshake is hard-coded (`:655`); the only constructor Setup can call is
  `cls(port=, gamepad=, sim=)` (`:1024`). Red Percent already grew a
  `screen=` parameter outside that signature. A device with two ports or
  a network address does not fit.
- **Proposal.** The class declares its resources, `REQUIRES = ("port",
  "gamepad")`, and Setup builds the row from that; an optional
  `IDENTIFY(port) -> bool` classmethod replaces the hard-coded SMC100
  branch; `Setup.register(cls)` replaces the tuple.

### MOD-5 — The Device base is duck-typed past its four members [S3]

`set_gate`, `is_hardware` (CON-6) and "lost" are read by `getattr` or by
class name. Add them to `Device` with defaults (`set_gate` no-op,
`is_hardware` False, `status` unchanged) so a new device declares them
instead of matching a name.

### MOD-6 — `inputs` is declared and not read (CON-8) [S3]

Every writable entry travels with every command (`views/base.py:162`).
Harmless today; either honour `inputs` in the views or drop the field so
the schema does not describe a behaviour that does not exist.

## Not a modularity blocker, noted for completeness

The three renderers are 6.4k / 5.7k / 3.0k lines and still carry their
own copy tables (ARCH-2, ARCH-13). That taxes changing a view, not
adding a device. It stays on the architecture audit's list.

## Route

All core work, lead only (standing rule: core files change only by the
lead). Order: MOD-1 with MOD-3 (one mixin, one worktree, golden gate as
the proof), then MOD-2, then MOD-4, then MOD-5/6 as `router` refactors
under `test_model_contract.py` extended with one check per new rule. The
`PiezoStage` prototype in the model-contract audit's scratchpad is the
acceptance test: rebuild it gamepad-driven and drive it through Tk, Qt and
Web unedited.
