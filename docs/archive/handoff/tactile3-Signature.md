# Tactile round 3, axis "Signature" (prefix `Signature`), 2026-09-27

Artboards: `$SP/tactile3/Signature/project/Signature_{Launched,Details,Stopped,Narrow,Parts}.dc.html` (+ `canvas.json`; Parts is `is_interactive`).
Renders: `handoff/shots/tactile3_Signature_{Launched,Details,Stopped,Narrow,Parts}.png`.
Generator and render tools: `$SP/tactile3/Signature/tools/` (`build.py`, `parts.py`, `shoot.cjs`, `contrast.py`). `shoot.cjs` fills the Parts board's holes from its own `renderVals()`, so a state can be rendered: `node shoot.cjs 'Signature_Parts|{"latched":true}|alt'`. It reports clipping under the tray and horizontal overflow per entry. All five boards report none.

## Direction

Tactile's light mineral faceplate, with one family of parts added that an operator can tell apart without reading. Every key has the same anatomy: a face, a 1.5 px rim and a dark lip below. A latched key folds its lip under and drops. A small lamp window is the plate's single "this is on" sign. The stop is the largest member of the family and the emblem of the product. It is a red key in an ink guard collar, with a pale socket band showing between key and collar. When every model latches, the key drops, the band floods dark red and the collar turns red. From three metres the idle stop reads as a bullseye and the latched stop as one solid red coin. That change is carried by tone alone, so Tk and Qt keep it.

## What changed from Tactile

