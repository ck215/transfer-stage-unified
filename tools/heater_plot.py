"""Plot the Temperature Controller's readings (Tier P1, reshaped 2026-10-08).

The heater writes no log file of its own any more (owner ruling 2026-10-08):
its readings are kept with each TRIAL in the Transfer Map's store (the
`trial_heater` table, and a `heater.csv` beside the trial's other files),
and in the station's local DEVICE LOG (`<data root>/logs/device_log.sqlite`,
the Temperature Controller's `reading.*` keys, about once a second). This
tool renders either to a PNG: temperature, the board's ramped setpoint and
the endpoint against time, with the latest heating run's overshoot
annotated. It needs nothing from `src/`: it reads the tables and CSVs by
their column names.

    python3 tools/heater_plot.py                          # device log, last 2 h
    python3 tools/heater_plot.py --log PATH --from 2026-10-08T09:00 --to 11:30
    python3 tools/heater_plot.py --trial 12 --store ~/transfer-stage-runs/stores/me/map.sqlite
    python3 tools/heater_plot.py heater.csv -o out.png    # a trial's CSV, or an old log
    python3 tools/heater_plot.py --follow                 # redraw every 5 s (Ctrl+C ends)
    python3 tools/heater_plot.py --stats                  # metrics only, as JSON

Times for --from/--to: ISO local time (`2026-10-08T09:00[:SS]`), a time
today (`09:00`), or Unix seconds. Anyone can run it against a source the
station is writing: SQLite is opened read-only, a half-written CSV row is
skipped. It lives outside `src/` because matplotlib is a display library and
nothing in the app imports it.
"""
import argparse
import csv
import json
import os
import pathlib
import sqlite3
import sys
import time

NUMERIC = ("wall_epoch_s", "elapsed_s", "board_timer_s", "temperature_c",
           "setpoint_c", "endpoint_c", "ramp_s_per_c", "kp", "ki", "kd",
           "offset_c", "heater_on")
REQUIRED = ("elapsed_s", "temperature_c", "setpoint_c")
#: A trial's heater columns (the store's `trial_heater`, its `heater.csv`,
#: the export's heater file) -> this tool's names.
TRIAL_NAMES = {"t_s": "elapsed_s", "wall_epoch_s": "wall_epoch_s",
               "board_t_s": "board_timer_s", "temp_c": "temperature_c",
               "setpoint_c": "setpoint_c", "endpoint_c": "endpoint_c",
               "ramp_s_per_c": "ramp_s_per_c", "kp": "kp", "ki": "ki",
               "kd": "kd", "offset_c": "offset_c", "heater_on": "heater_on"}
#: The device log's model and its reading keys -> this tool's names.
HEATER_MODEL = "Temperature Controller"
LOG_KEYS = {"reading.temperature_c": "temperature_c",
            "reading.setpoint_c": "setpoint_c",
            "reading.endpoint_c": "endpoint_c",
            "reading.ramp_s_per_c": "ramp_s_per_c", "reading.kp": "kp",
            "reading.ki": "ki", "reading.kd": "kd",
            "reading.offset_c": "offset_c", "reading.heater_on": "heater_on"}
#: The device log window when --from is not given.
DEFAULT_WINDOW_S = 2 * 3600
SETTLE_BAND_C = 0.5
SSE_WINDOW_S = 120.0

# Reference palette, light mode (dataviz skill): identity by hue AND by line
# style, so the chart reads in greyscale and for colour-blind readers.
INK, INK_MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
TEMP_COLOR, SETPOINT_COLOR, ENDPOINT_COLOR = "#2a78d6", "#1baf7a", "#eb6834"


def data_root():
    root = os.environ.get("TRANSFER_STAGE_DATA_ROOT")
    return pathlib.Path(root).expanduser() if root else (
        pathlib.Path.home() / "transfer-stage-runs")


def default_log():
    """The station's device log (`controller.device_log.default_path`)."""
    return data_root() / "logs" / "device_log.sqlite"


