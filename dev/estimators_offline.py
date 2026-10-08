"""Run the estimator bank over a recorded video (dev tool).

    python dev/estimators_offline.py <trial.mp4> [--crop NAMES] [--out PREFIX]
                                     [--region left,top,w,h] [--custom l,t,w,h]
                                     [--band auto|N] [--keys K1,K2,...]

Owner ruling 2026-10-07: the force model is chosen on the footage, so every
estimator of `model.estimators` is run over every frame of a recording, and
the curves are written where the methods can be compared:

    <PREFIX>.csv   t_s, then one RAW column per `<crop>.<estimator>` key
    <PREFIX>.png   the NORMALISED curves (each against its own first-second
                   baseline, peak +-1), the bank's `series()` as drawn live

`--crop` picks crops (comma list of full, right_half, left_half, centre,
custom; default the four fixed ones); `--keys` picks exact keys instead
(default for the PNG: the live plot's four). Old region recordings (the
frame IS the region) need nothing else; a recorded trial carries the label
band the recorder adds above each frame, which `--band auto` (the default)
finds and crops; `--band 0` keeps the frame whole, `--band N` crops N rows.
A full-display recording takes `--region left,top,w,h` to cut the region
first (applied before the band, so give 0 band rows there).

The time of a frame is its index over the video's frame rate. The bank runs
with the same code as the live app; nothing here re-implements an estimator.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "src"))

import numpy                                                  # noqa: E402

from model import estimators                                   # noqa: E402

#: The label band's colour (`devices.video`), and how far a pixel may stray.
BAND_COLOUR = (16, 16, 16)
BAND_TOLERANCE = 12
#: Keys the PNG draws when `--keys` is not given: the live plot's defaults.
PLOT_KEYS = ("full.red_share", "right_half.shade_g_median", "full.g_mean",
             "centre.shade_g_median")
#: More curves than this and the PNG keeps the live plot's four.
MAX_CURVES = 12
#: A window longer than any video: the bank's own 60 s default would cut it.
WHOLE = 1e9
FIXED_CROPS = ("full", "right_half", "left_half", "centre")


def band_rows(rgb):
    """Label-band rows above the picture: the first run of six rows that are
    mostly not the band's colour (0 for a frame with no band)."""
    off = (numpy.abs(rgb.astype(numpy.int16) - BAND_COLOUR).max(axis=2)
           > BAND_TOLERANCE).mean(axis=1)
    for y in range(len(off) - 5):
        if (off[y:y + 6] > 0.5).all():
            return y
    return 0


def parse_rect(text, name):
    try:
        values = tuple(int(v) for v in text.split(","))
    except ValueError:
        values = ()
    if len(values) != 4:
        raise SystemExit(f"{name} wants four integers: left,top,w,h")
    return values


def frames(path):
    """`(index, fps, frame count, rgb frame)` for every frame of the video."""
    import cv2
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise SystemExit(f"cannot open {path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 15.0
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    index = 0
    while True:
        ok, bgr = capture.read()
        if not ok:
            break
        yield index, fps, count, bgr[:, :, ::-1]
        index += 1
    capture.release()


def selected_keys(args):
    if args.keys:
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        for key in keys:
            estimators.key_parts(key)
        return keys
    crops = [c.strip() for c in args.crop.split(",") if c.strip()]
    for crop in crops:
        if crop not in estimators.CROPS:
            raise SystemExit(f"{crop!r} is not a crop; choose from "
                             f"{', '.join(estimators.CROP_NAMES)}")
    if "custom" in crops and not args.custom:
        raise SystemExit("--crop custom needs --custom left,top,w,h")
    return [f"{crop}.{name}" for crop in crops
            for name in estimators.ESTIMATOR_NAMES]


def run(args):
    keys = selected_keys(args)
    custom = parse_rect(args.custom, "--custom") if args.custom else None
    region = parse_rect(args.region, "--region") if args.region else None
    prefix = args.out or os.path.splitext(args.video)[0] + "_estimators"
    os.makedirs(os.path.dirname(os.path.abspath(prefix)), exist_ok=True)

    bank = None
    band = None
    for index, fps, count, rgb in frames(args.video):
        if region is not None:
            left, top, width, height = region
            rgb = rgb[top:top + height, left:left + width]
        if band is None:
            band = band_rows(rgb) if args.band == "auto" else int(args.band)
        rgb = numpy.ascontiguousarray(rgb[band:])
        if bank is None:
            # A ring that holds the whole video: nothing is dropped.
            seconds = max(count + 2, 2) / estimators.ASSUMED_MAX_HZ
            bank = estimators.EstimatorBank(seconds=seconds, custom=custom)
        bank.update(index / fps, rgb)
    if bank is None or not len(bank):
        raise SystemExit("no frames")

    raw = bank.series(keys, seconds=WHOLE, normalise=None)
    with open(prefix + ".csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["t_s"] + keys)
        for i, t in enumerate(raw["t"]):
            writer.writerow([t] + ["" if raw[k][i] is None else raw[k][i]
                                   for k in keys])
    if args.keys or len(keys) <= MAX_CURVES:
        plot_keys = keys
    else:
        plot_keys = [k for k in PLOT_KEYS if k in keys] or keys[:4]
    curves = bank.series(plot_keys, seconds=WHOLE, normalise="baseline",
                         max_points=1200)
    png = estimators.render_png(
        curves, title=f"{os.path.basename(args.video)}: estimators vs baseline",
        size=(9.0, 4.4))
    with open(prefix + ".png", "wb") as handle:
        handle.write(png)
    return prefix + ".csv", prefix + ".png", len(bank), band


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("video")
    parser.add_argument("--crop", default=",".join(FIXED_CROPS),
                        help="crops to write (default: the four fixed ones)")
    parser.add_argument("--keys", default=None,
                        help="exact <crop>.<estimator> keys, comma separated")
    parser.add_argument("--out", default=None,
                        help="output prefix (default beside the video)")
    parser.add_argument("--region", default=None,
                        help="left,top,w,h of the region in a full-display video")
    parser.add_argument("--custom", default=None,
                        help="left,top,w,h of the custom crop, region-relative")
    parser.add_argument("--band", default="auto",
                        help="label-band rows to crop: auto, or a number")
    args = parser.parse_args(argv)
    csv_path, png_path, count, band = run(args)
    print(f"{count} frames, band {band} rows -> {csv_path}, {png_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
