"""`devices.screen_recorder`: the full-display trial recorder (REC-1; audit
capture.md section 5).

**No test here grabs the real screen** (someone works at this Mac): every
recording is fed by an injected `frame_source`, and the default source is
exercised through a `Screen` over a fake capture factory. The encoder tests
run the wheel's real ffmpeg and read the file back with it, so they skip on a
machine without `imageio-ffmpeg`; the wedged/dying encoder tests swap in a
shell script for the binary.
"""
import csv
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

import numpy
import pytest
from PIL import Image

from devices import screen_recorder
from devices.screen import Screen
from devices.screen_recorder import RecorderResult, ScreenRecorder, _ByteQueue


@pytest.fixture
def encoder():
    """The real ffmpeg: these tests encode and decode."""
    return pytest.importorskip("imageio_ffmpeg")


class FakeSource:
    """A `frame_source`: a fresh array per call, stamped on the monotonic
    clock. `sizes` cycles (w, h) per call; `channels` 4 is mss's BGRA, 3 is
    RGB; `noise` makes every frame random (and remembered, for an exact
    comparison). After `hang_after` calls every call blocks until `release`."""

    def __init__(self, width=64, height=48, channels=3, sizes=None,
                 noise=False, hang_after=None, colour=None):
        self.sizes = sizes or [(width, height)]
        self.channels, self.noise, self.colour = channels, noise, colour
        self.hang_after = hang_after
        self.release = threading.Event()
        self.calls = 0
        self.threads = set()
        self.returned = []                 # (frame, t) in call order
        self._rng = numpy.random.default_rng(7)

    def __call__(self):
        self.calls += 1
        self.threads.add(threading.current_thread().name)
        if self.hang_after is not None and self.calls > self.hang_after:
            self.release.wait(30)
        width, height = self.sizes[(self.calls - 1) % len(self.sizes)]
        if self.noise:
            frame = self._rng.integers(0, 256, (height, width, self.channels),
                                       dtype=numpy.uint8)
        else:
            frame = numpy.zeros((height, width, self.channels), numpy.uint8)
            frame[...] = self.colour if self.colour is not None else \
                (self.calls * 9) % 256
        t = time.monotonic()
        self.returned.append((frame, t))
        return frame, t


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _index(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _read_back(path):
    """(frames as HxWx3 RGB arrays, metadata) through the wheel's reader."""
    import imageio_ffmpeg
    reader = imageio_ffmpeg.read_frames(str(path))
    meta = reader.__next__()
    width, height = meta["size"]
    frames = [numpy.frombuffer(chunk, dtype=numpy.uint8).reshape(height, width, 3)
              for chunk in reader]
    return frames, meta


def _script(tmp_path, name, body):
    """A stand-in for the ffmpeg binary."""
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return str(path)


def _recorder_threads():
    return [t for t in threading.enumerate() if t.name.startswith("screen-recorder")]


# -- the file ----------------------------------------------------------------------

def test_records_h264_yuv420p_with_one_sidecar_row_per_written_frame(tmp_path, encoder):
    source = FakeSource()
    recorder = ScreenRecorder(tmp_path / "trial", 20, 1, frame_source=source)
    recorder.start()
    assert _wait_for(lambda: recorder.stats["frames"] >= 10)
    result = recorder.stop()
    assert isinstance(result, RecorderResult)
    assert result.video_path == tmp_path / "trial" / "screen.mp4"
    assert result.index_path == tmp_path / "trial" / "frames.csv"
    rows = _index(result.index_path)
    assert len(rows) == result.frames >= 10
    assert [int(r["frame"]) for r in rows] == list(range(result.frames))
    frames, meta = _read_back(result.video_path)
    assert len(frames) == result.frames
    assert meta["codec"].startswith("h264"), meta
    assert meta["pix_fmt"].startswith("yuv420p"), meta
    assert tuple(meta["size"]) == (64, 48)
    assert result.dropped == 0
    assert not _recorder_threads()


def test_the_sidecar_carries_the_sources_monotonic_times_and_wall_times(tmp_path, encoder):
    source = FakeSource()
    recorder = ScreenRecorder(tmp_path, 25, 1, frame_source=source)
    wall_before = time.time()
    recorder.start()
    assert _wait_for(lambda: recorder.stats["frames"] >= 8)
    recorder.mark("Arm")
    result = recorder.stop()
    wall_after = time.time()
    with open(result.index_path, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header == ["frame", "t_monotonic", "t_wall", "marked"]
    rows = _index(result.index_path)
    returned = [round(t, 6) for _frame, t in source.returned]
    times = [float(r["t_monotonic"]) for r in rows]
    assert times == sorted(times)
    assert all(t in returned for t in times), "t_monotonic is the source's own stamp"
    walls = [float(r["t_wall"]) for r in rows]
    assert all(wall_before - 0.05 <= w <= wall_after + 0.05 for w in walls)
    # The two columns are one instant: their difference is constant.
    offsets = [w - t for w, t in zip(walls, times)]
    assert max(offsets) - min(offsets) < 0.01


def test_mss_bgra_frames_record_losslessly_pixel_for_pixel(tmp_path, encoder):
    """quality="lossless": libx264rgb at qp 0 (4:4:4 RGB). A BGRA frame (as
    mss grabs) comes back exactly, in RGB order."""
    source = FakeSource(width=40, height=30, channels=4, noise=True)
    recorder = ScreenRecorder(tmp_path, 20, 1, quality="lossless",
                              frame_source=source)
    recorder.start()
    assert _wait_for(lambda: recorder.stats["frames"] >= 5)
    result = recorder.stop()
    assert result.dropped == 0
    frames, meta = _read_back(result.video_path)
    assert meta["codec"].startswith("h264"), meta
    assert len(frames) == result.frames
    for decoded, (sent, _t) in zip(frames, source.returned):
        assert numpy.array_equal(decoded, sent[:, :, 2::-1])


def test_an_odd_size_is_padded_to_even_at_the_right_and_bottom(tmp_path, encoder):
    """yuv420p needs even dimensions. The pad goes right and bottom, so a
    pixel's coordinates in the video are its coordinates on the screen."""
    source = FakeSource(width=33, height=21, noise=True)
    recorder = ScreenRecorder(tmp_path, 20, 1, quality="lossless",
                              frame_source=source)
    recorder.start()
    assert _wait_for(lambda: recorder.stats["frames"] >= 3)
    result = recorder.stop()
    frames, meta = _read_back(result.video_path)
    assert tuple(meta["size"]) == (34, 22)
    sent = source.returned[0][0]
    assert numpy.array_equal(frames[0][:21, :33], sent)
    assert not frames[0][21:, :].any() and not frames[0][:, 33:].any()


def test_a_frame_of_another_size_is_dropped_and_counted_never_resized(tmp_path, encoder):
    source = FakeSource(sizes=[(64, 48), (64, 48), (32, 32)])
    recorder = ScreenRecorder(tmp_path, 30, 1, frame_source=source)
    recorder.start()
    assert _wait_for(lambda: recorder.stats["frames"] >= 6 and source.calls >= 9)
    result = recorder.stop()
    sizes = [frame.shape[:2] for frame, _t in source.returned]
    off_size = sum(1 for s in sizes if s != (48, 64))
    assert off_size >= 3
    assert recorder.stats["dropped_size"] == off_size
    assert result.dropped == off_size
    assert result.frames + result.dropped == source.calls
    frames, meta = _read_back(result.video_path)
    assert tuple(meta["size"]) == (64, 48)
    assert len(frames) == result.frames == len(_index(result.index_path))


def test_a_sigkilled_encoder_leaves_a_playable_file(tmp_path, encoder):
    """Fragmented MP4: a killed writer (a timeout, a crash, power) still
    leaves every completed fragment playable. A plain MP4 writes its index
    only at exit and is unreadable after the same kill (audit CAP-6)."""
    source = FakeSource(noise=False)
    recorder = ScreenRecorder(tmp_path, 20, 1, frame_source=source)
    recorder.start()
    assert _wait_for(lambda: recorder.stats["frames"] >= 60)
    os.kill(recorder._proc.pid, signal.SIGKILL)
    assert _wait_for(lambda: recorder._proc.poll() is not None)
    result = recorder.stop()
    assert result.video_path is not None and result.video_path.stat().st_size > 0
    frames, meta = _read_back(result.video_path)
    assert meta["codec"].startswith("h264"), meta
    assert len(frames) >= 20, "at least the first one-second fragment plays"
    assert len(frames) <= result.frames
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,nb_read_frames", "-of", "csv=p=0",
             str(result.video_path)], capture_output=True, text=True, timeout=30)
        assert probe.returncode == 0, probe.stderr
        codec, count = probe.stdout.strip().split(",")[:2]
        assert codec == "h264" and int(count) == len(frames)
    assert not _recorder_threads()


