"""Autodetection: `Setup.identify` and the scan worker around it.

Ported from `tests/core/test_app_bootstrap.py` (the probe_device_at cases) and
`tests/core/test_manager20_scan_abort.py` (a scan can be told to give up).
The three baud steps are the same steps in the same order; what changed is
that they go through `SerialPort` / `serial_port.query` instead of opening
pyserial here, and that a failure is reported instead of swallowed.

No test in this file opens a real port: `SerialPort` and `query` are replaced.
"""
import threading
import time

import pytest

from station import setup as station_setup
from station.controller import Controller
from station.devices import serial_port as serial_port_module
from station.events import events
from station.setup import HEADLESS, Setup

from tests.station.test_setup import RecordingController, make_model_class

PORT = "/dev/ttyFAKE0"


# -- fakes -----------------------------------------------------------------

class FakePort:
    """A SerialPort stand-in.

    `wait_open` follows the real one: True once the connect worker finished
    **and** the port is open, False while connecting *and* when the open
    failed. `status` is what tells those two apart.
    """

    opened = []

    def __init__(self, port, baud_rate=115200):
        self.port, self.baud_rate = port, baud_rate
        self.identity = None
        self.status = "connecting"
        self.is_closed = False
        FakePort.opened.append(baud_rate)

    def open(self):
        pass

    def wait_open(self, timeout=None):
        return self.status in ("verified", "unverified", "simulated")

    def close(self):
        self.is_closed = True


def port_answering(identities, on_open=None, status=None):
    """A SerialPort factory whose identity depends on the baud rate."""
    def factory(port, baud_rate=115200):
        device = FakePort(port, baud_rate)
        device.identity = identities.get(baud_rate)
        device.status = status or ("verified" if device.identity else "connecting")
        if on_open is not None:
            on_open(baud_rate)
        return device
    return factory


@pytest.fixture(autouse=True)
def isolated_serial(monkeypatch):
    """No real port, and the per-baud budget shortened so the suite does not
    wait out 4.5 s x 2 bauds x N ports of real handshake timeout."""
    FakePort.opened = []
    monkeypatch.setattr(station_setup, "SerialPort", port_answering({}))
    monkeypatch.setattr(serial_port_module, "query",
                        lambda *a, **k: b"", raising=False)
    monkeypatch.setattr(station_setup, "PROBE_SECONDS", 0.05)
    monkeypatch.setattr(station_setup, "PROBE_SLICE", 0.01)
    yield


@pytest.fixture
def types(monkeypatch):
    types = {
        "Stepper Probe": make_model_class("Stepper Probe", "s"),
        "Temperature Controller": make_model_class("Temperature Controller", "t"),
        "SMC100 Rotator": make_model_class("SMC100 Rotator"),
    }
    monkeypatch.setattr(station_setup, "MODEL_TYPES", types)
    # `identify` asks the Rotator class for the name the SMC100 step reports.
    monkeypatch.setitem(station_setup._Stub.TABLE, station_setup.Rotator,
                        ("SMC100 Rotator", None, True, False))
    monkeypatch.setattr(station_setup, "Rotator",
                        types["SMC100 Rotator"], raising=True)
    return types


@pytest.fixture
def panel(types):
    return Setup(RecordingController())


@pytest.fixture
def warnings():
    events.clear()          # a repeat within DEDUPE_SECONDS is not re-notified
    seen = []
    events.subscribe(seen.append)
    yield seen
    events.unsubscribe(seen.append)
    events.clear()


# -- the three handshake steps --------------------------------------------

def test_the_transport_really_offers_what_identify_calls():
    """The fallbacks below are for a tree where the transport predates them;
    on a healthy one both are present, so neither fallback should ever fire."""
    assert callable(getattr(serial_port_module, "list_ports", None))
    assert callable(getattr(serial_port_module, "query", None))


def test_the_smc100_step_runs_first_and_costs_no_firmware_attempt(
        panel, monkeypatch):
    """57600 is cheap, so the rotator does not have to burn through both
    firmware handshake timeouts before reaching the check that identifies it."""
    asked = []

    def query(port, baud, payload, **kwargs):
        asked.append((port, baud, payload, kwargs))
        return b"1ID SMC100 1.0"

    monkeypatch.setattr(serial_port_module, "query", query, raising=False)
    assert panel.identify(PORT) == "SMC100 Rotator"
    assert asked == [(PORT, 57600, b"1ID?\r\n", {"xonxoff": True})]
    assert FakePort.opened == [], "a firmware baud was opened anyway"


def test_the_smc100_step_falls_back_to_the_status_query(panel, monkeypatch):
    replies = {b"1ID?\r\n": "", b"1TS?\r\n": "1TS000032"}
    monkeypatch.setattr(serial_port_module, "query",
                        lambda port, baud, payload, **kw: replies[payload],
                        raising=False)
    assert panel.identify(PORT) == "SMC100 Rotator"


