---
name: station-map
description: Orientation for any agent working on transfer-stage-unified — where the three code trees are, what each one is, how to run and test the rebuild, the owner rulings that decide what is intentional, and the traps this codebase sets. Read before auditing, pruning, or relocating anything.
---

# Station map

## Geography (absolute paths; the parent directory is not a git repo)

```
/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/
  main/             worktree, branch `main`      — the LAB's original app (ran Aug 26–Sep 22 was mvc-refactor, not main)
  mvc-refactor/     worktree, branch `mvc-refactor` — the working branch (this is where you work)
  rebuild-handoff/  NOT in any repo — agent reports and screenshots live here
  rebuild/          stray directory; the `rebuild` branch was retired 2026-09-23. Do not use.
```

Three generations of the app exist:

| Generation | Where | Layout | Status |
|---|---|---|---|
| Original | `main/src/` | flat Tk files: `mainGUI.py`, `stepper_frame.py`, `DC_frame.py`, `chuck_frame.py`, `controllerDrive.py`, `serialDrive.py`, `temp_control.py`, `rotator.py`, `camera_control.py`, `color_test_new.py`, `lib/{smc100,redpercent,toupcam}.py` | The behaviour the lab knows. Reference for "what the app is supposed to do". |
| MVC repair | `mvc-refactor/src/` | `controller/ model/ views/{tkinter,pyside,web} lib/` | 13 root causes repaired over 213 findings. Frozen. Reference for the golden wire tests only. |
| Rebuild | `mvc-refactor/station/` | `app.py controller.py setup.py model.py models/ devices/ views/{tk,qt,web/}` + `events param schema panel result palette` | **The app going forward.** 13.7k lines, 1498 fast tests, 78 golden wire scenarios byte-identical to `src/`. |

Firmware (`firmware/`) is untouched by the rebuild, but it is **not** the
same as `main`'s: the stepper, chuck and temperature sketches changed on
`mvc-refactor` (enable/disable became `'e'`/`'d'`, the jog packet became the
42-byte `<BBffffffffff`). The MVC repair tree (`mvc-refactor/src/`) and the
rebuild both speak the new protocol, and the lab ran that repair tree from
2026-08-26 to 2026-09-22, so the bench boards presumably carry the new
firmware — unverified. Boards still on `main`'s firmware answer the identity
query identically and would launch without any warning.

## The rebuild's hierarchy (one paragraph, from docs/rebuild/STATUS.md)

`app.main()` → one `Controller` + one `Setup` panel → a view. Setup scans
serial ports automatically and constructs Models into the Controller. A Model
owns its Devices (`SerialPort`, `Gamepad`, `SMC100`, `Screen`) and the ONE
estop latch (`Model.estop`; subclasses write `_halt_hardware` only). Views
hold the Controller and nothing else: `schema(name)`, `state(name)`,
`run(name, cmd, inputs, args)`. Commands return a value or raise
`Refused`/`NeedsConfirm`. One `EventLog`; only `error()` may pop up. Import
rules are a test (`tests/station/test_architecture.py`).

Read, in order: `docs/rebuild/STATUS.md`, `docs/rebuild/BRIEF.md`,
`docs/rebuild/BUGFIX_PLAN.md`. `docs/rebuild/design.rules` maps every old
`src/` method to kept/renamed/merged/purged. `tests/station/TEST_PORTING.md`
lists the VOID families (features removed on purpose).

## Run and test (from `mvc-refactor/`; the venv is `main/.venv`)

```
PY=/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/main/.venv/bin/python
$PY -m station.app --web --no-browser --port 8080        # then set ports to "SIM" via /api/setup
$PY -m pytest tests/station -q -p no:cacheprovider -m "not qt"   # 1498 pass, ~46 s
$PY -m pytest tests/station/test_wire_golden.py -q               # 78 scenarios byte-identical to src/
```

Never run the Qt pass (`-m qt`) as an agent: a native SIGABRT kills the
session and discards results. The lead runs it. Write test output to a file
and grep it; a pipeline's exit code is `tail`'s, not pytest's.

## Owner rulings — these decide what is INTENTIONAL, not a defect

- Scripts / G-code: **purged**. Hide/show tabs: **purged** (close = destruct
  the model, reopen = construct again). Web session token: purged
  (localhost bind + Origin check). Per-model client-liveness gates: replaced
  by ONE Web watchdog calling `estop_all`.
- Autonomous mode stays; repeated Step works inside it; distances lock while
  autonomous.
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
- Firmware untouched; every byte on the wire identical to `src/`.
- D-7 (DC board has no coil kill) is open, bench-only, owner-only.

Anything on this list that looks "missing" in `station/` is a ruling, not a
finding. Cite the ruling and move on.

## Traps

1. Comments in `src/` and `station/` quote the OLD broken code at length. A
   grep hit for a defect is prose about its own removal until you read the
   line.
2. `tests/conftest.py` (the OLD suite) mocks `matplotlib`, `PIL`, `mss`,
   `serial` and Qt as `MagicMock`. Old tests asserting on those are vacuous.
   `tests/station/` has its own conftest with real fakes.
3. `main/` has no docs directory; its README is the operator manual and the
   `tests/test_edge_main_*.py` files are the closest thing to a behavioural
   spec of the original app.
4. Scan takes ~18 s on this Mac because two junk ports get the full
   handshake. Not a bug.

## Hygiene

- Scratch files go in the session scratch directory or
  `rebuild-handoff/`, never a repo root.
- Never push. Never amend. Commit only if your brief says to.
- `docs/**` belongs to whoever the brief says; default is the lead.
