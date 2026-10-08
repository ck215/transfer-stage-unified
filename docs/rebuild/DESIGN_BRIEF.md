# Design brief — "Bench sheet, tiered" (owner ruling 2026-09-25)

Supersedes `WEB_DESIGN_BRIEF.md` (the 2026-09-22 "instrument console"),
which stays in the tree as history. **Since 2026-10-07 the Web view is the
only frontend** (owner ruling): this brief binds the Web view. It used to bind
all three views (Tk, Qt and Web, one `palette.py` / `theme.py`); `views/tk.py`
and `qt.py` are frozen at `413f504`, unregistered, and no longer follow it.
Where a sentence below names Tk or Qt it records what those views did, and the
"What each view owes this round" section is history. The Web renders the
instrument from `palette.py` / `theme.py`; the procedure rules are in "The
procedure (2026-10-07)" at the end.
The reference is canvas row E and its source (the handoff files and artboards named here were removed with the old handoffs; history: tag pre-root-cleanup-2026-10-07, so the text below is the surviving record):
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
  rail, always red (it does not go quiet), reads `Clear` only while EVERY
  model is latched (a partial stop keeps it a working Stop; `views.base.stop_words`), with
  a thicker ring; Ctrl+. is its one chord; no pop-up may cover it.
- Sentence case; copy says what happens; no platform-specific UI.
- Every `schema.ELEMENT_TYPES` entry renders in every view; every entry
  travels with every command; the wire bytes are pinned.
- No gradients, shadows, blur, textures. Depth is tone steps and rules.

## The three tiers (schema `section(..., tier=)`)

| Tier | Drawn | Holds |
|---|---|---|
| 1 | always | a device's X/Y/Z position; its speeds as a **slider beside an exact entry** in percent of the device's ceiling (`entry(..., slider=(0, 100))`, steps/s as a quiet secondary readout under it), the gamepad choice (Tier K: picked every session), the mode toggles, Step; the heater's temperature and setpoint; the rotator's angle, step and moves; RGB Analysis's Current Red, Red Change, Start run, Stop run (hosted on the Transfer Map's page, its live group behind its details; see "The procedure") |
| 2 | behind one disclosure per model in a panel-toned well; the disclosure names the device (`disclosure`: "Configure Stepper Probe", "Configure Temperature Controller", "RGB analysis details" …, Tier K 2026-09-26) | step sizes, targets, brakes; PID/ramp and the heater plot; the rotator's target and reset; **all of RGB Analysis's statistics, channels, annotations, region, save/load, analysis and the Estimators comparison** (its live plots left the live view on 2026-10-07; the trace is drawn once, at review) |
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

- **Dashboard** (named "Overview" until the owner renamed it, 2026-10-08) — the rail's first item, shown at launch, after Setup
  launches, and whenever the shown device is closed. Every launched model
  is a compact entry on a standard tile (owner 2026-10-08, replacing rows of
  three): one grid column wide or two ("Wide"), a whole number of grid rows
  tall; three columns, two under 1000 px, one under 760. The viewer
  reorders tiles by dragging "Move" or with its arrow keys; the order and
  the Wide tiles are kept per browser. Its tier-1 body only, no wells, no
  disclosures. The entry's
  head is a press target ("Open" affordance at its right); a press opens
  the device.
- **Device page** — one model alone, full width, its readings `focal`, its
  tier-2 and tier-3 disclosures at the foot of its body; their open state
  is remembered per model for the session. The rail highlights the shown
  device; pressing Dashboard returns.
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
  "Stop: Ctrl+." under it; the page list: Dashboard first, then the models (the shown
  page highlighted; a press switches to it); Setup and Quit at the bottom.
- **Sheet**: entries, not cards. An entry is a 2 px ink rule, the model
  name, then its tier-1 body; entries are separated by whitespace. On the device page the
  shown model's readings are `focal`; on the Dashboard a probe's are `compact`, a
  single-value model's `primary`, a change `secondary` (`theme.READING_SIZES`).
  Captions are `theme.CAPTION_SIZE`, axis letters inline ("Position, steps"
  once per probe, then X Y Z).
