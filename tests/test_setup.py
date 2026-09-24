"""Setup: the one wizard.

Ported from `tests/core/test_app_bootstrap.py` (discover_ports,
validate_assignment, the build_models rollback tests) and the setup half of
`tests/core/test_composition_root.py`, adapted to the Panel contract: a
refusal is a `Refused`, not an error string in a list.

Addendum 2 reshaped the panel: one table row per model type, ONE Port
dropdown per row carrying Off / SIM / a real port, no Mode dropdown, and a
Refresh button instead of a Scan button.
"""
import threading
import time

import pytest

from controller import setup as station_setup
from controller.controller import Controller
from devices import gamepad as gamepad_module
from devices import serial_port as serial_port_module
from events import events
from result import Refused
from controller.setup import MODEL_TYPES, OFF, ON, SIM, Setup


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


def offer(panel, port):
    """Pretend a scan found `port`, so the dropdown offers it."""
    panel._ports.append(port)


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
    assert titles == ["Devices", *fake_types, "Launch"]


# -- the table (Addendum 2) ------------------------------------------------

def test_every_section_is_a_row_so_setup_is_a_table_not_a_column(panel):
    """The owner's ruling: one compact table, one row per model type. Every
    renderer lays a `layout="row"` section out horizontally."""
    assert [s["layout"] for s in panel.schema["sections"]] == \
        ["row"] * len(panel.schema["sections"])


def test_a_row_is_name_then_one_port_dropdown_then_status(panel):
    row = next(s for s in panel.schema["sections"] if s["title"] == "Alpha")
    assert [(e["type"], e.get("model_attr")) for e in row["elements"]] == [
        ("dropdown", "alpha_port"),
        ("dropdown", "alpha_gamepad"),
        ("readonly", "alpha_status"),
    ]
    assert panel.alpha_name == "Alpha"


def test_there_is_no_mode_dropdown_and_no_set_mode_command(panel):
    """Addendum 2: the Port dropdown carries it all; "Off" is the disabled
    state. A second control that could contradict the first is gone."""
    commands = {e.get("command") for e in _elements(panel)}
    assert not any(str(c).endswith("_mode") for c in commands if c)
    assert not hasattr(panel, "set_alpha_mode")
    assert not hasattr(panel, "alpha_mode")
    assert "mode_options" not in {e.get("options_command") for e in _elements(panel)}


def test_the_port_dropdown_offers_off_sim_and_every_scanned_port(panel):
    panel._ports = ["/dev/ttyUSB0", "/dev/ttyUSB1"]
    assert panel.port_options() == [OFF, SIM, "/dev/ttyUSB0", "/dev/ttyUSB1"]
    assert panel.options("port_options") == panel.port_options()


def test_a_model_that_needs_no_port_still_has_one_dropdown(panel):
    """The screen monitor has nothing to plug in, so its dropdown is the same
    control with the port names left out - not a different kind of widget."""
    row = next(s for s in panel.schema["sections"] if s["title"] == "Screen")
    dropdowns = [e for e in row["elements"] if e["type"] == "dropdown"]
    assert len(dropdowns) == 1
    assert dropdowns[0]["options_command"] == "device_options"
    assert panel.options("device_options") == [OFF, ON, SIM]


def test_the_header_row_offers_refresh_the_scan_status_and_cancel(panel):
    # F18 added "Cancel scan" (enabled only while scanning): a hung scan
    # could not be given up before.
    header = panel.schema["sections"][0]
    assert header["title"] == "Devices"
    assert [(e["type"], e.get("command") or e.get("model_attr"))
            for e in header["elements"]] == [("button", "refresh"),
                                             ("readonly", "scan_status"),
                                             ("button", "cancel_scan")]
    cancel = header["elements"][2]
    assert cancel["enabled_when"] == ["scanning"]
    assert "scan" not in {e.get("command") for e in _elements(panel)}


def test_the_launch_row_is_launch_relaunch_and_stop(panel):
    row = panel.schema["sections"][-1]
    assert row["title"] == "Launch"
    assert [e.get("text") for e in row["elements"]] == [
        "Selected:", "Launch", "Relaunch", "Stop system"]


