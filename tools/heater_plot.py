"""Plot a Temperature Controller reading log (Tier P1).

The station writes every heater reading to
`$TRANSFER_STAGE_DATA_ROOT/heater/heater-<stamp>.csv` (default root
`~/transfer-stage-runs`), one flushed row per reading. This tool renders such
a log to a PNG: temperature, the board's ramped setpoint and the endpoint
against time, with the latest heating run's overshoot annotated. It needs
nothing from `src/`: it reads the CSV by its header.

    python3 tools/heater_plot.py                    # newest log -> PNG beside it
    python3 tools/heater_plot.py LOG.csv -o out.png
    python3 tools/heater_plot.py --follow           # redraw every 5 s (Ctrl+C ends)
    python3 tools/heater_plot.py --stats            # metrics only, as JSON

Anyone can run it against a log another process is writing: a half-written
last row is skipped, not fatal. It lives outside `src/` because matplotlib is
a display library and nothing in the app imports it.
"""
import argparse
import csv
import json
import os
import pathlib
import sys
import time

NUMERIC = ("wall_epoch_s", "elapsed_s", "board_timer_s", "temperature_c",
           "setpoint_c", "endpoint_c", "ramp_s_per_c", "kp", "ki", "kd",
           "offset_c", "heater_on")
REQUIRED = ("elapsed_s", "temperature_c", "setpoint_c")
SETTLE_BAND_C = 0.5
SSE_WINDOW_S = 120.0

# Reference palette, light mode (dataviz skill): identity by hue AND by line
# style, so the chart reads in greyscale and for colour-blind readers.
INK, INK_MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
TEMP_COLOR, SETPOINT_COLOR, ENDPOINT_COLOR = "#2a78d6", "#1baf7a", "#eb6834"


def log_folder():
    root = os.environ.get("TRANSFER_STAGE_DATA_ROOT")
    root = pathlib.Path(root).expanduser() if root else (
        pathlib.Path.home() / "transfer-stage-runs")
    return root / "heater"


def newest_log(folder=None):
    """The most recently written heater log, or None."""
    folder = pathlib.Path(folder) if folder else log_folder()
    logs = list(folder.glob("heater-*.csv")) if folder.exists() else []
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def load(path):
    """Rows as dicts of floats (None for a blank field). Rows that are short
    or will not parse - the line being written right now - are skipped."""
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            try:
                row = {"wall_time": raw.get("wall_time") or ""}
                for name in NUMERIC:
                    text = raw.get(name)
                    row[name] = float(text) if text not in (None, "") else None
                if any(row[name] is None for name in REQUIRED):
                    continue
            except (TypeError, ValueError):
                continue
            rows.append(row)
    return rows


def heating_segment(rows):
    """The last contiguous run of rows with the same non-zero endpoint."""
    end = len(rows)
    while end and not (rows[end - 1]["endpoint_c"] or 0) > 0:
        end -= 1
    if not end:
        return []
    endpoint = rows[end - 1]["endpoint_c"]
    start = end - 1
    while start and rows[start - 1]["endpoint_c"] == endpoint:
        start -= 1
    return rows[start:end]


def analyse(rows):
    """Step-response metrics of the latest heating run. Times are seconds
    from the run's first reading. None where a metric has not happened."""
    segment = heating_segment(rows)
    if not segment:
        return {"endpoint_c": None, "readings": len(rows)}
    endpoint = segment[0]["endpoint_c"]
    t0 = segment[0]["elapsed_s"]
    times = [r["elapsed_s"] - t0 for r in segment]
    temps = [r["temperature_c"] for r in segment]
    start = temps[0]

    def first_at_or_above(level):
        return next((t for t, v in zip(times, temps) if v >= level), None)

    span = endpoint - start
    t10 = first_at_or_above(start + 0.1 * span) if span > 0 else None
    t90 = first_at_or_above(start + 0.9 * span) if span > 0 else None
    reach = first_at_or_above(endpoint)
    peak_index = max(range(len(temps)), key=temps.__getitem__)
    settle = None
    if abs(temps[-1] - endpoint) <= SETTLE_BAND_C:
        index = len(temps) - 1
        while index and abs(temps[index - 1] - endpoint) <= SETTLE_BAND_C:
            index -= 1
        settle = times[index]
    tail = [v for t, v in zip(times, temps) if t >= times[-1] - SSE_WINDOW_S]
    crossings = 0
    if reach is not None:
        after = [v - endpoint for t, v in zip(times, temps) if t >= reach]
        crossings = sum(1 for a, b in zip(after, after[1:])
                        if (a > 0) != (b > 0) and b != 0)
    first = segment[0]
    return {
        "endpoint_c": endpoint,
        "ramp_s_per_c": first["ramp_s_per_c"],
        "kp": first["kp"], "ki": first["ki"], "kd": first["kd"],
        "offset_c": first["offset_c"],
        "readings": len(segment),
        "duration_s": round(times[-1], 1),
        "start_c": start,
        "rise_10_90_s": (round(t90 - t10, 1)
                         if t10 is not None and t90 is not None else None),
        "reach_s": None if reach is None else round(reach, 1),
        "peak_c": temps[peak_index],
        "peak_s": round(times[peak_index], 1),
        "overshoot_c": round(max(0.0, temps[peak_index] - endpoint), 2),
        "settle_s": None if settle is None else round(settle, 1),
        "sse_c": round(sum(tail) / len(tail) - endpoint, 2),
        "crossings": crossings,
        "latest_c": temps[-1],
    }


