# Station aesthetics: unified proposal (2026-09-27)

Sources: `handoff/aesthetic-{Industrial,Block,Tactile,Night}.md`, their sixteen renders in `handoff/shots/aesthetic_*`, the incumbent `handoff/design-Tiered.md` and `handoff/shots/design_Tiered_*.png`, `src/palette.py`, `src/views/theme.py`, `docs/rebuild/DESIGN_BRIEF.md`. Every contrast figure below is quoted from a spec. None is measured here.

## What was held fixed and what varied

The layout of "Bench sheet, tiered" (2026-09-25) was held fixed. That covers the rail and the stop disc, the Overview and device pages, the three tiers and where their disclosures sit, sliders beside exact entries, status by exception, the copy, and the stop rules. Four agents restyled the same four boards (Launched, Details, Stopped, Narrow) on that layout. Each could change the six token values, the type pairing, material and depth, control shapes and motion. Each could also break one of the incumbent's aesthetic rules ("six tokens", "no shadows", "depth is tone steps and rules") if it listed the break for the owner to ratify. No agent could break the stop rules or the copy rules.

## The four directions at a glance

| Direction | Axis explored | Tokens (bg / surface / text / muted / signal / trace) | Type pairing | Depth model | Rule changes proposed | Character |
|---|---|---|---|---|---|---|
| Industrial | warm light enamel, machined parts | `#e1ded6` / `#cfcbc1` / `#1c1a16` / `#544f45` / `#b3241a` / `#0b5a7c` | Barlow Semi Condensed (numerals, captions, names) + IBM Plex Sans | tone steps plus machined edges; a 3 px key lip | 2 | A warm bench instrument with a red key in a black guard collar. |
| Block | flat, heavy, poster weight | `#f1f3f5` / `#dde1e6` / `#0e1116` / `#4f5661` / `#d11d14` / `#1437d8` | Barlow Condensed + Barlow | 3 px ink rules, tone, and inversion (ink rail) | 0 formal, 2 to ratify | Loud sheet, oversized numerals, the red disc on a black rail. |
| Tactile | light cool faceplate with real depth | `#e7eae9` / `#d8dcdb` / `#1a1f22` / `#51595c` / `#c0241b` / `#2344b8` | Rubik + Figtree | three heights (raised, flush, sunk) by two shadow tokens | 3 | Soft moulded keys; the stop is a domed key that sits down when latched. |
| Night | true black, low emission | `#000000` / `#121519` / `#e3e0d8` / `#8d949e` / `#d42a22` / `#86c5ff` | Atkinson Hyperlegible Mono + Atkinson Hyperlegible Next | three tones only, hairline structure | 3 | A dark screen with a few ice-blue numbers; on a latch only the stop stays lit. |

## Comparison on fixed criteria

Scores are 1 (weak) to 5 (strong). For cost, 5 means cheapest.

| Criterion | Industrial | Block | Tactile | Night |
|---|---|---|---|---|
| Legibility of readings from a metre | 4 | 5 | 3 | 4 |
| Stop salience | 4 | 5 | 3 | 5 |
| Tier hierarchy | 4 | 4 | 4 | 3 |
| Toolkit portability | 3 | 4 | 2 | 3 |
| Contrast and accessibility | 4 | 5 | 3 | 4 |
| Fatigue over a long session | 5 | 2 | 4 | 4 |
| Distinctiveness from dashboard tropes | 5 | 4 | 3 | 2 |
| Cost to implement | 2 | 2 | 3 | 4 |
| Total (unweighted) | 31 | 31 | 25 | 29 |

The totals are unweighted. The owner should weight them. On a Tk-default station, portability and cost count for more than they look.

**Legibility**
- Industrial 4: the numerals are tall and condensed, at focal 58 and compact 36. The trace is the weakest of the four, at 5.64 on the sheet and 4.68 on a panel. The petrol blue is also closer to the ink than the incumbent's blue in the render.
- Block 5: numerals are Barlow Condensed 700 at focal 72 and compact 44. Trace is 7.35 on the sheet, and ink is 17.00. From a metre these are the biggest and heaviest readings.
- Tactile 3: the sizes are unchanged from the incumbent, and Rubik 500 is a step lighter than the incumbent's Archivo 600. The readings are clear but not improved.
- Night 4: trace is 11.44 on black, and the mono face has a slashed zero and distinct 1/l/I. In the render the mono decimal point takes a full cell ("3 . 42"), which loosens the number.

**Stop salience**
- Industrial 4: the guard collar makes the stop the darkest, heaviest object on the screen. In the Stopped render, though, the 3 to 6 px ring change is hard to see inside the collar, so "Clear" does the work alone.
- Block 5: the red disc on the ink rail is the heaviest mark on every screen. In the latch, "Clear", the 10 px ring and the narrowed gap make a clear change.
- Tactile 3: the skirt and socket are handsome. In the Stopped render, however, the latched "down" key is hard to tell from the idle one except by its words. The raised keys around it also carry shadows that compete a little.
- Night 5: the disc is the only saturated area. On a latch the readings drop to muted, so the disc is left as the one lit thing (see Night_Stopped).

