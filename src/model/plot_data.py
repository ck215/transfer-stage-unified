"""Red Percent CSV parsing and figure rendering, used by RedMonitor so all
three views show the same image.

Two halves, deliberately split:

* `parse_run_csv` / `load_run` are **pure**: text in, numbers out. They know
  the column vocabulary a run's CSV is written with and nothing else.
* `render_figure` decides *what to draw* in `_plot_request` — also pure — and
  only then touches matplotlib, lazily, through the Agg backend.

That split is what makes the analysis plot testable. `tests/conftest.py`
replaces matplotlib with a `MagicMock`, so asserting on Figure/Axes calls
asserts on the mock's behaviour rather than on ours. `_plot_request` returns
the series that *were* requested, which is the thing worth pinning.

**REDPERCENT-17.** A request the data cannot satisfy — a 2D/3D plot with a
dimension left unselected, or one whose samples do not line up with the red
series — used to fall through every branch and return a bare `Figure` with no
`Axes` at all: an indistinguishable blank PNG. Every path here ends in a
request, and a request that cannot be drawn is `kind="message"` carrying the
reason, which the renderer draws onto the figure.

**REDPERCENT-16/ERRORS-7.** A cell that was never measured is empty in the
CSV, never `0.0` — `0.0` is a position the stage can actually be at. The
parser keeps such a cell as `None` in a row that stays aligned with every
other column, and the plot request filters to the samples that carry every
series it needs. The old parser dropped the whole row, which threw away a
perfectly good red sample because a position read had failed; at the sampling
rates this station now runs at, that would discard most of the dataset.
"""
import csv
import io

import palette
import json
import re
from pathlib import Path

#: The column vocabulary `RunLog.save` writes and this module reads. One
#: definition, imported by the writer, so the two cannot drift apart.
TIME_COLUMN = "t_s"
RED_COLUMN = "red_percent"
AGE_COLUMN = "position_age_s"
POSITION_UNIT = "steps"
VELOCITY_UNIT = "steps_per_s"

#: Headers carry units and never say "Stepper" — the position source is a
#: probe of whatever family, named in the sidecar, not in every column name.
_POSITION_PATTERN = re.compile(r"^(?P<axis>[A-Za-z]+)_position(?:_\w+)?$")
_VELOCITY_PATTERN = re.compile(r"^(?P<axis>[A-Za-z]+)_velocity(?:_\w+)?$")

#: Files written before the rename still load: `Red Percent`, `Timestamp`,
#: `Stepper X Location`, `Stepper X Velocity`.
_LEGACY_RED = "Red Percent"
_LEGACY_TIME = "Timestamp"
_LEGACY_POSITION = re.compile(r"^(?:Stepper\s+)?(?P<axis>\w+)\s+Location$")
_LEGACY_VELOCITY = re.compile(r"^(?:Stepper\s+)?(?P<axis>\w+)\s+Velocity$")

_RED_HEADERS = frozenset({RED_COLUMN, _LEGACY_RED})
_TIME_HEADERS = frozenset({TIME_COLUMN, _LEGACY_TIME})

_EMPTY = {"metadata": {}, "red_percents": [], "times": [], "dims": [],
          "dim_data": {}, "dim_velocity": {}, "position_ages": [],
          "dropped_rows": 0}


def parse_run_csv(csv_text):
    """Parse a run CSV into aligned series.

    Returns::

        {"metadata": {...},          # legacy '#' block, or {} for a plain file
         "red_percents": [float],    # one entry per readable row
         "times": [float | None],    # t_s since run start
         "dims": ["X", "Y"],         # axes the run synced, in column order
         "dim_data": {dim: [float | None]},
         "dim_velocity": {dim: [float | None]},
         "position_ages": [float | None],
         "dropped_rows": int}        # rows with no readable red percent

    Every list is the same length. `None` means "not measured on this row" —
    an empty cell — and is never silently turned into a number.
    """
    reader = csv.reader(io.StringIO(csv_text))
    metadata, header, rows = {}, None, []
    for row in reader:
        if not row:
            continue
        if row[0].startswith("#"):
            if len(row) > 1:
                metadata[row[0].lstrip("#").strip()] = row[1]
            continue
        if header is None:
            if any(cell.strip() in _RED_HEADERS for cell in row):
                header = [cell.strip() for cell in row]
            continue
        rows.append(row)

    if header is None:
        result = dict(_EMPTY)
        result["metadata"] = metadata
        result["dim_data"], result["dim_velocity"] = {}, {}
        return result

    red_index = next(i for i, name in enumerate(header) if name in _RED_HEADERS)
    time_index = next((i for i, name in enumerate(header)
                       if name in _TIME_HEADERS), None)
    age_index = next((i for i, name in enumerate(header)
                      if name == AGE_COLUMN), None)

    dims, position_index, velocity_index = [], {}, {}
    for index, name in enumerate(header):
        axis = _axis_of(name, _POSITION_PATTERN, _LEGACY_POSITION)
        if axis is not None:
            if axis not in dims:
                dims.append(axis)
            position_index[axis] = index
            continue
        axis = _axis_of(name, _VELOCITY_PATTERN, _LEGACY_VELOCITY)
        if axis is not None:
            velocity_index[axis] = index

    red_percents, times, ages, dropped = [], [], [], 0
    dim_data = {dim: [] for dim in dims}
    dim_velocity = {dim: [] for dim in dims}
    for row in rows:
        red = _number(row, red_index)
        if red is None:
            dropped += 1
            continue
        red_percents.append(red)
        times.append(_number(row, time_index))
        ages.append(_number(row, age_index))
        for dim in dims:
            dim_data[dim].append(_number(row, position_index.get(dim)))
            dim_velocity[dim].append(_number(row, velocity_index.get(dim)))

    return {"metadata": metadata, "red_percents": red_percents, "times": times,
            "dims": dims, "dim_data": dim_data, "dim_velocity": dim_velocity,
            "position_ages": ages, "dropped_rows": dropped}


