# Bugfix plan — post-push sweep

Written 2026-09-23 from a sweep of `station/`, `tests/station/` and
`docs/rebuild/` (now `src/` and `tests/`: paths below are corrected for the
move, line numbers are as of `8228991`) for swallowed exceptions, unverified constants, stop-path
gaps and dead test scaffolding. Baseline at the time of writing: 1498 fast
tests pass (47 s), 78 golden wire scenarios byte-identical, pushed to
`origin/mvc-refactor` (the `rebuild` branch was fast-forwarded onto it and retired).

Each item names its **route** (direct / `router` / `agy`) up front, per the
delegation rule: decided at plan time, not improvised. Bench items are
owner-only and never delegated.

Ground rule for every code item: **test first, prove the defect against the
pre-fix code, then fix.** No baseline is ever bumped to go green.

---

## Tier A — code defects, fixable now (no bench needed)

| # | Where | Defect | Fix | Route |
|---|---|---|---|---|
| A1 | `src/model/probe.py:471` `_write_stop` | Returns `True` when `self.port is None`, so `_halt_hardware` reports a confirmed stop with nothing written. `Rotator._halt_hardware` (`rotator.py:414`) returns `False` in the same situation. The two models disagree on what a portless stop means. | **Decision first (direct):** a stop that wrote nothing is *unconfirmed*, matching the rotator, unless the owner rules that a closed/portless model's stop is vacuously true. Then align probe to rotator, add `test_a_stop_with_no_port_is_not_confirmed` (probe) and `test_a_stop_with_no_controller_is_not_confirmed` (rotator, currently untested). | direct decision → `agy` |
| A2 | `src/views/web/server.py:704` `_watch_loop` | A raising `_check_heartbeat` is logged at `events.debug` only. The heartbeat watchdog is the one thing that turns a dead browser into `estop_all`; if it is broken, nothing visible says so. | Log at `events.warn` (once per fault, `every=`) and add `test_a_failing_watchdog_check_is_reported`. Consider counting consecutive failures and calling `estop_all` after N, since a watchdog that cannot check is a watchdog that cannot protect. | `agy` |
| A3 | `src/model/red_monitor.py:420` `position_age` | `except Exception: return None` makes a broken probe indistinguishable from "no sample yet". Velocity silently goes missing. | Narrow to the exceptions a missing attribute can raise, log the rest at `events.debug` with `every=`. Test: a source whose `position_age` raises is logged, not hidden. | `agy` |
| A4 | `src/model/plot_data.py:161` sidecar load | Corrupt `_station_meta.json` is dropped silently; the plot renders with no metadata and no message. | `events.warn("Run Metadata Unreadable", path)`; still return the CSV rows. Test with a truncated sidecar. | `agy` |
| A5 | `src/views/web/server.py:527` request body | Any exception maps to "invalid JSON" with no log. A non-JSON failure (encoding, size) is misreported. | Catch `ValueError` for the JSON case; log anything else at `events.debug` with the type. | `agy` |
| A6 | `src/controller/setup.py:526` port probe | `except Exception: return False` hides a real fault on a real port during scan; the port just reads as "not ours". | Keep the `False`, add `events.debug("Port Probe Failed", port, exc)`. The docstring cites SERIAL-17; the branch should say so in the log. | `agy` |
| A7 | `src/events.py:117` log-file write | `except (OSError, ValueError): pass`. If the run log cannot be written, every later diagnostic is lost with no sign. | One-time `sys.stderr` note and a flag so the view can show "logging off". Test: unwritable path → stderr line, no raise. | `agy` |
| A8 | `tests/test_wire_golden.py:20-46` | Docstring says the replays are `xfail(strict=False)` and an `AWAITING` dict carries reasons; no `xfail` marker exists, all 78 pass. Dead scaffolding that misdescribes the gate. | Delete the `AWAITING` dict and rewrite the docstring: the gate is strict. | `router patch-plan` → direct edit |
| A10 | `src/controller/controller.py` `_hook_exit`, `src/app.py` `launch` | **Safety (found 2026-09-25 by the packaging smoke test): under Tk on macOS a SIGTERM ended the process with exit 1 and `Controller.close()` never ran** - Tk 9 on Aqua installs its own C-level SIGTERM handler when the first window is created, after `_hook_exit` had installed ours; `getsignal()` still reported ours. Every model live, every port open, from source and from the bundle. Qt and Web unaffected. **Done the same day:** `Controller.hook_signals()` split out and re-armed by `launch()` after the view is built; child-process test `test_a_sigterm_under_tk_still_runs_the_close_path` (exit 1 before, -15 after). | | done (lead) |
| A9 | `tests/test_view_web_client.py` (7 tests) | `skipif(NODE is None)` silently drops the JS client tests where node is absent. On a CI box without node the Web client is untested and the run is green. | Make the skip loud (`pytest -rs` in the verify recipe, or a single always-on test that fails when node is missing on a box that has `STATION_REQUIRE_NODE=1`). | `agy` |

Write set for the `agy` batch: `src/model/probe.py`, `src/model/rotator.py`,
`src/model/red_monitor.py`, `src/model/plot_data.py`,
`src/views/web/server.py`, `src/controller/setup.py`, `src/events.py`, and
their tests under `tests/`. One worktree, one branch (`rb-bugfix-a`),
one handoff in `handoff/bugfix-a.md`. Lead re-runs the three
verify commands and hand-drives A1/A2 in the Web view before merge.

## Tier B — bench only (owner; never delegated)

These are the constants and directions the code pins to *today's* value and
that no test can settle. Each has a test that will need its expected value
changed if the bench disagrees, so the fix is "measure, then edit the number".

| # | Where | Question |
|---|---|---|
| B1 | `src/devices/gamepad.py:322-361`, `tests/test_gamepad_layouts.py` | GAMEPAD-11 name match (a DualShock silently gets the Bluetooth-Xbox row); GAMEPAD-12 throttle → Z direction; GAMEPAD-13 D-pad right direction; GAMEPAD-14 deadzone 0.12 vs 0.1. |
| B2 | `src/model/heater.py:54-57` | `max_setpoint` 300 °C ceiling, PID/ramp bounds, 31-char frame limit. All PROVISIONAL. |
| B3 | `src/views/web/server.py:562` | Heartbeat warn/stop at 5 s / 15 s (D-8a, WEB-19, WEB-23). |
| B4 | `src/model/probe.py:1081` | D-7: the DC board has no coil kill. Firmware v2 is the only fix; owner-only. |
| B5 | `src/devices/serial_port.py:676` | Stop wait when a stop "feels slow". |
| B6 | `src/devices/smc100.py` | `READ_TIMEOUT_SEC` now bounds a whole line: confirm against the real SMC100. |
| B7 | `src/views/tk.py:111,841` | Which physical button on a Mac; the ttk theme pass. |
| B8 | all views | Region-picker display scaling; achieved Red Percent capture rate (every sidecar records it). |

Deliverable for Tier B is a one-page bench checklist with a blank per row;
the code change afterwards is a number edit plus its test, routed `router`.

## Tier C — hygiene, low risk

| # | Item | Route |
|---|---|---|
| C1 | Scan time: two junk macOS ports get the full handshake (~18 s). A name filter is one line in `Setup.scan_ports`. Owner picks the filter; the line is trivial. | direct after owner ruling |
| C2 | `src/views/tk.py` has 18 bare `except: pass` sites (285, 308, 312, 437, 504, 797, 887, 999, 1121, 1167, 1242, 1254, 1274, 1289, 1296, 1424, 1515, 1668) and ~40 `events.debug`-only swallows. Most are Tk teardown races. Audit each: keep, narrow, or log. | `router review` produces the keep/narrow/log table; `agy` applies it |
| C3 | `src/devices/gamepad.py` silent swallows at 198, 219, 273, 305, 419, 762, 791. Same audit as C2. | same as C2 |
| C4 | `STATUS.md` said 547 commits; `git rev-list --count mvc-refactor..rebuild` says 98. Corrected in this commit. | done |

## Tier D — `main` vs the rebuild: operator-facing regressions (audit of 2026-09-23)

Four read-only Opus auditors compared the lab's original app on `main`
against the rebuild, one subsystem each, reporting only behaviour that is
missing or changed with a negative or neutral consequence and excluding
everything an owner ruling covers. Full reports:
`handoff/audit-{probes,heater-rotator,redpercent-camera,shell}.md`
(outside the repo). The lead re-verified every row marked **verified**.

Framing correction the auditors missed: `main`'s firmware is not what the
bench runs. The lab ran `legacy/src` from 2026-08-26, which already speaks
the new `'e'`/`'d'` + 42-byte protocol, so the boards were presumably
reflashed before then (unverified, see D13).

