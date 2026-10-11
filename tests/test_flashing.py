"""`controller.flashing`: the flashing logic that was
`firmware/flash_firmware.py`'s, in `src/` so a frozen bundle runs it
in-process (brief rb-dist-app A1).

Nothing here starts a tool or opens a port: every subprocess goes through
a recording runner, detection through a fake handshake (`identify`) over a
fake port list, and the one real handshake used is `PortProbe` over the
same `SerialPort` stand-in `test_setup_identify.py` uses.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from controller import flashing
from controller import setup as station_setup

REPO = Path(__file__).resolve().parents[1]
#: The real reboot, before the autouse fixture replaces it.
REAL_REBOOT = flashing.reboot_teensy


def make_tree(root, boards=flashing.BOARDS):
    root = Path(root)
    for board, cfg in boards.items():
        sketch = root / cfg["dir"]
        sketch.mkdir(parents=True)
        (sketch / f"{cfg['dir']}.ino").write_text(f"// {board}\nvoid setup(){{}}\n")
    return root


class Runner:
    """Records every command; answers 0 unless `fail` names a board's sketch
    directory in the argv."""

    def __init__(self, fail=(), code=1):
        self.calls, self.fail, self.code = [], set(fail), code

    def __call__(self, argv, cwd, on_line, timeout, env=None):
        self.calls.append({"argv": list(argv), "cwd": cwd, "env": env,
                           "timeout": timeout})
        on_line("tool output")
        if any(part.endswith(tuple(self.fail)) for part in argv if self.fail):
            return self.code
        return 0


def answering(mapping):
    """identify(port) from {port: model name}."""
    return lambda port: mapping.get(port)


@pytest.fixture
def tree(tmp_path):
    return make_tree(tmp_path / "firmware")


@pytest.fixture
def stamp(tmp_path):
    return tmp_path / "runs" / "flashed.json"


@pytest.fixture
def path_tools():
    return flashing.Tools("/usr/bin/arduino-cli", "/usr/bin/teensy_loader_cli")


@pytest.fixture(autouse=True)
def reboots(monkeypatch):
    """No test opens a real port: the Teensy reboot (134 baud on the board's
    own port) is recorded instead."""
    seen = []
    monkeypatch.setattr(flashing, "reboot_teensy", seen.append)
    return seen


# -- the board table ------------------------------------------------------------

def test_the_board_table_matches_the_sketch_directories_on_disk():
    """Every board's sketch is <firmware>/<dir>/<dir>.ino in this checkout,
    and every sketch directory there is a board (libraries aside)."""
    firmware = REPO / "firmware"
    for board, cfg in flashing.BOARDS.items():
        assert (firmware / cfg["dir"] / f"{cfg['dir']}.ino").is_file(), board
    on_disk = {p.name for p in firmware.iterdir()
               if p.is_dir() and any(p.glob("*.ino"))}
    assert on_disk == {cfg["dir"] for cfg in flashing.BOARDS.values()}


def test_the_chips_are_three_megas_and_the_teensy_boards():
    assert [cfg["board"] for cfg in flashing.BOARDS.values()] == [
        "mega", "mega", "mega", "teensy", "teensy", "mega"]


def test_the_xyz_stage_is_three_tagged_boards_and_station_only():
    assert flashing.BOARDS["XYZ Stage"]["tags"] == ["X", "Y", "Z"]
    assert flashing.stamp_keys("XYZ Stage") == ["XYZ Stage X", "XYZ Stage Y", "XYZ Stage Z"]
    assert flashing.stamp_keys("DC Probe") == ["DC Probe"]
    assert "XYZ Stage" not in flashing.STABLE_BOARDS
    assert set(flashing.STABLE_BOARDS) == set(flashing.BOARDS) - {"XYZ Stage",
                                                                   "XYZ Stage (Mega)"}


def test_the_xyz_stage_mega_is_one_untagged_mega_and_station_only():
    """MEGA_STANDARD (2026-10-09): one Mega 2560 for all three axes, named
    by its identity letter (`DEV: m caps=...`); flashed like the probes."""
    assert flashing.BOARDS["XYZ Stage (Mega)"] == {"dir": "xyz_stage_mega", "board": "mega"}
    assert list(flashing.BOARDS).index("XYZ Stage (Mega)") == \
        list(flashing.BOARDS).index("XYZ Stage") + 1
    assert flashing.stamp_keys("XYZ Stage (Mega)") == ["XYZ Stage (Mega)"]
    assert "XYZ Stage (Mega)" not in flashing.STABLE_BOARDS
    assert "XYZ Stage (Mega)" in station_setup.MODEL_TYPES
    tools = flashing.Tools("cli", "loader")
    (argv,) = flashing.commands("XYZ Stage (Mega)", "COM9", Path("/fw"), tools)
    assert argv[:5] == ["cli", "compile", "--fqbn", flashing.MEGA_FQBN, "--upload"]
    assert "COM9" in argv and str(Path("/fw") / "xyz_stage_mega") in argv


# -- detection: Setup's handshake, COM ports included (OP-12) -------------------

@pytest.mark.parametrize("port", ["COM7", "/dev/ttyACM0"])
def test_a_port_answering_dev_s_is_the_stepper_probe(port, monkeypatch):
    """The real `PortProbe.identify` (Setup's own handshake), over a fake
    `SerialPort` whose reply is `DEV: s`: the Stepper Probe, on a Windows
    COM port as well as a Linux one."""
    from tests.test_setup_identify import port_answering
    monkeypatch.setattr(station_setup, "SerialPort",
                        port_answering({500000: "DEV: s"}))
    monkeypatch.setattr(station_setup, "PROBE_SECONDS", 0.05)
    monkeypatch.setattr(station_setup, "PROBE_SLICE", 0.01)
    from devices import serial_port as serial_port_module
    monkeypatch.setattr(serial_port_module, "query", lambda *a, **k: b"", raising=False)
    probe = station_setup.PortProbe()
    assert flashing.detect([port], probe.identify) == {"Stepper Probe": port}


def test_detection_skips_nothing_but_sim_and_names_what_it_found():
    lines = []
    found = flashing.detect(["COM3", "COM7", "/dev/ttyACM1", "SIM"],
                            answering({"COM3": "Rotator", "COM7": "DC Probe",
                                       "/dev/ttyACM1": None}), lines.append)
    assert found == {"DC Probe": "COM7"}
    assert "  probing COM7 ... identified as DC Probe" in lines
    assert any("Rotator" in l and "not a flash target" in l for l in lines)
    assert "  probing /dev/ttyACM1 ... no response" in lines


def test_a_port_whose_handshake_raises_is_one_line_not_a_crash():
    def identify(port):
        raise OSError("busy")
    lines = []
    assert flashing.detect(["COM9"], identify, lines.append) == {}
    assert lines == ["  probing COM9 ... failed (busy)"]


def test_setup_is_a_port_probe():
    """One handshake in the codebase: Setup's scan and the flash use the
    same class."""
    assert issubclass(station_setup.Setup, station_setup.PortProbe)
    assert station_setup.Setup.identify is station_setup.PortProbe.identify


# -- the flash sequence ---------------------------------------------------------

def test_a_checkout_flashes_with_the_tools_on_path(tree, stamp, path_tools):
    run = Runner()
    answer = flashing.flash(["Stepper Probe", "Temperature Controller"],
                            sketch_root=tree, stamp=stamp, tools=path_tools,
                            run=run, ports=["/dev/ttyACM0", "/dev/ttyACM1"],
                            identify=answering({"/dev/ttyACM0": "Stepper Probe",
                                                "/dev/ttyACM1": "Temperature Controller"}))
    assert answer["returncode"] == 0
    assert answer["results"] == {"Stepper Probe": "ok", "Temperature Controller": "ok"}
    argvs = [c["argv"] for c in run.calls]
    build = tree / "temp_controller" / "build"
    assert argvs == [
        ["/usr/bin/arduino-cli", "compile", "--fqbn", flashing.MEGA_FQBN,
         "--upload", "-p", "/dev/ttyACM0", str(tree / "stepper_firmware")],
        ["/usr/bin/arduino-cli", "compile", "--fqbn", flashing.TEENSY_FQBN,
         "--output-dir", str(build), str(tree / "temp_controller")],
        ["/usr/bin/teensy_loader_cli", f"--mcu={flashing.TEENSY_MCU}", "-w",
         "-v", str(build / "temp_controller.ino.hex")],
    ]
    assert all(c["env"] is None for c in run.calls)     # a checkout inherits


def test_a_frozen_bundle_flashes_with_its_own_tools_and_data_dir(tmp_path, stamp,
                                                                  monkeypatch):
    """sys.frozen faked: the tools are `<bundle>/tools/...` (the layout
    contract), and every arduino-cli call is pointed at the bundle's
    offline data directory."""
    bundle = tmp_path / "station"
    make_tree(bundle / "firmware")
    tools_dir = bundle / "tools"
    (tools_dir / "arduino-data").mkdir(parents=True)
    exe = ".exe" if os.name == "nt" else ""
    for tool in ("arduino-cli", "teensy_loader_cli"):
        (tools_dir / f"{tool}{exe}").write_text("")
    (bundle / f"station-web{exe}").write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle / f"station-web{exe}"))
    assert flashing.bundle_root() == bundle.resolve()
    tools = flashing.tools_for()
    assert tools.missing == []
    run = Runner()
    answer = flashing.flash(["DC Probe", "Temperature Controller"],
                            sketch_root=flashing.default_sketch_root(), stamp=stamp,
                            tools=tools, run=run, ports=["COM4", "COM5"],
                            identify=answering({"COM4": "DC Probe",
                                                "COM5": "Temperature Controller"}))
    assert answer["returncode"] == 0
    cli = str(bundle.resolve() / "tools" / f"arduino-cli{exe}")
    loader = str(bundle.resolve() / "tools" / f"teensy_loader_cli{exe}")
    firmware = bundle.resolve() / "firmware"
    assert [c["argv"][0] for c in run.calls] == [cli, cli, loader]
    assert run.calls[0]["argv"][1:] == ["compile", "--fqbn", flashing.MEGA_FQBN,
                                        "--upload", "-p", "COM4",
                                        str(firmware / "high_polling_rate")]
    data = str(bundle.resolve() / "tools" / "arduino-data")
    for call in run.calls[:2]:
        # Absolute: arduino-cli resolves the yaml's relative paths against
        # the working directory, not the file (rb-dist-build B2).
        assert call["env"]["ARDUINO_DIRECTORIES_DATA"] == data
        assert call["env"]["ARDUINO_DIRECTORIES_USER"] == data + os.sep + "user"
        assert call["env"]["ARDUINO_DIRECTORIES_DOWNLOADS"] == data + os.sep + "staging"
    # ... and every tool runs from tools/, where the yaml's paths point.
    assert {c["cwd"] for c in run.calls} == {str(bundle.resolve() / "tools")}


def test_the_bundles_config_file_beside_the_cli_is_passed_explicitly(tmp_path):
    """The layout contract: `tools/arduino-cli.yaml`, beside the binary."""
    home = tmp_path / "tools"
    (home / "arduino-data").mkdir(parents=True)
    (home / "arduino-cli.yaml").write_text("directories: {data: arduino-data}\n")
    tools = flashing.tools_for(root=tmp_path, frozen=True)
    assert tools.cwd == str(home)
    assert tools.arduino("compile")[1:] == ["--config-file",
                                            str(home / "arduino-cli.yaml"), "compile"]


def test_a_frozen_bundle_without_its_tools_names_them_missing(tmp_path):
    tools = flashing.tools_for(root=tmp_path, frozen=True)
    assert tools.missing == ["arduino-cli", "teensy_loader_cli"]


def test_the_stamp_is_written_with_the_channel_and_the_version(tree, stamp, path_tools):
    flashing.flash(["Chuck Positioner"], sketch_root=tree, stamp=stamp,
                   tools=path_tools, run=Runner(), ports=["COM2"],
                   identify=answering({"COM2": "Chuck Positioner"}),
                   channel="stable", version="v1.3.0")
    entry = json.loads(stamp.read_text())["Chuck Positioner"]
    assert entry["hash"] == flashing.sketch_hash(tree, "Chuck Positioner")
    assert entry["channel"] == "stable" and entry["version"] == "v1.3.0"
    assert entry["port"] == "COM2"
    assert entry["sketch"] == str(tree / "chuck_firmware")


def test_a_failed_upload_records_nothing_and_fails(tree, stamp, path_tools):
    run = Runner(fail=["stepper_firmware"])
    answer = flashing.flash(["Stepper Probe", "DC Probe"], sketch_root=tree,
                            stamp=stamp, tools=path_tools, run=run,
                            ports=["COM1", "COM2"],
                            identify=answering({"COM1": "Stepper Probe",
                                                "COM2": "DC Probe"}))
    assert answer["returncode"] == 1
    assert answer["results"] == {"Stepper Probe": "FAILED", "DC Probe": "ok"}
    assert set(json.loads(stamp.read_text())) == {"DC Probe"}


def test_a_current_board_opens_no_port_and_runs_nothing(tree, stamp, path_tools):
    flashing.record_flash(stamp, "DC Probe", flashing.sketch_hash(tree, "DC Probe"),
                          tree / "high_polling_rate", "COM2")
    asked, run = [], Runner()

    def identify(port):
        asked.append(port)
        return None

    answer = flashing.flash(["DC Probe"], sketch_root=tree, stamp=stamp,
                            tools=path_tools, run=run, ports=["COM2"],
                            identify=identify)
    assert answer["returncode"] == 0 and asked == [] and run.calls == []


def test_a_board_not_plugged_in_is_said_and_is_not_a_failure(tree, stamp, path_tools):
    lines = []
    answer = flashing.flash(["Stepper Probe", "DC Probe"], sketch_root=tree,
                            stamp=stamp, tools=path_tools, run=Runner(),
                            ports=["COM1"], identify=answering({"COM1": "Stepper Probe"}),
                            on_line=lines.append)
    assert answer["returncode"] == 0 and answer["absent"] == ["DC Probe"]
    assert "Not connected (nothing flashed, nothing recorded): DC Probe" in lines


def test_missing_tools_refuse_before_any_port(tree, stamp):
    asked = []
    answer = flashing.flash(None, sketch_root=tree, stamp=stamp,
                            tools=flashing.Tools(None, "/x"), run=Runner(),
                            ports=["COM1"], identify=lambda p: asked.append(p))
    assert answer["returncode"] == 1 and asked == []


def test_a_runner_that_raises_fails_that_board(tree, stamp, path_tools):
    def broken(argv, cwd, on_line, timeout, env=None):
        raise OSError("no such tool")
    lines = []
    answer = flashing.flash(["DC Probe"], sketch_root=tree, stamp=stamp,
                            tools=path_tools, run=broken, ports=["COM2"],
                            identify=answering({"COM2": "DC Probe"}),
                            on_line=lines.append)
    assert answer["results"] == {"DC Probe": "FAILED"}
    assert any("no such tool" in l for l in lines)
    assert not stamp.exists()


def test_a_runner_that_times_out_fails_that_board(tree, stamp, path_tools):
    answer = flashing.flash(["DC Probe"], sketch_root=tree, stamp=stamp,
                            tools=path_tools, run=lambda *a, **k: None,
                            ports=["COM2"], identify=answering({"COM2": "DC Probe"}))
    assert answer["results"] == {"DC Probe": "FAILED"}


def test_the_default_runner_passes_the_environment(tmp_path):
    seen = []
    code = flashing.stream_lines(
        [sys.executable, "-c", "import os; print(os.environ['ARDUINO_DIRECTORIES_DATA'])"],
        cwd=str(tmp_path), on_line=seen.append, timeout=30,
        env=dict(os.environ, ARDUINO_DIRECTORIES_DATA="/bundle/data"))
    assert code == 0 and seen == ["/bundle/data"]


# -- the command line: a thin shell with no legacy import ------------------------

def test_the_command_line_imports_nothing_from_legacy():
    text = (REPO / "firmware" / "flash_firmware.py").read_text()
    assert "legacy" not in text.split('"""', 2)[2]
    assert "from controller import flashing" in text


def test_the_command_line_keeps_its_flags(tree, stamp):
    done = subprocess.run(
        [sys.executable, str(REPO / "firmware" / "flash_firmware.py"), "--help"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO))
    assert done.returncode == 0
    for flag in ("--list", "--only", "--port", "--yes", "--dry-run",
                 "--install-deps", "--sketch-root", "--force", "--stamp",
                 "--no-detect", "--channel"):
        assert flag in done.stdout, flag


def test_the_command_lines_dry_run_prints_the_commands_and_records_nothing(tree, stamp):
    done = subprocess.run(
        [sys.executable, str(REPO / "firmware" / "flash_firmware.py"), "--dry-run",
         "--no-detect", "--yes", "--sketch-root", str(tree), "--stamp", str(stamp),
         "--port", "Stepper Probe=COM7"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "--upload -p COM7" in done.stdout
    assert "Stepper Probe            ok (dry run)" in done.stdout
    assert not stamp.exists()


# -- Classic and the station on the same boards (2026-10-08) ---------------------
# `dev/swap_branch.sh legacy` flashed with the legacy tree's own script, which
# never wrote the stamp: the Launcher then read "already current" and started
# the station on Megas still running the legacy sketches (the stepper took the
# station's 'e' and 42-byte jog packets, logged the gamepad and never moved).
# The swap now flashes through this command line, so both apps' flashes land
# in the one stamp.

def test_a_classic_flash_lands_in_the_stamp_so_the_station_reflashes_it(
        tmp_path, stamp, path_tools):
    from controller.firmware import FirmwareCheck
    station = REPO / "firmware"
    legacy = tmp_path / "legacy-app" / "firmware"
    shutil.copytree(station, legacy, ignore=shutil.ignore_patterns(
        "build", "__pycache__", "libraries"))
    for name in ("stepper_firmware", "chuck_firmware"):
        (legacy / name / f"{name}.ino").write_text(
            "// the legacy sketch: 't' toggles enable, 28-byte jog packet\n")
    megas = ["Stepper Probe", "DC Probe", "Chuck Positioner"]
    boards = {"/dev/ttyACM0": "Stepper Probe", "/dev/ttyACM1": "DC Probe",
              "/dev/ttyACM2": "Chuck Positioner"}

    def flash(root, channel):
        run = Runner()
        answer = flashing.flash(megas, sketch_root=root, stamp=stamp,
                                tools=path_tools, run=run, ports=list(boards),
                                identify=answering(boards), channel=channel)
        assert answer["returncode"] == 0
        return [c["argv"][-1] for c in run.calls]

    flash(station, flashing.STATION)                    # the Launcher
    # Classic: the DC Probe's sketch is the same in both trees, so it stays.
    assert flash(legacy, flashing.STABLE) == [str(legacy / "stepper_firmware"),
                                              str(legacy / "chuck_firmware")]
    recorded = json.loads(stamp.read_text())
    assert recorded["Stepper Probe"]["channel"] == "stable"
    assert recorded["DC Probe"]["channel"] == "station"
    # What the Launcher's flash and Setup's Firmware row read next.
    check = FirmwareCheck(root=REPO, stamp=stamp, tools=path_tools).check()
    assert check["stale"] == ["Stepper Probe", "Chuck Positioner"]
    assert flash(station, flashing.STATION) == [str(station / "stepper_firmware"),
                                                str(station / "chuck_firmware")]
    assert FirmwareCheck(root=REPO, stamp=stamp, tools=path_tools).check()["stale"] == []


# -- rb-dist-build's findings: the table the bundle reads, the libraries -------

def _literals(path):
    """What packaging/layout.py's `flash_constants` reads: the top-level
    literal assignments, by `ast`, importing nothing."""
    import ast
    found = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            try:
                found[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return found


def test_the_command_line_carries_the_table_as_literals_the_bundle_build_reads():
    """packaging/layout.py and tools.py read DEVICES, the FQBNs and the
    library lists from flash_firmware.py with `ast`: they must be literals
    there, and equal to the table the code uses."""
    found = _literals(REPO / "firmware" / "flash_firmware.py")
    assert found["DEVICES"] == flashing.BOARDS
    for name in ("MEGA_FQBN", "TEENSY_FQBN", "TEENSY_MCU", "MEGA_LIBS", "TEENSY_LIBS"):
        assert found[name] == getattr(flashing, name), name


def test_the_teensy_needs_max6675_which_its_core_does_not_ship():
    sketch = (REPO / "firmware" / "temp_controller" / "temp_controller.ino").read_text()
    assert "#include <MAX6675.h>" in sketch
    assert "MAX6675" in flashing.TEENSY_LIBS
    assert "MAX6675" in flashing.install_deps_commands(
        flashing.Tools("cli", "loader"))[-1]


def test_no_command_passes_the_vendored_libraries(tmp_path):
    """The registry LiquidCrystal_I2C wins: the vendored NewLiquidCrystal of
    the same name does not compile the sketch (`lcd.init()` is private
    there), so `--libraries firmware/libraries` is never passed."""
    tree = make_tree(tmp_path / "firmware")
    tools = flashing.Tools("cli", "loader")
    for board in flashing.BOARDS:
        for argv in flashing.commands(board, "COM1", tree, tools):
            assert "--libraries" not in argv and "--library" not in argv
    sketch = (REPO / "firmware" / "temp_controller" / "temp_controller.ino").read_text()
    assert "LiquidCrystal_I2C lcd(0x27, 20, 4);" in sketch and "lcd.init();" in sketch


# -- a tool that fails says what the operator can do --------------------------

class Saying(Runner):
    def __init__(self, line, code=1):
        super().__init__()
        self.line, self.rc = line, code

    def __call__(self, argv, cwd, on_line, timeout, env=None):
        self.calls.append({"argv": list(argv)})
        on_line(self.line)
        return self.rc


@pytest.mark.parametrize("board, line, words", [
    ("DC Probe", "fork/exec /t/avr-g++: bad CPU type in executable", "Rosetta 2"),
    ("Temperature Controller",
     "teensy_loader_cli: error while loading shared libraries: libusb-0.1.so.4: "
     "cannot open shared object file", "libusb-0.1-4"),
    ("Stepper Probe", "avrdude: ser_open(): can't open device \"/dev/ttyACM0\": "
     "Permission denied", "dialout"),
    ("Temperature Controller", "Unable to open device", "udev rules"),
])
def test_a_tool_failure_names_what_to_do(tree, stamp, path_tools, board, line, words):
    lines = []
    answer = flashing.flash([board], sketch_root=tree, stamp=stamp, tools=path_tools,
                            run=Saying(line), ports=["P"], identify=answering({"P": board}),
                            on_line=lines.append)
    assert answer["results"] == {board: "FAILED"}
    [hint] = answer["hints"]
    assert words in hint
    assert f"[HINT] {hint}" in lines


# -- a Teensy is targeted by its own port, never "whichever Teensy answers" ------

def test_the_teensy_is_rebooted_through_its_own_port_and_loaded_without_soft_reboot(
        tree, stamp, path_tools, reboots):
    """2026-10-09: `teensy_loader_cli -s` soft-reboots whichever Teensy it
    finds; with the heater and three XYZ axis boards on one station that is
    the wrong board. The station reboots the Teensy on the identified port
    (134 baud) and the loader runs without -s, after that reboot."""
    order = []
    reboots_seen = reboots

    def run(argv, cwd, on_line, timeout, env=None):
        order.append(("run", argv[0]))
        return 0

    original = flashing.reboot_teensy

    def reboot(port):
        order.append(("reboot", port))
        original(port)
    flashing.reboot_teensy = reboot
    try:
        answer = flashing.flash(["Temperature Controller"], sketch_root=tree, stamp=stamp,
                                tools=path_tools, run=run, ports=["/dev/ttyACM7"],
                                identify=answering({"/dev/ttyACM7": "Temperature Controller"}))
    finally:
        flashing.reboot_teensy = original
    assert answer["results"] == {"Temperature Controller": "ok"}
    assert reboots_seen == ["/dev/ttyACM7"]
    assert order == [("run", "/usr/bin/arduino-cli"), ("reboot", "/dev/ttyACM7"),
                     ("run", "/usr/bin/teensy_loader_cli")]


def test_no_teensy_command_soft_reboots_an_arbitrary_board(tree, path_tools):
    steps = flashing.commands("Temperature Controller", "/dev/ttyACM7", tree, path_tools)
    loader = [step for step in steps if isinstance(step, list)
              and step[0].endswith("teensy_loader_cli")]
    assert loader and all("-s" not in step for step in loader)
    assert (flashing.REBOOT, "/dev/ttyACM7") in steps


def test_a_teensy_that_cannot_be_rebooted_fails_without_running_the_loader(
        tree, stamp, path_tools, monkeypatch):
    ran = []

    def run(argv, cwd, on_line, timeout, env=None):
        ran.append(argv[0])
        return 0

    def refuse(port):
        raise OSError(16, "Resource busy")
    monkeypatch.setattr(flashing, "reboot_teensy", refuse)
    answer = flashing.flash(["Temperature Controller"], sketch_root=tree, stamp=stamp,
                            tools=path_tools, run=run, ports=["P"],
                            identify=answering({"P": "Temperature Controller"}))
    assert answer["results"] == {"Temperature Controller": "FAILED"}
    assert answer["hints"] == [flashing.TEENSY_REBOOT_HINT]
    assert not any("teensy_loader_cli" in tool for tool in ran)
    assert not stamp.exists()          # nothing recorded for a failed upload


def test_a_dry_run_names_the_reboot_and_reboots_nothing(tree, stamp, path_tools, reboots):
    lines = []
    flashing.flash(["Temperature Controller"], sketch_root=tree, stamp=stamp,
                   tools=path_tools, run=lambda *a, **k: 0, ports=["P"], dry_run=True,
                   identify=answering({"P": "Temperature Controller"}), on_line=lines.append)
    assert any("reboot the Teensy on P into its bootloader" in line for line in lines)
    assert reboots == []


def test_a_teensy_that_never_reaches_its_bootloader_says_so(tree, stamp, path_tools):
    def run(argv, cwd, on_line, timeout, env=None):
        return None if "teensy_loader_cli" in argv[0] else 0
    answer = flashing.flash(["Temperature Controller"], sketch_root=tree, stamp=stamp,
                            tools=path_tools, run=run, ports=["P"],
                            identify=answering({"P": "Temperature Controller"}))
    assert answer["hints"] == [flashing.TEENSY_WAIT_HINT]


def test_an_ordinary_failure_adds_no_hint(tree, stamp, path_tools):
    answer = flashing.flash(["DC Probe"], sketch_root=tree, stamp=stamp, tools=path_tools,
                            run=Saying("avrdude: stk500v2_getsync(): timeout"),
                            ports=["P"], identify=answering({"P": "DC Probe"}))
    assert answer["results"] == {"DC Probe": "FAILED"} and answer["hints"] == []


# -- the XYZ Stage: three boards, one per axis tag, all or none (2026-10-09) -----

XYZ = {"/dev/ttyACM1": "X", "/dev/ttyACM2": "Y", "/dev/ttyACM3": "Z"}


def axis_boards(tags):
    """identify= and tag_of= for {port: tag}: every port answers XYZ Stage."""
    return (lambda port: "XYZ Stage" if port in tags else None), tags.get


class AxisProbe:
    """Setup's handshake as flashing sees it: `identify` names the model and
    leaves the tag the board answered in `probe_tags`."""

    def __init__(self, tags):
        self.probe_tags = dict(tags)

    def identify(self, port):
        return "XYZ Stage" if port in self.probe_tags else None


def flash_xyz(tree, stamp, tools, tags, run=None, lines=None, **kwargs):
    return flashing.flash(["XYZ Stage"], sketch_root=tree, stamp=stamp, tools=tools,
                          run=run or Runner(), ports=list(tags),
                          identify=AxisProbe(tags).identify,
                          on_line=(lines.append if lines is not None else None), **kwargs)


def test_detect_places_a_complete_set_by_tag_not_by_port_order():
    tags = {"/dev/ttyACM1": "Z", "/dev/ttyACM2": "X", "/dev/ttyACM3": "Y"}
    identify, tag_of = axis_boards(tags)
    faults = {}
    found = flashing.detect(list(tags), identify, tag_of=tag_of, faults=faults)
    assert found == {"XYZ Stage": {"X": "/dev/ttyACM2", "Y": "/dev/ttyACM3",
                                   "Z": "/dev/ttyACM1"}}
    assert faults == {}


@pytest.mark.parametrize("tags, reason", [
    ({"/dev/ttyACM1": "X", "/dev/ttyACM2": "Y"}, "Z missing"),
    ({"/dev/ttyACM1": "X", "/dev/ttyACM2": "X", "/dev/ttyACM3": "Y",
      "/dev/ttyACM4": "Z"}, "two boards answered as X (/dev/ttyACM1, /dev/ttyACM2)"),
    ({"/dev/ttyACM1": "X", "/dev/ttyACM2": "Y", "/dev/ttyACM3": None},
     "Z missing; no X/Y/Z tag on /dev/ttyACM3"),
])
def test_detect_finds_no_partial_set_and_names_the_fault(tags, reason):
    identify, tag_of = axis_boards(tags)
    faults, lines = {}, []
    found = flashing.detect(list(tags), identify, lines.append, tag_of=tag_of,
                            faults=faults)
    assert found == {}
    assert faults == {"XYZ Stage": reason}
    assert any("NOT flashed" in line and reason in line for line in lines)


def test_detect_reads_the_tags_the_probes_handshake_recorded():
    probe = AxisProbe({"COM4": "X", "COM5": "Y", "COM6": "Z"})
    found = flashing.detect(["COM4", "COM5", "COM6"], probe.identify)
    assert found == {"XYZ Stage": {"X": "COM4", "Y": "COM5", "Z": "COM6"}}


def test_all_three_axis_boards_are_flashed_each_on_its_own_port_and_stamped(
        tree, stamp, path_tools, reboots):
    run = Runner()
    answer = flash_xyz(tree, stamp, path_tools, XYZ, run=run)
    assert answer["returncode"] == 0
    assert answer["results"] == {"XYZ Stage X": "ok", "XYZ Stage Y": "ok",
                                 "XYZ Stage Z": "ok"}
    # Each axis board is rebooted through its own port, never "whichever answers".
    assert reboots == ["/dev/ttyACM1", "/dev/ttyACM2", "/dev/ttyACM3"]
    loads = [c["argv"] for c in run.calls if c["argv"][0].endswith("teensy_loader_cli")]
    assert len(loads) == 3 and all("-s" not in argv for argv in loads)
    recorded = json.loads(stamp.read_text())
    digest = flashing.sketch_hash(tree, "XYZ Stage")
    assert {k: (v["hash"], v["port"]) for k, v in recorded.items()} == {
        "XYZ Stage X": (digest, "/dev/ttyACM1"), "XYZ Stage Y": (digest, "/dev/ttyACM2"),
        "XYZ Stage Z": (digest, "/dev/ttyACM3")}


def test_a_partial_set_is_a_fault_that_flashes_nothing(tree, stamp, path_tools,
                                                         reboots):
    run, lines = Runner(), []
    answer = flash_xyz(tree, stamp, path_tools, {"/dev/ttyACM1": "X", "/dev/ttyACM2": "Y"},
                       run=run, lines=lines)
    assert answer["returncode"] == 1
    assert answer["faults"] == {"XYZ Stage": "Z missing"}
    assert answer["results"] == {"XYZ Stage": "FAULT: Z missing, nothing flashed"}
    assert answer["absent"] == []
    assert run.calls == [] and reboots == [] and not stamp.exists()
    assert lines[-1].strip() == "XYZ Stage                FAULT: Z missing, nothing flashed"


def test_no_axis_board_at_all_is_not_connected_not_a_fault(tree, stamp, path_tools):
    answer = flash_xyz(tree, stamp, path_tools, {})
    assert answer["returncode"] == 0 and answer["absent"] == ["XYZ Stage"]
    assert answer["results"] == {}


def test_a_set_with_one_axis_behind_reflashes_all_three(tree, stamp, path_tools):
    digest = flashing.sketch_hash(tree, "XYZ Stage")
    for tag, hashed in (("X", digest), ("Y", digest), ("Z", "0" * 64)):
        flashing.record_flash(stamp, f"XYZ Stage {tag}", hashed,
                              tree / "xyz_stage_axis", f"/dev/ttyACM{tag}")
    run = Runner()
    answer = flash_xyz(tree, stamp, path_tools, XYZ, run=run)
    assert set(answer["results"]) == {"XYZ Stage X", "XYZ Stage Y", "XYZ Stage Z"}


def test_a_current_set_runs_nothing(tree, stamp, path_tools):
    digest = flashing.sketch_hash(tree, "XYZ Stage")
    for tag in "XYZ":
        flashing.record_flash(stamp, f"XYZ Stage {tag}", digest,
                              tree / "xyz_stage_axis", "p")
    run = Runner()
    answer = flash_xyz(tree, stamp, path_tools, XYZ, run=run)
    assert answer["returncode"] == 0 and run.calls == []


def test_an_axis_board_is_never_assigned_by_hand(tree, stamp, path_tools):
    lines, run = [], Runner()
    answer = flashing.flash(["XYZ Stage"], sketch_root=tree, stamp=stamp,
                            tools=path_tools, run=run, manual={"XYZ Stage": "COM9"},
                            detect_ports=False, on_line=lines.append)
    assert answer["returncode"] == 1 and run.calls == []
    assert any("flashed only by detection" in line for line in lines)


def test_only_the_boards_asked_for_need_a_sketch(tmp_path, stamp, path_tools):
    """The stable tree has no XYZ Stage sketch: flashing its four boards
    must not refuse over the fifth."""
    stable = make_tree(tmp_path / "stable", {b: flashing.BOARDS[b]
                                             for b in flashing.STABLE_BOARDS})
    answer = flashing.flash(["DC Probe"], sketch_root=stable, stamp=stamp,
                            tools=path_tools, run=Runner(), ports=["COM2"],
                            identify=answering({"COM2": "DC Probe"}), channel="stable")
    assert answer["results"] == {"DC Probe": "ok"}
    missing = flashing.flash(["XYZ Stage"], sketch_root=stable, stamp=stamp,
                             tools=path_tools, run=Runner(), ports=[],
                             identify=answering({}))
    assert missing["returncode"] == 1 and missing["results"] == {}


def test_the_reboot_opens_only_that_port_at_134_baud_through_the_serial_owner(monkeypatch):
    from devices import serial_port
    opened = []
    monkeypatch.setattr(serial_port, "touch", lambda port, baud: opened.append((port, baud)))
    REAL_REBOOT("/dev/ttyACM2")
    assert opened == [("/dev/ttyACM2", 134)]


def test_touch_opens_at_the_baud_sends_nothing_and_closes(monkeypatch):
    from devices import serial_port

    class Handle:
        closed = False
        written = b""

        def write(self, data):
            self.written += data

        def close(self):
            self.closed = True
    handle, settings = Handle(), {}

    def opened(**kwargs):
        settings.update(kwargs)
        return handle
    monkeypatch.setattr(serial_port, "pyserial", object())
    monkeypatch.setattr(serial_port, "_open_serial", opened)
    serial_port.touch("COM5", 134)
    assert settings["port"] == "COM5" and settings["baudrate"] == 134
    assert handle.closed and handle.written == b""


def test_touch_that_cannot_open_is_a_transport_error(monkeypatch):
    from devices import serial_port

    def refused(**kwargs):
        raise OSError(16, "Resource busy")
    monkeypatch.setattr(serial_port, "pyserial", object())
    monkeypatch.setattr(serial_port, "_open_serial", refused)
    with pytest.raises(serial_port.TransportError, match="COM5 at 134"):
        serial_port.touch("COM5", 134)


def test_a_board_behind_a_held_port_is_a_failure_not_a_silent_ok(tree, stamp, path_tools,
                                                                   monkeypatch):
    """2026-10-09: with a station still holding the ports, the Classic icon's
    flash found nothing, said "ok", and the original app started on the
    station's sketches. A needed board missing beside a port another program
    holds now fails, names the port, and flashes nothing."""
    class HeldProbe:
        busy_ports = ["/dev/ttyACM0"]

        def scan_ports(self):
            return ["/dev/ttyACM0"]

        def identify(self, port, should_abort=None):
            return None                      # held: nothing could be asked

    monkeypatch.setattr(flashing, "default_probe", lambda: HeldProbe())
    lines, runner = [], Runner()
    answer = flashing.flash(["Stepper Probe"], sketch_root=tree, stamp=stamp,
                            tools=path_tools, run=runner, on_line=lines.append)
    assert answer["returncode"] == 1
    assert any("/dev/ttyACM0 is in use by another program" in line for line in lines), lines
    assert runner.calls == [] and not stamp.exists()
    # Nothing held: a board that is simply absent is still not a failure.
    HeldProbe.busy_ports = []
    answer = flashing.flash(["Stepper Probe"], sketch_root=tree, stamp=stamp,
                            tools=path_tools, run=Runner(), on_line=lines.append)
    assert answer["returncode"] == 0 and answer["absent"] == ["Stepper Probe"]


def test_an_incomplete_axis_set_beside_a_held_port_names_the_port(tree, stamp, path_tools,
                                                                  monkeypatch):
    """The missing axis board may be the one behind the held port: say so,
    not just "Z missing", and flash nothing."""
    class HeldAxes(AxisProbe):
        busy_ports = ["/dev/ttyACM9"]

        def scan_ports(self):
            return ["/dev/ttyACM1", "/dev/ttyACM2", "/dev/ttyACM9"]

    monkeypatch.setattr(flashing, "default_probe",
                        lambda: HeldAxes({"/dev/ttyACM1": "X", "/dev/ttyACM2": "Y"}))
    lines, runner = [], Runner()
    answer = flashing.flash(["XYZ Stage"], sketch_root=tree, stamp=stamp,
                            tools=path_tools, run=runner, on_line=lines.append)
    assert answer["returncode"] == 1 and runner.calls == [] and not stamp.exists()
    assert any("/dev/ttyACM9 is in use by another program" in line
               and "XYZ Stage could not be checked" in line for line in lines), lines


# -- R-3: a Teensy left in its bootloader stops every later Teensy (2026-10-09) --
# The loader runs without -s and programs the first HalfKay device it finds. A
# Teensy whose upload failed after its 134-baud reboot may still be waiting in
# HalfKay, so the next Teensy's loader could write ITS sketch onto that board.

HEATER = {"/dev/ttyACM0": "Temperature Controller"}


class StationProbe(AxisProbe):
    """The heater (or any untagged board) and the three axis boards on one
    station, as Setup's handshake answers them."""

    def __init__(self, tags, others):
        super().__init__(tags)
        self.others = dict(others)

    def identify(self, port):
        return self.others.get(port) or super().identify(port)


def flash_station(tree, stamp, tools, run, boards=("Temperature Controller", "XYZ Stage"),
                  others=HEATER, tags=XYZ, **kwargs):
    probe = StationProbe(tags, others)
    return flashing.flash(list(boards), sketch_root=tree, stamp=stamp, tools=tools,
                          run=run, ports=list(others) + list(tags),
                          identify=probe.identify, **kwargs)


class Nth(Runner):
    """Fails the `n`-th call (1-based) whose argv `pick` accepts; 0 otherwise."""

    def __init__(self, pick, n=1, code=1):
        super().__init__(code=code)
        self.pick, self.n, self.picked = pick, n, 0

    def __call__(self, argv, cwd, on_line, timeout, env=None):
        self.calls.append({"argv": list(argv), "cwd": cwd, "env": env,
                           "timeout": timeout})
        on_line("tool output")
        if self.pick(list(argv)):
            self.picked += 1
            if self.picked == self.n:
                return self.code
        return 0


def is_loader(argv):
    return argv[0].endswith("teensy_loader_cli")


def is_compile(argv):
    return "compile" in argv


def test_r3_a_teensy_that_failed_after_its_reboot_stops_every_later_teensy(
        tree, stamp, path_tools, reboots):
    """The heater's upload fails after its reboot, so it waits in HalfKay. The
    run went on to reboot "XYZ Stage X", whose loader found the heater first:
    xyz_stage_axis on the heater, and the stamp said "XYZ Stage X ok"."""
    run, lines = Runner(fail=["temp_controller.ino.hex"]), []
    answer = flash_station(tree, stamp, path_tools, run, on_line=lines.append)
    assert reboots == ["/dev/ttyACM0"]
    assert len([c for c in run.calls if is_loader(c["argv"])]) == 1
    assert answer["returncode"] == 1
    assert answer["results"]["Temperature Controller"] == "FAILED"
    for axis in "XYZ":
        status = answer["results"][f"XYZ Stage {axis}"]
        assert status.startswith("NOT FLASHED") and "bootloader" in status, status
        assert "Temperature Controller" in status
    assert not stamp.exists()
    [hint] = [h for h in answer["hints"] if "unplug" in h]
    assert "Temperature Controller on /dev/ttyACM0" in hint and "press its button" in hint
    assert f"[HINT] {hint}" in lines


def test_r3_a_reboot_that_raised_may_still_have_reached_the_board(
        tree, stamp, path_tools, monkeypatch):
    """The 134-baud open can reboot the Teensy and still raise (it drops off
    USB under pyserial's next ioctl), so the heater may be in HalfKay: no
    later Teensy is touched."""
    touched = []

    def reboot(port):
        touched.append(port)
        if port == "/dev/ttyACM0":
            raise OSError(5, "Input/output error")
    monkeypatch.setattr(flashing, "reboot_teensy", reboot)
    run = Runner()
    answer = flash_station(tree, stamp, path_tools, run)
    assert touched == ["/dev/ttyACM0"]
    assert not any(is_loader(c["argv"]) for c in run.calls)
    assert answer["returncode"] == 1
    assert all("bootloader" in answer["results"][f"XYZ Stage {axis}"] for axis in "XYZ")
    assert flashing.TEENSY_REBOOT_HINT in answer["hints"]


def test_r3_the_deadline_is_checked_before_the_reboot(tree, stamp, path_tools, reboots,
                                                     monkeypatch):
    """The compile used the whole budget: the Teensy is not rebooted into a
    bootloader no loader will be run for."""
    clock = [0.0]
    monkeypatch.setattr(flashing, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def run(argv, cwd, on_line, timeout, env=None):
        clock[0] += 1000.0              # the compile ran past the deadline
        return 0
    lines = []
    answer = flashing.flash(["Temperature Controller"], sketch_root=tree, stamp=stamp,
                            tools=path_tools, run=run, ports=["P"], timeout=100,
                            identify=answering({"P": "Temperature Controller"}),
                            on_line=lines.append)
    assert reboots == []
    assert answer["results"] == {"Temperature Controller": "FAILED"}
    assert "The flash took longer than 100 s and was stopped." in lines
    assert not stamp.exists()


def test_r3_a_mega_after_a_teensy_left_in_its_bootloader_still_flashes(
        tree, stamp, path_tools, reboots):
    """A Mega is uploaded by avrdude on its own serial port: HalfKay is not in
    its way."""
    run = Runner(fail=["temp_controller.ino.hex"])
    answer = flash_station(tree, stamp, path_tools, run,
                           boards=("Temperature Controller", "DC Probe"),
                           others={"/dev/ttyACM0": "Temperature Controller",
                                   "/dev/ttyACM5": "DC Probe"}, tags={})
    assert answer["results"] == {"Temperature Controller": "FAILED", "DC Probe": "ok"}
    assert answer["returncode"] == 1
    assert set(json.loads(stamp.read_text())) == {"DC Probe"}


def test_r3_a_teensy_that_failed_before_its_reboot_does_not_stop_the_next(
        tree, stamp, path_tools, reboots):
    """The heater's compile failed: it was never rebooted, so it is running
    its old sketch, not waiting in HalfKay, and the axis set still flashes."""
    run = Runner(fail=["temp_controller"])
    answer = flash_station(tree, stamp, path_tools, run)
    assert answer["results"] == {"Temperature Controller": "FAILED", "XYZ Stage X": "ok",
                                 "XYZ Stage Y": "ok", "XYZ Stage Z": "ok"}
    assert reboots == list(XYZ)


# -- R-6: the axis set is asked once and flashed all or none (2026-10-09) --------

def test_r6_the_axis_set_is_confirmed_once_with_every_port_named(tree, stamp, path_tools):
    asked = []

    def confirm(board, port):
        asked.append((board, port))
        return True
    answer = flash_xyz(tree, stamp, path_tools, XYZ, confirm=confirm)
    assert [board for board, _ in asked] == ["XYZ Stage"]
    assert all(f"{tag} {port}" in asked[0][1] for port, tag in XYZ.items())
    assert answer["returncode"] == 0
    assert set(answer["results"]) == {"XYZ Stage X", "XYZ Stage Y", "XYZ Stage Z"}


def test_r6_declining_the_set_flashes_none_of_it(tree, stamp, path_tools, reboots):
    run = Runner()
    answer = flash_xyz(tree, stamp, path_tools, XYZ, run=run, confirm=lambda b, p: False)
    assert answer["results"] == {"XYZ Stage": "skipped"}
    assert answer["returncode"] == 0
    assert run.calls == [] and reboots == [] and not stamp.exists()


def test_r6_once_an_axis_board_fails_the_rest_of_the_set_is_not_flashed(
        tree, stamp, path_tools, reboots):
    """X fails before its reboot (no HalfKay): Y and Z still are not flashed,
    and say why; the board is reported as failed."""
    run, lines = Nth(is_compile, n=1), []
    answer = flash_xyz(tree, stamp, path_tools, XYZ, run=run, lines=lines)
    assert len(run.calls) == 1 and reboots == []
    results = answer["results"]
    assert results["XYZ Stage X"] == "FAILED"
    for axis in "YZ":
        assert results[f"XYZ Stage {axis}"].startswith("NOT FLASHED")
        assert "XYZ Stage X failed" in results[f"XYZ Stage {axis}"]
    assert results["XYZ Stage"] == "FAILED"
    assert answer["returncode"] == 1 and not stamp.exists()
    assert any("not flashed" in line.lower() and "XYZ Stage X failed" in line
               for line in lines if not line.startswith("  XYZ Stage")), lines


def test_r6_a_set_that_fails_partway_stops_there(tree, stamp, path_tools, reboots):
    """Y's upload fails after X's went on: X stays recorded (it does run the
    new sketch), Z is not touched, the board is reported as failed."""
    run = Nth(is_loader, n=2)
    answer = flash_xyz(tree, stamp, path_tools, XYZ, run=run)
    assert reboots == ["/dev/ttyACM1", "/dev/ttyACM2"]
    results = answer["results"]
    assert results["XYZ Stage X"] == "ok" and results["XYZ Stage Y"] == "FAILED"
    assert results["XYZ Stage Z"].startswith("NOT FLASHED")
    assert results["XYZ Stage"] == "FAILED" and answer["returncode"] == 1
    assert set(json.loads(stamp.read_text())) == {"XYZ Stage X"}


def test_a_dry_run_of_the_axis_set_names_the_set_then_each_board(tree, stamp, path_tools,
                                                                 reboots):
    lines = []
    answer = flash_xyz(tree, stamp, path_tools, XYZ, lines=lines, dry_run=True)
    assert answer["returncode"] == 0 and reboots == [] and not stamp.exists()
    set_line = "XYZ Stage on X /dev/ttyACM1, Y /dev/ttyACM2, Z /dev/ttyACM3 (all or none):"
    units = [f"XYZ Stage {tag} on {port}:" for port, tag in XYZ.items()]
    assert lines.index(set_line) < lines.index(units[0]) < lines.index(units[1]) \
        < lines.index(units[2])
    assert answer["results"] == {f"XYZ Stage {tag}": "ok (dry run)" for tag in "XYZ"}
