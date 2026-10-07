# Control sheet (prefix `Sheet`): design spec

Artboards: `scratchpad/canvas/project/Sheet_{Launched,Setup,Stopped,Narrow}.dc.html`
Captures: `handoff/shots/design_Sheet_{Launched,Setup,Stopped,Narrow}.png`

## Direction

The station becomes a lab run sheet: a cool paper ground where each model is an entry of large, characterful numerals under one short caption, and alignment and whitespace do the grouping instead of boxes. Configuration sits collapsed behind each reading and opens in place into a single tone-step well, so the task in front of the operator is always the focal object. Status is reported by exception: normal is silence, and the stop is a compact red block that always sits in the same place. When latched it grows, reads Clear, and the page's headline says what happened.

Critique round 6 changes folded in: caption-to-reading size ratio of 3× or more (13px caption over 40–56px numerals; axis letters inline, so "Position, steps" is written once per probe). Trace colour only on values that change. Lamps, ticks, rules and status words are ink or muted. No value appears twice (the rail index carries names only). No "Connected", "Fault No" or "SIM" restated per model. The tray shows warnings and errors only. The Rotator's unconfirmed stop is marked at its own readout. The per-model stop is a small switch that is red only when latched. There are no coloured card bars.

## The six tokens

| Token (palette.py) | Role | Hex |
|---|---|---|
| `BACKGROUND` (base) | the sheet, where every reading sits | `#f5f7f9` |
| `SURFACE` (panel) | the rail, open-configuration wells, neutral buttons, input fills on the sheet | `#e8ecf0` |
| `TEXT` (ink) | text, rules, the "go" button fill, lamps, toggles | `#141c26` |
| `MUTED` | captions, units, input underlines, axes, frozen readings | `#56606d` |
| `SIGNAL` | stop block, latched switch, unconfirmed mark, error mark. Nothing else | `#c21f1a` |
| `TRACE` | live, changing numbers and plot lines. Nothing else | `#2440c4` |

Pure `#ffffff` appears only as stop-face text and the latched switch's knob. Measured on the rendered pages: exactly these seven values, nothing else (script check over every inline style and SVG fill/stroke).

Contrast (WCAG 2.x, measured):

| Pair | Ratio |
|---|---|
| ink on panel | 14.45:1 |
| muted on panel | 5.38:1 |
| trace on panel | 6.79:1 |
| white on signal | 5.99:1 |
| signal on base | 5.58:1 |
| ink on base / muted on base / trace on base / signal on panel | 15.98 / 5.94 / 7.51 / 5.05 |

Muted input underlines on base are 5.94:1, well above the 3:1 non-text floor, so a control's edge is identifiable without a box.

## Type

- **Numerals and display:** Archivo, variable, at `font-stretch: 118%` (stop face 125%), weight 600 (stop 700), tracking −0.02em, `font-variant-numeric: tabular-nums`. It is used for every reading, the stop face, and the two page headlines ("Setup", "Every model is stopped.").
- **Text:** Public Sans 400/500/600 for everything else: names, captions, controls, inputs, the tray.
- **Scale (px):** 12 field label and plot tick · 13 caption, unit, hint · 14 button, toggle, nav · 15 body, input, small reading · 17 model name (compact) · 20 model name (opened) · 26 secondary reading (change) · 28–32 compact and frozen readings · 40 primary reading · 48–56 focal reading (the opened model) · 36/44 page headline · 40/52 stop face (normal/latched).
- **Tabular numerals:** every reading, every numeric input (right-aligned), timestamps, the Rows count, and plot tick labels (for SVG and matplotlib, pick a numeral face with tabular figures by default).
- Sentence case everywhere. No all-caps, no letter-spaced eyebrows, no monospace.

## Spacing and radii

- Spacing steps: 4, 6, 8, 12, 14, 16, 20, 24, 28, 32, 44 (column gutter), 64 (Setup side margin).
- Entry: a 2px ink rule on top, 14px to the name, then 18px between rows. Entries are separated by 30–36px of whitespace. There are no card edges.
- Radii: 4px top corners on underline inputs; 6px on buttons, toggles and the model nav highlight; 9px on the switch track (a pill, small control only); 10px on the stop block and the open-configuration well. No other rounded containers exist.

## What changes in `palette.py` / `theme.py`

`palette.py`: the six values above. `ACCENT = TRACE` unchanged. `GRID` becomes `SURFACE` (panel hairlines on the base sheet), or `mix(BACKGROUND, TEXT, .08)`.

