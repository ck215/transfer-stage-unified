"""The tip shade read back from recorded footage (owner 2026-10-06: record
the whole tip region as before, extract afterwards with a mask). The same
tracker as live, fed frame by frame from the video or its JPEG folder."""
import csv

import numpy as np
import pytest

from devices import video
from model import shade_offline as so
from model import tip_shade as ts

HZ = 15


def series(n=330, base=150.0, peak=1.5):
    t = np.arange(n) / HZ
    out = np.full(n, base)
    up = (t >= 4) & (t < 7)
    out[up] = base + (peak - 1) * base * (1 - np.cos(np.pi * (t[up] - 4) / 3)) / 2
    dn = t >= 7
    f = np.clip((t[dn] - 7) / 12, 0, 1)
    out[dn] = base + (peak - 1) * base * (1 + np.cos(np.pi * f)) / 2
    return out


def tip_image(green, size=(96, 160)):
    """A tip-like picture: orange everywhere, the right half's green set."""
    rgb = np.zeros((*size, 3), np.uint8)
    rgb[..., 0], rgb[..., 1], rgb[..., 2] = 215, 150, 32
    rgb[:, size[1] // 2:, 1] = int(round(green))
    return rgb


def record(folder, values, jpeg, z0=1000.0, with_shade=False):
    """Write a labelled trial video and its index, as the map does."""
    folder.mkdir(parents=True, exist_ok=True)
    rec = video.TrialRecorder().open(folder / "trial.mp4", HZ, (160, 96))
    rows = []
    for i, v in enumerate(values):
        t = i / HZ
        rec.write(tip_image(v), [f"t={t:.2f} s  red 0.6 %  z {z0:.0f}",
                                 "trial 1  tip T  chip C  flake F  cut 1"])
        row = [i + 1, round(t, 4), 0.6, z0 - max(0.0, (t - 1.0) * 10), 0]
        if with_shade:
            row.append(round(v, 2))
        rows.append(row)
    path = rec.close()
    index = folder / "video_index.csv"
    with open(index, "w", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["frame", "t_s", "red", "z", "marked"] + (["shade"] if with_shade else []))
        w.writerows(rows)
    return str(path), str(index)


@pytest.fixture(params=["jpeg", "mp4"])
def encoder(request, monkeypatch):
    if request.param == "jpeg":
        monkeypatch.setattr(video, "_encoder", lambda: None)
    else:
        pytest.importorskip("imageio_ffmpeg")
    return request.param


def live(values, mark_i):
    tracker = ts.ShadeTracker()
    for i, v in enumerate(values):
        t = i / HZ
        tracker.update(t, ts.right_half_median_green(tip_image(v)), 1000.0 - max(0.0, (t - 1.0) * 10))
        if i >= mark_i:
            tracker.mark()
            break
    return tracker.mark_snapshot, tracker


def test_the_band_constant_matches_the_recorders():
    assert ts.BAND_COLOUR == video.BAND_COLOUR


def test_the_image_top_skips_the_label_band(tmp_path, monkeypatch):
    monkeypatch.setattr(video, "_encoder", lambda: None)
    folder = tmp_path / "t"
    path, _ = record(folder, series(30), jpeg=True)
    frame = next(video.read_frames(path))
    rec_band = video.TrialRecorder()
    rec_band.open(tmp_path / "x" / "t.mp4", HZ, (160, 96))
    expected = rec_band.band_height
    rec_band.close()
    assert abs(ts.image_top(frame) - expected) <= 2      # JPEG blur at the border


def test_read_frames_yields_every_frame_in_order(tmp_path, encoder):
    values = series(40)
    path, _ = record(tmp_path / "t", values, jpeg=encoder == "jpeg")
    frames = list(video.read_frames(path))
    assert len(frames) == 40
    top = ts.image_top(frames[0])
    shades = [ts.right_half_median_green(f[top:]) for f in frames]
    assert shades == pytest.approx(list(np.round(values)), abs=3.0)


def test_the_footage_estimate_matches_the_live_tracker(tmp_path, encoder):
    values = series()
    mark_i = 11 * HZ
    path, index = record(tmp_path / "t", values, jpeg=encoder == "jpeg")
    got = so.estimate_from_footage(path, index, mark_i / HZ)
    snap, tracker = live(values, mark_i)
    assert got["force_class"] == snap["status"]
    assert got["force_position"] == pytest.approx(snap["position"], abs=0.04)
    assert got["shade_baseline"] == pytest.approx(150.0, abs=2.0)
    assert got["contact_lowered"] == pytest.approx(tracker.contact_lowered, abs=2.0)


def test_the_estimate_uses_the_shade_in_the_index_when_there_is_one(tmp_path, monkeypatch):
    monkeypatch.setattr(video, "_encoder", lambda: None)
    values = series()
    path, index = record(tmp_path / "t", values, jpeg=True, with_shade=True)
    import shutil
    shutil.rmtree(path)                       # the footage is gone; the index suffices
    got = so.estimate_from_footage(path, index, 11.0)
    assert got["force_class"] in ("Low", "Medium")


def test_no_mark_means_no_force(tmp_path, monkeypatch):
    monkeypatch.setattr(video, "_encoder", lambda: None)
    path, index = record(tmp_path / "t", series(), jpeg=True)
    got = so.estimate_from_footage(path, index, None)
    assert got["force_position"] is None and got["force_class"] is None
    assert got["shade_baseline"] == pytest.approx(150.0, abs=2.0)


def test_a_mark_before_contact_has_no_force(tmp_path, monkeypatch):
    monkeypatch.setattr(video, "_encoder", lambda: None)
    path, index = record(tmp_path / "t", series(), jpeg=True)
    got = so.estimate_from_footage(path, index, 2.0)
    assert got["force_position"] is None and got["force_class"] is None
