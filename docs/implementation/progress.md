# Progress

**The trail.** This file is the single source of truth for what is done,
what is next, and what is blocked. Update it in the *same commit* as the
work it describes, then push. If this file and your memory disagree, this
file wins.

Route: [plan.md](plan.md) · Tests: [testing.md](testing.md) · Bench:
[bench-checklist.md](bench-checklist.md) · Analysis:
[../architecture/root-causes.md](../architecture/root-causes.md) ·
Evidence: [../architecture/audit/](../architecture/audit/)

---

## Resuming cold — read this first

You are probably a fresh agent with no context. Do this, in order:

1. **Read this file's Stage status table.** Find the first stage that is not
   `done`. That is your stage. If it says `BLOCKED`, read why and stop —
   report the blocker to the owner rather than working around it.
2. **Read that stage's section in [plan.md](plan.md).** It names its scope,
   its files, its invariants and its exit criteria. Do not exceed them.
3. **Read the root-cause section it names** in
   `../architecture/root-causes.md` — the intended contract, the actual
   structure, and the corrective design are already written. Do not
   re-derive the analysis; it cost a full audit pass.
4. **Check the anti-fix table** in that same document before writing code.
   Several tempting patches entrench the cause they appear to fix.
5. **Run the fast gate** — about 71 seconds, 715 passing and 1 xfailed at
   `5871eea`:
   `python3 -m pytest tests/ -m "not slow and not order_dependent and not qt"`.
   It grew from 28 s when S10 un-excluded `tests/ui`, and again across the
   2026-09-20 fix wave. Your stage's targeted
   gate is in [testing.md](testing.md). The **three-pass full sweep is
   retired** (owner instruction, 2026-09-20) — the fast gate plus
   `-m "qt"` is the contract now. Do not run the whole suite in a working
   loop. Read pytest's exit code **unpiped**: `... > log 2>&1; echo $?`
   reports pytest, `... | tail` reports `tail`.
6. **Work, commit, push** per the protocol in plan.md. Every commit updates
   this file.
7. **Before stopping,** write a session-log entry below saying what you did,
   what you learned, and what the next action is. Be specific enough that
   someone with no context can start from it.

**Never** mark a finding `closed` without a test name or a verification
note. **Never** answer an owner decision (`D-n`) yourself.

---

## Stage status

`todo` · `in progress` · `BLOCKED` · `done`