# -- never block, always stop ------------------------------------------------------------

def test_a_wedged_encoder_never_blocks_the_capture_and_stop_is_bounded(tmp_path, monkeypatch):
    """An ffmpeg that reads nothing: the writer blocks on the pipe, the capture
    keeps its rate (frames past the byte budget are dropped and counted), and
    stop() terminates the encoder after STOP_TIMEOUT instead of waiting."""
    monkeypatch.setattr(screen_recorder, "_ffmpeg_exe",
                        lambda: _script(tmp_path, "ffmpeg", "exec sleep 30"))
    source = FakeSource(width=320, height=240, channels=4)   # 300 KB > a pipe
    recorder = ScreenRecorder(tmp_path / "out", 50, 1, frame_source=source)
    frame_bytes = 320 * 240 * 4
    recorder.QUEUE_BYTES = 3 * frame_bytes
    recorder.STOP_TIMEOUT = 0.3
    recorder.KILL_GRACE = 1.0
    recorder.start()
    assert _wait_for(lambda: source.calls >= 25), "the capture kept grabbing"
    stats = recorder.stats
    assert stats["dropped_full"] > 0
    assert stats["peak_queued_bytes"] <= 3 * frame_bytes
    started = time.monotonic()
    result = recorder.stop()
    elapsed = time.monotonic() - started
    assert elapsed < 0.3 + 2 * 1.0 + 1.0, elapsed
    assert recorder.stats["encoder_timeouts"] == 1
    assert recorder._proc.poll() is not None, "the wedged encoder was ended"
    assert result.frames == 0
    assert result.dropped == source.calls
    assert not _recorder_threads()


