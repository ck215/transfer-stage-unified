# Tk round 2

Branch `rb-tk`, merged `mvc-refactor` first (fast-forward to `54ce0f1`: Stop
system is neutral). Write set: `src/views/tk.py` and `tests/test_view_tk.py`
only. Not pushed.

**The coordinator stopped on-screen capture partway through the round.** The owner was
using this Mac. There are no `after_*` shots. The `interim_*` shots were
taken before that instruction, with everything below except the outline
frame on commands (the last fix). Final verification was done with tests and a
withdrawn-window build (`round2_tk_tree_after.txt`).

## Screenshots   (`rebuild-handoff/shots/`)

| file | shows |
|---|---|
| `round2_tk_before_1_setup.png` | boot. Oversized type (12 pt drawn 16 px), "Port / Gamepad / Status:" on every row, ⟳ glyphs, clipped "…othing selected", white Aqua scrollbar, centred silver tab, full-width FULL STOP bar |
| `round2_tk_before_2_launched.png` | Stepper Probe: one narrow column and an empty right half, "X Position:" captions, sunken entries at a different width from the readouts, events faded to unreadable |
| `round2_tk_before_3_latched.png` | "CLEAR FULL STOP" in a trace-yellow bar |
| `round2_tk_interim_1_setup.png` | Setup as a table: one header row, values only in rows, the Gamepad cell empty for rows with no gamepad, Devices and Launch as bars, mushroom `Stop` bottom-right |
| `round2_tk_interim_2_launched.png` | Stepper Probe in 3 columns at 1400 px (Coordinate frame + Safety / Configuration / System control), same value-column width, commands on one line, mini stop disc, lamp ring, events with ink latest line |
| `round2_tk_interim_2b_redpercent.png` | Red Percent in 3 columns, run ID in trace, plot empty state "no data yet", outlined danger `Stop` (disabled here) |
| `round2_tk_interim_3_latched.png` | both discs read `Clear` with a trace ring; the bar reads "Clear the stop on every model" |
| `round2_tk_tree_after.txt` | final code, withdrawn build (never mapped): fonts, grid cells, pack order, the latched state, chrome height, an elision sample |

## Changed   (defect, then fix, then `src/views/tk.py` line)

1. **Scale.** Tk on Aqua runs at `tk scaling` 1.33 (96 dpi), so 12 pt was 16 px. `_font(step)` (:118) is `theme.font(RATIO**step)`, with RATIO 1.125. On Aqua it passes the points as pixels (a Mac point is a logical pixel), set in `_configure_styles` (:1985). Sizes are SMALL 11 / BASE 12 / STEP_1 14 / STEP_2 15. Captions and entries use BASE, readouts STEP_1, section titles STEP_1 bold (one step up), and the panel name STEP_2. Tabs are left-aligned on BASE: ttk now runs `clam`, coloured from the theme, because Aqua's ttk ignores colours and always centres tabs.
2. **Clipping.** A readout asks for no width (`width=1`) and takes its cell. `_fit_readout` (:1305) and `_elide` shorten it with "…". `_Tooltip` (:440) shows the whole text. The Setup status column carries the grid weight. The Scan and Selected values expand to fill their bar. A combobox shows its full value as a tooltip when it is longer than the box.
3. **One header row.** `_plan_tables` (:880) looks ahead. A caption that two or more row sections share becomes a header column (Port, Gamepad, Status), and each control goes under its caption. A row section whose captions are its own (Devices/Scan, Launch/Selected) becomes a bar spanning the table, with its caption inline. Hairlines sit under the header and above the Launch bar. `_align_table` is gone, since columns now line up by caption.
4. **Labels.** `_sentence`/`_label` (:147, :160) port the Web view's `sentence`/`sentenceCase`. They are used for captions, section titles, buttons and toggle faces. Row names keep their capitals ("DC Probe"). The schema text is unchanged.
5. **Events.** The fade was a real bug, not a style choice: info lines were drawn in the `info` role's *background* colour, `#2f363f`, on the log's own dark background. Now the tags are info MUTED, `latest` TEXT, warning TRACE and error SIGNAL. `latest` moves to each new line, and tag creation order sets priority (`_build_event_panel` :2114, `_show_event` :2446).
6. **The stop object.** `_Mushroom` (:506) is a round signal disc with a darker ring and an inset highlight. It reads `Stop`, or `Clear` once latched with a trace ring, pulses once on the edge (6 frames, about 400 ms, cancelled on close), and is never dimmed. It has a hover ring, a focus ring, and responds to Return/Space. It sits bottom-right in `_stop_bar`, which is packed before the notebook. A muted line beside it says "Stop every model" or "Clear the stop on every model". A model's own Safety toggle (`is_estopped`) renders as the same disc at 34 px, like the Web `.mini`. Checked in the withdrawn build: the chrome the notebook may not take is 218 px of a 900 px window, and the panels scroll.
7. **Stepper Probe panel.**
   - Column sections are *runs*. `_reflow`/`_lay_out_run` (:1017, :1039) give them 1–3 columns by viewport width (`COLUMN_PX` 400), filled shortest-first. That is 1 column when narrow, 2 at the default 1100 px and 3 at 1400. Never more columns than there are sections.
   - Each section has one value column with a `VALUE_PX` minimum. Entries, dropdowns and readouts all fill it with sticky `ew`, so they share a width and a right edge. The caption column takes the slack.
   - Consecutive commands share one line (`_command_slot` :1134).
