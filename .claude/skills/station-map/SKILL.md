---
name: station-map
description: Orientation for any agent working on transfer-stage-unified — where the code trees are, what each one is, how to run and test the app, the owner rulings that decide what is intentional, and the traps this codebase sets. Read before auditing, fixing, pruning, or relocating anything.
---

# Station map

## Geography (absolute paths; the parent directory is not a git repo)

```
/Users/ianalbinogonzalez/GitHub/transfer-stage-unified/
  mvc-refactor/     worktree, branch `mvc-refactor` - the integration checkout (work here);
                    it holds the ONE venv, mvc-refactor/.venv, the $PY below
  main/             a PLAIN checkout of the lab's original app (NOT a worktree), kept for
                    dev/swap_branch.sh; it has no venv of its own any more
  rb-<name>/        one worktree per agent while a fix round runs; removed after the merge
                    (no other worktrees exist between rounds)
```

Branches: `main` is the pre-release line (receives merges from `mvc-refactor`
and feature PRs `feat/*`, `fix/*`, `agent/*`; releases are tags on main cut with
`dev/release.sh vX.Y.Z`); `mvc-refactor` is the integration branch for agent
rounds; `legacy` is the lab's original Tk app, frozen; `stable` is the
original app's packaging ref. `.github/workflows/gate.yml` runs the fast gate,
golden and a Web launch on every PR and push to main and mvc-refactor. The
version is the git tag (`station --version`, `packaging/release.py version`);
`pyproject.toml` says 0.0.0. The lab deploys by git today (`git fetch && git
checkout main` or `mvc-refactor`, `pip install -e .`, `run.sh`); the first
installed release comes after v1.0.0. Branch model detail: `packaging/README.md`.

Agent reports live in `handoff/` INSIDE each worktree (git-ignored; each
worktree has its own). The handoff files that code and docs cite were removed from the tree (history: tag pre-root-cleanup-2026-10-07). `handoff/shots/` is never tracked.

Three generations of the app exist:

| Generation | Where | Layout | Status |
|---|---|---|---|
| Original | branch `legacy` (the checkout `main/`) | flat Tk files: `mainGUI.py`, `stepper_frame.py`, `DC_frame.py`, `chuck_frame.py`, `controllerDrive.py`, `serialDrive.py`, `temp_control.py`, `rotator.py`, `lib/{smc100,redpercent,toupcam}.py` | The behaviour the lab knows. Reference for "what the app is supposed to do". Frozen. |
| MVC repair | removed from the tree (history: tag pre-root-cleanup-2026-10-07) | `controller/ model/ views/{tkinter,pyside,web} lib/` | Ran in the lab 2026-08-26 -> 09-22. Frozen; its wire bytes live on as `tests/golden/*.json`. |
| Rebuild | `mvc-refactor/src/` (+ `tests/`) | `app.py`, `events panel param schema result palette`, `controller/{controller,setup,flashing,updater,user_config,firmware}.py`, `devices/{screen_recorder,camera,video,...}.py`, `views/{base,theme,web/}` (+ `tk.py`, `qt.py`, `qt_finalizer.py`: frozen, see below), and the models below | **The app.** Fast gate: see the `verify` skill for counts; 77 golden wire captures pinned by `tests/golden/`. |

