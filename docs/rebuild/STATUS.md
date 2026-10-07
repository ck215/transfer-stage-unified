# Rebuild status — cold-resume document

Last updated 2026-09-28 (evening). Read this first; then `BRIEF.md` (the architecture
contract and its addenda), `WEB_DESIGN_BRIEF.md`, and `BUGFIX_PLAN.md` (the
ranked defect list with a delegation route per item).

## Where things are

| What | Where |
|---|---|
| The new app | `src/` on branch `mvc-refactor`, worktree `../mvc-refactor` (this checkout). Built as the `station/` package on the `rebuild` branch (the refactor redone from scratch), fast-forwarded here and retired 2026-09-23, then moved to `src/` the same day. Layout: `src/app.py` (run as a script), `events.py panel.py param.py schema.py result.py palette.py`, `controller/{controller,setup}.py`, `model/base.py` + `model/{probe,heater,rotator,red_monitor,plot_data}.py`, `devices/`, `views/`. |
| The old app | `legacy/src/` (was `src/`), its suite in `legacy/tests/` (was `tests/` minus `tests/station/`); code untouched, still the reference for the golden wire tests. The pre-rebuild repair history ends at `91b5dc9` on `mvc-refactor`. |
| Agent worktrees | Removed 2026-09-23: the 12 `rb-*` worktrees and branches were all merged and clean; `../rebuild` retired the same day. Worktrees now: `main`, `mvc-refactor`. |
| Agent handoffs | `handoff/*.md` (outside every repo). `serial, gamepad, probe, heater, rotator, redmonitor, setup, tk, qt, web, coretests, golden`, then `setup2, tk2, qt2, web2` (polish pass), `web3` (console redesign). Each has DONE / TESTS / MUST-SATISFY / UNVERIFIED / NOTES sections. |
| **Timeouts, audit round 8, the model contract (2026-09-26)** | Owner: warn before closing the tab with devices energized, validate the watchdog and the idle timeout in every view, warn before the timeout with a way to extend. Core (**Tier N/O**): `Probe.idle_remaining` + one "Idle Timeout Soon" warning inside the last 60 s + `extend_idle`; `Controller.is_energized` / `state()["energized"]` (a probe in a mode, a heating heater, a recording run); the Web watchdog keys on it; stop-class commands and `set_mode("disabled")` skip input validation (`Panel.UNGATED_COMMANDS`, schema `stop=True`) so a stop is never refused over bad text in a box; `/api/data` runs only schema-declared sources, JSON POSTs need an Origin, frame-blocking headers; `views.base.GATE_WORDS` / `gate_reason` as the one direction-aware table, served to the Web; Setup's `stop_system` / `launch` ask while anything is energized; the schema's `fault` gate; `Heater.heating_to`. Views (rb-o-{tk,qt,web}, `handoff/fix-o-*.md`): a countdown line per probe under the disc with **Extend**; energized ring and faulted "!" marks; "Disable failed. Treat as live." in tier 1; quit / close questions name what is energized; Web `beforeunload` while energized, `/api/quit` answers after the stop with the unconfirmed named, busy commands, refusal at its field, commit on release, tier 1 pinned; live regions (Web) and announcements (Qt); one Qt Tab order. Audits: round 8 with one skill per auditor (`handoff/audit-ui-round8-*.md`), the architecture audit (intact with debt), and the **model contract** audit (`handoff/audit-model-contract-2026-09-26.md`: a `PiezoStage` written from the new `docs/rebuild/MODEL_CONTRACT.md` drove all three views unedited once CON-1/3/4/5/9 were fixed; `tests/test_model_contract.py`, 160 cases). Headless validation: the watchdog warns at 4.9 s and stops every model at 15.2 s of browser silence; no memory leak. Gates **fast 2290 (+11 window tests deferred, 1 xfail = CON-13), golden 78, Qt 212**. Open: O17 (lead part), O19–O23, CON-6/7/11, CON-13 (owner), the display-dependent captures, owner calls below. |
| **Owner's Web pass + audit round 7 (2026-09-26)** | Tier K from the owner's look at the merged E views: the gamepad choice is tier 1; every tier-2 disclosure names its device and sits at the foot of the tier-1 body above its well; the sheet has an **Overview** page (rail's first item, every model compact, no wells, pressable heads) and a **device page** (one model alone). Tier L from three real-display auditors (`handoff/audit-ui-round7-{web,tk,qt}.md`): the S1 in every view was that one model's own stop read as "Every model is stopped." and turned the disc to Clear over five live models (`is_estopped` = any). Core: `Controller.stop_state` {latched, unconfirmed, every, since}, `Model.stop_confirmed`, `views.base.stop_words()` / `event_line()`, `events.forget` on clear; views: disc face from the words, per-model rail marks, reasons on disabled commands, 24/36 px targets, slider keys, tier 1 pinned on the device page, Quit asks in Qt, Tab reaches every Qt control. Also: `handoff/proposal-probe-zeroing.md` (K5: TMC2209 on a Mega; host-only set-zero now, optical home switches later); the `window` test marker + `STATION_NO_WINDOWS=1` (owner: strictly background while at the Mac); a memory-leak check (none: 49→23 MB flat under a 10 Hz client). Gates **fast 2056 (+11 window tests deferred), golden 78, Qt 195**. Open: Tier M; owner calls below. |
| **Bench sheet, tiered (2026-09-26)** | The owner chose canvas row E (C "Control sheet" + A's circular stop + three tiers of prominence) and said "execute on that vision". Lead core (`rb-e-core`): the six light tokens in `palette.py`, `theme.py` (Public Sans text, Archivo numerals, READING_SIZES, STOP, SWITCH, RADIUS, TIER_LABELS, QUIET_VALUES, muted OFF outlines), `schema.section(tier=, disclosure=)`, `entry(slider=)`, `readonly(unit=)`, the four model schemas re-tiered (Red Percent = Red, Change, Start/Stop run in tier 1; everything else behind Details), `docs/rebuild/DESIGN_BRIEF.md`. Three Opus view worktrees rendered it (`rb-e-{web,tk,qt}`, handoffs `handoff/fix-e-{web,tk,qt}.md`): left rail with A's disc, entries not cards, one disclosure per model with Diagnostics inside, a slider beside every speed entry, status by exception, tray = warnings and errors only. Lead-verified: gates **fast 1956, golden 78, Qt 155**; ritual captures `handoff/shots/final_e_{web,tk,qt}_*`. Open follow-ups (Tier J below): fonts not installed on this Mac (Helvetica renders in Tk/Qt), heater shows the word "Simulated" as its reading in SIM, per-model stop switch caption, `theme.SLIDER`, px-vs-pt base unit in Qt. |
| Owner session round (2026-09-25) | The owner's first list from running the Web console (BUGFIX_PLAN **Tier G**): G1 no stderr tracebacks on tab close (`handle_error` override + handler idle timeout), G2 a Quit control and `POST /api/quit` (closing the tab never quits; the watchdog remains the guard), G3 a **Launch checkbox per Setup row** (new `sch.checkbox` element, `enabled_by` gating in the one `is_enabled` rule, "Off" gone from the Port dropdown, the summary is a count), G4 the **Gamepad log behind a button** in its own non-modal window (`log_stream(detached=True)`, polled only while open), G5 **no platform-specific UI** (owner ruling: Ctrl+. is the one stop chord; ⌘ variants removed in all three views; Tk keeps the `tk::mac::Quit` hook because Aqua's forced Quit would otherwise exit past the shutdown path — owner may overrule), G6 the Web rail's unconfirmed-stop line clears with the latch. Four Opus worktrees on the lead's core commit, lead-verified. Gates: **fast 1759, golden 78, Qt 142**. Real-display audit round 4 (52 captures, `handoff/audit-ui-round4.md`) filed as **Tier H**. Owner decisions open: P3 (the mac Quit hook), P8 (D-9's platform-dependent default view conflicts with the ruling). |
| UI fix round (2026-09-24) | Tier F rows landed from four Opus worktrees (core, Tk, Qt, Web), lead-verified: no pop-up can cover or block the stop in any view (queued, non-modal acks); a failed stop request and a dead station are visible; a lost serial port turns its card signal in every view; the Qt rail never clips a number; the stop has a keyboard chord everywhere (Ctrl+. / ⌘.); latch pre-disables what it would refuse; the analysis figure is trace-coloured, sized and cached; hung scans can be cancelled; error text is operator sentences; one word for the stop. Gates: fast 1675, golden 78, qt 127. Handoffs `handoff/fix-{core,tk,qt,web}.md`; captures `handoff/shots/round3_*`. Tk still unseen on screen (owner at the Mac). |
| UI audit (2026-09-24) | Six read-only auditors, one design skill each, on the round-2 tree: 97 findings merged into **BUGFIX_PLAN Tier F** (26 rows, 9 S1). Stop-path S1s the lead reproduced: a pop-up covers the stop; a failed stop request is silent; a lost serial port looks live in every view; the Qt rail clips numbers. Reports `handoff/audit-ui-*.md`. |
| UI round 2 (2026-09-24) | All three views refined as siblings of the Web console: one red (the stop object reads `Stop` / `Clear`, pulses once on the edge), sentence-case labels without colons, one-header Setup tables, readable event logs, empty states that say what to do next, boolean readouts as Yes/No, `Start run` / `Stop run` / `Stop heater` / `Stop motion` as quiet commands. Handoffs `handoff/{tk3,qt3,web5}.md`; shots `round2_{tk,qt,web}_*`. Tk `after` shots are pending (the Mac was in use; capture commands are in tk3.md UNVERIFIED). |
| Screenshots | `handoff/shots/`: `tk_*`, `qt_*` (polish pass), `web4_*` (console: drawer, launched, stopped), `web3_*` (agent's own rounds). Capture scripts: `/tmp/shoot_tk.py`, `/tmp/shoot_qt.py`, `/tmp/shoot_web.cjs` (temp; recreate from the procedure below if gone). |
| Tests | `tests/` (was `tests/station/`, flattened); wire captures in `tests/golden/`. |
| Design data | `docs/rebuild/design.json` (every new class/member with origins), `design.rules` (one line per old method: kept/renamed/merged/purged/implied; its paths are pre-move: `station/` = `src/`, old `src/` = `legacy/src/`), `carry.json` (all 229 old ledger findings classified against the design), `narrative.json`. Interactive pages (temp, may be gone): `/private/tmp/claude-501/.../412595fe.../scratchpad/{control_system_uml,ideal_system_uml}.html`. |
| Logs at runtime | `~/transfer-stage-runs/logs/station-<timestamp>.log`, one per launch; every event with thread and traceback; `events.debug` is file-only. |
| Run output | `~/transfer-stage-runs/<run_id>/` (CSV + `<run_id>_station_meta.json`). |

## How to run and verify

```
cd ../mvc-refactor
./run.sh --web | --qt | --tk              # uses the already-active venv (main/.venv)
python3 src/app.py --web --no-browser --port 8080

STATION_NO_WINDOWS=1 python3 -m pytest tests -q -p no:cacheprovider -m "not qt"   # 2290 pass + 11 window tests skipped + 1 xfail (CON-13), ~240 s; drop the variable when the display is free
QT_QPA_PLATFORM=offscreen python3 -m pytest tests -q -p no:cacheprovider -m qt   # 212 pass
python3 -m pytest tests/test_wire_golden.py -q                         # 78 scenarios byte-identical to legacy/src/
cd legacy && python3 -m pytest tests -q -m "not slow and not order_dependent and not qt"   # the old suite
```

macOS: pip re-hides PySide6's Qt plugin dylibs (UF_HIDDEN) — `run.sh --qt` (was `run_macos.sh`)
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

- 2026-09-26: **no zeroing / homing function for now** (stall detection is
  StealthChop-only and unreliable at homing speeds; no switches; collision
  risk). The firmware's microstep count is truth: stepper 8, chuck 2.

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
- Manual Speed is live in manual mode (2026-09-26): adjusting it on the fly
  while jogging is a feature; the next 50 Hz jog frame carries the new value.
  Its entry is `disabled_when=("autonomous",)`; Autonomous Speed, step sizes,
  distances and the DC brake fields stay locked in autonomous and manual. A
  value written while the motors are live is parsed strictly (finite, ≥ 1); a
  refused one leaves the previous speed in effect. The stop latch still blocks
  every frame. Overrides DC-6 for this one field (`e2f60c7`).
- Probe speed ceiling (2026-09-26): Autonomous and Manual Speed top out at
  3200 steps/s on every probe, slider 1–3200 (`MAX_SPEED`). Arbitrary for
  now; per-device, adapted limits are BUGFIX_PLAN Q1 (architecture audit).
- Setup: auto-scan at boot + Refresh; a **Launch checkbox** and one Port
  dropdown (SIM / port) per row (2026-09-25, replaces the "Off" entry), no
  Mode; one table, one row per model; minimises on launch, reopenable.
  Integers display without decimals.
- No platform-specific UI (2026-09-25): one stop chord (Ctrl+.), no ⌘
  variants, no macOS-only commands or copy in any view.
- Names: "Red Percent", "Rotator", "Temperature Controller".
- 2026-10-04: **the Rotator turns the chip** (in-plane, about a centre that
  is neither corner A nor the stage origin); the Sample Map models it
  (`rb-rotator-frame`, 2026-10-05). The Transfer Map still calls the angle
  `tilt_deg`; renaming it is the owner's call.
- Web = candidate primary frontend ("instrument console"); Tk/Qt persist as
  backups. Making Web the default is `VIEW_MODE` in the launchers.

## Next programme: packaging (2026-09-25)

`docs/rebuild/PACKAGING_PLAN.md`: one PyInstaller bundle per platform with
three entry points (`station-web`, `station-qt`, `station-tk`), steps P1–P8
with routes. P1–P4 landed 2026-09-25: **D-9 amended, Tk is the default view
on every OS**; `pyproject.toml` with `station-tk/qt/web`; `packaging/station.spec`
(one folder, three launchers, 134 MB on this Mac); `packaging/smoke.sh` (42
checks). The smoke found and the lead fixed a safety hole: Tk 9 on macOS
swallowed SIGTERM past `close()` (A10). Next: P5 (CI matrix), then the lab's
Windows PC. A design-language proposal (four boards rendered in HTML, one
component strip each) was published for the owner the same day; the choice
lands as `palette.py` / `theme.py` tokens.

## Open items

### Tip-shade force, held features, the finalizer (2026-10-06)

- **The force is read from the tip's shade, not the red percent** (owner ruling). Median green of the right half of the recorded tip region; contact is the shade rising off its baseline, and the force is where the shade stands on its peak at the Mark (`model/tip_shade.py`, `docs/rebuild/RECORDING_A_TRIAL.md`). The sheet's **Force** field reads No contact, Contact, Low, Medium or High live; schema v8 stores `force_position`, `force_class`, `contact_lowered`, `shade_*`; the map's default definition is `shade_position`; the video index gains a `shade` column. Recording is unchanged (the whole tip region). The red-percent profile only logs when the rounded value changes by 0.1, so its force indices (`at_operator_mark` and the rest) are not to be trusted. `rebuild_force` recomputes the columns from footage; run on the bench database on 2026-10-06 for the four valid trials. The thresholds (contact line, 0.10, the thirds) are bench values, the owner's.
- **The map has two axes, speed and force** (owner, 2026-10-06; tilt is fixed at 7° and recorded but no longer plotted). The figure dropdown is Map (speed × force, coloured by width; `plot_data` kind `map`, `map_limits`), Heatmap (the Gaussian process over speed × force; replaces the tilt × speed slice) , Compare, Profile. The Force band dropdown is gone; `width_gradient` reads um per step/s and um per unit force. Trials with no tilt are plotted. Bench database, 2026-10-06: trials 7 and 8 (400 steps/s, off the 100/200/300 plan) unflagged as supplemental points and their force rebuilt from footage (Medium, High); trials 6, 2 and 35 stay invalid (6 troublesome, 2 and 35 have no force).
- **Sample Map and user profiles are off by default** (owner, 2026-10-06) until validated. The code stays whole in this tree and on branch `feature/sample-map-profiles`; `STATION_SAMPLE_MAP=1` and `STATION_PROFILES=1` turn them on (`controller/setup.py`). History was not rewritten: every commit is on `origin/mvc-refactor`, and some are another developer's.
- **Data finalizer** (Qt only, temporary): the Transfer Map's "Finalize data..." button opens a window to walk the samples with video and stills and enter AFM and optical estimates (`model/finalize.py`, `views/qt_finalizer.py`).
- **Bench database:** schema v8; trials 3, 4, 5 and 9 are the valid series (7 deg, 100/200/300 steps/s); every other trial is flagged invalid, notes untouched. Backups beside it in `data/` (`*_pre_reconcile`, `*_old` with its pictures, `*_pre_shade`).


### The Rotator turns the chip: the Sample Map follows it (2026-10-05)

Landed (merged 1710d18; not pushed: the repository is public, the owner
pushes). Gates on the branch tip 6368de5, whose tree the merge equals:
fast **3478** (3433 + 45) / golden 78 / Qt 255 / legacy 1038 / launch 200;
the fast gate re-run on the merged tree: 3478 passed, 12 skipped,
1 xfailed, 255 deselected. The verify skill's baseline still says 3433
(`.claude/` was left alone); today's fast count is 3478.

- `rb-rotator-frame` (owner ruling 2026-10-04: the SMC100 spins the chip
  in-plane about a centre c that is neither corner A nor the stage origin).
  A registration records the Rotator's angle phi0; at phi the frame is the
  registered one followed by the turn, p = c + R(s(phi - phi0))(p_reg - c)
  (`sample_frame.RotatedFrame`). c and the sense s come from one feature
  marked at several angles (`rotation_centre`: two marks with a known
  sense by the chord, three or more by least squares over both senses; a
  set that is not a turn, a tilt for one, is refused). The calibration is
  station-only (sample store version 2, `rotator_calibrations`, never
  exported) and expires with the stage's `position_epoch`. Uncalibrated, a
  turn makes the registration unusable and the next mark ends it; back at
  phi0 it holds. Corner A re-marked after a turn reports the **Rotator
  closure**. Angle unknown (no reading, not referenced, homing, closed):
  the gate word `rotator_unknown` greys the marks; a mark while the Rotator
  moves is refused. Guidance only: nothing moves the Rotator or the stage.
- Lead review: the base proof re-run (all 45 new or changed tests fail on
  the base); two tests added (an extent marked across a turn, which fails
  on the old `mark_extent`; a mark refused while the Rotator turns) and the
  calibration-expiry test tightened to assert "calibration expired".
  `src/views/base.py` (lead-only) carries the one `GATE_WORDS` line, the
  lead's own edit. Not hand-driven here: there is no Rotator simulator and
  a SIM probe sends no positions, so the bench run is the first drive.
- Bench procedure: `docs/rebuild/BENCH_ROTATOR.md` (back up, the bench
  facts, one calibration, spin not tilt, the map follows a turn, roll back).

Open, for the lead or the owner:
- A flake flagged while the chip is turned keeps its raw stage position
  without the angle it was taken at (corners are stored at phi0, flakes are
  not). Harmless today (nothing re-places a flake from its stage position),
  but the stage half of "both frames" is ambiguous once the Rotator turns:
  a flake `rotator_deg` column, or the stage position stored at phi0.
- The `A'turned` check corner is stored at the turned angle, while every
  other corner row is at phi0.
- The turn is computed in stage counts, which assumes equal X and Y um per
  count (true of every locating source today; a skewed stage shows up as
  the calibration's residual).
- The Transfer Map records the Rotator's angle as each trial's `tilt_deg`;
  under the ruling it is the chip's in-plane turn. Renaming is the owner's
  call.
- Owner facts still open as before (axes, um per count of the chuck and
  the DC probe, backlash, mounting, objectives, the manual rig, the gamepad
  mark button; Q7, Q10, Q20-Q22).

### Phase 1 continued: core changes, the Sample Map, profiles (2026-10-04, night)

Landed (merged aa5e68f, cb85d47, 4a7bfef; not pushed: the repository is
public, the owner pushes). Gates on the gated tip, byte-identical to the
merged tree: fast **3433** / golden 78 / Qt 255 / legacy 1038 / launch 200.
Hand-driven through the Web API on scratch stores (profile, sign-in,
typed-readings registration, flag, extent, rate, figure, Remember my
settings).

- `rb-core-sample` (lead, flake-coords section 10 items 2, 3, 5):
  `probe.position_epoch` (a new epoch each time the port comes up),
  `GATE_WORDS` `unregistered` / `no_source` (the proposal had
  `unregistered`'s sentence in the enabled slot; it is the disabled-direction
  word, as `armed`), `--sample-db`.
- `rb-sample-map`: `model/sample_map.py`, registered after the Transfer Map
  (its own page; Setup rows, smokes and the Web tests count eight models).
  Crosshair marks, the frame refit per mark, closure and rectangularity,
  registration ended by an epoch change or the axes closing, flakes with both
  frames, Red Percent's reading and picture, quality/defects, the bbox
  extent, thickness approx/AFM apart, guidance only (nothing moves), typed
  micrometer readings for a rig without probes, flake-coords/1 export/import.
  Fixed on the way: `sample_frame` said `stage_bbox` (the vocabulary is
  `stage_corners`); the A-B minimum is now 30 um on the chip (was raw
  units, so typed mm read as too close). A SIM probe sends no POS lines, so
  marking with it is refused as stale: correct, by design.
- `rb-profiles`: `model/profile.py` (merge with provenance, the Q4 lists,
  local files, sign-in by name), `Panel.apply_defaults`,
  `Panel.SECRET_INPUTS`, `Controller.models`, the Profile row first on
  Setup. Every session is `offline-unverified` until a lab server exists;
  no PIN or hash is stored, and no PIN box is shown yet (nothing could check
  it; it comes with the server and a masked entry type). Trials gain
  `operator_auth` (still the one v6 migration, unreleased) and flakes
  `owner_auth`.

Not done yet: the `launch`, `default_controller` and `controller_binds`
namespaces (gamepad identity and binds touch the jog path), the Transfer
Map's "Flake being cut" dropdown (flake-coords Phase 2), the viewfinder and
um-per-px calibration (Phase 2), the lab server (user-system Phase 2).
Owner facts still open: axes, um per count (chuck, DC), backlash, chip
mounting, objectives, the manual rig's pitch and knob sense, the gamepad
mark button; Q7, Q10, Q20-Q22. **Known flaky test (pre-existing):**
`test_transfer_map.py::test_mark_appears_in_the_index_and_the_label_from_the_mark_on`
fails about 1 run in 3 on an unchanged tree (a 1 ms timing bound); the
verify skill now says so and gives today's counts.

### Phase 1 of the flake-coordinates and user-system proposals (2026-10-04, evening)

Owner answers of 2026-10-04 are in `handoff/proposal-user-system.md` §10.1a
(git-ignored). Landed, gates on the merged tree: fast 3337 (3269 + 18
store-v6 + 44 new + 6 architecture parametrisations of the two new
modules) / golden 78 / Qt 255 / legacy 1038 / launch 200. Not pushed.

- `rb-store-v6` (8ab88fa): Transfer Map store **version 6**, the one
  migration of both proposals: `sample_id`, `flake_uid`, `operator_id`
  ("station" until profiles), `camera_profile_id`, the two AFM heights
  (`channel_height_nm`: substrate to channel top, positive up;
  `trench_depth_nm`: tip cut depth, positive down; Q17), the optical width
  with its method (`capture_px` default; Q19), and `meta(map_db_uuid)`.
  Attach optical width never makes a trial measured; `pick_width` (AFM,
  else optical) feeds every figure; the 3D map rings optical, the slice /
  compare / gradient are AFM-only unless **Width source** says otherwise
  (optical at 3x noise); titles name their sources. Proven on copies of
  the two inventory stores (v4 and v5, both empty) and on seeded v1/v2/v4/v5
  files; hand-driven through the Web API (found and fixed a clipped title).
- `rb-sample-frame` (7286a58): flake-coords Phase 0, `model/sample_frame.py`.
  The chuck's and DC probe's um per count raise `BenchFactMissing` until
  measured or typed.
- `rb-sample-store` (6482e6c): `model/sample_store.py`, the
  `data/sample_map.sqlite` store and the `flake-coords/1` export/import
  (Q11 additive fields, Q16 no red-percent thickness, Q18 quality/defects).

Next, not started: the `SampleMap` model and its sheet (needs the lead's
core changes: `Setup.register`, the two `GATE_WORDS` lines, the probe's
`position_epoch`), then user-system Phase 1 (`ProfileService`, local
profiles). Owner facts still open: axes, um per count (chuck, DC),
backlash, chip mounting, objectives, the manual rig's pitch, the gamepad
mark button; Q7, Q10, Q20-Q22. Known flake, not new:
`test_mark_appears_in_the_index_and_the_label_from_the_mark_on` failed
1 of 3 runs on the unchanged base (a 1 ms timing bound). The verify
skill's counts (2290 / 212) are stale: today's base is 3269 / 255.

### Resume here (handoff written 2026-09-26, late; Tier R added 2026-09-27)

- **2026-09-27, night: the first real trial, and its findings.** The owner
  recorded a trial at the station, which is a **Linux PC** (bare X11, no
  compositor): the Tk region picker showed an opaque white sheet (its
  `-alpha` is ignored there; Qt's `WA_TranslucentBackground` has the same
  requirement; the Web picker draws on a screenshot and works). Workflow
  findings: Red Percent and the Transfer Map read as two tools on two
  pages; no prompt before the before/after pictures and a failed grab was
  only a tray warning; nothing at launch said where the database goes;
  no per-tip trial count. Two worktrees in flight, briefs in `handoff/`:
  `rb-trial` (the Transfer Map page becomes the whole trial sheet: region
  picker and Next step on it, Arm starts the Red Percent run, picture
  prompts via `NeedsConfirm`, pictures shown, database created and
  announced in `open()`, "New session database", "Trials on this tip") and
  `rb-picker` (both pickers draw on a screenshot; transparency is only a
  fallback). Landed the same night: `update.sh` / `update.bat`, a
  fast-forward-only updater so the bench takes fixes without GitHub
  Desktop (`1474a80`). Open owner call: the pictures are the capture
  region, not the whole feed.
  **Landed (2026-09-28, small hours):** the three worktrees merged by the
  lead (`3b5fe48` picker, `4177d3c` trial sheet, `a2456bc` boot grace),
  plus the lead's `tests/conftest.py` fixture (every Transfer Map store
  under tmp_path, since `open()` now creates it) and the prompt copy
  ("Continue", the key the views show). Gates on the merged tree: **fast
  2715, golden 78, Qt 230**. Headless Web capture of one SIM trial on the
  sheet: `handoff/shots/trial_sheet_web_*`. Still owed: the one
  `window`-marked Tk picker test on a free display; the bench check on the
  Linux PC (picture under the pointer, HiDPI); the Tk and Qt renders of
  the sheet (two pickers, three images, the Session section) have not
  been seen. Handoffs: `handoff/fix-{trial-sheet,region-picker,boot-warning}.md`.
  Boot-warning note: the heater warned because the reader's first pass ran
  before the port worker had opened the handle; the rotator because
  `_poll_ok` started as None. Both now hold a boot grace (4.5 s heater,
  derived from the port's bootloader and handshake waits; 3 s rotator).
  If the bench warning was titled "Port Unverified", that is
  `devices/serial_port.py` and is not covered.
  **2026-09-28: whole-screen pictures per trial** (`rb-full`, merged
  `46076c8`): `before_full.png` / `after_full.png` beside the region
  pictures, `Screen.screenshot_png(max_width=None)`, Red Percent
  `grab_screen()`, store schema 2 with an in-place upgrade of a version-1
  file (trials kept, proven on a seeded file), a tier-2 "Full pictures"
  section, both paths in the export, and a second stop-latch check in Arm
  after the pictures (the full grab widens the window). Gates: **fast
  2733, golden 78, Qt 230**. Open owner call: the full picture is PNG
  (~0.7 MB here, more on the lab display, ~0.5 s to encode); JPEG or a
  lower compression is one line if it matters.
  **2026-09-28: one dashboard, and the hidden-tab stop.** Owner: "why is
  Red Percent still separate from the Transfer Map? They should be a
  single dashboard", and "the app going out of focus stops controller
  polling". Core `0d3645d`: `Model.HOST` (Red Percent -> "Transfer Map"),
  `Controller.state` publishes `host` while the host is launched, Setup
  ticks the hosted rows, `MODEL_CONTRACT.md` step 1. Three view worktrees
  (`rb-dash-{tk,qt,web}`, one contract `handoff/brief-dashboard-contract.md`)
  merged: the map's page runs tier 1, the Red Percent group (name one
  step down, its mode as a caption), Configure Transfer Map, Red Percent
  details; no Red Percent link or Overview entry while hosted; its stop
  marks fold into the map's (unconfirmed first, the views' existing
  order); close the map and it gets its page back. Focus bug root cause
  (Web): `visibilitychange` stopped the heartbeat, so a hidden tab looked
  like a gone browser and the watchdog FULL STOPPED at 15 s. Now the
  heartbeat runs in a Worker that beats while hidden and ends on
  `pagehide`/Quit; `/api/heartbeat` carries `hidden` for the log; the
  watchdog rules are unchanged. Owner note: D-8's "closed the laptop lid"
  case now counts as present until the machine sleeps. Captures
  `handoff/shots/dashboard_web_*`. Open: the map's tier 1 (~480 px) is
  pinned and Red Percent's group scrolls under it; pinning off for a host
  page is the likely follow-up. Tk/Qt renders unseen. Gates on the
  merged tree: **fast 2798, golden 78, Qt 243**.
  **2026-09-28, later: the owner's next four asks, landed.** (1) Setup:
  a hosted model has no row; the Transfer Map row launches Red Percent
  first (`25ac461`; `register` refuses a hosted class with resources).
  (2) `rb-mark` merged `effd876`: the Mark picture (region at once,
  whole screen on the model's one picture thread; store schema 3, the
  same in-place upgrade), tips as records (`tips` table created on
  demand at the first Arm or an import; status line under the count;
  Tip section with note, retire, return; Tips log; a third export;
  arming on a broken or retired tip asks in the same prompt), polling
  that starts itself (an entry commit or `set_region` starts Red
  Percent's run once region + tip are set; keyed on that pair so a
  Note commit never restarts a run the operator ended; Arm takes the
  run over; no `reset_baseline`, a new run baselines on its first
  frame), and the mss handle leak closed at the root (`Screen.keep_handle`
  for the run loop only; every other grab opens and closes its own; one
  Web trial used to leave 5 X connections). (3) `rb-update` merged
  `c86ef66`: `controller/updater.py` (fast-forward only, same rules as
  `update.sh`), Setup's Update row first (Station, Updates, Update now,
  Check again, Coming), a startup check on a thread
  (`STATION_NO_UPDATE_CHECK=1` in tests), Launch refused while an
  update lands and, the lead's rule, after one until the restart
  (`d66c462`). Web Setup capture checked by the lead: sizes fine; the
  Web capitalises the sha's first letter (nit). Open owner calls:
  restart polling after Finish (today the next Arm starts it); a tip
  note before its first trial (refused today).
  **2026-09-28, bench fixes at the owner's word:** a "Known tips" dropdown
  and a "New tip" key beside the Tip ID entry (a tip record on demand,
  before any trial; a note also creates one); **Qt is the default view on
  every platform** (owner ruling 2026-09-28, replacing D-9's Tk;
  `app.DEFAULT_VIEW`, the launchers' comments, `run_macos.sh` runs its
  PySide repair by default). Fast-suite gates run after the push.
  **2026-09-28, closing the day (bench feedback during data collection):**
  the tilt is asked per trial (tier-1 entry under the tip, Next step
  insists when no rotator reads, the Arm prompt names it, "Set tilt for
  trial"); the speed likewise typed per trial and, at Finish, the cut's
  speed **measured** from the Z trace (`transfer_map_analysis.cut_speed`,
  fastest sustained |dz/dt| after the Mark; column
  `speed_measured_steps_s`, store schema 4) because the probe's setting
  was one number per session; the 3D map's axes padded (`map3d_limits`)
  so one tilt no longer autoscales to a hair; `rb-ack` merged (acknowledged
  notices: `events.warn(ack=True)`, `events.ATTENTION` = Idle Timeout,
  Temperature Disconnected, Rotator Unreachable, Heater Off Not Sent; one
  Understood dialog per view built from the latch-release dialog; the
  paragraph is in DESIGN_BRIEF); `rb-picker2` merged (the pickers draw
  the desktop 1:1 in desktop coordinates via `views/picking.py`, never
  stretched: the "squished, offset" picker). **Owner's note to act on:**
  bench trials 1 and 2 need their tilts set retroactively to 6.5 and 7
  degrees (Trial number under AFM measurement, Tilt for this trial, Set
  tilt for trial). Open: the Web view does not log an acknowledgement at
  debug (a `server.py` route); Tk/Qt keep the alert band beside the new
  dialog (owner ruling wanted); the restart prompt
  (`handoff/brief-restart-prompt.md`: an action key on the acknowledged
  notice, "Update now" / "Restart now", `Setup.restart_station`) is
  briefed and not started; the picker still uses the 1600-px picture (a
  full-size one is a one-line change in `screen_image`); Tk/Qt renders of
  the sheet, the dialog and the picker remain unseen on a display.
  **2026-09-28, paused on the owner's word.** Two worktrees hold WIP
  commits, to be resumed on command with their briefs: `rb-restart`
  (`handoff/brief-restart-prompt.md`: an action key on acknowledged
  notices, "Update now" / "Restart now", `Setup.restart_station`, the Web
  reload, the Tk/Qt alert band removed, `/api/ack`, and R8: three model
  names drawn over each other low in the Qt rail, see
  `handoff/shots/qt_offscreen_transfer_map.png`) and `rb-launch`
  (`handoff/brief-launchers.md`: two launchers with OS detection, the
  swap/flash check moved onto the Setup page as a Firmware row, nothing
  printed to the terminal, docs). Each handoff starts with a "Paused
  here" block. To resume: a fresh Opus agent per worktree with the
  worktree-fixer profile, the brief, and "continue from the Paused here
  block". Landed by the lead meanwhile: the picker picture at full size
  (`836f241`), the Next step copy shortened for Qt (`a8a2614`), Qt
  offscreen captures (`handoff/shots/qt_offscreen_*.png`; the harness is
  in the session scratch, recipe: `QtDashboard(Controller(), Setup())`,
  `controller.add` + `board._add_panel`, `open_entry`, `grab().save`).
  Rulings the lead owes at resume: (1) `tests/test_setup_registry.py`
  (the titles line) joins `rb-launch`'s write set so the Firmware row can
  land (its patch: `handoff/fix-launchers-firmware-row.patch`); (2)
  `src/events.py` echoes every info/warning to the terminal (line ~167):
  keep it for `--no-browser` and tests, silence it otherwise, or make it
  debug-only, so nothing but the log speaks; (3) the old `run_swap.sh`
  mentions in `updater.py`, `update.sh`/`.bat` and `setup.py` point at
  `dev/swap_branch.sh` or at the Firmware row; (4) R8's fix is `hide()`
  before `deleteLater()` in `_sync_rail` and `_build_reopen`.
  **2026-09-28, resumed and landed** (merged `5db7ff7` rb-launch,
  `a8883c5` rb-restart): two launchers (`run.sh` with `uname` for the
  macOS PySide repair, `run.bat`; `run_macos.sh` / `run_swap_macos.sh`
  were shims, removed the same night at the owner's word rather than after
  2026-10-31; `run_swap.sh` is `dev/swap_branch.sh`, a developer tool);
  the firmware check is Setup's **Firmware** row
  (`controller/firmware.py`: status per board from the stamp file and the
  sketch hashes, pinned against the script; "Flash out-of-date boards"
  asks and runs the script as a subprocess; Launch refuses during a
  flash and asks once for an out-of-date board; later that night the
  owner asked for the old launcher's unattended flash back, behind one
  popup: the startup check raises "Firmware Out of Date" with **Flash
  now** as its action, `tests/test_setup.py`); nothing prints on a
  normal launch (`events.py` echoes only errors unless
  `STATION_ECHO_EVENTS=1`; the Web address is Setup's "Address" and the
  one deliberate line with `--no-browser`); acknowledged notices carry an
  **action** ("Update Ready" → Update now, "Restart Needed" → Restart
  now; `Setup.restart_station` closes every model and re-executes the
  station, the Web page waits on a `boot` id and reloads); the Tk/Qt
  alert band is gone (the dialog is transient / a Tool window over the
  main one); `POST /api/ack` logs each answer; the Qt rail's stray links
  are hidden before their deferred delete (R8). Captures:
  `handoff/shots/restart_web_*.png`, `qt_offscreen_transfer_map_r8.png`.
  Unverified: a real restart on each view and on Windows, `run.bat`
  beyond static checks, a real flash. Gates on the final tree: **fast
  3073, golden 78, Qt 255**.
  **2026-09-28, paused again on the owner's word: three worktrees hold
  WIP commits**, resumable with their briefs and the "Paused here" block
  at the top of each handoff. `rb-video` (`handoff/brief-trial-video.md`,
  V1–V8 landed and green, 3112 fast; the follow-up in progress is an
  autouse JPEG-path fixture so the map tests stop starting ffmpeg; the
  V7 doc text is applied at merge from the handoff, not before).
  `rb-web-polish` (`brief-web-polish.md`: W1 landed 853f79a; W2 needs
  the one narrowed assertion in `test_view_web_server.py`, ruled).
  `rb-bundle` (`brief-bundle-update.md`: B1–B6 landed, 3133 fast; the
  owner ruled NO token file: the bundle uses `gh auth token`, then `git
  credential fill`; the follow-ups are in WIP commit c012b7b:
  `macos-15-intel` for the retired `macos-13`, a Windows swap-on-restart
  in `app.restart_process` with an `UPDATE_PENDING` marker, the neutral
  confirm text; its fast gate is RED by one test,
  `test_view_web_server.py::test_o4_a_faulted_probe_is_marked_like_an_unconfirmed_stop`,
  unread; the Windows swap path is untested on Windows). Merge order at
  resume: video, web polish, bundle; then the Qt pass. Incident at the
  pause (13:56): a file-sync client wrote an older snapshot over all four
  checkouts, resetting every branch ref to 345763f and leaving `SFConflict`
  copies; the commits survived, the lead reset each worktree to its WIP
  commit and verified every conflict copy equalled committed content. The
  repository folder is inside a synced directory; keep it out of the sync,
  owner's call. Owner steps
  after the bundle lands: one GitHub sign-in per lab machine (`gh auth
  login` or one https pull) and the first annotated tag (`git tag -a
  v0.2.0 -m "First packaged release"`), which starts the builds.
  **Resumed 2026-09-28 on the owner's word:** rb-video merged (aa50e7d;
  a labelled MP4 per trial, schema 5) and rb-web-polish merged (1f448cb;
  no pinning on a host page, values shown as the model gives them), the
  V7 text applied to `RECORDING_A_TRIAL.md` (722ccc5). Gates on the
  merged tree: fast 3116 / golden 78 / Qt 255, pushed. rb-bundle resumed
  on this base: merge it in, fix its one red test, gates, then merge.
  **rb-bundle landed (9de18da):** a frozen bundle updates itself from
  GitHub Releases with the machine's own sign-in (`gh auth token`, then
  `git credential fill`; no token file, owner's ruling); the package
  workflow builds per `v*` tag (`ubuntu-22.04`, `macos-15-intel`,
  `macos-14`, `windows-2022`) and stamps `VERSION` and `release.json`;
  on Windows an update that cannot swap while the launcher runs waits in
  `<install>.next` with an `UPDATE_PENDING` marker and swaps on Restart
  through a detached script (`os._exit`, since the delayed restart runs on
  a daemon thread); the Setup line says "Press Restart". Its "red" test
  was a one-off Puppeteer flake ("Attempted to use detached Frame"), green
  on the merged tree. Gates on the final tree: fast 3185 / golden 78 /
  Qt 255, pushed; the three worktrees retired. Open, bench-only: the
  Windows swap path has only run against fakes; an operator who quits
  instead of pressing Restart starts the old bundle by hand and is offered
  the release again (a swap at startup in `app.main` would close it);
  the workflow's first real run, the real credential stores and a real
  PyInstaller build are unverified. Owner steps: `gh auth login` (or one
  https pull) once per lab machine; `git tag -a v0.2.0 -m "First packaged
  release" && git push origin v0.2.0` starts the builds; if the release
  step is refused, allow read and write workflow permissions under the
  repository's Actions settings.

  **2026-09-28, evening: the owner's run-of-the-day fixes.** (1) The
  desktop icons: "Transfer Stage Launcher" had gone from the Linux PC's
  desktop and "Transfer Stage Classic" still ran `run_swap.sh`;
  `dev/desktop_shortcuts.sh` rewrites both (Exec = `dev/launch_desktop.sh`
  [`classic`], since a .desktop Exec line may hold no shell; a failed
  launch shows run.sh's message in a zenity dialog and keeps it in
  `~/transfer-stage-runs/launcher.log`), run on the bench PC, both files
  pass `desktop-file-validate`; two tests in `test_launchers.py`. (2) Tip
  ID could not be emptied or retyped in Qt ("Refused: tip is already on
  record"): only a slider's release committed a Qt entry, so the refresh
  wrote the model's old value back the moment focus left the box, and a
  click on New tip could carry the old ID. Every Qt entry now commits on
  `editingFinished` (Return or focus-out) when its text differs from the
  last refresh (`_on_editing_finished`), O14's rule as Tk and the Web
  already had it; three tests in `test_view_qt_widgets.py`. (3) The
  tilt could not be set per trial: `_read_tilt` preferred a rotator's
  reading, and the bench Rotator read 0.0 on trials tilted by hand to 6.5
  and 7 deg (both rows say `tilt_source` Rotator, 0.0). A typed tilt wins
  now; blank the entry and the rotator is the source again; the Arm prompt
  names the source either way ("at 7 deg (typed)"). (4) The bench
  database with the wrong tilts was set aside at the owner's word:
  `data/transfer_map_20260927_bench.sqlite` and its pictures folder
  `data/transfer_map_20260927_bench/` (the stored picture paths still
  name the old folder; delete both when the export is no longer wanted);
  the next launch creates a fresh `data/transfer_map.sqlite`. Tk and the
  Web already committed an entry on Return / focus-out, so (2) is Qt-only.
  **Same evening, the owner's next ask: "the runs no longer have labels;
  each trial has a chip, flake and cut ID for better sorting later."**
  Three tier-1 text entries under the tip (`chip_id`, `flake_id`,
  `cut_id`; store schema 6, the same in-place upgrade; Arm's inputs; Next
  step asks for a blank one after the tilt and never refuses; the prompt
  names them or says "NO chip, flake or cut ID"); the trials export and
  `trials_log` carry them; the video band gains a second line (`trial 12
  tip T7  chip C1  flake F2  cut 3`; `LABEL_TEMPLATE` sizes the band for
  it); and the Red Percent run a trial records through is **named after
  the trial** at Arm (`TransferMap.run_label`:
  `trial012_tip-T7_chip-C1_flake-F2_cut-3`, folder-safe) through the new
  `RedMonitor.label_run(run_id, annotations)`, which renames the active
  run only while nothing of it is on disk and fills specimen / consumable
  / note; the operator's own run keeps its name. Import keeps the columns. Later the same evening a fourth, **Sample (date /
  ID)** (`sample_id`, schema 7), above the chip; the bench values were set by
  a one-off at the owner's word (the owner ran it). Tests: ten in `test_transfer_map.py`, one in
  `test_red_monitor.py`. Views unchanged (entries render from the schema).
  `TrialRecorder.label_layout` packs each label line on rows of its own,
  so MARK stays at the end of the measurement line and the identity line
  sits under it. **Owner's third note, "persistence of field typing is
  also a problem on the sliders":** the same Qt defect (only the slider's
  release committed; a value typed into its box snapped back once focus
  left), covered by the same `_on_editing_finished` and pinned by
  `test_e_a_value_typed_into_a_sliders_box_persists_when_focus_leaves`;
  Tk (Return / FocusOut / ButtonRelease on the scale) and the Web
  (`change`, released drag) already committed there. Qt pass on this
  Linux PC: the offscreen suite twice sat forever on a test (different
  ones: after an F near 83 %, then `test_l1_the_rail_marks_each_model_
  latched_or_unconfirmed`), each of which passes alone; the widgets file
  is run with `timeout` now and the hang is not reproduced on its own.