def test_the_smc100_reply_is_stripped_before_it_is_matched(panel, monkeypatch):
    """`query()` hands back what arrived, unstripped; the old 57600 branch
    stripped it before matching."""
    monkeypatch.setattr(serial_port_module, "query",
                        lambda *a, **k: "\r\n1ID SMC100CC\r\n", raising=False)
    assert panel.identify(PORT) == "SMC100 Rotator"


def test_a_whitespace_only_reply_is_no_reply(panel, monkeypatch):
    replies = {b"1ID?\r\n": "\r\n", b"1TS?\r\n": "  "}
    monkeypatch.setattr(serial_port_module, "query",
                        lambda port, baud, payload, **kw: replies[payload],
                        raising=False)
    assert panel.identify(PORT) is None
    assert FakePort.opened == [500000, 115200], "the firmware bauds were skipped"


def test_the_firmware_identity_is_read_at_500000(panel, monkeypatch):
    monkeypatch.setattr(station_setup, "SerialPort",
                        port_answering({500000: "s"}))
    assert panel.identify(PORT) == "Stepper Probe"
    assert FakePort.opened == [500000]


def test_the_firmware_identity_falls_back_to_115200(panel, monkeypatch):
    monkeypatch.setattr(station_setup, "SerialPort",
                        port_answering({115200: "DEV: t"}))
    assert panel.identify(PORT) == "Temperature Controller"
    assert FakePort.opened == [500000, 115200]


def test_an_identity_no_model_claims_is_not_a_device(panel, monkeypatch):
    monkeypatch.setattr(station_setup, "SerialPort",
                        port_answering({500000: "z"}))
    assert panel.identify(PORT) is None


def test_a_silent_port_is_identified_as_nothing(panel):
    assert panel.identify(PORT) is None
    assert FakePort.opened == [500000, 115200]


def test_an_open_that_answers_but_does_not_verify_is_not_a_device(
        panel, monkeypatch):
    """Opening a port proves nothing: a cable into a powered-off board opens
    exactly like a working one (SERIAL-7), and `wait_open` is True for both."""
    monkeypatch.setattr(station_setup, "SerialPort",
                        port_answering({}, status="unverified"))
    assert panel.identify(PORT) is None


def test_a_port_that_could_not_be_opened_is_reported_not_waited_out(
        panel, monkeypatch, warnings):
    """`wait_open()` is False both for "still connecting" and for "the open
    failed"; only the state says which, and waiting out the handshake budget
    on a port that is LOST wastes 4.5 s per baud."""
    monkeypatch.setattr(station_setup, "PROBE_SECONDS", 30.0)
    monkeypatch.setattr(station_setup, "SerialPort",
                        port_answering({}, status="lost"))
    started = time.monotonic()
    assert panel.identify(PORT) is None
    assert time.monotonic() - started < 5.0
    reported = [e for e in warnings if e.title == "Probe Failed"]
    assert len(reported) == 1 and "could not open" in reported[0].message


# -- failures are reported, not swallowed (SERIAL-17) ---------------------

def test_a_probe_error_is_reported_once_per_port(panel, monkeypatch, warnings):
    """`probe_device_at` wrapped all three attempts in `except Exception:
    pass`, so a port that raised on every open looked exactly like a port
    with nothing attached."""
    def boom(port, baud_rate=115200):
        raise OSError(f"[Errno 16] Resource busy: {port}")

    monkeypatch.setattr(station_setup, "SerialPort", boom)
    assert panel.identify(PORT) is None
    panel.identify(PORT)        # a second pass over the same port: no repeat
    reported = [e for e in warnings if e.title == "Probe Failed"]
    assert len(reported) == 1
    assert PORT in reported[0].message and "Resource busy" in reported[0].message


def test_a_failing_smc100_query_is_reported_and_the_scan_continues(
        panel, monkeypatch, warnings):
    def boom(*args, **kwargs):
        raise OSError("port busy")

    monkeypatch.setattr(serial_port_module, "query", boom, raising=False)
    monkeypatch.setattr(station_setup, "SerialPort",
                        port_answering({500000: "s"}))
    assert panel.identify(PORT) == "Stepper Probe"
    assert [e.title for e in warnings] == ["Probe Failed"]


def test_without_query_the_smc100_step_is_skipped_with_one_warning(
        panel, monkeypatch, warnings):
    monkeypatch.delattr(serial_port_module, "query", raising=False)
    monkeypatch.setattr(station_setup, "SerialPort",
                        port_answering({500000: "s"}))
    assert panel.identify(PORT) == "Stepper Probe"
    panel.identify(PORT)
    assert [e.title for e in warnings] == ["Not Available"]


def test_the_probed_port_is_always_closed(panel, monkeypatch):
    devices = []

    def factory(port, baud_rate=115200):
        device = FakePort(port, baud_rate)
        devices.append(device)
        return device

    monkeypatch.setattr(station_setup, "SerialPort", factory)
    panel.identify(PORT)
    assert devices and all(d.is_closed for d in devices)