- **Key anatomy.** Tactile had a soft outline at 1.87:1, an EDGE lower lip and a blur shadow. Signature has a 1.5 px `KEY_RIM` outline (4.38 on the sheet, 3.83 on SURFACE) and a 4 px `KEY_LIP` bottom edge (6.54). A key now reads as a key in tone alone. The blur is reduced to garnish.
- **Pressed and latched are one gesture.** The lip folds from 4 px to 1 px and the face drops 3 px, so the key's total height does not change. The latching mode key is the ink key pressed down, with its lamp lit.
- **Lamp slot.** A 6 × 13 px window replaces Tactile's radio dot on latching keys. The same slot marks the shown page in the rail and "Running" on Red Percent. It is SIGNAL only for a model whose stop did not confirm.
- **Disabled keys stay keys.** Their shape and lip are kept, drawn in ghost tones: a dashed `EDGE` rim, a solid `EDGE_SOFT` lip and `DISABLED` legend. Tactile's disabled keys went flat.
- **The stop.** It gains an ink guard collar and a visible socket band. Latched, the band floods (SURFACE to SKIRT, 7.11:1 between the two states) and the collar turns SIGNAL. The key drops 6 px and loses its skirt. The release glyph appears on the face above "Clear". The idle face carries only "Stop".
- **Nameplate.** The rail's title block, the stop and the chord sit on one CAP plate with an engraved double frame and four drawn fasteners. The chord is set as two small keycaps, `Ctrl` + `.`, and the text still reads "Stop: Ctrl+.".
- **Unconfirmed is a physical flag.** Tactile marked it with a 3 px SIGNAL rule and the words. Signature keeps both and adds a tripped-flag window at the entry: SIGNAL with an ink hatch in an ink frame, which drops in on appearance. The rail's lamp for that model turns SIGNAL. The tray line leads with the warning glyph in SIGNAL.
- **Named disclosures.** The disclosures now use the schema's words: "Configure Stepper Probe", "Configure DC Probe", "Configure Chuck Positioner", "Configure Temperature Controller", "Configure Rotator", "Red Percent details" and "Diagnostics". The disclosure control is a 24 px member of the key family, and it sinks when open.
- **Readings are heavier and bigger.** Rubik moves from 500 to 600 (the incumbent Archivo's weight), and `READING_SIZES` becomes 60/44/36/30 (was 52/40/32/26). Axis letters are 14/700. Tabular figures are verified: "1111", "0000" and "8888" all set at 151.5 px at 60 px.
- **Fader cap.** The slider thumb is a 16 × 30 key cap with a lip and an ink index line, replacing the round knob.
- **Switch.** The knob of the "Stop this model only" switch is a key cap. On, the track is SIGNAL and the knob white.
- **Select.** The select is a key with the disclosure glyph turned down.
- **Icon set.** Nine glyphs of the station's own on a 20 px grid, with one 1.75 stroke, round caps and joins, and 2 px corners. Keys may carry one glyph before the legend.
- **Engraved legends.** Captions, units, axis letters and CAP-key legends share one 1 px white lower highlight (`ENGRAVE`).
- **`DISABLED` darkens** from mix(BG, TEXT, .65) to .72. Tactile's value failed on SURFACE (4.19) and DEEP (3.76).
- **Kept unchanged:** the six palette values, CAP, RAIL, DEEP, EDGE, EDGE_SOFT, SKIRT, the three heights, `SHADOW_INSET`, Figtree, Rubik, the entry rule, and the tray and pocket.

## Tokens (measured contrasts)

The palette is unchanged from Tactile. `BACKGROUND #e7eae9`, `SURFACE #d8dcdb`, `TEXT #1a1f22`, `MUTED #51595c`, `SIGNAL #c0241b`, `TRACE #2344b8`. Derived values are unchanged too: CAP `#f5f7f6`, RAIL `#f1f2f2`, DEEP `#cdd1d0`, EDGE `#767a7c`, EDGE_SOFT `#aaadad`, SKIRT `#7e221e`.

New values, all derived with `theme.mix()`, each with one job:

| Name | Value | Job | Measured |
|---|---|---|---|
| `KEY_RIM` = mix(BG, TEXT, .62) | `#686c6e` | the 1.5 px outline of every raised part (key, disclosure key, select, fader cap, switch knob and track, kbd, nameplate frame) | on BG 4.38, SURFACE 3.83, DEEP 3.44, CAP 4.93, RAIL 4.73 |
| `KEY_LIP` = mix(BG, TEXT, .75) | `#4d5254` | a key's lower lip (4 px keys and select, 3 px disclosure key and fader cap, 2.5 px switch knob, 2 px kbd) | on BG 6.54, SURFACE 5.73, DEEP 5.14, RAIL 7.06; against the rim 1.49, so it reads as a darker band |
| `GO_LIP` = mix(TEXT, black, .55) | `#0c0e0f` | the lip of an ink key | on BG 15.98 |
| `DISABLED` fg = mix(BG, TEXT, .72) | `#53585a` | a disabled legend or value | on BG 5.96, SURFACE 5.21, DEEP 4.68, CAP 6.70, RAIL 6.43 |
| `ENGRAVE` | `text-shadow: 0 1px 0 rgba(255,255,255,.7)` (CAP-key legends `#fff`) | the engraved lower highlight on captions, units, axis letters and CAP-key legends. Web only | none (a highlight, not a pairing) |
| `SHADOW_RAISED` (new value) | `0 3px 6px -3px rgba(26,31,34,.38)` | ambient under raised parts. The rim and lip now carry the key | none |
| `SHADOW_INSET` | unchanged | fields, tray, pocket, sunk rail pad | none |

Every text pairing in use (WCAG 2.x relative luminance, `tools/contrast.py`):

| Text | On | Ratio |
|---|---|---|
| ink legends, values | BG / SURFACE / DEEP / CAP / RAIL | 13.73 / 12.02 / 10.79 / 15.45 / 14.82 |
| CAP legend on an ink key (`go`, latched mode) | TEXT | 15.45 |
| MUTED captions | BG / SURFACE / DEEP / CAP (nameplate) / RAIL | 5.91 / 5.17 / 4.65 / 6.65 / 6.38 |
| TRACE readings | BG / SURFACE / DEEP | 6.70 / 5.86 / 5.26 |
| DISABLED | BG / SURFACE / DEEP | 5.96 / 5.21 / 4.68 |
| white "Stop" / "Clear" (30 px) | SIGNAL | 5.98 |

Marks and edges at the 3:1 floor:

| Mark or edge | Ratio |
|---|---|
| SIGNAL rule, lamp and switch track on BG / CAP / RAIL | 4.94 / 5.56 / 5.34 |
| stop face against the idle socket band (SIGNAL on SURFACE) | 4.33 |
| idle collar against the band (TEXT on SURFACE) | 12.02 |
| socket band, idle against latched (SURFACE against SKIRT) | 7.11 |
| disabled dashed rim (EDGE) on BG / SURFACE | 3.58 / 3.13 |

Two edges fall below the floor. On DEEP the disabled dashed rim is 2.81. It is an inactive control, exempt under WCAG 1.4.11, and the ghost lip and legend still mark it. Latched, the face against its flooded socket is 1.64 (SIGNAL on SKIRT). That edge is carried by the face's top shading and the white legend. It is never the latch signal itself: the flood is.

## Type

- Numerals and the two headlines are **Rubik 600**, tabular, letter-spacing −0.02 em. Typed values are Rubik 500 16. `NUMERAL_WEIGHT = 600`, `NUMERAL_STRETCH = 100`.
- **Figtree** is the text face: 400/500/600/700. Model name 18/600, key legend 14/600, captions 13/500 MUTED +0.005 em with `ENGRAVE`, axis letters 14/700 MUTED, plate name 19/700 (17 at 900 px), kbd legend 11.5/600.
- `READING_SIZES` is focal 60, primary 44, compact 36, secondary 30. The 900 px board draws focal at 54. `CAPTION_SIZE` stays 13. Tier-2 statistics are 24.
- Fit is measured in Rubik 600: "1 184" at 60 is 163.6 px, "5 000" at 36 is 98.1 px, "112.50" at 44 is 150.1 px. The compact probe row fits the three-across column with 16 px left, and the Narrow two-column grid with 14 px gaps.
- One Google Fonts request: `family=Figtree:wght@400;500;600;700&family=Rubik:wght@500;600`. Both families have static TTFs for Tk and Qt ("Rubik SemiBold", "Rubik Medium", "Figtree").

## Material and depth

Tactile's three heights stay (raised, flush, sunk) with one light from above. What changes is how a raised part is drawn. In Tactile it was a soft shape with a shadow. In Signature it is a made part: a face, a rim around it and a lip beneath it, all in solid tones. The blur under it is optional. Sunk parts are unchanged: fields, tray, pocket, the socket and the rail's shown page are SURFACE or DEEP with `SHADOW_INSET`. The nameplate is the one CAP plate on the rail. Its engraved double frame is a KEY_RIM line, then a CAP gap, then a 14 % ink hairline. It has four drawn fasteners, 7 px circles with a slot, in EDGE. Radii: keys 8, disclosure key 6, fields 6, switch 7, fader cap 5, pocket 10, tray 14, plate 12, collar and face fully round.

## Controls

- **Key (neutral).** CAP face, `KEY_RIM` 1.5 px, 4 px `KEY_LIP`, a 1.5 px white top highlight inside the rim, radius 8, height 34 (lip included), padding 0 12. Legend 14/600 ink with `ENGRAVE`. Pressed, the lip folds to 1 px, the face drops 3 px and an inner top shade appears; the height is unchanged.
- **Key (`go`).** Ink face, ink rim, `GO_LIP`, CAP legend. Pressed works as for neutral.
- **Latching mode key.** Off, it is a neutral key with a hollow lamp slot (SURFACE fill, rim outline). On, the ink face is **down** (1 px lip, 3 px drop, inner shade) with the slot lit CAP. It is told from `go` by its lit slot and its missing lip.
- **Disabled key.** Transparent face, 1.5 px dashed `EDGE` rim, 4 px `EDGE_SOFT` ghost lip, `DISABLED` legend, and glyphs in `DISABLED`. It is still the key's silhouette, unlit.
- **Text key** (Quit) and **link key** ("3D analysis plot…"). These have no face and no lip. The link key has a 1.5 px underline at a 4 px offset and the link glyph.
- **Legends with a glyph.** Home (home), Start run (run), Save run… (download), Gamepad log… (gamepad) and 3D analysis plot… (link). The glyph is 16 px before the legend, in the legend's colour.
- **Field.** A sunk window: SURFACE (DEEP inside a tray), radius 6, height 34, the MUTED 1.5 px floor lip, right-aligned Rubik 500 16. Focus adds a 2 px ink ring and a 2 px ink floor. Disabled is transparent with a dashed EDGE edge.
- **Slider.** A 6 px sunk groove with an ink fill. The thumb is a 16 × 30 fader cap (CAP, rim, 3 px lip) with a 2 × 14 ink index line. Disabled, the fill is EDGE_SOFT and the cap is flat BG with an EDGE rim and index. It keeps its exact entry beside it.
- **Disclosure.** A 24 × 24 key (3 px lip) holding the disclosure glyph, then the schema's words, 14/600. Open, the key sinks (SURFACE, 1 px lip, inner shade), the glyph turns down, and the tray follows 6 px below. "Diagnostics" repeats this inside the tray and opens the DEEP pocket.
- **Select.** A neutral key (4 px lip) with the disclosure glyph turned down at its right.
- **Switch.** A 42 × 24 sunk track with a rim. The knob is an 18 × 18 key cap with a 2.5 px lip. On, the track is SIGNAL, the knob white with SKIRT lip and rim, and it sits to the right.
- **Lamp slot.** 6 × 13 px (12 px in the rail), radius 2. Hollow means off. Ink means on, or shown. SIGNAL means unconfirmed. It is never trace.
- **Rail.** Items are 36 px tall. The shown page is a sunk SURFACE pad with its lamp lit ink. Other lamps are hidden, and the space is kept so names align. Setup is a neutral key and Quit a text key.
- **Tray.** Unchanged: an engraved line. Its error line leads with the warning glyph in SIGNAL instead of the red square.

## The stop

The stop is a `KEY_RIM`-edged ink **collar**: 172 px outer and 10 px wide (150/9 under 1000 px). It holds a sunk SURFACE socket and a 124 px SIGNAL **key** (106 px narrow) with a 6 px SKIRT. The legend is white Rubik 600.

- **Idle and energized.** These are identical, always red. The key stands 4 px above centre with its SKIRT showing below, a white top highlight and a soft drop. A pale **socket band** (SURFACE) shows all round between the key and the ink collar. The face reads "Stop" at 32 px with no glyph. Seen from across a room it is a bullseye: red, then a pale ring, then a black ring.
- **Latched (every model).** The key is **down**: 6 px lower, no skirt, a dark inner shade across the top of the face. The socket band **floods** SKIRT, which is 7.11:1 against the idle band. The collar turns **SIGNAL**. The face shows the **release glyph** above "Clear". That legend was hidden until now and says which way the key goes. Under the chord, "Stopped: every model latched" appears with the warning glyph. From across the room it is one solid red coin, 172 px across instead of a 124 px dot. This is the "thicker ring" of the rule, now 10 px of red where idle had none. A partial stop leaves the key up and reading "Stop".
- **Unconfirmed**, at the model's entry. The rule becomes 3 px SIGNAL. The head carries the **tripped-flag window**: a 30 × 20 ink frame showing SIGNAL with an ink hatch, which drops into the frame in 200 ms when it appears. It is followed by "Stop not confirmed. Treat as live." The model's lamp in the rail turns SIGNAL, and the tray repeats the line once. The disc does not change for it; it stays the stop the operator reaches for.
- **Focus.** A 2 px ink ring outside the collar (`STOP_FOCUS` stays ink).
- **Chord.** "Stop:" in MUTED, then `Ctrl` and `.` as 11.5 px keycaps (CAP, rim, 2 px lip) joined by "+".

## Motion

- **Key press.** The lip folds and the face drops in 70 ms ease-out. Release takes 110 ms. The same applies to the disclosure key, the fader cap and the switch knob. The switch knob slides in 120 ms.
- **Stop latching.** The key drops, the band floods and the collar turns red in 120 ms, with the release glyph appearing at the end. Clearing reverses this in 180 ms. It is never slower than the state it reports.
- **Disclosure.** The glyph turns in 160 ms. The tray opens in 200 ms ease-out, and its contents do not fade.
- **Unconfirmed flag.** It drops into its window once, 200 ms, `cubic-bezier(.16,1,.3,1)`. This is the one authored moment: a mechanical flag tripping.
- **Numbers and the fader never animate.** A tweened position is a position the probe never held.
- **`prefers-reduced-motion`.** Every duration is 0. The pressed, latched and flagged states still apply, because they are states, not motion.

## Per toolkit

- **Web.** Everything as drawn. The lip is a real `border-bottom`, so the key's silhouette never depends on the blur. The rim is an inset box-shadow line, so the lip can fold without the height changing. The new tokens are CSS variables from `css_variables()`. The icons are inline SVG from one path table.
- **Qt.** QSS has per-side borders and radii, so it honours the key family almost exactly: `border:1.5px solid KEY_RIM; border-bottom:4px solid KEY_LIP; border-radius:8px`. Pressed or checked gives `border-top:3px solid <parent bg>; border-bottom-width:1px`. It loses the blur and `ENGRAVE`. Icons are a `QIcon` from the same SVG paths via QtSvg, tinted by role. The fader cap is `QSlider::handle` with the rim and lip. Its index line needs a handle image (the SVG), or it falls back to no line. The stop, the flag window and the lamp slot are custom `paintEvent`s: ellipses, one path, one hatch. Radii are kept.
- **Tk.** ttk `clam` cannot draw a thick bottom edge on one side. Each key becomes a `tk.Frame` in `KEY_LIP` with `pady=(0,4)` around a ttk button (`bordercolor=KEY_RIM`, `lightcolor=darkcolor=CAP`). Pressed or latched rebinds the frame to `pady=(3,1)`, so the face drops by the same 3 px. Rims are 1 px, not 1.5 px, and corners are square: the accepted fallback, since tone and lip carry the part. Icons are `PhotoImage` PNGs pre-rendered from the same SVGs at 1× and 2×. Tk 9's built-in SVG photo format can load them directly. The stop is the existing Canvas: collar oval, socket oval, SKIRT oval 6 px under the face, face oval. Latched, the socket fills SKIRT, the collar SIGNAL, and the face is drawn 6 px lower with a darker crescent and the glyph. The flag window and lamp slot are small Canvases. The nameplate is a Frame with a 1 px `KEY_RIM` highlight. The fasteners and the inner hairline are dropped. The fader is a Canvas cap (about 80 lines), or `ttk.Scale` in clam without the cap as the cheaper fallback. `ENGRAVE` and the blur are dropped.
- **Result.** All three views show the same parts by tone: rim, lip, lamp, collar and flooded band. The Web adds only garnish (blur, engraving, radii where Tk has none). No state is carried by anything Tk or Qt cannot draw.

## Rule changes proposed

1. **"No gradients, shadows, blur, textures. Depth is tone steps and rules."** becomes **"No gradients or textures. Depth is tone steps, rules and the key lip. Exactly two shadow tokens (`SHADOW_RAISED`, `SHADOW_INSET`) and the stop's solid SKIRT offset, all Web-only garnish that no state depends on."**
   Reason: this is Tactile's ratified-if-chosen rule, carried forward. The lip is a border, not a shadow, so every view draws it.
2. **New: "Every raised part is one family: a face, a 1.5 px `KEY_RIM` outline and a `KEY_LIP` bottom edge (4 px keys and selects, 3 px small parts). Pressed or latched, the lip folds to 1 px and the face drops by the difference. Disabled keys keep their silhouette: dashed EDGE rim, EDGE_SOFT lip, DISABLED legend."**
   Reason: this makes a key read as a key in tone alone. The outline is 4.38:1 and the lip 6.54:1, against Tactile's 1.87:1. It also gives the product a part vocabulary of its own.
3. **"The stop ... always red ... with a thicker ring"** becomes **"The stop is a red key in an ink collar with a pale socket band between them. Latched (every model), the key drops, the band floods SKIRT, the collar turns SIGNAL, and the release glyph appears above 'Clear'."**
   Reason: Tactile's 3 to 6 px ring and a 3 px drop were not visible at board scale. The flooded band is a 7.11:1 tone change over the whole 172 px assembly, readable from three metres and in every toolkit. The key stays red in every state. *Owner's choice:* keep the ink collar at idle, or keep Tactile's red 3 px ring at idle and use only the flood and the red collar when latched. The second is quieter at idle, but the idle and latched outlines then share a red edge and differ less.
4. **"A model whose stop did not confirm is marked at its own entry with a red rule and the words"** becomes **"... with a red rule, the tripped-flag window and the words. The model's rail lamp turns SIGNAL."**
   Reason: a flag is a physical mark that stays found when the operator is on another page, which the words alone are not. The hatch is a mark, not a surface texture. *Owner's choice:* rail lamp on or off, independently of the flag window.
5. **New: "One 'on' sign: the lamp slot (6 × 13 px). Ink when on or shown, SIGNAL only for an unconfirmed model, never trace."**
   Reason: Tactile used a radio dot on keys, a black dot on "Running" and a pad in the rail. Three signs for one idea become one.
6. **New: "Icons come only from the station's glyph set (stop, clear, disclosure, gamepad, link, run, home, download, warning): a 20 px grid, a 1.75 stroke, 2 px corners. A key carries at most one, before its legend. The warning glyph replaces the red square as the error mark."**
   Reason: it gives a consistent legend language, and the mark reads as a warning at 16 px where a square reads as a bullet.
7. **"White only on the stop face"** becomes **"White on the stop face, the 'on' switch knob, key-top highlights and `ENGRAVE`, and in CAP and RAIL mixes."**
   Reason: this is Tactile's rule 2, extended by the engraving. It is Web-only and no state depends on it.
8. **Disabled ink rises to 72 % of ink** (`#53585a`). Reason: Tactile's 65 % measured 4.19 on SURFACE and 3.76 on DEEP.

The stop and copy rules are unchanged. SIGNAL keeps its one job: stop, latch, fault and unconfirmed (the rail lamp and the flag are unconfirmed marks). Trace stays on changing numbers and the plot line.

## Cost

- `palette.py`: none beyond Tactile's six values.
- `theme.py`: Tactile's roughly 22, plus about 14. These are `KEY_RIM`, `KEY_LIP`, `GO_LIP`, `KEY_LIP_PX` (4/3/2.5/2/1), `ENGRAVE`, `LAMP` (size and colours), `FLAG`, a new `SHADOW_RAISED` value, `DISABLED` 0.72, `NUMERAL_WEIGHT` 600, `READING_SIZES`, `STOP` gaining collar, collar_latched, socket_latched and glyph, `RADIUS` for the new parts, and an `ICONS` table of nine path strings.
- View code, honestly:
  - **Web**: about 150 CSS lines and about 40 markup changes (the plate, the chord kbds, lamp slots, key glyphs, flag window, disclosure key). This is the cheapest view.
  - **Qt**: about 150 lines. QSS for the family is about 40. Painters for the stop, flag and lamp are about 70. The icon loader is about 20. The fader handle is about 20.
  - **Tk**: about 250 lines. The lip-frame key wrapper and its press bindings are about 60, and the wrapper touches every key call site. The stop Canvas redraw is about 50. The flag and lamp Canvases are about 30. The PNG icon pipeline is about 30. The nameplate is about 20. The fader Canvas is about 80, or 0 with the ttk fallback.
  - Every capture in all three views is retaken. Tests that pin fonts, sizes, colours or the stop geometry are updated with reasons.
- On the station PC: Rubik and Figtree static faces, and PNG exports of the nine glyphs for Tk 8.6.

## Tradeoff

Signature buys recognisability with parts, and parts cost the most exactly where the station runs by default. Tk has to wrap every key in a lip frame and hand-draw the stop, the flag and the lamps. That is about 250 lines, a new place for geometry and focus bugs, and a retest of every Tk capture. The rims also make the plate busier than Tactile. Every key is now outlined at 4.38:1, so a dense tray such as Red Percent's reads as a field of outlined parts rather than a calm sheet. The ink collar makes the stop the heaviest object on every screen, which is the point, but some operators may find the fully red latched coin loud for a routine end-of-run stop. The hatched flag sits close to the hazard-tape cliché and is kept deliberately small for that reason.

## References consulted

From knowledge, not fetched in this session. What I took is language, not form.

- **Emergency-stop practice (ISO 13850, IEC 60947-5-5 devices).** I took the red latching actuator that stays down until deliberately released, and the "released" indicator band that is visible only when the actuator is up. That became the pale socket band that floods when latched. I did not take the yellow ground.
- **1960s–70s German calculators and radios (the Rams school).** I took one key family that differs only by material (light plastic, dark plastic), colour used only where it has a function, and legends printed on the key face.
- **Swiss field recorders.** I took engraved legends with a light lower edge, guarded switches, and a nameplate treated as part of the instrument. These became the engraved double frame and the fasteners.
- **Contemporary synthesiser panels.** I took small pictograms of one stroke weight sitting beside a word, and keycaps as the visible unit of the product.
- **Mechanical flag indicators (relay and aircraft annunciator windows).** I took a tripped state shown as a flag that drops into a framed window and stays there.
- **Keycap anatomy.** I took face, skirt and legend, and pressed as the skirt disappearing under the face.
- **Impeccable** (`bolder.md`, `delight.md`, `animate.md`, `craft-floor.md`). The rules I applied: amplify what the system owns (Tactile's heights and SKIRT), one authored motion moment (the flag drop), no zero-blur offset shadows as a costume (the lip is a border and the stop skirt is the stop's own part), and icons drawn in one stroke.
- **ui-ux-pro-max catalogue** (style: "tactile hardware skeuomorphic instrument panel"; typography: "technical instrument engraved legend"). Its "tactile digital" entry confirmed pressed as a skirt collapse with an inset. I rejected its bounce curves, gradients and metallic fills. The typography results (Fira, JetBrains Mono) were off-target and not used.