def render(path, out=None, stats=None):
    """Draw `path` to `out` (default: the CSV's name with .png). Returns out."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = pathlib.Path(path)
    out = pathlib.Path(out) if out else path.with_suffix(".png")
    rows = load(path)
    stats = stats if stats is not None else analyse(rows)
    minutes = [r["elapsed_s"] / 60 for r in rows]

    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=110)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    if rows:
        # Endpoint 0 is "heater off", not a target of 0 °C: a gap, so the
        # scale stays on the readings.
        ax.plot(minutes, [r["endpoint_c"] if r["endpoint_c"] else float("nan")
                          for r in rows],
                color=ENDPOINT_COLOR, lw=1.5, ls=(0, (2, 2)), label="endpoint")
        ax.plot(minutes, [r["setpoint_c"] for r in rows], color=SETPOINT_COLOR,
                lw=1.5, ls="--", label="board setpoint (ramped)")
        ax.plot(minutes, [r["temperature_c"] for r in rows], color=TEMP_COLOR,
                lw=2, label="temperature")
        segment = heating_segment(rows)
        if segment and stats.get("overshoot_c"):
            peak_t = (segment[0]["elapsed_s"] + stats["peak_s"]) / 60
            ax.plot([peak_t], [stats["peak_c"]], "o", ms=8, color=TEMP_COLOR,
                    mec="#fcfcfb", mew=2)
            ax.annotate(f"peak {stats['peak_c']:g} °C, overshoot "
                        f"+{stats['overshoot_c']:.2f} °C",
                        (peak_t, stats["peak_c"]), xytext=(12, -18),
                        textcoords="offset points", color=INK, fontsize=10)
        # The board's setpoint jumps to the reading when a frame lands, so
        # the readings and the endpoints bound everything worth seeing.
        levels = [r["temperature_c"] for r in rows] + [
            r["endpoint_c"] for r in rows if r["endpoint_c"]]
        ax.set_ylim(min(levels) - 1, max(levels) + 1)
    else:
        ax.text(0.5, 0.5, "No readings yet", transform=ax.transAxes,
                ha="center", color=INK_MUTED)
    ax.set_xlabel("time since the log opened (min)", color=INK_MUTED)
    ax.set_ylabel("temperature (°C)", color=INK_MUTED)
    ax.grid(True, color=GRID, lw=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_MUTED)
    if rows:
        ax.legend(loc="lower right", frameon=False)
    ax.set_title(f"{path.name}\n{summary(stats)}", loc="left", fontsize=10,
                 color=INK)
    fig.tight_layout()
    tmp = out.with_name(out.name + ".tmp.png")
    fig.savefig(tmp, facecolor=fig.get_facecolor())
    plt.close(fig)
    os.replace(tmp, out)   # a viewer never opens a half-written PNG
    return out


def summary(stats):
    if not stats.get("endpoint_c"):
        return f"heater off - {stats.get('readings', 0)} readings"

    def value(key, unit="s"):
        v = stats.get(key)
        return "-" if v is None else f"{v:g} {unit}"
    return (f"endpoint {stats['endpoint_c']:g} °C, ramp {stats['ramp_s_per_c']:g} s/°C, "
            f"Kp {stats['kp']:g} Ki {stats['ki']:g} Kd {stats['kd']:g} | "
            f"start {stats['start_c']:g} °C, reach {value('reach_s')}, "
            f"peak {stats['peak_c']:g} °C (+{stats['overshoot_c']:g}), "
            f"settled ±{SETTLE_BAND_C:g} from {value('settle_s')}, "
            f"now {stats['latest_c']:g} °C")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv", nargs="?", help="a heater log (default: newest)")
    parser.add_argument("-o", "--out", help="PNG path (default: beside the CSV)")
    parser.add_argument("--follow", action="store_true",
                        help="redraw every --interval seconds until Ctrl+C")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--stats", action="store_true",
                        help="print the metrics as JSON and draw nothing")
    args = parser.parse_args(argv)

    def target():
        if args.csv:
            return pathlib.Path(args.csv)
        found = newest_log()
        if found is None:
            print(f"no heater log under {log_folder()}", file=sys.stderr)
        return found

    path = target()
    if path is None:
        return 1
    if args.stats:
        print(json.dumps(analyse(load(path)), indent=2))
        return 0
    while True:
        if not args.csv:          # a new session starts a new log: follow it
            path = target() or path
        stats = analyse(load(path))
        out = render(path, args.out, stats)
        print(f"{time.strftime('%H:%M:%S')} {out}  {summary(stats)}", flush=True)
        if not args.follow:
            return 0
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
