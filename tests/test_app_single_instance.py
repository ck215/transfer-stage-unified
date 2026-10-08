"""One station per computer (owner 2026-10-07).

Two station processes left running fought over the serial ports: the old
one held every port, so a new launch's scan found no device, and the old one
was a heater nobody watched. A launch takes an OS lock under the data root;
a second launch finds it held, points the operator at the running station
and exits 0; a lock left by a dead process is taken over.

The real-process tests run `src/app.py` through a runner whose port listing
is empty: no test here ever opens a serial port.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

import pytest

import app
from controller import single_instance
from controller.controller import Controller
from events import events

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

#: `src/app.py` with an empty port listing: the Setup scan finds nothing, so
#: nothing a test starts can touch the bench's serial ports.
NO_PORTS_APP = (
    "import sys; sys.path.insert(0, {src!r}); "
    "import devices.serial_port as s; s.list_ports = lambda: []; "
    "import app; sys.argv[0] = {app!r}; sys.exit(app.main())"
).format(src=str(SRC), app=str(SRC / "app.py"))

#: Holds the lock in another process until its stdin closes.
HOLDER = (
    "import sys; sys.path.insert(0, {src!r}); "
    "from controller import single_instance as s; "
    "lock, info = s.acquire(sys.argv[1]); assert lock is not None; "
    "lock.set_url(sys.argv[2]); print('held', flush=True); sys.stdin.read()"
).format(src=str(SRC))


def _isolated_env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("STATION_ECHO_EVENTS", single_instance.RESTART_ENV)}
    env.update(TRANSFER_STAGE_DATA_ROOT=str(tmp_path / "data"),
               STATION_CONFIG=str(tmp_path / "station.json"),
               STATION_NO_UPDATE_CHECK="1", STATION_NO_FIRMWARE_CHECK="1",
               QT_QPA_PLATFORM="offscreen", HOME=str(tmp_path))
    env.update(extra)
    return env


def _holder(tmp_path, root, url="http://127.0.0.1:8123"):
    child = subprocess.Popen([sys.executable, "-c", HOLDER, str(root), url],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                             env=_isolated_env(tmp_path))
    assert child.stdout.readline().strip() == "held"
    return child


@pytest.fixture(autouse=True)
def no_restart_marker(monkeypatch):
    monkeypatch.delenv(single_instance.RESTART_ENV, raising=False)


# -- the lock itself ---------------------------------------------------------

def test_a_free_lock_is_taken_and_says_who_holds_it(tmp_path):
    lock, info = single_instance.acquire(str(tmp_path))
    try:
        assert lock is not None and lock.held and info is None
        assert single_instance.read_info(str(tmp_path))["pid"] == os.getpid()
        lock.set_url("http://127.0.0.1:8090")
        assert single_instance.read_info(str(tmp_path))["url"] == "http://127.0.0.1:8090"
    finally:
        lock.release()
    assert single_instance.read_info(str(tmp_path)) == {}, "the record outlived the lock"
    again, _ = single_instance.acquire(str(tmp_path))
    assert again is not None, "a released lock could not be taken again"
    again.release()


def test_a_second_process_finds_the_lock_held_and_reads_the_address(tmp_path):
    root = tmp_path / "data"
    child = _holder(tmp_path, root)
    try:
        lock, info = single_instance.acquire(str(root))
        assert lock is None, "two processes held the one-station lock"
        assert info == {"pid": child.pid, "url": "http://127.0.0.1:8123",
                        "started": info["started"]}
    finally:
        child.stdin.close()
        child.wait(timeout=10)
    lock, _ = single_instance.acquire(str(root))
    assert lock is not None, "the holder exited and its lock was not free"
    lock.release()


def test_a_killed_holders_lock_is_taken_over(tmp_path):
    """SIGKILL runs no exit path: the record stays, the OS lock does not."""
    root = tmp_path / "data"
    child = _holder(tmp_path, root)
    child.send_signal(signal.SIGKILL)
    child.wait(timeout=10)
    assert single_instance.read_info(str(root))["pid"] == child.pid, "the stale record"
    lock, info = single_instance.acquire(str(root))
    try:
        assert lock is not None and info is None
        assert f"taken over from PID {child.pid}" in lock.note
        assert single_instance.read_info(str(root))["pid"] == os.getpid()
    finally:
        lock.release()


def test_a_stale_record_with_no_lock_is_taken_over(tmp_path):
    (tmp_path / single_instance.LOCK_NAME).write_text("")
    (tmp_path / single_instance.INFO_NAME).write_text(
        json.dumps({"pid": 999999, "url": "http://127.0.0.1:9"}))
    lock, info = single_instance.acquire(str(tmp_path))
    assert lock is not None and info is None
    lock.release()


def test_a_restart_waits_for_the_process_it_replaces(tmp_path, monkeypatch):
    """Windows starts the restarted run before the old one has exited: the
    new run waits for that PID's lock instead of calling it a second
    station. (On macOS and Linux the exec keeps the PID and drops the lock.)"""
    old, _ = single_instance.acquire(str(tmp_path))
    monkeypatch.setenv(single_instance.RESTART_ENV, str(os.getpid()))
    threading.Timer(0.5, old.release).start()
    started = time.monotonic()
    new, info = single_instance.acquire(str(tmp_path))
    try:
        assert new is not None and info is None
        assert time.monotonic() - started >= 0.4
        assert single_instance.RESTART_ENV not in os.environ, "the marker outlived its use"
    finally:
        new.release()


def test_a_lock_that_cannot_be_made_does_not_stop_the_launch(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    lock, info = single_instance.acquire(str(blocker / "below-a-file"))
    assert lock is not None and not lock.held and info is None
    assert "would not be refused" in lock.note
    lock.set_url("http://127.0.0.1:1")      # harmless
    lock.release()


# -- launch() and main() -----------------------------------------------------

@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """launch() with no hardware: no exit hooks, no scan, no gamepad."""
    monkeypatch.setattr(Controller, "_hook_exit", lambda self: None)
    monkeypatch.setattr(Controller, "hook_signals", lambda self: None)
    monkeypatch.setattr(events, "hook_exceptions", lambda: None)
    monkeypatch.setattr(app.Setup, "start", lambda self: None)
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path / "data"))
    opened = []
    monkeypatch.setattr(app.webbrowser, "open", opened.append)
    yield tmp_path / "data", opened
    events.close_file()


def test_launch_holds_the_lock_while_the_view_runs_and_records_its_address(
        isolated, monkeypatch):
    root, _ = isolated
    seen = {}

    class View:
        url = "http://127.0.0.1:8555"

        def __init__(self, controller, setup, **kwargs):
            pass

        def open(self):
            pass

        def wait(self):
            seen["info"] = single_instance.read_info(str(root))
            seen["second"] = single_instance.acquire(str(root))

    module = types.ModuleType("views.locked")
    module.View = View
    monkeypatch.setitem(sys.modules, "views.locked", module)
    monkeypatch.setattr(app, "VIEWS", {"web": ("views.locked", "View")})
    assert isinstance(app.launch("web", open_browser=False), View)
    assert seen["info"]["url"] == "http://127.0.0.1:8555"
    assert seen["info"]["pid"] == os.getpid()
    assert seen["second"][0] is None, "the lock was not held while the view ran"
    lock, _ = single_instance.acquire(str(root))
    assert lock is not None, "launch() did not release the lock when the view closed"
    lock.release()


def test_a_second_launch_opens_the_running_station_and_ends_with_0(
        isolated, tmp_path, capsys, monkeypatch):
    root, opened = isolated
    built = []
    monkeypatch.setattr(app, "_launch", lambda *a, **k: built.append(a))
    child = _holder(tmp_path, root, url="http://127.0.0.1:8082")
    try:
        assert app.main(["--port", "8080"]) == 0
        out = capsys.readouterr().out
        assert out == (f"The station is already running at http://127.0.0.1:8082 "
                       f"(PID {child.pid}); opened it in the browser.\n")
        assert opened == ["http://127.0.0.1:8082"]
        assert built == [], "a second station was started"
        assert app.main(["--no-browser"]) == 0
        out = capsys.readouterr().out
        assert out == (f"The station is already running at http://127.0.0.1:8082 "
                       f"(PID {child.pid}); open that address in the browser.\n")
        assert opened == ["http://127.0.0.1:8082"], "--no-browser opened a browser"
    finally:
        child.stdin.close()
        child.wait(timeout=10)


# -- two real station processes ----------------------------------------------

def _post(port, route, body):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{route}", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_regression_20261007_a_closed_tab_ends_the_process_and_frees_the_next_launch(
        tmp_path):
    """Bench 2026-10-07 23:02-23:21: the page's last heartbeat was 23:14:39
    (tab closed), the station ran on holding every port until the lead quit
    it at 23:21:40, and the 23:18 launch found no device. Now: the closed
    tab's leave, then no page within the grace, ends the process by the Quit
    path (exit 0, its log saying why), and a launch meanwhile points at it."""
    port, other = _free_port(), _free_port()
    env = _isolated_env(tmp_path, STATION_LEAVE_GRACE_SECONDS="2")
    first = subprocess.Popen([sys.executable, "-c", NO_PORTS_APP, "--no-browser",
                              "--port", str(port)], env=env, cwd=str(tmp_path),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert first.stdout.readline().strip() == f"Station served at http://127.0.0.1:{port}"
        for _ in range(3):                      # the page, checking in
            assert _post(port, "/api/heartbeat", {"page": "p1", "hidden": False})["status"] == "ok"
            time.sleep(0.3)
        # 23:18: a second launch while the first one runs.
        second = subprocess.run([sys.executable, "-c", NO_PORTS_APP, "--no-browser",
                                 "--port", str(other)], env=env, cwd=str(tmp_path),
                                capture_output=True, text=True, timeout=60)
        assert second.returncode == 0 and "already running" in second.stdout, second
        # 23:14:39: the tab closes.
        closed = time.monotonic()
        assert _post(port, "/api/leave", {"page": "p1"}) == {"status": "ok", "quit_in": 2.0}
        first.communicate(timeout=30)
        took = time.monotonic() - closed
        assert first.returncode == 0, "the process did not end in an orderly quit"
        assert 1.5 <= took < 12, took
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate()
    log = "".join(p.read_text() for p in (tmp_path / "data" / "logs").glob("station-*.log"))
    assert "Tab Closed" in log and "No Browser Left" in log and "Quit:" in log, log[-3000:]
    # The next launch is the station again, not a second one.
    lock, info = single_instance.acquire(str(tmp_path / "data"))
    assert lock is not None, info
    lock.release()

def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _quit(port):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/quit", data=b"{}", method="POST",
        headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
    urllib.request.urlopen(request, timeout=5).read()


def test_a_second_real_launch_points_at_the_first_and_exits_0(tmp_path):
    port, other = _free_port(), _free_port()
    env = _isolated_env(tmp_path)
    first = subprocess.Popen([sys.executable, "-c", NO_PORTS_APP, "--no-browser",
                              "--port", str(port)], env=env, cwd=str(tmp_path),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert first.stdout.readline().strip() == f"Station served at http://127.0.0.1:{port}"
        second = subprocess.run([sys.executable, "-c", NO_PORTS_APP, "--no-browser",
                                 "--port", str(other)], env=env, cwd=str(tmp_path),
                                capture_output=True, text=True, timeout=60)
        assert second.returncode == 0, second.stderr
        assert second.stdout == (f"The station is already running at "
                                 f"http://127.0.0.1:{port} (PID {first.pid}); open that "
                                 f"address in the browser.\n")
        assert second.stderr == ""
        with pytest.raises(OSError):        # it never served
            urllib.request.urlopen(f"http://127.0.0.1:{other}/api/setup", timeout=2)
        assert first.poll() is None, "the second launch disturbed the first"
        _quit(port)
        first.communicate(timeout=30)
        assert first.returncode == 0
        # The first one quit: the next launch is the station again.
        third = subprocess.Popen([sys.executable, "-c", NO_PORTS_APP, "--no-browser",
                                  "--port", str(other)], env=env, cwd=str(tmp_path),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert third.stdout.readline().strip() == f"Station served at http://127.0.0.1:{other}"
            _quit(other)
            third.communicate(timeout=30)
        finally:
            if third.poll() is None:
                third.kill()
                third.communicate()
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate()
