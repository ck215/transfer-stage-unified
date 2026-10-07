"""Offline re-analysis of recorded Transfer Map trials (dev tool).

    python dev/reanalyse_trials.py <db.sqlite> [--out DIR] [--factor NAME]
                                   [--write] [--repair-video]

For every trial: load its profile, drop the glitch rows (`settled_mask`),
recompute the extrema and force definitions through the same `detect()` and
`force_indices()` the app uses, and report old vs new. Read-only by default;
`--write` backs the db up first and updates only red_min, red_max and
red_baseline. `--repair-video` writes `trial_repaired.mp4` beside each
trial.mp4 (a VISUAL repair; red % is never re-derived from video).

`--factor` (RG-2, 2026-10-07) picks the profile column that drives the
extrema: red (the default), green, blue, r_mean, g_mean, b_mean, or a ratio
such as red/green. Only profiles that carry the column can be read with it:
a store or a trial recorded before RGB analysis has red only, and the report
says so (its new cells stay empty). A report for another factor is written
as `reanalysis_<date>_<factor>.*` beside the red one, and `--write` (which
updates the stored RED extrema) runs with the red factor only.
"""
import argparse
import csv
import datetime
import os
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "src"))

NEAR_BLACK_LUMA = 16.0       # mean luma (0-255) below which a frame is black
FIELDS = ("red_min", "red_max", "red_baseline")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("db", help="transfer_map sqlite file")
    p.add_argument("--out", default=None, help="report folder (default: beside the db)")
    p.add_argument("--factor", default="red", type=_factor,
                   help="the profile column that drives the extrema: red "
                        "(default), green, blue, r_mean, g_mean, b_mean, or "
                        "a ratio such as red/green")
    p.add_argument("--write", action="store_true",
                   help="update red_min/red_max/red_baseline in the db (after a backup)")
    p.add_argument("--repair-video", action="store_true",
                   help="write trial_repaired.mp4 beside each trial video (trial.mp4 or screen.mp4)")
    return p.parse_args(argv)


def _factor(text):
    """argparse's `type` for --factor: the factor as given, once the
    analysis's own parser accepts it."""
    from model import transfer_map_analysis as tma
    try:
        tma.parse_factor(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))
    return text


def _fmt(v):
    return "" if v is None else f"{v:.4g}"


def _columns_of(factor):
    """The profile columns `factor` reads, numerator first."""
    from model import transfer_map_analysis as tma
    return [c for c in tma.parse_factor(factor) if c is not None]


def missing_columns(conn, factor):
    """The columns `factor` needs that this store's profile table lacks
    (a store from before RGB analysis has red only)."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(profile)")}
    return [c for c in _columns_of(factor) if c not in have]


def analyse(conn, factor="red"):
    from model import transfer_map_analysis as tma
    rows = []
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trials)")}
    missing = missing_columns(conn, factor)
    extra = [c for c in _columns_of(factor)
             if c not in ("red",) and c not in missing]
    select = "".join(f", {c}" for c in extra)
    for trial in conn.execute("SELECT * FROM trials ORDER BY id").fetchall():
        prof = conn.execute(f"SELECT t_s, red, z{select} FROM profile "
                            "WHERE trial_id=? ORDER BY rowid",
                            (trial["id"],)).fetchall()
        profile = {"t": [r[0] for r in prof], "red": [r[1] for r in prof],
                   "z": [r[2] for r in prof]}
        for k, c in enumerate(extra):
            profile[c] = [r[3 + k] for r in prof]
        op = trial["mark_operator_t"]
        note = ""
        if missing:
            note = (f"no {', '.join(missing)} column: red only (recorded "
                    "before RGB analysis)")
        else:
            empty = [c for c in extra if prof and all(v is None for v in profile[c])]
            if empty:
                note = (f"no {', '.join(empty)} samples: red only (recorded "
                        "before RGB analysis)")
        if note:
            found, new = {}, {}
        else:
            found = tma.detect(profile, op, factor=factor) or {}
            marks = {"operator_t": op}
            new = (tma.force_indices(profile, marks, factor=factor)
                   if prof else {})
        row = {"id": trial["id"], "rows": len(prof),
               "masked_share": found.get("masked_share"),
               "invalid": trial["invalid"] if "invalid" in cols else None,
               "force_class": trial["force_class"] if "force_class" in cols else None,
               "factor": factor, "note": note}
        for f, key in (("red_min", "red_min"), ("red_max", "red_max"),
                       ("red_baseline", "baseline")):
            row["old_" + f] = trial[f]
            row["new_" + f] = found.get(key)
        for name in tma.FORCE_DEFINITIONS:
            row["new_" + name] = new.get(name)
        rows.append(row)
    return rows


def _suffix(factor):
    """The report name's tail for a factor: none for red (the report the
    owner reviews before --write keeps its name), else `_<factor>` with a
    ratio's slash as a dash."""
    return "" if factor == "red" else "_" + factor.replace("/", "-")


