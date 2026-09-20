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
| S5 | Input service and model-owned loops | done | | 2026-09-19 | RC-13 + RC-4. I-4.1 holds. Web has manual mode for the first time. S6 unblocked. |
| S6 | Hide/show semantics (D-1) | todo | | | Needs S5. **Blocked on D-2** when reached. |
| S7 | Probe mode state machine (RC-3) | todo | | | **Blocked on D-2** when reached. |
| S8 | Motion serialization, ConnectionState | todo | | | |
| S9 | Typed parameters (RC-6) | todo | | | |
| S10 | Schema v2, three renderers (RC-7) | todo | | | |
| S11 | Result channel and event bus (RC-8) | todo | | | |
| S12 | Composition root, registry events | todo | | | |
| S13 | MonitoringRun (RC-11) | todo | | | |
| S14 | Remaining web work | todo | | | **Blocked on D-8** when reached. |
| S15 | Explicit `LOCAL-OK` sweep | todo | | | Any time; good filler while blocked. |
| S16 | Owner verification, firmware v2 | todo | | | **Owner only.** Never delegate. |

## Owner decisions

| ID | Question | Answer | Date |
|---|---|---|---|
| D-1 | Close = hide or destroy? | **hide** — model, connection and config persist | 2026-09-19 |
| D-2 | Mode off = stop only, or disable coils? | *open* — holding torque on loaded axes is a bench call | |
| D-3 | Manual mode idle-timeout? | **yes**, on real input inactivity | 2026-09-19 |
| D-4 | Window focus loss behavior? | *open* — recommendation: gate input, never stop on child-dialog deactivation | |
| D-5 | Commit contract | **(a)** commands carry their inputs | 2026-09-19 |
| D-6 | Red Percent rendering | **schema-driven** in all three views | 2026-09-19 |
| D-7 | Firmware protocol v2 | *open* — adopt is recommended; requires reflashing every board | |
| D-8 | Web client liveness gate | *open* | |
| D-9 | macOS default view | **tkinter**, until the codebase is stabilized | 2026-09-19 |
| D-10 | Unsaved Red Percent data on exit | **autosave** to a timestamped file, plus a prompt where the UI allows | 2026-09-19 |
| D-11 | Runtime serial reconnect | **not supported** — purged as legacy | 2026-09-19 |
| D-12 | Gamepad poll rate: 200 Hz (code) or 50 Hz (comment)? | *open* — raised in S5; the value is unchanged at ~200 Hz pending a ruling | |

D-3, D-5, D-6 and D-10 carry the recommendations recorded in
`root-causes.md`; they were not separately re-confirmed by the owner and any
of them can be reopened before its stage begins. D-2, D-4, D-7 and D-8 are
genuinely open and block the stages noted above.

