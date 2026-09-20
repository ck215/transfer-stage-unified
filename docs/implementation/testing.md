# Test Policy

How the suite is divided, what is disabled and why, and which command to run
at each stage. Companion to [plan.md](plan.md); status lives in
[progress.md](progress.md).

## The problem this solves

Before this pass, validating anything meant running all 264 tests: about
three minutes, 12 failures of mixed provenance, and — worse — a
**non-deterministic native SIGABRT from Qt that killed the session and
discarded every already-passed result**. Measured 2026-09-19: two of three
identical full runs aborted with exit 134. A sweep that randomly throws away
its own results is not a gate; it teaches you to ignore it.

The suite is now divided so that each stage validates only what it touches,
and the expensive, fragile parts are quarantined into their own passes at
the end.

| Run | Command | Time |
|---|---|---|
| Fast gate (default working loop) | `pytest tests/ -m "not slow and not order_dependent and not qt"` | ~30 s |
| One concern (e.g. S3) | `pytest tests/ -m "transport and not slow and not order_dependent"` | ~3 s |
| Full sweep, main pass | `pytest tests/ -m "not order_dependent and not qt"` | ~2.5 min |
| Full sweep, Qt pass | `pytest tests/ -m "qt and not order_dependent"` | ~5 s |
| Full sweep, isolation pass | each `order_dependent` test, one at a time | ~5 s |

Run the fast gate constantly. Run the full sweep at stage boundaries and
before the final hand-off — not in between.

## Markers

**Concern markers** select the subset a stage actually touches. They are
applied per file from `_FILE_MARKERS` in `tests/conftest.py` — one table,
rather than 39 scattered `pytestmark` lines.

`lifecycle` · `transport` · `estop` · `mode` · `loops` · `params` ·
`schema` · `errors` · `bootstrap` · `redpercent` · `scripting` · `web` ·
`integration` · `invariants`

**Execution markers** control cost and fragility: `slow`, `qt`, `tk`,
`known_bad`, `order_dependent`.

`--strict-markers` is on, so a typo in a marker name is an error rather
than a silently empty selection.

### Why `slow` exists

The `slow` files are dominated by **real sleeps during model construction** —
`serial.py:63`'s 1.5 s bootloader wait and `probes.py:168`'s `sleep(1)` —
about 4.5 s per test, roughly 160 s of the 180 s total. Excluding them is
what takes the working loop from three minutes to 28 seconds.

Those sleeps are themselves findings (SERIAL-6, RC-4). **When S5 moves
construction off the calling thread, most of this marking should be deleted,
not maintained.** Do not treat the `slow` list as permanent furniture.

## Disabled tests

Nothing is deleted to make the suite green. There are two quarantines, and
they mean different things.

### `known_bad` — the test is wrong, or the product is

Registry: `_KNOWN_BAD` in `tests/conftest.py`. Each entry carries a reason
and an **owning stage**. Applied as `xfail(strict=True)` deliberately: when
the stage lands and the test starts passing, pytest reports XPASS **as a
failure**, which forces someone to re-author the test and update the ledger.
A quarantine that goes stale silently is how suites rot.

**The quarantine is empty as of S10**, and keeping it that way is the point
of the policy rather than a milestone. The five gamepad/mode entries retired
in S7; the two S10 entries retired in S10 — one XPASSed the moment schema v2
made PYSIDE-7 inexpressible, the other had to be re-authored against the
schema toggles that replaced the control it asserted. The history below is
kept because the *reasons* entries left the list are the useful part.

The seven were all of one kind: **stale tests asserting behavior that was
deliberately removed.** Re-author them when their stage lands; the product is
right and the test is wrong.

- Five assert `manual_flag` becomes True after `enter_manual()` with no
  gamepad bound. Commit `046533f` made that refuse — that fix is *why* manual
  mode no longer energizes coils with no pad attached. Owned by **S7**, which
  redefines the mode contract as `ProbeMode`. **S7 is blocked on D-2**, so
  expect these to sit for a while.
- `test_pyside_redpercent_sync_and_probe_controls` asserts `view.sync_cbs`,
  the duplicate hand-built checkbox row deleted in `046533f` as known-issues
  #5. It guards a redundancy removed on purpose. **S10**.
- `test_pyside_dashboard_sidebar_dock_sync` — PYSIDE-7: a dropdown with
  `model_attr` and no `command` reaches `getattr(self.model, None)` and
  raises `TypeError`. This one is a real product bug. **S10**.

