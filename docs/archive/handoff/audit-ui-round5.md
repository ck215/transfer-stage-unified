# UI audit round 5: UI UX Pro Max guideline lens, Tk / Qt / Web, the Tier G after-state on a real display

Run:
- **Tree:** `rb-h-audit`, detached at `79d04f5` (mvc-refactor HEAD after Tier G). No repository file was edited. Scratch is in `…/scratchpad/rb-h-audit/` (`web5.cjs`, `web5b.cjs`, `tk5.py`, `qt5.py`).
- **Display:** built-in Retina, 1800×1169 pt at backing scale 2 (Tk `winfo_screen*` reported 1800×1169). Every capture is 2 px per pt.
- **Web:** `src/app.py --web --no-browser --port 8098`, run under `script` so it had a real pty. The page was a headed Chrome for Testing driven by puppeteer at deviceScaleFactor 2, with real key events (Tab, Space, Enter, Escape, Ctrl+.). There were two server runs; each ended through the page's own Quit, and the process exited 0 both times.
- **Tk:** the real `TkDashboard` as an Aqua window at +60+60, driven through `after()` steps (`tk5.py`) and captured with `screencapture -x -R`. Keys were synthetic (`event_generate`). Runs: 1400×900, 900×900, and 1400×900 at `--font-size 28` (`theme.set_font_size`).
- **Qt:** the real `QtDashboard` on Aqua, with `chflags -R nohidden` on PySide6 before each process, driven through `QTimer` steps (`qt5.py`). Keys were synthetic (`QTest.keyClick`; the stop chord as `MetaModifier+.`, which is Qt's name for the physical Control key on macOS). Same three sizes.
- **Skill mode:** ui-ux-pro-max, guideline lookups only. I read the `ux-guidelines.csv` (119 rules) and ran `scripts/search.py … --domain ux` for target size, focus, empty states, disabled states and truncation. The design-system generator was not run. The skill ran inline with no sub-agents. Guideline IDs below are `UX-<No>` from `ux-guidelines.csv`.
- **Cleanup:** no process is left on 8098. Every Tk and Qt window closed through `dash.close()`, and both browsers closed.

Scope: `src/views/{tk.py, qt.py, theme.py, web/static/*}`, `src/controller/{setup.py, controller.py}`, `src/app.py`; BUGFIX_PLAN Tier G/H; `audit-ui-round4.md` and its captures, which I did not re-report.

Summary: **16 findings (1 S1, 5 S2, 7 S3, 3 S4).**

## Capture index (`handoff/shots/`)

45 PNGs, plus per-run JSON (`round5_{web_report,web_report_b,tk_*_report,qt_*_report}.json`).

| View | Size | States |
|---|---|---|
| Tk | 1400×900, 900×900, 1400×900 font28 | `round5_tk_{1_setup_mixed, 1b_checkbox_focus, 1c_setup_none_ticked, 2_gamepadlog_open, 3_latched_log_open}_<size>[_font28].png` (15) |
| Qt | same | `round5_qt_{same five states}_<size>[_font28].png` (15) |
| Web | 1400×900 | `round5_web_{1_setup_mixed, 1b_checkbox_focus, 1c_setup_none_ticked, 1d_setup_all_ticked, 1e_setup_scanning, 2_gamepadlog_open_kbd, 3_latched_log_open, 3b_clear_confirm, 4_quit_confirm, 4b_quit_confirm_latched, 5_after_quit, 5b_after_quit_unlatched}_1400x900.png` |
| Web | 900×900 | `round5_web_{1_setup_reopened, 2_gamepadlog_open, 5_after_quit}_900x900.png` |

"Mixed" means three rows ticked (Stepper, DC, Chuck) and three unticked. A1 (the SIM Rotator's unconfirmed stop, plus the DC Probe's in Tk and Qt) puts an error band in every latched capture; it is not re-reported.

## Findings

### UXPM5-1: The Tk Gamepad log window opens on top of the probe's own Safety column: per-model Stop, Fault lamp, Step, and its own opener          [S1]  views: tk
where:      `src/views/tk.py:2253` (`LOG_WINDOW_SIZE = (520, 300)`), `:2309` `_place_log_window` (top-right of the station window, a fixed offset)
observed:
- At 1400×900 (`round5_tk_3_latched_log_open_1400x900.png`, `tk_1400x900_12_report.json` `covered_by_log_window`), the 520×300 window sits at 916,148 pt. It covers the whole System control and Safety column:
  - Enter autonomous / manual mode, Step, and the "Gamepad log…" button that opened it
  - the per-model **Stop** disc (canvas 61×61 at 1357,333)
  - the **Fault** lamp and Fault reason
  - the right-aligned values of every Configuration entry. X/Y/Z step size and target distances read as empty wells.
- At 900×900 (`round5_tk_2_gamepadlog_open_900x900.png`), it covers the live X/Y/Z position and velocity **values**, all eight Configuration entries, Step, and the opener.
- The global stop (bottom bar) and Ctrl+. still work. Ctrl+. from inside the window latched, synthetic, `ctrl_period_from_log_window: true`.
expected:   UX-100 Focus Not Obscured and UX-15 z-index management: the app must not place its own window over a stop or a hardware-state lamp. Under this profile's S1 definition, a stop is harder to reach and a fault lamp cannot be read.
fix:        In `_place_log_window`, open the window **outside** the station window: to the right of it, clamped to the screen, else below it. Never over the panel that owns it. Scale `LOG_WINDOW_SIZE` from the font metric, as Qt does.
confidence: high

### UXPM5-2: Tk's "Gamepad log…" button is clipped at every size: 47 of 109 px shown at 12 pt, entirely off the panel at 28 pt          [S2]  views: tk
where:      `src/views/tk.py:1548` (consecutive commands share ONE line, no wrap), `:2225` `_make_log_stream` (detached opener as a fourth command on that line)
observed:
- `round4_tk_after_launched.png` and `round5_tk_2_gamepadlog_open_900x900.png` both read "mepad log".
- Measured (`tk_*_report.json` `log_button`): `req_w` 109 px, shown 47 px at 1400 and at 900.
- At 28 pt the button starts at x 1723 while the panel ends at 1450, so it is **not on screen at all**.
- G4 added a fourth button to IMP-3's unwrapped action row, so the round-4 28 pt clipping now happens at the default font.
expected:   UX-113 Essential Text Truncation: action labels need complete access. Discoverability of G4's only entry point.
fix:        IMP-3's fix (wrap the action group by measured width), which now also covers 12 pt. Or give the opener its own line, as Web and Qt do (under Step).
confidence: high

### UXPM5-3: The Web Gamepad log panel lands on another card, far from its opener, and cannot be moved          [S2]  views: web
where:      `src/views/web/static/styles.css:1253` (`.log-window`: fixed, top-right, `width: min(30rem, …)`), `app.js:865` `renderDetachedLog`
observed:
- 1400×900 (`round5_web_2_gamepadlog_open_kbd_1400x900.png`):
  - The Stepper Probe's log opens at x 885–1365 while its button is at x 37.
  - It covers **105,591 px² of the DC Probe card**, including that card's Configuration inputs, whose values are cut at the panel edge.
  - It takes 35 % of the rack width.
- 900×900 (`round5_web_2_gamepadlog_open_900x900.png`): 480 of 885 px (**54 %**). It covers the Stepper card's "Enter autonomous mode" button (cut mid-word) and the DC Probe's own "Gamepad log…" button, and it stops 1 px above the Stepper's per-model Stop.
- There is no drag, no dock and no minimise. The only way to see what is under it is to close it.
expected:   UX-100 (persistent overlays must not obscure controls; dismiss or move them). Proximity: an opened panel belongs near its source (UX-3 active state / spatial continuity).
fix:        Anchor the panel to its card: open it in the card's own column below the opener, pushing content down, or as a drawer section. Or make it draggable, with its last position remembered per stream. At minimum, never place it over a column that holds a Stop.
confidence: high

### UXPM5-4: After Quit the Web page still shows a latched, red, live-looking station          [S2]  views: web
where:      `src/views/web/static/app.js:1911` `showShutDown`, `styles.css:206` (`.link-state.is-shut-down`: `--muted`, 14 px)
observed:
- Quit while latched (`round5_web_5_after_quit_1400x900.png`):
  - The disc still reads **"Clear"** with its trace ring. It is disabled, but it is the loudest object on the page.
  - The red rail line "Stop latched, but Rotator has not confirmed it. Treat it as live." stays up with a **live Dismiss** button.
  - The tray still shows the raw Error line.
- Quit unlatched (`round5_web_5b_after_quit_unlatched_1400x900.png`): the disc reads "Stop" in dimmed red. The close path latched every model, so this face is stale in the other direction.
- In both cases, the only statement that the program has exited is "The station has shut down. You can close this tab." in muted 14 px text in the top-left corner (4.63:1).
- Focus falls to `BODY` (`after_quit.focus`).
- Server log: `Quit … exiting` then `Stop Not Confirmed: Shutdown could not confirm the stop of: Rotator`. The page never sees that last line, because polling has already stopped.
expected:   UX-78/UX-83 status and confirmation messages: the final state must be unmistakable, and the page must not keep asserting states it can no longer observe. UX-37: the disc still says latched or live by colour.
fix:        In `showShutDown`:
- Replace the rail with one ink sentence at readout size ("The station program has exited. Every model was stopped. Close this tab, or start the program again.").
- Remove every rail line and its Dismiss.
- Render the disc neutral (the disabled pair, no ring, face "Off").
- Move focus to the sentence (`tabindex=-1`).
confidence: high

### UXPM5-5: Tk Setup at 28 pt elides every Status word to "…" and puts the Launch row out of view          [S2]  views: tk
where:      `src/views/tk.py:1747` `_fit_readout` → `_elide` (Status cells), Setup panel scroll area
observed:   `round5_tk_1_setup_mixed_1400x900_font28.png`:
- The Status cells of the ticked rows read **"…"**. "simulated", "detected: X" and "not detected" are the only per-row confirmation that a handshake agreed.
- The "Status" header is cut to "Stat".
- The Red Percent row is cut by the scroll viewport, and the Launch row (the sentence and the Launch button) is below it.
- The ticked rows' dropdowns get 560 px each while Status gets about 60 px.
expected:   UX-113 Essential Text Truncation: distinguishing and safety text needs complete access. This is IMP-4's rule applied to words.
fix:        Give the Status column a minimum width of its longest word, and shrink the dropdowns (they are the elastic column), or wrap Status under the row at 28 pt.
confidence: high

### UXPM5-6: Platform-specific: on Aqua, Tk's Models and Setup menus vanish while the Gamepad log window has focus          [S2]  views: tk
where:      `src/views/tk.py:3137-3156` (menubar on the root only), `:2272` (`tk.Toplevel` with no `menu=`)
observed:
- With the log window focused, the screen's menu bar reads "Python File Edit Window Help" (`round4_tk_after_gamepadlog.png`, the lead's after-capture of this code).
- With the main window focused, it reads "Python Models Setup" (`round4_tk_1400x900_menubar.png`).
- The tray event "Setup Minimised: Setup is on the toolbar and the menu bar" points there.
- On Windows and Linux the menu bar is inside the station window and does not change with focus.
- Aqua also injects the default File/Edit/Window/Help set, which appears only on a Mac.
expected:   Owner ruling 2026-09-25 (no platform-specific UI), plus UX-105 Consistent Help: repeated navigation stays in the same place.
fix:        Give the log `Toplevel` the same menubar (`window.configure(menu=root_menubar_clone)`), so Aqua shows Models and Setup whichever station window is key. The global menu-bar placement itself is toolkit-forced; list it with the P3 exception.
confidence: medium (from the lead's after-capture; my own captures crop below the menu bar)

### UXPM5-7: The Launch tick box has three different looks: amber ✓ (Web), ink × (Tk), filled ink square with no glyph (Qt)          [S3]  views: tk, qt, web
where:      `styles.css:742` (`accent-color: var(--trace)`), `tk.py:2867` (`CHECK_STYLE`, Aqua ttk draws ×), `qt.py:471-477` (`indicator:checked` = `TEXT` fill)
observed:
- `round5_{web,tk,qt}_1_setup_mixed_1400x900.png`.
- Tk's × is the conventional "excluded / remove" mark, and in a column headed "Launch" it can read as "will not launch".
- Qt tells ticked from unticked only by fill (12.87:1 between the two), with no mark (H10).
- Web ticks in **trace** and Qt ticks in **ink**. `qt.py:461` says a tick is "not a live reading" (so ink), while `styles.css:738` says "a ticked box is a lit lamp" (so trace). The two views read the brief oppositely.
- At 28 pt the Tk indicator stays about 15 pt beside 28 pt row text (`round5_tk_1_setup_mixed_1400x900_font28.png`).
expected:   UX-37 (not by colour or fill alone), UX-4 style consistency across the three views (same words, same marks).
fix:        One mark everywhere: a ✓ drawn in the indicator (Qt: an `image:` or painted check; Tk: a custom indicator element or the Canvas lamp pattern). One colour, owner's choice of trace or ink. Scale the Tk indicator from `_line_px()`.
confidence: high

### UXPM5-8: "Selection: N devices ticked to launch." restates the tick column, in trace, in all three views; the plan's G3-A kept a sentence only for the blocked states          [S3]  views: tk, qt, web
where:      `src/controller/setup.py:1080` (`sch.readonly("Selection:", "summary")`), `:1143` `_refresh_summary`, `:263` `summary`
observed:
- The sentence is always present. It is drawn in **trace** in Tk (`#e0b34c`), in Qt (`#e0b34c`) and on **Web** (`rgb(224,179,76)`, `web_report.json setup_mixed.selection`). The Web part is a regression of F24, which moved Web's status words out of trace.
- Its three states:
  - a count, which duplicates six visible ticks
  - "Nothing selected. Tick a device to launch." (earns its place)
  - "Scanning. Launch waits for the scan to finish; press Cancel scan to launch now." (earns its place: it explains the disabled Launch, `round5_web_1e_setup_scanning_1400x900.png`)
- G3 design A in BUGFIX_PLAN: "the Launch row keeps one sentence only while scanning or when nothing is ticked".
- Minor copy defects: the count ends in a period, and the caption "Selection" does not match the column word "Launch" or the verb "ticked".
expected:   UX-79 / minimal design (each element does one job), plus the brief's "trace = live readouts".
fix:        `summary` returns `""` when one or more rows are ticked and no scan runs. The views hide an empty readout row. The sentence is muted or ink, never trace.
confidence: high

### UXPM5-9: Launch stays enabled with nothing ticked (all views); pressing it only refuses          [S3]  views: tk, qt, web
where:      `src/controller/setup.py:1081` (`enabled_when=[self.READY]`); the refusal is at `:927-928`
observed:   Nothing ticked: Tk `none_launch_enabled: true`, Qt `true`, Web `launch_disabled: false` (`round5_*_1c_setup_none_ticked_*.png`). The sentence says "Tick a device to launch" beside an enabled Launch.
expected:   UX-31 Disabled States: a command that cannot succeed looks it (F11's rule).
fix:        Add "has a ticked row" to Launch's gating (a `disabled_when` on an empty `configs`), so the sentence explains a greyed button.
confidence: high

### UXPM5-10: The stop chord is stated three ways, in three places; Web's `aria-keyshortcuts` stays on the button while it reads "Clear"          [S3]  views: tk, qt, web
where:      `index.html:31` (`Stop: Ctrl+.`, 12 px muted, `aria-hidden`), `index.html:41` + `app.js:2255` `renderEstop`; `qt.py:3081-3087` (hint under the title, rail left); `tk.py:2958` (hint beside the disc)
observed:
- Web: "Stop: Ctrl+.", 190 px left of the disc with Setup and Quit between them.
- Tk: "Stop every model (Ctrl+.)" beside the disc, bottom-right.
- Qt: "Stop every model (Ctrl+.)" under "Transfer stage", about 1,750 px from the disc at 1400 (`round5_qt_1_setup_mixed_1400x900.png`).
- Latched, Tk and Qt drop the chord ("Clear the stop on every model"), while Web keeps "Stop: Ctrl+.".
- Web latched: the button's accessible name is "Clear the stop on every model", but `aria-keyshortcuts="Control+Period"` still says the chord activates it. The chord only stops (`web_report.json latched_ax`).
- The chord itself works everywhere: Web with real keys (including with the log panel and the Quit confirm open), Tk and Qt with synthetic keys from inside the log window.
expected:   UX-105 consistent placement; UX-40 (the accessible name and shortcut must describe the same action).
fix:        One string beside the disc in all three views ("Stop: Ctrl+."). Remove `aria-keyshortcuts` while latched, or move it to a hidden "Stop every model" description. Qt: move the hint to the rail's right beside Setup.
confidence: high

### UXPM5-11: The Web log panel takes focus with no visible focus indicator          [S3]  views: web
where:      `styles.css:1271` (`.log-window:focus { outline: none; }`), `app.js:929` (`win.focus()`)
observed:   Opening with Enter moves focus to the panel itself (`log_open_1400.active: "the panel itself"`, `panel_outline: none`). A keyboard user sees no focus anywhere. The next Tab goes to Close. Escape works and returns focus to the opener (`focus_after_escape: "Gamepad log…"`).
expected:   UX-28 Focus States and UX-102 Focus Appearance.
fix:        Focus the feed (it is `tabindex=0` and already has the 2 px ink ring) or the Close button on open.
confidence: high

### UXPM5-12: The clear confirmation still says "Release the FULL STOP latch on: …"          [S3]  views: web (and any view that shows `result.reason`)
where:      `src/controller/controller.py:207`
observed:   `round5_web_3b_clear_confirm_1400x900.png`: "Release the FULL STOP latch on: Chuck Positioner, DC Probe, Red Percent, Rotator, Stepper Probe, Temperature Controller? This does not restart anything." The button reads "Clear the stop".
- The same dialog uses three names for the object (FULL STOP / latch / stop) and two verbs (Release / Clear).
- It is all caps (the brief says sentence case).
- The models are alphabetical here, but in rail order elsewhere.
expected:   F20's vocabulary (object "Stop", state "Stopped", action "Clear"); UX-83 confirmation copy matches the action.
fix:        "Clear the stop on every model? Nothing restarts until you start it." List the models only when the list is partial.
confidence: high

### UXPM5-13: Log window titles use the brief's banned spaced em dash, in all three views          [S3]  views: tk, qt, web
where:      `tk.py:2276` (`f"{self.name} — {label}"`), `qt.py:2193`, `app.js:892`
observed:   "Stepper Probe — Gamepad log" in every log capture. The brief bans "labels built as 'WORD — fragment' with a spaced em dash".
expected:   WEB_DESIGN_BRIEF copy rules.
fix:        "Stepper Probe gamepad log" (window title and heading).
confidence: high

### UXPM5-14: The Web tick box is a 20 px target, and its only clickable caption is visually hidden          [S4]  views: web
where:      `styles.css:742` (`.checkbox` 1.25 rem), `app.js:604` (`labelControl` ties the hidden "Launch" label; the row's model name is a section title, not a label)
observed:   20×20 CSS px (`setup_mixed.boxes[*].w/h`). The `<label for>` is 1×1 px. Clicking "Stepper Probe" does nothing. The row is 45 px tall, so WCAG 2.5.8's spacing exception is met.
expected:   UX-104 (24 px, met only by exception); UX-43 (the label is the natural large target).
fix:        Make the row title a `<label for>` the box, or grow the box to 1.5 rem.
confidence: high

### UXPM5-15: An unticked Red Percent row reads "On" (greyed) beside "off"          [S4]  views: tk, qt, web
where:      `setup.py:369` (`device_options` = On/SIM)
observed:   `round5_*_1_setup_mixed_*.png`: Port "On", Status "off".
fix:        Relabel the option "Screen" (vs SIM), or show "SIM / Screen" as the choice only.
confidence: medium

### UXPM5-16: While scanning, the Web Scan line reflows Cancel scan onto its own row          [S4]  views: web
where:      Devices section, `layout="row"`
observed:   `round5_web_1e_setup_scanning_1400x900.png`: "scanning /dev/cu.KEFQ150AIYIMAB07 (2 of 3), 1 s on this port" drops to a second line and pushes Cancel scan to a third. Every row below jumps about 60 px when the scan ends.
expected:   UX-19 Content Jumping.
fix:        Reserve the scan line's row, or put Cancel scan beside Refresh.
confidence: medium

## Tier G after-state

| Item | View | Verdict | Evidence |
|---|---|---|---|
| (a) ticked vs unticked | web | pass | amber fill + ✓ vs dark box, `round5_web_1_setup_mixed_1400x900.png` |
| | tk | weak | × glyph (reads as "excluded"); indicator does not scale at 28 pt. UXPM5-7 |
| | qt | weak | fill only, no mark (H10). UXPM5-7 |
| (a) target size | web | 20×20 px (spacing exception) | UXPM5-14 |
| | tk / qt | pass | Tk widget 24×24 (24×40 at 28 pt); Qt 38×26 (88×47 at 28 pt) |
| (a) focus ring | all | pass | 2 px ink ring: `round5_{web,tk,qt}_1b_checkbox_focus_*.png`; Web `outline 2px solid ink, offset 2px` |
| (a) label | web / qt | pass | accessible name "Launch Stepper Probe" etc. (AX tree; Qt `accessibleName`) |
| | tk | tooltip only | Tk has no accessibility API on Aqua (not verifiable) |
| (a) greyed dropdowns | all | pass | Web disabled text 3.09:1, border 1.40:1 on panel (disabled is WCAG-exempt); Tk/Qt greyed |
| (a) keyboard | all | pass | Web: Tab from Refresh → first box (1 hop), Space toggles (real keys); Tk and Qt: Space toggles (synthetic); tab order box → Port → Gamepad → next box |
| (a) Launch with none ticked | all | fail | UXPM5-9 |
| (b) discoverable | web / qt | pass | "Gamepad log…" under Step, `aria-haspopup=dialog`, `aria-expanded` |
| | tk | fail | clipped to "mepad log" / off-panel at 28 pt. UXPM5-2 |
| (b) Escape + focus return | all | pass | Web → opener (real keys); Tk → opener label; Qt → opener at 1400 and 28 pt. Qt 900: focus `None` after Escape (window not key in my run; low confidence) |
| (b) stop reachable while open | all | pass | Ctrl+. latched from inside the window/panel in all three; Web ack dialog then returned focus to the panel |
| (b) empty state | web | pass | "No gamepad input yet. Choose a gamepad under Configuration, then enter manual mode to drive with it." |
| | tk / qt | fail | empty box (`feed_text ""`, no placeholder) = H10 |
| (b) placement 1400 / 900 | web | fail | covers DC Probe (35 % / 54 % of rack). UXPM5-3 |
| | tk | fail (S1) | covers per-model Stop and Fault at 1400, readouts at 900. UXPM5-1 |
| | qt | marginal | opens below the button, outside the main window, at y 830–1150 of a 1169 pt screen (19 pt from the edge); clamped to the edge at 28 pt = H10 |
| (c) Quit copy | web | pass | "Quit the station? This stops every model, closes every port and exits the program." / Cancel · Quit |
| (c) default button + Escape | web | pass | focus on Cancel; Escape cancels and focus returns to Quit; Ctrl+. during the confirm stops and closes it |
| (c) keyboard path | web | pass | Setup → Tab → Quit → Enter → Tab → Quit → Enter (real keys) |
| (c) page after quit | web | fail | UXPM5-4 |
| (c) cost on the rail | web | measured | rail 130 → **188 px** at 1400×900 (3 rows; round 4 predicted +58), 363 px at 900 |
| (d) Selection sentence | all | drop the count | UXPM5-8 |
| (e) chord copy | all | inconsistent | UXPM5-10; `aria-keyshortcuts="Control+Period"` matches the handler (`ctrlKey` only) |

## Tier H on screen

| # | Verdict | Evidence (round 5) |
|---|---|---|
| H1 | **confirmed** | Tk latched: `_station_line` = "" and hint "Clear the stop on every model" (`tk_*_report.json`); Web latched link "Connected" (`web_report.json latched_ax.link`). Qt says "Stopped". |
| H2 | **confirmed, wider** | 28 pt: velocity "0.0, 0.0,…", alert text under Acknowledge, the per-model Clear disc cut by the panel viewport (`round5_tk_3_latched_log_open_1400x900_font28.png`). Now also at 12 pt: the G4 opener (UXPM5-2) and the Setup Status column (UXPM5-5). |
| H3 | **confirmed** | `round5_qt_3_latched_log_open_1400x900.png`: rail "X pos… / Curre… / Positio…", Red Percent "Current red 0." cut at the dock edge, "Enter autonomous mode" unchanged beside a greyed Step while latched |
| H4 | **confirmed, changed** | rail 188 px at 1400 (was 130: Quit added a row), 363 px at 900 (was 428: shorter hint). Setup reopened at 900: drawer top 305 px, Launch/Relaunch/Stop system at y 817, cut by the tray at about 847 (`round5_web_1_setup_reopened_900x900.png`) |
| H5 | **confirmed, and Web regressed** | Tk: Scan status, "simulated", "off" in trace. Qt: "simulated" trace, "off" and scan muted (Tk and Qt disagree). Web: the Selection sentence in trace (UXPM5-8); the tray Warning line is full trace |
| H6 | **confirmed** | titles "Transfer Station" (Tk), "Transfer Stage" (Qt, Web `<title>`), h1 "Transfer stage" |
| H7 | **confirmed** | Web latched: rail line + ack dialog (raw `[Controller] Stop Not Confirmed: FULL STOP …`) + tray; Tk/Qt raw band only |
| H8 | **confirmed** | Web rail "Rotator Stale" and card badge right after launch |
| H9 | **confirmed** (Qt) | Red Percent "Analysis plot" is a caption over nothing (`round5_qt_2_gamepadlog_open_1400x900.png`) |
| H10 | **confirmed** | Qt tick without a glyph; Tk/Qt log empty box; Qt dialog 19 pt from the screen bottom (0 at 28 pt, clamped by the OS) |

## Platform-specific UI

Accepted items (P3 hook, Qt "Meta+." spelling, P8 default view) are not re-reported. The code sweep (`darwin|aqua|win32|platform|Meta|metaKey|Cmd|⌘` over `views/`, `app.py`, `devices/`) found:

| Item | Where | Verdict |
|---|---|---|
| Tk menus leave the menu bar when the log window is key; Aqua adds File/Edit/Window/Help | `tk.py:3137-3156`, `:2272` | **S2**, UXPM5-6 |
| Tk tab close is the middle button on every platform | `tk.py:339` | Neutral in code. In effect, a Mac trackpad has no middle button, so Tk tabs cannot be closed by gesture on the lab Mac (the Models menu still works). Tabs show no close mark. S3 note for the lead. |
| Web chord | `app.js` `event.ctrlKey` only | neutral (real-key tested) |
| Qt copy | `qt.py:3645` returns literal "Ctrl+." | neutral |
| Gamepad bind table (`gamepad.py:781`), darwin enumeration (`:833`) | devices | device mapping, so the same physical stick does the same thing: neutral |
| Tk `_PIXEL_FONTS` on Aqua (`tk.py:2834`), wheel dialects (`:996`) | tk | normalising, neutral |

## Design-language notes for the lead (facts, not findings)

**What the instrument-console language does well on this display**
- **The stop is never in doubt.** One round red disc in the same corner (Web and Qt top-right, Tk bottom-right) at every size. It scales at 28 pt, and white on signal is 4.87:1.
- **Trace amber holds up.** Numbers in trace on panel are 6.79:1 and read from a distance. The Web rail's large tabular zeros are the most legible object after the stop.
- **Tokens hold across toolkits.** Six tokens give one palette in three toolkits: ink on panel 10.97:1, muted 4.63:1 on panel and 5.43:1 on base.
- **Authored copy is the strongest part where it exists.** Examples: "Nothing restarts until you start it", the Gamepad empty state, the Quit confirmation.

**Where it fails on the real display**
- **"One red" is really "one red kind".** A launched Web page shows 1 + N red discs (the global stop plus one per probe card: 4 at 1400 with three probes). Latched, each also carries a trace ring. The eye has several red things to find, although only one is global.
- **Trace has drifted from numbers to words.**
  - Setup status words (Tk, Qt), "Simulated" at readout size in all three views, the Selection sentence (all three), and the full Warning tray line (Web).
  - Status words appear in trace in Tk but muted in Qt, even though both use the same theme tokens.
  - "Numbers first" is diluted.
- **Density and hierarchy.**
  - Web: a Stepper card is about 1,150 px tall (rack scroll is mandatory at 900), and the rail is 188 px (21 %) at 1400 and 363 px (40 %) at 900.
  - Qt: the six-dock layout forces elided captions ("Curre…").
  - Tk: no rail, so only the open tab's numbers are visible.
  - Setup: the table uses about 40 % of a 1400 px window in Web and Qt. In Tk it stretches edge to edge, with Launch 1,000 px from its sentence.
- **The three toolkits render the same tokens differently.** Each of these differs across Web, Tk and Qt:

| Element | Web | Tk | Qt |
|---|---|---|---|
| Tick mark | amber ✓ | ink × | ink fill |
| Stop-hint position | rail right | bottom right | rail left |
| Setup container | drawer | tab | dock |
| Event log | one-line tray | always-open 4-line box | one-line tray |
| Detached log window | in-page panel over another card | Toplevel over its own card | dialog under its button |
| Font | Plex | Helvetica | Helvetica |
| Latched sentence | none | none | "Stopped" |
| Product name | "Transfer Stage" | "Transfer Station" | "Transfer Stage" |

  The palette is shared; layout, marks, placement and words are not.
- **The latch in practice.** The ring (trace on signal, 2.48:1 by token math) is still the main visual cue in Tk and Web (F6, owner's).

## Skill disagrees with the brief
- The skill's 44×44 target (UX-22/66) vs the brief's "controls compact". The Web tick box (20 px), ghost buttons and card Close links are under 44. Only the 24 px WCAG floor is reported (UXPM5-14). Left for the owner.
- The skill's 3:1 non-text contrast vs the pinned signal and trace: signal on panel 2.73:1, ring on face 2.48:1. This is F6, owner's.
- The brief's "trace = indicator lamps ON" does not say whether a tick box is a lamp. Web reads it as yes (trace) and Qt as no (ink). The owner picks one (UXPM5-7).

## Already fixed or kept on purpose (not re-reported)
- F1 holds: the Web Quit confirm and ack sit below the rail, and the stop is not inert under either (`quit_confirm.stop_inert: false`).
- F9/G5: Ctrl+. works from a text box, from the log panel and from the Quit confirm (Web, real keys), and from the log windows (Tk and Qt, synthetic). There is no ⌘ binding or ⌘ copy in any view.
- G6: the Web unconfirmed-stop line clears with the latch (`after_clear.alertHidden: true`).
- F17: every confirm defaults to Cancel, and Escape answers No (Web measured).
- IMP-8 (G3 before): the 159-character list is gone.

## Could not verify
- Real keystrokes in Tk and Qt: no Accessibility permission. Space, Escape and the chord were synthetic.
- The Tk menu bar in my own captures: they are cropped below it. UXPM5-6 rests on the lead's `round4_tk_after_gamepadlog.png` of the same code.
- The Qt focus return at 900×900: focus was `None` after Escape, probably because the window was not key in the scripted run.
- Tk's "Launch with nothing ticked" refusal text on screen. The refusal is at `setup.py:928`, and the harness did not press it in Tk/Qt. On Web, my second run hit the boot scan, so the capture shows the scanning state instead.
- VoiceOver on the tick boxes, lamps and log dialogs.
- Windows and Linux rendering (no machines).
- The region picker.
