"""The launchers (owner, 2026-09-28: "Consolidate the various run scripts,
using OS detection, and retire any system that doesn't use the SWAP check").

Two launchers: `run.sh` for every POSIX OS (no OS branch since the Qt view
was retired, 2026-10-07) and `run.bat` for Windows. Both
find the venv and run `src/app.py` with every argument, and print nothing
when all is well: the firmware check is a Setup row now. The old names
(`run_macos.sh`, `run_swap_macos.sh`, `run_swap.sh`) are gone (owner,
2026-09-28: consolidate now, not after a grace period); `dev/swap_branch.sh`
is the developer tool that runs the `legacy` branch's app.

Every script here runs against stubs: a `python3` that records its argv and
runs nothing, a `uname` that says what the test wants, a `chflags` that
records. Nothing is launched, installed or flashed.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") or "/bin/bash"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX launchers")

STUB_PYTHON = f"""#!{sys.executable}
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps(["python3", *sys.argv[1:]]) + "\\n")
sys.exit(0)
"""


def _executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def bench(tmp_path):
    """A copy of the launchers in a tree with no .venv, a fake venv whose
    python3 records its argv, and stub `uname` / `chflags` first on PATH."""
    tree = tmp_path / "tree"
    (tree / "dev").mkdir(parents=True)
    shutil.copy2(REPO / "run.sh", tree / "run.sh")
    shutil.copy2(REPO / "dev" / "swap_branch.sh", tree / "dev" / "swap_branch.sh")
    shutil.copy2(REPO / "dev" / "launch_desktop.sh", tree / "dev" / "launch_desktop.sh")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    _executable(venv / "bin" / "python3", STUB_PYTHON)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _executable(stubs / "uname", '#!/bin/sh\necho "$FAKE_UNAME"\n')
    _executable(stubs / "chflags", f'#!/bin/sh\necho chflags "$@" >> "{tmp_path}/chflags.log"\n')
    log = tmp_path / "argv.log"
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "STUB_LOG": str(log),
           "VIRTUAL_ENV": str(venv), "FAKE_UNAME": "Darwin"}

    class Bench:
        pass

    b = Bench()
    b.tree, b.venv, b.env, b.log, b.tmp = tree, venv, env, log, tmp_path

    def run(script, *args, **env_overrides):
        env_run = dict(b.env, **env_overrides)
        env_run = {k: v for k, v in env_run.items() if v is not None}
        done = subprocess.run([BASH, str(tree / script), *args], env=env_run,
                              capture_output=True, text=True, timeout=60,
                              cwd=str(tmp_path))
        calls = ([json.loads(line) for line in log.read_text().splitlines()]
                 if log.exists() else [])
        if log.exists():
            log.unlink()
        return done, calls

    b.run = run
    return b


APP_HELP = ["python3", "src/app.py", "--help"]


# -- the files --------------------------------------------------------------

@pytest.mark.parametrize("script", ["run.sh", "dev/swap_branch.sh", "update.sh"])
def test_every_launcher_parses(script):
    done = subprocess.run([BASH, "-n", str(REPO / script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_two_launchers_and_the_updaters_are_all_that_is_left_at_the_root():
    scripts = {p.name for p in REPO.iterdir() if p.suffix in (".sh", ".bat")}
    assert scripts == {"run.sh", "run.bat", "update.sh", "update.bat"}
    for old in ("run_swap.sh", "run_macos.sh", "run_swap_macos.sh"):
        assert not (REPO / old).exists(), old


def test_the_launchers_are_executable():
    for script in ("run.sh", "dev/swap_branch.sh", "update.sh"):
        assert os.access(REPO / script, os.X_OK), script


def test_swap_branch_says_it_is_a_developer_tool():
    header = (REPO / "dev" / "swap_branch.sh").read_text().split("\nset -u", 1)[0]
    assert "developer tool" in header
    assert "legacy" in header and "flash" in header


def test_run_bat_finds_the_venv_and_runs_the_app_silently():
    """Not runnable here; its shape is pinned instead (UNVERIFIED on Windows)."""
    text = (REPO / "run.bat").read_text()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    assert lines[0].lower() == "@echo off"
    assert r"python src\app.py %*" in text
    assert r".venv\Scripts\activate.bat" in text and "VIRTUAL_ENV" in text
    # The only echo is the failure message, to stderr.
    echoes = [l for l in lines[1:] if l.lower().startswith("echo")]
    assert echoes and all(l.endswith("1>&2") for l in echoes)
    assert "flash" not in text.lower()


# -- run.sh, driven ---------------------------------------------------------

@pytest.mark.parametrize("uname", ["Darwin", "Linux"])
def test_help_reaches_the_app_and_prints_nothing_else(bench, uname):
    done, calls = bench.run("run.sh", "--help", FAKE_UNAME=uname)
    assert done.returncode == 0, done.stderr
    assert (done.stdout, done.stderr) == ("", "")
    assert calls == [APP_HELP]


@pytest.mark.parametrize("uname", ["Darwin", "Linux"])
def test_the_default_launch_is_just_the_app_on_every_os(bench, uname):
    """Web is the only view and the default (2026-10-07): no PySide6 check, no
    reinstall, no dylib unhide step, on macOS or anywhere else."""
    done, calls = bench.run("run.sh", FAKE_UNAME=uname)
    assert done.returncode == 0, done.stderr
    assert (done.stdout, done.stderr) == ("", "")
    assert calls == [["python3", "src/app.py"]]
    assert not (bench.tmp / "chflags.log").exists()


@pytest.mark.parametrize("flags", [["--web"], ["--qt"], ["--tk"], ["--pyside"],
                                   ["--view", "qt"], ["--view=tk"]])
def test_every_flag_goes_to_the_app_untouched_with_no_qt_repair(bench, flags):
    """The retired-view message is the app's to print, not the launcher's."""
    done, calls = bench.run("run.sh", *flags, FAKE_UNAME="Darwin",
                            STUB_QT_BROKEN="1")
    assert (done.stdout, done.stderr) == ("", "")
    assert calls == [["python3", "src/app.py", *flags]]
    assert not (bench.tmp / "chflags.log").exists()


def test_the_launchers_no_longer_mention_pyside_or_qt_repair():
    for name in ("run.sh", "run.bat", "update.sh", "update.bat"):
        text = (REPO / name).read_text().lower()
        assert "pyside" not in text and "chflags" not in text, name
        assert "[qt]" not in text, name


def test_arguments_pass_through_intact(bench):
    done, calls = bench.run("run.sh", "--web", "--no-browser", "--map-db",
                            "/tmp/a dir/x.sqlite", FAKE_UNAME="Linux")
    assert calls == [["python3", "src/app.py", "--web", "--no-browser",
                      "--map-db", "/tmp/a dir/x.sqlite"]]


def test_no_venv_is_the_one_message(bench):
    done, calls = bench.run("run.sh", "--help", VIRTUAL_ENV=None)
    assert done.returncode == 1
    assert done.stdout == ""
    assert "venv" in done.stderr and len(done.stderr.strip().splitlines()) == 1
    assert calls == []


def test_a_local_venv_wins_over_the_active_one(bench):
    local = bench.tree / ".venv" / "bin"
    local.mkdir(parents=True)
    _executable(local / "python3", STUB_PYTHON.replace('["python3", ', '["local-python3", '))
    (local / "activate").write_text(
        f'VIRTUAL_ENV="{bench.tree}/.venv"; export VIRTUAL_ENV\n'
        f'PATH="{local}:$PATH"; export PATH\n')
    done, calls = bench.run("run.sh", "--help")
    assert (done.stdout, done.stderr) == ("", "")
    assert calls == [["local-python3", "src/app.py", "--help"]]


def test_the_launcher_runs_from_any_directory(bench):
    # `bench.run` starts every script from tmp_path, not from the tree.
    done, calls = bench.run("run.sh", "--help")
    assert calls == [APP_HELP]


# -- dev/swap_branch.sh ------------------------------------------------------
# Real git in tmp_path (a bare origin, a station repo with or without a local
# `legacy` branch), a stub python3 that records its argv. Nothing is cloned
# outside tmp_path, flashed or launched; every run here is a dry run unless
# it says otherwise.

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd, *args, home):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, **GIT_ENV, "HOME": str(home)})


@pytest.fixture
def swap(bench):
    """w/origin.git (bare, with main and legacy), w/with-legacy (a station
    repo that has a local `legacy` branch) and w/clone-only (a clone of main
    that only knows origin/legacy). Both carry the real script."""
    w = bench.tmp / "w"
    w.mkdir()
    seed, origin = w / "seed", w / "origin.git"
    seed.mkdir()
    h = bench.tmp
    _git(seed, "init", "-q", "-b", "main", home=h)
    (seed / "dev").mkdir()
    shutil.copy2(REPO / "dev" / "swap_branch.sh", seed / "dev" / "swap_branch.sh")
    _git(seed, "add", "-A", home=h)
    _git(seed, "commit", "-qm", "station", home=h)
    _git(seed, "checkout", "-q", "-b", "legacy", home=h)
    (seed / "src").mkdir()
    (seed / "src" / "mainGUI.py").write_text("")
    (seed / "firmware").mkdir()
    (seed / "firmware" / "flash_firmware.py").write_text("")
    _git(seed, "add", "-A", home=h)
    _git(seed, "commit", "-qm", "legacy", home=h)
    _git(w, "init", "-q", "--bare", str(origin), home=h)
    _git(seed, "push", "-q", str(origin), "main", "legacy", home=h)
    _git(w, "clone", "-q", "--branch", "main", str(origin), "with-legacy", home=h)
    _git(w / "with-legacy", "branch", "legacy", "origin/legacy", home=h)
    _git(w, "clone", "-q", "--branch", "main", str(origin), "clone-only", home=h)
    bench.w = w
    bench.origin = origin
    bench.env.update(GIT_ENV)
    bench.env["PATH"] = bench.env["PATH"] + ":" + os.path.dirname(shutil.which("git"))

    def go(repo, *args, **env):
        return bench.run(f"../w/{repo}/dev/swap_branch.sh", *args, RUN_SWAP_DRY_RUN="1",
                         **env)

    bench.go = go
    return bench


def test_swap_legacy_dry_run_adds_a_worktree_and_runs_nothing(swap):
    done, calls = swap.go("with-legacy", "legacy")
    assert done.returncode == 0, done.stderr
    assert calls == [] and done.stderr == ""
    legacy = swap.w / "legacy-app"
    assert not legacy.exists()
    lines = done.stdout.splitlines()
    assert lines[0].startswith("+ git --no-optional-locks -C ")
    assert f"worktree add {legacy} legacy" in lines[0]
    assert f"+ python3 -m venv {legacy}/.venv" in lines
    pip = next(l for l in lines if "-m pip install" in l)
    assert pip.startswith(f"+ {legacy}/.venv/bin/python -m pip install --quiet pyserial==3.5")
    # The station's flasher, over the legacy tree's sketches, recorded as
    # `stable`, the Megas only (2026-10-08: the legacy tree's own flasher
    # never wrote the stamp, so the Launcher then started the station on
    # boards still running the legacy sketches).
    repo = swap.w / "with-legacy"
    assert (f"+ {swap.venv}/bin/python3 {repo}/firmware/flash_firmware.py --yes "
            f"--sketch-root {legacy}/firmware --channel stable "
            "--only Stepper\\ Probe DC\\ Probe Chuck\\ Positioner") in lines
    assert f"{legacy}/firmware/flash_firmware.py" not in done.stdout
    assert lines[-2] == f"+ cd {legacy}"
    assert lines[-1] == f"+ {legacy}/.venv/bin/python src/mainGUI.py"
    assert lines.index(next(l for l in lines if "flash_firmware" in l)) < len(lines) - 2


def test_swap_legacy_dry_run_clones_when_there_is_no_local_branch(swap):
    done, calls = swap.go("clone-only", "legacy", "--no-flash", "--", "--x", "a b")
    assert done.returncode == 0, done.stderr
    assert calls == []
    legacy = swap.w / "legacy-app"
    assert not legacy.exists()
    lines = done.stdout.splitlines()
    assert lines[0] == (f"+ git --no-optional-locks clone --quiet --branch legacy "
                        f"{swap.origin} {legacy}")
    assert "flash_firmware" not in done.stdout
    assert lines[-1] == f"+ {legacy}/.venv/bin/python src/mainGUI.py --x a\\ b"
    # the checkout itself is untouched: still on main
    head = subprocess.run(["git", "-C", str(swap.w / "clone-only"), "branch", "--show-current"],
                          capture_output=True, text=True).stdout.strip()
    assert head == "main"


def test_swap_legacy_with_a_finished_tree_only_flashes_and_launches(swap):
    legacy = swap.w / "legacy-app"
    (legacy / "src").mkdir(parents=True)
    (legacy / "src" / "mainGUI.py").write_text("")
    (legacy / ".venv" / "bin").mkdir(parents=True)
    _executable(legacy / ".venv" / "bin" / "python", "#!/bin/sh\n")
    done, _ = swap.go("with-legacy", "legacy", STATION_FLASH_ONLY="Stepper Probe, Chuck Positioner")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert len(lines) == 3
    repo = swap.w / "with-legacy"
    assert lines[0] == (f"+ {swap.venv}/bin/python3 {repo}/firmware/flash_firmware.py --yes "
                        f"--sketch-root {legacy}/firmware --channel stable "
                        "--only Stepper\\ Probe Chuck\\ Positioner")
    assert "worktree" not in done.stdout and "-m venv" not in done.stdout
    assert "pip" not in done.stdout


def test_swap_legacy_flashes_from_the_station_venv(swap):
    """The flash is the station's (it keeps the stamp), so it needs the
    station's venv; running the legacy app alone does not."""
    legacy = swap.w / "legacy-app"
    (legacy / "src").mkdir(parents=True)
    (legacy / "src" / "mainGUI.py").write_text("")
    (legacy / ".venv" / "bin").mkdir(parents=True)
    _executable(legacy / ".venv" / "bin" / "python", "#!/bin/sh\n")
    done, calls = swap.go("with-legacy", "legacy", VIRTUAL_ENV=None)
    assert done.returncode == 1 and done.stdout == "" and calls == []
    assert done.stderr.startswith("swap_branch: ") and "no .venv" in done.stderr
    assert len(done.stderr.strip().splitlines()) == 1
    done, _ = swap.go("with-legacy", "legacy", "--no-flash", VIRTUAL_ENV=None)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-1] == f"+ {legacy}/.venv/bin/python src/mainGUI.py"