- **2026-09-27, late: pushed at the owner's word for a data-collection
  session at the station.** Tree `54bb213` + this note; gates fast 2638,
  Qt 227, golden 78, seven-row launch. What the bench should know:
  1. **The Tk view (the default) has never been seen on a real display in
     its Signature form.** Its tests pass, its ten `window` tests are
     unrun. If anything looks wrong, launch `--web` (fully captured and
     verified headless) and keep working; report what Tk did.
  2. **Fonts:** install Figtree and Rubik (SemiBold, Medium) on the station
     PC, or the desktop views fall back to the platform sans.
  3. **Transfer Map:** the seventh Setup row ("On", no port). The store is
     `data/transfer_map.sqlite` in the checkout (git-ignored), created on
     the first Arm; `--map-db PATH` or `STATION_MAP_DB` overrides it. Red
     Percent must be recording for a trial to collect a profile. Read
     `RECORDING_A_TRIAL.md` before the first trial. The detector has only
     seen synthetic profiles: keep the Mark key as the reference and export
     the CSV at the end of the session.
  4. **Open owner questions** (the organisation audit,
     `handoff/audit-organisation-2026-09-27.md`, and the eight questions the
     lead put to the owner) are unanswered; nothing from it was applied.

- **2026-09-27, evening: the Signature aesthetic is in all three views.**
  Owner ruling: "Signature looks great, I'd love to have that be our
  layout" (round 3 on Tactile; canvas
  https://claude.ai/artifact/CbDb1WtYKZRJddarJZaU8V, spec
  `handoff/tactile3-Signature.md`, ratified rules in `DESIGN_BRIEF.md`).
  Tokens `af7a325`; views by three worktrees (`rb-sig-tk` 43ccd52,
  `rb-sig-web` a53057f, `rb-sig-qt` 878baad), merged by the lead. Every
  colour, size, radius, shadow, font and glyph comes from `theme.py`; the
  views name none of their own (a Tk test enforces it). **Open after the
  merge:** Tk captures on the real display (no headless Tk on macOS; the
  10 `window`-marked tests are the risk: keys are taller by their lip);
  install Figtree and Rubik (SemiBold, Medium) on the station PC; the old
  rail marks (L1 square, O6 ring, O16 "!") still sit beside the new lamp
  slot in Tk and Qt, which the Signature renders do not show: an owner
  call; Close and rescan icons are outside the nine-glyph set; the Web
  tray-open motion is not built (it clipped clicks). The four
  "Needs the lead" lists are in `handoff/fix-signature-{tk,web,qt}.md`.
