"""One-off, at the owner's word (2026-09-28, later). Delete this file when done.

Every trial on record is from sample 7/27/26; the naming of the chip, flake
and cut was typed three ways today. This sets, per trial, the values in the
table below (edit it first if any is wrong), adding the `sample_id` column
if the station has not opened this database since the field was added.

Run from the repository root with the station CLOSED:

    python3 set_sample_ids.py --show      # look, change nothing
    python3 set_sample_ids.py             # apply (a backup is written first)

Backup: data/transfer_map_before_sample.sqlite.
"""
import shutil
import sqlite3
import sys
from pathlib import Path

#: trial number -> the values to set. Anything not named is left as it is.
VALUES = {
    1: {"sample_id": "7/27/26", "chip_id": "2", "flake_id": "13", "cut_id": "1"},
    2: {"sample_id": "7/27/26", "chip_id": "2", "flake_id": "13", "cut_id": "2"},
    3: {"sample_id": "7/27/26", "chip_id": "2", "flake_id": "13", "cut_id": "3"},
    4: {"sample_id": "7/27/26", "chip_id": "2", "flake_id": "13", "cut_id": "4"},
    5: {"sample_id": "7/27/26", "chip_id": "2", "flake_id": "13", "cut_id": "5"},
}
#: Trials not in VALUES get this sample id if theirs is blank.
DEFAULT_SAMPLE = "7/27/26"

live = Path("data").resolve() / "transfer_map.sqlite"
db = sqlite3.connect(live)
db.row_factory = sqlite3.Row


def show(title):
    print(f"== {title}")
    have = {c[1] for c in db.execute("pragma table_info(trials)")}
    cols = [c for c in ("id", "tip_id", "sample_id", "chip_id", "flake_id", "cut_id",
                        "tilt_deg", "status") if c in have]
    for r in db.execute(f"select {', '.join(cols)} from trials order by id"):
        print("  ", dict(r))


show("now")
if "--show" in sys.argv:
    sys.exit(0)
backup = live.with_name("transfer_map_before_sample.sqlite")
shutil.copy2(live, backup)
print("backup:", backup)
with db:
    have = {c[1] for c in db.execute("pragma table_info(trials)")}
    if "sample_id" not in have:
        db.execute("ALTER TABLE trials ADD COLUMN sample_id TEXT")
        print("   added the sample_id column (the station's own upgrade will find it there)")
    for trial_id, fields in VALUES.items():
        if db.execute("select 1 from trials where id=?", (trial_id,)).fetchone() is None:
            print(f"   no trial {trial_id}: skipped")
            continue
        for column, value in fields.items():
            db.execute(f"update trials set {column}=? where id=?", (value, trial_id))
    db.execute("update trials set sample_id=? where id not in (%s) and "
               "trim(coalesce(sample_id, ''))=''" % ",".join(map(str, VALUES)),
               (DEFAULT_SAMPLE,))
show("after")
