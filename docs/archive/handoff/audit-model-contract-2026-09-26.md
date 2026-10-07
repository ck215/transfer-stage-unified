# Model-contract audit — 2026-09-26 (branch mvc-refactor, HEAD 48b2c97)

Read-only. Nothing in `src/`, `tests/` or `docs/` was edited and nothing was committed. HEAD moved while this audit ran, from 612397c to 48b2c97: the Tk Tier N/O merge a7bbcce and the core commit 48b2c97. Findings are stated against 48b2c97. The Qt and Web Tier N/O worktrees had not merged, and the matrix marks those rows "in flight".

**Prototype and scripts** are in `/private/tmp/claude-501/-Users-ianalbinogonzalez-Documents-GitHub-transfer-stage-unified-mvc-refactor/b12e7bce-b4ad-4079-b8fa-512ada187e83/scratchpad/audit-contract/`:

- `piezo.py` is the new device.
- `test_model_contract.py` is the proposed conformance suite.
- `tk/test_piezo_tk.py` drives the real TkDashboard over the fake-tkinter stand-in.
- `qt/drive_qt.py` drives the real QtDashboard offscreen, in a subprocess.
- `web/serve.py` and `web/scenario.cjs` drive the real WebView on :8251 through headless Chrome.
- `web/rotator_args.py` proves CON-1 with the real Rotator.
- `web/redsave.py` proves CON-5.
- `endrun.py` proves CON-4.
- `MODEL_CONTRACT.md` is the draft for `docs/rebuild/`.
- The run logs are `contract_run.txt`, `tk_run.txt`, `qt_run_raw.txt` and `web/web_run_raw.txt`. The Web capture is `web/piezo_web.png`.

Every process I started was killed. No window was opened, no `-m qt` run was made and no screencapture was taken.

## Executive summary

1. **Verdict: complete with gaps.** A new non-probe device, `PiezoStage`, was written only against `Model`, `Param`, `schema` and `Device`, and registered with `Controller.add`. Tk and Qt render it, move it autonomously and manually, confirm and stop it, and name it, with no view edited. Tk also counts down its idle clock and marks it energized.
2. **Gap 1 [S1]: the Web client drops a button's `args`** (`app.js:823`, `app.js:1891-1894`). Proved with the real Rotator: pressing **"Move -" with step 5 sent `move_relative_deg(+5.0)`**. In the candidate primary UI, a relative move goes the wrong way. The PiezoStage's per-axis Jog and Set-zero buttons fail there as "Command failed". CON-1.
3. **Gap 2 [S2]: safety seams keyed on names, not on the contract.**
   - `Controller.set_input_focus` gates only an attribute named `gamepad` (`controller.py:180`). A non-gamepad manual input source keeps driving while the window is unfocused (CON-3).
   - The stop-class list is command names in core (`panel.py:22-23,197`). Red Percent's real "Stop run" (`end_run`) is refused while any box holds bad text (proved) (CON-4).
4. **Gap 3 [S2]: registration is closed.**
   - `MODEL_TYPES` is a hard-coded tuple (`setup.py:132-143`), and non-`DEV:` handshakes are hard-coded (`setup.py:655`).
   - A model added at runtime cannot be reopened after its tab is closed (`controller.py:76-81`, proved).
   - The Web download ignores the model's `output_root` (`server.py:504-514`). The real Red Percent "Save" returns 409 (proved) (CON-5).
5. Step size, autonomous move with inputs, position with `rail`/`unit`, plot, detached log, gates, NeedsConfirm, stop latch, unconfirmed naming, Overview head, energized and fault marks, and the idle countdown are all driven by schema or state keys. None of them is probe-only in the views. The idle clock itself is still per-model code (CON-11).
6. The proposed `tests/test_model_contract.py` has 26 checks across 8 classes (184 cases). Against today's tree it fails 3 cases: CON-5 on Red Percent, the Rotator's SIM stop, and CON-3 on PiezoStage. It also caught the first draft of my own device (a go input locked in the only mode where the button runs).
7. The drive matrix (section 3):
   - **Tk: 17 PASS, 2 DEGRADED** (the focus gate, and an unrelated bad box blocking a mode change).
   - **Qt: 12 PASS, 3 DEGRADED, 2 in flight.**
   - **Web: 7 PASS, 3 DEGRADED, 3 BLOCKED, 2 in flight.** The blocked rows are args (manual jog, and confirm on an args button) and the download.
8. The gate words are wrong in the enabled_when direction in Web (inverted: "Not in autonomous mode" while in autonomous) and in Qt ("Only while autonomous"). The probe never exercised this direction. It is part of O3, which is in flight for both (CON-2).
9. The findings are 1 × S1, 6 × S2 and 8 × S3. The smallest fixes are one line (CON-1, CON-5, the `end_run` part of CON-4) or a few lines in core (CON-3, CON-9).
10. Route: CON-1 goes to the Web worktree now, or the lead lands it as a one-liner with a browser test. CON-3, CON-4, CON-5, CON-7 and CON-9 are lead core work. The rest is docs or `router`.

---

## 1. The contract as it stands

What a model author must provide today, with the line that enforces or consumes each piece. Anything not listed is optional.

### 1.1 Class and constructor

| Item | Must / may | Where it is consumed |
|---|---|---|
| `class X(Model)` (Model ⊂ Panel) | must | `model/base.py:16`, `panel.py:14` |
| `NAME` | must. It is the operator name, the Controller key, the Setup row and the rail line | `setup.py:114-143` (`_declared`, stops at Model/Panel so an inherited "Panel" does not count) |
| `IDENTITY` (one letter a–z, or None) | may | `setup.py:73` `IDENTITY_PATTERN`, `setup.py:739-757` |
| `NEEDS_PORT`, `NEEDS_GAMEPAD` | may (default False) | `setup.py:1016-1024`, `setup.py:1053-1063`; `NEEDS_GAMEPAD` hands the model a **gamepad name** from `GamepadHub` |
| `__init__(self, port=None, gamepad=None, sim=False)` | must. This is Setup's only call | `setup.py:1024`; `port` is a name, `"SIM"` or None |
| `ESTOP_BUDGET` (0.08 s) | may | `base.py:20,119` |
| `PARAMS = {name: Param}` | must, for every writable entry | `panel.py:199-221` (`_apply_inputs`), `panel.py:231-246` (`_defaults` seeds each as an attribute; a read-only property Param is not seeded) |

