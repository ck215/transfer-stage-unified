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
| S0 | Baseline, plan, invariant harness | done | `bd6f205` | 2026-09-19 | Docs baseline, plan, ledger, test division, invariant harness. |
| S1 | Purge legacy paths (D-9, D-11) | todo | | | Pure deletion. No dependencies. |
| S2 | Lifecycle authority (RC-1) | todo | | | Largest stage (41 findings). Split per numbered item. |
| S3 | Transport truth and E-stop latch | todo | | | SAFETY. |
| S4 | Web AppContext and security boundary | todo | | | Live CSRF hole; independent of S5+. |
| S5 | Input service and model-owned loops | todo | | | Highest coupling. RC-13 first, then RC-4. |
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

D-3, D-5, D-6 and D-10 carry the recommendations recorded in
`root-causes.md`; they were not separately re-confirmed by the owner and any
of them can be reopened before its stage begins. D-2, D-4, D-7 and D-8 are
genuinely open and block the stages noted above.

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
| DC-2 | RC1 | S2 | root cause | open |
| DC-3 | RC1 | S2 | root cause | open |
| DC-4 | RC6 | S9 | root cause | open |
| DC-5 | RC4 | S5 | root cause | open |
| DC-6 | RC7 | S10 | root cause | open |
| DC-7 | RC6 | S9 | root cause | open |
| DC-8 | RC6 | S9 | root cause | open |
| DC-9 | RC1 | S2 | root cause | open |
| DC-10 | RC3 | S7 | root cause | open |
| DC-11 | RC7 / RC3 | S10 | root cause | open |
| DC-12 | RC9 | S12 | root cause | open |
| DC-13 | RC2 / RC7 | S3 | root cause | open |
| DC-14 | RC7 | S1 | root cause | open |
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
| GAMEPAD-9 | RC1 | S2 | root cause | open |
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
| MANAGER-1 | RC1 / RC10 | S2 | root cause | open |
| MANAGER-2 | RC1 / RC10 | S2 | root cause | open |
| MANAGER-3 | RC1 | S2 | root cause | open |
| MANAGER-4 | RC1 / RC10 | S2 | root cause | open |
| MANAGER-5 | RC1 | S2 | root cause | open |
| MANAGER-6 | RC1 | S2 | root cause | open |
| MANAGER-7 | RC1 | S2 | root cause | open |
| MANAGER-8 | RC1 | S2 | root cause | open |
| MANAGER-9 | RC1 | S2 | root cause | open |
| MANAGER-10 | RC1 / RC5 | S2 | root cause | open |
| MANAGER-11 | RC1 | S2 | root cause | open |
| MANAGER-12 | RC9 | S12 | root cause | open |
| MANAGER-13 | RC4 / RC10 | S5 | root cause | open |
| MANAGER-14 | LOCAL-OK | S1 | explicit | open |
| MANAGER-15 | RC10 | S4 | root cause | open |
| MANAGER-16 | RC13 | S5 | root cause | open |
| MANAGER-17 | RC8 | S11 | root cause | open |
| MANAGER-18 | RC9 | S12 | root cause | open |
| MANAGER-19 | RC5 | S8 | root cause | open |
| MANAGER-20 | RC4 | S5 | root cause | open |
| PYSIDE-1 | RC1 | S2 | root cause | open |
| PYSIDE-2 | RC1 | S2 | root cause | open |
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
| PYSIDE-13 | RC1 | S1 | root cause | open |
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
| REDPERCENT-12 | RC1 | S2 | root cause | open |
| REDPERCENT-13 | RC7 | S10 | root cause | open |
| REDPERCENT-14 | RC6 | S9 | root cause | open |
| REDPERCENT-15 | RC9 | S12 | root cause | open |
| REDPERCENT-16 | RC11 | S13 | root cause | open |
| REDPERCENT-17 | RC7 | S10 | root cause | open |
| REDPERCENT-18 | RC7 | S10 | root cause | open |
| REDPERCENT-19 | RC7 | S10 | root cause | open |
| REDPERCENT-20 | LOCAL-OK | S15 | explicit | open |
| ROTATOR-1 | RC1 / RC5 | S2 | root cause | open |
| ROTATOR-2 | RC10 | S14 | root cause | open |
| ROTATOR-3 | RC8 / RC7 | S11 | root cause | open |
| ROTATOR-4 | RC5 | S8 | root cause | open |
| ROTATOR-5 | RC1 | S2 | root cause | open |
| ROTATOR-6 | RC4 | S5 | root cause | open |
| ROTATOR-7 | RC4 / RC10 | S5 | root cause | open |
| ROTATOR-8 | RC5 | S8 | root cause | open |
| ROTATOR-9 | RC2 / RC7 | S3 | root cause | open |
| ROTATOR-10 | RC1 | S2 | root cause | open |
| ROTATOR-11 | RC2 / LOCAL-OK | S3 | explicit | open |
| ROTATOR-12 | RC6 | S9 | root cause | open |
| ROTATOR-13 | RC2 / RC8 | S3 | root cause | open |
| ROTATOR-14 | RC1 | S2 | root cause | open |
| ROTATOR-15 | RC8 / doc | S11 | root cause | open |
| SERIAL-1 | RC2 | S3 | root cause | open |
| SERIAL-2 | RC1 | S2 | root cause | open |
| SERIAL-3 | RC1 | S2 | root cause | open |
| SERIAL-4 | RC1 | S2 | root cause | open |
| SERIAL-5 | RC1 / RC10 | S2 | root cause | open |
| SERIAL-6 | RC4 | S5 | root cause | open |
| SERIAL-7 | RC2 | S3 | root cause | open |
| SERIAL-8 | RC2 | S3 | root cause | open |
| SERIAL-9 | RC2 | S3 | root cause | open |
| SERIAL-10 | RC2 | S3 | root cause | open |
| SERIAL-11 | RC2 | S3 | root cause | open |
| SERIAL-12 | RC4 | S5 | root cause | open |
| SERIAL-13 | RC2 | S3 | root cause | open |
| SERIAL-14 | RC1 | S1 | root cause | open |
| SERIAL-15 | RC1 | S2 | root cause | open |
| SERIAL-16 | RC8 | S11 | root cause | open |
| SERIAL-17 | RC2 / LOCAL-OK | S3 | explicit | open |
| SERIAL-18 | RC1 | S1 | root cause | open |
| SERIAL-19 | doc | S0 | root cause | open |
| STEPPER-1 | RC1 | S2 | root cause | open |
| STEPPER-2 | RC4 | S5 | root cause | open |
| STEPPER-3 | RC1 | S2 | root cause | open |
| STEPPER-4 | RC2 | S3 | root cause | open |
| STEPPER-5 | RC3 | S7 | root cause | open |
| STEPPER-6 | RC3 | S7 | root cause | open |
| STEPPER-7 | RC5 / RC3 | S8 | root cause | open |
| STEPPER-8 | RC5 | S8 | root cause | open |
| STEPPER-9 | RC2 / LOCAL-OK | S3 | explicit | open |
| STEPPER-10 | RC7 | S10 | root cause | open |
| STEPPER-11 | RC6 / RC7 / RC3 | S9 | root cause | open |
| STEPPER-12 | RC7 | S1 | root cause | open |
| STEPPER-13 | RC9 | S12 | root cause | open |
| STEPPER-14 | RC4 | S5 | root cause | open |
| STEPPER-15 | RC4 | S5 | root cause | open |
| TEMP-1 | RC1 / RC10 | S2 | root cause | open |
| TEMP-2 | RC2 | S3 | root cause | open |
| TEMP-3 | RC6 | S9 | root cause | open |
| TEMP-4 | RC6 | S9 | root cause | open |
| TEMP-5 | RC1 | S2 | root cause | open |
| TEMP-6 | RC1 | S2 | root cause | open |
| TEMP-7 | RC5 | S8 | root cause | open |
| TEMP-8 | RC1 | S2 | root cause | open |
| TEMP-9 | LOCAL-OK | S15 | explicit | open |
| TEMP-10 | RC2 / RC8 | S3 | root cause | open |
| TEMP-11 | RC1 / RC2 / doc | S2 | root cause | open |
| TEMP-12 | RC8 | S11 | root cause | open |
| TEMP-13 | RC6 | S9 | root cause | open |
| VIEW-TKINTER-1 | RC1 | S2 | root cause | open |
| VIEW-TKINTER-2 | RC8 | S11 | root cause | open |
| VIEW-TKINTER-3 | RC3 | S7 | root cause | open |
| VIEW-TKINTER-4 | RC3 | S7 | root cause | open |
| VIEW-TKINTER-5 | RC4 | S5 | root cause | open |
| VIEW-TKINTER-6 | RC13 | S5 | root cause | open |
| VIEW-TKINTER-7 | RC1 | S2 | root cause | open |
| VIEW-TKINTER-8 | RC1 | S2 | root cause | open |
| VIEW-TKINTER-9 | RC4 | S5 | root cause | open |
| VIEW-TKINTER-10 | RC13 / RC7 | S5 | root cause | open |
| VIEW-TKINTER-11 | RC4 | S5 | root cause | open |
| VIEW-TKINTER-12 | RC4 | S5 | root cause | open |
| VIEW-TKINTER-13 | RC3 | S7 | root cause | open |
| VIEW-TKINTER-14 | RC11 / RC7 | S13 | root cause | open |
| VIEW-TKINTER-15 | RC11 | S13 | root cause | open |
| VIEW-TKINTER-16 | RC1 | S2 | root cause | open |
| VIEW-TKINTER-17 | RC7 / RC9 | S10 | root cause | open |
| VIEW-TKINTER-18 | LOCAL-OK | S15 | explicit | open |
| WEB-1 | RC1 / RC10 | S2 | root cause | open |
| WEB-2 | RC4 | S5 | root cause | open |
| WEB-3 | RC1 / RC10 | S2 | root cause | open |
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