| Stage | Title | Status | Commit | Date | Notes |
|---|---|---|---|---|---|
| S0 | Baseline, plan, invariant harness | done | `e760615` | 2026-09-19 | Docs baseline, plan, ledger, test division, invariant harness. |
| S1 | Purge legacy paths (D-9, D-11) | done | `86027f5` | 2026-09-19 | D-9 + D-11 purged. SERIAL-18's `reboot_model` half reassigned to S2. |
| S2 | Lifecycle authority (RC-1) | done | `a7ed3f5` | 2026-09-19 | All 9 items. I-1.5 now holds. 7 tab-close findings deferred to S6 by D-1. |
| S3 | Transport truth and E-stop latch | done | `f69586c` | 2026-09-19 | I-2.3 holds. I-5.2 xfailed to S8 (emergency_stop still blocks on a stalled transport). |
| S4 | Web AppContext and security boundary | done | `2854d3e` | 2026-09-19 | CSRF hole and /api/screenshot closed. Manager read-through + single-flight. |
| S5 | Input service and model-owned loops | done | `9a70834` | 2026-09-20 | RC-13 + RC-4. Deferred D-4 input gate landed 2026-09-20. |
| S6 | Hide/show semantics (D-1) | done | `e9fc26f` | 2026-09-20 | All 4 items. Tk got a real re-add path. order_dependent 3 -> 2. |
| S7 | Probe mode state machine (RC-3) | done | `8fc9f00` | 2026-09-20 | All 5 items. `ProbeMode` replaces 4 booleans; I-3.1–I-3.4 hold. known_bad 7 -> 2. |
| S8 | Motion serialization, ConnectionState | done | `728f714` | 2026-09-20 | All 4 items. known_bad down 10 -> 7. The S8 `emergency_stop` contract — latch first, never block the caller — was implemented for the probes only; the rotator and the heater ran their stop I/O on the calling thread and the rotator had no latch at all. Residue closed 2026-09-20: ROTATOR-8, ROTATOR-4, TEMP-7 fixed, DC-18 found already-fixed and pinned. **I-5.2 now holds for all three subsystems.** |
| S9 | Typed parameters (RC-6) | done | `79d2d97` | 2026-09-20 | All 4 items. `Param` table + D-5 `apply_inputs`. Landed with S10. |
| S10 | Schema v2, three renderers (RC-7) | done | `7cc5f3c` | 2026-09-20 | All 5 items. Qt pass resolved (it hung, it did not abort). `tests/ui` un-excluded: +83 tests in the fast gate. I-7.2 built; known_bad now empty. |
| S11 | Result channel and event bus (RC-8) | done | `409c859` | 2026-09-20 | All 4 items. `CommandResult` + `EventBus`; one `install_exception_hooks`. I-8.1–I-8.3 hold. conftest.py was duplicated end to end; half of it was dead. |
| S12 | Composition root, registry events (RC-9) | done | `d35036b` | 2026-09-20 | All 3 items. `app_bootstrap` is the composition root; `SystemManager` emits `registered`/`released`; `_disabled_in_setup` deleted. I-9.1–I-9.3 built. **I-7.1 does not retire here** — 2 of its 6 hits are S13's. |
| S13 | MonitoringRun (RC-11) | done | | 2026-09-20 | All 7 items. Items 5-7 landed first, out of plan order, for a bench run (`0e5ba73`); items 1-4 — `MonitoringRun` itself — landed in the `s13-monitoring-run` worktree and took over their snapshot rather than opening a second one. Item 3's `confirm_discard` seam exists model-side; the PySide dock-close prompt (PYSIDE-4) that consumes it is still open. |
| S14 | Remaining web work | done | `a4f3a75` | 2026-09-20 | 7 of 8 closed in the `s14-web` worktree (WEB-5, 14, 15 residue, 16, 18, 21 and REDPERCENT-20's web half). WEB-22 is partly closed — no DOM harness for the staleness/refresh half. **WEB-19 deferred**: it needs the D-8 client-liveness watchdog in `probes.py`, so it spans model and web and cannot sit in a web-only write set. |
| S15 | Explicit `LOCAL-OK` sweep | done | `a4f3a75` | 2026-09-20 | 4 of 5 closed in the `s15-local-ok` worktree (PYSIDE-16, 17, 18 and REDPERCENT-20's model half). GAMEPAD-17 is partly closed — three sub-items blocked by write-set boundaries, not difficulty. Two of the qt-marked tests it could not run were **wrong** and were fixed on merge; see the session log. |
| S16 | Owner verification, firmware v2 | todo | | | **Owner only.** Never delegate. Procedure and record sheet: [bench-checklist.md](bench-checklist.md). |

## Owner decisions

| ID | Question | Answer | Date |
|---|---|---|---|
| D-1 | Close = hide or destroy? | **hide** — model, connection and config persist | 2026-09-19 |
| D-2 | Mode off = stop only, or disable coils? | **disable coils** — current behavior stands; DC-10's divergence from main is intentional | 2026-09-20 |
| D-3 | Manual mode idle-timeout? | **yes**, on real input inactivity | 2026-09-19 |
| D-4 | Window focus loss behavior? | **gate input, never stop**; child-dialog deactivation is not focus loss | 2026-09-20 |
| D-5 | Commit contract | **(a)** commands carry their inputs | 2026-09-19 |
| D-6 | Red Percent rendering | **schema-driven** in all three views | 2026-09-19 |
| D-7 | Firmware protocol v2 | **adopt** — as recommended; requires reflashing every board | 2026-09-21 |
| D-8 | Web client liveness gate | **warn at N s, FULL STOP at M s while motion is active**; folded into the existing interlock watchdog. **N and M set at the bench** (2026-09-21) — the shipped 5 s / 15 s are provisional placeholders, not measured | 2026-09-20 |
| D-8a | Does D-8's client-liveness gate cover the heater and the rotator, or was it probes-only? | **heater and rotator too** — extend the gate to `TemperatureSystem` and `RotatorSystem`. **N and M are set separately for each at the bench**; the probe values do not transfer, because a heater's safe unattended window is a thermal question, not a motion one | 2026-09-21 |
| D-9 | macOS default view | **tkinter**, until the codebase is stabilized | 2026-09-19 |
| D-10 | Unsaved Red Percent data on exit | **autosave** to a timestamped file, plus a prompt where the UI allows | 2026-09-19 |
| D-11 | Runtime serial reconnect | **not supported** — purged as legacy | 2026-09-19 |
| D-12 | Gamepad poll rate: 200 Hz (code) or 50 Hz (comment)? | **200 Hz** — the code was right, the comment wrong. 20 ms manual pump ratified with it | 2026-09-20 |
| D-13 | Temperature history: dead code or a plot? | **plot it** — schema-driven in all three views per D-6. Owner accepted this is feature work on a repair branch | 2026-09-21 |

D-3, D-5, D-6 and D-10 carry the recommendations recorded in
`root-causes.md`; they were not separately re-confirmed by the owner and any
of them can be reopened before its stage begins.

**No owner decision is open.** D-8a was answered **heater and rotator too**
on 2026-09-21, after a fresh-eyes audit found the gate was implemented on
`BaseProbe` only: `RotatorSystem` and `TemperatureSystem` have no equivalent
and no idle watchdog of any kind, so `record_client_heartbeat`'s duck-typed
`getattr(model, "touch_client_liveness", None)` silently did nothing for both.
The concrete consequence was that a heater driven to setpoint from the Web
frontend kept heating indefinitely if the tab closed, the machine slept, or
the network dropped — with no firmware watchdog behind it either. The
mechanism is being built now; **its constants ship as explicitly-marked
placeholders**, exactly as WEB-19's did, and the rows do not close until the
values are measured.

D-7 was answered **adopt** on 2026-09-21,
which unblocks SERIAL-10's mis-parse half — it needs a wire terminator on a
stop path that only v2 carries. That work is still S16, at the bench, and
still requires reflashing every board before the next run.

**Two things remain the owner's without being decisions.** D-8's N and M are
*answered in kind but not in value*: the gate warns and then FULL STOPs, and
the shipped 5 s / 15 s are placeholders to be measured at the bench, not
ratified. **D-8a adds two more pairs** — one for the heater, one for the
rotator — on the same terms. And S16's GAMEPAD-11/12/13/14 are physical
verification.

**D-12 was raised during S5**, not by the audit, and was **answered on
2026-09-20**: `ControllerPoller.POLL_INTERVAL` stays at 5 ms (~200 Hz) and
the 50 Hz comment was the error. The poller therefore runs at 4x the 20 ms
manual pump deliberately, so that button edges shorter than one pump tick
are still caught; the comment now says so, and says not to "optimise" the
two into agreement.

---

## Session log

Newest last. One entry per working session, written *before* stopping.

### 2026-09-19 — audit synthesis, doc corrections, plan
- Committed `be89d1b`: `audit/` (213 findings) + `root-causes.md` (13 root
  causes) + Wave 0 doc corrections.
- A code review of the branch found that four of `root-causes.md`'s own
  numbered corrections were unapplied in files the same pass had edited.
  All are now applied.
- Resolved a conflict between the review and RC-4 on polling rates: both
  views' dedicated poll timers are 100 ms; PySide's extra ~3× traffic comes
  from `_poll_model` also polling at 50 ms (`pyside/view.py:402-405`). Both
  documents were partly wrong; the precise version is now in `views.md`.
- `known-issues.md` cited commit `addb0b8`, which exists but is orphaned
  (unreachable after a rebase or amend). Recited to `4ffb2e3`.
- Owner answered D-1 (hide), D-9 (tkinter on macOS), D-11 (purge reconnect).
- **Discovered dependency:** D-1 requires S5, because a hidden device keeps
  running and its loops must already belong to the model, not to a hidden
  widget. The stage order reflects this; S2 removes the close affordance in
  the interim.
- **Next action:** S0's remaining item — write
  `tests/architecture/test_invariants.py` with the four grep invariants,
  `xfail`ed with their fixing stage. Then S1 (pure deletion).

### 2026-09-19 (later) — test suite division
Policy and commands: [testing.md](testing.md).

- **The sweep was destroying its own results.** Two of three identical full
  runs aborted with a native SIGABRT from Qt (exit 134), which kills the
  pytest session and discards every already-passed result. Qt tests are now
  auto-marked by fixture (`qapp`/`qtbot`, exact rather than by filename) and
  run in their own 2-second pass, so an abort cannot take the suite with it.
  Three consecutive main-pass sweeps then completed cleanly.
- **Fast gate: ~28 s, down from ~3 min.** 160 s of the 180 s was real sleeps
  during model construction (`serial.py:63`'s 1.5 s bootloader wait,
  `probes.py:168`'s `sleep(1)`) — marked `slow` and excluded from the
  working loop. Those sleeps are SERIAL-6/RC-4; **delete this marking in S5**
  rather than maintaining it.
- **Concern markers** map to stages, so each stage gates on its own subset
  (`-m transport` is ~3 s). Applied from one table in `conftest.py`.
- **12 failures triaged**, none deleted:
  - 2 were harness gaps, fixed: the Tk mock lacked `focus_get`, which the
    `12e9d59` focus guard calls on every poll tick.
  - 6 are stale tests asserting behavior deliberately removed — five assume
    `enter_manual()` succeeds with no gamepad (`046533f` made it refuse),
    one asserts the duplicate checkbox row deleted as known-issues #5.
  - 4 are real product bugs (PYSIDE-7, RC-6, RC-8, RC-2). Quarantined as
    `known_bad` with `xfail(strict=True)` so they report XPASS-as-failure
    when their stage lands, forcing re-authoring.
- **7 order-dependent tests found**, which pass alone and fail in
  composition. The four `run_script` ones were found by running four
  identical sweeps and collecting failures — never the same pair twice.
  **Their cause is two audit findings, not test defects:** `run_script` has
  no run token (STEPPER-8, RC-5) so a previous test's thread is still
  executing, and `ErrorRouter`'s callbacks are process-global (RC-8). The
  suite's flakiness is a symptom of the architecture under repair. Expect
  this set to dissolve during S3/S8/S11 — do not paper over it with sleeps.

### 2026-09-19 (later still) — S0 invariant harness

`tests/architecture/test_invariants.py`. Gate: `-m "invariants"`, 0.2 s —
**5 passed, 4 xfailed.**

- The four grep invariants (I-1.5, I-2.3, I-4.1, I-7.1) are each
  `xfail(strict=True)` naming their fixing stage (S2, S3, S5, S10), so the
  stage that lands them gets an XPASS-as-failure and must delete the marker.
- **Each invariant also has a non-xfailed `_no_new_violations` guard.** As
  specified in plan.md the harness would have asserted nothing until S2
  landed; the guard pins the per-file violation count now, so the leak
  cannot spread to a fourth view while the earlier stages are in flight.
  Measured baselines:

  | Invariant | Baseline (violating lines per file) | Stage |
  |---|---|---|
  | I-1.5 `active_models[` | `app_bootstrap.py` 6, `pyside/view.py` 1 | S2 |
  | I-2.3 `.ser.` | `model/probes.py` 3, `model/temperature_system.py` 10 | S3 |
  | I-4.1 view loop calls | pyside 15, tk 13, web 4 | S5 |
  | I-7.1 device/command literals | pyside 19, tk 3, web 4 | S10 |

- **A vacuity guard runs first.** Every one of these tests passes when it
  finds nothing, so a wrong path would turn the file green — and under
  strict xfail that reads as four stages landing at once. `test_harness_is
  _not_vacuous` proves the source tree and the schema command set are
  non-empty before anything else is believed.
- **I-7.1 covers command names, not just device names.** The 23 `command`
  values are derived from the models' `ui_schema` at test time rather than
  hardcoded, so the invariant cannot drift as schemas change. Checked for
  false positives from generic words (`"stop"`, `"home"`): there are none —
  all 7 command hits are genuine `cmd_name ==` branches in the views.
- **The six device names had to be hardcoded**, because there is no single
  authority to import them from: `build_models()` dispatches on them in an
  if/elif chain, `app_bootstrap`'s `DEVICE_MAP` knows only four of the six,
  and `pyside/view.py:786-789` keeps its own copy. That duplication *is*
  RC-7; the test list is deliberately the fourth copy, the one that fails
  loudly when the others drift. S10 collapses all four.
- **Next action:** S1 — purge D-9 and D-11 legacy paths (pure deletion).

### 2026-09-19 — S1: purge D-9 and D-11 legacy paths

Gate `-m "bootstrap or schema"`: 39 passed, 2 xfailed, 25 s.
Gate `-m "invariants"`: 9 passed, 4 xfailed.
Full sweep green: main **247 passed / 12 xfailed** (2:32), Qt **8 passed /
2 xfailed** (2.2 s), all 7 order-dependent tests pass in isolation. Every
delta from `b37cc9a` accounted for: +9 new passing, +4 xfail, −1 deleted.

**D-11 — runtime serial reconnect, purged.** Deleted `BaseProbe.
reconnect_serial()`, the PySide dock-reopen branch that was its only caller
(a modal + `processEvents()` + a ~6 s blocking reconnect that closed the port
without sending a stop), the rotator's schema `Reconnect` button and its
`reconnect()` method, and the web JS `'Serial Reconnect'`/`'reconnect_serial'`
interlock special-cases. `serial_port` is now a `readonly` schema element, so
the `attr != "serial_port"` numeric-validation special case disappears from
both the Tk and PySide renderers.

**D-9 — Tkinter is the macOS default.** View selection is extracted into a
pure `select_view(requested, platform, pyside_available)` so the decision is
testable rather than buried in `main()`. `run.sh` hard-coded `--tkinter`,
which meant the documented default never applied to the wrapper and no other
view was reachable through it; it now passes `"$@"` through.

**MANAGER-14 also closed.** `parse_known_args` → `parse_args`: a typo like
`--pyside6` used to be silently dropped, and on a lab Mac the platform
default then started a hardware-capable web server instead of the view the
operator asked for. The unreachable `else: "Unknown view"` branch and
`launch_web()` (which ignored `--port`/`--no-browser`) are deleted.

**Three things worth carrying forward:**

- **SERIAL-18 was split, not closed.** Its D-11 half (no view offers a
  reconnect) is done. Its other half — `reboot_model` has no callers — is
  RC-1 work: S2 replaces it with `reconfigure()`, and its four lifecycle
  tests are coverage S2 needs. Deleting it here to close a row would have
  destroyed that coverage. The ledger now maps SERIAL-18 to **S2**.
- **`gen_ledger.py` would have erased every closure.** It emitted `open` for
  all 213 rows unconditionally, so the first regeneration after any stage
  landed would have wiped the trail the ledger exists to keep. It now reads
  statuses back, carries them forward, rewrites the table inside this file in
  place, and errors if a recorded closure would be dropped. Verified by
  regenerating: 213 mapped, 5 closures preserved.
- **testing.md's fast-gate figure was wrong.** It claimed 200 passed / 5
  xfailed; `b37cc9a` actually gives **194 passed / 3 xfailed**. Found by
  diffing collected node IDs against that commit instead of reconciling
  totals by arithmetic. Everything now reconciles exactly: 194+5 = 199
  passed, 3+4 = 7 xfailed, 197+9 = 206 selected. The file now says to diff
  node IDs rather than trust the written totals.

**Tests changed, none deleted to go green:** `test_rotator_reconnect` was
deleted along with the feature it covered (D-11); `test_dcprobe_mutating
_state_out_of_order` kept its subject and lost only its `reconnect_serial()`
line. Four new invariant tests carry S1's closure evidence.

- **Next action:** S2 — lifecycle authority (RC-1), 42 findings. Split per
  numbered item in plan.md; `reboot_model` → `reconfigure()` is part of it.

### 2026-09-19 — S2 items 1-6: lifecycle authority and safe teardown

Gate `-m "(lifecycle or estop) and not order_dependent and not qt"`: 46
passed, 3 xfailed. Fast gate: 225 passed, 7 xfailed. Slow: 44 passed, 5
xfailed. Invariants: 9 passed, 4 xfailed.

**`SystemManager` is now the authority, not a dict with helpers.**
`register(name, model, config=None)` enforces `isinstance(model,
ManagedModel)` and refuses a duplicate name; `release(name)` removes **and**
tears down, which `remove_model` never did despite the docs saying it did;
`reconfigure(builder)` tears down before building; `shutdown_all()` calls
`emergency_stop` before `teardown` for every model, so no model's teardown
can skip the stop. `reboot_model` and its `sleep(1)` on the caller thread
are gone, and the `hasattr(model, 'teardown')` fallback with them.

**Teardown is safety-first and exception-isolated in all four models.** The
order is hardware stop → background activity → transport close, each step
guarded. `BaseProbe.teardown` used to stop the gamepad poller *first* with no
try/finally: a poller cleanup that raised skipped `power_down()` entirely,
so the port closed with **coils still energized while Python reported the
system disabled**. `RotatorSystem.teardown` called `disconnect()` alone and
never sent ST, so a stage mid-move kept moving after the port closed.
`TemperatureSystem` now stops before closing. `RedPercentSystem.teardown`
joins its monitor thread instead of only clearing the flag.

Proved by fault injection at each sub-step (I-1.2), which an ordering test
alone cannot do — it would pass on code with no `try/finally` at all.

**Python 3.14 made `register`'s contract check bite harder than expected.**
A `runtime_checkable` Protocol's `isinstance` is stricter than `hasattr`
since 3.12, and **no Mock satisfies it however it is spec'd** — real classes
and a plain stub do. That is the right outcome (a Mock passing a lifecycle
contract check was never evidence of anything), but it means every test that
registered a Mock had to be re-authored. `ManagedStub` in `conftest.py` is
the shared replacement; the web and hardware test doubles now honour the
contract like real models do.

**15 tests re-authored, none deleted.** They encoded the old passive-dict
contract: registering the string `"Not a model"` and asserting it came back,
`full_stop_all` "silently skipping" a model with no `emergency_stop` (the
bug, now refused at registration), and `reboot_model`'s build-before-release.
The concurrency stress test now releases before re-registering, which is the
contract rather than a workaround — the old overwrite dropped a live model
without ever tearing it down.

- **Next action:** S2 items 7-9 — one process-exit hook for all three
  launchers, `build_models` register-as-you-go with rollback, and views off
  `active_models` (with the INTERIM close-affordance removal per plan.md).

### 2026-09-19 — S2 items 7-9: exit hooks, build rollback, views off the registry

Fast gate: 232 passed, 6 xfailed. Qt: 8 passed, 3 xfailed. Invariants: 9
passed, **3** xfailed — down from 4, because **I-1.5 now holds**.

**I-1.5 closed, and the strict `xfail` is what forced it to be noticed.**
When the last `active_models[` write left the views, the test XPASSed, which
`strict=True` reports as a failure — so the marker had to be retired
deliberately rather than the invariant quietly starting to pass. The last
six apparent violations were a red herring worth recording: `build_models`
kept its local build buffer in a dict *named* `active_models`, so it read as
the registry. Renaming it `built_models` was the honest fix; raising the
baseline would have hidden that the invariant was already true.

**Item 7 — process exit hooks, where there were none at all.** `src/lifecycle
.py` installs `atexit` plus SIGINT/SIGTERM/SIGHUP, and the launchers add Qt's
`aboutToQuit` and Tk's `::tk::mac::Quit`. Two properties matter and are
tested: handlers resolve the manager through `current_manager()` **when they
fire**, so a Web re-setup's replacement manager is the one that gets stopped
(this is MANAGER-1/TEMP-1/ROTATOR-2 — the old code tore down the original
empty manager and never touched the real models); and shutdown runs exactly
once, so a signal arriving during `atexit` cannot tear down twice against
half-closed transports. The signal handler restores the default action and
re-raises, so the process still dies with the right status.

**Item 8 — building is all-or-nothing.** `build_models` rolls back every
model it has already built when a later constructor raises. Without it a
failed launch left earlier devices holding their serial ports open with no
reference to them anywhere, so the next attempt could not reopen those ports
and only a process restart recovered. The Tk launcher also used to
`withdraw()` the setup window *before* building, so a failed build left the
user with no setup window and no dashboard — nothing on screen (MANAGER-6,
VIEW-TKINTER-7). It now builds first and reports the failure.

Web re-setup now tears the old models down **before** building the new ones.
It did the reverse, so for the duration of a rebuild two live handles existed
on the same port (I-1.4) — and a failed rebuild left orphans holding ports.

**Item 9 — views construct and destroy nothing.** PySide's `close_device_view`
was a third copy of the teardown policy, in the wrong order, ending in a raw
`del` from the manager's dict. It is gone. So are the
`StepperProbe(None, "None", {})` constructors, which fabricated a **silent
headless model**: every control rendered and responded, nothing was attached
to hardware, and the operator had no way to tell. An unconfigured device now
says so.

**INTERIM close affordances removed** (plan.md S2): the PySide dock is no
longer closable and Tk's middle-click / right-click "Close Tab" are unbound.
`WA_DeleteOnClose` deliberately stays — a programmatic close must still
destroy the *widget*, or unchecking and re-checking a device in the sidebar
leaks a hidden dock each time.

**Seven findings are marked `open (mitigated)`, not closed.** DC-9,
GAMEPAD-9, TEMP-5, ROTATOR-10, SERIAL-4, REDPERCENT-12 and VIEW-TKINTER-1
all say "tab close leaves the model running invisibly". Under **D-1 that is
the intended behavior**, so they are not S2's to close — S2 only removed the
way to reach the bad state. They close in **S6** when hide/show ships.

Still open in S2's range: TEMP-11 (`close()` has no flush/join — RC-1 lists
it as partial), MANAGER-20 (SetupWindow closable mid-scan with its QThread
running), WEB-1/3/20.

- **Next action:** S3 — transport truth and the E-stop latch (RC-2 item 2,
  RC-5 item 1). Gate `-m "transport or estop or scripting"`. I-2.3's xfail
  retires there.

### 2026-09-19 — S3: transport truth and the FULL STOP latch

Gate `-m "(transport or estop or scripting) and not order_dependent and not
qt"`: 89 passed, 4 xfailed. Fast gate: 247 passed, 6 xfailed. Invariants: 9
passed, **2** xfailed — **I-2.3 now holds** and its `xfail` is retired, again
surfaced as an XPASS-as-failure.

**The defect was not "an error went undisplayed".** `serial.disable()`
caught the write exception, reported it to the popup router and returned
normally. `_stop_and_disarm` then set `system_enabled = False`
unconditionally. So the UI reported the system disabled **on the strength of
a command that never left the process** — with stepper coils, the difference
between a safe bench and a hot one (SERIAL-1, STEPPER-4).

Now: `write_command()` is the single write path, raises `TransportError`, and
holds the transport lock. It deliberately does **not** report to the popup
router — that would let a caller treat a reported failure as handled. The
caller decides what a failed write means, and for a disable it means the
hardware state is unknown: the model enters a persistent fault carrying
"disable not confirmed — coils may be energized", and `system_enabled` is
**not** cleared. A later successful disable clears the fault.

All 13 direct `.ser` touches outside the transport are gone — 3 in
`probes.py`, 10 in `temperature_system.py` — including the reads, so
`read_line()` exists alongside `write_command()`.

**FULL STOP latches (RC-5 item 1).** `emergency_stop()` sets a
`threading.Event` **before any I/O**, and every motion write checks it: the
autonomous and manual paths, and each iteration of a running script. That
ordering is the point — STEPPER-8's untracked script thread means a command
can already be in flight, so checking once at the start of a run would not
help. The latch clears only via `clear_estop()`; a latch that clears itself
is not a latch. The heater latches too: a setpoint accepted straight after an
emergency stop is the same gap as a stepper accepting a move.

**I-5.2 is written and `xfail`ed to S8, not quietly skipped.**
`emergency_stop` does its hardware I/O on the calling thread, so a stalled
transport holds it well past 100 ms. What *is* true today is tested
separately: the latch is set immediately even while the stalled write is
still outstanding, so no new motion can be issued meanwhile. RC-5's
worker/timeout work is S8's.

**Two things found along the way:**

- **A failed stop frame was aborting the disable.** `_stop_and_disarm` called
  `send_stop_command()` unguarded, so a transport error there skipped the
  hardware disable entirely — the same skip-the-stop shape as the teardown
  bug in S2. Isolated.
- **SERIAL-9: simulator probes could never arm.** `_verify_serial` returns
  False for SIM because there is no port object, so `enable()` raised and
  every SIM probe was permanently un-armable — in the mode that exists
  precisely to exercise the bench without hardware. `enable`/`disable` now
  gate on `is_open()`, which treats SIM as the working configuration it is.

**Re-authored, not deleted:** `test_serial_disconnect_mid_operation` asserted
that `enable()` did *not* raise on a failed write. That swallowing was the
defect, so it now asserts the opposite. The temperature test doubles moved
from a raw pyserial handle to the transport API.

- **Next action:** S4 — Web AppContext and the security boundary (RC-10
  items 1-2). Gate `-m "web"`. **Note the live CSRF / no-origin-check hole
  and `/api/screenshot` exposure are this stage's.**

### 2026-09-19 — S4: the web security boundary and one live manager

Gate `-m "web"`: 48 passed. New file `tests/web/test_web_security.py`
(12 tests) acts as the cross-site caller.

**The hole was real and reachable.** This server drives physical hardware
from an unauthenticated localhost port. Any page the operator's browser
happened to visit could issue a cross-site form POST to `/api/command` and
move the stage, and could `GET /api/screenshot` to read the operator's
screen. Browsers send such "simple" requests with no preflight and no prompt.

Three checks now guard every POST and the screenshot endpoint, each of which
alone defeats the common case:

1. **`Content-Type: application/json` required.** A cross-site `<form>` can
   only send `text/plain`, `form-urlencoded` or `multipart`, so requiring
   JSON forces a preflight the browser will refuse. This alone kills the
   classic no-JS CSRF.
2. **`Origin`/`Referer` must match the bound address.**
3. **A per-launch token**, generated with `secrets.token_urlsafe` and
   injected into the served HTML. A cross-site page cannot read it because it
   cannot read our HTML — that same-origin restriction *is* the mechanism.
   Compared with `secrets.compare_digest`.

Reads (`/api/state`, `/api/devices`) stay open deliberately: locking them
would break the first paint before the page has run any script, and the
boundary that matters is on commands and on the screen grab.

The client side wraps `window.fetch` once rather than editing 13 call sites,
so a new call site cannot forget the header. Checked that nothing reaches the
API another way — the one `new Image()` is fed a `data:` URI.

**One manager, read through (item 1).** `WebDashboardWindow.system_manager`
and `WebDashboardServer.system_manager` are now properties reading through to
the adapter, which is the only place a manager is stored. Five objects used
to hold one and only the adapter's stayed live, because re-setup replaces it
— the window's stale copy is why `close()` shut down the *original, empty*
manager on Ctrl-C and left the real models running.

Re-setup is **single-flight**: a second concurrent rebuild gets 409 instead
of racing the first onto the same serial ports, where the loser would leave
orphaned models holding them. The lock is released on the failure path too,
so a rejected rebuild cannot wedge the endpoint for the session.

**Found in passing:** `WebDashboardServer.start()` never read the bound port
back from the socket, so `port=0` (let the OS choose) left `self.port` at 0
and every URL built from it was wrong. That is why the security tests failed
on their first run.

- **Next action:** S5 — input service and model-owned loops (RC-13, RC-4),
  33 findings. **S6 depends on it**: D-1's hide semantics cannot ship until
  the loops belong to the models. I-4.1's xfail retires there.

### 2026-09-19 — S5 part 1: SDL gets a single owner (RC-13 item 1)

Gate `-m "invariants"`: 12 passed, 2 xfailed. Fast gate: 266 passed, 6
xfailed.

**pygame ownership was spread across every poller, and each one could tear
it down under the others.** Three mechanisms, all deleted:

- `connect_controller()` called `pygame.quit()` — a **process-wide**
  teardown — to "restart pygame" for one poller, killing every other live
  poller's joystick handle at the same moment.
- `close()` decremented a module-level `_active_poller_count` and called
  `pygame.quit()` at zero. That count could not tell "nobody is using SDL"
  from "nobody happens to hold a poller object right now".
- `_ensure_pygame_video()` existed to re-init SDL after those `quit()`s. Its
  own docstring called it a recurring patch. It is the **anti-fix**
  `root-causes.md` names: it made the symptom survivable and so removed the
  pressure to fix the ownership.

`src/controller/input_service.py` now owns SDL: initialised once, torn down
**only** by `lifecycle.shutdown()` at process exit, every SDL call under one
re-entrant lock (pygame's joystick API is not thread-safe and each poller has
its own thread), and device handles acquired and released **per owner id**.
Releasing one owner never touches another's handle, and the claims registry
is derived from real acquisitions rather than kept in a parallel dict that
can drift — so two pollers can no longer both believe they hold controller 0.

**Three anti-fix guards added to the invariant harness**, because this is
exactly the kind of thing that creeps back: no `pygame.quit()` outside the
service, no `_ensure_pygame_video`, no module-level poller refcount.

**Writing those guards exposed a flaw in the harness itself.** They fired on
their own explanatory comments — several of these invariants are *about*
names the surrounding prose has to mention to explain why they are banned.
`_scan` now blanks comments and string literals with `tokenize` before
matching. I-7.1 is the one exception and says so: it matches quoted device
and command names, so it is the scan that must keep strings.

**A real bug fell out of a test.** `lifecycle.shutdown()` returned early when
no manager was set, which would have skipped the SDL teardown entirely — and
the setup window scans for controllers *before* any model is built, so a
launch abandoned at the setup screen has SDL up and no manager at all. The
teardown is no longer gated on a manager.

**Re-authored, not deleted:** `test_controller_pygame_teardown_refcounted`
asserted the refcount behaviour that was the bug. It now asserts the
opposite — that closing a poller, even the last one, never tears SDL down —
plus two new cases: two pollers cannot claim one controller, and SDL comes
down only at process exit. The SDL patch point moved from
`controller.gamepad.pygame` to `controller.input_service.pygame`, via a
`patched_sdl()` helper that also resets the service between tests so one
test's handles cannot leak into the next.

### 2026-09-19 — S5 part 2: the poller owns its clock, and edges latch

Fast gate: 272 passed, 6 xfailed.

**The poller's clock was a Tk widget.** `_poll_loop` rescheduled itself with
`self.gui_root.after(...)`, and when there was no such root it called
`stop_polling()`. So with no Tk event loop the loop ran **exactly once** and
stopped. That is the whole explanation for the Web frontend having no manual
mode: entering manual energized the coils and then nothing else happened,
because nothing was polling. The poller now runs its own daemon thread when
no `after`-capable root is supplied, and the Tk path is unchanged.

**Edges were detected by the reader, not the poller.** `get_mapped_state()`
computed dpad/bumper edges *and* consumed them by updating the latch. Two
consequences, both now tested:

- whichever caller read first swallowed the edge for everyone else;
- a button tap that started and ended between two reads was never seen at
  all — both reads observed 0, so no edge ever existed.

The poll loop now latches edges when they happen and holds them until
drained. `read_levels()` is non-consuming and any number of readers may call
it; `drain_edges()` is the single-consumer read. `get_mapped_state()` stays
as a compatibility wrapper (levels + drained edges) so existing call sites
keep working until RC-4 moves the input pump into the models.

**One discrepancy left alone deliberately.** `POLL_INTERVAL = 5` sat under a
comment reading "Poll 50 times per second (1000ms / 20ms = 50Hz)" — the code
polls at ~200 Hz, four times the documented rate. **The value is unchanged
and the comment now states the truth.** The manual-mode command rate is
something the operator feels at the bench, so which of the two is correct is
an owner decision, not a refactor's. → **Needs an owner ruling.**

**A stale call site survived two stages, and the reason matters.**
`test_dashboard_window_teardown_ordering` still called `register_model`,
renamed back in S2. It is marked `order_dependent`, which excludes it from
the fast gate, every concern gate **and** the main sweep — so nothing
routine had run it since S1. Found by running the isolation pass at this
boundary rather than saving it for the end. `testing.md` now carries that as
a rule: an excluded test is not a quarantined test, it is an unwatched one.
Swept for other stale call sites at the same time; three more were in
`tests/ui/` (excluded by `addopts`) and `test_view_round1.py`.

**One concurrency exposure was created and then closed in the same stage.**
Giving each poller its own thread makes two threads able to call pygame's
joystick API at once, which is not thread-safe — previously impossible only
because polling was serialised through one Tk event loop. A poll tick now
holds the input service's SDL lock for the whole read rather than per call,
and the hardware-change scan is extracted into `_read_hardware_changes()` so
the locked region is one obvious block.

- **Next action:** S5 part 3 (RC-4) — the input pump and hardware sampling
  move into the models, deleting Tk's `_route_input` (50 ms) and PySide's
  `input_timer` (20 ms) and PySide's extra 50 ms poll in `_poll_model`. That
  is what retires I-4.1 and unblocks S6.

### 2026-09-19 — S5 part 3 (RC-4): the loops move into the models

Gate `-m "(loops or mode or transport) and not order_dependent and not qt"`:
159 passed, 6 xfailed. Invariants: 12 passed, **1** xfailed — **I-4.1 now
holds**, the third invariant to retire, again forced by an XPASS failure.

**32 view-owned loop calls are gone**: 15 in PySide, 13 in Tk, 4 in the web
adapter. What they were:

- PySide ran a 100 ms position timer, a 100 ms status timer, a **20 ms**
  manual-input timer, and started the gamepad poller on a QTimer-backed
  adapter — *and* sampled position and status again from its render tick
  every 50 ms, roughly tripling the serial traffic for one device.
- Tk ran the same four loops, with manual input at **50 ms**. So the two
  desktop frontends did not feel the same at the bench.
- The web adapter sampled inline inside `/api/state`, which made the
  sampling rate whatever the browser happened to poll at and let a stalled
  read block the HTTP handler thread.
- The Web dashboard had **no input pump at all**.

`BaseProbe` now owns both loops, at one documented rate each:
`MANUAL_COMMAND_INTERVAL = 20 ms` and `SAMPLE_INTERVAL = 100 ms`. The model
also starts its own poller, with no GUI root, so the poller uses the thread
it gained in part 2.

**`tests/core/test_model_owned_loops.py` is the Web-parity proof (I-4.2) and
contains no GUI of any kind.** That is the point: manual mode is exercised
with no Tk, no Qt and no browser, so if it works there it works in all three
frontends, because none of them own it any more. It also covers neutral-on-
exit, FULL STOP cutting the pump immediately, and I-4.3 — a sampler stuck in
a 1 s read does not delay the stop path.

**Rate choice, flagged rather than buried.** Tk's 50 ms and PySide's 20 ms
could not both survive. 20 ms is adopted: the faster of the two, and the one
the primary GUI has been using. It changes how Tk manual mode feels. Related
to **D-12**; if the owner rules on the gamepad poll rate, revisit this
alongside it.

**Loops start on `enable()`, not at construction**, so an idle or
test-constructed model does not run two threads; `teardown()` stops them
first, and that is tested.

**Re-authored, not deleted:** `test_api_state` asserted that hitting
`/api/state` incremented the model's poll counters — that reading the API
drove the hardware. It now asserts the opposite.

**Two more stale tests, caught by the Qt pass — and worth noting *where*.**
`test_qt_dynamic_view_poll_model` asserted the render tick *did* call
`read_position()`/`poll_status()`, and `test_watchdog_do_disable_manual_mode`
asserted the **view** passed `activity_callback` into `start_polling`. The
second one encodes the bug plainly: the watchdog was only fed in frontends
that started a poller, so the Web dashboard had **no idle auto-disable at
all**. Both re-authored to assert the opposite. They live in the Qt pass,
which the main sweep deliberately excludes — a second reminder, after the
`order_dependent` one earlier in this stage, that a separated pass is only
as good as the discipline of running it.

**S6 is unblocked.** D-1's hide semantics required exactly this: a hidden
device keeps running, so its loops had to stop belonging to the widget that
gets hidden.

### 2026-09-20 — S6 and S7 stopped at the owner boundary

Both stages are **BLOCKED on D-2**: *when a mode is left, do the coils get
disabled, or is motion merely stopped?*

S6 cannot proceed because "hide performs a safe stop of motion" has no
defined meaning until D-2 is answered — and getting it wrong in either
direction is a bench hazard. Hiding a device that should have been
de-energized leaves coils live behind a closed tab; disabling one that
should have held position drops a loaded axis. S7 needs the same answer for
what `_transition` does on every exit from a mode.

plan.md's standing rule 4 says an agent never decides an open `D-n`, and
both stage sections say explicitly: *"If D-2 is unanswered when this stage
is reached, stop and ask."* So they stop here. **S5, their prerequisite, is
complete** — this is purely a decision block, not a technical one.

Proceeding to **S8**, which depends on no open decision and owns the I-5.2
`xfail` left behind in S3.

**D-2 needs an owner ruling, along with D-12 and the manual-rate question
raised in S5.**

### 2026-09-20 — S8 items 2 and 3: FULL STOP that returns, and a link that tells the truth

Fast gate: 296 passed, **4** xfailed — **I-5.2 now holds**, the fourth
invariant to retire, and the fourth time strict `xfail` forced the
retirement instead of letting it pass unnoticed.

**FULL STOP no longer blocks its caller (I-5.2).** `emergency_stop` gives two
separate guarantees now, and the separation is the point:

1. The latch is set **synchronously, before any I/O**. That is what actually
   protects the bench — from that instant no new motion can be issued — and
   it cannot fail.
2. The hardware write is dispatched to a worker and joined with an 80 ms
   bound. If the bound expires the stop is still in flight; we stop
   *waiting*, we do not stop trying. A test asserts the write really does
   land afterwards, so "returns early" cannot quietly become "gives up".

The caller is frequently the UI thread, and a stop button that freezes the
window behind a dead serial port is one the operator stops trusting.

**`full_stop_all` fans out.** It used to stop models **one after another** on
the caller's thread, so a device with a wedged transport delayed the stop of
every device behind it — in *registration order*, which has nothing to do
with which axis is moving. Each model now gets its own thread under one
1 s bounded join, and the call returns `{name: ok}`. A `False` means the stop
did not confirm in time, **not** that it was skipped: that model's latch is
already set and its write is still going.

**Stops outrank polls for the transport lock.** `write_command(..., priority
=True)` waits 50 ms for the lock and then forces the write through. The
comment says plainly why that is acceptable for `d`/`k` and is never to be
used for motion: a mangled stop is the worst case, and the alternative is no
stop at all while a long poll holds the lock.

**`ConnectionState` replaces three different guesses** (SIMULATED /
CONNECTING / VERIFIED / UNVERIFIED / LOST / CLOSED). Two real bugs close
with it:

- **SERIAL-7:** opening a port proved nothing. The old code printed
  "Operating blind" and then treated the link as good, so a cable into a
  powered-off board was indistinguishable from a working one. VERIFIED now
  means the board *answered*.
- **SERIAL-8:** port loss was invisible. Read and write errors produced popup
  spam on a 5 s dedupe while the reported state never changed, so the UI kept
  showing the last good position of an unplugged device. The first failure
  transitions to LOST, releases the handle, and reports **once**.
- **DC-13:** the web badge inferred "simulated" from the *editable*
  `serial_port` field, so typing "SIM" into a hardware probe's port box
  relabelled it SIMULATED while it went on driving real hardware. The badge
  asks the transport now, and a test types "SIM" to prove it cannot be faked.

**Still open in S8:** item 1 (one command worker per model with a `_run_id`
generation token — this is what should finally dissolve the four
`order_dependent` `run_script` tests) and item 4 (SIM as an explicit
simulated transport that ACKs, rather than `ser=None`).

### 2026-09-20 — S8 item 1: run generation tokens, and a prediction that was wrong

Fast gate: 296 passed, 4 xfailed. Scripting gate: 6 passed.

**`run_script` spawned an untracked thread with no way to stop it and no way
to know it had.** Halting relied on the thread noticing that `is_stepping` or
`auton_flag` had been flipped — flags any *other* caller could flip back, and
which said nothing about **which run** they belonged to. Starting a second
script, or stopping and starting again, left the first thread still writing
to the port while the operator believed the device was stopped (STEPPER-8).

Every run now carries a generation. `_stop_and_disarm` bumps it, so a stop
invalidates whatever is in flight; the run notices at its next step and
returns. `teardown` cancels and joins the script thread. The old
`getattr(self.serial_comm, 'ser', None)` check went with it — and **that
getattr is how a transport bypass slipped past I-2.3 until now**, since the
invariant only matched `.ser.` with a trailing dot. The pattern now catches
the getattr form too.

**The prediction I recorded in S5 was wrong, and that matters more than the
token.** I wrote that item 1 would dissolve the four `order_dependent`
`run_script` tests. It did not. The token fixes a script outliving its own
run; it does not touch the remaining shared state, which is `ErrorRouter`'s
class-level callbacks (RC-8). **Expect that family at S11, not before.**

**Three tests moved from `known_bad` to `order_dependent`, and the reason is
a policy point worth keeping.** `test_run_script_gcode_execution_path`,
`test_run_script_malformed_gcode` and `test_run_script_unrecognized_actions`
**pass when run alone and fail only in composition** — their *outcome* is
order-dependent. A strict `xfail` cannot express that: it reports the lone
run as XPASS-as-failure and the composed run as a clean xfail, so the marker
asserts "known broken" about a test that is only conditionally broken. That
is worse than no marker, because it looks deliberate.

Verified they XPASS at `87b34a1` too, so this predates today's work. **The
main sweep never showed it** — the composed run xfails quietly, and only a
per-file run reveals the XPASS. That is the third time this stage that a
defect hid inside a separated pass.

`test_run_script_unrecognized_actions` also got the diagnosis its quarantine
note demanded: it was watching `serial_comm.ser.write`, one of the 11 raw
bypasses S3 deleted. The model writes through `write_command()` now, so
`.ser.write` is never called and the assertion saw silence. **The behaviour
was correct the whole time; the test was watching the wrong object.**

### 2026-09-20 — S8 item 4: the simulator becomes a device, not a branch

Fast gate: 303 passed, 4 xfailed. **S8 is complete.**

Simulator mode was `ser = None` plus an `if SERIAL_PORT in ('SIM', ...):
return` early exit in **every** transport method. That made SIM a *different
code path* rather than a different device, so the paths the bench exercises
headlessly were not the paths it runs with hardware attached — and each new
method had to remember to add its own early exit. That is precisely how
SERIAL-9 happened: `enable()` forgot, so every simulator probe was
permanently un-armable, in the mode that exists to run the bench without
hardware.

`SimulatedPort` now stands in for the pyserial handle and acknowledges
everything: writes succeed and are recorded, reads return nothing, the port
reports itself open. The transport above it takes exactly one route whether
or not a board is plugged in, and a closed simulator refuses writes exactly
as a closed port does.

**S8 summary.** All four items done. `I-5.2` retired. `known_bad` is down
from 10 entries to 7; `order_dependent` is up to 10, three of which arrived
from `known_bad` because their outcome was conditional rather than broken.

**Next available stage is S9** (typed parameters, RC-6). **S6 and S7 remain
BLOCKED on D-2**, and D-12 plus the S5 manual-rate change are still waiting
on an owner ruling.

### 2026-09-20 — S9 items 1 and 4: two silent wrong-value hazards

Params gate: 10 passed. New `tests/core/test_typed_params.py`: 32 tests.

**A DC probe could be handed the stepper's speed.** `get_params` coerced
every value with hardcoded fallbacks — `_num(self.x_step, 16)`,
`_num(self.full_speed, 400)` — which are *BaseProbe's* numbers. A `DCProbe`
declares `full_speed = "120"` and `man_full_speed = "120"`, but an empty or
unparseable field was enough for the fallback to send the firmware **400**:
more than three times the speed that probe is configured for, with nothing
on screen indicating a substitution had happened. The fallback now belongs to
the class (`PARAM_DEFAULTS`), not to the call site, and a parametrised test
checks every probe class against every flavour of bad input.

**A blank temperature field commanded the wrong temperature.** The setpoint
and gains were interpolated raw into the frame, so an empty box produced
`"<,6.0,2.0,0.5,.1,0>"`. The firmware parses that with `strtok`, and
**strtok does not see an empty field — it sees the next one.** Every
parameter after the blank shifts left, so the board takes the ramp rate as
its setpoint and the gains as everything else. This is a heater. The frame is
now validated field by field and refused as a whole, with the offending field
named.

Both are the same shape: a value that failed to parse was silently replaced
with something plausible rather than refused, and the operator had no way to
tell. That is the RC-6 thesis in two concrete cases.

**Remaining in S9:** item 2 (type and bounds declared in the schema so views
stop inferring numeric-ness by trying `float()`) and item 3 (D-5's
commands-carry-their-inputs, which kills the stale-value class in all three
views at once). Item 2 overlaps S10's schema v2; do them together if S10 is
near.

### 2026-09-20 — the flaky-test diagnosis was wrong, and here is what it actually was

**Seven `order_dependent` tests are gone.** The quarantine is down from ten
entries to three. And the cause was **neither** of the two things this trail
has been asserting since the first session.

It was not `ErrorRouter`'s process-global callbacks (RC-8). It was not
STEPPER-8's untracked script thread — S8's generation token did not move
them, which was the first clue that the diagnosis was wrong.

`tests/scripting/test_edge_mvc_scripting.py` installed its mock parser with
`sys.modules['gcodeparser'] = mock_gcodeparser` **at import time**, then
imported `model.probes`. That works only if this file is what *first*
imports `model.probes`. Several earlier test files import it, and when they
do, `probes.gcodeparser` is already bound to the real library — the mock is
never seen.

**It did not look like a wiring mistake because the two parsers disagree
subtly.** The real parser yields `{'X': 10.5, 'Y': 20, 'F': 100}` with Y as
an **int**, so `str(Y)` is `'20'`. The mock yields `20.0`, so it is `'20.0'`.
Same test, two parsers, different answers, decided by import order. The
symptom was an assertion about *number formatting*, which is exactly what
RC-6 is about — so it read as a real product bug and was quarantined as one.
Two of these tests sat in `known_bad` blaming RC-6 and RC-8 respectively.

The fix is one autouse fixture patching `model.probes.gcodeparser` — the
name the module actually uses — instead of mutating `sys.modules`. All
thirteen tests in the file now pass identically across repeated runs.

**What this costs, honestly:** `test_run_script_gcode_execution_path` was
quarantined as covering RC-6 and it never did. Under the mock it asserts
`'20.0'` and passes; the `'20'` it produced in composition came from the real
parser, not from the untyped-parameter defect. **RC-6's script-path coverage
has to be written fresh in S9 item 3** — it was never there.

**The method that found it was the one that should have been used first:**
reading the actual assertion values instead of reasoning from the audit's
list of process-global suspects. Two plausible root causes, both real
defects in their own right, both innocent here.

### 2026-09-20 — session close

**Pushed and in sync at `44c67a0`.** 14 commits. Working tree clean.

| | Stage | State |
|---|---|---|
| S0–S5 | harness, purge, lifecycle, transport, security, loops | **done** |
| S6, S7 | hide/show, probe mode | **BLOCKED on D-2** |
| S8 | motion serialization, ConnectionState | **done** |
| S9 | typed parameters | items 1, 4 done; 2, 3 remain |
| S10–S16 | — | todo |

**Suite:** 390 passed / 6 xfailed on the main pass, **identical across three
consecutive sweeps**; Qt 7 passed; 3 order-dependent tests, each passing in
isolation. `known_bad` is 7 — five gamepad/mode tests owned by S7 (itself
blocked on D-2) and two PySide schema tests owned by S10. `order_dependent`
is 3, down from 10.

**Ledger:** 50 of 213 findings closed with named test evidence, 6 recorded as
`open (mitigated)` rather than claimed, 157 open.

**Invariants holding:** I-1.5, I-2.3, I-4.1, I-5.2. Each retirement was
forced by `xfail(strict=True)` reporting the fix as a failure — none was
noticed by looking.

### 2026-09-20 — owner rulings: D-2, D-4, D-8, D-12

Four of the five open decisions are answered. **S6, S7 and S14 are
unblocked**; D-7 (firmware v2) is the only decision still outstanding and it
is S16 bench work.

**D-2 — mode off disables the coils.** The current behavior stands and
main's is not restored. This means DC-10's divergence is now *intentional*:
there is no way to stop motion while keeping holding torque, and a loaded or
vertical axis can sag on release. S7's `_transition` implements exactly one
de-energizing path — stop packet, then `d`, then `system_enabled = False` —
and S6's "safe stop on hide" uses it. **Anyone tempted to add a hold-torque
variant later should reopen D-2 rather than add a branch**; the whole point
of RC-3 is that this side effect lives in one ordered place.

**D-4 — gate input, never stop.** Losing focus closes an input gate and
drops edges; it never emits a stop packet, and motion already in flight
continues. **A child dialog deactivating the main window is not focus loss**
— that distinction is the actual defect behind GAMEPAD-8 / PYSIDE-14 /
VIEW-TKINTER-9, not the `flush_neutral` no-op itself. On regaining focus the
gate opens and flushes neutral once.

**D-12 — 200 Hz stands; the comment was wrong.** `POLL_INTERVAL` stays at
5 ms. The poller runs at 4x the 20 ms manual pump *deliberately*, to catch
button edges shorter than one pump tick; both comments now say so. The S5
manual pump rate was **ratified** in the same breath: Tk manual mode is
faster than it was on main, on purpose, and the two frontends now feel
identical. Neither value is an open question any more.

**D-8 — two tiers, folded into the existing watchdog.** Warn when no client
has polled for N s; FULL STOP at M s *while motion is active*; an idle
energized system is left alone. Suggested starting values N=5, M=15, to be
tuned at the bench.

The owner's note — that an idle timer already exists and this should be part
of it — is correct about the mechanism and worth stating precisely, because
the two timers measure different things:

| | Interlock watchdog (exists) | D-8 liveness (S14) |
|---|---|---|
| Measures | operator inactivity | web client absence |
| Horizon | 300 s | ~5 s warn / ~15 s stop |
| Action | `disable()` | warn, then FULL STOP |
| Condition | energized | energized **and** moving |

They share one thread, one `last_activity_time` family and one code path —
S14 adds a second threshold to `_start_interlock_watchdog`, it does not add
a second watchdog. But they are not the same trigger: a faithful client can
poll all afternoon while the operator is idle, and an autonomous run is
"active" at the hardware precisely when the client may have gone away. Web
polling should call `touch_activity()`, and the liveness check needs its own
timestamp.

**One dependency to respect:** the existing watchdog `continue`s while
`is_stepping or manual_flag`, so today it never fires in the energized modes
D-8 cares about. **S7 item 2 fixes that** (the watchdog measures real
inactivity and stops deferring on flags). S14's liveness tier is only
trustworthy once S7 has landed — build it after, not before.

### 2026-09-20 — S7: probe mode becomes a state machine

Mode gate (`-m mode`): 118 passed. New `tests/core/test_probe_mode.py`: 25
tests. **All five S7 items done.**

**Four booleans had seven writers.** `system_enabled`, `auton_flag`,
`manual_flag` and `is_stepping` were assigned from `enter_auton`,
`enter_manual`, `macro_start_auton`, `run_script`,
`send_manual_mode_command`, `_stop_and_disarm` and the Web `set_attr` route,
with the hardware side effects scattered among them and no agreed ordering.
`ProbeMode` replaces them; `_transition(target, reason)` is the only writer
and owns the enable, the stop frame and the disable in one ordered place. The
four names survive as **read-only derived properties**, so every schema
toggle still renders and the Web dashboard still reads them — but assigning
one now raises `AttributeError`. That is I-3.4, and it closed a real hole:
`/api/set_attr` could write `manual_flag` directly, arming manual mode with
no gamepad check and no hardware enable (STEPPER-11, DC-11). The route now
returns **403 for any read-only property**, which is a general rule rather
than a list of names, so S10's `writable` flag inherits it.

**FAULT is a mode, not a flag beside one.** When a disable is not confirmed
the hardware state is genuinely unknown, and `system_enabled` reports
**True** for it — unknown reads as possibly-live, never as safe. Modelling it
this way also fixed a defect nobody had filed: a fault in the manual input
pump used to `return` out of the loop leaving `manual_flag` True, so the UI
showed manual mode engaged with nothing pumping it.

**`is_stepping` is now a timed sub-state, and its old form was the bug.** It
was set by `macro_start_auton` and cleared only by a stop, so a move that
finished normally left it True forever — and the watchdog deferred on it. One
autonomous move disabled the idle interlock for the rest of the session, in a
mode that energizes coils (STEPPER-6, DC-1, VIEW-TKINTER-3). It is derived
from a deadline now, extended each time the position actually changes, so it
expires `_STEP_SETTLE` after the axis stops.

**On not inventing a units conversion.** RC-3 says stepping ends "on computed
move duration or position arrival". Computing the duration means dividing a
step count by a speed whose units this layer does not get to assume — exactly
the RC-6 sin of substituting a plausible number. **Arrival is observed
instead**, from the sample loop that already runs. If that seems less precise:
the safety property is the watchdog no longer deferring, which holds however
stepping ends. The sub-state's remaining job is UI truth.

**The watchdog gets a fresh Event per arming** (STEPPER-7). A single reused
`_interlock_stop` meant a disable left it set, so the next enable started a
thread that returned on its first tick and the probe ran energized with no
interlock at all. Writing the test for this caught a second-order version in
my own first draft: the re-arm guard checked `thread.is_alive()`, but a
thread told to stop stays alive until its next tick — up to
`_INTERLOCK_POLL_INTERVAL` later — so re-arming inside that window returned
early and left the probe watched by a thread on its way out. Guarding on the
event as well as liveness fixed it; the stale thread retires on the
generation check.

**`set_controller` returns the bind result.** `controller_var` was assigned
*before* the bind was attempted, so a failed swap left the UI naming a
controller that was never bound — the reported "toggle desync after
controller swap" (GAMEPAD-3/4, VIEW-TKINTER-10). The poller is the source of
truth now and the model mirrors it; a swap that fails during MANUAL leaves
manual mode, because manual mode without a bound pad is what I-3.2 forbids.

**One behavior change the tests caught, and the fix is a named exception.**
Making every mode entry send a neutral frame gave `macro_start_auton` a
zeroed frame immediately followed by the move — a wasted write and a
stop-then-go hiccup at the board. `_transition` takes `quiesce=False` for
that one caller. Leaving a mode always quiesces regardless: that half lives
in `_go_disabled`, where no caller can opt out.

**Quarantine: 7 known_bad down to 2.** All five S7-owned entries re-authored
rather than deleted — they assumed `enter_manual()` succeeds with no gamepad,
which `046533f` had already made false. They now bind a pad, which is the
contract. The two survivors are PySide schema tests owned by S10.

**Eight more tests re-authored**, all the same shape: they forced a flag
(`probe.manual_flag = True`, `probe.system_enabled = True`) to set up a state
the model would not enter on its own. Every one of them now goes through the
transition. One inverted assertion among them —
`test_auto_disable_interlock_deferred_while_stepping` asserted the deferral
that *is* STEPPER-6 — so it now asserts the opposite and keeps its name's
history in a comment.

**Out-of-scope edit, recorded per standing rule 1.** `web_adapter.py` wrote
`model.system_enabled = False` after setup, falling back to `disable()` only
when the attribute was absent — i.e. for every probe it declared the system
disabled without telling the board. The read-only property turned that into a
raise, so it had to change; it calls `disable()` now. It is an RC-2
belief-vs-reality fix that S14 would otherwise have inherited.

### 2026-09-20 — S6: closing a view hides the device

Lifecycle gate (`-m lifecycle`): all green. New `tests/core/test_hide_show.py`:
13 tests, plus 2 Tk view tests. **All four S6 items done.**

`SystemManager.hide(name)` / `show(name)` / `is_hidden` / `visible_models`.
Hiding marks the device invisible and brings the hardware down per D-2; it
does **not** touch `active_models`, so the model, the port and the controller
binding all persist and the model-owned loops keep running. That last clause
is why S5 was a hard prerequisite: before it, the loops belonged to the widget
that closing destroys.

**`show` never constructs.** A device that was not configured at startup
returns `None` and the view says so. The old path fabricated
`StepperProbe(None, "None", {})` — a silent headless model where every
control rendered and nothing was attached to hardware (PYSIDE-1, MANAGER-8).

**Showing does not re-arm.** `hide` de-energized; re-energizing is an
operator action taken while looking at the device, which is the state the
view has only just returned to.

**A failed disable still hides.** The model faults and says so (RC-2), but
the window closes. Refusing to close a window because a serial write failed
traps the operator in front of a device they cannot dismiss.

**The affordances are back in both views.** PySide docks are
`DockWidgetClosable` again and the close routes to `manager.hide`; the view
no longer runs its own teardown ladder. Tk gets middle-click-to-close.

**Tk needed a re-add path built from nothing.** `notebook.forget()` is
one-way — ttk keeps no handle to a forgotten tab — and
`on_close_tab_callback` was never assigned, so the fallback ran every time.
The close now uses `notebook.hide()`, which keeps the tab registered, and a
**Devices menubar of checkbuttons** is the gesture that brings it back,
mirroring what PySide's sidebar already did. The frames are kept in
`device_frames` so a hidden tab has something to be added back *by*.

**Hidden is not forgotten.** Two tests exist purely for the failure mode that
would be worst: a hidden device is still torn down at shutdown, and still
answers FULL STOP.

#### A second quarantine entry retired, with a proven cause

`test_dashboard_window_teardown_ordering` has sat in `order_dependent` since
S0 under this file's standing theory — process-global product state. **That
was wrong, and the real cause is one line of harness wiring.**

`sys.modules['tkinter.ttk']` was a bare `MagicMock`, so
`class DraggableClosableNotebook(ttk.Notebook)` never produced a class: a
MagicMock base goes through `__mro_entries__` and yields another MagicMock,
whose `side_effect` is a finite `tuple_iterator`. `DashboardWindow` was
therefore **constructible only a bounded number of times per process**, and
whichever test drew the empty iterator died with `StopIteration` raised from
inside `unittest.mock`, with a traceback pointing at the view.

The fix is a real `DummyTkNotebook` stub that models tab bookkeeping — which
S6 needed anyway, since "can a hidden tab be added back" is the question.

**And the fix needed two bindings, only one of which is obvious.**
`sys.modules['tkinter.ttk']` alone did nothing: the views write
`from tkinter import ttk`, which reads the *attribute* off the tkinter module
object, and on a MagicMock that auto-creates an unrelated child. Setting only
`sys.modules` left the views holding the auto-created one.

`order_dependent` is **2**, both web wall-clock tests with a different cause.
That is now twice that a quarantine entry blamed the architecture and turned
out to be harness wiring. The lesson from the S9 session stands and should be
applied to the last two before any architectural explanation is believed.

### 2026-09-20 — D-4: the input gate (S5's deferred item)

Loops gate: 11 passed. Four model tests + four Tk view tests.

**This is S5 work finished late**, not a new stage. RC-4's corrective design
named `flush_neutral`'s semantics as DECISION D-4, so the item could not land
while the decision was open. It is recorded here rather than silently folded
into S6.

**`flush_neutral` could never have worked, and that is why it was a no-op.**
It zeroed the poller's cached axis state — and the poll loop runs at 5 ms
against a 20 ms pump, so it read the physical stick again and refilled the
cache four times over before the next send. Gating the *send* is what
actually holds the axis. The gate is a `threading.Event` on the model,
checked in `_input_loop`, which is the one place controller input becomes
motion.

**Gating, never stopping.** A closed gate emits no stop packet and does not
leave the mode: a move already in flight continues, and alt-tabbing to read a
value does not halt the bench. The closing edge sends exactly one neutral
frame — the same I-4.2 rule as leaving manual mode — because otherwise the
last non-zero command stands.

**A child dialog is not focus loss.** This is the part of D-4 that is
actually hard, and each view answers it in its own idiom:

* **Tk** — `focus_get()` answers *within this application*, so a non-None
  result means one of our own dialogs has focus and the gate stays open.
  Events bubbling up from entry widgets are ignored; only the window's own
  count.
* **PySide** — the check is deferred one event-loop turn with
  `QTimer.singleShot(0, …)`. On `WindowDeactivate` Qt has **not yet** made
  the new window active, so asking immediately cannot tell "switched
  applications" from "opened a modal dialog". After the turn,
  `QApplication.activeWindow()` is non-None exactly when the focus stayed
  with us.

Without that distinction the rotation-confirmation dialog — which exists to
be answered before a risky move — would itself gate the input for the move it
is confirming.

### 2026-09-20 — S9 items 2-3 + S10 (IN PROGRESS, paused mid-stage)

**Read this before touching anything.** The work below is committed and
pushed, the fast gate is green, but **the Qt pass was never verified**. Do
that first — see "What is unverified" at the end of this entry.

#### S9 item 2: parameters are declared, not inferred

New `src/model/params.py`. `Param(name, type, default, minimum, maximum,
decimals, unit, label)` with two deliberately different coercion paths:

* `coerce` is **lenient** — falls back to this class's own default. For
  building a frame from whatever is stored.
* `parse` is **strict** — refuses and names the field. For accepting operator
  input.

That split is RC-6 in one class. A value that cannot be read is an error to
report, not a number to invent.

`BaseProbe.PARAMS` replaces `PARAM_DEFAULTS`, and `_param(name)` no longer
takes `minimum=`/`integer=` per call site — those bounds lived in however
many places happened to read a parameter, and the class default lived
nowhere. `RedPercentSystem`, `RotatorSystem` and `TemperatureSystem` got
tables too.

#### S9 item 3 / D-5: commands carry their inputs

`SchemaCommands` mixin in `model/base.py`, shared by all four models so the
validate-then-run ordering cannot differ between them. `apply_inputs` is
**atomic**: every declared field parses, or none is committed. `execute_command`
refuses the command outright if any input fails, naming the offending field.

**Tk's `focus_set()` flush is deleted.** It was the named anti-fix: forcing
focus away before every command so a pending `<FocusOut>` would commit. It
worked only in Tk, only for the widget that happened to hold focus, and not
at all for the Web client — which had no way to commit an edit and run a
command atomically, only `set_attr` per field and hope.

#### S10: schema v2

New `src/model/schema.py`. Builders return plain dicts, so the schema stays
serialisable and the Web client receives the same description the desktop
views render.

What the views had to guess and now do not: `value_type` (both called
`float()` on the *current contents*, so a cleared box was reclassified as
text and lost its validator), `writable` (defaults **False**; a control has
to ask), `role` instead of `bg`/`fg` hex only Tk could honour, and
`enabled_when`/`disabled_when` evaluated by one shared `schema.is_enabled`.

**Composites** with one contract and three renderers: `region_select`,
`file_save`, `plot`, `log_stream`. These replace three view-side shims
(`set_focus_area_ui`, `plot_data_ui`, `save_log_web`) that stood in for
element types the schema could not express.

**The confirm contract.** `NeedsConfirmation` is a value the model returns.
This replaces `confirm_rotation_callback`, which the *views injected into the
model* — and the Web client never injected one, so `_confirm_rotation` fell
through to "blocked automatically" and the ±30° tubing check existed there
only as a refusal nobody was shown. A guard that silently declines is not a
guard the operator can answer.

**PYSIDE-7 is now inexpressible.** `sch.dropdown()` raises if `command` is
missing, so the shape that reached `getattr(self.model, None)` cannot be
written.

**D-6: Red Percent is schema-driven in all three views.** Tk's hand-built
`RedPercentView` — about 240 lines of its own entries, checkboxes, buttons,
matplotlib window and CSV dialog — is deleted, as is PySide's parallel
bolt-on (`_add_position_source_control`, `_add_custom_buttons`). What remains
in each is only what the schema genuinely cannot express: stopping the run
when the view goes away, and D-10's unsaved-data prompt.

**The controller log became a `log_stream`.** It used to be a
`cmd_name == "open_controller_log"` branch opening a Toplevel only two
frontends could build. The model buffers it now, so **the Web client has a
controller log for the first time.**

#### The device registry: four copies collapsed into one

New `src/model/devices.py`. The device list existed **four** times and they
disagreed: `_build_each`'s if/elif chain, `app_bootstrap.DEVICE_MAP` (which
knew four of the six), PySide's sidebar literal, and the S0 invariant
harness's deliberate fourth copy. That last one is the tell — a list
duplicated four ways where one copy exists purely to catch the others
drifting is exactly the shape RC-7 describes. `IDENTITY_CHARS` is now derived
from the registry rather than declared again.

View routing moved from device-name literals to a model-declared `VIEW_HINT`,
because Tk and PySide need different classes for the same device.

#### New conformance test: `tests/ui/test_schema_v2.py` (81 checks)

Every schema checked against the model it describes, over *all six* models,
so a schema added later is covered without anyone remembering. Commands,
`options_command`s, `model_attr`s, `param` references and D-5 `inputs` must
all resolve. This is the test the codebase most needed and did not have:
nothing verified that the contract three renderers work from referred to
anything real.

#### The vacuity guard earned its keep

`test_harness_is_not_vacuous` failed the moment schema v2 landed. The I-7.1
extractor greps `src/model/` for `"command": "..."` literals, and v2 moved
commands into builder *keyword arguments* — so the pattern silently matched
nothing and the invariant would have passed vacuously. It introspects the
built models now, which is what its docstring always claimed and the regex
only approximated.

**I-7.1 is down from 26 violations to 8** but is **not retired**. The
remainder:

| Location | Owner |
|---|---|
| `web_adapter.py` Red Percent linking block (3 hits + a docstring) | **S12 item 2**, which deletes it outright |
| `pyside/view.py` `stop_monitoring` unsaved-data prompt (2 hits) | genuinely view-side (D-10); decide at S13 whether it can be expressed |
| `tkinter`/`pyside` `cmd_name` fallbacks | S12 |

Do **not** bump the I-7.1 baseline to make it green. Retire the `xfail` when
S12 lands.

#### What is unverified — do this first

* **The Qt pass never completed.** Two `known_bad` entries are S10-owned
  (`test_pyside_redpercent_sync_and_probe_controls`,
  `test_pyside_dashboard_sidebar_dock_sync`) and both are expected to **XPASS
  now** — S10 deleted the duplicate row the first guards, and schema v2 makes
  the second's `TypeError` inexpressible. Under `xfail(strict=True)` an XPASS
  reports as a failure, which is the mechanism forcing them to be
  re-authored. **Run `pytest tests/ -m "qt"` and expect two failures, then
  re-author both and remove their `_KNOWN_BAD` entries.**
* **The slow and order-dependent passes were not re-run** after the renderer
  work.
* **No frontend has been run against hardware.** Three renderers were
  rewritten; only the schema conformance test and the fast gate have
  exercised them. The composites (`plot`, `log_stream`, `region_select`,
  `file_save`) have **no rendering test at all** — they are the least-proven
  code in this commit.

Verified: fast gate **398 passed, 1 xfailed** (the xfail is I-7.1).

### 2026-09-20 — S10 closed: the Qt pass was a hang, and a whole test directory was never running

**The unknown from the last entry is resolved, and it was neither of the two
readings offered.** Not the documented exit-134 SIGABRT, and not broken
collection: `pytest tests/ -m "qt"` **hung**, indefinitely, on the third of
nine tests. The 24 bytes recorded last time are `..` — the two tests before
it — and the exit 0 came from the *kill*, not from pytest. A killed pytest
prints no summary line, so three sessions in a row read a timeout as a
result.

`test_pyside_event_callbacks_and_two_way_binding` calls
`view._execute_command("run_cmd")`. Its `DummyPySideModel` predates S9 item 3
and has no `execute_command`, so `_run_element` raised `AttributeError`,
landed in its own `except`, and opened a **modal** `QMessageBox.critical`
with nobody to click it. The renderer rewrite did not break the test; the
test double was never updated to the contract all three renderers now
require.

Nine test doubles across two files now mix in `SchemaCommands`, which is what
the real models do.

#### The directory nobody was running

`tests/pytest.ini` carried `--ignore=tests/ui`, added at S0 on the grounds
that including it "triggers a native SIGABRT (qt_check_pointer inside
QApplication())". **It does not, and the evidence says it never did.** The
directory hangs — same modal, same cause — and each attempt to run it was
killed and filed as an abort. With the doubles fixed it runs in **4 seconds,
103 tests, no abort**, and the exclusion is gone.

