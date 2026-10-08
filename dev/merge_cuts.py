"""One-off, at the owner's word (2026-09-28). Delete this file when done.

The fresh database's trials are cuts 3, 4, 5 ... on tip 9/27/26 Tip1: the
first two cuts are in the set-aside database. This script

  1. shows the trials as they are (`--show`, changes nothing);
  2. renumbers every trial in the live database up by two, moving its
     pictures folder with it, and brings the two 2026-09-27 trials over as
     trials 1 and 2 (tilts 6.5 and 7 deg as the owner noted, cuts 1 and 2,
     profiles and pictures included);
  3. applies CORRECTIONS below (chip, flake, cut, tip per trial, by the NEW
     trial number) and collapses a doubled value ("X X" -> "X") in the chip,
     flake and cut columns, since a few were typed twice;
  4. rewrites the tip record to run from trial 1 to the last one.

Run from the repository root with the station CLOSED:

    python3 merge_cuts.py --show        # look first
    python3 merge_cuts.py               # do it (a backup is written first)

Backup: data/transfer_map_before_merge.sqlite (and the pictures folders are
moved, never deleted). To undo: copy the backup back over
data/transfer_map.sqlite and move data/transfer_map/<n+2> back to <n>.
The old database is never touched.
"""
import os
import shutil
import sqlite3
import sys
from pathlib import Path

TIP = "9/27/26 Tip1"
OLD_DB = "transfer_map_20260927_bench.sqlite"

#: Fixes by NEW trial number (old trials are 1 and 2; today's first trial is
#: 3, the next 4, ...). Any column of the trials table may be named:
#: chip_id, flake_id, cut_id, tip_id, note, tilt_deg ... Edit before running.
CORRECTIONS = {
    # 3: {"chip_id": "7/27/26 Chip 2", "flake_id": "Flake 13", "cut_id": "3"},
    # 4: {"cut_id": "4"},
    # 5: {"cut_id": "5"},
}
#: The two old trials' identity, if you want it on record (blank otherwise).
OLD_TRIALS = {
    1: {"tilt_deg": 6.5, "tilt_source": "typed later", "cut_id": "1",
        "chip_id": "", "flake_id": ""},
    2: {"tilt_deg": 7.0, "tilt_source": "typed later", "cut_id": "2",
        "chip_id": "", "flake_id": ""},
}
SHIFT = len(OLD_TRIALS)

root = Path("data").resolve()
live = root / "transfer_map.sqlite"
PATHS = ("before_full_path", "video_path", "video_index_path", "mark_path",
         "mark_full_path", "before_path", "after_path", "after_full_path")


