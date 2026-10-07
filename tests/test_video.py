"""`devices.video`: the per-trial recorder (owner, 2026-09-28: "rather than
like 7 pictures, a video where timestamps label the footage for analysis
afterward").

H.264 MP4 through `imageio-ffmpeg` when the wheel is installed; a folder of
JPEG frames, labelled the same way, when it is not. The encoder tests read
the file back with the wheel's own ffmpeg, so they skip on a machine
without it; the fallback tests hide the wheel and run everywhere.
"""
import sys
from pathlib import Path

import numpy
import pytest
from PIL import Image

from devices import video
from devices.video import TrialRecorder


def _frame(width, height, value=255):
    return numpy.full((height, width, 3), value, dtype=numpy.uint8)


@pytest.fixture
def no_encoder(monkeypatch):
    """`import imageio_ffmpeg` fails, as on a station without the wheel."""
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)


def _ffmpeg():
    return pytest.importorskip("imageio_ffmpeg")


def _read_back(path):
    """(frames as HxWx3 arrays, (width, height)) through the wheel's reader."""
    ffmpeg = _ffmpeg()
    reader = ffmpeg.read_frames(str(path))
    meta = reader.__next__()
    width, height = meta["size"]
    frames = [numpy.frombuffer(chunk, dtype=numpy.uint8).reshape(height, width, 3)
              for chunk in reader]
    return frames, (width, height)


# -- the encoder -----------------------------------------------------------------

def test_probe_reports_mp4_when_the_wheel_is_installed():
    ffmpeg = _ffmpeg()
    found = TrialRecorder.probe()
    assert found["encoder"] == "mp4"
    assert ffmpeg.__version__ in found["detail"]


def test_probe_reports_jpeg_frames_without_the_wheel(no_encoder):
    found = TrialRecorder.probe()
    assert found["encoder"] == "jpeg"
    assert "imageio-ffmpeg" in found["detail"]


def test_the_recorder_writes_an_h264_mp4_with_every_frame(tmp_path):
    _ffmpeg()
    recorder = TrialRecorder()
    recorder.open(tmp_path / "trial.mp4", 15, (64, 48))
    assert recorder.kind == "mp4"
    for i in range(12):
        recorder.write(_frame(64, 48, value=20 * i), [f"t={i / 15:.2f} s"])
    path = recorder.close()
    assert Path(path) == tmp_path / "trial.mp4" and Path(path).stat().st_size > 0
    assert recorder.frames == 12
    frames, size = _read_back(path)
    assert len(frames) == 12
    assert size == recorder.size
    ffmpeg = _ffmpeg()
    probe = ffmpeg.read_frames(str(path)).__next__()
    assert probe["codec"].startswith("h264"), probe
    assert probe["pix_fmt"].startswith("yuv420p"), probe


def test_odd_sizes_are_padded_to_even(tmp_path):
    """yuv420p needs even dimensions; the recorder pads rather than fail."""
    _ffmpeg()
    recorder = TrialRecorder()
    recorder.open(tmp_path / "trial.mp4", 15, (33, 21))
    width, height = recorder.size
    assert width % 2 == 0 and height % 2 == 0
    assert width >= 33 and height >= 21
    for _ in range(3):
        recorder.write(_frame(33, 21), ["t=0.00 s"])
    frames, size = _read_back(recorder.close())
    assert len(frames) == 3 and size == (width, height)


def test_the_label_is_drawn_on_a_dark_band_at_the_top(tmp_path):
    """A white frame: the band above it is dark, with light text in it, and
    the footage below is untouched (the band is added, not painted over)."""
    recorder = TrialRecorder()
    recorder.open(tmp_path / "trial.mp4", 15, (240, 60))
    band = recorder.band_height
    assert band > 0
    labelled = recorder.write(_frame(240, 60), ["t=12.34 s  red 63.2 %  z -1520  MARK"])
    recorder.close()
    assert labelled.shape[:2] == (recorder.size[1], recorder.size[0])
    top = labelled[:band]
    assert top.mean() < 80, "the band is dark"
    assert top.max() > 180, "the label is drawn in it"
    assert (labelled[band:band + 60, :240] == 255).all(), "the footage is intact"


def test_the_label_band_holds_the_label_on_a_narrow_region(tmp_path):
    """A narrow region wraps the label onto more lines of the band, fixed at
    open, rather than cutting MARK off the end."""
    wide, narrow = TrialRecorder(), TrialRecorder()
    wide.open(tmp_path / "wide.mp4", 15, (900, 40))
    narrow.open(tmp_path / "narrow.mp4", 15, (120, 40))
    assert narrow.label_rows > wide.label_rows == 1
    text = "t=12.34 s  red 63.2 %  z -1520  MARK"
    assert len(narrow.label_layout(text)) <= narrow.label_rows
    for recorder in (wide, narrow):
        recorder.write(_frame(recorder.frame_size[0], 40), [text])
        recorder.close()