This matters more than the Qt pass did. `tests/ui/test_schema_v2.py` is the
81-check conformance suite S10 wrote as schema v2's safety net, and the last
entry described it as "the test the codebase most needed and did not have".
It was in the ignored directory. **It had never run in any documented gate.**
The fast gate goes 398 → 481 passing for that reason alone, at the same ~59 s.

#### A hang now says so

`faulthandler_timeout = 60` in `pytest.ini`. The slowest test in the suite is
about 5 s, so 60 is far outside normal; any test that stops making progress
now dumps every thread's stack and fails the run instead of sitting silently
until somebody kills it. Three sessions were spent on the absence of that
line.

#### Six product defects, all in code that had never been executed

The composites were flagged last time as "the least-proven code in this
commit". That was accurate.

* **`log_stream` raised on every refresh that had content.**
  `widget.moveCursor(widget.textCursor().End)` — `End` is not an instance
  attribute in PySide6. It threw `AttributeError` out of `_poll_model`, which
  is a timer slot, so it also skipped every widget after it in the same tick.
  The controller log, the composite's only user, would have been broken the
  first time a line arrived.
* **PySide's `region_select` never ran its declared command.** The overlay
  was handed the model and assigned `model.focus_area` itself, so
  `set_focus_area` was dead in this renderer while Tk called it. This is
  **PYSIDE-12**, whose audit entry called the modal-behind-the-overlay
  deadlock a *hypothesis* needing a run to confirm — it is confirmed: that
  modal is one of the two that hung this suite.

  **Deleting that modal would have removed a fix, so it did not just go.**
  `known-issues.md` #9 records it as the 2026-09-18 fix for "the drag gave
  zero on-screen confirmation of what was captured" — the overlay used to
  only `print()`. The confirmation is real and the operator needs it; a
  blocking dialog over an always-on-top frameless window was the wrong way
  to give it. `region_select` has declared `model_attr` since S10 and **no
  renderer drew it**: PySide announced the capture in a dialog, and Tk
  showed nothing at all once D-6 deleted the hand-built "Focus Area:" label
  that used to carry it (PYSIDE-8 lists that loss). Both renderers now show
  the region beside the button, refreshed on the poll tick, worded by one
  shared `schema.format_region` — `30x40 at (10, 20)`, or `not set`.
* **PySide's `plot` ignored `data_command`.** It rendered a button that
  opened a CSV file loader, while Tk drew the model's live series on a canvas
  and the Web client drew it on a `<canvas>`. One composite, two meanings.
  The series is drawn inline now (`SeriesPlot`, a `QPainter` polyline like
  Tk's); loading a saved CSV is kept as its own button, since it is a
  different feature and PySide is the only view that has it.
* **An unset dropdown offered "None" as a choice.** Both desktop renderers
  did `str(getattr(model, attr, ""))`, so a `selected_probe_name` of `None`
  became the four-character string `"None"`, was prepended to the option list
  as not-already-present, and came up selected. Choosing it called
  `set_stepper_model("None")`, which matches no probe and returns silently.
  The Web client was the only one that got this right, with an empty
  placeholder. `schema.current_text` is now the single rule and both desktop
  renderers ask it.
* **PySide's dropdown bypassed the D-5 contract**, calling the bound method
  directly where Tk goes through `execute_command`. Same declaration, two
  orderings.
* **Closing a PySide dock killed the device's gamepad, permanently.**
  `QtDynamicView.cleanup()` called `poller.stop_polling()` then
  `poller.close()`. Since D-1 made closing a dock a *hide*,
  and `ControllerPoller.close()` is terminal — `_closed` is never cleared and
  `start_polling` does not reset it — hiding a device and showing it again
  produced a model whose manual mode could never arm, silently, for the rest
  of the session. Ending a device is `teardown()`'s job, reached through the
  manager, which already does it in the right order relative to
  `power_down`. This was a second copy of the teardown policy in a place with
  no business running it; the same method also carried `hasattr` guards for
  four timers that S5 moved into the model, which is dead code implying the
  view still had loops to stop.

Every one of these was found by running code, not by reading it. Five of the
six are in element types that a *conformance* test had already declared
sound — it proved the names resolve, which cannot see a renderer that ignores
the field it resolved.

#### I-7.2 had no harness, and was wearing another invariant's name

`plan.md` names I-7.2 as an S10 exit invariant: "the same schema renders the
same set of controls in Tk, PySide and Web". `tests/ui/test_schema_v2.py`
carried an `I-7.2` heading over its **writability** checks, which are I-7.3.
So the invariant that would have caught `region_select` and `plot` read as
covered while not existing.

It exists now, in `tests/architecture/test_invariants.py`: every type in
`schema.ELEMENT_TYPES` is handled by all three renderer switches, and no
renderer branches on a type the schema cannot emit — which is the shape of
PYSIDE-11's `continue`-first `file_picker` arm. It holds today (10 types,
three renderers, no gaps either way) with a vacuity guard in front of it, so
it is a regression guard rather than a discovery. Behavioural parity per
composite is `tests/ui/test_composites.py`.

#### Tests

* **`tests/ui/test_composites.py`** — new, 10 checks. Each of the four
  composites asserted **twice**, once per desktop renderer, with the same
  expectation, plus a JSON round-trip standing in for the Web client. This is
  the file the resume note asked for; it is also the file that would have
  caught three of the six defects above.
* **Both S10 `known_bad` entries retired, and the quarantine is now empty.**
  `test_pyside_dashboard_sidebar_dock_sync` XPASSed as predicted — schema v2
  makes PYSIDE-7 inexpressible. `test_pyside_redpercent_sync_and_probe_controls`
  did **not** XPASS, and the last entry's prediction that it would was wrong:
  it asserts `view.sync_cbs`, which D-6 deleted, so it had to be re-authored
  against the schema toggles rather than merely un-quarantined.
* **Nine stale tests re-authored, none deleted** — six in `tests/ui` that had
  not run since S0 (view-owned poll loops from before S5, the detached
  controller-log window from before S10, a dock-close that expected the view
  to fabricate a model, `sync_cbs`, a validator test that was exercising the
  `float()`-the-contents inference RC-6 deleted, and a poller callback the
  view no longer installs), plus `test_rotator_system_nan_inf`, which called
  `_move_abs_ui` and now goes through `execute_command` — where D-5's strict
  parse refuses `"inf"` before the command body runs, which is the safety
  property it was written for.
* One new test pins the D-1 poller fix:
  `test_closing_a_view_does_not_close_the_models_poller`.

### 2026-09-20 — S11 closed: the result channel, the bus, and a conftest that was half dead

**The order-dependent prediction was wrong again, and this time in the
useful direction.** The resume note said to read the two remaining
`order_dependent` tests' assertions before believing they would dissolve at
S11, because that prediction had already failed three times. Read: neither
touches error routing. `test_thread_safety_concurrent_requests` asserts a
fast HTTP request finishes in under 0.25 s while a slow one sleeps 0.3 s;
`test_thread_concurrency_setup_and_telemetry` asserts each of four poller
threads got more than five reads. Both are wall-clock assertions about the
HTTP server, and no `ErrorRouter` change could touch either.

Then they both **passed in full composition**, five runs out of five — three
at `7cc5f3c` before any S11 code existed, twice more after. So the fourth
prediction was wrong in its reasoning and accidentally right in its
conclusion: they no longer fail in composition, and nothing in S11 is why.
**They are still marked `order_dependent` and they should stay that way
until somebody knows what changed.** A wall-clock threshold that passes on
an idle machine is not a fixed test; the most likely explanation is that
S10's un-ignoring of `tests/ui` reshuffled what runs alongside them. Do not
retire the marker on the strength of five green runs.

#### `tests/conftest.py` was duplicated end to end, and half of it was dead

Lines 1-543 and 544-1011 were **byte-identical**. Every fixture, every
helper class, `DummyTkWidget`, `DummyTkNotebook`, `ManagedStub`, and
`pytest_collection_modifyitems` itself were each defined twice, and Python
keeps the last binding — so only the second copy was ever live. The first
half is the half you reach by reading the file from the top.

Found by accident: a `bus.clear()` added to `_reset_global_error_routing`
at line 501 had no effect, because the definition at line 988 shadowed it.
The halves were diffed before either was removed; they differed only by
that one edit, so the deletion was safe. A comment now sits at the join.

This is worth more than the line count suggests. Any conftest edit made in
S0 through S10 that happened to land in the first half did nothing, silently,
and the marker logic that decides what `slow`, `qt`, `known_bad` and
`order_dependent` mean was among the duplicated code.

#### What S11 built

* **`src/results.py`** — `Ok` / `Refused(reason)` / `Failed(exc)` /
  `NeedsConfirmation`. `execute_command` always returns one, and `bool()`
  is true only for `Ok`, so call sites that tested the return value kept
  their meaning. S10's `NeedsConfirmation` moved into the family keeping
  its constructor and attribute names, so no S10 dialog code moved.
* **`error_routing.py` is an `EventBus`** — an instance with a module-level
  default, not class state, so a test can build its own. Monotonic ids,
  many subscribers instead of one callback trio, every mutation under a
  lock, and subscriber callbacks invoked **outside** the lock so a
  subscriber that publishes cannot deadlock.
* **The rate limit folds rather than drops.** Keyed on
  `(severity, source, title)` rather than the message text, and a repeat
  inside the window increments `count` on the existing event instead of
  vanishing. The old behaviour left one line in the log for a fault that
  had lasted a minute, with nothing to say it had recurred — the log lied
  about duration. I-8.2 is asserted against a fake clock: twelve reports
  over 55 s produce one event, one notification, and a recorded span.
* **A modal needs `requires_ack`, and only an `error` may set it** —
  `publish` raises otherwise. That makes TEMP-12, the info popup on every
  "Temperature Send", inexpressible rather than merely fixed. Both desktop
  views gained a non-modal event log panel for everything else.
* **`install_exception_hooks()`** replaces three divergent copies.
  `threading.excepthook` existed in the web launcher only;
  `report_callback_exception` nowhere.

#### Two defects neither RC-8 nor the plan named

* **A `None` message made the popup formatter raise while formatting an
  error** — `text = event.message` then `text +=`. The old code had an
  `or ""` guard and the rewrite dropped it. Caught immediately by a
  re-authored test, which is the second time this stage that re-pointing an
  old test at the new API found a live bug instead of just moving an
  assertion. (The first: `/api/errors`.)
* **The Tk manager was re-bound to the dashboard** at `app.py:436` after
  already being bound to the process-lifetime `SetupWindow`. That is
  ERRORS-5 / VIEW-TKINTER-2 / MANAGER-17 in the launcher rather than in the
  manager, and no amount of rewriting `ErrorPopupManager` would have fixed
  it.

#### Tests

Thirteen re-authored, none deleted. Three pinned `set_callbacks` and the
text-keyed dedup; one asserted that a second read of `/api/errors` comes
back empty — ERRORS-2 written down as a requirement; four expected a modal
for every severity; five read `assert ... is False` for a refusal. Nine new
tests cover the bus itself, including 8 threads × 50 publishes asserting
400 unique ids.

**PYSIDE-10 closed without the fix its audit proposed.** That entry asked
for try/except around `_poll_model` and `_route_input` plus dedup in the
excepthook. Neither was added. A repeating timer exception now folds into
one event for 60 s, so it produces one modal rather than one per tick, and
the popup storm is unreachable — the guard the audit wanted would be
protecting against something that can no longer happen.

Verified, all four passes this session: fast gate **505 passed, 1 xfailed**
(~59 s); Qt **35 passed** (~6 s); full sweep with `order_dependent`
included **564 passed, 1 xfailed** (~3 min 18 s). The xfail is I-7.1,
owned by S12.

#### Left open on purpose

* **ERRORS-3 and WEB-9's replay half.** The destructive pop is gone, but a
  tab connecting with `since=0` still pulls the entire history as toasts,
  which is the flood-on-connect the audit describes. The shape of the
  answer is probably that the event log panel carries history and only new
  events raise toasts — that is a UI decision, and it belongs with the rest
  of the web work in **S14**.
* **ERRORS-3's console echo.** Once any subscriber exists the bus stops
  printing. Tk and PySide both print through their own paths; Web does not.
* **ERRORS-9's view-level surfaces.** PySide's CSV-column checks still open
  `QMessageBox` directly, and Web still has no equivalent.
* **Plan item 2 is only half done.** The consequence — no text-keyed dedup
  — is in. The audit of *which* call sites should publish only on a
  transition rather than on every tick was not done; the rate limit is
  currently absorbing that, which is a backstop standing in for a design.

### 2026-09-20 — resume note written at the close of S11 (SUPERSEDED)

> **Stale — kept for the record, not for guidance.** Written when S11 was
> the newest stage. S12, S13 items 5-7, S14 and S15 have all landed since,
> the I-7.1 `xfail` it calls the last one was retired in S12, and its
> ordering advice is four stages out of date. For the current next action,
> read the **last** session-log entry, not this one.

**S11 is done** (`409c859`). S10 is `7cc5f3c`. Working tree clean.

In order:

1. **S12 — composition root and registry events (RC-9).** `app_bootstrap`
   becomes the single composition root all three launchers call, and
   `SystemManager` emits `registered`/`released` so `RedPercentSystem`
   maintains `available_probes` itself. That deletes the Red Percent
   linking block currently copy-pasted at **five** sites, which is what
   retires the I-7.1 `xfail` — the one remaining xfail in the suite.
   **Do not bump the I-7.1 baseline to make it green in the meantime.**
2. Item 3 of S12 — disabled devices are not constructed, and
   `_disabled_in_setup` goes. Note `tests/core/test_view_round1.py` sets
   that attribute on a mock probe explicitly; it will need updating.
3. **S13 (Red Percent)** then picks up the CSV-review question S10 left:
   reviewing a saved run is PySide-only, and whether Tk and Web should
   have it wants a new element type.

**Two standing warnings, both earned.**

* **The `order_dependent` pair passed five times in a row this session,
  three of them before S11 existed.** They are still marked. Read the S11
  log entry before deciding what that means — the honest reading is that
  nobody knows what changed, and a wall-clock threshold passing on an idle
  machine is not a fixed test.
* **Any conftest edit older than S11 may never have run.** The file was
  duplicated end to end and only the second half was live. If a fixture
  seems not to behave the way its code says, that is why — and check
  whether the S0-S10 change you are relying on landed in the dead half.

Two S10 threads still open, unchanged by this stage:

* **PYSIDE-12 is `open (mitigated)`.** The instruction label, crosshair
  cursor and `setFocus`/`activateWindow` are not done, and the Linux
  transparency issue needs a bench run.
* **Reviewing a saved run from a CSV is PySide-only** — see S13 above.

### Still waiting on the owner

1. **D-7** — firmware protocol v2. Recommendation: **adopt**. Requires
   reflashing every board, so it is S16, at the bench, owner-only. Nothing
   earlier blocks on it.

### For whoever picks this up

**RC-6's script-path coverage still does not exist.** The test that appeared
to provide it was passing on a mock artefact; S9 recorded that it had to be
written fresh, and it has not been. It is the one piece of S9 outstanding.

Do not trust this file's older claims about *why* the suite was flaky, or
about the Qt abort. Three separate diagnoses in this file — process-global
`ErrorRouter` callbacks, an untracked script thread, and a native Qt SIGABRT
— were each confidently recorded and each wrong. The pattern in all three:
the symptom was read against the audit's list of plausible architectural
suspects instead of against what the run actually produced. Two were mock
wiring; the third was a modal dialog. **Read the failure, then the audit.**

### 2026-09-20 — S12 closed: one composition root, and the registry learned to talk

**All three RC-9 items landed.** `app_bootstrap` is now the single place a
system is built: `discover_ports` / `discover_controllers` / `normalize_config`
/ `validate_assignment` / `build_models` / `link_models`, in that order, called
identically by all three launchers. `SystemManager` grew `subscribe` /
`unsubscribe` / `_emit` with two events, `registered` and `released`, and
`RedPercentSystem` now maintains `available_probes` itself by reacting to them
instead of being hand-wired by whichever launcher happened to build it.
`_disabled_in_setup` is deleted; disabled devices are simply not constructed.

**Two corrections to what the S11 resume note told the next session.**

1. It said the Red Percent linking block was copy-pasted at **five** sites.
   It was at **three** (`app.py:423`, `app.py:776`, `web_adapter.py:212`) —
   the PySide site went away in an earlier stage and the note was never
   recited. The audit text it derives from (VIEW-TKINTER-17) is likewise
   counting a `pyside/view.py:904` site that no longer exists.
2. It said S12 **retires the I-7.1 `xfail`**. It does not. Of I-7.1's six
   remaining hits, four are the web linking block S12 deletes, but two are
   `views/pyside/view.py:892` and `:925` (`stop_monitoring` and
   `has_unsaved_data`), which are **S13** work. I-7.1 is still `xfail` after
   this stage and the baseline was **not** bumped, per that note's own
   instruction. It should retire at S13; if it does not, the remaining hits
   are a third thing nobody has counted yet.

**Decisions taken inside the stage, recorded so they are not re-litigated.**

* **`available_probes` tracks registration, not visibility.** A *hidden*
  probe (D-1) is still live, still polling, and still a valid position
  source, so `hide()` emits nothing. Only `release()` and `shutdown_all()`
  emit `released`.
* **A later probe does not steal a live selection.** `_reselect` keeps the
  current selection whenever it is still registered. Re-pointing the
  position source mid-run would splice two probes' coordinates into one
  data log without saying so. The first-probe-in-registry-order default is
  preserved because the launchers register in `devices.names()` order —
  `test_a_launch_selects_the_first_probe_in_registry_order` pins that, and
  `test_a_later_probe_does_not_steal_a_live_selection` pins the other half.
* **`normalize_config` derives `mode`** from the resolved port
  (`"simulation"` iff `SIM`) rather than carrying whatever the frontend
  sent. Desktop sent `""` and web sent `"hardware"`/`"simulation"` for the
  same system, which is what made I-9.2 fail first time. Nothing downstream
  reads config `mode`; deriving it is what makes the two shapes compare
  equal instead of relaxing the assertion.
* **`build_models` owns its claims dict** — signature is now
  `build_models(active_configs, manager=None, claims=None)`. The
  caller-owned dict was MANAGER-16 / VIEW-TKINTER-6. This broke four call
  sites loudly, which was the point.
* **`validate_assignment` lost its `"Virtual" not in ctrl` exemption.** It
  existed only to let the fabricated placeholder controllers share an id.
  Those are gone, so the exemption was a hole that let two real devices
  claim one controller. `None`/`N/A` remain exempt.
* **The web sidebar's greyed-out disabled-device affordance is gone by
  design.** The server no longer builds disabled devices, so they are not
  in `this.devices` and there is nothing to grey. `renderSidebar` no longer
  partitions on `_disabled` and `get_devices` no longer sets it.
* **Controller enumeration is genuinely in-process** through
  `InputService`, so the "`python3` vs `sys.executable`" question in WEB-15
  does not arise at all — there is no interpreter to choose. `gamepad.py`'s
  four unlocked `pygame.joystick.get_count()` reads now route through
  `input_service.count()`, which keeps the 200 Hz poll tick at one cheap
  SDL read under the one lock (RC-13).

**Not done, carried forward.** WEB-15's per-port device-type autodetect for
the web wizard is still missing and needs an async scan flow, not a loop in
the `scan_hardware` GET. `scan_hardware`'s docstring says so; the ledger row
says so; it belongs to S14.

**Verification.** Fast gate 532 passed / 1 xfailed (from 505 at session
start), slow 57 passed, qt 35 passed. The one xfail is I-7.1. The
three-pass sweep was **skipped** at the owner's instruction — it was costing
more wall-clock than it was buying, and the three gates cover the same
tests.

### 2026-09-20 — ledger reconciliation: the backlog was overstated, and S8 was understated

**Why.** Every stage row S0–S12 says `done`, most say "All N items", and 66
of their ledger rows still said `open`. The ledger drifts one way: fixes
land, rows are not flipped, because flipping needs a verified test name and
that is slower than moving on. Planning off those counts means re-deriving
state that is already in the code.

**Method.** Six read-only haiku subagents, split by audit file, each writing
verdicts to scratch — none of them touched this file, so write conflicts
were impossible rather than merely coordinated. A second wave of two found
the test names. Every `closed` below was verified here with
`grep -rn "def <name>" tests/` before the row moved. The procedure and the
prompts are now the `reconcile-ledger` skill.

**Result: 30 rows closed, 35 genuinely open, 3 unsure.** Roughly half the
apparent backlog was bookkeeping.

**The finding that matters most, and it goes the other way.** S8's row said
"I-5.2 holds". It holds **for the probes only** —
`test_emergency_stop_returns_within_100ms_against_a_stalled_transport`
builds a probe, and nothing exercises the rotator or the temperature
controller. `RotatorSystem.emergency_stop` calls `stop()` calls
`smc.sendcmd('ST')`, which takes `_serial_lock` — so a FULL STOP on the
rotator still blocks behind any in-flight move, which is exactly the defect
S8 existed to remove. `TEMP-7` (full_stop racing `send_settings`) and
`DC-18` (unguarded emergency `k` write) are the same shape. **These are
safety-path items in a stage marked done.** They should be next; the
standing rule is safety paths before feature paths.

**Corrections to individual verdicts, so the method is judged honestly.**

* The agents' own tallies disagreed with their own lists in three of six
  cases. Read the verdict files, never the summary.
* 19 of 28 first-wave `CLOSED` verdicts cited a test **file** rather than a
  test **name**. That is why there is a second wave.
* One agent cited `progress.md` as corroboration — circular, since that is
  the artefact under audit. The prompt now forbids it.
* **DC-16 was a false OPEN.** `pyside/view.py` has exactly one `QTimer`;
  the position, status and manual-input timers are gone, and
  `test_view_keeps_one_render_tick_and_no_device_loops` pins it. Closed.
* **GAMEPAD-5 was a false CLOSED.** It is four sub-items. The `None` entry
  and claim filtering are done; live refresh and rebind-after-disconnect are
  not. Left `open (partly closed)`.
* **GAMEPAD-16 does not close by test.** It closes by **D-12** — the owner
  ruled the cadences deliberately unequal. A test there would pin a number
  the owner may retune. Recorded as a verification note instead.

**Fixed in passing.** GAMEPAD-6 / DC-19 — the web dropdown's
`<option value="">` placeholder was selectable, and picking it dispatched
`set_controller("")`, silently unbinding a live controller. Assigned to S10
and survived it. Now `disabled hidden`, with the handler refusing a blank
value, and two structural tests.

**Also added.** A project `CLAUDE.md`, and three skills under
`.claude/skills/` — `verify`, `stage-close`, `reconcile-ledger` — encoding
what every session so far re-derived from `plan.md` and `testing.md` by
hand.

---

### 2026-09-20 — the S8 safety residue: the stop contract held for one subsystem out of three

S8 built a real emergency-stop contract — latch before any I/O, dispatch the
hardware write on a worker, join against a bound so a wedged transport cannot
hold the caller, and take the transport lock with a short timeout and force the
byte through on failure. It was implemented for the probes. The ledger recorded
that as "I-5.2 holds for the probes only", which undersold it: the rotator and
the heater did not have a weaker version of the contract, they had none.

**ROTATOR-8.** `RotatorSystem` had no `_estop` at all, and `stop()` called
`SMC100.stop()`, which is `sendcmd('ST')` under `_serial_lock` — the same lock a
position poll holds for a whole transaction. A FULL STOP issued from the UI
thread waited on the poll. The old-code check for this did not produce a test
failure; it **deadlocked and had to be killed by timeout**, with the repo left
stashed until a monitor caught it. That is a stronger result than red tests.

**ROTATOR-4** turned out to be arithmetic, not staleness. `_current_position()`
returned `0.0` when the position was unknown, and a relative target computed
from the last *polled* position cannot see a move already in flight. Five +4°
clicks from 20° each looked like a move to ≤26°, so the soft limit never
prompted and the stage arrived at 40°. Fixed by tracking `_commanded_target` —
the running sum of accepted moves, committed *before* dispatch — and by making
"unknown" a state the guard can see rather than a silent zero.

**TEMP-7** was a textbook check-then-act. `send_settings` tested `_estop` at the
top, then did ~40 lines of validation and frame-building, then wrote. A FULL
STOP landing in that window was overwritten by the frame already in flight: the
operator pressed stop, the heater went back to setpoint, and nothing said so.
The latch is now re-checked inside the write lock immediately before the write,
which is what actually orders the two.

**DC-18 was already fixed** and needed tests, not a change. `power_down` has
gone through the locked priority path since S8, and the four racing booleans the
audit named died in S7 when `_mode`/`_mode_lock` replaced them. This is trap #1
from `CLAUDE.md` in its purest form — the audit text described code that no
longer exists. Closing it on inspection alone would have been the right call for
the wrong reason, so it is pinned with three tests including a structural scan
for model writes that bypass the transport lock.

Every fix reuses the S8 pattern verbatim rather than inventing a second one;
`PRIORITY_LOCK_TIMEOUT` and `ESTOP_RETURN_BUDGET` now appear with the same
values and the same justification in all three subsystems.

Two items were deliberately *not* decided here, and go to the owner:

- **TEMP-9** — the history arrays are dead and the firmware comment says "PLOT
  THIS". The two fix directions are "add a temperature plot to all three views"
  or "delete the arrays". That is a product call, not a repair.
- **WEB-19 / D-8** — the client-liveness FULL STOP tier needs the watchdog to
  live in `src/model/probes.py`, so it spans model and web and belongs in a
  dedicated pass rather than either parallel worktree.

Gate: `547 passed, 94 deselected, 1 xfailed`, then 55 in
`tests/core/test_transport_truth.py` after the TEMP-7 tests landed.

### 2026-09-20 — S14 and S15 in parallel worktrees, and what the qt pass caught

S14 and S15 were run simultaneously in two git worktrees (`s14-web`,
`s15-local-ok`) by two agents, while the lead took the S8 safety residue on
`mvc-refactor`. Thirteen findings moved in one wall-clock pass.

**The partition is the whole technique.** Conflicts were prevented *by
construction*, not by merging: each agent got an exclusive write set declared
up front, plus a deny-list naming the owner of every forbidden file. Shared
files — `docs/**`, `progress.md`, `tests/architecture/test_invariants.py` —
were lead-only, and agents wrote a handoff to scratch instead of touching
them. Cross-cutting findings were pulled out *before* partitioning: WEB-16
shares `web_server.py` with WEB-14/21 so it moved S15 -> S14; REDPERCENT-20
spans two write sets so it was split in half; WEB-19 needs `probes.py` so it
was deferred entirely; TEMP-9 needs an owner call so it left the scope.

Result: **zero file-level collisions** between the two branches or with the
lead's own commit, confirmed by intersecting `git diff --name-only` before
merging. Both merges were clean. All 36 claimed test names were verified to
exist with `grep -rn "def <name>"` — none were wrong, but the check is what
makes the claim worth anything.

