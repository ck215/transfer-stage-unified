# transfer-stage-unified — mvc-refactor

A lab-instrument control app, "the station": stepper and DC probes, a chuck
positioner, a Temperature Controller, an SMC100 Rotator, and a
screen-capture RGB Analysis monitor (formerly Red Percent), plus the Transfer Map (the recorded-trial
end goal) with its Sample Map, and user accounts (Guest = the station defaults). One frontend, the Web view (owner ruling
2026-10-07; the Tk and PySide6 views are frozen at `413f504` and
unregistered), over one Controller. Firmware is untouched;
every byte on the wire is identical to the old app's (the repair tree
the lab ran Aug 26–Sep 22, 2026; history: tag pre-root-cleanup-2026-10-07), pinned by `tests/golden/`.

The repository lives at `/Users/ianalbinogonzalez/GitHub/transfer-stage-unified/`:
one checkout, the clone itself (work here; it holds the one venv, `.venv`, the `$PY`
below), with `rb-<name>/` agent worktrees as siblings only while a round runs. On the
lead's Mac that folder is still named `mvc-refactor/` until it is re-cloned; a sibling
`main/` there is a plain checkout of the lab's original app (for `dev/swap_branch.sh`).
The lab PC clones fresh from `main` (`git clone --branch main <url>`); no sibling `main/` checkout exists after a fresh clone, and `dev/swap_branch.sh legacy` creates `../legacy-app` from the `legacy` branch when the old app is needed.

**Branch model** (owner, 2026-10-07): `main` is the station, the pre-release line; it receives
pull requests from short-lived branches (`feat/*`, `fix/*`, `agent/*`). The
`mvc-refactor` branch is deleted once it is merged into `main`; it is not kept as an
integration branch. Releases are tags on main cut with
`dev/release.sh vX.Y.Z` (CI builds, uploads `SHA256SUMS` and publishes the draft
itself). `legacy` is the lab's original Tk app, frozen; `stable` is the original
app's packaging ref. `.github/workflows/gate.yml` runs the fast gate, golden and a
Web launch on PRs and pushes to main (the mvc-refactor trigger is harmless and goes away with the branch). The version is the git tag
(`src/app.py --version`; pyproject says 0.0.0). The lab deploys by git today
(a fresh `git clone --branch main <url>`, `pip install -e .`, `run.sh`); the first
installed release comes after v1.0.0. Details: `packaging/README.md`.

The import rules between the `src/` layers (controller, model, devices, views) are a test (`tests/test_architecture.py`).

## Read these first, in this order

1. `docs/rebuild/STATUS.md` — cold resume: where things are, how to run and
   verify, the owner rulings, the open items.
2. `docs/rebuild/BRIEF.md` — the architecture contract and its addenda
   (paths in it are pre-move; its banner maps them).
3. `docs/rebuild/DESIGN_BRIEF.md` — the design ruling for the Web view
   ("Bench sheet, tiered", 2026-09-25, plus the procedure section of
   2026-10-07).
4. `docs/rebuild/BUGFIX_PLAN.md` — the ranked defect list, a route per item.
5. `docs/rebuild/MODEL_CONTRACT.md` — how to add a device: what a `Model`
   subclass must provide and what the views do for free;
   `tests/test_model_contract.py` asserts it against every registered class.
6. `docs/rebuild/RECORDING_A_TRIAL.md` — the Transfer Map (the project's
   end goal): the procedure, how a trial is recorded, where the store lives,
   the force definitions.

Inputs that open work still reads, kept with a banner: `root-causes.md`,
`safety-pattern.md` (in `docs/architecture/`), `docs/rebuild/bench-checklist.md`,
and `tests/TEST_PORTING.md` (the second test wave). The finding ledger
(`progress.md`, `carry.json`), the design data, the legacy audits and `docs/archive`
are gone from the tree (history: tag pre-root-cleanup-2026-10-07).

## Commands (from the checkout (today `mvc-refactor/`) or an `rb-*` worktree; `$PY` = `/Users/ianalbinogonzalez/GitHub/transfer-stage-unified/mvc-refactor/.venv/bin/python`, see `station-map`)

```
./run.sh --no-browser --port 8080       # the Web view, the only one; python3 src/app.py ... in the venv
# test gates: see the verify skill (the fast gate, golden (77), a launch; no legacy gate)
```

One entry point, `station-web` (`pyproject.toml` `[project.scripts]`), and one
PyInstaller launcher; the one venv is made with `python3 -m venv .venv &&
.venv/bin/pip install -e ".[dev]"`. `--tk` and `--qt` print a retired message and exit 2.
`pip install -e .[qt]` is needed only to run the frozen Qt view's tests, which
are optional and the lead's.

Launchers: `run.sh` (macOS and Linux) and `run.bat` (Windows) find the venv and
pass every flag to `src/app.py`, printing nothing on success; the firmware
check is Setup's Firmware row (`src/controller/firmware.py`), `update.sh` /
`update.bat` stay. `dev/swap_branch.sh` runs the original app (from the `legacy` branch, `../legacy-app`) on the same boards,
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
- Scratch files, patches and logs never land in the repo root.

## Two traps this codebase sets

1. **Comments quote the old broken code.** `src/` (and the old tree, history: tag pre-root-cleanup-2026-10-07) document
   their own repairs at length, so a grep hit for a defect is often prose
   about its removal. Read the matching line.
2. **Ledgers drift.** Fixes landed and rows were not flipped in the old
   finding ledger (removed from the tree). Check the code, never a ledger,
   before calling a finding open.

## History

The staged repair, the from-scratch rebuild, the 2026-09-23 relayout, the
retirement of `legacy/` and the 2026-10-07 round (Web the only frontend,
settled-frame recording, the full-display recorder, the procedure phases), then
the proposal round, the lab's stage merge, the root cleanup and the move to
`~/GitHub`, are in `docs/rebuild/STATUS.md` under "History", "Round 2026-10-07"
and "Round 2026-10-07 (proposal)".
