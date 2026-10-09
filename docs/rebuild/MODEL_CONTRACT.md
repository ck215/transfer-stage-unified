# Model contract: how to add a device (2026-09-26)

From the abstraction audit `handoff/audit-model-contract-2026-09-26.md`, which
built a non-probe device (`PiezoStage`) and drove it through all three views
(then Tk, Qt and Web; only the Web remains since 2026-10-07) without editing them. Landed by the lead with the CON-3/4/9 fixes applied.

A device becomes a first-class member of the station when it is a `Model`
subclass that declares its controls in a schema. The views (the Web; the retired Tk and Qt did too), the
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
   RGB Analysis (formerly Red Percent) sets `HOST = "Transfer Map"` (owner ruling 2026-09-28, one
   dashboard). A hosted model keeps its own Panel, commands, stop path and
   tests; while its host is launched the views draw its sections on the
   host's page and list no page of its own. It has no Setup row: the
   host's row launches it first, with no resources and the host's SIM
   choice (so it may declare none). Launched without its host (a test, a
   script), it is an ordinary page.
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
     `sch.log_stream(text, source_cmd, detached=True)`. An image with
     `model_attr=` is a still (2026-10-08, the picture previews): the
     attribute is a key that changes exactly when the picture does, and the
     view fetches the PNG only on a change (`preview_picture` /
     `preview_key` on the Sample DB and the Transfer Map). Keep the key
     cheap: it is read on every state poll.
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
   join cannot (RGB Analysis's in-flight run), then call the base.
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
   - Position source for RGB Analysis: `position` as `(x, y, z)`,
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

- The Web view renders every element with its tier, disclosure, slider,
  unit and axis grouping.
- Refusal lines appear where they happened, and greyed controls carry their
  reason (`views.base.gate_reason`).
- The Dashboard entry and the device page. The head opens the page.
- The stop disc, the per-model switch, the headline and the rail marks
  (latched, unconfirmed, faulted, energized). `stop_words` names the device.
- Lost-link and stale marks, which come from `devices` and `age`.
- The confirm dialog for every `NeedsConfirm`.
- The idle countdown with Extend, drawn from `idle_remaining`.
- The Quit and close-tab warnings, which come from `is_energized`.
- The Web watchdog.
- Clean shutdown on exit and on signals.

## Phases: the interactive procedure (2026-10-07)

A model whose operator follows a procedure (the Transfer Map: start screen,
armed, capture region, force mark, finalise or abort) declares its steps and
hides what the current step does not need. Owner ruling 2026-10-07; the
retired Tk and Qt views ignore the key and draw everything.

- `PHASES = ("setup", "new_tip", "region", "live", "marked", "finish", "new_store")` (the Transfer Map's) on the class: the
  step names, in order. Empty (the default) means no procedure. A name that
  starts `new_` is a **prompt phase**: a sub-step that asks for a few entries
  (the Transfer Map's `new_tip`, the Sample DB's `new_sample`, `new_chip`,
  `new_flake`, and both maps' `new_store`), entered from and left back to the main procedure; the Web draws
  it as a card dialog and does not number it in the strip.
- **A per-user store** (2026-10-08): mix in `model.store_choice.StorePrompt`
  (before `Model`, as the Transfer Map and the Sample DB do) and add its
  `PROMPT` (`"new_store"`) to `PHASES`. The host supplies the Params
  `store_path`, `store_dir`, `store_name`, and `has_store`, `db_path`,
  `output_root`, `NAME`, and returns `PROMPT` from `phase` while it has no
  store or `_choosing_store` is set. The mixin gives the suggested folder,
  the "Choose folder..." list, Change store / Cancel, the refusal of a store
  inside the station's own folder (`_refuse_inside_install`) and the backup
  hook (`backup_hook`, set by Setup). A live store is copied only through
  `store_choice.snapshot_sqlite` (the online backup API, then a rename).
- `phase` property: the current step, one of `PHASES`, or `""` at rest. It is
  published as `state["phase"]` (and the ordered steps as `state["phases"]`,
  so the Web view draws a step strip for any phased model), beside `state["mode"]`; a phase is WHERE IN
  THE PROCEDURE the operator is, the mode is WHAT THE HARDWARE IS DOING, and
  neither overloads the other (`latched` still comes through `mode`).
- `sch.section(..., phases=("live", "marked"))` draws a section only in those
  steps; `sch.phased(sch.button(...), "live")` does the same for one element.
  Absent means always drawn.
- `sch.is_shown(item, phase)` and `sch.shown_elements(schema, phase)` are the
  one rule, for the renderer and for the Panel: a control hidden in this step
  is REFUSED by `run` ("Mark is not part of the setup step."), so the Web API
  cannot call what the page does not show. A `stop=True` control and the
  Safety section can never be phased (`phased()` raises; the contract test
  checks), and a hardware-down command bypasses the step check.
- Hidden is not disabled: `enabled_when`/`disabled_when` still grey a control
  where it stands; `phases` removes it from the sheet.

`tests/test_model_contract.py` asserts against every registered class and a
two-step `PhasedModel`: every phase named is declared, `phase` is one of
them before and after a stop, no stop is ever hidden, every command is shown
in at least one step, and a model without a procedure hides nothing.

## Secondary readouts and hosted tiers (2026-10-07)

- `sch.readonly(label, attr, ..., secondary=True)` marks a readout as a small
  quiet line under the control it follows, in that control's row (the speed
  dials' steps/s under the percent). The client never counts it as the
  model's reading. Absent means an ordinary readout.
- `sch.section(..., hosted_tier=N)` (N in `schema.TIERS`) says which tier of
  its HOST's page (`HOST`, above) a hosted model's section is drawn in; on its
  own page the section keeps its `tier`. RGB Analysis's Live group is
  `hosted_tier=2` (behind its details on the trial page) and its Estimators
  section `hosted_tier=1`. A section without it follows its own tier on the
  host's page.
- **Speed dials** are percent in the schema (`Param(unit="%")`, 0-100) over a
  per-class ceiling (`MAX_SPEED`: chuck 600 steps/s); the stored value stays
  steps/s and the wire is unchanged. The DC Probe and the Chuck keep them.
- **Physical units over counts** (Stepper Probe, owner ruling 2026-10-09). The
  editable entries are µm and µm/s: `x/y/z_dist_um`, `x/y/z_step_um`,
  `full_speed_um_s` and `man_full_speed_um_s`.
  - Each is a view over the stored count Param (`Probe.VIEW_OF`), at 0.625 µm
    per count. That scale is flagged on the page as "lead unmeasured".
  - Each entry has its count line beneath it as a secondary readout (steps,
    steps/s).
  - A typed value rounds to whole counts, and the readout shows the achieved
    value.
  - The ceiling is 2000 µm/s (3200 counts/s). The stored Params, their names
    and the wire bytes are unchanged.

## One board per axis: tagged ports (2026-10-09)

A model may own several boards, one per port resource. The XYZ Stage
(`src/model/xyz_stage.py`) owns three Teensy boards: `RESOURCES = ("port_x",
"port_y", "port_z", "gamepad")`.

- **`PORT_TAGS = {resource: tag}`** must tag every port resource, with
  distinct tags, and the class needs an `IDENTITY`. `register()` refuses
  anything else. Each board answers the scan `DEV: <letter> <tag>` (the XYZ
  Stage's boards answer `DEV: x X`, from a tag stored in their EEPROM).
- **Setup places boards by tag, not by port order.** The row launches only
  when every tag answered on exactly one port. A missing, repeated or
  untagged board is a fault: the row's status says so ("fault: Z missing"),
  and Launch and start refuse it. A board on the wrong resource is refused
  too ("is the Y board, not X"). Choosing SIM simulates every port.
- **Flashing follows the same rule.** A `flashing.BOARDS` entry with
  `"tags"` is flashed all or none. Detection must find the complete set,
  or the board is a FAULT that flashes nothing and fails the flash. Each
  board is rebooted through its own port and stamped `"<board> <tag>"`.
  The check calls the board current only when every one is.
- **Devices in state.** Each link carries a `state_key` (`"Axis X"`), so
  `state["devices"]` names one entry per board. The model's link state is
  the worst of its ports.
- **The contract** builds every class from its declared resources (every
  `port*` "SIM", every `gamepad*` None).
- **A board's own log.** The XYZ Stage sets `LOG 2` on each link-up and
  writes every `EVT` line an axis sends to the log file, at debug. The
  board's account of a run is kept rather than lost on the board.

## Operator words (accounts)

`model.user.User` owns the signed-in account's config. Since 2026-10-08 it
is a `Panel`, NOT a `Model`: no device, no stop, never in the Controller, so
it is in none of the stop, energized or watchdog aggregates by construction,
and the contract test does not check it. Setup (the composition root) holds
the one current User as `Setup.user`; the Web view draws its sheet as the
rail's account menu under the reserved name `__user__`
(`views.web.server.USER_NAME`). Guest is a User with no account row, so every
model gets the station's defaults. The records the maps write are stamped with the operator: the
Transfer Map's `operator_id` / `operator_auth` and the Sample DB's `owner` /
`owner_auth` carry the account's email and the word `password`, or `guest` and
`guest` for a Guest (a map built without Setup says `station`).
Passwords are `SECRET_INPUTS` (masked in the client, redacted in the log, never
in `state`).

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