Three entries left this list in S8 and S9 and the reasons are worth keeping:

- `test_run_script_unrecognized_actions` was quarantined with "the silence is
  **not yet diagnosed**". It is now: the test watched `serial_comm.ser.write`,
  one of the 11 raw bypasses S3 deleted. The model writes through
  `write_command()`, so `.ser.write` is never called. The behavior was correct
  all along; the test was watching the wrong object.
- `test_run_script_gcode_execution_path` (blamed on RC-6) and
  `test_run_script_malformed_gcode` (blamed on RC-8) were **not product bugs
  at all** — see the correction below. Both were mock-parser artefacts.
  **`test_run_script_gcode_execution_path` therefore never provided the RC-6
  script-path coverage it was credited with. That coverage has to be written
  fresh in S9 item 3.**

### `order_dependent` — the test is fine, the harness leaks

> **Seven of these were retired in S9, and the cause recorded here was
> wrong.** The `run_script` family was blamed on `ErrorRouter`'s
> process-global callbacks (RC-8) and on STEPPER-8's untracked thread. It was
> neither — S8's generation token landing without moving them was the first
> clue. The test file installed its mock parser with
> `sys.modules['gcodeparser'] = mock` at import time, which only takes effect
> when that file is what *first* imports `model.probes`; several earlier
> files import it.
>
> It did not read as a wiring mistake because the two parsers disagree
> subtly: the real one yields `Y` as an **int**, so `str(Y)` is `'20'`, while
> the mock yields `20.0`, so it is `'20.0'`. Same test, two parsers, outcome
> decided by import order — and the symptom was an assertion about number
> formatting, which is exactly what RC-6 looks like.
>
> **Read the actual assertion values before reaching for the audit's list of
> process-global suspects.**

Registry: `_ORDER_DEPENDENT` in `tests/conftest.py`. These **pass in
isolation and fail in composition**, so `xfail` would be wrong — it would
XPASS the moment anyone ran the test alone. They are excluded from targeted
and default runs and get their own pass at the end.

**Three tests** remain: a Tk teardown-ordering test, and two web tests with
wall-clock assertions (`>= 0.28 s`, "at least 5 reads per poller") that miss
when other tests' threads compete for the GIL. **Do not "fix" one by adding a
sleep or loosening a threshold** — for the wall-clock pair the threshold *is*
the assertion.

### Qt is isolated, not disabled

Every test using `qapp`/`qtbot` is auto-marked `qt` by fixture — exactly,
not by filename — so the main sweep can run `-m "not qt"` and survive a Qt
abort with its results intact. If the Qt pass aborts, rerun just that pass.

**`tests/ui/` is no longer excluded.** It was, from S0 to S10, on the stated
grounds of "a native SIGABRT (qt_check_pointer inside QApplication()) that
the chflags self-heal does not fully prevent". That was wrong. The directory
**hung** — on a modal `QMessageBox` opened in a view's command-failure path,
with nobody to click it — and each attempt to run it was killed and filed as
an abort, because a killed pytest prints no summary and leaves only its
buffered dots behind. The whole directory now runs in about 4 seconds.

The cost of that mistake is worth stating plainly, because it is the reason
rule 6 exists: `tests/ui/test_schema_v2.py`, the 81-check conformance suite
written in S10 as schema v2's safety net, lived in the excluded directory and
had **never run in any documented gate**.

### A hang is not a result

`faulthandler_timeout = 60` in `pytest.ini`. Any test that stops making
progress for a minute dumps every thread's stack and fails the run. The
slowest test in the suite is about 5 s, so this cannot fire on slowness.

Three consecutive sessions failed to get a result out of the Qt pass and
recorded the killed runs as an unknown, then as an abort. A killed pytest
prints no summary line — **if you have no summary line, you have no
result**, whatever the exit code says.

## Per-stage gates

Each stage runs its own concern plus the concerns it could regress. Append
`and not slow and not order_dependent` while working; drop `not slow` at the
stage's exit.

