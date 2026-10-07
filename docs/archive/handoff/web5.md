# Web round 2

Worktree `rb-web`, branch `rb-web`, merged `mvc-refactor` (54ce0f1) first.
Write set respected: `src/views/web/static/{app.js,index.html,styles.css}`,
`tests/test_view_web_client.py`. `server.py` untouched (no route or header
was needed).

## Screenshots
`rebuild-handoff/shots/round2_web_{before,after}_*.png`, headless Chrome, SIM
ports for Stepper Probe and Red Percent, driven through the page's own API.
- `1_drawer` (1400x900): boot, Setup open. After: column captions once in a
  header row, Launch row on two lines (summary / right-aligned Launch,
  Relaunch, Stop system), Stop system neutral (confirmed after the merge),
  latest event no longer hidden under the drawer, focus lands on the
  drawer, not on a ringed Close.
- `2_console` (1400x900): launched. After: no second red; Start/Stop and
  Reset baseline/Save as two equal pairs; Sync toggles read "Off" with a
  lamp; "Running" reads "No"; plot empty state at page size; rail Setup
  a 44 px target; link state a lamp + word.
- `3_latched` (1400x900): after `/api/estop_all`. Mushroom "Clear", trace
  ring, signal grouping bars; nothing else red.
- `4_narrow` (900x900): rack in two columns, rail groups wrap onto a
  second line and the rail grows; mushroom in view.
- after only: `5_narrow_full` (900, full page), `6_phone` (600 wide, one
  column, rail stacked, mushroom top right), `7_reopened` (Setup reopened
  over live modules, scrim, Launch disabled / Relaunch enabled).

## Changed
1. Second red -> `button.button.role-danger` renders as a quiet neutral
   command (styles.css:715). Red Percent's Stop, the heater's "Stop system"
   (halt) and the rotator's "STOP" (halt) are now quiet; the mushroom in
   each Safety section is that model's stop. Consecutive commands are one
   action group of equal cells (app.js:713 `groupCommands`, styles.css:637),
   so Start/Stop are a pair on one line. Copy "Start run"/"Stop run" is
   schema-owned: CCR 1.
2. Vocabulary -> toggles: one width (14rem), a lamp (trace when ON, ring when
   OFF), `aria-pressed`, the state words via `toggleFace` (app.js:149):
   repeated caption dropped ("Sync X: OFF" -> "Off"), a bracketed aside
   moved to the title ("Autonomous mode", title "Click to stop"). Buttons:
   one height, hover/active states. Booleans read Yes/No (`readoutText`,
   app.js:293); an empty readout is muted, so an empty "Fault reason" is
   no longer a red "--" (styles.css:762). Lamps are a dot and a word, not a
   box (styles.css:769). One type scale of five steps on `:root`
   (styles.css:51); every font-size uses it (tested).
3. Empty states -> page text, readable, muted, saying what to do next:
   plot (app.js:527, drawn only when there are 2+ points), log stream
   (`data-empty` + `.feed:empty::before`, styles.css:852), figure (shown
   when the image fails to load, app.js:549). Copy in `EMPTY_STATES`
   (app.js:103), overridable by an `empty` key on the element (CCR 3).
   The analysis figure's own tiny "No samples in this run." is pixels the
   model renders; the view cannot see that it is empty: CCR 2.
4. Rail -> Setup, drawer Close and Show events are `rail-control`, 44 px
   (`--hit`, styles.css:678); group rules at `--rule-strong`, one rule per
   group so a wrapped group aligns (styles.css:216); groups wrap instead of
   clipping to "0..." (styles.css:204); link state is a lamp + word, the
   lamp going to a signal ring when the station stops answering, with a
   sentence saying what to check. Rail readout values carry their full text
   as a title.
5. Module close -> borderless muted "Close", 36 px, title "Close <name>: it
   stops and disconnects. Reopen it from the rail.", aria-label, and the
   existing confirm now says how to get it back (app.js:760, styles.css:683).
6. Drawer -> Launch row's command group takes its own right-aligned line
   (styles.css:949); the table's per-row captions (Port/Gamepad/Status) are
   said once in a header row (`tableHead`, app.js:690) and kept as
   visually-hidden labels, which also cured the "Non" truncation of the
   gamepad dropdown; drawer ends at the tray (`bottom: var(--tray-h)`).
7. Narrow -> rack `minmax(min(24rem,100%),1fr)` collapses 3 -> 2 -> 1; the
   wide Red Percent panel spans two only at >= 76.5rem, where three
   columns fit (it used to span 2 in a 2-column rack, leaving the probe a
   half-empty row). `--rail-h`/`--tray-h` are measured by a ResizeObserver
   (app.js:1141), so the drawer and scrim follow a wrapped rail; the rail's
   floor is a separate `--rail-min`.
