# Progress

**The trail.** This file is the single source of truth for what is done,
what is next, and what is blocked. Update it in the *same commit* as the
work it describes, then push. If this file and your memory disagree, this
file wins.

Route: [plan.md](plan.md) · Tests: [testing.md](testing.md) · Analysis:
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
5. **Run the fast gate** — about 28 seconds:
   `python3 -m pytest tests/ -m "not slow and not order_dependent and not qt"`.
   Your stage's targeted gate and the three-pass full sweep are in
   [testing.md](testing.md). Do not run the whole suite in a working loop.
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
| S1 | Purge legacy paths (D-9, D-11) | done | | 2026-09-19 | D-9 + D-11 purged. SERIAL-18's `reboot_model` half reassigned to S2. |
| S2 | Lifecycle authority (RC-1) | done | | 2026-09-19 | All 9 items. I-1.5 now holds. 7 tab-close findings deferred to S6 by D-1. |
| S3 | Transport truth and E-stop latch | done | | 2026-09-19 | I-2.3 holds. I-5.2 xfailed to S8 (emergency_stop still blocks on a stalled transport). |
| S4 | Web AppContext and security boundary | done | | 2026-09-19 | CSRF hole and /api/screenshot closed. Manager read-through + single-flight. |
| S5 | Input service and model-owned loops | done | `9a70834` | 2026-09-20 | RC-13 + RC-4. Deferred D-4 input gate landed 2026-09-20. |
| S6 | Hide/show semantics (D-1) | done | `e9fc26f` | 2026-09-20 | All 4 items. Tk got a real re-add path. order_dependent 3 -> 2. |
| S7 | Probe mode state machine (RC-3) | done | `8fc9f00` | 2026-09-20 | All 5 items. `ProbeMode` replaces 4 booleans; I-3.1–I-3.4 hold. known_bad 7 -> 2. |
| S8 | Motion serialization, ConnectionState | done | | 2026-09-20 | All 4 items. I-5.2 holds. known_bad down 10 -> 7. |
| S9 | Typed parameters (RC-6) | done | `79d2d97` | 2026-09-20 | All 4 items. `Param` table + D-5 `apply_inputs`. Landed with S10. |
| S10 | Schema v2, three renderers (RC-7) | in progress | `79d2d97` | 2026-09-20 | **Items 1-5 written, Qt pass UNVERIFIED.** See session log for the exact remaining check. |
| S11 | Result channel and event bus (RC-8) | todo | | | |
| S12 | Composition root, registry events | todo | | | |
| S13 | MonitoringRun (RC-11) | todo | | | |
| S14 | Remaining web work | todo | | | **Unblocked 2026-09-20** — D-8 answered (warn/FULL STOP tiers). |
| S15 | Explicit `LOCAL-OK` sweep | todo | | | Any time; good filler while blocked. |
| S16 | Owner verification, firmware v2 | todo | | | **Owner only.** Never delegate. |

## Owner decisions

| ID | Question | Answer | Date |
|---|---|---|---|
| D-1 | Close = hide or destroy? | **hide** — model, connection and config persist | 2026-09-19 |
| D-2 | Mode off = stop only, or disable coils? | **disable coils** — current behavior stands; DC-10's divergence from main is intentional | 2026-09-20 |
| D-3 | Manual mode idle-timeout? | **yes**, on real input inactivity | 2026-09-19 |
| D-4 | Window focus loss behavior? | **gate input, never stop**; child-dialog deactivation is not focus loss | 2026-09-20 |
| D-5 | Commit contract | **(a)** commands carry their inputs | 2026-09-19 |
| D-6 | Red Percent rendering | **schema-driven** in all three views | 2026-09-19 |
| D-7 | Firmware protocol v2 | *open* — adopt is recommended; requires reflashing every board | |
| D-8 | Web client liveness gate | **warn at N s, FULL STOP at M s while motion is active**; folded into the existing interlock watchdog | 2026-09-20 |
| D-9 | macOS default view | **tkinter**, until the codebase is stabilized | 2026-09-19 |
| D-10 | Unsaved Red Percent data on exit | **autosave** to a timestamped file, plus a prompt where the UI allows | 2026-09-19 |
| D-11 | Runtime serial reconnect | **not supported** — purged as legacy | 2026-09-19 |
| D-12 | Gamepad poll rate: 200 Hz (code) or 50 Hz (comment)? | **200 Hz** — the code was right, the comment wrong. 20 ms manual pump ratified with it | 2026-09-20 |