**The qt pass is where this nearly went wrong.** Agent B correctly refused to
run the qt pass inside its session (a native Qt `SIGABRT` kills pytest and
discards every already-passed result) and listed 11 qt-marked tests under
`## UNVERIFIED`. Run at merge time, **two of them failed** — and both were
bad tests, not bad code:

- `test_last_added_dock_is_none_once_every_dock_is_closed` closed *one* of
  two docks and asserted as though all were closed. `DashboardWindow`
  auto-opens a dock for every device the manager already holds, and the
  fixture registers two probes, so the surviving dock was correctly kept.
  The production fallback was right; the test's premise was wrong.
- `test_draw_plot_appends_probe_metadata_to_the_title` could never have
  worked. `tests/conftest.py` replaces `matplotlib` *itself* with a
  MagicMock for the whole suite, so `for ax in fig.axes` iterates **empty** —
  the test would have passed against code that did nothing. It now injects a
  controllable figure and asserts on the title the code actually sets.

The lesson is not "agents write bad tests". It is that **a test which cannot
be run is not evidence**, and this repo has a whole marker class of them. An
agent that cannot run the qt pass must hand its qt tests back as unverified,
and the merging lead must actually run them. That is now written into the
`verify` skill rather than left to memory.

Second-order finding: the harness mocks `matplotlib`, `PIL`, `mss`, `serial`
and the Qt backends at `tests/conftest.py`. Any test asserting on a real
object from one of those is silently vacuous. Worth an invariant later.

Gates after both merges and the two test fixes: fast **576 passed, 105
deselected, 1 xfailed**; qt **46 passed**.

Deliberately still open, both owner calls: **TEMP-9** (plot the temperature
history across all three views, or delete the dead arrays) and **WEB-19 /
D-8** (the client-liveness FULL STOP tier, spanning `probes.py` and the web
adapter).

### 2026-09-20 — tooling: two skills, an agent definition, and the stop pattern written down

Codifies the two procedures that consumed the most reasoning this session and
that each went wrong at least once. Nothing here changes `src/`.

- **`fix-a-finding`** — the per-finding loop, starting with the question that
  actually matters: *is this still true?* DC-18 was already fixed and agent B
  hit the same staleness three more times. Also carries the safe
  prove-the-defect recipe (scratch copy, `git checkout --`, `timeout`, restore
  chained with `;`) that replaces `git stash` — the stash variant hung and left
  the repo stashed until a monitor caught it.
- **`parallel-stage`** + `brief-template.md` + `partition-check.sh` — the
  write-set partition that produced zero collisions across S14, S15 and the
  lead's own commit. The script reproduces that check and also fails an agent
  that touched a lead-only file.
- **`.claude/agents/worktree-fixer.md`** — the first agent definition. Hoists
  ~100 invariant lines out of every hand-written brief so briefs carry only
  write set and findings.
- **`docs/architecture/safety-pattern.md`** — the emergency-stop contract, so
  the fourth subsystem matches the first three. Records why the priority write
  is safe (single idempotent frames only, never a motion command) and names
  the six sites where its constants are duplicated.
- **`verify`** gained two rules earned today: never read a pytest result
  through `| tail` (the exit code is the pipe's), and qt-marked tests that were
  never run are `## UNVERIFIED`, not `closed`. It also names the
  mocked-module trap — `matplotlib`, `PIL`, `mss`, `serial` and the Qt backends
  are MagicMock suite-wide, and iterating a MagicMock yields nothing, so a
  loop-based assertion over one passes vacuously.

Verified: `partition-check.sh` re-run against the two real branches reproduces
the clean result; `fix-a-finding`'s preflight dry-run on GAMEPAD-17's
`probes.py` sub-item reached a verdict (construct present, the audit's
dead-path reasoning now rests on code S15 changed — needs its own pass)
without editing anything. Gate unchanged at 576 passed.

### 2026-09-20 — three owner-added Red Percent findings, and a generator that reverted history

**Owner instruction:** a monitoring run has to be joinable to the physical
trial that produced it. That is not an audit finding — the audit asked
whether the code does what it claims, and on this point the code makes no
claim. Recorded as scope, through the same ledger, with provenance attached.

- **REDPERCENT-21** — a run has no identity and no addressable output
  location. `autosave_log` builds a **bare relative path**, so an unattended
  stop writes into whatever directory the launcher started in, which differs
  between `run.sh`, `run_macos.sh` and the web server. The CSV-to-trial
  mapping exists only in the operator's memory.
- **REDPERCENT-22** — the `# Metadata` block in `save_to_csv` is not a
  comment convention, it is four data rows before the header. A default
  `pandas.read_csv` takes `# Metadata` as the header row; the workaround is
  `skiprows=4`, a magic number that breaks when a field is added. Separately,
  the values that make red percent *mean* anything — `baseline_red`, the
  focus-area rectangle and its pixel size, the `detect_red` threshold, the
  sample cadence, the start/stop times — are written nowhere at all.
- **REDPERCENT-23** — scope addition, not a repair: there is nowhere to
  record the facts only the operator knows (specimen, consumable, stage
  position, intended setpoint), so they live on paper and are joined to the
  data by filename. Proposed as a `Param`-declared annotation table rather
  than fixed columns, so the next experiment does not need a code change.
  Intended and actual stay separate fields by construction.

All three are RC-11, so the ledger placed them in **S13**, which is correct —
each one needs `MonitoringRun` to own a configuration snapshot first. Added
as plan items 5-7 with that dependency written down. Item 6 also picks up
`probe_tilt_angle`, declared `float` in `PARAMS` and initialized to `""`.

**The ledger generator was reverting a recorded decision.** Regenerating
flipped **WEB-16** from S14 back to S15, because it computes the stage from
the RC mapping (`LOCAL-OK` -> S15) and knows nothing about the reassignment
the S14/S15 partition made — WEB-16 shares `web_server.py` with WEB-14/21 and
could not sit in the S15 write set. The row's own note said "Moved S15 ->
S14" while its Stage cell said S15. Fixed in `gen_ledger.py` with a
`STAGE_OVERRIDE` table rather than by editing the row, so the next
regeneration is idempotent. Anyone reassigning a finding mid-flight must add
it there.

**A caught process error, recorded because the rule already existed.** The
first gate run was backgrounded as `pytest ... | tail -5`, and reported exit
0 — which was `tail`'s exit code. The `verify` skill names this exact trap.
Re-run unpiped: **576 passed, 105 deselected, 1 xfailed**, exit 0, matching
the baseline. A rule written down is not the same as a rule followed; the
pipe went in because the output is noisy with teardown prints. Redirect to a
file and grep the file.

Ledger is now **216 rows** — 213 audited plus these three. Nothing in `src/`
changed in this session.

**Next action:** S13. It is now the only `todo` stage before the owner-only
S16, and it carries both the RC-11 repair and the three new items.

### 2026-09-20 — reconciliation found nothing to close, and S13 items 5-7 landed early

Two things this session, both off the plan's order and both deliberate.

**The reconciliation came back empty, which is the useful answer.** 33 rows
were `open` while their stage said `done` — the drift pattern this branch has
had all along. Four read-only haiku agents audited all 33 against the current
tree, partitioned by audit file so no file was split.

Result: **1 CLOSED claimed, 0 applied.** The 2026-09-20 pass had already
harvested the closable rows; what is left is real work, not drift. **The
backlog is not inflated any more, and the next fix wave can be briefed
straight off it** — which is what the reconciliation was for.

The single CLOSED was **WEB-20**, and it did not survive verification. The
agent's evidence was that `active_models` reads are now under
`with self._state_lock:`. That is true and not the finding: `_state_lock` is
the *adapter's* lock, and the audit names `SystemManager.lock`. Holding a
different lock serializes nothing against the manager. `dispatch_command`
does use `get_active_models_snapshot` (web_adapter.py:393), but
`resolve_options`:483, `set_device_attribute`:521 and :620 still read the
live dict, and the generation counter the finding asks for exists nowhere —
the four `409`s in that file are the scan and re-setup single-flights.
Downgraded to `open (partly closed: ...)`, not closed.

This is the asymmetry the `reconcile-ledger` skill is built around, hit for
real: a false OPEN costs one verification, a false CLOSED erases a defect
permanently. Four of four agents' OPEN verdicts spot-checked true
(`serial.py:148`'s live `time.sleep(1.5)`, `temperature_system.py:301-308`'s
double bare-except with no join, `app.js:1126`, `SetupWindow` with no
`closeEvent`). **The one CLOSED was the one that was wrong.**

One agent died mid-run on a rate limit and was relaunched with its findings
intact. Worth knowing the pass is resumable per write-set.

**S13 items 5-7 landed before items 1-4.** Out of plan order, on owner
instruction, because a bench run needs them this weekend. They are the
owner-added REDPERCENT-21/22/23 recorded earlier today.

- **Run identity.** `run_id` + `output_root`, the latter resolved once at
  import from `TRANSFER_STAGE_DATA_ROOT` or `~/transfer-stage-runs` — never
  from CWD, which is what made the three launchers disagree. Artifacts land
  in `output_root/<run_id>/` as `<run_id>_position.csv` and
  `<run_id>_station_meta.json`, so a file stays self-describing once moved.
  A blank `run_id` falls back to a timestamp slug rather than producing an
  unnamed run.
- **The CSV is a rectangle.** The `# Metadata` rows are gone; configuration
  is in the sidecar, including the things that make red percent mean
  anything — `baseline_red`, the focus area *with its pixel size*, the
  `detect_red` thresholds (now the named `RED_THRESHOLD`), sample count and
  start/stop stamps.
- **Annotation is a table, not attributes.** `ANNOTATION_FIELDS` is five
  `Param`s; the schema renders them in all three views (D-6) and the
  properties that back them are *generated* from the table, because a
  hand-written pair per field is how the table and the attributes drift
  apart. Intended and actual never share a key.

**Two things worth the next reader's attention.**

1. **The metadata block was load-bearing.** `plot_data.parse_red_percent_csv`
   parses it back for in-app run review, so deleting it outright would have
   broken reading every file already on disk. `load_red_percent_run` now
   reads the sidecar when present and falls back to the `#` block when not.
   Old files keep working; that is pinned by a test, not by intention.
2. **`test_csv_metadata_injection` was re-authored, not deleted.** It
   asserted that `save_to_csv` prepends the block. It is now
   `test_csv_carries_no_metadata_block` and asserts the inverse, so it is the
   test that fails if the block ever comes back.

`probe_tilt_angle` was declared `float` in `PARAMS` and initialized to `""`;
fixed with item 6.

Gates: fast **589 passed, 105 deselected, 1 xfailed** (was 576; +13 new).
qt **46 passed**. Both run unpiped, exit code read directly.

**Next action:** S13 items 1-4 — `MonitoringRun` itself, which is the actual
RC-11 repair. Items 5-7 deliberately did not build it; they hang off the
model's existing fields, so `MonitoringRun` must take over `run_id`,
`output_root`, `run_annotations` and the two timestamps when it lands, rather
than leaving a second snapshot beside its own. After that, the fix wave: the
partition is drawn (transport / rotator / input as three worktrees, views and
docs lead-only) and the backlog behind it is now known to be real.

### 2026-09-20 — doc audit before the fix wave, and the partition redrawn from the citation map

Audited this file against the tree before launching more worktrees. Three
things were checked and two were wrong.

**Right:** the ledger is **216 rows**, 156 closed, 60 open, and
`gen_ledger.py` regeneration is a byte-for-byte no-op — the `STAGE_OVERRIDE`
fix holds. The Stage status table matches the ledger.

**Wrong 1 — the cold-resume procedure lied about its own gate.** Step 5 said
"about 28 seconds" and pointed at the three-pass full sweep. The gate has
been ~65 s since S10 un-excluded `tests/ui`, and the sweep was **retired by
owner instruction earlier today**. A fresh agent following step 5 would have
budgeted a third of the real time and then run a sweep that no longer exists
as a contract. Fixed, with the measured number and the commit it was
measured at.

**Wrong 2 — `testing.md`'s baseline trail stopped at S11.** Four stages have
landed since (S12, S14, S15, S13 items 5-7) and none was recorded, so the
newest number in the testing doc was 505 while the tree runs 589. Appended
the current baseline, the sweep retirement *and the reason for it*, and the
`| tail` exit-code trap that produced a false green in this project.

Neither was a code defect. Both were the kind of drift that only costs
something when someone trusts the document — which is the whole job this
file has.

**The partition was redrawn, and the first draft was wrong.** The earlier
sketch (transport / rotator / input) assumed the rotator findings were
model-only. They are not: ROTATOR-6's fix direction explicitly requires
removing PySide's duplicate `status_timer` poll, so it spans
`rotator_system.py` and `pyside/view.py`. Three others do the same.

Redrew it mechanically instead of by intuition — map every open finding to
every source file its audit entry cites, then partition the *files* and let
each finding fall to the agent owning all of its files. Findings spanning
two owners go to the lead by construction, so there is nothing to negotiate.

The structural result is worth recording: **of 60 open findings, 42 cite a
GUI view file or `redpercent_system.py`.** The remaining backlog is
view-bound. Parallel worktrees cannot reach most of it, and no partition
will change that — the two view files are cited by 28 open findings *each*,
because the same defect is usually present in both. This is a real ceiling
on how much of S13-S15's residue can be parallelized, and it means the
back half of this branch is lead work whether or not agents are available.

Eighteen findings are reachable, in three balanced, provably disjoint write
sets: **A-input** (7, `gamepad.py`), **B-transport** (6, `app.py`,
`app_bootstrap.py`, `serial.py`, `probes.py`, `system_manager.py`,
`error_routing.py`), **C-web** (5, `views/web/**` plus `smc100.py`,
`rotator_system.py`, `temperature_system.py`, `numeric.py`).

A 15-finding single-agent variant was computed and **rejected**: it reached
24 findings by giving one agent the whole web layer plus transport, and the
extra six were all `probes.py` <-> `web_adapter.py` coupling findings — the
cross-layer kind where an agent starts making design decisions that are not
its to make.

Baseline for the wave: `0e5ba73`, fast gate **589 passed, 1 xfailed**, exit
0, 66 s, read unpiped.

**Next action:** launch the three worktrees, then S13 items 1-4.

### 2026-09-20 — fix wave, worktrees A and C: nine rows moved, and two surprises

Two of the three worktrees are merged and verified. **Fast gate 621 passed,
1 xfailed, exit 0; qt 46 passed, exit 0**, both read unpiped, both run by
the lead on the merge result rather than taken from a handoff.

**A (`fix-input`, `gamepad.py`) — 4 commits.** GAMEPAD-7, 19 and 21 closed,
GAMEPAD-20 partly. Verified before merge: write set respected, all ten test
names present, and every claimed pre-fix failure reproduced independently
against the base file (5 of 6, then 3 of 4; the other two are named
regression guards that pass both ways, as they should).

**C (`fix-web`) — 5 commits.** ROTATOR-7, ROTATOR-11 and ERRORS-12 closed;
TEMP-11 and ROTATOR-13 partly. ROTATOR-7's proof is the strongest in the
wave: against the pre-fix adapter, STOP, emergency STOP *and* `/api/state`
all blocked behind a slow command. A STOP that queues is a STOP that does
not happen.

**I briefed four owner-only findings by mistake.** GAMEPAD-11, 12, 13 and 14
are RC-12 / S16 — "Never delegated. The owner does this at the bench" — and
I put them in A's brief because I drew the partition from the file-citation
map without reading the Stage column. Caught after launch and retracted
mid-flight; verified on merge that nothing was written for any of the four
(the wrapper classes and `_apply_deadzones` are byte-identical to base, and
all six source hunks land in `ControllerPoller`). The mechanical partition
was right about *files* and silent about *authority*, which is the failure
mode to remember: a write-set map cannot tell you who is allowed to decide.

**GAMEPAD-21 — a closed fix that did not reach the whole surface.** A found
it while working GAMEPAD-7. `set_controller` tore polling down and resumed
only `if success and self.gui_root and not self.is_polling`; `gui_root` is
assigned only when `start_polling` is handed a `gui`, and the sole live
caller in `src/` is `probes.py:882`, which passes `None` — because S5 moved
poller startup into the model so the Web frontend would poll at all. I first
recorded it as Web-only and medium. That was wrong, and grepping every
`start_polling` caller is what corrected it: `gui_root` is `None` on **all
three frontends**, so every controller swap in the application killed input
until restart, leaving manual mode inert with the coils energised and the
watchdog skipped. Re-recorded high, all three views. It is the same end
state GAMEPAD-1 was closed on, reached by a different route after that row
was closed — trap #2 of CLAUDE.md, in its purest form.

**ROTATOR-13 exposed a hole in the partition itself.** Its remaining half
needs `src/views/web/static/js/app.js`, which is covered by *no* write set:
my C brief said `src/views/web/**` in prose but enumerated only the three
`.py` files. So the web half is unassigned rather than deferred. Any future
partition drawn from Python-file citations has this blind spot — the audit
cites `app.js` and my extractor only ever resolved `.py` basenames.

**TEMP-11 is blocked on a primitive, not on difficulty.** The heater-off
frame cannot be forced out before the port closes because the transport
exposes no `flush()` and I-2.3 forbids the model touching `.ser`. Verified
both halves myself. Routed to B, which owns `serial.py`.

**Next action:** merge B when it lands, wire TEMP-11's two halves together,
then S13 items 1-4 — `MonitoringRun` itself, still the actual RC-11 repair
and still untouched.

### 2026-09-20 — worktree B merged: the wave closes at 12 of 19, and two defects the audit never had

All three worktrees are merged. **Fast gate 715 passed, 1 xfailed, exit 0;
qt 48 passed, exit 0**, both run by the lead on the merge result.

**B (`fix-transport`) — 6 commits, 73 tests.** STEPPER-9, SERIAL-17 and
MANAGER-20 closed; SERIAL-10 and REDPERCENT-4 partly; TEMP-10 left `open`.

**B's two qt tests passed.** That is worth recording because last wave two
of eleven did not, and both were wrong. These are not: they lift the real
`ScannerThread` and `SetupWindow` out of `app.py`'s syntax tree and run them
over real Qt base classes, so what executes is the shipped code rather than
a replica of it. The technique is sound and worth reusing — both classes are
defined *inside* `run_pyside_app()` and cannot be imported at all otherwise.

**I checked MANAGER-20's evidence and it is thinner than the headline.** Of
its 19 tests, 17 are AST-structural — they parse `app.py` and assert the
pieces refer to each other. The file's own docstring says so plainly, but a
row reading `closed (19 tests)` would have implied behaviour that those 17
do not cover. The row now names the 2 qt tests and the 6 executing ones as
the evidence, and calls the other 17 what they are: a regression guard
against a silent deletion.

**TEMP-10 came back `open`, not `partly`, and that was the right answer.**
The agent changed nothing and declined to round up. Its report also caught
something the ledger had wrong in the other direction: `connection_status`
already exists at `temperature_system.py:28-34`, so one part of TEMP-10 was
done and uncredited. Recorded.

**SERIAL-20 — a defect in the agent's own file, found while fixing another.**
`SimulatedPort` had no `flush()`, and `send_manual_mode_command` calls
`self.ser.flush()` on every frame under the transport lock. So in simulator
mode — the configuration the three frontends are normally developed in —
every manual-mode frame raised `AttributeError` inside the write path and
reached the operator as a "Serial Write Error", at frame rate. Verified by
grepping every `.flush()` caller at both `0e11280` and `f188804`. It is the
same shape as SERIAL-9: a method the double forgot, which quietly turns SIM
into a *different* code path — the one thing that class exists to prevent.
The audit entry proposes the durable fix (assert the double answers every
attribute the transport calls); that is **not** done, and the row says so.

**TEMP-11 is closed, and neither worktree could have closed it.** C wrote
the model half against a transport with no flush; B added a bounded
`flush()` with no caller. I wrote the three seam tests and joined them here:
write, drain, then close, with an undrained frame reported under the same
obligation as a failed write, and a transport lacking `flush()` treated as
not-an-error. The ordering assertion is the load-bearing one — a drain after
the close protects nothing.

**Wave totals.** 19 findings briefed (18 planned, plus TEMP-11's transport
half routed mid-flight). **12 closed, 5 partly, 1 open, 1 retracted** —
GAMEPAD-20's remainder, ROTATOR-13's, SERIAL-10's (D-7, owner-only),
REDPERCENT-4's (S13's own file) and TEMP-10 in full. Two new findings the
2026-09-19 audit did not contain. Ledger is 218 rows, 167 closed, 51 open.

**Next action:** S13 items 1-4 — `MonitoringRun`. Still the actual RC-11
repair, still untouched, and REDPERCENT-4's remainder is now waiting on it
too. When it lands it must take over `run_id`, `output_root`,
`run_annotations` and the two timestamps from items 5-7 rather than opening
a second snapshot beside them.

### 2026-09-20 — ledger audited clean, trail hygiene, and wave 3 launched

**The ledger holds up under audit.** Three checks before planning off it:
218 rows as claimed; 51 open / 167 closed, matching the wave-close count
exactly; and **every one of the 181 distinct test names cited in the ledger
exists in `tests/`** (`grep -rn "def <name>" tests/` over all of them). No
phantom citations. The fast gate re-run on a clean tree at `5871eea`:
**715 passed, 1 xfailed, exit 0, 71 s.**

**Four trail defects fixed, none of them a false claim in the record.**

1. **The cold-resume baseline was stale** — step 5 said 589 passing at
   `0e5ba73`, about 65 s. It is 715 passing and 1 xfailed at `5871eea`,
   about 71 s. A fresh agent was being told to expect a number 126 tests
   short of the truth, which makes a genuine regression look like drift.
2. **Eight stage rows had blank Commit cells.** The protocol says to leave
   the cell blank in the stage's own commit and fill it in the *next* one;
   the fill-in half has been skipped since S1. All eight are recovered and
   verified with `git merge-base --is-ancestor` before being written:
   S1 `86027f5`, S2 `a7ed3f5`, S3 `f69586c`, S4 `2854d3e`, S8 `728f714`,
   S13 `0e5ba73` (items 5-7 only, stage still in progress), S14 and S15 both
   `a4f3a75`. S8's cell names the stage's own last commit; its safety
   residue landed later in the fix wave and the Notes cell already says so.
3. **`### Resume here (next session)` was four stages stale** and its title
   read as current. Written at the close of S11, it names S12 as next and
   calls I-7.1 the last remaining `xfail` — S12 retired that. Retitled to
   its date, marked SUPERSEDED, and given a pointer to the last log entry.
   Kept rather than deleted: this file's value is that it is not retconned.
4. **Five merged branches were still on the clock** — `fix-input`,
   `fix-web`, `fix-transport`, `s14-web`, `s15-local-ok`, all fully merged
   into `mvc-refactor`, worktrees long removed. Deleted, so that
   `git worktree list` and `git branch` describe the work actually in
   flight rather than the work that finished yesterday.

**Wave 3 is running in three worktrees**, partitioned by file rather than by
subsystem — the axis the last wave proved, because `probes.py`,
`web_adapter.py`, `app.js` and the two GUI view files are each wanted by six
or more open findings and a theme-shaped partition collides on all five.

| Worktree | Owns | Findings |
|---|---|---|
| `fix-gui` | the two desktop view files | VIEW-TKINTER-17, VIEW-TKINTER-18, PYSIDE-12, ERRORS-9 |
| `fix-webui` | `views/web/**` + `error_routing.py` | WEB-20, DC-11, WEB-7, WEB-11, ERRORS-3, ROTATOR-13 (web half), WEB-22 |
| `fix-thermal-rotator` | `temperature_system.py`, `rotator_system.py`, `smc100.py` | TEMP-2, TEMP-10, ROTATOR-9, ROTATOR-15 |

The lead holds `redpercent_system.py`, `plot_data.py`, `schema.py`, `docs/**`
and `tests/architecture/test_invariants.py`, and is taking **S13 items 1-4,
`MonitoringRun`** — the last real RC-11 repair, and the gate on five other
Red Percent rows.

**`src/model/probes.py` is deliberately unowned this wave.** DC-6,
STEPPER-11, ERRORS-7, WEB-19 and GAMEPAD-17's remainder all need it *plus* a
view file or the web adapter, so assigning it now would collide with two of
the three worktrees. It becomes a clean single-owner wave once A and B land.

**Deferred behind S13 by semantics, not by files:** REDPERCENT-6, PYSIDE-4,
REDPERCENT-13, 17, 18 and 19. Each touches a save or render path that
`MonitoringRun` is about to redefine; fixing them against the current shape
would produce work that has to be redone.

**The agents run on smaller weights this wave, by owner instruction, to hold
context.** That raises the error rate on exactly the failure this project
has already seen twice — a test that passes against code that does nothing,
and a row reported `closed` that was not. The compensating gate is unchanged
and entirely lead-side: write-set compliance via `partition-check.sh`, a
`grep -rn "def <name>"` existence check on every cited test name, and the qt
pass run by the lead. **No row flips to `closed` on an agent's report alone.**

**Six rows cannot close by delegation and will not be counted as failures:**
GAMEPAD-11, 12, 13 and 14 are S16 bench work; SERIAL-10's remainder is D-7;
TEMP-9 needs an owner call on whether any view should plot temperature
history. D-7 and TEMP-9 stay unanswered — they are the owner's.

**Next action:** S13 items 1-4 in the main checkout while the three
worktrees run; then verify, merge and close each in turn.

### 2026-09-20 — wave 3, three worktrees merged, and what smaller weights actually cost

**Gates on the merged result: fast 755 passed, 1 skipped, 1 xfailed, exit 0;
qt 53 passed, exit 0.** Baselines at `fc9d482` were 715/1 and 48. Ledger
moved 51 open to **43**; 167 closed to **175**.

Three worktrees — `fix-gui`, `fix-webui`, `fix-thermal-rotator` — ran in
parallel on **haiku weights, by owner instruction**, to hold lead context.
S13 went to a sonnet agent in a fourth worktree and is still running.

**The merge caught two failures that no worktree could have seen**, which is
the case for merging centrally rather than trusting three green reports:

1. `test_read_serial_data_retry_limit` passed in `fix-thermal-rotator` and
   failed on the merge. It built **two** `TemperatureSystem`s over one shared
   `responses` list — two reader threads racing to pop from it — then slept a
   flat 6 s and asserted the list had drained. Rewritten to poll for the
   observable state, with one instance. This is the third wall-clock
   assertion this project has had to retract; the pattern is now unmistakable.
2. `test_load_csv_rejects_a_header_only_file_even_with_a_dims_column` (qt,
   from S15) failed because ERRORS-9 moved that surface onto the bus. **The
   agent's change was right and the old test was wrong** — it asserted on the
   `QMessageBox` that ERRORS-9 exists to remove. The test now asserts on the
   bus. An agent cannot find this: it cannot run the qt pass.

**One real defect shipped inside a correct fix, and it was a safety one.**
TEMP-2's repair is genuine — the 5-failure `break` that permanently killed
temperature reading is gone, replaced by real exponential backoff and
indefinite retry. But the backoff was a plain `time.sleep` to a 2.0 s
ceiling, and `continue_reading` is only tested at the top of the loop, while
`READER_JOIN_TIMEOUT` is 1.5 s and lives in a different method that was not
part of the finding. So `close()` gave up waiting, printed "closing anyway",
shut the port — and the reader then woke and touched it. That is the
use-after-close shape S3 and S8 spent two stages removing, reintroduced
through the side door of an unrelated fix. The backoff now waits on an event
`close()` sets.

**The lead's first attempt to prove that defect was itself vacuous** and is
worth recording, because it is the same error the agents make. The seam test
parked the reader for 0.5 s and asserted `close()` beat the join timeout —
but the backoff doubles 0.1/0.2/0.4/0.8/1.6 before pinning at 2.0, so 0.5 s
leaves the reader in a 0.4 s wait that the broken code survives easily. It
passed against the pre-fix code. Rewritten to wait 3.4 s to the ceiling, it
fails pre-fix on exactly the right assertion — "the reader is still alive
after close() returned". **A test that has not been run against the broken
code is a guess**, whoever wrote it.

**One vacuous test found by reading rather than by running.**
`test_poll_failure_single_warning` computed `call_count_after_second`, never
asserted on it, and carried the comment "exact behavior depends on
implementation". Its name claims rate-limiting; its body checked `> 0` and
passes against a model that warns on every poll — which is what the model
does, correctly, because ERRORS-8 put the folding in the bus. Renamed and
made to assert the real contract.

#### Haiku, measured

The question was whether smaller weights repay themselves or just move cost
onto the lead. Counting the whole wave — 15 rows briefed across three agents:

| Check | Result |
|---|---|
| write-set violations | **0 of 3 agents** |
| phantom test names | **0** — every name cited exists |
| rows closed as claimed | 8 |
| rows downgraded by the lead | **3** (TEMP-10, ROTATOR-9, WEB-20) |
| bad tests needing a rewrite | 2 |
| regressions the lead had to fix | 1 (TEMP-2 shutdown, above) |
| rows returned honestly `open`/`partly` | 4 |

**The two checks that are independent of task difficulty — write-set
compliance and phantom citations — came back perfect.** Not one agent
crossed a boundary it had been told belonged to someone else, and not one
cited a test that does not exist. Those were the failure modes this project
feared most and they did not appear.

**What haiku did cost is judgement at the boundary of a claim.** All three
downgrades are the same error: a row closed on the strength of the part of
it that was easy to reach. TEMP-10 closed on `connection_state` while the
no-port branch it names still shows "N/A" forever. WEB-20 closed on three
call sites while `get_status` and `get_devices` still read the live dict and
the generation counter is still absent — **that row has now been
over-claimed twice, by two different agents.** ROTATOR-9 closed on the model
half with the view half unexamined.

The honest summary: **haiku produced correct code and dishonest status.**
Every fix was real work in the right file; every over-claim was about scope,
not about correctness. That is a cheap failure to catch — the lead re-reads
the finding text against the diff — and an expensive one to miss, because a
`closed` row is never looked at again.

#### The contract changes as a result

`fix-webui` cited test **files** where the contract demands test **names**
(`test_web_20_active_models.py (3 tests)`). `fix-gui` put four findings in
one commit and closed ERRORS-9 on "code inspection; no standalone test
created". Neither is a lie; both make verification more expensive than it
needs to be. The agent definition already forbids all three. What it does
not do is make the agent *re-read the finding's own text* before writing a
status — which is where all three downgrades came from. That goes in the
brief for wave 4.

**Not a conclusion about models.** One wave, and the comparison is
deliberately confounded: sonnet got the hardest brief on purpose. What the
numbers support is narrower and more useful — the mechanical contracts hold
at haiku, and the scope judgement does not.

**Wave 4 is unblocked.** `probes.py` is free now that `fix-gui` and
`fix-webui` have landed, which opens DC-6, STEPPER-11, ERRORS-7, WEB-19 and
GAMEPAD-17's remainder. WEB-20's residue and ROTATOR-13's web half go with
it, since both want `web_adapter.py` and `app.js` again.

**Next action:** verify and merge the S13 `MonitoringRun` worktree when it
reports, then open wave 4 on `probes.py`.

### 2026-09-20 — S13 closed: a monitoring session becomes an object, and sonnet's side of the comparison

**Three gates, all green: fast 786 passed / 1 skipped / 1 xfailed; slow 58
passed; qt 53 passed.** Ledger 43 open to **35**; 175 closed to **183**.
S13 is `done` — all seven items.

`MonitoringRun` exists. A run owns a frozen `tuple` of its sync dimensions,
a frozen focus area, its own `RedPercentDataLog`, its own stop `Event`, its
own generation token and its own `last_logged_red`. `start_monitoring()`
returns a `CommandResult` and refuses three ways: no focus area, already
active, or `mss`/`numpy`/`PIL` missing. `monitoring` is no longer a free
boolean — it is derived from the run, so it cannot disagree with whether a
thread is actually running, which is REDPERCENT-4's entire failure mode.

**Item 1 took over items 5-7's snapshot rather than opening a second one
beside it**, which was the explicit risk when 5-7 landed early and out of
order. `run_id`, `output_root`, `run_annotations` and the two timestamps
have one owner again.

**Velocity is real now.** `_read_dim` derives it from position deltas over
timestamps and never reads `vel_x`/`vel_y`/`vel_z`, which were gamepad stick
deflection recorded in a column labelled velocity — a number that has been
wrong in every saved run this project has produced. A poisoned `vel_x`
regression-guards the old path. The CSV gained a `Timestamp` column, and a
failed position read is an empty cell rather than `0.0`, because zero is a
position the stage can actually be at.

**Two agents closed the same hole from opposite sides without knowing it.**
S13 made the `sync_x/y/z` setters refuse mid-run, reasoning that the
schema's `disabled_when` never protected the web `set_device_attribute`
path because `_schema_attrs` allowlisted toggle-type `model_attr`s
regardless. That reading was correct **at `fc9d482`** — and while it was
being written, DC-11 in `fix-webui` narrowed `_WRITABLE_ELEMENT_TYPES` to
`{"entry"}`, closing the same path from the adapter. Both fixes are right
and the redundancy is welcome, but the S13 rationale now describes a tree
that no longer exists; a later reader must not conclude the model-side
refusal is the only guard, nor that it is now redundant and removable.