def test_an_encoder_that_dies_is_counted_never_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(screen_recorder, "_ffmpeg_exe",
                        lambda: _script(tmp_path, "ffmpeg", "exit 1"))
    source = FakeSource(width=320, height=240, channels=4)
    recorder = ScreenRecorder(tmp_path / "out", 50, 1, frame_source=source)
    recorder.start()
    assert _wait_for(lambda: recorder.stats["dropped_encoder"] >= 3)
    assert recorder.stats["encoder_error"]
    started = time.monotonic()
    result = recorder.stop()
    assert time.monotonic() - started < 3.0
    assert result.frames + result.dropped == source.calls
    assert result.dropped >= 3
    assert not _recorder_threads()


def test_stop_is_bounded_when_the_frame_source_hangs(tmp_path, encoder):
    source = FakeSource(hang_after=3)
    recorder = ScreenRecorder(tmp_path, 20, 1, frame_source=source)
    recorder.CAPTURE_JOIN = 0.3
    recorder.start()
    try:
        assert _wait_for(lambda: source.calls >= 4)
        started = time.monotonic()
        result = recorder.stop()
        assert time.monotonic() - started < 0.3 + recorder.STOP_TIMEOUT
        assert recorder.stats["capture_timeouts"] == 1
        assert result.frames == 3
    finally:
        source.release.set()
        assert _wait_for(lambda: not _recorder_threads(), 5.0)


def test_the_byte_queue_refuses_rather_than_blocks():
    queue = _ByteQueue(100)
    assert queue.put("a", 60)
    assert not queue.put("b", 60), "over budget: refused at once"
    assert queue.put("c", 40)
    assert queue.queued_bytes == 100 and queue.peak == 100
    assert queue.get() == "a"
    assert queue.put("d", 50)
    queue.close()
    assert not queue.put("e", 1), "closed"
    assert [queue.get(), queue.get(), queue.get()] == ["c", "d", None]
    big = _ByteQueue(10)
    assert big.put("frame", 50), "one item larger than the budget fits an empty queue"
    assert not big.put("next", 1)


# -- marks and stills ----------------------------------------------------------------

def test_a_mark_flags_the_next_written_row_and_grabs_nothing(tmp_path, encoder):
    source = FakeSource()
    recorder = ScreenRecorder(tmp_path, 20, 1, frame_source=source)
    recorder.mark("before start")                  # ignored, never raises
    recorder.start()
    assert _wait_for(lambda: recorder.stats["frames"] >= 3)
    t_before = time.monotonic()
    recorder.mark("Arm")
    t_after = time.monotonic()
    assert _wait_for(lambda: recorder.stats["frames"] >= 8)
    result = recorder.stop()
    recorder.mark("after stop")                    # ignored, never raises
    rows = _index(result.index_path)
    marked = [r for r in rows if r["marked"]]
    assert [r["marked"] for r in marked] == ["Arm"]
    # The flag lands on the first frame grabbed at or after the mark.
    assert float(marked[0]["t_monotonic"]) >= round(t_before, 6) - 1e-6
    before = rows[:rows.index(marked[0])]
    assert before and all(float(r["t_monotonic"]) <= t_after + 1e-6 for r in before)
    assert source.threads == {"screen-recorder-capture"}, \
        "only the capture thread grabs: mark() grabbed nothing"
    assert result.frames + result.dropped == source.calls


def test_capture_still_is_a_full_resolution_png_while_not_recording(tmp_path):
    source = FakeSource(width=1920, height=1080, channels=4, colour=(10, 20, 200, 255))
    recorder = ScreenRecorder(tmp_path / "trial", 15, 1, frame_source=source)
    path = recorder.capture_still(tmp_path / "trial" / "before.png")
    assert path == tmp_path / "trial" / "before.png" and isinstance(path, Path)
    image = Image.open(path)
    assert image.format == "PNG" and image.size == (1920, 1080)
    assert image.convert("RGB").getpixel((5, 5)) == (200, 20, 10)   # BGRA read
    assert not (tmp_path / "trial" / "frames.csv").exists(), "no recording started"


