# Model contract: how to add a device (2026-09-26)

From the abstraction audit `handoff/audit-model-contract-2026-09-26.md`, which
built a non-probe device (`PiezoStage`) and drove it through all three views
without editing them. Landed by the lead with the CON-3/4/9 fixes applied.

A device becomes a first-class member of the station when it is a `Model`
subclass that declares its controls in a schema. The three views, the
Controller, the stop and the watchdog then handle it without being edited.
`tests/test_model_contract.py` checks every rule below against every
registered class. Worked examples: the audit's `PiezoStage`
(`handoff/audit-model-contract-2026-09-26.md`) and, in the tree, the
Transfer Map (`src/model/transfer_map.py`, Tier S, 2026-09-27): a model
with no hardware that owns a local store, reads other models by duck type
(`subscribe`, `grab_frame`, `position_deg`) and draws its figures through
`plot_data`; it needed no view change.

## Recipe

1. **Subclass and declare.** `class Piezo(Model)` in `src/model/piezo.py`.
   Set `NAME` (the operator's name, which is also the Setup row and the rail
   line), `IDENTITY` (a one-letter `DEV:` byte, or `None`) and `RESOURCES`:
   the keyword arguments Setup fills in, each named `port*` (a serial-port
   dropdown) or `gamepad*` (a gamepad dropdown), for example `("port",
   "gamepad")`. A class that sets `NEEDS_PORT` / `NEEDS_GAMEPAD` instead
   gets its resources derived from them. Write `__init__(self, sim=False,
   **resources)` with one keyword per resource: Setup constructs
   `cls(sim=..., port=..., gamepad=...)` and passes only what the class
   declares. With `sim=True` or `port="SIM"` the model must build a
   simulated device. A device with no `DEV:` byte identifies its own port
   with a classmethod `identify_port(port, should_abort) -> bool`, which the
   scan asks before the firmware handshake (the Rotator's SMC100 query is
   the example).
   `HOST` (default `None`) names the model whose page draws this one:
   Red Percent sets `HOST = "Transfer Map"` (owner ruling 2026-09-28, one
   dashboard). A hosted model keeps its own Panel, commands, stop path and
   tests; while its host is launched the views draw its sections on the
   host's page and list no page of its own, and ticking the host in Setup
   ticks it. Launched without its host, it is an ordinary page.
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
   Every Device answers `set_gate(bool)` (the base remembers it as
   `is_gate_open`; an input device such as the Gamepad acts on it); the
   Controller calls it on every device of every model when the window
   loses focus (D-4). A Device whose loss means the instrument is
   unreachable (a serial board, a motion controller) sets `is_hardware =
   True`; `state["hardware_devices"]` lists those and the views' simulation
   line counts them. Call `_touch()` from your loop, or return `False` from
   `_expects_heartbeat()` if the device has no loop.
8. **Threads.** Start each loop in `_start_threads` with
   `self._spawn(name, target)`; the loop waits on `self._threads_stop`.
   The base `_stop_threads` sets the flag and joins every spawned loop
   within `THREAD_JOIN_TIMEOUT` (2 s); override it only to end something a
   join cannot (Red Percent's in-flight run), then call the base.
   `disable()` de-energizes. `close()` calls `_stop_threads`, then `halt`,
   then `disable`, then closes the devices.
9. **Optional capabilities.**
   - Idle clock: mix in `model.idle.IdleInterlock`; set
     `INTERLOCK_TIMEOUT` and `IDLE_WARN_SECONDS`, write `_idle_is_armed()`
     and `_on_idle_expired(idle)`, call `_touch_activity()` on operator
     input. It publishes `idle_remaining` and `idle_warn_seconds` and
     answers `extend_idle` (declare the internal element).
   - Gamepad: mix in `model.gamepad_input.GamepadInput` (before `Model`);
     set `GAMEPAD_RATE_HZ`, write `_pumps_gamepad()`, `_on_gamepad(levels,
     edges)`, `_on_gamepad_lost(reason)` and `_on_gamepad_fault(reason)`,
     and put `*self._gamepad_elements()` in the schema. The mixin owns
     building, binding, the dropdown and log commands, the pump and the
     focus gate. Channels are the generic contract in
     `devices.gamepad.NEUTRAL` (`axis_x`, `axis_y`, `trigger_left`,
     `trigger_right`; edges `hat_x`, `hat_y`, `bumper_left`,
     `bumper_right`); the model maps them to its own axes.
   - Position source for Red Percent: `position` as `(x, y, z)`,
     `position_time` and `position_age`.
   - Downloads: a `file_save` command returns the path it wrote, under
     `output_root`.
10. **Register the device.** `Setup.register(cls)` (also a decorator) adds
    the class to the station: a Setup row in registration order, the scan,
    construction and reopen. Register before `Setup` is built. It refuses a
    class with no `NAME` of its own, a taken name or row key, an identity
    byte another class answers with, or a resource Setup cannot fill. For a
    model added at runtime, `controller.add(NAME, model, {"model": NAME,
    ...resources, "sim": bool})` reopens through the same registry.

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

## Still open

- **A SIM Rotator's stop never confirms** (CON-13): every simulated FULL
  STOP reads "Rotator did not confirm" - an owner call.
- **Per-view status rules and `device_word`** are still three copies, and
  Tk's lost-device sentence prints the raw class name (the remainder of
  CON-6).

Fixed on 2026-09-26: a button's own `args` travel in the Web (CON-1); the
Web's gate words come from the core (CON-2); the window-focus gate reaches
every device with `set_gate` (CON-3); stop-class commands are a schema
property (CON-4); Web downloads read `output_root` (CON-5); a fault gates
from the base (CON-9). Fixed on 2026-09-27 (Tier R, MOD-1..6): the gamepad
contract and mixin, one loop helper on the base, the idle mixin, the open
Setup registry with class resources and the `identify_port` hook, `Device`
defaults with `hardware_devices`, and the `inputs` rule. The open rows are
tracked in `BUGFIX_PLAN.md`.
