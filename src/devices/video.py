"""The trial recorder: labelled footage of the capture region, one file per
trial (owner, 2026-09-28: "rather than like 7 pictures, a video where
timestamps label the footage for analysis afterward").

**The encoder is optional and imported lazily.** `imageio_ffmpeg` (a wheel
that ships its own ffmpeg binary) is imported inside `open()` and `probe()`,
never at module import, so a model importing this module loads nothing
native. With it, the recording is an H.264 MP4 (`libx264`, `-crf 23`,
`-pix_fmt yuv420p`). Without it, or when its binary will not start (a bundle
built without it), the recording is a folder of JPEG frames beside where the
MP4 would have been (`frames/frame_000001.jpg`, quality 85), labelled the
same way: a trial is never left without its record for want of an encoder.

**The label is added, never painted over.** Each frame gets a dark band
ABOVE it holding the label lines in a monospace face (`t=12.34 s  red 63.2 %
z -1520  MARK`), so the footage the analysis reads is untouched. The band's
height is fixed when the recording opens (a narrow region wraps the label
onto more lines of it); the output is padded to even dimensions, which
yuv420p needs. A frame of another size than the one the recording opened
with is resized to it.

Not a `Device`: it opens a file, not the outside world, and belongs to the
Transfer Map's picture thread, which is the only caller of `write` and
`close`. Nothing here is thread-safe on its own.
"""
from pathlib import Path

#: The worst label the map writes, used to size the band at `open()`.
LABEL_TEMPLATE = "t=99999.99 s  red 100.0 %  z -9999999  MARK"
BAND_COLOUR = (16, 16, 16)
TEXT_COLOUR = (240, 240, 240)
#: Monospace faces, first found wins: Linux (the bench PC), macOS, Windows.
MONOSPACE_FONTS = (
    "DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf", "LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Monaco.ttf",
    "consola.ttf", "cour.ttf",
)


def _encoder():
    """The `imageio_ffmpeg` module, or None when it cannot be imported."""
    try:
        import imageio_ffmpeg
    except Exception:
        return None
    return imageio_ffmpeg


def to_rgb(frame):
    """An HxWx3 uint8 RGB array from what Red Percent grabs: an mss
    screenshot (a BGRA buffer and a size), a BGRA array, or an RGB array
    (as `RedMonitor._channels` reads them). None for anything else."""
    import numpy
    if frame is None:
        return None
    buffer = getattr(frame, "bgra", None)
    if buffer is not None:
        width = getattr(frame, "width", None)
        height = getattr(frame, "height", None)
        if width is None or height is None:
            width, height = frame.size
        pixels = numpy.frombuffer(buffer, dtype=numpy.uint8).reshape(
            int(height), int(width), 4)
        return numpy.ascontiguousarray(pixels[:, :, 2::-1])
    if isinstance(frame, numpy.ndarray) and frame.ndim == 3:
        if frame.shape[2] >= 4:
            return numpy.ascontiguousarray(frame[:, :, 2::-1], dtype=numpy.uint8)
        return numpy.ascontiguousarray(frame[:, :, :3], dtype=numpy.uint8)
    return None


def _font(size):
    from PIL import ImageFont
    for name in MONOSPACE_FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:                          # a Pillow before 10.1
        return ImageFont.load_default()