- **2026-09-27: the Transfer Map (Tier S) landed** (`rb-map`, merged by the
  lead, registered as the seventh row; operator page
  `RECORDING_A_TRIAL.md`). Unverified: the profile path on real footage,
  since a SIM screen never changes; Tk/Qt rendering of its schema. It was built
  from `handoff/brief-transfer-map.md`: the project's end-goal heatmap
  (tilt, speed, force from the red-percent lowering profile, channel
  width), a local git-ignored SQLite store under `data/`, footage,
  detector plus Mark key, several force definitions cross-compared, AFM
  attachment, a numpy Gaussian process for confidence. Model-only.
- **2026-09-27, Tier R (modularity) landed**: MOD-1..6 via three worktrees
  (`rb-mod-input`, `rb-mod-setup`, `rb-mod-views`), merged `3ebc269`,
  `e340a05`, `33f638e`, then the lead's relocation of the SMC100 query onto
  `Rotator.identify_port`. `MODEL_CONTRACT.md` rewritten for the mixins and
  the registry. The gates after the merge are in the commit that follows.
  Same day: the aesthetics round (four directions on the fixed Tiered
  layout) is on the Design canvas and in `handoff/aesthetic-proposal.md`,
  awaiting the owner's ruling; nothing under `src/` changed for it.

