"""Unit tests for firmware/flash_firmware.py -- no hardware, no arduino-cli.

Everything that would touch a serial port or shell out is mocked. The point
of these tests is the two things that are easy to get wrong and impossible to
notice until you are standing at the bench: that the DEV: handshake is read
correctly, and that Windows COM ports survive detection.
"""
import io
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

FIRMWARE_DIR = Path(__file__).resolve().parent.parent / "firmware"
if str(FIRMWARE_DIR) not in sys.path:
    sys.path.insert(0, str(FIRMWARE_DIR))

import flash_firmware  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
class FakeSerial:
    """Minimal stand-in for serial.Serial used as a context manager.

    ``chunks`` is a list of byte strings handed out one read() at a time.
    """

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.written = []

    # context-manager protocol -- probe_device_at uses `with serial.Serial(...)`
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def in_waiting(self):
        return len(self._chunks[0]) if self._chunks else 0

    def read(self, _n):
        return self._chunks.pop(0)

    def write(self, data):
        self.written.append(data)
        return len(data)

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass


@pytest.fixture
def fast_clock(monkeypatch):
    """Make probe_device_at's 2 s settle and 1.5 s read window instant.

    time.time() advances 0.2 s per call, so the read window closes after a
    handful of iterations instead of wall-clock 1.5 s.
    """
    state = {"t": 0.0}

    def fake_time():
        state["t"] += 0.2
        return state["t"]

    fake = types.SimpleNamespace(time=fake_time, sleep=lambda _s: None)
    monkeypatch.setattr(flash_firmware, "time", fake)
    return state


def _port(name):
    return types.SimpleNamespace(device=name)


# --------------------------------------------------------------------------
# probe_device_at
# --------------------------------------------------------------------------
def test_probe_identifies_stepper_from_dev_s(fast_clock):
    fake = FakeSerial([b"DEV: s\r\n"])
    with mock.patch.object(flash_firmware.serial, "Serial", return_value=fake) as ser_cls:
        assert flash_firmware.probe_device_at("COM3") == "Stepper Probe"

    # opened with the same handshake parameters mainGUI uses
    kwargs = ser_cls.call_args.kwargs
    assert ser_cls.call_args.args[0] == "COM3"
    assert kwargs["baudrate"] == 500000
    assert kwargs["timeout"] == pytest.approx(0.1)
    assert kwargs["write_timeout"] == pytest.approx(0.2)
    assert fake.written == [b"s\n"]


@pytest.mark.parametrize(
    "payload,expected",
    [
        (b"DEV: s", "Stepper Probe"),
        (b"DEV: d", "DC Probe"),
        (b"DEV: c", "Chuck Positioner"),
        (b"DEV: t", "Temperature Controller"),
    ],
)
def test_probe_maps_every_dev_code(fast_clock, payload, expected):
    fake = FakeSerial([payload])
    with mock.patch.object(flash_firmware.serial, "Serial", return_value=fake):
        assert flash_firmware.probe_device_at("COM4") == expected


def test_probe_returns_none_when_board_says_nothing(fast_clock):
    fake = FakeSerial([])  # in_waiting stays 0 for the whole read window
    with mock.patch.object(flash_firmware.serial, "Serial", return_value=fake):
        assert flash_firmware.probe_device_at("COM5") is None


def test_probe_returns_none_and_prints_when_open_raises(fast_clock):
    boom = OSError("could not open port COM9: Access is denied.")
    buf = io.StringIO()
    with mock.patch.object(flash_firmware.serial, "Serial", side_effect=boom):
        with redirect_stdout(buf):
            assert flash_firmware.probe_device_at("COM9") is None

    out = buf.getvalue()
    assert "COM9" in out
    # the failure is reported, not swallowed: the exception text is printed
    assert "Access is denied" in out


