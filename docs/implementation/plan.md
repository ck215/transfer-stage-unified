# Staged Repair Plan

**Branch:** `mvc-refactor` · **Created:** 2026-09-19 · **Status doc:**
[progress.md](progress.md)

This is the route. [progress.md](progress.md) is where the trail is marked.
Read that one first if you are resuming work.

## What this plan is for

`docs/architecture/audit/` holds 213 symptom-level findings.
`docs/architecture/root-causes.md` groups them under 13 root causes and
states, per cause, the intended contract and the corrective design. This
document turns that into an ordered sequence of stages, each of which ends
in a commit and a push, so that:

- work survives a cleared agent context — any stage can be picked up cold
  from `progress.md` plus this file plus the relevant root-cause section;
- every one of the 213 findings is accounted for, either closed by a root
  cause or closed explicitly, and the ledger proves which;
- the repository history is the audit trail, not a chat log.

### The two closure modes

**By root cause (the default).** Most findings are symptoms. They close
because the structure that produced them changed, and the proof is the root
cause's invariant tests, not a per-finding check. Do not patch these
individually; `root-causes.md` lists the anti-fixes that entrench them.

**Explicitly (when necessary).** Some findings have no shared cause: dead
code, cosmetic divergence, a wrong argparse default, a hardware mapping only
a human can verify. These are closed one at a time, each named in its commit
message. They live in **S15** (`LOCAL-OK` sweep) and **S16** (owner
verification), except where an earlier stage deletes the code anyway.

A finding is closed only when a test covers it, or, for explicit closures,
when the commit names it and says how it was verified. "Looks right in the
UI" is not closure.

## Standing rules for every stage

1. **Stay in scope.** A stage names its files and its root causes. Work
   outside that belongs to a later stage; note it in `progress.md` and move
   on. Scope creep is what produced this backlog.
2. **Tests before implementation.** Each stage lists invariants from
   `root-causes.md`. Write them as failing tests first. Several are grep
   tests over the source tree, which makes regression cheap to detect.
   Which tests to run, and the per-stage gate commands, are in
   **[testing.md](testing.md)**. Work against the fast gate (~28 s); run the
   full three-pass sweep at stage boundaries, not in between.
3. **Never mark a finding closed without evidence.** The ledger's Status
   column moves to `closed` only with a test name or a verification note.
4. **Hardware and design calls stay with the owner.** RC-12 mappings,
   firmware reflashing, and any open `D-n` decision are never decided by an
   agent. If a stage is blocked on one, mark it `BLOCKED` and stop.
5. **Safety first within a stage.** If a stage touches a path that can
   energize coils or move an axis, the stop path is implemented and tested
   before the feature path.
6. **Commit and push at every step** (see below). A stage that is half done
   and pushed is recoverable; a stage that is done and unpushed is not.

## Commit and push protocol

This is what leaves the trail. It is not optional, and it is what makes
clearing the agent's context safe.

**Every commit must:**
- touch exactly one stage;
- update `progress.md` in the *same* commit — stage status, session log
  entry, and any ledger rows that moved to `closed`;
- be pushed immediately: `git push origin mvc-refactor`.

**Message convention:**

```
S<n>(<RC-id or "explicit">): <imperative summary>

<what changed structurally, and why this is the root fix rather than a patch>

Closes: <finding IDs>, verified by <test names>
Stage: S<n> <in progress | complete>
```

Example:

```
S2(RC-1): make SystemManager the only lifecycle authority

register()/release()/reconfigure() replace the passive dict. release() now
tears down, which remove_model never did. Views no longer mutate
active_models; the PySide hasattr ladder and its del are deleted.

Closes: MANAGER-7, MANAGER-9, PYSIDE-2, SERIAL-2
Stage: S2 in progress
```

**At stage completion**, the final commit sets the stage row to `done` with
its date, flips its ledger rows to `closed`, and says `Stage: S<n> complete`.

**Recording the SHA: never amend to insert it.** A commit cannot contain its
own hash, and amending to add it orphans the hash you just wrote — the
unreachable-`addb0b8` defect this repair already had to correct once, and
which S0 reproduced before catching it. Write the stage row with the SHA left
blank, then fill it in the *next* commit. Verify any SHA a document cites
with `git merge-base --is-ancestor <sha> HEAD`; if that fails, the citation
is unreachable and therefore useless.

