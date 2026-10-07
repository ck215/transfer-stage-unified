# UI audit round 4: Impeccable `audit` + `critique`, Tk / Qt / Web, first run on a real display

Run:
- **Tree:** `rb-g-audit` detached at `3b87d38`. Nothing in any repository was edited. Scratch lives in `…/scratchpad/rb-g-audit/`.
- **Display:** built-in Liquid Retina XDR, 3024×1964 physical, used at 1800×1169 pt, backing scale 2 (main display, where every window opened). A second display was attached but not used: AORUS FO32U2P, 5120×2880 at 2560×1440 pt, scale 2. Every capture is at 2 px per pt.
- **Tk:** the real `TkDashboard` on Aqua (Tk 9.0.4), built the way `app.launch` builds it and driven by `tk_capture.py` through the real Setup panel view (`_run` on Launch) and `_on_stop_clicked`. Captured with `screencapture -x -R` over the window frame.
- **Qt:** the real `QtDashboard` on Aqua, not offscreen. PySide6 was un-hidden with `chflags` before every run. Driven and captured the same way (`qt_capture.py`).
- **Web:** the server was `src/app.py --web --no-browser --port 8095`, run under `script` so it had a real pty, the way the owner runs it. The page was a real **headed** Chrome for Testing 152 window on this display, driven by puppeteer at deviceScaleFactor 2 and captured with `page.screenshot` (`web_capture.cjs`).
- **Skill modes:**
  - `audit` ran inline. It is a code-level checklist, and the measurements below are its evidence.
  - `critique` ran as the reference requires: Assessment A (design review) and Assessment B (detector) as two isolated sub-agents, synthesised here. See "Critique" below for the method line.
  - `impeccable context` reported NO_PRODUCT_MD and SCOPED_EXISTING_ALLOWED, so the incumbent code plus WEB_DESIGN_BRIEF.md served as the authority.
- **Cleanup:** every server, window and browser I started was closed, and no process was left on 8095. A stray `--port 8093` server belongs to another agent and was left alone.

Scope:
- `src/views/{tk.py, qt.py, base.py, theme.py, web/server.py, web/static/*}`, `src/controller/setup.py`, `src/app.py`.
- Earlier rounds, so that landed rows are not re-reported: `handoff/fix-{tk,qt,web}.md` and `BUGFIX_PLAN.md` Tier F/G.

Summary: **18 findings (2 S1, 6 S2, 9 S3, 1 S4)**, plus 8 platform-specific items filed as S2 under the owner ruling of 2026-09-25 (section below). Critique health: **25/40 (Acceptable)**. Audit health: **12/20 (Acceptable)**.

## Capture index (all in `handoff/shots/`)

52 PNGs, plus per-run JSON measurements (`*_report.json`) and one terminal text file.

| View | Size / font | States | Files |
|---|---|---|---|
| Tk | 1400×900, 12 pt | setup, launched (Stepper), launched (Red Percent), latched, Setup reopened, menubar strip | `round4_tk_1400x900_{1_setup,2_launched,2b_redpercent,3_latched,4_setup_reopened,menubar}.png` |
| Tk | 900×900, 12 pt | same six | `round4_tk_900x900_*.png` |
| Tk | 1400×900, 28 pt | same six | `round4_tk_1400x900_font28_*.png` |
| Qt | 1400×900, 12 pt, 6 models | setup, launched, latched, Setup reopened | `round4_qt_1400x900_{1..4}_*.png` |
| Qt | 900×900, 12 pt, 6 models | same | `round4_qt_900x900_*.png` |
| Qt | 1400×900, 28 pt, 6 models | same | `round4_qt_1400x900_font28_*.png` |
| Qt | 1000×700, 4 models (F5) | same | `round4_qt_1000x700_4models_*.png` |
| Qt | 1000×700, 6 models (F22) | same | `round4_qt_1000x700_6models_*.png` |
| Qt | 1400×900, Rotator and heater (lamps, F8) | same | `round4_qt_1400x900_lamps_*.png` |
| Web | 1400×900, 6 models | setup drawer, launched (+ full page), latched, Setup reopened | `round4_web_1400x900_{1_setup,2_launched,2_launched_full,3_latched,4_setup_reopened}.png` |
| Web | 900×900, 6 models | Setup drawer reopened after launch, launched (+ full), latched, Setup reopened latched | `round4_web_900x900_*.png` |
| Web | G1 before | terminal output after six keep-alive sockets reset | `round4_web_g1_terminal_before.txt` |

In every "latched" capture the SIM Rotator reports its stop as unconfirmed. That is A1, and it puts an error band or dialog on screen in every view. It is not re-reported here.

## Findings