def test_swap_legacy_stops_when_the_flash_fails(swap, tmp_path):
    legacy = swap.w / "legacy-app"
    (legacy / "src").mkdir(parents=True)
    (legacy / "src" / "mainGUI.py").write_text("")
    (legacy / ".venv" / "bin").mkdir(parents=True)
    _executable(legacy / ".venv" / "bin" / "python", "#!/bin/sh\n")
    failing = tmp_path / "failvenv"
    (failing / "bin").mkdir(parents=True)
    _executable(failing / "bin" / "python3", "#!/bin/sh\necho boom\nexit 3\n")
    done, _ = swap.run("../w/with-legacy/dev/swap_branch.sh", "legacy",
                       VIRTUAL_ENV=str(failing))
    assert done.returncode == 1
    err = done.stderr.strip().splitlines()
    assert err[0] == "boom"
    assert err[-1].startswith("swap_branch: flashing failed, so the legacy app was not launched")


def test_swap_station_dry_run_flashes_in_process_then_runs_the_web_view(swap):
    done, calls = swap.go("with-legacy", "station", "--", "--port", "8080")
    assert done.returncode == 0, done.stderr
    assert calls == []
    repo = swap.w / "with-legacy"
    out = done.stdout
    assert f"+ env PYTHONPATH={repo}/src {swap.venv}/bin/python3 -c " in out
    assert "from controller import flashing" in out and "f.flash(" in out
    assert out.splitlines()[-2] == f"+ cd {repo}"
    assert out.splitlines()[-1] == f"+ {swap.venv}/bin/python3 src/app.py --web --port 8080"
    assert "legacy-app" not in out