def load_run(csv_path):
    """Load a saved run from disk, from either artifact shape.

    The configuration lives in a sibling `<stem>_station_meta.json`
    (REDPERCENT-22). A legacy CSV carries its own `#` block instead and has no
    sidecar; both stay readable, and a corrupt sidecar never makes the samples
    unreadable.
    """
    csv_path = Path(csv_path)
    result = parse_run_csv(csv_path.read_text())

    sidecar = csv_path.with_name(csv_path.stem + "_station_meta.json")
    if not sidecar.exists() and csv_path.stem.endswith("_position"):
        stem = csv_path.stem[: -len("_position")]
        sidecar = csv_path.with_name(stem + "_station_meta.json")
    if sidecar.exists():
        try:
            result["metadata"] = json.loads(sidecar.read_text())
        except (ValueError, OSError):
            pass
    return result


def render_figure(plot_type, dim1=None, dim2=None, dim3=None,
                  red_percents=(), dim_data=None, times=None, *, size=None,
                  dpi=None):
    """PNG bytes of the analysis plot. One image, drawn once, shown by all
    three views — Tk hand-built its own matplotlib canvas, PySide bolted on a
    duplicate in a dialog, and the Web client could not show it at all.

    matplotlib is imported lazily and driven through Agg with the `Figure`
    class directly: no pyplot, no global state, no backend to pick per view.

    `size` is `(width, height)` in inches and `dpi` its resolution; the
    defaults (`FIGURE_SIZE`, `FIGURE_DPI`) fit a module card.
    """
    request = _plot_request(plot_type, dim1, dim2, dim3, red_percents,
                            dim_data or {}, times)
    return _draw(request, size=size, dpi=dpi)


# -- what to draw, decided without matplotlib ------------------------------

def _plot_request(plot_type, dim1, dim2, dim3, red_percents, dim_data,
                  times=None):
    """The series a `plot_type` asks for, or the reason it cannot be drawn.

    Pure, and the only place the "can this be plotted?" decision is made, so
    a test can assert on the series requested rather than on matplotlib.
    """
    red = list(red_percents or ())
    kind = (plot_type or "0D").upper()
    if not red:
        return _message("No samples in this run.")

    if kind == "0D" or not dim1:
        x, y = _aligned(red, [times] if times else [], red)
        if times and x[0]:
            return {"kind": "line", "x": x[0], "y": y, "x_label": "time (s)",
                    "y_label": "red (%)", "title": "Red percent over time"}
        return {"kind": "line", "x": list(range(len(red))), "y": red,
                "x_label": "sample", "y_label": "red (%)",
                "title": "Red percent over time"}

    if kind == "1D":
        series = dim_data.get(dim1)
        if not series:
            return _message(f"No {dim1} position was recorded in this run.")
        (xs,), ys = _aligned(red, [series], red)
        if not xs:
            return _message(f"No samples carry both {dim1} and a red percent.")
        paired = sorted(zip(xs, ys))
        return {"kind": "line", "x": [p[0] for p in paired],
                "y": [p[1] for p in paired],
                "x_label": f"{dim1} position ({POSITION_UNIT})",
                "y_label": "red (%)", "title": f"Red percent vs {dim1}"}

    if kind == "2D":
        if not (dim1 and dim2):
            return _message("Select two dimensions for a 2D plot.")
        (xs, ys), cs = _aligned(red, [dim_data.get(dim1), dim_data.get(dim2)], red)
        if not xs:
            return _message(f"No samples with both {dim1} and {dim2} recorded "
                            "alongside a red percent.")
        return {"kind": "scatter3d", "x": xs, "y": ys, "z": cs, "c": cs,
                "x_label": f"{dim1} position ({POSITION_UNIT})",
                "y_label": f"{dim2} position ({POSITION_UNIT})",
                "z_label": "red (%)", "c_label": "red (%)",
                "title": f"Red percent over {dim1}/{dim2}"}

    if kind == "3D":
        if not (dim1 and dim2 and dim3):
            return _message("Select three dimensions for a 3D plot.")
        (xs, ys, zs), cs = _aligned(
            red, [dim_data.get(dim1), dim_data.get(dim2), dim_data.get(dim3)], red)
        if not xs:
            return _message(f"No samples with {dim1}, {dim2} and {dim3} all "
                            "recorded alongside a red percent.")
        return {"kind": "scatter3d", "x": xs, "y": ys, "z": zs, "c": cs,
                "x_label": f"{dim1} position ({POSITION_UNIT})",
                "y_label": f"{dim2} position ({POSITION_UNIT})",
                "z_label": f"{dim3} position ({POSITION_UNIT})",
                "c_label": "red (%)",
                "title": f"Red percent over {dim1}/{dim2}/{dim3}"}

    return _message(f"Unknown plot type: {plot_type!r}.")