| Stage | Gate |
|---|---|
| S0 | `-m "invariants"` |
| S1 | `-m "bootstrap or schema"` |
| S2 | `-m "lifecycle or estop"` |
| S3 | `-m "transport or estop or scripting"` |
| S4 | `-m "web"` |
| S5 | `-m "loops or mode or transport"` |
| S6 | `-m "lifecycle or loops"` |
| S7 | `-m "mode or estop or loops"` |
| S8 | `-m "estop or transport or mode"` |
| S9 | `-m "params or scripting or schema"` |
| S10 | `-m "schema or redpercent"` + the Qt pass |
| S11 | `-m "errors or schema or web"` |
| S12 | `-m "bootstrap or lifecycle or redpercent"` |
| S13 | `-m "redpercent or errors"` |
| S14 | `-m "web or errors"` |
| S15 | fast gate |
| S16 | owner, at the bench — plus `-m "transport"` after reflashing |

**A stage does not close on a green targeted gate alone.** Its exit also
requires the full sweep (all three passes) to be green, and any `known_bad`
entry it owns to be removed rather than left XPASSing.

## Rules

1. **Never delete a test to make the suite green.** Quarantine it with a
   reason and an owning stage.
2. **Never add to `known_bad` without naming the stage that resolves it.**
3. **Never loosen a timing assertion** to fix an `order_dependent` test.
   The leak is the bug.
4. **Re-measure rather than trust this file.** `--durations=40` tells you
   what is actually slow now; these figures are from 2026-09-19.

   This is not hypothetical: the first version of this file reported the fast
   gate as "200 passed, 5 xfailed" when the commit it cited actually gave
   **194 passed, 3 xfailed**. The error was caught in S1 by diffing collected
   node IDs against the commit rather than trusting the written number. Diff
   node IDs, do not reconcile totals by arithmetic.
5. **New tests carry a concern marker**, or they will not run in any
   targeted gate.
6. **Never exclude a directory or file from collection.** A marker excludes
   a test from *this* run and the sweep picks it up later; `--ignore` and
   `--deselect` exclude it from every run, and nothing reports what is
   missing. `tests/ui/` sat outside the suite from S0 to S10 on a
   misdiagnosis, taking S10's own 81-check conformance suite with it. If
   something genuinely cannot run, quarantine it per rule 1 so it has an
   owning stage and a name.
7. **A test whose *outcome* is order-dependent belongs in
   `order_dependent`, never in `known_bad`.** Strict `xfail` reports such a
   test as XPASS-as-failure when run alone and as a clean xfail when run in
   composition — so the marker asserts "known broken" about something that is
   only conditionally broken, and the composed sweep stays silent. Three
   `run_script` tests sat like that until S8. If a `known_bad` entry passes
   when you run its file alone, it is in the wrong list.
8. **Run the isolation pass at every stage boundary, not just the final
   one.** `order_dependent` tests are excluded from the fast gate, every
   concern gate *and* the main sweep, so nothing routine touches them. In S5
   this was found the hard way: `test_dashboard_window_teardown_ordering`
   still called `register_model`, which S2 had renamed two stages earlier,
   and no gate had run it since. An excluded test is not a quarantined test —
   it is an unwatched one.

---

*Baseline at `b37cc9a` (2026-09-19, pre-S0): 264 collected (`tests/ui`
excluded on top of that). Fast gate: 197 selected — 194 pass, 3 xfail. Main
pass: 247 selected — 239 pass, 8 xfail (known-bad), identical across four
consecutive sweeps. Qt pass: 10. Order-dependent: 7. Known-bad: 10. Slow: 56.*

*After S0 and S1: 272 collected (+9 invariant tests, −1 test deleted with the
feature it covered). Fast gate: 206 selected — 199 pass, 7 xfail. Every delta
accounted for by node-ID diff against `b37cc9a`.*

*After S9 (2026-09-20): main pass **390 passed, 6 xfailed**, identical across
three consecutive sweeps (~3:15). Qt: 7 passed, 2 xfailed. Order-dependent:
**3**, down from 10 — seven retired when the `run_script` family turned out
to be a mock-parser import-order problem, not the process-global state this
file had blamed. `known_bad`: **7** — five gamepad/mode tests owned by S7
(itself blocked on D-2) and two PySide schema tests owned by S10.*

*After S5 part 2: fast gate 272 pass, 6 xfail (~38 s). The xfail count has
fallen from 9 as invariants started holding — **I-1.5 retired in S2, I-2.3 in
S3** — which is the intended direction. A retirement is forced, not optional:
`xfail(strict=True)` turns a now-passing invariant into a failure until
someone deletes the marker.*

