"""MOD-4 (= CON-7): Setup's registry is open.

A device joins the station by `register(cls)`: no edit to `setup.py`, no
mutation of a module global. The class declares what Setup must fill in
(`RESOURCES`), may identify its own board (`identify_port`), and reopens
from its remembered config whether Setup built it or `controller.add` did.

No test here opens a real port: `SerialPort` and `query` are replaced by the
autouse fixture imported from `test_setup_identify`.
"""
import pytest

from controller import setup as station_setup

pytestmark = pytest.mark.usefixtures("profiles_on")   # the held feature, on
from controller.controller import Controller
from controller.setup import SIM, Setup
from devices import serial_port as serial_port_module
from events import events
from model.base import Model
from model.heater import Heater
from model.probe import ChuckPositioner, DCProbe, StepperProbe
from model.rgb_analysis import RgbAnalysis
from model.rotator import Rotator
from model.sample_map import SampleMap
from model.transfer_map import TransferMap
from model.xyz_stage import XyzStage
from result import Refused

from tests.test_setup import RecordingController
from tests.test_setup_identify import (  # noqa: F401  (autouse fixture)
    FakePort, isolated_serial, port_answering)

PORT = "/dev/ttyFAKE0"


# -- a device the station has never heard of -------------------------------

class PiezoStage(Model):
    """The model-contract audit's acceptance device: one port, no gamepad,
    a `DEV: p` board. A real `Model`, so the latch and close order are the
    base class's."""

    NAME = "Piezo Stage"
    IDENTITY = "p"
    NEEDS_PORT = True

    def __init__(self, port=None, sim=False):
        # No `gamepad=` parameter on purpose: Setup must pass only what the
        # class declares.
        super().__init__()
        self.port, self.sim = port, sim
        self.is_open = False

    def open(self):
        super().open()
        self.is_open = True

    def close(self):
        super().close()
        self.is_open = False


class TwoPortStage(Model):
    """A device with a second serial port: a resource the old
    `cls(port=, gamepad=, sim=)` call could not express."""

    NAME = "Two Port Stage"
    RESOURCES = ("port", "port_aux", "gamepad")

    def __init__(self, port=None, port_aux=None, gamepad=None, sim=False):
        super().__init__()
        self.port, self.port_aux, self.gamepad, self.sim = (
            port, port_aux, gamepad, sim)


@pytest.fixture
def registry(monkeypatch):
    """A private copy of the registry, so a test's registration never leaks
    into the next test or into `test_model_contract`'s class list."""
    fresh = dict(station_setup.MODEL_TYPES)
    monkeypatch.setattr(station_setup, "MODEL_TYPES", fresh)
    return fresh


@pytest.fixture
def warnings():
    events.clear()
    seen = []
    events.subscribe(seen.append)
    yield seen
    events.unsubscribe(seen.append)
    events.clear()


# -- 1. register ------------------------------------------------------------

BUILT_INS = ["Stepper Probe", "DC Probe", "Chuck Positioner",
             "Temperature Controller", "Rotator",
             "XYZ Stage",      # 2026-10-09: three axis boards, one row
             "RGB Analysis",
             "Transfer Map",   # Tier S (2026-09-27)
             *(["Sample DB"] if station_setup.SAMPLE_MAP_ENABLED else [])]   # the flag: setup.py
#: The built-in Setup rows: RGB Analysis is registered but has no row of its
#: own, being drawn on the Transfer Map's page and launched by its row
#: (Model.HOST, owner ruling 2026-09-28).
BUILT_IN_ROWS = [name for name in BUILT_INS if name != "RGB Analysis"]


def test_the_six_built_ins_are_registered_in_todays_display_order():
    assert list(station_setup.MODEL_TYPES) == BUILT_INS
    assert list(station_setup.MODEL_TYPES.values()) == [
        StepperProbe, DCProbe, ChuckPositioner, Heater, Rotator, XyzStage,
        RgbAnalysis, TransferMap, *([SampleMap] if station_setup.SAMPLE_MAP_ENABLED else [])]


def test_register_is_reachable_as_setup_register():
    assert Setup.register is station_setup.register


def test_the_stub_table_is_gone():
    """`_Stub.TABLE` filled gaps for classes that no longer have any."""
    assert not hasattr(station_setup, "_Stub")


