"""Setup: the one wizard.

Ported from `tests/core/test_app_bootstrap.py` (discover_ports,
validate_assignment, the build_models rollback tests) and the setup half of
`tests/core/test_composition_root.py`, adapted to the Panel contract: a
refusal is a `Refused`, not an error string in a list.
"""
import threading

import pytest

from station import setup as station_setup
from station.controller import Controller
from station.devices import gamepad as gamepad_module
from station.devices import serial_port as serial_port_module
from station.events import events
from station.result import Refused
from station.setup import HEADLESS, MODEL_TYPES, SIM, Setup


# -- fakes -----------------------------------------------------------------

class FakeModel:
    """A stand-in model. `Controller.add` opens it, `remove` stops and closes
    it, so the real ownership contract is exercised."""

    NAME = "Fake"
    IDENTITY = None
    NEEDS_PORT = True
    NEEDS_GAMEPAD = False
    FAIL_ON_OPEN = False

    def __init__(self, port=None, gamepad=None, sim=False):
        self.port, self.gamepad, self.sim = port, gamepad, sim
        self.is_open = False
        self.closed = 0
        if self.FAIL_ON_OPEN:
            raise RuntimeError("port busy")

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False
        self.closed += 1

    def estop(self):
        return True

    def on_model_added(self, name, model):
        pass

    def on_model_removed(self, name, model):
        pass


def make_model_class(name, identity=None, needs_port=True,
                     needs_gamepad=False, fail=False):
    return type(name.replace(" ", ""), (FakeModel,), {
        "NAME": name, "IDENTITY": identity, "NEEDS_PORT": needs_port,
        "NEEDS_GAMEPAD": needs_gamepad, "FAIL_ON_OPEN": fail,
    })


class RecordingController(Controller):
    """A real Controller that remembers the order of the calls Setup makes."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def reset(self):
        self.calls.append("reset")
        return super().reset()

    def add(self, name, model, config=None):
        self.calls.append(f"add:{name}")
        return super().add(name, model, config)

    def remove(self, name):
        self.calls.append(f"remove:{name}")
        return super().remove(name)


@pytest.fixture
def warnings():
    """Every warning/error the code under test published.

    Cleared on the way in as well as out: the event log collapses a repeat of
    the same event within `DEDUPE_SECONDS`, so one left behind by an earlier
    test would silently swallow this one.
    """
    events.clear()
    seen = []
    events.subscribe(seen.append)
    yield seen
    events.unsubscribe(seen.append)
    events.clear()


@pytest.fixture
def fake_types(monkeypatch):
    types = {}
    for model_class in (make_model_class("Alpha", "a", needs_gamepad=True),
                        make_model_class("Beta", "b", needs_gamepad=True),
                        make_model_class("Screen", needs_port=False)):
        types[model_class.NAME] = model_class
    monkeypatch.setattr(station_setup, "MODEL_TYPES", types)
    return types


@pytest.fixture
def panel(fake_types):
    return Setup(RecordingController())


def select(panel, key, field, choice):
    """What a view does: send the dropdown's command with the choice."""
    result = panel.run(f"set_{key}_{field}", args=(choice,))
    assert result.is_ok, result.reason
    return result


# -- the model registry ----------------------------------------------------

def test_the_per_baud_probe_budget_is_the_one_probe_device_at_spent():
    """1.5 s for the board to leave its bootloader, then up to 3.0 s of
    polling - per baud, per port. Shortening it silently would make the
    handshake miss slow boards on the bench."""
    assert station_setup.PROBE_SECONDS == 4.5


def test_model_types_is_every_model_class_keyed_by_its_name():
    assert list(MODEL_TYPES) == ["Stepper Probe", "DC Probe",
                                 "Chuck Positioner", "Temperature Controller",
                                 "Rotator", "Red Percent"]
    assert len(set(MODEL_TYPES.values())) == 6


def test_model_types_is_the_only_list_of_models(panel, fake_types):
    """One registry: the rows a view renders come from the same dict that
    builds. Four copies of this list disagreed before RC-7."""
    assert panel.model_types == list(fake_types)
    titles = [section["title"] for section in panel.schema["sections"]]
    assert titles == ["Hardware Scan", *fake_types, "Launch"]


