import csv
import io
import json
from pathlib import Path

def parse_red_percent_csv(csv_text: str) -> dict:
    """Parses the CSV format RedPercentDataLog.save_to_csv() writes: a
    '#'-prefixed metadata block, a blank line, then a header row starting
    with 'Red Percent' followed by 'Stepper {dim} Location'/'Stepper {dim}
    Velocity' column pairs per synced dimension. Returns
    {'metadata': {...}, 'red_percents': [...], 'dims': [...],
    'dim_data': {dim: [...]}} — dim_data only contains Location columns
    (Velocity columns are written to the CSV but not currently plotted by
    any of the three views).
    """
    reader = csv.reader(io.StringIO(csv_text))
    metadata = {}
    header = None
    rows = []
    for row in reader:
        if not row:
            continue
        if row[0].startswith('#'):
            if len(row) > 1:
                metadata[row[0].lstrip('#').strip()] = row[1]
            continue
        if header is None:
            if row[0] == "Red Percent":
                header = row
            continue
        rows.append(row)

    if not header:
        return {"metadata": metadata, "red_percents": [], "dims": [], "dim_data": {}}

    dims = []
    dim_loc_idx = {}
    for i, col in enumerate(header):
        if col.endswith(" Location"):
            dim = col.replace("Stepper ", "").replace(" Location", "")
            dims.append(dim)
            dim_loc_idx[dim] = i

    red_percents = []
    dim_data = {dim: [] for dim in dims}
    for row in rows:
        try:
            red_val = float(row[0])
            row_dim_vals = {}
            for dim, idx in dim_loc_idx.items():
                if len(row) > idx:
                    row_dim_vals[dim] = float(row[idx])
        except (ValueError, IndexError):
            continue
        # Only commit the row once every value in it parsed cleanly — a
        # malformed dimension column must not leave red_percents and
        # dim_data at mismatched lengths.
        red_percents.append(red_val)
        for dim, val in row_dim_vals.items():
            dim_data[dim].append(val)

    return {"metadata": metadata, "red_percents": red_percents, "dims": dims, "dim_data": dim_data}

def load_red_percent_run(csv_path):
    """Load a saved run from disk, from either artifact shape.

    REDPERCENT-22 moved the configuration out of the CSV's `#` rows and into a
    sibling `<stem>_station_meta.json`. Both shapes stay readable:

    - a **new** run's CSV is a plain rectangle, and its metadata comes from
      the sidecar (rich: baseline, focus-area px, threshold, timestamps);
    - a **legacy** CSV carries its `#` block and has no sidecar, so the
      metadata comes from the block exactly as before.

    Returns the same dict as `parse_red_percent_csv`, whose `metadata` key
    holds whichever of the two was found. Sidecar keys are snake_case
    (`probe_name`); legacy block keys are the old labels (`Probe Name`).
    """
    csv_path = Path(csv_path)
    result = parse_red_percent_csv(csv_path.read_text())

    sidecar = csv_path.with_name(csv_path.stem + "_station_meta.json")
    if not sidecar.exists() and csv_path.stem.endswith("_position"):
        # The autosave naming: `<run_id>_position.csv` beside
        # `<run_id>_station_meta.json`.
        stem = csv_path.stem[: -len("_position")]
        sidecar = csv_path.with_name(stem + "_station_meta.json")

    if sidecar.exists():
        try:
            result["metadata"] = json.loads(sidecar.read_text())
        except (ValueError, OSError):
            # A corrupt sidecar must not make the samples unreadable.
            pass
    return result


def render_red_percent_figure(plot_type, dim1, dim2, dim3, red_percents, dim_data):
    """Builds a matplotlib Figure for plot_type in {'0D','1D','2D','3D'} —
    backend-agnostic (returns a plain matplotlib.figure.Figure); the caller
    attaches whatever canvas fits its own context (FigureCanvasTkAgg,
    FigureCanvasQTAgg, or fig.savefig(buf, format='png') for the web view —
    no pyplot/backend-switching needed since this uses the Figure class
    directly, not the pyplot global-state API).
    """
    from matplotlib.figure import Figure
    fig = Figure(figsize=(8, 6), dpi=100)

    if plot_type == "0D" or not dim1:
        ax = fig.add_subplot(111)
        ax.plot(red_percents, marker='o', linestyle='-', color='b')
        ax.set_xlabel('Index (Time / Samples)')
        ax.set_ylabel('Red Percent')
        ax.set_title('Red Percent Data')
        ax.grid(True)
    elif plot_type == "1D":
        ax = fig.add_subplot(111)
        if dim_data.get(dim1) and len(dim_data[dim1]) == len(red_percents):
            paired = sorted(zip(dim_data[dim1], red_percents))
            sorted_xs = [p[0] for p in paired]
            sorted_rs = [p[1] for p in paired]
            ax.plot(sorted_xs, sorted_rs, marker='o', linestyle='-', color='b')
            ax.set_xlabel(f'Stepper {dim1} Location')
        else:
            ax.plot(red_percents, marker='o', linestyle='-', color='b')
            ax.set_xlabel('Index')
        ax.set_ylabel('Red Percent')
        ax.set_title(f'Red Percent vs {dim1}')
        ax.grid(True)
    elif plot_type == "2D" and dim1 and dim2:
        ax = fig.add_subplot(111, projection='3d')
        x, y, z = dim_data.get(dim1, []), dim_data.get(dim2, []), red_percents
        if len(x) == len(z) and len(y) == len(z) and len(z) > 0:
            scatter = ax.scatter(x, y, z, c=z, cmap='coolwarm', marker='o')
            ax.set_xlabel(f'Stepper {dim1}')
            ax.set_ylabel(f'Stepper {dim2}')
            ax.set_zlabel('Red Percent')
            fig.colorbar(scatter, ax=ax, label='Red Percent')
    elif plot_type == "3D" and dim1 and dim2 and dim3:
        ax = fig.add_subplot(111, projection='3d')
        x, y, z, c = dim_data.get(dim1, []), dim_data.get(dim2, []), dim_data.get(dim3, []), red_percents
        if len(x) == len(c) and len(y) == len(c) and len(z) == len(c) and len(c) > 0:
            scatter = ax.scatter(x, y, z, c=c, cmap='coolwarm', marker='o')
            ax.set_xlabel(f'Stepper {dim1}')
            ax.set_ylabel(f'Stepper {dim2}')
            ax.set_zlabel(f'Stepper {dim3}')
            fig.colorbar(scatter, ax=ax, label='Red Percent')

    return fig
