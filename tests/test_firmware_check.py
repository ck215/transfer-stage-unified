"""The firmware check that used to live in `run_swap.sh` (now `dev/swap_branch.sh`; owner, 2026-09-28:
"retire any system that doesn't use the SWAP check. Push any setup left in
the terminal to GUI indicators").

`controller.firmware.FirmwareCheck` works out each board's status from the
stamp file and the sketch hashes `controller.flashing` computes (the one
copy of the rule since brief rb-dist-app A1). The rule is still pinned here
against the command line's own function, run as a subprocess. Flashing
runs in this process through `controller.flashing`; every flash in this
file goes through an injected `run=` and a fake handshake, and the only
calls to the real command line are `--dry-run --no-detect` (no port is
opened, nothing is compiled, nothing is recorded).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from controller import firmware as fw
from controller import flashing
from controller.firmware import FirmwareCheck

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "firmware" / "flash_firmware.py"
BOARDS = tuple(fw.BOARDS)


# -- a sketch tree and a stamp file in tmp_path -------------------------------

def make_tree(root):
    """Four sketches the way `firmware/` lays them out, with the corners the
    hash rule has to get right: a header beside the .ino, a CRLF file, build
    output that must be skipped, a vendored library one sketch includes and
    one it does not."""
    root = Path(root)
    for board, directory in fw.BOARDS.items():
        sketch = root / directory
        sketch.mkdir(parents=True)
        (sketch / f"{directory}.ino").write_text(f"// {board}\nvoid setup(){{}}\n")
    stepper = root / "stepper_firmware"
    (stepper / "motion.h").write_text('#include "Vendored.h"\nint x;\n')
    (stepper / "crlf.cpp").write_bytes(b"int a;\r\nint b;\r\n")
    (stepper / "build").mkdir()
    (stepper / "build" / "stale.cpp").write_text("ignored by the hash\n")
    (stepper / "notes.txt").write_text("not source\n")
    (root / "libraries" / "Vendored" / "src").mkdir(parents=True)
    (root / "libraries" / "Vendored" / "src" / "Vendored.h").write_text("#pragma once\n")
    (root / "libraries" / "Unused").mkdir(parents=True)
    (root / "libraries" / "Unused" / "Unused.h").write_text("#pragma once\n")
    return root


def scripts_hashes(sketch_root):
    """`flash_firmware.sketch_hash` for every board, from the script itself
    in a child process (the architecture test forbids importing it here)."""
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); "
            "import flash_firmware as f; "
            "print(json.dumps({'dirs': {n: c['dir'] for n, c in f.DEVICES.items()}, "
            "'hashes': {n: f.sketch_hash(n, sys.argv[2]) for n in f.DEVICES}}))")
    done = subprocess.run([sys.executable, "-c", code, str(SCRIPT.parent), str(sketch_root)],
                          capture_output=True, text=True, timeout=60, cwd=str(REPO))
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def seed(stamp, **hashes):
    stamp = Path(stamp)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({board: {"hash": digest, "sketch": "x", "port": "p",
                                         "flashed_at": "2026-09-28T10:00:00"}
                                 for board, digest in hashes.items()}))
    return stamp


@pytest.fixture(autouse=True)
def no_teensy_reboot(monkeypatch):
    """No test opens a real port: the Teensy reboot is a no-op here."""
    monkeypatch.setattr(flashing, "reboot_teensy", lambda port: None)


@pytest.fixture
def tree(tmp_path):
    return make_tree(tmp_path / "firmware")


@pytest.fixture
def stamp(tmp_path):
    return tmp_path / "runs" / "flashed.json"


def everything_found(_tool):
    return "/usr/local/bin/tool"


def checker(tree, stamp, **kwargs):
    kwargs.setdefault("which", everything_found)
    return FirmwareCheck(sketch_root=tree, stamp=stamp, **kwargs)


def all_current(check):
    """Every stamp entry current: one per board, one per axis for the XYZ
    Stage ("XYZ Stage X", ...)."""
    return {key: check.sketch_hash(board) for board in BOARDS
            for key in flashing.stamp_keys(board)}


# -- the hash rule, pinned against the script ---------------------------------

def test_the_board_list_is_the_scripts_board_list(tree):
    assert scripts_hashes(tree)["dirs"] == fw.BOARDS


def test_the_hash_rule_is_the_scripts_own_on_a_tree_with_every_corner(tree, stamp):
    check = checker(tree, stamp)
    assert {b: check.sketch_hash(b) for b in BOARDS} == scripts_hashes(tree)["hashes"]


def test_the_hash_rule_is_the_scripts_own_on_this_checkouts_firmware(stamp):
    check = checker(REPO / "firmware", stamp)
    assert {b: check.sketch_hash(b) for b in BOARDS} == scripts_hashes(REPO / "firmware")["hashes"]


def test_the_hash_ignores_line_endings_build_output_and_non_source(tree, stamp):
    check = checker(tree, stamp)
    before = check.sketch_hash("Stepper Probe")
    stepper = tree / "stepper_firmware"
    (stepper / "crlf.cpp").write_bytes(b"int a;\nint b;\n")
    (stepper / "build" / "stale.cpp").write_text("changed\n")
    (stepper / "notes.txt").write_text("changed\n")
    (tree / "libraries" / "Unused" / "Unused.h").write_text("// changed\n")
    assert check.sketch_hash("Stepper Probe") == before


def test_a_change_to_an_included_library_is_a_change_to_the_firmware(tree, stamp):
    check = checker(tree, stamp)
    before = check.sketch_hash("Stepper Probe")
    other = check.sketch_hash("DC Probe")
    (tree / "libraries" / "Vendored" / "src" / "Vendored.h").write_text("// v2\n")
    assert check.sketch_hash("Stepper Probe") != before
    assert check.sketch_hash("DC Probe") == other


def test_the_out_of_date_rule_is_the_scripts_own(tree, stamp):
    """The script's `--dry-run --no-detect` decides which boards it would
    flash from the same stamp: it must name exactly the boards the check
    says need flashing. Opens no port, compiles nothing, records nothing."""
    check = checker(tree, stamp)
    hashes = all_current(check)
    seed(stamp, **{"Stepper Probe": hashes["Stepper Probe"],
                   "DC Probe": "0" * 64,
                   "Temperature Controller": hashes["Temperature Controller"]})
    before = stamp.read_text()
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--no-detect", "--yes",
         "--sketch-root", str(tree), "--stamp", str(stamp)],
        capture_output=True, text=True, timeout=60, cwd=str(REPO))
    assert done.returncode == 0, done.stdout + done.stderr
    line = next(l for l in done.stdout.splitlines() if l.startswith("Needs flashing"))
    scripts = line.split(":", 1)[1].strip().split(", ")
    result = check.check()
    assert result["to_flash"] == scripts == ["DC Probe", "Chuck Positioner", "XYZ Stage"]
    assert stamp.read_text() == before


# -- status from the stamp ------------------------------------------------------

def test_every_board_current_says_all_current(tree, stamp):
    check = checker(tree, stamp)
    seed(stamp, **all_current(check))
    result = check.check()
    assert result["summary"] == "all current"
    assert result["to_flash"] == [] and result["stale"] == []
    assert set(result["boards"].values()) == {fw.CURRENT}


def test_one_board_behind_is_named(tree, stamp):
    check = checker(tree, stamp)
    seed(stamp, **dict(all_current(check), **{"Stepper Probe": "f" * 64}))
    result = check.check()
    assert result["summary"] == "Stepper Probe out of date"
    assert result["stale"] == ["Stepper Probe"]
    assert result["boards"]["Stepper Probe"] == fw.OUT_OF_DATE


def test_two_boards_behind_are_named_in_order(tree, stamp):
    check = checker(tree, stamp)
    seed(stamp, **dict(all_current(check), **{"Chuck Positioner": "a", "Stepper Probe": "b"}))
    assert check.check()["summary"] == "Stepper Probe and Chuck Positioner out of date"


def test_no_stamp_file_says_never_flashed_here(tree, stamp):
    result = checker(tree, stamp).check()
    assert result["summary"] == "never flashed here"
    assert result["to_flash"] == list(BOARDS)
    assert result["stale"] == []
    assert set(result["boards"].values()) == {fw.NEVER}


def test_a_board_missing_from_the_stamp_is_never_flashed_not_out_of_date(tree, stamp):
    check = checker(tree, stamp)
    hashes = all_current(check)
    del hashes["Chuck Positioner"]
    seed(stamp, **hashes)
    result = check.check()
    assert result["summary"] == "Chuck Positioner never flashed here"
    assert result["stale"] == [] and result["to_flash"] == ["Chuck Positioner"]


def test_behind_and_never_flashed_are_both_said(tree, stamp):
    check = checker(tree, stamp)
    hashes = dict(all_current(check), **{"DC Probe": "x"})
    del hashes["Temperature Controller"]
    seed(stamp, **hashes)
    assert check.check()["summary"] == ("DC Probe out of date; "
                                        "Temperature Controller never flashed here")


def test_one_axis_board_behind_makes_the_xyz_stage_out_of_date(tree, stamp):
    check = checker(tree, stamp)
    seed(stamp, **dict(all_current(check), **{"XYZ Stage Y": "f" * 64}))
    result = check.check()
    assert result["boards"]["XYZ Stage"] == fw.OUT_OF_DATE
    assert result["summary"] == "XYZ Stage out of date"


def test_an_axis_board_missing_from_the_stamp_is_never_flashed(tree, stamp):
    check = checker(tree, stamp)
    hashes = all_current(check)
    del hashes["XYZ Stage Z"]
    seed(stamp, **hashes)
    assert check.check()["summary"] == "XYZ Stage never flashed here"


def test_an_axis_behind_outranks_an_axis_never_flashed(tree, stamp):
    check = checker(tree, stamp)
    hashes = dict(all_current(check), **{"XYZ Stage X": "a"})
    del hashes["XYZ Stage Z"]
    seed(stamp, **hashes)
    assert check.check()["boards"]["XYZ Stage"] == fw.OUT_OF_DATE


def test_an_unreadable_stamp_is_never_flashed_and_never_raises(tree, stamp):
    stamp.parent.mkdir(parents=True)
    stamp.write_text("{not json")
    assert checker(tree, stamp).check()["summary"] == "never flashed here"


def test_missing_arduino_cli_says_flash_by_hand(tree, stamp):
    check = checker(tree, stamp, which=lambda tool: None if tool == "arduino-cli" else "/x")
    seed(stamp, **dict(all_current(check), **{"Stepper Probe": "b"}))
    result = check.check()
    assert result["missing_tools"] == ["arduino-cli"]
    assert result["summary"] == "Stepper Probe out of date; arduino-cli not found: flash by hand"


def test_missing_tools_with_nothing_to_flash_is_still_all_current(tree, stamp):
    check = checker(tree, stamp, which=lambda tool: None)
    seed(stamp, **all_current(check))
    assert check.check()["summary"] == "all current"


def test_the_teensy_loader_is_required_as_the_script_requires_it(tree, stamp):
    check = checker(tree, stamp, which=lambda tool: None if tool == "teensy_loader_cli" else "/x")
    assert check.check()["summary"] == ("never flashed here; teensy_loader_cli "
                                        "not found: flash by hand")


def test_no_sketches_here_says_so(tmp_path, stamp):
    result = checker(tmp_path / "nowhere", stamp).check()
    assert result["summary"] == "firmware sketches not found: flash by hand"
    assert result["to_flash"] == []


def test_the_stamp_follows_the_scripts_environment_variable(tree, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere.json"
    monkeypatch.setenv("STATION_FLASH_STAMP", str(other))
    check = FirmwareCheck(sketch_root=tree, which=everything_found)
    assert check.stamp == other
    seed(other, **all_current(check))
    assert check.check()["summary"] == "all current"


def test_the_default_stamp_is_the_scripts_default(monkeypatch):
    monkeypatch.delenv("STATION_FLASH_STAMP", raising=False)
    assert FirmwareCheck().stamp == Path.home() / "transfer-stage-runs" / "flashed.json"


def test_the_suite_never_reads_the_benchs_real_stamp():
    assert os.environ["STATION_FLASH_STAMP"] != str(
        Path.home() / "transfer-stage-runs" / "flashed.json")
    assert os.environ.get("STATION_NO_FIRMWARE_CHECK") == "1"


# -- flashing: in this process, through `controller.flashing` (A1) -----------

class FakeRun:
    """Stands in for the tool runner: records argv, streams scripted lines."""

    def __init__(self, lines=("compiling", "uploaded"), code=0):
        self.lines, self.code = list(lines), code
        self.calls = []

    def __call__(self, argv, cwd, on_line, timeout, env=None):
        self.calls.append({"argv": list(argv), "cwd": cwd, "timeout": timeout})
        for line in self.lines:
            on_line(line)
        return self.code


def on_ports(**found):
    """identify= and ports= for a checker: {port: board}."""
    ports = {port.replace("_", "/"): board for port, board in found.items()}
    return {"identify": ports.get, "ports": list(ports)}


def test_flash_runs_in_this_process_never_through_sys_executable(tree, stamp):
    """Frozen, `sys.executable` is the launcher: spawning it "with the
    script" started the station again. The flash is the tools only."""
    run = FakeRun()
    check = checker(tree, stamp, run=run, **on_ports(COM7="Stepper Probe",
                                                     COM8="DC Probe"))
    seen = []
    result = check.flash(["Stepper Probe", "DC Probe"], on_line=seen.append)
    argvs = [c["argv"] for c in run.calls]
    assert all(sys.executable not in argv for argv in argvs)
    assert [argv[0] for argv in argvs] == ["/usr/local/bin/tool"] * 2
    assert [argv[argv.index("-p") + 1] for argv in argvs] == ["COM7", "COM8"]
    assert "--force" not in sum(argvs, [])
    assert "compiling" in seen and seen == result["lines"]
    assert result["ok"] is True and result["returncode"] == 0
    assert result["results"] == {"Stepper Probe": "ok", "DC Probe": "ok"}
    assert result["last"] == "DC Probe                 ok"
    recorded = json.loads(stamp.read_text())
    assert recorded["Stepper Probe"]["channel"] == "station"