def _message(reason):
    return {"kind": "message", "reason": reason}


def _aligned(red, series_list, values):
    """Keep only the samples where every series carries a measured number.

    Returns `(columns, values)`. A series shorter than `red` — a legacy file,
    or one written while an axis was added — simply runs out, and the samples
    past its end are dropped rather than paired with the wrong row.
    """
    columns = [[] for _ in series_list]
    kept = []
    for index in range(len(red)):
        row = []
        for series in series_list:
            if series is None or index >= len(series) or series[index] is None:
                row = None
                break
            row.append(series[index])
        if row is None:
            continue
        for column, value in zip(columns, row):
            column.append(value)
        kept.append(values[index])
    return columns, kept


# -- drawing, the only part that touches matplotlib ------------------------

#: The default figure: inches and dots per inch. 640x400 px fits a module
#: card in every view without scaling text below reading size; an 800x600
#: figure was shown at 0.48x on the Web (6.7 px text) and cropped in Qt
#: (UXPM-2/3). A caller that knows its slot passes `size`.
FIGURE_SIZE = (6.4, 4.0)
FIGURE_DPI = 100

#: Past this many samples a marker per sample is a ribbon, not a reading.
MARKER_LIMIT = 200

#: Text sizes, in points at FIGURE_DPI: readable at 1x.
TICK_SIZE, LABEL_SIZE, TITLE_SIZE = 10, 11, 12


def _line_style(count):
    """The trace line: `palette.ACCENT`, with markers only while few."""
    few = count <= MARKER_LIMIT
    return {"color": palette.ACCENT, "linestyle": "-", "linewidth": 1.5,
            "marker": "o" if few else None, "markersize": 3 if few else 0}


def _colormap():
    """The 3D scatter's map: viridis from its dark end to teal, so every
    value stands off the panel (`palette.SURFACE`) at 3:1 or better and no
    colour approaches the stop's red. The top of the range follows the
    panel tone: 0.48 on the 2026-09-25 panel, 0.44 on the Signature panel
    (2026-09-27, `#d8dcdb`), measured against the test that pins the
    contrast. (On the earlier dark panel this was plasma's bright half.)"""
    import numpy
    from matplotlib import colormaps
    from matplotlib.colors import ListedColormap
    base = colormaps["viridis"]
    return ListedColormap(base(numpy.linspace(0.0, 0.44, 256)),
                          name="station-sheet")


def _style_axes(axes):
    """The station's dark surface, readable text, dark 3D panes."""
    axes.set_facecolor(palette.SURFACE)
    axes.tick_params(colors=palette.TEXT, labelcolor=palette.TEXT,
                     labelsize=TICK_SIZE)
    for spine in axes.spines.values():
        spine.set_color(palette.MUTED)
    axis_list = [axes.xaxis, axes.yaxis]
    if hasattr(axes, "zaxis"):
        axis_list.append(axes.zaxis)
        for axis in axis_list:
            axis.set_pane_color(palette.SURFACE)
            axis.pane.set_edgecolor(palette.GRID)
    for axis in axis_list:
        axis.label.set_color(palette.TEXT)
        axis.label.set_fontsize(LABEL_SIZE)
    axes.title.set_color(palette.TEXT)
    axes.title.set_fontsize(TITLE_SIZE)
    axes.grid(True, color=palette.GRID, linewidth=0.5)
    for text in axes.texts:
        text.set_color(palette.TEXT)