8. **Two stops.** Only the stop object is *filled* red. `_command_colors` (:1228) outlines a danger command (Red Percent "Stop", Rotator "STOP", heater "Stop System") in signal and a warning command in trace, as the Web view does. The plot line was drawn in `danger` red and is now TRACE. The region-picker band is TRACE. Stop system is neutral, as the lead merged.
9. **Control states and vocabulary.**
   - **Commands:** hover (a step lighter), a trace focus ring on the outline, Return/Space press, disabled pair plus no action. The outline is a 1 px *frame* around the label (`_button_label` :1188), because Aqua does not draw a Label's highlight ring and an OFF toggle rendered as bare text.
   - **Entries:** dark field, hairline border, trace focus ring.
   - **Comboboxes:** `postcommand` re-reads options when opened, which replaces the ⟳ glyph (a unicode stand-in for an icon, and redundant with the 2 s refresh).
   - **Lamps:** round lights. Lit is trace, or signal for a danger lamp; unlit is a muted ring. Before, the Aqua lamp label was invisible.
   - **Readouts:** trace text with no box, `--` muted when empty.
   - **Scrollbars:** shown only when content overflows.
   - **Empty states:** the image shows "No figure yet". Closing the last model brings Setup back instead of leaving an empty notebook (`_remove_panel` :2307).

## Kept on purpose

- Per-row captions are gone in Tk, but the Web view still repeats them. The brief asked Tk for one header row. The Web view may want the same.
- Toggle captions ("Autonomous:") are not drawn. The toggle face names the action, which is unchanged from before.
- The bottom stop bar, not the Web rail's top-right position, as the brief says ("the bottom bar is the right place").
- Three columns at 1400 px goes past "two columns on wide windows". At 1400 px, two columns put a caption about 500 px from its value. Three give each column about the Web card's width. The default 1100 px window gets two. This is a judgement call; set `MAX_COLUMNS = 2` to revert.
- "Running: False" and "(stale)" are model values and panel wording, not view copy I own.
- The menu bar (Models / Setup) is unchanged.

## CORE CHANGE REQUESTS

1. `src/views/theme.py`, `FONT_FAMILY = "Helvetica"`. The brief names IBM Plex Sans. Tk can use it only if it is installed or registered with the OS, and the view cannot register a font. Suggestion: document Plex as the preferred family, with a `font.families()` fallback helper in theme. Low priority.
2. `src/views/theme.py`, `ROLES["info"]`/`SEVERITY_ROLE`. The event-log defect came from taking `colors(role)[0]` (a background) as a text colour. Suggestion: add a `SEVERITY_INK = {"info": MUTED, "warning": TRACE, "error": SIGNAL}` so Qt cannot repeat it. Tk hard-wires the same mapping for now (:2114).

## Tests

- Fast suite: **1543 passed**, 85 deselected (base 1527; +16 new), exit 0.
- Golden gate: **78 passed**. The Qt pass was not run (agent rule).
- `tests/test_view_tk.py` is now 108 tests (was 92).
  - **Harness:** `ttk.Scrollbar`, `grid_remove`, `create_oval`/`create_arc`, `tag_remove`.
  - **Rewritten because they asserted the old look:**
    - toggle text: "RUNNING" is now "Running".
    - lamp: was fill plus a highlight ring, now a canvas oval.
    - empty readout: now drawn on the panel, in trace.
    - readout/entry placement: from `FIELD_WIDTH` and `sticky="w"` to one `VALUE_PX` column with `ew`.
    - readout-not-a-box: from sunken to bordered field.
    - short-row status: from right-aligned columnspan to the same column by caption.
    - status slack: anchor `w`.
    - column stacking: the button now sits in a strip.
    - stop bar pack order: now asserted on `_stop_bar`.
    - stop label: faces Stop/Clear and a trace ring, instead of a yellow fill.
    - event colours: muted/ink/trace/signal, with `latest`.
  - **New:**
    - command outline frame and focus
    - danger command outlined, never filled
    - hover and disabled states
    - sentence case
    - elision with tooltip
    - one header row
    - bar row
    - commands on one line
    - columns by width
    - dropdown `postcommand`
    - one pulse on the edge
    - keyboard stop
    - a model's mini stop disc
    - last model closed brings Setup back
    - Aqua points as pixels
    - the type scale ratio
  - No test was deleted or skipped. The colour/font-literal test still passes unchanged.

## UNVERIFIED

- **The final look on screen was not captured.** The coordinator stopped capture partway through the round. The one change after the interim shots is the 1 px outline frame on commands, meant to make OFF toggles and the disabled Relaunch visible. Also unseen: the focus rings, the pulse animation, the tooltip, and the narrow (1-column) layout. Capture command for the lead (moves a real window; run when the Mac is free):
  ```
  S=<scratch>/ui/tk   # capture.py is there; copy it anywhere
  cd rb-tk && EXTRA=1 ../main/.venv/bin/python $S/capture.py $PWD ../rebuild-handoff/shots/round2_tk_after 1400x900
  ../main/.venv/bin/python $S/capture.py $PWD ../rebuild-handoff/shots/round2_tk_after_narrow 900x900
  ```
  In the shots, check four things:
  - OFF toggles ("Enter autonomous mode", "Sync X: off") show a visible outline.
  - Relaunch is visibly a disabled button.
  - The 900 px-wide capture shows one or two columns, and the Setup table fits or scrolls.
  - The stop disc is fully on-screen at 900 px tall.
- The capture script re-pins the window to `+40+40` before each shot. In one run the OS placed the window at y=1061, mostly off-screen. That was window placement, not layout (the root was 1400×900), but re-check it in the shots.
- `clam` combobox popdown list colours come from `option_add` and were not seen open.
- Aqua detection is `tk windowingsystem == "aqua"`. On X11/Win32 fonts stay in points, which was not seen.
- The Impeccable detector is for web targets and was not run on a Tk file.

COMMIT: 9e572c2
