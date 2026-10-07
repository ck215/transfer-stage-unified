"""Camera device class against a fake ToupCam library. No hardware, no SDK."""
import pytest

from devices.camera import (Camera, CameraError, CameraUnavailable,
                            FrameTimeout, SimToupcamLib, frame_bytes)

W, H = 8, 4
NBYTES = frame_bytes(W, H)


class FakeDev:
    id = "usb-1"
    displayname = "MU1003"


class FakeInfo:
    width = height = flag = seq = timestamp = 0


class FakeHandle:
    def __init__(self, log):
        self.log = log
        self.cb = None
        self.seq = 0
        self.fill = b"\x01"

    def _rec(self, *a):
        self.log.append(a)

    def ResolutionNumber(self):
        return 2

    def get_Resolution(self, i):
        return [(16, 8), (W, H)][i]

    def put_eSize(self, i):
        self._rec("put_eSize", i)

    def put_AutoExpoEnable(self, m):
        self._rec("put_AutoExpoEnable", m)

    def put_ExpoTime(self, t):
        self._rec("put_ExpoTime", t)

    def put_ExpoAGain(self, g):
        self._rec("put_ExpoAGain", g)

    def put_Option(self, o, v):
        self._rec("put_Option", o, v)

    def StartPullModeWithCallback(self, fun, ctx):
        self._rec("start")
        self.cb = (fun, ctx)

    def PullImageV2(self, buf, bits, info):
        self._rec("pull", bits)
        buf[:NBYTES] = self.fill * NBYTES
        info.width, info.height, info.seq = W, H, self.seq
        info.timestamp = self.seq * 1000

    def get_FrameRate(self):
        return (30, 1000, 99)

    def Stop(self):
        self._rec("Stop")

    def Close(self):
        self._rec("Close")

    def fire(self, seq, event=4):
        self.seq = seq
        self.cb[0](event, self.cb[1])


class FakeToupcam:
    def __init__(self, log, devices, opened, exc):
        self.log, self.devices, self.opened, self.exc = log, devices, opened, exc
        self.handle = FakeHandle(log)

    def EnumV2(self):
        if self.exc == "enum":
            raise OSError("no libtoupcam")
        return self.devices

    def Open(self, cam_id):
        self.log.append(("Open", cam_id))
        return self.handle if self.opened else None


class FakeLib:
    ToupcamFrameInfoV2 = FakeInfo

    def __init__(self, devices=(FakeDev(),), opened=True, exc=None):
        self.log = []
        self.Toupcam = FakeToupcam(self.log, list(devices), opened, exc)

    @property
    def handle(self):
        return self.Toupcam.handle

    def names(self):
        return [e[0] for e in self.log]


def started(**kw):
    lib = FakeLib()
    cam = Camera(lib, **kw)
    cam.open()
    cam.start_pull(1, 5000)
    return lib, cam


def test_open_success_reports_model_serial_resolutions():
    cam = Camera(FakeLib())
    info = cam.open()
    assert (info.model, info.serial) == ("MU1003", "usb-1")
    assert info.resolutions == [(16, 8), (W, H)]
    assert cam.is_open


def test_open_with_nothing_enumerated_is_unavailable():
    with pytest.raises(CameraUnavailable):
        Camera(FakeLib(devices=[])).open()


def test_open_returning_none_is_unavailable_and_names_the_viewer():
    with pytest.raises(CameraUnavailable, match="AmLite"):
        Camera(FakeLib(opened=False)).open()


def test_library_oserror_is_unavailable_not_a_crash():
    cam = Camera(FakeLib(exc="enum"))
    with pytest.raises(CameraUnavailable, match="no libtoupcam"):
        cam.open()
    assert not cam.is_open


def test_auto_exposure_off_and_byte_order_set_before_streaming():
    lib, _ = started()
    names = lib.names()
    assert ("put_AutoExpoEnable", 0) in lib.log
    assert ("put_Option", 0x2A, 0) in lib.log
    assert names.index("put_AutoExpoEnable") < names.index("start")
    assert names.index("put_Option") < names.index("start")
    assert ("put_ExpoTime", 5000) in lib.log and ("put_eSize", 1) in lib.log