| # | Where | Defect | Fix | Route |
|---|---|---|---|---|
| D1 | `src/model/probe.py` `_build_port` (`SerialPort(name)`, no rate) | **Probes open at 115200; the stepper and chuck firmware and `legacy/src` run at 500000.** SIM ignores baud and the golden gate compares bytes only, so nothing caught it. No probe will talk to a real board. **verified** | Pass `baud_rate=500000` from the probe; test that a probe built with a port *name* records 500000 (and the heater 115200). Prove against pre-fix. | `agy` (safety: first in the batch) |
| D2 | `src/devices/smc100.py` `sendcmd` | **Move/Home can report done while the stage is still turning.** `legacy/src` held the serial lock from write to reply and cleared the input first; the rebuild does neither, so the 4 Hz position poll and the move's status loop read each other's replies. **verified**: lead's rerun of the auditor's repro, 4/10 early returns at 20 ms reply latency (0/10 at 5, 10, 40, 60 ms). | Hold one lock across write+read in `sendcmd`, discard stale input before the write; regression test with a latency-injecting fake at 20 ms, ≥25 trials. Bytes unchanged. | direct decision on lock scope → `agy` |
| D3 | `src/devices/gamepad.py` `drain_edges`, `src/model/probe.py` | **D-pad and bumper steps do nothing.** Edges are parked for `drain_edges()`, which nothing calls. **verified** (grep: only its own docstring). Tests miss it because the fakes put D-pad values straight into the stick readings. **Re-confirmed 2026-09-25 by a SIM run** (`handoff/audit-gamepad-steps.md`): dead on every layout for the Stepper Probe and the Chuck; the DC board never had it; `legacy/src` had it (rebuild regression). Triggers are NOT mapped to Z steps anywhere. | Consume edges in the probe's poll: extract `_jog_tick()` from `_jog_loop`, `drain_edges()` every tick in every mode; when manual, gate open and not latched, merge the four edge keys into the levels before `_send_jog`, else discard (a tap made while idle must not fire on entering manual). Bytes unchanged (golden `stepper.jog.dpad_*`/`bumper_*`). Tests: the fakes report edges the way the device does; 6–8 real-Gamepad + real-probe tests per layout. | **done 2026-09-25** (`rb-d3`, lead-verified by SIM rerun: one packet per press on every layout; 77 tests; bytes unchanged). B1 remains: T.16000M buttons 7/9 do nothing (the layout row uses 4/5), D-pad sign vs `main`. |
| D4 | `src/model/probe.py` gamepad swap | **Swapping the gamepad during manual mode no longer stops the stage** — undoes `main`'s last hotfix (`68e412f`). Auditor confirmed in SIM. | On swap while manual: stop packet on the still-open port, leave manual. Test first. | `agy` (safety) |
| D5 | `src/model/probe.py` / `src/devices/serial_port.py` write failure | A failed jog write marks the port lost and closes it; no stop is attempted, runtime reconnect is gone, the stage can drift at the last jog speed. `main` sent a stop on the still-open port. Medium confidence (no hardware). | Diagnose with a fake that fails one write; then: attempt the stop frame before closing. | direct diagnosis → `agy` (safety) |
| D6 | `src/controller/setup.py` Refresh | **Refresh after launch probes ports held by running models**, reopening them at another baud. Shown on a pty (held handle went 9600→115200, board side received the identity query and twelve `s`). On Windows the open fails with a warning instead. | Skip ports owned by live models; disable Refresh while any model exists, or scan only unowned ports. Test with a model holding a fake port. | `agy` |
| D7 | `src/controller/setup.py` build | A bad Rotator port blocks the whole launch (build is all-or-nothing; probes and heater on bad ports still build and show port errors). `main` launched each device separately. | Build per model; a failed SMC100 open surfaces as that model's port error. | `agy` |
| D8 | `src/model/heater.py` frame length check | The heater refuses settings `main` sent intact: the ramp is always written with two decimals (`.05`→`0.05`), lengthening the frame past the 31-char refusal (`<250.5,120,12.25,0.25,.05,-1.5>` is 29 chars on `main`, 33 here). The two-decimal format is `legacy/src`'s and is pinned by the bytes ruling; the refusal is new. | Decision: measure the *actual* frame the model will send and refuse only when that exceeds 31, with a message naming the field to shorten. | direct decision → `router patch-plan` |
| D9 | `src/model/red_monitor.py` baseline, Tk Save | Reset Baseline mid-run overwrites the starting baseline and the sidecar keeps only the last value; `main` logged `BASELINE SET/RESET` rows. Auditor reproduced (first 20.0 %, sidecar 50.0 after two resets). Tk Save copies only the CSV; the sidecar stays under the runs folder. | Append a baseline row to the CSV on every set/reset and keep a list in the sidecar; Save copies both files. | `agy` |
| D10 | `src/devices/gamepad.py` T.16000M rows, deadzone | One mapping for both T.16000M device names (Z reversed on Linux Mint per `main`); throttle buttons map to ±1 vs `main`'s ±0.5; deadzone 0.12 for every pad vs `main`'s final 0.03 hotfix; first 5 % of trigger travel ignored. | Values from `main` are known: restore ±0.5 and 0.03 with tests. **Direction per OS name is B1 (bench).** | `router patch-plan` → direct; direction: bench |
| D11 | `src/model/rotator.py` Step default | Step defaults to 0 (was 1.0), so Move ± does nothing and says nothing. | Default 1.0; refuse a zero step with a message. | `router` |
| D12 | `firmware/flash_firmware.py` | Imports `discover_ports`/`probe_device_at` from the old tree (re-pointed to `legacy/src` on 2026-09-23 so it still runs); `flash.sh`/`flash.bat` deleted on this branch; auto-detect drops every `COM*` port so it finds nothing on Windows; `main`'s 20 tests for it are gone. | Port onto `src/controller/setup.py`'s scan and identify; restore the two launchers; keep COM ports; port the tests. | `agy` |
| D13 | `src/controller/setup.py` identify | A board still on `main`'s firmware answers the identity query identically and launches with no warning, then never enables and misreads every jog packet. | **Owner question**: is a protocol-version reply worth a firmware change? Until then, a one-line note in the README's flashing section. | owner |
| D14 | `src/model/probe.py` manual mode | Manual speed and step sizes cannot be changed in manual mode; changing them means leaving manual, which de-energizes the coils. The ruling locks distances only in autonomous. | Allow edits in manual; keep the autonomous lock. | `agy` |
| D15 | `src/views/web/server.py` | **Now G2.** No quit control; closing the browser leaves the process holding the serial ports (until the watchdog latches FULL STOP, which does not exit). | Design call: a Quit command that shuts the Controller down and exits. | direct design → `agy` |
| D16 | `run_macos.sh` | Prints that it is launching Web but starts Tk. **Done 2026-09-25** with the D-9 amendment. | One-line fix. | done |
| D17 | `src/devices/smc100.py` | Controller address fixed at 1; `main` had an ID field. Low impact. | Expose the address in Setup only if the bench has more than one SMC100. | owner |

Neutral differences recorded in the audits and not planned (operator-manual
material, not bugs): Red Change readout lost its sign and colour; save
format moved from one session text file to per-run CSV + sidecar; Red
Percent no longer opens from a probe window; port/controller collisions are
a status line, not a popup; the terminal no longer shows tracebacks (they go
to the run log); the Tk view shows one instrument at a time; every detected
device is switched on at launch; a dead port costs ~9.5 s of scan.

Batch order for Tier D: D1 → D4 → D2 → D5 → D3 (safety and hardware first),
then D6/D7 together (one write set: `setup.py`), then the rest.

## Tier E — UI follow-ups from round 2 (2026-09-24), small

| # | Where | Item | Route |
|---|---|---|---|
| E1 | `src/model/red_monitor.py` `figure()` | Return no image when no run is loaded so every view shows its "No run loaded" empty state instead of matplotlib's 6 px caption; confirm all three views render a missing image as the empty state first. | `agy` |
| E2 | `src/schema.py` | Accept `empty=` text on `plot`, `image` and `log_stream`; the Web view already prefers it. | `router patch-plan` → direct |
| E3 | `src/views/theme.py` | A severity → text-colour map for event logs (Tk and Qt each hard-wire one now; `ROLES["info"]` as a text colour was the unreadable-log bug in both). | direct |
| E4 | `src/views/theme.py` `FONT_FAMILY` | The brief names IBM Plex Sans; Tk/Qt can use it only if installed on the station PC. Owner: install it there, or accept Helvetica for the desktop views. | owner |
| E5 | `src/controller/setup.py` `stop_system` | Owner call: confirm before tearing every model down? | owner |
| E6 | Tk after-shots | Capture the three states at 1400×900 and 900×900 when the Mac is free (`handoff/tk3.md`, UNVERIFIED). | direct |
| E7 | `src/devices/serial_port.py` ~730 | "Connection Lost" is published with the port as source; views want the owning model's name so the tray line reads "Stepper Probe lost its serial port". | `router` |
| E8 | `src/model/red_monitor.py` `load_run` | Refuse with a sentence when called without a path (the Web view now always sends one; Tk/Qt dialogs cancel → no call). | `router` |
| E9 | `src/views/base.py` | One shared list of at-rest ("muted") words and one `_show_refused(element, …)` signature so all three views place refusals the same way; a failed command clears the previous refusal in `PanelView._run`. | direct |
| E10 | Web / Qt | The alert queue has no cap; the Web type scale ignores `theme.size()`; Safari keeps ⌘. so only Ctrl+. reaches the page there. | `router` |
| E11 | Tk, Qt on screen | Tk after-shots (rounds 2 and 3) and the ⌘. chord on a real Aqua display are unseen: run the capture commands in `handoff/fix-tk.md` when the Mac is free. | direct |

## Tier F — UI audit round (2026-09-24): six skills, six read-only auditors, one ranked list

Auditors (all Opus, fresh context, `ui-auditor` profile): Impeccable `critique` (Web),
Impeccable `harden`+`clarify` (all views), Impeccable `audit` (Tk, Qt), UI UX Pro
Max guideline sweep (all views), Vercel Web Design Guidelines (Web), `design-system`
token audit (theme + three consumers). Reports: `handoff/audit-ui-*.md`.
Duplicates across reports are merged here; **verified** = the lead reproduced it.
Severity: S1 misleads about hardware state or weakens the stop path; S2 blocks or
makes a task error-prone; S3 inconsistent or off-brief; S4 nit.

