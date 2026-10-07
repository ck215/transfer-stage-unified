"""The full-display trial recorder (REC-1; audit capture.md section 5).

One monitor, at its native resolution, for the length of a trial, with a
`frames.csv` sidecar row per written frame so the footage can be aligned
with the trial's telemetry (`model.trial_telemetry`, same monotonic clock)
and cropped to any region afterwards. Not the region recorder: that is
`devices.video.TrialRecorder`, which stays as it is (labels burned in, a
resize for an odd frame). Here the pixels are left pristine.

    recorder = ScreenRecorder(out_dir, fps=15, monitor=1)
    recorder.start()                  # raises if there is no encoder / monitor
    recorder.mark("Mark")             # flags the next frame's row
    result = recorder.stop()          # RecorderResult(video_path, index_path, frames, dropped)
    recorder.capture_still(path)      # one full-resolution PNG, any time

**Two threads, one bounded queue between them.** The capture thread calls
the frame source at `fps` on a fixed schedule (a slow grab skips the ticks
it missed rather than bursting) and offers each frame to a queue bounded in
BYTES (a raw 1080p BGRA frame is 8.3 MB; a frame count would be no bound).
A full queue drops the frame and counts it: the capture never waits on the
encoder. The writer thread owns the ONE ffmpeg process and pipes each frame
into it, then writes the frame's sidecar row.

**The frame size is fixed by the first frame.** Native resolution is what
the grab returns (on a Retina display that is twice the monitor's bounds in
points), so the size cannot be known before the first grab. A later frame of
any other shape is dropped and counted, never resized.

**The file survives a kill.** H.264 in a fragmented MP4 (`-movflags
+frag_keyframe+empty_moov`) with a keyframe every second: each completed
one-second fragment is playable even if ffmpeg, the station or the power
dies, where a plain MP4 writes its index only at exit and is unreadable
without it (audit CAP-6). The sidecar is line-buffered, so after a kill it
may hold rows for the last frames the encoder had not yet muxed: row `i` is
video frame `i` for every frame the video holds.

**`stop()` is bounded.** The writer gets `STOP_TIMEOUT` to drain the queue
and let ffmpeg finish; then ffmpeg is terminated, then killed, and the
timeout is counted (`stats["encoder_timeouts"]`). A frame source that hangs
is abandoned after `CAPTURE_JOIN` (`stats["capture_timeouts"]`).

**Quality.** `"crf18"` (default): libx264, CRF 18, yuv420p, a visual
record. `"lossless"`: libx264rgb at qp 0, RGB 4:4:4, pixel-exact, for
re-deriving a measurement from the footage (10-100x the size). Either way
an odd width or height is padded to even at the right and bottom, so a
pixel's coordinates in the video are its coordinates on the monitor.

The encoder is the ffmpeg binary the `imageio-ffmpeg` wheel ships (found
by `devices.video.ffmpeg_exe`, the wheel's one importer), run with
`subprocess` so this module owns the process (its pid, its pipe, its kill).
The default frame source is a `devices.screen.Screen` (the one importer of
the capture library); a test injects `frame_source` and never touches the
screen.
"""
import csv
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import NamedTuple

from devices import screen as screen_module
from devices import video
from events import events

SOURCE = "Screen Recorder"
QUALITIES = ("crf18", "lossless")
VIDEO_NAME = "screen.mp4"
INDEX_NAME = "frames.csv"
INDEX_COLUMNS = ("frame", "t_monotonic", "t_wall", "marked")
#: ffmpeg's own error output, kept beside the video only when it said something.
ENCODER_LOG = "ffmpeg.log"


class RecorderResult(NamedTuple):
    """What a recording left: the video (None if no encoder ever started),
    the sidecar (None if the recording never started), the frames written
    (one sidecar row each) and the frames grabbed but not written."""
    video_path: Path | None
    index_path: Path | None
    frames: int
    dropped: int