def _draw(request, size=None, dpi=None):
    from matplotlib.figure import Figure

    figure = Figure(figsize=tuple(size or FIGURE_SIZE), dpi=dpi or FIGURE_DPI,
                    facecolor=palette.SURFACE)
    try:
        # Explicit Agg: no pyplot, no global backend state, nothing that needs
        # a display. `savefig(format="png")` would pick Agg on its own, but
        # saying so is what keeps this callable from a worker thread.
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(figure)
    except ImportError:
        pass

    if request["kind"] == "message":
        axes = figure.add_subplot(111)
        axes.axis("off")
        axes.text(0.5, 0.5, request["reason"], ha="center", va="center",
                  wrap=True, fontsize=LABEL_SIZE, transform=axes.transAxes)
    elif request["kind"] == "line":
        axes = figure.add_subplot(111)
        axes.plot(request["x"], request["y"], **_line_style(len(request["y"])))
        axes.set_xlabel(request["x_label"])
        axes.set_ylabel(request["y_label"])
        axes.set_title(request["title"])
    else:
        axes = figure.add_subplot(111, projection="3d")
        drawn = axes.scatter(request["x"], request["y"], request["z"],
                             c=request["c"], cmap=_colormap(), marker="o",
                             s=12 if len(request["c"]) > MARKER_LIMIT else 20)
        axes.set_xlabel(request["x_label"])
        axes.set_ylabel(request["y_label"])
        axes.set_zlabel(request["z_label"])
        bar = figure.colorbar(drawn, ax=axes, label=request["c_label"])
        bar.ax.tick_params(colors=palette.TEXT, labelcolor=palette.TEXT,
                           labelsize=TICK_SIZE)
        bar.ax.yaxis.label.set_color(palette.TEXT)
        bar.outline.set_edgecolor(palette.MUTED)
        axes.set_title(request["title"])

    for each in figure.get_axes():
        if each is axes:
            _style_axes(each)
    try:
        figure.tight_layout()
    except Exception:
        pass     # a 3D axes may refuse; the figure still draws
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    return buffer.getvalue()


def _axis_of(name, pattern, legacy):
    match = pattern.match(name) or legacy.match(name)
    return match.group("axis").upper() if match else None


def _number(row, index):
    if index is None or index >= len(row):
        return None
    text = row[index].strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# -- the Transfer Map's figures (MAP-3, 2026-09-27) -------------------------
#
# Same split as the analysis plot: `transfer_request` decides what to draw
# from plain rows, without matplotlib, and `render_transfer_figure` draws it.
# A trial row is `{"id", "tilt", "speed", "force": {definition: value|None},
# "force_class", "width", "width_sigma", "width_source"}`; the model builds
# the rows from its store and computes the force indices from each trial's
# raw profile. `tilt` is collected and kept in the row but NOT drawn (owner
# ruling 2026-10-07: no tilt axis); `force_class` ("Low", "Medium", "High" or
# absent) is the bench's `trials.force_class`, read with `.get`.
# `width` is the chosen one (`transfer_map_analysis.pick_width`: AFM when
# present, else optical) and `width_source` says which: "afm", "optical" or
# None (a row without the key and with a width is AFM, as before version 6).

#: The figure types, in dropdown order.
TRANSFER_FIGURES = ("map3d", "slice", "compare", "profile")
#: The force classes the bench records (`trials.force_class`), lowest
#: first: the map's y axis, bottom to top. A trial with none (or one not in
#: this list) is "Unclassed", a band drawn below them only when needed.
FORCE_CLASSES = ("Low", "Medium", "High")
UNCLASSED = "Unclassed"
#: What the curve figure can be drawn for: every band, or one of them.
FORCE_BANDS = ("All forces",) + FORCE_CLASSES + (UNCLASSED,)
#: Grid resolution of the curves over speed.
SLICE_GRID = 40
#: The curve's Gaussian process, in speed scaled to [0, 1].
SLICE_LENGTH = 0.35
SPEED_LABEL = "speed (steps/s)"
FORCE_CLASS_LABEL = "force class"
WIDTH_LABEL = "channel width (um)"
#: Which widths the slice and the comparison fit (store v6, owner
#: 2026-10-04: AFM only by default; optical on request, trusted less).
WIDTH_SOURCES = ("AFM only", "AFM, else optical")
#: An optical width with no sigma of its own gets this many times the AFM
#: default noise (0.05 x the widths' spread) in the slice's GP (Q19).
OPTICAL_SIGMA_FACTOR = 3
#: Two lines: one would be clipped at the station's figure size.
MAP3D_TITLE = ("Transfer map: speed by force class\n(filled: AFM width; "
               "ringed: optical width; hollow: no width yet)")


def _source(row):
    """A row's width source; rows from before version 6 carry none."""
    if "width_source" in row:
        return row["width_source"]
    return "afm" if row.get("width") is not None else None


def with_width(rows, width_source):
    """The rows whose width the chosen width source admits."""
    if width_source not in WIDTH_SOURCES:
        raise ValueError(f"not a width source: {width_source!r}")
    admitted = ("afm",) if width_source == WIDTH_SOURCES[0] else ("afm", "optical")
    return [row for row in rows if _source(row) in admitted]


def width_noise(rows, spread):
    """The GP's per-point noise variance: the width's own sigma, or 0.05 x
    the spread for AFM and `OPTICAL_SIGMA_FACTOR` times that for optical."""
    out = []
    for row in rows:
        default = 0.05 * spread * (OPTICAL_SIGMA_FACTOR
                                   if _source(row) == "optical" else 1)
        out.append((row.get("width_sigma") or default) ** 2)
    return out