| # | Sev | Views | Finding (sources) | Fix | Route |
|---|---|---|---|---|---|
| F1 | S1 | all | **A pop-up covers or blocks the stop.** Web: the ack overlay (`styles.css:1040`, z 10) sits above the rail (z 9); with any failed command showing, the mushroom is dimmed and a click does nothing. `showAck` overwrites its text, so stacked errors vanish. Qt stacks blocking modals; Tk queues app-modal `showerror`. (WDG-1, HC-2, UXPM-1, CRIT) **verified** | Web: rail above every overlay, overlay starts below the rail; ack queue, not overwrite. Qt/Tk: errors go to a non-modal banner/tray; a modal is never allowed while the stop is reachable only behind it. | `agy` (Web) + `agy` (Tk/Qt) |
| F2 | S1 | web | **A stop that never reaches the station fails silently.** `toggleEstopAll` (`app.js:1446`) has no error handling and ignores the `unconfirmed` list; with the server gone the button still reads Stop and the rail reads Connected. (WDG-2, CRIT-1, HC-3) **verified** | try/catch → banner "Stop did not reach the station", rail state "Not answering", list unconfirmed models. | `agy` |
| F3 | S1 | all | **A lost serial port looks live.** The model publishes `devices: {SerialPort: "lost"}` (`model/base.py:178`); no view reads it. Card bar stays live, rail zeros stay trace, the autonomous toggle stays lit. (HC-1) **verified** | Every view reads `state.devices`; a lost device turns the card's bar signal, readouts muted with a stale mark, and the rail says which model lost what. | `agy` |
| F4 | S1 | web | **Offline, the console still looks live.** When polling fails, numbers stay trace and cards stay live; only one word of link text changes. (CRIT-1) | Stale marker on poll failure, readouts muted, rail sentence "Not answering since …". | with F2 |
| F5 | S1 | qt | **The rail clips numbers mid-glyph**: "0." for 0.00, "X (" for positions, with 4 models at 1000 px or 2 at 28 pt. A clipped number can read as another value. (AUD-1, HC-4) **verified** | Never clip a readout: elide the label first, then wrap groups, then drop to fewer readouts per model; the number itself is always whole. | `agy` |
| F6 | S1 | web | **Latched reads only as the word "Clear" and a ring colour.** Ring trace-on-signal 2.48:1, card bars signal-on-panel 2.73:1; no rail sentence says the station is stopped; a tab opened on a latched station shows "Waiting for the station…". (CRIT-2, CRIT-9) | Rail sentence "Stopped — every model latched"; latched bars get a second cue (pattern or width), not colour alone. Owner: the pinned signal is 2.73:1 on panel; accept, or allow a darker latch ring. | `agy`; ring: owner |
| F7 | S1 | tk | **The stop face does not scale with `--font-size`**: fixed 64 px disc, "Clear" is 88 px wide at 28 pt and cut by its canvas. (AUD-2) | Disc diameter from the font metric. | `router patch-plan` → direct |
| F8 | S1 | qt | **Lamps are invisible** (lit 1.50:1, unlit 1.23:1), a disconnected stage shows a red ring (a second red), lamps have no accessible name. Tk's lamp colours are right. (AUD-3) | Copy Tk's lamp colours; ring in ink; accessible names. | `router patch-plan` → direct |
| F9 | S1 | web, qt | **No keyboard path to the stop** on Web (only Escape is handled); on Qt Return/Enter do nothing, only Space. The Web focus ring on the stop is trace, i.e. the latched look (`styles.css:114`). (CRIT-8, AUD-4, DS-1, UXPM-5, WDG) **verified** (ring) | A global shortcut for the stop in every view (documented on the face's tooltip); Return/Enter/Space all press it; a `STOP_FOCUS = TEXT` token for the ring. | `agy` |
| F10 | S2 | all | Refusals land where the operator cannot see them: behind the sticky rail at 1400 px, 888 px off-screen at 900 px, below the fold in Qt, cut at ~60 % in Tk; the shake plays unseen; the line never clears. (CRIT-3, HC, WDG) | Refusal renders at the control that caused it, scrolls into view, clears on the next successful command. | `agy` |
| F11 | S2 | all (core) | Commands the latch will refuse stay enabled (Step, Start run, Enter autonomous mode); Start run is enabled with no capture region. (CRIT-4) | `disabled_when` gains the latch and the region precondition (`probe.py:1025`, `red_monitor.py:1226`). | direct (schema) |
| F12 | S2 | web | Focus management: the open drawer is inert but does not contain focus (44 controls reachable behind it); focus falls to `body` after launch, close, ack and picker; Reopen buttons are torn down every 250 ms poll (focus lost); the "Connected" live region is rewritten every poll. (CRIT-5, UXPM-6, WDG-3/6/7) | Focus trap in the drawer; return focus to the invoker; diff-render the Reopen row; write the live region only on change. | `agy` |
| F13 | S2 | web | **Load run always fails**: the file-open control sends no `path`, the operator sees a raw Python TypeError in a pop-up, on the card and in the tray. (WDG-5) **verified** | `renderFileOpen` collects a path (file input → upload, or a path field for localhost); the error, if any, is a sentence. | `agy` |
| F14 | S2 | tk, qt, web | Error events and "Fault reason" are drawn in signal red at 3.21:1 (window) / 2.73:1 (panel), below 4.5:1; severity is colour-only on Web and Tk. (DS-2, AUD-5, UXPM-4, UXPM-10) **verified** (ratios) | `SEVERITY_INK = {info: MUTED, warning: TRACE, error: TEXT}` + `SEVERITY_MARK = {error: SIGNAL}` in theme.py; a word or glyph beside the colour. | direct (theme) + `router` per view |
| F15 | S2 | all | Dropdowns truncate the part of a port or gamepad name that tells devices apart ("/dev/cu.usbmo"; 50 gamepads read the same); a 60-char Run ID overprints the next column and forces sideways scroll. (HC) | Elide from the middle, full name as tooltip; Run ID wraps or elides. | `agy` |
| F16 | S2 | all (core) | The analysis figure: matplotlib pure blue at 1.55:1 with a marker per sample (a ribbon at 1500 samples; `palette.ACCENT` declared for it and unused), unscaled in Qt (only the middle band visible), 0.48× on Web (6.7 px text), full size in Tk; re-rendered every second at 214 ms and the whole series re-sent. 3D panes light grey, coolwarm red. (UXPM-2/3/8/9/13/14) | `plot_data.py`: trace line, no markers past N samples, figure sized to the slot, dark panes; `red_monitor.py`: cache the figure until the data changes; views scale to fit. | `agy` (core, lead reviews bytes untouched) |
| F17 | S2 | tk | The clear-the-latch confirmation defaults to Yes in Tk (Return clears the latch) and on Web; Qt defaults to No. (AUD-6, UXPM-15) | Default No everywhere; Escape = No. | `router` |
| F18 | S2 | all (core) | A hung scan leaves Launch disabled with no reason and no cancel; Qt's Refresh freezes the window ~1 s. (HC) | Scan status sentence with elapsed time and a Cancel; Refresh off the UI thread. | `agy` (core `setup.py`) |
| F19 | S2 | all (core copy) | Failure messages show command names and Python bytes (`step failed: … b'1,1,1,…' was not sent`); empty-reason faults come up blank; warnings carry firmware jargon ("SERIAL-10 … D-7"). (HC, WDG) | Operator sentences: what happened, what to do; the technical line goes to the file log only. | `router patch-plan` → direct |
| F20 | S2 | all (core copy) | The stop goes by five names (FULL STOP, latch, Release, Stop, Clear); confirm buttons differ (Yes/No vs OK/Cancel); per-model stops all have the accessible name "Stop". (HC, WDG-4, UXPM-16) | One vocabulary: the object is "Stop", its latched state "Stopped", the action "Clear"; accessible names carry the model. | direct (schema + views) |
| F21 | S2 | tk, qt, web | Repaint cost: idle Qt makes 94–154 stylesheet calls/s, Tk rebuilds its stop discs 24×/s, the hidden Setup keeps ticking in both; Web rebuilds the Reopen row every poll. (AUD-9, WDG-3) | Diff before restyle; stop ticking hidden panels. | `agy` |
| F22 | S2 | qt, web | Six models: Qt docks become ~100 px slivers (Red Percent needs sideways scroll even at 1920 px); the Web rail leaves 242 px for modules at 900×600. (AUD-7, HC) | A layout strategy for many models: tabs beyond N docks, rail readouts collapse to one per model. | design → `agy` |
| F23 | S3 | theme | Token architecture is not three-layer: hairline derived seven ways (1.29–2.33:1), five colour literals in theme.py outside the six tokens, no type-scale or spacing-scale tokens (Web 12/14/16/18/24 px, Qt 10/12/14/17 pt, Tk 11/12/14/15 px; 27 spacing values in styles.css, 23 `GAP+2`-style sums in Tk/Qt), control states differ per view, disabled text 2.75:1, Tk outlines danger commands in signal while the others render neutral, at-rest values muted only in Qt, `mix()` helpers read their amount in opposite directions. (DS-3…DS-12) | The audit's token proposal: `RULE/RULE_STRONG`, `TYPE_RATIO` + `size(step)`, `SPACE` scale, `WELL/LIFT/BUTTON_*/INPUT_*`, `command_colors()`, `DISABLED` fg #767f8b, one `mix()`; then each view consumes them. | direct (theme) → `agy` per view |
| F24 | S3 | web | Trace used for words and identifiers ("off", "Not set", the Run ID); mushroom face text 4.15:1; stale card labels 3.15:1; region picker rectangle in signal red; nothing warns before closing the tab while active. (CRIT-6/7, UXPM-11/12, WDG) | Trace for numbers and lamps only; face text weight/size up or ink; stale ≥ 4.5:1; picker in trace; `beforeunload` while active. | `router` |
| F25 | S3 | tk, qt | Focus indicators 1 px / invisible on tabs and scroll areas; input borders 1.67–1.88:1; rescan 24×20 px, dock close ~10 px, lamps fixed 16 px; a reopened model is not brought forward; the latch pulse has no reduced-motion setting; at 28 pt the rail+tray take 246 of 700 px. (AUD-8/10/11/12/13/14) | 2 px ink focus ring; borders ≥ 3:1; targets ≥ 24 px scaled with font; select/raise on reopen; a `--no-motion` flag. | `router` |
| F26 | S3 | all | The heater's empty plot says "red % is plotted here"; no live plot shows a numeric scale; Qt draws the live line in ink. (UXPM-7/8/9) | Empty text per model; axis scale; trace line. | `router` |

**Owner decisions surfaced by the audits** (not routed): (a) signal on panel is 2.73:1 for bars, lamps and the latch ring — the brief's pinned colour vs the 3:1 non-text floor; (b) `--danger-fg #ffffff` and `palette.GRID` are a seventh and eighth colour in a six-token file; (c) the Setup drawer holds a primary action (Launch); (d) "Clear" on the big red button as a mode-error risk; (e) how "latched" should read from across the bench; (f) what the console should do when the station stops answering; (g) whether the latch should pre-disable commands (F11) — the lead recommends yes.

**Model-side items surfaced by the audits, for Tier A/D:** pressing Stop in SIM raises "Stop Not Confirmed: Rotator" (this is A1 — a portless stop is unconfirmed; the popup then triggers F1); the Qt harden run saw Step refused in autonomous mode with "cannot be changed while autonomous" when nothing had been edited — diagnose before F11 changes the gating.

**Status after the fix round (2026-09-24, lead-verified on the merged tree):** F1–F5, F7–F21, F23–F25 landed (`handoff/fix-{core,tk,qt,web}.md`). Open: **F6** (latched sentence and ring contrast — the ring is an owner call), **F22 Web part** (six-model rail at 900×600), **F26**, and the Web type scale (its rem steps do not match `theme.size()`). Follow-ups filed as E7–E11.

Batch order for Tier F: F1, F2+F4, F3, F9 (the stop path, one at a time, lead-verified) → F5, F7, F8 (desktop rail and stop) → F14, F23 (theme tokens, one worktree) → F10–F13, F15–F22 in parallel worktrees by view → F24–F26.

## Tier G — first owner session on the Web console (2026-09-25)

Owner's list from running `./run_macos.sh --web` on the Mac (log
`~/transfer-stage-runs/logs/station-20260925-164722.log`). Tk and Qt: nothing
reported yet. Every code item follows the ground rule: test first, prove the
defect against the pre-fix code. **verified** = the lead reproduced it.

