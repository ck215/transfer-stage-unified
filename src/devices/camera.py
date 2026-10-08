"""A ToupCam camera in pull mode: the station's own frames, not the screen's.

The injected `lib` is the vendor Python wrapper's surface (legacy/src/lib/
toupcam.py): `lib.Toupcam` with `EnumV2()` and `Open(id)`, and
`lib.ToupcamFrameInfoV2`. A real run injects the `toupcam` module; tests and
SIM inject a fake. This module never imports the SDK and never loads a native
library, so it imports on a Mac with no libtoupcam.

Call sequence (audit capture.md section 6): EnumV2 -> Open -> pick a
resolution (put_eSize) -> put_AutoExpoEnable(0) -> put_ExpoTime ->
put_Option(BYTEORDER) -> StartPullModeWithCallback -> on EVENT_IMAGE,
PullImageV2 into a preallocated buffer -> Stop -> Close. Auto-exposure is
turned OFF and the byte order is set explicitly: the SDK default is BGR on
Windows and RGB on Linux and macOS, so leaving it alone gives channel-swapped
frames on one of the two benches.

Exclusive USB lock: only ONE process can hold the camera. The vendor viewer
(AmLite) must be closed while the station pulls, and the station must be
closed for AmLite to see the camera. `open()` failing with CameraUnavailable
is the usual symptom of the viewer still running.

Bench question, not answered here: whether ToupTek's libtoupcam enumerates the
AmScope MU1003 at all, or AmScope's rebranded `amcam` SDK is needed (same API,
different library name). `Camera` takes either, as long as `lib` has the
wrapper surface above.

Frames are RGB24 (or BGR24 with byte_order="bgr"), rows padded to 4 bytes
(TDIBWIDTHBYTES), so a buffer is `frame_bytes` long.
"""
import ctypes
import threading
import time
from collections import namedtuple

from devices.device import Device

# Values from the SDK header (legacy/src/lib/toupcam.py); read off `lib` when
# it has them, so a vendor renumbering wins over these.
_EVENT_IMAGE = 0x0004
_EVENT_ERROR = 0x0080
_EVENT_DISCONNECTED = 0x0081
_OPTION_BYTEORDER = 0x2A  # 0 = RGB, 1 = BGR
_BITS = 24

CameraInfo = namedtuple("CameraInfo", "model serial resolutions")
FrameInfo = namedtuple("FrameInfo", "seq t_us exposure_us width height")


class CameraError(Exception):
    """The camera failed after it was open."""


class CameraUnavailable(CameraError):
    """No camera could be opened: none enumerated, Open returned None (the
    usual sign of another process, e.g. AmLite, holding it), or the native
    library is missing or failed."""


class FrameTimeout(CameraError):
    """No frame arrived within the timeout."""