def test_a_failed_flash_is_not_ok_and_keeps_the_last_line(tree, stamp):
    run = FakeRun(lines=["avrdude: stk500v2_getsync(): timeout"], code=1)
    result = checker(tree, stamp, run=run, **on_ports(COM7="Stepper Probe")).flash(
        ["Stepper Probe"])
    assert result["ok"] is False and result["returncode"] == 1
    assert result["results"] == {"Stepper Probe": "FAILED"}
    assert "avrdude: stk500v2_getsync(): timeout" in result["lines"]


def test_a_runner_that_raises_is_a_failed_flash_not_an_exception(tree, stamp):
    def broken(argv, cwd, on_line, timeout, env=None):
        raise OSError("no arduino-cli")
    result = checker(tree, stamp, run=broken, **on_ports(COM8="DC Probe")).flash(
        ["DC Probe"])
    assert result["ok"] is False
    assert any("no arduino-cli" in line for line in result["lines"])


def test_a_detection_that_raises_is_a_failed_flash_not_an_exception(tree, stamp):
    def ports():
        raise RuntimeError("listing gone")
    result = checker(tree, stamp, run=FakeRun(), identify=lambda p: None,
                     ports=ports).flash(["DC Probe"])
    assert result["ok"] is False and "listing gone" in result["last"]