def _sources_note(rows):
    """"3 AFM" or "3 AFM, 1 optical": a figure says which widths it used."""
    afm = sum(1 for row in rows if _source(row) == "afm")
    optical = sum(1 for row in rows if _source(row) == "optical")
    return f"{afm} AFM" + (f", {optical} optical" if optical else "")


def transfer_request(kind, trials, definition, *, band="All forces",
                     profile=None, marks=None, definitions=None,
                     width_source=WIDTH_SOURCES[0]):
    """What a Transfer Map figure of `kind` asks for, or why it cannot be
    drawn. Pure; `kind` is one of `TRANSFER_FIGURES`; `width_source` (one of
    `WIDTH_SOURCES`) picks the widths the slice and the comparison use."""
    trials = list(trials or ())
    if width_source not in WIDTH_SOURCES:
        raise ValueError(f"not a width source: {width_source!r}")
    if kind == "profile":
        return _profile_request(profile, marks or {})
    if kind == "compare":
        return _compare_request(trials, definitions or (definition,), width_source)
    if not trials:
        return _message("No trials yet. Arm a trial, lower the tip, then "
                        "Finish; or import trials.")
    placed = [row for row in trials if row.get("speed") is not None]
    if not placed:
        return _message("No trial has a speed yet.")
    if kind == "map3d":
        bands = _bands(placed)
        position = {name: i for i, name in enumerate(bands)}
        return {"kind": "map3d",
                "x": [row["speed"] for row in placed],
                "y": [position[force_class(row)] for row in placed],
                "bands": bands,
                "c": [row.get("width") for row in placed],
                "measured": [_source(row) == "afm" for row in placed],
                "optical": [_source(row) == "optical" for row in placed],
                "x_label": SPEED_LABEL, "y_label": FORCE_CLASS_LABEL,
                "c_label": WIDTH_LABEL,
                "title": MAP3D_TITLE}
    if kind == "slice":
        return _slice_request(placed, definition, band, width_source)
    return _message(f"Unknown figure type: {kind!r}.")


def force_class(row):
    """A row's force class: "Low", "Medium" or "High" (any case), else
    "Unclassed". Rows without the key (the Transfer Map before it ports
    `trials.force_class`) are Unclassed."""
    text = str(row.get("force_class") or "").strip().lower()
    for name in FORCE_CLASSES:
        if text == name.lower():
            return name
    return UNCLASSED


def _bands(rows):
    """The map's bands, bottom to top: Unclassed (only when some trial has
    no class), then Low, Medium, High (always: the axis does not move
    between a half-classed map and a full one)."""
    unclassed = any(force_class(row) == UNCLASSED for row in rows)
    return ([UNCLASSED] if unclassed else []) + list(FORCE_CLASSES)


def _span(values):
    low, high = min(values), max(values)
    if high - low <= 0:
        return low - 0.5, high + 0.5
    margin = 0.05 * (high - low)
    return low - margin, high + margin


def map_limits(request):
    """Axis limits for the map: speed padded by `_span`, so one trial (or
    several at one speed) does not leave matplotlib autoscaling to a hair's
    width whose tick labels then read as a wrong speed; the force-class axis
    holds one unit per band. None for an axis with no values."""
    speeds = [v for v in request.get("x", ()) if v is not None]
    bands = request.get("bands") or ()
    return {"x": _span(speeds) if speeds else None,
            "y": (-0.5, len(bands) - 0.5) if bands else None}


def _slice_request(placed, definition, band, width_source=WIDTH_SOURCES[0]):
    """Width over speed, one curve per force class: each band's Gaussian
    process mean and sigma over its measured trials (AFM only, or AFM else
    optical: `width_source`), and those trials as points. A band with one
    measured trial is its point alone; `band` (one of `FORCE_BANDS`) draws
    just that band."""
    import numpy
    from model import transfer_map_analysis as tma
    if band not in (None, "") and band not in FORCE_BANDS:
        raise ValueError(f"not a force band: {band!r}")
    measured = with_width(placed, width_source)
    names = [n for n in (list(FORCE_CLASSES) + [UNCLASSED])
             if band in (None, "", FORCE_BANDS[0], n)]
    by_band = {n: [r for r in measured if force_class(r) == n] for n in names}
    by_band = {n: rows for n, rows in by_band.items() if rows}
    if not any(len(rows) >= 2 for rows in by_band.values()):
        where = ("" if band in (None, "", FORCE_BANDS[0])
                 else f" in the {band} force class")
        return _message(f"Measure the width of at least two trials of one "
                        f"force class{where} to draw a curve over speed.")
    (x0, x1) = _span([row["speed"] for row in placed])
    grid = numpy.linspace(x0, x1, SLICE_GRID)
    unit = lambda v: (numpy.asarray(v, dtype=float) - x0) / (x1 - x0)  # noqa: E731
    lines = []
    for name, rows in by_band.items():
        line = {"band": name, "points_x": [r["speed"] for r in rows],
                "points_c": [r["width"] for r in rows],
                "points_optical": [_source(r) == "optical" for r in rows],
                "mean": None, "sigma": None}
        if len(rows) >= 2:
            widths = numpy.array([r["width"] for r in rows], dtype=float)
            spread = float(widths.std()) or 1.0
            noise = numpy.array(width_noise(rows, spread))
            mean, variance = tma.gp_predict(
                unit([r["speed"] for r in rows]).reshape(-1, 1), widths,
                unit(grid).reshape(-1, 1), length=SLICE_LENGTH, noise=noise)
            line["mean"] = mean.tolist()
            line["sigma"] = numpy.sqrt(variance).tolist()
        lines.append(line)
    used = [r for rows in by_band.values() for r in rows]
    return {"kind": "slice", "x": grid.tolist(), "lines": lines,
            "x_label": SPEED_LABEL, "y_label": WIDTH_LABEL,
            "title": f"Width over speed by force class "
                     f"({_sources_note(used)}; band: one sigma)"}


