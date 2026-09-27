"""The station's colours, shared by the views' theme and the model-rendered
figure. Models may not import a view, and a view cannot repaint a PNG, so
the palette lives below both.

Six tokens, and nothing else (owner ruling 2026-09-22; values re-chosen
2026-09-25 for the "Bench sheet, tiered" language and again 2026-09-27 for
the "Signature" aesthetic ruling, `handoff/tactile3-Signature.md`,
`docs/rebuild/DESIGN_BRIEF.md`). `SIGNAL` is the stop colour and is spent
on nothing else; `TRACE` is what a CHANGING number and a plot line are
drawn in - never a lamp, a tick, a bar or a status word. Anything that is
not one of those two is base, panel, ink or muted. Every derived shade
(key faces, rims, lips, the stop's skirt) comes from `theme.mix()`.

Measured contrast (WCAG 2.x): ink on sheet 13.73, ink on panel 12.02, muted
on sheet 5.91, muted on panel 5.17, trace on sheet 6.70, trace on panel
5.86, white on signal 5.98, signal on sheet 4.94 (a mark, not text).
"""

#: The sheet (a light mineral-grey faceplate), the panel wells, and the ink.
BACKGROUND, SURFACE, TEXT = "#e7eae9", "#d8dcdb", "#1a1f22"
#: Captions, units, axis letters, frozen readings.
MUTED = "#51595c"

#: Stop, latch, fault, the unconfirmed mark. Nothing else may be drawn in it.
SIGNAL = "#c0241b"
#: Live, changing numbers and plot lines. Nothing else.
TRACE = "#2344b8"

#: The series colour a model-rendered figure draws its line in: the trace.
ACCENT = TRACE
#: Figure gridlines - the panel tone on the sheet.
GRID = SURFACE