def test_swap_station_no_flash_prints_only_the_launch(swap):
    done, _ = swap.go("with-legacy", "station", "--no-flash")
    assert done.returncode == 0, done.stderr
    assert len(done.stdout.splitlines()) == 2 and "flashing" not in done.stdout


def test_swap_station_for_real_is_silent_and_flashes_before_it_launches(swap):
    """The stub python3 records argv: the flash `-c` first, then the app."""
    done, calls = swap.run("../w/with-legacy/dev/swap_branch.sh", "station", "--", "--x")
    assert done.returncode == 0, done.stderr
    assert done.stdout == "" and done.stderr == ""
    assert [c[1] for c in calls] == ["-c", "src/app.py"]
    assert calls[1][2:] == ["--web", "--x"]


def test_swap_station_stops_when_the_flash_fails(swap, tmp_path):
    failing = tmp_path / "failvenv"
    (failing / "bin").mkdir(parents=True)
    _executable(failing / "bin" / "python3", "#!/bin/sh\necho boom\nexit 3\n")
    done, _ = swap.run("../w/with-legacy/dev/swap_branch.sh", "station",
                       VIRTUAL_ENV=str(failing))
    assert done.returncode == 1
    err = done.stderr.strip().splitlines()
    assert err[0] == "boom"
    assert err[-1].startswith("swap_branch: flashing failed, so the station was not launched")