# -- giving up (MANAGER-20) ------------------------------------------------

def test_an_abort_before_the_first_step_opens_no_port_at_all(panel, monkeypatch):
    asked = []
    monkeypatch.setattr(serial_port_module, "query",
                        lambda *a, **k: asked.append(a) or b"", raising=False)
    assert panel.identify(PORT, should_abort=lambda: True) is None
    assert FakePort.opened == [] and asked == []


def test_an_abort_stops_the_scan_between_steps(panel, monkeypatch):
    calls = {"n": 0}

    def should_abort():
        calls["n"] += 1
        return calls["n"] > 1        # let the SMC100 step happen, then stop

    assert panel.identify(PORT, should_abort=should_abort) is None
    assert FakePort.opened == [], "kept scanning after the abort"


def test_an_abort_breaks_out_of_the_wait_not_only_between_ports(
        panel, monkeypatch):
    """The wait is where the scan spends its time; aborting has to be noticed
    inside it, or a cancelled scan still blocks for seconds per port."""
    aborted = {"yes": False}
    monkeypatch.setattr(station_setup, "PROBE_SECONDS", 30.0)
    monkeypatch.setattr(station_setup, "PROBE_SLICE", 0.0)
    monkeypatch.setattr(station_setup, "SerialPort", port_answering(
        {}, on_open=lambda baud: aborted.__setitem__("yes", True)))

    started = time.monotonic()
    assert panel.identify(PORT, should_abort=lambda: aborted["yes"]) is None
    assert time.monotonic() - started < 5.0
    assert FakePort.opened == [500000], "opened the next baud after the abort"


def test_an_abort_callback_that_raises_does_not_break_the_scan(panel):
    assert panel.identify(PORT, should_abort=lambda: 1 / 0) is None
    assert FakePort.opened == [500000, 115200]


# -- the scan worker -------------------------------------------------------

def test_a_scan_runs_off_the_calling_thread_with_progress_in_state(
        panel, monkeypatch):
    monkeypatch.setattr(serial_port_module, "list_ports",
                        lambda: ["/dev/ttyUSB0", "/dev/ttyUSB1"], raising=False)
    monkeypatch.setattr(station_setup, "SerialPort", port_answering({500000: "s"}))
    caller = threading.current_thread()
    seen = []
    monkeypatch.setattr(Setup, "identify",
                        lambda self, port, should_abort=None: seen.append(
                            (port, threading.current_thread())) or "Stepper Probe")

    assert panel.run("scan").is_ok
    _join(panel)

    assert [port for port, _ in seen] == ["/dev/ttyUSB0", "/dev/ttyUSB1"]
    assert all(thread is not caller for _, thread in seen)
    state = panel.state
    assert state["scan"]["progress"] == 100
    assert state["scan"]["status"] == "Scan complete."
    assert state["scan"]["found"] == {"/dev/ttyUSB0": "Stepper Probe",
                                      "/dev/ttyUSB1": "Stepper Probe"}
    assert state["is_scanning"] is False


def test_the_headless_entry_is_never_probed(panel, monkeypatch):
    probed = []
    monkeypatch.setattr(serial_port_module, "list_ports",
                        lambda: ["/dev/ttyUSB0"], raising=False)
    monkeypatch.setattr(Setup, "identify",
                        lambda self, port, should_abort=None: probed.append(port))
    panel.run("scan")
    _join(panel)
    assert probed == ["/dev/ttyUSB0"] and HEADLESS not in probed


def test_a_running_scan_can_be_cancelled(panel, monkeypatch):
    reached = threading.Event()
    monkeypatch.setattr(serial_port_module, "list_ports",
                        lambda: [f"/dev/ttyUSB{n}" for n in range(6)],
                        raising=False)

    def slow_identify(self, port, should_abort=None):
        reached.set()
        for _ in range(200):
            if should_abort and should_abort():
                return None
            time.sleep(0.005)
        return None

    monkeypatch.setattr(Setup, "identify", slow_identify)
    panel.run("scan")
    assert reached.wait(2), "the scan never started"
    assert panel.run("cancel_scan").is_ok
    _join(panel)
    assert panel.state["scan"]["status"] == "Scan cancelled."
    assert len(panel.state["scan"]["found"]) < 6


def test_a_scan_drops_a_selection_whose_port_is_gone(panel, monkeypatch):
    panel._ports = [HEADLESS, "/dev/ttyUSB9"]
    panel.stepper_probe_port = "/dev/ttyUSB9"
    monkeypatch.setattr(serial_port_module, "list_ports",
                        lambda: ["/dev/ttyUSB0"], raising=False)
    monkeypatch.setattr(Setup, "identify",
                        lambda self, port, should_abort=None: None)
    panel.run("scan")
    _join(panel)
    assert panel.stepper_probe_port == HEADLESS


def _join(panel, timeout=5.0):
    thread = panel._scan_thread
    if thread is not None:
        thread.join(timeout)
    assert not panel.is_scanning, "the scan thread did not finish"