def test_bgr_byte_order_is_explicit():
    lib, _ = started(byte_order="bgr")
    assert ("put_Option", 0x2A, 1) in lib.log


def test_bad_byte_order_rejected():
    with pytest.raises(ValueError):
        Camera(FakeLib(), byte_order="grb")


def test_pull_fills_given_buffer_and_returns_seq_and_timestamp():
    lib, cam = started()
    buf = bytearray(NBYTES)
    lib.handle.fire(7)
    f = cam.pull(buf, timeout=0.5)
    assert (f.seq, f.t_us, f.exposure_us, f.width, f.height) == \
        (7, 7000, 5000, W, H)
    assert bytes(buf) == b"\x01" * NBYTES
    assert ("pull", 24) in lib.log


def test_pull_reuses_the_same_buffer_without_rewrapping():
    lib, cam = started()
    buf = bytearray(NBYTES)
    lib.handle.fire(1)
    cam.pull(buf)
    view = cam._cbuf[1]
    lib.handle.fire(2)
    cam.pull(buf)
    assert cam._cbuf[1] is view


def test_pull_rejects_short_buffer_and_times_out():
    lib, cam = started()
    with pytest.raises(ValueError):
        cam.pull(bytearray(NBYTES - 1))
    with pytest.raises(FrameTimeout):
        cam.pull(bytearray(NBYTES), timeout=0.01)


def test_pull_before_start_is_an_error():
    cam = Camera(FakeLib())
    cam.open()
    with pytest.raises(CameraError):
        cam.pull(bytearray(NBYTES))


def test_sequence_gaps_are_counted_as_dropped():
    lib, cam = started()
    buf = bytearray(NBYTES)
    for seq in (1, 2, 5, 6, 10):
        lib.handle.fire(seq)
        cam.pull(buf)
    assert cam.dropped == (5 - 2 - 1) + (10 - 6 - 1)


def test_disconnect_event_raises_camera_error():
    lib, cam = started()
    lib.handle.fire(1, event=0x81)
    with pytest.raises(CameraError, match="disconnected"):
        cam.pull(bytearray(NBYTES))


def test_frame_rate():
    _, cam = started()
    assert cam.frame_rate() == pytest.approx(30.0)


def test_stop_and_close_are_idempotent():
    lib, cam = started()
    cam.stop()
    cam.stop()
    assert lib.names().count("Stop") == 1
    cam.close()
    cam.close()
    assert lib.names().count("Close") == 1
    assert not cam.is_open and cam.status == "closed"
    cam.stop()


def test_close_without_open_and_context_manager():
    Camera(FakeLib()).close()
    lib = FakeLib()
    with Camera(lib) as cam:
        assert cam.is_open
    assert "Close" in lib.names()


def test_start_pull_requires_open():
    with pytest.raises(CameraError):
        Camera(FakeLib()).start_pull(0, 1000)


def test_sim_produces_frames_at_declared_size():
    lib = SimToupcamLib(32, 16, 30.0, realtime=False)
    cam = Camera(lib, simulated=True)
    info = cam.open()
    assert info.resolutions == [(32, 16)] and cam.status == "simulated"
    cam.start_pull(0, 1000)
    buf = bytearray(cam.frame_bytes)
    assert len(buf) == frame_bytes(32, 16) == 32 * 16 * 3
    lib.Toupcam.handle.emit()
    f = cam.pull(buf)
    assert (f.width, f.height) == (32, 16)
    lib.Toupcam.handle.emit(2)
    assert cam.pull(buf).seq == 3 and cam.dropped == 1  # two events, one pull
    cam.close()


def test_sim_realtime_streams_frames():
    cam = Camera.sim(16, 8, 200.0)
    with cam:
        cam.start_pull(0, 1000)
        buf = bytearray(cam.frame_bytes)
        seqs = [cam.pull(buf, timeout=1.0).seq for _ in range(3)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3