# -- scan_ports (was discover_ports) --------------------------------------

def test_scan_ports_falls_back_when_the_serial_module_offers_no_listing(
        panel, monkeypatch, warnings):
    monkeypatch.delattr(serial_port_module, "list_ports", raising=False)
    assert panel.scan_ports() == [HEADLESS, "COM1", "COM2", "COM3", "COM4"]
    panel.scan_ports()      # a second call must not warn again
    assert [e.title for e in warnings] == ["Not Available"]


def test_scan_ports_filters_bluetooth_and_puts_usb_first(panel, monkeypatch):
    """The sort is today's, unchanged: the `/dev/cu.usb*` family and anything
    with a literal "USB" in its name come first, then the rest by name."""
    monkeypatch.setattr(serial_port_module, "list_ports", lambda: [
        ("/dev/cu.Bluetooth-Incoming-Port", "n/a"),
        ("/dev/cu.usbmodem1101", "USB VID:PID=2341"),
        ("/dev/cu.debug-console", "n/a"),
    ], raising=False)
    ports = panel.scan_ports()
    assert ports[0] == HEADLESS
    assert "/dev/cu.Bluetooth-Incoming-Port" not in ports
    assert ports[1] == "/dev/cu.usbmodem1101"
    assert ports[2] == "/dev/cu.debug-console"


def test_scan_ports_drops_linux_ttys_without_a_hwid(panel, monkeypatch):
    monkeypatch.setattr(serial_port_module, "list_ports", lambda: [
        ("/dev/ttyS0", "n/a"), ("/dev/ttyS1", "some_hwid"),
    ], raising=False)
    ports = panel.scan_ports()
    assert "/dev/ttyS0" not in ports
    assert "/dev/ttyS1" in ports


def test_scan_ports_offers_everything_when_filtering_left_nothing(
        panel, monkeypatch):
    monkeypatch.setattr(serial_port_module, "list_ports",
                        lambda: [("/dev/ttyS0", "n/a")], raising=False)
    assert panel.scan_ports() == [HEADLESS, "/dev/ttyS0"]


def test_a_listing_that_raises_is_reported_not_swallowed(
        panel, monkeypatch, warnings):
    def boom():
        raise OSError("enumeration failed")

    monkeypatch.setattr(serial_port_module, "list_ports", boom, raising=False)
    assert panel.scan_ports() == [HEADLESS, "COM1", "COM2", "COM3", "COM4"]
    assert [e.title for e in warnings] == ["Port Listing Failed"]


# -- scan_gamepads (was discover_controllers) ------------------------------

def test_scan_gamepads_is_none_plus_the_hub_names(panel, monkeypatch):
    class Hub:
        names = ["ID 0: Xbox", "ID 1: T16000M"]

    monkeypatch.setattr(gamepad_module, "hub", Hub(), raising=False)
    assert panel.scan_gamepads() == ["None", "ID 0: Xbox", "ID 1: T16000M"]