def frame_bytes(width, height):
    """Bytes of one RGB24 frame: rows padded to 4 bytes (TDIBWIDTHBYTES)."""
    return ((width * _BITS + 31) // 32 * 4) * height


class Camera(Device):
    is_hardware = True

    def __init__(self, lib, *, byte_order="rgb", simulated=False):
        if byte_order not in ("rgb", "bgr"):
            raise ValueError("byte_order must be 'rgb' or 'bgr'")
        self._lib = lib
        self._byte_order = byte_order
        self._simulated = simulated
        self._cam = None
        self._lock = threading.RLock()
        self._frame_ready = threading.Event()
        self._streaming = False
        self._fault = None
        self._width = self._height = 0
        self._exposure_us = 0
        self._last_seq = None
        self._info = None  # reused FrameInfoV2: no per-frame allocation
        self._cbuf = (None, None)  # (buffer, ctypes view) of the last buffer
        self._info_open = None
        self.dropped = 0

    @classmethod
    def sim(cls, width=640, height=480, fps=30.0, **kw):
        """A Camera over a built-in library that synthesises frames."""
        return cls(SimToupcamLib(width, height, fps), simulated=True, **kw)

    # lifecycle --------------------------------------------------------
    def _const(self, name, default):
        return getattr(self._lib, name, default)

    def open(self):
        with self._lock:
            if self._cam is not None:
                return self._info_open
            try:
                devices = list(self._lib.Toupcam.EnumV2())
                if not devices:
                    raise CameraUnavailable("no ToupCam camera enumerated")
                cam = self._lib.Toupcam.Open(devices[0].id)
            except CameraUnavailable:
                raise
            except OSError as e:
                raise CameraUnavailable(f"camera library failed: {e}") from e
            if cam is None:
                raise CameraUnavailable(
                    "Open returned None: the camera is held by another process "
                    "(close the vendor viewer, AmLite) or was unplugged")
            try:
                n = cam.ResolutionNumber()
                res = [tuple(cam.get_Resolution(i)) for i in range(n)]
            except OSError as e:
                cam.Close()
                raise CameraUnavailable(f"camera query failed: {e}") from e
            self._cam = cam
            self._fault = None
            self._info_open = CameraInfo(
                getattr(devices[0], "displayname", ""), str(devices[0].id), res)
            return self._info_open

    @property
    def is_open(self):
        return self._cam is not None

    @property
    def status(self):
        if self._cam is None:
            return "closed"
        return "simulated" if self._simulated else "open"

    @property
    def is_streaming(self):
        return self._streaming

    @property
    def frame_bytes(self):
        return frame_bytes(self._width, self._height)

    def start_pull(self, resolution_index, exposure_us, gain=None):
        with self._lock:
            if self._cam is None:
                raise CameraError("camera is not open")
            if self._streaming:
                self._stop_locked()
            cam = self._cam
            try:
                self._width, self._height = tuple(
                    cam.get_Resolution(resolution_index))
                cam.put_eSize(resolution_index)
                cam.put_AutoExpoEnable(0)
                cam.put_ExpoTime(int(exposure_us))
                if gain is not None:
                    cam.put_ExpoAGain(int(gain))
                cam.put_Option(self._const("TOUPCAM_OPTION_BYTEORDER",
                                           _OPTION_BYTEORDER),
                               1 if self._byte_order == "bgr" else 0)
                self._exposure_us = int(exposure_us)
                self._info = self._lib.ToupcamFrameInfoV2()
                self._last_seq = None
                self.dropped = 0
                self._fault = None
                self._frame_ready.clear()
                cam.StartPullModeWithCallback(self._on_event, self)
            except OSError as e:
                raise CameraError(f"start_pull failed: {e}") from e
            self._streaming = True

    def _on_event(self, event, ctx):
        # SDK thread: set a flag, nothing else.
        if event == self._const("TOUPCAM_EVENT_IMAGE", _EVENT_IMAGE):
            self._frame_ready.set()
        elif event in (self._const("TOUPCAM_EVENT_ERROR", _EVENT_ERROR),
                       self._const("TOUPCAM_EVENT_DISCONNECTED",
                                   _EVENT_DISCONNECTED)):
            self._fault = event
            self._frame_ready.set()

    def pull(self, buffer, timeout=1.0):
        """Wait for a frame and copy it into `buffer` (a writable bytearray or
        ndarray of at least `frame_bytes`). Returns FrameInfo."""
        if not self._streaming:
            raise CameraError("not streaming: call start_pull first")
        need = self.frame_bytes
        if len(memoryview(buffer).cast("B")) < need:
            raise ValueError(f"buffer too small: need {need} bytes")
        if not self._frame_ready.wait(timeout):
            raise FrameTimeout(f"no frame within {timeout}s")
        with self._lock:
            if self._fault is not None:
                raise CameraError(f"camera event 0x{self._fault:x} "
                                  "(disconnected or error)")
            if not self._streaming:
                raise CameraError("stopped while waiting for a frame")
            self._frame_ready.clear()
            if self._cbuf[0] is not buffer:
                view = (ctypes.c_char * need).from_buffer(buffer)
                self._cbuf = (buffer, view)
            try:
                self._cam.PullImageV2(self._cbuf[1], _BITS, self._info)
            except OSError as e:
                raise CameraError(f"PullImageV2 failed: {e}") from e
            i = self._info
            if self._last_seq is not None and i.seq > self._last_seq + 1:
                self.dropped += i.seq - self._last_seq - 1
            self._last_seq = i.seq
            return FrameInfo(i.seq, i.timestamp, self._exposure_us,
                             i.width, i.height)

    def frame_rate(self):
        """Frames per second over the SDK's recent window (0.0 if unknown)."""
        with self._lock:
            if self._cam is None:
                return 0.0
            frames, ms, _total = self._cam.get_FrameRate()
            return frames * 1000.0 / ms if ms else 0.0

    def _stop_locked(self):
        self._streaming = False
        self._frame_ready.set()  # release a waiting pull
        self._cbuf = (None, None)
        try:
            self._cam.Stop()
        except OSError:
            pass

    def stop(self):
        with self._lock:
            if self._cam is not None and self._streaming:
                self._stop_locked()

    def close(self):
        with self._lock:
            cam, self._cam = self._cam, None
            if cam is None:
                return
            self._streaming = False
            self._frame_ready.set()
            self._cbuf = (None, None)
            for fn in (cam.Stop, cam.Close):
                try:
                    fn()
                except OSError:
                    pass

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()


# SIM ---------------------------------------------------------------------
class _SimFrameInfo:
    width = height = flag = seq = timestamp = 0


class _SimDevice:
    id = "sim:0"
    displayname = "Simulated ToupCam"


class _SimHandle:
    def __init__(self, lib):
        self._lib = lib
        self._size = (lib.width, lib.height)
        self._cb = None
        self._ctx = None
        self._thread = None
        self._stop = threading.Event()
        self._seq = 0
        self._t0 = time.monotonic()

    def ResolutionNumber(self):
        return 1

    def _resolution(self, i):
        return (self._lib.width, self._lib.height)

    # The SDK's names, bound by assignment: the house rule bans `def get_*`.
    get_Resolution = _resolution

    def put_eSize(self, i):
        pass

    def put_AutoExpoEnable(self, mode):
        pass

    def put_ExpoTime(self, t):
        pass

    def put_ExpoAGain(self, g):
        pass

    def put_Option(self, opt, val):
        pass

    def _frame_rate(self):
        return (int(self._lib.fps), 1000, self._seq)

    get_FrameRate = _frame_rate

    def StartPullModeWithCallback(self, fun, ctx):
        self._cb, self._ctx = fun, ctx
        self._stop.clear()
        if self._lib.realtime:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def emit(self, n=1):
        """Fire n image events synchronously (non-realtime sims and tests)."""
        for _ in range(n):
            self._seq += 1
            self._cb(self._lib.image_event, self._ctx)

    def _run(self):
        period = 1.0 / self._lib.fps
        while not self._stop.wait(period):
            self.emit()

    def PullImageV2(self, buf, bits, info):
        w, h = self._lib.width, self._lib.height
        pitch = ((w * bits + 31) // 32) * 4
        n = pitch * h
        shade = (self._seq * 7) & 0xFF
        buf[:n] = bytes([shade]) * n
        info.width, info.height = w, h
        info.seq = self._seq
        info.timestamp = int((time.monotonic() - self._t0) * 1e6)

    def Stop(self):
        self._stop.set()
        if self._thread is not None and \
                self._thread is not threading.current_thread():
            self._thread.join(1.0)
        self._thread = None

    def Close(self):
        self.Stop()


class _SimToupcamClass:
    def __init__(self, lib):
        self._lib = lib
        self.handle = None

    def EnumV2(self):
        return [_SimDevice()]

    def Open(self, cam_id):
        self.handle = _SimHandle(self._lib)
        return self.handle


class SimToupcamLib:
    """A stand-in wrapper surface that synthesises solid-grey frames whose
    shade follows the sequence number. `realtime=False` pulls no frames by
    itself: call `Toupcam.handle.emit(n)`."""
    image_event = _EVENT_IMAGE
    ToupcamFrameInfoV2 = _SimFrameInfo

    def __init__(self, width=640, height=480, fps=30.0, realtime=True):
        self.width, self.height = int(width), int(height)
        self.fps = float(fps)
        self.realtime = realtime
        self.Toupcam = _SimToupcamClass(self)