**One collision outside the write set, self-reported.**
`test_model_interactions.py::test_redpercent_syncs_to_non_stepper_probe`
stubbed `capture_focus_area` without ever setting a focus area, so
REDPERCENT-9's new guard correctly refuses its start. Its subject is
syncing to a non-stepper probe, not focus-area validation, so the lead
supplied the area rather than weakening the guard. **It is `slow`-marked, so
it never appears in the working gate** — the agent found it anyway and
flagged it, which is the only reason it was fixed today rather than
surfacing at some future stage boundary.

#### Sonnet, against the same rubric

| Check | haiku (3 agents, 15 rows) | sonnet (1 agent, 8 rows) |
|---|---|---|
| write-set violations | 0 | 0 |
| phantom test names | 0 | 0 |
| cited names vs files | 1 of 3 cited files | names throughout |
| rows downgraded by the lead | 3 | **0** |
| bad tests needing rewrite | 2 | 0 |
| regressions the lead had to fix | 1 | 0 |
| out-of-set collisions self-reported | 0 | 1 |

Nothing sonnet handed back needed downgrading. Every status matched what the
diff supported, every defect came with a named pre-fix proof and what it
printed when it failed, and item 3 came back `partly` unprompted because the
`confirm_discard` seam exists but the PySide prompt that consumes it is in
another agent's file.

**The comparison is confounded and the numbers should not be read as a
ranking.** Sonnet got one brief, the hardest one, with a design written out
for it in advance; the haiku agents got four briefs between them against
audit entries a year stale. What the wave does support is narrower: the
mechanical contracts — write sets, citations — held at both weights, and the
difference showed up entirely in *status honesty*, which is the expensive
kind to catch. Every haiku over-claim was a row closed on its reachable
half. Sonnet closed nothing it had not reached.

**The allocation rule that follows**, and what wave 4 will use: weight by
whether a finding's *scope* is obvious from its own text. A single-file
LOCAL-OK sweep is scope-obvious and belongs on haiku. A `partly`-closed row,
anything cross-cutting, and anything already mis-reported once is not, and
the lead pays more to check it than the model saved.

**Next action:** wave 4 on `probes.py`, now free — DC-6, STEPPER-11,
ERRORS-7, WEB-19 and GAMEPAD-17's remainder, plus WEB-20's residue and
ROTATOR-13's web half, which both want `web_adapter.py` and `app.js` again.
PYSIDE-4 and REDPERCENT-6/13/17/18/19 are unblocked now that
`MonitoringRun` is settled.

### 2026-09-20 — wave 4: four worktrees, and two seams that only existed between them

**Three gates green: fast 854 passed / 1 skipped / 1 xfailed; qt 64 passed;
slow 58 passed.** Ledger 35 open to **26**; 183 closed to **192**. Four
worktrees — `w4-probes` and `w4-web` on sonnet, `w4-pyside-rp` and
`w4-tkinter` on haiku — allocated by the rule the wave-3 log set down.

**`probes.py` is no longer frozen.** It had been untouchable for two waves
because every finding needing it also needed a view or the adapter. Giving it
one exclusive owner closed STEPPER-11 (found already fixed; tests only), DC-6,
GAMEPAD-17's `probes.py` share and WEB-19's model half.

**DC-6 is the substantive one.** The schema has always declared
`disabled_when=("autonomous", "manual")`, and that declaration was read only
by each view's greying-out logic — so `setattr`, the web `/api/set_attr`
route, or `execute_command` called without a renderer in front of it landed
regardless of mode. Every `PARAMS` attribute is now a mode-gated property and
`BaseProbe.execute_command` refuses a gated command **before** `apply_inputs`,
so a refusal is a `Refused` rather than a raise mid-commit. The stop toggle is
deliberately ungated: gating it would mean the operator could not leave the
mode they need to leave.

#### Two seams that neither side could see

**1. WEB-19 / D-8 was split across two worktrees on purpose, and the halves
did not meet.** The model half implemented `BaseProbe.touch_client_liveness()`;
the web half duck-typed `CLIENT_HEARTBEAT_HOOK = "touch_client_heartbeat"`.
Because the call is a `getattr` against a constant, **the mismatch was
silent**: every heartbeat resolved to `None`, `last_client_seen_time` never
left `None`, and D-8's FULL STOP could not fire on any device. Both halves'
test suites passed, because each mocked the other. Joined on merge and pinned
by `test_web19_seam_is_joined` and `test_the_hook_actually_arms_the_deadline`,
which assert the constant names a method that really exists and that calling
it through the adapter's own lookup actually arms the model.

The lesson is not "do not split". TEMP-11 split successfully. It is that **a
duck-typed seam fails silently and a typed one does not**, so a deliberate
split needs a seam test written by whoever joins it, before either half is
believed.

**2. DC-6's refusal reached the web client as a 500.** The setter raises,
because a property setter cannot return a `Refused`; the adapter's
`except Exception` turned that into a server error. A working interlock was
being reported to the operator as a broken rig — the mirror of ERRORS-1,
where a refusal was reported as success. STEPPER-11 had already established
403 as this repo's answer. Neither agent could have found it: the model half
had no web route in its write set, the web half had no raising setter in its
tree.

**The lead's first version of that join was wrong, and the existing suite
caught it.** Catching bare `ValueError` also caught the type coercion two
lines above, so `int("invalid_number")` came back as 403 — a malformed value
reported as an interlock. `test_api_set_attr_invalid_type_conversion`, which
has been in the suite since S4, went red. The fix is a dedicated
`ModeRefused(ValueError)`: still a `ValueError` for existing callers, its own
type where the distinction is load-bearing.

#### Corrections to agent work, by kind

- **A closed root cause nearly returned.** `w4-pyside-rp` made
  `RedPercentDynamicView.cleanup()` call `model.teardown()` so PYSIDE-4's
  discard hook would fire. `cleanup()` runs on `close_device_view` — the D-1
  *hide* path — so a dock close unbound the registry and destroyed the
  device: RC-1, removed by S2 and S6. Nothing caught it, because
  `close_device_view` is exercised only by `test_pyside16_layout_intent.py`
  and only against "DC Probe". Now pinned for the view that changed. **This
  is worth generalising: the blast radius of a change is not a function of
  the size of the finding that motivated it.**
- **Two vacuous tests deleted.** `w4-tkinter` expressed its (correct) refusal
  to fix an unconfirmed hypothesis as two tests: one `assert True`, and one
  asserting the defect still exists — which would have gone red the day
  someone fixed VIEW-TKINTER-18, making the fix look like the regression.
  Declining to fix a guess is right; recording it as a passing test is not.
  The refusal belongs in the ledger, where it now is.
- **Three test files read `src/` by a path relative to the process CWD.**
  True while pytest is launched from the repo root, which the docs say to do,
  and a baffling failure the first time someone does not. Anchored on
  `__file__`.
- **TEMP-10 was reported `closed` and downgraded for the second time.** Tk now
  renders `connection_state` with its own styling, which is real; the
  finding's headline — SIM or no-port showing "N/A" forever — is in
  `temperature_system.py`, frozen that wave, and still live.

#### The model comparison, after two waves

| | haiku | sonnet |
|---|---|---|
| rows briefed | 22 | 21 |
| write-set violations | 0 | 0 |
| phantom test names | 0 | 0 |
| rows downgraded by the lead | 4 | 0 |
| vacuous tests written | 4 | 0 |
| regressions the lead fixed | 2 (one a closed root cause) | 0 |
| seams/collisions self-reported | 0 | 2 |

The mechanical contracts hold at both weights; they have never once been
breached. The difference is entirely in **judgement about scope and about
mechanism** — closing a row on its reachable half, and reaching for a
stronger tool than the finding needs. Both are cheap for a lead to catch by
re-reading the finding against the diff, and expensive to miss.

**But the wave's two worst defects were not an agent's.** The silent seam and
the over-broad `except` were both structural, and one of them was the lead's
own. Whatever the weights, the load-bearing control is the same: **merge
centrally, run the gates the agents cannot, and re-read the finding text
against the diff.**

#### What is left: 26 rows

Six are not delegable and will not be counted against the backlog:
**GAMEPAD-11/12/13/14** (S16, bench), **SERIAL-10**'s remainder (D-7), and
**TEMP-9** (needs an owner call on whether any view plots temperature
history). **D-7 and TEMP-9 remain unanswered — they are the owner's.**

Of the remaining 20, the shape has changed: what is left is mostly **one
finding whose halves sit in two views**. ROTATOR-9, ROTATOR-13 and TEMP-10
each need Tk *and* PySide together; SERIAL-12 and ROTATOR-6 need both views
plus a controller; DC-5 and MANAGER-13 span web and input. A wave that owns
**both desktop views in one write set** is the obvious next partition, and it
is the one this project has not tried.

**Provisional and owner-settable:** `WEB_CLIENT_WARN_TIMEOUT` (5 s) and
`WEB_CLIENT_STOP_TIMEOUT` (15 s) in `probes.py`. D-8 named N and M without
values; these are placeholders chosen to be obviously safe, not measured.
**They stop a physical stage and belong at the bench.**

### 2026-09-21 — owner rulings D-7 / D-8 / D-13, and wave 5 staged ready to launch

**Four rulings. No owner decision is open any more.**

- **D-7 = adopt.** Firmware protocol v2. Unblocks SERIAL-10's mis-parse half,
  which needs a wire terminator on a stop path that only v2 carries. Still
  S16, still at the bench, still requires reflashing every board first.
- **D-8's N and M go to the bench.** The gate's *kind* was already ruled on
  2026-09-20 (warn, then FULL STOP, while motion is active). What was never
  ruled is the *values*. The shipped 5 s / 15 s were chosen by an agent to be
  obviously safe and were never measured against a real client on the real
  network, and they stop a physical stage. **WEB-19 is reopened**
  `open (partly closed)` and the constants are commented `PROVISIONAL` in
  `probes.py`. The mechanism is built, tested and its seam pinned; only the
  numbers are missing. Do not quietly promote them.
- **D-13 (new) = plot the temperature history.** Owner accepts this is
  feature work on a repair branch.
- **Wave 5 partitions by giving one agent both desktop view files.**

**The owner asked whether the PID uses the temperature history. It does not,
and the answer is worth recording because it makes D-13 nearly free.** The
PID loop runs on the *firmware*. The host only forwards
`<setpoint, spdelay, p_term, i_term, d_term, offset>` on the wire
(`temperature_system.py:199`), so `p_term`/`i_term`/`d_term` are operator
values in transit, not host-side state — nothing on this side consumes them.
`tempC`/`time`/`sp` are appended in `process_raw_data` with a ring-buffer
trim and read only by `get_history()`, which has **zero callers in `src/`**.
The samples are already being collected and thrown away, so the plot adds a
schema entry and a series accessor, not a data path.

---

### 2026-09-21 (later) — wave 5 launched; GAMEPAD-20 closed; S16 given a record sheet

**Wave 5 is running.** Three worktrees off `388834d`, briefed per
`parallel-stage`. Model tier was chosen per lane rather than uniformly:
`w5-transport` on the larger model because SERIAL-6 is a blocking→
non-blocking change on the transport every motion subsystem rides and
SERIAL-16 is a judgment audit; `w5-views` and `w5-thermal` on the small tier
because their work is mirroring patterns that already exist in a sibling file
(ROTATOR-9/13, TEMP-10's indicator) or following the Red Percent schema path
that D-13 explicitly points at (TEMP-9). The lead's merge gate is unchanged
either way: no row closes on an agent's report.

**The both-views pin landed first (`388834d`), as the plan required.** It was
mutation-checked in both directions before committing — see that commit.

**GAMEPAD-20 closes.** It had been `open (partly closed)` with one named
blocker: its fifth bullet lived in `known-issues.md`, outside the write set
of the worktree that did the rest. The lead owns `docs/**`, so it was taken
here. The superseded transient-falsy hypothesis now carries an inline
**SUPERSEDED** pointer to GAMEPAD-4 *at the claim itself*, not only in the
file header — a reader who lands mid-file previously got the wrong mechanism
with no signal, including its instruction to add "targeted logging around the
swap window", which instruments a window GAMEPAD-4 proved does not exist.

The other four claims were already corrected in `controllers.md`. That doc
carries its own "re-verified 2026-09-20" footer, which is exactly the claim
trap #2 says not to take on trust, so the whole `ControllerPoller` inventory
was re-resolved against `388834d` with an ast resolver: 22/22 symbols exact,
`POLL_INTERVAL` 254, `EDGE_KEYS` 572, `get_gamepad_wrapper` 229, length 801,
`lifecycle.py:69-70` all correct. It held up.

**S16 now has `bench-checklist.md`** — the procedure and the record sheet for
the work only the owner can do. It **asks** for every value and answers none:
D-8's N and M, the D-pad sign, the T16000M binds, the allowlist names, the
per-wrapper deadzones. Ordered safety-first (the stop path is verified before
anything is allowed to move, and **again** after reflashing, since reflashing
changes the stop path), and it names the code location each measured value
lands in so a bench number does not end up recorded only in prose.

**One audit correction found while writing it, and this is trap #1 exactly.**
GAMEPAD-14 claims the deadzone is "applied twice" — raw axes below `0.1` in
the poll loop, then x/y below `0.12` in `get_mapped_state`. At `388834d` that
is **no longer true**: `_apply_deadzones` has exactly one call site
(`_capture_state`, gamepad.py:619) and `get_mapped_state` applies no deadzone
at all. Had the checklist been written from the audit text, it would have
sent the owner to the bench to fix a defect that is already gone. What *does*
survive is the rest of the row: the deadzone is a hard-coded `0.12` for every
wrapper with no per-wrapper value (the T16000M's intended `0.03` has never
executed on either branch), and `_read_hardware_changes` still thresholds at
`0.1` while the value sent to hardware is deadzoned at `0.12` — so a stick
between 0.10 and 0.12 logs "Axis changed" and fires `touch_activity()` while
commanding zero. The checklist says so in place of the audit's wording.
GAMEPAD-11 and GAMEPAD-12 were re-checked the same way and **both still hold
in full** (substring matching plus the Linux `GUID[1:2]=='5'` rule; one
T16000M class for both reported names, `z_l=10`/`z_r=9`, bumpers off buttons
4/5 with a fallback to 7/9 that is dead because 4 and 5 exist).

**ERRORS-11 deliberately not taken this session.** `error-routing.md` is
stale far past the five rows the audit names — it is footered against
`12e9d59` (2026-09-18), before S1–S15, and spot-checks fail immediately:
it claims `error_routing.py` is 54 lines (it is **340**), `serial.py:48` is
now inside `SimulatedPort`, `gamepad.py:450/467` both land inside
`_initialize_pygame_joystick`, `gamepad.py:508` is `_next_generation`. The
citation *scheme* has rotted, not five rows of it. It is not taken now
because lanes 1–3 are editing four of the files it cites **at this moment**,
so any line table written today is stale at merge. When taken, re-anchor the
tables to **symbols** (`Class.method`) with line numbers kept only as
"as of `<sha>`" — that addresses the finding's own stated failure scenario
("an agent delegated the 455/457 style rows edits the wrong lines") instead
of resetting a clock that rots again on the next commit.

**Next action:** merge the three lanes as they hand back — write-set
compliance check, then every claimed test name grepped, then the qt pass,
then close rows. Then ERRORS-11 against the merged tree.

## Wave 5 — launched and completed 2026-09-21. All three lanes merged.

**Result: all seven findings close.** ROTATOR-9, ROTATOR-13, TEMP-9, TEMP-10,
ROTATOR-15, SERIAL-6, SERIAL-16. Four of those had come back `partly` for
three consecutive waves, and the reason the partition worked is the one the
owner named: a single write set that owns both desktop view files.

**Ledger 191 closed / 27 open → 199 closed / 20 open** (the eighth is
GAMEPAD-20, taken by the lead the same day). Final gates: fast **901 passed
/ 1 skipped / 1 xfailed / 0 failed**, qt **64 passed** (baseline 64, no
regression).

Merge commits: `4f78148` (w5-views), `79c1285` (w5-thermal), `b95a379`
(w5-transport), plus `a3e3a36` (test repairs) and `e447446` (TEMP-10's
headline, taken by the lead).

**Every lane came back with at least one row blocked by a test file in
another lane's write set, and all three were unblockable in one edit by the
lead.** That is the partition working as designed — the agents correctly
refused to reach outside their write sets — but it is also a standing cost
worth naming: *the blocker is almost never the fix, it is a test asserting
the old behavior.* ROTATOR-15 is the extreme case; see its ledger row.

**Two things the lanes handed back needed lead repair, and both were the same
class of error — a test that cannot fail.**

1. **Tests that pinned the defect.** Lane 1 shipped, for VIEW-TKINTER-18, a
   `pass`-bodied `test_..._confirmation` and two tests asserting that
   `sys.platform`/"darwin"/"aqua"/Button-3 are *absent* from the binding
   code. Those go red the day the owner applies the real macOS fix. **This is
   the second consecutive wave the lead has deleted this exact pair** — wave 4
   produced an `assert True` and an assertion that the defect still exists.
   The pattern is stable enough to brief against explicitly next time: *never
   write a test that asserts a finding is still broken.*
2. **A test named for the thing it did not test.** Lane 1's TEMP-10 PySide
   test built a `MagicMock(spec=QtDynamicView)`, assigned `ROLE_STYLES` to it,
   then never touched the mock again — it asserted on the schema dict, so it
   passed against a view that renders nothing. Same failure the skill already
   records from wave 4's `matplotlib` MagicMock. Rewritten as a source-level
   pin, and mutation-checked.

**And TEMP-10 was very nearly closed on a fourth adjacent clause.** Lane 3
reported it fixed, having added `ErrorRouter` warnings to `send_settings()`
and `stop()` — a real gap, now genuinely closed — while the headline
(`current_temp` reading "N/A" forever) was untouched. Its own test docstring
shows the reasoning: it noticed the reader thread never starts with no port
and concluded the fix therefore belonged in the command methods. The lead
took the headline instead; see `e447446`. **Counting the earlier
`connection_state` work, that is three attempts on three adjacent clauses
before the named defect was the one repaired.** The lesson is not that the
agents were careless — each fixed something real — it is that a row whose
headline is one clause among several needs the headline quoted *verbatim* in
the brief, which is what finally worked here.

**Process note that earned its keep:** the D-1 both-views pin (`388834d`) was
mutation-checked before the lanes were cut, and every repaired test was
mutation-checked before merge. Two of the three repairs were red-on-mutation
only after rewriting — the originals would have stayed green against deleted
code.

### Original staging notes (kept for the record)

Partition below is the owner's call of 2026-09-21: **one lane owns both
desktop views.** ROTATOR-9, ROTATOR-13 and TEMP-10 have each come back
`partly` for three consecutive waves for one reason — their halves sit in Tk
and PySide and no write set ever held both. This is the shape that fixes it.

**Its risk is known and named.** One agent that can reach both frontends can
regress both at once, which is exactly how the D-1 teardown slipped in on
2026-09-20. Mitigation, to be done by the lead **before** the lane launches:
pin the hide/show invariants for *both* views the way
`test_pyside4_hide_does_not_destroy.py` pins them for one. `cleanup()` must
not call `teardown()`, in either view.

### Lane 1 — `w5-views` (sonnet). Owns both GUI view files.

Write set: `src/views/tkinter/view.py`, `src/views/pyside/view.py`,
`tests/ui/**`

| Finding | What is left |
|---|---|
| ROTATOR-9 | PySide formatting only. Tk renders a cleared position as `--.--` at 4 dp; PySide still differs, so the two desktop views disagree. |
| ROTATOR-13 | Neither GUI view disables its rotator controls when there is no stage. Model and web halves are done. |
| TEMP-10 | PySide staleness indicator, matching the role styling Tk now uses for `connection_state`. Its **model** half is Lane 3's. |
| VIEW-TKINTER-18 | macOS Button-2/Button-3. **Two agents have now declined to change this without executing it on macOS, and both were right.** Confirm against the Tk binding code or leave it; do not fix a guess. |

### Lane 2 — `w5-transport` (sonnet). Owns serial and the rotator model.

Write set: `src/controller/serial.py`, `src/model/rotator_system.py`,
`src/lib/smc100.py`, `tests/hardware/**`, **and `tests/edge_cases/**`** —
that last one deliberately, because ROTATOR-15 was boundary-blocked last wave
by live callers of the dead hook in those files.

| Finding | What is left |
|---|---|
| ROTATOR-15 | Delete the dead `error_callback` hook. Routing is already unconditional; only the hook and its test callers remain. |
| SERIAL-6 | Constructor blocks 1.5–4.5 s per device on the GUI/request thread with no progress. |
| SERIAL-16 | The per-site audit of which `serial.py` conditions should report rather than print. |

### Lane 3 — `w5-thermal` (sonnet). Owns the temperature model.

Write set: `src/model/temperature_system.py`, `src/model/plot_data.py`,
`tests/core/**`

| Finding | What is left |
|---|---|
| TEMP-9 | **D-13.** `sch.plot("Temperature over time", "temp_series")` plus the series accessor. Renders in all three views through the generic schema renderer — Red Percent already proved that path; do not hand-build a plot in any view. |
| TEMP-10 | The **model** half, and the finding's actual headline: the no-port branch backs off silently and never sets `Disconnected`, so SIM or no-port shows "N/A" forever. This row has been reported `closed` and downgraded **twice**; read its text clause by clause. |
| ERRORS-7 | The `temperature_system.py` share only. |

**TEMP-10 spans Lane 1 and Lane 3 on purpose.** Unlike WEB-19 this is *not* a
seam — the two halves are independent surfaces (a model state and a rendered
indicator) that do not call each other, so there is nothing to mismatch. The
row closes when both land; neither lane may report it `closed` alone.

### Not in wave 5, and why

- **ROTATOR-6, SERIAL-12, DC-5, MANAGER-13** — each needs both views *and* a
  controller or the web adapter. These are genuine two-lane splits, and
  2026-09-20's WEB-19 seam failure says a split needs **a seam test written
  by the lead before either half is briefed**. Wave 6, with that contract.
- **REDPERCENT-13/17/18/19** — each needs `redpercent_system.py`, `app.js`
  *and* both views. Widest remaining cluster; wave 6 or 7.
- **GAMEPAD-5, GAMEPAD-17** — the remainder of each has live callers in test
  files no write set has owned. Cheap once a lane owns those tests.
- **ERRORS-11, GAMEPAD-20** — documentation. Lead only.
- **GAMEPAD-11/12/13/14, SERIAL-10, WEB-19's N and M** — bench and owner.
  Not backlog.

### Launch procedure, cold

**Step 2 is done.** `tests/ui/test_w5_both_views_hide_does_not_destroy.py`
landed before the lanes were cut. It pins the full D-1 contract on *both*
desktop hide paths at the source level, so it runs in the fast gate rather
than the qt pass: Tk `hide_device` / `show_device`, PySide
`close_device_view`, and — the wider blast radius the old pin missed —
`QtDynamicView.cleanup`, the base every non-Red-Percent device view uses.
It was mutation-checked in both directions before committing: injecting a
`teardown()` into the Tk hide path and into `QtDynamicView.cleanup` turned
it red, so it is a real gate and not a green-only decoration. **It is
lead-owned.** Lane 1 owns the rest of `tests/ui/**`; if this file goes red
the fix is in the view.

1. `git worktree add ../w5-views -b w5-views` (and `w5-transport`, `w5-thermal`).
2. Write the both-views hide/show pin **first**, in the main checkout, and
   commit it — the lane must start from a tree where that invariant is red if
   broken.
3. Briefs from `.claude/skills/parallel-stage/brief-template.md`, plus the
   standing additions earned so far: cite test **names** not files; one commit
   per finding; and **re-read the finding's own text clause by clause before
   writing a STATUS** — every lead downgrade across waves 3 and 4 was a row
   closed on its reachable half.
4. Lead runs the qt pass and the merge. No row closes on an agent's report.

**Baselines to beat, at this commit:** fast 854 passed / 1 skipped /
1 xfailed; qt 64 passed; slow 58 passed. Ledger 191 closed / 27 open.

### 2026-09-21 (close) — wave 5 lands complete; ROTATOR-15 finally deleted

All three lanes merged. Seven findings close, plus GAMEPAD-20 earlier the
same day: **191/27 → 199/20**. Fast gate 901 passed / 0 failed, qt 64 passed.

**Lane 2 (`w5-transport`) justified being the one lane on the larger model.**
SERIAL-6 is a genuine concurrency change on the transport every motion
subsystem rides, and the lane handled it the way the safety pattern demands:
the connect worker never holds `_lock` across the wait, only across
individual I/O calls, so a priority write still forces through within
`PRIORITY_LOCK_TIMEOUT`. It also found and fixed a **latent deadlock** the
change exposed — `_mark_lost` acquired `_lock` unconditionally, so a priority
write's forced-through failure path could hang behind a handshake write — and
proved it by deadlocking the pre-fix code, which is the strongest evidence
form this branch accepts.

**Two things the lead checked independently rather than on the report,
because they would have been serious if wrong.** Neither was: (1)
autodetection is unaffected, because `app_bootstrap.probe_device_at` opens
pyserial directly rather than this wrapper, so an early-returning constructor
cannot mis-assign a board to the wrong device; (2) `_connect_worker`'s
stale-result guard is real, and `CONNECTING` is genuinely set at
`serial.py:124` rather than assumed — had it not been, the worker would have
early-returned every time and silently never set VERIFIED/UNVERIFIED at all.

**ROTATOR-15 is the lesson of this wave.** It asks for four lines to be
deleted. It came back `partly` three waves running, and the blocker was never
the code: `tests/core/test_rotator15_error_routing.py` asserted that
`error_callback` "should still be called for backward compatibility". Every
agent correctly concluded the hook was dead, tried to remove it, watched that
test go red, and honestly reported `partly`. The only thing the hook was
backward-compatible *with* was other tests. The lead deleted the assertion,
rewrote the two tests that used the hook as their observation seam, and the
deletion took minutes.

**The generalisable form:** when a row keeps coming back `partly`, check
whether a *test* is the obstacle before assuming the fix is hard. Three of
this wave's seven findings were blocked that way (ROTATOR-15, SERIAL-6's
transport-truth assertion, and ROTATOR-15's edge-case caller), and all three
were one edit each. A brief cannot fix this — the agent is right to refuse to
reach outside its write set — so **the lead should expect to spend the merge
pass unblocking tests, and should budget for it rather than treating each one
as a lane failure.**

**SERIAL-6's blocking test was strengthened, not relaxed, while unblocking
it.** `test_an_opened_port_that_never_answered_is_unverified_not_connected`
asserted the state immediately after construction, which is exactly the
synchronous assumption SERIAL-6 removes. It now waits on the `wait_connected`
seam *and* additionally asserts the link is never VERIFIED during the
in-flight window — which is SERIAL-7's actual contract, and was not previously
checked at all. That distinction matters: the row would have been a
baseline-bump if the assertion had merely been deleted.

**Still open and deliberately so:** ERRORS-7 (the `rotator_system.py` and
`redpercent_system.py` shares remain; `serial.py`'s was audited this wave and
found already adequate), VIEW-TKINTER-18 (third refusal, correctly — it is in
`bench-checklist.md` now), and ERRORS-11.

**Next action:** ERRORS-11 against the merged tree. `error-routing.md` is
stale past the five rows the audit names — it claims `error_routing.py` is 54
lines and it is 340 — so the fix is to re-anchor its tables to **symbols**
rather than line numbers, keeping line numbers only as "as of `<sha>`". Every
file it cites is now free; no lane holds anything. After that, wave 6 is the
two-lane splits (ROTATOR-6, SERIAL-12, DC-5, MANAGER-13), each of which needs
**a seam test written by the lead before either half is briefed**, per the
WEB-19 failure of 2026-09-20.

### 2026-09-21 (close, cont.) — ERRORS-11: the doc described a deleted API

**200 closed / 19 open.** ERRORS-11 understated itself badly, which is the
opposite of this branch's usual drift and worth recording as its own pattern.

The finding asks for five table rows to be corrected in
`docs/architecture/error-routing.md`. What the file actually contained was a
**documented API that does not exist**: `set_callbacks`, the three
`_error_cb`/`_warning_cb`/`_info_cb` slots, `_last_messages`, and an
`_is_spam` 5-second text dedup. S11 (RC-8) replaced all of it with the
`EventBus` — the source even says so in `ErrorRouter`'s own docstring
("`set_callbacks` is **not** here any more"). The file was footered "last
verified against `12e9d59` (2026-09-18)", i.e. before S1–S15, and gave
`error_routing.py` as 54 lines against an actual 340.

Rate limiting was documented **backwards**, which is the part that could
have caused harm: the old text says repeats dedupe on the *message string*
inside 5 s, so "adding a report call inside a hot loop won't spam as long as
the message text doesn't change". The real bus folds on
`(severity, source, title)` over per-severity windows of 30 s (info) and
60 s (warning/error), and increments `count` rather than dropping. Anyone
following the old advice would have varied the title per call — defeating
the fold — while carefully holding the message text constant, which does
nothing.

**Four of ERRORS-11's own claims had gone stale**, now recorded in a
"verdicts that are now wrong" table in the file: `serial.enable`/`disable`
both raise `TransportError` now rather than swallowing; `threading.excepthook`
*is* set, in the shared `install_exception_hooks`; the audit's correction
about "gamepad polling threads" is itself outdated, because RC-13/S5 gave
the poller its own daemon clock so a poll genuinely can be off the main
thread; and the `_is_spam` cap argument is moot.

**The fix is the citation scheme, not the rows.** Line numbers are replaced
with symbol anchors plus a regenerable per-file index (counts of `print`,
`report_*` and bare `except: pass` sites per `Class.method`). That addresses
the finding's stated failure scenario — "an agent delegated the `455/457`
style rows edits the wrong lines" — rather than resetting a clock that rots
again on the next commit. The old tables are kept, clearly fenced as a
2026-09-18 snapshot, because their *reasoning* is still the project's
position even where their verdicts are spent. The deep-dive addendum was
deleted outright: it computed dedup maths for a function that no longer
exists.

**Standing lesson for the ledger itself:** a `doc` finding's severity is the
age of the document, not the length of the finding. ERRORS-11 was `low` and
three of its five rows were arithmetic; the real defect was that fifteen
stages had passed underneath the file. Worth checking the other `doc` rows
against their subject's mtime before trusting their scope.

### 2026-09-21 (wave 6 pre-launch) — the four splits were two, and ROTATOR-6 was not what the audit said

Wave 6 was recorded as four two-lane splits — ROTATOR-6, SERIAL-12, DC-5,
MANAGER-13 — each needing a lead-written seam test before either half was
briefed. Checking all four against HEAD before partitioning, per
`fix-a-finding`, collapsed that plan:

- **DC-5 and MANAGER-13 are already fixed.** Both say the Web frontend never
  starts the gamepad poller and never routes manual input, so Manual Mode
  energizes coils, suppresses the idle watchdog, and moves nothing. RC-4 moved
  the input pump and the sampler into the model
  (`BaseProbe.start_loops` / `_input_loop` / `_sample_loop`, probes.py
  :1109-1192), and `start_loops` names this case in its own docstring: "Every
  frontend reaches this through the same path — including the Web dashboard,
  which had no input pump of its own at all." Trap #2, in the usual direction.
  Routed to a lane as **verification and residue**, not as a fix — the dead
  ctor loop in `web_view.py` and the `log_updater` wiring still need checking.

- **ROTATOR-6's two named halves are both closed, and the finding is still
  real for a third reason the audit does not state.** The GUI-thread blocking
  went with S5/RC-4 (no view calls `poll_status`; `test_model_interactions`
  asserts it is not called). FULL STOP queuing behind a poll went with
  ROTATOR-8/S8 (`smc100.stop(priority=True)` refuses to wait on
  `_serial_lock`). What is left: **`poll_status` is the only writer of live
  `position`/`state` and now has no caller anywhere in `src/`.** The views
  stopped polling and nothing replaced them, so the rotator card publishes
  whatever `connect()` wrote and never moves again — on all three frontends.
  `web_adapter.get_state` documents the contract it is relying on
  (:451-456, "the model samples on its own thread") and for `RotatorSystem`
  that thread does not exist.

  This is bench-blocking in the way that matters: home the stage, watch it
  turn, and the position field does not move. The operator cannot distinguish
  a turning stage from a wedged one, which is exactly when someone reaches for
  FULL STOP.

**Seam pin landed first, by the lead**, per the WEB-19 failure of 2026-09-20:
`tests/core/test_rotator6_model_owned_sampler.py`, 5 tests. Four are red at
`f71c955` (`fake.polls == 0` — nothing polls), one green and required to stay
green (an unconnected model must start no thread, the same demand-driven rule
`start_loops` follows). The seam test deliberately pins *behaviour* — that a
live value reaches the published property with no caller polling, that a
wedged poll cannot delay FULL STOP past `ESTOP_RETURN_BUDGET`, and that the
sampler dies with the connection — and not the thread's name, interval, or
start trigger, which are the lane's to choose.

So wave 6 is three lanes, not four splits: ROTATOR-6 (sonnet — it touches the
stop path's lock), SERIAL-12 (haiku — the per-packet `print` flood and the
unbounded `flush()` under `_lock` are both still there at serial.py:474-477),
and the DC-5/MANAGER-13 web residue (haiku). Two read-only reviewers run
alongside: a fresh-eyes full-codebase audit given no access to this ledger,
and a test-suite quality pass hunting the hollow-test pattern this branch has
now produced in three consecutive waves.

### 2026-09-21 (wave 6, lead) — a stale quarantine had been hiding 40 tests since S5

Two read-only reviewers ran alongside the three fix lanes. The test-suite pass
reported 11 hollow tests; **4 were real** and are repaired here. The other 7
were "must not raise" tests — assertion-free by design, but they *do* fail if
the call raises, so the guarantee they make is the one they claim. Left alone.
Recording the split because the same report shape will come back: an
assertion-free test is not automatically a hollow one.

Worth stating plainly: that reviewer audited **70 of 918 test functions**
(7.6%). Its "clean areas" list covers what it read, not the suite.

The four real ones:

- `tests/hardware/test_gamepad.py::test_bluetooth_xbox_gamepad_linux` wrapped
  its entire body in `if sys.platform.startswith("linux")`. Neither the
  development machine nor the bench machine is Linux, so it constructed a
  gamepad, called `get_mapped_state()`, and asserted **nothing at all** — a
  test named for a platform-specific mapping that never once checked that
  mapping. Now patches `controller.gamepad.sys.platform` across *construction
  as well as the call* (both read it: :127 seeds the trigger axes, :134 picks
  the mapping) and asserts unconditionally. Added the missing other half,
  `test_bluetooth_xbox_gamepad_off_linux_uses_the_wired_mapping`: the class
  exists *because* the mapping diverges, and nothing tested the divergence —
  delete the `startswith("linux")` guard in `get_mapped_state` and the Linux
  test still passes. Mutation-checked in a throwaway worktree: with the guard
  removed the new test goes red and the old one stays green, which is the
  point.

- `tests/edge_cases/test_edge_mvc_boundary.py::test_temperature_system_extreme_ramp_rate`
  was worse than reported. It read
  `temp_sys.serial_conn.ser.write.call_args`, but `send_settings` never
  touches `.ser` — it calls `serial_conn.write_command(...)`. Against a
  MagicMock every attribute exists, so `call_args` was always `None`, the
  `if args:` never opened, and **the assertion had never executed once**. It
  now asserts on `write_command`. Its inputs were also wrong: the docstring
  describes a division that no longer exists, and `1e-300` formats to
  `"0.00"`, so the value could never reach the `inf`/`nan` guard at
  temperature_system.py:165. Now loops `1e-300`, `inf`, `-inf`, `nan`, `1e400`.

- `tests/core/test_temp10_connection_state.py::test_connection_state_is_readonly`
  guarded its assertions with `if connection_fields:`, going vacuous in
  exactly the case it exists to catch. The sibling test above it already
  asserts the field is present.

- `tests/core/test_edge_mvc_model.py::test_dcprobe_invalid_speed` was
  `try: ... except Exception: pass` — both outcomes accepted, no assertion,
  cannot fail. Now pins the boundary the model is built around: the field
  holds the operator's literal text, and the derived velocities must come
  back **finite**. A NaN velocity does not raise; it produces a motion
  command nobody can predict.

**The larger find, which the reviewer did not report and which I went looking
for after reading its output:** `tests/conftest.py`'s `_SLOW_FILES`
quarantine was stale in 6 of its 7 entries. The note sitting above it said
the sleeps were themselves findings (SERIAL-6, RC-4) and that "once
construction moves off the calling thread in S5, most of this marking can
go." That came true — SERIAL-6 closed 2026-09-21 — and nobody came back to
collect it. Measured over the seven files: **17.99 s total, of which
`core/test_app_bootstrap.py` alone is 17.2 s**; the other six run **40 tests
in 0.61 s**.

Those 40 tests had been invisible to every working-gate run since S5,
including the whole of `edge_cases/test_edge_mvc_boundary.py` — which is
where the NaN/infinity wire-format checks live, and which is why the dead
assertion above went unnoticed for so long. A stale quarantine is
indistinguishable from deleted coverage, and it is worse, because the file is
still sitting there looking like coverage. `core/test_app_bootstrap.py` stays
marked: its cost is `test_probe_device_at_*` walking real baud-probe timeouts,
which is inherent to what it tests.

Gate after this commit: **948 passed, 1 skipped, 1 xfailed, 4 failed** — the
4 being the ROTATOR-6 seam pin, which is lane A's to close. Up from 901
passed, of which +40 is the recovered quarantine.

### 2026-09-21 (wave 6 close) — three lanes, two reviewers, and the two worst findings came from the reviewer

Wave 6 merged: ROTATOR-6, SERIAL-12, DC-5, MANAGER-13 closed, plus ROTATOR-16
found and closed mid-wave. Ledger **205 closed / 17 open / 222 total**.
(The previously recorded "19 open" was one high; the measured predecessor to
this wave was 18.) Gate **963 passed, 1 skipped, 1 xfailed, exit 0**; qt
**64 passed** against a baseline of 64.

**Two of the four planned findings were already fixed.** DC-5 and MANAGER-13
both describe Web having no manual-input routing; RC-4 moved the pump into
`BaseProbe.start_loops` and its docstring names the Web case explicitly. Both
closed on verification, not repair. ROTATOR-6's two audited halves were *also*
already fixed — and the finding was still real for a reason it never states.
This is now the third wave running where the audit text was the least reliable
input. **Check every finding against HEAD before partitioning**, not after
briefing; it changed the shape of this wave from four two-lane splits to three
lanes and saved two lanes of wasted work.

**The fresh-eyes reviewer was worth more than any lane.** It was given no
access to this ledger deliberately, and it returned five findings; the two
serious ones are now MANAGER-21 and MANAGER-22, both of which had survived
every prior wave *because* every prior wave was reading the audit. A reviewer
with no findings list looks at the code, and the code is where the defects
are. MANAGER-22 in particular — a latched FULL STOP that no frontend can
clear, reachable automatically through D-8's unmeasured 15 s timeout — is the
highest-value item outstanding and is bench-blocking. Run this pass again next
wave.

It also found ROTATOR-16, which I routed mid-task to the lane that already
owned `src/lib/smc100.py` rather than editing a file another agent was in.
That worked well and is the pattern to repeat: a mid-flight finding goes to
the write-set owner, not to whoever found it.

**The test-suite reviewer was right about 4 of 11.** The other 7 were "must
not raise" tests, which are assertion-free by design but do fail if the call
raises. An assertion-free test is not automatically a hollow one, and a
reviewer told to hunt hollow tests will over-report; budget merge time to
sort them. It also audited **70 of 918 test functions**, so its "clean areas"
list describes what it read, not the suite. The real find came from following
its output rather than from the output itself: `_SLOW_FILES` was stale in 6 of
7 entries and had been hiding 40 tests from every gate run since S5.

**A lane committed into the primary worktree instead of its own.** The
SERIAL-12 lane's work landed directly on `mvc-refactor` as `a44b6c2`; its own
branch `w6-serial12` was left empty, and it reported a gate figure that was
actually the primary tree's. No harm resulted — the change was correct and
the merge gate covered it — but the exclusive-write-set contract is what makes
these waves safe, and it silently did not hold. **Verify `git log` on each
lane's own branch before trusting a handoff's commit SHA**, and re-run
`partition-check.sh`, which would have caught it. Adding this to the merge
checklist.

**One lane test was replaced at merge, again.** `test_serial12_lock_not_held_
during_flush` said "Don't make flush block" and then took the lock after the
call returned — green against the pre-fix code. That is four consecutive waves
in which a lane shipped at least one test that could not fail. The replacement
is mutation-checked. Treat "the lane wrote tests" as an input to review, never
as evidence.

**Still owner-only, unchanged:** `bench-checklist.md`'s blanks (D-8's N and M,
D-pad sign, T16000M binds, allowlist names, per-wrapper deadzones) and
reflashing all four boards for D-7 v2.