### 1.2 Lifecycle (override the hooks, never the template)

| Member | Must / may | Contract |
|---|---|---|
| `devices` | must if it owns hardware | A list of `devices.device.Device` (`open/close/is_open/status`, `device.py:4-22`). `state["devices"]` is `{type(d).__name__: d.status}` (`base.py:233`) |
| `open()` | inherited | Opens each device, then `_start_threads` (`base.py:39-43`). A model may extend it (the probe binds its gamepad, `probe.py:212-228`) |
| `_start_threads` / `_stop_threads` | may | Start and join loops, with a timeout (`base.py:68-72`) |
| `enable` / `disable` | `disable` should de-energize | `base.py:74-78`; `close` calls it |
| `close()` | inherited, never override | `_stop_threads`, then `halt`, then `disable`, then each `device.close()`. Each step is isolated (`base.py:45-66`) |
| `on_model_added/removed(name, model)` | may | Cross-model wiring, for example Red Percent following a position source (`controller.py:54-56,69-70`) |
| `release` | **not part of the contract**: it exists only on `FakeModel` (`tests/test_core_fakes.py:226`) and `GamepadHub.release` | — |

### 1.3 Stop, latch and fault (inherited; write one method)

| Member | Must / may | Contract |
|---|---|---|
| `_halt_hardware()` | **must** | The strongest stop on the priority lane. It must not wait on a lock without a timeout. It returns True only when the stop landed (`base.py:87-91`) |
| `halt()` | inherited | Calls `_halt_hardware`, with no latch (`base.py:93-95`) |
| `estop()` | inherited, never override | Latches first, runs `_halt_hardware` on a worker, waits `ESTOP_BUDGET`, and sets `stop_confirmed` (`base.py:97-125`) |
| `clear_estop(confirmed=False)` / `toggle_estop` | inherited | NeedsConfirm, then clear, then `events.forget(STOP_NOT_CONFIRMED)` (`base.py:127-148`) |
| `is_estopped`, `stop_confirmed`, `latched_at` | inherited | `base.py:150-162`; read by `Controller._stop_state` (`controller.py:204-214`) and every view |
| `gate_mode` | inherited | `"latched"` while latched, otherwise `mode_name` (`base.py:164-169`). Red Percent adds `no_region` (`red_monitor.py:360-366`) |
| `_guard(what)` | must be called at the top of every motion or heat command | Raises `Refused` while latched (`base.py:171-178`). The model must also pass `abort_if=self._estop.is_set` to the device write (BRIEF) |
| `_fault(reason)` / `_clear_fault` / `fault` / `is_faulted` | may | `base.py:181-199`. **It does not change `gate_mode`** (CON-9) |
| `is_active` | must | True while moving, heating or recording (`base.py:201-204`) |
| `is_energized` | should | Wider than `is_active`, defaulting to `is_active` (`base.py:206-212`). It drives `controller.state()["energized"]` (`controller.py:151`), the watchdog (`server.py:909`), the rail's energized ring (`tk.py:5932`), the Quit prompt (`tk.py:5272-5285`) and Setup's confirm-before-teardown |
| `_touch()` / `_expects_heartbeat()` | must, one or the other | `state["age"]`; a view marks the model stale above 1 s (`views/base.py:212-213`) |

### 1.4 Mode and commands

| Member | Contract |
|---|---|
| `mode_name` | The word that `enabled_when` and `disabled_when` are matched against (`panel.py:37-39,55-62`). Free vocabulary; the view words come from `views.base.GATE_WORDS` (`views/base.py:45-60`), and an unknown word falls back to "In X mode" / "Not in X mode" (`views/base.py:63-77`) |
| Commands | Any method or property named by a schema `command`, `data_command` or `source_command`. The allow-list is `panel.py:164-189`. A command returns a value or raises `Refused` / `NeedsConfirm`. Anything else becomes FAILED plus an acknowledged event (`panel.py:120-130`) |
| `NeedsConfirm(prompt, command, inputs=None, args=())` | The view re-runs it as `command(*args, True)` (`result.py` `rerun_args`; `views/base.py:172-173`; `app.js:1899-1901`). **`confirmed` must be the last positional parameter**, and the model must put its own fixed args back into `args=` |
| Duration | A command must return promptly. `Controller.run` serialises per model, and only `toggle_estop` and `estop` bypass the lock (`controller.py:163-166`). A model-level stop (`halt`, `end_run`, `extend_idle`) waits behind a slow command. The Rotator moves on a worker for this reason |
| Stop-class commands | `panel.UNGATED_COMMANDS` = `toggle_estop, estop, clear_estop, halt, stop_run, extend_idle`, plus `set_mode("disabled")`. These skip entry validation (`panel.py:22-23,191-197`). **Names, not a schema field** (CON-4) |
| `_refuse(reason)` | A convention, not in the base. The probe and Setup define it (`probe.py:1230-1232`, `setup.py:776`) to log at debug, then raise `Refused` |

### 1.5 Schema: element types and the fields a view honours

The element types are `schema.py:39-43` (14 types). A view that lacks `_make_<type>` cannot be constructed (`views/base.py:128-131`). Web: `ELEMENT_RENDERERS` (checked by `tests/test_view_web_client.py:79`).

