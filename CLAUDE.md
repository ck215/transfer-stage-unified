# transfer-stage-unified — mvc-refactor

A lab-instrument control app, "the station": stepper and DC probes, a chuck
positioner, a Temperature Controller, an SMC100 Rotator, and a
screen-capture Red Percent monitor, plus the Transfer Map (the recorded-trial
end goal) with its Sample Map. One frontend, the Web view (owner ruling
2026-10-07; the Tk and PySide6 views are frozen at `413f504` and
unregistered), over one Controller. Firmware is untouched;
every byte on the wire is identical to `legacy/src`'s (the repair tree
the lab ran Aug 26–Sep 22, 2026), pinned by `tests/golden/`.

The import rules between the `src/` layers (controller, model, devices, views) are a test (`tests/test_architecture.py`).

## Read these first, in this order

1. `docs/rebuild/STATUS.md` — cold resume: where things are, how to run and
   verify, the owner rulings, the open items.
2. `docs/rebuild/BRIEF.md` — the architecture contract and its addenda
   (paths in it are pre-move; its banner maps them).
3. `docs/rebuild/DESIGN_BRIEF.md` — the design ruling for the Web view
   ("Bench sheet, tiered", 2026-09-25, plus the procedure section of
   2026-10-07; `WEB_DESIGN_BRIEF.md` is its superseded predecessor).
4. `docs/rebuild/BUGFIX_PLAN.md` — the ranked defect list, a route per item.
5. `docs/rebuild/MODEL_CONTRACT.md` — how to add a device: what a `Model`
   subclass must provide and what the views do for free;
   `tests/test_model_contract.py` asserts it against every registered class.
6. `docs/rebuild/RECORDING_A_TRIAL.md` — the Transfer Map (the project's
   end goal): the procedure, how a trial is recorded, where the store lives,
   the force definitions.

Inputs that open work still reads, kept with a banner: the finding ledger
`docs/implementation/progress.md` (`docs/rebuild/carry.json` cites its IDs),
`docs/architecture/audit/*.md`, `root-causes.md`, `safety-pattern.md`,
`docs/implementation/bench-checklist.md`, and `tests/TEST_PORTING.md` (the
second test wave).

## Commands (from the repo root; `$PY` = `../main/.venv/bin/python`, see `station-map`)

```
./run.sh --no-browser --port 8080       # the Web view, the only one; python3 src/app.py ... in the venv
# test gates: see the verify skill (the fast gate, golden, legacy, a launch)
```

One entry point, `station-web` (`pyproject.toml` `[project.scripts]`), and one
PyInstaller launcher. `--tk` and `--qt` print a retired message and exit 2.
`pip install -e .[qt]` is needed only to run the frozen Qt view's tests, which
are optional and the lead's.

Launchers: `run.sh` (macOS and Linux) and `run.bat` (Windows) find the venv and
pass every flag to `src/app.py`, printing nothing on success; the firmware
check is Setup's Firmware row (`src/controller/firmware.py`), `update.sh` /
`update.bat` stay. `dev/swap_branch.sh` runs `main`'s app on the same boards,
flashing first (`RUN_SWAP_DRY_RUN=1` prints, runs nothing). Agents do not run
the Qt pass (a native SIGABRT can kill the session). **While anyone is working
at this Mac, everything runs strictly in the background**:
`STATION_NO_WINDOWS=1` before every pytest (skips the `window`-marked tests
that map a real Tk window; the lead runs them later), headless Chrome only, and
no capture harness on screen (owner ruling 2026-09-26). Write test output to a
file and read pytest's exit code unpiped.

## Skills

| Skill | When |
|---|---|
| `station-map` | before auditing, pruning or relocating anything: the code trees and worktrees, the owner rulings that make a "missing" feature intentional, the traps |
| `verify` | after any change under `src/` or `tests/`; the gates and a launch before any merge |
| `parallel-stage` | several independent BUGFIX_PLAN items at once: exclusive write sets, one worktree per agent, the lead verifies and merges |

Agent profiles in `.claude/agents/`: `worktree-fixer` (fixes plan items in a
worktree), `main-feature-auditor` (read-only, `main` vs the rebuild, one
subsystem each), `docs-pruner`, `mvc-relayout`, `ui-refiner` (the Web view, Impeccable
in Operate mode, before/after captures), `ui-auditor` (read-only, one design
skill per agent, fixed finding format). Fresh context, briefed by
the lead (a brief may pick the model by task). Design skills in `~/.claude/skills`: `impeccable` (primary),
`web-design-guidelines` (Web accessibility gate), `ui-ux-pro-max` (guideline
lookups only; its design-system generator is off-target for this product).

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
- **No platform-specific UI** (owner ruling 2026-09-25): shortcuts and copy
  are identical on macOS, Windows and Linux. Ctrl+. is the one stop chord.
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

The staged repair, the from-scratch rebuild, the 2026-09-23 relayout, the
retirement of `legacy/` and the 2026-10-07 round (Web the only frontend,
settled-frame recording, the full-display recorder, the procedure phases) are
in `docs/rebuild/STATUS.md` under "History" and "Round 2026-10-07".
