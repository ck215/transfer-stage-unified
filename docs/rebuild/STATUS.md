# Rebuild status — cold-resume document

Last updated 2026-10-08, after the proposal round, the lab merge, the root
cleanup and the move to `~/GitHub` ("Round 2026-10-07" and "Round 2026-10-07
(proposal)" below), and the night's feature merges ("Round 2026-10-08 (night)"). Read this first; then `BRIEF.md` (the architecture contract and its addenda),
`DESIGN_BRIEF.md` (its predecessor `WEB_DESIGN_BRIEF.md` was removed (history: tag pre-root-cleanup-2026-10-07)), and
`BUGFIX_PLAN.md` (the ranked defect list with a delegation route per item).
Paths are `/Users/ianalbinogonzalez/GitHub/transfer-stage-unified/...` (the
repository moved there from `~/Documents/GitHub`); `$PY` is
`main/.venv/bin/python`, the one venv (created from pyproject's `[dev]`
extra; the old sibling checkout's `.venv` is gone). Older sections say "Red Percent" for what
is now RGB Analysis (renamed 2026-10-07) and describe the old sibling `main/` as the lab's
original app, which is now branch `legacy`. The sections from "Where things are" down to "Resume here" were written while
Tk and Qt were live views; where they say so, read it as history (the Web is
the only frontend since 2026-10-07).

## Where things are

| What | Where |
|---|---|
| The new app | `src/` on branch `main`, the one checkout, the folder `main/` (branch model: "Round 2026-10-07 (proposal)"). The Web view (`src/views/web/`, over `views/base.py`) is the only frontend; `src/views/tk.py` and `qt.py` are frozen at `413f504`, unregistered, with a banner on line 1, kept intact for reference. Also since the layout below: `devices/{screen_recorder,camera}.py`, `model/{trial_telemetry,transfer_map_analysis,sample_store}.py`, `controller/{flashing,updater,user_config}.py`. Built as the `station/` package on the `rebuild` branch (the refactor redone from scratch), fast-forwarded here and retired 2026-09-23, then moved to `src/` the same day. Layout: `src/app.py` (run as a script), `events.py panel.py param.py schema.py result.py palette.py`, `controller/{controller,setup}.py`, `model/base.py` + `model/{probe,heater,rotator,red_monitor,plot_data}.py`, `devices/`, `views/`. |
| The old app | Removed from the tree 2026-10-07 (history: tag pre-root-cleanup-2026-10-07); its wire bytes are pinned in `tests/golden/*.json`. The pre-rebuild repair history ends at `91b5dc9` on `mvc-refactor`. |
| Worktrees | `main/` (the one checkout, branch `main`; it holds the one venv, `main/.venv`, the `$PY` of the skills) and, while a fix round runs, one `rb-<name>` per agent beside it; no others between rounds. `dev/swap_branch.sh legacy` makes `../legacy-app` from the `legacy` branch when the old app is needed. The eleven stale merged `rb-*` worktrees were removed on 2026-10-07 (rb-clean). |
| Agent handoffs | `handoff/*.md` inside each worktree, git-ignored (`handoff/` stays ignored). The handoff files that code and docs cite were removed from the tree (history: tag pre-root-cleanup-2026-10-07); a worktree's own handoff for a round is `handoff/fix-<name>.md`. Each has DONE / TESTS / MUST-SATISFY / UNVERIFIED / NOTES sections. |
| **Timeouts, audit round 8, the model contract (2026-09-26)** | Owner: warn before closing the tab with devices energized, validate the watchdog and the idle timeout in every view, warn before the timeout with a way to extend. Core (**Tier N/O**): `Probe.idle_remaining` + one "Idle Timeout Soon" warning inside the last 60 s + `extend_idle`; `Controller.is_energized` / `state()["energized"]` (a probe in a mode, a heating heater, a recording run); the Web watchdog keys on it; stop-class commands and `set_mode("disabled")` skip input validation (`Panel.UNGATED_COMMANDS`, schema `stop=True`) so a stop is never refused over bad text in a box; `/api/data` runs only schema-declared sources, JSON POSTs need an Origin, frame-blocking headers; `views.base.GATE_WORDS` / `gate_reason` as the one direction-aware table, served to the Web; Setup's `stop_system` / `launch` ask while anything is energized; the schema's `fault` gate; `Heater.heating_to`. Views (rb-o-{tk,qt,web}, `handoff/fix-o-*.md`): a countdown line per probe under the disc with **Extend**; energized ring and faulted "!" marks; "Disable failed. Treat as live." in tier 1; quit / close questions name what is energized; Web `beforeunload` while energized, `/api/quit` answers after the stop with the unconfirmed named, busy commands, refusal at its field, commit on release, tier 1 pinned; live regions (Web) and announcements (Qt); one Qt Tab order. Audits: round 8 with one skill per auditor (`handoff/audit-ui-round8-*.md`), the architecture audit (intact with debt), and the **model contract** audit (`handoff/audit-model-contract-2026-09-26.md`: a `PiezoStage` written from the new `docs/rebuild/MODEL_CONTRACT.md` drove all three views unedited once CON-1/3/4/5/9 were fixed; `tests/test_model_contract.py`, 160 cases). Headless validation: the watchdog warns at 4.9 s and stops every model at 15.2 s of browser silence; no memory leak. Gates **fast 2290 (+11 window tests deferred, 1 xfail = CON-13), golden 78, Qt 212**. Open: O17 (lead part), O19–O23, CON-6/7/11, CON-13 (owner), the display-dependent captures, owner calls below. |
| **Owner's Web pass + audit round 7 (2026-09-26)** | Tier K from the owner's look at the merged E views: the gamepad choice is tier 1; every tier-2 disclosure names its device and sits at the foot of the tier-1 body above its well; the sheet has an **Overview** page (rail's first item, every model compact, no wells, pressable heads) and a **device page** (one model alone). Tier L from three real-display auditors (`handoff/audit-ui-round7-{web,tk,qt}.md`): the S1 in every view was that one model's own stop read as "Every model is stopped." and turned the disc to Clear over five live models (`is_estopped` = any). Core: `Controller.stop_state` {latched, unconfirmed, every, since}, `Model.stop_confirmed`, `views.base.stop_words()` / `event_line()`, `events.forget` on clear; views: disc face from the words, per-model rail marks, reasons on disabled commands, 24/36 px targets, slider keys, tier 1 pinned on the device page, Quit asks in Qt, Tab reaches every Qt control. Also: `handoff/proposal-probe-zeroing.md` (K5: TMC2209 on a Mega; host-only set-zero now, optical home switches later); the `window` test marker + `STATION_NO_WINDOWS=1` (owner: strictly background while at the Mac); a memory-leak check (none: 49→23 MB flat under a 10 Hz client). Gates **fast 2056 (+11 window tests deferred), golden 78, Qt 195**. Open: Tier M; owner calls below. |
| **Bench sheet, tiered (2026-09-26)** | The owner chose canvas row E (C "Control sheet" + A's circular stop + three tiers of prominence) and said "execute on that vision". Lead core (`rb-e-core`): the six light tokens in `palette.py`, `theme.py` (Public Sans text, Archivo numerals, READING_SIZES, STOP, SWITCH, RADIUS, TIER_LABELS, QUIET_VALUES, muted OFF outlines), `schema.section(tier=, disclosure=)`, `entry(slider=)`, `readonly(unit=)`, the four model schemas re-tiered (Red Percent = Red, Change, Start/Stop run in tier 1; everything else behind Details), `docs/rebuild/DESIGN_BRIEF.md`. Three Opus view worktrees rendered it (`rb-e-{web,tk,qt}`, handoffs `handoff/fix-e-{web,tk,qt}.md`): left rail with A's disc, entries not cards, one disclosure per model with Diagnostics inside, a slider beside every speed entry, status by exception, tray = warnings and errors only. Lead-verified: gates **fast 1956, golden 78, Qt 155**; ritual captures `handoff/shots/final_e_{web,tk,qt}_*`. Open follow-ups (Tier J below): fonts not installed on this Mac (Helvetica renders in Tk/Qt), heater shows the word "Simulated" as its reading in SIM, per-model stop switch caption, `theme.SLIDER`, px-vs-pt base unit in Qt. |
| Owner session round (2026-09-25) | The owner's first list from running the Web console (BUGFIX_PLAN **Tier G**): G1 no stderr tracebacks on tab close (`handle_error` override + handler idle timeout), G2 a Quit control and `POST /api/quit` (closing the tab never quits; the watchdog remains the guard), G3 a **Launch checkbox per Setup row** (new `sch.checkbox` element, `enabled_by` gating in the one `is_enabled` rule, "Off" gone from the Port dropdown, the summary is a count), G4 the **Gamepad log behind a button** in its own non-modal window (`log_stream(detached=True)`, polled only while open), G5 **no platform-specific UI** (owner ruling: Ctrl+. is the one stop chord; ⌘ variants removed in all three views; Tk keeps the `tk::mac::Quit` hook because Aqua's forced Quit would otherwise exit past the shutdown path — owner may overrule), G6 the Web rail's unconfirmed-stop line clears with the latch. Four Opus worktrees on the lead's core commit, lead-verified. Gates: **fast 1759, golden 78, Qt 142**. Real-display audit round 4 (52 captures, `handoff/audit-ui-round4.md`) filed as **Tier H**. Owner decisions open: P3 (the mac Quit hook), P8 (D-9's platform-dependent default view conflicts with the ruling). |
| UI fix round (2026-09-24) | Tier F rows landed from four Opus worktrees (core, Tk, Qt, Web), lead-verified: no pop-up can cover or block the stop in any view (queued, non-modal acks); a failed stop request and a dead station are visible; a lost serial port turns its card signal in every view; the Qt rail never clips a number; the stop has a keyboard chord everywhere (Ctrl+. / ⌘.); latch pre-disables what it would refuse; the analysis figure is trace-coloured, sized and cached; hung scans can be cancelled; error text is operator sentences; one word for the stop. Gates: fast 1675, golden 78, qt 127. Handoffs `handoff/fix-{core,tk,qt,web}.md`; captures `handoff/shots/round3_*`. Tk still unseen on screen (owner at the Mac). |
| UI audit (2026-09-24) | Six read-only auditors, one design skill each, on the round-2 tree: 97 findings merged into **BUGFIX_PLAN Tier F** (26 rows, 9 S1). Stop-path S1s the lead reproduced: a pop-up covers the stop; a failed stop request is silent; a lost serial port looks live in every view; the Qt rail clips numbers. Reports `handoff/audit-ui-*.md`. |
| UI round 2 (2026-09-24) | All three views refined as siblings of the Web console: one red (the stop object reads `Stop` / `Clear`, pulses once on the edge), sentence-case labels without colons, one-header Setup tables, readable event logs, empty states that say what to do next, boolean readouts as Yes/No, `Start run` / `Stop run` / `Stop heater` / `Stop motion` as quiet commands. Handoffs `handoff/{tk3,qt3,web5}.md`; shots `round2_{tk,qt,web}_*`. Tk `after` shots are pending (the Mac was in use; capture commands are in tk3.md UNVERIFIED). |
| Screenshots | `handoff/shots/` (ignored, never tracked): `tk_*`, `qt_*` (polish pass), `web4_*` (console: drawer, launched, stopped), `web3_*` (agent's own rounds). The capture scripts were temporary and are gone; recreate from the procedure below. |
| Tests | `tests/` (was `tests/station/`, flattened); wire captures in `tests/golden/`. |
| Design data | `design.json`, `design.rules`, `carry.json`, `narrative.json` (and the ledger `progress.md`) were removed (history: tag pre-root-cleanup-2026-10-07). |
| Logs at runtime | `~/transfer-stage-runs/logs/station-<timestamp>.log`, one per launch; every event with thread and traceback; `events.debug` is file-only. Beside it, `device_log.sqlite` (2026-10-08): every published event plus a once-a-second snapshot of every open model, local only, kept 14 days / 200 MB; see `docs/rebuild/DEVICE_LOG.md`. |
| Run output | `~/transfer-stage-runs/<run_id>/` (CSV + `<run_id>_station_meta.json`). |

## How to run and verify

```
cd /Users/ianalbinogonzalez/GitHub/transfer-stage-unified/main
./run.sh --no-browser --port 8080         # the Web view (the only one; --tk/--qt print "retired" and exit 2)
$PY src/app.py --no-browser --port 8080   # the same, with the one venv's python ($PY = main/.venv/bin/python)
$PY src/app.py --version                  # the git tag; 0.0.0+<sha> before the first release

STATION_NO_WINDOWS=1 $PY -m pytest tests -q -p no:cacheprovider -m "not qt"   # the fast gate; counts are in the verify skill
$PY -m pytest tests/test_wire_golden.py -q                         # 77 captures byte-identical to the stored golden captures
# optional: the frozen Qt view's tests, only after `pip install -e .[qt]`:
QT_QPA_PLATFORM=offscreen $PY -m pytest tests -q -p no:cacheprovider -m qt
```

Screenshot capture of the Web view needs headless Chrome (puppeteer, below);
the Tk screenshot note (Screen Recording permission, `screencapture`) is
history with the Tk view.

Screenshot ritual (the only check that catches an off-screen FULL STOP):
launch the Web view, wait for the startup scan (`/api/setup` → `state.is_scanning`
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
- Red Percent: ONE mode, a row when red % changes (the 2026-09-21/22 "fastest sampling" part is AMENDED 2026-10-07: settled frames at the source rate, see "Round 2026-10-07"); red
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
- Names: "RGB Analysis" (renamed from "Red Percent", 2026-10-07), "Rotator", "Temperature Controller".
- 2026-10-04: **the Rotator turns the chip** (in-plane, about a centre that
  is neither corner A nor the stage origin); the Sample Map models it
  (`rb-rotator-frame`, 2026-10-05). The Transfer Map still calls the angle
  `tilt_deg`; renaming it is the owner's call.
- SUPERSEDED 2026-10-07: "Web = candidate primary frontend; Tk/Qt persist as
  backups" (and the DEFAULT_VIEW qt ruling of 2026-09-28, and D-9 "Tk is the
  default on every OS" of 2026-09-25). Web is the ONLY frontend; Tk and Qt are
  frozen at `413f504`; one entry point, `station-web`.

## Next programme: packaging (2026-09-25)

`docs/rebuild/PACKAGING_PLAN.md`: one PyInstaller bundle per platform, steps
P1–P8 with routes. Since 2026-10-07 the bundle has ONE launcher,
`station-web` (RET-3; it began as three, with D-9 "Tk default" amended on
2026-09-25, both superseded). P1–P4 landed 2026-09-25: `pyproject.toml`
(`[project.scripts]` is `station-web` alone; PySide6 is the `qt` extra, needed
only to run the frozen Qt tests); `packaging/station.spec`; `packaging/smoke.sh`. The smoke found and the lead fixed a safety hole: Tk 9 on macOS
swallowed SIGTERM past `close()` (A10; the Tk view is retired). P5 (CI matrix) was written 2026-09-28; then the lab's
Windows PC. A design-language proposal (four boards rendered in HTML, one
component strip each) was published for the owner the same day; the choice
lands as `palette.py` / `theme.py` tokens.

## Round 2026-10-07

Base `87558ad` (tag `round-2026-10-07-start`). Plan `calm-growing-lark`; the
owner's rulings are applied as listed. Each merge commit's message says what
landed.

**Owner rulings of the day** (they supersede the dated ones named):
- **Web is the ONLY frontend.** `src/views/tk.py` and `qt.py` are frozen at
  `413f504` (after the Signature restyle), unregistered, banner on line 1, kept
  intact for reference; `views/picking.py` is deleted; `--tk` / `--qt` print a
  retired message and exit 2; one entry point `station-web`; one PyInstaller
  launcher. `pip install -e .[qt]` is needed only to run the frozen Qt tests.
  Supersedes "Web candidate primary, Tk/Qt backups" (STATUS), DESIGN_BRIEF
  "binds all three views", DEFAULT_VIEW qt (2026-09-28) and D-9 "Tk is the
  default on every OS" (2026-09-25).
- **Record everything during a trial, trim in analysis.** Red Percent samples
  SETTLED frames at the source rate (amends the 2026-09-21/22 "fastest
  sampling" ruling): two reads at least 5 ms apart must agree; black, stale and
  unsettled grabs are rejected and counted (Diagnostics counters
  `frames_accepted`, `rejected_black`, `rejected_stale`, `rejected_unsettled`).
  The loop runs at about 15 Hz on the bench's 7 fps viewer. Root cause (real
  bench data, 40 trials): mid-repaint grabs of the vendor viewer put black and
  stale frames into the video, the profile and the force extrema (median 51 %
  glitch rows).
- **The interactive procedure**: setup (preliminary info) -> Arm -> region (a
  full-resolution still of the whole display is taken as the trial's first
  asset and the capture region is picked ON that still; nothing records yet)
  -> live (trial row, Red Percent run, full-display video, telemetry) ->
  marked (after Mark force) -> finish (review) -> setup. Abort from region on;
  the stop overrides everything. There is no boot-time capture buffer. The
  live red-percent plots are gone from the live view; the profile is still
  stored at Finish and on abort. Unused fields leave the sheet; the Overview
  has a hairline boundary around each device. The model contract is
  "Phases: the interactive procedure (2026-10-07)" in `MODEL_CONTRACT.md`.
- **Maps.** The map figures are speed x force class (Low / Medium / High,
  Unclassed) with width as colour; the tilt is collected, never drawn. The
  Sample Map is a microscope-image store keyed by the free-text `sample_id` the
  Transfer Map's trials carry, with a read-only "Trials for this sample"
  listing; flake-coordinate homing is DORMANT (code and tables kept, out of the
  schema, `SampleMap._dormant_schema`). Sample store v3 adds
  `sample_images(sample_id, instrument in {transfer_stage, microscope},
  magnification in {10, 20, 50, 100}, path relative, sha256, captured_at, note)`.

**Landed** (merge commits on `mvc-refactor`; `git log --oneline 87558ad..HEAD`):
- `rb-link` L1-L13 (serial-link safety: reconnect by itself, counters, a stop
  confirmed by telemetry, no mode entry out of an unconfirmed disable),
  `rb-dist-app` A1-A5 (in-process flashing in `controller/flashing.py`, the
  bundle firmware check, an operator-chosen trial store, Switch to stable, the
  updater on the public repository) and `rb-link-views` (Web + base only; two
  Tk/Qt-only commits were dropped and tagged `rb-link-views-dropped-tkqt`):
  merge `3bfb8bc`.
- `rb-capture` CAP-1 (settled-frame Red Percent) `295d15f`; CAP-6 (`Screen.monitors`
  public; the region recorder writes fragmented MP4).
- `rb-recorder` `0015553`: `src/devices/screen_recorder.py` (`ScreenRecorder`:
  full-display, native resolution, fragmented MP4 that survives a kill, a
  `frames.csv` sidecar, `capture_still`), `src/model/trial_telemetry.py`
  (`TrialTelemetry`: every model's public state on one clock, plus Red Percent
  rows and EventLog lines), `devices.video.ffmpeg_exe` (REC-1..3).
- `rb-analysis` `f666864`: `settled_mask` and robust extrema in
  `model/transfer_map_analysis.py` (None when nothing settled) and
  `dev/reanalyse_trials.py` (offline re-analysis of recorded trials;
  read-only unless `--write`, backup first) (AN-1, AN-2).
- `rb-camera` `5579d21`: `src/devices/camera.py`, a ToupCam pull-mode device
  with an injected library and a SIM (CAM-1). Wiring is bench-gated: only one
  process can hold the camera, so the vendor viewer must be closed.
- `rb-stores` `7ff07fb`: sample store v3 (STO-1), the Sample Map as an image
  store (STO-2), the map as speed x force class (STO-3).
- `rb-retire` `79ae0bd`: RET-1..4 (freeze, unregister, one launcher, lazy Qt probe).
- `rb-web` `7f509cb`: WEB-1..5 (the page draws the procedure step; the region
  picker draws on the model's still at full resolution; explicit device
  boxes in the Overview; `GET /api/image` serves thumbnails by relative path
  under the output root).
- `rb-clean` `3cd94cc`: eleven merged worktrees removed, `.DS_Store`
  untracked, 32 cited handoff files archived (since removed (history: tag pre-root-cleanup-2026-10-07)).
- `rb-tmap` (part 1) `436400a`: TM-1..TM-4, the Transfer Map follows the
  procedure (`PHASES` setup, region, live, marked, finish; `new_tip` joined it in
  the proposal round), the live plots leave the live view, the tilt is collected,
  never demanded or drawn.
- `rb-docs` `af3bb75`: the first docs pass of the round.

**Transfer Map store.** At the time of this first pass the repo was at v6 and the
lab's databases at v7/v8 (superseded: the lab's stage push was merged in the
proposal round below and the repo is now v8; the planned v9 is no longer needed).

**Gates** (the first pass's lead run, after rb-tmap part 1 and the docs pass; the
current counts are in "Round 2026-10-07 (proposal)"): 3841 passed, 10 skipped,
227 deselected, 1 xfailed; legacy 1038 passed (the legacy tree and gate are gone); a SIM end-to-end trial (setup -> region with the stage still ->
live -> marked -> finish) produced screen.mp4, frames.csv, telemetry.csv,
before_full.png, mark_full.png and a recorded row.
Golden 78 (now 77). The Qt pass is optional and needs `.[qt]`.

**Bench-only, the owner's (Phase 4)**: the transient mechanism on the Mint box,
`libtoupcam` / `amcam` for the MU1003, the full-screen grab and x264 cost,
flashing after A1/A2, D-7, and reviewing `reanalysis_<date>.md` before any
`dev/reanalyse_trials.py --write`.

## Round 2026-10-07 (proposal)

Base: tag `round-2026-10-07-merged` (`da1205b`). The proposal round, the merge of
the lab's stage push, the root cleanup and the move to `~/GitHub`; `git log
--oneline round-2026-10-07-merged..HEAD` is the list, and each merge message says
what landed. The row-by-row closures are in `BUGFIX_PLAN.md`
("Round 2026-10-07 (proposal)").

**Owner rulings** (they supersede the dated ones named):
- **The force is read from the tip's shade** (the lab's estimator, `model/tip_shade.py`):
  a baseline comparison beats the red extrema. The red-trace definitions stay as a
  secondary analysis, the `factor=`. The estimator bank compares candidates on
  footage before the model is chosen for good. See `RECORDING_A_TRIAL.md`, "The force".
- **Red Percent is RGB Analysis** (`src/model/rgb_analysis.py`, class `RgbAnalysis`,
  section "RGB analysis details"): six channel numbers per settled sample (red,
  green, blue shares; r, g, b means), `factor=` on the analysis, profile columns
  `green blue r_mean g_mean b_mean shade`, `dev/reanalyse_trials.py --factor`.
- **Tips have a model** (`tip_models`, seeded TAP300; existing tips backfilled TAP300);
  "New tip..." is a prompt phase (`new_tip`) with Tip ID and Model; `set_tip_model`.
- **Every trial names its sample, chip and flake**, picked from the Sample DB's store
  read-only; Arm refuses without all three (a flake never without its chip); the
  pickers are a hierarchy (Chip greyed until a sample is chosen, Flake until a chip
  is: `enabled_by` + `enabled_by_reason`); the cut number is derived. Every Arm asks
  the vacuum question first (the station cannot sense it).
- **The Sample DB (formerly the Sample Map; renamed 2026-10-07, internal module
  `sample_map.py` kept; `profile.RENAMED_MODELS` reads the old name from station and
  user preferences) is a sample > chip > flake store** with photos at every level
  (browsing is three tiers, `sample`, `chip`, `flake`, each offering only its own
  New button, then the prompts new_sample, new_chip, new_flake; store v4; materials
  seeded hBN, graphite, MoS2). Photo rule (owner 2026-10-07): a flake needs a photo,
  a sample and a chip do not; a chip itself is always required for a flake. It is ON by default
  again (`STATION_SAMPLE_MAP=0` turns it off).
- **Speed dials are percent** over per-device ceilings (stepper 3200, chuck 600
  steps/s), steps/s as a secondary readout.
- **User accounts**: `model/user_store.py` (scrypt passwords, `users.sqlite`),
  `model/user.py` (the User owns a config the Controller loads; Guest = the
  station defaults; since 2026-10-08 a `Panel`, not a device `Model`, drawn as the
  rail's account menu, see "Round 2026-10-08 (night)"), Setup's "Station defaults"
  section and the sign-in screen; Phase 1 profiles migrated;
  `STATION_PROFILES=0` hides them. The maps stamp `operator_auth`/`owner_auth` with
  `password` or `guest`. Reserved settings keys `layout`, `default_fields` and
  `sample_base` exist, nothing built on them.
- **Tutorials**: `views/web/static/tutorial.js`, `tutorials/*.json` ("Your first
  trial", "Register a sample"); simulation only.
- **The branch model**: (owner, final) `main` is the station, the pre-release line, receiving
  pull requests from short-lived branches (`feat/*`, `fix/*`, `agent/*`);
  `mvc-refactor` is DELETED once merged into main, not kept as an integration
  branch; releases are tags on main cut with
  `dev/release.sh vX.Y.Z`; `legacy` is the lab's original Tk app, frozen; `stable`
  is the original app's packaging ref. `gate.yml` runs the fast gate, golden and a
  Web launch on PRs and pushes to main (the mvc-refactor trigger is harmless and goes away with the branch). The version is the git tag. The lab PC clones fresh from main (`git clone --branch main <url>`); `dev/swap_branch.sh legacy` creates `../legacy-app` from the `legacy` branch.

**Landed** (merges on `mvc-refactor`):
- `dd10f01` `state["phases"]`; `b8aadfc` rb-samples (sample store v4, the Sample Map
  pickers); `606c724` rb-web2 (procedure strip, prompt dialog, cascading dropdowns);
  `565abd5` rb-rgb (six channels, `factor=`, the rename); `13c9caa` `section(hosted_tier=)`
  and `readonly(secondary=)`; `e644b89` rb-speed; `889cac0` rb-tutorial.
- `c345e2f` the lab's 2026-10-07 stage push merged (the lab's commit `f55de75`; tag
  `bench-2026-10-07-stage`): store v8 (`chip_id`, `flake_id`, `cut_id`, `invalid`, six
  tip-shade columns), `tip_shade.py`, `shade_offline.py`, `finalize.py`, the vacuum
  question; the Qt finalizer frozen with the Qt view.
- `429a988` rb-trial (the trial page per the approved proposal: tip dropdown and New tip
  prompt, pickers and cut number, the Force estimate from the shade, Video as one word,
  step text and analysis health in `state["step_text"]` and `state["analysis_health"]`,
  counts in Diagnostics).
- `17791b8` rb-release (+ `ad311eb`): one version source, the publishing pipeline with
  `SHA256SUMS`, `dev/release.sh`, the updater in versions, `update.sh`/`update.bat`, the
  branch model, `gate.yml`.
- `e3ef866` rb-accounts; `53a9cdb` rb-estimators (`model/estimators.py`, the Estimators
  section hosted at tier 1, `dev/estimators_offline.py`; the first-trial tutorial walk
  xfail).
- `c315201` rb-rootclean (+ `aa2ae4e`): `legacy/`, `docs/archive`, the frozen ledger
  (`progress.md`, `carry.json`), the design data and `shots` leave the repository (history:
  tag `pre-root-cleanup-2026-10-07`); `bench-checklist.md` moved to `docs/rebuild`; the
  lab's `merge_cuts.py` and `set_sample_ids.py` moved to `dev/`; the golden re-capture test and
  the three live frame comparisons are retired (golden pins 77 captures). Lost with them: the
  SMC100 pacing comparison and the heater ramp-decimals cross-check.

**The move.** The repository moved from `~/Documents/GitHub` to
`~/GitHub/transfer-stage-unified/`: `mvc-refactor/` (the one checkout, still named after the
branch until re-cloned; the one venv `.venv` made from the `[dev]` extra), `main/` (a plain
checkout of the lab's original app, for `dev/swap_branch.sh`; absent after a fresh clone), `rb-<name>/`
siblings while a round runs. The lab deploys by git today (a fresh `git clone --branch main <url>`,
`pip install -e .`, `run.sh`); the
first installed release comes after v1.0.0.

**Transfer Map store.** v8, the lab's, adopted; "Transfer Map part 2" is DONE by the lab
merge, and the planned v9 (with a `.v8.bak` backup) is no longer needed. The Sample DB
store is v4.

**Gates**: fast **4194**, golden **77**, a launch; known flakes are listed in the
`verify` skill (the first-trial tutorial walk is an xfail, `test_o15` under load, the Sample
Map's rated-later flake, the transfer-map mark timing flake). The lead fills the count after the
final gate. Collected under `-m "not qt"`: 4211 (collect-only, 2026-10-07).

**Open**:
- The first-trial tutorial walk re-anchored to the pickers (xfail until then).
- The Web finalizer (the data finalizer exists only in the frozen Qt view).
- Per-user layout, default fields and sample bases (reserved settings keys only).
- Phase 4 bench items (the owner's): the list under "Bench-only" in "Round 2026-10-07"
  plus reviewing the estimator bank's curves on new footage before the force model is
  fixed, and the shade thresholds (contact line, 0.10, the thirds).
- The lab PC's code must reach a branch before a release is installed there
  (`packaging/README.md`); the stale `rb-estimators` and `rb-rootclean` directories beside
  `mvc-refactor/` are leftovers, not worktrees.

## Round 2026-10-08 (night)

Merged into `main` between `619ba9e` and `129f76d` (fast merges; the
architecture audit of what they left is `docs/rebuild/audit-architecture-2026-10-08.md`):
friendly device names; per-device tutorials (five devices); auto-launch of
connected devices, Hard reset per row (which since `25a41d3` also applies a
port or gamepad changed after the launch), no add/remove, no Relaunch; the
sign-in screen and full-screen Setup ("Settings" after the launch); the heater
close watchdog (setpoint 0 read back before any close) and host-gone
watchdog; the account menu (`model.user.User` is a `Panel`, not a device
`Model`; Guest gets the tool controls only, no Transfer Map or Sample DB);
tab-close quit (`/api/leave` plus a silence backstop), one live tab, a single
station instance (`controller/single_instance.py`) and exclusive serial ports;
the Sample DB rename, chips required, hierarchy pickers; per-user stores for
both maps behind a `new_store` prompt (`model/store_choice.py`, the
`StorePrompt` mixin; never inside the station's folder); the store backup to
`~/QMDL_Drive` or a user's folder (`controller/backup.py`, on the account
menu; an unmounted `~/QMDL_Drive` is unavailable); chuck runaway guards (NOT
yet bench-validated); the step indicator and launch transition; "Overview"
renamed "Dashboard" with reorderable tiles; picture previews (100x, then 50x,
then lower; newest wins) on the Sample DB and the trial setup.

## Round 2026-10-09 (XYZ Stage)

Branch `feat/xyz-stage`, by PR. A new device: the 50 mm XYZ stage, one Teensy 3.5
and one TMC2209 (single-wire UART) per axis.

What landed:
- **Firmware.** `firmware/xyz_stage_axis/`, protocol v1 in its `PROTOCOL.md`.
  It grew from the bench validator (`dev/equipment_test/`):
  - identity `DEV: x X|Y|Z` from an EEPROM tag (`AXIS X`);
  - HOME on the photo-interrupter, with the limit switches as interlocks;
  - the JOGV dead-man, the STREAM of P lines, HOSTTIMEOUT;
  - `LOG 0|1|2`.
- **Model.** `model/xyz_stage.py` and `devices/teensy_axis.py`:
  - Stepper Probe parity: the modes, the vector Step, the gamepad jog;
  - per-axis Zero here and Home, and Home all (provisional);
  - readouts in µm and µm/s, with microsteps beneath;
  - the stop path first: parallel ESTOP, all-or-none enable, and any axis
    fault or silence latches the stage.
- **Multi-port models.**
  - `PORT_TAGS` in Setup. Boards are placed by tag, and the row launches only
    with all three ("fault: Z missing" otherwise).
  - Per-axis flashing, all or none, each axis rebooted through its own port.
  - The Teensy flash no longer uses `teensy_loader_cli -s`, which soft-reboots
    whichever Teensy it finds.
  - See MODEL_CONTRACT "One board per axis".
- **Logging.** Every EVT line an axis sends is in the log file, `EVT DBG`
  included.
- **Equipment test kit.** `dev/equipment_test/`: the validator, its GUI and
  wiring sheets, and the UART and limit diagnostics. Not shipped, never
  flashed by Setup.

Open, bench-only (owner):
- Prove each limit switch by a press before TEST LIMITS. Under NO, an open wire
  reads clear.
- HOME polarity and direction (`HOME_DIR`, `HOME_FLAG_LEVEL`) and its
  repeatability (within ±2 microsteps).
- The axis tag survives an upload and a power cycle.
- The sign of each stick and trigger per axis.
- `PARKED_TRAVEL_MM` (0.5 mm, provisional) must be larger than each switch's
  overtravel plus its differential travel.
  - Park on LS1, then on LS2: jogging off must learn the right sign, and
    jogging in must halt within 0.5 mm.
  - Jog off each switch repeatedly and see no `EVT LIMIT`.
  - Silence the host with the port open and see the axis disable after 10 s.
- Stepper Probe's Mega: `int x_steps` wraps past 32767 steps. The station now
  refuses such a Step. Changing it to `long` in the firmware is a bench change.

Review of the branch (Opus, 2026-10-09), all fixed on the branch:
- **R-1:** the µm target ignored the step-size multiplier the Mega applies, so
  a step of 4 moved 4× the displayed distance.
- **R-2:** a target past the 16-bit move could reverse the move.
- **R-3, R-6:** after a failed Teensy upload, no further Teensy is flashed in
  that run; the axis set is flashed all or none.
- **R-4:** chatter while leaving a switch could learn its end backwards.
- **R-5, R-7, R-8:** XYZ mode exit with a silent axis, a late Home reply, and
  the Home all race.

Owner rulings the same day:
- A host silent 10 s after a host-timeout stop disables the driver.
- A switch pressed with its end unknown allows only a slow jog off it
  (0.1 mm/s, 0.5 mm budget).
- Bench test tools live only in this repo, under `dev/`: `dev/equipment_test/`
  and `dev/firmware_sim/`.

**Stepper Probe in physical units.** Distances, step sizes and speeds are entered
in µm and µm/s, over the same stored counts. Each count line sits beneath its
entry in small type, and the percent dials are gone for the Stepper. The wire
bytes are unchanged (golden 77). The 0.625 µm/count scale is unverified because
the lead screw is unmeasured, and the page says so.

## Open items

### Classic and the station on the same boards (2026-10-08)

At the bench: Classic's flash failed ("flashing failed, so the legacy app was
not launched") and the station's Stepper Probe logged the gamepad ("Axis 5
changed") but never moved. Cause: `dev/swap_branch.sh legacy`, rebuilt
2026-10-07, flashed with the legacy tree's own `flash_firmware.py`, which
never writes `~/transfer-stage-runs/flashed.json`, and flashed all four
boards. A Mega it reached kept the legacy sketch (`'t'` toggles enable,
28-byte jog) while the stamp still said "station", so the Launcher's flash,
`swap_branch.sh station` and Setup's Firmware row all read "already current";
the station's `'e'` and 42-byte jog went to a board that ignores them. The
legacy heater sketch cannot build beside the station's libraries (Adafruit
`max6675.h` and the NewLiquidCrystal `LiquidCrystal_I2C` against Tillaart's
`MAX6675.h` and the registry `LiquidCrystal_I2C`; compiled 2026-10-08 with
arduino-cli 1.1.1, avr 1.8.8: all six Mega sketches of both trees build, the
legacy `temp_controller` stops at `max6675.h`). Fixed on `fix/classic-flash-stamp`:
the legacy swap flashes with THIS tree's `firmware/flash_firmware.py
--sketch-root ../legacy-app/firmware --channel stable`, the three Megas only,
as the bench-validated `run_swap.sh` of 2026-09-28 did (the heater's wire is
the same in both trees). Bench recovery once, for boards flashed before the
fix: run Classic once (it now records what it flashes), then the Launcher;
or move `flashed.json` aside and start the Launcher (it flashes every board).

### Tip-shade force, held features, the finalizer (2026-10-06)

- **The force is read from the tip's shade, not the red percent** (owner ruling). Median green of the right half of the recorded tip region; contact is the shade rising off its baseline, and the force is where the shade stands on its peak at the Mark (`model/tip_shade.py`, `docs/rebuild/RECORDING_A_TRIAL.md`). The sheet's **Force** field reads No contact, Contact, Low, Medium or High live; schema v8 stores `force_position`, `force_class`, `contact_lowered`, `shade_*`; the map's default definition is `shade_position`; the video index gains a `shade` column. Recording is unchanged (the whole tip region). The red-percent profile only logs when the rounded value changes by 0.1, so its force indices (`at_operator_mark` and the rest) are not to be trusted. `rebuild_force` recomputes the columns from footage; run on the bench database on 2026-10-06 for the four valid trials. The thresholds (contact line, 0.10, the thirds) are bench values, the owner's.
- **The map has two axes, speed and force** (owner, 2026-10-06; tilt is fixed at 7° and recorded but no longer plotted). The figure dropdown is Map (speed × force, coloured by width; `plot_data` kind `map`, `map_limits`), Heatmap (the Gaussian process over speed × force; replaces the tilt × speed slice) , Compare, Profile. The Force band dropdown is gone; `width_gradient` reads um per step/s and um per unit force. Trials with no tilt are plotted. Bench database, 2026-10-06: trials 7 and 8 (400 steps/s, off the 100/200/300 plan) unflagged as supplemental points and their force rebuilt from footage (Medium, High); trials 6, 2 and 35 stay invalid (6 troublesome, 2 and 35 have no force).
- **Sample Map and user profiles were off by default** (owner, 2026-10-06; SUPERSEDED 2026-10-07: the Sample Map is on again, `STATION_SAMPLE_MAP=0` turns it off, and accounts are on, `STATION_PROFILES=0` hides them; `controller/setup.py`). The code is also on branch `feature/sample-map-profiles`. History was not rewritten: every commit is on `origin/mvc-refactor`, and some are another developer's.
- **Data finalizer** (Qt only, temporary; frozen with the Qt view, a Web finalizer is open): the Transfer Map's "Finalize data..." button opens a window to walk the samples with video and stills and enter AFM and optical estimates (`model/finalize.py`, `views/qt_finalizer.py`).
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
  `tools/heater_plot.py`, branch `worktree-agent-a620f8f275b8607b4`,
  `b602f68`) was merged on 2026-10-08 and reshaped by the owner's ruling of
  that day: the CSV is gone; a trial's heater readings are in its store
  (`trial_heater`, `RECORDING_A_TRIAL.md`) and everything else is in the
  device log (`DEVICE_LOG.md`); `heater_plot.py` reads both. P2 stopped by the owner after
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
   have ported equivalents before `legacy/` was deleted (history: tag pre-root-cleanup-2026-10-07).
4. **Cutover** — partly done. Done 2026-09-23: the move (`station/` → `src/`,
   old `src/` and `tests/` → `legacy/`, `tests/station/` → `tests/`) and the
   docs prune (stale pages to `docs/archive/`). Done 2026-10-07: `legacy/` deleted (history: tag pre-root-cleanup-2026-10-07).
   Not done: merge `mvc-refactor` → `main` and push.
5. `Rotator.home()` target-commit ordering is tested now (rb-rotator); the
   `COLUMN_SPLIT_CARDS = 6` Qt rule is a judgement, not a measurement.
6. Heater refusals new vs old: 300 °C ceiling, PID/ramp bounds, 31-char
   frame limit — confirm at the bench.

## History

- 2026-10-07 (later): the proposal round, the lab's stage merge, the root cleanup and the
  move to `~/GitHub` ("Round 2026-10-07 (proposal)").
- 2026-10-07: the round above: Web the only frontend (Tk and Qt frozen at
  `413f504`), settled-frame recording, full-display recorder, the procedure
  phases, the image-store Sample Map, serial-link safety, in-process flashing.
- Aug–Sep 2026: `src/` was a staged MVC repair (S0–S16) of 13 root causes
  across 213 audited findings. Plan and test policy: (history: tag pre-root-cleanup-2026-10-07).
- 2026-09-23: the refactor redone from scratch as `station/` on a `rebuild`
  branch was fast-forwarded here; `rebuild` retired. The same day `station/`
  became `src/`, the old tree became `legacy/`, and stale docs were archived.
- 2026-09-23: `verify` and `parallel-stage` rewritten for the new tree;
  `stage-close`, `reconcile-ledger` and `fix-a-finding` retired with the
  ledger process (in git history before `beb7b94`).
- `legacy/` was deleted 2026-10-07 (history: tag pre-root-cleanup-2026-10-07); `mvc-refactor` still merges to `main`.

## Process notes

- Twelve Opus agents in exclusive-write-set worktrees, lead (Fable) verifies:
  write-set diff, rerun tests, hand-drive the feature, merge. Core changes
  only by the lead; agents file CORE CHANGE REQUESTS in handoffs.
- Every claimed pass was re-run by the lead. The golden gate and a real
  process launch each caught defects no unit test did (SDL init off the main
  thread trapping at exit; Web launcher returning before serving; Qt with no
  QApplication; FULL STOP bar pushed off-screen in Tk).