def parse_time(text):
    """Unix seconds from ISO local time, a time today (HH:MM[:SS]) or a
    number of seconds."""
    text = str(text).strip()
    try:
        return float(text)
    except ValueError:
        pass
    for form in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                 "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(text, form))
        except ValueError:
            continue
    for form in ("%H:%M:%S", "%H:%M"):
        try:
            clock = time.strptime(text, form)
        except ValueError:
            continue
        today = time.localtime()
        return time.mktime(today[:3] + clock[3:6] + (0, 0, -1))
    raise ValueError(f"not a time: {text!r} (ISO, HH:MM or seconds)")


def _number(text):
    if text in (None, ""):
        return None
    return float(text)


def _finish(row):
    """A row with every NUMERIC name, or None when a required one is
    missing."""
    for name in NUMERIC:
        row.setdefault(name, None)
    if any(row[name] is None for name in REQUIRED):
        return None
    return row


def load(path):
    """Rows of a CSV, by its header: the merged branch's per-session log
    (`temperature_c`, `elapsed_s`, ...) or a trial's heater file (`t_s`,
    `temp_c`, ...). Dicts of floats (None for a blank field); a row that is
    short or will not parse - the line being written right now - is
    skipped."""
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        trial = "temp_c" in (reader.fieldnames or ())
        for raw in reader:
            try:
                if trial:
                    row = {mine: _number(raw.get(theirs))
                           for theirs, mine in TRIAL_NAMES.items()}
                    row["wall_time"] = ""
                else:
                    row = {"wall_time": raw.get("wall_time") or ""}
                    for name in NUMERIC:
                        row[name] = _number(raw.get(name))
                row = _finish(row)
            except (TypeError, ValueError):
                continue
            if row is not None:
                rows.append(row)
    return rows


def _read_only(path):
    """A read-only connection; FileNotFoundError for a missing file (a
    plot never creates a database)."""
    path = pathlib.Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"no such database: {path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)


def load_trial(store, trial_id):
    """A trial's heater readings from its Transfer Map store (`trial_heater`),
    by time; [] for a trial without any (or a store from before the table)."""
    db = _read_only(store)
    try:
        db.row_factory = sqlite3.Row
        try:
            found = db.execute("SELECT * FROM trial_heater WHERE trial_id = ? "
                               "ORDER BY t_s, rowid", (int(trial_id),)).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        db.close()
    rows = []
    for raw in found:
        raw = dict(raw)
        row = _finish({mine: raw.get(theirs)
                       for theirs, mine in TRIAL_NAMES.items()})
        if row is not None:
            row["wall_time"] = ""
            rows.append(row)
    return rows


def load_log(path, start=None, end=None, model=HEATER_MODEL):
    """The heater's readings from the device log between `start` and `end`
    (Unix seconds; None = open). The log writes a key when it changes, so
    each value is carried forward from its last row, including one written
    before the window. `elapsed_s` counts from the window's first row."""
    db = _read_only(path)
    keys = tuple(LOG_KEYS)
    marks = ", ".join("?" for _ in keys)
    try:
        before, inside = [], []
        if start is not None:
            # Each key's last value before the window.
            before = db.execute(
                f"SELECT key, num FROM readings r WHERE model = ? AND key IN ({marks}) "
                "AND t = (SELECT MAX(t) FROM readings WHERE model = r.model AND "
                "key = r.key AND t < ?)", (model, *keys, float(start))).fetchall()
        sql = (f"SELECT t, key, num FROM readings WHERE model = ? AND key IN "
               f"({marks})")
        args = [model, *keys]
        if start is not None:
            sql += " AND t >= ?"
            args.append(float(start))
        if end is not None:
            sql += " AND t <= ?"
            args.append(float(end))
        inside = db.execute(sql + " ORDER BY t, rowid", args).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    current = {LOG_KEYS[key]: num for key, num in before}
    by_time = {}
    for t, key, num in inside:
        by_time.setdefault(t, []).append((LOG_KEYS[key], num))
    rows, first = [], None
    for t in sorted(by_time):
        current.update(by_time[t])
        first = t if first is None else first
        row = _finish(dict(current, wall_epoch_s=t, elapsed_s=t - first,
                           wall_time=time.strftime("%Y-%m-%dT%H:%M:%S",
                                                   time.localtime(t))))
        if row is not None:
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