- **Later the same night (Linux bench PC)**: swap launchers validated at the
  bench by the owner. Desktop shortcuts "Transfer Stage Launcher" (`run_swap.sh
  new`) and "Transfer Stage Classic" (`run_swap.sh main`); `main` is a sibling
  worktree `../transfer-stage-unified-main`, never modified. Flash state per
  board in `~/transfer-stage-runs/flashed.json` (only a swap reflashes the
  stepper and chuck; the Teensy keeps this tree's sketch). Speed ceiling 3200;
  Manual Speed live in manual. **Tier P (heater PID)**: P1 (live CSV + 
  `tools/heater_plot.py`) is committed but NOT merged, on branch
  `worktree-agent-a620f8f275b8607b4` (`b602f68`, worktree under
  `.claude/worktrees/`); verify and merge. P2 stopped by the owner after
  trial 0 (no overnight heating): defaults overshoot 30 C to 34.75 C, rise
  ~40 s, heat keeps climbing ~40 s after the controller cuts; data and
  scripts in `~/transfer-stage-runs/heater/`. Resume in daytime only.

- **Tree**: `mvc-refactor` at the commit after `e58fd3b`, clean, in step
  with origin apart from the lead's last docs commits (push is the owner's
  call). Gates on this tree: fast 2293 (+11 `window` skipped, 1 xfail =
  CON-13), golden 78, Qt 212 offscreen. Only `main/` and `mvc-refactor/`
  exist on disk; no agent, server or worktree is running.
