# Qt round 2 (qt3.md): `src/views/qt.py`

Worktree `rb-qt`, branch `rb-qt`. I merged `mvc-refactor` (`54ce0f1`, Stop system is neutral) first, then made one commit on top. Write set followed exactly: `src/views/qt.py`, `tests/test_view_qt.py`, `tests/test_view_qt_widgets.py`. Nothing pushed.
Mode: Impeccable Operate. This is a refinement: the palette, copy vocabulary and behaviour are kept, and the chrome was restructured to be the Web view's sibling.

## Screenshots

The screenshots are in `rebuild-handoff/shots/`. They are offscreen captures at 1400×900 built the way `app.py` builds the app: Controller + Setup, `hub.open()` on the main thread, the scan runs for real, Stepper Probe and Red Percent are set to SIM, then Launch, then `estop_all`.

| Pair | Before | After |
|---|---|---|
| `round2_qt_{before,after}_0_scanning.png` | The scan message sits in a "Scan" column and pushes the other columns. There's an empty "Selected" column, dark ↺ blocks, the Models void with a floating FULL STOP, and a huge empty Event Log. | The scan message is on the Devices line and wraps. The header is below it. Rescan is a quiet drawn icon. The empty state says what to do next, and the log tray is one line. |
| `…_1_setup.png` | "nothing selected" floats in a column of its own, "simulated"/"off" are bold labels, and there are three tabs plus three dock titles. | The Launch line reads `Selected  Stepper Probe (simulated), …  [Launch] [Relaunch] [Stop system]`. Status is a readout: trace when live, muted when "off". The rail replaces the tabs. |
| `…_2_launched.png` | The window grew to 1944×1267 to fit the panels' minimum sizes, so on a laptop it would run past the screen. The sidebar, the Models dock and the model docks all repeat the names. Red Percent's red "Stop" run button is filled. | Stays at 1400×900. Panels scroll inside their docks. The rail shows `Stepper Probe  X position 0 …` and `Red Percent  Current red 0.00 …` as large tabular trace numbers. Docks are sized in proportion to their content. |
| `…_3_latched.png` | "CLEAR FULL STOP" plus two red "LATCHED - click to clear" slabs: three reds, none of them round. | One round `Clear` with a trace ring on the rail, and the status reads "Stopped". |
| `round2_qt_after_4_narrow.png` | none | At 1000×700 the stop is still on screen. Red Percent scrolls horizontally. |
| `round2_qt_after_5_red_panel_latched.png` | none | The whole Red Percent panel: Safety has the mini stop reading `Clear`, "Stop" (end run) is a neutral button, and the empty plot says "No samples yet". |
| `round2_qt_after_7_tray_open.png` | none | The tray open: the log with sentence-case severities. `Info` is muted, and before this change it was unreadable panel grey. |

## Changed

Line numbers are in `src/views/qt.py` at `d0fe081`.

1. **Duplicated chrome.**
   - Removed the `QToolBar` (Setup / Models / Event Log) and the Models sidebar dock with its checkbox list. The model docks' own titles are now the only navigation, and the float glyph was dropped from them (`DeviceDock`: movable + closable).
   - A **rail** (`_build_rail`, :1948) is installed with `setMenuWidget`, so it sits above every dock and nothing can cover it. It holds:
     - the identity and a status line ("Scanning ports" / "N running" / "Stopped");
     - each open model's key numbers, chosen by `rail_elements` (:235), which is the Web view's `railElements` rule: flagged `rail=True` first, otherwise the first section's readouts, at most 4;
     - "Reopen [name]" ghost buttons for closed models (these replace the sidebar);
     - **Setup**, a `QToolButton` bound to the Setup dock's `toggleViewAction`, so the check state and the dock's visibility are one piece of state;
     - the stop object.
   - Setup still collapses on launch and comes back from that one control. The Setup dock is closable only.
2. **The void.**
   - Central widget = empty state (`_build_empty_state`, :2154): "No instruments running." / "Choose ports in Setup and press Launch." Once Setup is put away it also shows an "Open setup" button. It hides when any model dock is open.
   - The event log is a **tray in the `QStatusBar`** (`_build_event_tray`, :2186). It shows one elided line with the latest event, and the full text is in the tooltip. "Show events" opens a 150 px log.
   - A dock-based tray with a fixed height left a blank band under it that the docks never reclaimed. I measured that and moved the tray to the status bar.
   - Model panels are wrapped in a frameless `QScrollArea` (`_add_panel`, :2293), so the window's minimum size is 156×176 instead of forcing 1944×1267.
   - `_balance_docks` (:2321) sizes side-by-side docks by each panel's size hint.