| Field | On | Honoured by | Notes |
|---|---|---|---|
| section `title`, `layout="row"`, `tier` 1/2/3, `disclosure` | section | all three (ARCH audit §3) | Tier 1 has no disclosure (`schema.py:63-66`) |
| `text`, `model_attr`, `role` | all | all | `role` is one of neutral, go, danger, warning, info (`schema.py:46`) |
| `rail=True`, `unit` | readonly | all. Qt `rail_elements` (`qt.py:455`), Web `railElements` (`app.js:463`). At most 4 (`RAIL_READOUTS`) | A caption of `X:`, `Y:` or `Z:` becomes an axis group (CON-10) |
| `param` fields (`value_type`, `min`, `max`, `decimals`, `unit`) | entry, readonly | all | From `Param.to_schema` (`param.py:103-112`) |
| `slider=(lo, hi)` | entry | all | Beside the entry, never instead of it |
| `enabled_when` / `disabled_when` | entry, button, toggle, checkbox, dropdown | all, through `schema.is_enabled` (`schema.py:301-318`). Enforced again in `Panel._allows` / `_apply_inputs` | The words differ by view (CON-2) |
| `enabled_by` | dropdown and others | all | Web's wording assumes Setup's `_enabled` suffix (`app.js:216`) |
| `inputs` | button | **no view reads it**. Every writable entry travels with every command (`views/base.py:162-166`, `app.js:1881-1889`) | CON-8 |
| `args` | button | Tk and Qt (`views/base.py:170`); **Web drops them** (CON-1) | |
| `on_args` / `off_args` | toggle | all (`views/base.py:181-183`, `app.js:1944`) | |
| `confirm` | button | none (ARCH-11) | Use `NeedsConfirm` |
| `detached` | log_stream | all | Polled only while its window is open |
| `empty` | plot, image | all. Web falls back to command-named copy (CON-12) | `log_stream` has no `empty` (ARCH-12) |
| `tooltip`, `tooltip_on` | toggle, checkbox | Tk and Qt. Web ignores `tooltip_on` (ARCH-11) | |
| `type: "internal"` | any | renders nothing; passes the allow-list | For `extend_idle` and `clear_estop` |
| `_safety_section()` | last section | all. Tk and Web special-case `model_attr == "is_estopped"` for the switch (`app.js:843`) | Must be present, and last |

### 1.6 State keys the views read by name

- From `Panel.state` (`panel.py:71-80`): `name`, `mode` (= `gate_mode`), `model_mode`, `values` (every `model_attr` as display text).
- From `Model.state` (`base.py:222-238`): `is_estopped`, `is_faulted`, `stop_confirmed`, `latched_at`, `fault`, `is_active`, `age`, `devices`, and optionally `output_root` (top level, see CON-5).
- Model-optional, read generically:
  - `idle_remaining` and `idle_warn_seconds` (Tk `tk.py:5942-5944`; Qt and Web in flight).
  - `next_step` as a `values` key (Web `app.js:2085` suppresses the go caption when it is shown).
- From `Controller.state()` (`controller.py:142-154`): `models`, `is_estopped`, `is_active`, `energized`, `stop` (`latched/unconfirmed/every/since`), `closed`, `latest_event`.
- `devices` status words the views act on: `lost` (all), `simulated` (all), `verified` (Qt `qt.py:314-330`). The device **class name** is also read: `SerialPort` and `SMC100` in Tk `tk.py:6100` and Web `app.js:2977`.

### 1.7 Event titles the views key on

- **Constants now in `events.py`** (48b2c97): `STOP_NOT_CONFIRMED`, `IDLE_TIMEOUT_SOON`, `IDLE_TIMEOUT`, `BROWSER_SILENT`, `BROWSER_GONE`.
- **Local copies still exist**: `tk.py:161,172`, `qt.py:250`, `app.js:49`.
- A model raises `STOP_NOT_CONFIRMED` only through the inherited `toggle_estop`. `IDLE_TIMEOUT_SOON` and `IDLE_TIMEOUT` are raised by the probe's own interlock (`probe.py:1029-1062`); a new device must copy this.

### 1.8 Setup registration

- `MODEL_TYPES = {NAME: cls}` is built from a **hard-coded tuple** (`setup.py:132-143`), and its order is the display order. `_Stub.TABLE` (`setup.py:89-111`) is dead scaffolding now that the six classes declare their own attributes.
- Rows are keyed by `_key_for(NAME)` (`setup.py:146-148`). The row attributes are `{key}_port`, `{key}_gamepad` and `{key}_enabled`, with the commands `set_{key}_port` and so on.
- **Identify**:
  - Step 1: the SMC100 query at 57600, **hard-coded to the Rotator class** (`setup.py:655-672`).
  - Step 2: the custom firmware's `DEV: <letter>` at 500000 and then 115200 (`setup.py:674-757`).
  - Any other handshake needs a Setup edit.
- **Build**: `model_from_config` passes `port` (None if `not NEEDS_PORT`; `"SIM"` if sim) and `gamepad` (None if `not NEEDS_GAMEPAD`), plus `sim` (`setup.py:1009-1024`). `Controller.factory = model_from_config`, so `reopen` works only for classes in `MODEL_TYPES` (`controller.py:76-81`).
- Proven in the scratch dir: `S.MODEL_TYPES["Piezo Stage"] = PiezoStage` before `Setup()` gives a row. `DEV: p` then identifies as Piezo Stage, a SIM build succeeds and reopen succeeds. That one line is the whole registration, but it is a mutation of a module global and not an API (CON-7).

---

## 2. Hidden coupling outside `src/model/` and `src/devices/`

Grep of `views/{base,theme}.py`, `tk.py`, `qt.py`, `web/server.py`, `web/static/{app.js,index.html}`, `schema.py`, `panel.py`, `controller/*`, `app.py`, `packaging/*` and `tests/test_core_fakes.py`, with every hit read.