`theme.py`, existing members re-pointed:
- `FONT_FAMILY = "Public Sans"`.
- `ROLES["go"] = (TEXT, BACKGROUND)`: the pressed-first button is ink-filled, not a panel step. `neutral = (SURFACE, TEXT)`, `info = (BACKGROUND, TEXT)`.
- `ROLES["warning"] = (SURFACE, TEXT)`. Warning is no longer trace, because trace is reserved for changing values. `SEVERITY_INK["warning"] = TEXT` and `SEVERITY_MARK["warning"] = TEXT`, drawn as a hollow square. Error keeps a solid `SIGNAL` square.
- `toggle_colors`: an ON toggle is ink-filled; an OFF toggle is outlined in ink on the ground. The `danger` role (per-model stop) is drawn as a switch (see below), not as a filled role button.
- `DISABLED = (BACKGROUND, mix(BACKGROUND, TEXT, .45))` plus a dashed or 1px `MUTED` edge.
- `RULE = SURFACE`, `RULE_STRONG = TEXT` (2px entry head rule). `WELL` is context-dependent: inputs on the sheet fill with `SURFACE`, inputs inside a panel well fill with `BACKGROUND`.
- `INPUT_BORDER = MUTED`, applied as a 1.5px bottom edge only.
- `SPACE` gains 20, 28, 32, 44. `TYPE_RATIO` stays 1.2 for text steps. Readings use the separate numeral scale below.

New theme members needed:
- `NUMERAL_FAMILY = "Archivo"` and `NUMERAL_STRETCH = 118` (Tk/Qt: a static "Archivo SemiExpanded SemiBold" instance; see below).
- `READING_SIZES = {"focal": 56, "primary": 40, "compact": 32, "secondary": 26}` (px), with the rule that only `readonly(..., rail=True)` values of the opened model get `focal`.
- `CAPTION_SIZE = 13`, and `AXIS_LETTER` rendering, so readouts of the form `"X Position:"` in one section collapse to one caption plus inline letters. This is a view-side label transform, not a schema change.
- `STOP_BLOCK = {"normal": (rail width, 92), "latched": (rail width, 132)}` and `STOP_RING = (SIGNAL, 3, 4)`: a latched ring 3px wide, offset 4px.
- `SWITCH = {"track": (30, 18), "off_edge": MUTED, "on_fill": SIGNAL, "knob_on": "#ffffff"}` for the per-model stop.
- `RADIUS = {"control": 6, "input": 4, "well": 10, "stop": 10}`.
- `STATUS_BY_EXCEPTION = True`, meaning views omit readonly status values equal to their normal value ("Connected", "No" fault, "None" reason, "Idle"). A test asserts the list of normal values per element key.

Model-rendered figures (`plot_data.py`) draw on `BACKGROUND` with `SURFACE` gridlines, a `MUTED` x-axis only, and a `TRACE` 1.75px line. On a frozen (latched) model the line is drawn in `MUTED`.

## What Tk and Qt cannot render exactly, and the nearest flat equivalent

| Web treatment | Tk | Qt |
|---|---|---|
| Archivo at `font-stretch:118%` (variable axis) | Install the static instance "Archivo SemiExpanded" (SemiBold) and name it directly; Tk has no axis control | Same static instance; `QFont.setStretch(118)` works only with some variable-font backends, so prefer the static file |
| Bottom-only 1.5px input underline | Flat `Entry` (`relief=flat, bd=0`) above a 2px-high `Frame` in `MUTED` | Stylesheet `border:0; border-bottom:1.5px solid` renders exactly |
| 6/10px radii | Square corners on widgets. The stop block and wells can be a `Canvas` rounded rectangle; otherwise square | Stylesheet `border-radius` renders exactly |
| Switch (track and knob) | `Canvas`-drawn 30×18 switch bound to the toggle command, or a `ttk.Checkbutton` with a flat indicator as the fallback | `QCheckBox::indicator` stylesheet with a drawn track; the knob needs two indicator images or a small custom `paintEvent` |
| Latched ring with offset | Nested frames: `SIGNAL` frame, 4px `SURFACE` gap frame, then the stop | Same nesting, or `border` plus `margin` in a stylesheet |
| `opacity:.55` on disabled | No opacity: `DISABLED` derived colour via `mix()` | Same derived colour (stylesheet opacity is unreliable on native widgets) |
| Dashed disabled edge | Not available on buttons: 1px solid `MUTED` edge instead | `border:1px dashed` renders |
| Underlined disclosure with offset and muted underline colour | Font `underline=1` in ink; no offset or colour control | `text-decoration: underline` only; no offset |
| Custom chevron on selects | `ttk.Combobox` default arrow, flat style | `QComboBox::down-arrow` needs an image; keep the default arrow |
| Inline SVG plot | Matplotlib figure or `Canvas` polyline, same colours and weights | Same figure (`FigureCanvasQTAgg`) |
| 2px entry head rule | 2px `Frame` in ink | `border-top:2px solid` on the entry frame |