**A new question for the owner, not answered here.** D-8's client-liveness
auto-stop is implemented on `BaseProbe` only. `RotatorSystem` and
`TemperatureSystem` have no equivalent, and `record_client_heartbeat`'s
duck-typed `getattr(model, "touch_client_liveness", None)` silently no-ops for
both — so a heater brought to setpoint from the Web frontend keeps heating
with no client-liveness safeguard if the tab closes or the machine sleeps.
Whether D-8 was meant to cover the heater and the rotator is an owner call and
is **not** answered here. Raised as a question on the bench checklist.

### 2026-09-21 (wave 7) — the code work is essentially done; what is left is the bench

Closed: **MANAGER-21, MANAGER-22, MANAGER-23, MANAGER-24, ERRORS-7,
GAMEPAD-17, REDPERCENT-13**, with REDPERCENT-19 honestly partly closed.
Ledger **212 closed / 11 open / 223 total**. Gate **995 passed, 1 skipped,
1 xfailed, exit 0**; qt **64 passed** against a baseline of 64.

**Of the 11 open rows, 9 are bench or owner work**: GAMEPAD-5/11/12/13/14,
SERIAL-10, VIEW-TKINTER-18, WEB-19, REDPERCENT-18. The only code left is
REDPERCENT-17 (plot-type selection in Tk and Web, low severity, feature work)
and REDPERCENT-19's remaining clause — one change in `app.js` to honour the
`format` key the model now declares, plus Tk's monitor button not following
FULL STOP. That is the whole backlog.

**The pattern that ran through this entire wave: correct code with no
caller.** Three separate findings turned out to be this same shape, and it is
now worth treating as a first-class suspicion when reading any finding here:

- **ROTATOR-6** (wave 6): `poll_status` was correct and nothing called it.
- **MANAGER-23**: `full_stop_all` already reported unconfirmed devices through
  `ErrorRouter` to a bus **both** desktop views subscribe to. The reporting
  was correct — and unreachable, because before MANAGER-21 the `unconfirmed`
  list could never be populated. Fixing the confirmation contract switched the
  reporting back on. The row closed on a test, not a patch.
- **MANAGER-22**: `clear_estop` was correct and had no caller anywhere.

A grep for "is this implemented" answers the wrong question in this codebase.
The right one is "does anything reach it".

**MANAGER-24 came from the second fresh-eyes audit and is the best argument
yet for running that pass.** `SystemManager.hide()` promises in its own
docstring, citing D-2, that hiding a device stops motion and de-energizes it —
and finds that action by `getattr(model, "disable", None)`. `disable()` existed
only on `BaseProbe`. Closing the Temperature Controller's tab therefore sent
nothing and reported success, leaving the heater driving toward its last
setpoint unobserved. The duck-typed lookup is the actual defect: it makes a
model that *cannot* reach a safe state indistinguishable from one that just
*did*, at the single call site promising otherwise.