8. Accessibility gate (Vercel guidelines, fetched to scratch): labels tied
   to inputs/selects/toggles (`labelControl`, app.js:91), name +
   autocomplete=off + spellcheck=false on entries, `role=status`
   `aria-live=polite` on card status lines, tray line and link state,
   skip link, `color-scheme: dark`, `theme-color` filled from `--bg` at
   runtime (no literal), themed selection/caret/scrollbar,
   `touch-action: manipulation`, `overscroll-behavior: contain` on drawer,
   tray and log, `text-wrap: balance` on headings, "Select…"/"Connecting…"/
   "Waiting for the station…", img width/height, dialogs get
   role/aria-modal and focus their button, region picker uses pointer
   events (pen/touch) with `touch-action: none`, drawer is `inert` while
   shut, `translate="no"` on rail values, hover states on buttons and
   inputs. `impeccable detect --json index.html` after edits: `[]` (it
   warned it could not read `/api/theme.css`, so its colour checks were
   incomplete).
9. Refusal line: the 0.2rem signal left bar (craft-floor ban on thick
   coloured side borders on alerts) became 1px rules above and below.

## Kept on purpose
- Sentence case over the guideline's Title Case: the brief wins.
- Every card's 0.2rem left grouping bar: the brief asks for it, and it
  carries state (live/latched).
- The mini mushroom in each Safety section is red: it is the stop object
  (owner ruling: per-model stop wired into the global stop).
- The close confirm and the latch-clear confirm are `window.confirm`,
  as the web3 contract has them; the mushroom stops without asking.
- Red Percent's two-column flow is still `column-count` (test-pinned); a
  section landing at the top of the second column still shows its top rule.
- No URL state for drawer/tray, no `Intl` formatting (values arrive
  formatted by the models), no safe-area insets (a bench desktop), no
  keyboard alternative to the region drag (the region can be typed through
  no other element today).
- `!important` on the drawer's grid track list (inline style from build()).
- A lone command (Step, Load run) is one cell of the action grid, half a
  column wide, so it matches its neighbours' width.
- /favicon.ico 404s in the console; no icon was asked for.

## CORE CHANGE REQUESTS
1. `src/model/red_monitor.py:1226-1229`: `sch.button("Start", ...)` ->
   `"Start run"`, `sch.button("Stop", "end_run", role="danger", ...)` ->
   `"Stop run"`, `role="neutral"`. The brief's copy; a recording's stop is not
   a safety stop. (The view already renders it quiet.)
2. `src/model/red_monitor.py:1103-1109` (`figure`): when `self._loaded is
   None`, return `None` (or refuse with "No run loaded") instead of a
   rendered empty figure. The Web view then shows its empty state
   ("No run loaded. Load run opens a saved CSV and plots it here."); today
   it shows matplotlib's own caption at ~6 px.
3. `src/schema.py` plot/image/log_stream builders: accept `empty="..."` so
   a model says its own empty state; the Web view already prefers
   `element.empty` over its `EMPTY_STATES` fallback (keyed by command name).
4. Schema copy the view cannot fix: `red_monitor.py:1155` "Run / Cut ID:"
   beside "Run ID:" (suggest "Cut name:" or "Run name:"),
   `red_monitor.py:1161` "Operator Annotation (intended)" (suggest
   "Annotation"), `red_monitor.py:1185-1193` toggle texts "Sync X: ON/OFF"
   (suggest "On"/"Off"), `probe.py:1010-1016` "AUTONOMOUS MODE (Click to
   Stop)" / "MANUAL MODE (Click to Stop)" (suggest "Autonomous: stop" or
   "Leave autonomous mode"), `rotator.py:217` "STOP" and `heater.py:265`
   "Stop System" (halts; suggest "Halt" so they are not confused with the
   stop object, or decide they ARE stop objects and render them as such).
5. `controller/setup.py:933` "Stop system" neutral: confirmed rendered
   neutral after the merge; consider a `needs_confirm` on `stop_system`
   (it tears every model down, and the guideline flags destructive actions
   without confirmation). Owner call.

## Tests
- fast: `1539 passed, 85 deselected` (1527 at base + 12 new client tests).
- golden: 78 passed.
- qt: not run (not my view).
- Tests added (test_view_web_client.py, "round 2" block): danger command is
  quiet; toggleFace; readoutText + muted empty; action groups + Launch
  line; empty states; table header; rail wraps and is measured; rack
  3/2/1 breakpoint; 44 px rail controls; quiet close + confirm; every
  font-size on the scale; labelled controls. No existing test changed.

## UNVERIFIED
- The figure empty state is wired to an image load error, which only
  happens once CCR 2 lands; not seen.
- `prefers-reduced-motion`, a refusal shaking, the needs_confirm dialogs,
  the region picker with pointer events, a live plot, heater/rotator panels
  (their halts now render quiet), keyboard-only walk-through and a screen
  reader were not exercised in a browser.
- Firefox/Safari: `clip-path` visually-hidden labels, `inert`,
  `text-wrap: balance`, ResizeObserver are modern-only; Chrome headless
  only was seen.

COMMIT: 1efe6ad