def _elements(panel):
    return [e for s in panel.schema["sections"] for e in s["elements"]]


# -- scan_ports (was discover_ports) --------------------------------------

def test_scan_ports_falls_back_when_the_serial_module_offers_no_listing(
        panel, monkeypatch, warnings):
    monkeypatch.delattr(serial_port_module, "list_ports", raising=False)
    assert panel.scan_ports() == ["COM1", "COM2", "COM3", "COM4"]
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
    assert "/dev/cu.Bluetooth-Incoming-Port" not in ports
    assert ports == ["/dev/cu.usbmodem1101", "/dev/cu.debug-console"]


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
    assert panel.scan_ports() == ["/dev/ttyS0"]


def test_a_listing_that_found_nothing_invents_no_placeholder_port(
        panel, monkeypatch):
    """A working listing with nothing attached means no port is attached.
    "Off" and "SIM" are always on offer, so there is nothing left for a
    placeholder like COM1 to stand in for - and COM1..COM4 on a lab Mac read
    as four ports that are not there."""
    monkeypatch.setattr(serial_port_module, "list_ports", lambda: [],
                        raising=False)
    assert panel.scan_ports() == []
    panel._ports = panel.scan_ports()
    assert panel.port_options() == [OFF, SIM]


def test_a_listing_that_raises_is_reported_not_swallowed(
        panel, monkeypatch, warnings):
    def boom():
        raise OSError("enumeration failed")

    monkeypatch.setattr(serial_port_module, "list_ports", boom, raising=False)
    assert panel.scan_ports() == ["COM1", "COM2", "COM3", "COM4"]
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
        panel.validate([{"model": "Alpha", "port": OFF, "gamepad": None,
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
    assert panel.is_launched is False


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


def test_a_row_left_off_builds_nothing(panel, fake_types):
    """WEB-4/MANAGER-12: Web dropped the enabled flag and built every row."""
    select(panel, "alpha", "port", SIM)
    assert [c["model"] for c in panel.configs] == ["Alpha"]
    panel.run("launch")
    assert panel.controller.model_names == ["Alpha"]


def test_sim_works_for_every_model(panel, fake_types):
    for key in ("alpha", "beta", "screen"):
        select(panel, key, "port", SIM)
    built = panel.build()
    assert built == list(fake_types)
    for name, model_class in fake_types.items():
        model = panel.controller._model(name)
        assert isinstance(model, model_class) and model.is_open
        assert model.sim is True
        # A model that needs no port is built without one rather than with
        # the string "SIM".
        assert model.port == (SIM if model_class.NEEDS_PORT else None)


def test_a_no_port_model_set_to_on_is_built_as_hardware(panel):
    select(panel, "screen", "port", ON)
    assert panel.configs == [{"model": "Screen", "port": None,
                              "gamepad": None, "sim": False}]
    panel.build()
    model = panel.controller._model("Screen")
    assert model.sim is False and model.port is None


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
    offer(panel, "/dev/ttyUSB0")
    for key in ("alpha", "beta"):
        select(panel, key, "port", "/dev/ttyUSB0")
    result = panel.run("launch")
    assert result.is_refused and "already assigned" in result.reason
    assert panel.controller.calls == []      # not even a reset


# -- launched, and back again ---------------------------------------------

def test_a_successful_launch_is_what_tells_a_view_to_collapse_the_panel(panel):
    """The views collapse Setup themselves; `is_launched` is the fact they
    collapse on, and it is in `state` so a polling view sees it."""
    assert panel.is_launched is False and panel.state["is_launched"] is False
    select(panel, "alpha", "port", SIM)
    assert panel.run("launch").is_ok
    assert panel.is_launched is True and panel.state["is_launched"] is True
    assert panel.mode_name == "launched"


def test_launch_gives_way_to_relaunch_once_the_system_is_up(panel):
    select(panel, "alpha", "port", SIM)
    launch, relaunch = [e for e in _elements(panel)
                        if e.get("command") == "launch"]
    import schema as sch
    assert sch.is_enabled(launch, "ready") and not sch.is_enabled(launch, "launched")
    assert sch.is_enabled(relaunch, "launched")
    panel.run("launch")
    # Both buttons carry the one command, and it stays runnable: a relaunch
    # resets the Controller first, like any build.
    assert panel.run("launch").is_ok
    assert panel.controller.model_names == ["Alpha"]


def test_stop_system_takes_everything_down_and_offers_launch_again(panel):
    select(panel, "alpha", "port", SIM)
    panel.run("launch")
    model = panel.controller._model("Alpha")
    assert panel.run("stop_system").is_ok
    assert panel.controller.model_names == [] and model.closed == 1
    assert panel.is_launched is False and panel.mode_name == "ready"


def test_stop_system_refuses_when_nothing_is_running(panel):
    result = panel.run("stop_system")
    assert result.is_refused and "Nothing is running" in result.reason


# -- selection and auto-assign --------------------------------------------

def test_a_dropdown_choice_travels_as_the_command_argument(panel):
    offer(panel, "/dev/ttyUSB0")
    select(panel, "alpha", "port", "/dev/ttyUSB0")
    assert panel.alpha_port == "/dev/ttyUSB0"
    assert panel.state["values"]["alpha_port"] == "/dev/ttyUSB0"


def test_a_choice_that_is_not_on_offer_is_refused(panel):
    result = panel.run("set_alpha_port", args=("/dev/nope",))
    assert result.is_refused and "options" in result.reason


def test_a_port_name_is_refused_for_a_model_that_has_no_port(panel):
    offer(panel, "/dev/ttyUSB0")
    result = panel.run("set_screen_port", args=("/dev/ttyUSB0",))
    assert result.is_refused and "options" in result.reason


def test_a_model_without_a_gamepad_has_no_gamepad_dropdown(panel):
    commands = {e.get("command") for e in _elements(panel)}
    assert "set_alpha_gamepad" in commands
    assert "set_screen_gamepad" not in commands
    assert "set_alpha_port" in commands and "set_screen_port" in commands


def test_the_row_commands_are_named_after_the_model(panel, fake_types):
    """The view agents render these generically, but the names are the
    contract: `set_<row>_port` / `set_<row>_gamepad`, one per row."""
    assert {e.get("command") for e in _elements(panel) if e["type"] == "dropdown"} == {
        "set_alpha_port", "set_alpha_gamepad",
        "set_beta_port", "set_beta_gamepad", "set_screen_port"}


def test_auto_assign_points_each_row_at_the_port_that_answered(panel):
    panel._found = {"/dev/ttyUSB0": "Beta", "/dev/ttyUSB1": None}
    assert panel.auto_assign() == ["Beta on /dev/ttyUSB0"]
    assert panel.beta_port == "/dev/ttyUSB0"
    assert panel.alpha_port == OFF
    assert panel.beta_status == "detected: Beta"
    assert panel.alpha_status == "off"


def test_auto_assign_never_overrides_what_the_operator_chose(panel):
    """The machine's guess does not overrule a person's choice (Addendum 2:
    auto-assign now runs by itself, so it has to be the polite one)."""
    offer(panel, "/dev/ttyUSB7")
    select(panel, "beta", "port", "/dev/ttyUSB7")
    panel._found = {"/dev/ttyUSB0": "Beta"}
    assert panel.auto_assign() == []
    assert panel.beta_port == "/dev/ttyUSB7"
    assert panel.auto_assign(force=True) == ["Beta on /dev/ttyUSB0"]


def test_auto_assign_with_nothing_identified_is_not_an_error(panel):
    """It runs on the scan worker now: "nothing was found" is an outcome, not
    a refusal to raise at a thread that has no one to tell."""
    assert panel.auto_assign() == []


def test_two_ports_answering_as_one_model_keeps_the_first_and_warns(
        panel, warnings):
    panel._found = {"/dev/ttyUSB0": "Beta", "/dev/ttyUSB1": "Beta"}
    assert panel.auto_assign() == ["Beta on /dev/ttyUSB0"]
    assert panel.beta_port == "/dev/ttyUSB0"
    assert [e.title for e in warnings if e.title != "Auto-assign"] == \
        ["Two Devices Answered Alike"]


# -- the status column -----------------------------------------------------

def test_the_status_column_says_off_simulated_detected_or_not_detected(panel):
    assert panel.alpha_status == "off"
    select(panel, "alpha", "port", SIM)
    assert panel.alpha_status == "simulated"
    offer(panel, "/dev/ttyUSB0")
    select(panel, "alpha", "port", "/dev/ttyUSB0")
    assert panel.alpha_status == "not scanned"
    panel._found["/dev/ttyUSB0"] = None
    panel._refresh_rows()
    assert panel.alpha_status == "not detected"
    panel._found["/dev/ttyUSB0"] = "Alpha"
    panel._refresh_rows()
    assert panel.alpha_status == "detected: Alpha"


def test_a_row_pointed_at_a_port_that_answered_otherwise_says_so(panel):
    offer(panel, "/dev/ttyUSB0")
    select(panel, "alpha", "port", "/dev/ttyUSB0")
    panel._found["/dev/ttyUSB0"] = "Beta"
    panel._refresh_rows()
    assert panel.alpha_status == "detected: Beta"      # and build() refuses it
    assert panel.run("launch").is_refused


# -- the schema every view renders ----------------------------------------

def test_every_element_is_a_type_a_renderer_must_implement(panel):
    import schema as sch
    for element in sch.elements(panel.schema):
        assert element["type"] in sch.ELEMENT_TYPES


def test_every_declared_command_and_options_source_exists(panel):
    import schema as sch
    for element in sch.elements(panel.schema):
        for key in ("command", "options_command"):
            name = element.get(key)
            if name:
                assert callable(getattr(panel, name)), name


def test_options_come_from_the_scan(panel):
    panel._ports = ["/dev/ttyUSB0"]
    panel._gamepads = ["None", "ID 0: Pad"]
    assert panel.options("port_options") == [OFF, SIM, "/dev/ttyUSB0"]
    assert panel.options("gamepad_options") == ["None", "ID 0: Pad"]
    assert panel.options("device_options") == [OFF, ON, SIM]


def test_a_command_the_schema_does_not_declare_is_refused(panel):
    assert panel.run("build").is_refused
    assert panel.run("auto_assign").is_refused   # automatic, not a button


# -- state carries what a view needs to draw ------------------------------

def test_state_carries_the_scan_phase_ports_rows_and_launch_flag(panel):
    panel._ports = ["/dev/ttyUSB0"]
    panel._found = {"/dev/ttyUSB0": "Alpha"}
    panel._refresh_rows()
    state = panel.state
    assert state["scan"]["phase"] == "idle"
    assert state["scan"]["ports"] == ["/dev/ttyUSB0"]
    assert state["scan"]["found"] == {"/dev/ttyUSB0": "Alpha"}
    assert state["is_scanning"] is False and state["is_launched"] is False
    alpha = next(r for r in state["rows"] if r["key"] == "alpha")
    assert alpha == {"key": "alpha", "name": "Alpha", "port": OFF,
                     "gamepad": "None", "status": "off", "detected": None,
                     "needs_port": True, "needs_gamepad": True,
                     "is_chosen": False, "options_command": "port_options"}
    assert state["values"]["scan_status"] == "not scanned yet"
    assert state["scan"]["elapsed"] is None and state["scan"]["port"] is None


# -- refresh and gating ----------------------------------------------------

def test_refresh_is_the_only_scan_button_and_starts_a_scan(panel, monkeypatch):
    started = []
    monkeypatch.setattr(Setup, "scan", lambda self: started.append(True) or True)
    assert panel.run("refresh").is_ok
    assert started == [True]


def test_launching_is_gated_while_a_scan_runs_but_choosing_is_not(
        panel, monkeypatch):
    """The operator may point a row at a port while the scan is still walking
    the rest of them; that choice then wins over auto-assign."""
    started = []
    monkeypatch.setattr(Setup, "scan", lambda self: started.append(True) or True)
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    panel._scan_thread = thread
    try:
        assert panel.mode_name == "scanning"
        assert panel.run("launch").is_refused
        assert panel.run("set_alpha_port", args=(SIM,)).is_ok
        # Refresh cancels the running scan first. This one will not stop;
        # F18: Refresh returns at once anyway (it used to join for up to 1 s
        # on the view thread and then refuse), and the next scan starts when
        # the old one finally ends.
        began = time.monotonic()
        assert panel.run("refresh").is_ok
        assert time.monotonic() - began < 0.2
        assert panel._abort.is_set()
        assert panel.state["scan"]["restart_pending"] is True
        assert started == []
    finally:
        release.set()
        thread.join(timeout=1)
    assert _wait(lambda: started == [True])
    assert panel.mode_name == "ready"


def test_refresh_cancels_a_running_scan_before_starting_the_next(
        panel, monkeypatch):
    stopping = threading.Event()
    thread = threading.Thread(target=stopping.wait, daemon=True)
    thread.start()
    panel._scan_thread = thread
    started = []

    def scan(self):
        started.append(True)
        return True

    monkeypatch.setattr(Setup, "scan", scan)
    monkeypatch.setattr(Setup, "cancel_scan",
                        lambda self: stopping.set() or True)
    assert panel.run("refresh").is_ok
    thread.join(timeout=1)
    # The next scan starts on Refresh's helper thread once the old one ends.
    assert _wait(lambda: started == [True])


def test_start_kicks_the_scan_off_by_itself(panel, monkeypatch):
    """Addendum 2: Setup scans automatically at start; `app.launch()` calls
    this right before the view opens."""
    monkeypatch.setattr(serial_port_module, "list_ports", lambda: [],
                        raising=False)
    assert panel.start() is True
    thread = panel._scan_thread
    assert thread is not None and thread is not threading.current_thread()
    thread.join(timeout=5)
    assert not panel.is_scanning
    assert panel.scan_phase == "done"
    assert panel.state["scan"]["status"] == "ready"


def test_start_never_raises_when_a_scan_is_already_running(panel):
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    panel._scan_thread = thread
    try:
        assert panel.start() is False
    finally:
        release.set()
        thread.join(timeout=1)



def _wait(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# -- F18: a hung scan -------------------------------------------------------

@pytest.fixture
def hung_port(panel, monkeypatch):
    """A port whose handshake never answers (and ignores the abort) until
    released: the worst case, a driver call that does not come back."""
    release = threading.Event()
    reached = threading.Event()
    monkeypatch.setattr(serial_port_module, "list_ports",
                        lambda: ["/dev/ttyHUNG"], raising=False)

    def identify(self, port, should_abort=None):
        reached.set()
        release.wait(10)
        return None

    monkeypatch.setattr(Setup, "identify", identify)
    yield panel, reached
    release.set()
    thread = panel._scan_thread
    if thread is not None:
        thread.join(timeout=5)
    restart = panel._restart_thread
    if restart is not None:
        restart.join(timeout=5)


def test_a_hung_scan_names_the_port_and_how_long_it_has_waited(
        hung_port, monkeypatch):
    panel, reached = hung_port
    clock = [1000.0]
    panel.scan()
    assert reached.wait(2)
    monkeypatch.setattr(station_setup.time, "monotonic", lambda: clock[0])
    panel._port_started = panel._scan_started = 1000.0
    clock[0] = 1007.4
    state = panel.state
    assert "/dev/ttyHUNG" in state["values"]["scan_status"]
    assert "7 s" in state["values"]["scan_status"]
    assert state["scan"]["port"] == "/dev/ttyHUNG"
    assert state["scan"]["elapsed"] == 7.4


def test_a_hung_scan_says_why_launch_is_greyed_out_and_can_be_cancelled(
        hung_port):
    panel, reached = hung_port
    panel.scan()
    assert reached.wait(2)
    summary = panel.state["values"]["summary"]
    assert "Launch waits for the scan" in summary and "Cancel scan" in summary
    assert panel.run("cancel_scan").is_ok
    assert panel._abort.is_set()


def test_refresh_during_a_hung_scan_does_not_block_the_caller(hung_port):
    panel, reached = hung_port
    panel.scan()
    assert reached.wait(2)
    began = time.monotonic()
    assert panel.run("refresh").is_ok
    assert panel.run("refresh").is_ok, "a second press while restarting"
    assert time.monotonic() - began < 0.2


def test_the_summary_is_a_sentence_when_nothing_is_selected(panel):
    assert panel.summary.startswith("Nothing selected.")
