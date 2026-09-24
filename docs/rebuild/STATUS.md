# Rebuild status — cold-resume document

Last updated 2026-09-23. Read this first; then `BRIEF.md` (the architecture
contract and its addenda), `WEB_DESIGN_BRIEF.md`, and `BUGFIX_PLAN.md` (the
ranked defect list with a delegation route per item).

## Where things are

| What | Where |
|---|---|
| The new app | `src/` on branch `mvc-refactor`, worktree `../mvc-refactor` (this checkout). Built as the `station/` package on the `rebuild` branch (the refactor redone from scratch), fast-forwarded here and retired 2026-09-23, then moved to `src/` the same day. Layout: `src/app.py` (run as a script), `events.py panel.py param.py schema.py result.py palette.py`, `controller/{controller,setup}.py`, `model/base.py` + `model/{probe,heater,rotator,red_monitor,plot_data}.py`, `devices/`, `views/`. |
| The old app | `legacy/src/` (was `src/`), its suite in `legacy/tests/` (was `tests/` minus `tests/station/`); code untouched, still the reference for the golden wire tests. The pre-rebuild repair history ends at `91b5dc9` on `mvc-refactor`. |
| Agent worktrees | Removed 2026-09-23: the 12 `rb-*` worktrees and branches were all merged and clean; `../rebuild` retired the same day. Worktrees now: `main`, `mvc-refactor`. |
| Agent handoffs | `../rebuild-handoff/*.md` (outside every repo). `serial, gamepad, probe, heater, rotator, redmonitor, setup, tk, qt, web, coretests, golden`, then `setup2, tk2, qt2, web2` (polish pass), `web3` (console redesign). Each has DONE / TESTS / MUST-SATISFY / UNVERIFIED / NOTES sections. |
| Screenshots | `../rebuild-handoff/shots/`: `tk_*`, `qt_*` (polish pass), `web4_*` (console: drawer, launched, stopped), `web3_*` (agent's own rounds). Capture scripts: `/tmp/shoot_tk.py`, `/tmp/shoot_qt.py`, `/tmp/shoot_web.cjs` (temp; recreate from the procedure below if gone). |
| Tests | `tests/` (was `tests/station/`, flattened); wire captures in `tests/golden/`. |
| Design data | `docs/rebuild/design.json` (every new class/member with origins), `design.rules` (one line per old method: kept/renamed/merged/purged/implied; its paths are pre-move: `station/` = `src/`, old `src/` = `legacy/src/`), `carry.json` (all 229 old ledger findings classified against the design), `narrative.json`. Interactive pages (temp, may be gone): `/private/tmp/claude-501/.../412595fe.../scratchpad/{control_system_uml,ideal_system_uml}.html`. |
| Logs at runtime | `~/transfer-stage-runs/logs/station-<timestamp>.log`, one per launch; every event with thread and traceback; `events.debug` is file-only. |
| Run output | `~/transfer-stage-runs/<run_id>/` (CSV + `<run_id>_station_meta.json`). |

## How to run and verify

```
cd ../mvc-refactor
./run_macos.sh --web | --qt | --tk        # uses the already-active venv (main/.venv)
python3 src/app.py --web --no-browser --port 8080

python3 -m pytest tests -q -p no:cacheprovider -m "not qt"             # 1498 pass, ~46 s
QT_QPA_PLATFORM=offscreen python3 -m pytest tests -q -p no:cacheprovider -m qt   # 85 pass
python3 -m pytest tests/test_wire_golden.py -q                         # 78 scenarios byte-identical to legacy/src/
cd legacy && python3 -m pytest tests -q -m "not slow and not order_dependent and not qt"   # the old suite
```

macOS: pip re-hides PySide6's Qt plugin dylibs (UF_HIDDEN) — `run_macos.sh --qt`
runs `chflags -R nohidden` first; do the same before any offscreen Qt run.
Screen Recording permission is needed for Tk screenshots (`screencapture`).

Screenshot ritual (the only check that catches an off-screen FULL STOP):
launch each view, wait for the startup scan (`/api/setup` → `state.is_scanning`
false; ~20 s on this Mac because of two junk ports), set
`set_stepper_probe_port`/`set_red_percent_port` to `"SIM"`, run `launch`,
capture. Web via puppeteer at
`/opt/homebrew/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer`.

## Architecture (one paragraph)

`app.main()` → one `Controller` + one `Setup` panel → a view. Setup scans
automatically, constructs Models into the Controller. A Model owns its Devices
(`SerialPort`, `Gamepad`, `SMC100`, `Screen`) and the ONE estop latch
(`Model.estop`; subclasses write `_halt_hardware` only). Views hold the
Controller and nothing else: `schema(name)`, `state(name)`, `run(name, cmd,
inputs, args)`; a view that cannot render every schema element type cannot be
constructed. Commands return a value or raise `Refused`/`NeedsConfirm`. One
`EventLog`; only `error()` may pop up. Import rules are a test
(`tests/test_architecture.py`). Firmware untouched; every byte on the
wire is pinned by `tests/golden/`.

## Owner rulings (all applied)

- Close a tab = destruct the model; reopen = construct again. No hide/show.
- Autonomous mode stays; repeated Step works inside it. Distances are locked
  while autonomous (as on main); leave the mode to edit them.
- Scripts/G-code purged. Web is localhost-only. Per-model estop toggles wired
  into the global stop. Per-model clear needs confirmation.
- Red Percent: fastest sampling → ONE mode, a row when red % changes; red
  threshold only (green/blue caps fixed at 100 internally); Stage X/Y
  annotations dropped; velocity only between distinct 10 Hz position samples.
- Probe distances/speeds/brake fields are ints to the operator (floats on
  the wire, unchanged).
- Setup: auto-scan at boot + Refresh; one Port dropdown per row (Off / SIM /
  port), no Mode; one table, one row per model; minimises on launch,
  reopenable. Integers display without decimals.
- Names: "Red Percent", "Rotator", "Temperature Controller".
- Web = candidate primary frontend ("instrument console"); Tk/Qt persist as
  backups. Making Web the default is `VIEW_MODE` in the launchers.

## Open items

Code defects found by the 2026-09-23 sweep are in `BUGFIX_PLAN.md` (Tier A);
the bench questions below are its Tier B.

1. **Bench**: region-picker display scaling (all views), the four gamepad
   layouts (`Gamepad.LAYOUTS`, marked UNVERIFIED), achieved Red Percent
   capture rate (in every sidecar), Web heartbeat 5 s/15 s, first real serial
   handshake, `SMC100.READ_TIMEOUT_SEC` now bounds a whole line.
2. **Scan time**: two junk macOS ports get the full handshake (~18 s). A
   name filter is one line in `Setup.scan_ports` if the station PC is slow.
3. **Second test wave**: `tests/TEST_PORTING.md` lists 135 old test
   files (PORT 22 / PORT-ADAPTED 103 / VOID 10) and 26 safety tests that must
   have ported equivalents before `legacy/` is deleted.
4. **Cutover** — partly done. Done 2026-09-23: the move (`station/` → `src/`,
   old `src/` and `tests/` → `legacy/`, `tests/station/` → `tests/`) and the
   docs prune (stale pages to `docs/archive/`). Not done: deleting `legacy/`,
   which waits until every one of TEST_PORTING's 135 files has a ported
   equivalent (item 3); then merge `mvc-refactor` → `main` and push.
5. `Rotator.home()` target-commit ordering is tested now (rb-rotator); the
   `COLUMN_SPLIT_CARDS = 6` Qt rule is a judgement, not a measurement.
6. Heater refusals new vs old: 300 °C ceiling, PID/ramp bounds, 31-char
   frame limit — confirm at the bench.

## Process notes

- Twelve Opus agents in exclusive-write-set worktrees, lead (Fable) verifies:
  write-set diff, rerun tests, hand-drive the feature, merge. Core changes
  only by the lead; agents file CORE CHANGE REQUESTS in handoffs.
- Every claimed pass was re-run by the lead. The golden gate and a real
  process launch each caught defects no unit test did (SDL init off the main
  thread trapping at exit; Web launcher returning before serving; Qt with no
  QApplication; FULL STOP bar pushed off-screen in Tk).
