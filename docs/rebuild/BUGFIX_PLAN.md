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
| D3 | `src/devices/gamepad.py` `drain_edges`, `src/model/probe.py` | **D-pad and bumper steps do nothing.** Edges are parked for `drain_edges()`, which nothing calls. **verified** (grep: only its own docstring). Tests miss it because the fakes put D-pad values straight into the stick readings. | Consume edges in the probe's poll; test with a fake that reports edges the way the real device does. | `agy` |
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
| D15 | `src/views/web/server.py` | No quit control; closing the browser leaves the process holding the serial ports (until the watchdog latches FULL STOP, which does not exit). | Design call: a Quit command that shuts the Controller down and exits. | direct design → `agy` |
| D16 | `run_macos.sh` | Prints that it is launching Web but starts Tk. | One-line fix. | direct |
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

## Out of scope here

- The second test wave (135 old files, 26 safety tests: `tests/TEST_PORTING.md`) is a programme, not a bugfix batch; it stays under STATUS.md item 3.
- Cutover (delete `legacy/`, merge to `main`) stays under STATUS.md item 4 and follows the test wave.

## Order

1. A1 decision (owner or lead) — it changes what "FULL STOP confirmed" means for the probe, so it goes first.
2. `agy` batch A1–A7, A9 in `rb-bugfix-a`; A8 directly.
3. Lead verification: fast suite, golden gate, Qt suite, screenshot ritual for the Web console with a forced watchdog fault.
4. Bench checklist (Tier B) printed for the next lab visit.
5. Tier C audits when the bench items are back.
6. Tier D in the batch order above, in parallel worktrees (`parallel-stage`), after D1–D5 have landed one at a time.
7. Tier F stop-path items (F1–F4, F9) before or alongside D1–D5; the rest of Tier F by view in parallel worktrees.