- **First bench contact happened** (owner, 2026-09-26): D1 (probe baud)
  hit and fixed at the bench (`b3c69cd`). Tier D then reconciled against
  the code: 11 rows still present (`handoff/audit-tier-d-2026-09-26.md`,
  banner under Tier D). **Next work, in order: D4 and D5** (a gamepad swap
  in Manual sends no stop; a failed jog write goes to FAULT with no stop
  and a closed handle), lead, core, test first; then D2, D6, D14, D11.
- **Owner rulings tonight**: no zeroing / homing function for now (K5
  shelved; the proposal stays in `handoff/` for later); the firmware's
  microstep count is truth (stepper 8, chuck 2, identical to `main` since
  2026-07-15; the README line is corrected).
- **Still deferred until the display is free**: the 11 `window` tests and
  the Tk/Qt captures of the Tier N/O states (list below).
- **Open code rows**: O17 (lead part), O19, O20–O23, CON-6/7/11. Owner
  calls: below.


Owner calls from 2026-09-26 (Tier K/L): 44 px targets everywhere vs the
artboards' compact commands (24/36 shipped); the slider track / entry well
at 1.11:1 against the sheet (WCAG 1.4.11) vs the brief; whether a partial
latch should ever be clearable from the disc (shipped: no, a single model
clears at its own switch); the overview keeps each device's whole tier-1
body (assumption stated in Tier K); Tk's `tk::mac::Quit` hook P3 (the OS Quit still
exits without the question); push.

