"""The tip shade force of a trial already recorded, read back from its footage.

Owner, 2026-10-06: recording keeps the whole tip region; the mask (the right
half, median green; `model/tip_shade.py`) is applied afterwards. This feeds
the same `ShadeTracker` a live trial uses, frame by frame, from the video (or
its JPEG folder), or from the `shade` column of the video index when the
trial was recorded with one (the live trackers values, so nothing is decoded).
"""
import csv

from devices import video
from model import tip_shade


def _index(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def estimate_from_footage(video_path, index_path, mark_t):
    """The v8 tip-shade columns for one trial. `mark_t` is the first Mark's
    time in seconds since Arm (the video index's clock), or None for no Mark
    (then only the baseline and contact are found). Frames after the Mark are
    not read."""
    rows = _index(index_path)
    tracker = tip_shade.ShadeTracker()
    stored = [_number(r.get("shade")) for r in rows]
    if rows and all(v is not None for v in stored):
        shades = iter(stored)
        frames = None
    else:
        shades = None
        frames = video.read_frames(video_path)
    top = None
    try:
        for i, row in enumerate(rows):
            t, z = _number(row["t_s"]), _number(row.get("z"))
            if shades is not None:
                shade = next(shades)
            else:
                frame = next(frames, None)
                if frame is None:
                    break
                if top is None:
                    top = tip_shade.image_top(frame)
                shade = tip_shade.right_half_median_green(frame[top:])
            tracker.update(t, shade, z)
            if mark_t is not None and t >= mark_t:
                tracker.mark()
                break
    finally:
        if frames is not None and hasattr(frames, "close"):
            frames.close()
    return tip_shade.columns(tracker)