| # | Sev | Views | Finding | Fix | Route |
|---|---|---|---|---|---|
| G1 | S2 | web | **Closing the browser tab spews stdlib tracebacks on the terminal.** `_StationServer` (`server.py:610`) inherits `socketserver.BaseServer.handle_error`, which prints a traceback to stderr for any exception in a handler thread. The handler speaks HTTP/1.1, so every keep-alive connection parks a thread in `readline`; when the tab closes the browser resets them all at once and each thread prints `ConnectionResetError: [Errno 54]`. **verified** (standalone repro on the venv's 3.14.7: one RST → one traceback). Nothing was wrong with the station; the noise hides anything that is. | Override `handle_error` on `_StationServer`: `ConnectionResetError` / `BrokenPipeError` / `ConnectionAbortedError` → `events.debug("Client Went Away", …, every=1.0)`; any other exception → `events.error(…, exception=exc)` to the file log and tray, never stderr. Second line: `ApiHandler.timeout = 120` so an idle keep-alive thread retires quietly (`handle_one_request` already treats a timeout as close-connection) instead of living until the browser drops it. Tests: `test_a_reset_connection_is_not_a_traceback` (send one request, close with SO_LINGER 0, assert stderr empty and one debug event) and `test_a_handler_crash_is_an_error_event`. | `agy` (with G2, one Web worktree) |
| G2 | S2 | web | **No way to quit the program from the console** (was D15). Closing the tab leaves the process holding the serial ports; the watchdog latches FULL STOP but never exits. Tk has ⌘Q / window close, Qt has window close; Web has nothing. | **Design (lead):** a `Quit` ghost control on the rail's side, beside Setup; confirm dialog below the rail with Cancel as the default (F17), text "Stops every model, closes every port and exits the program."; `POST /api/quit` answers `{"status":"ok"}` first and then sets `WebView._halt`, so `wait()` returns → `close()` → `Controller.close()` (which estops everything before it closes anything) → `launch()` returns → the process exits 0 and atexit closes SDL. Client: stop both timers, disarm `beforeunload`, link state reads "The station has shut down. You can close this tab." Quit is allowed while active because the close path stops first. Closing the tab still does NOT quit: the server stays up so a reopened tab finds the station (the watchdog remains the guard). Tests: server `test_quit_answers_then_releases_wait` (wait() returns < 1 s, controller closed, second POST answers 410 or is idempotent); client (headless Chrome) `test_quit_asks_first_then_the_page_says_the_station_is_down`. | direct design (above) → `agy` (with G1) |
| G3 | S2 | all (core Setup) | **The "Selected:" readout is one long string** (`setup.py:1108`: `"Stepper Probe (/dev/cu.X), DC Probe (simulated), …"`) sitting in the Launch row of every view. `main` shows the selection as a checkbox per device that enables that device's dropdowns (`mainGUI.py:245`). | **Owner call, lead recommends A.** **A:** amend the Setup ruling to "a checkbox and a Port dropdown per row": new element `sch.checkbox(text, model_attr, command)` (Tk `Checkbutton`, Qt `QCheckBox`, Web `input[type=checkbox]`; the conformance test makes all three land together); unchecked = the row is off and its dropdowns are disabled; checking restores the row's last port, else the detected port, else SIM; the dropdown lists SIM and ports only (no Off); auto-assign ticks the row whose board it finds; `is_chosen` stays the operator's claim. The `Selected:` readout goes; the Launch row keeps one sentence only while scanning or when nothing is ticked (F18 text). **B (fallback, no ruling change):** delete the readout, keep the per-row Status cell as the only summary; two lines in `setup.py` plus one test. | A: direct (lead: `schema.py`, `setup.py`, `tests/test_setup.py`, `test_core_schema.py`) → `agy` per view for the renderer (three worktrees, `parallel-stage`). B: `router patch-plan` → direct |
| G4 | S3 | all | **The Gamepad Log is a persistent panel element** (`probe.py:1074`, a `log_stream` in System Control) on every probe card. Owner wants it behind a button that opens it on demand. | `sch.log_stream(…, detached=True)` (one flag in `schema.py`, set on the probe's element): a renderer draws a `Gamepad log…` button in the element's place; pressing it opens ONE non-modal window holding the feed — Tk `Toplevel` (pattern at `tk.py:736`), Qt non-modal `QDialog` parented to the main window, Web a floating panel that sits BELOW the rail's z-index (never an `overlay` scrim: F1), Escape closes it, focus returns to the button (F12). Reopen raises the existing window; the stream's data poll runs only while it is open. Tests per view: the button renders instead of the feed, opens, shows the source command's lines, closes, and the stop stays reachable while it is open. | flag + probe line: direct (lead) → `agy` per view, in the same three worktrees as G3 |

| G5 | S2 | all | **macOS-only shortcuts and commands** (owner ruling 2026-09-25: the app is multiplatform, so no view may carry a shortcut or command that exists on one OS only). Known: the ⌘. stop chord beside Ctrl+. (Tier F9 landed both), Tk's `::tk::mac::Quit` hook (`tk.py:2978`), and any `darwin` branch that changes what the operator sees or presses. The round-4 auditor lists them all. | One chord everywhere: Ctrl+. for the stop; window close is the quit (plus G2's Quit control on Web); remove the ⌘ variants and the mac Quit hook, and any tooltip or copy that names ⌘. Test: no `Command`/`Meta`/`⌘`/`tk::mac` binding in any view; Ctrl+. presses the stop in all three. | `router patch-plan` → per-view agents (same worktrees as G3/G4) |

**Owner ruling (2026-09-25 evening): three tiers of prominence.** Common
controls are always visible: a device's X/Y/Z position and its speeds, the
speed as a **slider in addition to an entry field** (both). Less common
settings (step sizes, targets, brakes, gamepad choice) stay one disclosure
away. The gamepad log is a **debug-tier** resource. Specialised features are
on demand, never implied: Red Percent's statistics and live plotting appear
only when requested. Design direction: **C (Control sheet) with A's circular
stop**; the lead's refinement is canvas row E (`handoff/design-Tiered.md`).

**Owner ruling (2026-09-25): no platform-specific UI.** Shortcuts, menu
commands and copy are the same on macOS, Windows and Linux. A platform
branch is allowed only where the toolkit forces one (Qt plugin unhiding,
screen-capture backends), never where the operator would notice.

**Side finding from the same log** (not on the owner's list): at shutdown the
SIM Rotator reported its stop as unconfirmed and the Controller logged
`ERROR Stop Not Confirmed: … Rotator. Treat them as live.` for a stage that
does not exist. This is A1 (what a portless / no-stage stop means); it makes
every SIM quit end on a red line, so A1's decision moves up with G2.

**Decisions taken (owner, 2026-09-25):** G3 = A; closing the tab keeps the
process alive and only Quit exits; G4 Web = in-page panel.

**Status (2026-09-25, lead-verified on the merged tree `734c80f`):** G1–G6
landed from four Opus worktrees (`rb-g-web`, `rb-g-tk`, `rb-g-qt`,
`rb-g-webui`) on the lead's core commit `0cf11c8`. Gates: fast **1759**,
golden 78, Qt **142**. Lead launches: five reset connections → zero stderr
lines; `/api/quit` → exit 0. On-screen after-captures
`handoff/shots/round4_{tk,qt}_after_*.png`, `round4_web_*.png`. Handoffs
`handoff/fix-g-{web,tk,qt,webui}.md`. Left open from the round: **G6 part 2**
(a tab opened after an unconfirmed stop never sees the line: `/api/state`
should carry the unconfirmed models, a `controller.py` change, lead), and
the H10 follow-ups below.

| G6 | S1 | web | (audit IMP-0) The rail's "Stop latched, but X has not confirmed it" line survived a clear from any client. **Landed** with G3–G5: the line follows the latch from the poll. Part 2 open (above). | | done / lead |
| H10 | S3 | tk, qt | Seen on the after-captures: the Qt tick box is a filled light square with no check glyph (ticked vs unticked reads as filled vs outlined; Tk draws an ×); the Tk and Qt Gamepad log windows open as an empty box with no empty-state sentence (Web has one, IMP-9); the Qt log dialog opens near the bottom screen edge. | Check glyph or ink tick mark in the Qt indicator; the Web empty sentence in both desktop feeds; clamp the dialog inside the screen. | `router patch-plan` → per view |

Batch order for Tier G: `rb-g-web` (G1+G2; write set `src/views/web/**`,
`tests/test_view_web_*.py`) first, lead verifies with a launch: open a tab,
close it, terminal stays clean; press Quit, process exits 0, log ends with
`Server Stopped`. Then the lead lands the core of G3 and G4 directly on
`mvc-refactor` (schema element, Setup semantics, probe flag, core tests).
Then three view worktrees in parallel (`rb-g-tk`, `rb-g-qt`, `rb-g-webui`),
each owning one renderer file set, for the G3 checkbox and the G4 window.
Gates per merge (`verify`): fast ≥ 1675, golden 78, Qt 127 (lead), and the
screenshot ritual for the view touched.

## Tier H — UI audit round 4 (2026-09-25): first audit on a real display

One `ui-auditor` (Impeccable `audit` + `critique`), Tk and Qt as real Aqua
windows, Web in a headed Chrome, Retina 2×, 1400×900 / 900×900 / 28 pt, 52
captures (`handoff/shots/round4_*`, report `handoff/audit-ui-round4.md`).
IMP-0 became **G6** (Web, with the G3–G5 renderer batch). IMP-8 and IMP-9
are G3 and G4 before-measurements. IMP-16 is E4. Confirmed on screen: F1,
F5, F7, F8 hold; E1, F26 and F22 (Web) are still open. Platform-specific
items P1–P8 are **G5** (P8 is D-9, owner).

| # | Sev | Views | Finding | Fix | Route |
|---|---|---|---|---|---|
| H1 | S1 | tk, web | **Latched reads only as the face word "Clear" and a ring** in Tk and Web; Qt says "Stopped". F6's sentence half, now measured on screen (ring on face 2.76:1). (IMP-1) | Tk stop bar and Web link/rail line: "Stopped: every model latched", as Qt has it. Ring colour stays F6 (owner). | `router patch-plan` → per view |
| H2 | S2 | tk | **28 pt clips controls and numbers**: "Enter manual mode" → "ıanua", Step off the card (IMP-3); velocity readout end-elided to "0.0, 0.0,…" (IMP-4); alert text runs under Acknowledge, `wraplength=720` fixed (IMP-5). | Wrap the action group; never elide a numeric readout (F5's rule, Tk side); wraplength from the band's width on `<Configure>`. | `agy` (Tk) |
| H3 | S2 | qt | **Red Percent dock clips live numbers** at 1400 and 1000×700 ("Current red 0."), tab titles elide to "R…" (IMP-6); rail captions elide to stubs ("Curre… 0.00") (IMP-7); latched toggles look enabled beside a greyed Step (IMP-15); Setup reopen grows the window by 67 pt at 28 pt (IMP-12). | One-column fallback for Red Percent under ~600 px; tab titles ≥ 8 chars or middle-elided; shed a readout before its caption falls under 6 chars; verify toggle disabled paint; Setup dock in a capped scroll area. | `agy` (Qt) |
| H4 | S2 | web | **Rail is 428 of 900 px with six models** (48 %); latched adds 60 px; the reopened drawer's Launch row is below the fold. F22's Web part, measured. (IMP-2) | Below ~1100 px collapse each group to its first readout or one `name value` line, as Qt sheds. | design → `agy` (Web) |
| H5 | S3 | tk, qt | Status words drawn in trace (Setup "ready", "simulated", the heater's "Simulated"); F24 fixed Web only. (IMP-10) | `readoutKind` / quiet words into `views/base.py` (fix-web CCR 3), both desktop views consume them. | direct (base) → `router` per view |
| H6 | S3 | all | Three product names ("Transfer Station" / "Transfer Stage" / "Transfer stage"); Tk's Aqua app menu reads "Python". (IMP-11) | One title constant in `views/base.py`; the app-menu name is a launcher/bundle matter (S4). | `router` |
| H7 | S3 | all | One unconfirmed stop raises three notices on Web (rail line, ack dialog, tray) and Tk/Qt show only the raw event with no next step; screen readers hear it twice. (IMP-17) | One authored sentence per view; skip the ack dialog when the rail line carries the event. Moves with A1. | with A1 → `agy` |
| H8 | S3 | web, qt | A freshly launched SIM Rotator reads "Stale" / "Readings are stale", Motion "Disconnected"; Tk shows nothing for the same state. (IMP-13) | Model-side: what a portless stage reports (A1 family). Views are consistent with the state given. | with A1 (lead) |
| H9 | S3 | tk, qt | Analysis image and heater plot with no scale and no empty state on screen. (IMP-14) | E1 and F26 as planned; confirmed only. | E1 / F26 |

**Owner decisions from this round:** (a) **P3 / G5**: Tk's `::tk::mac::Quit`
hook. Aqua gives every Tk app a Quit menu item and ⌘Q whether or not the app
binds them; the Tk agent measured that without the hook the process exits
past `close()` and atexit (nothing stopped, no port closed). The hook adds
no binding, no menu item and no text on any platform. The lead's reading:
it is the "toolkit forces it" exception and stays, renamed `_hook_os_quit`.
Overrule if you want it gone, and the forced Quit then needs its own safe
shutdown path first. (b) **P8**: **resolved 2026-09-25**: D-9 amended, Tk is the default view on
every OS ("simple and lightweight and local"); `pick_view` has no platform
branch.

## Tier I — UI audit round 5 (2026-09-25, UI UX Pro Max lens, real display): regressions from the Tier G renderers

Report `handoff/audit-ui-round5.md`, 45 captures `handoff/shots/round5_*`.
Everything here is on the tree after Tier G landed (`79d04f5`).

| # | Sev | Views | Finding | Fix | Route |
|---|---|---|---|---|---|
| I1 | S1 | tk | **The Gamepad log window opens over the probe's own Safety column**: a fixed 520×300 Toplevel at the station window's top right covers the probe's Stop disc, Fault lamp, Step, its own opener and every Configuration value at 1400; the live X/Y/Z at 900. (UXPM5-1) | Place the window beside the station window, clamped to the screen, never over the panel that owns it; remember the last position. | `agy` (Tk) |
| I2 | S2 | tk | The "Gamepad log…" opener shows 47 of 109 px at 12 pt and is off the panel at 28 pt; Setup Status words cut to "…" and the Launch row scrolled out of view at 28 pt. (UXPM5-2, UXPM5-5; H2's family) | Wrap the action group; never clip a button's caption; Setup rows wrap or the panel scrolls with Launch pinned. | with I1 |
| I3 | S2 | web | The Gamepad log panel is pinned top right, so one probe's log covers the next card's inputs (35 % of the rack at 1400, 54 % at 900) and the other probe's opener. (UXPM5-3) | Anchor the panel to its opener's card (below or beside it), clamp inside the rack, never over another card's controls; one open panel at a time. | `agy` (Web) |
| I4 | S2 | all (core) | The "N devices ticked to launch." sentence is always shown, in trace, contrary to G3's design (a sentence only while scanning or when nothing is ticked). (UXPM5 Tier G) **Done 2026-09-25 (lead):** the readout leaves the Launch row; the scan line carries the F18 reason while scanning; a Launch with nothing ticked is refused with the reason. | | done |
| I5 | S2 | web | After Quit the page still looks like a latched live station: red Clear disc, the "Treat it as live" line with a working Dismiss, the raw error in the tray; the only sign is 14 px muted text; focus falls to body. (UXPM5-4) | A quit end-state: rail sentence "The station has shut down. You can close this tab." at readout size, stop disc drawn inert (no red), alert lines cleared, focus on the sentence. | with I3 |
| I6 | S3 | all | The tick mark differs per view (amber ✓ Web, ink × Tk, filled square Qt). Owner: one mark, ink or trace. Stop chord written three ways; Qt's hint 1,750 px from the disc; Web's `aria-keyshortcuts` stays on a button named "Clear". (UXPM5 Tier G) | One glyph (an ink ✓) and one caption in `theme.py`; hint beside the disc in Qt; keyshortcuts follow the face. | `router` per view; mark: owner |
| I7 | S2 | tk (platform) | On a Mac, Tk's Models and Setup menus vanish from the menu bar while the log window has focus (the Toplevel has no menu, so Aqua shows the defaults). (UXPM5-6) | Give the Toplevel the same menubar, or make the log a child frame of the station window. | with I1 |

| I8 | S1 | web | **The "Treat it as live" line can be dismissed while the stop is still latched** (`app.js` passes `canDismiss` for the unconfirmed line); after Dismiss the page shows only "Connected" and "Clear" while `/api/state` is latched. (round 6 WDG6-1) | No Dismiss while the latch holds; the line leaves only with the latch or a later fully-confirmed stop. | `agy` (with I3, rb-i-web) |
| I9 | S2 | web | At 390 px with Setup open the rail's numbers collapse to 0 px and the page scrolls sideways (512 px in 390); the tick box paints over the row name. `styles.css:1361 width:100%` on a flex child. (WDG6-2) | `flex-basis: 100%`; Setup table at phone width. | with I8 |
| I10 | S2 | web (core copy) | Disabled commands never say why (ten after launch: the SIM Rotator's eight, Red Percent's Start/Stop run); accessible names drop the visible words and the model (nine toggles read "Autonomous", six dropdowns "Port"); the stop copy still says "Release the FULL STOP latch" and "Stop Cleared" in `controller.py:196,207` and `base.py:128`; spaced em dashes in core copy. (WDG6-3, S3s) | A `disabled_reason` the views show as tooltip/description (schema, lead); accessible name = visible words + model; one stop vocabulary in core copy (F20). | direct (core) → `router` per view |
| I11 | S3 | web | Polling cost: Setup fetched every 250 ms while the drawer is closed; the same 4.3 KB figure re-downloaded every second; the stop's class rewritten 4×/s (20 of 34 idle mutations in 5 s). (WDG6) | Fetch Setup only while the drawer is open or launching; ETag/If-None-Match on `/api/data` images; diff before writing the stop's class. | `agy` (Web) |

Rail height on Web grew with the Quit control: 130 → 188 px at 1400, 363 px at 900 (H4 worsened). H1–H10 all confirmed open on screen. Round-6 density (Web): 215 words on the first screen at 1400, 9 % of them live numbers; label-to-value ratio 1.29; eight size/weight pairs across five sizes; ten labels appear six times each; the rail takes 21 % of the screen at 1400, 40 % at 900, 46 % on a phone.

## Tier J — follow-ups from the Bench-sheet round (2026-09-26)

Raised by the three view agents' CORE CHANGE REQUESTS and the lead's ritual;
none blocks the merge.

| # | Sev | Views | Item | Fix | Route |
|---|---|---|---|---|---|
| J1 | S2 | tk, qt | Public Sans and Archivo are not installed on this Mac, so the desktop views render Helvetica (E4 again, now with the chosen faces). | Ship the fonts: register the bundled woff2/ttf at startup (Tk 9 `font create` cannot load a file; Qt `QFontDatabase.addApplicationFont` can) or install on the station PC; packaging P3 carries them. | owner (install) + `agy` (Qt loader) |
| J2 | S3 | all | The heater's reading is the word "Simulated" in SIM, drawn at reading size; a word sits where the number goes. | Model-side: SIM reports a plausible number and the sim state is a quiet status, not the reading (A1 family). | direct (model) |
| J3 | S3 | all | The per-model stop switch shows the schema's "Stop"/"Stopped"; the artboard says "Stop this model only". | Decide the caption once in `base._safety_section`. | owner → `router` |
| J4 | S3 | tk, qt | Slider track/thumb sizes are hard-coded per view; a `theme.SLIDER` member would keep the three equal. | Add `SLIDER = {"track": 4, "thumb": 18, "halo": 2}` and consume it. | direct (theme) → `router` per view |
| J5 | S3 | qt | The theme's pixel sizes (readings, captions, disc) meet a point-based base font; Qt converted through the screen DPI. `theme.py` should state the unit once. | `FONT_SIZE` in px with a pt conversion helper, or the reverse; one rule for three views. | direct (theme) |
| J6 | S3 | all | The schema cannot say which tier-1 elements a closed (compact) entry keeps, so compact probes show both speeds and the toggles. | A `compact=True` flag on tier-1 elements, or a per-model `rail` set; renderers show only those when the entry is not the opened one. | direct (schema) → per view |
| J7 | S4 | qt | `base._make_section` receives title and layout only; the tier lives on the section dict. | Hand the section dict to `_make_section` (base change; both desktop views adapt). | direct (base) |

## Tier K — the owner's first pass over the Bench sheet (2026-09-26)

Four rulings from the owner's look at the merged E views, plus a study.
K1–K2 are core (landed with this tier's first commit); K3–K4 are one
worktree per view; K5 is a proposal, not code.

| # | Sev | Views | Item | Fix | Route |
|---|---|---|---|---|---|
| K1 | S2 | all (core) | The gamepad choice sat behind Configure; it is picked every session. | `probe.schema`: the Gamepad dropdown is tier 1, first in System Control, ahead of the Manual toggle. Views re-render from the schema. | direct — done |
| K2 | S2 | all (core) | "Configure" / "Details" did not say what they opened. | The tier-2 `disclosure` names the device: "Configure Stepper Probe", "Configure DC Probe", "Configure Chuck Positioner", "Configure Temperature Controller", "Configure Rotator", "Red Percent details". `theme.TIER_LABELS` stays the default for a schema that says nothing. | direct — done |
| K3 | S2 | web, tk, qt | The disclosure sat in the entry's head (top right) while the well it opens appeared under the whole tier-1 body: the press and its effect were a screen apart. | The disclosure moves to the foot of the tier-1 body, left-aligned, directly above its well; the head's right side keeps only the state words and the close button. | worktree per view — done (`fix-k-{web,tk,qt}.md`, merged 34c6b1b) |
| K4 | S1 | web, tk, qt | No overview. The sheet led with one opened model full width and the others underneath; the owner wants a summary of every active device first, and one press to bring a device up alone. | Two pages on the sheet. **Overview** (the rail's first item; shown at launch and whenever the shown device closes): every launched model as a compact entry in the existing grid, tier-1 body only, no wells; each entry's head is a press target that opens the device. **Device page**: that model alone, full width, focal readings, tiers 2 and 3 available, their open state remembered per model for the session. The rail highlights the shown page; the stop disc, the latched headline and the tray are the same on both. | worktree per view — done (`k_{web,tk,qt}_*` captures; Tk also fixed a real-Tk wrap oscillation on the overview) |
| K5 | — | model + firmware | A zeroing / centring routine for the stepper probe with no limit switches ("global zero" at the start of a session). | A proposal first: `handoff/proposal-probe-zeroing.md` (driver, stall detection vs current sensing vs bump homing vs switches vs vision vs software zero; safety; owner decisions). Any firmware or wire change is an owner decision. | done: proposal written, datasheet claims verified by the lead → owner |

Assumption stated for K4 (owner may pare it down): the overview keeps each
device's whole tier-1 body (speed slider and entry, mode toggles, Step),
because speed is the control the owner named as commonly adjusted; a
readings-only overview is a one-line change to the compact entry once J6's
`compact` flag exists.

## Tier L — UI audit round 7 (2026-09-26): first full analysis per view on the real display

Three read-only auditors, one per view, Impeccable critique + a11y lens,
after E landed and while Tier K was in flight (K1–K4 excluded from scope).
Reports: `handoff/audit-ui-round7-{web,tk,qt}.md` (16 / 18 / 19 findings);
captures `handoff/shots/round7_*`. Merged here by theme; the source IDs are
in the Item column. Core parts are the lead's and land first; view parts go
one worktree per view after Tier K merges (the same files).

| # | Sev | Views | Item (source) | Fix | Route |
|---|---|---|---|---|---|
| L1 | S1 | all | **One model's own stop reads as "every model is stopped"**, the disc turns to "Clear" and can no longer stop the five live models (IMP7-1, IMP7-2); a stop that did not confirm is contradicted by the headline "Every model is stopped." and its mark is below the fold (IMP7-3, TK7-1, QT7-1). | Core, landed: `Controller.stop_state` = {latched, unconfirmed, every} in `state()["stop"]`; `Model.stop_confirmed` in state; `views.base.stop_words()` gives the disc face/action, headline, subline and rail line for every case (Clear only when every model is latched; "Stopped. Rotator did not confirm." + "Treat it as live until you have checked it by hand." when one did not; "Stopped: Stepper Probe" for a partial stop). Views: consume `stop_words`; the disc's press is `estop_all` unless `action == "clear"`; the rail's model list marks latched (square) and unconfirmed (signal square + "did not confirm") models; "Stop: Ctrl+." stays visible in every state (the chord always stops); the Web server serves `stop_words` in `/api/state`. | core done → worktree per view |
| L2 | S2 | all | The "Stop Not Confirmed" line outlives the latch: tray / band still say it after Clear and on reload (IMP7-5, TK7-2, QT7-12). | When the latch opens, drop that event from the tray/band; on boot show only events newer than the current latch. | worktree per view |
| L3 | S2 | all | Disabled commands never say why; Red Percent's Start run is greyed from launch and its precondition (a capture region) is two tiers down (IMP7-7, TK7-4, QT7-6). | Core: Red Percent publishes a tier-1 quiet status line while `no_region` ("Set a capture region under Red Percent details to start a run."). Views: a disabled command carries the gate's reason as tooltip/title and, for `go` buttons, one muted caption under the row. | core (model) + worktree per view |
| L4 | S2 | all | Targets: entries 16–32 px, sliders 18–24, commands 22–36; only the disc meets 44 (IMP7-8, TK7-6, QT7-5). | Floor now: every pressable ≥ 24 px (WCAG 2.5.8), commands ≥ 36 px, the well/ring of an entry is part of its target, Setup's tick and row name share one target. 44 px everywhere is an owner call (it conflicts with the artboards' compact commands). | worktree per view; owner for 44 |
| L5 | S2 | tk, qt | The opened model's tier 1 scrolls away with its well open or when latched (TK7-3, QT7-15); "Tier 1 never scrolls away" is the brief. | On the device page (K4) the head and tier-1 body sit above the scroll area; only the well scrolls. | worktree per view (after K4) |
| L6 | S2 | all | Slider keyboard: End commits the maximum speed in one key; every arrow release commits 1 step/s (TK7-5; the Web range and QSlider behave the same way). | Keyboard step = 1 % of the travel; Home/End do nothing; commit on release as today. | worktree per view |
| L7 | S2 | qt | On macOS Tab never reaches a button: not the disc, the rail, Step, the toggles, Quit or Acknowledge (QT7-4). | Buttons take `StrongFocus` (or the style hint that lets Tab reach them); test: Tab from the sheet reaches the disc. | worktree qt |
| L8 | S2 | qt | Setup's dock is capped at 55 % and hides Launch below its own fold; at 900 px a sideways bar covers the Launch row (QT7-3). | Let the dock take the height it needs while the sheet is empty; no sideways scroll at 900. | worktree qt |
| L9 | S2 | qt | Quit exits without asking; Tk and Web confirm (QT7-7). | Route Quit and the window's close through the same confirmation as Tk/Web. Not P3. | worktree qt |
| L10 | S2 | web | The Rotator's angle is not drawn when unknown, so a tier-1 position disappears (IMP7-6; fix-e-web kept it on purpose). | Ruling: a `rail: true` tier-1 reading is never hidden; unknown draws "--" muted at reading size (TK7-14's objection to the glyph is noted and kept). | worktree web (+ check tk/qt) |
| L11 | S3 | all | Event lines are raw log text: bracketed source, Title Case, "FULL STOP" (IMP7-4, TK7-11, QT7-12). Core copy was changed with L1 (sentence case, the model named first, "Treat it as live"). | Views render title + message in sentence case without the `[source]` prefix; the acknowledgement dialog too. | worktree per view |
| L12 | S3 | tk, qt | The gamepad log window opens blank: no empty state, no Close; Tk's title has a spaced em dash (TK7-15, QT7-10, UXPM5-13). | "No gamepad input yet." + a Close button; title "Stepper Probe gamepad log". | worktree tk, qt |
| L13 | S3 | tk | No visible way to close a model (TK7-16). | "Close this model…" in the well foot, as Web. | worktree tk |
| L14 | S3 | tk, qt | Confirmations answer "Yes / No" under "Confirm"; the Clear dialog does not name the unconfirmed model (TK7-10, QT7-13; the naming is L1 core, done). | Titled dialogs with verb buttons ("Clear the stop" / "Keep it stopped"; "Quit" / "Stay"). | worktree tk, qt |
| L15 | S3 | all | Red Percent details stack two empty plot panes (600–700 px of "no data") before Diagnostics (TK7-13, QT7-14, IMP7-13 part). | An empty plot/figure pane is one caption line tall until it has data. | worktree per view |
| L16 | S3 | all | Accessible names drop the visible words; three rescan buttons share one name (IMP7-10, QT7-11). | name = face text + model; unique names per row. | worktree per view |
| L17 | S3 | web (+core) | Setup: "Stop system" is a third stop word; lowercase row statuses; an orphan disabled "Cancel scan"; Relaunch is the filled primary (IMP7-12). | Core: "Stop system" → "Close every model". Web: sentence-case statuses; Cancel scan only while scanning. | core + worktree web |
| L18 | S3 | web, tk | A tier-2 well repeats its disclosure as a heading; Diagnostics opens onto a "Diagnostics" heading (IMP7-13, TK7-17). | Skip a section title that equals the disclosure's words. | worktree per view |
| L19 | S3 | qt | Run ID drawn in trace and ticking every second (QT7-8); "Hide events" becomes a full-width bar (QT7-9); Setup's close is an 8 px glyph and the rail's Setup does not show it is open (QT7-16). | Per finding. | worktree qt |
| L20 | S3 | tk | Tab order: disc, Setup, Quit, models, tray, a hidden tab strip, then the sheet (TK7-7); unequal sheet columns (TK7-8); captions at different heights in a row (TK7-9). | Per finding. | worktree tk |
| L21 | S3 | web | After Quit the tray goes translucent and paints over the sheet (IMP7-9); the warning mark reads as an empty checkbox and a port no model uses holds the tray from boot (IMP7-11). | Per finding. | worktree web |
| L22 | S4 | all (+core) | The heater plot's empty state talks about red % (IMP7-14); heater unit "C" vs "°C" (IMP7-15, J2 family); copy nits (QT7-17); rail hover 1.1:1 (QT7-18); wrapped tray line under its mark (QT7-19); focus nits (IMP7-16). | Core: neutral empty-state default for `plot`; heater unit. Views: per finding. | core + worktree per view |

**Status (2026-09-26, merged 1eafb59):** L1–L22 landed in all three views
(`handoff/fix-l-{web,tk,qt}.md`); core follow-ups from the agents landed
too (`events.forget` on clear, `stop_state["since"]`, `Dashboard.toggle_estop_all`
via `stop_words`, `views.base.event_line`, `schema.image(empty=)`, the scan's
unanswered ports as info). Deferred until the display is free (owner: strictly
background while working): the 11 `window`-marked real-Tk tests and the
`l_{tk,qt}_*` captures; Web's `l_web_*` captures exist (headless). Not done,
carried to Tier M: see below.

Not defects (checked by the lead): the Rotator's own "Stop motion" is gated
on a lost link only (`rotator.py:194-211`), not on staleness; a halt cannot
reach a device that is not connected, and the disc still latches it.

Owner calls from this round: (1) 44 px targets everywhere vs the
artboards' compact commands (L4 ships the 24/36 floors); (2) the slider
track and entry well at 1.11:1 against the sheet (WCAG 1.4.11 wants 3:1 on
a control's boundary; the brief draws them so); (3) whether a partial latch
should ever be clearable from the disc (L1 ships: the disc clears only when
every model is latched; a single model clears at its own switch).

## Tier M — carried from Tier L (2026-09-26)

| # | Sev | Views | Item | Fix | Route |
|---|---|---|---|---|---|
| M1 | S3 | all | The 11 `window` tests and the Tk/Qt Tier L captures were not run on a display (owner working). | **Done**: window tests 11 passed on the display; `l_{tk,qt}_{partial_stop,unconfirmed,cleared,disabled_reason}_1400x900.png` captured on the built-in display by window id (harness `scratchpad/lead/lshots/cap_{tk,qt}.py`) and reviewed by the lead. | done |
| M2 | S3 | core | `red_monitor.run_id` is a fresh timestamp every second while idle, so the readout ticks (QT7-8). | One slug per idle period: mint it when a run ends / at open, keep it until a run starts or the name changes. | direct (model) |
| M3 | S3 | core + views | The L3 gate words (mode → sentence) live in each view; Qt added "In manual mode", "No run in progress", "Nothing launched yet". | `views.base.gate_reason(mode)` as the one table; Web serves it. **Done**: O3 landed it. | done |
| M4 | S4 | qt, tk | Rail hover tone needs a theme colour (QT7-18). | `theme.RAIL_LIFT = mix(SURFACE, TEXT, 0.08)`, consumed by Tk and Qt. | direct (theme) |
| M5 | S4 | qt, tk | Red Percent's "Next step" line is cut in the compact entry (Qt at 28 characters, Tk with an ellipsis); full on the device page. | Wrap the readonly's text in the compact entry. **Done** with O16's wrap. | done |
| M6 | S4 | web | Red Percent tier-1 caption misalignment and the "Shut down" wrap (IMP7-15 parts). | Per finding. | worktree web |
| M8 | S4 | tk | After a partial stop the same "Error: Stop not confirmed …" line is drawn twice: once in the acknowledgement band and once as the tray's latest line (`l_tk_partial_stop`). | The tray's latest line yields while the band shows the same event. **Done** with O13: the tray's latest line yields. | done |
| M9 | bench | dc | In SIM under Tk the DC Probe's stop came back unconfirmed on both runs ("Power down not supported", D-7 family) while under Qt it confirmed: the 1 s budget is timing-sensitive when the Tk loop is busy. Not a UI defect; a bench observation for D-7. | note under Tier B / D-7 | owner |
| M7 | S4 | all | Qt lists Setup status words with a capital; Tk/Web sentence-case them client-side; the model could publish them capitalised once. | `setup.py` status words in sentence case. | direct (core) |

## Tier N — timeouts an operator can see coming (owner, 2026-09-26)

Owner: a Web popup that warns against closing the tab without disabling
devices; validate the server's behaviour when the client disconnects (the
browser-liveness watchdog); validate the idle timeout across all views, with
a popup that warns of the incoming timeout and offers to extend. Spec for the
views: `handoff/brief-n-views.md`.

| # | Sev | Views | Item | Fix | Route |
|---|---|---|---|---|---|
| N0 | — | web (server) | Validate the browser-liveness watchdog end to end. | **Done, headless** (`scratchpad/lead/watchdog/validate.py`): heater heating, heartbeat, then silence: "Browser Silent" at 4.9 s, FULL STOP with every model latched at 15.2 s (WARN 5 s / STOP 15 s / poll 0.5 s); `/api/state` polls are not heartbeats by design. Existing tests `test_view_web_watchdog.py` cover the rules. | done |
| N1 | S2 | core | The idle interlock (300 s) disabled the motors with no warning first and no way to keep them awake short of moving. | **Core, landed**: `Probe.idle_remaining` + `idle_warn_seconds` in state; one "Idle Timeout Soon" warning inside the last `IDLE_WARN_SECONDS` (60) per idle period; `extend_idle` command (schema internal; refused when nothing is energized; ends the warning's dedupe episode). Tests in `test_probe.py`. | done |
| N2 | S2 | all | Views: a non-modal countdown line per probe inside the window, "Stepper Probe powers down in 42 s." with **Extend**; never covers the disc. | Per view from state; brief-n-views.md N1. **Done in all three views** (rb-o-{tk,qt,web}, handoffs `handoff/fix-o-{tk,qt,web}.md`). Line under the disc, station order, ceil of `idle_remaining`, Extend runs `extend_idle`; no modal, no focus change. | done |
| N3 | S2 | web | `beforeunload` armed only while `is_active`; a probe merely in a mode did not warn. | Core: `controller.state()["energized"]` (a probe in any mode, a heating heater, a recording run). Web: arm on `energized`; a rail line while energized says the station stops them 15 s after the tab goes (seconds served, not hard-coded). **Done**: Web arms `beforeunload` on `state.energized`; the rail line carries the served `watchdog.stop_seconds`. | done |
| N4 | S3 | tk, qt | The Quit / close confirmation does not say what is energized. | Name the energized models in the prompt. **Done** (Tk, Qt): "Quit the station? A and B are energized; quitting stops and disconnects them." Quit and the window close share it. Web's quit names the unconfirmed (O5). | done |
## Tier O — audit round 8 (2026-09-26): one skill per auditor, plus the architecture audit

Reports: `handoff/audit-ui-round8-{impeccable,a11y,wdg,promax}.md`,
`handoff/audit-architecture-2026-09-26.md` (verdict: intact with debt; no
layering or stop-path violation). Merged by theme. Core rows landed with the
Tier N/O core commit; view rows ride in the Tier N worktrees (same files).

| # | Sev | Views | Item (source) | Fix | Route |
|---|---|---|---|---|---|
| O1 | S1 | core | **A stop was refused while any entry on the model held bad text** (A11Y-1, PM8-1): "Stop heater" with 9999 in the box, the per-model switch with X step 0. Every entry travels with every command and `panel.run` validated them all first. | `Panel.UNGATED_COMMANDS` (toggle_estop, estop, clear_estop, halt, stop_run, extend_idle) and `set_mode("disabled")` skip input validation; the real commands still validate. Tests in heater/probe. | done (core) |
| O2 | S1 | web (server) | **Any page could run station commands** (WDG8-1): `GET /api/data?command=…` ran whatever it was given; a request with no Origin passed; no frame-blocking headers; a framed copy kept heart-beating. | `/api/data` runs only schema-declared data sources; a JSON POST without Origin/Referer is refused; Host checked on every route; `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff` on every response. Owner ruling "localhost + Origin check" now holds for every route. | done (core) |
| O3 | S1 | web, tk (+core) | **Gate words inverted** (IMP8-1, ARCH): "Not in manual mode" shown while IN manual mode; three copies of the table. | Core: `views.base.GATE_WORDS` (both directions) + `gate_reason(element, mode)`; served in `/api/theme.json`. Views: consume it, delete local tables (M3 closes). **Done in all three views** (rb-o-{tk,qt,web}, handoffs `handoff/fix-o-{tk,qt,web}.md`). Local tables deleted; Web mirrors `gate_reason` over the served `gate_words` with a case-by-case parity test. Left local: the `enabled_by` tick-box gate ("Tick Launch first"), see O20. | done |
| O4 | S1 | all | **A probe whose disable failed (FAULT) looks like a safely disabled one** (IMP8-2): normal toggles, no red rule; the fault lamp two disclosures down. | Views: `is_faulted` marks the entry like an unconfirmed stop (red rule + "Disable failed. Treat as live." + rail mark); Fault reason surfaces in tier 1 while faulted. **Done in all three views** (rb-o-{tk,qt,web}, handoffs `handoff/fix-o-{tk,qt,web}.md`). Red rule + "Disable failed. Treat as live." + the fault reason in tier 1; rail "!" mark; the schema's `fault` gate greys the mode toggles. | done |
| O5 | S1 | web | After Quit the page shows "Off" everywhere even when a stop did not confirm (IMP8-3); the server shuts before the models report. | Quit answers only after `estop_all` results are known and the end state names unconfirmed models. **Done**: `/api/quit` runs `estop_all` before it answers and returns `{status, stopped, unconfirmed}`; a second Quit does not stop again; the end state names the unconfirmed. | done |
| O6 | S1 | all | Nothing shows whether the heater is heating or to what (PM8-2, IMP8-4): a typed setpoint and a sent one look alike. | Core done: tier-1 quiet readonly "Heating to: 30.0 °C" (`Heater.heating_to`). Views: a rail mark for energized models from `state.energized`. **Done in all three views** (rb-o-{tk,qt,web}, handoffs `handoff/fix-o-{tk,qt,web}.md`). Ink ring after the stop mark from `state.energized`, never red; the name/tooltip reads "energized". | done |
| O7 | S1 | all (a11y) | Stopping and clearing are silent to a screen reader; only the unconfirmed case is spoken (A11Y-2). Qt announces nothing; Tk has no accessibility tree. | Web: the headline and rail line in a polite live region inside `<main>`, the stop/clear as assertive announcements; Qt: `QAccessible.updateAccessibility` on the same events. Tk: note the limit. **Done** (Web: polite + assertive sr-only regions in `<main>`, `aria-modal` gone; Qt: `QAccessibleAnnouncementEvent` on each stop edge and on a new fault). Tk: no accessibility tree, noted. | done |
| O8 | S2 | web (server) | The watchdog keyed on `is_active`: a probe held in autonomous mode stayed powered through a closed tab (IMP8-7); merely hiding the tab stops a heating heater after 15 s (IMP8-6). | Core done: `Controller.is_energized`; the watchdog keys on it; copy says "devices are energized". The hidden-tab rule stays (nobody is watching) — **owner call**. | done + owner |
| O9 | S2 | tk, qt, setup | Closing without asking (PM8-5/6, ARCH parity): Qt's × on an entry head; Tk's middle-click on a rail name; Tk's window close / OS Quit skip the Quit question; "Close every model" / Relaunch took energized models down on one press. | Core done: Setup's `stop_system` and `launch` ask while anything is energized (NeedsConfirm, rerun with True). Views: Qt close-model confirms; Tk window close and middle-click ask (P3 hook: owner). **Done** (Tk, Qt): Qt's × asks "Close Rotator?"; Tk's window close, middle-click and Models-menu untick ask; the OS Quit hook is unchanged (P3, owner). | done |
| O10 | S2 | web | Double press on Step sends two Steps; no busy state (WDG8-2). Mouse wheel changes a focused number box (WDG8-3). A refusal lands 141 px from its field (WDG8-4). Region picker pointer-only, no loading state (WDG8-5). | Per finding: disable while in flight; `wheel` ignored on focused inputs; refusal at the field with `aria-describedby`; keyboard region entry. **Done**: in-flight guard + `aria-busy` (stop-class exempt); wheel on a focused number box scrolls the page; the refusal lands at its field (`aria-invalid`, `aria-describedby`, focus); region picker has X/Y/W/H fields and a loading state. | done |
| O11 | S2 | qt | Tab order splits the rail around the first entry and stops on unnamed scroll areas; Overview "Open" reads as "Border"; dropdowns announced by value (A11Y-4/5). | Per finding. **Done**: one Tab order after every rebuild (disc, Extends, Overview, models, Setup, Quit, dock, entries, band, tray); scroll areas `NoFocus`; the overview head is a Button to assistive tech; dropdowns named "<caption>, <row>". | done |
| O12 | S2 | web | "Browser silent … full stop at 15s." stays the tray line after the tab returns (PM8-3). | Clear it on the next heartbeat. **Done**: a heartbeat that lands takes "Browser silent" off the tray; an event already answered by a later beat never reaches it. | done |
| O13 | S2 | all | The idle warning's tray line carries a frozen count and is never taken back after Extend or motion (PM8-4). | Tier N's countdown line renders from state; the event line is history in the log only. **Done in all three views** (rb-o-{tk,qt,web}, handoffs `handoff/fix-o-{tk,qt,web}.md`). "Idle Timeout Soon" goes to the log only; the countdown is the live line; the core message carries no number. | done |
| O14 | S2 | web | Slider drags and typed speeds are not sent until some later command runs, with no pending mark (PM8-7); Tk/Qt commit on release/Return. | Commit on release / change as the desktop views do. **Done**: release / key / Return / blur commits that one field; refusal shows at the field and the box reverts; Escape reverts. | done |
| O15 | S2 | web | Web's device page lets tier 1 scroll away (ARCH parity; L5 covered Tk/Qt). | Pin the head + tier-1 body. **Done**: `pinOpened` keeps head + tier 1 sticky when they take at most 60% of the window. | done |
| O16 | S3 | all | "Stop" names five actions and the idle timeout has three names (PM8-8); Step's distances not visible where Step is pressed (PM8-9); rail marks differ by colour only (A11Y-6); refusal mark looks like a checkbox (WDG8); log windows take focus with no ring; under 640 px the rail hides the chord hint and the stop marks. | Per finding. **Done in all three views** (rb-o-{tk,qt,web}, handoffs `handoff/fix-o-{tk,qt,web}.md`). Marks differ by shape (square, "!" square, ring); the switch reads "Stop this model" (core); log windows focus their feed inside a ring; under 640 px the hint stays. | done |
| O17 | S2 | core (tests, tk) | Tk keeps its own disc logic and a `_stop_state` fallback with the old any-latched rule (ARCH); three hand-written FakeControllers, the Tk one with that rule; 88 fixed sleeps in the browser tests; `test_architecture` no longer encodes the allowed-imports contract. | Tk defers to `Dashboard.toggle_estop_all` and deletes the fallback; one `Controller`-over-fakes fixture; `test_architecture` allow-list + no `controller._` reads from views. **Tk part done** (rb-o-tk): the disc calls `Dashboard.toggle_estop_all`, the `_stop_state` fallback is gone, the test FakeController derives `stop_state` per model. **Open (lead)**: one `Controller`-over-fakes fixture, `test_architecture` allow-list, the fixed sleeps. | partly |
| O18 | S3 | docs | STATUS points at the superseded WEB_DESIGN_BRIEF; DESIGN_BRIEF says Clear "when latched" and describes the opened-model layout; station-map lists old counts and the "Off" port option (ARCH). | Fix the three. **Done** in the Tier O docs pass. | done |
| O19 | S3 | tk | `tk.py` grew to 6,169 lines; `TkPanelView` is 3,047 lines / 141 methods (ARCH). | Split by concern after Tier N/O land; not before. | later |

| O20 | S3 | core (views/base) | `gate_reason` has no words for the `enabled_by` tick-box gate, so Tk and Qt keep one local phrase, "Tick Launch first" (Qt CCR 1). | Give `gate_reason` the `enabled_by` direction (needs `values` and a caption lookup) and delete the last local phrase. | direct (core) |
| O21 | S3 | core (result, param) | A refusal names its field only by its leading words; the Web finds the entry by matching captions (`PanelCard.fieldFor`) (Web CCR 1, WDG8-4). | `Refused` carries the offending attribute (`result.attr`); the views place it directly. | direct (core) |
| O22 | S3 | core (schema copy) | The mode toggles' aside reads "(press to stop)", so the hint says "Press to stop" where the action is powering down (Web CCR 2, PM8-8); Step's distances are not visible where Step is pressed (Web CCR 3, PM8-9). | "(press to power down)"; a `step_text` readonly beside Step ("Step: X 100 µm, Y 100 µm, Z 10 µm"); the views render both with no change. | direct (core) |
| O23 | S3 | core + all | Each view hard-codes the hazard sentence "Disable failed. Treat as live." for every faulted model, wrong for the Rotator, whose faults are motion errors (Tk CCR 3). `Panel.UNGATED_COMMANDS` is copied into the Web's `STOP_COMMANDS` (Web CCR 4). | A per-model `fault_line` in state; serve `UNGATED_COMMANDS` in `/api/theme.json`. | direct (core) → `router` per view |

Tk CCR 1, 2, 4 and 5 (the `fault` gate on the mode toggles, "Stop this model" from the base schema, no number in the idle warning, `events` title constants) landed in the Tier N/O core commit.

## Model contract (audit 2026-09-26, `handoff/audit-model-contract-2026-09-26.md`)

A `PiezoStage` model written from `MODEL_CONTRACT.md` alone was driven through all three views unedited. `tests/test_model_contract.py` (160 cases over every registered class plus a minimal one) pins the contract.

| # | Sev | Views | Item | Fix | Route |
|---|---|---|---|---|---|
| CON-1 | S1 | web | A button's own `args` were dropped, so two buttons sharing one command differed only by their press: the Rotator's "Move -" moved +. | **Done** (rb-o-web): a button's own args travel before the press's; the Rotator move test pins the sign. | done |
| CON-2 | S2 | web | The gate words were a local copy. | **Done** with O3. | done |
| CON-3 | S2 | core | The window-focus gate reached only a `gamepad` attribute. | **Done**: `set_input_focus` walks `model.devices` calling `set_gate`. | done |
| CON-4 | S2 | core | Stop-class commands were a hand list in the views. | **Done**: schema `button(stop=True)`; `Panel.UNGATED_COMMANDS`. | done |
| CON-5 | S2 | web | Downloads assumed the probe's output path. | **Done** (rb-o-web): reads `output_root` where models publish it. | done |
| CON-6 | S3 | web, tk | The simulation line counts only `SerialPort` and `SMC100` as hardware. | A `Device.is_hardware` flag; the views count it. | direct (core) → `router` per view |
| CON-7 | S3 | setup | No registration call: `MODEL_TYPES` is a tuple, the SMC100 handshake is hard-coded, a model added at runtime cannot be reopened after its entry closes. | `Setup.register(cls)`; handshake from the class; reopen from the registry. | direct (core) |
| CON-9 | S2 | core | A fault gated only in the probe. | **Done**: `Model.gate_mode` returns "fault" from `is_faulted`. | done |
| CON-11 | S3 | core | The idle clock is probe code. | Lift `idle_remaining` / `extend_idle` into a mixin on `Model`. | direct (core) |
| CON-13 | S2 | rotator (SIM) | A simulated Rotator's stop never confirms: every SIM FULL STOP reads "Rotator did not confirm". `test_model_contract` carries it as an expected failure. | Owner call: does the SIM stage answer its stop, or is the unconfirmed reading the intended rehearsal of the real SMC100? | owner |

## Out of scope here

- The second test wave (135 old files, 26 safety tests: `tests/TEST_PORTING.md`) is a programme, not a bugfix batch; it stays under STATUS.md item 3.
- Cutover (delete `legacy/`, merge to `main`) stays under STATUS.md item 4 and follows the test wave.

## Order

1. A1 decision (owner or lead) — it changes what "FULL STOP confirmed" means for the probe, so it goes first.
2. `agy` batch A1–A7, A9 in `rb-bugfix-a`; A8 directly.
3. Lead verification: fast suite, golden gate, Qt suite, screenshot ritual for the Web console with a forced watchdog fault.
4. Bench checklist (Tier B) printed for the next lab visit.
5. Tier C audits when the bench items are back.
6. Tier G (owner session 2026-09-25) in its own batch order: G1+G2 first, they are the ones an operator meets every session.
7. Tier D in the batch order above, in parallel worktrees (`parallel-stage`), after D1–D5 have landed one at a time.
8. Tier F stop-path items (F1–F4, F9) before or alongside D1–D5; the rest of Tier F by view in parallel worktrees.