@pytest.mark.parametrize("args, sentence", [
    ((), "usage:"),
    (("main",), "usage:"),
    (("legacy", "--force-flash"), "unknown argument '--force-flash'"),
    (("station", "stray"), "unknown argument 'stray'"),
])
def test_swap_refuses_a_bad_command_line_in_one_sentence(swap, args, sentence):
    done, calls = swap.go("with-legacy", *args)
    assert done.returncode == 1 and done.stdout == "" and calls == []
    assert done.stderr.startswith("swap_branch: ") and sentence in done.stderr
    assert len(done.stderr.strip().splitlines()) == 1


def test_swap_refuses_to_run_inside_the_legacy_tree(swap):
    legacy = swap.w / "legacy-app"
    (legacy / "dev").mkdir(parents=True)
    shutil.copy2(REPO / "dev" / "swap_branch.sh", legacy / "dev" / "swap_branch.sh")
    done, _ = swap.go("legacy-app", "station")
    assert done.returncode == 1 and done.stdout == ""
    assert "legacy app's own tree" in done.stderr
    # also when it was checked out under another name
    (swap.w / "with-legacy" / "src").mkdir()
    (swap.w / "with-legacy" / "src" / "mainGUI.py").write_text("")
    done, _ = swap.go("with-legacy", "legacy")
    assert done.returncode == 1 and "legacy app's own tree" in done.stderr