def test_a_board_not_plugged_in_says_not_connected(tree, stamp):
    result = checker(tree, stamp, run=FakeRun(), **on_ports()).flash(["DC Probe"])
    assert result["ok"] is True and result["absent"] == ["DC Probe"]
    assert any(l.startswith("Not connected") for l in result["lines"])


@pytest.mark.parametrize("boards", [[], ["Rotator"], ["Stepper Probe", "Nope"]])
def test_flash_refuses_no_board_or_an_unknown_one(tree, stamp, boards):
    run = FakeRun()
    with pytest.raises(ValueError):
        checker(tree, stamp, run=run).flash(boards)
    assert run.calls == []


def test_the_default_runner_streams_lines_as_they_come():
    seen = []
    code = fw.stream_lines([sys.executable, "-u", "-c",
                            "import sys; print('one'); print('two'); "
                            "print('three', file=sys.stderr); sys.exit(3)"],
                           cwd=str(REPO), on_line=seen.append, timeout=30)
    assert code == 3
    assert seen == ["one", "two", "three"]


def test_the_default_runner_kills_a_flash_that_hangs():
    """teensy_loader_cli -w waits for a bootloader forever when the soft
    reboot fails: the flash must end, or Launch would wait forever."""
    started = time.monotonic()
    code = fw.stream_lines([sys.executable, "-u", "-c",
                            "import time; print('waiting', flush=True); time.sleep(30)"],
                           cwd=str(REPO), on_line=lambda line: None, timeout=0.5)
    assert code is None
    assert time.monotonic() - started < 10