**Model names.** No code path in any view, `server.py`, `schema.py`, `panel.py` or `controller.py` branches on a model name. Every hit is a comment (`qt.py:361,725,1437,2944,3610,4178`; `tk.py:7,211,3510,3800,3923,5296`; `app.js:144,277,1801,2079`; `schema.py:266`). Setup names classes by import (`setup.py:46-49,101-110,134-135,655-661`), which is its job. `packaging/smoke.sh:39-41,185` and `smoke.ps1:103-116` hard-code the six (CON-14).

| # | File:line | What it assumes | New device |
|---|---|---|---|
| 1 | `web/static/app.js:823`, `:1891-1894` | A button carries no `args` | **(c) blocked** for any button whose command takes args (CON-1) |
| 2 | `controller/controller.py:180` | The manual input source is `model.gamepad` | (b) degraded: another source is not focus-gated (CON-3) |
| 3 | `panel.py:22-23,197` | The stop-class commands are `halt`, `stop_run`, `extend_idle` and `set_mode("disabled")` | (b) degraded: a stop named differently is validated. It already hits `end_run` (CON-4) |
| 4 | `controller/controller.py:163` | Only `toggle_estop` and `estop` skip the per-model lock | (b) a model-level stop waits behind a slow command (documented in the draft) |
| 5 | `web/server.py:504-514` | `output_root` / `run_dir` sit in `state["values"]` or the config | (c) blocked for downloads. It already hits Red Percent (CON-5) |
| 6 | `tk.py:6100-6112`; `app.js:2970-2982` | Hardware links are the classes `SerialPort` and `SMC100` | (b) wrong "Simulation, no hardware attached" line (CON-6) |
| 7 | `tk.py:1454-1459` (`DEVICE_WORDS.get(name, name)`); `app.js:547-557`; `qt.py:291-297` | Device class words | (b) Tk says "PiezoLink" raw; Qt and Web de-camel it (CON-6) |
| 8 | `qt.py:314-330` | A link is real when its status is `verified` | (a) fine if the device uses `ConnectionState` words (CON-6) |
| 9 | `tk.py:4243-4252` | A mode toggle is one whose command is `set_mode` (O4 fault hold) | (b) a device using another mode command is not held while faulted (CON-9) |
| 10 | `app.js:370-376`, `tk.py:96,408-411` vs `qt.py:418-422` | An axis is caption X, Y or Z (Web, Tk), or any single capital (Qt) | (b) a θ/U/V axis groups in Qt only (CON-10) |
| 11 | `app.js:189-226` (`MODE_REASONS`, `WAITS_FOR`, `modeWords`); `qt.py:379-402` (`WAITING_WORDS`) | Gate words for the probe's gate shapes | (b) wrong words for `enabled_when=("autonomous",)` (CON-2) |
| 12 | `app.js:250-259` `EMPTY_STATES` (`series`, `figure`, `gamepad_log`); `qt.py:3618` (`"gamepad" in caption`); `tk.py:3838-3844` ("X log" → "No X input yet.") | Copy by command or label name | (a)/(b) cosmetic (CON-12, ARCH-12) |
| 13 | `app.js:2085` (`next_step`); `app.js:2203-2206` (`/clear_estop/`); `app.js:216` (`/_enabled$/`) | Model attribute and command names | (a) cosmetic (CON-12) |
| 14 | `theme.py:105-106` `QUIET_VALUES` incl. "Not recording" | Status by exception: "None", "No", "Idle" and "Connected" readouts are hidden in tier 1 | (b) a device cannot opt a readout out (CON-12) |
| 15 | `tk.py:161,172`, `qt.py:250`, `app.js:49` | Local copies of the event titles | (a) until a title changes (CON-11 / ARCH-4) |
| 16 | `setup.py:132-143,655-672,73` | Closed registry; SMC100 handshake; a one-letter identity | (b)/(c) no Setup row or identification without a core edit (CON-7) |
| 17 | `controller.py:76-81` + `setup.py:1012-1014` | Reopen goes through Setup's factory | (c) a runtime-added device cannot be reopened after its tab closes (CON-7) |
| 18 | `app.py:87-89` | The gamepad hub always opens | (a) harmless |
| 19 | `tests/test_view_web_server.py:136` | The fake puts `output_root` into `values` | Masks CON-5 (a test-fake coupling) |
| 20 | `model/red_monitor.py:262,465-471` | A position source has `position` (x, y, z), `position_time` and `position_age`, with axes X/Y/Z | (a) a duck type, and PiezoStage met it. It is model-internal, but it is part of the contract (draft §9) |

The mode words beyond `GATE_WORDS`: `panel.py:44-45` (`latched`, `no_region` in `GATE_REASONS`), `panel.py:197` (`disabled`), and `app.js:189-205` / `qt.py:379-382` (local tables). Otherwise views use only `state["mode"]` against the element's own lists.

`tests/test_core_fakes.py` `FakeModel` is generic (no probe names) and is the right base for the conformance suite's minimal model. The three view FakeControllers are ARCH-8.

---

## 3. Proof: `PiezoStage`, a device the station has never seen

`piezo.py` is 326 lines. It is a three-axis piezo with:

- a per-axis step size (`x_step` and so on, float µm) and per-axis distances;
- a speed with a slider;
- Autonomous and Manual `set_mode` toggles;
- a **Move by** go button with `inputs=(x_dist, y_dist, z_dist, speed)`, `enabled_when=("autonomous",)`;
- six **Jog** buttons sharing `jog` with `args=(axis, ±1)`, `enabled_when=("manual",)`;
- a manual loop driven by a `JogKnob` Device (not a Gamepad; it has `set_gate` like one);
- per-axis **Set zero** raising `NeedsConfirm(args=(axis,))`;
- a readonly X/Y/Z with `rail=True`, `unit="um"`;
- a tier-3 plot with `empty=` and a detached log;
- an idle clock that publishes `idle_remaining` and `idle_warn_seconds` and declares `extend_idle`;
- a fake `PiezoLink` Device whose status is `simulated`, `verified` or `lost`;
- a `_halt_hardware` that confirms, or does not when `stop_lands=False`;
- the SIM constructor `(port=None, gamepad=None, sim=False)`.

