# transfer-stage-unified — mvc-refactor

A lab-instrument control app, "the station": stepper and DC probes, a chuck
positioner, a Temperature Controller, an SMC100 Rotator, and a
screen-capture Red Percent monitor. Three frontends — Web (the candidate
primary), Tkinter and PySide6 — over one Controller. Firmware is untouched;
every byte on the wire is identical to `legacy/src`'s (the repair tree
the lab ran Aug 26–Sep 22, 2026), pinned by `tests/golden/`.

## Tree

```
src/                     the app
  app.py                 run as a script: python3 src/app.py --web | --qt | --tk
  events.py panel.py param.py schema.py result.py palette.py
  controller/            controller.py (owns the models), setup.py (port scan, builds models)
  model/                 base.py (Model, the one estop latch), probe.py heater.py
                         rotator.py red_monitor.py plot_data.py
  devices/               serial_port, gamepad, smc100, screen (only place hardware libs are imported)
  views/                 tk.py, qt.py, web/ (hold the Controller and nothing else)
tests/                   the app's suite; tests/golden/ holds the wire captures
legacy/src/              the old MVC repair tree: reference for the golden wire captures only
legacy/tests/            the old suite; still runnable
firmware/                Arduino / Teensy sketches, unchanged
docs/rebuild/            current docs; docs/archive/ is history
```

The import rules between these layers are a test (`tests/test_architecture.py`).

## Read these first, in this order

1. `docs/rebuild/STATUS.md` — cold resume: where things are, how to run and
   verify, the owner rulings, the open items.
2. `docs/rebuild/BRIEF.md` — the architecture contract and its addenda
   (paths in it are pre-move; its banner maps them).
3. `docs/rebuild/WEB_DESIGN_BRIEF.md` — the Web view's design ruling.
4. `docs/rebuild/BUGFIX_PLAN.md` — the ranked defect list, a route per item.

Inputs that open work still reads, kept with a banner: the finding ledger
`docs/implementation/progress.md` (`docs/rebuild/carry.json` cites its IDs),
`docs/architecture/audit/*.md`, `root-causes.md`, `safety-pattern.md`,
`docs/implementation/bench-checklist.md`, and `tests/TEST_PORTING.md` (the
second test wave).

## Commands (from the repo root)

```
python3 src/app.py --web --no-browser --port 8080
python3 -m pytest tests -q -p no:cacheprovider -m "not qt"                        # 1527 pass
QT_QPA_PLATFORM=offscreen python3 -m pytest tests -q -p no:cacheprovider -m qt    # 85 pass
python3 -m pytest tests/test_wire_golden.py -q                                    # 78 scenarios byte-identical to legacy/src
cd legacy && python3 -m pytest tests -q -m "not slow and not order_dependent and not qt"   # the old suite
```

Launchers: `run.sh` / `run_macos.sh` / `run.bat`, passing `--web|--qt|--tk`
through. Agents do not run the Qt pass (a native SIGABRT can kill the
session); the lead does. Write test output to a file and read pytest's exit
code unpiped.

## Skills

| Skill | When |
|---|---|
| `station-map` | before auditing, pruning or relocating anything: the three code trees, the owner rulings that make a "missing" feature intentional, the traps |
| `verify` | after any change under `src/` or `tests/`; the four gates and a launch before any merge |
| `parallel-stage` | several independent BUGFIX_PLAN items at once: exclusive write sets, one worktree per agent, the lead verifies and merges |

Agent profiles in `.claude/agents/`: `worktree-fixer` (fixes plan items in a
worktree), `main-feature-auditor` (read-only, `main` vs the rebuild, one
subsystem each), `docs-pruner`, `mvc-relayout`. All Opus, fresh context,
briefed by the lead.

## Standing rules

- **Safety paths before feature paths.** If it can energize a coil or move
  an axis, the stop path is implemented and tested first.
- **Tests before implementation**: prove the defect against the pre-fix
  code, then fix. Never bump a baseline to make a test green.
- **Never answer an owner decision (`D-n`).** D-7 (the DC board has no coil
  kill) is open, bench-only, owner-only. Bench values are the owner's.
- **Work lands by worktree, not by stage** (replaces "one stage per
  commit"): each agent works in its own worktree on an exclusive write set
  and commits there; the lead diffs the write set, re-runs the tests,
  hand-drives the feature, and merges. Core files change only by the lead.
- Never push, never amend, unless the lead says so.
- `progress.md` is frozen; do not flip its rows.
- Scratch files, patches and logs never land in the repo root.

## Two traps this codebase sets

1. **Comments quote the old broken code.** `src/` and `legacy/src/` document
   their own repairs at length, so a grep hit for a defect is often prose
   about its removal. Read the matching line.
2. **The ledger drifts.** In `progress.md`, fixes landed and rows were not
   flipped: a stage marked `done` does not mean its findings are closed, and
   a 2026-09-20 reconciliation found 28 of 66 such rows already fixed.
   Check the code, never the ledger, before calling a finding open.

## History

- Aug–Sep 2026: `src/` was a staged MVC repair (S0–S16) of 13 root causes
  across 213 audited findings. Plan and test policy are in `docs/archive/`.
- 2026-09-23: the refactor redone from scratch as `station/` on a `rebuild`
  branch was fast-forwarded here; `rebuild` retired. The same day `station/`
  became `src/`, the old tree became `legacy/`, and stale docs were archived.
- 2026-09-23: `verify` and `parallel-stage` rewritten for the new tree;
  `stage-close`, `reconcile-ledger` and `fix-a-finding` retired with the
  ledger process (in git history before `beb7b94`).
- `legacy/` is deleted once every file in `tests/TEST_PORTING.md` has a
  ported equivalent; then `mvc-refactor` merges to `main`.