## Resuming with a fresh context

The full procedure is at the top of [progress.md](progress.md). In short:
read `progress.md`, open the first stage that is not `done`, read that
stage's section here and the root-cause section it names, run the test
command, and continue. Do not re-derive the analysis — it is already
written down.

---

# Stages

Dependencies are real, not stylistic. The ordering constraint that is easy
to miss: **D-1 (close means hide) requires the control loops to have moved
into the models first**, because a hidden device keeps running. So S5
precedes S6, and no hide/show semantics ship before it.

| Stage | Title | Root causes | Findings | Gate |
|---|---|---|---|---|
| S0 | Baseline, plan, invariant harness | — | 3 | — |
| S1 | Purge legacy paths (D-9, D-11) | — | 6 | D-9, D-11 answered |
| S2 | Lifecycle authority | RC-1 | 41 | S0 |
| S3 | Transport truth and E-stop latch | RC-2 (item 2), RC-5 (item 1) | 18 | S2 |
| S4 | Web AppContext and security boundary | RC-10 (items 1–2) | 3 | S2 |
| S5 | Input service and model-owned loops | RC-13, RC-4 | 33 | S3 |
| S6 | Hide/show semantics (D-1) | RC-1 (completion) | 0 | **S5** |
| S7 | Probe mode state machine | RC-3 | 9 | S3, S5 |
| S8 | Motion serialization and ConnectionState | RC-5 (2–3), RC-2 (item 1) | 8 | S7 |
| S9 | Typed parameters | RC-6 | 13 | S2 |
| S10 | Schema v2 and three renderers | RC-7 | 20 | S9 |
| S11 | Result channel and event bus | RC-8 | 19 | S10 |
| S12 | Composition root and registry events | RC-9 | 9 | S2, S5 |
| S13 | MonitoringRun | RC-11 | 11 | S11 |
| S14 | Remaining web work | RC-10 (3–5) | 7 | S11 |
| S15 | Explicit `LOCAL-OK` sweep | — | 9 | any time |
| S16 | Owner verification and firmware v2 | RC-12, RC-2 (item 3) | 4 | owner |

Findings total 213. S6 closes no audit finding of its own — it implements a
decision — but it is where D-1 actually lands.

---

## S0 — Baseline, plan, invariant harness

**Goal.** A committed, corrected documentation baseline and a test harness
that can prove the structural invariants, before any behavior changes.

**Done:** `audit/` and `root-causes.md` committed (`be89d1b`); Wave 0 doc
corrections applied; D-1, D-9, D-11 recorded; this plan and the ledger.

**Remaining:** the invariant harness, `tests/architecture/test_invariants.py`.
It holds the grep-style structural tests, each named for its invariant, each
currently **expected to fail** and marked `xfail` with the stage that will
fix it:

| Invariant | Test | Fixed in |
|---|---|---|
| I-1.5 | `active_models[` appears only in `system_manager.py` | S2 |
| I-2.3 | `.ser.` appears only in `controller/serial.py` | S3 |
| I-4.1 | no `read_position`/`poll_status`/`send_manual_mode_command`/`start_polling`/`get_mapped_state` under `src/views/` | S5 |
| I-7.1 | no device or command name literal under `src/views/**` | S10 |

These four are cheap, brutal, and exactly track the redundancy this repair
exists to remove. As each stage lands, its `xfail` is removed.

Each invariant also carries a non-`xfail`ed `_no_new_violations` guard
pinning its per-file baseline, so the harness has teeth before S2 lands, and
a vacuity guard runs first so a wrong path cannot turn the file green.

**Exit.** Harness committed and running; every test either passes or
`xfail`s with a named stage. **Done** — 5 passed, 4 xfailed.

---

## S1 — Purge legacy paths (D-9, D-11)

**Goal.** Delete two systems the owner has decided against, before building
anything on top of them.

**Scope.**
- **D-9:** `src/app.py` argparse fallback defaults to Tkinter on macOS.
  Docs have claimed this since `ada4dbf`; only `run.sh` does it.