def test_swap_refuses_without_git(swap, tmp_path):
    bare_path = tmp_path / "nogit"
    bare_path.mkdir()
    for tool in ("dirname", "basename", "sed", "env", "uname"):
        found = shutil.which(tool)
        if found:
            (bare_path / tool).symlink_to(found)
    done, _ = swap.go("with-legacy", "legacy", PATH=str(bare_path))
    assert done.returncode == 1 and done.stdout == ""
    assert done.stderr.strip() == ("swap_branch: git is not installed, so the legacy "
                                   "checkout cannot be made.")


def test_swap_refuses_when_there_is_neither_a_branch_nor_an_origin(swap):
    lone = swap.w / "lone"
    (lone / "dev").mkdir(parents=True)
    shutil.copy2(REPO / "dev" / "swap_branch.sh", lone / "dev" / "swap_branch.sh")
    done, _ = swap.go("lone", "legacy")
    assert done.returncode == 1 and done.stdout == ""
    assert "no legacy branch and no origin" in done.stderr


def test_swap_refuses_a_legacy_app_folder_that_is_not_the_branch(swap):
    (swap.w / "legacy-app").mkdir()
    done, _ = swap.go("with-legacy", "legacy")
    assert done.returncode == 1 and "is not a checkout of the legacy branch" in done.stderr