3. **Stop object** (`StopButton`, :862):
   - A round disc sized in lines of the theme base font (4.6 lines; 2.9 for the mini).
   - Signal fill with a darker ring, which is signal mixed toward base, as the Web view's `color-mix` does.
   - Reads `Stop`, and `Clear` when latched, with the ring turning trace.
   - One 400 ms ring pulse on the latch edge (`QVariantAnimation`), not repeated while latched.
   - `setEnabled(False)` is refused.
   - Focus ring is ink, not trace, so keyboard focus never looks like the latch. Tab reaches it; nothing takes focus at boot.
   - A model's own stop (a toggle with a `danger` role) is the same object one size down (`_make_toggle`, :1404), with the schema's true/false text as its tooltip.
4. **Setup table.**
   - `is_action_row` (:258): a `layout="row"` section that contains a command. `PanelTable.add_bar` (:716) and `TableBar` (:765) render it as one line spanning the table: title, caption + wrapping value, then the commands aligned right.
   - The header row is claimed by the first *table* row, so Setup reads Devices line → header → six model rows → Launch line. No Scan or Selected column exists any more.
   - The table card is as wide as its columns (Maximum policy, left-aligned) instead of stretched across 1400 px.
   - Rescan is `#iconButton` with a drawn reload icon (`reload_icon`, :978). Its ink is muted, becoming text on hover, and it has a tooltip and an accessible name.
   - Status and every readout go through `_set_readout` (:1560). Live values are trace, values at rest are muted (`is_quiet_value`: off / None / False / nothing selected / …), and an empty value shows "—".
5. **Labels.** `sentence` and `sentence_case` (:219) port the Web view's rules word for word. They apply to form captions, section titles, column headers, bar captions, rail captions, and button and toggle text. Model names (row titles, dock titles) are left alone.
6. **Scale and states** (`stylesheet`, :269).
   - Font sizes are 10 / 12 / 14 / 17 pt (base 12, ratio 1.2) in one family from `theme.font()`.
   - `QPushButton`, `QComboBox` and `QLineEdit` each have hover, focus (2 px trace ring, as on the Web), pressed and disabled states. Toggles keep hover and focus because their per-state sheet is now selector-scoped.
   - Ghost and chrome buttons, dock title buttons, scrollbars, tooltips and selection colours are themed.
   - Colours come only from theme values, plus `rgba()`/`mix()` derivations of them. The source still contains no hex literal.
7. **Two stops / one red.**
   - A plain button with the `danger` role (Red Percent "Stop" = `end_run`) renders neutral (:361).
   - The region-picker rectangle is drawn in trace, not signal.
   - Severities in the event log: error = signal (a fault), warning = trace, info = muted.
   - After a latch, the only red on screen is the stop object or objects.
8. **Other defects I found.**
   - `[INFO]` in the log was drawn in the info role's fill (#2f363f on #1f242b) and could not be read.
   - Form fields grew to the card's width; an 800 px well held a 3-digit speed. They now use `FieldsStayAtSizeHint`.
   - Buttons in column sections stretched full width; they now keep their natural width.
   - Holder widgets painted a BACKGROUND block over cards and the rail (`#bare`).
   - An empty plot now says "No samples yet".
   - The stale label is now sentence case.

## Kept on purpose