- **D-11:** runtime serial reconnect is purged. `serial_port` becomes
  readonly in every schema; `reconnect_serial` and its dead PySide branch
  are deleted; the rotator's schema "Reconnect" button goes with them.

**Closes explicitly.** MANAGER-14 (D-9); SERIAL-14, SERIAL-18, STEPPER-12,
DC-14, PYSIDE-13 (D-11).

**Why first.** Both are deletions. They shrink the surface every later stage
has to reason about, and neither depends on the refactor.

**Exit.** `grep -rn reconnect_serial src/` is empty; launching with no flag
on macOS gives Tkinter; no schema exposes a writable `serial_port`.

---

## S2 — Lifecycle authority (RC-1)

**Goal.** One object owns model lifetime. Views stop constructing and
destroying.

**Scope.** `src/model/system_manager.py`, `src/model/base.py`,
`src/app_bootstrap.py`, the three views' close paths, `src/app.py`.

1. `register(name, model, config)` — enforces `isinstance(model,
   ManagedModel)`, keeps the `DeviceConfig`, raises on duplicate names.
2. `release(name)` — remove **and** `teardown()`. Note under D-1 this is
   reached only by shutdown and reconfigure, never by a tab close.
3. `reconfigure(configs)` — `shutdown_all()` first, then build with
   rollback.
4. `shutdown_all()` — `emergency_stop` before `teardown`, per model.
5. Delete `reboot_model` (and its `sleep(1)` on the caller thread) and the
   `hasattr` fallback in `_teardown_model`.
6. `teardown()` per model becomes safety-first and exception-safe:
   hardware stop → background activity → transport close, each in its own
   `try/finally`. `RotatorSystem.teardown` sends ST first.
7. One process-exit hook in `app.py` for all three launchers: `atexit`,
   SIGINT/SIGTERM/SIGHUP, Qt `aboutToQuit`, Tk `::tk::mac::Quit`. There are
   currently **zero** such hooks in `src`.
8. `build_models` registers as it goes and rolls back on failure; launchers
   hide the setup window only on success.
9. Views never touch `active_models`. Delete PySide's `hasattr` ladder,
   its `del self.system_manager.active_models[...]`, and the
   `StepperProbe(None, "None", {})` constructors.

**Interim.** Until S6 delivers hide/show, **remove the close affordance**
from the PySide dock and the Tk tab rather than wiring them to `release()`.
Closing must not mean destroy under D-1, and a hidden device cannot be left
running while views still own its loops. Mark with
`# INTERIM: see plan.md S6`.

**Invariants.** I-1.1 through I-1.5 (root-causes.md RC-1). I-1.2 needs fault
injection on each teardown sub-step; I-1.5 is the grep test from S0.

**Risk.** This is the largest stage (41 findings). Split it into commits per
numbered item above; each one pushes.

---

## S3 — Transport truth and E-stop latch (RC-2 item 2, RC-5 item 1)

**Goal.** A write that failed can never be recorded as a success, and FULL
STOP latches.

**Scope.** `src/controller/serial.py`, `src/model/probes.py`,
`src/model/temperature_system.py`.

1. `SerialTransport.write_command(bytes)` raises `TransportError`. Every
   write goes through it, under the lock — including `k`, G-code and
   temperature frames. There are currently **11** direct `.ser.write` call
   sites outside the transport.
2. `enable()`/`disable()` raise on failure. The model sets `system_enabled`
   only on success; otherwise it enters `FAULT("disable not confirmed —
   coils may be energized")`, which persists.
3. `_estop` latch (`threading.Event`) per model, set by `emergency_stop()`
   before any I/O, checked immediately before each transport write, cleared
   only by an explicit user command.

**Invariants.** I-2.1, I-2.3 (grep, from S0), I-5.1, I-5.2 (`emergency_stop`
returns in < 100 ms against a stalled fake transport).

**Note.** Full ACK confirmation needs firmware v2 (D-7, S16). Until then
"confirmed" means a successful write, and the UI must say so honestly
rather than claiming more.

---

## S4 — Web AppContext and security boundary (RC-10 items 1–2)

**Goal.** Close two live holes. Small, independent of the behavioral work.

