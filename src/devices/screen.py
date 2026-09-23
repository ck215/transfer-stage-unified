"""Screen capture. The ONE importer of `mss` in the whole package.

`RedPercentSystem.capture_focus_area` and the web server each built their own
`mss.mss()` and their own try/except around it, so a capture failure meant two
different things depending on which frontend you were looking at. One Device
now, owned by the model, reported through `Model.state` like every other piece
of hardware.

Three things this has to get right that the old duplicate did not:

**The import is lazy and guarded.** `mss` is a hard import at module scope in
`legacy/src/`, wrapped in a bare `try/except ImportError` that set `mss = None` — and
then the monitor thread did `with mss.mss()` anyway and raised `AttributeError`
from inside the thread the instant it started (REDPERCENT-4). Here the import
happens in `open()`, a failure is recorded, `is_available` reports it, and the
model refuses to start a run rather than starting one that cannot capture.

**One `mss` instance per thread.** `mss` is explicitly not thread-safe; an
instance belongs to the thread that created it. The run loop is a worker
thread, the schema's live readouts are polled from the UI thread. A
thread-local instance is created on demand and every one of them is closed by
`close()`.

**A grab returns the raw frame.** No PIL, no per-frame `numpy.array(...)` copy
— `RedMonitor._measure_red` reads the BGRA buffer the screenshot already
holds. At the sampling rate the owner asked for, a per-frame RGB conversion is
the difference between a few hundred frames a second and a few dozen.
"""
import threading

from devices.device import Device
from events import events


class Screen(Device):
    """Grab one rectangular region of the screen, as fast as the OS allows."""

    NAME = "Screen"

    def __init__(self, factory=None):
        """`factory` is a zero-argument callable returning an object with a
        `grab(region)` method — `mss.mss` in production, a fake in tests, so
        no test ever has to touch a real display."""
        self._factory = factory
        self._local = threading.local()
        self._instances = []
        self._lock = threading.Lock()
        self._is_open = False
        self._error = ""
        self._failures = 0

    # -- Device ------------------------------------------------------------
    def open(self):
        if self._is_open:
            return
        if self._factory is None:
            try:
                import mss
            except Exception as exc:          # ImportError, and the OSError
                self._error = f"mss is unavailable: {exc}"   # a headless box
                events.warn("Screen Capture Unavailable", self._error,
                            source=self.NAME, exception=exc)
                return
            self._factory = mss.mss
        self._error = ""
        self._is_open = True
        events.debug("Open", f"screen capture ready via {self._factory!r}",
                     source=self.NAME)

    def close(self):
        self._is_open = False
        with self._lock:
            instances, self._instances = self._instances, []
        for instance in instances:
            try:
                instance.close()
            except Exception as exc:
                events.debug("Close Failed", str(exc), source=self.NAME,
                             exception=exc)
        self._local = threading.local()
        events.debug("Close", f"{len(instances)} capture handle(s) closed; "
                     f"{self._failures} grab failure(s) this session",
                     source=self.NAME)

    @property
    def is_open(self):
        return self._is_open

    @property
    def is_available(self):
        """False when `mss` could not be loaded. `error` says why."""
        return self._is_open or self._factory is not None

    @property
    def error(self):
        return self._error

    @property
    def failures(self):
        return self._failures

    @property
    def status(self):
        if self._error:
            return "unavailable"
        return "capturing" if self._is_open else "closed"

    # -- capture -----------------------------------------------------------
    def screenshot_png(self, max_width=1600):
        """The whole virtual desktop as PNG bytes, downscaled to `max_width`,
        plus its full-size bounds: `(png_bytes, {"left","top","width","height"})`.
        For a view that has no overlay of its own (the browser) to draw a
        region on. Returns (None, None) when capture is unavailable."""
        if not self._is_open:
            return None, None
        try:
            from PIL import Image
            instance = self._instance()
            bounds = dict(instance.monitors[0])   # the virtual desktop
            shot = instance.grab(bounds)
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            if image.width > max_width:
                image = image.resize((max_width, round(image.height * max_width / image.width)))
            import io
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue(), bounds
        except Exception as exc:
            events.debug("Screenshot Failed", str(exc), source=self.NAME,
                         exception=exc, every=1.0)
            return None, None

    def grab(self, region):
        """The frame under `region`, or None when the grab failed.

        `region` is an mss-shaped dict: `{"top", "left", "width", "height"}`.
        Only that rectangle is read — never the full screen followed by a
        crop, which is what makes the fast sample modes affordable.

        A failure is a `None`, never an exception into the run loop: a
        transient grab error (a display sleeping, a space switching) must not
        end a run that is otherwise fine. It is counted and logged at most
        once a second, because at these rates a per-failure line is a flood.
        """
        if not self._is_open or not region:
            return None
        try:
            return self._instance().grab(region)
        except Exception as exc:
            self._failures += 1
            events.debug("Grab Failed", f"{exc} (failure #{self._failures})",
                         source=self.NAME, exception=exc, every=1.0)
            return None

    def _instance(self):
        instance = getattr(self._local, "capture", None)
        if instance is None:
            instance = self._factory()
            self._local.capture = instance
            with self._lock:
                self._instances.append(instance)
            events.debug("Capture Handle", "new per-thread mss instance",
                         source=self.NAME)
        return instance