D-3, D-5, D-6 and D-10 carry the recommendations recorded in
`root-causes.md`; they were not separately re-confirmed by the owner and any
of them can be reopened before its stage begins.

**D-7 is the only decision still open.** It is S16 work, at the bench, and
requires reflashing every board; the recommendation remains *adopt*. Nothing
before S16 blocks on it.

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

### Resume here (next session)

State at `79d2d97`, working tree clean, pushed. **S10 is mid-stage.** In
order:

1. **`pytest tests/ -m "qt"`.** It has not completed since the renderer
   rewrite. Expect the two S10-owned `known_bad` entries to XPASS-as-failure;
   re-author both and delete their `_KNOWN_BAD` entries.
2. **Write rendering tests for the four composites** (`plot`, `log_stream`,
   `region_select`, `file_save`). They are the least-proven code on the
   branch — nothing but the schema conformance test has touched them.
3. **Re-run the full three-pass sweep** and set S10 `done`.
4. Then **S11** (result channel and event bus, RC-8), which is also where the
   last two `order_dependent` tests were predicted to dissolve. Read their
   actual assertion values before believing that prediction — it has been
   wrong twice.

S12 then deletes `web_adapter`'s Red Percent linking block, which is what
retires the I-7.1 `xfail`.

### Still waiting on the owner

1. **D-7** — firmware protocol v2. Recommendation: **adopt**. Requires
   reflashing every board, so it is S16, at the bench, owner-only. Nothing
   earlier blocks on it.

### For whoever picks this up

S9 items 2–3 overlap S10's schema v2 — do them together. **RC-6's
script-path coverage does not exist**: the test that appeared to provide it
was passing on a mock artefact (see the entry above). Write it fresh.

Do not trust this file's older claims about *why* the suite was flaky; the
entry above corrects them.

---

## Finding ledger

All 213 audit findings. `Closed by` is `root cause` when the finding closes
because the structure changed, `explicit` when it is fixed and named
individually. Generated from `root-causes.md`'s cross-reference table — if
you add a finding there, regenerate rather than hand-editing, so nothing is
dropped.

Status: `open` · `closed` (with the test or verification note that proves
it) · `n/a` (with a reason).