**Tier hierarchy**
- Industrial 4: wells have an EDGE top line and a tone step. The spec admits sheet to panel is only 1.2:1, so the wells separate by edge more than by tone.
- Block 4: a 3 px ink rule caps each open well, and Diagnostics has a 3 px left rule, so the tiers read as bands. The spec admits the bold ink captions compete with the readings more than muted captions did.
- Tactile 4: on the Web, sunk trays and a deeper Diagnostics pocket make tier 2 and tier 3 recede clearly. The tone fallback keeps the order in Tk.
- Night 3: the well step is 1.15:1 and the hairlines are 1.2 to 1.9:1. Tier 3 dropping back to black is a good idea, but on a glossy or lit panel the tier-2 well can vanish.

**Toolkit portability**
- Industrial 3: Qt honours nearly all of it. Tk needs a Canvas fader (about 80 lines) or loses the cap, the index line and the ticks. It also needs a lip-frame helper for keys and caption-font plumbing. Two things fall back: the ticks, and the fader cap if the Canvas is skipped.
- Block 4: all three views honour the palette, weights, rules and inversion. Tk falls back on the bar thumb, the dashed edge and the 800 weight (static-family name needed). That is three fallbacks, none of which carries meaning.
- Tactile 2: the signature, soft shadow depth, is Web-only. Qt and Tk lose the blur. Tk also loses every radius. Since Tk is the default view, most operators would see the fallback.
- Night 3: the colours and fonts port natively. The spec's own tradeoff is that OS title bars, Tk menus and native file dialogs stay light without a per-platform dark API. The no-platform-specific-UI ruling forbids that API. Qt also needs a full dark `QPalette`. The bar thumb needs an image element in Tk.

**Contrast and accessibility**
- Industrial 4: all text pairs are at or above 4.5. The lowest is trace on panel at 4.68. EDGE is 2.27 on the sheet, but it is decorative and no control relies on it alone.
- Block 5: the lowest text pair is muted on panel at 5.64. Signal on panel is 4.10, but only as a mark. Muted is kept off the rail (2.55 on ink).
- Tactile 3: the text is fine: muted on DEEP is 4.65 and disabled is 4.79. A key's top and side outline, however, is 1.87. The key depends on its lower lip (3.58) and a shadow that Tk and Qt drop.
- Night 4: the lowest text pair is muted on the lifted rail item at 4.51, right at the floor. Disabled is 4.77 on a well. The structure lines sit at 1.15 to 1.9 by design.
- None of the specs reports a text pairing under 4.5:1 in use. Near the floor are Night's 4.51, Tactile's 4.65 and Industrial's 4.68. All four found that the incumbent's disabled ink (a 45 % mix) fails on their ground, at 2.69 to 2.99, and replaced it.

**Fatigue**
- Industrial 5: a warm, low-chroma ground with one cool colour. The ink ceiling is 12.9 rather than 14.5, which is gentler over hours.
- Block 2: poster weight everywhere, bold ink captions and a saturated trace. A large ink rail meets a near-white sheet. It is legible, but it is loud for an eight-hour session.
- Tactile 4: a soft, cool light grey with low chroma. The shadows add visual noise but not glare.
- Night 4: in a dim lab it is the least tiring of the four. In a lit room the black ground reflects the room, and the light OS chrome is a glare strip on Windows (spec tradeoff).

**Distinctiveness**
- Industrial 5: the collar, the ticked fader, the key lips and the engraved captions look like an instrument, not a web dashboard.
- Block 4: it is recognisably brutalist or poster work. The trope is familiar, but not as a dashboard trope.
- Tactile 3: soft raised keys are close to the neumorphic "soft UI" trope.
- Night 2: a black ground with blue monospace numerals is the most common dark-dashboard look of the four.