def _ffmpeg_exe():
    """The ffmpeg binary (`devices.video.ffmpeg_exe`, the one importer of
    the wheel). Raises RuntimeError when there is none. A test replaces
    this with a stand-in script."""
    return video.ffmpeg_exe()


def _make_screen():
    """The `Screen` the default source and the still grab through. A test
    replaces this with one over a fake capture factory."""
    return screen_module.Screen()


def _monitor_bounds(screen, monitor):
    """`monitor` as a capture region: an index into the capture library's
    monitor list (0 = all monitors as one desktop, 1 = the primary, 2... the
    others) or a `{"left", "top", "width", "height"}` dict taken as given.
    Raises ValueError for an index that does not exist.

    `Screen` has no public monitor list, so this borrows the calling
    thread's capture instance the way `Screen.screenshot_png` does (its kept
    handle on the capture thread, a one-shot instance elsewhere)."""
    if isinstance(monitor, dict):
        return {key: int(monitor[key]) for key in ("left", "top", "width", "height")}
    with screen._handle() as instance:
        monitors = [dict(m) for m in instance.monitors]
    index = int(monitor)
    if not 0 <= index < len(monitors):
        raise ValueError(f"there is no monitor {index}: this display has "
                         f"{len(monitors) - 1} (0 is all of them as one)")
    return monitors[index]


def _shot_array(shot):
    """An HxWx4 BGRA view of an mss-shaped screenshot, without a copy
    (`raw` is the screenshot's own buffer; `bgra` would copy it)."""
    import numpy
    buffer = getattr(shot, "raw", None)
    if buffer is None:
        buffer = shot.bgra
    width, height = shot.size
    return numpy.frombuffer(buffer, dtype=numpy.uint8).reshape(int(height), int(width), 4)


class _ScreenSource:
    """The default `frame_source`: one monitor through a `Screen`, which
    opens on the thread that first calls this and keeps that thread's
    capture handle (the capture library is not thread-safe). Returns
    `(HxWx4 BGRA array, t)` with `t` the monotonic midpoint of the grab, or
    None when the grab failed (`Screen` counts and logs those)."""

    def __init__(self, monitor):
        self._monitor = monitor
        self._screen = None
        self._bounds = None

    def __call__(self):
        if self._screen is None:
            screen = _make_screen()
            screen.open()
            try:
                if not screen.is_open:
                    raise RuntimeError(screen.error or "screen capture is unavailable")
                screen.keep_handle()
                self._bounds = _monitor_bounds(screen, self._monitor)
            except Exception:
                screen.drop_handle()
                screen.close()
                raise
            self._screen = screen
        before = time.monotonic()
        shot = self._screen.grab(self._bounds)
        after = time.monotonic()
        if shot is None:
            return None
        return _shot_array(shot), (before + after) / 2

    def close(self):
        screen, self._screen = self._screen, None
        if screen is not None:
            screen.drop_handle()
            screen.close()


class _ByteQueue:
    """FIFO bounded by the total bytes it holds. `put` never blocks: it
    refuses an item that would overflow the budget (one item larger than
    the whole budget is taken, alone, into an empty queue, or such a frame
    could never be recorded). `get` blocks for the next item and returns
    None once the queue is closed and empty."""

    def __init__(self, limit):
        self.limit = int(limit)
        self._items = deque()
        self._bytes = 0
        self.peak = 0
        self._closed = False
        self._cond = threading.Condition()

    def put(self, item, nbytes):
        with self._cond:
            if self._closed:
                return False
            if self._items and self._bytes + nbytes > self.limit:
                return False
            self._items.append((item, nbytes))
            self._bytes += nbytes
            self.peak = max(self.peak, self._bytes)
            self._cond.notify()
            return True

    def get(self):
        with self._cond:
            while not self._items and not self._closed:
                self._cond.wait()
            if not self._items:
                return None
            item, nbytes = self._items.popleft()
            self._bytes -= nbytes
            return item

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    @property
    def queued_bytes(self):
        with self._cond:
            return self._bytes

    def __len__(self):
        with self._cond:
            return len(self._items)