def test_capture_still_works_while_recording_and_adds_no_row(tmp_path, encoder):
    source = FakeSource()
    recorder = ScreenRecorder(tmp_path, 20, 1, frame_source=source)
    recorder.start()
    assert _wait_for(lambda: recorder.stats["frames"] >= 2)
    still = recorder.capture_still(tmp_path / "mid.png")
    result = recorder.stop()
    assert Image.open(still).size == (64, 48)
    assert result.frames + result.dropped == source.calls - 1   # the still's grab


# -- the default source: Screen over the capture library, never the real one ------------

class FakeShot:
    """mss-shaped: `raw` BGRA bytes and a size."""

    def __init__(self, width, height, bgra):
        self.raw = bytearray(bytes(bgra) * (width * height))
        self.width, self.height = width, height
        self.size = (width, height)

    @property
    def bgra(self):
        return bytes(self.raw)


class FakeMss:
    """The capture library's instance: `monitors` and `grab`. Records every
    region grabbed and whether it was closed."""

    instances = []
    MONITORS = [{"left": 0, "top": 0, "width": 60, "height": 30},
                {"left": 0, "top": 0, "width": 40, "height": 30},
                {"left": 40, "top": 0, "width": 20, "height": 10}]

    def __init__(self):
        self.monitors = [dict(m) for m in self.MONITORS]
        self.regions, self.closed = [], False
        self.thread = threading.current_thread().name
        FakeMss.instances.append(self)

    def grab(self, region):
        self.regions.append(dict(region))
        return FakeShot(region["width"], region["height"], (10, 20, 200, 255))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_display(monkeypatch):
    FakeMss.instances = []
    monkeypatch.setattr(screen_recorder, "_make_screen",
                        lambda: Screen(factory=FakeMss))
    return FakeMss


def test_the_default_source_records_the_chosen_monitor_at_its_own_size(
        tmp_path, encoder, fake_display):
    recorder = ScreenRecorder(tmp_path, 20, 2)
    recorder.start()
    assert _wait_for(lambda: recorder.stats["frames"] >= 3)
    result = recorder.stop()
    frames, meta = _read_back(result.video_path)
    assert tuple(meta["size"]) == (20, 10)
    grabbed = [r for m in fake_display.instances for r in m.regions]
    assert grabbed and all(r == FakeMss.MONITORS[2] for r in grabbed)
    capture = [m for m in fake_display.instances if m.thread == "screen-recorder-capture"]
    assert len(capture) == 1, "one kept handle on the capture thread"
    assert all(m.closed for m in fake_display.instances)


def test_the_default_still_is_the_chosen_monitor(tmp_path, fake_display):
    recorder = ScreenRecorder(tmp_path, 15, 1)
    path = recorder.capture_still(tmp_path / "full.png")
    image = Image.open(path).convert("RGB")
    assert image.size == (40, 30)
    assert image.getpixel((0, 0)) == (200, 20, 10)
    assert all(m.closed for m in fake_display.instances)


def test_a_monitor_that_does_not_exist_is_refused_at_start(tmp_path, encoder, fake_display):
    recorder = ScreenRecorder(tmp_path, 15, 7)
    with pytest.raises(ValueError):
        recorder.start()
    assert not _recorder_threads()


# -- the contract's edges ----------------------------------------------------------------

def test_an_unknown_quality_is_refused():
    with pytest.raises(ValueError):
        ScreenRecorder("/nonexistent", 15, 1, quality="best")
    with pytest.raises(ValueError):
        ScreenRecorder("/nonexistent", 0, 1)


def test_start_without_an_encoder_raises_and_starts_nothing(tmp_path, monkeypatch):
    def missing():
        raise RuntimeError("imageio-ffmpeg is not installed")
    monkeypatch.setattr(screen_recorder, "_ffmpeg_exe", missing)
    recorder = ScreenRecorder(tmp_path / "out", 15, 1, frame_source=FakeSource())
    with pytest.raises(RuntimeError):
        recorder.start()
    assert not _recorder_threads()
    assert recorder.stop() == RecorderResult(None, None, 0, 0)


def test_a_recorder_records_once_and_stop_is_idempotent(tmp_path, encoder):
    recorder = ScreenRecorder(tmp_path, 20, 1, frame_source=FakeSource())
    recorder.start()
    with pytest.raises(RuntimeError):
        recorder.start()
    assert _wait_for(lambda: recorder.stats["frames"] >= 2)
    first = recorder.stop()
    assert recorder.stop() == first
    with pytest.raises(RuntimeError):
        recorder.start()
