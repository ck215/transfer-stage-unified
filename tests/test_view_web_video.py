"""The trial video's Range route (`/api/video`, C-11 tail, 2026-10-11) and the
finalizer's player source: a real Transfer Map and Finalizer behind a real
WebView, driven over HTTP with no browser. The route serves one MP4 inside a
numbered trial folder of the map's output root and nothing else; it is gated
like `/api/image` (loopback and Host, the model must be open)."""
import urllib.parse

import pytest

from controller.controller import Controller
from model.finalizer import Finalizer
from model.transfer_map import TransferMap
from views.web.server import WebView

from test_view_web_file_open import _Setup
from test_view_web_server import _request

PAYLOAD = bytes(range(256)) * 4            # 1024 bytes, every byte distinct per offset mod 256
NAME = "Transfer Map"


@pytest.fixture
def station(tmp_path):
    controller = Controller()
    tm = TransferMap(db_path=tmp_path / "map.sqlite")
    trial = tm._store.insert({
        "started_at": "2026-10-06T10:00:00", "tip_id": "T", "tilt_deg": 7.0,
        "speed_steps_s": 200.0, "status": "recorded", "origin": "recorded",
        "sample_id": "S1", "chip_id": "1", "flake_id": "1", "cut_id": "1"})
    folder = tm.pictures_root / str(trial)
    folder.mkdir(parents=True)
    video = folder / "screen.mp4"
    video.write_bytes(PAYLOAD)
    tm._store.update(trial, {"video_path": str(video), "video_frames": 42})
    fin = Finalizer(sim=True)
    controller.add(NAME, tm)
    controller.add(Finalizer.NAME, fin)
    view = WebView(controller, _Setup(), port=0, open_browser=False)
    assert view.open()
    view.fin, view.tm, view.trial, view.root = fin, tm, trial, tmp_path
    try:
        yield view
    finally:
        view.close()
        controller.close()


def rel(view):
    return f"{view.tm.pictures_root.name}/{view.trial}/screen.mp4"


def get(view, path, range_=None, method="GET", name=NAME):
    headers = {"Range": range_} if range_ is not None else {}
    url = f"/api/video?name={urllib.parse.quote(name)}&path={urllib.parse.quote(path)}"
    return _request(view, url, method=method, headers=headers)


# -- Range ---------------------------------------------------------------
def test_no_range_is_the_whole_file_and_says_ranges_are_accepted(station):
    status, headers, body = get(station, rel(station))
    assert status == 200 and body == PAYLOAD
    assert headers["Accept-Ranges"] == "bytes" and headers["Content-Type"] == "video/mp4"
    assert headers["Content-Length"] == str(len(PAYLOAD))
    assert "Content-Range" not in headers


@pytest.mark.parametrize("spec, first, last", [
    ("bytes=0-99", 0, 99),
    ("bytes=100-199", 100, 199),
    ("bytes=1000-", 1000, 1023),
    ("bytes=0-", 0, 1023),
    ("bytes=-24", 1000, 1023),               # suffix: the last 24 bytes
    ("bytes=-5000", 0, 1023),                # a suffix longer than the file is the file
    ("bytes=1000-99999", 1000, 1023),        # an end past the file is clamped
    ("bytes=1023-1023", 1023, 1023),
])
def test_a_satisfiable_range_is_a_206_with_its_content_range(station, spec, first, last):
    status, headers, body = get(station, rel(station), spec)
    assert status == 206, spec
    assert body == PAYLOAD[first:last + 1]
    assert headers["Content-Range"] == f"bytes {first}-{last}/{len(PAYLOAD)}"
    assert headers["Content-Length"] == str(last - first + 1)
    assert headers["Accept-Ranges"] == "bytes"


@pytest.mark.parametrize("spec", [
    "bytes=1024-", "bytes=5000-6000",        # starts past the end
    "bytes=10-5",                            # backwards
    "bytes=-0",                              # an empty suffix
    "bytes=-", "bytes=", "bytes=abc-def",
    "bytes=0-1,5-9",                         # multiple ranges are not served
    "items=0-5",                             # not the bytes unit
    "0-5",
])
def test_a_bad_or_unsatisfiable_range_is_a_416_naming_the_size(station, spec):
    status, headers, _ = get(station, rel(station), spec)
    assert status == 416, spec
    assert headers["Content-Range"] == f"bytes */{len(PAYLOAD)}"