def test_swap_station_refuses_without_a_venv(swap):
    done, _ = swap.go("with-legacy", "station", VIRTUAL_ENV=None)
    assert done.returncode == 1 and "no .venv" in done.stderr


def test_swap_legacy_pins_match_the_station_pins():
    """The legacy venv is filled from the script's pins (the legacy branch has
    no requirements file): they must be the versions pyproject.toml and
    packaging/requirements-stable.txt pin."""
    text = (REPO / "dev" / "swap_branch.sh").read_text()
    pins = re.search(r"LEGACY_PINS=\((.*?)\)", text).group(1).split()
    declared = (REPO / "pyproject.toml").read_text() + (
        REPO / "packaging" / "requirements-stable.txt").read_text()
    for pin in pins:
        name, version = pin.split("==")
        assert re.search(rf'{name}\s*==\s*{re.escape(version)}', declared, re.I), pin


# -- nothing else prints (L3; lead's ruling 2026-09-28) -----------------------
# The log file is the record and the tray is the operator's view: info and
# warning events no longer echo to the terminal unless STATION_ECHO_EVENTS=1.
# Errors still reach stderr.

@pytest.fixture
def fresh_log():
    from events import EventLog
    return EventLog()


def test_info_and_warning_events_are_silent_on_the_terminal(fresh_log, capsys, monkeypatch):
    monkeypatch.delenv("STATION_ECHO_EVENTS", raising=False)
    fresh_log.info("Log File", "/tmp/x.log", source="app")
    fresh_log.warn("Firmware Not Flashed", "DC Probe still needs flashing.", source="Setup")
    assert capsys.readouterr() == ("", "")


def test_the_echo_comes_back_with_station_echo_events(fresh_log, capsys, monkeypatch):
    monkeypatch.setenv("STATION_ECHO_EVENTS", "1")
    fresh_log.info("Log File", "/tmp/x.log", source="app")
    fresh_log.warn("Careful", "a warning", source="app")
    out = capsys.readouterr().out
    assert "Log File" in out and "Careful" in out


def test_an_error_still_reaches_stderr(fresh_log, capsys, monkeypatch):
    monkeypatch.delenv("STATION_ECHO_EVENTS", raising=False)
    fresh_log.error("Web Server Failed", "cannot bind", source="web", ack=False)
    captured = capsys.readouterr()
    assert captured.out == "" and "Web Server Failed" in captured.err