- **Controls**: inputs are a panel-toned well with a muted underline, no
  box, right-aligned tabular numerals; `go` buttons ink-filled; neutral
  buttons outlined; disabled = sheet fill, 45 % ink, dashed muted edge;
  radii from `theme.RADIUS`.
- **Setup** keeps its one table with the Launch checkbox first; it is a
  drawer in the Web (it was a tab in Tk and a dock in Qt).
- Under 1000 px the entries stack in one column; the rail narrows.

## Type

Text: Public Sans 400/500/600 (`theme.FONT_FAMILY`, fallback Helvetica).
Numerals and the two headlines ("Setup", "Every model is stopped."):
Archivo at stretch 118 %, weight 600, tabular figures
(`theme.NUMERAL_FAMILY`; Tk/Qt use a static "Archivo SemiExpanded" face when
installed, else the text face bold). Sizes per `theme.READING_SIZES`,
`CAPTION_SIZE`, and `theme.size()` for text steps.

## What each view owed that round (history: Tk and Qt are retired)

Render `tier` and `disclosure`; render `slider` beside its entry; the rail
with the disc; entries instead of cards; status by exception; the tray
rule; the new tokens through `theme` only (no view-local colour constants
derived from the old dark tokens); before/after captures at 1400×900 and
900×900 in the four states (launched, RGB analysis details open, stopped,
setup); every existing test green or updated with the reason in the test.

## Signature (2026-09-27): the aesthetic ruling