**Scope.** `src/views/web/`.

1. One `AppContext` owns the manager. Window, server and adapter hold the
   context, never a manager — today **five** objects hold one and only the
   adapter's is live. `reconfigure` is the sole mutator, single-flight
   (409 while running).
2. Require `Content-Type: application/json`; validate `Origin` and `Host`
   against the bound address; require a per-launch token injected into the
   served HTML. Apply to every POST **and** `/api/screenshot`, which
   currently serves the operator's screen to any cross-site caller.

**Closes.** MANAGER-1, MANAGER-4, MANAGER-15, WEB-1, WEB-3, WEB-10,
WEB-20, ERRORS-12.

**Why early.** A cross-site "simple" POST can drive the stage today. This
does not depend on S5 or later.

---

## S5 — Input service and model-owned loops (RC-13, RC-4)

**Goal.** Remove the largest redundancy: control and I/O loops owned by
views. This is the stage that makes Web reach parity and makes D-1
possible.

**Scope.** `src/controller/gamepad.py`, `src/model/probes.py`,
`src/model/rotator_system.py`, all three views.

1. **`InputService`** — one module-level owner of `pygame.init()` (once),
   `pygame.quit()` (only at process exit, via the S2 hook), enumeration,
   the claims registry with owner ids, and a lock around all SDL calls.
   Pollers acquire and release device handles through it. This deletes the
   bind-counting refcount that currently kills a live poller's joystick,
   and with it the need for `_ensure_pygame_video()` — *do not add more
   call sites for that helper; it is an anti-fix.*
2. **`ControllerPoller`** owns a daemon thread or an injected `Scheduler`;
   `poll_once()`, `read_levels()` (non-consuming), `drain_edges()` (single
   consumer). Edges latch at poll time, so taps shorter than a tick survive.
3. **Input pump moves into the model.** `BaseProbe._input_loop` runs while
   mode is MANUAL, at one documented rate, exception-isolated to `FAULT`.
   Delete Tk `_route_input` (50 ms) and PySide `input_timer` (20 ms).
   Web gets manual mode for free — today it has none, which is why Web
   manual mode energizes coils and does nothing else.
4. **Hardware sampling on a model-owned thread per model**, into
   lock-guarded cached fields with `last_sample_time`. Views and
   `/api/state` only read the cache. This removes PySide's extra 50 ms
   poll from `_poll_model` (~3× the hardware traffic).
5. **Serial construction and verification move off the UI thread**; split
   `serial.__init__` (open) from `verify()`.
6. **Views keep one render tick each**, exception-isolated, no I/O.

**Invariants.** I-4.1 (grep, from S0), I-4.2 (manual mode identical under a
headless harness — this is the Web parity proof), I-4.3 (a 1 s transport
stall blocks neither the render tick nor FULL STOP).

**Risk.** Highest-coupling stage. Land RC-13 first and push, then RC-4.

---

## S6 — Hide/show semantics (D-1)

**Goal.** Implement the owner's decision that closing a tab or dock hides.

**Prerequisite: S5.** A hidden device keeps its port, its controller binding
and its loops. If views still owned those loops, hiding would either strand
a live loop on a hidden widget or silently stop polling an energized device.

**Scope.**
1. `SystemManager.show(name)` / `hide(name)`. Hide performs a safe stop of
   motion (per **D-2**, still open) and leaves the transport open.
2. Restore the close affordance removed in S2, wired to `hide`.
3. **Tk needs a real re-add path.** Its `notebook.forget()` has no reopen
   and `on_close_tab_callback` is never assigned, so Tk today is a one-way
   hide that leaks. Either give it reopen or leave the affordance off.
4. Reopen shows the existing model. It never constructs one.

**Blocked on.** D-2 (does leaving a mode disable the coils, or only stop
motion?) governs what "safe stop on hide" does. If D-2 is unanswered when
this stage is reached, stop and ask.

---

## S7 — Probe mode state machine (RC-3)

**Goal.** One mode at a time, with defined side effects.

**Scope.** `src/model/probes.py`.

