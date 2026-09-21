# Root-Cause Synthesis

**Status: COMPLETE (first pass, 2026-09-19).** Verified against the
working tree at commit `9f88b54` (source unchanged since `046533f`).
Documentation-only pass; no source was modified.

## Purpose

`audit/*.md` holds 213 symptom-level findings organized per subsystem
(MANAGER-n, SERIAL-n, ERRORS-n, GAMEPAD-n, STEPPER-n, DC-n, ROTATOR-n,
TEMP-n, REDPERCENT-n, PYSIDE-n, VIEW-TKINTER-n, WEB-n). Fixing those one at
a time is the failure mode this branch has already exhibited. Examples from
the last two days of commits:

| Patch | What it fixed | What it left (root cause still live) |
|---|---|---|
| `12e9d59` Tk poll-loop focus guard | Tk entries unmodifiable | Views still own the commit protocol (RC-6). PySide and Web still have their own versions of the bug (PYSIDE-5, DC-8, TEMP-4). |
| `b42c13e` Tk `focus_set()` before command | Tk one-edit-behind numeric values | Same root. The fix is Tk-specific and invisible to the other two views. |
| `a7a8eaa` → `046533f` `_ensure_pygame_video()` at 4 call sites | "No video instance" SDL errors | The real cause is `pygame.quit()` being called while other pollers are alive, driven by a refcount that counts binds, not pollers (GAMEPAD-2, RC-13). Re-initialising video after the quit hides the symptom. The next poller still holds a stale `Joystick`. |
| `046533f` gamepad check before `enable()` | Manual mode energised coils without a pad | Mode is four independent booleans (RC-3). The same class of desync is still reachable via controller swap, Web `set_attr`, `is_stepping` never clearing, and controller loss clearing only `manual_flag`. |
| `4ffb2e3` always send `'d'` regardless of stale flag | Coils not cut on stop | Still no confirmation that `'d'` arrived. A write failure is swallowed and the flag is set anyway (RC-2). |

This document groups the audit findings under the **root causes** that
produce them. For each root cause it states the **intended behavior** (the
contract the object should honor) and proposes the **corrective design**:
the change that makes the object behave correctly by construction instead
of patching what the UI shows.

### Rules for the implementation fleet

1. **Fix at the root-cause level, not at the finding level.** A finding
   listed under RC-n is closed by RC-n's corrective design. A local patch is
   out of bounds unless the finding is tagged `LOCAL-OK` in the
   cross-reference table.
2. **REFACTOR-FLAG** marks places where the intended behavior conflicts with
   the existing structure. These need the refactor, not a workaround that
   makes the UI *look* right.
3. **SAFETY** marks risk to hardware state. Those root causes go first
   (see Sequencing).