# --------------------------------------------------------------------------
# port enumeration -- the Windows regression
# --------------------------------------------------------------------------
def test_discover_ports_keeps_windows_com_names():
    with mock.patch.object(
        flash_firmware.serial.tools.list_ports,
        "comports",
        return_value=[_port("COM4"), _port("COM3")],
    ):
        assert flash_firmware.discover_ports() == ["COM3", "COM4"]


def test_detect_devices_does_not_filter_com_ports(fast_clock):
    """The lab PC is Windows. Every real port there is named COM<n>; a filter
    on that prefix would detect nothing at all."""
    probed = []

    def fake_probe(port):
        probed.append(port)
        return "Stepper Probe" if port == "COM3" else None

    with mock.patch.object(
        flash_firmware.serial.tools.list_ports,
        "comports",
        return_value=[_port("COM3"), _port("COM4")],
    ), mock.patch.object(flash_firmware, "probe_device_at", side_effect=fake_probe):
        with redirect_stdout(io.StringIO()):
            found = flash_firmware.detect_devices()

    assert probed == ["COM3", "COM4"]
    assert found == {"Stepper Probe": "COM3"}


def test_detect_devices_skips_placeholder_ports(fast_clock):
    with mock.patch.object(
        flash_firmware.serial.tools.list_ports,
        "comports",
        return_value=[_port("COM3"), _port("Headless"), _port("SIM")],
    ), mock.patch.object(flash_firmware, "probe_device_at", return_value=None) as probe:
        with redirect_stdout(io.StringIO()):
            flash_firmware.detect_devices()

    assert [c.args[0] for c in probe.call_args_list] == ["COM3"]


# --------------------------------------------------------------------------
# sketch layout
# --------------------------------------------------------------------------
def test_every_device_sketch_exists_on_this_branch():
    assert flash_firmware.validate_sketches() is True
    for name, cfg in flash_firmware.DEVICES.items():
        sketch = cfg["sketch"]
        assert sketch.is_dir(), name
        assert (sketch / f"{sketch.name}.ino").is_file(), name


def test_validate_sketches_reports_a_missing_sketch():
    broken = dict(flash_firmware.DEVICES)
    broken["Stepper Probe"] = {
        "sketch": flash_firmware.REPO_ROOT / "firmware" / "does_not_exist",
        "board": "mega",
    }
    with mock.patch.object(flash_firmware, "DEVICES", broken):
        with pytest.raises(RuntimeError, match="does_not_exist"):
            flash_firmware.validate_sketches()


# --------------------------------------------------------------------------
# compile_and_upload
# --------------------------------------------------------------------------
def test_dry_run_mega_prints_fqbn_and_sketch_path():
    buf = io.StringIO()
    with mock.patch.object(flash_firmware.subprocess, "run") as run:
        with redirect_stdout(buf):
            assert flash_firmware.compile_and_upload("Stepper Probe", "COM7", dry_run=True) is True
    run.assert_not_called()

    out = buf.getvalue()
    assert "arduino-cli compile" in out
    assert "arduino:avr:mega:cpu=atmega2560" in out
    assert str(flash_firmware.DEVICES["Stepper Probe"]["sketch"]) in out
    assert "--upload -p COM7" in out


def test_dry_run_teensy_prints_compile_and_teensy_loader_lines():
    buf = io.StringIO()
    with mock.patch.object(flash_firmware.subprocess, "run") as run:
        with redirect_stdout(buf):
            assert flash_firmware.compile_and_upload(
                "Temperature Controller", "COM8", dry_run=True
            ) is True
    run.assert_not_called()

    out = buf.getvalue()
    assert "teensy:avr:teensy35" in out
    assert str(flash_firmware.DEVICES["Temperature Controller"]["sketch"]) in out
    assert "teensy_loader_cli --mcu=MK64FX512 -w -v" in out
    assert "temp_controller.ino.hex" in out


def test_dc_probe_maps_to_high_polling_rate_sketch():
    assert flash_firmware.DEVICES["DC Probe"]["sketch"].name == "high_polling_rate"


