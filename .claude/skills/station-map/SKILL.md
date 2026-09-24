---
name: station-map
description: Orientation for any agent working on transfer-stage-unified — where the three code trees are, what each one is, how to run and test the app, the owner rulings that decide what is intentional, and the traps this codebase sets. Read before auditing, fixing, pruning, or relocating anything.
---

# Station map

## Geography (absolute paths; the parent directory is not a git repo)

```
/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/
  main/             worktree, branch `main`         — the LAB's original app
  mvc-refactor/     worktree, branch `mvc-refactor` — the working branch (work here)
  handoff/  NOT in any repo — agent reports (audit-*.md, relayout.md, docs-prune.md), screenshots
  rebuild/          stray directory, no .git; the `rebuild` branch was retired 2026-09-23. Do not use.
```

Three generations of the app exist:

| Generation | Where | Layout | Status |
|---|---|---|---|
| Original | `main/src/` | flat Tk files: `mainGUI.py`, `stepper_frame.py`, `DC_frame.py`, `chuck_frame.py`, `controllerDrive.py`, `serialDrive.py`, `temp_control.py`, `rotator.py`, `lib/{smc100,redpercent,toupcam}.py` | The behaviour the lab knows. Reference for "what the app is supposed to do". |
| MVC repair | `mvc-refactor/legacy/src/` (+ `legacy/tests/`) | `controller/ model/ views/{tkinter,pyside,web} lib/` | Ran in the lab 2026-08-26 → 09-22. Frozen. Reference for the golden wire captures only; deleted once `tests/TEST_PORTING.md` is worked off. |
| Rebuild | `mvc-refactor/src/` (+ `tests/`) | `app.py`, `events panel param schema result palette`, `controller/{controller,setup}.py`, `model/{base,probe,heater,rotator,red_monitor,plot_data}.py`, `devices/`, `views/{tk,qt,web/}` | **The app.** 13.7k lines; 1675 fast tests, 127 Qt, 78 golden wire scenarios byte-identical to `legacy/src`. Has never touched real hardware (see BUGFIX_PLAN Tier D). |

Firmware (`firmware/`) is untouched by the rebuild, but it is **not** the
same as `main`'s: the stepper, chuck and temperature sketches changed on
`mvc-refactor` (enable/disable became `'e'`/`'d'`, the jog packet became the
42-byte `<BBffffffffff`). `legacy/src` and `src/` both speak the new
protocol, and the lab ran `legacy/src`, so the bench boards presumably carry
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
`views`/`controller`; nothing under `src/` imports `legacy`.

Read, in order: `docs/rebuild/STATUS.md`, `BRIEF.md` (paths pre-move; its
banner maps them), `BUGFIX_PLAN.md` (Tier A code defects, B bench, C
hygiene, D `main`-vs-rebuild regressions). `docs/rebuild/design.rules` maps
every old `legacy/src` method to kept/renamed/merged/purged.
`tests/TEST_PORTING.md` lists the VOID families (features removed on purpose).

## Run and test (from `mvc-refactor/`; the venv is `main/.venv`)

```
PY=/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/main/.venv/bin/python
$PY src/app.py --web --no-browser --port 8080          # then set ports to "SIM" via /api/setup
$PY -m pytest tests -q -p no:cacheprovider -m "not qt"                  # 1675 passed, ~80 s (12 headless-Chrome tests included)
$PY -m pytest tests/test_wire_golden.py -q -p no:cacheprovider          # 78 passed; recaptures legacy/src in a subprocess
cd legacy && $PY -m pytest tests -q -p no:cacheprovider -m "not slow and not order_dependent and not qt"   # old suite: 1038 passed
```

Never run the Qt pass (`-m qt`) as an agent: a native SIGABRT kills the
session and discards results. The lead runs it. Write test output to a file
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
- Red Percent: fastest sampling → one mode, a row when red % changes; red
  threshold only; Stage X/Y annotations dropped; velocity only between
  distinct 10 Hz position samples.
- Probe distances/speeds/brake fields are ints to the operator (floats on
  the wire, unchanged).
- Setup: auto-scan at boot + Refresh; one Port dropdown per row
  (Off / SIM / port), no Mode column; one table, one row per model;
  minimises on launch, reopenable.
- Names: "Red Percent", "Rotator", "Temperature Controller".
- Web is the candidate primary frontend; Tk/Qt persist as backups.
- Firmware untouched; every byte on the wire identical to `legacy/src`.
- D-7 (DC board has no coil kill) is open, bench-only, owner-only.

Anything on this list that looks "missing" in `src/` is a ruling, not a
finding. Cite the ruling and move on.

## Traps

1. Comments in `src/` and `legacy/src/` quote the OLD broken code at length.
   A grep hit for a defect is prose about its own removal until you read
   the line.
2. `legacy/tests/conftest.py` mocks `matplotlib`, `PIL`, `mss`, `serial` and
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
  `legacy/**`, `firmware/**` belong to the lead unless the brief says
  otherwise.