def _compare_request(trials, definitions, width_source=WIDTH_SOURCES[0]):
    """One panel per definition: force index against width, same trials
    (the widths `width_source` admits). Before any such width exists,
    against the trial number instead."""
    if not trials:
        return _message("No trials to compare yet.")
    widths = {id(row) for row in with_width(trials, width_source)}
    any_width = bool(widths)
    panels = []
    used = []
    for name in definitions:
        rows = [row for row in trials
                if (row.get("force") or {}).get(name) is not None
                and (id(row) in widths or not any_width)]
        used += rows
        panels.append({
            "name": name,
            "x": [row["force"][name] for row in rows],
            "y": [row["width"] if any_width else row.get("id") for row in rows],
            "yerr": [row.get("width_sigma") or 0.0 for row in rows]
                    if any_width else None,
            "optical": [_source(row) == "optical" for row in rows]})
    title = "Force definitions compared"
    if any_width and any(_source(row) == "optical" for row in used):
        title += " (ringed: optical width)"
    return {"kind": "compare", "panels": panels,
            "y_label": WIDTH_LABEL if any_width else "trial",
            "title": title}


def _profile_request(profile, marks):
    profile = profile or {}
    t, red = list(profile.get("t") or ()), list(profile.get("red") or ())
    if not red:
        return _message("No profile to show. Record a trial first.")
    trial = marks.get("trial_id")
    return {"kind": "profile", "t": t, "red": red,
            "baseline": marks.get("baseline"),
            "operator_t": marks.get("operator_t"),
            "max_t": marks.get("max_t"), "min_t": marks.get("min_t"),
            "red_max": marks.get("red_max"), "red_min": marks.get("red_min"),
            "x_label": "time since Arm (s)", "y_label": "red (%)",
            "title": f"Trial {trial} profile" if trial is not None
                     else "Trial profile"}


def render_transfer_figure(kind, trials, definition, *, band="All forces",
                           profile=None, marks=None, definitions=None,
                           size=None, dpi=None, width_source=WIDTH_SOURCES[0]):
    """PNG bytes of a Transfer Map figure, drawn once for all three views."""
    request = transfer_request(kind, trials, definition, band=band,
                               profile=profile, marks=marks,
                               definitions=definitions,
                               width_source=width_source)
    if request["kind"] == "message":
        return _draw(request, size=size, dpi=dpi)
    return _draw_transfer(request, size=size, dpi=dpi)