def test_a_frame_of_another_size_is_resized(tmp_path):
    _ffmpeg()
    recorder = TrialRecorder()
    recorder.open(tmp_path / "trial.mp4", 15, (40, 30))
    out = recorder.write(_frame(80, 70), ["t=0.00 s"])
    assert out.shape[:2] == (recorder.size[1], recorder.size[0])
    recorder.write(_frame(40, 30), ["t=0.07 s"])
    frames, _ = _read_back(recorder.close())
    assert len(frames) == 2


def test_to_rgb_reads_bgra_screenshots_and_arrays():
    class Shot:                      # mss-shaped: a BGRA buffer and a size
        def __init__(self, bgra, size):
            self.bgra, self.size = bgra, size

    pixel = bytes([10, 20, 30, 255])                     # B, G, R, A
    rgb = video.to_rgb(Shot(pixel * 6, (3, 2)))
    assert rgb.shape == (2, 3, 3) and tuple(rgb[0, 0]) == (30, 20, 10)
    bgra = numpy.zeros((2, 2, 4), dtype=numpy.uint8)
    bgra[..., :3] = (10, 20, 30)
    assert tuple(video.to_rgb(bgra)[1, 1]) == (30, 20, 10)
    plain = numpy.zeros((2, 2, 3), dtype=numpy.uint8)
    plain[...] = (200, 0, 0)                             # already RGB
    assert tuple(video.to_rgb(plain)[0, 0]) == (200, 0, 0)
    assert video.to_rgb(None) is None


# -- the encoder lookup the full-display recorder uses ------------------------------

def test_ffmpeg_exe_is_the_wheels_binary():
    ffmpeg = _ffmpeg()
    assert video.ffmpeg_exe() == ffmpeg.get_ffmpeg_exe()
    assert Path(video.ffmpeg_exe()).exists()


def test_ffmpeg_exe_raises_a_worded_error_without_the_wheel(no_encoder):
    with pytest.raises(RuntimeError, match="imageio-ffmpeg is not installed"):
        video.ffmpeg_exe()


def test_ffmpeg_exe_raises_when_the_wheel_has_no_binary(monkeypatch):
    class Broken:
        @staticmethod
        def get_ffmpeg_exe():
            raise RuntimeError("no ffmpeg binary")

    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", Broken)
    with pytest.raises(RuntimeError, match="no ffmpeg binary"):
        video.ffmpeg_exe()


# -- the fallback ----------------------------------------------------------------

def test_without_the_wheel_the_recorder_writes_labelled_jpeg_frames(tmp_path,
                                                                    no_encoder):
    recorder = TrialRecorder()
    recorder.open(tmp_path / "trial.mp4", 15, (65, 33))
    assert recorder.kind == "jpeg"
    for i in range(4):
        recorder.write(_frame(65, 33), [f"t={i / 15:.2f} s  MARK"])
    folder = Path(recorder.close())
    assert folder == tmp_path / "frames"
    assert not (tmp_path / "trial.mp4").exists()
    names = sorted(p.name for p in folder.iterdir())
    assert names == [f"frame_{i:06d}.jpg" for i in range(1, 5)]
    image = numpy.asarray(Image.open(folder / names[0]).convert("RGB"))
    assert (image.shape[1], image.shape[0]) == recorder.size
    band = image[:recorder.band_height]
    assert band.mean() < 90 and band.max() > 150        # a label on a dark band
    assert image[recorder.band_height + 5:, :60].mean() > 230   # the frame


def test_an_encoder_that_will_not_start_falls_back_to_jpeg(tmp_path, monkeypatch):
    """The wheel present but its binary missing (a bundle built without it):
    the same folder of frames, not a trial without a record."""
    class Broken:
        __version__ = "0"

        @staticmethod
        def get_ffmpeg_exe():
            raise RuntimeError("no ffmpeg binary")

        @staticmethod
        def write_frames(*_a, **_k):
            raise RuntimeError("no ffmpeg binary")

    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", Broken)
    assert TrialRecorder.probe()["encoder"] == "jpeg"
    recorder = TrialRecorder()
    recorder.open(tmp_path / "trial.mp4", 15, (20, 20))
    assert recorder.kind == "jpeg"
    recorder.write(_frame(20, 20), ["t=0.00 s"])
    assert Path(recorder.close()) == tmp_path / "frames"


def test_close_is_idempotent_and_write_after_close_refuses(tmp_path, no_encoder):
    recorder = TrialRecorder()
    recorder.open(tmp_path / "trial.mp4", 15, (10, 10))
    recorder.write(_frame(10, 10), ["t=0.00 s"])
    first = recorder.close()
    assert recorder.close() == first
    with pytest.raises(RuntimeError):
        recorder.write(_frame(10, 10), ["late"])
