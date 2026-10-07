"""Offline re-analysis of recorded Transfer Map trials (dev tool).

    python dev/reanalyse_trials.py <db.sqlite> [--out DIR] [--write] [--repair-video]

For every trial: load its profile, drop the glitch rows (`settled_mask`),
recompute the extrema and force definitions through the same `detect()` and
`force_indices()` the app uses, and report old vs new. Read-only by default;
`--write` backs the db up first and updates only red_min, red_max and
red_baseline. `--repair-video` writes `trial_repaired.mp4` beside each
trial.mp4 (a VISUAL repair; red % is never re-derived from video).
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
    p.add_argument("--write", action="store_true",
                   help="update red_min/red_max/red_baseline in the db (after a backup)")
    p.add_argument("--repair-video", action="store_true",
                   help="write trial_repaired.mp4 beside each trial video (trial.mp4 or screen.mp4)")
    return p.parse_args(argv)


def _fmt(v):
    return "" if v is None else f"{v:.4g}"


def analyse(conn):
    from model import transfer_map_analysis as tma
    rows = []
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trials)")}
    for trial in conn.execute("SELECT * FROM trials ORDER BY id").fetchall():
        prof = conn.execute("SELECT t_s, red, z FROM profile WHERE trial_id=? "
                            "ORDER BY rowid", (trial["id"],)).fetchall()
        profile = {"t": [r[0] for r in prof], "red": [r[1] for r in prof],
                   "z": [r[2] for r in prof]}
        op = trial["mark_operator_t"]
        found = tma.detect(profile, op) or {}
        marks = {"operator_t": op}
        new = tma.force_indices(profile, marks) if prof else {}
        row = {"id": trial["id"], "rows": len(prof),
               "masked_share": found.get("masked_share"),
               "invalid": trial["invalid"] if "invalid" in cols else None,
               "force_class": trial["force_class"] if "force_class" in cols else None}
        for f, key in (("red_min", "red_min"), ("red_max", "red_max"),
                       ("red_baseline", "baseline")):
            row["old_" + f] = trial[f]
            row["new_" + f] = found.get(key)
        for name in tma.FORCE_DEFINITIONS:
            row["new_" + name] = new.get(name)
        rows.append(row)
    return rows


def report(rows, out_dir, stamp):
    os.makedirs(out_dir, exist_ok=True)
    names = list(rows[0].keys()) if rows else []
    csv_path = os.path.join(out_dir, f"reanalysis_{stamp}.csv")
    with open(csv_path, "w", newline="") as handle:
        w = csv.writer(handle, lineterminator="\n")
        w.writerow(names)
        for r in rows:
            w.writerow(["" if r[n] is None else r[n] for n in names])
    md_path = os.path.join(out_dir, f"reanalysis_{stamp}.md")
    head = ["id", "rows", "masked", "old min", "new min", "old max", "new max",
            "old base", "new base", "shadow_vs_peak", "dip_area", "invalid", "class"]
    with open(md_path, "w") as handle:
        handle.write(f"# Re-analysis {stamp}\n\n| " + " | ".join(head) + " |\n")
        handle.write("|" + "---|" * len(head) + "\n")
        for r in rows:
            share = r["masked_share"]
            cells = [r["id"], r["rows"], "" if share is None else f"{share:.0%}",
                     _fmt(r["old_red_min"]), _fmt(r["new_red_min"]),
                     _fmt(r["old_red_max"]), _fmt(r["new_red_max"]),
                     _fmt(r["old_red_baseline"]), _fmt(r["new_red_baseline"]),
                     _fmt(r.get("new_shadow_vs_peak")), _fmt(r.get("new_dip_area")),
                     "" if r["invalid"] is None else r["invalid"],
                     r["force_class"] or ""]
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
    uri = "file:" + os.path.abspath(args.db) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    rows = analyse(conn)
    conn.close()
    out = args.out or os.path.dirname(os.path.abspath(args.db))
    stamp = datetime.date.today().isoformat()
    md, cs = report(rows, out, stamp)
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
