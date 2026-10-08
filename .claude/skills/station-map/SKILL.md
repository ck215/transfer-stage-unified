---
name: station-map
description: Orientation for any agent working on transfer-stage-unified — where the code trees are, what each one is, how to run and test the app, the owner rulings that decide what is intentional, and the traps this codebase sets. Read before auditing, fixing, pruning, or relocating anything.
---

# Station map

## Geography (absolute paths; the parent directory is not a git repo)

```
/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/
  main/             a PLAIN checkout of origin/main (NOT a worktree) — the LAB's original app;
                    it also holds the venv, main/.venv, the $PY below
  mvc-refactor/     worktree, branch `mvc-refactor` — the working branch (work here)
  rb-<name>/        one worktree per agent while a fix round runs (rb-tmap, rb-docs in the
                    2026-10-07 round); removed after the merge
```

Agent reports live in `handoff/` INSIDE each worktree (git-ignored; each
worktree has its own). The handoff files that code and docs cite were removed from the tree (history: tag pre-root-cleanup-2026-10-07). `handoff/shots/` is never tracked.

Three generations of the app exist:

| Generation | Where | Layout | Status |
|---|---|---|---|
| Original | `main/src/` | flat Tk files: `mainGUI.py`, `stepper_frame.py`, `DC_frame.py`, `chuck_frame.py`, `controllerDrive.py`, `serialDrive.py`, `temp_control.py`, `rotator.py`, `lib/{smc100,redpercent,toupcam}.py` | The behaviour the lab knows. Reference for "what the app is supposed to do". |
| MVC repair | removed from the tree (history: tag pre-root-cleanup-2026-10-07) | `controller/ model/ views/{tkinter,pyside,web} lib/` | Ran in the lab 2026-08-26 → 09-22. Frozen; its wire bytes live on as `tests/golden/*.json`. (Its `views/{tkinter,pyside,web}` are not the rebuild's.) |
| Rebuild | `mvc-refactor/src/` (+ `tests/`) | `app.py`, `events panel param schema result palette`, `controller/{controller,setup,flashing,updater,user_config,firmware}.py`, `model/{base,probe,heater,rotator,red_monitor,plot_data,transfer_map,transfer_map_analysis,trial_telemetry,sample_map,sample_store,...}.py`, `devices/{screen_recorder,camera,video,...}.py`, `views/{base,theme,web/}` (+ `tk.py`, `qt.py`: frozen, see below) | **The app.** Fast gate: see the `verify` skill for counts; 78 golden wire scenarios byte-identical to the old app's, pinned by `tests/golden/`. First bench contact 2026-09-26: D1 (probe baud) hit and fixed there; the rest of Tier D is being reconciled against the code. |

Firmware (`firmware/`) is untouched by the rebuild, but it is **not** the
same as `main`'s: the stepper, chuck and temperature sketches changed on
`mvc-refactor` (enable/disable became `'e'`/`'d'`, the jog packet became the
42-byte `<BBffffffffff`). the old tree and `src/` both speak the new
protocol, and the lab ran the old tree, so the bench boards presumably carry
the new firmware — unverified. Boards still on `main`'s firmware answer the
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
hygiene, D `main`-vs-rebuild regressions).
`tests/TEST_PORTING.md` lists the VOID families (features removed on purpose).

## Run and test (from `mvc-refactor/` or your `rb-*` worktree; the venv is `main/.venv`)

```
PY=/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/main/.venv/bin/python
$PY src/app.py --no-browser --port 8080          # the Web view, the only one; then set ports to "SIM" via /api/setup
$PY -m pytest tests -q -p no:cacheprovider -m "not qt"                  # the fast gate; counts in the verify skill (STATION_NO_WINDOWS=1 while anyone is at the display)
$PY -m pytest tests/test_wire_golden.py -q -p no:cacheprovider          # 78 passed; replays the stored golden JSON against src/
```

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
- Red Percent: one mode, a row when red % changes, sampling SETTLED frames at
  the source rate (2026-10-07 amends "fastest sampling": black, stale and
  unsettled grabs are rejected and counted); red threshold only; Stage X/Y annotations dropped; velocity only between
  distinct 10 Hz position samples.
- Probe distances/speeds/brake fields are ints to the operator (floats on
  the wire, unchanged).
- Setup: auto-scan at boot + Refresh; a Launch checkbox and one Port dropdown per row
  (SIM / port; an unticked row is off), no Mode column; one table, one row per model;
  minimises on launch, reopenable.
- Names: "Red Percent", "Rotator", "Temperature Controller".
- **Web is the ONLY frontend** (owner, 2026-10-07). `views/tk.py` and `qt.py` are
  frozen at `413f504`, unregistered, banner on line 1, kept for reference; `picking.py`
  is deleted; `--tk`/`--qt` print "retired" and exit 2. Supersedes "Web candidate
  primary, Tk/Qt backups", DEFAULT_VIEW qt (2026-09-28) and D-9 "Tk default" (2026-09-25).
  A missing Tk/Qt feature is a ruling, not a finding.
- **Record everything during a trial, trim in analysis** (2026-10-07): full-display
  video + `frames.csv`, `telemetry.csv` on one clock, a full-resolution stage still
  at Arm. The trial is a procedure: setup -> region -> live -> marked -> finish.
- **The Sample Map is a microscope-image store** (flake-coordinate homing is
  dormant); the map figures are speed x force class, tilt collected never drawn.
- Transfer Map store: repo is v6; the lab's are v7/v8; v9 is not built.
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
3. `main/` has no docs directory; its README is the operator manual and the
   `tests/test_edge_main_*.py` files are the closest thing to a behavioural
   spec of the original app.
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