It is registered with `Controller.add("Piezo Stage", model, config)` only, and no view was edited.

**Is the idle clock probe-only?** In the views, no. Tk reads `idle_remaining` and `idle_warn_seconds` from any model's state (`tk.py:5942-5944`), and the brief says the same for Qt and Web. In the model layer, yes: the interlock thread, the warning event and `extend_idle` live in `Probe` (`probe.py:968-1064`). A new device copies them (CON-11).

### Matrix (at 48b2c97)

| Check | Web | Tk | Qt |
|---|---|---|---|
| Every element renders (`_make_` / ELEMENT_RENDERERS), 27 elements | PASS: 29 widgets, no page error | PASS: 27/27 widgets | PASS: 27/27 widgets |
| Entry commits (Return / change) and refuses out of range | DEGRADED: travels with the next command only (O14 in flight) | PASS: `x_dist=2.5`; "X distance must be at most 100" | PASS: travels with the next command; the slider release commits (`qt.py:3341-3346`) |
| An unrelated bad entry does not block a mode change | not run | DEGRADED: box "999" refused Enter autonomous (CON-8) | not run (same `views/base.py:162-166`) |
| Go command (`Move by`) receives its declared inputs | PASS: X 0 → 1.5 | PASS: wire `M1.5,0.0,0.0@7.0` | PASS: wire `M1.5,0.0,0.0@7.0` |
| Manual navigation: Jog ± by the per-axis step (`args`) | **BLOCKED**: `jog()` got no args, "Command failed: Jog X+ did not complete" (capture) (CON-1) | PASS: x 1.5 → 2.0 (step 0.5) | PASS: x 1.5 → 2.0 |
| A non-gamepad manual source drives motion | PASS (model-level) | PASS: y 0 → 4.0 | PASS (model-level) |
| Window unfocus gates that source (D-4) | DEGRADED (CON-3) | DEGRADED: `JogKnob.set_gate` never called | DEGRADED: never called |
| Confirm round-trips (`Set X zero`, NeedsConfirm with args) | **BLOCKED**: args dropped, so `set_zero()` TypeError, no question asked | PASS: asked, then X = 0 | PASS: asked, then X = 0 |
| Gate reason, Move by while not autonomous | DEGRADED: "Disabled" (mode disabled); "Not in manual mode" while in manual (inverted) | PASS: "Not in autonomous mode" | DEGRADED: "Only while autonomous" |
| Gate reason, Jog while autonomous | DEGRADED: "Not in autonomous mode" (inverted) | PASS: "Not in manual mode" | PASS: "Not in manual mode" |
| Stop disc latches it (`_halt_hardware` called, priority write) | PASS: `latched: ["Piezo Stage"]`, write `!` | PASS: `halt_calls=1`, `(b'!', True)` | PASS: `halt_calls=1` |
| `stop_words` rail line | PASS: "Stopped: every model latched" | PASS | PASS |
| Unconfirmed stop names it | PASS: "Stopped: Piezo Stage did not confirm" | PASS | PASS |
| Overview head opens its device page | PASS: `opened=Piezo Stage` | PASS: `_opened='Piezo Stage'` | PASS: `page='Piezo Stage'` |
| Countdown line inside the window; Extend | in flight (not at HEAD) | PASS: "Piezo Stage powers down in 30 s."; Extend → 300 s | in flight (not at HEAD) |
| Rail marks it energized | in flight | PASS: energized ring drawn | in flight |
| `file_save` download against `output_root` | **BLOCKED**: 409 (proved with the real Red Percent, CON-5) | n/a (a desktop dialog; the model returns the path) | n/a |
| Sim line with a real non-SerialPort link beside a SIM model | DEGRADED by code (CON-6) | DEGRADED by code | PASS by code (keys on `verified`) |

The Tk evidence is in `tk_run.txt`, Qt in `qt_run_raw.txt`, and Web in `web/web_run_raw.txt` with `web/piezo_web.png`. The capture shows "Command failed: Jog X+ …" and "Set X zero …".

---

## 4. Findings

### CON-1 — The Web client drops a button's `args`: Rotator "Move -" moves positive [S1]
- **Where:**
  - `src/views/web/static/app.js:823` calls `panel.run(element)` with no args.
  - `app.js:1891-1894` sends `args || []` and never adds `element.args`.
  - Tk and Qt get this right through `views/base.py:170`.
- **Observed:**
  - `web/rotator_args.py` ran the real Rotator on `tests/test_rotator.py`'s FakeSMC at 10°, the real WebView and real app.js in headless Chrome. It set Step (deg) to 5 and clicked **"Move -"**. The stage received `[('move_relative_deg', 5.0)]`.
  - The Python path for the same element gives `-5.0`.
  - `move_by(sign=1, confirmed=False)` (`rotator.py`) defaults the missing sign to +1.
  - Every args-carrying button fails or misbehaves on Web. PiezoStage Jog and Set zero become "Command failed".
  - No test covers `args` on the client. Only the confirm re-run has one (`test_view_web_client.py:114`).
- **Expected:** Web sends `element.args` followed by any per-press args, as `PanelView._run` does.
- **Smallest fix:** In `run(element, args)`, use `const all = (element.args || []).concat(args || []);`. Add a server-level browser test: Rotator Move - on a FakeSMC gives a negative relative move. Add a static test that `renderButton` passes `element.args`.
- **Confidence:** high (reproduced end to end).

### CON-2 — Gate words in the `enabled_when` direction are inverted on Web and generic on Qt [S2]
- **Where:**
  - `app.js:213-226`: an unmet `enabled_when` returns `modeWords(currentMode)`.
  - `qt.py:385-402`: "Only while …" for any want not in `WAITING_WORDS`.
  - Tk uses `views.base.gate_reason` and is correct.
