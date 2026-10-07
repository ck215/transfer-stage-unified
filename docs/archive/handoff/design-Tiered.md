# E. Bench sheet, tiered (prefix `Tiered`) — the lead's refinement of C, 2026-09-25

Artboards: `scratchpad/canvas/project/Tiered_{Launched,Details,Stopped,Narrow}.dc.html`
(generator `scratchpad/lead/gen_e.py`). Captures: `handoff/shots/design_Tiered_*.png`.
Canvas row E: https://claude.ai/artifact/Ddu9NiNUpvztiRzPj8CUec

## What the owner asked for (ruling 2026-09-25)

- Some features are common use, others highly specialised; the specialised
  ones must not be prominent unless requested. Red Percent's statistics and
  live plotting are **on demand**, never implied.
- The most important controls: **speed**, commonly adjusted, as a **slider
  in addition to an entry field** (both; the entry keeps precision), and the
  **current X/Y/Z position** of a device.
- Step sizes are less common but must stay accessible. The **controller
  (gamepad) log is a debug-tier resource**.
- Base direction **C (Control sheet)**, with **the full-stop button in the
  style of A's circles**.

## The three tiers, as drawn

| Tier | Where | What |
|---|---|---|
| 1, always visible | the entry's body | X/Y/Z position (focal 52 px on the opened model, 32 px compact); Autonomous speed and Manual speed each as slider + exact entry + unit; the two mode toggles; Step. Temperature: the reading and the setpoint entry. Rotator: the reading, "Move by", Move −/+, Home. Red Percent: Red and Change, Start run / Stop run, the running mark. |
| 2, one press away | `Configure` (probes) / `Details` (others), a panel-tone well under the entry | Gamepad choice, X/Y/Z step sizes, target distances, brake speed and distance (DC). Temperature: PID and ramp, a trend. Red Percent: frame rate, frames, rows, baseline, red threshold, the live plot, annotations (specimen, tip, note, tilt), region picker, reset baseline, save/load, the 3D analysis figure, position source. |
| 3, diagnostics | `Diagnostics` inside the Configure well, a rule-marked strip | Position age, fault and fault reason, **Gamepad log…**, the per-model stop switch. |

Tier 1 never scrolls away; tiers 2 and 3 remember their open state per model
for the session. Status by exception (no "Connected", no "Fault No" outside
Diagnostics, "Not recording" only when a run is not running).

## The stop

A's disc (136 px, red face, red ring, white face text "Stop") set in C's
left rail under the title, with "Stop: Ctrl+." beneath. **Always red**: the
standing rule that the stop looks the same in every state is kept; the
"quiet while nothing moves" idea from A/B/D is NOT applied here (the owner
has not ruled on it). Latched: the face reads "Clear", the ring thickens
from 3 px to 6 px, and the rail gains the sentence "Stopped: every model
latched" with a red square; the page headline reads "Every model is
stopped."

## Tokens, type, spacing

Identical to C (`handoff/design-Sheet.md`): base `#f5f7f9`, panel `#e8ecf0`,
ink `#141c26`, muted `#56606d`, signal `#c21f1a`, trace `#2440c4`; white only
on the stop face. Archivo (stretch 118 %, 600) for numerals and the headline,
Public Sans for everything else. The slider: a 4 px panel track, ink fill to
the value, an 18 px ink thumb with a 2 px base halo; disabled = muted.

## What it costs across the three views

- **Schema**: a `tier` attribute on sections or elements (`1` default, `2`
  behind the model's Configure/Details, `3` behind Diagnostics), read by all
  three renderers; a `slider=True` hint on a numeric `entry` (or a new
  `slider` element that pairs a range with the entry; the Panel keeps
  receiving the value through the same writable attribute, so the wire is
  untouched). Red Percent's plot/image/statistics move to tier 2 in its
  schema; the probes' `gamepad_log`, `position_age`, fault fields to tier 3.
- **Tk**: `ttk.Scale` + `Entry` for the slider pair; a collapsible frame per
  tier (already have the Setup minimise pattern); A's disc is a Canvas.
- **Qt**: `QSlider` + `QLineEdit`; `QToolButton` with an arrow for the
  disclosure; the disc is the existing mushroom painter.
- **Web**: `input[type=range]` + `input`; `<details>`-style disclosure with
  the open state kept per model; the disc exists.
- Palette/theme: C's changes (`design-Sheet.md`), nothing further.

## Open for the owner

1. The slider's range per speed field (drawn 0–1000 steps/s autonomous,
   0–400 manual): take the model's `Param` bounds.
2. Whether tier 2 opens per model (drawn) or one at a time across the page.
3. Whether the per-model stop switch belongs in Diagnostics (tier 3, drawn)
   or beside the mode toggles (tier 1).