Models in `src/model/` (the registered ones have a Setup row; names are the operator's):
Stepper Probe, DC Probe and Chuck Positioner (`probe.py`), Temperature Controller
(`heater.py`), Rotator (`rotator.py`), RGB Analysis (`rgb_analysis.py`, class
`RgbAnalysis`, formerly Red Percent; hosted on the Transfer Map's page, no Setup
row of its own), Transfer Map (`transfer_map.py`, store v8; with
`transfer_map_analysis.py`, `tip_shade.py`, `trial_telemetry.py`, `finalize.py`,
`shade_offline.py`), Sample Map (`sample_map.py`, `sample_store.py`, store v4; on by default, `STATION_SAMPLE_MAP=0` turns it off),
User (`user.py`, with `user_store.py`: scrypt-hashed accounts in `users.sqlite`;
Guest = the station defaults). Also `estimators.py` (the estimator bank),
`plot_data.py`, `profile.py` (Phase 1 profiles), `idle.py`, `gamepad_input.py`.
The Sample Map is a four-phase sheet (browse, new_sample, new_chip, new_flake);
the Transfer Map's phases are setup, new_tip, region, live, marked, finish.

Firmware (`firmware/`) is untouched by the rebuild, but it is **not** the
same as `legacy`'s: the stepper, chuck and temperature sketches changed on
`mvc-refactor` (enable/disable became `'e'`/`'d'`, the jog packet became the
42-byte `<BBffffffffff`). `src/` speaks the new protocol (as did the removed
repair tree, which the lab ran), so the bench boards presumably carry
the new firmware — unverified. Boards still on `legacy`'s firmware answer the
identity query identically and would launch without any warning.

## The hierarchy (one paragraph, from docs/rebuild/STATUS.md)

`app.main()` → one `Controller` + one `Setup` panel → a view. Setup scans
serial ports automatically and constructs Models into the Controller. A Model
owns its Devices (`SerialPort`, `Gamepad`, `SMC100`, `Screen`) and the ONE
estop latch (`Model.estop`; subclasses write `_halt_hardware` only). Views
hold the Controller and nothing else: `schema(name)`, `state(name)`,
`run(name, cmd, inputs, args)`. Commands return a value or raise
`Refused`/`NeedsConfirm`. One `EventLog`; only `error()` may pop up. Import
rules are a test (`tests/test_architecture.py`): `views/` never imports
`model`/`devices`; `model/`, `devices/`, `panel.py` never import
`views`/`controller`; nothing under `src/` imports the old tree (history: tag pre-root-cleanup-2026-10-07).

Read, in order: `docs/rebuild/STATUS.md`, `BRIEF.md` (paths pre-move; its
banner maps them), `BUGFIX_PLAN.md` (Tier A code defects, B bench, C
hygiene, D `legacy`-vs-rebuild regressions).
`tests/TEST_PORTING.md` lists the VOID families (features removed on purpose).

## Run and test (from `mvc-refactor/` or your `rb-*` worktree; the one venv is `mvc-refactor/.venv`)

```
PY=/Users/ianalbinogonzalez/GitHub/transfer-stage-unified/mvc-refactor/.venv/bin/python
$PY src/app.py --no-browser --port 8080          # the Web view, the only one; then set ports to "SIM" via /api/setup
$PY src/app.py --version                         # the git tag; 0.0.0+<sha> before the first release
$PY -m pytest tests -q -p no:cacheprovider -m "not qt"                  # the fast gate; counts in the verify skill (STATION_NO_WINDOWS=1 while anyone is at the display)
$PY -m pytest tests/test_wire_golden.py -q -p no:cacheprovider          # 77 passed; replays the stored golden JSON against src/
```

The venv is made from pyproject's `[dev]` extra (`python3 -m venv .venv &&
.venv/bin/pip install -e ".[dev]"`); the old `main/.venv` is gone.

Never run the Qt pass (`-m qt`) as an agent: a native SIGABRT kills the
session and discards results. It tests only the frozen Qt view, is optional,
and needs `pip install -e .[qt]`; the lead runs it if at all. Write test output to a file
and read the exit code unpiped; a pipeline's exit code is `tail`'s.

## Owner rulings — these decide what is INTENTIONAL, not a defect

- Scripts / G-code: **purged**. Hide/show tabs: **purged** (close = destruct
  the model, reopen = construct again). Web session token: purged
  (localhost bind + Origin check). Per-model client-liveness gates: replaced
  by ONE Web watchdog calling `estop_all`.
- Autonomous mode stays; repeated Step works inside it; distances lock while
  autonomous (and only then).
- Per-model estop toggles wire into the global stop. Per-model clear needs
  confirmation.
- RGB Analysis (formerly Red Percent, renamed 2026-10-07): one mode, a row when red % changes, sampling SETTLED frames at
  the source rate (2026-10-07 amends "fastest sampling": black, stale and
  unsettled grabs are rejected and counted); red threshold only; Stage X/Y annotations dropped; velocity only between
  distinct 10 Hz position samples.
- Probe distances/speeds/brake fields are ints to the operator (floats on
  the wire, unchanged).
- Setup: auto-scan at boot + Refresh; a Launch checkbox and one Port dropdown per row
  (SIM / port; an unticked row is off), no Mode column; one table, one row per model;
  minimises on launch, reopenable.
- Names: "RGB Analysis" (was "Red Percent"; the class is `RgbAnalysis`, the
  section "RGB analysis details"), "Rotator", "Temperature Controller".
- **Web is the ONLY frontend** (owner, 2026-10-07). `views/tk.py` and `qt.py` are
  frozen at `413f504`, unregistered, banner on line 1, kept for reference; `picking.py`
  is deleted; `--tk`/`--qt` print "retired" and exit 2. Supersedes "Web candidate
  primary, Tk/Qt backups", DEFAULT_VIEW qt (2026-09-28) and D-9 "Tk default" (2026-09-25).
  A missing Tk/Qt feature is a ruling, not a finding.
- **Record everything during a trial, trim in analysis** (2026-10-07): full-display
  video + `frames.csv`, `telemetry.csv` on one clock, a full-resolution stage still
  at Arm. The trial is a procedure: setup -> (new_tip) -> region -> live -> marked -> finish.
- **The force is read from the tip's shade** (owner, 2026-10-07: the lab's baseline
  comparison beats the red extrema); the red-trace extrema remain a secondary
  analysis `factor=`. The estimator bank compares candidates; the model is chosen on footage.
- **The Sample Map is a sample > chip > flake store with photos at every level**
  (flake-coordinate homing is dormant); the map figures are speed x force class, tilt collected never drawn.
- Transfer Map store: v8 (the lab's, adopted); Sample Map store v4. A store v9 is no longer planned.
- Speed dials are percent over per-device ceilings (stepper 3200, chuck 600 steps/s).
- Accounts: Guest = the station defaults; `STATION_PROFILES=0` hides Setup's Account section.
- Firmware untouched; every byte on the wire identical to the old app's, pinned by `tests/golden/`.
- D-7 (DC board has no coil kill) is open, bench-only, owner-only.

Anything on this list that looks "missing" in `src/` is a ruling, not a
finding. Cite the ruling and move on.

## Traps

1. Comments in `src/` quote the OLD broken code at length.
   A grep hit for a defect is prose about its own removal until you read
   the line.
2. The old suite's conftest (removed) mocked `matplotlib`, `PIL`, `mss`, `serial` and
   Qt as `MagicMock`; old tests asserting on those are vacuous. The new
   suite (`tests/`) runs real libraries; only `tkinter` is a stand-in.
3. `main/` (the plain checkout of the lab's original app) has no docs directory; its README is the operator manual.
   The old `tests/test_edge_main_*.py` files are gone with the legacy tree (history: tag pre-root-cleanup-2026-10-07).
4. SIM mode ignores baud rate, and the golden gate compares bytes only.
   Neither says anything about port speed, timing or lock discipline.
5. Scan takes ~18 s on this Mac because two junk ports get the full
   handshake. Not a bug.

## Hygiene

- Scratch files go in the session scratch directory or
  `handoff/`, never a repo root.
- Never push. Never amend. Commit only if your brief says to.
- `docs/**`, `CLAUDE.md`, `README.md`, `.claude/**`, `tests/golden/**`,
  `firmware/**` belong to the lead unless the brief says
  otherwise.