*After S7 (2026-09-20): fast gate **364 passed, 1 xfailed** (~58 s). Qt: 7
passed, 2 xfailed. **`known_bad` is down from 7 to 2**, both PySide schema
tests owned by S10 — the five S7 entries were re-authored, not deleted: they
assumed `enter_manual()` succeeds with no gamepad, and they now bind a pad,
which is the contract. Eight further tests were re-authored for the same
reason: they forced a mode flag (`probe.manual_flag = True`) to reach a state
the model will not enter on its own. Those assignments raise now (I-3.4).*

*The single remaining fast-gate `xfail` is **I-7.1**, owned by S10. Three of
the four grep invariants have now retired.*

*After S6 (2026-09-20): fast gate **380 passed, 1 xfailed**; slow 57; qt 7
passed 2 xfailed; **order-dependent 2, down from 3**. The retired entry,
`test_dashboard_window_teardown_ordering`, had a cause nothing here had
guessed: `sys.modules['tkinter.ttk']` was a bare MagicMock, so subclassing
`ttk.Notebook` produced a MagicMock rather than a class, and its finite
`side_effect` iterator capped how many times `DashboardWindow` could be
constructed per process. `ttk.Notebook` is a real stub class now. Note the
two bindings it needed: `from tkinter import ttk` reads the attribute off the
module object, not `sys.modules`.*

*Mid-S10 (2026-09-20), **partially verified**: fast gate **390 passed, 1
xfailed** (~59 s). The **Qt pass was not run to completion**, and its two
`known_bad` entries are S10-owned and expected to XPASS — which
`xfail(strict=True)` reports as a failure, on purpose, to force
re-authoring. Run it before trusting this stage. The slow and
order-dependent passes were also not re-run after the renderer rewrite.*

*One quarantine note for whoever picks this up. The `order_dependent` set was
predicted to dissolve during S3/S8/S11; it fell from 10 to 3 at S8 — but for
a reason none of the predictions named (a mock-parser import-order problem).
Treat the remaining three the same way: **read the actual assertion values
before believing any architectural explanation for them.***

*After S10 (2026-09-20), **all four passes verified in one session** for the
first time since S0. Fast gate **491 passed, 1 xfailed** (~59 s); main sweep
**548 passed, 1 xfailed** (~3 min 17 s); Qt pass **35 passed** (~6 s);
`order_dependent` pass **2 passed** (~2 s). The single xfail is I-7.1, owned
by S12.*

*Three numbers here correct earlier entries. The Qt pass had not completed
since S0 — it hung rather than aborting, and `--ignore=tests/ui` had hidden
103 tests including the schema-v2 conformance suite; both are fixed, which is
most of the 390 → 491 fast-gate jump. The `order_dependent` set is **2**, not
the 3 the note above records: S8 left 3, S6's `ttk.Notebook` fix retired one
more. And the quarantine is genuinely empty — `_KNOWN_BAD = {}` in
`conftest.py`, no entries, so nothing is being held green by an xfail.*

*The standing warning still applies to the remaining two. Both are
wall-clock assertions and both were predicted to dissolve at S11. **Read
their actual assertion values before believing that.** A timing threshold
that misses under GIL contention is not obviously an `ErrorRouter` problem,
and this prediction has now been wrong three times.*

*After S11 (2026-09-20): fast gate **505 passed, 1 xfailed** (~59 s); full
sweep **564 passed, 1 xfailed** (~3 min 18 s, `order_dependent` included);
Qt **35 passed** (~6 s). Quarantine still empty. The one xfail is I-7.1,
owned by S12.*

*Two things about this file's own machinery changed at S11.*

*First, **`tests/conftest.py` was duplicated end to end** — lines 1-543 and
544-1011 byte-identical, so every fixture and `pytest_collection_modifyitems`
itself were defined twice and only the **second** copy was live. The marker
logic that decides what `slow`, `qt`, `known_bad` and `order_dependent` mean
was in the duplicated region. **Any conftest change made between S0 and S10
that landed in the first half never ran.** If a fixture does not behave the
way its source says, check that first. The duplicate is gone and a comment
marks the join.*

*Second, the **`order_dependent` pair passed in full composition five times
running** — three of those at `7cc5f3c`, before S11 existed. They are
deliberately **still marked**. Both are wall-clock assertions about the HTTP
server (a request under 0.25 s; four pollers over five reads each), neither
has anything to do with the `ErrorRouter` change that was predicted to fix
them, and passing on an idle machine is not evidence of a fix. The likeliest
cause is that S10's un-ignoring of `tests/ui` changed what runs alongside
them. Do not retire the marker without knowing which.*