def test_register_appends_in_order_and_returns_the_class(registry):
    assert Setup.register(PiezoStage) is PiezoStage     # usable as a decorator
    assert list(station_setup.MODEL_TYPES) == [*BUILT_INS, "Piezo Stage"]
    assert station_setup.MODEL_TYPES["Piezo Stage"] is PiezoStage


def test_registering_the_same_class_twice_is_a_no_op(registry):
    Setup.register(PiezoStage)
    Setup.register(PiezoStage)
    assert list(station_setup.MODEL_TYPES).count("Piezo Stage") == 1


def test_register_refuses_a_second_class_under_a_taken_name(registry):
    impostor = type("Impostor", (PiezoStage,), {"NAME": "Rotator"})
    with pytest.raises(ValueError, match="Rotator"):
        Setup.register(impostor)
    assert station_setup.MODEL_TYPES["Rotator"] is Rotator


def test_register_refuses_a_name_whose_row_key_is_taken(registry):
    """Rows are keyed by `_key_for(name)`; "DC-Probe" would silently share
    the DC Probe's row attributes."""
    clash = type("Clash", (PiezoStage,), {"NAME": "DC-Probe", "IDENTITY": None})
    with pytest.raises(ValueError, match="dc_probe"):
        Setup.register(clash)


def test_register_refuses_an_identity_byte_already_claimed(registry):
    twin = type("Twin", (PiezoStage,), {"NAME": "Twin Stage", "IDENTITY": "s"})
    with pytest.raises(ValueError, match="Stepper Probe"):
        Setup.register(twin)


def test_register_refuses_a_class_that_declares_no_name(registry):
    """Inheriting `Panel.NAME` is not declaring one."""
    nameless = type("Nameless", (Model,), {})
    with pytest.raises(ValueError, match="NAME"):
        Setup.register(nameless)


def test_register_refuses_a_resource_setup_cannot_fill(registry):
    odd = type("Odd", (PiezoStage,), {"NAME": "Odd Stage", "IDENTITY": None,
                                     "RESOURCES": ("port", "ip_address")})
    with pytest.raises(ValueError, match="ip_address"):
        Setup.register(odd)


def test_a_registered_class_gets_a_row_identifies_and_builds(
        registry, monkeypatch):
    Setup.register(PiezoStage)
    panel = Setup(RecordingController())
    titles = [s["title"] for s in panel.schema["sections"]]
    assert titles == ["Devices", *BUILT_IN_ROWS, "Piezo Stage", "Launch",
                      "Update", "Firmware", "Station defaults"]
    monkeypatch.setattr(station_setup, "SerialPort",
                        port_answering({500000: "DEV: p"}))
    assert panel.identify(PORT) == "Piezo Stage"
    built = panel.build([{"model": "Piezo Stage", "port": SIM,
                          "gamepad": None, "sim": True}])
    assert built == ["Piezo Stage"]
    model = panel.controller._model("Piezo Stage")
    assert isinstance(model, PiezoStage) and model.is_open and model.sim


# -- 2. resources -----------------------------------------------------------

def test_resources_derive_from_the_needs_flags_so_the_six_need_no_edit():
    assert station_setup.resources_of(StepperProbe) == ("port", "gamepad")
    assert station_setup.resources_of(DCProbe) == ("port", "gamepad")
    assert station_setup.resources_of(ChuckPositioner) == ("port", "gamepad")
    assert station_setup.resources_of(Heater) == ("port",)
    assert station_setup.resources_of(Rotator) == ("port",)
    assert station_setup.resources_of(RgbAnalysis) == ()


def test_declared_resources_win_over_the_needs_flags():
    assert station_setup.resources_of(TwoPortStage) == (
        "port", "port_aux", "gamepad")


def test_setup_passes_only_the_resources_the_class_declares(registry):
    """The old call was `cls(port=, gamepad=, sim=)` for everyone, so a class
    without a `gamepad` parameter raised TypeError on construction."""
    Setup.register(PiezoStage)
    panel = Setup(RecordingController())
    model = panel.model_from_config({"model": "Piezo Stage", "port": SIM,
                                     "gamepad": None, "sim": True})
    assert isinstance(model, PiezoStage) and model.port == SIM


