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

from station import palette
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
                  red_percents=(), dim_data=None, times=None):
    """PNG bytes of the analysis plot. One image, drawn once, shown by all
    three views — Tk hand-built its own matplotlib canvas, PySide bolted on a
    duplicate in a dialog, and the Web client could not show it at all.

    matplotlib is imported lazily and driven through Agg with the `Figure`
    class directly: no pyplot, no global state, no backend to pick per view.
    """
    request = _plot_request(plot_type, dim1, dim2, dim3, red_percents,
                            dim_data or {}, times)
    return _draw(request)


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

def _draw(request):
    from matplotlib.figure import Figure

    figure = Figure(figsize=(8, 6), dpi=100, facecolor=palette.SURFACE)
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
                  wrap=True, fontsize=11, transform=axes.transAxes)
    elif request["kind"] == "line":
        axes = figure.add_subplot(111)
        axes.plot(request["x"], request["y"], marker="o", linestyle="-",
                  color="b")
        axes.set_xlabel(request["x_label"])
        axes.set_ylabel(request["y_label"])
        axes.set_title(request["title"])
        axes.grid(True)
    else:
        axes = figure.add_subplot(111, projection="3d")
        drawn = axes.scatter(request["x"], request["y"], request["z"],
                             c=request["c"], cmap="coolwarm", marker="o")
        axes.set_xlabel(request["x_label"])
        axes.set_ylabel(request["y_label"])
        axes.set_zlabel(request["z_label"])
        figure.colorbar(drawn, ax=axes, label=request["c_label"])
        axes.set_title(request["title"])

    buffer = io.BytesIO()
    for axes in figure.get_axes():          # the station's dark surface
        axes.set_facecolor(palette.SURFACE)
        axes.tick_params(colors=palette.TEXT, labelcolor=palette.TEXT)
        for spine in axes.spines.values():
            spine.set_color(palette.MUTED)
        axes.xaxis.label.set_color(palette.TEXT)
        axes.yaxis.label.set_color(palette.TEXT)
        axes.title.set_color(palette.TEXT)
        axes.grid(True, color=palette.GRID, linewidth=0.5)
        for text in axes.texts:
            text.set_color(palette.TEXT)
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