- **Cards per section.** This is the incumbent Qt identity. The Web view uses rules instead, but this round refines rather than redesigns.
- **Red Percent's two-column split and `COLUMN_SPLIT_CARDS`.** Unchanged, as the lead's split ruling decided.
- **Model names in Title Case** (row and dock titles, the rail's group headings). They are proper names under the owner ruling (the names are "Red Percent", "Rotator", "Temperature Controller").
- **`Sync X: OFF` and other capitals of three letters or fewer.** This is the Web view's rule: only words of four or more letters are lowered.
- **The per-dropdown rescan.** It is still the only way to reload a dropdown's options (qt2 NOTES 6). I made it quiet, not removed.
- **"Stop system" is neutral.** That is the lead's `54ce0f1`, and nothing else competes with the stop object.
- **The Setup dock keeps its own title bar with a close ×.** It is a dock like the rest, and closing it puts it away.

## CORE CHANGE REQUESTS

1. `src/model/red_monitor.py:1228`: change `sch.button("Stop", "end_run", role="danger", …)` to `sch.button("Stop run", "end_run", role="neutral", …)`. Two reasons:
   - It is the second red in every view. The Web view still fills it red, dimmed, while the Qt view now neutralises the role in the renderer, so the schema should say what it means.
   - Its face "Stop" is the stop object's own word, so an operator reading "Stop" in two places has to work out which one halts the hardware. The design brief's copy rule is "Start run / Stop run".
2. `src/views/theme.py`: add a `RULE` (hairline) token and an `INPUT` (well) token. The Qt view derives `rgba(TEXT, .10/.18)` for rules and uses `BACKGROUND` for wells; qt2 raised the same gap. This is not blocking.
3. `src/views/theme.py:ROLES["info"]`: it is used as a text colour by the event log in every desktop view, where it is illegible (1.2:1 on base). The Qt view now maps info → `MUTED` locally. Tk probably shows the same "[INFO] is invisible" defect, which is the rb-tk agent's to confirm.

## Tests

- **Fast suite:** `pytest tests -m "not qt"` gave **1560 passed**, 101 deselected (base 1527; the +33 are new pure tests in `tests/test_view_qt.py`, which now has 80 tests).
- **Golden:** `tests/test_wire_golden.py` gave **78 passed**.
- **Qt:** `QT_QPA_PLATFORM=offscreen timeout 180 … tests/test_view_qt.py tests/test_view_qt_widgets.py -m qt` gave **101 passed** (my file; was 85). Both files together, unmarked: 181 passed. The lead re-runs the full Qt pass.

**Tests updated, each because it asserted the old look (the docstrings say so):**
- `test_the_stylesheet_dresses_the_table_and_the_toolbar` is now `…_and_the_rail`. The toolbar is gone.
- `test_a_readout_and_an_entry_do_not_look_the_same` now asserts a trace colour where it asserted bold ink.
- The sidebar tests (`test_the_sidebar_lists_…`, `test_checking_a_closed_model_reopens_it`, `test_a_reopen_that_fails_reverts_the_checkbox…`) became rail Reopen tests with the same intents: closed models are listed, clicking reopens, and a failed reopen does not raise.
- The three sidebar-width tests were replaced by no-wasted-column, proportional-dock and scroll-not-overflow tests.
- `test_the_full_stop_button_latches_and_relabels` now expects `Clear`.
- `test_the_stop_button_colour_comes_from_the_theme` now expects a signal fill and a trace ring.
- `test_a_models_own_stop_takes_the_whole_section_and_the_tall_metric` now expects the mini `StopButton`.
- `test_an_ordinary_toggle_keeps_its_caption…` now asserts the toggle is not a `StopButton`.
- `test_the_full_stop_button_is_tall_and_set_in_the_theme_size` now expects a disc sized from the font.
- `test_the_collapsed_setup_dock_comes_back_from_the_toolbar` is now `…from_the_rail`, with the same assertions.
- `test_the_toolbar_offers_an_entry_for_every_dock…` is now `test_there_is_one_navigation_not_two`.

**New tests:**
- pure: sentence case (parametrised), quiet values, `rail_elements`, `is_action_row`, control states, one red in the sheet, type scale, colour helpers;
- Qt: rail readouts and tick, stop never dimmed, one pulse per edge, empty state and its Setup path, tray one line / open / closed, info legibility, sentence-case captions, quiet readouts and "—", quiet rescan icon, and action lines (no invented column, header placement, span and wrap).

## UNVERIFIED

- **Offscreen only.** A real macOS display may differ in these places:
  - dock title glyphs, where the close button is the platform's own glyph with a themed hover;
  - font metrics for the disc size, where the Helvetica fallback was "Sans Serif" offscreen;
  - `QFont.Tag("tnum")` tabular figures, which are try/excepted if the Qt build lacks them.
- **Reduced motion.** Qt exposes no `prefers-reduced-motion`, so the 400 ms latch pulse always runs. It is one ring swell and the only animation.
- **At 1000 px the Red Percent dock scrolls horizontally.** That is acceptable to me because the stop is on the rail, but if the owner uses a small bench screen, Red Percent's split threshold may want revisiting.
- I did not run Tk or Web. The CCRs above touch all three views.

COMMIT: d0fe081