def _detached():
    """Popen options that keep a terminal's Ctrl+C away from the encoder (the
    station's own shutdown closes its input, and it finishes the file) and,
    on Windows, keep a console window from opening for it."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _write_all(stream, view):
    """Every byte of `view` into an unbuffered pipe (a raw write may be
    partial). Raises OSError/ValueError when the reader is gone."""
    while len(view):
        written = stream.write(view)
        view = view[written or 0:]


class ScreenRecorder:
    """One recording of one monitor: `start()`, `mark(label)`, `stop()`,
    and `capture_still(path)` at any time. One recorder per trial."""

    #: Grabbed frames waiting for the encoder, in bytes (about 15 raw 1080p
    #: BGRA frames, one second at 15 fps). Past it a frame is dropped.
    QUEUE_BYTES = 128 * 1024 * 1024
    #: What `stop()` gives the writer to drain and ffmpeg to finish.
    STOP_TIMEOUT = 5.0
    #: After a timeout: terminate, wait this long, kill, wait this long.
    KILL_GRACE = 2.0
    #: What `stop()` gives a frame source that is mid-grab.
    CAPTURE_JOIN = 2.0
    CRF = 18
    PRESET = "veryfast"
    LOSSLESS_PRESET = "ultrafast"
    #: One keyframe, hence one playable fragment, per this many seconds.
    KEYFRAME_SECONDS = 1.0
    PNG_COMPRESS_LEVEL = 3

    def __init__(self, out_dir, fps, monitor, *, quality="crf18", frame_source=None):
        """`frame_source` is a zero-argument callable returning
        `(frame, t_monotonic)`, or None for a failed grab. `frame` is an
        HxWx4 uint8 BGRA array (as the capture library grabs) or an HxWx3
        RGB one, a new array per call (it is queued, not copied). It is
        called on the capture thread, and by `capture_still` on the caller's.
        None means the monitor, through `devices.screen.Screen`."""
        if quality not in QUALITIES:
            raise ValueError(f"quality is one of {QUALITIES}, not {quality!r}")
        fps = float(fps)
        if not fps > 0:
            raise ValueError(f"fps must be positive, not {fps}")
        self.out_dir = Path(out_dir)
        self.fps = fps
        self.monitor = monitor
        self.quality = quality
        self.video_path = self.out_dir / VIDEO_NAME
        self.index_path = self.out_dir / INDEX_NAME
        self._source = frame_source
        self._lock = threading.Lock()
        self._started = False
        self._result = None
        self._marks = []                   # [(t_monotonic, label)] not yet on a row
        self._stop = threading.Event()
        self._queue = None
        self._capture = self._writer = None
        self._exe = None
        self._proc = None
        self._encoder_dead = False
        self._stopping = False
        self._shape = None
        self._index_file = None
        self._index = None
        self._counts = {"grabbed": 0, "frames": 0, "dropped_full": 0,
                        "dropped_size": 0, "dropped_encoder": 0,
                        "grab_failures": 0, "late_ticks": 0,
                        "encoder_timeouts": 0, "capture_timeouts": 0,
                        "unplaced_marks": 0}
        self._encoder_error = ""

    # -- what the caller reads -----------------------------------------------------
    @property
    def is_recording(self):
        return self._started and self._result is None

    @property
    def stats(self):
        """Counters for diagnostics (each written by one thread): grabbed,
        frames, dropped_full / dropped_size / dropped_encoder, grab_failures,
        late_ticks (capture ticks missed), encoder_timeouts,
        capture_timeouts, unplaced_marks, peak_queued_bytes, encoder_error."""
        stats = dict(self._counts)
        queue = self._queue
        stats["queued_bytes"] = queue.queued_bytes if queue is not None else 0
        stats["peak_queued_bytes"] = queue.peak if queue is not None else 0
        stats["encoder_error"] = self._encoder_error
        return stats

    # -- the recording ---------------------------------------------------------------
    def start(self):
        """Begin recording. Raises RuntimeError when there is no encoder or
        no screen capture, ValueError for a monitor that does not exist, and
        RuntimeError on a second start: nothing is left running."""
        with self._lock:
            if self._started:
                raise RuntimeError("a ScreenRecorder records once: build a new "
                                   "one for the next trial")
            self._exe = _ffmpeg_exe()
            if self._source is None:
                self._check_monitor()
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self._index_file = open(self.index_path, "w", newline="",
                                    encoding="utf-8", buffering=1)
            self._index = csv.writer(self._index_file, lineterminator="\n")
            self._index.writerow(INDEX_COLUMNS)
            self._queue = _ByteQueue(self.QUEUE_BYTES)
            self._started = True
        source = self._source if self._source is not None else _ScreenSource(self.monitor)
        self._writer = threading.Thread(target=self._writer_loop,
                                        name="screen-recorder-writer", daemon=True)
        self._capture = threading.Thread(target=self._capture_loop, args=(source,),
                                         name="screen-recorder-capture", daemon=True)
        self._writer.start()
        self._capture.start()
        events.debug("Recording", f"monitor {self.monitor!r} at {self.fps:g} fps, "
                     f"{self.quality}, into {self.out_dir}", source=SOURCE)

    def _check_monitor(self):
        """Fail at start, not on the capture thread, when the display cannot
        be captured or the monitor does not exist."""
        screen = _make_screen()
        screen.open()
        try:
            if not screen.is_open:
                raise RuntimeError(screen.error or "screen capture is unavailable")
            _monitor_bounds(screen, self.monitor)
        finally:
            screen.close()

    def mark(self, label):
        """Flag the sidecar row of the first frame grabbed at or after now
        with `label` (several marks before that frame are joined with "; ").
        Grabs nothing. Ignored when not recording."""
        now = time.monotonic()
        with self._lock:
            if not self._started or self._result is not None or self._stop.is_set():
                return
            self._marks.append((now, str(label)))

    def stop(self):
        """End the recording and return its `RecorderResult`. Bounded (see
        the module docstring); idempotent; before `start()` it is
        `RecorderResult(None, None, 0, 0)`."""
        with self._lock:
            if self._result is not None:
                return self._result
            if not self._started:
                return RecorderResult(None, None, 0, 0)
            self._stop.set()
        self._capture.join(self.CAPTURE_JOIN)
        if self._capture.is_alive():
            self._counts["capture_timeouts"] += 1
            events.debug("Capture Hung", f"the frame source did not return within "
                         f"{self.CAPTURE_JOIN:g} s; abandoned", source=SOURCE)
        self._queue.close()
        self._writer.join(self.STOP_TIMEOUT)
        if self._writer.is_alive():
            self._end_encoder()
        pending = len(self._queue) if self._writer.is_alive() else 0
        with self._lock:
            unplaced, self._marks = len(self._marks), []
        self._counts["unplaced_marks"] += unplaced
        counts = self._counts
        dropped = (counts["dropped_full"] + counts["dropped_size"]
                   + counts["dropped_encoder"] + pending)
        video = self.video_path if self.video_path.exists() else None
        result = RecorderResult(video, self.index_path, counts["frames"], dropped)
        with self._lock:
            self._result = result
        if not self._writer.is_alive():
            self._tidy_encoder_log()
        events.debug("Recording Stopped",
                     f"{result.frames} frame(s) written, {dropped} dropped "
                     f"({counts['dropped_full']} queue full, {counts['dropped_size']} "
                     f"wrong size, {counts['dropped_encoder']} encoder, {pending} "
                     f"unwritten); {counts['grab_failures']} failed grab(s), "
                     f"{counts['late_ticks']} late tick(s), peak queue "
                     f"{self._queue.peak / 1e6:.1f} MB", source=SOURCE)
        return result

    def _end_encoder(self):
        """The writer missed STOP_TIMEOUT: a wedged or slow encoder. Ask it
        to finish (terminate lets ffmpeg close the file), then kill it; the
        broken pipe frees the writer."""
        self._counts["encoder_timeouts"] += 1
        self._stopping = True
        proc = self._proc
        for end in ("terminate", "kill"):
            if proc is not None:
                try:
                    getattr(proc, end)()
                except OSError:
                    pass
            self._writer.join(self.KILL_GRACE)
            if not self._writer.is_alive():
                break
        events.warn("Recording Stop Timed Out",
                    f"The screen recording's encoder did not finish within "
                    f"{self.STOP_TIMEOUT:g} s and was ended. The video holds every "
                    f"fragment it completed: {self.video_path}", source=SOURCE)

    def _tidy_encoder_log(self):
        log = self.out_dir / ENCODER_LOG
        try:
            if log.exists() and log.stat().st_size == 0:
                log.unlink()
        except OSError:
            pass

    # -- the capture thread ------------------------------------------------------------
    def _capture_loop(self, source):
        period = 1.0 / self.fps
        next_tick = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    item = source()
                except Exception as exc:
                    item = None
                    events.debug("Grab Failed", repr(exc), source=SOURCE,
                                 exception=exc, every=1.0)
                try:
                    if item is None:
                        self._counts["grab_failures"] += 1
                    else:
                        self._offer(*item)
                except Exception as exc:          # never end the capture
                    events.debug("Frame Refused", repr(exc), source=SOURCE,
                                 exception=exc, every=1.0)
                next_tick += period
                now = time.monotonic()
                if next_tick < now:               # behind: skip, never burst
                    missed = int((now - next_tick) / period) + 1
                    self._counts["late_ticks"] += missed
                    next_tick += missed * period
                if self._stop.wait(max(0.0, next_tick - time.monotonic())):
                    break
        finally:
            if source is not self._source:
                try:
                    source.close()
                except Exception as exc:
                    events.debug("Capture Close Failed", repr(exc), source=SOURCE,
                                 exception=exc)

    def _offer(self, frame, t_monotonic):
        """One grabbed frame to the queue, or a counted drop."""
        t_wall = time.time() - (time.monotonic() - t_monotonic)
        self._counts["grabbed"] += 1
        shape = getattr(frame, "shape", None)
        if (shape is None or len(shape) != 3 or shape[2] not in (3, 4)
                or str(getattr(frame, "dtype", "")) != "uint8"):
            self._counts["dropped_size"] += 1
            events.debug("Frame Dropped", f"not an HxWx3/4 uint8 array: {shape}",
                         source=SOURCE, every=1.0)
            return
        if self._shape is None:
            self._shape = tuple(shape)
        elif tuple(shape) != self._shape:
            self._counts["dropped_size"] += 1
            events.debug("Frame Dropped", f"{shape} is not the recording's "
                         f"{self._shape}", source=SOURCE, every=1.0)
            return
        with self._lock:
            placed = [label for t, label in self._marks if t <= t_monotonic]
            if placed:
                self._marks = [(t, label) for t, label in self._marks if t > t_monotonic]
        if not self._queue.put((frame, t_monotonic, t_wall, placed), frame.nbytes):
            self._counts["dropped_full"] += 1
            if placed:                            # the next frame carries them
                with self._lock:
                    self._marks = [(t_monotonic, label) for label in placed] + self._marks

    # -- the writer thread -------------------------------------------------------------
    def _writer_loop(self):
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                frame, t_monotonic, t_wall, marks = item
                if not self._encode(frame):
                    self._counts["dropped_encoder"] += 1
                    continue
                self._index.writerow([self._counts["frames"], f"{t_monotonic:.6f}",
                                      f"{t_wall:.6f}", "; ".join(marks)])
                self._counts["frames"] += 1
            self._finish_encoder()
        except Exception as exc:
            self._encoder_error = self._encoder_error or repr(exc)
            events.debug("Writer Failed", repr(exc), source=SOURCE, exception=exc)
        finally:
            try:
                self._index_file.close()
            except Exception:
                pass

    def _encode(self, frame):
        """Pipe one frame to ffmpeg, starting it at the first. False when
        the encoder is gone (the frame is the caller's to count)."""
        if self._encoder_dead:
            return False
        if self._proc is None:
            try:
                self._proc = self._open_encoder(frame.shape)
            except Exception as exc:
                self._lose_encoder(exc)
                return False
        try:
            import numpy
            data = numpy.ascontiguousarray(frame)
            _write_all(self._proc.stdin, memoryview(data).cast("B"))
            return True
        except (OSError, ValueError) as exc:
            self._lose_encoder(exc)
            return False

    def _lose_encoder(self, exc):
        self._encoder_dead = True
        self._encoder_error = repr(exc)
        events.debug("Encoder Lost", repr(exc), source=SOURCE, exception=exc)
        if not self._stopping:
            events.warn("Recording Encoder Lost",
                        f"The screen recording's encoder stopped ({exc}). Frames "
                        f"from now on are not recorded; the video keeps every "
                        f"fragment it completed.", source=SOURCE)

    def _encoder_command(self, shape):
        height, width, channels = shape
        pix_in = "bgr0" if channels == 4 else "rgb24"
        command = [self._exe, "-hide_banner", "-nostats", "-loglevel", "error", "-y",
                   "-f", "rawvideo", "-pix_fmt", pix_in, "-s", f"{width}x{height}",
                   "-framerate", f"{self.fps:g}", "-i", "pipe:0", "-an"]
        if width % 2 or height % 2:
            command += ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0:black"]
        if self.quality == "lossless":
            command += ["-c:v", "libx264rgb", "-preset", self.LOSSLESS_PRESET,
                        "-qp", "0", "-pix_fmt", pix_in]
        else:
            command += ["-c:v", "libx264", "-preset", self.PRESET,
                        "-crf", str(self.CRF), "-pix_fmt", "yuv420p"]
        keyint = max(1, round(self.fps * self.KEYFRAME_SECONDS))
        command += ["-g", str(keyint),
                    "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
                    "-flush_packets", "1", "-f", "mp4", str(self.video_path)]
        return command

    def _open_encoder(self, shape):
        command = self._encoder_command(shape)
        with open(self.out_dir / ENCODER_LOG, "wb") as log:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=log, bufsize=0,
                                    **_detached())
        events.debug("Encoder Started", f"pid {proc.pid}: {' '.join(command[1:])}",
                     source=SOURCE)
        return proc

    def _finish_encoder(self):
        """End of input: ffmpeg flushes and exits. Unbounded here; `stop()`
        bounds it by ending the process."""
        proc = self._proc
        if proc is None:
            return
        try:
            proc.stdin.close()
        except OSError:
            pass
        code = proc.wait()
        if code not in (0, None) and not self._stopping and not self._encoder_dead:
            self._encoder_error = f"ffmpeg exited with {code}"
            events.debug("Encoder Exit", self._encoder_error, source=SOURCE)

    # -- the still ---------------------------------------------------------------------
    def capture_still(self, path):
        """One full-resolution PNG of the monitor (or of one frame of the
        injected source) at `path`, which is returned. Works whether or not
        a recording runs, and adds no row to it. Raises RuntimeError when
        nothing could be grabbed."""
        path = Path(path)
        own = None
        source = self._source
        if source is None:
            source = own = _ScreenSource(self.monitor)
        try:
            item = source()
        finally:
            if own is not None:
                own.close()
        if item is None:
            raise RuntimeError("the screen could not be grabbed for a still")
        rgb = video.to_rgb(item[0])
        if rgb is None:
            raise RuntimeError(f"the still is not an image: {type(item[0]).__name__}")
        from PIL import Image
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgb, "RGB").save(path, format="PNG",
                                         compress_level=self.PNG_COMPRESS_LEVEL)
        return path