**D-12 was raised during S5**, not by the audit. `ControllerPoller.POLL_
INTERVAL` is 5 ms (~200 Hz) under a comment claiming 50 Hz. Nothing was
changed: the manual-mode command rate is felt at the bench, so which number
is right is a hardware judgement. It does not block S5 — the rate is the same
as it has always been — but it should be settled before S8 tunes anything
around it.

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
| DC-1 | RC3 | S7 | root cause | open |
| DC-2 | RC1 | S2 | root cause | closed (close_device_view no longer tears down; test_i_1_5_active_models_written_only_by_system_manager) |
| DC-3 | RC1 | S2 | root cause | closed (test_probe_teardown_order_is_stop_then_poller_then_transport, tests/core/test_lifecycle_teardown.py) |
| DC-4 | RC6 | S9 | root cause | open |
| DC-5 | RC4 | S5 | root cause | open |
| DC-6 | RC7 | S10 | root cause | open |
| DC-7 | RC6 | S9 | root cause | open |
| DC-8 | RC6 | S9 | root cause | open |
| DC-9 | RC1 | S2 | root cause | open (mitigated S2 INTERIM: close affordance removed; D-1 hide/show lands in S6) |
| DC-10 | RC3 | S7 | root cause | open |
| DC-11 | RC7 / RC3 | S10 | root cause | open |
| DC-12 | RC9 | S12 | root cause | open |
| DC-13 | RC2 / RC7 | S3 | root cause | open |
| DC-14 | RC7 | S1 | root cause | closed (test_d11_serial_port_is_readonly_in_every_schema) |
| DC-15 | RC6 | S9 | root cause | open |
| DC-16 | RC4 | S5 | root cause | open |
| DC-17 | RC4 / RC3 | S5 | root cause | open |
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
| GAMEPAD-3 | RC3 | S7 | root cause | open |
| GAMEPAD-4 | RC3 | S7 | root cause | open |
| GAMEPAD-5 | RC13 / RC7 | S5 | root cause | open |
| GAMEPAD-6 | RC7 | S10 | root cause | open |
| GAMEPAD-7 | RC4 | S5 | root cause | open |
| GAMEPAD-8 | RC4 | S5 | root cause | open |
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
| PYSIDE-5 | RC6 | S9 | root cause | open |
| PYSIDE-6 | RC6 | S9 | root cause | open |
| PYSIDE-7 | RC7 | S10 | root cause | open |
| PYSIDE-8 | RC7 | S10 | root cause | open |
| PYSIDE-9 | RC4 | S5 | root cause | open |
| PYSIDE-10 | RC8 / RC4 | S11 | root cause | open |
| PYSIDE-11 | LOCAL-OK | S15 | explicit | open |
| PYSIDE-12 | RC7 | S10 | root cause | open |
| PYSIDE-13 | RC1 | S1 | root cause | closed (test_d11_no_runtime_serial_reconnect) |
| PYSIDE-14 | RC4 | S5 | root cause | open |
| PYSIDE-15 | RC8 | S11 | root cause | open |
| PYSIDE-16 | LOCAL-OK | S15 | explicit | open |
| PYSIDE-17 | LOCAL-OK | S15 | explicit | open |
| PYSIDE-18 | LOCAL-OK | S15 | explicit | open |
| PYSIDE-19 | RC6 | S9 | root cause | open |
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
| REDPERCENT-12 | RC1 | S2 | root cause | open (mitigated S2 INTERIM: close affordance removed; D-1 hide/show lands in S6) |
| REDPERCENT-13 | RC7 | S10 | root cause | open |
| REDPERCENT-14 | RC6 | S9 | root cause | open |
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
| ROTATOR-10 | RC1 | S2 | root cause | open (mitigated S2 INTERIM: close affordance removed; D-1 hide/show lands in S6) |
| ROTATOR-11 | RC2 / LOCAL-OK | S3 | explicit | open |
| ROTATOR-12 | RC6 | S9 | root cause | open |
| ROTATOR-13 | RC2 / RC8 | S3 | root cause | open |
| ROTATOR-14 | RC1 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| ROTATOR-15 | RC8 / doc | S11 | root cause | open |
| SERIAL-1 | RC2 | S3 | root cause | closed (test_a_failed_disable_faults_instead_of_claiming_the_system_is_off, tests/core/test_transport_truth.py) |
| SERIAL-2 | RC1 | S2 | root cause | closed (test_probe_teardown_sends_hardware_stop_when_poller_stop_raises, tests/core/test_lifecycle_teardown.py) |
| SERIAL-3 | RC1 | S2 | root cause | closed (close_device_view no longer tears down; test_i_1_5_active_models_written_only_by_system_manager) |
| SERIAL-4 | RC1 | S2 | root cause | open (mitigated S2 INTERIM: close affordance removed; D-1 hide/show lands in S6) |
| SERIAL-5 | RC1 / RC10 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| SERIAL-6 | RC4 | S5 | root cause | open |
| SERIAL-7 | RC2 | S3 | root cause | open |
| SERIAL-8 | RC2 | S3 | root cause | open |
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
| STEPPER-5 | RC3 | S7 | root cause | open |
| STEPPER-6 | RC3 | S7 | root cause | open |
| STEPPER-7 | RC5 / RC3 | S8 | root cause | open |
| STEPPER-8 | RC5 | S8 | root cause | open |
| STEPPER-9 | RC2 / LOCAL-OK | S3 | explicit | open |
| STEPPER-10 | RC7 | S10 | root cause | open |
| STEPPER-11 | RC6 / RC7 / RC3 | S9 | root cause | open |
| STEPPER-12 | RC7 | S1 | root cause | closed (test_d11_serial_port_is_readonly_in_every_schema) |
| STEPPER-13 | RC9 | S12 | root cause | open |
| STEPPER-14 | RC4 | S5 | root cause | open |
| STEPPER-15 | RC4 | S5 | root cause | open |
| TEMP-1 | RC1 / RC10 | S2 | root cause | closed (test_shutdown_resolves_the_manager_when_it_fires_not_when_installed) |
| TEMP-2 | RC2 | S3 | root cause | open |
| TEMP-3 | RC6 | S9 | root cause | open |
| TEMP-4 | RC6 | S9 | root cause | open |
| TEMP-5 | RC1 | S2 | root cause | open (mitigated S2 INTERIM: close affordance removed; D-1 hide/show lands in S6) |
| TEMP-6 | RC1 | S2 | root cause | closed (view no longer constructs models; open_device_view refuses an unconfigured device) |
| TEMP-7 | RC5 | S8 | root cause | open |
| TEMP-8 | RC1 | S2 | root cause | closed (web re-setup routed through teardown-then-build; test_web_setup.py) |
| TEMP-9 | LOCAL-OK | S15 | explicit | open |
| TEMP-10 | RC2 / RC8 | S3 | root cause | open |
| TEMP-11 | RC1 / RC2 / doc | S2 | root cause | open |
| TEMP-12 | RC8 | S11 | root cause | open |
| TEMP-13 | RC6 | S9 | root cause | open |
| VIEW-TKINTER-1 | RC1 | S2 | root cause | open (mitigated S2 INTERIM: close affordance removed; D-1 hide/show lands in S6) |
| VIEW-TKINTER-2 | RC8 | S11 | root cause | open |
| VIEW-TKINTER-3 | RC3 | S7 | root cause | open |
| VIEW-TKINTER-4 | RC3 | S7 | root cause | open |
| VIEW-TKINTER-5 | RC4 | S5 | root cause | open |
| VIEW-TKINTER-6 | RC13 | S5 | root cause | open |
| VIEW-TKINTER-7 | RC1 | S2 | root cause | closed (verified by inspection: app.py builds before withdraw and reports failure) |
| VIEW-TKINTER-8 | RC1 | S2 | root cause | closed (tests/core/test_lifecycle_exit.py) |
| VIEW-TKINTER-9 | RC4 | S5 | root cause | open |
| VIEW-TKINTER-10 | RC13 / RC7 | S5 | root cause | open |
| VIEW-TKINTER-11 | RC4 | S5 | root cause | open |
| VIEW-TKINTER-12 | RC4 | S5 | root cause | open |
| VIEW-TKINTER-13 | RC3 | S7 | root cause | open |
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
