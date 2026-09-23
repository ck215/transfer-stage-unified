"""The station's colours, shared by the views' theme and the model-rendered
figure. Models may not import a view, and a view cannot repaint a PNG, so
the palette lives below both.

Six tokens, and nothing else (Web design brief, owner ruling 2026-09-22).
`SIGNAL` is the stop colour and is spent on nothing else; `TRACE` is what a
live number, a plot line and a lit lamp are drawn in. Anything that is not
one of those two is base, panel, ink or muted.
"""

#: The surfaces and the two text weights.
BACKGROUND, SURFACE, TEXT = "#1f242b", "#2a3038", "#ece9e2"
#: Bumped one step from the brief's #8c95a3, which measured 4.40:1 on
#: SURFACE - under the 4.5:1 floor. This reads 5.43:1 on base, 4.63:1 on
#: panel. See the Web handoff's contrast numbers.
MUTED = "#9099a7"

#: Stop, latch, fault. Nothing else may be drawn in it.
SIGNAL = "#d92a2a"
#: Live readouts, plot lines, indicator lamps that are ON.
TRACE = "#e0b34c"

#: The series colour a model-rendered figure draws its line in: the trace.
ACCENT = TRACE
#: Figure gridlines - a hairline on the panel, in the panel's own hue.
GRID = "#39414c"