4. **DECISION** marks a design question only the owner can answer. Do not
   pick an answer by default. The decisions are listed in one place:
   [Owner decisions](#owner-decisions-required-before-implementation).
5. Physical and hardware verification (gamepad mappings, firmware behavior,
   bench checks) stays with the human owner. Never delegate or assume it.
6. Each root cause lists **invariants**. Implementers turn them into tests;
   an RC is not closed until its invariants have tests.

---

## Root-cause index

| ID | Root cause | Class | Refactor? | Findings closed (approx.) |
|---|---|---|---|---|
| [RC-1](#rc-1-no-single-lifecycle-authority) | No single lifecycle authority: `SystemManager` is a passive dict; views decide destruction | SAFETY, integrity | **REFACTOR-FLAG** | ~45 |
| [RC-2](#rc-2-model-state-records-intent-not-observed-hardware-state) | Model state records *intent*, not *observed hardware state*; the transport swallows failures | SAFETY, integrity | **REFACTOR-FLAG** (incl. firmware) | ~25 |
| [RC-3](#rc-3-probe-mode-is-four-independent-booleans-not-a-state-machine) | Probe mode is four independent booleans, not a state machine | SAFETY, integrity | **REFACTOR-FLAG** | ~15 |
| [RC-4](#rc-4-control-and-io-loops-are-owned-by-views) | Control and I/O loops are owned by views (manual routing, gamepad pump, hardware polling) | SAFETY, parity | **REFACTOR-FLAG** | ~25 |
| [RC-5](#rc-5-emergency-stop-is-an-action-not-a-latched-state-motion-is-not-serialized) | Emergency stop is an action, not a latched state; motion is not serialized | SAFETY | yes (contained) | ~12 |
| [RC-6](#rc-6-parameters-are-untyped-strings-and-the-commit-protocol-lives-in-views) | Parameters are untyped strings, and the commit protocol lives in views | integrity, parity | **REFACTOR-FLAG** | ~20 |
| [RC-7](#rc-7-ui_schema-under-specifies-behavior-so-each-view-infers-it) | `ui_schema` under-specifies behavior, so each view infers it | parity | **REFACTOR-FLAG** | ~35 |
| [RC-8](#rc-8-no-result-or-status-channel-from-model-to-view-errorrouter-is-carrying-all-of-it) | No result or status channel from model to view; `ErrorRouter` is carrying all of it | integrity, UX | yes | ~25 |
| [RC-9](#rc-9-bootstrap-and-cross-model-wiring-are-duplicated-per-launcher-with-no-registry-events) | Bootstrap and cross-model wiring are duplicated per launcher, with no registry events | integrity, parity | yes (contained) | ~15 |
| [RC-10](#rc-10-the-web-adapter-is-a-parallel-controller-and-bootstrap-with-no-security-boundary) | The web adapter is a parallel controller and bootstrap with no security boundary | SAFETY, parity | **REFACTOR-FLAG** | ~20 |
| [RC-11](#rc-11-red-percent-run-semantics-are-undefined) | Red Percent run semantics are undefined (log lifetime, dimensions, unsaved data, thread robustness) | integrity (data) | contained | ~12 |
| [RC-12](#rc-12-gamepad-hardware-mapping-truth-is-unverified) | Gamepad hardware-mapping truth is unverified | SAFETY (motion) | no, verification | 4 |
| [RC-13](#rc-13-process-global-input-resources-are-modeled-as-per-instance) | Process-global input resources (pygame, claims, edge latch) are modeled as per-instance | integrity | yes (contained) | ~10 |

A single finding can appear under two RCs. The cross-reference table at
the end is the authoritative mapping.

---

## Root causes

### RC-1 No single lifecycle authority

**SAFETY · REFACTOR-FLAG**

**Intended behavior.** `base.py`'s `ManagedModel` docstring and README
§"Architectural integrity contract": one sanctioned way to shut a model
down (`teardown()`), one sanctioned emergency path (`emergency_stop()`), and
`SystemManager` as the owner of model lifetime. Every exit path (window
close, tab or dock close, reconfigure, Ctrl-C, SIGTERM, Cmd-Q, crash)
should run exactly the same, safety-first teardown on the live models.

**Actual structure** (verified: `src/model/system_manager.py:1-78`,
`src/model/base.py:1-22`):
- `SystemManager` is a locked dict with no policy. `register_model`
  silently overwrites without tearing down (`:10-12`). `remove_model` only
  pops the entry and **does not tear down** (`:24-27`). `_teardown_model`
  uses `hasattr` and silently skips models without `teardown` (`:21`).
  `ManagedModel` is never referenced outside its definition, so nothing
  enforces the contract.
- There is no "close one device" primitive that includes teardown, so each
  view improvised its own: PySide uses a `hasattr` ladder plus
  `del active_models[...]` (MANAGER-7, PYSIDE-2), and Tk just calls
  `notebook.forget()` (VIEW-TKINTER-1). The Web adapter swaps whole managers
  and holds a stale reference (MANAGER-1, WEB-1).
- The device configuration (port, controller, claims) is not kept anywhere
  after construction. As a result, "reopen" can only fabricate
  `StepperProbe(None, "None", {})`, a silent headless model (PYSIDE-1,
  MANAGER-8, ROTATOR-5, TEMP-6).
- `teardown()` ordering is not safety-first and not exception-safe:
  `BaseProbe.teardown` stops the poller before `power_down` with no
  try/finally (SERIAL-2, DC-3), and `RotatorSystem.teardown` never sends ST
  (MANAGER-10, ROTATOR-1).
- There is no process-level hook: zero `atexit`, `signal`, or
  `aboutToQuit` handlers in `src` (MANAGER-2/3, SERIAL-15,
  VIEW-TKINTER-8).
- Construction has no rollback: `build_models` leaks models it has already
  built when a later constructor raises (MANAGER-5), and Tk withdraws the
  setup window first (MANAGER-6).
- Reconfigure builds the new models before tearing down the old ones (Web:
  MANAGER-4, SERIAL-5, TEMP-8, ROTATOR-14).

**Why patching fails.** The documented "fix" for the PySide port leak
(ownership-and-lifecycle.md: "call `remove_model`, which internally calls
`_teardown_model`") is wrong, because `remove_model` does not tear down.
Adding `BaseProbe.disconnect()` to satisfy the `hasattr` ladder keeps the
ladder, which means every new model class must again be matched against
guessed method names. Each per-view fix (Tk `on_close_tab_callback`, PySide
`close_device_view`, Web `close()`) re-implements the policy a third time.

**Corrective design.**
1. **Make `SystemManager` the only lifecycle authority** and give it the
   primitives the views need:
   - `register(name, model, config)` enforces `isinstance(model,
     ManagedModel)` and keeps the `DeviceConfig` (port, controller, class,
     claims). Raise on duplicate names; never overwrite silently.
   - `release(name) -> None` = remove + `teardown()` + registry event
     (RC-9). This is what "close device" means if the owner chooses destroy
     semantics (**DECISION D-1**).
   - `reconfigure(configs)` = `shutdown_all()` **first**, then build with
     rollback, then register. It is the only path by which Web re-setup
     creates models.
   - `shutdown_all()` stays idempotent (it already clears the dict first).
     Add `emergency_stop` before `teardown` for every model, so no model's
     teardown can skip the stop.
   - Delete `reboot_model`'s `sleep(1)` on the caller thread (MANAGER-11).
     Either wire the method properly via `reconfigure`, or delete it.
2. **Make `teardown()` safety-first and exception-safe per model:**
   hardware stop → background activity stop → transport close, each step in
   its own `try/finally`. `RotatorSystem.teardown` sends ST first.
3. **Install one process-exit hook** in `app.py` for all three launchers:
   `atexit`, SIGINT/SIGTERM/SIGHUP, Tk `::tk::mac::Quit`, Qt
   `aboutToQuit`. It calls `shutdown_all()` on the *current* manager,
   through one accessor (`app_context.manager`), so no launcher can hold a
   stale manager.
4. **Move construction into `app_bootstrap.build_models(configs, manager)`**
   with register-as-you-go and rollback on failure. Launchers wrap it and
   only hide the setup window on success.
5. **Views never construct, destroy, or mutate `active_models`.** They call
   `manager.release(name)` or `manager.show(name)` (depending on D-1) and
   render. Delete PySide's `(None, "None", {})` constructors and the dead
   `reconnect_serial` branch (PYSIDE-13, SERIAL-14).

**Invariants (write as tests).**
- I-1.1 Every constructed model is either registered in exactly one live
  manager or has had `teardown()` called.
- I-1.2 `teardown()` sends the hardware stop even if the poller or reader
  cleanup raises. Test with fault injection on every sub-step.
- I-1.3 After any exit path (window close, tab or dock close under destroy
  semantics, Ctrl-C, SIGTERM, reconfigure), each hardware model has
  received its stop and its transport is closed.
- I-1.4 No two live transport handles exist for one port path at any time.
- I-1.5 Only `SystemManager` writes to `active_models`. Grep test:
  `active_models[` does not appear outside `system_manager.py`.

**Closes.** MANAGER-1..11,20; SERIAL-2,3,4,5,14,15,18; GAMEPAD-9;
STEPPER-1,3; DC-2,3,9; ROTATOR-1,2,5,10,14; TEMP-1,5,6,8,11(partial);
PYSIDE-1,2,13; VIEW-TKINTER-1,7,8,16; WEB-1,3,20; REDPERCENT-12.

---

### RC-2 Model state records intent, not observed hardware state

**SAFETY · REFACTOR-FLAG (spans firmware)**

**Intended behavior.** `system_enabled == False` should mean the coils are
off. A connection badge of "hardware" should mean the device is answering.
When the operator presses FULL STOP, they should be told whether the stop
was confirmed.

**Actual structure.**
- The transport swallows failures. `serial.enable()` and `disable()` catch
  write exceptions, report a popup, and return normally. The model then
  sets `system_enabled` unconditionally (SERIAL-1, ERRORS-6, STEPPER-4).
  `power_down`'s `'k'` failure is print-only (SERIAL-16).
- There is no ACK on `e`, `d`, or text commands; the firmware never replies
  (SERIAL table). The ping only checks that *some* `DEV:` line appears:
  `device_type` is parsed and never compared, and there is no protocol
  version (SERIAL-7, SERIAL-13, SERIAL-17).
- "Operating blind" (unverified) ports are treated as connected (SERIAL-7).
  A lost port is never detected: read and write errors turn into popups,
  while `ser.is_open`, `manual_flag`, and the web badge stay unchanged
  (SERIAL-8). The rotator (ROTATOR-9) and temperature (TEMP-2) models have
  the same flaw: when polling dies, a frozen value stays on screen.
- The wire protocol is not uniform across boards:
  - `'k'` is handled by **no** firmware.
  - The DC firmware has no `e` or `d` handler (SERIAL-10).
  - Raw `ser.write` from models bypasses the transport lock (SERIAL-11,
    DC-18, TEMP-7).
- The temperature firmware has no host-liveness watchdog: if the host
  dies, the last setpoint persists (TEMP-1 facts).
- SIM, None, and absent-port models silently no-op every command (SERIAL-9,
  ROTATOR-13, TEMP-10).

**Corrective design.**
1. **Give each hardware model an explicit `ConnectionState`**, exposed
   as a schema readonly field and shared by all views and the web badge:
   `SIMULATED | CONNECTING | VERIFIED | UNVERIFIED | LOST | CLOSED`. On the
   first transport exception, move to `LOST`, close the handle, report
   *once* (on the state transition), and have the model drop to a safe
   mode (RC-3 `FAULT`).
2. **Make transport methods return or raise outcomes.** Add
   `SerialTransport.write_command(bytes) -> None | raises TransportError`.
   All writes go through it under the lock, including `k`, G-code, and
   temperature frames. `enable()` and `disable()` raise on failure. The
   model sets `system_enabled` only on success and otherwise enters
   `FAULT("disable not confirmed — coils may be energized")`, which
   persists until cleared.
3. **Firmware protocol v2 (REFACTOR-FLAG, DECISION D-7, owner flashes the
   boards).** Required:
   - A versioned identity reply, e.g. `DEV: s v2`.
   - ACK lines for `e`, `d`, and stop.
   - `e` and `d` handlers on the DC board.
   - Either implement `k` or delete it from the host.
   - A host-liveness timeout on the temperature board (heater off after N
     seconds with no valid frame).

   `serial.__init__` takes `expected_type` and rejects a mismatched or
   older board. Until v2 lands, the model state for enable/disable is
   `UNCONFIRMED`, not `True`/`False`. Say so honestly in the UI.
4. **Make SIM an explicit simulated transport** (a fake that ACKs), not
   `ser=None`. Commands on a model with no connection return a refusal
   result (RC-8), never a silent success.

**Invariants.**
- I-2.1 `system_enabled` becomes `False` only after a confirmed disable
  (ACK, or, pre-v2, a successful write). Otherwise the model is in `FAULT`.
- I-2.2 After a transport exception, the next state poll shows `LOST`
  within one poll period, and no motion command is sent until the model
  reconnects.
- I-2.3 No code outside the transport touches `.ser`. Grep test:
  `\.ser\.` appears only in `controller/serial.py`.
- I-2.4 A board of the wrong type or protocol is rejected at construction.

**Closes.** SERIAL-1,7,8,9,10,11,13,16,17,20; ERRORS-6,7; STEPPER-4,9(partial);
DC-13,18; ROTATOR-9,11,13; TEMP-2,10,11; known-issues "ACK gap" and
"Raw Serial Bypass".

---

### RC-3 Probe mode is four independent booleans, not a state machine

**SAFETY · REFACTOR-FLAG**

**Intended behavior.** A probe is in exactly one mode: idle-disabled,
enabled-idle, autonomous (optionally executing a move), manual, or fault.
Every transition has one defined hardware side effect. The idle interlock
applies based on real inactivity.

**Actual structure** (verified `probes.py:57-61, 197-251`):
`system_enabled`, `auton_flag`, `manual_flag`, and `is_stepping` are set
independently by different methods, threads, and views:
- `is_stepping` is set by `macro_start_auton` and never cleared except by
  stop, which suppresses the watchdog forever (STEPPER-6, DC-1,
  VIEW-TKINTER-3).
- Controller loss clears only `manual_flag`, leaving `system_enabled`
  True (STEPPER-5, GAMEPAD-3, VIEW-TKINTER-4).
- The watchdog defers on `manual_flag` or `is_stepping`, so the idle
  timeout never fires in the modes that energize coils.
- Web `set_attr` can write `manual_flag` or `auton_flag` directly,
  bypassing `enable()` (STEPPER-11, DC-11).
- `set_controller` sets `controller_var` before knowing whether the bind
  succeeded (GAMEPAD-3, VIEW-TKINTER-10).
- The view-side falling-edge neutral manual packet re-arms firmware
  `MANUAL_ON` after the disable (VIEW-TKINTER-13).
- The reported "toggle desync after controller swap" is a symptom of this.
  GAMEPAD-4 traced it: on a *failed* swap the flag flips lazily on the next
  route tick; on Web it never flips.

**Corrective design.**
1. **Replace the four flags with a `ProbeMode` enum and one transition
   function**, `_transition(target, reason)`, which owns the hardware side
   effects (enable, stop, disable, neutral packet) in one ordered place.
   Keep `auton_flag`, `manual_flag`, and `system_enabled` as **read-only
   derived properties** so existing schema toggles still render. They must
   not be writable through the schema or API (RC-7).
2. **Autonomous "stepping" becomes a timed sub-state.** It ends on computed
   move duration or position arrival and calls `touch_activity()`. The
   watchdog measures `now - last_activity` in every energized mode and does
   not defer on flags. (**DECISION D-3**: should manual mode idle-time-out?
   Main did.)
3. **Every transition out of `MANUAL`** (controller lost, swap failed, swap
   to None, stop) goes through `_transition(ENABLED_IDLE or DISABLED)` with
   a stop packet. Whether toggle-off disables the coils or only stops
   motion is **DECISION D-2** (DC-10: holding torque on loaded axes).
4. **`set_controller` returns the bind result.** `controller_var` reflects
   the *actual* bound controller: the poller is the source of truth and the
   model mirrors it.
5. **Restart the watchdog with a fresh event per generation**
   (STEPPER-7).

**Invariants.**
- I-3.1 `mode ∈ {MANUAL, AUTONOMOUS}` ⇒ an enable was confirmed (RC-2) and
  a watchdog generation is alive.
- I-3.2 `mode == MANUAL` ⇒ a gamepad is bound *and* the input pump is
  running (RC-4). Otherwise the transition is refused.
- I-3.3 An energized probe with no activity for `_INTERLOCK_TIMEOUT` is
  disabled, subject to the D-3 answer for manual mode.
- I-3.4 No schema or API write can change the mode except through a
  command.

**Closes.** STEPPER-5,6,7(with RC-5),11(flags part); DC-1,10,11(flags),17;
GAMEPAD-3,4; VIEW-TKINTER-3,4,13; known-issues "manual/auton toggle desync"
(superseded; see GAMEPAD-4 for the trace).

---

### RC-4 Control and I/O loops are owned by views

**SAFETY · parity · REFACTOR-FLAG**

**Intended behavior.** The same model behaves identically regardless of
the frontend. The interlock comment in `probes.py:63-65` already states
this principle ("lives in the model, not the view, so every frontend shares
it"), but it was applied only to the watchdog.

**Actual structure.**
- **Manual input routing** (`poller.get_mapped_state()` →
  `send_manual_mode_command`) exists only as view timers: Tk `after(50)`,
  PySide `QTimer(20)`. Web has **none**, so Web manual mode energizes the
  coils, does nothing else, and defeats the watchdog (GAMEPAD-1, STEPPER-2,
  DC-5, WEB-2, MANAGER-13).
- **The gamepad pump** can only reschedule itself through
  `gui_root.after`, a duck-typed scheduler (`gamepad.py:637-640`). A
  non-GUI host cannot drive it. Every swap adds a second loop chain
  (GAMEPAD-7).
- **Hardware polling** (`read_position`, `poll_status`) runs as view timers
  on the GUI thread. PySide calls each one about 3× as often as Tk
  (PYSIDE-9, STEPPER-14, DC-16). The rotator's 10-retry poll can freeze the
  UI, including FULL STOP (ROTATOR-6, VIEW-TKINTER-12). Web polls on the
  request thread under the same lock as commands (WEB-8, ROTATOR-7).
- The serial handshake (1.5 to 4.5 s) and `reconnect_serial`'s `sleep(1)`
  run on the UI or request thread (SERIAL-6, SERIAL-14).
- Cadence diverges across views (5/20/50 ms), edges shorter than the tick
  are lost, and `activity_callback` fires outside manual mode (GAMEPAD-16,
  STEPPER-15, VIEW-TKINTER-11).
- The view-level pumps have no exception isolation. A raise kills the Tk
  chain silently (the last velocity keeps running) or storms popups in Qt
  (VIEW-TKINTER-5, PYSIDE-10).
- `flush_neutral` fights the 5 ms poll loop and is a no-op (GAMEPAD-8,
  PYSIDE-14, VIEW-TKINTER-9). Its intended semantics are **DECISION D-4**.

**Corrective design** (the conflict with existing structure is that
`ControllerPoller` is built around a GUI scheduler; this needs the
refactor):
1. **`ControllerPoller` owns a daemon thread**, or accepts an injected
   `Scheduler` interface with `call_later(ms, fn)` and `cancel(handle)`. It
   exposes `poll_once()`, `read_levels()` (non-consuming), and
   `drain_edges()` (single consumer, RC-13). Edges are latched at poll
   time, not at read time, so taps are not lost.
2. **Move the input pump into the model:** `BaseProbe._input_loop` runs
   while `mode == MANUAL`, at one documented rate, with exception isolation
   that transitions to `FAULT`. Delete Tk `_route_input` and PySide
   `input_timer`. Web gets manual mode for free, or it is explicitly
   refused on Web if the owner decides so.
3. **Hardware sampling happens on a model-owned thread per model**
   (rotator, probe position, temperature already has one). Results go into
   lock-guarded cached fields with a `last_sample_time`. Views and `/api/state`
   only *read* the cache; no view calls `read_position` or `poll_status`.
4. **Serial construction and verification happen off the UI thread**, with
   progress reported through the setup UI. Split `serial.__init__` (open)
   from `verify()`.
5. **Views keep one timer each: a render tick** that reads model state. It
   is exception-isolated and never does I/O.

**Invariants.**
- I-4.1 No file under `src/views/` calls `read_position`, `poll_status`,
  `send_manual_mode_command`, `start_polling`, or `get_mapped_state`.
  Grep test.
- I-4.2 Manual mode behaves identically (rate, edges, neutral on exit)
  under a headless test harness with no GUI. This proves Web parity.
- I-4.3 A 1 s stall in the transport never blocks the render tick or
  FULL STOP.

**Closes.** GAMEPAD-1,7,8,16; STEPPER-2,14,15; DC-5,16,17; SERIAL-6,12,14;
ROTATOR-6,7; PYSIDE-9,10(render part),14; VIEW-TKINTER-5,9,11,12;
WEB-2,8; MANAGER-13,19,20; known-issues "PySide6 vs Tkinter polling
threads" (reframed).

---

### RC-5 Emergency stop is an action, not a latched state; motion is not serialized

**SAFETY**

**Intended behavior.** `ManagedModel.emergency_stop`: "must be fast and
must never block." After FULL STOP, nothing that was queued, in flight, or
racing should move hardware until the operator issues a new explicit
command.

**Actual structure.**
- Rotator moves run on untracked daemon threads with no busy flag and no
  cancel. A move clicked before STOP can land after STOP (ROTATOR-8).
  Stacked relative moves bypass the ±30° tubing check (ROTATOR-4).
- `run_script` has no run token. Its thread resumes after stop plus
  re-enter (STEPPER-8).
- Temperature `send_settings` can land after `stop()` because the Web
  `full_stop_all` takes no device lock (TEMP-7).
- `full_stop_all` is sequential, on the GUI thread, with no timeout. One
  blocked serial lock delays the heater stop (MANAGER-19).
- The Web FULL STOP reports success before the result comes back, and
  per-model failures are hidden (WEB-18).

**Corrective design.**
1. **Give each model an `_estop` latch (`threading.Event`)**, set by
   `emergency_stop()` before any I/O. Every motion and heat command checks
   the latch *immediately before the transport write*, at the transport
   boundary, not at the UI boundary. The latch is cleared only by an
   explicit user command (`enter_*`, `send_settings`, `move_*`).
2. **Serialize motion per model:** one command worker per model, a queue
   with latest-wins or reject-while-busy semantics, and a `_run_id`
   generation token. The rotator validates the ±30° check against the
   *commanded* target, inside the worker.
3. **`full_stop_all` fans out in parallel** with a bounded join and
   returns per-model results (RC-8). ST and `d` writes take a priority path
   (`lock.acquire(timeout)`, then a raw write fallback) so a poll holding
   the lock cannot delay them.

**Invariants.**
- I-5.1 After `emergency_stop()` returns, no motion or heat frame is
  written until an explicit re-arm command. Test by racing a queued move.
- I-5.2 `emergency_stop()` returns in < 100 ms with a stalled transport
  (fake).
- I-5.3 At most one motion command is in flight per model.

**Closes.** ROTATOR-4,8; STEPPER-7,8; TEMP-7; MANAGER-19; WEB-18;
DC-18; REDPERCENT-3 (same pattern: run generation).

---

### RC-6 Parameters are untyped strings, and the commit protocol lives in views

**integrity · parity · REFACTOR-FLAG**

**Intended behavior.** The value the operator sees in a field is the value
the next command uses. Invalid values are rejected in one place with a
visible message. The same rules apply in every view and over the API.

**Actual structure** (verified: Tk `view.py:333-370`, PySide
`view.py:235-259`, `web_adapter.py:448-463`):
- Every model parameter is a `str`. Tk and PySide each guess whether a
  field is numeric by trying `float(current_value)`, with a hard-coded
  `attr != "serial_port"` exception duplicated in both. Web does no check.
- The model sanitizes lazily in `get_params` with **class-agnostic
  defaults**: a DC probe falls back to 400 speed and 16 steps instead of
  its own 120/1 (DC-4). There is no maximum and no cross-field check
  (DC-15, TEMP-13).
- Commit timing is defined differently by each view:
  - **Tk:** commits on FocusOut/Return, plus a `focus_set()` flush before
    each command (the `b42c13e` patch).
  - **PySide:** commits on `editingFinished`, with no flush, so the macOS
    one-behind bug is plausible (PYSIDE-5, DC-7). Its `QDoubleValidator`
    caps input at 3 decimals and depends on locale (PYSIDE-19).
  - **Web:** commits only through a Set button, and the field displays a
    placeholder rather than the value (DC-8, TEMP-4, ROTATOR-12,
    REDPERCENT-14).
- Temperature frames interpolate raw strings, so an empty field shifts the
  firmware's `strtok` fields: the wrong target is sent with wrong gains
  (TEMP-3).

**Corrective design** (**REFACTOR-FLAG**: the schema contract and every
model's attribute storage both change):
1. **Declare typed parameters in the model**, for example a
   `Param(name, type=float, min, max, decimals, unit, default)` table per
   class. Values are stored as numbers. Setters validate and raise
   `ValueError` with a message. Remove the lazy `_num(…, 400)` fallbacks.
   Defaults are per class.
2. **Put the type in the schema** (`"param": "full_speed"` or
   `"value_type": "float"`, plus bounds) so views never infer it.
   Validation happens in the model setter; views only display the
   rejection (RC-8).
3. **Make the commit contract explicit, pick one of two:**
   - (a) **Commands take their inputs explicitly.** The schema declares
     `"inputs": ["x_dist", …]` per button, and the view sends the current
     widget values with the command. The model validates the snapshot
     atomically. This eliminates the entire stale-value class in every
     view, including Web.
   - (b) **Views must commit all dirty fields before dispatching any
     command**, implemented once in a shared view-base helper per toolkit
     and on the Web client.

   (a) is the root fix. (b) is the Tk patch generalized. **DECISION D-5**;
   recommendation: (a).
4. **Temperature frame construction formats numbers from typed values**
   and refuses to send if any field is invalid.

**Anti-fix.** Do **not** copy Tk's `focus_set()` trick into PySide
`_execute_command` as "the fix" for PYSIDE-5. It is permitted only as a
labelled interim safety patch pointing here.

**Invariants.**
- I-6.1 For every schema command that reads parameters: typing a value and
  immediately invoking the command uses the typed value. One parametrized
  test per view, plus the API.
- I-6.2 No setter accepts NaN, inf, empty, or out-of-range values. The
  same rejection applies via Tk, Qt, and `/api/set_attr`.
- I-6.3 A DC probe's fallback defaults equal the DC class defaults.

**Closes.** known-issues #3/#4 (root; the Tk patches remain but become
redundant); DC-4,7,8,15; PYSIDE-5,6,19; TEMP-3,4,13; ROTATOR-12;
STEPPER-11 (values part); REDPERCENT-14.

---

### RC-7 `ui_schema` under-specifies behavior, so each view infers it

**parity · REFACTOR-FLAG**

**Intended behavior.** README §Current framing: "same capabilities, same
layout intent, same model lifecycle/ownership methods called." A
schema-driven UI only delivers that if the schema carries everything a
renderer needs, so renderers have nothing to invent.

**Actual structure.** The current schema has element `type`, `text`,
`model_attr`, `command`, `options_command`, `true_text`/`false_text`, and
`bg`/`fg`. Missing:
- **Writability.** The Web allowlist treats every `model_attr` as writable,
  including readonly positions, mode flags, `controller_var`, and live
  readouts (DC-11, REDPERCENT-20). Tk and PySide write `serial_port`
  through a special case, though nothing consumes it (DC-14, STEPPER-12).
- **Mode gating.** Web text-matches button labels (`"Full Stop"`,
  `"Power Down"`) that no longer exist, so Start Stepping and all entries
  are disabled in autonomous mode (DC-6, STEPPER-10).
- **Dropdown semantics.** Position Source has `model_attr` but no
  `command`. Web writes the attribute and never calls `set_stepper_model`
  (REDPERCENT-8, WEB-6). PySide's generic handler raises a `TypeError`
  (PYSIDE-7). Placeholder options send `""` (GAMEPAD-6, DC-19).
- **Formatting and units.** Raw floats and `None` are displayed
  (ROTATOR-9, REDPERCENT-19). Schema `bg`/`fg` hints are ignored by the
  QSS (PYSIDE-8).
- **View-special commands.** `open_controller_log` is intercepted by Tk and
  PySide and is a print stub on Web. `confirm_rotation_callback` is
  injected by view name match. The views hard-code `"SMC100 Rotator"` and
  `"Red Percent Window"` (VIEW-TKINTER-17).
- **Composite widgets.** Focus-area selection, plotting, and
  save-with-file-dialog are hand-built per view, so parity is impossible
  by construction:
  - The Red Percent tab is fully hand-built in Tk, schema-driven plus
    bolt-ons in PySide (duplicate Position Source, duplicate Save Log), and
    schema-driven plus a separate plotter on Web (PYSIDE-8, REDPERCENT-10,
    REDPERCENT-17, known-issues "Red Percent divergence").
- **Dead front-end commands.** Web calls `execute_script` and
  `send_raw_command`, which no model has (WEB-11, STEPPER-10).

**Corrective design** (schema v2; **REFACTOR-FLAG**: the three renderers
change together):
1. **Add element attributes:**
   - `writable` (default `false` for `readonly` and `toggle`).
   - `param` reference into the RC-6 table (type, bounds, format, unit).
   - `enabled_when` / `disabled_when` as a list of mode names from RC-3.
   - `command` required on any interactive dropdown.
   - `format` on readonly fields.
   - `role` (e.g. `primary`, `danger`) instead of raw colors.
2. **Add first-class composite element types** with one semantic contract
   and three renderers: `region_select` (focus area, with monitor and DPI
   handled in the model; REDPERCENT-18), `file_save` (the model produces
   bytes; the view decides where they go, so Web can download), `plot`
   (the model provides data; the view renders), `log_stream`
   (controller log).
3. **Add a `confirm` interaction contract:** a command may return
   `NeedsConfirmation(prompt)` (RC-8). Every view implements one generic
   confirm dialog. This replaces view-injected `confirm_rotation_callback`
   and makes Web enforce the ±30° check.
4. **Make Red Percent schema-driven in all three views** (**DECISION
   D-6**; recommended because the other two views already are). Tk's
   `RedPercentView` becomes the renderer of the v2 composite elements.
   Delete PySide's bolt-on controls.
5. **Add a schema conformance test:** every command, `options_command`,
   and `param` named in any schema exists on the model; no view references
   a device name or command name literally.

**Invariants.**
- I-7.1 Grep test: no string literal naming a device or a command appears
  in `src/views/**` (except the schema-driven renderer's type switch).
- I-7.2 The same schema renders the same set of controls in Tk, PySide,
  and Web, checked by a golden-structure test per renderer.
- I-7.3 `/api/set_attr` accepts only `writable` elements.

**Closes.** DC-6,11,13,14,19; STEPPER-10,12(UI part); REDPERCENT-6,8,10,13,
17,18,19; PYSIDE-7,8,12,20; WEB-6,7,11; VIEW-TKINTER-10(UI part),14,17;
GAMEPAD-5(UI part),6; ROTATOR-13(UI part); known-issues "Red Percent
strategy", "Plotting strategy divergence", "file_picker dead code",
"SelectionOverlay Linux" (through `region_select`).

---

### RC-8 No result or status channel from model to view; `ErrorRouter` is carrying all of it

**integrity · UX**

**Intended behavior.** The operator learns about every refused, failed, or
degraded action exactly once, in a form they cannot miss. Persistent faults
show as state, not as repeating popups.

**Actual structure** (verified `error_routing.py:1-53`):
- Commands return `None` on refusal (rotator > 30°, invalid numeric, no
  connection, no data to save). Views report "executed" (ERRORS-1,
  ROTATOR-3, WEB-12, REDPERCENT-7, TEMP-10).
- `ErrorRouter` is a global class with process-wide callbacks.
  Deduplication keys on the message text alone, for 5 s from the first
  send (ERRORS-8). It is not thread-safe (ERRORS-8). It runs dedup before
  checking whether callbacks are installed (ERRORS-10).
- Modal popups are used for info-level and repeating conditions. Nested
  event loops stack dialogs (ERRORS-8, PYSIDE-10). "Temperature Send"
  raises an info popup on every send (TEMP-12).
- Excepthook coverage differs per view. Tk and PySide miss
  `threading.excepthook`, and Tk misses `report_callback_exception`
  (ERRORS-4, PYSIDE-15). The Tk popup loop dies when the dashboard is
  destroyed (ERRORS-5, VIEW-TKINTER-2, MANAGER-17).
- Web errors go through a destructive pop into short toasts that inject
  HTML (ERRORS-2, ERRORS-3, WEB-9, WEB-17).
- Swallowed exceptions hide faults: heater-off at close, the rotator's
  `smc.close()`, Red Percent's bare `except:` writing 0.0 into the data
  (ERRORS-7).

**Corrective design.**
1. **Give commands a result type**: `CommandResult(ok | refused(reason) |
   failed(exc) | needs_confirmation(prompt))`. The schema dispatcher in
   every view, and the Web adapter, map it to UI feedback. `refused` is
   never shown as success.
2. **Separate *state* from *events*.** Persistent conditions (RC-2
   `ConnectionState`, RC-3 `FAULT`, "stale sample") are model state
   rendered as badges or banners. Only transitions produce events.
   Repeated faults then need no dedup.
3. **Make `ErrorRouter` an event bus**: thread-safe, a severity-keyed
   rate limit on `(severity, source, title)`, and a monotonic id per event.
   Frontends are subscribers:
   - Tk and Qt: a non-modal event log panel, plus a modal only for
     `error` with `requires_ack`.
   - Web: `?since=<id>` polling instead of a destructive pop.
4. **One `install_exception_hooks(bus)` in `error_routing`** covers
   `sys.excepthook`, `threading.excepthook`, and Tk
   `report_callback_exception`. Every launcher calls it. The Tk subscriber
   is bound to the process-lifetime root, not the dashboard.

**Invariants.**
- I-8.1 A refused command never produces a success indication, in any
  view.
- I-8.2 A fault persisting for 60 s produces one event and a visible
  state, not 12 popups.
- I-8.3 An exception in any background thread reaches the bus, in all
  three launchers.

**Closes.** ERRORS-1..10,12; ROTATOR-3,15; WEB-9,12,17; TEMP-12; PYSIDE-15;
VIEW-TKINTER-2; MANAGER-17; SERIAL-16; REDPERCENT-7 (result part);
known-issues "two ErrorPopupManager implementations", "~20 silent state
transitions" (these become state, not popups).

---

### RC-9 Bootstrap and cross-model wiring are duplicated per launcher, with no registry events

**integrity · parity**

**Actual structure.**
- The Red Percent ↔ probe linking block is copy-pasted **four** times
  (Tk `app.py:404-412`, PySide `app.py:733-741` and `view.py:902-917`, Web
  `web_adapter.py:166-174`).
- Controller enumeration is implemented three ways: Tk in-process, PySide
  in-process with `js.init()`, Web in a `python3` subprocess that
  fabricates "Virtual Controller" entries (MANAGER-18, GAMEPAD-18,
  WEB-15).
- The claims dict lifetime differs: Tk reuses it forever (MANAGER-16), and
  PySide reopen uses a private `{}` (PYSIDE-1).
- Web drops `enabled` during normalization, so disabled devices are built
  and wired (MANAGER-12, WEB-4, DC-12, REDPERCENT-15).
- `available_probes` holds references with no update on close or open,
  so the logged positions come from a dead probe (PYSIDE-3, STEPPER-13,
  REDPERCENT-11).

**Corrective design.**
1. **`app_bootstrap` becomes the single composition root**:
   `discover_ports()`, `discover_controllers()` (in-process, via
   `sys.executable`), `normalize_config()`, `validate_assignment()`,
   `build_models(configs, manager)`, and `link_models(manager)`. All three
   launchers call the same functions. There is no per-view setup logic
   beyond collecting user choices.
2. **`SystemManager` emits registry events** (`registered`, `released`).
   `RedPercentSystem` subscribes and maintains `available_probes` itself,
   dropping or reselecting on `released`. The cross-model reference is
   therefore owned by the dependent model, not by whichever view last
   touched it.
3. **Disabled devices are not constructed.** Delete `_disabled_in_setup`.

**Invariants.**
- I-9.1 `RedPercentSystem.available_probes` ⊆ live registered probes at
  all times.
- I-9.2 The three launchers produce identical managers for the same
  config. Test via `build_models` directly.

**Closes.** MANAGER-12,14(LOCAL-OK),16(claims part),18; WEB-4,15;
REDPERCENT-11,15; PYSIDE-3; STEPPER-13; DC-12; VIEW-TKINTER-6,17(wiring);
known-issues "RedPercent available_probes stale", "redundant js.init",
"parse_controller_id dead".

---

### RC-10 The web adapter is a parallel controller and bootstrap with no security boundary

**SAFETY · REFACTOR-FLAG**

**Intended behavior** (view-web.md "Contract mapping"): the server owns
the models; tabs are pure clients; tab load, reload, and close never
construct or tear down models; process exit tears down the current
manager; FULL STOP never waits on device locks. Only same-origin clients
may drive hardware.

**Actual structure.** Most Web findings are RC-1, RC-4, RC-7, and RC-8
expressed in a threaded server. What remains is specific to Web:
- Three objects hold a `system_manager` (window, server, adapter); only
  the adapter's is live (MANAGER-1, WEB-1).
- There is no Origin, Host, or Content-Type check, so a cross-site
  "simple" POST drives the stage (MANAGER-15, WEB-10).
  `/api/screenshot` serves the operator's screen.
- Per-device locks are shared by reads and commands, and the state poll is
  sequential (WEB-8, ROTATOR-7).
- Handler exceptions drop connections (WEB-14). Toasts use `innerHTML`
  (WEB-17).
- Session semantics are undefined: several tabs split errors and multiply
  polling; nobody watches an unattended run (WEB-19).
- Heavy unbounded work runs on request threads (WEB-21).
- Fetches have no timeout or staleness marker (WEB-22).
- `_BufferProxy` lazily constructs adapters (ERRORS-12).

**Corrective design.**
1. **One `AppContext` owns the manager.** The window, server, and adapter
   hold the context, never a manager. `reconfigure` (RC-1) is the only
   mutator and is single-flight (409 while running unless explicitly
   reconfiguring).
2. **Security boundary:**
   - Require `Content-Type: application/json`.
   - Validate `Origin` and `Host` against the bound address.
   - Require a per-launch token, injected into the served HTML and sent as
     a header.

   Apply this to every POST and to `/api/screenshot`.
3. **`/api/state` reads the RC-4 caches with no locks.** Commands go
   through the RC-5 per-model worker. FULL STOP stays lock-free.
4. **Errors use `since=<id>`** (RC-8). Each device gets a staleness
   marker. Every route is wrapped in a JSON error envelope.
5. **Client liveness (DECISION D-8):** decide whether an energized system
   with no client polling for N seconds should warn or FULL STOP.
6. **Until 1–3 land, reconsider the macOS default of `web`** (MANAGER-14,
   README addendum). **DECISION D-9.**

**Closes.** MANAGER-1,2(web part),4,15; WEB-1,3,5,8,10,14,16(LOCAL-OK),17,
19,20,21,22; ERRORS-12; ROTATOR-2,7(lock part).

---

### RC-11 Red Percent run semantics are undefined

**integrity (experimental data)**

**Intended behavior.** A monitoring run is a unit:
start → samples → stop → save or discard. The run's configuration (sync
dimensions, probe, metadata) is fixed for its duration. Data is never
silently lost or corrupted.

**Actual structure.**
- `data_log` persists across runs and aliases the model's live
  `sync_dimensions` list. Toggling a dimension mid-run raises `KeyError` in
  the thread and kills it while `monitoring` stays True
  (REDPERCENT-1/2, VIEW-TKINTER-15).
- There is no run generation, so a Stop→Start race produces two threads
  (REDPERCENT-3).
- There is no exception isolation or dependency check (REDPERCENT-4), and
  Start is allowed with no focus area (REDPERCENT-9).
- Shared fields are updated without locks (REDPERCENT-5).
- The unsaved-data prompt exists only on the explicit Stop button in
  Tk/PySide, and is lost on close, FULL STOP, and Web (PYSIDE-4, WEB-13,
  VIEW-TKINTER-14).
- The "velocity" column is actually gamepad stick deflection, and it is
  read through the edge-consuming `get_mapped_state` (REDPERCENT-16,
  GAMEPAD-15).
- The bare `except:` writes 0.0 positions into the data (ERRORS-7).

**Corrective design.**
1. **Introduce a `MonitoringRun` object**, created by `start_monitoring()`.
   It snapshots the configuration and owns its own thread, stop event,
   generation, and data log. `start` refuses without a focus area or
   dependencies, and returns `CommandResult` (RC-8).
2. **Sync-dimension toggles are `disabled_when: monitoring`** (RC-7 schema)
   or apply to the next run only.
3. **The unsaved-data check is a model query** (`pending_run_data()`).
   `SystemManager.release` and `shutdown_all` consult a
   `confirm_discard` hook that every view implements once (**DECISION
   D-10**: prompt or autosave on exit?).
4. **Velocity is derived from position deltas and timestamps.** Add a
   timestamp column. Invalid samples are flagged, never replaced with 0.0.

5. **A run is an addressable artifact set, not a loose file.** The run
   snapshots a `run_id` and an `output_root` resolved once — never from the
   process CWD — and emits `<run_id>_position.csv` (a plain rectangle, no
   comment block) plus `<run_id>_station_meta.json` (the configuration
   snapshot: baseline, focus-area px, threshold, cadence, start/stop times)
   under `output_root/<run_id>/`. Operator annotation — what was *intended*
   — is a `Param`-declared table rendered by all three views (D-6) and
   emitted under its own key, never merged with what the station *did*.
   (REDPERCENT-21,22,23 — **owner-added 2026-09-20, not audit findings**.)

**Closes.** REDPERCENT-1,2,3,4,5,9,16,21,22,23; PYSIDE-4; WEB-13;
VIEW-TKINTER-14,15; known-issues "RedPercent monitor race", "Dead State".

---

### RC-12 Gamepad hardware-mapping truth is unverified

**SAFETY (motion direction) · human verification only**

The substring-based whitelist sends foreign pads through Xbox layouts
(GAMEPAD-11). The T16000M Z axis and bumpers differ from main (GAMEPAD-12).
The D-pad sign was flipped against a mock test, not the stage
(GAMEPAD-13). The deadzone is applied twice (GAMEPAD-14).

**Corrective design.** The owner verifies each (controller, platform) pair
on the physical stage against main. The result is encoded as **one**
data table (`name, platform → axis map, sign, deadzone`) with an explicit
allowlist that rejects unknown pads. Tests assert the table, not
behavior guessed from a mock. **Never delegate this step.**

**Closes.** GAMEPAD-11,12,13,14.

---

### RC-13 Process-global input resources are modeled as per-instance

**integrity**

- `pygame` is process-global, but each `ControllerPoller` calls
  `pygame.quit()` when a module-level "count" reaches 0. That count
  increments per *bind*, including re-binds, and decrements per *close*,
  including never-bound pollers. Closing an unbound poller therefore kills
  a live poller's joystick (GAMEPAD-2). The `_ensure_pygame_video()`
  re-init series (`a7a8eaa`, `046533f`) patches the aftermath of that quit.
- Claims are never released, so they carry over into relaunches
  (MANAGER-16, GAMEPAD-10, VIEW-TKINTER-6).
- The edge latch in `get_mapped_state()` is consumed by two readers: the
  view's route loop and the Red Percent thread through `vel_*`
  (GAMEPAD-15).
- pygame is called from HTTP threads (GAMEPAD-18).
- macOS disconnect detection only checks the old joystick object
  (GAMEPAD-19).

**Corrective design.** A single module-level `InputService` owns
`pygame.init()` (once), `pygame.quit()` (only at process exit, via the
RC-1 hook), enumeration, the claims registry (acquire/release with
owner id), and a lock around all SDL calls. Pollers acquire a device
handle from it and release it on close. Edges have exactly one consumer
(RC-4 `drain_edges`); telemetry reads levels only.

**Anti-fix.** Do not add more `_ensure_pygame_video()` calls. Once
`pygame.quit()` is no longer called mid-session, the helper can collapse
to the single init.

**Closes.** GAMEPAD-2,5(claims part),10,15,18,19,21; MANAGER-16;
VIEW-TKINTER-6,10(claims part); known-issues #7 (root).

---

## Refactor flags (intended behavior vs. existing structure)

These places cannot reach intended behavior by local change. The structure
itself must move:

| Flag | Intended behavior | Structural conflict | Refactor |
|---|---|---|---|
| RF-1 | Closing or reopening a device keeps it connected to its configured hardware | Config is discarded after construction; views own destruction | `SystemManager` owns `DeviceConfig` and `release`/`show`/`reconfigure` (RC-1) |
| RF-2 | "Disabled" means coils off | Transport cannot report outcomes; firmware cannot ACK | `ConnectionState`, raising transport, firmware v2 (RC-2) |
| RF-3 | One mode at a time, with defined side effects | Four writable booleans | `ProbeMode` state machine; flags become derived read-only (RC-3) |
| RF-4 | Every frontend gets the same control behavior | `ControllerPoller` is built on a GUI scheduler; routing and polling live in views | Poller thread or scheduler injection; model-owned pump and samplers (RC-4) |
| RF-5 | The field value equals the command value, in every view | Parameters are strings; commit timing is per toolkit | Typed params plus commands with explicit inputs (RC-6) |
| RF-6 | Complete parity across views | Schema too thin; Red Percent hand-built in Tk | Schema v2 with composites; Red Percent schema-driven everywhere (RC-7) |
| RF-7 | Web is a safe, server-owned frontend | Three manager references; no security boundary; locks shared by reads and commands | `AppContext`, security boundary, cached state (RC-10) |

---

## Owner decisions required before implementation

The fleet must not choose these answers by default. Recommendations are
given, not assumed.

| ID | Question | Options | Recommendation | Blocks |
|---|---|---|---|---|
| D-1 | What does closing a device tab or dock mean? | (a) hide: model and connection persist, reopen = show; (b) destroy: `release()`, reopen rebuilds from the stored config | **ANSWERED 2026-09-19: (a) hide**, plus a safe stop of motion on hide. See [Answered decisions](#answered-decisions) for the consequences. | RC-1, RC-9 |
| D-2 | Does turning Autonomous or Manual off stop motion only, or also disable the coils? | stop-only (main) / disable (current) | **ANSWERED 2026-09-20: disable.** Current behavior stands; DC-10's divergence from main is intentional. No hold-torque variant without reopening D-2. | RC-3 |
| D-3 | Should manual mode idle-time-out? | yes (main) / no (current) | **Yes**, on real input inactivity | RC-3 |
| D-4 | What should happen on window focus loss? | ignore input while unfocused / stop / nothing | **ANSWERED 2026-09-20: gate input, never stop.** Child-dialog deactivation is not focus loss. | RC-4 |
| D-5 | How is the commit contract enforced? | (a) commands carry inputs; (b) commit-before-dispatch | **(a)** | RC-6, RC-7 |
| D-6 | Red Percent rendering strategy | schema-driven / hand-built | **Schema-driven**, via v2 composites | RC-7 |
| D-7 | Firmware protocol v2 (versioned identity, ACKs, DC e/d, `k`, temperature host watchdog) | adopt / defer | **Adopt**. It requires reflashing every board. | RC-2 |
| D-8 | Should the Web client's liveness gate energized operation? | warn / FULL STOP / nothing | **ANSWERED 2026-09-20: warn at N s, FULL STOP at M s while motion is active** (suggested N=5, M=15). Extends the existing interlock watchdog with a second threshold; needs S7 item 2 first. | RC-10 |
| D-9 | Default view on macOS when no flag is given | web / tkinter | **ANSWERED 2026-09-19: tkinter**, until the codebase is stabilized (not merely until RC-10 items 1–3 land) | RC-10 |
| D-10 | Unsaved Red Percent data on exit or release | prompt / autosave / discard | Autosave to a timestamped file, plus a prompt where UI allows | RC-11 |
| D-11 | Serial port reconnect at runtime | supported (setup-wizard path) / not supported (make the field readonly) | **ANSWERED 2026-09-19: not supported.** Purge it as a legacy feature — readonly field, dead code deleted, no deferred `reconfigure` promise. | RC-1, RC-7 |

### Answered decisions

**D-1 — closing a tab or dock means HIDE (2026-09-19, owner).**

The model, its serial connection, its controller binding and its config all
stay alive. Reopening shows the existing model; it never constructs one.

A note on the evidence, since the original recommendation cited it wrongly:
"it matches Tk's actual behavior" was **false**. Tk's tab close is
`notebook.forget()` with `on_close_tab_callback` never assigned anywhere in
`src` (`tkinter/view.py:120,158-162`), and there is no re-add path — so Tk
today is a one-way hide that leaves the model and every `after()` loop
running with no way to get the tab back. That is a leak wearing a hide's
clothing, not the semantics D-1 chose. The decision stands on its own
merits: it keeps the port and the controller binding stable, and it deletes
the reconstruct-on-reopen path that produced the video-driver and
toggle-desync bug history.

Consequences for implementation:
1. `release()` is reached only by `shutdown_all()` and `reconfigure()`. It
   is not what a tab or dock close calls.
2. `SystemManager` gains `show(name)` / `hide(name)`. Hide performs a safe
   stop of motion (per D-2) and leaves the transport open.
3. PySide's `StepperProbe(None, "None", {})` constructors
   (`pyside/view.py:886-892`) and the whole lazy-construct branch of
   `open_device_view` are **deleted**, not repaired.
4. Tk must gain a real re-add path, or lose the close affordance, so that
   hide is reversible. Its current one-way `forget()` does not satisfy (a).
5. **RC-4 becomes a prerequisite, not a parallel track.** A hidden device
   keeps running, so the loops that drive it must belong to the model, not
   to a destroyed widget. Hiding a tab while the view still owns its
   `after`/`QTimer` chains either keeps a dead widget's loop alive or
   silently stops polling a live, energized device.

**D-9 — macOS default view is tkinter (2026-09-19, owner),** until the
codebase is stabilized, not merely until the RC-10 security items land.
`app.py`'s argparse fallback is corrected to match; the docs already claim
this.

**D-11 — runtime serial reconnect is not supported (2026-09-19, owner).**
Purged as a legacy feature. The `serial_port` schema field becomes
readonly, `reconnect_serial` and its dead PySide branch
(`pyside/view.py`, SERIAL-14, PYSIDE-13) are deleted, and the rotator's
schema "Reconnect" button goes with them. Port assignment happens once, in
setup. No deferred `reconfigure`-based reconnect is promised.

---

## Recommended sequencing for the implementation fleet

The waves are ordered by dependency. Within a wave, the items are
independent enough for parallel agents.

- **Wave 0 (no code).** Owner answers D-1 to D-12 — **all are answered
  except D-7** (see [Answered decisions](#answered-decisions) and
  `../implementation/progress.md`'s decision table, which is authoritative).
  D-7 blocks only S16. Apply the doc
  corrections below (corrections 8 and 11 applied 2026-09-19). RC-12
  hardware verification can start now, in parallel.
  - D-9 and D-11 are small enough to land as code in Wave 0: the `app.py`
    macOS argparse fallback, and the readonly `serial_port` field plus
    deletion of the reconnect paths.
- **Wave 1 (SAFETY foundation, smallest root-level changes).**
  - RC-1 items 1–4: `SystemManager` authority, safety-first `teardown`,
    process-exit hook, `build_models` rollback.
  - RC-2 item 2: raising transport and single write path.
  - RC-5 item 1: E-stop latch.
  - RC-10 items 1–2: `AppContext` and security boundary. These are small,
    and the web shutdown and CSRF holes are live.
- **Wave 2 (behavioral core).**
  - RC-3 state machine, which depends on RC-2 outcomes.
  - RC-4 loop ownership, which depends on RC-13 `InputService` (do both
    together).
  - RC-5 items 2–3.
  - RC-2 item 1 (`ConnectionState`).
- **Wave 3 (contract and parity).**
  - RC-6 typed params, then RC-7 schema v2 and the three renderers.
  - RC-8 result type and event bus.
  - Views become thin renderers here. Most parity findings close as a side
    effect, and the remaining ones should be re-audited, not pre-patched.
- **Wave 4 (features on the new core).**
  - RC-9 composition root and registry events.
  - RC-11 `MonitoringRun`.
  - RC-10 items 3–5.
  - RC-2 item 3: firmware v2, when the owner schedules reflashing.
- **Any time:** `LOCAL-OK` findings (dead code, cosmetic layout, launcher
  argparse).

**Interim SAFETY patches** are allowed before their wave *only* if they
are marked in code with `# INTERIM: see root-causes.md RC-n` and do not
entrench the structure being replaced. Candidates:
- Web `close()` using the adapter's manager (MANAGER-1).
- `atexit` → `shutdown_all` (MANAGER-2/3).
- Web `initialize_setup` rejecting a request while running (MANAGER-4).
- `teardown` reorder (SERIAL-2).
- Web refusing `toggle_manual` until RC-4 (GAMEPAD-1).
- Tk removing the tab-close affordance until D-1 (VIEW-TKINTER-1).

### Anti-fixes (do not apply; they entrench a root cause)

| Tempting patch | Why not | Root |
|---|---|---|
| Add `BaseProbe.disconnect()` so PySide's `hasattr` ladder closes the port | Keeps view-owned teardown; the next model class breaks again | RC-1 |
| Call `system_manager.remove_model(name)` "to tear down" | It does not tear down | RC-1 |
| More `_ensure_pygame_video()` call sites | Treats the aftermath of an unnecessary `pygame.quit()` | RC-13 |
| Copy Tk's `focus_set()` flush into PySide or JS | Generalizes a toolkit-specific patch; leaves the API path open | RC-6 |
| Set `confirm_rotation_callback` on Web to something that auto-confirms or blocks a request thread | Bypasses a tubing-safety check or deadlocks the server | RC-7/8 |
| Add sequence numbers to messages to beat `_is_spam` | Converts dedup into a popup storm | RC-8 |
| Add `try: … except: pass` around view poll loops | Hides the fault that should transition the model to `FAULT` | RC-4/8 |
| Clear `manual_flag` in more places after controller swap | Adds another writer to a flag that must become derived | RC-3 |
| Add Red Percent linking to a fifth call site | Formalize it via registry events instead | RC-9 |
| Add `self.after_cancel`/timer-stop calls for hidden Tk tabs while keeping view-owned loops | Loops move to the model | RC-4 |

---

## Corrections to existing docs (apply in Wave 0)

Verified during this pass or by the audits (source audit ID in
parentheses):

1. `ownership-and-lifecycle.md` §close_device_view: the recommended fix
   says `remove_model` "internally calls `_teardown_model`". **False**:
   `system_manager.py:24-27` only pops the entry (PYSIDE doc-corrections).
2. `ownership-and-lifecycle.md` and `known-issues.md` (serial-port leak):
   reopen **does not** open a second handle on the same port. It passes
   `port=None` and produces a silent headless model. The old handle leaks
   (SERIAL-19, STEPPER-1, PYSIDE).
3. `ownership-and-lifecycle.md`: "Tkinter flow constructs a fresh model
   when a tab is reopened". **False**: Tk has no reopen; tab close is
   `forget()` only (VIEW-TKINTER doc-corrections).
4. `ownership-and-lifecycle.md` §teardown comparison: `power_down()` ≠
   `disable()`; `power_down` also writes `k` (PYSIDE). Note separately
   that no firmware handles `k` (SERIAL table).
5. `ownership-and-lifecycle.md` threading table: temperature "backoff" is a
   fixed 0.1 s retry, and it gives up permanently after 5 failures
   (TEMP-2).
6. `known-issues.md` "Fixed" #2, #5, #7, #14 say "uncommitted, pending
   batch". They landed in `046533f`.
7. `known-issues.md` "manual/auton toggle desync" hypothesis (transient
   falsy gamepad during a successful swap) cannot occur in Tk. See
   GAMEPAD-4 for the actual per-view trace (failure-path lazy flip; Web
   never flips).
8. **APPLIED 2026-09-19.** `known-issues.md` and `views.md` "Tk polls in UI
   thread, PySide uses QTimers": both run on the GUI thread. Stated
   precisely: both *dedicated* poll timers are 100 ms
   (`tkinter/view.py:565-578`, `pyside/view.py:148,153`); the ~3× figure
   comes from PySide additionally polling in `_poll_model` at 50 ms
   (`pyside/view.py:402-405`) (PYSIDE-9).
9. `controllers.md`: `_active_poller_count` counts binds, not pollers.
   `get_mapped_state` returns `{}`, not a zeroed dict (GAMEPAD-20).
10. `error-routing.md`: "gamepad polling threads" is wrong (GUI-loop
    callback); line numbers for `probes.py` 455/457 and
    `temperature_system.py` 178 are wrong (ERRORS-11, TEMP-11).
11. **APPLIED 2026-09-19.** `views.md:111` `populate_sidebar` does not use
    `SYSTEM_CONFIG` — no such name exists in `src/`; the device list is
    hardcoded at `pyside/view.py:786-789` (PYSIDE). `views.md` manual
    neutral send is 50 ms in Tk, not 20 ms; 20 ms is PySide's rate
    (VIEW-TKINTER).
14. **APPLIED 2026-09-19.** `views.md` `close_device_view` omitted
    `model.poller.close()` (`pyside/view.py:840`) from the hand-rolled
    teardown list, which reads as though the missing `disconnect()` is the
    only defect on that path. `close()` is what decrements the global
    poller refcount and can fire `pygame.quit()` mid-session (RC-13).
15. **APPLIED 2026-09-19.** `known-issues.md` Fixed #1 cited `addb0b8` for
    the Python-side fix. That commit exists but is orphaned — unreachable
    from this branch after a rebase or amend. Cite `4ffb2e3`.
16. **APPLIED 2026-09-19.** `README.md` said 2 pending design decisions;
    `known-issues.md` lists 3. This document's own finding count was
    "~230"; the audit IDs number 213.
12. `models.md`: rotator `connect()` is synchronous, not `_run_async`
    (ROTATOR-15). `detect_red` uses no OpenCV (REDPERCENT summary).
13. `README.md` Quick Facts counts are superseded by this document and
    `audit/`.

---

## Finding → root-cause cross-reference

`LOCAL-OK` = a local fix is acceptable (dead code, cosmetic, isolated).
`doc` = documentation correction only.

| Audit | Mapping |
|---|---|
| MANAGER | 1 RC1/RC10 · 2 RC1/RC10 · 3 RC1 · 4 RC1/RC10 · 5 RC1 · 6 RC1 · 7 RC1 · 8 RC1 · 9 RC1 · 10 RC1/RC5 · 11 RC1 · 12 RC9 · 13 RC4/RC10 · 14 LOCAL-OK (+D-9) · 15 RC10 · 16 RC13 · 17 RC8 · 18 RC9 · 19 RC5 · 20 RC4 |
| SERIAL | 1 RC2 · 2 RC1 · 3 RC1 · 4 RC1 · 5 RC1/RC10 · 6 RC4 · 7 RC2 · 8 RC2 · 9 RC2 · 10 RC2 (firmware, D-7) · 11 RC2 · 12 RC4 · 13 RC2 (D-7) · 14 RC1 · 15 RC1 · 16 RC8 · 17 RC2 (LOCAL-OK) · 18 RC1 (D-11) · 19 doc · 20 RC2 |
| ERRORS | 1 RC8/RC7 · 2 RC8/RC10 · 3 RC8 · 4 RC8 · 5 RC8 · 6 RC2 · 7 RC2/RC8/RC11 · 8 RC8 · 9 RC8 · 10 RC8 · 11 doc · 12 RC10 |
| GAMEPAD | 1 RC4 · 2 RC13 · 3 RC3 · 4 RC3 · 5 RC13/RC7 · 6 RC7 · 7 RC4 · 8 RC4 (D-4) · 9 RC1 · 10 RC13 · 11 RC12 · 12 RC12 · 13 RC12 · 14 RC12 · 15 RC13/RC11 · 16 RC4 · 17 LOCAL-OK · 18 RC13/RC9 · 19 RC13 · 20 doc · 21 RC13 |
| STEPPER | 1 RC1 · 2 RC4 · 3 RC1 · 4 RC2 · 5 RC3 · 6 RC3 · 7 RC5/RC3 · 8 RC5 · 9 RC2 (+LOCAL-OK G-code parse) · 10 RC7 · 11 RC6/RC7/RC3 · 12 RC7 (D-11) · 13 RC9 · 14 RC4 · 15 RC4 |
| DC | 1 RC3 · 2 RC1 · 3 RC1 · 4 RC6 · 5 RC4 · 6 RC7 · 7 RC6 · 8 RC6 · 9 RC1 · 10 RC3 (D-2) · 11 RC7/RC3 · 12 RC9 · 13 RC2/RC7 · 14 RC7 (D-11) · 15 RC6 · 16 RC4 · 17 RC4/RC3 · 18 RC5/RC2 · 19 RC7 |
| ROTATOR | 1 RC1/RC5 · 2 RC10 · 3 RC8/RC7 · 4 RC5 · 5 RC1 · 6 RC4 · 7 RC4/RC10 · 8 RC5 · 9 RC2/RC7 · 10 RC1 · 11 RC2 (LOCAL-OK) · 12 RC6 · 13 RC2/RC8 · 14 RC1 · 15 RC8 + doc |
| TEMP | 1 RC1/RC10 · 2 RC2 · 3 RC6 · 4 RC6 · 5 RC1 · 6 RC1 · 7 RC5 · 8 RC1 · 9 LOCAL-OK (feature decision) · 10 RC2/RC8 · 11 RC1/RC2 + doc · 12 RC8 · 13 RC6 |
| REDPERCENT | 1 RC11 · 2 RC11 · 3 RC11/RC5 · 4 RC11 · 5 RC11 · 6 RC7 (single save path) · 7 RC8/RC7 · 8 RC7 · 9 RC11 · 10 RC7 · 11 RC9/RC1 · 12 RC1 · 13 RC7 · 14 RC6 · 15 RC9 · 16 RC11 · 17 RC7 · 18 RC7 · 19 RC7 · 20 LOCAL-OK · 21 RC11 · 22 RC11 · 23 RC11 |
| PYSIDE | 1 RC1 · 2 RC1 · 3 RC9 · 4 RC11 · 5 RC6 · 6 RC6 · 7 RC7 · 8 RC7 · 9 RC4 · 10 RC8/RC4 · 11 LOCAL-OK · 12 RC7 · 13 RC1 · 14 RC4 (D-4) · 15 RC8 · 16 LOCAL-OK · 17 LOCAL-OK · 18 LOCAL-OK · 19 RC6 · 20 RC7/RC13 |
| VIEW-TKINTER | 1 RC1 (D-1) · 2 RC8 · 3 RC3 · 4 RC3 · 5 RC4 · 6 RC13 · 7 RC1 · 8 RC1 · 9 RC4 (D-4) · 10 RC13/RC7 · 11 RC4 · 12 RC4 · 13 RC3 · 14 RC11/RC7 · 15 RC11 · 16 RC1 · 17 RC7/RC9 · 18 LOCAL-OK |
| WEB | 1 RC1/RC10 · 2 RC4 · 3 RC1/RC10 · 4 RC9 · 5 RC10 · 6 RC7 · 7 RC7 · 8 RC4/RC10 · 9 RC8 · 10 RC10 · 11 RC7 · 12 RC8 · 13 RC11 · 14 RC10 · 15 RC9 · 16 LOCAL-OK · 17 RC10 · 18 RC5/RC8 · 19 RC10 (D-8) · 20 RC1 · 21 RC10 · 22 RC10 |

**`known-issues.md` open items → root cause:**

| Open item | RC |
|---|---|
| Swallowed exceptions in probing | RC-8 / RC-2 |
| Serial-port leak on dock close | RC-1 (see correction 2) |
| Toggle desync after controller swap | RC-3 (+ GAMEPAD-4 trace) |
| Red Percent strategy divergence | RC-7 (D-6) |
| SelectionOverlay broken on Linux | RC-7 `region_select` |
| Temperature emergency stop | confirmed correct, no action |
| Raw serial bypass | RC-2 |
| Coupled base class (`slow_speed` in BaseProbe) | RC-6 (per-class param table) |
| Dead state (Red Percent) | RC-11 / LOCAL-OK |
| Wasted packet sends | RC-3 |
| G-code race | RC-5 |
| Redundant `js.init` | RC-9 |
| Dead code (`parse_controller_id`, `toupcam`) | LOCAL-OK |
| PySide save-log bypass | RC-7 |
| Polling divergence | RC-4 |
| Red Percent race | RC-11 |
| Stale `available_probes` | RC-9 |
| `file_picker` dead | RC-7 |
| e/d ACK | RC-2 (D-7) |
| Silent transitions audit | RC-8 |
| Duplicate popup managers | RC-8 |

---

## Method and coverage

- Read in full: all 12 `audit/*.md` files, `README.md`, `known-issues.md`,
  `ownership-and-lifecycle.md`.
- Re-verified against source this pass:
  - `src/model/base.py`, `system_manager.py`, `error_routing.py` (full).
  - `probes.py:1-260`.
  - Tk `view.py:330-372`, PySide `view.py:230-262`, `web_adapter.py`
    `_schema_attrs` and `set_device_attribute`.
  - `git log` and `git show 046533f`.
- The audits carry their own per-finding confidence (verified or
  hypothesis). Hypothesis-grade findings stay hypothesis-grade here. The
  root-cause grouping does not upgrade their confidence.
- Not re-verified line by line: the remaining audit citations. Each audit
  states it re-read its cited lines in its own run. Where a claim here
  depends on one, the audit ID is given so the implementer re-checks it
  before acting.
