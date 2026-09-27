# Model contract: how to add a device (2026-09-26)

From the abstraction audit `handoff/audit-model-contract-2026-09-26.md`, which
built a non-probe device (`PiezoStage`) and drove it through all three views
without editing them. Landed by the lead with the CON-3/4/9 fixes applied.

A device becomes a first-class member of the station when it is a `Model`
subclass that declares its controls in a schema. The three views, the
Controller, the stop and the watchdog then handle it without being edited.
`tests/test_model_contract.py` checks every rule below against every
registered class. Worked example: the audit's `PiezoStage`
(`handoff/audit-model-contract-2026-09-26.md`).

## Recipe

1. **Subclass and declare.** `class Piezo(Model)` in `src/model/piezo.py`.
   Set `NAME` (the operator's name, which is also the Setup row and the rail
   line), `IDENTITY` (a one-letter `DEV:` byte, or `None`), `NEEDS_PORT`
   and `NEEDS_GAMEPAD`. Write `__init__(self, port=None, gamepad=None,
   sim=False)`: this is the signature Setup calls. With `sim=True` or
   `port="SIM"` the model must build a simulated device.
2. **Own your devices.** Each device is a `devices.device.Device`
   (`open`, `close`, `is_open`, `status`). Return them from `devices`. Use
   one of these `status` words: `verified`, `unverified`, `simulated`,
   `lost`, `closed`, `connecting`, `bound`, `unbound`. The views mark
   `lost` and `simulated`. Put hardware libraries in `src/devices/` only.
3. **Declare parameters.** Write `PARAMS = {name: Param(name, "int"|"float"|
   "text", default=, minimum=, maximum=, decimals=, unit=, label=)}`.
   `Panel.__init__` seeds each one as an attribute. Views validate against
   the Param, and so does `Panel.run`.
4. **Write the schema.** It is a `schema` property returning
   `sch.schema(sch.section(...), ..., self._safety_section())`. The safety
   section must come last.
   - Readouts: `sch.readonly(text, attr, rail=True, unit=)`. A caption of
     `X:`, `Y:` or `Z:` is drawn as an axis group.
   - Editable values: `sch.entry(text, attr, PARAMS[attr], disabled_when=,
     slider=(lo, hi))`.
   - Commands: `sch.button(text, cmd, inputs=(...), args=(...), role="go",
     enabled_when=/disabled_when=, stop=)`. Put `role="go"` on the primary
     action. `args` are fixed positional arguments. Mark every command that
     takes hardware DOWN (`halt`, `end_run`, …) with `stop=True`: the Panel
     then never refuses it over unrelated entry text.
   - Modes: `sch.toggle(text, bool_attr, "set_mode", on_text, off_text,
     on_args=[mode], off_args=["disabled"], disabled_when=("latched",))`.
   - Data: `sch.plot(text, data_cmd, empty=)`, `sch.image`, and
     `sch.log_stream(text, source_cmd, detached=True)`.
   - Commands with no control of their own (for example `extend_idle`) are
     declared as `{"type": "internal", "command": ...}`.
   - Put rarely used controls in `tier=2` (and give that section a
     `disclosure=`) or in `tier=3`.
5. **Write the commands.** A command returns a value or raises
   `Refused(reason)`. To ask the operator first, raise
   `NeedsConfirm(prompt, "cmd", args=(...))`. The view re-runs it as
   `cmd(*args, True)`, so `confirmed` must be the last positional
   parameter. Motion and heat commands start with `self._guard("Move")`
   and pass `abort_if=self._estop.is_set` to the device write. Return
   promptly and run long motion on a worker. The Controller serialises
   commands per model, and only `toggle_estop` and `estop` skip that queue.
6. **Write the stop.** `_halt_hardware()` sends the strongest stop the
   device has on its priority lane. It must not wait on a lock without a
   timeout, and it returns `True` only when the stop landed. Do not
   override `estop`, `clear_estop` or `close`.
7. **Report what you are doing.** `mode_name` is the word your gates name.
   `is_active` is true while the device is moving, heating or recording.
   `is_energized` is true while it holds something an operator must undo
   before leaving, which is a wider condition. Call `_fault(reason)` when a
   disable fails: the base then gates every control that lists `"fault"`.
   A manual input Device exposes `set_gate(bool)`; the Controller calls it
   on every device of every model when the window loses focus (D-4). Call `_touch()` from your loop, or return `False` from
   `_expects_heartbeat()` if the device has no loop.
8. **Threads.** Start them in `_start_threads` and join them with a timeout
   in `_stop_threads`. `disable()` de-energizes. `close()` calls
   `_stop_threads`, then `halt`, then `disable`, then closes the devices.
9. **Optional capabilities.**
   - Idle clock: publish `idle_remaining` and `idle_warn_seconds` in
     `state` and declare `extend_idle`.
   - Position source for Red Percent: `position` as `(x, y, z)`,
     `position_time` and `position_age`.
   - Downloads: a `file_save` command returns the path it wrote, under
     `output_root`.
10. **Register the device.** For runtime use, call
    `controller.add(NAME, model, config)`. For Setup (scan, auto-assign,
    Launch checkbox, reopen), add the class to `MODEL_TYPES` in
    `controller/setup.py`.

## What you get for free

- All three views render every element with its tier, disclosure, slider,
  unit and axis grouping.
- Refusal lines appear where they happened, and greyed controls carry their
  reason (`views.base.gate_reason`).
- The Overview entry and the device page. The head opens the page.
- The stop disc, the per-model switch, the headline and the rail marks
  (latched, unconfirmed, faulted, energized). `stop_words` names the device.
- Lost-link and stale marks, which come from `devices` and `age`.
- The confirm dialog for every `NeedsConfirm`.
- The idle countdown with Extend, drawn from `idle_remaining`.
- The Quit and close-tab warnings, which come from `is_energized`.
- The Web watchdog.
- Clean shutdown on exit and on signals.

## Still probe-shaped (open, see the audit)

- **Web drops a button's `args`** (CON-1, S1): being fixed in the Web
  worktree. Until it lands, two Web buttons that share one command and
  differ only by `args` send the wrong one.
- **Hardware links in the simulation line.** Web and Tk count only
  `SerialPort` and `SMC100` as hardware (CON-6).
- **Setup.** There is no registration call; `MODEL_TYPES` is a tuple and the
  SMC100 handshake is hard-coded; a model added at runtime cannot be
  reopened after its entry closes (CON-7).
- **The idle clock is per-model code** (CON-11): copy the probe's shape.
- **A SIM Rotator's stop never confirms** (CON-13): every simulated FULL
  STOP reads "Rotator did not confirm" - an owner call.

Fixed on 2026-09-26: the window-focus gate reaches every device with
`set_gate` (CON-3); stop-class commands are a schema property (CON-4); a
fault gates from the base (CON-9).