**Cost** (from each spec's Cost section)
- Industrial 2: 6 palette values and about 16 theme values. View code runs to about 120 CSS lines and 10 markup lines on the Web, 90 QSS and painter lines in Qt, and about 150 lines in Tk.
- Block 2: 6 palette values and about 30 theme values. View code is 80 to 120 CSS lines, 60 to 100 Qt lines and 120 to 180 Tk lines.
- Tactile 3: 6 palette values and about 22 theme values. The Web cost is small (about 15 CSS rules). Tk is 80 to 120 lines and Qt 50 to 80. Every Tk capture must be retaken.
- Night 4: 6 palette values plus GRID, and about 16 theme values. The spec puts view code at 3 to 5 small edits per view. On top of that come a dark `QPalette`, the `plot_data._colormap()` range change with its test baseline, and the `go` edge in all three views.

## Rule changes each direction asks the owner to ratify

**Industrial**
1. "Key lip (the incumbent's "depth is tone steps and rules", stretched rather than broken). Neutral and go keys carry a 3 px bottom edge in their own edge colour, heavier than the other three sides."
   My view: this is a rule, not a shadow, so it survives all three toolkits. It is the cheapest honest press affordance of the four. Worth ratifying.
2. "Captions move from the text face to the numeral face (`CAPTION_FAMILY`, new)."
   My view: this is harmless to the rules and gives the direction its engraved feel. It does add caption-font plumbing at every Tk label call site.

**Block** (the spec lists no rule changes, but asks for two things to be "ratified knowingly")
1. "The rail is inverted to ink."
   My view: it gives the stop the most contrast of any direction. But it is a large dark mass beside a light sheet, and it is the main source of Block's fatigue cost.
2. "The slider thumb and latch square carry a 3 px sheet-tone ring. On the Web this is drawn with `box-shadow: 0 0 0 3px`."
   My view: this is an outline and not a shadow. Qt and Tk draw it as a border. It needs no ruling.
3. Not listed by the spec, but visible in the render: on the Launched board the opened probe's speeds sit beside its position rather than under it. The spec says this was done to fit 900 px. That is a layout change, and the layout is fixed. The owner should know it is there.

**Tactile**
1. "'No gradients, shadows, blur, textures' becomes 'no gradients or textures; exactly two shadow tokens (`SHADOW_RAISED`, `SHADOW_INSET`) plus the stop's solid SKIRT offset.'"
   My view: this is the direction's whole identity, and only the Web can draw it. Ratifying it makes the Web look different from Tk, which is the default view.
2. "'White only on the stop face' becomes 'white on the stop face and the switch knob, and at alpha inside the two shadow tokens; white may be mixed into BACKGROUND for CAP and RAIL.'"
   My view: this is needed only if rule 1 is taken. On its own it is harmless.
3. "Disabled ink rises from 45 % to 65 % of ink (2.69 to 4.79 on this ground)."
   My view: take it in some form whatever else is chosen. Every direction found the 45 % mix failing on its ground.

**Night**
1. "Entry head: 2 px ink rule → 1 px `RULE_STRONG` hairline."
   My view: it is right for a black ground. It is also what makes the tier structure the faintest of the four.
2. "`go` is no longer ink-filled."
   My view: this is necessary on black, since an off-white slab would outshine the stop. It costs a `go` edge in all three views.
3. "The headline 'Every model is stopped.' moves to the text face."
   My view: this is correct for a monospace numeral face and has no downside.

## Recommendation

My recommendation is a hybrid: **Industrial, with Block's rules**. Industrial is the base. It scores highest on fatigue and distinctiveness and is strong on legibility. It keeps six colours, no shadows and white only on the stop face. Its two rule changes are mild, and both survive Tk. Its weak point is figure and ground. The sheet-to-panel step is only 1.2:1, so on a washed-out bench monitor the rail, the wells and the tray can merge into one grey field. Block's strength is exactly that separation by rule rather than by tone, so the hybrid borrows it.

**Taken from Industrial:**
- All six token values and the derived EDGE and TRAY.
- Barlow Semi Condensed for numerals, captions and model names, with IBM Plex Sans for text. `READING_SIZES` becomes 58/46/36/30 and `CAPTION_SIZE` 14.
- The guard collar on the stop, the key lip, the fader with the ink cap, and the square lamp windows and marks.
- The motion spec.

**Taken from Block:**
- The 3 px ink rule that caps an open tier-2 well and heads the tray, in place of Industrial's 1 px EDGE top line.
- The 3 px ink left rule that marks Diagnostics inside the well, in place of Industrial's 2 px muted rule.
- Nothing else. Block's ink rail, poster sizes, bold ink captions and bar thumb stay out. Those are where its fatigue and fit costs come from.

The honest tradeoff is this. The hybrid is among the more expensive options to build, and Tk carries most of the cost: a Canvas fader, a lip helper, the collar oval and caption-font plumbing, about 150 lines by Industrial's own estimate. If the owner wants Tk cheaper, Industrial's stated fallback is `ttk.Scale` in `clam` with no ticks. Industrial's petrol trace is also the lowest-contrast trace of the four (4.68 on a panel). It passes, but it separates from the ink less than the incumbent's blue does. I have not proposed a different trace value, because no spec measured one on this ground. Finally, the latched ring change is weak inside the collar in the render. The collar gap, or the latched ring width, should be tuned so that the latch reads as more than a change of words.

**Cost in files:** this is not palette-only.
- `palette.py`: 6 values, plus a rewritten contrast docstring.
- `theme.py`: about 16 values from Industrial. These are the fonts, `DISABLED`, `READING_SIZES`, `CAPTION_SIZE`, `RADIUS`, `STOP` (collar), and the new `EDGE`, `TRAY`, `CAPTION_FAMILY`, `KEY_LIP_PX` and `SLIDER`.
- Block's rules add one value: a well-cap width (3 px), or `RULE_STRONG_PX` reused for it.
- Per-view work:
  - Web: about 120 CSS lines.
  - Qt: about 90 QSS and painter lines.
  - Tk: about 150 lines.
- Barlow Semi Condensed and IBM Plex Sans must be installed on the station PC.
- Tests that pin fonts, radii or colours need updating, each with its reason.

This is a recommendation only. The choice of direction, and each rule change, is the owner's.

## What the owner decides

1. **Direction.**
   - Keep the incumbent (Tiered, as shipped).
   - Industrial.
   - Block.
   - Tactile.
   - Night.
   - The recommended hybrid (Industrial with Block's rules).
   - A different hybrid of at most two.
2. **Light or dark.**
   - One light theme.
   - One dark theme (Night).
   - A light theme plus Night as a second, operator-chosen theme. This doubles the captures and the contrast tests, and it does not fix the light OS chrome in Tk and Qt.
3. **Disabled ink.** The incumbent's 45 % mix fails on every proposed ground.
   - `DISABLED = (BACKGROUND, MUTED)` (Industrial, Block).
   - A 65 % ink mix (Tactile).
   - A 58 % mix (Night).
4. **Key lip** (Industrial rule change 1): ratify, or keep keys flat.
5. **Caption face** (Industrial rule change 2): captions in the numeral face, or keep them in the text face.
6. **Shadows** (Tactile rule changes 1 and 2):
   - Keep "no shadows".
   - Allow the two shadow tokens and the white mixing, knowing they are Web-only.
7. **Entry head and `go` fill** (Night rule changes 1 and 2). These apply only if Night, or a dark theme, is chosen.
   - Keep the 2 px ink rule and the ink `go`.
   - Accept the 1 px hairline and the charcoal `go` with an ink edge.
8. **Headline face** (Night rule change 3): "Every model is stopped." and "Setup" in the numeral face, or in the text face.
9. **Ink rail** (Block): allowed or not. The same question applies to Block's speeds-beside-position arrangement on the Launched board, which departs from the fixed layout.
10. **Reading sizes.**
    - Keep 52/40/32/26.
    - Take Industrial's 58/46/36/30.
    - Take Block's 72/56/44/34. Block's spec measures that a signed five-digit value ("-12 345") will not fit its compact column.
11. **Tk slider.**
    - A Canvas fader, about 80 to 150 lines, identical across views.
    - The `ttk.Scale` (clam) fallback, cheaper, but Tk then looks different.
12. **Fonts to install on the station PC**, once the direction is chosen:
    - Industrial: Barlow Semi Condensed and IBM Plex Sans.
    - Block: Barlow and Barlow Condensed.
    - Tactile: Rubik and Figtree.
    - Night: Atkinson Hyperlegible Next and Mono.

## Appendix: per-direction notes

**Where the renders differ from the specs (trust the render)**
- Tactile's disclosures read "Configure" and "Details" on every board. The Tier K ruling calls for the named words ("Configure Stepper Probe", "Red Percent details"), and the other three directions use them. The Tactile spec says "then the words" without noting the gap. Any Tactile adoption must use the named disclosures.
- Tactile's latched "key down" state is barely visible at board scale. Idle and latched discs look almost the same apart from "Clear" and a slightly heavier rim.
- Industrial's latched ring (3 to 6 px) reads weakly inside the collar in Industrial_Stopped.
- Block's Launched board moves the opened probe's speeds to the right of its position. The spec admits this was done to fit.
- The incumbent renders (`design_Tiered_*`) predate Tier K. They have no Overview in the rail and put the disclosure in the entry head. Compare them for tone and type, not for layout.

**Worth carrying whatever direction is chosen**
- Night's latch choreography: readings drop from trace to muted, so only the stop stays lit. All four boards already mute frozen readings. Night's 240 ms cross-fade is optional.
- Industrial's `WINDOW` rule: a typed value always sits one tone off its surround, so an input inside a well uses the sheet tone. Block and Tactile do the same thing under other names.
- Block's "numbers never animate" rationale: a tweened position is a position the probe never held. All four agree.
- Night's warning about the model figure: `plot_data._colormap()` would need its bright half on any dark ground, and its contrast test baseline would change with it.
- Industrial and Block both drop `NUMERAL_STRETCH` to 100 by choosing a family that is condensed by design. This removes the "Archivo SemiExpanded" static-face dependency in Tk and Qt. Night and Tactile drop it too.