def test_gamepad_enumeration_never_shells_out(panel, monkeypatch):
    """WEB-16: the web wizard ran `subprocess.run(["python3", ...])` - not
    even `sys.executable` - to enumerate gamepads."""
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("gamepad enumeration spawned a subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    panel.scan_gamepads()


def test_no_hub_means_no_gamepad_and_never_an_invented_one(
        panel, monkeypatch, warnings):
    """WEB-15/MANAGER-18/GAMEPAD-18: the web wizard invented two placeholder
    names when its enumeration found nothing, and a device assigned one got
    no input at all."""
    monkeypatch.delattr(gamepad_module, "hub", raising=False)
    assert panel.scan_gamepads() == ["None"]
    panel.scan_gamepads()
    assert [e.title for e in warnings] == ["Not Available"]


# -- validate (was validate_assignment) ------------------------------------

def test_validate_refuses_two_rows_on_one_port_and_names_the_field(panel):
    configs = [{"model": "Alpha", "port": "/dev/ttyUSB0", "gamepad": None, "sim": False},
               {"model": "Beta", "port": "/dev/ttyUSB0", "gamepad": None, "sim": False}]
    with pytest.raises(Refused) as refusal:
        panel.validate(configs)
    assert "Beta port" in refusal.value.reason
    assert "/dev/ttyUSB0" in refusal.value.reason


def test_validate_refuses_two_rows_claiming_one_gamepad(panel):
    configs = [{"model": "Alpha", "port": SIM, "gamepad": "ID 0: Pad", "sim": True},
               {"model": "Beta", "port": SIM, "gamepad": "ID 0: Pad", "sim": True}]
    with pytest.raises(Refused) as refusal:
        panel.validate(configs)
    assert "Beta gamepad" in refusal.value.reason


def test_validate_allows_shared_sim_ports_and_any_number_of_no_gamepads(panel):
    assert panel.validate([
        {"model": "Alpha", "port": SIM, "gamepad": None, "sim": True},
        {"model": "Beta", "port": SIM, "gamepad": None, "sim": True},
        {"model": "Screen", "port": None, "gamepad": None, "sim": False},
    ]) is True


def test_validate_refuses_a_hardware_row_with_no_port(panel):
    with pytest.raises(Refused) as refusal:
        panel.validate([{"model": "Alpha", "port": HEADLESS, "gamepad": None,
                         "sim": False}])
    assert "Alpha port" in refusal.value.reason


# -- build (was build_models / _build_each) --------------------------------

def test_build_resets_the_controller_before_constructing_anything(panel):
    """One port never has two handles: the outgoing models come down first
    (MANAGER-4, SERIAL-5, invariant I-1.4)."""
    panel.build([{"model": "Alpha", "port": SIM, "gamepad": None, "sim": True}])
    assert panel.controller.calls == ["reset", "add:Alpha"]


def test_build_rolls_back_everything_when_a_later_model_fails(
        panel, fake_types, monkeypatch):
    """MANAGER-5: a failed launch used to strand every already-built model
    holding its port open, with no reference to it anywhere."""
    fake_types["Beta"] = make_model_class("Beta", "b", fail=True)
    with pytest.raises(Refused) as refusal:
        panel.build([
            {"model": "Alpha", "port": SIM, "gamepad": None, "sim": True},
            {"model": "Beta", "port": SIM, "gamepad": None, "sim": True},
        ])
    assert "Beta" in refusal.value.reason
    assert panel.controller.model_names == []
    assert "remove:Alpha" in panel.controller.calls


def test_build_refuses_a_port_that_answered_as_another_model(panel):
    """The identity byte is checked against the model class."""
    panel._found["/dev/ttyUSB0"] = "Beta"
    with pytest.raises(Refused) as refusal:
        panel.build([{"model": "Alpha", "port": "/dev/ttyUSB0",
                      "gamepad": None, "sim": False}])
    assert "answered as Beta" in refusal.value.reason
    assert panel.controller.model_names == []


def test_a_port_that_answered_nothing_is_still_allowed(panel):
    """A handshake can miss a live board; only a positive mismatch refuses."""
    panel._found["/dev/ttyUSB0"] = None
    panel.build([{"model": "Alpha", "port": "/dev/ttyUSB0", "gamepad": None,
                  "sim": False}])
    assert panel.controller.model_names == ["Alpha"]


def test_a_disabled_row_builds_nothing(panel, fake_types):
    """WEB-4/MANAGER-12: Web dropped the enabled flag and built every row."""
    select(panel, "alpha", "mode", "Simulated")
    assert [c["model"] for c in panel.configs] == ["Alpha"]
    panel.run("launch")
    assert panel.controller.model_names == ["Alpha"]


def test_sim_works_for_every_model(panel, fake_types):
    for key in ("alpha", "beta", "screen"):
        select(panel, key, "mode", "Simulated")
    built = panel.build()
    assert built == list(fake_types)
    for name, model_class in fake_types.items():
        model = panel.controller._model(name)
        assert isinstance(model, model_class) and model.is_open
        assert model.sim is True
        # A model that needs no port is built without one rather than with
        # the string "SIM".
        assert model.port == (SIM if model_class.NEEDS_PORT else None)


def test_build_sets_the_factory_so_reopen_works(panel):
    panel.build([{"model": "Alpha", "port": SIM, "gamepad": None, "sim": True}])
    panel.controller.remove("Alpha")
    assert panel.controller.model_names == []
    reopened = panel.controller.reopen("Alpha")
    assert reopened.is_open and panel.controller.model_names == ["Alpha"]


def test_launch_refuses_when_nothing_is_selected(panel):
    result = panel.run("launch")
    assert result.is_refused and "at least one" in result.reason


def test_launch_refuses_a_collision_before_building_anything(panel):
    for key in ("alpha", "beta"):
        select(panel, key, "mode", "Hardware")
        panel._ports.append("/dev/ttyUSB0")
        select(panel, key, "port", "/dev/ttyUSB0")
    result = panel.run("launch")
    assert result.is_refused and "already assigned" in result.reason
    assert panel.controller.calls == []      # not even a reset


# -- selection and auto-assign --------------------------------------------

def test_a_dropdown_choice_travels_as_the_command_argument(panel):
    panel._ports.append("/dev/ttyUSB0")
    select(panel, "alpha", "port", "/dev/ttyUSB0")
    assert panel.alpha_port == "/dev/ttyUSB0"
    assert panel.state["values"]["alpha_port"] == "/dev/ttyUSB0"


def test_a_choice_that_is_not_on_offer_is_refused(panel):
    result = panel.run("set_alpha_port", args=("/dev/nope",))
    assert result.is_refused and "options" in result.reason


def test_a_model_without_a_gamepad_has_no_gamepad_dropdown(panel):
    commands = {e.get("command") for section in panel.schema["sections"]
                for e in section["elements"]}
    assert "set_alpha_gamepad" in commands
    assert "set_screen_gamepad" not in commands
    assert "set_screen_port" not in commands


def test_auto_assign_points_each_row_at_the_port_that_answered(panel):
    panel._found = {"/dev/ttyUSB0": "Beta", "/dev/ttyUSB1": None}
    panel.run("auto_assign")
    assert panel.beta_port == "/dev/ttyUSB0"
    assert panel.beta_mode == "Hardware"
    assert panel.alpha_mode == "Off"
    assert panel.beta_found == "/dev/ttyUSB0"
    assert panel.alpha_found == "not found"


def test_auto_assign_refuses_when_nothing_was_identified(panel):
    result = panel.run("auto_assign")
    assert result.is_refused and "scan first" in result.reason


# -- the schema every view renders ----------------------------------------

def test_every_element_is_a_type_a_renderer_must_implement(panel):
    from station import schema as sch
    for element in sch.elements(panel.schema):
        assert element["type"] in sch.ELEMENT_TYPES


def test_every_declared_command_and_options_source_exists(panel):
    from station import schema as sch
    for element in sch.elements(panel.schema):
        for key in ("command", "options_command"):
            name = element.get(key)
            if name:
                assert callable(getattr(panel, name)), name


def test_options_come_from_the_scan(panel, monkeypatch):
    panel._ports = [HEADLESS, "/dev/ttyUSB0"]
    panel._gamepads = ["None", "ID 0: Pad"]
    assert panel.options("port_options") == [HEADLESS, "/dev/ttyUSB0"]
    assert panel.options("gamepad_options") == ["None", "ID 0: Pad"]
    assert panel.options("mode_options") == ["Off", "Hardware", "Simulated"]


def test_a_command_the_schema_does_not_declare_is_refused(panel):
    assert panel.run("build").is_refused


# -- gating while a scan runs ---------------------------------------------

def test_launching_and_selecting_are_gated_while_a_scan_runs(panel):
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    panel._scan_thread = thread
    try:
        assert panel.mode_name == "scanning"
        assert panel.run("launch").is_refused
        assert panel.run("set_alpha_mode", args=("Simulated",)).is_refused
        assert panel.run("scan").is_refused           # single-flight
        assert panel.run("cancel_scan").is_ok
    finally:
        release.set()
        thread.join(timeout=1)
    assert panel.mode_name == "ready"