| Finding | Root cause | Stage | Closed by | Status |
|---|---|---|---|---|
| DC-1 | RC3 | S7 | root cause | closed (test_i_3_3_the_interlock_no_longer_defers_on_stepping) |
| DC-2 | RC1 | S2 | root cause | closed (close_device_view no longer tears down; test_i_1_5_active_models_written_only_by_system_manager) |
| DC-3 | RC1 | S2 | root cause | closed (test_probe_teardown_order_is_stop_then_poller_then_transport, tests/core/test_lifecycle_teardown.py) |
| DC-4 | RC6 | S9 | root cause | closed (S9 item 2: Param table declares type and bounds; tests/core/test_typed_params.py) |
| DC-5 | RC4 | S5 | root cause | open |
| DC-6 | RC7 | S10 | root cause | open |
| DC-7 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| DC-8 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| DC-9 | RC1 | S2 | root cause | closed (hide/show; test_hiding_does_not_release_the_model, tests/core/test_hide_show.py) |
| DC-10 | RC3 | S7 | root cause | closed (D-2 ruled disable; test_d_2_leaving_a_mode_disables_the_coils) |
| DC-11 | RC7 / RC3 | S10 | root cause | open (RC-3 flags part closed in S7, same test; RC-7 part remains) |
| DC-12 | RC9 | S12 | root cause | open |
| DC-13 | RC2 / RC7 | S3 | root cause | closed (test_the_badge_cannot_be_faked_by_typing_SIM_into_the_port_field, tests/web/test_web_security.py) |
| DC-14 | RC7 | S1 | root cause | closed (test_d11_serial_port_is_readonly_in_every_schema) |
| DC-15 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| DC-16 | RC4 | S5 | root cause | open |
| DC-17 | RC4 / RC3 | S5 | root cause | closed (RC-4 half in S5; RC-3 half in S7 — mode transitions own their side effects, tests/core/test_probe_mode.py) |
| DC-18 | RC5 / RC2 | S8 | root cause | open |
| DC-19 | RC7 | S10 | root cause | open |
| ERRORS-1 | RC8 / RC7 | S11 | root cause | open |
| ERRORS-2 | RC8 / RC10 | S11 | root cause | open |
| ERRORS-3 | RC8 | S11 | root cause | open |
| ERRORS-4 | RC8 | S11 | root cause | open |
| ERRORS-5 | RC8 | S11 | root cause | open |
| ERRORS-6 | RC2 | S3 | root cause | open |
| ERRORS-7 | RC2 / RC8 / RC11 | S3 | root cause | open |
| ERRORS-8 | RC8 | S11 | root cause | open |
| ERRORS-9 | RC8 | S11 | root cause | open |
| ERRORS-10 | RC8 | S11 | root cause | open |
| ERRORS-11 | doc | S0 | root cause | open |
| ERRORS-12 | RC10 | S4 | root cause | open |
| GAMEPAD-1 | RC4 | S5 | root cause | open |
| GAMEPAD-2 | RC13 | S5 | root cause | open |
| GAMEPAD-3 | RC3 | S7 | root cause | closed (test_a_failed_controller_swap_does_not_claim_the_controller) |
| GAMEPAD-4 | RC3 | S7 | root cause | closed (test_a_failed_controller_swap_does_not_claim_the_controller) |
| GAMEPAD-5 | RC13 / RC7 | S5 | root cause | open |
| GAMEPAD-6 | RC7 | S10 | root cause | open |
| GAMEPAD-7 | RC4 | S5 | root cause | open |
| GAMEPAD-8 | RC4 | S5 | root cause | closed (D-4 input gate replaces flush_neutral; test_d4_a_closed_gate_stops_the_manual_pump_without_stopping_the_mode) |
| GAMEPAD-9 | RC1 | S2 | root cause | closed (loops moved to the models; test_manual_mode_drives_hardware_with_no_gui_at_all, tests/core/test_model_owned_loops.py) |
| GAMEPAD-10 | RC13 | S5 | root cause | open |
| GAMEPAD-11 | RC12 | S16 | explicit | open |
| GAMEPAD-12 | RC12 | S16 | explicit | open |
| GAMEPAD-13 | RC12 | S16 | explicit | open |
| GAMEPAD-14 | RC12 | S16 | explicit | open |
| GAMEPAD-15 | RC13 / RC11 | S5 | root cause | open |
| GAMEPAD-16 | RC4 | S5 | root cause | open |
| GAMEPAD-17 | LOCAL-OK | S15 | explicit | open |
| GAMEPAD-18 | RC13 / RC9 | S5 | root cause | open |
| GAMEPAD-19 | RC13 | S5 | root cause | open |
| GAMEPAD-20 | doc | S0 | root cause | open |
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
| MANAGER-12 | RC9 | S12 | root cause | open |
| MANAGER-13 | RC4 / RC10 | S5 | root cause | open |
| MANAGER-14 | LOCAL-OK | S1 | explicit | closed (test_d9_macos_defaults_to_tkinter, test_manager14_launcher_rejects_unknown_flags) |
| MANAGER-15 | RC10 | S4 | root cause | closed (test_window_and_server_read_the_live_manager_not_a_stored_copy, tests/web/test_web_security.py) |
| MANAGER-16 | RC13 | S5 | root cause | open |
| MANAGER-17 | RC8 | S11 | root cause | open |
| MANAGER-18 | RC9 | S12 | root cause | open |
| MANAGER-19 | RC5 | S8 | root cause | open |
| MANAGER-20 | RC4 | S5 | root cause | open |
| PYSIDE-1 | RC1 | S2 | root cause | closed (view no longer constructs models; open_device_view refuses an unconfigured device) |
| PYSIDE-2 | RC1 | S2 | root cause | closed (close_device_view no longer tears down; test_i_1_5_active_models_written_only_by_system_manager) |
| PYSIDE-3 | RC9 | S12 | root cause | open |
| PYSIDE-4 | RC11 | S13 | root cause | open |
| PYSIDE-5 | RC6 | S9 | root cause | closed (S9 item 2: views read value_type instead of calling float() on the current value) |
| PYSIDE-6 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| PYSIDE-7 | RC7 | S10 | root cause | open |
| PYSIDE-8 | RC7 | S10 | root cause | open |
| PYSIDE-9 | RC4 | S5 | root cause | open |
| PYSIDE-10 | RC8 / RC4 | S11 | root cause | open |
| PYSIDE-11 | LOCAL-OK | S15 | explicit | closed (S10: the unreachable `continue`-first file_picker branch replaced by the file_save composite) |
| PYSIDE-12 | RC7 | S10 | root cause | open |
| PYSIDE-13 | RC1 | S1 | root cause | closed (test_d11_no_runtime_serial_reconnect) |
| PYSIDE-14 | RC4 | S5 | root cause | closed (D-4; deferred activeWindow check distinguishes a child dialog, tests/core/test_tkinter_teardown.py + pyside _app_has_focus) |
| PYSIDE-15 | RC8 | S11 | root cause | open |
| PYSIDE-16 | LOCAL-OK | S15 | explicit | open |
| PYSIDE-17 | LOCAL-OK | S15 | explicit | open |
| PYSIDE-18 | LOCAL-OK | S15 | explicit | open |
| PYSIDE-19 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| PYSIDE-20 | RC7 / RC13 | S10 | root cause | open |
| REDPERCENT-1 | RC11 | S13 | root cause | open |
| REDPERCENT-2 | RC11 | S13 | root cause | open |
| REDPERCENT-3 | RC11 / RC5 | S13 | root cause | open |
| REDPERCENT-4 | RC11 | S13 | root cause | open |
| REDPERCENT-5 | RC11 | S13 | root cause | open |
| REDPERCENT-6 | RC7 | S10 | root cause | open |
| REDPERCENT-7 | RC8 / RC7 | S11 | root cause | open |
| REDPERCENT-8 | RC7 | S10 | root cause | open |
| REDPERCENT-9 | RC11 | S13 | root cause | open |
| REDPERCENT-10 | RC7 | S10 | root cause | open |
| REDPERCENT-11 | RC9 / RC1 | S12 | root cause | open |
| REDPERCENT-12 | RC1 | S2 | root cause | closed (hide/show; test_hiding_does_not_release_the_model) |
| REDPERCENT-13 | RC7 | S10 | root cause | open |
| REDPERCENT-14 | RC6 | S9 | root cause | closed (S9 item 2: redpercent params typed) |
| REDPERCENT-15 | RC9 | S12 | root cause | open |
| REDPERCENT-16 | RC11 | S13 | root cause | open |
| REDPERCENT-17 | RC7 | S10 | root cause | open |
| REDPERCENT-18 | RC7 | S10 | root cause | open |
| REDPERCENT-19 | RC7 | S10 | root cause | open |
| REDPERCENT-20 | LOCAL-OK | S15 | explicit | open |
| ROTATOR-1 | RC1 / RC5 | S2 | root cause | closed (test_rotator_teardown_sends_stop_before_disconnecting, tests/core/test_lifecycle_teardown.py) |
| ROTATOR-2 | RC10 | S14 | root cause | closed (test_shutdown_resolves_the_manager_when_it_fires_not_when_installed) |
| ROTATOR-3 | RC8 / RC7 | S11 | root cause | open |
| ROTATOR-4 | RC5 | S8 | root cause | open |
| ROTATOR-5 | RC1 | S2 | root cause | closed (view no longer constructs models; open_device_view refuses an unconfigured device) |
| ROTATOR-6 | RC4 | S5 | root cause | open |
| ROTATOR-7 | RC4 / RC10 | S5 | root cause | open |
| ROTATOR-8 | RC5 | S8 | root cause | open |
| ROTATOR-9 | RC2 / RC7 | S3 | root cause | open |
| ROTATOR-10 | RC1 | S2 | root cause | closed (hide/show; test_hiding_does_not_release_the_model) |
| ROTATOR-11 | RC2 / LOCAL-OK | S3 | explicit | open |
| ROTATOR-12 | RC6 | S9 | root cause | closed (S9 item 2: rotator params typed and bounded) |
| ROTATOR-13 | RC2 / RC8 | S3 | root cause | open |
| ROTATOR-14 | RC1 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| ROTATOR-15 | RC8 / doc | S11 | root cause | open |
| SERIAL-1 | RC2 | S3 | root cause | closed (test_a_failed_disable_faults_instead_of_claiming_the_system_is_off, tests/core/test_transport_truth.py) |
| SERIAL-2 | RC1 | S2 | root cause | closed (test_probe_teardown_sends_hardware_stop_when_poller_stop_raises, tests/core/test_lifecycle_teardown.py) |
| SERIAL-3 | RC1 | S2 | root cause | closed (close_device_view no longer tears down; test_i_1_5_active_models_written_only_by_system_manager) |
| SERIAL-4 | RC1 | S2 | root cause | closed (hide keeps the transport open; test_hiding_does_not_release_the_model) |
| SERIAL-5 | RC1 / RC10 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| SERIAL-6 | RC4 | S5 | root cause | open |
| SERIAL-7 | RC2 | S3 | root cause | closed (test_an_opened_port_that_never_answered_is_unverified_not_connected, tests/core/test_transport_truth.py) |
| SERIAL-8 | RC2 | S3 | root cause | closed (test_the_first_write_failure_moves_the_link_to_lost_and_closes_it, test_loss_is_reported_once_not_on_every_subsequent_command, tests/core/test_transport_truth.py) |
| SERIAL-9 | RC2 | S3 | root cause | closed (test_simulator_probes_can_arm_and_disarm, tests/core/test_transport_truth.py) |
| SERIAL-10 | RC2 | S3 | root cause | open |
| SERIAL-11 | RC2 | S3 | root cause | closed (test_i_2_3_serial_handle_confined_to_transport; all writes go through write_command under the lock) |
| SERIAL-12 | RC4 | S5 | root cause | open |
| SERIAL-13 | RC2 | S3 | root cause | open |
| SERIAL-14 | RC1 | S1 | root cause | closed (test_d11_no_runtime_serial_reconnect) |
| SERIAL-15 | RC1 | S2 | root cause | closed (tests/core/test_lifecycle_exit.py) |
| SERIAL-16 | RC8 | S11 | root cause | open |
| SERIAL-17 | RC2 / LOCAL-OK | S3 | explicit | open |
| SERIAL-18 | RC1 | S2 | root cause | closed (reboot_model deleted; test_system_manager_reconfigure_replaces_the_model_set) |
| SERIAL-19 | doc | S0 | root cause | open |
| STEPPER-1 | RC1 | S2 | root cause | closed (close_device_view no longer tears down; test_i_1_5_active_models_written_only_by_system_manager) |
| STEPPER-2 | RC4 | S5 | root cause | open |
| STEPPER-3 | RC1 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| STEPPER-4 | RC2 | S3 | root cause | closed (test_a_failed_disable_faults_instead_of_claiming_the_system_is_off, tests/core/test_transport_truth.py) |
| STEPPER-5 | RC3 | S7 | root cause | closed (test_losing_the_controller_leaves_manual_mode_entirely, tests/core/test_probe_mode.py) |
| STEPPER-6 | RC3 | S7 | root cause | closed (test_i_3_3_the_interlock_no_longer_defers_on_stepping) |
| STEPPER-7 | RC5 / RC3 | S8 | root cause | closed (RC-5 half in S8; RC-3 watchdog-generation half in S7, test_the_watchdog_gets_a_fresh_event_each_arming) |
| STEPPER-8 | RC5 | S8 | root cause | closed (test_stopping_invalidates_a_script_still_in_flight, tests/core/test_transport_truth.py) |
| STEPPER-9 | RC2 / LOCAL-OK | S3 | explicit | open |
| STEPPER-10 | RC7 | S10 | root cause | open |
| STEPPER-11 | RC6 / RC7 / RC3 | S9 | root cause | open (RC-3 flags part closed in S7: mode flags are read-only, API returns 403 — test_i_3_4_mode_flags_cannot_be_assigned. RC-6/RC-7 parts remain) |
| STEPPER-12 | RC7 | S1 | root cause | closed (test_d11_serial_port_is_readonly_in_every_schema) |
| STEPPER-13 | RC9 | S12 | root cause | open |
| STEPPER-14 | RC4 | S5 | root cause | open |
| STEPPER-15 | RC4 | S5 | root cause | open |
| TEMP-1 | RC1 / RC10 | S2 | root cause | closed (test_shutdown_resolves_the_manager_when_it_fires_not_when_installed) |
| TEMP-2 | RC2 | S3 | root cause | open |
| TEMP-3 | RC6 | S9 | root cause | closed (test_a_non_numeric_field_refuses_the_whole_frame, tests/core/test_typed_params.py) |
| TEMP-4 | RC6 | S9 | root cause | closed (S9 item 2: temperature params typed) |
| TEMP-5 | RC1 | S2 | root cause | closed (hide/show; test_hiding_does_not_release_the_model) |
| TEMP-6 | RC1 | S2 | root cause | closed (view no longer constructs models; open_device_view refuses an unconfigured device) |
| TEMP-7 | RC5 | S8 | root cause | open |
| TEMP-8 | RC1 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| TEMP-9 | LOCAL-OK | S15 | explicit | open |
| TEMP-10 | RC2 / RC8 | S3 | root cause | open |
| TEMP-11 | RC1 / RC2 / doc | S2 | root cause | open |
| TEMP-12 | RC8 | S11 | root cause | open |
| TEMP-13 | RC6 | S9 | root cause | closed (S9 item 2, same) |
| VIEW-TKINTER-1 | RC1 | S2 | root cause | closed (Tk got a real re-add path; test_tk_hide_is_reversible, tests/core/test_tkinter_teardown.py) |
| VIEW-TKINTER-2 | RC8 | S11 | root cause | open |
| VIEW-TKINTER-3 | RC3 | S7 | root cause | closed (test_i_3_3_the_interlock_no_longer_defers_on_stepping) |
| VIEW-TKINTER-4 | RC3 | S7 | root cause | closed (test_losing_the_controller_leaves_manual_mode_entirely) |
| VIEW-TKINTER-5 | RC4 | S5 | root cause | open |
| VIEW-TKINTER-6 | RC13 | S5 | root cause | open |
| VIEW-TKINTER-7 | RC1 | S2 | root cause | closed (verified by inspection: app.py builds before withdraw and reports failure) |
| VIEW-TKINTER-8 | RC1 | S2 | root cause | closed (tests/core/test_lifecycle_exit.py) |
| VIEW-TKINTER-9 | RC4 | S5 | root cause | closed (D-4; test_d4_a_child_dialog_does_not_close_the_gate) |
| VIEW-TKINTER-10 | RC13 / RC7 | S5 | root cause | open |
| VIEW-TKINTER-11 | RC4 | S5 | root cause | open |
| VIEW-TKINTER-12 | RC4 | S5 | root cause | open |
| VIEW-TKINTER-13 | RC3 | S7 | root cause | closed (every exit routes through _transition; test_mode_is_exactly_one_value) |
| VIEW-TKINTER-14 | RC11 / RC7 | S13 | root cause | open |
| VIEW-TKINTER-15 | RC11 | S13 | root cause | open |
| VIEW-TKINTER-16 | RC1 | S2 | root cause | closed (test_rotator_teardown_sends_stop_before_disconnecting, tests/core/test_lifecycle_teardown.py) |
| VIEW-TKINTER-17 | RC7 / RC9 | S10 | root cause | open |
| VIEW-TKINTER-18 | LOCAL-OK | S15 | explicit | open |
| WEB-1 | RC1 / RC10 | S2 | root cause | closed (tests/web/test_web_security.py: token, Origin and Content-Type checks on every POST) |
| WEB-2 | RC4 | S5 | root cause | open |
| WEB-3 | RC1 / RC10 | S2 | root cause | closed (tests/web/test_web_security.py: /api/screenshot now requires the session token) |
| WEB-4 | RC9 | S12 | root cause | open |
| WEB-5 | RC10 | S14 | root cause | open |
| WEB-6 | RC7 | S10 | root cause | open |
| WEB-7 | RC7 | S10 | root cause | open |
| WEB-8 | RC4 / RC10 | S5 | root cause | open |
| WEB-9 | RC8 | S11 | root cause | open |
| WEB-10 | RC10 | S4 | root cause | open |
| WEB-11 | RC7 | S10 | root cause | open |
| WEB-12 | RC8 | S11 | root cause | open |
| WEB-13 | RC11 | S13 | root cause | open |
| WEB-14 | RC10 | S14 | root cause | open |
| WEB-15 | RC9 | S12 | root cause | open |
| WEB-16 | LOCAL-OK | S15 | explicit | open |
| WEB-17 | RC10 | S14 | root cause | open |
| WEB-18 | RC5 / RC8 | S8 | root cause | open |
| WEB-19 | RC10 | S14 | root cause | open |
| WEB-20 | RC1 | S2 | root cause | open |
| WEB-21 | RC10 | S14 | root cause | open |
| WEB-22 | RC10 | S14 | root cause | open |

---

*Ledger generated 2026-09-19 from `root-causes.md` cross-reference (213
findings). Regenerate rather than hand-edit if findings are added.*