def test_the_default_runner_gives_the_script_no_stdin():
    seen = []
    fw.stream_lines([sys.executable, "-c", "print(repr(input('?')))"],
                    cwd=str(REPO), on_line=seen.append, timeout=30)
    assert any("EOFError" in line for line in seen)


# -- A2: in a frozen bundle the check reads the bundle's sketches and tools -----

@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """A frozen launcher in tmp_path/station, with the layout contract's
    firmware/ and tools/ beside it."""
    root = tmp_path / "station"
    make_tree(root / "firmware")
    exe = ".exe" if os.name == "nt" else ""
    (root / "tools" / "arduino-data").mkdir(parents=True)
    for tool in ("arduino-cli", "teensy_loader_cli"):
        (root / "tools" / f"{tool}{exe}").write_text("")
    (root / f"station-qt{exe}").write_text("")
    (root / "VERSION").write_text("v1.4.0\nabc1234\n2026-09-30T00:00:00Z\n")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(root / f"station-qt{exe}"))
    monkeypatch.setattr(sys, "_MEIPASS", str(root / "_internal"), raising=False)
    return root.resolve()


def test_a_frozen_bundle_checks_the_sketches_beside_its_launchers(bundle, stamp):
    """Frozen, `__file__` is inside `_internal`: the check said "firmware
    sketches not found". It reads `<bundle>/firmware`."""
    check = FirmwareCheck(stamp=stamp)
    assert check.root == bundle
    assert check.sketch_root == bundle / "firmware"
    result = check.check()
    assert result["summary"] == "never flashed here"
    assert result["missing_tools"] == []


