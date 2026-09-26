"""The station's colours, shared by the views' theme and the model-rendered
figure. Models may not import a view, and a view cannot repaint a PNG, so
the palette lives below both.

Six tokens, and nothing else (owner ruling 2026-09-22; values re-chosen
2026-09-25 for the "Bench sheet, tiered" language, canvas row E,
`handoff/design-Tiered.md` / `design-Sheet.md`). `SIGNAL` is the stop
colour and is spent on nothing else; `TRACE` is what a CHANGING number and a
plot line are drawn in - never a lamp, a tick, a bar or a status word.
Anything that is not one of those two is base, panel, ink or muted.

Measured contrast (WCAG 2.x): ink on panel 14.45, muted on panel 5.38,
trace on panel 6.79, white on signal 5.99, signal on base 5.58, muted on
base 5.94 (so a muted underline is an identifiable control edge, 3:1 floor).
"""

#: The sheet, the panel wells, and the ink.
BACKGROUND, SURFACE, TEXT = "#f5f7f9", "#e8ecf0", "#141c26"
#: Captions, units, control edges, frozen readings.
MUTED = "#56606d"

#: Stop, latch, fault, the unconfirmed mark. Nothing else may be drawn in it.
SIGNAL = "#c21f1a"
#: Live, changing numbers and plot lines. Nothing else.
TRACE = "#2440c4"

#: The series colour a model-rendered figure draws its line in: the trace.
ACCENT = TRACE
#: Figure gridlines - the panel tone on the sheet.
GRID = SURFACE