The layout above is unchanged. On it, the owner chose the **Signature**
look (round 3 on the Tactile direction; spec `handoff/tactile3-Signature.md`,
renders `handoff/shots/tactile3_Signature_*.png`, canvas
https://claude.ai/artifact/CbDb1WtYKZRJddarJZaU8V): "Signature looks great,
I'd love to have that be our layout." Implemented as drawn, including the
two options the spec left open (the ink collar at idle; the rail lamp for
an unconfirmed model). The eight rule changes it proposed are ratified:

1. Depth is tone steps, rules and the key lip. Exactly two shadow tokens
   (`SHADOW_RAISED`, `SHADOW_INSET`) and the stop's solid SKIRT offset,
   all Web-only garnish that no state depends on. No gradients or textures.
2. Every raised part is one family: a face, a 1.5 px `KEY_RIM` outline and
   a `KEY_LIP` bottom edge (4 px keys and selects, 3 px small parts).
   Pressed or latched, the lip folds to 1 px and the face drops by the
   difference. Disabled keys keep their silhouette in ghost tones.
3. The stop is a red key in an ink collar with a pale socket band between
   them. Latched (every model), the key drops, the band floods SKIRT, the
   collar turns SIGNAL, and the release glyph appears above "Clear".
4. A model whose stop did not confirm is marked at its own entry with the
   red rule, the tripped-flag window and the words; its rail lamp turns
   SIGNAL.
5. One "on" sign: the lamp slot. Ink when on or shown, SIGNAL only for an
   unconfirmed model, never trace.
6. Icons come only from the station's nine-glyph set (`theme.ICONS`); a key
   carries at most one, before its legend; the warning glyph is the error
   mark.
7. White on the stop face, the "on" switch knob, key-top highlights and
   `ENGRAVE`, and in the CAP and RAIL mixes.
8. Disabled ink is 72 % of ink.

Type: Figtree for text, Rubik 600 for numerals and the two headlines,
tabular figures; readings 60/44/36/30. Tokens in `src/palette.py` and
`src/views/theme.py`; every view reads them and names nothing of its own.
(Tk drew the same parts by tone and lip, Qt kept radii and per-side
borders; both are retired. The Web adds the garnish.)

## Notices: tray line or acknowledgement (2026-09-28)

A notice is a tray line by default: status by exception, warnings and errors, history in the log. A notice asks for an acknowledgement only when missing it would leave the operator wrong about what the station is doing. That means every error (a failed command, a fault, an unconfirmed stop) and the few warnings in `events.ATTENTION`: the idle timeout that ended a mode, a temperature link or rotator still lost after its boot grace, and a heater-off that was never sent. Countdowns that carry their own affordance (Idle Timeout Soon and its Extend), notices nobody is present to read (Browser Silent), and soft advice (No Picture, Empty Trial) stay tray lines. An acknowledgement has the latch-release dialog's shape in every view. It never takes the stop away. It shows one title at a time with that title as its heading and the message as its body. It has one key, Understood, answered by Return and Escape. Further titles queue behind it, and a repeat of its own title counts in place.

## The procedure (2026-10-07)

Owner ruling 2026-10-07, rendered by the Web (WEB-1..5) from the model's
`phase` (`MODEL_CONTRACT.md`, "Phases: the interactive procedure"). The sheet
shows the step the operator is in and hides what the step does not need.

- **Phases.** A model with a procedure declares `PHASES`; the Transfer Map's
  are setup (preliminary info: tip, sample, chip, flake), new_tip (a prompt), region (a full-resolution still of the stage
  is taken and the capture region is picked on it), live, marked (after Mark
  force), finish (review). A section or element drawn only in some steps names
  them (`phases=`); the page marks the shown step on the sections and rows,
  moves focus to the step's first control, and draws the procedure strip on the
  model's card (see below). Hidden is not disabled: `enabled_when` still greys a control where
  it stands. The stop is never phased and stays reachable in every step.
- **The region picker** draws on the model's still at full resolution, maps
  the drag to source pixels and outlines the held region.
- **Dividers.** On the Dashboard each device sits inside an explicit hairline
  boundary in every reflow (three across, two, one column), not whitespace
  alone. This amends "entries are separated by whitespace" above for the
  Dashboard.
- **Thumbnails.** Images reach the page through `GET /api/image` (a relative
  path under the output root).

### What the proposal round added (2026-10-07)

- **The procedure strip.** A phased model's page draws a strip from
  `state["phases"]`, the lit step from `state["phase"]`, the step's own
  sentence from `state["step_text"]` and one health word from
  `state["analysis_health"]` (quiet when it says "settled", a warning pill
  otherwise: unsettled, stalled). Each part is written only when it changes.
  A phase whose name starts `new_` is a prompt, not a numbered step: the strip
  does not number it.
- **Prompt phases are a card dialog.** A `new_` step (the Transfer Map's
  `new_tip`; the Sample DB's `new_sample`, `new_chip`, `new_flake`) is asked
  as a dialog over the model's card: a scrim over the card (never the stop,
  never the rail), the step's sections centred in it with their widgets
  moved, not copied, focus on the first entry, Escape is Cancel and Return in
  an entry is Add. Everything else on the card is inert while it is open.
- **Cascading dropdowns refresh on change.** A dropdown whose options depend
  on another (Sample, Chip, Flake) is re-read after each change, so a new
  sample clears the chip and the flake in the page, not only in the model.
- **Secondary readouts.** `readonly(..., secondary=True)` is drawn as a small
  quiet line under the control it follows, inside that control's row (the
  speed dials' steps/s). A secondary readout never counts as the reading.
- **Hosted tiers.** `section(..., hosted_tier=N)` places a hosted model's
  section in tier N of its host's page (`MODEL_CONTRACT.md`); on its own page
  it keeps its own tier. RGB Analysis's Live group is hosted at tier 2 (behind
  its details) and its Estimators plot at tier 1 on the trial page.
- **Speed dials** read 0-100 % over each device's ceiling (stepper 3200,
  chuck 600 steps/s); the wire still carries steps/s.
- **Dividers.** The Dashboard's hairline boundaries stand as above.
- **Accounts and tutorials.** Setup's Account section masks secret entries
  (`secret: True` on an entry, cleared after use). The Tutorials entry on the
  rail runs coach cards (`tutorial.js`, `tutorials/*.json`) that point at real
  controls; simulation only.