def test_a_frozen_bundle_uses_its_own_tools_not_the_path(bundle, stamp):
    check = FirmwareCheck(stamp=stamp, which=lambda tool: None)
    exe = ".exe" if os.name == "nt" else ""
    assert check.tools.arduino_cli == str(bundle / "tools" / f"arduino-cli{exe}")
    assert check.check()["missing_tools"] == []


def test_a_frozen_bundles_flash_is_stamped_with_its_version(bundle, stamp):
    check = FirmwareCheck(stamp=stamp, run=FakeRun(),
                          **on_ports(COM3="Chuck Positioner"))
    assert check.version == "v1.4.0"
    assert check.flash(["Chuck Positioner"])["ok"]
    entry = json.loads(stamp.read_text())["Chuck Positioner"]
    assert entry["version"] == "v1.4.0" and entry["channel"] == "station"


def test_a_checkout_still_checks_its_own_firmware(stamp):
    check = FirmwareCheck(stamp=stamp, which=everything_found)
    assert check.sketch_root == REPO / "firmware" and check.version is None


# -- A4: the way back from stable needs no code beyond the check ---------------

def test_after_a_stable_flash_every_station_board_is_out_of_date(tmp_path, stamp):
    """Switch to stable stamps the stable sketches (channel `stable`); the
    station's startup check then sees every board differ and offers Flash
    now - the way back."""
    station = make_tree(tmp_path / "station")
    stable = tmp_path / "stable"
    for board in flashing.STABLE_BOARDS:
        directory = fw.BOARDS[board]
        (stable / directory).mkdir(parents=True)
        (stable / directory / f"{directory}.ino").write_text(f"// stable {board}\n")
    ports = {f"COM{i}": board for i, board in enumerate(flashing.STABLE_BOARDS, 1)}
    flashed = FirmwareCheck(sketch_root=stable, stamp=stamp, which=everything_found,
                            run=FakeRun(), identify=ports.get, ports=list(ports),
                            channel="stable").flash(list(flashing.STABLE_BOARDS))
    assert flashed["ok"]
    assert {e["channel"] for e in json.loads(stamp.read_text()).values()} == {"stable"}
    result = checker(station, stamp).check()
    # The XYZ Stage is station-only: stable never flashes it, so here it was
    # never flashed rather than out of date.
    assert {b: s for b, s in result["boards"].items()} == dict(
        {b: fw.OUT_OF_DATE for b in flashing.STABLE_BOARDS}, **{"XYZ Stage": fw.NEVER})
    assert result["to_flash"] == list(fw.BOARDS)


# -- R-3 as Setup sees it: a Teensy left in its bootloader (2026-10-09) ----------

class HeaterAndAxes:
    """Setup's handshake over the heater and the three axis boards."""
    probe_tags = {"COM4": "X", "COM5": "Y", "COM6": "Z"}

    def identify(self, port):
        if port in self.probe_tags:
            return "XYZ Stage"
        return {"COM3": "Temperature Controller"}.get(port)


def test_a_teensy_left_in_its_bootloader_fails_the_flash_with_the_replug_hint(tree, stamp):
    """The heater's upload fails after its reboot: Setup is told the flash
    failed, why the axis boards were not flashed, and to replug the heater;
    no axis board's loader runs (it would program the heater)."""
    calls = []

    def run(argv, cwd, on_line, timeout, env=None):
        calls.append(list(argv))
        return 1 if argv[-1].endswith("temp_controller.ino.hex") else 0
    probe = HeaterAndAxes()
    result = checker(tree, stamp, run=run, identify=probe.identify,
                     ports=["COM3", "COM4", "COM5", "COM6"]).flash(
        ["Temperature Controller", "XYZ Stage"])
    assert result["ok"] is False and result["returncode"] == 1
    assert [argv for argv in calls if "xyz_stage_axis.ino.hex" in argv[-1]] == []
    for axis in "XYZ":
        assert "bootloader" in result["results"][f"XYZ Stage {axis}"]
    assert any("unplug" in hint and "Temperature Controller" in hint
               for hint in result["hints"])
    assert not stamp.exists()