def _draw_transfer(request, size=None, dpi=None):
    from matplotlib.figure import Figure
    figure = Figure(figsize=tuple(size or FIGURE_SIZE), dpi=dpi or FIGURE_DPI,
                    facecolor=palette.SURFACE)
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(figure)
    except ImportError:
        pass
    kind = request["kind"]
    if kind == "map3d":
        # The map is flat since 2026-10-07 (no tilt axis); the kind keeps its
        # name because the Transfer Map's figure table points at it.
        axes = figure.add_subplot(111)
        optical_flags = request.get("optical") or [False] * len(request["measured"])
        done = [i for i, m in enumerate(request["measured"]) if m]
        ringed = [i for i, o in enumerate(optical_flags) if o]
        pending = [i for i, (m, o) in enumerate(zip(request["measured"],
                                                     optical_flags))
                   if not m and not o]
        pick = lambda key, idx: [request[key][i] for i in idx]  # noqa: E731
        coloured = pick("c", done + ringed)
        scale = ({"vmin": min(coloured), "vmax": max(coloured)}
                 if coloured else {})
        drawn = None
        if done:
            drawn = axes.scatter(pick("x", done), pick("y", done),
                                 c=pick("c", done), cmap=_colormap(),
                                 marker="o", s=40, **scale)
        if ringed:
            # Optical width (store v6): the same colour scale, ringed so it
            # never reads as an AFM measurement.
            shown = axes.scatter(pick("x", ringed), pick("y", ringed),
                                 c=pick("c", ringed), cmap=_colormap(),
                                 marker="o", s=60, edgecolors=palette.TEXT,
                                 linewidths=1.6, **scale)
            drawn = drawn or shown
        if drawn is not None:
            _colorbar(figure, drawn, axes, request["c_label"])
        if pending:
            axes.scatter(pick("x", pending), pick("y", pending),
                         facecolors="none", edgecolors=palette.MUTED,
                         marker="o", s=40)
        limits = map_limits(request)
        if limits["x"] is not None:
            axes.set_xlim(*limits["x"])
        if limits["y"] is not None:
            axes.set_ylim(*limits["y"])
        axes.set_yticks(range(len(request["bands"])))
        axes.set_yticklabels(request["bands"])
        from matplotlib.ticker import MaxNLocator
        axes.xaxis.set_major_locator(MaxNLocator(6))
        panels = [axes]
    elif kind == "slice":
        axes = figure.add_subplot(111)
        shades = [0.0, 0.55, 1.0]      # the sheet map's ends and middle
        styles = {name: ("-", "--", ":")[i] for i, name in enumerate(FORCE_CLASSES)}
        styles[UNCLASSED] = "-."
        cmap = _colormap()
        for line in request["lines"]:
            name = line["band"]
            index = FORCE_CLASSES.index(name) if name in FORCE_CLASSES else None
            colour = cmap(shades[index]) if index is not None else palette.MUTED
            if line["mean"] is not None:
                mean = line["mean"]
                sigma = line["sigma"]
                axes.fill_between(request["x"],
                                  [m - s for m, s in zip(mean, sigma)],
                                  [m + s for m, s in zip(mean, sigma)],
                                  color=colour, alpha=0.15, linewidth=0)
                axes.plot(request["x"], mean, color=colour, linewidth=1.8,
                          linestyle=styles[name], label=name)
            flags = line["points_optical"]
            for optical in (False, True):
                idx = [i for i, f in enumerate(flags) if f == optical]
                if idx:
                    axes.scatter([line["points_x"][i] for i in idx],
                                 [line["points_c"][i] for i in idx],
                                 color=colour, s=60 if optical else 30,
                                 edgecolors=palette.TEXT,
                                 linewidths=1.6 if optical else 0.6,
                                 label=None if line["mean"] is not None or optical
                                 else name)
        legend = axes.legend(fontsize=TICK_SIZE - 2, frameon=False,
                             title=FORCE_CLASS_LABEL)
        legend.get_title().set_color(palette.TEXT)
        for text in legend.get_texts():
            text.set_color(palette.TEXT)
        panels = [axes]
    elif kind == "compare":
        count = max(1, len(request["panels"]))
        cols = min(3, count)
        rows = (count + cols - 1) // cols
        panels = []
        for index, panel in enumerate(request["panels"]):
            axes = figure.add_subplot(rows, cols, index + 1)
            if panel["x"]:
                flags = panel.get("optical") or [False] * len(panel["x"])
                for optical in (False, True):
                    idx = [i for i, f in enumerate(flags) if f == optical]
                    if not idx:
                        continue
                    yerr = ([panel["yerr"][i] for i in idx]
                            if panel["yerr"] is not None else None)
                    axes.errorbar([panel["x"][i] for i in idx],
                                  [panel["y"][i] for i in idx], yerr=yerr,
                                  fmt="o", color=palette.ACCENT,
                                  markersize=5 if optical else 3,
                                  markeredgecolor=palette.TEXT if optical
                                  else palette.ACCENT,
                                  markeredgewidth=1.4 if optical else 0.8,
                                  elinewidth=0.8)
            else:
                axes.text(0.5, 0.5, "no values", ha="center", va="center",
                          transform=axes.transAxes, fontsize=TICK_SIZE)
            axes.set_title(panel["name"], fontsize=TICK_SIZE)
            axes.tick_params(labelsize=TICK_SIZE - 2)
            if index % cols == 0:
                axes.set_ylabel(request["y_label"], fontsize=TICK_SIZE - 1)
            panels.append(axes)
        figure.suptitle(request["title"], color=palette.TEXT,
                        fontsize=TITLE_SIZE)
    else:   # profile
        axes = figure.add_subplot(111)
        axes.plot(request["t"], request["red"], **_line_style(len(request["red"])))
        if request.get("baseline") is not None:
            axes.axhline(request["baseline"], color=palette.MUTED,
                         linestyle=":", linewidth=1, label="baseline")
        if request.get("operator_t") is not None:
            axes.axvline(request["operator_t"], color=palette.TEXT,
                         linestyle="--", linewidth=1, label="Mark (operator)")
        for key, level, marker, label in (
                ("max_t", "red_max", "^", "peak (auto)"),
                ("min_t", "red_min", "v", "dip (auto)")):
            if request.get(key) is not None and request.get(level) is not None:
                axes.plot([request[key]], [request[level]], marker=marker,
                          color=palette.TEXT, linestyle="none", markersize=7,
                          label=label)
        legend = axes.legend(fontsize=TICK_SIZE - 2, frameon=False)
        for text in legend.get_texts():
            text.set_color(palette.TEXT)
        panels = [axes]
    for axes in panels:
        _style_axes(axes)
    if kind != "compare":
        panels[0].set_xlabel(request["x_label"])
        panels[0].set_ylabel(request["y_label"])
        panels[0].set_title(request["title"])
    try:
        figure.tight_layout()
    except Exception:
        pass
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    return buffer.getvalue()


