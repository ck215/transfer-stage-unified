# Design brief — "Bench sheet, tiered" (owner ruling 2026-09-25)

Supersedes `WEB_DESIGN_BRIEF.md` (the 2026-09-22 "instrument console"),
which stays in the tree as history. This brief binds all three views: Tk,
Qt and Web render the same instrument from one `palette.py` / `theme.py`.
The reference is canvas row E and its source:
`handoff/design-Tiered.md` (the tiers, the stop, what each toolkit does),
`handoff/design-Sheet.md` (tokens, type, spacing, radii, per-toolkit
fallbacks), the artboards `scratchpad/canvas/project/Tiered_*.dc.html` and
their renders `handoff/shots/design_Tiered_*.png`. Where this page and those
files differ, the artboards win.

## Rules that do not move

- Exactly six colour tokens (`palette.py`) plus white on the stop face.
  One red: stop, latch, fault, the unconfirmed mark. Nothing else.
- **Trace is for changing numbers and plot lines only.** Never a lamp, a
  tick, a card bar, a status word or a button.
- The stop is reachable and unmistakable on every screen: A's disc in the
  rail, always red (it does not go quiet), reads `Clear` when latched with
  a thicker ring; Ctrl+. is its one chord; no pop-up may cover it.
- Sentence case; copy says what happens; no platform-specific UI.
- Every `schema.ELEMENT_TYPES` entry renders in every view; every entry
  travels with every command; the wire bytes are pinned.
- No gradients, shadows, blur, textures. Depth is tone steps and rules.

## The three tiers (schema `section(..., tier=)`)

| Tier | Drawn | Holds |
|---|---|---|
| 1 | always | a device's X/Y/Z position; its speeds as a **slider beside an exact entry** (`entry(..., slider=(low, high))`), the gamepad choice (Tier K: picked every session), the mode toggles, Step; the heater's temperature and setpoint; the rotator's angle, step and moves; Red Percent's Red, Change, Start run, Stop run |
| 2 | behind one disclosure per model in a panel-toned well; the disclosure names the device (`disclosure`: "Configure Stepper Probe", "Configure Temperature Controller", "Red Percent details" …, Tier K 2026-09-26) | step sizes, targets, brakes; PID/ramp and the heater plot; the rotator's target and reset; **all of Red Percent's statistics, live plot, annotations, region, save/load, analysis** |
| 3 | behind a second disclosure ("Diagnostics") inside the tier-2 well | velocity, position age, the gamepad log button, fault and fault reason, the per-model stop as a small switch |

Tier 1 never scrolls away. Tiers 2 and 3 remember their open state per
model for the session. A view renders sections in schema order within each
tier; the Safety section is last and tier 3.

**Where the disclosure sits (Tier K, 2026-09-26).** At the foot of the
tier-1 body, left-aligned, directly above the well it opens: chevron, then
the section's `disclosure` words; open, the chevron turns down and the well
follows with no gap. Never in the entry's head: the press and what it
reveals are never a screen apart. The tier-3 "Diagnostics" disclosure sits
the same way inside the well.

## Two pages on the sheet (Tier K, 2026-09-26)

- **Overview** — the rail's first item, shown at launch, after Setup
  launches, and whenever the shown device is closed. Every launched model
  is a compact entry in the grid (three across, then two; one column under
  1000 px): its tier-1 body only, no wells, no disclosures. The entry's
  head is a press target ("Open" affordance at its right); a press opens
  the device.
- **Device page** — one model alone, full width, its readings `focal`, its
  tier-2 and tier-3 disclosures at the foot of its body; their open state
  is remembered per model for the session. The rail highlights the shown
  device; pressing Overview returns.
- The rail (disc, "Stop: Ctrl+.", Setup, Quit), the latched headline
  "Every model is stopped." and the tray are identical on both pages.

## Status by exception

Normal is silence. A readonly whose value is in `theme.QUIET_VALUES`
("Connected", "Idle", "No", "None", "--", "Not recording", empty) is not
drawn in tier 1 (it stays in state for the API and in tier 3 where
declared). The event tray shows warnings and errors only; one event, once.
A model whose stop did not confirm is marked at its own entry with a red
rule and the words "Stop not confirmed. Treat as live."

## Layout

- **Rail** (left, 248 px; 200 px under 1000 px wide): title and "Simulation,
  no hardware attached" / nothing when connected; the stop disc with
  "Stop: Ctrl+." under it; the page list: Overview first, then the models (the shown
  page highlighted; a press switches to it); Setup and Quit at the bottom.
- **Sheet**: entries, not cards. An entry is a 2 px ink rule, the model
  name, then its tier-1 body; entries are separated by whitespace. The
  opened model's readings are `focal`, a closed probe's `compact`, a
  single-value model's `primary`, a change `secondary` (`theme.READING_SIZES`).
  Captions are `theme.CAPTION_SIZE`, axis letters inline ("Position, steps"
  once per probe, then X Y Z).
- **Controls**: inputs are a panel-toned well with a muted underline, no
  box, right-aligned tabular numerals; `go` buttons ink-filled; neutral
  buttons outlined; disabled = sheet fill, 45 % ink, dashed muted edge;
  radii from `theme.RADIUS`.
- **Setup** keeps its one table with the Launch checkbox first; it is a
  drawer (Web), a tab (Tk) or a dock (Qt) as today, restyled.
- Under 1000 px the entries stack in one column; the rail narrows.

## Type

Text: Public Sans 400/500/600 (`theme.FONT_FAMILY`, fallback Helvetica).
Numerals and the two headlines ("Setup", "Every model is stopped."):
Archivo at stretch 118 %, weight 600, tabular figures
(`theme.NUMERAL_FAMILY`; Tk/Qt use a static "Archivo SemiExpanded" face when
installed, else the text face bold). Sizes per `theme.READING_SIZES`,
`CAPTION_SIZE`, and `theme.size()` for text steps.

## What each view owes this round

Render `tier` and `disclosure`; render `slider` beside its entry; the rail
with the disc; entries instead of cards; status by exception; the tray
rule; the new tokens through `theme` only (no view-local colour constants
derived from the old dark tokens); before/after captures at 1400×900 and
900×900 in the four states (launched, Red Percent details open, stopped,
setup); every existing test green or updated with the reason in the test.