### IMP-0: Web keeps saying "Stop latched … Treat it as live" after the stop is cleared          [S1]  views: web
where:      `src/views/web/static/app.js:2023` (the only writer of the `stop` rail line, in `stopAll`); `renderEstop` at `:1971` and the clear path at `:1991` never touch it
observed:
- In `round4_web_900x900_2_launched.png` and `round4_web_900x900_1_setup_drawer.png`, the latch has been cleared: the face reads "Stop" and the tray reads "Stop Cleared". The red rail line still says "Stop latched, but Rotator has not confirmed it. Treat it as live."
- Code confirms it: the line is written only by `stopAll` and is removed only by Dismiss or the next clean stop. Clearing from the page, from Tk or Qt, or from a second tab leaves it up.
- Found by Assessment A; I verified it in code.

expected:   Nielsen #1. The rail must never state a latch that does not exist; this is the S1 definition.
fix:        In `renderEstop`, when `wasEstopped && !isEstopped`, drop the `stop` line or rewrite it in the past tense ("Rotator never confirmed the last stop; check it at the bench"). Test it with a clear coming from another client.
confidence: high

### IMP-1: Latched is still a face word and a ring in Tk and Web; only Qt says "Stopped"          [S1]  views: tk, web
where:      `src/views/tk.py:2759` (stop bar), `src/views/web/static/app.js` `renderEstop`, `index.html:23`
observed:
- **Tk** (`round4_tk_1400x900_3_latched.png`): the only signs of the latch are the face word "Clear", a trace ring, and the hint "Clear the stop on every model". Nothing on screen says the station is stopped.
- **Web** (`round4_web_1400x900_3_latched.png`): the link line still reads "Connected". The rail line that shows up is only A1's "Stop latched, but Rotator has not confirmed it", so with an A1 fix there is no sentence at all.
- **Qt** does it right (`round4_qt_1400x900_3_latched.png`): the rail says "Stopped".
- Ring measured on screen (Web): trace ring on the red face **2.76:1**, ring on the panel 6.79:1, face on the panel 2.46:1. The token math gives 2.48:1 ring-on-signal for Tk and Qt.

expected:   Nielsen #1, visibility of system status; WCAG 1.4.1, colour not the only cue. This is F6's open sentence half, and it applies to Tk as well as Web.
fix:        Add the rail/stop-bar sentence "Stopped: every model latched", as Qt has it, to `tk.py` beside the stop hint and to `app.js` in the link or rail line. The ring colour stays the owner's call (F6); it was measured once here, not argued.
confidence: high

### IMP-2: Web rail takes 428 of 900 px with six models          [S2]  views: web
where:      `src/views/web/static/styles.css:147-160` (`.rail`), `:238` (`.rail-readouts`)
observed:
- In `round4_web_900x900_2_launched.png` the rail is 428 px tall, 48 % of the viewport. Each of the six groups wraps onto its own line (groups are 282 px wide in a 406 px readout column).
- Latched, the rail line adds about 60 px, so the rail is about 490 px (`round4_web_900x900_3_latched.png`).
- With Setup reopened while latched (`round4_web_900x900_4_setup_reopened.png`), the drawer starts about 370 px down, and the Launch row is below the drawer's fold.
- At 1400 px the rail is 130 px on two rows, which is acceptable.

expected:   Adapt / responsive: the hero may not own half the viewport. This is the open **F22 Web part**, now measured at 900×900 instead of 900×600.
fix:        Below about 1100 px, collapse each group to its first readout, or to one line of `name value`, as Qt does with its readout shedding (`qt.py:2661`). The design is F22's.
confidence: high

### IMP-3: Tk at `--font-size 28` clips the probe's System control buttons mid-word          [S2]  views: tk
where:      `src/views/tk.py:2051` (`_make_button` → `_button_label`, row action group)
observed:   In `round4_tk_1400x900_font28_2_launched.png`, "Enter autonomous mode" is whole, "Enter manual mode" shows as "ıanua", and **Step is off the panel entirely**. The action row does not wrap. The same thing happens latched (`…font28_3_latched.png`).
expected:   Harden / text scaling: a control is never clipped. F5's rule of "never clip" was applied to Qt's rail only.
fix:        Wrap the action group (grid with `columnspan`, or pack into rows by measured width), in `tk.py` beside `_button_label`.
confidence: high

### IMP-4: Tk elides live numbers: the velocity readout shows "0.0, 0.0,…" at 28 pt          [S2]  views: tk
where:      `src/views/tk.py:1728` (`_fit_readout` → `_elide`)
observed:   `round4_tk_1400x900_font28_2_launched.png` shows Velocity (x, y, z) as "0.0, 0.0,…". The z component is hidden and exists only in a tooltip.
expected:   F5's rule as the plan words it: "the number itself is always whole". Qt was fixed; Tk's readouts end-elide any value that does not fit its cell.
fix:        In `_fit_readout`, never elide a numeric readout: wrap it, or let its column claim the width. Elide only word values.
confidence: high

