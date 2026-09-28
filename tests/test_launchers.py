"""The launchers (owner, 2026-09-28: "Consolidate the various run scripts,
using OS detection, and retire any system that doesn't use the SWAP check").

Two launchers: `run.sh` for every POSIX OS (`uname -s` picks the macOS
PySide6 repair, a toolkit-forced branch) and `run.bat` for Windows. Both
find the venv and run `src/app.py` with every argument, and print nothing
when all is well: the firmware check is a Setup row now. `run_macos.sh` and
`run_swap_macos.sh` are shims kept for old desktop shortcuts; `run_swap.sh`
is `dev/swap_branch.sh`, a developer tool.

Every script here runs against stubs: a `python3` that records its argv and
runs nothing, a `uname` that says what the test wants, a `chflags` that
records. Nothing is launched, installed or flashed.
"""
import json
import os
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
if sys.argv[1:2] == ["-c"] and os.environ.get("STUB_QT_BROKEN") == "1" \\
        and "QtWidgets" in sys.argv[2]:
    sys.exit(1)
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
    for name in ("run.sh", "run_macos.sh", "run_swap_macos.sh"):
        shutil.copy2(REPO / name, tree / name)
    shutil.copy2(REPO / "dev" / "swap_branch.sh", tree / "dev" / "swap_branch.sh")
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

@pytest.mark.parametrize("script", ["run.sh", "run_macos.sh", "run_swap_macos.sh",
                                    "dev/swap_branch.sh", "update.sh"])
def test_every_launcher_parses(script):
    done = subprocess.run([BASH, "-n", str(REPO / script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_two_launchers_and_the_updaters_are_all_that_is_left_at_the_root():
    scripts = {p.name for p in REPO.iterdir() if p.suffix in (".sh", ".bat")}
    assert scripts == {"run.sh", "run.bat", "update.sh", "update.bat",
                       "run_macos.sh", "run_swap_macos.sh"}
    assert not (REPO / "run_swap.sh").exists()


@pytest.mark.parametrize("shim", ["run_macos.sh", "run_swap_macos.sh"])
def test_the_old_names_are_shims_that_exec_run_sh(shim):
    lines = [l for l in (REPO / shim).read_text().splitlines() if l.strip()]
    code = [l for l in lines if not l.startswith("#")]
    assert code == ['exec "$(dirname "$0")/run.sh" "$@"']
    assert any("delete after 2026-10-31" in l for l in lines)
    assert os.access(REPO / shim, os.X_OK)


def test_the_launchers_are_executable():
    for script in ("run.sh", "dev/swap_branch.sh", "update.sh"):
        assert os.access(REPO / script, os.X_OK), script


def test_swap_branch_says_it_is_a_developer_tool():
    header = (REPO / "dev" / "swap_branch.sh").read_text().split("\nset -u", 1)[0]
    assert "developer tool" in header
    assert "main" in header and "flash" in header


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


def test_on_macos_the_qt_default_is_checked_then_launched_silently(bench):
    done, calls = bench.run("run.sh", FAKE_UNAME="Darwin")
    assert done.returncode == 0, done.stderr
    assert (done.stdout, done.stderr) == ("", "")
    assert calls[0][:2] == ["python3", "-c"] and "QtWidgets" in calls[0][2]
    assert calls[-1] == ["python3", "src/app.py"]
    assert not any("pip" in c for c in calls)


@pytest.mark.parametrize("flags", [["--qt"], ["--pyside"], ["--view", "qt"], ["--view=pyside"]])
def test_on_macos_an_explicit_qt_is_checked_too(bench, flags):
    done, calls = bench.run("run.sh", *flags, FAKE_UNAME="Darwin")
    assert (done.stdout, done.stderr) == ("", "")
    assert any(c[:2] == ["python3", "-c"] for c in calls)
    assert calls[-1] == ["python3", "src/app.py", *flags]


@pytest.mark.parametrize("flags", [["--web"], ["--tk"], ["--view", "web"], ["--view=tk"]])
def test_on_macos_another_view_skips_the_qt_repair(bench, flags):
    done, calls = bench.run("run.sh", *flags, FAKE_UNAME="Darwin")
    assert (done.stdout, done.stderr) == ("", "")
    assert calls == [["python3", "src/app.py", *flags]]


def test_on_linux_there_is_no_qt_repair_at_all(bench):
    done, calls = bench.run("run.sh", FAKE_UNAME="Linux")
    assert (done.stdout, done.stderr) == ("", "")
    assert calls == [["python3", "src/app.py"]]
    assert not (bench.tmp / "chflags.log").exists()


def test_a_broken_pyside_is_reinstalled_and_says_so_on_stderr_only(bench):
    done, calls = bench.run("run.sh", "--qt", FAKE_UNAME="Darwin", STUB_QT_BROKEN="1")
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
    assert "PySide6" in done.stderr and len(done.stderr.strip().splitlines()) == 1
    pips = [c for c in calls if c[1:3] == ["-m", "pip"]]
    assert any("install" in c and "--force-reinstall" in c for c in pips)
    assert calls[-1] == ["python3", "src/app.py", "--qt"]


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


@pytest.mark.parametrize("shim", ["run_macos.sh", "run_swap_macos.sh"])
def test_a_shim_lands_in_run_sh_with_its_arguments(bench, shim):
    done, calls = bench.run(shim, "--help", FAKE_UNAME="Darwin")
    assert (done.stdout, done.stderr) == ("", "")
    assert calls == [APP_HELP]


# -- dev/swap_branch.sh ------------------------------------------------------

def test_swap_branch_dry_run_says_what_it_would_do_and_runs_nothing(bench):
    main = bench.tmp / "main-tree"
    (main / "src").mkdir(parents=True)
    (main / "src" / "mainGUI.py").write_text("")
    done, calls = bench.run("dev/swap_branch.sh", "main", RUN_SWAP_DRY_RUN="1",
                            STATION_MAIN_TREE=str(main))
    assert done.returncode == 0, done.stderr
    assert calls == []
    out = done.stdout
    assert f"branch: main  tree: {main}" in out
    assert "would flash:" in out
    assert f"--sketch-root {main}/firmware --yes --only Stepper\\ Probe" in out
    assert f"--sketch-root {bench.tree}/firmware --yes --only Temperature\\ Controller" in out
    assert "would launch" in out and "python3 src/mainGUI.py" in out


def test_swap_branch_finds_a_main_worktree_beside_this_one(bench):
    """This machine's layout: transfer-stage-unified/{main,mvc-refactor,...}."""
    sibling = bench.tree.parent / "main"
    (sibling / "src").mkdir(parents=True)
    (sibling / "src" / "mainGUI.py").write_text("")
    done, calls = bench.run("dev/swap_branch.sh", "main", "--no-flash",
                            RUN_SWAP_DRY_RUN="1")
    assert done.returncode == 0, done.stderr
    assert f"tree: {sibling}" in done.stdout
    assert calls == []


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
    child = subprocess.Popen([sys.executable, str(REPO / "src" / "app.py"), "--web",
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
    assert titles[:3] == ["Update", "Firmware", "Devices"]
    assert setup["state"]["values"]["web_address"] == f"http://127.0.0.1:{port}"