- **Observed:** For `enabled_when=("autonomous",)`:
  - Web says "Not in manual mode" while in manual, "Not in autonomous mode" on Jog while in autonomous, and "Disabled" in the disabled mode.
  - Qt says "Only while autonomous".
  - The probe never used this direction, so ARCH-1 and O3 saw only the `disabled_when` half.
- **Expected:** `views.base.gate_reason` in all three (served as `gate_words`, O3).
- **Smallest fix:** It is already O3 in the Web and Qt worktrees. Add the `enabled_when` case to their tests (Move by / Jog shapes).
- **Confidence:** high.

### CON-3 — The window-focus gate (D-4) reaches only an attribute named `gamepad` [S2]
- **Where:** `src/controller/controller.py:175-182`.
- **Observed:**
  - `set_input_focus(False)` never called `JogKnob.set_gate`, in all three views.
  - The PiezoStage's manual loop keeps moving on knob input while the window is unfocused.
  - Conformance case `test_window_focus_gates_every_manual_input_device[Piezo_Stage]` fails.
- **Expected:** Every manual input Device of every model is gated, whatever the attribute is called.
- **Smallest fix:** In `set_input_focus`, iterate `model.devices` and call `set_gate(is_focused)` on each device that has a callable `set_gate` (the Gamepad has one), or add a `Model.set_input_focus(is_focused)` hook that the base implements that way. Document `set_gate` as the manual-input contract.
- **Confidence:** high.

### CON-4 — The stop-class commands are a name list in core; Red Percent's "Stop run" is not on it [S2]
- **Where:** `src/panel.py:22-23` (`UNGATED_COMMANDS` has `stop_run`) and `:191-197` (`set_mode("disabled")`); `red_monitor.py:681,1222` (the command is `end_run`).
- **Observed:** `endrun.py`: while a run records, `end_run` with `red_min="abc"` in the box returned `Refused("Red at least is not a number: 'abc'")` and the run kept recording. With a clean box it stops. That is the O1 defect again, for the one model whose stop has a different name.
- **Expected:** A declared stop is never refused over unrelated input, whatever its name.
- **Smallest fix:**
  - Now: add `"end_run"` to `UNGATED_COMMANDS`.
  - Properly: make stop-class a schema property (`sch.button(..., stop=True)`, or `role="stop"`), with `toggle.off_args` treated as a stop when it leaves an energized mode. `_takes_hardware_down` then reads the element instead of names.
  - Consider letting such elements bypass the per-model lock in `controller.py:163`.
- **Confidence:** high (reproduced).

### CON-5 — The Web download looks for `output_root` where no real model puts it [S2]
- **Where:**
  - `src/views/web/server.py:504-514` reads `state["values"]` and the config.
  - `src/model/base.py:235-237` publishes `output_root` at the top level of state.
  - The fake that hides this is `tests/test_view_web_server.py:136`.
- **Observed:** `web/redsave.py` used the real RedMonitor with `save` returning a file inside its `output_root`. `GET /api/file?name=Red Percent&command=save` returned **409** "Red Percent does not declare an output root". Conformance case `test_file_save_output_root_…[Red_Percent]` fails. The Web "Save" of a real run cannot download.
- **Expected:** The server reads the model's published root.
- **Smallest fix:** In `_output_root`, check `state.get("output_root")` first, then `values`, then the config. Change the fake to publish at the top level, as `Model.state` does.
- **Confidence:** high (reproduced).

### CON-6 — "Is this hardware?" is decided by device class name (Tk, Web) and the status vocabulary is open [S2]
- **Where:**
  - `tk.py:6099-6112` (`LINK_DEVICES = ("SerialPort", "SMC100")`)
  - `app.js:2970-2982`
  - `qt.py:314-330` keys on `verified`
  - `devices/device.py:19-22` ("A short word … 'bound', 'unbound'…")
- **Observed:**
  - With a SIM probe open and a real `PiezoLink` that is `verified`, Tk and Web say "Simulation, no hardware attached" while a live stage is connected. Qt is right.
  - The lost-device sentence in Tk uses the raw class name (`tk.py:1458-1459`, "PiezoLink connection lost").
  - Qt and Web de-camel it.
- **Expected:** One rule, in `views/base.py`, keyed on status words. Any `verified` or `unverified` link is hardware.
- **Smallest fix:**
  - Move Qt's `simulation_line` and `device_word` into `views/base.py` and serve them to Web.
  - Close the vocabulary: `Device.status` must be one of the `ConnectionState` values or `bound`/`unbound`/`closed`. Check it in the conformance test.
- **Confidence:** high (code read; the Tk and Web branches are unambiguous).

### CON-7 — Registration is closed: hard-coded `MODEL_TYPES` and handshakes, and a runtime-added device cannot be reopened [S2]
- **Where:**
  - `src/controller/setup.py:132-143` (tuple), `:89-111` (dead `_Stub`), `:655-672` (SMC100 branch tied to `Rotator`), `:73` (one-letter identity), `:1009-1024` (factory)
  - `src/controller/controller.py:76-81`
- **Observed:**
  - A device appears in Setup only by editing `setup.py`, or by mutating `MODEL_TYPES` before `Setup()` is built. Proved: after that mutation, the row, `DEV: p` identification, the SIM build and reopen all work.
  - After `Controller.add` without Setup, closing its tab and reopening raises `ValueError: Piezo Stage was never configured`. With Setup's factory it raises `Refused: 'Piezo Stage' is not a known model`. The Web `/api/open_model` returns 409.
- **Expected:** `setup.register(cls)` (or discovery from `model/*`). A class hook `identify(port, should_abort) -> bool` replaces the Rotator branch. `reopen` falls back to `type(model)(**config)`.
- **Smallest fix:**
  - Add `register(cls, *, before=None)` that rebuilds `MODEL_TYPES`, and delete `_Stub`.
  - Have `Controller.add` remember `type(model)`, and have `reopen` rebuild with it when `factory` returns nothing for that model.
  - Move the SMC100 query to `Rotator.identify` (classmethod) and loop over the registered classes.
