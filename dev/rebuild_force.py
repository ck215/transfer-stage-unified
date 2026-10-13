"""Rebuild the tip-shade force of stored Transfer Map trials (dev tool).

    python dev/rebuild_force.py <transfer_map.sqlite> [--trial N ...] [--apply]

Owner 2026-10-12: the force class is the probe's Z travel past the shade's
peak (`model/tip_shade.py`, thresholds 10 / 17 / 21 steps, "Break risk" from
21). This recomputes `force_class`, `force_past_peak_steps`, `shade_peak_z`
and the other tip-shade columns of stored trials from what each one saved
under `<store folder>/<store name>/<trial id>/`: its region video's index
(shade and Z columns), its full-display video (the capture region cropped
out) or the shade it stored per frame (`telemetry.csv`'s
`transfer_map.shade`), with the probe's Z from `telemetry.csv`'s
`stepper_probe.z` (else the profile's Z). It is `TransferMap.rebuild_force`,
run on a store from a script.

A DRY RUN by default: it prints each trial's stored class and the rebuilt
one and writes nothing (it opens no store for writing, so even a v8 file is
not upgraded). `--apply` first copies the store to
`<store>.pre-rebuild-force-<stamp>` (SQLite's backup, safe while the station
has the file open), then writes the columns (the store gains the v9 columns
by presence on that write). With no `--trial`, every valid recorded trial.
"""
import argparse
import datetime
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "src"))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("db", help="the Transfer Map store (transfer_map.sqlite)")
    p.add_argument("--trial", type=int, action="append", default=[],
                   help="a trial id (repeatable); default every valid recorded trial")
    p.add_argument("--apply", action="store_true",
                   help="write the rebuilt columns (after a backup copy); "
                        "without it nothing is written")
    return p.parse_args(argv)


def backup(db):
    """A consistent copy of `db` beside it; -> its path."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = db.with_name(f"{db.name}.pre-rebuild-force-{stamp}")
    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(str(target))
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    return target


def describe(fields):
    """`Break risk, 23 steps past peak (peak z -17057), position 0.57`."""
    if fields is None:
        return "nothing to read"
    words = [str(fields.get("force_class") or "no force")]
    past = fields.get("force_past_peak_steps")
    if past is not None:
        words.append(f"{past:g} steps past peak (peak z {fields.get('shade_peak_z'):g})")
    position = fields.get("force_position")
    if position is not None:
        words.append(f"position {position:.2f}")
    return ", ".join(words)


def main(argv=None):
    args = parse_args(argv)
    db = Path(args.db).expanduser().resolve()
    if not db.is_file():
        print(f"No store at {db}.", file=sys.stderr)
        return 2
    from model.transfer_map import TransferMap
    from result import Refused
    model = TransferMap(db_path=db)        # adopts the file; creates nothing
    print(("APPLY" if args.apply else "DRY RUN (nothing is written; --apply "
           "writes)") + f": {db}")
    if args.apply:
        print(f"Backup: {backup(db)}")
    before = {row["id"]: row for row in model._store.trials()}
    rebuilt = {}
    try:
        for trial in (args.trial or [0]):
            rebuilt.update(model.rebuild_force(trial, dry_run=not args.apply))
    except Refused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for trial in sorted(set(args.trial) | set(rebuilt)):
        old = before.get(trial) or {}
        new = rebuilt.get(trial)
        print(f"trial {trial}: {old.get('force_class') or 'no force'} -> "
              f"{(new or {}).get('force_class') or 'no force'}   [{describe(new)}]")
    print(f"{len(rebuilt)} trial(s) {'written' if args.apply else 'would be written'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
