"""The station's colours, shared by the views' theme and the model-rendered
figure. Models may not import a view, and a view cannot repaint a PNG, so
the palette lives below both."""

BACKGROUND, SURFACE, TEXT, MUTED = "#1e1e1e", "#2a2a2a", "#f0f0f0", "#9a9a9a"
ACCENT = "#c62828"          # the series colour: red, for red percent
GRID = "#3a3a3a"