### IMP-5: Tk alert band text runs under the Acknowledge button at 28 pt          [S2]  views: tk
where:      `src/views/tk.py:2828` (`wraplength=720`, fixed)
observed:   In `round4_tk_1400x900_font28_3_latched.png`, "…but these did n" is cut by the Acknowledge button, and the rest continues on the next line. The wrap length is a constant 720 px and ignores the button's width.
expected:   Harden: error text is always readable (F1's band).
fix:        Bind `<Configure>` on the band and set `wraplength = band width − button width − gaps`.
confidence: high

### IMP-6: Qt Red Percent dock clips its live readouts at the dock edge          [S2]  views: qt
where:      `src/views/qt.py:3356` (`_dock_columns`), `:3370` (`_balance_docks`)
observed:
- In `round4_qt_1400x900_2_launched.png`, the 466 px Red Percent dock cuts its right column mid-number: "Current red 0.", "Running N", "Set capture reg".
- It needs a horizontal scroll at 1400 px (the scroll bar is visible), and also at 1000×700 (`round4_qt_1000x700_6models_2_launched.png`).
- The tabified dock titles elide to "R…" and "Re…" (`round4_qt_900x900_2_launched.png`), so Rotator and Red Percent cannot be told apart.

expected:   Never clip a readout (F5); a tab must be identifiable. fix-qt.md "kept on purpose" accepted the sideways scroll. On a real display it cuts **live numbers**, so it is re-filed against F22.
fix:        Give Red Percent a one-column fallback under about 600 px (AUD-7's suggestion, which was not attempted), and elide tab titles from the middle, or give tabs a minimum of about 8 characters.
confidence: high

### IMP-7: Qt rail captions elide to stubs that no longer name the number          [S3]  views: qt
where:      `src/views/qt.py:2453` (`RailGroup`), `:2661` (`_arrange`)
observed:   With six models at 1400×900 the rail reads "Curre… 0.00  Red ch… 0.00", "Current te… Simulated" and "Position… —" (`round4_qt_1400x900_2_launched.png`). With four models at 1000×700 it reads "Curr… 0.00  Red … 0.00" (`round4_qt_1000x700_4models_2_launched.png`). At 28 pt it reads "Curr…", "Red c…" and "Positi…". The numbers are whole, so F5 holds, but two readouts of one model become indistinguishable.
expected:   Recognition over recall (Nielsen #6).
fix:        When a caption would fall under about 6 characters, shed the readout instead, since the elide-then-shed order is already there. Or use short rail captions from the schema (`rail_label`).
confidence: medium

### IMP-8: The "Selected:" readout is unreadable in two of three views (G3 before)          [S2]  views: tk, qt, web
where:      `src/controller/setup.py:1043`, `:1102`
observed:   The six-model text is 159 characters.
- Tk end-elides it: 128 of 159 characters shown at 1400 px, 51 at 900 px, 21 at 28 pt. Rotator and Red Percent are lost first.
- Qt middle-elides it into "Stepper Probe (simulated), DC…ted), Red Percent (simulated)" (346 of 858 px). Four of the six models vanish.
- Web wraps it to 3 right-aligned lines of 461 px and grows the Launch row to 72 px.
- All numbers are in "Tier G before".

expected:   Minimal design and recognition. The information already sits in the per-row Status column.
fix:        G3, owner's choice of A or B. Both remove the readout; this audit supports either.
confidence: high

### IMP-9: The Gamepad Log is a large empty box on every probe card (G4 before)          [S3]  views: tk, qt, web
where:      `src/model/probe.py:1074`, `tk.py:2172`, `qt.py:2064` (`LOG_STREAM_PX = 110`, fixed), Web `.feed`
observed:   It takes 11–18 % of the card height on Web and 36 % of the visible Tk panel at 28 pt (measurements below). Tk and Qt draw it as a **blank box with no empty-state text**. Web says "No gamepad input yet. Choose a gamepad…". The Qt box stays 110 px at 28 pt (it does not scale), while Tk's grows to 166 px.
expected:   Progressive disclosure, and a consistent empty state.
fix:        G4 (`detached=True`). Until then, the Web empty sentence belongs in Tk and Qt too.
confidence: high

### IMP-10: Status words are drawn in trace in Tk and Qt; F24 fixed only Web          [S3]  views: tk, qt
where:      `src/views/tk.py:2248-2252` (every non-quiet readout is TRACE), Qt `ReadoutLabel`
observed:
- Setup "ready" and six "simulated", and the Selected sentence, are in trace amber in Tk and Qt (`round4_tk_1400x900_1_setup.png`, `round4_qt_1400x900_1_setup.png`). Web draws them muted or ink (`round4_web_1400x900_1_setup.png`).
- The heater's "Simulated" sits in the readout slot at readout size in all three views, in trace in Tk and Qt.

expected:   Brief: trace is "live readouts, plot line, indicator lamps ON", so numbers only (F24).
fix:        Move `readoutKind` and `QUIET_WORDS` into `views/base.py` (fix-web CCR 3) and use them in both desktop views.
confidence: high

### IMP-11: Three names for one product across the views          [S3]  views: tk, qt, web
where:      `tk.py:2603` "Transfer Station", `qt.py:2741` "Transfer Stage", `index.html:8` "Transfer Stage" / h1 "Transfer stage"
observed:   The window titles differ (see every capture's title bar). On Aqua, the Tk menu bar's app menu reads **"Python"** (`round4_tk_1400x900_menubar.png`).
expected:   Consistency and standards (Nielsen #4).
fix:        One title constant in `views/base.py`. The "Python" app-menu name needs a bundle name or `CFBundleName`; that is a launcher change (S4 part).
confidence: high

### IMP-12: Qt's Setup reopen grows the window past its requested height at 28 pt          [S3]  views: qt
where:      `src/views/qt.py` `show_setup` (~3120)
observed:   `round4_qt_1400x900_font28_report.json` records the frame at 1400×963 with Setup hidden and **1400×1030** after Setup reopened. The window grew by 67 pt rather than scrolling. On a 768-pt-tall bench laptop this pushes the bottom tray off-screen.
expected:   Adapt: a window never outgrows the screen on its own.
fix:        Put the Setup dock's panel in a scroll area with a maximum height, or cap it to the available screen geometry.
confidence: medium

### IMP-13: Rotator reads "Stale" or "Readings are stale" the moment it launches in SIM          [S3]  views: web, qt
where:      Web rail group flag, Qt panel notice (`round4_qt_1400x900_lamps_3_latched.png` "Readings are stale"; `round4_web_1400x900_2_launched.png` "Rotator Stale")
observed:   A freshly launched simulated Rotator is flagged stale, with Motion state "Disconnected" and Stage connected "No". Tk shows no stale mark for the same state.
expected:   An operator's first view of a SIM run should not carry a fault-like word. The views also disagree.
fix:        Model-side (A1 family: what a portless stage means). The views are consistent with the state they are given, so this is recorded for the lead, not routed to a view.
confidence: medium

### IMP-14: Tk and Qt leave the Red Percent analysis image and the heater plot with no scale          [S3]  views: tk, qt
where:      `tk.py:2139` (`_make_image`), `qt.py` plot
observed:   Tk's "Analysis plot" is a caption over a hairline with nothing under it (`round4_tk_1400x900_2b_redpercent.png`). There is no "No run loaded" empty state (E1 is still open). The live plots show no numeric axis (F26 is still open).
expected:   Empty states direct the next action.
fix:        E1 and F26 as planned; nothing new beyond confirming them on screen.
confidence: high

### IMP-15: Qt's latched per-card commands look enabled beside a disabled Step          [S3]  views: qt
where:      `src/views/qt.py` toggle styling (`_restyle` 2174)
observed:   In `round4_qt_1400x900_3_latched.png`, Step is greyed, but "Enter autonomous mode" and "Enter manual mode" look the same as they did before the latch (flat, ink text). Tk greys all three (`round4_tk_1400x900_3_latched.png`), and Web greys them too.
expected:   F11: commands the latch will refuse look refused.
fix:        Check the toggle's disabled paint in `_restyle`. It may be disabled with no visual change: `isEnabled()` was not read, hence medium confidence.
confidence: medium

### IMP-17: One unconfirmed stop raises three notices on Web, and Tk and Qt never say what to do          [S3]  views: all
where:      Web `app.js:2023` (rail line) + ack dialog + tray; Tk band `tk.py:2817`; Qt alert band `qt.py:2945`
observed:
- **Web** (`round4_web_1400x900_3_latched.png`): the authored rail sentence "…Treat it as live", an alertdialog repeating the raw event "[Controller] Stop Not Confirmed: FULL STOP latched on every model, but these did not confirm within 1.0s: Rotator", and the tray line.
- **Tk and Qt** (`round4_tk_1400x900_3_latched.png`, `round4_qt_1400x900_3_latched.png`) show only the raw event string, with no next step.
- Screen readers get it twice: `role=alert` (`index.html:42`) and `role=alertdialog` (`:73`).

expected:   Single focus at the high-stakes moment, consistent copy (F19/F20), and errors that say what to do.
fix:        One authored sentence per view for an unconfirmed stop, the Web rail's words. Skip the ack dialog when the rail line already carries the event. This moves with A1.
confidence: medium

### IMP-16: Tk's IBM Plex Sans is not installed on this Mac; the desktop views render Helvetica          [S4]  views: tk, qt
where:      `src/views/theme.py:5` (`FONT_FAMILY = "Helvetica"`)
observed:   Tk `font.families()` has no Plex, and Qt resolves Helvetica. Web loads its three self-hosted Plex weights (`document.fonts`: 400, 500 and 600 loaded, `check('500 16px "IBM Plex Sans"')` = true).
expected:   The brief names one typeface. This is E4, the owner's call, reported only as confirmed on screen.
fix:        E4.
confidence: high

## Critique (Impeccable `critique`)
Method: dual-agent. Assessment A (design review, from the captures and the source) and Assessment B (detector) ran as two isolated sub-agents; the synthesis is mine. Target slug and persistence were skipped: the audit is read-only, and `.impeccable/critique/` would be a repository write. Questions skipped: the lead, not the user, takes this report, and the priority questions are the lead's decisions ("Decisions" in the reply).

**Design specificity verdict: mostly authored.**
- The stop object, one red, trace numbers and the rail are specific to this product and hold across three toolkits.
- The module bodies are generic label/input grids.
- Tk has no rail, so "numbers first" is lost there (positions for models on other tabs are invisible).
- Setup at 1400 px leaves about 60 % of the window empty in Tk, Qt and Web.
- **Detector:** `impeccable detect` on `index.html` + `app.js` exited 0 with no findings. It could not resolve the runtime `/api/theme.css`, so colour rules were blind. On a scratch copy with the theme built from `theme.css_variables()` it found one: `overused-font` (Helvetica, `theme.py:5`). That is a false positive for Web, because `styles.css:90` puts self-hosted Plex first, but it is true for Tk and Qt (IMP-16).
- No browser overlay was injected (a server app; ports owned elsewhere).

| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of system status | 2 | IMP-0 stale "Stop latched" line; IMP-1 no "Stopped" sentence (Tk, Web) |
| 2 | Match system / real world | 3 | Bench copy is good where authored; raw event strings leak ("[Controller] Stop Not Confirmed: FULL STOP…") |
| 3 | User control and freedom | 3 | Dismiss / Acknowledge / Setup reopen exist; no Quit on Web (G2) |
| 4 | Consistency and standards | 2 | Three product names, status words trace vs muted, three notice designs for one event |
| 5 | Error prevention | 3 | Clear asks first; latch gates commands (Qt toggles look enabled, IMP-15) |
| 6 | Recognition over recall | 3 | Chord printed by the stop; Qt rail captions "Curr…", tab "R…" |
| 7 | Flexibility and efficiency | 3 | Global stop chord in all views; no jog/step keys |
| 8 | Aesthetic and minimalist | 2 | Rail repeats "X position / Y position / Z position" nine times; 428 px rail at 900 px; empty Gamepad log boxes |
| 9 | Error recovery | 2 | "did not confirm within 1.0s: Rotator" names the fault, not the next step, outside the Web rail |
| 10 | Help and documentation | 2 | Good empty-state sentences on Web only |
| **Total** | | **25/40** | **Acceptable** |

**Cognitive load:** 4 failures (high).
- Single focus at the latch: three notices on Web.
- Chunking: the DC Probe Configuration block has 11 inputs.
- Visual hierarchy at 900 px: the rail swamps the modules.
- Minimal choices: Launch / Relaunch / Stop system sit beside the global Stop, so there are two "stop" words in one glance.

**Emotional journey:** the peak works on Web. The face turns to "Clear" with a ring, and the rail says "Treat it as live". The end is the worst moment (IMP-0): after Clear, the page still says the stop is latched.

**What's working:**
- The stop object is never in doubt: same place, same shape and one red in every view, and it scales at 28 pt in Tk (F7).
- The authored copy ("Nothing restarts until you start it", the Gamepad empty state).
- One token system across Tk, Qt and Web, which still holds at 28 pt.

**Priority issues** (P-levels map onto the severity table above):
- [P1] IMP-0
- [P1] IMP-1
- [P1] IMP-3/IMP-4/IMP-5: Tk at 28 pt
- [P2] IMP-2/IMP-6/IMP-7: rails and docks with six models
- [P2] IMP-17: notice consistency

**Persona red flags:**
- **Alex (power user):** no keyboard path for Step or Launch. The ack dialog takes focus mid-task.
- **Sam (accessibility):**
  - Tk and Qt lamps are a bare circle with no word; Web adds "No".
  - Tk's Synced axes are three unlabelled "Off" toggles (`round4_tk_1400x900_2b_redpercent.png`).
  - The latch is announced twice (alert + alertdialog).
- **Bench researcher at 1.5 m, hands on a micromanipulator:**
  - Every "0" looks alike, because the axis captions are small and muted.
  - At 900 px the stop sits mid-height on the right of a 428 px rail.
  - The heater's large "Simulated" reads as a value.
  - Tk shows only the open tab's numbers.

**Minor:**
- Title Case leaks through event names ("Port Unverified", "Brake speed (Slow)" on Qt).
- A spaced em dash in `app.js:2032` ("did not reach the station — "), which the brief bans.
- "Cancel scan" is orphaned on its own row in the Web drawer (`round4_web_1400x900_1_setup.png`).

**Assessment A claims I rejected as artifacts of my own test harness:**
- "The page jumps to Red Percent on latch": my script focused the first text input in the rack before pressing Ctrl+.
- "Tk lists the same error twice": my key test stopped twice.

## Audit (Impeccable `audit`), health score
| # | Dimension | Score | Key finding |
|---|---|---|---|
| 1 | Accessibility | 2 | Latch by colour+word only (IMP-1); Tk/Qt lamps no text; double announcement (IMP-17) |
| 2 | Performance | 3 | Not re-measured; F21 landed (idle repaint 0/s per fix handoffs) |
| 3 | Responsive / text scaling | 2 | Tk 28 pt clipping (IMP-3/4/5), Web rail at 900 (IMP-2), Qt dock clipping (IMP-6), Qt window growth (IMP-12) |
| 4 | Theming | 3 | Tokens hold; trace on words in Tk/Qt (IMP-10); desktop font Helvetica (IMP-16); served muted #9099a7 vs brief #8c95a3 (deliberate, `palette.py:13-16`) |
| 5 | Implementation integrity | 2 | IMP-0 stale state line; platform branches P1–P8 |
| **Total** | | **12/20** | **Acceptable** |

## Platform-specific UI (owner ruling 2026-09-25)

Ruling: no macOS-specific shortcut or command in any view. Every item below is **S2**. The neutral stop chord is Ctrl+., and the neutral quit is window close.

| # | Where | What the operator sees or presses | Platform-neutral equivalent |
|---|---|---|---|
| P1 | `src/views/tk.py:122` `STOP_KEYS_AQUA = ("<Command-period>",)`, bound at `:2795-2800` when `_stop_key == STOP_KEY_NAME_AQUA` | ⌘. stops on Aqua | Bind `<Control-period>` only (already in `STOP_KEYS`, `:121`). Delete the Aqua tuple and branch. |
| P2 | `src/views/tk.py:123`, `:2768` `STOP_KEY_NAME_AQUA = "⌘."`, chosen when `_windowing_system == "aqua"` | Hint and tooltip read "Stop every model (⌘.)" (every Tk capture, stop bar) | One name, "Ctrl+.", on every platform |
| P3 | `src/views/tk.py:2974-2980` `createcommand("::tk::mac::Quit", self.close)` | ⌘Q / app menu Quit runs the teardown | Window close is `WM_DELETE_WINDOW` → `close` (`:2644`, already there). **Caution for the lead:** with the hook removed, Aqua's app-menu Quit (still present in the "Python" menu) exits past the teardown, which was VIEW-TKINTER-8. The owner's ruling needs a neutral answer for that default menu item. Tk's `wm protocol` does not cover it, and removing the hook weakens the close path. |
| P4 | `src/views/qt.py:173-180` `STOP_SHORTCUTS = ("Ctrl+.",) + (("Meta+.",) if sys.platform == "darwin" else ())` | Qt's "Ctrl" is ⌘ on macOS, so the primary chord there is ⌘.; the physical Control+. works only through the darwin branch | Set `Qt.AA_MacDontSwapCtrlAndMeta` once in `ensure_application` (`qt.py:2760`) so that "Ctrl+." is the physical Control key everywhere, and bind that one sequence |
| P5 | `src/views/qt.py:3464-3466` `QKeySequence(STOP_SHORTCUT).toString(NativeText)` | Rail hint and tooltip render "Stop every model (⌘.)" on macOS (`round4_qt_1400x900_1_setup.png`) | Literal "Ctrl+." (PortableText), after P4 |
| P6 | `src/views/web/static/app.js:1421` `(event.ctrlKey \|\| event.metaKey)` | Cmd+. stops in the browser | `event.ctrlKey` only. **Measured:** Ctrl+. from a focused text box latched the station at 1400 and at 900 in Chrome (`round4_web_report.json` `ctrl_period_*: true`). |
| P7 | `app.js:32` `STOP_KEY_HINT`, `index.html:31` rail caption "Stop: Ctrl+. / Cmd+. on a Mac", `index.html:36` `aria-keyshortcuts="Control+Period Meta+Period"`, `index.html:37` title | Copy names a Mac chord (every Web capture, rail right) | "Stop: Ctrl+." and `aria-keyshortcuts="Control+Period"`. That also frees about 86 px of the rail for G2's Quit (see Tier G c). |
| P8 | `src/app.py:55-60` `if platform == "darwin": return "tk"` | An unqualified launch opens Tk on a Mac and Qt or Web elsewhere | Owner decision D-9 put it there. It changes which UI the operator sees by platform, so it conflicts with today's ruling, and only the owner can resolve that. Listed, not decided. |

Not findings (neutral in effect):
- `tk.py:338` `_close_tab_button` maps the same physical right-click on Aqua and X11.
- `tk.py:2658` `_PIXEL_FONTS` normalises point sizes on Aqua, so it keeps the look the same rather than changing it.
- Qt's default app-menu "Quit" (⌘Q) comes from Qt/Cocoa, not from app code, but it is still a macOS command; it is wired to `aboutToQuit` → `close` (`qt.py:2811`).

**⌘. and Ctrl+. on Aqua:**
- **Tk:** real keystrokes could not be sent. `osascript … keystroke` needs Accessibility permission. It opened a System Settings Privacy prompt, which I cancelled by quitting System Settings, and I granted nothing. Tk `event generate` from a focused Entry proved that `<Control-period>` and `<Command-period>` both reach `_on_stop_key` and latch (`round4_tk_1400x900_report.json` `keys`). Whether Aqua's menu bar swallows a real ⌘. before Tk sees it is **not verified**.
- **Qt:** no keystroke test, for the same reason.
- **Web:** tested with real Chrome key events. Ctrl+. works.

## Tier G before

**(a) The "Selected:" readout, six models on SIM. Full text 159 characters:** "Stepper Probe (simulated), DC Probe (simulated), Chuck Positioner (simulated), Temperature Controller (simulated), Rotator (simulated), Red Percent (simulated)"

| View | Size | Cell width | Text needs | Shown | Effect on the table |
|---|---|---|---|---|---|
| Tk | 1400×900 | 863 px | 1047 px | 128 chars, end-elided "…Rotator (sim…" | none: the cell never grows (`width=1`); the full text is in a tooltip |
| Tk | 900×900 | 363 px | 1047 px | 51 chars "…DC Probe (simulated), C…" | none |
| Tk | 1400×900, 28 pt | 368 px | 2510 px | 21 chars "Stepper Probe (simul…" | none |
| Qt | 1400 / 900 / 1000 | 346 px | 858 px | middle-elided "Stepper Probe (simulated), DC…ted), Red Percent (simulated)" | none; the Setup dock stays about 1080 px and left-aligned |
| Qt | 28 pt | 539 px | 2002 px | middle-elided | none |
| Qt | 4 models | 346 px | 589 px (108 chars) | middle-elided | none |
| Web | 1400×900 | 461 px | n/a (wraps) | all 159, **3 lines**, right-aligned | Launch row 72 px tall; the drawer does not widen (scrollWidth 559 = clientWidth) |
| Web | 900×900 | 446 px | n/a | 3 lines | same |

**(b) The Gamepad Log element on a probe card**

| View | Size | Log box | Card or panel | Share |
|---|---|---|---|---|
| Tk | 1400×900 | 428×76 px, top at y 151 of the panel | panel 1380×640 | 5 % of the panel's area; 12 % of its height; the whole System control column below the buttons |
| Tk | 900×900 | 428×76 px, at y 339 | panel 880×640 | about 12 % of the height, the left column's lower half |
| Tk | 1400×900, 28 pt | 418×166 px, at y 236 | visible panel 1380×456 | **36 % of the visible panel height** |
| Qt | 1400×900 | 404×110 px (fixed `LOG_STREAM_PX`) | dock 466×794, panel 450×843 | 13 % of the panel height |
| Qt | 900×900 / 1000×700 | 312–328×110 px | dock about 300–333 px wide | 13 % |
| Qt | 28 pt | 622×110 px (does not scale) | panel 668×1231 in a 466 px dock | 9 %, and the box runs past the dock edge (horizontal scroll) |
| Web | 1400×900 | 406×104 px (`.feed`), label 825 px down the card | Stepper card 438×1150 | 11.6 % of the card height, 10.8 % of its area |
| Web | 1400×900, DC Probe (two-column card) | 414×104 | card 891×741 | 18 % of the card height |
| Web | 900×900 | 383×104 | card 415×1150 | 11.6 % |

Tk and Qt show an empty box. Web shows "No gamepad input yet. Choose a gamepad under Configuration, then enter manual mode to drive with it."

**(c) The Web rail's right side, where G2's Quit will go (CSS px, six models)**

| | 1400×900 | 900×900 |
|---|---|---|
| Rail height | 130 px (2 readout rows) | **428 px** (6 rows) |
| Readout column | x 154–1060; widest row ends 1057 | x 154–560; groups end at 436 (124 px unused inside the column) |
| Free gap before `.rail-side` | **27 px** | 148 px (124 unused + 24 gap) |
| `.rail-side` | x 1084–1257 (173 px): hint 1092–1178 (86 px, two lines) + Setup 1186–1257 (71 px), 44 px tall inside a 130 px rail | x 584–757, same parts |
| Stop | x 1281–1365, 84 px | x 781–865, 84 px |

A Quit the size of Setup (71 px plus a 8 px gap) added to `.rail-side` at 1400 px shrinks the readout column from 906 to about 827 px. Row 1 then holds 2 groups instead of 3 (3×282 + 2×28 = 902 > 827), and the rail grows to 3 rows (about +58 px). **Replacing the platform-specific hint (P7, 86 px) with Quit costs nothing**, and the side column shrinks by 7 px. At 900 px Quit costs nothing in either case, because the rail is already one group per row (IMP-2).

**(d) G1: closing a tab while the server runs**
- **Headed Chrome for Testing:** closing it (`page.close()`, then `browser.close()`) printed **nothing**. Chrome shut its sockets cleanly (FIN), so G1 did not reproduce that way.
- **Six parked keep-alive sockets, each reset with `SO_LINGER 0`** (what the owner's browser did, per G1): the pty terminal printed **111 lines**. That is 6 interleaved `Exception occurred during processing of request from ('127.0.0.1', 640xx)` / `Traceback (most recent call last):` blocks through `socketserver.py:697 process_request_thread` → `:362 finish_request` → `:766 __init__`, ending in `ConnectionResetError: [Errno 54] Connection reset by peer`, with separator lines of dashes.
- The full text is in `handoff/shots/round4_web_g1_terminal_before.txt`.
- The six threads' tracebacks interleave line by line, which makes the output even harder to read than one traceback per reset.

## Skill disagrees with the brief
- The critique `audit` touch-target rule (44×44) vs the brief's "controls compact". The Web Close links on cards and the rail ghost buttons are under 44 px tall. That is left for the owner; a desktop bench console is not a touch surface.
- The skill's contrast floor for the latch ring (3:1 non-text) vs the brief's pinned signal and trace. Measured on screen at 2.76:1 ring on face and 2.46:1 face on panel (Web). F6 is the owner's call.

## Already fixed or kept on purpose (not re-reported)
- F1 (errors never cover the stop): holds on screen in all three views. The Tk band sits above the stop bar, the Qt band is in the rail, and the Web dialog starts below the rail.
- F5: every Qt rail number is whole at 4 and 6 models and 28 pt (`text_wider_than_label: []` in every Qt report). Captions eliding to stubs is IMP-7.
- F7: the Tk disc scales from 83 px at 12 pt to 141 px at 28 pt, and "Clear" fits (`round4_tk_1400x900_font28_3_latched.png`).
- F8: Qt lamps show as a muted ring (Fault) and an ink ring (Stage connected), with no second red (`round4_qt_1400x900_lamps_3_latched.png`).
- F11: Tk and Web grey the latched commands; the Qt part is IMP-15.
- F9's Ctrl+.: works in Tk (synthetic) and Web (real keys).
- Web self-hosted Plex: loaded.
- The served `--muted #9099a7` is not the brief's `#8c95a3`. `palette.py:13-16` records it as a deliberate 4.63:1 contrast bump; the owner should know the brief's "exactly these" is off by one token.

## Could not verify
- Real ⌘. and Ctrl+. keystrokes on Aqua for Tk and Qt: there is no Accessibility permission, and the prompt was cancelled, not granted. It is also still unknown whether the Aqua menu bar eats ⌘. before Tk or Qt sees it.
- Opening the Tk Models and Setup menus: that needs a real click (Accessibility). Only the menubar strip was captured, and it shows "Python  Models  Setup".
- VoiceOver on lamps and ring names.
- The region picker on this Retina display (B8 context). It was not opened, because opening it draws a full-desktop overlay and the brief's time went to the priority items.
- G1 through a real Google Chrome or Safari tab: Google Chrome is not installed, and Safari automation needs permission. G1 was reproduced by the socket-reset method instead.
- Qt `isEnabled()` for the latched toggles (IMP-15).