def undouble(value):
    """'Flake 13 Flake 13' -> 'Flake 13'; 'Flake 13Flake 13' -> 'Flake 13';
    anything else unchanged."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text.isdigit():          # "33" is a number, not "3" twice
        return text
    half, rest = divmod(len(text), 2)
    if rest == 0 and text[:half] == text[half:]:
        return text[:half].strip()
    if rest == 1 and text[half] == " " and text[:half] == text[half + 1:]:
        return text[:half].strip()
    return text


def show(db, title):
    print(f"== {title}")
    for r in db.execute("select id, started_at, tip_id, chip_id, flake_id, cut_id, "
                        "tilt_deg, tilt_source, speed_steps_s, status, note "
                        "from trials order by id"):
        print("  ", dict(r))
    for r in db.execute("select * from tips"):
        print("   tip", dict(r))
    print("   profile rows:", db.execute(
        "select trial_id, count(*) from profile group by trial_id").fetchall())


def repath(value, folder_from, i_from, folder_to, i_to):
    if not isinstance(value, str):
        return value
    return value.replace(f"{folder_from}/{i_from}/", f"{folder_to}/{i_to}/")


def main():
    new = sqlite3.connect(live)
    new.row_factory = sqlite3.Row
    show(new, "live database now")
    if "--show" in sys.argv:
        return
    ids = [r["id"] for r in new.execute("select id from trials order by id")]
    assert ids == list(range(1, len(ids) + 1)), f"trial numbers are not 1..n: {ids}"
    assert all(r["status"] != "armed" for r in new.execute("select status from trials")), \
        "a trial is still armed: finish or abort it first"
    backup = root / "transfer_map_before_merge.sqlite"
    shutil.copy2(live, backup)
    print("backup:", backup)

    old = sqlite3.connect(root / OLD_DB)
    old.row_factory = sqlite3.Row
    newcols = [c[1] for c in new.execute("pragma table_info(trials)")]
    oldcols = [c[1] for c in old.execute("pragma table_info(trials)")]
    common = [c for c in oldcols if c in newcols]
    oldfolder = str(root / Path(OLD_DB).stem)
    newfolder = str(root / "transfer_map")

    with new:
        # 2. renumber up by SHIFT, highest first, folders with them
        for i in reversed(ids):
            j = i + SHIFT
            new.execute("update trials set id=? where id=?", (j, i))
            new.execute("update profile set trial_id=? where trial_id=?", (j, i))
            row = dict(new.execute("select * from trials where id=?", (j,)).fetchone())
            for c in PATHS:
                if row.get(c):
                    new.execute(f"update trials set {c}=? where id=?",
                                (repath(row[c], newfolder, i, newfolder, j), j))
            if os.path.isdir(f"{newfolder}/{i}"):
                shutil.move(f"{newfolder}/{i}", f"{newfolder}/{j}")
        # the two old trials as 1 and 2
        for r in old.execute("select * from trials order by id"):
            d = {c: r[c] for c in common}
            i = d["id"]
            assert i in OLD_TRIALS, f"unexpected old trial {i}"
            for c in PATHS:
                if d.get(c):
                    d[c] = repath(d[c], oldfolder, i, newfolder, i)
            d.update(OLD_TRIALS[i])
            d["note"] = ((d.get("note") or "")
                         + f" (from {OLD_DB}; tilt typed later)").strip()
            cols = list(d)
            new.execute(f"insert into trials ({','.join(cols)}) values "
                        f"({','.join('?' * len(cols))})", [d[c] for c in cols])
            for p in old.execute("select * from profile where trial_id=? order by t_s", (i,)):
                new.execute("insert into profile (trial_id,t_s,red,z,x,y) values (?,?,?,?,?,?)",
                            (i, p["t_s"], p["red"], p["z"], p["x"], p["y"]))
            if os.path.isdir(f"{oldfolder}/{i}") and not os.path.isdir(f"{newfolder}/{i}"):
                shutil.copytree(f"{oldfolder}/{i}", f"{newfolder}/{i}")
        # 3. doubled values, then the explicit corrections
        for r in new.execute("select id, chip_id, flake_id, cut_id from trials").fetchall():
            for c in ("chip_id", "flake_id", "cut_id"):
                fixed = undouble(r[c])
                if fixed != r[c]:
                    print(f"   trial {r['id']}: {c} {r[c]!r} -> {fixed!r}")
                    new.execute(f"update trials set {c}=? where id=?", (fixed, r["id"]))
        for trial_id, fields in CORRECTIONS.items():
            for c, v in fields.items():
                assert c in newcols, f"not a trials column: {c}"
                new.execute(f"update trials set {c}=? where id=?", (v, trial_id))
        # 4. the tip record and the id counter
        last = new.execute("select max(id) from trials").fetchone()[0]
        old_tip = old.execute("select * from tips where tip_id=?", (TIP,)).fetchone()
        created = old_tip["created_at"] if old_tip else None
        new.execute("update tips set first_trial_id=1, last_trial_id=?, "
                    "created_at=coalesce(?, created_at) where tip_id=?", (last, created, TIP))
        new.execute("update sqlite_sequence set seq=? where name='trials'", (last,))
    show(new, "live database after")
    print("next trial will be:", last + 1)


if __name__ == "__main__":
    main()