1. `ProbeMode` enum plus one `_transition(target, reason)` owning the
   hardware side effects in one ordered place. `auton_flag`, `manual_flag`
   and `system_enabled` become **read-only derived properties** — they stay
   renderable but stop being four independently writable booleans set by
   different threads and views.
2. Autonomous "stepping" becomes a timed sub-state that ends on computed
   duration or arrival and calls `touch_activity()`. The watchdog measures
   real inactivity in every energized mode and stops deferring on flags.
   **D-3 is answered: manual mode does idle-time-out** on real input
   inactivity.
3. Every exit from MANUAL goes through `_transition` with a stop packet.
4. `set_controller` returns the bind result; the poller is the source of
   truth and `controller_var` mirrors it.
5. Watchdog restarts with a fresh event per generation.

**Blocked on.** D-2, as in S6.

**Invariants.** I-3.1 through I-3.4. I-3.4 ("no schema or API write can
change the mode") needs S10's `writable` flag to be fully enforceable; until
then enforce it in the setter.

---

## S8 — Motion serialization and ConnectionState (RC-5 items 2–3, RC-2 item 1)

1. One command worker per model; a queue with latest-wins or
   reject-while-busy; a `_run_id` generation token. The rotator validates
   its ±30° tubing check against the *commanded* target, inside the worker.
2. `full_stop_all` fans out in parallel with a bounded join and returns
   per-model results. ST and `d` take a priority path
   (`lock.acquire(timeout)` then raw-write fallback) so a poll holding the
   lock cannot delay a stop.
3. `ConnectionState` (`SIMULATED | CONNECTING | VERIFIED | UNVERIFIED |
   LOST | CLOSED`) as a schema readonly field shared by all views and the
   web badge. First transport exception → `LOST`, close the handle, report
   once on the transition, drop to `FAULT`.
4. SIM becomes an explicit simulated transport that ACKs, not `ser=None`.

**Invariants.** I-5.1, I-5.3, I-2.2.

---

## S9 — Typed parameters (RC-6)

1. Per-class `Param(name, type, min, max, decimals, unit, default)` table.
   Values stored as numbers. Setters validate and raise. Remove the lazy
   `_num(…, 400)` class-agnostic fallbacks — a DC probe currently falls
   back to the stepper's 400/16 instead of its own 120/1.
2. Type and bounds go in the schema, so views stop inferring numeric-ness
   by trying `float()` with a duplicated `attr != "serial_port"` exception.
3. **D-5 is answered: (a).** Commands take their inputs explicitly; the
   schema declares `"inputs": [...]` per button and the view sends current
   widget values with the command, validated atomically. This kills the
   stale-value class in all three views at once. *Do not copy Tk's
   `focus_set()` flush into PySide or JS — named anti-fix.*
4. Temperature frames format from typed values and refuse to send if any
   field is invalid (today an empty field shifts the firmware's `strtok`
   fields and sends the wrong target with wrong gains).

**Invariants.** I-6.1 (per view, plus the API), I-6.2, I-6.3.

---

## S10 — Schema v2 and three renderers (RC-7)

1. Element attributes: `writable` (default false for readonly and toggle),
   `param` reference, `enabled_when`/`disabled_when` by mode name,
   `command` required on interactive dropdowns, `format`, `role` instead of
   raw colors.
2. Composite element types with one contract and three renderers:
   `region_select`, `file_save`, `plot`, `log_stream`.
3. `confirm` contract: a command may return `NeedsConfirmation(prompt)`;
   each view implements one generic dialog. This replaces the view-injected
   `confirm_rotation_callback` and makes Web enforce the ±30° check.
4. **D-6 is answered: Red Percent becomes schema-driven in all three
   views.** Tk's hand-built `RedPercentView` becomes a renderer of v2
   composites; PySide's bolt-on duplicates are deleted.
5. Schema conformance test: every command, `options_command` and `param`
   named in any schema exists on the model.

**Invariants.** I-7.1 (grep, from S0), I-7.2 (golden structure per
renderer), I-7.3.

---

## S11 — Result channel and event bus (RC-8)

1. `CommandResult(ok | refused(reason) | failed(exc) | needs_confirmation)`.
   Refusal is never rendered as success — today commands return `None` on
   refusal and views report "executed".
2. Separate state from events: persistent conditions become badges and
   banners; only transitions produce events, which removes the need for
   text-keyed dedup.
3. `ErrorRouter` becomes a thread-safe bus with severity-keyed rate limits
   and a monotonic event id. Tk and Qt subscribe with a non-modal log panel
   plus a modal only for `error` with `requires_ack`; Web polls `?since=<id>`
   instead of destructively popping. The two duplicate popup managers
   collapse into subscribers.
4. One `install_exception_hooks(bus)` covering `sys.excepthook`,
   `threading.excepthook` and Tk `report_callback_exception`, called by
   every launcher, bound to the process-lifetime root.

**Invariants.** I-8.1, I-8.2, I-8.3.

---

## S12 — Composition root and registry events (RC-9)

1. `app_bootstrap` becomes the single composition root: `discover_ports`,
   `discover_controllers` (in-process, via `sys.executable` — not the Web
   subprocess that fabricates "Virtual Controller" entries),
   `normalize_config`, `validate_assignment`, `build_models`, `link_models`.
   All three launchers call the same functions.
2. `SystemManager` emits `registered`/`released`. `RedPercentSystem`
   subscribes and maintains `available_probes` itself. This deletes the
   Red Percent linking block currently copy-pasted at **five** sites.
3. Disabled devices are not constructed; delete `_disabled_in_setup`.

**Invariants.** I-9.1, I-9.2 (the three launchers produce identical managers
for the same config).

---

## S13 — MonitoringRun (RC-11)

1. `MonitoringRun` created by `start_monitoring()`, snapshotting its
   configuration and owning its thread, stop event, generation and data log.
   `start` refuses without a focus area or dependencies and returns a
   `CommandResult`.
2. Sync-dimension toggles are `disabled_when: monitoring`. Today toggling
   one mid-run raises `KeyError` in the thread and kills it while
   `monitoring` stays True.
3. `pending_run_data()` as a model query; `release` and `shutdown_all`
   consult a `confirm_discard` hook. **D-10 is answered: autosave to a
   timestamped file, plus a prompt where the UI allows.**
4. Velocity derives from position deltas and timestamps, with a timestamp
   column. Invalid samples are flagged, never written as 0.0.

---

## S14 — Remaining web work (RC-10 items 3–5)

`/api/state` reads the S5 caches without locks; commands go through the S8
worker; FULL STOP stays lock-free. Errors use `since=<id>`. Per-device
staleness markers. Every route wrapped in a JSON error envelope. Handler
exceptions stop dropping connections; toasts stop using `innerHTML`.
Heavy work leaves the request threads; fetches get timeouts.

**Blocked on.** D-8 (should an energized system with no polling client warn,
or FULL STOP?).

---

## S15 — Explicit `LOCAL-OK` sweep

Closed individually, each named in its commit. These have no shared cause:
dead code (`parse_controller_id`, `src/lib/toupcam.py`, PySide's
unreachable `file_picker` branch), cosmetic layout, launcher argparse
details, isolated parse fixes.

Can run any time; best used to fill gaps while blocked on an owner
decision.

---

## S16 — Owner verification and firmware v2

**Never delegated. The owner does this at the bench.**

1. **RC-12:** verify each (controller, platform) pair on the physical stage
   against `main` — the substring whitelist sends foreign pads through Xbox
   layouts, the T16000M Z axis and bumpers differ, the D-pad sign was
   flipped against a mock rather than the stage, and the deadzone is applied
   twice. The result is encoded as **one** data table
   (`name, platform → axis map, sign, deadzone`) with an explicit allowlist
   that rejects unknown pads. Tests assert the table, never behavior guessed
   from a mock.
2. **RC-2 item 3 / D-7:** firmware protocol v2 — versioned identity reply,
   ACKs for `e`/`d`/stop, `e` and `d` handlers on the DC board, implement or
   delete `k` (no firmware handles it today), host-liveness timeout on the
   temperature board. `serial.__init__` takes `expected_type` and rejects a
   mismatched board. **Requires reflashing every board.**

---

*Plan created 2026-09-19 against commit `be89d1b`. Amend it when reality
disagrees — but record the amendment in `progress.md` with a reason, rather
than quietly working to a different plan.*