def test_a_class_is_constructed_with_its_own_resources_and_sim_only(registry, monkeypatch):
    """A class with no port resource gets no `port=`; one with no gamepad
    resource gets no `gamepad=`. Each default is the value Setup used to
    pass, so nothing changes for the six."""
    seen = []

    class Spy(Model):
        NAME = "Spy"
        NEEDS_PORT = True

        def __init__(self, **kwargs):
            super().__init__()
            seen.append(kwargs)

    Setup.register(Spy)
    panel = Setup(RecordingController())
    panel.model_from_config({"model": "Spy", "port": "/dev/ttyUSB0",
                             "gamepad": "Pad", "sim": False})
    assert seen == [{"port": "/dev/ttyUSB0", "sim": False}]


def test_a_second_port_resource_gets_its_own_port_dropdown(registry):
    Setup.register(TwoPortStage)
    panel = Setup(RecordingController())
    row = next(s for s in panel.schema["sections"]
               if s["title"] == "Two Port Stage")
    dropdowns = [(e["text"], e["options_command"]) for e in row["elements"]
                 if e["type"] == "dropdown"]
    assert dropdowns == [("Port", "port_options"),
                         ("Port Aux", "port_options"),
                         ("Gamepad", "gamepad_options")]
    assert callable(getattr(panel, "set_two_port_stage_port_aux"))


def test_a_second_port_is_configured_validated_and_passed_in(registry):
    Setup.register(TwoPortStage)
    panel = Setup(RecordingController())
    panel._ports = ["/dev/ttyUSB0", "/dev/ttyUSB1"]
    # The row launches because its board answered on its port.
    panel._found["/dev/ttyUSB0"] = "Two Port Stage"
    for command, value in (("set_two_port_stage_port", "/dev/ttyUSB0"),
                           ("set_two_port_stage_port_aux", "/dev/ttyUSB0")):
        assert panel.run(command, args=(value,)).is_ok
    config = next(c for c in panel.configs if c["model"] == "Two Port Stage")
    assert config == {"model": "Two Port Stage", "port": "/dev/ttyUSB0",
                      "gamepad": None, "sim": False,
                      "port_aux": "/dev/ttyUSB0"}
    with pytest.raises(Refused, match="already assigned"):
        panel.validate([config])
    assert panel.run("set_two_port_stage_port_aux",
                     args=("/dev/ttyUSB1",)).is_ok
    config = next(c for c in panel.configs if c["model"] == "Two Port Stage")
    model = panel.model_from_config(config)
    assert (model.port, model.port_aux, model.sim) == (
        "/dev/ttyUSB0", "/dev/ttyUSB1", False)


def test_a_simulated_two_port_row_simulates_both_ports(registry):
    Setup.register(TwoPortStage)
    panel = Setup(RecordingController())
    model = panel.model_from_config({"model": "Two Port Stage", "port": SIM,
                                     "port_aux": "/dev/ttyUSB1", "sim": True})
    assert (model.port, model.port_aux, model.sim) == (SIM, SIM, True)


# -- 3. the identity hook ----------------------------------------------------

def test_a_class_hook_identifies_its_board_before_the_firmware_handshake(
        registry, monkeypatch):
    asked = []

    class Network(PiezoStage):
        NAME = "Hooked Stage"
        IDENTITY = None

        @classmethod
        def identify_port(cls, port, should_abort):
            asked.append((port, should_abort()))
            return True

    Setup.register(Network)
    panel = Setup(RecordingController())
    monkeypatch.setattr(serial_port_module, "query",
                        lambda *a, **k: "", raising=False)
    assert panel.identify(PORT) == "Hooked Stage"
    assert asked == [(PORT, False)]
    assert FakePort.opened == [], "a firmware baud was opened anyway"


def test_a_hook_that_answers_no_lets_the_firmware_handshake_run(
        registry, monkeypatch):
    class Shy(PiezoStage):
        NAME = "Shy Stage"
        IDENTITY = None

        @classmethod
        def identify_port(cls, port, should_abort):
            return False

    Setup.register(Shy)
    panel = Setup(RecordingController())
    monkeypatch.setattr(station_setup, "SerialPort",
                        port_answering({500000: "s"}))
    assert panel.identify(PORT) == "Stepper Probe"