## What the direction gives up

- **The dark room.** A paper ground is wrong under dimmed microscope lighting. No dark variant is offered; `palette.py` would need a second set.
- **Some at-a-glance density.** Six models fit one 1400×900 screen, but only one model's configuration is open at a time, and secondary readouts (position age, fault reason, motion state) are hidden until abnormal.
- **The stop is quieter while nothing is latched** (92px in the rail, not the loudest object). It is always in the same top-left place with the Ctrl+. hint under it, and it grows and rings once latched.
- **Explicit reassurance.** Operators used to reading "Connected" and "Fault No" see nothing when all is well. That needs a sentence in the operator notes.
- **Blue numerals** may read as links to Web-trained eyes. This is the price of a cool accent that is neither the red nor a warm cliché.

Printability: the ground and every token print as-is. A print stylesheet hides the rail and tray and flows the entries two up, which makes a run summary.

Copy this board introduces (not in `src/` today, the owner should confirm): "Stops every model", "Press to clear the stop", "Simulation, no hardware attached", "Leave autonomous mode" / "Leave manual mode" (the toggle's on-text), "Configure" / "Hide configuration", "Every model is stopped.", "Moves, steps and runs are refused until the stop is cleared.", "Stop not confirmed", "Not found on any port", the Setup subtitle, and the Narrow "Go to model" jump menu (a view affordance, not a schema element).

## `ELEMENT_TYPES` coverage

| Element type | Where it appears |
|---|---|
| `readonly` | Every reading. Launched: Stepper Probe X/Y/Z (focal 56px), Velocity, Temperature and Setpoint, Rotator Position, Red and Change. Stopped: Rows, frozen readings, Rotator "Motion state: Unknown" (shown because abnormal). Setup: the Scan line and the Selection sentence |
| `entry` | Launched: Stepper Probe configuration well (X/Y/Z step size, Target X/Y/Z dist, Autonomous/Manual speed, Brake speed (slow), Brake distance (steps)). Stopped: Red at least |
| `button` | Launched: Step, Gamepad log…, Move −, Move +, Home, Start run (disabled), Stop run. Setup: Refresh, Cancel scan (disabled), Stop system, Launch. Rail: Setup, Quit. Stopped: Reset baseline, disabled Step/Move/Home/Start run |
| `toggle` | Launched: Enter autonomous mode (off), Leave manual mode (on). Stopped: Sync X (on), Sync Y (off), Enter autonomous mode (disabled). Every artboard: the per-model Stop switch (a `danger` toggle), off on Launched/Narrow and latched "Stopped" on Stopped |
| `checkbox` | Setup: the Launch column, one per model (Rotator unticked) |
| `dropdown` | Launched: Gamepad. Setup: Port and Gamepad per row (disabled on the unticked row). Stopped: Position source (disabled while latched), Plot (2D/3D) |
| `region_select` | Stopped: "Set capture region…" in Red Percent's Control |
| `file_save` | Stopped: "Save…" |
| `file_open` | Stopped: "Load run…" |
| `plot` | Launched and Narrow: Temperature over time, Red % over time (trace line). Stopped: a frozen plot draws in muted |
| `image` | Stopped: Analysis plot (the model-rendered figure) with its file caption |
| `indicator` | Launched: Running (on, ink lamp, beside the run ID). Stopped: Running (off, hollow lamp) |
| `log_stream` | Launched: "Gamepad log…", the detached log opens a window |
| `internal` | Not rendered (by definition) |

## Process notes

- Impeccable `context` ran and returned NO_PRODUCT_MD / BUILD_INIT_REQUIRED. Init, the interview, and `concept-seed` were not run: this was a subagent with no channel to the owner, and the lead had already assigned the direction. The board is code-led against this brief. `impeccable detect --json` over the four artboards returned no findings.
- Self-review was one render, one fix batch (clipped Configure links, colliding field labels, wrapped Setup names, velocity spacing), and one re-render. A final one-line fix to Red Percent's Configure link was verified by measurement (no control under the tray on any artboard), not by a third visual pass.