- **Confidence:** high.

### CON-8 — A button's declared `inputs` is decoration: every entry travels with every command [S3]
- **Where:** `views/base.py:162-166`, `app.js:1878-1889`, `panel.py:199-221`.
- **Observed:** An out-of-range X distance left in its box refused **Enter autonomous** in Tk ("X distance must be at most 100"). The same holds for any non-stop command, such as Jog or Set zero. This is D-5 as ruled, but `inputs` suggests otherwise, and no view reads it.
- **Expected:** Either send `inputs` only (validation scoped to what the command uses), or drop `inputs` from `sch.button` and document "all entries travel". Keep the conformance check that a go button's inputs can be edited where it runs.
- **Smallest fix:** Document it in MODEL_CONTRACT (done in the draft). The owner decides on scoping.
- **Confidence:** high.

### CON-9 — A fault does not gate anything unless the model's `mode_name` says "fault"; Tk still holds by command name [S3]
- **Where:** `model/base.py:164-169` (`gate_mode` knows only `latched`), `probe.py:1164-1170` (the probe maps FAULT through `mode_name`), `tk.py:4243-4252` (`_is_mode_toggle` checks `command == "set_mode"`).
- **Observed:** A device that calls `_fault()` keeps every control live unless its own `mode_name` returns `"fault"`. Tk's view-side hold catches only `set_mode` toggles.
- **Expected:** The base does it once.
- **Smallest fix:** `Model.gate_mode` returns `"fault"` while `is_faulted`, after `latched`. `GATE_WORDS["fault"]` already exists. Then delete Tk's `_held_by_fault`.
- **Confidence:** medium (read, not driven).

### CON-10 — Axis grouping is inferred from the caption, and the rule differs by view [S3]
- **Where:** `app.js:370-376` and `tk.py:96,408-411` accept X, Y or Z. `qt.py:418-422` accepts any single capital.
- **Observed:** A `θ:` or `U:` readout is an axis in Qt and a plain readout in Tk and Web.
- **Smallest fix:** `sch.readonly(..., axis="X")`, read by all three, with the caption kept as the fallback.
- **Confidence:** high.