def test_a_hook_that_raises_is_reported_and_the_scan_goes_on(
        registry, monkeypatch, warnings):
    class Broken(PiezoStage):
        NAME = "Broken Stage"
        IDENTITY = None

        @classmethod
        def identify_port(cls, port, should_abort):
            raise OSError("port busy")

    Setup.register(Broken)
    panel = Setup(RecordingController())
    monkeypatch.setattr(station_setup, "SerialPort",
                        port_answering({500000: "s"}))
    assert panel.identify(PORT) == "Stepper Probe"
    assert [e.title for e in warnings] == ["Probe Failed"]


def test_the_smc100_fallback_yields_once_the_rotator_has_its_hook(
        monkeypatch):
    """The hard-coded SMC100 branch runs only for a registered Rotator that
    lacks `identify_port`. When the lead moves the query onto the class, the
    branch goes quiet by itself and the hook is what runs."""
    hooked = []
    queried = []

    class HookedRotator(Rotator):
        @classmethod
        def identify_port(cls, port, should_abort):
            hooked.append(port)
            return True

    types = dict(station_setup.MODEL_TYPES)
    types["Rotator"] = HookedRotator
    monkeypatch.setattr(station_setup, "MODEL_TYPES", types)
    monkeypatch.setattr(station_setup, "Rotator", HookedRotator)
    monkeypatch.setattr(serial_port_module, "query",
                        lambda *a, **k: queried.append(a) or "",
                        raising=False)
    panel = Setup(RecordingController())
    assert panel.identify(PORT) == "Rotator"
    assert hooked == [PORT] and queried == []


def test_the_smc100_fallback_still_identifies_todays_rotator(monkeypatch):
    """Until the hook lands on `Rotator`, the SMC100 query is the same bytes
    in the same place: first, 57600, XON/XOFF."""
    asked = []
    monkeypatch.setattr(serial_port_module, "query",
                        lambda port, baud, payload, **kw:
                        asked.append((baud, payload, kw)) or "1ID SMC100",
                        raising=False)
    panel = Setup(RecordingController())
    assert panel.identify(PORT) == "Rotator"
    assert asked == [(57600, b"1ID?\r\n", {"xonxoff": True})]
    assert FakePort.opened == []


# -- 4. reopen from the registry --------------------------------------------

def test_a_model_added_at_runtime_reopens_through_the_registry(registry):
    """`Controller.factory` used to be set only by `build()`, so a device
    added with `controller.add` could not come back once its tab closed:
    `ValueError: Piezo Stage was never configured`."""
    Setup.register(PiezoStage)
    controller = Controller()
    Setup(controller)
    first = controller.add("Piezo Stage", PiezoStage(port=SIM, sim=True),
                           {"model": "Piezo Stage", "port": SIM, "sim": True})
    controller.remove("Piezo Stage")
    assert not first.is_open and controller.model_names == []
    again = controller.reopen("Piezo Stage")
    assert isinstance(again, PiezoStage) and again is not first
    assert again.is_open and again.sim and again.port == SIM
    assert controller.model_names == ["Piezo Stage"]
    controller.close()


def test_a_runtime_config_that_names_only_its_resources_reopens(registry):
    """`{"model": NAME, ...resources, "sim": bool}`: no `gamepad` key, and a
    second port under its own resource name."""
    Setup.register(TwoPortStage)
    controller = Controller()
    Setup(controller)
    config = {"model": "Two Port Stage", "port": "/dev/ttyUSB0",
              "port_aux": "/dev/ttyUSB1", "sim": False}
    controller.add("Two Port Stage",
                   TwoPortStage(port="/dev/ttyUSB0", port_aux="/dev/ttyUSB1"),
                   config)
    controller.remove("Two Port Stage")
    again = controller.reopen("Two Port Stage")
    assert (again.port, again.port_aux, again.gamepad, again.sim) == (
        "/dev/ttyUSB0", "/dev/ttyUSB1", None, False)
    controller.close()


def test_reopening_an_unregistered_name_is_refused_by_name(registry):
    controller = Controller()
    Setup(controller)
    controller.add("Ghost", PiezoStage(sim=True), {"model": "Ghost"})
    controller.remove("Ghost")
    with pytest.raises(Refused, match="Ghost"):
        controller.reopen("Ghost")