def _served_web(tmp_path, *flags):
    """A real headless Web launch, SIM only, on a free port; returns what it
    printed before Quit."""
    import socket
    import urllib.request
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if k != "STATION_ECHO_EVENTS"}
    env.update(TRANSFER_STAGE_DATA_ROOT=str(tmp_path), STATION_NO_UPDATE_CHECK="1",
               STATION_NO_FIRMWARE_CHECK="1", QT_QPA_PLATFORM="offscreen")
    env.pop("STATION_RESTART_OF", None)
    # src/app.py with an empty port listing (2026-10-07): the Setup scan of a
    # test launch must never reach the bench's serial ports, which a station
    # in use may hold.
    no_ports = ("import sys; sys.path.insert(0, {src!r}); "
                "import devices.serial_port as s; s.list_ports = lambda: []; "
                "import app; sys.argv[0] = {app!r}; sys.exit(app.main())").format(
                    src=str(REPO / "src"), app=str(REPO / "src" / "app.py"))
    child = subprocess.Popen([sys.executable, "-c", no_ports, "--web",
                              "--port", str(port), *flags],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, cwd=str(tmp_path))
    try:
        import time
        deadline = time.monotonic() + 30
        setup = None
        while time.monotonic() < deadline and setup is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/setup", timeout=2) as r:
                    setup = json.loads(r.read())
            except OSError:
                time.sleep(0.2)
        assert setup is not None, "the Web view never served"
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/quit", data=b"{}",
                                         headers={"Content-Type": "application/json",
                                                  "Origin": f"http://127.0.0.1:{port}"},
                                         method="POST")
        urllib.request.urlopen(request, timeout=5).read()
        out, err = child.communicate(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
    return setup, out, err, port


def test_a_headless_web_launch_prints_only_its_address(tmp_path):
    setup, out, err, port = _served_web(tmp_path, "--no-browser")
    assert out.strip().splitlines() == [f"Station served at http://127.0.0.1:{port}"]
    assert err == ""
    titles = [s["title"] for s in setup["schema"]["sections"]]
    # Devices first, upkeep after Launch, then the defaults (accounts on by
    # default, 2026-10-07; order from the UX audit 2026-10-08).
    assert titles[0] == "Devices"
    assert titles[-3:] == ["Update", "Firmware", "Station defaults"]
    assert setup["state"]["values"]["web_address"] == f"http://127.0.0.1:{port}"


# -- the desktop icons (Linux station PC) -----------------------------------------
# Bench 2026-09-28: "Transfer Stage Launcher" had gone from the desktop and
# "Transfer Stage Classic" still ran run_swap.sh, which is dev/swap_branch.sh
# now. dev/desktop_shortcuts.sh rewrites both; the icons run
# dev/launch_desktop.sh, since a .desktop Exec line may hold no shell.

linux_only = pytest.mark.skipif(sys.platform != "linux", reason=".desktop files")


@linux_only
def test_desktop_shortcuts_point_at_scripts_that_exist(tmp_path):
    done = subprocess.run([BASH, str(REPO / "dev" / "desktop_shortcuts.sh"), str(tmp_path)],
                          capture_output=True, text=True, env={**os.environ, "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr
    files = {p.name: p.read_text() for p in tmp_path.glob("*.desktop")}
    assert set(files) == {"Transfer Stage Launcher.desktop", "Transfer Stage Classic.desktop"}
    for name, text in files.items():
        exec_line = next(l for l in text.splitlines() if l.startswith("Exec="))
        script = Path(exec_line[len("Exec="):].split()[0])
        assert script == REPO / "dev" / "launch_desktop.sh", exec_line
        assert script.exists() and os.access(script, os.X_OK)
        assert "run_swap.sh" not in text
        assert "Terminal=false" in text
        assert os.access(tmp_path / name, os.X_OK)
    assert files["Transfer Stage Classic.desktop"].count("launch_desktop.sh classic") == 1
    if shutil.which("desktop-file-validate"):
        checked = subprocess.run(["desktop-file-validate", *map(str, tmp_path.glob("*.desktop"))],
                                 capture_output=True, text=True)
        assert checked.returncode == 0 and checked.stdout == "", checked.stdout


@linux_only
def test_the_launcher_icon_runs_run_sh_silently_and_keeps_a_failure_in_the_log(bench):
    """The icon's script runs run.sh (the stubbed python records the launch,
    nothing starts) and opens no terminal and no dialog when all is well; a
    failing run.sh leaves its message in ~/transfer-stage-runs/launcher.log
    and shows it through zenity, stubbed here so nothing reaches the screen."""
    stubs = bench.tmp / "stubs"
    shown = bench.tmp / "zenity.log"
    _executable(stubs / "zenity", f'#!/bin/sh\ncat >> "{shown}"\n')
    _executable(stubs / "xmessage", f'#!/bin/sh\necho xmessage "$@" >> "{shown}"\n')
    # The firmware step has its own test below; this one is about run.sh.
    done, calls = bench.run("dev/launch_desktop.sh", FAKE_UNAME="Linux",
                            STATION_NO_AUTO_FLASH="1")
    assert done.returncode == 0, done.stderr
    assert (done.stdout, done.stderr) == ("", "")
    assert calls[-1] == ["python3", "src/app.py"]
    assert not shown.exists()
    # No venv anywhere: run.sh's one message lands in the log and the dialog.
    done, calls = bench.run("dev/launch_desktop.sh", FAKE_UNAME="Linux", VIRTUAL_ENV=None)
    assert done.returncode != 0 and calls == []
    log = bench.tmp / "transfer-stage-runs" / "launcher.log"
    assert "no .venv" in log.read_text()
    assert "no .venv" in shown.read_text()


def test_the_launcher_icon_flashes_stale_boards_first_unless_a_station_runs(bench):
    """Owner 2026-10-08: switching between the Classic and Launcher icons
    leaves the boards on the other app's sketches, so the Launcher flashes
    every out-of-date board (controller.flashing, in-process) before run.sh,
    as Classic does. Not when a station already runs: its ports are held and
    run.sh only opens its page."""
    stubs = bench.tmp / "stubs"
    _executable(stubs / "zenity", "#!/bin/sh\ncat > /dev/null\n")
    done, calls = bench.run("dev/launch_desktop.sh", FAKE_UNAME="Linux")
    assert done.returncode == 0, done.stderr
    assert calls[0][:2] == ["python3", "-c"] and "flashing" in calls[0][2]
    assert calls[-1] == ["python3", "src/app.py"]
    # A running station (its instance file names a live process): no flash.
    runs = bench.tmp / "transfer-stage-runs"
    runs.mkdir(exist_ok=True)
    (runs / "station-instance.json").write_text(
        json.dumps({"pid": os.getpid(), "url": "http://127.0.0.1:8080"}))
    done, calls = bench.run("dev/launch_desktop.sh", FAKE_UNAME="Linux")
    assert done.returncode == 0, done.stderr
    assert not any("flashing" in " ".join(c) for c in calls), calls
    assert calls[-1] == ["python3", "src/app.py"]


def test_a_failed_flash_keeps_the_launcher_from_starting_the_station(bench):
    """As Classic: a flash that fails stops the launch, and says why."""
    stubs = bench.tmp / "stubs"
    shown = bench.tmp / "zenity.log"
    _executable(stubs / "zenity", f'#!/bin/sh\ncat >> "{shown}"\n')
    _executable(bench.venv / "bin" / "python3", STUB_PYTHON.replace(
        "sys.exit(0)", "sys.exit(1 if sys.argv[1:2] == ['-c'] else 0)"))
    done, calls = bench.run("dev/launch_desktop.sh", FAKE_UNAME="Linux")
    assert done.returncode != 0
    assert ["python3", "src/app.py"] not in calls
    log = bench.tmp / "transfer-stage-runs" / "launcher.log"
    assert "flashing failed, so the station was not launched" in log.read_text()


def test_swap_refuses_while_a_station_holds_the_ports(swap):
    """2026-10-09: the Classic icon started the original app over a running
    station: its flash could not reach the held ports. A running station
    (its instance file names a live process) now stops both targets before
    anything is flashed or started, in one sentence."""
    legacy = swap.w / "legacy-app"
    (legacy / "src").mkdir(parents=True)
    (legacy / "src" / "mainGUI.py").write_text("")
    (legacy / ".venv" / "bin").mkdir(parents=True)
    _executable(legacy / ".venv" / "bin" / "python", "#!/bin/sh\n")
    runs = swap.tmp / "transfer-stage-runs"
    runs.mkdir(exist_ok=True)
    (runs / "station-instance.json").write_text(
        json.dumps({"pid": os.getpid(), "url": "http://127.0.0.1:8080"}))
    for target in ("legacy", "station"):
        done, calls = swap.run("../w/with-legacy/dev/swap_branch.sh", target,
                               TRANSFER_STAGE_DATA_ROOT=None)
        assert done.returncode == 1 and calls == [], (target, done.stderr)
        assert done.stderr.startswith("swap_branch: a station is running")