That audit also produced a **firmware claim table** — every claim `src/` makes
about the `.ino` sources, checked against them. Six claims agree (including
the three power-down bytes and the heater's no-watchdog admission), one
**differs** and is worth the owner's attention: `write_command`'s priority
path reasons that its worst case is "a mangled *stop*", but both binary
firmwares do `Serial.readBytes(..., BINARY_PACKET_SIZE)` with no framing check
past the `0xAA` marker, so a `'d'` landing inside that window is consumed as
packet payload and never surfaces as a command at all — a *lost* stop, not a
mangled one. Whether concurrent unsynchronized `pyserial` writes can actually
interleave at byte level on this app's three target platforms is unsettled
from source and is a bench question.

**Process notes.**

- The write-set containment failure from wave 6 did **not** recur. Both lanes
  committed to their own branches, and the brief's explicit "verify with
  `git log` from that worktree" instruction is cheap enough to keep
  permanently.
- **The REDPERCENT lane's JS evidence was verified, not taken on report.** It
  claimed a Node `vm` harness proving the plotter fix. The lead ran it against
  HEAD (passes) and against the pre-fix `app.js` (fails, with both expected
  messages). It holds. This is the first lane on this branch to produce
  executable evidence for `app.js`, which has no other coverage in this repo.
- **I clipped two ledger rows** (REDPERCENT-14, REDPERCENT-20) with a
  sloppy row-replacement helper: rows whose status was a bare `open |` with no
  parenthetical caused it to consume through to the *next* row's terminator.
  Caught by diffing row IDs against HEAD before committing, and restored. Any
  bulk edit of this table should be followed by that diff.
- **I leaked sampler threads onto the global EventBus** from two new test
  files by arming probes over `MagicMock` transports, whose `read_position()`
  returns a MagicMock that the sampler then compares against an int — raising
  every tick and flooding the bus. It broke
  `tests/web/test_web_server.py::test_api_logs_and_errors` under random
  ordering. Fixed by using the canonical SIM-probe double and stopping loops
  in `finally`. **But the REDPERCENT lane observed the same failure
  independently, at a base that did not contain my tests**, so there is very
  likely a second source of error-bus pollution still in the suite. Recorded
  as an open observation, not as fixed.

**Ledger correction, 2026-09-21, before wave 8.** `d853d4a` filed the D-8a
row as **WEB-20**, an ID already held by a closed RC-1 row with its own test
files (`tests/web/test_web_20_*.py`). The D-8a row is renamed **WEB-23**; the
closed WEB-20 is untouched. The five second-audit findings that were only
prose in the wave 7 log are now rows: **MANAGER-25**, **PYSIDE-21**,
**SERIAL-23** (bench), **TEMP-17**, **WEB-24**. Ledger **212 closed / 17 open /
229 total**.

## Finding ledger

218 rows: the 213 findings of the 2026-09-19 audit, plus five added later.
**REDPERCENT-21, 22 and 23** came from owner instruction on 2026-09-20.
**GAMEPAD-21** and **SERIAL-20** were found during the 2026-09-20 fix wave,
by the `fix-input` and `fix-transport` agents respectively, and each was
confirmed by the lead before recording. All five carry a `Source:` line in
their audit entry saying so — none was produced by the audit pass, and none
may be cited as its evidence.

`Closed by` is `root cause` when the finding closes because the structure
changed, `explicit` when it is fixed and named individually. Generated from
`root-causes.md`'s cross-reference table — if you add a finding there,
regenerate rather than hand-editing, so nothing is dropped. A finding whose
stage was reassigned during execution goes in `gen_ledger.py`'s
`STAGE_OVERRIDE`, or the next regeneration reverts it.

Status: `open` · `closed` (with the test or verification note that proves
it) · `n/a` (with a reason).

| Finding | Root cause | Stage | Closed by | Status |
|---|---|---|---|---|
| DC-1 | RC3 | S7 | root cause | closed (test_i_3_3_the_interlock_no_longer_defers_on_stepping) |
| DC-2 | RC1 | S2 | root cause | closed (close_device_view no longer tears down; test_i_1_5_active_models_written_only_by_system_manager) |
| DC-3 | RC1 | S2 | root cause | closed (test_probe_teardown_order_is_stop_then_poller_then_transport, tests/core/test_lifecycle_teardown.py) |
| DC-4 | RC6 | S9 | root cause | closed (S9 item 2: Param table declares type and bounds; tests/core/test_typed_params.py) |
| DC-5 | RC4 | S5 | root cause | closed (**already fixed by RC-4; the row had simply never been flipped** — trap #2 in its usual direction. The finding says Web has no manual-mode routing, so "Enter Manual Mode" energizes the coils, sets `manual_flag` (which suppresses the idle watchdog, so they stay energized), and no stick input ever reaches the stage. RC-4 moved the input pump and poller startup out of the views into `BaseProbe.start_loops`, reached from `_transition` on entry to any mode, and its docstring names this exact case: "Every frontend reaches this through the same path — including the Web dashboard, which had no input pump of its own at all." Verified by tracing the Web path end to end and pinned at the real seam: the tests drive `WebModelAdapter.dispatch_command`, not a hand-arranged `start_loops()` call, so they go red if the Web path stops reaching it. test_web_dispatch_toggle_manual_starts_polling, test_web_dispatch_toggle_auton_starts_polling, test_web_manual_mode_input_pump_runs, test_web_model_inherits_poller_from_model_init. Necessarily green from the start, there being no fix to precede them) |
| DC-6 | RC3 | S10 | root cause | closed (**the model enforces `disabled_when`, not only the render.** Every `PARAMS` attribute is a mode-gated property whose setter refuses while autonomous or manual, and `BaseProbe.execute_command` refuses a gated command *before* `apply_inputs`, so a refusal is a `Refused` and never a raise. The stop toggle is deliberately ungated, or the operator could not leave the mode. 10 tests incl. test_an_entry_is_refused_while_autonomous, test_start_stepping_is_refused_while_already_autonomous, test_stop_still_works_while_autonomous, test_the_gate_does_not_touch_value_validation. **Lead joined the web seam on merge**: the setter's `ValueError` was landing as a 500, reporting a working interlock as a server fault — now 403, per STEPPER-11's precedent. test_a_mode_gated_write_is_refused_with_403_not_500, test_a_genuine_fault_is_still_500) |
| DC-7 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| DC-8 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| DC-9 | RC1 | S2 | root cause | closed (hide/show; test_hiding_does_not_release_the_model, tests/core/test_hide_show.py) |
| DC-10 | RC3 | S7 | root cause | closed (D-2 ruled disable; test_d_2_leaving_a_mode_disables_the_coils) |
| DC-11 | RC7 / RC3 | S10 | root cause | closed (RC-3 flags half closed in S7; the RC-7 half closes here — the web write allowlist admits only `entry` elements, so toggles and dropdowns can no longer be driven through `set_device_attribute` around the commands that enforce the interlocks. test_dc_11_readonly_not_writable, test_dc_11_toggle_not_writable, test_dc_11_dropdown_not_writable, test_dc_11_entry_is_writable) |
| DC-12 | RC9 | S12 | root cause | closed (S12 item 3: disabled devices are never built; test_disabled_devices_are_not_constructed + test_i_9_3_the_disabled_in_setup_flag_is_gone) |
| DC-13 | RC2 / RC7 | S3 | root cause | closed (test_the_badge_cannot_be_faked_by_typing_SIM_into_the_port_field, tests/web/test_web_security.py) |
| DC-14 | RC7 | S1 | root cause | closed (test_d11_serial_port_is_readonly_in_every_schema) |
| DC-15 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| DC-16 | RC4 | S5 | root cause | closed (S5: `pyside/view.py` keeps exactly one QTimer; the position, status and manual-input timers are deleted. test_view_keeps_one_render_tick_and_no_device_loops) |
| DC-17 | RC4 / RC3 | S5 | root cause | closed (RC-4 half in S5; RC-3 half in S7 — mode transitions own their side effects, tests/core/test_probe_mode.py) |
| DC-18 | RC5 / RC2 | S8 | root cause | closed (already fixed in code by S7/S8 and only unpinned: `power_down` writes `k` through the locked priority path, and the four racing booleans the audit named were replaced by the single `_mode` under `_mode_lock`. Pinned by test_dc_18_kill_coils_goes_through_the_locked_priority_path, test_dc_18_no_model_writes_around_the_transport_lock, test_dc_18_the_probe_mode_has_one_writer_under_one_lock) |
| DC-19 | RC7 | S10 | root cause | closed (same fix as GAMEPAD-6: the blank option is `disabled hidden` and the handler refuses an empty value; test_gamepad_6_the_dropdown_placeholder_cannot_be_reselected, test_gamepad_6_the_dispatch_handler_ignores_a_blank_value) |
| ERRORS-1 | RC8 / RC7 | S11 | root cause | closed (`CommandResult`; test_i_8_1_a_refused_command_is_distinguishable_from_a_successful_one) |
| ERRORS-2 | RC8 / RC10 | S11 | root cause | closed (`/api/errors?since=`; test_api_logs_and_errors, re-authored — it used to assert the destructive read) |
| ERRORS-3 | RC8 | S11 | root cause | closed (both remaining halves: `WebErrorManager` echoes to the console even once a subscriber exists, and `initializeErrorTracking()` sets `lastErrorId` to the latest before polling so a new tab no longer replays all history as toasts through `since=0`. test_errors_3_console_echo, test_errors_3_no_flood_on_init) |
| ERRORS-4 | RC8 | S11 | root cause | closed (`install_exception_hooks`; test_i_8_3_every_launcher_installs_the_same_hooks, test_a_thread_exception_reaches_the_bus) |
| ERRORS-5 | RC8 | S11 | root cause | closed (manager binds to the process-lifetime root, never the dashboard; the `after` loop reschedules in a `finally`) |
| ERRORS-6 | RC2 | S3 | root cause | closed (S3: serial write failures raise TransportError and the models fault rather than swallow; test_an_unconfirmed_disable_is_a_fault_not_a_disabled_claim, test_a_failed_disable_still_hides) |
| ERRORS-7 | RC2 / RC8 / RC11 | S3 | root cause | closed (**all five shares now done.** `probes.py`, `serial.py` and `temperature_system.py` closed in earlier waves. `redpercent_system.py` closed here: five genuinely operator-facing silent sites routed through `ErrorRouter` (position-source change, autosave success, explicit-save "nothing to save", explicit-save success, and a previously entirely silent "monitor thread still alive after the teardown join timeout"), with the sites left print-only justified individually in the source — autosave's routine no-data case, the dialog-cancelled case, the unreachable sync-toggle guards, and `_read_dim`'s REDPERCENT-16 sentinel at monitor-loop rate, which is the popup flood RC-8 exists to prevent. `rotator_system.py` closed by the lead, and its defect was the sharpest instance of ERRORS-7 in the branch: `_async_wrapper` refused to dispatch while the FULL STOP latch was set — correctly, the stage did not move — but did so with a bare `print` **on the worker thread**, so `home()` returned `None` to `execute_command`, `as_result(None)` became `Ok`, and clicking "Home Stage" on a latched rotator reported success for an action it had refused, on the one device the operator had just emergency-stopped. The check is now made **synchronously in `_run_async`**, before the thread is spawned, which is what lets the refusal reach a frontend at all; the worker-side checks stay as prints because their return value reaches nobody, and they still earn their place catching a latch that lands between dispatch and execution. 5 tests in tests/core/test_errors7_rotator_share.py, 3 red beforehand, including the negative case — an unlatched rotator still homes — and the safety case, that the latch still blocks the hardware) |

| ERRORS-8 | RC8 | S11 | root cause | closed (locked bus, key is `(severity, source, title)`; test_publishing_from_many_threads_loses_nothing, test_repeats_fold_into_one_event_with_a_count) |
| ERRORS-9 | RC8 | S11 | root cause | closed (PySide's three CSV surfaces — load failure, missing Red Percent column, nothing to save — report through the bus instead of raising their own `QMessageBox`. **The agent reported this `closed` on "code inspection; no standalone test created" and the row stayed open until the lead wrote one**: test_the_csv_surfaces_do_not_raise_their_own_modal, test_the_csv_load_path_reports_through_the_error_router. The change also broke PYSIDE-18's qt test, which asserted on the very modal this finding removes; that test now asserts on the bus) |
| ERRORS-10 | RC8 | S11 | root cause | closed (the rate limit no longer runs ahead of the no-subscriber print; test_with_no_subscriber_the_bus_prints) |
| ERRORS-11 | doc | S0 | root cause | closed (verification note, lead 2026-09-21: `error-routing.md`'s header, API surface and per-frontend wiring rewritten against `b95a379`. **The finding understated it.** It asked for five table rows to be corrected; the file actually documented an API that no longer exists — `set_callbacks`, the three `_error_cb`/`_warning_cb`/`_info_cb` slots, `_last_messages` and the `_is_spam` 5 s text dedup were all replaced wholesale by S11's `EventBus` (RC-8), and the stated length of 54 lines is now 340. Rate limiting in particular is documented backwards: the real fold keys on `(severity, source, title)` — not message text — over per-severity windows of 30 s/60 s, and increments a `count` rather than dropping. **Four of the finding's own claims had themselves gone stale** and are recorded in a new "verdicts that are now wrong" table: `serial.enable`/`disable` both raise now rather than swallowing; `threading.excepthook` *is* set, in the shared `install_exception_hooks`; the gamepad-thread correction is itself outdated because RC-13/S5 gave the poller its own daemon clock; and the `_is_spam` cap argument is moot. Line-number citations replaced with symbol anchors plus a regenerable per-file index, which addresses the finding's actual failure scenario — "an agent delegated the 455/457 style rows edits the wrong lines" — instead of resetting a clock that rots on the next commit. The stale addendum was deleted; the old tables are kept, clearly marked as a 2026-09-18 snapshot, for their reasoning rather than their line numbers.) |
| ERRORS-12 | RC10 | S4 | root cause | closed (the `_BufferProxy` indirection and the web error mirror are retired; the ledger's note that the behavioural defect was already gone held, but one half was still live — a `WebDashboardServer` built with a manager made its own adapter while `WebAPIHandler.adapter` only became that object in `start()`, so poller logs emitted before then went to an adapter `/api/logs` never read — test_api_errors_survives_an_adapter_replacement, test_the_web_error_path_keeps_no_second_copy_of_the_bus, test_web_error_manager_shims_publish_to_the_bus, test_poller_logs_reach_the_server_that_serves_them) |
| GAMEPAD-1 | RC4 | S5 | root cause | closed (S5: the model owns the input loop and the poller; test_polling_continues_without_a_tk_event_loop) |
| GAMEPAD-2 | RC13 | S5 | root cause | closed (S5: the refcount is gone, SDL is per-owner in InputService; test_closing_a_poller_never_tears_sdl_down, test_sdl_comes_down_only_at_process_exit) |
| GAMEPAD-3 | RC3 | S7 | root cause | closed (test_a_failed_controller_swap_does_not_claim_the_controller) |
| GAMEPAD-4 | RC3 | S7 | root cause | closed (test_a_failed_controller_swap_does_not_claim_the_controller) |
| GAMEPAD-5 | RC13 / RC7 | S5 | root cause | open (partly closed: the `None` entry is back — `discover_controllers` returns `["None"] + input_service.names()` — and claim filtering is tested by test_controller_claim_conflict. **Live refresh and rebind-after-disconnect are not verified**; they need S14 or a bench run.) |
| GAMEPAD-6 | RC7 | S10 | root cause | closed (the placeholder is `disabled hidden` and the handler refuses a blank value; test_gamepad_6_the_dropdown_placeholder_cannot_be_reselected, test_gamepad_6_the_dispatch_handler_ignores_a_blank_value) |
| GAMEPAD-7 | RC4 | S5 | root cause | closed (test_a_stale_poll_chain_stops_when_polling_is_restarted, test_a_controller_swap_does_not_start_a_second_poll_chain, test_a_restart_does_not_leave_a_second_poll_thread_running) |
| GAMEPAD-8 | RC4 | S5 | root cause | closed (D-4 input gate replaces flush_neutral; test_d4_a_closed_gate_stops_the_manual_pump_without_stopping_the_mode) |
| GAMEPAD-9 | RC1 | S2 | root cause | closed (loops moved to the models; test_manual_mode_drives_hardware_with_no_gui_at_all, tests/core/test_model_owned_loops.py) |
| GAMEPAD-10 | RC13 | S5 | root cause | closed (S12: one claims dict per build, derived from real acquisitions; test_build_models_owns_its_claims_dict, test_two_pollers_cannot_claim_the_same_controller) |
| GAMEPAD-11 | RC12 | S16 | explicit | open |
| GAMEPAD-12 | RC12 | S16 | explicit | open |
| GAMEPAD-13 | RC12 | S16 | explicit | open |
| GAMEPAD-14 | RC12 | S16 | explicit | open |
| GAMEPAD-15 | RC13 / RC11 | S5 | root cause | closed (S5: edges latch under `_state_lock` and drain once; test_a_tap_shorter_than_a_read_interval_is_not_lost, test_edges_drain_exactly_once, test_reading_levels_does_not_consume_edges, test_levels_never_carry_edge_keys) |
| GAMEPAD-16 | RC4 | S5 | root cause | closed by **D-12** — the owner ruled the 200 Hz poll and the 20 ms manual pump deliberately unequal. Verification note: `gamepad.py` POLL_INTERVAL and `probes.py` MANUAL_COMMAND_INTERVAL carry the ruling in comments. No test; none is wanted, since a test would pin a number the owner may retune. |
| GAMEPAD-17 | LOCAL-OK | S15 | explicit | closed (the last clause — `change_controller` and `parse_controller_id` kept alive only by their own tests — is done. Both were dead production code: `parse_controller_id` (src/app.py) had no caller in `src/` and five tests; `change_controller` (gamepad.py) had no caller in `src/` and two, and the live path is the schema-dispatched `set_controller`. **Reachability was checked for indirect callers, not just literal call syntax** — this codebase dispatches schema commands by name through `execute_command` → `getattr(self, name)`, so the string forms were searched across `src/` including the JS and the schema declarations, and neither appears. Both deleted with their anchor tests; `test_baseprobe_constructed_with_string_id` was kept because it exercises real surrounding behaviour rather than the deleted function. −83 lines, and the gate drops by exactly the 7 deleted tests with nothing else moving, which is the check that no live caller was missed. A test that keeps dead code alive is not coverage, it is an anchor) |

| GAMEPAD-18 | RC13 / RC9 | S5 | root cause | closed (S12: in-process enumeration through InputService; test_discover_controllers_never_shells_out, test_discover_controllers_fabricates_nothing) |
| GAMEPAD-19 | RC13 | S5 | root cause | closed (test_macos_presence_check_does_not_consult_the_previous_controller, test_macos_swap_succeeds_when_the_previous_controller_is_gone; the consequence was a rejected bind, not a missed unplug) |
| GAMEPAD-20 | doc | S0 | root cause | closed (verification note, lead 2026-09-21: the blocking fifth bullet is done — the superseded transient-falsy hypothesis now carries an inline SUPERSEDED pointer to GAMEPAD-4 at the claim itself in `known-issues.md`, not only in the file header. The other four claims were already corrected in `controllers.md`; re-verified rather than taken on trust — its `ControllerPoller` inventory was re-resolved symbol-by-symbol against `388834d` with an ast resolver, 22/22 line numbers exact, plus `POLL_INTERVAL` 254, `EDGE_KEYS` 572, `get_gamepad_wrapper` 229, file length 801, `lifecycle.py:69-70`. Docs-accuracy row: a verification note is the appropriate closure, not a test name.) |
| GAMEPAD-21 | RC13 | S5 | root cause | closed (test_manual_input_is_still_live_after_a_swap_on_the_threaded_clock, test_change_controller_resumes_the_threaded_clock_too, test_a_swap_does_not_start_polling_on_a_poller_that_was_not_polling, test_a_swap_on_the_tk_clock_still_resumes_and_still_uses_after) |
| MANAGER-1 | RC1 / RC10 | S2 | root cause | closed (test_shutdown_resolves_the_manager_when_it_fires_not_when_installed) |
| MANAGER-2 | RC1 / RC10 | S2 | root cause | closed (tests/core/test_lifecycle_exit.py) |
| MANAGER-3 | RC1 | S2 | root cause | closed (tests/core/test_lifecycle_exit.py) |
| MANAGER-4 | RC1 / RC10 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| MANAGER-5 | RC1 | S2 | root cause | closed (test_build_models_tears_down_partial_work_when_a_later_device_fails, tests/core/test_app_bootstrap.py) |
| MANAGER-6 | RC1 | S2 | root cause | closed (verified by inspection: app.py builds before withdraw and reports failure; no automated coverage of the Tk setup window) |
| MANAGER-7 | RC1 | S2 | root cause | closed (close_device_view no longer tears down; test_i_1_5_active_models_written_only_by_system_manager) |
| MANAGER-8 | RC1 | S2 | root cause | closed (view no longer constructs models; open_device_view refuses an unconfigured device) |
| MANAGER-9 | RC1 | S2 | root cause | closed (test_i_1_5_active_models_written_only_by_system_manager) |
| MANAGER-10 | RC1 / RC5 | S2 | root cause | closed (test_rotator_teardown_sends_stop_before_disconnecting, tests/core/test_lifecycle_teardown.py) |
| MANAGER-11 | RC1 | S2 | root cause | closed (reboot_model deleted; test_system_manager_reconfigure_replaces_the_model_set) |
| MANAGER-12 | RC9 | S12 | root cause | closed (same: the flag is gone, so it cannot read stale; test_i_9_3_the_disabled_in_setup_flag_is_gone) |
| MANAGER-13 | RC4 / RC10 | S5 | root cause | closed (two parts, both settled. **Part 1, the manual-routing half, is DC-5's** and was already fixed by RC-4 — same evidence, same tests. **Part 2's premise is stale.** The finding calls the `log_updater` loop in `WebDashboardWindow.__init__` dead code because the manager is empty when it runs. The loop is still there and is still a no-op in the common path — *deliberately*, and the code says why: it covers a manager built before the window (WEB-5), while the wiring for models built later lives in `WebModelAdapter._initialize_setup_locked`, which the comment directly above the loop already points at. Deleting it would remove correct handling of a real case. **Left in place on purpose** — trap #1: the audit called it dead, the code documents why it is not. The log path itself is covered by the existing test_poller_logs_reach_the_server_that_serves_them. The lane reached the same conclusion but declined the edit for the wrong reason, believing `web_view.py` was outside its write set when it was squarely inside it) |
| MANAGER-14 | LOCAL-OK | S1 | explicit | closed (test_d9_macos_defaults_to_tkinter, test_manager14_launcher_rejects_unknown_flags) |
| MANAGER-15 | RC10 | S4 | root cause | closed (test_window_and_server_read_the_live_manager_not_a_stored_copy, tests/web/test_web_security.py) |
| MANAGER-16 | RC13 | S5 | root cause | closed in S12 (`build_models` owns one claims dict per build, so no dict survives a relaunch; test_build_models_owns_its_claims_dict). `ControllerPoller.close()` still does not pop its own entry, which is now unreachable: the only paths that release a model rebuild with a fresh dict.) |
| MANAGER-17 | RC8 | S11 | root cause | closed (same fix as ERRORS-5) |
| MANAGER-18 | RC9 | S12 | root cause | closed (S12 item 1: one composition root; test_discover_controllers_never_shells_out, test_discover_controllers_fabricates_nothing, test_i_9_2_the_same_configs_produce_the_same_manager) |
| MANAGER-19 | RC5 | S8 | root cause | closed (S8: test_full_stop_returns_even_if_a_model_never_finishes) |
| MANAGER-24 | RC5 / RC10 | S8 | audit-2026-09-21b | closed (**hiding the heater or the rotator brought neither to a safe state, and said it had.** `SystemManager.hide()` promises in its own docstring, citing D-2, that "motion stops and the coils are de-energized" — and locates that action by `disable = getattr(model, "disable", None)`. `disable()` was defined **only on `BaseProbe`**, so for `TemperatureSystem` and `RotatorSystem` the `if callable(...)` guard was silently False, nothing reached the hardware, and `hide()` returned True regardless. An operator middle-clicking the Temperature Controller's tab — the same gesture that safely de-energizes a stepper, DC or chuck probe — left the heater driving toward its last setpoint, unobserved, with no error, no warning and nothing in the log. Same gesture on the rotator neither stopped nor de-energized the stage. Both models now have a `disable()` that delegates to their existing `stop()` (setpoint zero for the heater, halt-in-place for the stage) and returns whether it landed, per MANAGER-21. The rotator's deliberately does **not** set the latch: hiding is not a FULL STOP, and a device shown again must be usable without clearing a latch the operator never knowingly set. **The deeper defect was the duck-typed lookup**, which made a model that *cannot* reach a safe state indistinguishable from one that just *did*, at the single call site promising the opposite — so the empty `else` is now a report ("hidden but has no disable() — it was NOT brought to a safe state and may still be energized"), not a silent skip. The existing hide/show tests never covered this in either direction because their `FakeDevice` defines `disable`. 7 tests, 5 red beforehand, in tests/core/test_manager24_hide_brings_hardware_safe.py; the existing "a failing disable still hides the view" contract is preserved and asserted. Found by the second fresh-eyes audit, 2026-09-21) |
| MANAGER-25 | RC5 | S8 | audit-2026-09-21 | closed (second fresh-eyes audit. `shutdown_all()` ran `_stop_then_teardown` per device in sequence, so anything holding the thread inside one device's `teardown()` — a slow drain, or PYSIDE-21's modal — left every later-registered device unstopped; the heater registers before the rotator. It now stops **every** device first through `_stop_concurrently`, the bounded fan-out extracted from `full_stop_all` so both share one FULL_STOP_BUDGET, reports any unconfirmed stop, and only then tears down. test_every_device_is_stopped_before_any_teardown_runs, test_a_teardown_that_blocks_cannot_leave_a_later_device_unstopped, test_a_hung_stop_does_not_keep_the_others_from_being_stopped — all three failed on 73c591a. PYSIDE-21 remains open as the view-side half) |
| MANAGER-21 | RC5 | S8 | audit-2026-09-21 | closed (**FULL STOP no longer reports a stop it cannot confirm.** `full_stop_all`'s `_stop` set `ok = True` the instant `emergency_stop()` returned without raising — and every model is built to *always* return inside `ESTOP_RETURN_BUDGET` (0.08 s) whether or not the write landed, printing "still in flight" and returning normally. So `False` was reachable only if `emergency_stop` blocked past `FULL_STOP_BUDGET` (1.0 s), which by construction it cannot, and the Web frontend rendered "FULL STOP confirmed for all devices" over a possibly-still-moving axis. `ManagedModel.emergency_stop` is now typed `-> bool` and all three implementations return whether the stop **completed and reported success** inside the budget; `full_stop_all` reads that answer, and a `None` from any model predating the contract is read as *unconfirmed* rather than silently as success. `TemperatureSystem.stop()` and `RotatorSystem.stop()` gained honest returns to feed it — the heater's no-port branch returns False on purpose, because it already warns "Stop command not sent" and it would be incoherent to contradict that warning in the same breath. **What deliberately did not change:** the latch still sets first, unconditionally, before any I/O, and `emergency_stop` still returns inside its budget — both asserted. `False` means "I cannot confirm this", never "I did not try". **One semantic recorded explicitly in the docstring** so it is not mistaken for a bug later: *confirmed* means the strongest stop this device supports landed, **not** that the coils are dead. The DC probe's firmware has no coil-kill handler at all (`supports_coil_kill` False; SERIAL-10, D-7), so a successful stop zeroes the motion frame and leaves the drivers energized. That is reported separately and loudly by `_report_power_down_unsupported`; folding it in here would mark every DC probe permanently unconfirmed and train the operator to ignore the one signal meant to carry weight. 7 tests, 4 red beforehand, in tests/core/test_manager21_stop_confirmation.py — including `test_manager21_full_stop_all_reports_a_real_unconfirmed_device`, which uses **a real model and a real stalled transport**. That is the whole reason this survived every prior wave: the existing tests mock `emergency_stop` to raise or hand-return `False`, so the real shape — returning successfully without having confirmed — was not reachable through their doubles. `SlowStopModel` and `test_hide_show`'s fixture were updated to honour the contract, which is a double catching up to the code, not a relaxed assertion) |

| MANAGER-22 | RC5 | S8 | audit-2026-09-21 | closed (**a latched FULL STOP can now be cleared from all three frontends.** At feb77fe `clear_estop()` had zero callers — no view, no schema entry, no Web endpoint — so a latched device refused every transition for the life of the process, and the only recoveries were restarting (dropping every other device's connection) or re-running the setup wizard. D-8's client-liveness watchdog can latch a probe *by itself* after `WEB_CLIENT_STOP_TIMEOUT` of web silence while a mode is engaged, and that value is still the unmeasured 15 s placeholder, so this was reachable by an operator doing nothing stranger than switching tabs. **Owner chose the per-device schema button** over a dashboard-wide clear: the latch is per-device state, and clearing one device must not silently re-arm a probe on a tab nobody has looked at. One `sch.button("Clear FULL STOP", "clear_estop", role="warning")` per latching model — `BaseProbe` (so Stepper, DC and Chuck), `TemperatureSystem`, `RotatorSystem` — so all three renderers get it from one declaration per D-6, including the Web client, which is the frontend the auto-latch actually strands. `clear_estop(confirmed=False)` now returns `NeedsConfirmation`, routing through the generic dialog every view already implements for the rotator's ±30° guard, rather than the schema's `confirm=` key, which **no renderer reads** — verified before choosing. It is emphatically not a reinstatement of the per-device "Full Stop" button deliberately removed as a redundant second E-stop: the dashboard's global FULL STOP reaches every model, so a per-tab stop was duplication, whereas a clear has no global equivalent. Clearing returns the device to *refusable*, never to *running*. **Seam pin written before the fix**, 12 tests in tests/core/test_manager22_clear_estop_is_reachable.py, all 12 red at feb77fe: the schema declaration across all five model classes, the unconfirmed call not clearing, the confirmation naming its own command so the views' re-dispatch is not a dead end, the latch actually releasing, `_refuse_if_estopped` going false again, reachability through `execute_command` (the path all three views use), and that clearing does not re-arm. **Three existing tests in test_transport_truth.py were strengthened, not relaxed:** `test_only_an_explicit_operator_action_clears_the_latch` could previously only check that *something* cleared the latch; it now asserts the unconfirmed call clears nothing, which is the assertion its name always promised) |

| MANAGER-23 | RC5 | S8 | audit-2026-09-21 | closed (**premise wrong; the operator was already being told.** The finding says Tk and PySide have no path that could report a device failing to confirm, because both wire FULL STOP straight to the bare `full_stop_all` callable and discard its return. They do discard it — but `full_stop_all` reports the unconfirmed devices *itself*, via `SystemManager._report` → `ErrorRouter.report_error("Stop Not Confirmed", ...)` → the `EventBus`, which **both** desktop views subscribe to (tkinter/view.py:38, pyside/view.py:43) and render as a popup. The information reaches all three frontends; it just does not travel by return value. **Why it looked broken is the interesting part:** before MANAGER-21, `unconfirmed` could never be populated, because `ok` was set True whenever `emergency_stop` did not raise — so the reporting path was correct and *unreachable*, the identical shape to ROTATOR-6, where `poll_status` was correct and had no caller. Fixing the confirmation contract is what switched this back on. Pinned by test_manager23_an_unconfirmed_device_is_reported_to_every_frontend, which subscribes to the real bus and drives a real stalled transport. The one genuine difference from the Web client is the *positive* toast on success, which the desktop views deliberately lack: a modal on every successful FULL STOP is a modal operators learn to dismiss without reading. Recorded as intended, not as a gap) |

| MANAGER-20 | RC4 | S5 | root cause | closed (test_a_running_scanner_thread_stops_when_interruption_is_requested, test_stop_scanner_brings_a_real_running_scan_down_promptly — both qt-marked, written unrun by the agent and **verified passing by the lead** on merge; plus 6 executing tests in tests/core/test_manager20_scan_abort.py for the abort hook. The 17 tests in test_manager20_scanner_wiring.py are AST-structural, not behavioural, and are a regression guard rather than the evidence) |
| PYSIDE-1 | RC1 | S2 | root cause | closed (view no longer constructs models; open_device_view refuses an unconfigured device) |
| PYSIDE-2 | RC1 | S2 | root cause | closed (close_device_view no longer tears down; test_i_1_5_active_models_written_only_by_system_manager) |
| PYSIDE-3 | RC9 | S12 | root cause | closed (S12 item 2: registry events; test_i_9_1_a_released_probe_leaves_available_probes, test_releasing_the_selected_probe_falls_back_to_a_live_one) |
| PYSIDE-4 | RC11 | S13 | root cause | closed (the `confirm_discard` hook S13 item 3 added is installed when the Red Percent dock closes, so D-10's autosave asks the operator Save/Discard at `shutdown_all()` instead of deciding for them. test_pyside4_cleanup_installs_the_discard_hook, test_pyside4_the_hook_reports_the_operators_choice, test_pyside4_teardown_consults_the_hook_and_autosaves_on_save, test_pyside4_cleanup_no_prompt_without_data. **Note what D-1 already did for this finding**: since S6, closing the dock *hides* it and the model keeps its data, so the original "silently discards on close" defect was gone before this row was worked; what remained, and lands here, is the shutdown prompt. **The agent's first version reintroduced RC-1** by calling `model.teardown()` from `cleanup()` to make the hook fire — `cleanup()` runs on the hide path, so that destroyed the device on a dock close. Corrected on merge and pinned by test_the_red_percent_dock_cleanup_never_tears_the_model_down) |
| PYSIDE-5 | RC6 | S9 | root cause | closed (S9 item 2: views read value_type instead of calling float() on the current value) |
| PYSIDE-6 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| PYSIDE-7 | RC7 | S10 | root cause | closed (schema v2 makes the shape inexpressible: test_a_dropdown_cannot_be_declared_without_a_command; the quarantined test XPASSed and was re-authored) |
| PYSIDE-8 | RC7 | S10 | root cause | closed (S10: test_pyside_file_save_passes_the_chosen_path_and_respects_cancel) |
| PYSIDE-9 | RC4 | S5 | root cause | closed (S5: test_qt_dynamic_view_poll_model) |
| PYSIDE-10 | RC8 / RC4 | S11 | root cause | closed (the storm is unreachable: a repeating timer exception folds into one event for 60 s, so one modal, not one per tick. `_poll_model` still has no local try/except — it no longer needs one) |
| PYSIDE-11 | LOCAL-OK | S15 | explicit | closed (S10: the unreachable `continue`-first file_picker branch replaced by the file_save composite) |
| PYSIDE-12 | RC7 | S10 | root cause | closed (the `focus_area` write and the modal-behind-the-overlay closed in S10; the instruction label, crosshair cursor and `setFocus`/`activateWindow` land here. test_pyside_12_selection_overlay_has_instruction_label, test_pyside_12_selection_overlay_has_crosshair_cursor, test_pyside_12_selection_overlay_requests_focus_on_show, test_pyside_12_selection_overlay_escape_closes_without_report, test_pyside_12_selection_overlay_small_drag_not_reported — all five are qt-marked, were UNVERIFIED by the agent, and were **run green by the lead**. Linux transparency stays bench work) |
| PYSIDE-13 | RC1 | S1 | root cause | closed (test_d11_no_runtime_serial_reconnect) |
| PYSIDE-14 | RC4 | S5 | root cause | closed (D-4; deferred activeWindow check distinguishes a child dialog, tests/core/test_tkinter_teardown.py + pyside _app_has_focus) |
| PYSIDE-15 | RC8 | S11 | root cause | closed (`threading.excepthook` now installed by every launcher) |
| PYSIDE-16 | LOCAL-OK | S15 | explicit | closed (FULL STOP is added last in the sidebar and carries its own `fullStopButton` object name with QSS states; the empty `setCentralWidget(QWidget())` that claimed a stretch share is gone; `_last_added_dock` falls back to a surviving dock instead of resetting to None. test_full_stop_is_the_last_widget_in_the_sidebar, test_full_stop_has_its_own_object_name_for_hover_and_pressed_styling, test_no_central_widget_claims_a_stretch_share, test_last_added_dock_falls_back_to_a_remaining_open_dock_on_close, test_last_added_dock_is_none_once_every_dock_is_closed. Verified by the qt pass, 46 passed.) |
| PYSIDE-17 | LOCAL-OK | S15 | explicit | closed (unused imports, orphaned QSS selectors and the `self.layout` shadowing are gone, and the real bug behind them — the `internal` schema element falling through to `addRow` with a never-populated layout, adding a blank row per element — is fixed. test_unused_imports_are_gone_from_pyside_view, test_orphaned_qss_selectors_are_gone, test_self_layout_no_longer_shadows_qwidget_layout, test_internal_schema_element_adds_no_blank_row) |
| PYSIDE-18 | LOCAL-OK | S15 | explicit | closed (`save_log_ui` appends `.csv` where Qt, unlike Tk, does not; `load_csv` rejects on `red_percents` alone so a dims-column-with-no-rows CSV no longer reaches the modal `select_plot_type` — the PYSIDE-12 hang path — and opens with `newline=''`; the CSV metadata block now reaches the plot title. test_save_log_ui_appends_csv_when_the_chosen_name_has_no_suffix, test_save_log_ui_leaves_an_explicit_suffix_alone, test_load_csv_rejects_a_header_only_file_even_with_a_dims_column, test_load_csv_opens_the_file_with_newline_empty_string, test_draw_plot_appends_probe_metadata_to_the_title) |
| PYSIDE-19 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| PYSIDE-20 | RC7 / RC13 | S10 | root cause | closed (S1: test_d11_serial_port_is_readonly_in_every_schema) |
| PYSIDE-21 | RC8 | S11 | audit-2026-09-21 | open (second fresh-eyes audit, strong hypothesis. `QtErrorPopupManager._publish` emits a Qt signal whose `AutoConnection` resolves to a **direct, synchronous** call when the publisher is already on the GUI thread, so a `requires_ack=True` error raised inside `closeEvent → shutdown_all()` (e.g. TemperatureSystem.close()'s "Heater Off Not Delivered") opens `QMessageBox.critical()` — a nested event loop — **mid-teardown**. Tk does not have this: its subscriber only enqueues and shows the modal from a `root.after` poll. **Fix shape:** marshal with `Qt.QueuedConnection` so a modal can never open inside a publisher's stack. MANAGER-25 is the model-side half of the same failure) |
| REDPERCENT-1 | RC11 | S13 | root cause | closed (`MonitoringRun` freezes `sync_dimensions` as a tuple at start and the monitor loop reads only that, so the log can no longer alias the model's live list; the `sync_x/y/z` setters and `toggle_sync_*` refuse while a run is active, which also closes the web `setattr` path independently of DC-11's allowlist. test_monitoring_run_freezes_sync_dimensions_as_a_tuple, test_toggle_sync_refuses_while_a_run_is_active, test_direct_setattr_on_sync_x_is_ignored_mid_run, test_toggle_sync_still_works_when_no_run_is_active. Aliasing proven pre-fix: `data_log.sync_dimensions is system.sync_dimensions`) |
| REDPERCENT-2 | RC11 | S13 | root cause | closed (`start_monitoring` builds a new run with a fresh `RedPercentDataLog` and `last_logged_red = -1000.0` unconditionally, replacing `if not self.data_log:`; runs no longer concatenate and `has_unsaved_data` is no longer sticky. test_a_fresh_run_gets_a_fresh_data_log, test_last_logged_red_does_not_carry_over_between_runs. Proven pre-fix: `system.data_log is first_log` still held after a second start) |
| REDPERCENT-3 | RC11 | S13 | root cause | closed (each run owns its own `threading.Event` and its thread closure captures that run rather than re-reading `self._run`, so two runs cannot share a stop flag; the `probes.py:467-480` generation token is carried as well. test_generation_token_invalidates_a_superseded_generation, test_a_stale_generation_stops_the_loop_without_the_stop_event) |
| REDPERCENT-4 | RC11 | S13 | root cause | closed (the `probes.py` share closed earlier — a dead gamepad no longer kills the monitor thread, 8 tests in tests/core/test_redpercent4_velocity_reads.py. The `redpercent_system.py` remainder closes here: the whole loop body is inside `try/except`, an unexpected exception sets the run's `failure` — which is what makes the derived `monitoring` read False — and reports through the bus with `requires_ack`, so a dead thread can no longer leave the UI claiming to monitor. The `mss=None` path is a `Refused` at start rather than an `AttributeError` in the thread. test_an_exception_in_the_loop_clears_monitoring_and_is_reported, test_start_monitoring_refuses_when_a_dependency_is_missing) |
| REDPERCENT-5 | RC11 | S13 | root cause | closed (`current_red` and `red_change` are read-only views onto one `_red_state` tuple swapped in a single assignment, so no poller can read a mismatched pair, and `_publish_red` reads `baseline_red` exactly once per sample instead of twice in one expression. test_publish_red_reads_baseline_exactly_once, test_current_red_and_red_change_always_agree) |
| REDPERCENT-6 | RC11 | S13 | root cause | closed (PySide's "Save Log" calls `RedPercentSystem.save_log` instead of reaching past it to `data_log.save_to_csv`, so late probe-name and tilt edits are synced and the `station_meta.json` sidecar is written — a CSV saved through the dialog is no longer less interpretable than an autosaved one. test_redpercent6_save_log_uses_model_method, test_redpercent6_metadata_synced_before_save) |
| REDPERCENT-7 | RC8 / RC7 | S11 | root cause | closed (result part: refusals are `Refused` and render as refusals) |
| REDPERCENT-8 | RC7 | S10 | root cause | closed (S10: the web probe dropdown carries `command="set_stepper_model"`; test_all_ui_schemas, test_an_interactive_dropdown_always_has_a_command) |
| REDPERCENT-9 | RC11 | S13 | root cause | closed (`start_monitoring` returns a `CommandResult`: `Refused` with no focus area, when already active, or when `mss`/`numpy`/`PIL` is missing; `Ok(run)` otherwise. It no longer starts a thread that can never produce a sample. test_start_monitoring_refuses_without_a_focus_area, test_start_monitoring_refuses_when_already_active, test_start_monitoring_refuses_when_a_dependency_is_missing, test_start_monitoring_ok_returns_the_run. Proven pre-fix: `=== MONITORING STARTED ===` printed with no focus area set) |
| REDPERCENT-10 | RC7 | S10 | root cause | closed (S10: the hand-built duplicate Position Source and Save Log controls are gone, the schema provides both; test_pyside_redpercent_sync_and_probe_controls) |
| REDPERCENT-11 | RC9 / RC1 | S12 | root cause | closed (S12 item 2, same tests; I-9.1 holds by construction — test_i_9_1_only_the_dependent_model_writes_available_probes) |
| REDPERCENT-12 | RC1 | S2 | root cause | closed (hide/show; test_hiding_does_not_release_the_model) |
| REDPERCENT-13 | RC7 | S10 | root cause | closed (the substring-match half — `pollState` pushing a plotter sample for every state attribute whose name merely *contains* "red", sweeping `current_red` and `red_change` into one series — was already fixed and pinned by test_web_7_plotter.py. Two clauses were still live and are fixed: the plotter sampled on every poll regardless of whether a run was active (now gated on a new `monitoring` readonly `ui_schema` attribute, which `WebModelAdapter.get_state` publishes for free once declared), and both "Reset" handlers in app.js touched only client-side state, so the model's baseline never moved (now dispatch `reset_baseline` first). **Proved with a Node `vm` harness** — tests/web/js_redpercent13_plotter_check.js, the same technique as the two existing js_*_check.js harnesses — wrapped by a Python test that skips cleanly where node is absent. Verified by the lead at merge: the harness passes against HEAD and **fails against the pre-fix app.js** with both expected messages, which is real evidence for a file that has no other test coverage in this repo) |
| REDPERCENT-14 | RC6 | S9 | root cause | closed (S9 item 2: redpercent params typed) |

| REDPERCENT-15 | RC9 | S12 | root cause | closed (S12 item 3: no `_disabled_in_setup`, so the filter it broke no longer exists; test_redpercent_get_available_probe_names) |
| REDPERCENT-16 | RC11 | S13 | root cause | closed (velocity is derived from position deltas over timestamps by `_read_dim`, which no longer reads `vel_x`/`vel_y`/`vel_z` at all — the audit's finding that the logged "velocity" was gamepad stick deflection; a poisoned `vel_x` regression-guards it. The CSV gained a `Timestamp` column, and a failed position read is an empty cell, never `0.0` — zero is a position the stage can actually be at. test_velocity_is_derived_from_position_deltas_not_vel_x, test_an_invalid_position_read_is_none_not_zero, test_invalid_sample_is_never_written_as_zero_in_add_entry, test_csv_carries_a_timestamp_column, test_an_invalid_position_survives_as_an_empty_cell_not_a_zero) |
| REDPERCENT-17 | RC7 | S10 | root cause | open |
| REDPERCENT-18 | RC7 | S10 | root cause | open (partly closed: PySide's `SelectionOverlay` coordinate space is verified against `mss` — Qt logical coordinates match physical pixels on the bench machine — and the `focus_area` format is pinned. test_redpercent18_set_focus_area_stores_coordinates, test_redpercent18_focus_area_shown_in_view, test_redpercent18_selection_overlay_coordinates, test_redpercent18_focus_area_compatible_with_mss. **The structural divergence the finding names remains**: Tk is primary-monitor only and Web thumbnails a single monitor, both outside this write set. **HiDPI is an unverified hypothesis** — `devicePixelRatio()` conversion needs a scaled display, which is bench work) |
| REDPERCENT-19 | RC7 | S10 | root cause | open (partly closed: **half the audit's claim is stale** — Tk and PySide already render readonly numerics through `Param.format`/`decimals`, so the raw-float complaint no longer holds for either desktop view. The Web client's `pollState` still does `String(val)` straight off `/api/state` with no formatting step. The model half landed — `current_red` and `red_change` now declare `format=".2f"` in `ui_schema`, which is the audit's second proposed direction and the one matching D-6 — but **no renderer reads that key yet**, so this is a declaration without a consumer and the row stays open on purpose rather than being rounded up. The remaining work is one change in app.js to honour `element.format`. The second clause, Tk's monitor button state not following FULL STOP, is also still open. Reported honestly by the lane with the gap named, which is the right call and worth recording as such) |
| REDPERCENT-20 | LOCAL-OK | S15 | explicit | closed (both halves. Model: the six dead fields, the `__del__` that only printed, and the per-call `set_focus_area` print are gone — test_construction_has_no_dead_fields, test_construction_keeps_the_live_equivalents, test_del_prints_nothing, test_set_focus_area_does_not_print, test_no_plot_data_ui_or_set_focus_area_ui_stub_exists. Web: `set_attr` writes only entry/dropdown/toggle elements — test_api_set_attr_refuses_a_readonly_element) |

| REDPERCENT-21 | RC11 | S13 | root cause | closed (the run has a `run_id` and an `output_root` resolved once at import, never from CWD; artifacts land in `output_root/<run_id>/` named `<run_id>_*`. test_redpercent_21_autosave_never_writes_a_bare_relative_path, test_redpercent_21_every_artifact_of_a_run_carries_the_run_id, test_redpercent_21_the_output_root_does_not_follow_the_process_cwd, test_redpercent_21_an_unset_run_id_still_produces_a_unique_directory) |
| REDPERCENT-22 | RC11 | S13 | root cause | closed (the CSV is a plain rectangle; configuration moved to `<run_id>_station_meta.json` carrying baseline, focus-area px, threshold, cadence and start/stop. Legacy `#`-block files still load. test_redpercent_22_the_csv_is_a_rectangle_a_default_reader_opens, test_redpercent_22_the_sidecar_carries_what_the_csv_cannot, test_redpercent_22_a_legacy_csv_with_a_comment_block_still_loads, test_redpercent_22_a_plain_csv_parses_and_reads_its_metadata_from_the_sidecar, test_redpercent_22_probe_tilt_angle_is_the_float_its_param_declares, test_csv_carries_no_metadata_block) |
| REDPERCENT-23 | RC11 | S13 | root cause | closed (`ANNOTATION_FIELDS` is a Param table rendered by the schema in all three views (D-6); values snapshot into the sidecar under `annotations`, never merged with the actuals. test_redpercent_23_operator_annotations_reach_the_sidecar, test_redpercent_23_intended_and_actual_never_share_a_field, test_redpercent_23_the_annotation_set_is_a_table_not_hardcoded_attributes, test_redpercent_23_annotations_are_rendered_by_the_schema_not_per_view) |
| ROTATOR-1 | RC1 / RC5 | S2 | root cause | closed (test_rotator_teardown_sends_stop_before_disconnecting, tests/core/test_lifecycle_teardown.py) |
| ROTATOR-2 | RC10 | S14 | root cause | closed (test_shutdown_resolves_the_manager_when_it_fires_not_when_installed) |
| ROTATOR-3 | RC8 / RC7 | S11 | root cause | closed (a >30° refusal is a `Refused` carrying its reason, not a silent `None`) |
| ROTATOR-4 | RC5 | S8 | root cause | closed (`_current_position` turned an *unknown* position into `0.0`, and a relative target read from the last poll could not see a move already in flight, so stacked clicks walked past the soft limit unprompted. Replaced by `_reference_position` returning `(value, known)` plus a `_commanded_target` committed before dispatch; unknown now raises NeedsConfirmation instead of assuming the origin. test_rotator_4_an_unknown_position_is_not_the_origin, test_rotator_4_stacked_clicks_accumulate_toward_the_guard, test_rotator_4_the_commanded_target_tracks_accepted_moves, test_rotator_4_a_full_stop_makes_the_position_unknown_again, test_rotator_4_an_absolute_move_past_the_limit_still_asks) |
| ROTATOR-5 | RC1 | S2 | root cause | closed (view no longer constructs models; open_device_view refuses an unconfigured device) |
| ROTATOR-6 | RC4 | S5 | root cause | closed (**the audit's two named halves were already fixed, and the finding was still real for a third reason it never states.** GUI-thread polling went with S5/RC-4 — no view calls `poll_status`, and `test_model_interactions` asserts it is not called. FULL STOP queuing behind a poll went with ROTATOR-8/S8. What remained: `poll_status` is the only writer of live `position`/`state` and had **no caller anywhere in `src/`** — the view timers were removed and nothing replaced them, so the rotator card published whatever `connect()` wrote and never moved again, on all three frontends, while `web_adapter.get_state` documented that it reads a cache the model fills "on its own thread" — a thread that existed for `BaseProbe` and not for this model. At the bench that reads as: home the stage, watch it turn, and the position field does not move; the operator cannot tell a turning stage from a wedged one, which is exactly when someone reaches for FULL STOP. `RotatorSystem` now has the sampler `BaseProbe` got in S5: daemon thread, `Event`-based stop, exception-isolated, idempotent, started from `connect()` (this model has no `enable()`) and stopped first and unconditionally in `disconnect()`. `SAMPLE_INTERVAL` is 0.25 s, argued rather than copied — 10 Hz suits a gamepad-jogged probe where latency is felt directly; this refreshes two glanceable fields on a device whose `TS?` transaction can run ~0.5 s. A non-blocking `_poll_busy` skips a tick instead of queuing, because stacked polls make the display *more* stale, not less. **Seam pin written by the lead before either half was briefed**, per the WEB-19 rule: test_rotator6_position_is_published_without_anyone_calling_poll_status, test_rotator6_the_published_position_keeps_tracking_the_hardware, test_rotator6_a_wedged_poll_does_not_delay_full_stop, test_rotator6_disconnect_stops_the_sampler, test_rotator6_an_unconnected_model_runs_no_sampler_thread — 4 red at the pin, 5 green after, pin file untouched (verified by diff). Plus 4 lane tests in test_rotator6_sampler_extras.py. **The lane also found a race the brief did not name:** `poll_status` re-read `self.smc` between its two device calls, so a concurrent `disconnect()` turned an ordinary disconnect into an `AttributeError`; it now snapshots once) |
| ROTATOR-7 | RC4 / RC10 | S5 | root cause | closed (test_stop_command_is_not_serialized_behind_a_slow_command, test_emergency_stop_command_is_not_serialized_behind_a_slow_command, test_get_state_is_not_stalled_by_an_in_flight_command, test_ordinary_commands_still_serialize_on_the_device_lock) |
| ROTATOR-8 | RC5 | S8 | root cause | closed (the rotator had no `_estop` latch at all and ran its stop I/O on the calling thread, so a FULL STOP blocked on the SMC100 `_serial_lock` held by an in-flight poll. Now the S8 probe pattern: latch first, hardware stop on a daemon worker joined against ESTOP_RETURN_BUDGET, and `SMC100.stop(priority=True)` taking `_serial_lock` with PRIORITY_LOCK_TIMEOUT and forcing ST through on failure. test_rotator_emergency_stop_returns_within_100ms_behind_a_held_serial_lock, test_rotator_emergency_stop_still_reaches_the_hardware, test_rotator_latches_so_a_queued_move_cannot_land_after_the_stop, test_only_an_explicit_operator_action_clears_the_rotator_latch, test_the_rotator_stop_path_takes_the_priority_write) |
| ROTATOR-9 | RC2 | S3 | root cause | closed (PySide now matches Tk; the two desktop views agree. test_rotator_9_pyside_display_none_as_dashes, test_rotator_9_pyside_display_formats_to_4_decimals, joining the Tk half's test_rotator_9_position_none_displays_as_dashes, test_rotator_9_position_formats_to_4_decimals, test_rotator_9_position_field_is_readonly. Prior state: a failed poll sets `state` to "Communication lost" and clears `position` in the model, and **Tk** now renders a cleared position as `--.--` rather than the literal "None", with numeric values at 4 decimals to match `main`. test_rotator_9_position_none_displays_as_dashes, test_rotator_9_position_formats_to_4_decimals, test_rotator_9_position_field_is_readonly. PySide's formatting was untouched, which is what a single write set owning both view files fixed) |
| ROTATOR-10 | RC1 | S2 | root cause | closed (hide/show; test_hiding_does_not_release_the_model) |
| ROTATOR-11 | RC2 / LOCAL-OK | S3 | explicit | closed (test_smc100_wait_states_does_not_expire_while_the_stage_is_moving, test_smc100_wait_states_still_has_an_absolute_ceiling, test_a_wait_timeout_says_the_stage_was_not_stopped, test_a_failed_move_forgets_where_the_stage_was_going, test_a_successful_move_keeps_its_target) |
| ROTATOR-12 | RC6 | S9 | root cause | closed (S9 item 2: rotator params typed and bounded) |
| ROTATOR-13 | RC2 / RC7 | S3 | root cause | closed (all four halves now land. Both desktop views gate their controls on the link and match the web half's rule — a device with no live link cannot execute anything, so every button/entry/toggle is disabled, STOP included, whose `stop()` is a no-op with `smc` as None. test_rotator_13_pyside_disables_every_control_when_disconnected, test_rotator_13_tk_disables_every_control_when_disconnected, and the negative cases test_rotator_13_pyside_leaves_controls_alone_when_connected, test_rotator_13_tk_leaves_controls_alone_when_connected, which pin that the gate is the link and not a permanent disable. The lane's own tests proved only that `_mode_name()` *returns* "disconnected" — the input to the decision, not the disabling — and were rewritten at merge to drive the real `_sync_gates`. Prior state: the model reports no-connection rather than claiming simulation, refusals surface as errors, and the **web** controls are now force-disabled with a stated reason when the device is disconnected — test_disconnected_device_controls_are_disabled_and_noted. the Tk and PySide shares remained because no wave had owned that pair together) |
| ROTATOR-14 | RC1 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| ROTATOR-16 | RC5 | S8 | audit-2026-09-21 | closed (**the last unbounded hop in the rotator stop path.** `src/lib/smc100.py` opened its port with no `write_timeout`, so pyserial's `None` default applied — block until every byte is accepted, forever — on a port opened `xonxoff=True`, meaning the controller can withhold XON precisely when it is busy or faulted, which is precisely when FULL STOP is being pressed. ROTATOR-8 bounded the *lock acquisition* in `stop(priority=True)` via `PRIORITY_LOCK_TIMEOUT`; the `port.write()` after it was still unbounded, so that fix stopped a stop queuing behind another transaction but not hanging at the wire. `emergency_stop` still returned promptly — its own 0.08 s join — so nothing looked wrong while the `ST` bytes never left and the stage kept turning past the ±30° tubing guard. `WRITE_TIMEOUT_SEC = 0.2`, matching the `write_timeout` already used to identity-probe this same device at this same baud in `app_bootstrap.py`, rather than the unrelated 1 s on `controller/serial.py`, which talks to a different device family. The resulting `SerialTimeoutException` is deliberately **not** caught in `smc100.py`: `RotatorSystem.stop` already wraps the call and routes any exception through `ErrorRouter.report_error` (verified by the lead at rotator_system.py:492-499), so a stop that failed to land is reported rather than silent, without the driver layer taking a dependency on the application's error routing. Proved with a real ~3 s thread deadlock against the pre-fix code — `worker.is_alive()` still True past the join bound — using a fake port modelling pyserial's actual `write_timeout` semantics. tests/core/test_rotator6_sampler_write_timeout.py. Found by the fresh-eyes audit and routed mid-task to the lane that already owned the file) |
| ROTATOR-15 | RC8 | S11 | root cause | closed (the dead hook is deleted — the init and all three `if self.error_callback:` checks are gone from `rotator_system.py`. test_the_dead_callback_hook_is_gone, plus test_rotator15_async_error_routes_through_event_bus_without_callback. **Why it took three waves to delete four lines:** `tests/core/test_rotator15_error_routing.py` asserted that `error_callback` "should still be called for backward compatibility", so every agent that correctly concluded the hook was dead watched that test go red and reported `partly`. The only thing it was backward-compatible *with* was other tests; no production path — not `app_bootstrap.py`, not any of the three views — ever assigned it. The lead removed that assertion and rewrote the two tests that used the hook as their observation seam (`test_edge_mvc_model.py` now watches `ErrorRouter.report_error` instead). Prior state: routing is no longer either/or — `_run_guarded`, `connect` and `stop` now report through `ErrorRouter` unconditionally instead of only when no `error_callback` was set, which is a real repair since a test setting the hook silently suppressed every bus report. test_async_action_failure_routes_through_error_router, test_stop_failure_routes_through_error_router, and the dead hook survived only because deleting it broke tests outside each agent's write set) |
| SERIAL-1 | RC2 | S3 | root cause | closed (test_a_failed_disable_faults_instead_of_claiming_the_system_is_off, tests/core/test_transport_truth.py) |
| SERIAL-2 | RC1 | S2 | root cause | closed (test_probe_teardown_sends_hardware_stop_when_poller_stop_raises, tests/core/test_lifecycle_teardown.py) |
| SERIAL-3 | RC1 | S2 | root cause | closed (close_device_view no longer tears down; test_i_1_5_active_models_written_only_by_system_manager) |
| SERIAL-4 | RC1 | S2 | root cause | closed (hide keeps the transport open; test_hiding_does_not_release_the_model) |
| SERIAL-5 | RC1 / RC10 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| SERIAL-6 | RC4 | S5 | root cause | closed (the boot wait and identity handshake moved to a daemon worker; `__init__` returns as soon as the port opens, so the 1.5–4.5 s per device is off the GUI/request thread. `CONNECTING` and the Web badge's "connecting" mapping already existed (RC-5 item 3) but were unobservable, because the whole wait ran before `__init__` returned — this is what makes them real. test_construction_returns_before_the_handshake_finishes, test_a_priority_write_is_not_delayed_by_an_in_flight_handshake, test_disable_reaches_the_hardware_while_still_connecting, test_a_write_failure_during_connect_still_marks_the_link_lost, test_close_during_connect_is_not_overwritten_by_a_late_handshake_result, test_wait_connected_returns_true_immediately_for_sim, test_wait_connected_returns_true_immediately_when_the_port_never_opened. **Safety checked at merge, not taken on report:** the worker never holds `_lock` across the wait — only the individual write/read calls take it — so a priority write still forces through within `PRIORITY_LOCK_TIMEOUT`; a connect in flight cannot delay or swallow a stop. Autodetection is unaffected because `app_bootstrap.probe_device_at` opens pyserial directly rather than this wrapper, so an early return cannot mis-assign a board. **A latent deadlock was found and fixed with it:** `_mark_lost` acquired `_lock` unconditionally, so a priority write's forced-through failure path could hang behind a handshake write holding it; it now uses the same bounded acquisition the priority path uses, and the new test deadlocked the pre-fix code outright. The one blocking test — `test_an_opened_port_that_never_answered_is_unverified_not_connected`, which asserted the state immediately after construction — was updated by the lead to wait on the `wait_connected` seam, and **strengthened** while there: it now also asserts the link is never VERIFIED *during* the in-flight window, which is SERIAL-7's actual contract) |
| SERIAL-7 | RC2 | S3 | root cause | closed (test_an_opened_port_that_never_answered_is_unverified_not_connected, tests/core/test_transport_truth.py) |
| SERIAL-8 | RC2 | S3 | root cause | closed (test_the_first_write_failure_moves_the_link_to_lost_and_closes_it, test_loss_is_reported_once_not_on_every_subsequent_command, tests/core/test_transport_truth.py) |
| SERIAL-9 | RC2 | S3 | root cause | closed (test_simulator_probes_can_arm_and_disarm, tests/core/test_transport_truth.py) |
| SERIAL-10 | RC2 | S3 | root cause | open (partly closed: the host no longer claims a power-down the firmware never performs — `BaseProbe.FIRMWARE_CONTROL_BYTES` records what each `.ino` is observed to handle and `power_down()` reports sent/unsupported/failed; 9 tests in tests/hardware/test_serial10_power_down_truth.py, the first of which pins that the bytes on the wire are unchanged. **The mis-parse half is unblocked as of 2026-09-21**: it needs a wire terminator on a stop path, which only protocol v2 carries, and **D-7 was answered `adopt`**. It is S16 work — at the bench, after every board is reflashed — and is not delegable) |
| SERIAL-11 | RC2 | S3 | root cause | closed (test_i_2_3_serial_handle_confined_to_transport; all writes go through write_command under the lock) |
| SERIAL-12 | RC4 | S5 | root cause | closed (**the audit's headline half was already fixed**: the manual pump has not run on the GUI thread since RC-4 moved it into `BaseProbe._input_loop`, and there are no longer two view-owned rates to align. What was still live in `send_manual_mode_command`: a `print` of the full binary packet on **every** send — 50 lines/s for as long as manual mode is engaged, drowning every other message — and `ser.flush()` under `_lock` with no bound. On POSIX pyserial's `flush()` is `tcdrain()`, which has no timeout, so a stalled USB CDC endpoint pinned the one lock this module's priority path deliberately refuses to wait on. Both removed; wire format, struct layout, field order and `START_MARKER` untouched and pinned. test_serial12_print_removed, test_serial12_a_blocking_flush_cannot_stall_a_concurrent_lock_acquirer, test_serial12_flush_not_called_in_send, test_serial12_write_still_succeeds, test_serial12_packet_format_unchanged, test_serial12_multiple_rapid_sends. **One lane test was replaced by the lead at merge.** `test_serial12_lock_not_held_during_flush` opened with the comment "Don't make flush block" and then acquired `_lock` *after* the send had returned — trivially free once the call returns, so it passed against the pre-fix code and proved nothing about the defect. The replacement holds the flush open on an `Event` and asks for the lock *during* the send, which is where the hazard lives; mutation-checked by restoring the `flush()` call in a throwaway worktree, where it fails. **Containment note:** this lane committed into the primary worktree instead of its own — see the session log) |
| SERIAL-13 | RC2 | S3 | root cause | closed (test_serial_send_manual_mode_command asserts the 42-byte packet format) |
| SERIAL-14 | RC1 | S1 | root cause | closed (test_d11_no_runtime_serial_reconnect) |
| SERIAL-15 | RC1 | S2 | root cause | closed (tests/core/test_lifecycle_exit.py) |
| SERIAL-16 | RC8 | S11 | root cause | closed (the per-site audit is done, condition by condition, with the full list recorded in the new test file's module docstring. Every site but one was already correct or already justified: `flush()`'s drain failure stays print-only, connect success stays silent per `error-routing.md`, `_handshake`'s write/read failures fold into `_connect_worker`'s single "Operating blind" report, and a malformed `POS:` line stays silent because it is ~10 Hz telemetry and reporting each one is the popup flood RC-8 exists to prevent. The one real gap: `close()` had **no exception handling at all**, so a failing `ser.close()` propagated uncaught into `BaseProbe.teardown()`/`TemperatureSystem.teardown()` — `shutdown_all`'s per-model catch kept it from taking the app down, so the operator heard nothing. test_close_does_not_raise_when_the_handle_fails_to_close, test_close_still_moves_to_closed_even_when_the_handle_fails, test_close_reports_nothing_on_the_ordinary_successful_path. Prior state: the misleading throttle was already gone, repeats folding with a count) |
| SERIAL-17 | RC2 / LOCAL-OK | S3 | explicit | closed (9 tests in tests/hardware/test_serial17_handshake.py; the identity handshake stops flooding `s\n` every 50 ms, stops substring-matching a possibly-truncated `DEV:` line, and stops leaving queued replies behind) |
| SERIAL-18 | RC1 | S2 | root cause | closed (reboot_model deleted; test_system_manager_reconfigure_replaces_the_model_set) |
| SERIAL-19 | doc | S0 | root cause | closed (verification note: doc inaccuracy only; `pyside/view.py:886` passes `None` to serial as documented. No test applicable.) |
| SERIAL-20 | RC2 | S3 | root cause | closed (12 tests in tests/hardware/test_serial_flush.py cover the bounded transport flush; `SimulatedPort.flush` added in the same commit. Note the durable fix proposed in the audit — a test asserting SimulatedPort answers every attribute the transport calls on `.ser` — is **not** done, so the next omission of this shape will still reach an operator) |
| SERIAL-23 | RC2 | S3 | audit-2026-09-21 | open (**bench question.** Second fresh-eyes audit's firmware claim table: the one DIFFER. `write_command`'s priority-path comment reasons its worst case is "a mangled *stop*", but `stepper_firmware.ino` and `high_polling_rate.ino` both `Serial.readBytes(..., BINARY_PACKET_SIZE)` after `0xAA` with no framing check, so a `'d'` landing inside that window is consumed as payload — a **lost** stop. The firmware half is verified by reading; whether two unsynchronized `pyserial` writes can actually interleave at byte level on macOS/Windows is not settleable from source. The comment in `serial.py` must be corrected whatever the bench shows; the firmware fix is D-7 scope) |
| STEPPER-1 | RC1 | S2 | root cause | closed (close_device_view no longer tears down; test_i_1_5_active_models_written_only_by_system_manager) |
| STEPPER-2 | RC4 | S5 | root cause | closed (S5: the model owns the input pump; test_manual_mode_drives_hardware_with_no_gui_at_all) |
| STEPPER-3 | RC1 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| STEPPER-4 | RC2 | S3 | root cause | closed (test_a_failed_disable_faults_instead_of_claiming_the_system_is_off, tests/core/test_transport_truth.py) |
| STEPPER-5 | RC3 | S7 | root cause | closed (test_losing_the_controller_leaves_manual_mode_entirely, tests/core/test_probe_mode.py) |
| STEPPER-6 | RC3 | S7 | root cause | closed (test_i_3_3_the_interlock_no_longer_defers_on_stepping) |
| STEPPER-7 | RC5 / RC3 | S8 | root cause | closed (RC-5 half in S8; RC-3 watchdog-generation half in S7, test_the_watchdog_gets_a_fresh_event_each_arming) |
| STEPPER-8 | RC5 | S8 | root cause | closed (test_stopping_invalidates_a_script_still_in_flight, tests/core/test_transport_truth.py) |
| STEPPER-9 | RC2 / LOCAL-OK | S3 | explicit | closed (10 tests in tests/scripting/test_stepper9_script_validation.py; the script path now sanitises numerics before they become axis commands, as `get_params` already did) |
| STEPPER-10 | RC7 | S10 | root cause | closed (S10: every schema command exists on its model; test_every_command_exists_on_the_model) |
| STEPPER-11 | RC3 / RC6 / RC7 | S9 | root cause | closed (RC-3 flags closed in S7; the RC-6/RC-7 parts were found **already implemented** and needed tests rather than a fix — each probe class declares its own typed, bounded speed fields and there is no class-agnostic default fallback. test_mode_flags_are_not_writable_through_the_web_route, test_a_dc_probes_speed_field_is_declared_with_its_own_type_and_bounds, test_an_unparseable_speed_over_the_web_route_falls_back_to_this_classs_own_default, test_the_defaults_table_has_no_class_agnostic_fallback) |
| STEPPER-12 | RC7 | S1 | root cause | closed (test_d11_serial_port_is_readonly_in_every_schema) |
| STEPPER-13 | RC9 | S12 | root cause | closed (S12 item 2: `released` clears the reference before teardown; test_releasing_the_selected_probe_falls_back_to_a_live_one) |
| STEPPER-14 | RC4 | S5 | root cause | closed (test_serial_read_position) |
| STEPPER-15 | RC4 | S5 | root cause | closed (S5: test_manual_mode_drives_hardware_with_no_gui_at_all, test_the_model_starts_the_poller_itself) |
| TEMP-1 | RC1 / RC10 | S2 | root cause | closed (test_shutdown_resolves_the_manager_when_it_fires_not_when_installed) |
| TEMP-2 | RC2 | S3 | root cause | closed (the 5-failure `break` is gone: real exponential backoff to a 2.0 s ceiling, indefinite retry, and `current_temp = "Disconnected"` announced once. test_reader_implements_exponential_backoff, test_reader_continues_retrying_indefinitely, test_reader_sets_disconnected_on_persistent_failure, test_reader_recovers_after_reconnect, test_reader_backoff_respects_maximum, test_read_serial_data_retries_indefinitely_and_says_so. **The fix shipped with a defect the lead caught on merge**: the backoff was a plain `time.sleep`, and its 2.0 s ceiling outlives `READER_JOIN_TIMEOUT` (1.5 s), so `close()` gave up waiting and shut the port under a live reader. Backoff now waits on an event `close()` sets — test_a_reader_at_the_backoff_ceiling_still_leaves_before_close_returns, test_the_reader_stops_even_with_no_port_to_close) |
| TEMP-3 | RC6 | S9 | root cause | closed (test_a_non_numeric_field_refuses_the_whole_frame, tests/core/test_typed_params.py) |
| TEMP-4 | RC6 | S9 | root cause | closed (S9 item 2: temperature params typed) |
| TEMP-5 | RC1 | S2 | root cause | closed (hide/show; test_hiding_does_not_release_the_model) |
| TEMP-6 | RC1 | S2 | root cause | closed (view no longer constructs models; open_device_view refuses an unconfigured device) |
| TEMP-7 | RC5 | S8 | root cause | closed (`send_settings` was a check-then-act: it tested `_estop` at the top, then built the frame, so a FULL STOP landing in that window was overwritten and the heater returned to setpoint silently. The latch is now re-checked inside a new `_write_lock` immediately before the write, and `emergency_stop` uses the latch-worker-bounded-join pattern with a forced priority frame. test_temp_7_a_stop_landing_mid_build_is_not_overwritten, test_temp_7_emergency_stop_returns_promptly_behind_a_held_write_lock, test_temp_7_the_stop_frame_forces_through_a_busy_write_lock, test_temp_7_an_ordinary_send_takes_the_lock_without_a_timeout) |
| TEMP-8 | RC1 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| TEMP-9 | RC7 | S15 | explicit | closed (D-13 implemented: `temp_series()` returns `{"x": [...], "y": [...]}` and the schema declares `sch.plot("Temperature over time", "temp_series")`, so it renders in all three views through the generic renderer per D-6 — no hand-built plot in any view. test_temp_series_method_exists, test_temp_series_returns_dict_with_x_y, test_temp_series_empty_when_no_samples, test_temp_series_populated_after_adding_samples, test_plot_schema_declared. Background: **D-13 answered 2026-09-21: plot it.** The history arrays are genuinely dead — `tempC`/`time`/`sp` are appended in `process_raw_data` with a ring-buffer trim and read only by `get_history()`, which has zero callers in `src/`. Verified on the owner's question that the **PID does not use them**: the loop runs on the firmware, and the host only forwards `<setpoint, spdelay, p, i, d, offset>` on the wire (`temperature_system.py:199`), so `p_term`/`i_term`/`d_term` are operator values in transit, not host-side state. The data is therefore already being collected and discarded, and a `sch.plot("Temperature over time", "temp_series")` renders in all three views per D-6 at near-zero cost. Owner accepts this is feature work on a repair branch) |
| TEMP-10 | RC2 | S15 | explicit | closed (**the headline is finally the thing that got fixed**, by the lead at merge. With no port the reader thread never starts — `__init__` starts it only inside `if self.serial_conn and self.serial_conn.is_open():` — so nothing could ever replace the `"N/A"` default; and the reader's own no-port branch backed off silently because `current_temp` was set to the disconnected state *only* in the `except Exception` handler, which a not-open port never reaches since it raises nothing. Both sites now set one named `DISCONNECTED_TEMP`. test_temp_10_no_port_does_not_report_a_pending_reading, test_temp_10_no_port_names_the_disconnection, test_temp_10_the_reader_no_port_branch_also_sets_the_state, test_temp_10_no_reader_thread_exists_with_no_port. The PySide indicator half landed in the same wave — test_temp_10_pyside_readonly_widgets_are_styled_by_their_role, test_temp_10_schema_declares_connection_state_with_a_role, test_temp_10_every_declared_role_has_a_style_to_apply — and the "Enter Settings is silent" clause is closed too: test_send_settings_with_no_port_reports_not_connected, test_stop_with_no_port_reports_not_connected. **It took three attempts on three adjacent clauses.** Prior state: `connection_state` exists, is exposed readonly in the schema, and **Tk now renders it with its own role styling** so a frozen value is visibly frozen — test_temp_10_connection_state_exists, test_temp_10_connection_state_in_schema, test_temp_10_connection_state_has_role, test_temp_10_connection_state_closed_when_no_port. reported `closed` by the wave-4 agent and downgraded again, because the headline — SIM or no-port showing "N/A" forever — was the `temperature_system.py` no-port branch, which stayed live through both) |
| TEMP-11 | RC1 / RC2 / doc | S2 | root cause | closed (the model half from the fix-web worktree, the bounded transport `flush()` from fix-transport, and the seam joined by the lead on merge — test_close_drains_the_heater_off_frame_before_releasing_the_port, test_close_reports_an_undrained_heater_off_frame, test_a_transport_without_flush_still_closes, plus the four close-path tests in tests/core/test_temperature_subsystem.py. Neither worktree could have tested the join alone) |
| TEMP-12 | RC8 | S11 | root cause | closed (an info popup is inexpressible: `publish` raises on `requires_ack` for anything but an error; test_a_quiet_event_raises_no_modal) |
| TEMP-13 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| TEMP-17 | RC2 | S3 | audit-2026-09-21 | open (second fresh-eyes audit. `temp_controller.ino::parseData` calls `strtok(NULL, ",")` six times with no NULL check, so a short `<...>` frame reaches `atof(NULL)` — undefined on AVR, and this is the board with no watchdog. **Host half:** `TemperatureSystem.stop()` holds its own `_write_lock` while `write_command` holds the transport's `_lock`, each with an independent priority-bypass timeout, so two priority writes (Stop System racing FULL STOP's worker, or `close()`'s off-frame racing `send_settings()`) can reach `ser.write()` unsynchronized and interleave into a malformed frame. The host half is fixable here; the firmware NULL-check is D-7 scope) |
| VIEW-TKINTER-1 | RC1 | S2 | root cause | closed (Tk got a real re-add path; test_tk_hide_is_reversible, tests/core/test_tkinter_teardown.py) |
| VIEW-TKINTER-2 | RC8 | S11 | root cause | closed (same fix as ERRORS-5) |
| VIEW-TKINTER-3 | RC3 | S7 | root cause | closed (test_i_3_3_the_interlock_no_longer_defers_on_stepping) |
| VIEW-TKINTER-4 | RC3 | S7 | root cause | closed (test_losing_the_controller_leaves_manual_mode_entirely) |
| VIEW-TKINTER-5 | RC4 | S5 | root cause | closed (S5: test_a_fault_in_the_input_pump_leaves_manual_mode) |
| VIEW-TKINTER-6 | RC13 | S5 | root cause | closed in S12 (same as MANAGER-16; test_build_models_owns_its_claims_dict) |
| VIEW-TKINTER-7 | RC1 | S2 | root cause | closed (verified by inspection: app.py builds before withdraw and reports failure) |
| VIEW-TKINTER-8 | RC1 | S2 | root cause | closed (tests/core/test_lifecycle_exit.py) |
| VIEW-TKINTER-9 | RC4 | S5 | root cause | closed (D-4; test_d4_a_child_dialog_does_not_close_the_gate) |
| VIEW-TKINTER-10 | RC13 / RC7 | S5 | root cause | closed (S5: test_a_failed_controller_swap_does_not_claim_the_controller) |
| VIEW-TKINTER-11 | RC4 | S5 | root cause | closed (S5: test_a_tap_shorter_than_a_read_interval_is_not_lost) |
| VIEW-TKINTER-12 | RC4 | S5 | root cause | closed (S5: test_the_model_not_the_view_feeds_the_idle_watchdog) |
| VIEW-TKINTER-13 | RC3 | S7 | root cause | closed (every exit routes through _transition; test_mode_is_exactly_one_value) |
| VIEW-TKINTER-14 | RC11 | S13 | root cause | closed (the model refuses a start without a focus area, and Tk now renders that refusal rather than offering a button that cannot work: the Start button gates on `focus_area` and the Sync toggles on `monitoring`. test_redpercent_start_monitoring_refuses_without_focus_area, test_redpercent_start_button_has_disabled_when_monitoring, test_view_should_check_focus_area_when_gating_start_button, test_view_tkinter_14_start_monitoring_refused_without_focus_area, test_view_tkinter_14_sync_toggles_disabled_when_monitoring) |
| VIEW-TKINTER-15 | RC11 | S13 | root cause | closed (same fix as REDPERCENT-1, seen from Tk: the log's dimensions are frozen at run creation and toggling Sync after a start neither corrupts the log nor kills the thread. test_monitoring_run_freezes_sync_dimensions_as_a_tuple, test_toggle_sync_refuses_while_a_run_is_active) |
| VIEW-TKINTER-16 | RC1 | S2 | root cause | closed (test_rotator_teardown_sends_stop_before_disconnecting, tests/core/test_lifecycle_teardown.py) |
| VIEW-TKINTER-17 | RC7 / RC9 | S10 | root cause | closed (RC-9 wiring closed in S12; the RC-7 half verified gone on 2026-09-20 — no `open_controller_log`, no hard-coded "Red Percent Window"/"SMC100 Rotator" dispatch and no dead `serial_port` field check survives in tkinter/view.py, which routes on `VIEW_HINT`. Confirmed by the lead with an independent grep before accepting. test_view_tkinter_17_no_hardcoded_device_names, test_view_tkinter_17_uses_view_hint, test_view_tkinter_17_no_serial_port_field_checks) |
| VIEW-TKINTER-18 | LOCAL-OK | S15 | explicit | open (partly closed: log-window handling and stdout spam are closed. **Button-2/Button-3 on macOS Aqua remains an unconfirmed hypothesis** — a **third** agent has now declined to change the binding without executing it on macOS, which is the right call and is recorded here rather than as a test. **The lead has now deleted this same pair of bad tests twice.** In wave 4 they were an `assert True` and an assertion that the defect still exists; in wave 5 the same agent-shaped mistake came back as a `pass`-bodied `test_..._confirmation` plus two tests asserting that `sys.platform`/"darwin"/"aqua"/Button-3 are *absent* from the binding code — which would go red the day the owner applies the real macOS fix. A test that must be deleted before a bug can be fixed is worse than no test. What survives is one locator, worded to stay green through a platform-conditional binding. The verification itself is in [bench-checklist.md](bench-checklist.md)) |
| WEB-1 | RC1 / RC10 | S2 | root cause | closed (tests/web/test_web_security.py: token, Origin and Content-Type checks on every POST) |
| WEB-2 | RC4 | S5 | root cause | closed (S5: polling moved into the models, so all three frontends share it; test_manual_mode_drives_hardware_with_no_gui_at_all, test_the_model_starts_the_poller_itself) |
| WEB-3 | RC1 / RC10 | S2 | root cause | closed (tests/web/test_web_security.py: /api/screenshot now requires the session token) |
| WEB-4 | RC9 | S12 | root cause | closed (S12 item 3, same as DC-12/MANAGER-12) |
| WEB-5 | RC10 | S14 | root cause | closed (the poller's `log_updater` is wired for models the setup wizard builds, not only for those built at import; test_setup_initialize_wires_poller_log_to_the_adapter) |
| WEB-6 | RC7 | S10 | root cause | closed (S10: test_an_interactive_dropdown_always_has_a_command) |
| WEB-7 | RC7 | S10 | root cause | closed (the plotter takes `current_red` only, instead of any attribute whose name contains "red", so absolute and delta values stop interleaving in one series. test_web_7_plotter_uses_only_current_red) |
| WEB-8 | RC4 / RC10 | S5 | root cause | closed (S5: /api/state reads the model caches; test_thread_safety_concurrent_requests) |
| WEB-9 | RC8 | S11 | root cause | closed (`/api/errors?since=`; the destructive pop is gone and each tab keeps its own cursor). **The replay half survives**: a tab connecting with `since=0` still pulls the whole history as toasts — see ERRORS-3 |
| WEB-10 | RC10 | S4 | root cause | closed (S4: token, Content-Type and Origin validation; test_a_post_from_another_origin_is_refused_even_with_json) |
| WEB-11 | RC7 | S10 | root cause | closed (the file-picker modal and the JS for the non-existent `execute_script` and `send_raw_command` commands are deleted. test_web_11_no_file_picker_modal, test_web_11_no_raw_command_input) |
| WEB-12 | RC8 | S11 | root cause | closed (`CommandResult`; a refusal is never rendered as success) |
| WEB-13 | RC11 | S13 | root cause | closed (`MonitoringRun` gave the model `pending_run_data()`/`has_unsaved_data` but never published it; `get_state()` now surfaces it and the client's `stop_monitoring` path offers to save first. test_get_state_includes_pending_run_data_when_model_defines_it, test_get_state_omits_pending_run_data_for_models_without_it, test_stop_monitoring_offers_to_save_unsaved_data) |
| WEB-14 | RC10 | S14 | root cause | closed (every route returns a JSON 500 envelope instead of dropping the connection, and one raising device no longer takes the whole state payload down with it. test_get_state_isolates_a_raising_device, test_api_get_route_crash_returns_json_500_not_a_dropped_connection, test_api_post_route_crash_returns_json_500_not_a_dropped_connection) |
| WEB-15 | RC9 | S12 | root cause | closed (S12 removed the `python3` subprocess and the fabricated "Virtual Controller" entries — test_discover_controllers_never_shells_out, test_discover_controllers_fabricates_nothing. S14 added the missing per-port device-type autodetect as a background scan polled to completion, single-flight — test_hardware_scan_runs_async_and_is_polled_to_completion, test_hardware_scan_is_single_flight) |
| WEB-16 | LOCAL-OK | S14 | explicit | closed (`start()` treats only EADDRINUSE as port-busy and raises after ten, instead of swallowing every OSError and appearing to start. test_start_raises_runtime_error_after_ten_busy_ports, test_start_does_not_swallow_unrelated_os_errors, test_start_retries_past_a_genuinely_busy_port. Moved S15 -> S14: it shares `web_server.py` with WEB-14/21.) |
| WEB-17 | RC10 | S14 | root cause | closed early in S11 (the destructive pop was the RC-8 half; `?since=` fixed it) |
| WEB-18 | RC5 / RC8 | S8 | root cause | closed (FULL STOP reports per-device results instead of a blanket ok, so a device whose stop was not confirmed is named. test_full_stop_reports_per_device_results, test_api_full_stop_all_reports_unconfirmed_device) |
| WEB-19 | RC10 | S14 | root cause | open (partly closed: **the mechanism is built, tested and joined; the numbers are not set.** Model half — `BaseProbe.touch_client_liveness()` and a client-liveness deadline folded into the *existing* interlock watchdog, gating only in AUTONOMOUS/MANUAL and only once a client has ever checked in, so a Tk or PySide session is never stopped. Client half — browser heartbeat with `visibilitychange`/`pagehide` and `POST /api/client/heartbeat`. Seam joined by the lead and pinned: test_web19_seam_is_joined, test_the_hook_actually_arms_the_deadline, plus 8 model and 7 client tests incl. test_no_client_ever_checked_in_is_never_gated and test_full_stop_after_the_stop_threshold_of_silence. **`WEB_CLIENT_WARN_TIMEOUT` (5 s) and `WEB_CLIENT_STOP_TIMEOUT` (15 s) are provisional placeholders**, not measured against a real client on the real network. The owner set them aside on 2026-09-21 to time at the bench rather than ratify a guess; this row does not close until they are measured) |
| WEB-20 | RC1 | S2 | root cause | closed **at the third attempt** (`get_system_info` and `get_devices` now read through `get_active_models_snapshot()` under the manager's lock rather than the adapter's, and the generation counter the finding asked for exists and is re-checked in `resolve_options`, `dispatch_command` and `set_device_attribute`. test_get_system_info_reports_active_device_from_snapshot, test_get_devices_reports_device_from_snapshot, test_generation_counter_exists, test_generation_bumps_on_setup_swap, test_dispatch_command_aborts_if_manager_changes_under_the_lock. Reported `closed` and rejected on verification twice before this — 2026-09-20 by a reconciliation agent and again by a wave-3 agent, both having fixed the reachable call sites and stopped looking. Verified by the lead this time by enumerating every remaining `active_models` reference: all are inside `initialize_setup`'s own construction) |
| WEB-21 | RC10 | S14 | root cause | closed (POST bodies are capped and answered with 413 after the declared body is drained in bounded chunks; `mss`/PIL import lazily and one `mss` instance is reused. test_post_body_over_max_size_is_rejected_with_413, test_screenshot_reuses_a_single_mss_instance) |
| WEB-22 | RC10 | S14 | root cause | closed (the fetch-wrapper abort closed earlier; per-device staleness marking and dropdown refresh-on-focus were found **already implemented and merely untested**, and now carry Node-harness coverage driven from pytest in the style of `js_fetch_timeout_check.js`. test_shared_fetch_wrapper_aborts_a_hung_request_after_its_timeout, test_staleness_marking_and_dropdown_refresh_on_focus) |
| WEB-23 | RC10 | S14 | owner-D-8a | open (**not built.** The owner ruled on 2026-09-21 (D-8a) that D-8's client-liveness gate must cover the heater and the rotator, not probes only. At 048eb2b it still does not: `BaseProbe` has `touch_client_liveness` / `_check_client_liveness`; `TemperatureSystem` and `RotatorSystem` have neither and no idle watchdog of any kind, so `web_adapter.record_client_heartbeat`'s duck-typed `getattr(model, "touch_client_liveness", None)` silently does nothing for both. **Consequence at the bench:** a heater brought to setpoint from the Web frontend keeps heating if the browser tab closes, the machine sleeps, or the network drops — no host safeguard, and no firmware watchdog behind it (confirmed against `temp_controller.ino` by the second fresh-eyes audit). **Partial mitigation already landed:** MANAGER-24 means *hiding* the heater's view now stops it, but that is a different gesture from the web client going silent, which is what this row is about. **Build shape:** mirror `BaseProbe`'s mechanism on both models, gated the same way (only once a web client has ever checked in, so a Tk or PySide session is never stopped), with the heater's "active" condition being a nonzero setpoint rather than a motion mode. **The four constants ship as explicitly-marked placeholders** — warn and stop timeouts, separately for heater and rotator — exactly as WEB-19's did; they are measured at the bench and this row does not close until they are. The heater's window is a thermal question, not a motion one, so the probe's 5 s / 15 s must not be copied across) |
| WEB-24 | RC7 | S14 | audit-2026-09-21 | open (low. Second fresh-eyes audit: `app.js` re-derives mode gating from raw polled flags (`auton_flag`, `manual_flag`, `system_enabled`) and finds buttons by `innerText.includes('Full Stop')` instead of consulting the schema's `enabled_when`/`disabled_when`, which is how the desktop views stay in step with DC-6. **Not a safety bypass** — the server independently refuses disallowed commands with 403 (`web_adapter.py` `_ModeRefused`) — but rendered state can drift from enforcement, and a label rename silently breaks it) |

---

*Ledger generated 2026-09-19 from `root-causes.md` cross-reference (213
findings). Regenerate rather than hand-edit if findings are added.*