### CON-11 — The idle clock is per-model code; event titles are still copied in the views [S3]
- **Where:** `probe.py:968-1064`; `tk.py:161,172`, `qt.py:250`, `app.js:49` (copies of `events.py`'s constants from 48b2c97).
- **Observed:** The views are generic in `idle_remaining`, `idle_warn_seconds` and `extend_idle`. A new device must still re-implement the interlock thread, the one-warning-per-period rule and `extend_idle` exactly.
- **Smallest fix:** An `IdleInterlock` helper in `model/base.py`, given a timeout, a warn window and an `on_timeout` callable, that publishes the two keys and declares `extend_idle`. Have views import the titles from `events`.
- **Confidence:** high.

### CON-12 — Copy keyed on command, attribute or label names [S3]
- **Where:** `app.js:250-259` (`series`/`figure`/`gamepad_log`), `app.js:2085` (`next_step`), `app.js:2203-2206`, `app.js:216` (`/_enabled$/`), `qt.py:3618`, `tk.py:3838-3844`, `theme.py:105-106` ("Not recording" and the other quiet words).
- **Observed:** Cosmetic for a new device. A plot whose data command happens to be called `series` inherits Red Percent's empty sentence. A tier-1 readout reading "None", "No" or "Idle" is hidden and cannot opt out.
- **Smallest fix:** ARCH-12 (`log_stream(empty=)`). A `quiet=False` element flag. `next_step` becomes an element `role="info"` hint flag.
- **Confidence:** high.

### CON-13 — A SIM Rotator is no rotator: no devices, and its stop never confirms [S3]
- **Where:** `rotator.py:100-104,109-110,428-432`.
- **Observed:** The conformance case `test_estop_confirms_in_sim[Rotator]` fails. Every FULL STOP in a simulated session reports "Rotator did not confirm", and the sim line cannot count it as simulated. This is intentional (MANAGER-21), but it makes SIM sessions cry wolf on the stop path.
- **Smallest fix:** An owner call: a simulated SMC100, or exempt SIM from "did not confirm".
- **Confidence:** high.

### CON-14 — The packaging smoke hard-codes six models [S3]
- **Where:** `packaging/smoke.sh:39-41,155-185`; `packaging/smoke.ps1:103-116`.
- **Observed:** A seventh registered model fails the exact `Launched:` log check.
- **Smallest fix:** Derive the rows from `/api/setup`.
- **Confidence:** high.

### CON-15 — The contract is undocumented [S3]
- **Where:** No `docs/rebuild/` page. The pieces are spread across the BRIEF's pinned interfaces, docstrings and the tests.
- **Observed:** The rules below each cost a failed first draft or a code dive:
  - the confirm re-run signature;
  - "return promptly";
  - the `status` vocabulary;
  - the position-source duck type;
  - the stop-class names;
  - "go inputs must be editable in the button's modes";
  - "the safety section last".
- **Smallest fix:** Land `MODEL_CONTRACT.md` (section 6) and `tests/test_model_contract.py` (section 5).
- **Confidence:** high.

---

## 5. The conformance test the repo lacks

The file is `scratch/audit-contract/test_model_contract.py`. It is parametrised over `setup.MODEL_TYPES` (the six), `PiezoStage` and a `MinimalModel` (one readout plus the safety section). Each model is constructed exactly as Setup does (`cls(port="SIM", gamepad=None, sim=True)`) and opened. The 26 checks:

- **Class:**
  - the constructor matches Setup's call;
  - `NAME`, `IDENTITY` and `NEEDS_*` are declared by the class, not inherited from Panel or Model (NAME only for a runtime-only class).
- **Schema:**
  - every element type is in `ELEMENT_TYPES`, with its required keys and a valid role;
  - tiers are valid, and Safety is last with `toggle_estop` and `clear_estop`;
  - every `command`, `data_command`, `source_command` and `options_command` exists;
  - every `model_attr` reads without raising;
  - every writable element is an entry with a Param that matches `to_schema`, and a slider sits within the Param bounds;
  - every go `inputs` is a writable entry;
  - every go input can be edited in some mode where the button runs;
  - toggle args pass the allow-list;
  - rail axis captions are X, Y or Z.
- **State:**
  - carries every key the views read;
  - values ⊇ every `model_attr`;
  - JSON-serialisable (Web);
  - `idle_remaining` comes with `idle_warn_seconds` and a declared `extend_idle`;
  - device status words are from the known set;
  - a `file_save` model publishes `output_root` where the server reads it.
- **Stop:**
  - `estop` latches, calls `_halt_hardware`, gives `gate_mode == "latched"` and sets `stop_confirmed` / `latched_at`;
  - `clear_estop` needs confirmation, then resets both;
  - SIM `estop` confirms;
  - every command gated off by the latch returns REFUSED while latched;
  - leaving an energized mode through a toggle's `off_args` with bad text in an entry succeeds;
  - `is_active` implies `is_energized` in every toggle-reachable mode.
- **Confirm:** every NeedsConfirm names a declared command, does not ask again on `(*args, True)`, and no button raises a TypeError against its declared args.
- **Focus:** after `Controller.set_input_focus(False)`, every device with `set_gate` is closed.

**Result today:** `STATION_NO_WINDOWS=1 python -m pytest test_model_contract.py` gives **184 cases, 181 passed, 3 failed** in 6.1 s.

- `test_file_save_output_root_is_where_the_web_server_reads_it[Red_Percent]` (CON-5)
- `test_estop_confirms_in_sim[Rotator]` (CON-13)
- `test_window_focus_gates_every_manual_input_device[Piezo_Stage]` (CON-3)

A mutant PiezoStage with the go inputs locked in autonomous (my first draft) is caught by `test_a_go_buttons_inputs_can_be_edited_where_the_button_runs`.

**Not asserted (views, not models):** the Web args bug (CON-1) needs a client test. Suggested: a static test that `renderButton`'s click passes `element.args`, and a browser test with Rotator Move - on a FakeSMC.

**To land:**

1. Drop the `PiezoStage` import.
2. Keep `MinimalModel`.
3. Mark `test_estop_confirms_in_sim[Rotator]` `xfail(reason="CON-13, owner call")` until it is decided.
4. Land CON-3 and CON-5 first, or xfail them with their IDs.

---

## 6. The contract document the repo lacks

The draft is `scratch/audit-contract/MODEL_CONTRACT.md` (850 words). It is a ten-step recipe: subclass and declare, devices, Params, schema, commands, stop, reporting, threads, optional capabilities (idle clock, position source, downloads), and registration. It is followed by "What you get for free" and "Still probe-shaped", which cites CON-1, 3, 4, 6, 7 and 9. For the lead to review and land as `docs/rebuild/MODEL_CONTRACT.md`. Its "Still probe-shaped" list shrinks as the fixes land.

---

## Route

| Fix | Files | Route |
|---|---|---|
| CON-1 Web sends `element.args`, plus a static and a browser test | `src/views/web/static/app.js`, `tests/test_view_web_client.py`, `tests/test_view_web_server.py` | Worktree web: the rb-o-web agent owns app.js now, so hand it over as an add-on. Otherwise the lead lands it after that merge (one line) |
| CON-2 `enabled_when` gate words | `app.js`, `qt.py` (+ tests) | Already O3 in the web/qt worktrees; add the Move by / Jog test shapes to their briefs |
| CON-3 focus gate over `devices` with `set_gate` | `src/controller/controller.py`, `tests/test_core_controller.py` | direct (lead core) |
| CON-4 `end_run` ungated now; `stop=True` element flag later | `src/panel.py`, `src/schema.py`, `src/model/*.py`, tests | direct: the name now; `router refactor-plan` for the flag |
| CON-5 server reads the top-level `output_root`; fix the fake | `src/views/web/server.py`, `tests/test_view_web_server.py` | direct (or the web worktree, since server.py is in its write set) |
| CON-6 one `simulation_line` / `device_word` in `views/base.py`; close `Device.status` | `views/base.py`, `tk.py`, `qt.py`, `app.js`, `server.py` (serve it), `devices/device.py` | direct core, then worktree per view (tk, qt, web) |
| CON-7 `setup.register`, class `identify`, reopen by type; delete `_Stub` | `src/controller/setup.py`, `src/controller/controller.py`, `src/model/rotator.py`, tests | agy (multi-file, own verification: golden gate + setup tests) |
| CON-8 document, or scope validation to `inputs` | `docs/rebuild/MODEL_CONTRACT.md` (or `views/base.py` + `app.js`) | direct doc now; the scoping is an owner call |
| CON-9 `gate_mode` returns `fault`; delete the Tk hold | `src/model/base.py`, `src/views/tk.py` | direct core, then worktree tk |
| CON-10 `readonly(axis=)` | `schema.py`, three views | router `patch-plan`, then worktree per view |
| CON-11 `IdleInterlock` in the base; views import the titles | `model/base.py`, `model/probe.py`, three views | agy (probe tests + golden gate) |
| CON-12 copy flags (`empty` on log_stream, `quiet`) | `schema.py`, three views | Rides ARCH-12; worktree per view |
| CON-13 SIM rotator semantics | `model/rotator.py` | owner call (D-n style), then direct |
| CON-14 smoke derives its rows | `packaging/smoke.sh`, `smoke.ps1` | router `transform` |
| CON-15 land the contract doc and conformance suite | `docs/rebuild/MODEL_CONTRACT.md`, `tests/test_model_contract.py` | direct (lead review) |