# -- refusals --------------------------------------------------------------
def test_path_traversal_and_absolute_paths_are_refused(station):
    secret = station.root.parent / "secret.mp4"
    secret.write_bytes(b"secret")
    folder = rel(station).rsplit("/", 1)[0]
    for path in (f"../{secret.name}", f"{folder}/../../../{secret.name}",
                 f"{folder}/..\\..\\{secret.name}", str(secret), "/etc/passwd.mp4",
                 f"{folder}/%2e%2e/screen.mp4"):
        assert get(station, path)[0] in (403, 404), path
        assert get(station, path)[2] != b"secret", path
    assert get(station, f"../{secret.name}")[0] == 403
    assert get(station, str(secret))[0] == 403


def test_a_symlink_out_of_the_store_is_refused(station):
    secret = station.root.parent / "outside.mp4"
    secret.write_bytes(b"secret")
    link = station.tm.pictures_root / str(station.trial) / "link.mp4"
    link.symlink_to(secret)
    status, _, body = get(station, f"{station.tm.pictures_root.name}/{station.trial}/link.mp4")
    assert status == 403 and body != b"secret"


def test_only_an_mp4_directly_inside_a_numbered_trial_folder_is_served(station):
    folder = station.tm.pictures_root / str(station.trial)
    (folder / "first_frame.png").write_bytes(b"\x89PNG")
    (folder / "notes.txt").write_text("x")
    other = station.tm.pictures_root / "exports"          # inside the root, not a trial folder
    other.mkdir(exist_ok=True)
    (other / "screen.mp4").write_bytes(b"not a trial")
    (station.root / "stray.mp4").write_bytes(b"not a trial")
    base = station.tm.pictures_root.name
    for path in (f"{base}/{station.trial}/first_frame.png", f"{base}/{station.trial}/notes.txt",
                 f"{base}/exports/screen.mp4", "stray.mp4", "map.sqlite"):
        assert get(station, path)[0] == 403, path
    assert get(station, f"{base}/{station.trial}/missing.mp4")[0] == 404


def test_the_model_must_be_open_and_the_host_must_be_the_stations(station):
    assert get(station, rel(station), name="Nobody")[0] == 404
    status, _, _ = _request(station, f"/api/video?name=Transfer%20Map&path={rel(station)}",
                            headers={"Host": "evil.example"})
    assert status == 403, "a DNS-rebound Host reaches no data route"


# -- the finalizer's player source -------------------------------------------
def test_the_finalizer_points_its_video_at_the_route_and_it_plays(station):
    src = station.fin.video_src
    assert src.startswith("/api/video?name=Transfer%20Map&path=")
    status, headers, body = _request(station, src, headers={"Range": "bytes=0-3"})
    assert status == 206 and body == PAYLOAD[:4]
    values = station.fin.state["values"]
    assert values["video_src"] == src
    assert "42 frames" in values["video_text"]
    element = next(e for e in station.fin.schema["sections"][3]["elements"]
                   if e.get("model_attr") == "video_text")
    assert element["video_attr"] == "video_src"


def test_no_video_leaves_the_fallback_words_and_no_source(station):
    station.tm._store.update(station.trial, {"video_path": ""})
    station.fin.reload()
    assert station.fin.video_src == ""
    assert station.fin.video_text == "This trial has no video."
    assert station.fin.state["values"]["video_src"] == ""


def test_a_folder_of_frames_is_named_not_played(station):
    frames = station.tm.pictures_root / str(station.trial) / "frames"
    frames.mkdir()
    (frames / "0001.jpg").write_bytes(b"\xff\xd8")
    station.tm._store.update(station.trial, {"video_path": str(frames)})
    station.fin.reload()
    assert station.fin.video_src == ""
    assert "JPEG frames" in station.fin.video_text