class TrialRecorder:
    """`open(path, fps, size)`, `write(frame_rgb, label_lines)`, `close()`."""

    CODEC = "libx264"
    CRF = 23
    #: The bench PC encodes while Red Percent grabs at ~66 Hz: the fastest
    #: preset that keeps H.264's quality at this CRF, for a smaller CPU cost.
    PRESET = "veryfast"
    JPEG_QUALITY = 85
    #: A close that hangs (a wedged ffmpeg) is killed after this long.
    CLOSE_TIMEOUT = 10.0
    PAD = 3                                    # px around the label lines

    def __init__(self):
        self.kind = None                       # "mp4" or "jpeg" once open
        self.path = None                       # the MP4, or the frames folder
        self.frames = 0
        self.frame_size = None                 # (w, h) of the footage
        self.size = None                       # (w, h) written: band + pad
        self.band_height = 0
        self.label_rows = 0
        self.fallback_reason = ""
        self._writer = None
        self._font = None
        self._line_height = 0
        self._closed = False

    @classmethod
    def probe(cls):
        """Which path a recording takes on this machine, for Diagnostics:
        `{"encoder": "mp4" | "jpeg", "detail": words}`. Starts nothing."""
        encoder = _encoder()
        if encoder is None:
            return {"encoder": "jpeg",
                    "detail": "imageio-ffmpeg is not installed: JPEG frames"}
        try:
            exe = encoder.get_ffmpeg_exe()
        except Exception as exc:
            return {"encoder": "jpeg",
                    "detail": f"imageio-ffmpeg has no ffmpeg ({exc}): JPEG frames"}
        version = getattr(encoder, "__version__", "?")
        return {"encoder": "mp4",
                "detail": f"H.264 MP4 via imageio-ffmpeg {version} ({Path(exe).name})"}

    # -- the label -------------------------------------------------------------
    def _layout(self, width):
        """Font size from the region's width, then how many lines the worst
        label needs at it: the band is fixed from here on."""
        size = max(10, min(20, int(width) // 32))
        self._font = _font(size)
        ascent, descent = self._font.getmetrics()
        self._line_height = ascent + descent + 1
        self.label_rows = max(1, len(self.label_layout(LABEL_TEMPLATE, width)))
        self.band_height = self.label_rows * self._line_height + 2 * self.PAD

    def label_layout(self, lines, width=None):
        """The label as it is drawn: its fields (split on two spaces) packed
        greedily into lines that fit the footage's width."""
        width = self.frame_size[0] if width is None else width
        if isinstance(lines, str):
            lines = [lines]
        fields = [f.strip() for line in lines for f in str(line).split("  ")
                  if f.strip()]
        room = max(1, int(width) - 2 * self.PAD)
        packed = []
        for field in fields:
            candidate = f"{packed[-1]}  {field}" if packed else field
            if packed and self._font.getlength(candidate) <= room:
                packed[-1] = candidate
            else:
                packed.append(field)
        return packed

    # -- the file ----------------------------------------------------------------
    def open(self, path, fps, size):
        """Start the recording of `size` = (width, height) frames at `fps`:
        the MP4 at `path` if the encoder starts, else `path`'s folder's
        `frames/`. Never raises for want of an encoder."""
        path = Path(path)
        width, height = (int(v) for v in size)
        self.frame_size = (width, height)
        self._layout(width)
        out_w = width + width % 2
        out_h = self.band_height + height
        out_h += out_h % 2
        self.size = (out_w, out_h)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoder = _encoder()
        if encoder is None:
            self.fallback_reason = "imageio-ffmpeg is not installed"
        else:
            try:
                writer = encoder.write_frames(
                    str(path), self.size, fps=float(fps), codec=self.CODEC,
                    pix_fmt_in="rgb24", pix_fmt_out="yuv420p", quality=None,
                    macro_block_size=1, ffmpeg_log_level="error",
                    ffmpeg_timeout=self.CLOSE_TIMEOUT,
                    output_params=["-crf", str(self.CRF), "-preset", self.PRESET])
                writer.send(None)              # starts ffmpeg; raises here
                self._writer, self.kind, self.path = writer, "mp4", path
                return self
            except Exception as exc:
                self.fallback_reason = f"the encoder did not start ({exc})"
        self.kind = "jpeg"
        self.path = path.parent / "frames"
        self.path.mkdir(parents=True, exist_ok=True)
        return self

    def compose(self, frame_rgb, label_lines):
        """The frame as it is written: resized to the recording's size if it
        is not, under a dark band holding the label, padded to even."""
        import numpy
        from PIL import Image, ImageDraw
        image = Image.fromarray(numpy.asarray(frame_rgb, dtype=numpy.uint8)[:, :, :3],
                                "RGB")
        if image.size != self.frame_size:
            image = image.resize(self.frame_size)
        canvas = Image.new("RGB", self.size, BAND_COLOUR)
        canvas.paste(image, (0, self.band_height))
        draw = ImageDraw.Draw(canvas)
        for row, text in enumerate(self.label_layout(label_lines)[:self.label_rows]):
            draw.text((self.PAD, self.PAD + row * self._line_height), text,
                      fill=TEXT_COLOUR, font=self._font)
        return numpy.asarray(canvas)

    def write(self, frame_rgb, label_lines):
        """Label and encode one frame; returns the labelled frame (an HxWx3
        array, for a still of it). Raises when the recording is closed or the
        encoder failed: the caller decides what a lost video means."""
        if self._closed or self.kind is None:
            raise RuntimeError("the recording is not open")
        labelled = self.compose(frame_rgb, label_lines)
        if self.kind == "mp4":
            self._writer.send(labelled.tobytes())
        else:
            from PIL import Image
            Image.fromarray(labelled, "RGB").save(
                self.path / f"frame_{self.frames + 1:06d}.jpg", format="JPEG",
                quality=self.JPEG_QUALITY)
        self.frames += 1
        return labelled

    def close(self):
        """Finish the file (ffmpeg flushes and exits) and return its path:
        the MP4, or the frames folder. Idempotent."""
        if not self._closed:
            self._closed = True
            writer, self._writer = self._writer, None
            if writer is not None:
                writer.close()
        return None if self.path is None else str(self.path)
