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
| Full sweep, Qt pass | `pytest tests/ -m "qt and not order_dependent"` | ~2 s |
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

Ten tests, in two kinds:

**Stale — asserting behavior that was deliberately removed** (re-author them;
the product is right and the test is wrong):

- Five tests assert `manual_flag` becomes True after `enter_manual()` with
  no gamepad bound. Commit `046533f` made that refuse — that fix is *why*
  manual mode no longer energizes coils with no pad attached. Owned by
  **S7**, which redefines the mode contract as `ProbeMode`.
- `test_pyside_redpercent_sync_and_probe_controls` asserts `view.sync_cbs`,
  the duplicate hand-built checkbox row deleted in `046533f` as
  known-issues #5. The test guards a redundancy we removed on purpose.
  Owned by **S10**.

**Real product bugs — the test is right and fails honestly** (the stage fixes
the product):

- `test_pyside_dashboard_sidebar_dock_sync` — PYSIDE-7: a dropdown with
  `model_attr` and no `command` reaches `getattr(self.model, None)` and
  raises `TypeError` (`pyside/view.py:315`). **S10**.
- `test_run_script_gcode_execution_path` — RC-6: untyped string params send
  `'20'` where `'20.0'` is expected. **S9**.
- `test_run_script_malformed_gcode` — RC-8: malformed G-code is swallowed;
  there is no result channel to carry a refusal. **S11**.
- `test_run_script_unrecognized_actions` — expects a raw `.ser.write`
  passthrough, one of the 11 transport bypasses S3 deletes. It currently
  writes nothing at all, and **that silence is not yet diagnosed** — do that
  in S3 rather than assuming.

### `order_dependent` — the test is fine, the harness leaks

> **Seven of these were retired in S9, and the recorded cause was wrong.**
> The `run_script` family was blamed on `ErrorRouter`'s process-global
> callbacks and on STEPPER-8's untracked thread. It was neither: the test
> file installed its mock parser via `sys.modules` at import time, so it only
> took effect when that file happened to import `model.probes` first. Two
> parsers, one test, outcome decided by import order. Read the assertion
> values before reaching for the audit's list of usual suspects.

Registry: `_ORDER_DEPENDENT` in `tests/conftest.py`. These **pass in
isolation and fail in composition**, so `xfail` would be wrong — it would
XPASS the moment anyone ran the test alone. They are excluded from targeted
and default runs and get their own pass at the end.

Seven tests: a Tk teardown-ordering test; two web tests with wall-clock
assertions (`>= 0.28 s`, "at least 5 reads per poller") that miss when other
tests' threads compete for the GIL; and the four `run_script` tests.

The `run_script` set was found by running four identical sweeps and
collecting which tests failed: `missing_file` and `no_serial_port_sim`
failed once each, then `macro_halting` and `non_utf8` failed once each —
never the same pair twice. Two process-global mechanisms explain it, and
**both are findings in the audit, not test defects**:

- `run_script` spawns an untracked thread with **no run token or
  generation** (STEPPER-8, RC-5), so a previous test's script thread can
  still be executing during the next test's assertions.
- `ErrorRouter`'s callbacks are class-level process state that the
  `_reset_global_error_routing` fixture clears after every test (RC-8), so
  whether a report lands depends on what ran before.

That is the same process-global theme as **RC-5**, **RC-8** and **RC-13**,
so this set should largely dissolve as those stages land — the suite's
flakiness is a symptom of the architecture under repair, which is worth
remembering before blaming the tests. **Do not "fix" one by adding a sleep
or loosening a threshold.**

### Qt is isolated, not disabled

`tests/ui/` stays excluded via `addopts` (a native SIGABRT the existing
chflags self-heal does not fully prevent). Separately, every test using
`qapp`/`qtbot` is auto-marked `qt` by fixture — exactly, not by filename —
so the main sweep can run `-m "not qt"` and survive a Qt abort with its
results intact. The Qt pass is 8 tests and about 2 seconds; if it aborts,
rerun just that pass.

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
6. **A test whose *outcome* is order-dependent belongs in
   `order_dependent`, never in `known_bad`.** Strict `xfail` reports such a
   test as XPASS-as-failure when run alone and as a clean xfail when run in
   composition — so the marker asserts "known broken" about something that is
   only conditionally broken, and the composed sweep stays silent. Three
   `run_script` tests sat like that until S8. If a `known_bad` entry passes
   when you run its file alone, it is in the wrong list.
7. **Run the isolation pass at every stage boundary, not just the final
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
file had blamed. `known_bad`: 5.*

*After S5 part 2: fast gate 272 pass, 6 xfail (~38 s). The xfail count has
fallen from 9 as invariants started holding — **I-1.5 retired in S2, I-2.3 in
S3** — which is the intended direction. A retirement is forced, not optional:
`xfail(strict=True)` turns a now-passing invariant into a failure until
someone deletes the marker.*

*Two quarantine notes for whoever picks this up. The `known_bad` list still
names S7/S9/S10/S11 as owners, so it should keep shrinking. The
`order_dependent` set was predicted to dissolve during S3/S8/S11; **S3 has
landed and it has not shrunk yet** — re-check it at S8 rather than assuming
the prediction held.*
