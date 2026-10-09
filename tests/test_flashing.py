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

import pytest

from controller import flashing
from controller import setup as station_setup

REPO = Path(__file__).resolve().parents[1]


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


def test_the_chips_are_three_megas_and_one_teensy():
    assert [cfg["board"] for cfg in flashing.BOARDS.values()] == [
        "mega", "mega", "mega", "teensy"]


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
        ["/usr/bin/teensy_loader_cli", f"--mcu={flashing.TEENSY_MCU}", "-w", "-s",
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