def _colorbar(figure, drawn, axes, label, pad=0.05):
    bar = figure.colorbar(drawn, ax=axes, label=label, pad=pad, shrink=0.9)
    bar.ax.tick_params(colors=palette.TEXT, labelcolor=palette.TEXT,
                       labelsize=TICK_SIZE)
    bar.ax.yaxis.label.set_color(palette.TEXT)
    bar.outline.set_edgecolor(palette.MUTED)
    return bar


# -- the Sample DB's figure (flake-coords section 2, Phase 1) ---------------
#
# The chip in its own frame (um): the marked corners, the rectangle the
# derived width and height describe, the flakes (the selected one marked and
# its extent drawn), and the crosshair where the stage is now. Pure request,
# then one renderer, as the Transfer Map's figures are.

def sample_map_request(corners, flakes, crosshair, size_um):
    """What the Sample DB's figure draws, or why it cannot. `corners` maps
    a label to its sample-frame point (um); `flakes` are dicts with
    `label`, `x`, `y` (None when not placed), `selected`, `extent`;
    `crosshair` is the stage now in the sample frame, or None; `size_um`
    is the derived (width, height), or None."""
    if not corners:
        return _message("No corners yet. Mark corner A, then B, with the "
                        "crosshair on each corner.")
    placed = [f for f in flakes if f.get("x") is not None and f.get("y") is not None]
    unplaced = len(flakes) - len(placed)
    rectangle = None
    if size_um:
        w, h = size_um
        rectangle = [(0, 0), (w, 0), (w, h), (0, h)]
    title = f"Sample map ({len(placed)} flake{'s' if len(placed) != 1 else ''}"
    if unplaced:
        title += f"; {unplaced} flake{'s' if unplaced != 1 else ''} not placed"
    return {"kind": "sample", "corners": dict(corners), "flakes": placed,
            "unplaced": unplaced, "crosshair": crosshair, "rectangle": rectangle,
            "x_label": "x along A to B (um)", "y_label": "y toward the chip (um)",
            "title": title + ")"}


def render_sample_figure(request, size=None, dpi=None):
    """PNG bytes of the Sample DB's figure, drawn once for all three views."""
    if request["kind"] == "message":
        return _draw(request, size=size, dpi=dpi)
    from matplotlib.figure import Figure
    figure = Figure(figsize=tuple(size or FIGURE_SIZE), dpi=dpi or FIGURE_DPI,
                    facecolor=palette.SURFACE)
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(figure)
    except ImportError:
        pass
    axes = figure.add_subplot(111)
    if request["rectangle"]:
        xs, ys = zip(*(request["rectangle"] + request["rectangle"][:1]))
        axes.plot(xs, ys, color=palette.MUTED, linewidth=1, linestyle="--")
    for label, (x, y) in sorted(request["corners"].items()):
        axes.plot([x], [y], marker="s", color=palette.TEXT, markersize=6,
                  linestyle="none")
        axes.annotate(label, (x, y), textcoords="offset points", xytext=(5, 5),
                      color=palette.TEXT, fontsize=TICK_SIZE)
    for flake in request["flakes"]:
        if flake.get("extent"):
            ex, ey = zip(*(list(map(tuple, flake["extent"])) + [tuple(flake["extent"][0])]))
            axes.plot(ex, ey, color=palette.ACCENT, linewidth=1)
        axes.plot([flake["x"]], [flake["y"]], marker="o", linestyle="none",
                  color=palette.ACCENT, markersize=8 if flake.get("selected") else 5,
                  markeredgecolor=palette.TEXT if flake.get("selected") else palette.ACCENT,
                  markeredgewidth=1.6 if flake.get("selected") else 0.8)
        axes.annotate(flake["label"], (flake["x"], flake["y"]), textcoords="offset points",
                      xytext=(5, -10), color=palette.TEXT, fontsize=TICK_SIZE - 1)
    if request["crosshair"] is not None:
        cx, cy = request["crosshair"]
        axes.plot([cx], [cy], marker="+", color=palette.TEXT, markersize=14,
                  markeredgewidth=1.4, linestyle="none")
    axes.set_aspect("equal", adjustable="datalim")
    axes.set_xlabel(request["x_label"])
    axes.set_ylabel(request["y_label"])
    axes.set_title(request["title"])
    _style_axes(axes)
    try:
        figure.tight_layout()
    except Exception:
        pass
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    return buffer.getvalue()