# --------------------------------------------------------------------------
# protocol banner
# --------------------------------------------------------------------------
def test_protocol_banner_states_the_toggle_and_packet():
    buf = io.StringIO()
    with redirect_stdout(buf):
        flash_firmware.print_protocol_banner()
    out = buf.getvalue()
    assert "'t' toggle" in out
    assert "<BBfffhhhhhhh" in out
    # struct size is computed, not hard-coded -- this is what serialDrive sends
    assert f"{flash_firmware.PACKET_SIZE}-byte" in out


# --------------------------------------------------------------------------
# main() / CLI
# --------------------------------------------------------------------------
def _run_main(argv, detect_result=None):
    """Run main() with argv patched, subprocess blocked and detection stubbed."""
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", ["flash_firmware.py"] + argv), \
            mock.patch.object(flash_firmware.subprocess, "run") as run, \
            mock.patch.object(flash_firmware, "detect_devices",
                              return_value=dict(detect_result or {})), \
            mock.patch.object(flash_firmware.shutil, "which", return_value="/usr/bin/stub"):
        with redirect_stdout(buf):
            rc = flash_firmware.main()
    return rc, buf.getvalue(), run


def test_list_exits_zero_without_running_subprocess():
    rc, out, run = _run_main(["--list"], {"Stepper Probe": "COM3"})
    assert rc == 0
    run.assert_not_called()
    assert "Stepper Probe" in out
    assert "COM3" in out


def test_port_assignment_is_parsed_and_used():
    rc, out, run = _run_main(
        ["--port", "Stepper Probe=COM7", "--list"], detect_result={}
    )
    assert rc == 0
    run.assert_not_called()
    assert "Stepper Probe" in out
    assert "COM7" in out


def test_port_assignment_overrides_autodetect():
    rc, out, run = _run_main(
        ["--port", "Stepper Probe=COM7", "--list"],
        detect_result={"Stepper Probe": "COM3"},
    )
    assert rc == 0
    assert "COM7" in out
    assert "COM3" not in out


def test_port_assignment_then_dry_run_flash_emits_the_command():
    rc, out, run = _run_main(
        ["--port", "Stepper Probe=COM7", "--yes", "--dry-run"], detect_result={}
    )
    assert rc == 0
    run.assert_not_called()
    assert "arduino:avr:mega:cpu=atmega2560" in out
    assert "-p COM7" in out
    assert "'t' toggle" in out  # protocol banner precedes the flash
    assert "Stepper Probe            ok" in out


def test_malformed_port_argument_is_rejected():
    rc, _out, run = _run_main(["--port", "COM7"], detect_result={})
    assert rc == 1
    run.assert_not_called()


def test_unknown_device_in_port_argument_is_rejected():
    rc, _out, run = _run_main(["--port", "Nonexistent Probe=COM7"], detect_result={})
    assert rc == 1
    run.assert_not_called()


def test_only_filters_to_the_named_devices():
    rc, out, _run = _run_main(
        ["--only", "Chuck Positioner", "--list"],
        detect_result={"Stepper Probe": "COM3", "Chuck Positioner": "COM4"},
    )
    assert rc == 0
    assert "Chuck Positioner" in out
    assert "Stepper Probe" not in out


def test_install_deps_names_the_manual_fork_and_does_not_pretend():
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", ["flash_firmware.py", "--install-deps", "--dry-run"]), \
            mock.patch.object(flash_firmware.subprocess, "run") as run:
        with redirect_stdout(buf):
            rc = flash_firmware.main()
    assert rc == 0
    run.assert_not_called()  # --dry-run prints the commands only

    out = buf.getvalue()
    assert "arduino-cli lib install AccelStepper" in out
    assert "arduino-cli lib install TMCStepper" in out
    # the space in the library name is quoted, so the printed line is
    # copy-pasteable and installs one library rather than two
    assert "arduino-cli lib install 'MAX6675 library'" in out
    assert "NewLiquidCrystal" in out
    assert "not in the arduino-cli library index" in out.lower()