def render(path, out=None, stats=None, rows=None, title=None,
           x_label="time since the first reading (min)"):
    """Draw the readings to `out`. `path` is a CSV (read here unless `rows`
    is given; `out` defaults to its name with .png) or, with `rows`, just
    the source's name for the title. Returns out."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = pathlib.Path(path)
    out = pathlib.Path(out) if out else path.with_suffix(".png")
    rows = load(path) if rows is None else rows
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
    ax.set_xlabel(x_label, color=INK_MUTED)
    ax.set_ylabel("temperature (°C)", color=INK_MUTED)
    ax.grid(True, color=GRID, lw=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_MUTED)
    if rows:
        ax.legend(loc="lower right", frameon=False)
    ax.set_title(f"{title or path.name}\n{summary(stats)}", loc="left", fontsize=10,
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


def _source(args):
    """-> (load() -> rows, title, default PNG path, x label) for the source
    the arguments name. Raises FileNotFoundError / ValueError with a
    sentence."""
    if args.trial is not None:
        if not args.store:
            raise ValueError("--trial needs --store PATH (the Transfer Map store)")
        store = pathlib.Path(args.store).expanduser()
        if not store.is_file():
            raise FileNotFoundError(f"no such store: {store}")
        folder = store.parent / store.stem / str(args.trial)
        out = (folder / "heater.png" if folder.is_dir()
               else store.with_name(f"{store.stem}_trial{args.trial}_heater.png"))
        return (lambda: load_trial(store, args.trial),
                f"{store.name} - trial {args.trial}", out,
                "time since the trial's time zero (min)")
    if args.csv:
        path = pathlib.Path(args.csv)
        if not path.is_file():
            raise FileNotFoundError(f"no such file: {path}")
        return (lambda: load(path), path.name, path.with_suffix(".png"),
                "time since the first reading (min)")
    log = pathlib.Path(args.log).expanduser() if args.log else default_log()
    if not log.is_file():
        raise FileNotFoundError(f"no device log at {log} (is the station "
                                "running with this data root?)")
    start = parse_time(args.start) if args.start else None
    end = parse_time(args.end) if args.end else None

    def window():
        begin = start if start is not None else time.time() - DEFAULT_WINDOW_S
        return load_log(log, begin, end, args.model)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(start or time.time()))
    return (window, f"{log.name} - {args.model}",
            log.with_name(f"heater-{stamp}.png"),
            "time since the window's first reading (min)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("csv", nargs="?",
                        help="a heater CSV (a trial's heater.csv, the export's "
                             "heater file, or an old per-session log)")
    parser.add_argument("--trial", type=int, help="a trial's number (with --store)")
    parser.add_argument("--store", help="the Transfer Map store holding --trial")
    parser.add_argument("--log", help="the device log (default: "
                        "<data root>/logs/device_log.sqlite)")
    parser.add_argument("--from", dest="start",
                        help="the device log window's start (default: 2 h ago)")
    parser.add_argument("--to", dest="end", help="the window's end (default: now)")
    parser.add_argument("--model", default=HEATER_MODEL,
                        help="the heater's name in the device log")
    parser.add_argument("-o", "--out", help="PNG path (default: beside the source)")
    parser.add_argument("--follow", action="store_true",
                        help="redraw every --interval seconds until Ctrl+C")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--stats", action="store_true",
                        help="print the metrics as JSON and draw nothing")
    args = parser.parse_args(argv)
    try:
        read, title, default_out, x_label = _source(args)
        rows = read()
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"heater_plot: {exc}", file=sys.stderr)
        return 1
    if args.stats:
        print(json.dumps(analyse(rows), indent=2))
        return 0
    out = pathlib.Path(args.out) if args.out else default_out
    while True:
        stats = analyse(rows)
        render(title, out, stats, rows=rows, title=title, x_label=x_label)
        print(f"{time.strftime('%H:%M:%S')} {out}  {summary(stats)}", flush=True)
        if not args.follow:
            return 0
        try:
            time.sleep(args.interval)
            rows = read()
        except KeyboardInterrupt:
            return 0
        except (OSError, sqlite3.Error) as exc:
            print(f"heater_plot: {exc}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