def report(rows, out_dir, stamp, factor="red", missing=()):
    os.makedirs(out_dir, exist_ok=True)
    names = list(rows[0].keys()) if rows else []
    stem = f"reanalysis_{stamp}{_suffix(factor)}"
    csv_path = os.path.join(out_dir, f"{stem}.csv")
    with open(csv_path, "w", newline="") as handle:
        w = csv.writer(handle, lineterminator="\n")
        w.writerow(names)
        for r in rows:
            w.writerow(["" if r[n] is None else r[n] for n in names])
    md_path = os.path.join(out_dir, f"{stem}.md")
    head = ["id", "rows", "masked", "old min", "new min", "old max", "new max",
            "old base", "new base", "shadow_vs_peak", "dip_area", "invalid", "class",
            "note"]
    with open(md_path, "w") as handle:
        handle.write(f"# Re-analysis {stamp} (factor: {factor})\n\n")
        if factor != "red":
            handle.write(f"The new min, max and base are the factor's ({factor}); "
                         "the old ones are the stored red extrema.\n")
            if missing:
                handle.write(f"This store's profiles have no {', '.join(missing)} "
                             "column: every profile here is red only (recorded "
                             "before RGB analysis), so no trial has new values.\n")
            else:
                bare = sum(1 for r in rows if r["note"])
                if bare:
                    handle.write(f"{bare} of {len(rows)} trial(s) carry red only "
                                 "(recorded before RGB analysis); their new cells "
                                 "are empty.\n")
            handle.write("\n")
        handle.write("| " + " | ".join(head) + " |\n")
        handle.write("|" + "---|" * len(head) + "\n")
        for r in rows:
            share = r["masked_share"]
            cells = [r["id"], r["rows"], "" if share is None else f"{share:.0%}",
                     _fmt(r["old_red_min"]), _fmt(r["new_red_min"]),
                     _fmt(r["old_red_max"]), _fmt(r["new_red_max"]),
                     _fmt(r["old_red_baseline"]), _fmt(r["new_red_baseline"]),
                     _fmt(r.get("new_shadow_vs_peak")), _fmt(r.get("new_dip_area")),
                     "" if r["invalid"] is None else r["invalid"],
                     r["force_class"] or "", r["note"]]
            handle.write("| " + " | ".join(str(c) for c in cells) + " |\n")
    return md_path, csv_path


def write_back(db, rows):
    backup = db + ".pre-reanalysis.bak"
    if os.path.exists(backup):
        raise SystemExit(f"refusing to --write: {backup} already exists")
    shutil.copy2(db, backup)
    conn = sqlite3.connect(db)
    try:
        with conn:
            for r in rows:
                if r["new_red_max"] is None:
                    continue                       # nothing settled: keep the stored row
                conn.execute("UPDATE trials SET red_min=?, red_max=?, red_baseline=? "
                             "WHERE id=?", (r["new_red_min"], r["new_red_max"],
                                            r["new_red_baseline"], r["id"]))
    finally:
        conn.close()
    return backup


def repair_video(src, dst):
    """Drop near-black frames; `fps` then holds the previous frame across the
    gap, keeping the clip's length. Visual only."""
    probe = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", src, "-vf",
         "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
         "-f", "null", "-"], capture_output=True, text=True)
    lumas = [float(line.split("=")[1]) for line in probe.stdout.splitlines()
             if "YAVG=" in line]
    if not lumas:
        return False
    bad = [i for i, y in enumerate(lumas) if y < NEAR_BLACK_LUMA and i > 0]
    if not bad:
        shutil.copy2(src, dst)
        return True
    expr = "+".join(f"eq(n\\,{i})" for i in bad)
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", src, "-vf",
         f"select='not({expr})',fps=15", "-an", dst],
        capture_output=True, text=True)
    return done.returncode == 0


def main(argv=None):
    args = parse_args(argv)
    if args.write and args.factor != "red":
        raise SystemExit("refusing to --write: it updates the stored red "
                         "extrema, so it runs with --factor red only")
    uri = "file:" + os.path.abspath(args.db) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    missing = missing_columns(conn, args.factor)
    rows = analyse(conn, args.factor)
    conn.close()
    out = args.out or os.path.dirname(os.path.abspath(args.db))
    stamp = datetime.date.today().isoformat()
    md, cs = report(rows, out, stamp, args.factor, missing)
    print(f"{len(rows)} trial(s): {md}, {cs}")
    if args.write:
        print("backup:", write_back(args.db, rows))
    if args.repair_video:
        base = os.path.join(os.path.dirname(os.path.abspath(args.db)), "transfer_map")
        for r in rows:
            # Old trials recorded the region as trial.mp4; since 2026-10-07 the
            # full display is screen.mp4. Prefer the stored video_path.
            folder = os.path.join(base, str(r["id"]))
            stored = r.get("video_path") if hasattr(r, "get") else None
            candidates = ([stored] if stored else []) + [
                os.path.join(folder, "trial.mp4"), os.path.join(folder, "screen.mp4")]
            src = next((c for c in candidates if c and os.path.isfile(c)),
                       os.path.join(folder, "trial.mp4"))
            if os.path.exists(src):
                ok = repair_video(src, os.path.join(base, str(r["id"]), "trial_repaired.mp4"))
                print(f"trial {r['id']}: video {'repaired' if ok else 'FAILED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