Owner calls from 2026-09-26 (Tier N/O, contract): a hidden Web tab stops
energized devices 15 s after its last heartbeat, heater included (IMP8-6;
shipped: yes, nobody is watching); CON-13, the SIM Rotator's stop never
confirms, so every simulated FULL STOP reads "Rotator did not confirm".

Deferred until the display is free (owner ruling: strictly background while
anyone is at the Mac): the 11 `window` tests, and Tk/Qt captures of the
Tier N/O states (countdown with two probes, energized ring beside a stop
square, a faulted entry and its rail mark, the close and quit questions
naming an energized model, Step in manual mode reading "In manual mode").

Code defects found by the 2026-09-23 sweep are in `BUGFIX_PLAN.md` (Tier A);
the bench questions below are its Tier B. **Tier D** (same day) is the
`main`-vs-rebuild audit: 17 operator-facing regressions, the first five
safety-adjacent (probes open at the wrong baud rate; the rotator can report a
move done mid-motion; D-pad steps are dead; gamepad swap no longer stops the
stage). **First hardware contact 2026-09-26** (owner, bench): the probes
opened at the wrong baud (D1, the one Tier D row marked safety-first and
never routed) and were fixed at the bench (`b3c69cd`); the other Tier D
rows are being reconciled against the code (`handoff/audit-tier-d-2026-09-26.md`).

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

## Process notes

- Twelve Opus agents in exclusive-write-set worktrees, lead (Fable) verifies:
  write-set diff, rerun tests, hand-drive the feature, merge. Core changes
  only by the lead; agents file CORE CHANGE REQUESTS in handoffs.
- Every claimed pass was re-run by the lead. The golden gate and a real
  process launch each caught defects no unit test did (SDL init off the main
  thread trapping at exit; Web launcher returning before serving; Qt with no
  QApplication; FULL STOP bar pushed off-screen in Tk).
