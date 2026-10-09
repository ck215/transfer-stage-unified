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

pytestmark = pytest.mark.usefixtures("profiles_on")   # the held feature, on
from controller.controller import Controller
from devices import gamepad as gamepad_module
from devices import serial_port as serial_port_module
from events import events
from result import NeedsConfirm, Refused
from controller.setup import MODEL_TYPES, ON, SIM, Setup


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


def connect(panel, key):
    """There is no Launch tick (owner 2026-10-07): a row launches when its
    board answered. Without a board, SIM is the development choice."""
    result = panel.run(f"set_{key}_port", args=(SIM,))
    assert result.is_ok, result.reason
    return result


def disconnect(panel, key):
    """The row's board is gone, as a scan finds it: not connected."""
    setattr(panel, f"{key}_port", station_setup.NOT_CONNECTED)
    panel._refresh_rows()


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
                                 "Rotator", "XYZ Stage",                       # 2026-10-09
                                 "XYZ Stage (Mega)",                           # MEGA_STANDARD
                                 "RGB Analysis", "Transfer Map",               # Tier S; RG-3
                                 *(["Sample DB"] if station_setup.SAMPLE_MAP_ENABLED else [])]
    assert len(set(MODEL_TYPES.values())) == 9 + station_setup.SAMPLE_MAP_ENABLED


def test_model_types_is_the_only_list_of_models(panel, fake_types):
    """One registry: the rows a view renders come from the same dict that
    builds. Four copies of this list disagreed before RC-7."""
    assert panel.model_types == list(fake_types)
    titles = [section["title"] for section in panel.schema["sections"]]
    # The drawer's job first, then upkeep, then the defaults (UX audit
    # 2026-10-08).
    assert titles == ["Devices", *fake_types, "Launch", "Update", "Firmware",
                      Setup.ACCOUNT_SECTION]


# -- the table (Addendum 2) ------------------------------------------------

def test_every_section_is_a_row_so_setup_is_a_table_not_a_column(panel):
    """The owner's ruling: one compact table, one row per model type. Every
    renderer lays a `layout="row"` section out horizontally."""
    assert [s["layout"] for s in panel.schema["sections"]] == \
        ["row"] * len(panel.schema["sections"])


def test_a_row_is_name_then_port_hard_reset_gamepad_then_status(panel):
    """Owner 2026-10-07: no Launch tick - every connected device launches -
    and a launched device's row carries its Hard reset."""
    row = next(s for s in panel.schema["sections"] if s["title"] == "Alpha")
    assert [(e["type"], e.get("model_attr") or e.get("command"))
            for e in row["elements"]] == [
        ("dropdown", "alpha_port"),
        ("button", "hard_reset_alpha"),
        # Owner ruling 2026-10-08 (B): declared, drawn as the row's key.
        ("internal", "start_alpha"),
        ("dropdown", "alpha_gamepad"),
        ("readonly", "alpha_status"),
    ]
    port, reset, _, pad, _ = row["elements"]
    assert "enabled_by" not in port and "enabled_by" not in pad
    assert reset["text"] == "Hard reset" and reset["enabled_when"] == ["launched"]
    assert panel.alpha_name == "Alpha"
    # A row with no port has no board to reset.
    screen = next(s for s in panel.schema["sections"] if s["title"] == "Screen")
    assert "button" not in [e["type"] for e in screen["elements"]]


def test_there_is_no_launch_tick(panel):
    assert "checkbox" not in [e["type"] for e in _elements(panel)]
    assert not hasattr(panel, "set_alpha_enabled")
    assert panel.run("set_alpha_enabled", args=(True,)).is_refused


def test_there_is_no_mode_dropdown_and_no_set_mode_command(panel):
    """Addendum 2: the Port dropdown carries it all; "Off" is the disabled
    state. A second control that could contradict the first is gone."""
    commands = {e.get("command") for e in _elements(panel)}
    assert not any(str(c).endswith("_mode") for c in commands if c)
    assert not hasattr(panel, "set_alpha_mode")
    assert not hasattr(panel, "alpha_mode")
    assert "mode_options" not in {e.get("options_command") for e in _elements(panel)}


def test_the_port_dropdown_offers_sim_and_every_scanned_port_never_off(panel):
    """G3: "Off" is the checkbox, not a dropdown entry that could sit beside
    a chosen port and disagree with it."""
    panel._ports = ["/dev/ttyUSB0", "/dev/ttyUSB1"]
    assert panel.port_options() == [SIM, "/dev/ttyUSB0", "/dev/ttyUSB1"]
    assert panel.options("port_options") == panel.port_options()


def test_a_model_that_needs_no_port_still_has_one_dropdown(panel):
    """The screen monitor has nothing to plug in, so its dropdown is the same
    control with the port names left out - not a different kind of widget."""
    row = next(s for s in panel.schema["sections"] if s["title"] == "Screen")
    dropdowns = [e for e in row["elements"] if e["type"] == "dropdown"]
    assert len(dropdowns) == 1
    assert dropdowns[0]["options_command"] == "device_options"
    assert panel.options("device_options") == [ON, SIM]


def test_a_portless_rows_dropdown_is_not_captioned_port(panel):
    """The Transfer Map's and Sample DB's On/SIM choice has no port behind
    it: at phone width each control shows its own caption, and "Port On"
    read as a port named On (a screen reader said "Port, Transfer Map").
    A row with a port keeps "Port"."""
    screen = next(s for s in panel.schema["sections"] if s["title"] == "Screen")
    choice = next(e for e in screen["elements"] if e["type"] == "dropdown")
    assert choice["text"] == Setup.PORTLESS_CAPTION == "Run"
    alpha = next(s for s in panel.schema["sections"] if s["title"] == "Alpha")
    assert alpha["elements"][0]["text"] == "Port"


def test_the_header_row_offers_refresh_the_scan_status_and_cancel(panel):
    # F18 added "Cancel scan" (enabled only while scanning): a hung scan
    # could not be given up before.
    # L3 added "Address": the Web view's URL, which used to be a terminal line.
    header = next(s for s in panel.schema["sections"] if s["title"] == "Devices")
    assert [(e["type"], e.get("command") or e.get("model_attr"))
            for e in header["elements"]] == [("button", "refresh"),
                                             ("readonly", "scan_status"),
                                             ("button", "cancel_scan"),
                                             ("readonly", "web_address")]
    cancel = header["elements"][2]
    assert cancel["enabled_when"] == ["scanning"]
    assert "scan" not in {e.get("command") for e in _elements(panel)}


def test_the_launch_row_is_launch_alone(panel):
    """Owner 2026-10-07: no Relaunch and no Close every model. A device is
    recovered with its row's Hard reset; changing devices is a Restart."""
    [row] = [s for s in panel.schema["sections"] if s["title"] == "Launch"]
    assert [e.get("text") for e in row["elements"]] == ["Launch"]
    commands = {e.get("command") for e in _elements(panel)}
    assert "stop_system" not in commands and not hasattr(panel, "stop_system")
    # I4: no sentence in the Launch row; `summary` is state only.
    assert "summary" not in {e.get("model_attr") for e in row["elements"]}


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
    with a literal "USB" in its name come first, then the rest by name.
    macOS's debug console is dropped (2026-10-09): probing it at 57600 fails
    with EINVAL and warned "Probe failed" on every launch on a Mac."""
    monkeypatch.setattr(serial_port_module, "list_ports", lambda: [
        ("/dev/cu.Bluetooth-Incoming-Port", "n/a"),
        ("/dev/cu.usbmodem1101", "USB VID:PID=2341"),
        ("/dev/cu.debug-console", "n/a"),
    ], raising=False)
    ports = panel.scan_ports()
    assert "/dev/cu.Bluetooth-Incoming-Port" not in ports
    assert ports == ["/dev/cu.usbmodem1101"]


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
    assert panel.port_options() == [SIM]


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
        panel.validate([{"model": "Alpha", "port": "Off", "gamepad": None,
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


def test_a_row_not_connected_builds_nothing(panel, fake_types):
    """WEB-4/MANAGER-12: Web dropped the enabled flag and built every row.
    Beta's board never answered: it is not built. The Screen needs no port
    and is always built."""
    select(panel, "alpha", "port", SIM)
    assert [c["model"] for c in panel.configs] == ["Alpha", "Screen"]
    panel.run("launch")
    assert panel.controller.model_names == ["Alpha", "Screen"]


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


@pytest.fixture
def port_panel(fake_types):
    """A station of port rows only: nothing launches until a board answers."""
    fake_types.pop("Screen")
    return Setup(RecordingController())


def test_launch_refuses_when_nothing_is_connected(port_panel):
    result = port_panel.run("launch")
    assert result.is_refused and "No device is connected" in result.reason
    assert port_panel.controller.calls == []


def test_a_build_refuses_a_collision_before_building_anything(panel):
    """Rows launch only on a port their own board answered on, so two rows
    cannot share one through Launch; a build handed such configs refuses
    before the reset."""
    with pytest.raises(Refused) as refusal:
        panel.build([{"model": "Alpha", "port": "/dev/ttyUSB0", "sim": False},
                      {"model": "Beta", "port": "/dev/ttyUSB0", "sim": False}])
    assert "already assigned" in refusal.value.reason
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


def test_launch_runs_once_and_a_second_launch_is_refused(panel):
    """Owner 2026-10-07: Launch is once per run of the station. A second
    press is refused with the way forward and leaves the models alone."""
    select(panel, "alpha", "port", SIM)
    [launch] = [e for e in _elements(panel) if e.get("command") == "launch"]
    import schema as sch
    from views.base import gate_reason
    assert sch.is_enabled(launch, "ready") and not sch.is_enabled(launch, "launched")
    # Owner ruling 2026-10-08 (B): a device is started from its row, no Restart.
    assert gate_reason(launch, "launched") == "Launched: start a device from its row"
    assert panel.run("launch").is_ok
    model = panel.controller._model("Alpha")
    # The panel's gate refuses the command (the button is disabled), and a
    # direct call is refused by `launch()` itself with the way forward.
    again = panel.run("launch", args=(True,))
    assert again.is_refused and "launched" in again.reason
    from result import Refused
    with pytest.raises(Refused, match="Hard reset.*choose its Port, then Start"):
        panel.launch(True)
    assert panel.controller.model_names == ["Alpha", "Screen"]
    assert panel.controller._model("Alpha") is model and model.closed == 0


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
    # The account section (2026-10-07) has no dropdown: the Phase 1 profile
    # picker became an Email and a Password entry.


def test_auto_assign_points_each_row_at_the_port_that_answered(panel):
    panel._found = {"/dev/ttyUSB0": "Beta", "/dev/ttyUSB1": None}
    assert panel.auto_assign() == ["Beta on /dev/ttyUSB0"]
    assert panel.beta_port == "/dev/ttyUSB0" and panel.beta_enabled is True
    assert panel.alpha_port == station_setup.NOT_CONNECTED
    assert panel.alpha_enabled is False
    assert panel.beta_status == "detected: Beta"
    assert panel.alpha_status == "not connected"


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

def test_the_status_column_says_not_connected_simulated_or_detected(panel):
    assert panel.alpha_status == "not connected"
    select(panel, "alpha", "port", SIM)
    assert panel.alpha_status == "simulated"
    offer(panel, "/dev/ttyUSB0")
    select(panel, "alpha", "port", "/dev/ttyUSB0")
    assert panel.alpha_status == "not scanned"
    assert panel.alpha_enabled is False
    panel._found["/dev/ttyUSB0"] = None
    panel._refresh_rows()
    assert panel.alpha_status == "not connected" and panel.alpha_enabled is False
    panel._found["/dev/ttyUSB0"] = "Alpha"
    panel._refresh_rows()
    assert panel.alpha_status == "detected: Alpha" and panel.alpha_enabled is True


def test_a_row_pointed_at_a_port_that_answered_otherwise_says_so(panel):
    offer(panel, "/dev/ttyUSB0")
    select(panel, "alpha", "port", "/dev/ttyUSB0")
    panel._found["/dev/ttyUSB0"] = "Beta"
    panel._refresh_rows()
    assert panel.alpha_status == "detected: Beta"
    # Not connected as Alpha, so it does not launch (and build() refuses it).
    assert "Alpha" not in [c["model"] for c in panel.configs]
    assert panel.run("launch").is_ok
    assert "Alpha" not in panel.controller.model_names
    with pytest.raises(Refused):
        panel.build([{"model": "Alpha", "port": "/dev/ttyUSB0",
                      "gamepad": None, "sim": False}])


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
    assert panel.options("port_options") == [SIM, "/dev/ttyUSB0"]
    assert panel.options("gamepad_options") == ["None", "ID 0: Pad"]
    assert panel.options("device_options") == [ON, SIM]


def test_option_labels_name_known_devices_and_keep_the_raw_values(
        panel, monkeypatch):
    """Friendly names are labels only: the options (what configs, choices
    and the wire carry) stay raw; an unknown port shows its raw path."""
    monkeypatch.setattr(serial_port_module, "list_ports", lambda: [
        ("/dev/ttyACM0", "USB VID:PID=2341:0042 SER=A"),
        ("/dev/ttyACM1", "USB VID:PID=16C0:0483 SER=B"),
        ("/dev/ttyUSB0", "USB VID:PID=0403:6001 SER=C"),
        ("/dev/ttyUSB1", "USB VID:PID=1234:5678 SER=D"),
    ], raising=False)
    panel._ports = panel.scan_ports()
    panel._found = {"/dev/ttyACM0": "Stepper Probe", "/dev/ttyACM1": None}
    options = panel.options("port_options")
    assert options == [SIM, "/dev/ttyACM0", "/dev/ttyACM1", "/dev/ttyUSB0",
                       "/dev/ttyUSB1"]
    assert panel.option_labels("port_options", options) == [
        SIM, "Stepper Probe — ACM0", "Teensy — ACM1",
        "FTDI USB-serial — USB0", "/dev/ttyUSB1"]
    assert panel.option_labels("device_options", [ON, SIM]) == [ON, SIM]
    pads = ["None", "ID 0: Logitech Gamepad F310", "ID 1: Mystery Pad"]
    assert panel.option_labels("gamepad_options", pads) == [
        "None", "Logitech F310", "ID 1: Mystery Pad"]


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
    assert alpha == {"key": "alpha", "name": "Alpha", "enabled": False,
                     "port": station_setup.NOT_CONNECTED,
                     "gamepad": "None", "status": "not connected",
                     "detected": None,
                     "needs_port": True, "needs_gamepad": True,
                     "is_chosen": False, "seen_after_launch": None,
                     "pending_reset": False, "can_start": False,
                     "options_command": "port_options"}
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
    assert "Launch waits for the scan" in panel.summary      # state for the API
    # I4: the reason is on the scan line the operator is reading.
    line = panel.state["values"]["scan_status"]
    assert "Launch waits for the scan" in line and "Cancel scan" in line
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


def test_the_summary_is_a_sentence_when_nothing_is_connected(port_panel):
    assert port_panel.summary.startswith("Nothing to launch: no device is connected.")


# -- every connected device launches (owner 2026-10-07; was the G3 tick) ----

def test_every_port_row_starts_not_connected_and_a_row_with_no_port_is_on(panel):
    assert (panel.alpha_enabled, panel.beta_enabled, panel.screen_enabled) == \
        (False, False, True)
    assert panel.alpha_port == station_setup.NOT_CONNECTED and panel.screen_port == ON
    assert [c["model"] for c in panel.configs] == ["Screen"]


def test_sim_puts_a_row_in_the_configs_and_losing_the_board_takes_it_out(panel):
    connect(panel, "alpha")
    assert [c["model"] for c in panel.configs] == ["Alpha", "Screen"]
    assert panel.alpha_status == "simulated"
    disconnect(panel, "alpha")
    assert [c["model"] for c in panel.configs] == ["Screen"]
    assert panel.alpha_status == "not connected"


def test_not_connected_is_not_on_offer(panel):
    """The operator does not choose to leave a connected device out."""
    offer(panel, "/dev/ttyUSB0")
    assert station_setup.NOT_CONNECTED not in panel.port_options()
    assert panel.run("set_alpha_port",
                     args=(station_setup.NOT_CONNECTED,)).is_refused


def test_a_board_that_answered_launches_its_row(panel):
    panel._found = {"/dev/ttyUSB0": "Beta"}
    panel.auto_assign()
    assert panel.beta_enabled is True
    assert [(c["model"], c["port"]) for c in panel.configs] == [
        ("Beta", "/dev/ttyUSB0"), ("Screen", None)]


def test_a_port_that_vanished_is_not_connected_again(panel):
    offer(panel, "/dev/ttyUSB0")
    select(panel, "alpha", "port", "/dev/ttyUSB0")
    panel._ports.remove("/dev/ttyUSB0")
    panel._drop_stale_selections()
    panel._refresh_rows()
    assert panel.alpha_enabled is False
    assert panel.alpha_port == station_setup.NOT_CONNECTED
    assert "alpha" not in panel._chosen


def test_the_summary_is_a_count_not_a_list_of_names_and_ports(panel):
    """G3: the joined "Alpha (/dev/x), Beta (simulated), ..." line widened
    every table it sat in; the rows already say which and where."""
    assert panel.summary == "1 device to launch."
    connect(panel, "alpha")
    connect(panel, "beta")
    assert panel.summary == "3 devices to launch."
    assert len(panel.summary) < 40
    assert "Alpha" not in panel.summary and SIM not in panel.summary


def test_state_rows_carry_whether_the_row_launches(panel):
    connect(panel, "beta")
    rows = {r["key"]: r["enabled"] for r in panel.state["rows"]}
    assert rows == {"alpha": False, "beta": True, "screen": True}


def _launch_alpha(panel):
    select(panel, "alpha", "port", SIM)
    assert panel.run("launch").is_ok


def _energize_first_model(panel):
    name = panel.controller.model_names[0]
    panel.controller._model(name).is_energized = True   # this file's fake is a plain class
    return name


def test_an_energized_station_is_never_taken_down_by_launch(panel):
    """Round 8 (PM8-6) asked before a relaunch tore energized models down;
    since 2026-10-07 there is no relaunch, so a confirmed Launch over a
    running, energized station is refused and closes nothing."""
    _launch_alpha(panel)
    name = _energize_first_model(panel)
    result = panel.run("launch", args=(True,))
    assert result.is_refused
    assert name in panel.controller.model_names


# -- a host's row launches the models drawn on its page (Model.HOST) --------
# Owner ruling 2026-09-28: "it should just be Transfer Map that calls both
# those tools on the startup menu". A hosted model has no row of its own.

@pytest.fixture
def hosted_types(monkeypatch):
    types = {}
    for model_class in (make_model_class("Map", needs_port=False),
                        make_model_class("Red", needs_port=False),
                        make_model_class("Alpha", "a")):
        types[model_class.NAME] = model_class
    types["Red"].HOST = "Map"
    monkeypatch.setattr(station_setup, "MODEL_TYPES", types)
    return types


def test_a_hosted_model_has_no_setup_row(hosted_types):
    panel = Setup(RecordingController())
    assert list(panel._rows) == ["map", "alpha"]
    assert [r["name"] for r in panel.state["rows"]] == ["Map", "Alpha"]
    assert not hasattr(panel, "red_enabled")


def test_a_host_launches_the_hosted_model_first_with_its_sim_choice(hosted_types):
    panel = Setup(RecordingController())
    assert [c["model"] for c in panel.configs] == ["Red", "Map"]
    red = panel.configs[0]
    assert red["port"] is None and red["gamepad"] is None
    assert red["sim"] == panel.configs[1]["sim"]
    connect(panel, "map")
    assert [c["sim"] for c in panel.configs] == [True, True]


def test_a_hosted_class_may_declare_no_resources():
    hosted = make_model_class("Needy", "n")     # needs a port
    hosted.HOST = "Map"
    with pytest.raises(ValueError, match="no Setup row"):
        station_setup.register(hosted)
    station_setup.MODEL_TYPES.pop("Needy", None)


# -- Hard reset, and a board plugged in after the launch (owner 2026-10-07) --

def _launch_alpha_on(panel, port="/dev/ttyUSB0"):
    """Alpha's board answered on `port`, and the station is launched."""
    offer(panel, port)
    panel._found[port] = "Alpha"
    select(panel, "alpha", "port", port)
    assert panel.run("launch").is_ok
    return panel.controller._model("Alpha")


def test_hard_reset_is_not_offered_before_the_launch(panel):
    assert panel.run("hard_reset_alpha", args=(True,)).is_refused
    assert panel.controller.calls == []


def test_hard_reset_asks_then_reopens_the_same_device_on_the_same_port(panel):
    old = _launch_alpha_on(panel)
    config = panel.controller.config("Alpha")
    asked = panel.run("hard_reset_alpha")
    assert asked.needs_confirm and asked.command == "hard_reset_alpha"
    assert asked.reason.startswith("Hard reset Alpha?")
    assert "/dev/ttyUSB0" in asked.reason
    assert panel.controller._model("Alpha") is old, "nothing was reset by the question"
    panel.controller.calls.clear()
    assert panel.run("hard_reset_alpha", args=(True,)).is_ok
    assert panel.controller.calls == ["remove:Alpha", "add:Alpha"]
    new = panel.controller._model("Alpha")
    assert new is not old and new.is_open and old.closed == 1 and not old.is_open
    assert new.port == "/dev/ttyUSB0" and panel.controller.config("Alpha") == config
    assert panel.is_launched and "Screen" in panel.controller.model_names


# Owner ruling 2026-10-08 (A): "Hard reset should force a device to clear
# state, disconnect, reconnect." It is never refused for being energized: the
# model's full stop path runs first (estop, then close: halt, de-energize,
# the heater's off read back), then the port closes, and a fresh model (no
# latch, no mode) is built and identified. One question while energized.

@pytest.mark.parametrize("busy", ["is_energized", "is_active"])
def test_hard_reset_of_an_energized_device_asks_once_then_stops_it_first(
        panel, fake_types, monkeypatch, busy):
    log = []
    old = _launch_alpha_on(panel)
    setattr(old, busy, True)            # this file's fake is a plain class
    old.is_estopped = True              # a latched stop, cleared by the reset
    _record_order(monkeypatch, fake_types["Alpha"], log)
    asked = panel.run("hard_reset_alpha")
    assert asked.needs_confirm and asked.command == "hard_reset_alpha"
    assert asked.reason == "Alpha is energized: Hard reset stops it first. Reset?"
    assert log == [] and panel.controller._model("Alpha") is old
    assert panel.run("hard_reset_alpha", args=(True,)).is_ok
    assert log[0] == ("estop", "/dev/ttyUSB0"), "the stop path before anything else"
    assert log.index(("estop", "/dev/ttyUSB0")) < log.index(("close", "/dev/ttyUSB0")) \
        < log.index(("build", "/dev/ttyUSB0", None)) < log.index(("open", "/dev/ttyUSB0"))
    new = panel.controller._model("Alpha")
    assert new is not old and new.is_open and not old.is_open
    for cleared in ("is_energized", "is_active", "is_estopped"):
        assert not getattr(new, cleared, False), cleared


def test_the_forced_reset_stops_the_model_while_full_stop_still_reaches_it(
        panel, fake_types, monkeypatch):
    """Architecture audit 2026-10-08: `Controller.remove` drops a model
    before it stops it, so a FULL STOP pressed then would miss it. The
    reset's stop runs while the model is still registered."""
    old = _launch_alpha_on(panel)
    old.is_energized = True
    registered = []
    original = fake_types["Alpha"].estop

    def estop(self):
        registered.append("Alpha" in panel.controller.model_names)
        return original(self)
    monkeypatch.setattr(fake_types["Alpha"], "estop", estop)
    assert panel.run("hard_reset_alpha", args=(True,)).is_ok
    assert registered and registered[0] is True


def test_hard_reset_is_refused_while_a_scan_runs(panel):
    old = _launch_alpha_on(panel)

    class Alive:
        def is_alive(self):
            return True
    panel._scan_thread = Alive()
    result = panel.run("hard_reset_alpha", args=(True,))   # the gate
    assert result.is_refused and "scan" in result.reason.lower()
    # ... and the command itself, for a caller that is not gated.
    with pytest.raises(Refused, match="scan"):
        panel.hard_reset_alpha(True)
    assert panel.controller._model("Alpha") is old and old.closed == 0
    panel._scan_thread = None


def test_a_hard_reset_that_fails_mid_way_leaves_the_device_stopped_and_retryable(
        panel, fake_types, monkeypatch):
    """A peer that raises while the old model is being taken out must never
    leave it registered nowhere and still energized."""
    log = []
    old = _launch_alpha_on(panel)
    old.is_energized = True
    _record_order(monkeypatch, fake_types["Alpha"], log)

    falls = [True]

    def boom(self, name, model):
        if falls.pop() if falls else False:
            raise RuntimeError("a peer fell over")
    monkeypatch.setattr(fake_types["Screen"], "on_model_removed", boom)
    result = panel.run("hard_reset_alpha", args=(True,))
    assert result.is_refused and "Hard reset again" in result.reason
    assert ("estop", "/dev/ttyUSB0") in log and ("close", "/dev/ttyUSB0") in log
    assert not old.is_open, "stopped and its port let go"
    assert panel.run("hard_reset_alpha", args=(True,)).is_ok
    assert panel.controller._model("Alpha") is not old


def test_hard_reset_of_a_row_that_did_not_launch_is_refused(panel):
    _launch_alpha_on(panel)
    result = panel.run("hard_reset_beta", args=(True,))
    assert result.is_refused and "not launched" in result.reason


def test_a_hard_reset_that_did_not_come_back_says_so_and_can_be_pressed_again(
        panel, fake_types):
    _launch_alpha_on(panel)
    fake_types["Alpha"].FAIL_ON_OPEN = True
    result = panel.run("hard_reset_alpha", args=(True,))
    assert result.is_refused and "did not come back" in result.reason
    assert "Alpha" not in panel.controller.model_names
    fake_types["Alpha"].FAIL_ON_OPEN = False
    assert panel.run("hard_reset_alpha", args=(True,)).is_ok
    assert "Alpha" in panel.controller.model_names


# -- a changed port applies through Hard reset (owner ruling 2026-10-08) -----
# The no-relaunch rule only keeps devices from being enabled or disabled
# outside Settings; it never meant a device could not be restarted. A row's
# Port (or Gamepad) changed after the launch is applied by that row's Hard
# reset: the stop path first, the old port closed, the model built and opened
# on the new port, re-identified. No station Restart.

def _record_order(monkeypatch, model_class, log):
    """Every construction, open, estop and close of `model_class`, in order,
    with the port it was on."""
    init, open_, estop, close = (model_class.__init__, model_class.open,
                                 model_class.estop, model_class.close)

    def rec_init(self, port=None, gamepad=None, sim=False):
        log.append(("build", port, gamepad))
        init(self, port=port, gamepad=gamepad, sim=sim)

    def rec(name, original):
        def method(self):
            log.append((name, self.port))
            return original(self)
        return method
    monkeypatch.setattr(model_class, "__init__", rec_init)
    monkeypatch.setattr(model_class, "open", rec("open", open_))
    monkeypatch.setattr(model_class, "estop", rec("estop", estop))
    monkeypatch.setattr(model_class, "close", rec("close", close))


def test_a_port_changed_after_the_launch_waits_for_hard_reset(panel):
    old = _launch_alpha_on(panel)
    offer(panel, "/dev/ttyUSB1")
    panel._found["/dev/ttyUSB1"] = "Alpha"
    select(panel, "alpha", "port", "/dev/ttyUSB1")
    assert panel.controller._model("Alpha") is old and old.closed == 0, \
        "a dropdown never restarts a device by itself"
    row = next(r for r in panel.state["rows"] if r["key"] == "alpha")
    assert row["pending_reset"] is True
    assert "hard reset to apply" in panel.alpha_status.lower()
    # A row whose choice matches what runs has nothing pending.
    beta = next(r for r in panel.state["rows"] if r["key"] == "beta")
    assert beta["pending_reset"] is False


def test_a_changed_port_that_has_not_answered_says_so_and_is_not_applied(panel):
    old = _launch_alpha_on(panel)
    offer(panel, "/dev/ttyUSB1")            # listed, never identified
    select(panel, "alpha", "port", "/dev/ttyUSB1")
    assert panel.alpha_status == "changed: not identified there; press Refresh"
    result = panel.run("hard_reset_alpha", args=(True,))
    assert result.is_refused and "Refresh" in result.reason
    assert panel.controller._model("Alpha") is old and old.closed == 0


def test_hard_reset_applies_a_changed_port_stop_path_first(
        panel, fake_types, monkeypatch):
    log = []
    _launch_alpha_on(panel)
    _record_order(monkeypatch, fake_types["Alpha"], log)
    offer(panel, "/dev/ttyUSB1")
    panel._found["/dev/ttyUSB1"] = "Alpha"
    select(panel, "alpha", "port", "/dev/ttyUSB1")
    asked = panel.run("hard_reset_alpha")
    assert asked.needs_confirm
    assert "/dev/ttyUSB0" in asked.reason and "/dev/ttyUSB1" in asked.reason
    assert log == [], "nothing was touched by the question"
    assert panel.run("hard_reset_alpha", args=(True,)).is_ok
    # The stop path ran on the old port, then the old port closed, and only
    # then was the model built and opened on the new one.
    # 2026-10-08 (A): the reset's own estop runs while the model is still
    # registered (a FULL STOP still reaches it), then `remove` estops again
    # and closes.
    assert log == [("estop", "/dev/ttyUSB0"), ("estop", "/dev/ttyUSB0"),
                   ("close", "/dev/ttyUSB0"),
                   ("build", "/dev/ttyUSB1", None), ("open", "/dev/ttyUSB1")]
    assert panel.controller.config("Alpha")["port"] == "/dev/ttyUSB1"
    assert panel.controller._model("Alpha").port == "/dev/ttyUSB1"
    assert panel.is_launched, "no station Restart"
    row = next(r for r in panel.state["rows"] if r["key"] == "alpha")
    assert row["pending_reset"] is False
    assert panel.alpha_status == "detected: Alpha"


def test_hard_reset_applies_a_changed_gamepad(panel, monkeypatch):
    _launch_alpha_on(panel)
    monkeypatch.setattr(panel, "_gamepads", ["None", "Pad 1"])
    select(panel, "alpha", "gamepad", "Pad 1")
    assert next(r for r in panel.state["rows"]
                if r["key"] == "alpha")["pending_reset"] is True
    assert panel.run("hard_reset_alpha", args=(True,)).is_ok
    assert panel.controller.config("Alpha")["gamepad"] == "Pad 1"
    assert panel.controller._model("Alpha").gamepad == "Pad 1"


def test_hard_reset_onto_a_port_that_answered_as_another_model_is_refused(panel):
    old = _launch_alpha_on(panel)
    offer(panel, "/dev/ttyUSB1")
    panel._found["/dev/ttyUSB1"] = "Beta"
    select(panel, "alpha", "port", "/dev/ttyUSB1")
    result = panel.run("hard_reset_alpha", args=(True,))
    assert result.is_refused and "answered as Beta" in result.reason
    assert panel.controller._model("Alpha") is old and old.closed == 0


def test_hard_reset_onto_a_port_another_running_model_holds_is_refused(panel):
    offer(panel, "/dev/ttyUSB0")
    offer(panel, "/dev/ttyUSB1")
    panel._found.update({"/dev/ttyUSB0": "Alpha", "/dev/ttyUSB1": "Beta"})
    select(panel, "alpha", "port", "/dev/ttyUSB0")
    select(panel, "beta", "port", "/dev/ttyUSB1")
    assert panel.run("launch").is_ok
    old = panel.controller._model("Alpha")
    panel._found["/dev/ttyUSB1"] = None     # say the handshake missed it
    select(panel, "alpha", "port", "/dev/ttyUSB1")
    result = panel.run("hard_reset_alpha", args=(True,))
    assert result.is_refused and "already assigned to Beta" in result.reason
    assert panel.controller._model("Alpha") is old and old.closed == 0


def test_hard_reset_to_a_new_port_that_fails_can_be_pressed_again(
        panel, fake_types):
    _launch_alpha_on(panel)
    offer(panel, "/dev/ttyUSB1")
    panel._found["/dev/ttyUSB1"] = "Alpha"
    select(panel, "alpha", "port", "/dev/ttyUSB1")
    fake_types["Alpha"].FAIL_ON_OPEN = True
    result = panel.run("hard_reset_alpha", args=(True,))
    assert result.is_refused and "/dev/ttyUSB1" in result.reason
    assert "Alpha" not in panel.controller.model_names
    fake_types["Alpha"].FAIL_ON_OPEN = False
    assert panel.run("hard_reset_alpha", args=(True,)).is_ok
    assert panel.controller._model("Alpha").port == "/dev/ttyUSB1"


def test_a_port_chosen_before_the_launch_is_not_pending(panel):
    """Pre-launch behaviour is unchanged: a choice is what Launch builds."""
    offer(panel, "/dev/ttyUSB0")
    panel._found["/dev/ttyUSB0"] = "Alpha"
    select(panel, "alpha", "port", "/dev/ttyUSB0")
    row = next(r for r in panel.state["rows"] if r["key"] == "alpha")
    assert row["pending_reset"] is False
    assert panel.alpha_status == "detected: Alpha"
    assert panel.run("hard_reset_alpha", args=(True,)).is_refused


def _scan_with(panel, monkeypatch, answers):
    asked = []
    monkeypatch.setattr(serial_port_module, "list_ports",
                        lambda: list(answers), raising=False)

    def identify(self, port, should_abort=None):
        asked.append(port)
        return answers[port]

    monkeypatch.setattr(Setup, "identify", identify)
    panel.scan()
    panel._scan_thread.join(5)
    assert not panel.is_scanning
    return asked


def test_a_scan_after_the_launch_never_opens_a_port_a_model_holds(panel, monkeypatch):
    """Opening it again would reset the board under the running model."""
    _launch_alpha_on(panel)
    asked = _scan_with(panel, monkeypatch, {"/dev/ttyUSB0": "Alpha",
                                            "/dev/ttyUSB1": None})
    assert asked == ["/dev/ttyUSB1"]
    assert panel._found["/dev/ttyUSB0"] == "Alpha"
    assert panel.alpha_port == "/dev/ttyUSB0" and panel.alpha_enabled is True


# -- a device started after the launch, from Settings (owner ruling 2026-10-08, B) --
# "A device plugged in after launch should be able to have a port set in
# settings and be started after startup, just not dynamically from outside
# settings like before." A row that is not running gets a Port and its key
# reads Start (`start_<row>`, also what the row's key runs while the row is
# not running): identified first, never on a port a running model holds.

def test_a_board_plugged_in_after_the_launch_is_seen_and_offered_start_not_started(
        panel, monkeypatch, warnings):
    _launch_alpha_on(panel)
    answers = {"/dev/ttyUSB0": "Alpha", "/dev/ttyUSB1": "Beta"}
    _scan_with(panel, monkeypatch, answers)
    assert "Beta" not in panel.controller.model_names, "never started by itself"
    assert panel.beta_port == "/dev/ttyUSB1", "its row is pointed at it"
    assert panel.beta_status == "seen on /dev/ttyUSB1: press Start"
    row = next(r for r in panel.state["rows"] if r["key"] == "beta")
    assert row["seen_after_launch"] == "/dev/ttyUSB1" and row["can_start"] is True
    alpha = next(r for r in panel.state["rows"] if r["key"] == "alpha")
    assert alpha["can_start"] is False
    said = [e for e in warnings if e.title == events.RESTART_NEEDED]
    assert not said, "no Restart: it is started from Settings"
    [seen] = [e for e in warnings if e.title == "Device Not Started"]
    assert "Beta on /dev/ttyUSB1" in seen.message and "Start" in seen.message
    assert not seen.action, "nothing outside Settings starts it"
    _scan_with(panel, monkeypatch, answers)       # seen again: said once
    assert len([e for e in warnings if e.title == "Device Not Started"]) == 1


def test_a_device_plugged_in_after_the_launch_starts_from_its_row(panel, monkeypatch):
    _launch_alpha_on(panel)
    heard = []
    panel.controller.subscribe(lambda event, name: heard.append((event, name)))
    _scan_with(panel, monkeypatch, {"/dev/ttyUSB0": "Alpha", "/dev/ttyUSB1": "Beta"})
    alpha = panel.controller._model("Alpha")
    result = panel.run("start_beta")
    assert result.is_ok, result.reason
    beta = panel.controller._model("Beta")
    assert beta.is_open and beta.port == "/dev/ttyUSB1"
    assert panel.controller.config("Beta")["port"] == "/dev/ttyUSB1"
    assert ("added", "Beta") in heard, "the rail and the Controller are told"
    assert panel.controller._model("Alpha") is alpha, "nothing else restarts"
    row = next(r for r in panel.state["rows"] if r["key"] == "beta")
    assert row["can_start"] is False and row["seen_after_launch"] is None
    assert panel.beta_status == "detected: Beta"
    # Running now: its key is Hard reset again, and Start is refused.
    assert panel.run("start_beta").is_refused
    assert panel.run("hard_reset_beta").needs_confirm


def test_the_rows_key_starts_a_row_that_is_not_running(panel, monkeypatch):
    """The Settings row has one key: Start while the row is not running."""
    _launch_alpha_on(panel)
    offer(panel, "/dev/ttyUSB1")
    panel._found["/dev/ttyUSB1"] = "Beta"
    select(panel, "beta", "port", "/dev/ttyUSB1")
    assert panel.beta_status == "not running: press Start"
    assert panel.run("hard_reset_beta").is_ok
    assert "Beta" in panel.controller.model_names


def test_start_after_launch_refuses_a_port_that_did_not_answer_as_the_device(panel):
    _launch_alpha_on(panel)
    offer(panel, "/dev/ttyUSB1")                 # listed, never identified
    select(panel, "beta", "port", "/dev/ttyUSB1")
    result = panel.run("start_beta")
    assert result.is_refused and "not answered" in result.reason
    panel._found["/dev/ttyUSB1"] = "Alpha"       # answered as something else
    result = panel.run("start_beta")
    assert result.is_refused and "answered as Alpha" in result.reason
    assert "Beta" not in panel.controller.model_names


def test_start_after_launch_on_sim_needs_no_answer(panel):
    _launch_alpha_on(panel)
    select(panel, "beta", "port", SIM)
    assert panel.run("start_beta").is_ok
    assert panel.controller._model("Beta").sim is True


def test_start_after_launch_refuses_a_port_a_running_model_holds(panel):
    old = _launch_alpha_on(panel)
    panel._found["/dev/ttyUSB0"] = "Beta"        # say it answered as Beta
    select(panel, "beta", "port", "/dev/ttyUSB0")
    result = panel.run("start_beta")
    assert result.is_refused and "already assigned to Alpha" in result.reason
    assert "Beta" not in panel.controller.model_names
    assert panel.controller._model("Alpha") is old and old.closed == 0


def test_start_is_refused_before_the_launch_and_while_a_scan_runs(panel):
    assert panel.run("start_beta").is_refused, "before the launch, Launch starts it"
    _launch_alpha_on(panel)
    assert "Beta" not in panel.controller.model_names
    select(panel, "beta", "port", SIM)

    class Alive:
        def is_alive(self):
            return True
    panel._scan_thread = Alive()
    with pytest.raises(Refused, match="scan"):
        panel.start_beta()
    panel._scan_thread = None


def test_a_start_that_fails_says_so_and_can_be_pressed_again(panel, fake_types):
    _launch_alpha_on(panel)
    offer(panel, "/dev/ttyUSB1")
    panel._found["/dev/ttyUSB1"] = "Beta"
    select(panel, "beta", "port", "/dev/ttyUSB1")
    fake_types["Beta"].FAIL_ON_OPEN = True
    result = panel.run("start_beta")
    assert result.is_refused and "did not start" in result.reason
    assert "Beta" not in panel.controller.model_names
    fake_types["Beta"].FAIL_ON_OPEN = False
    assert panel.run("start_beta").is_ok


def test_no_command_outside_setup_adds_a_device():
    """Only Setup builds a new model (a device never launched): the Web
    server and the views have no route or call that does."""
    import inspect
    from views import base
    from views.web import server
    for module in (server, base):
        source = inspect.getsource(module)
        assert "controller.add(" not in source and "model_from_config" not in source


# -- the update check (owner, 2026-09-28) ------------------------------------

class FakeUpdater:
    """`controller.updater.Updater`'s two calls, scripted. `gate`, when set,
    holds `check()` until the test releases it."""

    def __init__(self, check=None, apply=None, version="abc1234, 2026-09-28"):
        self.check_result = check or {
            "status": "up_to_date", "branch": "main", "head": "abc1234",
            "remote": "abc1234", "behind": 0, "ahead": 0, "log": [], "reason": ""}
        self.apply_result = apply or {
            "updated": True, "old": "abc1234", "new": "def5678",
            "deps_changed": False, "deps_ok": True, "firmware_changed": False,
            "reason": "Updated main: abc1234 to def5678."}
        self._version = version
        self.checks = self.applies = 0
        self.gate = None

    def version(self):
        return self._version

    def check(self, timeout=10.0):
        self.checks += 1
        if self.gate is not None:
            assert self.gate.wait(5.0)
        return dict(self.check_result)

    def apply(self, timeout=10.0):
        self.applies += 1
        return dict(self.apply_result)


def behind(n, reason=""):
    return {"status": "behind", "branch": "main", "head": "abc1234",
            "remote": "def5678", "behind": n, "ahead": 0,
            "log": [f"{i:07x} commit {i}" for i in range(min(n, 8))],
            "reason": reason}


def wait_idle(panel):
    for thread in (panel._update_thread, panel._apply_thread):
        if thread is not None:
            thread.join(5.0)
            assert not thread.is_alive()


@pytest.fixture
def checking(monkeypatch):
    """The startup check switched back on for this test only (conftest turns
    it off for every test, so nothing ever fetches)."""
    monkeypatch.delenv("STATION_NO_UPDATE_CHECK", raising=False)


def test_the_update_section_follows_the_profile_row_and_is_a_tier_one_row(panel):
    """Update leads the upkeep, right after Launch (UX audit 2026-10-08:
    the drawer's job first; the account section, first until then, is
    last)."""
    titles = [s["title"] for s in panel.schema["sections"]]
    assert titles[-1] == Setup.ACCOUNT_SECTION
    section = panel.schema["sections"][titles.index("Launch") + 1]
    assert section["title"] == "Update"
    assert section["layout"] == "row" and section["tier"] == 1
    assert [(e["type"], e.get("text"), e.get("command") or e.get("model_attr"))
            for e in section["elements"]] == [
        ("readonly", "Station", "station_version"),
        ("readonly", "Updates", "update_status"),
        ("button", "Update now", "apply_update"),
        ("button", "Check again", "check_updates"),
        ("button", "Restart", "restart_station"),     # rb-restart R3
        ("readonly", "Coming", "update_log"),
    ]
    status = section["elements"][1]
    assert status["role"] == "info"
    update, again = section["elements"][2:4]
    # Outlined in the schema; the Web draws it ink only while an update is
    # ready (UX audit 2026-10-08: Launch is the drawer's one ink key).
    assert update["role"] == "neutral" and again["role"] == "neutral"
    assert update["confirm"].startswith("Update the station now?")


def test_no_test_ever_fetches_the_startup_check_is_off(fake_types):
    updater = FakeUpdater(check=behind(3))
    panel = Setup(RecordingController(), updater=updater)
    assert panel._update_thread is None
    assert updater.checks == 0
    assert panel.has_update is False
    assert panel.state["values"]["station_version"] == "unknown"


def test_the_startup_check_runs_on_a_thread_and_publishes(fake_types, checking):
    updater = FakeUpdater(check=behind(3))
    updater.gate = threading.Event()
    panel = Setup(RecordingController(), updater=updater)
    assert panel.state["values"]["update_status"] == "Checking for updates…"
    assert panel._update_thread.daemon
    updater.gate.set()
    wait_idle(panel)
    values = panel.state["values"]
    assert values["station_version"] == "abc1234, 2026-09-28"
    assert values["update_status"] == ("3 new commits are ready. Update now, "
                                       "then restart the station.")
    assert values["update_log"] == "\n".join(behind(3)["log"])
    assert panel.has_update is True
    assert panel.state["update"]["has_update"] is True
    assert panel.state["update"]["log"] == behind(3)["log"]


def test_one_new_commit_is_said_in_the_singular(fake_types, checking):
    panel = Setup(RecordingController(), updater=FakeUpdater(check=behind(1)))
    wait_idle(panel)
    assert panel.update_status == ("1 new commit is ready. Update now, then "
                                   "restart the station.")


@pytest.mark.parametrize("status,sentence", [
    ("up_to_date", "Up to date."),
    ("offline", "Could not reach GitHub; the station runs as it is."),
    ("dirty", "This checkout has local edits; update by hand."),
    ("not_git", "Not a git checkout."),
])
def test_each_outcome_is_one_sentence(fake_types, checking, status, sentence):
    result = dict(FakeUpdater().check_result, status=status)
    panel = Setup(RecordingController(), updater=FakeUpdater(check=result))
    wait_idle(panel)
    assert panel.update_status == sentence
    assert panel.has_update is False
    assert panel.update_log == ""


def test_a_check_that_raises_warns_once_and_leaves_the_station_running(
        fake_types, checking, warnings):
    class Broken(FakeUpdater):
        def check(self, timeout=10.0):
            raise RuntimeError("git exploded")

    panel = Setup(RecordingController(), updater=Broken())
    wait_idle(panel)
    assert panel.has_update is False
    assert "runs as it is" in panel.update_status
    assert [e.title for e in warnings if e.severity == "warning"] == ["Update Check Failed"]


def test_check_again_reruns_the_check_and_refuses_while_one_runs(fake_types, checking):
    updater = FakeUpdater()
    updater.gate = threading.Event()
    panel = Setup(RecordingController(), updater=updater)
    result = panel.run("check_updates")
    assert result.status == "refused"
    assert "already" in result.reason
    updater.gate.set()
    wait_idle(panel)
    updater.check_result = behind(2)
    assert panel.run("check_updates").is_ok
    wait_idle(panel)
    assert updater.checks == 2
    assert panel.has_update is True


def test_check_again_works_when_the_startup_check_was_off(fake_types):
    updater = FakeUpdater(check=behind(2))
    panel = Setup(RecordingController(), updater=updater)
    assert panel.run("check_updates").is_ok
    wait_idle(panel)
    assert updater.checks == 1 and panel.has_update is True
    assert panel.station_version == "abc1234, 2026-09-28"


def _ready(checking_panel):
    wait_idle(checking_panel)
    assert checking_panel.has_update is True
    return checking_panel


def test_update_now_refuses_when_there_is_nothing_to_update(fake_types, checking):
    updater = FakeUpdater()
    panel = Setup(RecordingController(), updater=updater)
    wait_idle(panel)
    result = panel.run("apply_update", args=(True,))
    assert result.status == "refused"
    assert updater.applies == 0


def test_update_now_refuses_while_the_station_is_launched(fake_types, checking):
    updater = FakeUpdater(check=behind(2))
    panel = _ready(Setup(RecordingController(), updater=updater))
    connect(panel, "alpha")
    assert panel.run("launch").is_ok
    result = panel.run("apply_update", args=(True,))
    assert result.status == "refused"
    assert result.reason == ("Restart the station first: an update must not "
                             "land under running devices.")
    assert updater.applies == 0


def test_update_now_asks_first(fake_types, checking):
    updater = FakeUpdater(check=behind(2))
    panel = _ready(Setup(RecordingController(), updater=updater))
    result = panel.run("apply_update")
    assert result.status == "needs_confirm"
    assert result.reason == ("Update the station now? The station must be "
                             "restarted afterwards.")
    assert result.command == "apply_update"
    assert updater.applies == 0


def test_a_landed_update_says_restart_and_clears_has_update(
        fake_types, checking, warnings):
    updater = FakeUpdater(check=behind(2))
    panel = _ready(Setup(RecordingController(), updater=updater))
    assert panel.run("apply_update", args=(True,)).is_ok
    wait_idle(panel)
    assert updater.applies == 1
    assert panel.update_status == ("Updated to def5678. Quit and start the "
                                   "station again to run it.")
    assert panel.has_update is False
    assert panel.update_log == ""
    # The version shown is what is RUNNING, not what landed on disk.
    assert panel.station_version == "abc1234, 2026-09-28"
    assert "Station Updated" in [e.title for e in warnings if e.severity == "info"]
    # Checking again does not pretend the new code is running.
    updater.check_result = dict(FakeUpdater().check_result, head="def5678")
    assert panel.run("check_updates").is_ok
    wait_idle(panel)
    assert panel.update_status.startswith("Updated to def5678. Quit and start")


def test_a_refused_update_warns_once_with_the_reason(fake_types, checking, warnings):
    updater = FakeUpdater(check=behind(2), apply={
        "updated": False, "old": "abc1234", "new": "abc1234",
        "deps_changed": False, "deps_ok": True, "firmware_changed": False,
        "reason": "This checkout has local edits; update by hand."})
    panel = _ready(Setup(RecordingController(), updater=updater))
    assert panel.run("apply_update", args=(True,)).is_ok
    wait_idle(panel)
    # rb-restart: the Update Ready prompt that led here is not this warning.
    warned = [e for e in warnings if e.severity == "warning"
              and e.title != events.UPDATE_READY]
    assert [e.title for e in warned] == ["Update Not Applied"]
    assert "local edits" in warned[0].message
    assert panel.update_status == "This checkout has local edits; update by hand."


def test_a_failed_reinstall_after_the_update_is_a_warning(fake_types, checking, warnings):
    updater = FakeUpdater(check=behind(2), apply={
        "updated": True, "old": "abc1234", "new": "def5678",
        "deps_changed": True, "deps_ok": False, "firmware_changed": False,
        "reason": "Updated main: abc1234 to def5678. The dependencies changed "
                  "and pip install failed (no network)."})
    panel = _ready(Setup(RecordingController(), updater=updater))
    assert panel.run("apply_update", args=(True,)).is_ok
    wait_idle(panel)
    prompts = (events.UPDATE_READY, events.RESTART_NEEDED)
    assert [e.title for e in warnings if e.severity == "warning"
            and e.title not in prompts] == ["Reinstall Failed"]
    # rb-restart: a restart into missing dependencies would not come back,
    # so the Restart Needed dialog says what to do and offers no Restart now.
    [restart] = [e for e in warnings if e.title == events.RESTART_NEEDED]
    assert restart.action is None and "pip install" in restart.message
    assert panel.has_update is False
    assert "pip install failed" in panel.update_status


def test_a_landed_firmware_change_points_at_the_firmware_row(fake_types, checking):
    updater = FakeUpdater(check=behind(2), apply=dict(
        FakeUpdater().apply_result, firmware_changed=True))
    panel = _ready(Setup(RecordingController(), updater=updater))
    assert panel.run("apply_update", args=(True,)).is_ok
    wait_idle(panel)
    assert panel.update_status.endswith(
        "The firmware changed: after the restart, the Firmware row flashes "
        "the boards that are out of date.")


def test_launch_waits_while_an_update_is_landing(fake_types, checking):
    updater = FakeUpdater(check=behind(2))
    panel = _ready(Setup(RecordingController(), updater=updater))
    gate = threading.Event()
    real_apply = updater.apply

    def slow_apply(timeout=10.0):
        assert gate.wait(5.0)
        return real_apply(timeout)

    updater.apply = slow_apply
    assert panel.run("apply_update", args=(True,)).is_ok
    connect(panel, "alpha")
    result = panel.run("launch")
    assert result.status == "refused"
    assert "update" in result.reason.lower()
    again = panel.run("apply_update", args=(True,))
    assert again.status == "refused"
    gate.set()
    wait_idle(panel)


def test_launch_is_refused_after_an_update_until_the_restart(fake_types, checking):
    """The lead's rule: files on disk newer than the running code never
    launch; only a restart runs the update."""
    panel = _ready(Setup(RecordingController(), updater=FakeUpdater(check=behind(2))))
    assert panel.run("apply_update", args=(True,)).is_ok
    wait_idle(panel)
    assert panel.update_status.startswith("Updated to")
    connect(panel, "alpha")
    result = panel.run("launch")
    assert result.status == "refused"
    assert "start it again" in result.reason



# -- the firmware check (owner, 2026-09-28: the old launcher's, on the Setup page) --

class FakeFirmware:
    """`controller.firmware.FirmwareCheck`'s two calls, scripted. `gate`
    holds `check()`, `flash_gate` holds `flash()`, until the test releases
    them. `after_flash` is what `check()` answers once a flash has run."""

    def __init__(self, result=None, after_flash=None, flash_ok=True,
                 lines=("Sketches: firmware", "  Stepper Probe ok")):
        self.result = result or firmware_result(stale=["Stepper Probe"])
        self.after_flash = after_flash
        self.flash_ok, self.lines = flash_ok, list(lines)
        self.checks, self.flashes = 0, []
        self.gate = self.flash_gate = None
        self.progress_seen = []

    def check(self):
        self.checks += 1
        if self.gate is not None:
            assert self.gate.wait(5.0)
        if self.flashes and self.after_flash is not None:
            return dict(self.after_flash)
        return dict(self.result)

    def flash(self, boards, on_line=None, timeout=None):
        self.flashes.append(list(boards))
        for line in self.lines:
            on_line(line)
        if self.flash_gate is not None:
            assert self.flash_gate.wait(5.0)
        return {"ok": self.flash_ok, "returncode": 0 if self.flash_ok else 1,
                "last": self.lines[-1].strip() if self.lines else "",
                "lines": list(self.lines)}


def firmware_result(stale=(), never=(), missing=()):
    boards = {b: "current" for b in ("Stepper Probe", "DC Probe",
                                     "Chuck Positioner", "Temperature Controller")}
    boards.update({b: "out_of_date" for b in stale})
    boards.update({b: "never_flashed" for b in never})
    parts = []
    if stale:
        parts.append(station_setup._and(stale) + " out of date")
    if never:
        parts.append(station_setup._and(never) + " never flashed here")
    return {"boards": boards, "stale": list(stale), "never": list(never),
            "to_flash": list(stale) + list(never), "missing_tools": list(missing),
            "summary": "; ".join(parts) or "all current"}


@pytest.fixture
def firmware_checking(monkeypatch):
    """The startup firmware check switched back on for this test only."""
    monkeypatch.delenv("STATION_NO_FIRMWARE_CHECK", raising=False)


@pytest.fixture
def board_types(monkeypatch):
    """Rows named like the boards, so Launch can match a row to a board."""
    types = {}
    for model_class in (make_model_class("Stepper Probe", "s"),
                        make_model_class("DC Probe", "d"),
                        make_model_class("Screen", needs_port=False)):
        types[model_class.NAME] = model_class
    monkeypatch.setattr(station_setup, "MODEL_TYPES", types)
    return types


def wait_firmware(panel):
    for thread in (panel._firmware_thread, panel._flash_thread):
        if thread is not None:
            thread.join(5.0)
            assert not thread.is_alive()


def checked(firmware):
    """A Setup whose startup check is off, then checked once by hand."""
    panel = Setup(RecordingController(), firmware=firmware)
    assert panel.check_firmware()
    wait_firmware(panel)
    return panel


def on_port(panel, key, port):
    """The row's board answered on `port` (a row launches only then)."""
    offer(panel, port)
    panel._found[port] = panel._rows[key]["name"]
    select(panel, key, "port", port)


def asked(call):
    with pytest.raises(NeedsConfirm) as raised:
        call()
    return raised.value


def refused(call):
    with pytest.raises(Refused) as raised:
        call()
    return raised.value.reason


def test_the_firmware_row_block_builds_the_brief_shape(panel):
    """What `_firmware_section()` builds; the schema inserts it right after
    Update (see the handoff: the insertion waits on a file outside this
    write set)."""
    section = panel._firmware_section()
    assert section["title"] == "Firmware"
    assert section["layout"] == "row" and section["tier"] == 1
    assert [(e["type"], e.get("text"), e.get("command") or e.get("model_attr"))
            for e in section["elements"]] == [
        ("readonly", "Boards", "firmware_status"),
        ("readonly", "Flashing", "firmware_progress"),
        ("button", "Flash out-of-date boards", "flash_firmware"),
        ("button", "Check firmware", "check_firmware"),
    ]
    boards, _, flash, again = section["elements"]
    assert boards["role"] == "info"
    # Outlined (UX audit 2026-10-08): Launch is the drawer's one ink key.
    assert flash["role"] == "neutral" and again["role"] == "neutral"
    assert flash["confirm"].startswith("Flash the out-of-date boards?")


def test_the_firmware_row_is_second_right_after_update(panel):
    sections = panel.schema["sections"]
    titles = [s["title"] for s in sections]
    at = titles.index("Update")
    assert titles[at - 1:at + 2] == ["Launch", "Update", "Firmware"]
    assert sections[at + 1] == panel._firmware_section()


def test_the_firmware_row_reaches_the_views_through_run_and_state(fake_types):
    firmware = FakeFirmware()
    panel = Setup(RecordingController(), firmware=firmware)
    assert panel.run("check_firmware").is_ok
    wait_firmware(panel)
    values = panel.state["values"]
    assert values["firmware_status"] == "Stepper Probe out of date"
    assert values["firmware_progress"] == ""
    result = panel.run("flash_firmware")
    assert result.status == "needs_confirm" and result.command == "flash_firmware"
    assert panel.run("flash_firmware", args=(True,)).is_ok
    wait_firmware(panel)
    assert firmware.flashes == [["Stepper Probe"]]


def test_no_test_ever_checks_firmware_the_startup_check_is_off(fake_types):
    firmware = FakeFirmware()
    panel = Setup(RecordingController(), firmware=firmware)
    assert panel._firmware_thread is None and firmware.checks == 0
    assert panel.firmware_status == Setup.FIRMWARE_CHECK_OFF
    assert panel.firmware_progress == ""


def test_the_startup_firmware_check_runs_on_a_thread_and_publishes(
        fake_types, firmware_checking):
    firmware = FakeFirmware()
    firmware.gate = threading.Event()
    panel = Setup(RecordingController(), firmware=firmware)
    assert panel.firmware_status == "checking…"
    assert panel._firmware_thread.daemon
    firmware.gate.set()
    wait_firmware(panel)
    assert panel.firmware_status == "Stepper Probe out of date"
    assert firmware.checks == 1 and firmware.flashes == []


# -- the startup check offers the flash (owner 2026-09-28: "Flash should be
# unattended, as with the prev. script. It can send a popup first") ---------

FLASH_NOW = {"label": "Flash now", "name": "__setup__", "command": "flash_firmware",
             "args": [True]}


def test_the_startup_check_asks_once_and_flash_now_flashes_unattended(
        fake_types, firmware_checking, warnings):
    firmware = FakeFirmware()
    panel = _listening(Setup(RecordingController(), firmware=firmware))
    [offer] = _prompts(warnings, events.FIRMWARE_OUT_OF_DATE)
    assert offer.severity == "warning" and offer.needs_ack is True
    assert offer.message == ("Stepper Probe out of date. Flash it now? This "
                             "overwrites its running firmware if it is plugged "
                             "in; otherwise it is skipped. Launch waits until "
                             "the flash finishes.")
    assert offer.to_dict()["action"] == FLASH_NOW
    assert firmware.flashes == []            # the dialog is the question
    # Flash now: the action runs as confirmed, on the flash thread.
    assert panel.run("flash_firmware", args=(True,)).is_ok
    wait_firmware(panel)
    assert firmware.flashes == [["Stepper Probe"]]
    # The recheck after the flash still says out of date (the fake keeps its
    # answer): no second question this run.
    assert panel.firmware_status == "Stepper Probe out of date"
    assert len(_prompts(warnings, events.FIRMWARE_OUT_OF_DATE)) == 1


def test_the_startup_offer_names_every_board_the_button_would_flash(
        fake_types, firmware_checking, warnings):
    firmware = FakeFirmware(result=firmware_result(stale=["Stepper Probe", "DC Probe"],
                                                   never=["Chuck Positioner"]))
    panel = _listening(Setup(RecordingController(), firmware=firmware))
    [offer] = _prompts(warnings, events.FIRMWARE_OUT_OF_DATE)
    assert offer.message.startswith(
        "Stepper Probe and DC Probe out of date; Chuck Positioner never flashed "
        "here. Flash them now? This overwrites the running firmware of every "
        "one of them that is plugged in; the others are skipped.")
    assert panel.run("flash_firmware", args=(True,)).is_ok
    wait_firmware(panel)
    assert firmware.flashes == [["Stepper Probe", "DC Probe", "Chuck Positioner"]]


def _listening(panel):
    """What `app.launch` does once the view has subscribed (A2)."""
    wait_firmware(panel)
    starting = getattr(panel, "startup_checks", None)
    if starting is not None:
        starting()
    return panel


def test_a_view_that_subscribes_after_setup_is_built_still_gets_the_offer(
        fake_types, firmware_checking):
    """OP-4: the startup check finished before any view had subscribed, so
    its Firmware Out of Date dialog went to nobody and Flash now was
    unreachable. The offer waits for `startup_checks()`, which the app
    calls once the view listens."""
    events.clear()
    firmware = FakeFirmware()
    panel = Setup(RecordingController(), firmware=firmware)
    wait_firmware(panel)                 # the check is done; no view yet
    seen = []
    events.subscribe(seen.append)        # the view subscribes
    try:
        _listening(panel)
        [offer] = _prompts(seen, events.FIRMWARE_OUT_OF_DATE)
        assert offer.needs_ack and offer.to_dict()["action"] == FLASH_NOW
    finally:
        events.unsubscribe(seen.append)
        events.clear()


def test_the_offer_waits_for_a_check_still_running_when_the_view_listens(
        fake_types, firmware_checking, warnings):
    firmware = FakeFirmware()
    firmware.gate = threading.Event()
    panel = Setup(RecordingController(), firmware=firmware)
    panel.startup_checks()               # the view listens before the answer
    assert _prompts(warnings, events.FIRMWARE_OUT_OF_DATE) == []
    firmware.gate.set()
    wait_firmware(panel)
    assert len(_prompts(warnings, events.FIRMWARE_OUT_OF_DATE)) == 1
    panel.startup_checks()               # a second call offers nothing new
    assert len(_prompts(warnings, events.FIRMWARE_OUT_OF_DATE)) == 1


def test_nothing_is_offered_before_the_view_listens(
        fake_types, firmware_checking, warnings):
    panel = Setup(RecordingController(), firmware=FakeFirmware())
    wait_firmware(panel)
    assert panel.firmware_status == "Stepper Probe out of date"
    assert _prompts(warnings, events.FIRMWARE_OUT_OF_DATE) == []


def test_on_next_read_the_offer_goes_out_with_the_first_state_read(
        fake_types, firmware_checking, warnings):
    """The Web: the page replays older events as history (no dialog), so
    the offer goes out when the page first reads Setup, after it has
    taken its place in the event stream."""
    panel = Setup(RecordingController(), firmware=FakeFirmware())
    wait_firmware(panel)
    panel.startup_checks(on_next_read=True)
    assert _prompts(warnings, events.FIRMWARE_OUT_OF_DATE) == []
    panel.state
    assert len(_prompts(warnings, events.FIRMWARE_OUT_OF_DATE)) == 1
    panel.state
    assert len(_prompts(warnings, events.FIRMWARE_OUT_OF_DATE)) == 1


def test_the_startup_check_asks_nothing_when_every_board_is_current(
        fake_types, firmware_checking, warnings):
    panel = _listening(Setup(RecordingController(),
                             firmware=FakeFirmware(result=firmware_result())))
    assert _prompts(warnings, events.FIRMWARE_OUT_OF_DATE) == []


def test_the_startup_check_asks_nothing_it_could_not_do_without_the_tools(
        fake_types, firmware_checking, warnings):
    firmware = FakeFirmware(result=firmware_result(stale=["DC Probe"],
                                                   missing=["arduino-cli"]))
    panel = _listening(Setup(RecordingController(), firmware=firmware))
    assert _prompts(warnings, events.FIRMWARE_OUT_OF_DATE) == []
    assert "by hand" in refused(lambda: panel.flash_firmware(True))


def test_check_firmware_by_hand_asks_nothing_the_button_is_beside_it(
        fake_types, warnings):
    firmware = FakeFirmware()
    panel = checked(firmware)
    assert panel.firmware_status == "Stepper Probe out of date"
    assert _prompts(warnings, events.FIRMWARE_OUT_OF_DATE) == []
    assert firmware.flashes == []


def test_a_firmware_check_that_raises_warns_and_the_station_runs(
        fake_types, firmware_checking, warnings):
    class Broken(FakeFirmware):
        def check(self):
            raise RuntimeError("disk gone")

    panel = Setup(RecordingController(), firmware=Broken())
    wait_firmware(panel)
    assert "check failed" in panel.firmware_status
    assert [e.title for e in warnings if e.severity == "warning"] == ["Firmware Check Failed"]


def test_check_firmware_refuses_while_a_check_runs(fake_types):
    firmware = FakeFirmware()
    firmware.gate = threading.Event()
    panel = Setup(RecordingController(), firmware=firmware)
    assert panel.check_firmware()
    assert "already running" in refused(panel.check_firmware)
    firmware.gate.set()
    wait_firmware(panel)
    assert firmware.checks == 1


def test_flash_asks_first_and_names_the_boards(fake_types):
    firmware = FakeFirmware(result=firmware_result(stale=["Stepper Probe"],
                                                   never=["Chuck Positioner"]))
    panel = checked(firmware)
    question = asked(panel.flash_firmware)
    assert question.command == "flash_firmware"
    assert question.prompt.startswith(
        "Flash Stepper Probe and Chuck Positioner now? This overwrites")
    assert firmware.flashes == []


def test_flash_refuses_when_nothing_is_out_of_date(fake_types):
    firmware = FakeFirmware(result=firmware_result())
    panel = checked(firmware)
    assert refused(lambda: panel.flash_firmware(True)) == (
        "Every board is current; there is nothing to flash.")
    assert firmware.flashes == []


def test_flash_refuses_before_any_check(fake_types):
    firmware = FakeFirmware()
    panel = Setup(RecordingController(), firmware=firmware)
    assert "Check firmware" in refused(lambda: panel.flash_firmware(True))
    assert firmware.flashes == []


def test_flash_refuses_without_the_tools_and_says_flash_by_hand(fake_types):
    firmware = FakeFirmware(result=firmware_result(stale=["DC Probe"],
                                                   missing=["arduino-cli"]))
    panel = checked(firmware)
    reason = refused(lambda: panel.flash_firmware(True))
    assert reason.startswith("arduino-cli is not installed") and "by hand" in reason
    assert firmware.flashes == []


def test_flash_refuses_while_the_station_is_launched(fake_types):
    firmware = FakeFirmware()
    panel = checked(firmware)
    connect(panel, "alpha")
    assert panel.run("launch").is_ok
    assert refused(lambda: panel.flash_firmware(True)).startswith("Restart the station first")
    assert firmware.flashes == []


def test_flash_refuses_while_the_scan_holds_the_ports(fake_types, monkeypatch):
    firmware = FakeFirmware()
    panel = checked(firmware)
    monkeypatch.setattr(Setup, "is_scanning", property(lambda self: True))
    assert "scan" in refused(lambda: panel.flash_firmware(True))
    assert firmware.flashes == []


def test_a_flash_streams_its_lines_then_rechecks(fake_types, warnings):
    firmware = FakeFirmware(after_flash=firmware_result())
    firmware.flash_gate = threading.Event()
    panel = checked(firmware)
    assert panel.flash_firmware(True)
    deadline = time.monotonic() + 5
    while panel.firmware_progress != "Stepper Probe ok" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert panel.firmware_progress == "Stepper Probe ok"
    assert panel.firmware_status == "flashing Stepper Probe"
    assert panel.is_flashing
    # While it runs: no second flash, no check, no launch, no scan.
    assert "already running" in refused(lambda: panel.flash_firmware(True))
    assert "flash is running" in refused(panel.check_firmware)
    connect(panel, "alpha")
    result = panel.run("launch")
    assert result.status == "refused" and "being flashed" in result.reason
    assert "being flashed" in refused(panel.scan)
    firmware.flash_gate.set()
    wait_firmware(panel)
    assert panel.firmware_progress == ""
    assert panel.firmware_status == "all current"
    assert "Firmware Flashed" in [e.title for e in warnings if e.severity == "info"]
    assert panel.run("launch").is_ok


def test_a_failed_flash_warns_with_the_scripts_last_line(fake_types, warnings):
    firmware = FakeFirmware(flash_ok=False,
                            lines=["Scanning for connected boards...", "  Stepper Probe FAILED"])
    panel = checked(firmware)
    assert panel.flash_firmware(True)
    wait_firmware(panel)
    warned = [e for e in warnings if e.severity == "warning"]
    assert [e.title for e in warned] == ["Firmware Flash Failed"]
    assert "Stepper Probe FAILED" in warned[0].message
    assert panel.firmware_status == "the last flash failed; Stepper Probe out of date"
    assert panel.firmware_progress == ""


def test_a_board_that_was_not_plugged_in_is_said_after_the_flash(fake_types, warnings):
    firmware = FakeFirmware(
        result=firmware_result(never=["Stepper Probe", "DC Probe"]),
        after_flash=firmware_result(never=["DC Probe"]),
        lines=["Not connected (nothing flashed, nothing recorded): DC Probe",
               "Summary:", "  Stepper Probe ok"])
    panel = checked(firmware)
    assert panel.flash_firmware(True)
    wait_firmware(panel)
    titles = [(e.severity, e.title) for e in warnings]
    assert ("warning", "Firmware Not Flashed") in titles
    assert ("info", "Firmware Flashed") in titles
    note = next(e for e in warnings if e.title == "Firmware Not Flashed")
    assert note.message.startswith("DC Probe still needs flashing. Not connected")


def test_launch_warns_once_when_a_launched_board_is_out_of_date(board_types):
    panel = checked(FakeFirmware())
    on_port(panel, "stepper_probe", "/dev/ttyACM0")
    result = panel.run("launch")
    assert result.status == "needs_confirm" and result.command == "launch"
    assert result.reason == "Stepper Probe's firmware is out of date. Launch anyway?"
    assert panel.controller.model_names == []
    assert panel.run("launch", args=(True,)).is_ok


def test_launch_names_every_out_of_date_board_it_opens(board_types):
    panel = checked(FakeFirmware(result=firmware_result(
        stale=["Stepper Probe", "DC Probe", "Chuck Positioner"])))
    on_port(panel, "stepper_probe", "/dev/ttyACM0")
    on_port(panel, "dc_probe", "/dev/ttyACM1")
    result = panel.run("launch")
    assert result.reason == ("The firmware on Stepper Probe and DC Probe is out "
                             "of date. Launch anyway?")


def test_a_simulated_row_is_never_warned_about(board_types):
    panel = checked(FakeFirmware())
    connect(panel, "stepper_probe")          # SIM by default
    assert panel.run("launch").is_ok


def test_a_board_never_flashed_here_is_not_warned_about(board_types):
    panel = checked(FakeFirmware(result=firmware_result(never=["Stepper Probe"])))
    on_port(panel, "stepper_probe", "/dev/ttyACM0")
    assert panel.run("launch").is_ok


def test_launch_does_not_wait_on_an_unchecked_firmware(board_types):
    panel = Setup(RecordingController(), firmware=FakeFirmware())
    on_port(panel, "stepper_probe", "/dev/ttyACM0")
    assert panel.run("launch").is_ok


def test_a_launch_over_energized_models_is_refused_before_any_question(board_types):
    """Was: a relaunch over energized models asked one combined question.
    Owner 2026-10-07: no relaunch - a running station refuses Launch before
    the firmware question, and its energized model stays up."""
    panel = checked(FakeFirmware())
    connect(panel, "dc_probe")
    assert panel.run("launch").is_ok
    panel.controller._model_or_none("DC Probe").is_energized = True
    on_port(panel, "stepper_probe", "/dev/ttyACM0")
    result = panel.run("launch", args=(True,))
    assert result.is_refused and "launched" in result.reason
    assert "DC Probe" in panel.controller.model_names


def test_the_web_address_is_empty_until_the_web_view_serves(panel):
    assert panel.web_address == ""
    from views.web.server import WebView
    view = WebView(panel.controller, panel, port=0, open_browser=False)
    try:
        url = view.open()
        assert url.startswith("http://127.0.0.1:")
        assert panel.web_address == url
        assert panel.state["values"]["web_address"] == url
    finally:
        view.close()

# -- rb-restart R2/R3: the update prompts act ---------------------------------

def _prompts(seen, title):
    return [e for e in seen if e.title == title]


def test_a_ready_update_asks_once_per_remote_sha_with_update_now(
        fake_types, checking, warnings):
    updater = FakeUpdater(check=behind(3))
    panel = _ready(Setup(RecordingController(), updater=updater))
    [ready] = _prompts(warnings, events.UPDATE_READY)
    assert ready.severity == "warning" and ready.needs_ack is True
    assert ready.message == ("3 new commits are ready: commit 0. Update now, "
                             "then restart the station.")
    assert ready.to_dict()["action"] == {"label": "Update now", "name": "__setup__",
                                         "command": "apply_update", "args": []}
    # Check again, same remote: no second prompt (not even a folded repeat).
    assert panel.run("check_updates").is_ok
    wait_idle(panel)
    assert len(_prompts(warnings, events.UPDATE_READY)) == 1 and ready.count == 1
    # A newer remote asks again, in the singular when it is one commit.
    updater.check_result = dict(behind(1), remote="fed9876")
    assert panel.run("check_updates").is_ok
    wait_idle(panel)
    prompts = _prompts(warnings, events.UPDATE_READY)
    assert len(prompts) == 2
    assert prompts[1].message == ("1 new commit is ready: commit 0. Update now, "
                                  "then restart the station.")
    # The Setup line is unchanged by the prompt ("Later" leaves it as it is).
    assert panel.update_status == ("1 new commit is ready. Update now, then "
                                   "restart the station.")


def test_an_up_to_date_check_asks_nothing(fake_types, checking, warnings):
    panel = Setup(RecordingController(), updater=FakeUpdater())
    wait_idle(panel)
    assert not _prompts(warnings, events.UPDATE_READY)
    assert not _prompts(warnings, events.RESTART_NEEDED)


def test_a_landed_update_asks_to_restart_with_restart_now(
        fake_types, checking, warnings):
    panel = _ready(Setup(RecordingController(), updater=FakeUpdater(check=behind(2))))
    assert panel.run("apply_update", args=(True,)).is_ok
    wait_idle(panel)
    [prompt] = _prompts(warnings, events.RESTART_NEEDED)
    assert prompt.severity == "warning" and prompt.needs_ack is True
    assert prompt.message == "Updated to def5678. Restart the station to run it."
    # From the dialog the modal IS the question: the action says confirmed.
    assert prompt.to_dict()["action"] == {"label": "Restart now", "name": "__setup__",
                                          "command": "restart_station", "args": [True]}
    # "Later": the line and the Launch refusal stay as they were.
    assert panel.update_status == ("Updated to def5678. Quit and start the "
                                   "station again to run it.")
    connect(panel, "alpha")
    assert panel.run("launch").status == "refused"


def test_an_update_that_did_not_land_asks_nothing(fake_types, checking, warnings):
    updater = FakeUpdater(check=behind(2), apply={
        "updated": False, "reason": "This checkout has local edits; update by hand."})
    panel = _ready(Setup(RecordingController(), updater=updater))
    assert panel.run("apply_update", args=(True,)).is_ok
    wait_idle(panel)
    assert not _prompts(warnings, events.RESTART_NEEDED)


class _EnergizedController(RecordingController):
    is_energized = True


def _restartable(controller=None, **kwargs):
    calls = []
    panel = Setup(controller or RecordingController(), updater=FakeUpdater(),
                  restart=lambda: calls.append(list(panel.controller.calls)),
                  **kwargs)
    return panel, calls


def test_restart_refuses_while_anything_is_energized(fake_types):
    panel, calls = _restartable(_EnergizedController())
    for args in ((), (True,)):
        result = panel.run("restart_station", args=args)
        assert result.status == "refused", result
        assert "energized" in result.reason
    assert calls == [] and "reset" not in panel.controller.calls


def test_restart_asks_once_then_closes_every_model_before_it_restarts(
        fake_types, monkeypatch):
    flushed = []
    monkeypatch.setattr(events, "flush_file", lambda: flushed.append(True))
    panel, calls = _restartable()
    asked = panel.run("restart_station")
    assert asked.needs_confirm and asked.command == "restart_station"
    assert asked.reason == "Restart the station now? Every model closes first."
    assert calls == [] and "reset" not in panel.controller.calls
    assert panel.run("restart_station", args=(True,)).is_ok
    # The restart ran once, after every model was closed and the log flushed.
    assert calls == [["reset"]]
    assert flushed


def test_restart_is_refused_without_a_way_to_restart(fake_types):
    """Setup never execs by itself: `app.launch` hands it the restart."""
    panel = Setup(RecordingController(), updater=FakeUpdater())
    result = panel.run("restart_station", args=(True,))
    assert result.status == "refused"
    assert "start it again by hand" in result.reason


def test_restart_waits_while_an_update_is_landing(fake_types, checking):
    gate = threading.Event()

    class Slow(FakeUpdater):
        def apply(self, timeout=10.0):
            gate.wait(5.0)
            return super().apply(timeout)

    calls = []
    panel = _ready(Setup(RecordingController(), updater=Slow(check=behind(2)),
                         restart=lambda: calls.append(1)))
    assert panel.run("apply_update", args=(True,)).is_ok
    result = panel.run("restart_station", args=(True,))
    assert result.status == "refused" and "being applied" in result.reason
    gate.set()
    wait_idle(panel)
    assert calls == []


def test_a_failed_restart_is_an_error_and_the_station_stays_up(fake_types, warnings):
    def broken():
        raise OSError("exec format error")
    panel = Setup(RecordingController(), updater=FakeUpdater(), restart=broken)
    result = panel.run("restart_station", args=(True,))
    assert result.status == "refused"
    assert "did not restart" in result.reason
    assert [e for e in warnings if e.title == "Restart Failed" and e.severity == "error"]


def test_changed_firmware_asks_for_a_restart_by_hand_and_offers_no_restart_now(
        fake_types, checking, warnings):
    """run_swap.sh flashes the boards on the way up; a re-exec would not."""
    updater = FakeUpdater(check=behind(2), apply=dict(
        FakeUpdater().apply_result, firmware_changed=True))
    panel = _ready(Setup(RecordingController(), updater=updater))
    assert panel.run("apply_update", args=(True,)).is_ok
    wait_idle(panel)
    [restart] = _prompts(warnings, events.RESTART_NEEDED)
    assert restart.needs_ack and restart.action is None
    assert "firmware changed" in restart.message.lower()


# -- the bundle's Update row copy (brief-bundle-update B4) --------------------
# One copy for both worlds, never naming which: a release is "vX is ready",
# a checkout keeps counting commits (the tests above).

def released(latest="v1.3.0", tag="v1.2.0", first="Faster jog"):
    status = "up_to_date" if latest == tag else "behind"
    return {"status": status, "branch": None, "head": tag, "remote": latest,
            "behind": 0 if status == "up_to_date" else 1, "ahead": 0,
            "log": [first] if first and status == "behind" else [],
            "reason": "", "tag": tag, "latest": latest, "title": f"Station {latest}"}


def test_a_newer_release_says_its_tag_and_first_line(fake_types, checking, warnings):
    panel = _ready(Setup(RecordingController(), updater=FakeUpdater(check=released())))
    assert panel.update_status == ("v1.3.0 is ready: Faster jog. Update now, "
                                   "then restart.")
    assert panel.update_log == "Faster jog"
    [ready] = _prompts(warnings, events.UPDATE_READY)
    assert ready.message == "v1.3.0 is ready: Faster jog. Update now, then restart."
    assert ready.to_dict()["action"]["command"] == "apply_update"
    for word in ("bundle", "checkout", "commit", "git"):
        assert word not in panel.update_status.lower()


def test_a_release_with_no_notes_is_still_ready(fake_types, checking, warnings):
    panel = _ready(Setup(RecordingController(),
                         updater=FakeUpdater(check=released(first=""))))
    assert panel.update_status == "v1.3.0 is ready. Update now, then restart."


def test_level_with_the_latest_release_says_the_version(fake_types, checking, warnings):
    panel = Setup(RecordingController(),
                  updater=FakeUpdater(check=released(latest="v1.2.0")))
    wait_idle(panel)
    assert panel.update_status == "Up to date (v1.2.0)."
    assert panel.has_update is False
    assert not _prompts(warnings, events.UPDATE_READY)


def test_a_machine_not_signed_in_reads_the_sign_in_sentence(fake_types, checking):
    from controller.updater import REASONS
    result = dict(released(), status="unauthorised", behind=0, log=[],
                  reason=REASONS["unauthorised"])
    panel = Setup(RecordingController(), updater=FakeUpdater(check=result))
    wait_idle(panel)
    assert panel.update_status == ("Sign in to GitHub on this machine first: "
                                   "`gh auth login`, or open the repository once with git.")
    assert panel.has_update is False


def test_a_landed_release_asks_to_restart_as_a_checkout_does(
        fake_types, checking, warnings):
    updater = FakeUpdater(check=released(), apply={
        "updated": True, "old": "v1.2.0", "new": "v1.3.0", "deps_changed": False,
        "deps_ok": True, "firmware_changed": False,
        "reason": "Updated to v1.3.0. Restart the station to run it."})
    panel = _ready(Setup(RecordingController(), updater=updater))
    assert panel.run("apply_update", args=(True,)).is_ok
    wait_idle(panel)
    [prompt] = _prompts(warnings, events.RESTART_NEEDED)
    assert prompt.message == "Updated to v1.3.0. Restart the station to run it."
    assert prompt.to_dict()["action"]["command"] == "restart_station"
    # a check after the swap finds the new tag on disk: still "restart"
    updater.check_result = released(latest="v1.3.0", tag="v1.3.0")
    assert panel.run("check_updates").is_ok
    wait_idle(panel)
    assert panel.update_status.startswith("Updated to v1.3.0.")


def test_the_login_never_reaches_the_station_log(fake_types, checking, tmp_path,
                                                 monkeypatch):
    """The real Updater, frozen, behind a fake GitHub and a fake `gh`,
    driven through Setup's check and apply: the log file (where Setup writes
    every check and apply result) never holds the login."""
    import test_updater as tu
    monkeypatch.setattr(__import__("sys"), "frozen", True, raising=False)
    install = tu._write_bundle(tmp_path / "apps" / "station", "v1.2.0", "old")
    from controller.updater import Updater
    updater = Updater(root=install, run=tu.FakeLogin(), fetch=tu.FakeGitHub())
    log = events.open_file(str(tmp_path / "logs"))
    try:
        panel = _ready(Setup(RecordingController(), updater=updater))
        assert panel.run("apply_update", args=(True,)).is_ok
        wait_idle(panel)
        assert panel.station_version == "v1.2.0"
        assert (install / "VERSION").read_text().startswith("v1.3.0")
        events.flush_file()
        text = open(log, encoding="utf-8").read()
    finally:
        events.close_file()
    assert "v1.3.0" in text                 # the log did record the update
    assert tu.SECRET not in text
    assert tu.SECRET not in repr(panel.state)


def test_the_update_confirmation_names_no_world(panel):
    assert Setup.UPDATE_CONFIRM == ("Update the station now? The station must be "
                                    "restarted afterwards.")
    assert "checkout" not in Setup.UPDATE_CONFIRM and "bundle" not in Setup.UPDATE_CONFIRM


def test_an_update_waiting_for_the_restart_says_press_restart(fake_types, checking,
                                                              warnings):
    """Windows: the swap waits for the restart, so "quit and start again" by
    hand would run the old version; the line and Launch say Restart."""
    updater = FakeUpdater(check=released(), apply={
        "updated": True, "old": "v1.2.0", "new": "v1.3.0", "pending": True,
        "deps_changed": False, "deps_ok": True, "firmware_changed": False,
        "reason": "Updated to v1.3.0. Restart the station to run it."})
    panel = _ready(Setup(RecordingController(), updater=updater))
    assert panel.run("apply_update", args=(True,)).is_ok
    wait_idle(panel)
    assert panel.update_status == "Updated to v1.3.0. Press Restart to run it."
    [prompt] = _prompts(warnings, events.RESTART_NEEDED)
    assert prompt.to_dict()["action"]["command"] == "restart_station"
    connect(panel, "alpha")
    refused = panel.run("launch")
    assert refused.status == "refused"
    assert refused.reason == ("The station was updated to v1.3.0. Press Restart "
                              "before launching.")


# -- A3: the Trial store row -------------------------------------------------

@pytest.fixture
def store_choice(tmp_path, monkeypatch):
    """No STATION_MAP_DB, a private choices file, a tmp install root."""
    from controller import user_config
    from model import transfer_map as tm_module
    monkeypatch.delenv("STATION_MAP_DB", raising=False)
    monkeypatch.setenv("STATION_CONFIG", str(tmp_path / "choices" / "station.json"))
    user_config.forget()
    install = tmp_path / "install"
    install.mkdir()
    from model import store_choice as shared
    monkeypatch.setattr(shared, "install_root", lambda: install)
    yield install
    user_config.forget()


def test_setup_draws_no_trial_store_row(panel):
    """The Trial store row (A3) was never drawn and its builder is gone
    (architecture audit 2026-10-08): an operator chooses a store on the
    map's own `new_store` prompt (`model.store_choice.StorePrompt`)."""
    titles = [s["title"] for s in panel.schema["sections"]]
    assert "Trial store" not in titles
    assert not hasattr(panel, "_store_section")


def test_setups_leftover_trial_store_commands_are_gone(panel):
    """Arch audit #15: Setup's A3 Trial-store commands and fields survived
    with no control; both maps choose their store on their own prompt. The
    commands, their handlers and their fields are gone, so nothing can
    reach them by name."""
    for name in ("map_store_status", "open_map_store", "new_map_store",
                 "_map_target", "_transfer_map"):
        assert not hasattr(Setup, name), name
    assert not {"map_store_path", "map_store_dir", "map_store_name"} & set(Setup.PARAMS)
    for command in ("open_map_store", "new_map_store"):
        assert panel.run(command).is_refused, command


def test_a_maps_own_store_choice_is_remembered_for_the_session(store_choice, tmp_path):
    """The per-session store choice (`_SessionChoices`), through the map's
    own New store: a Guest's goes to the station's choices file. A map
    built afresh never opens the station's store (owner ruling 2026-10-08,
    data safety)."""
    from controller import user_config
    from model.transfer_map import TransferMap
    panel = Setup(RecordingController())
    model = TransferMap()
    model.choices = panel._map_choices
    assert model.state["store"]["chosen"] is False
    path = tmp_path / "trials" / "lab.sqlite"
    result = model.run("new_store", {"store_dir": str(tmp_path / "trials"),
                                     "store_name": "lab"})
    assert result.is_ok, result.reason
    assert path.is_file() and model.db_path == path
    assert user_config.read("map_store") == str(path)   # remembered (a Guest's)
    assert TransferMap().db_path is None


# -- A4: Switch to stable (frozen bundles only; owner decision 3) ---------------

STABLE_CONFIRM = ("Switch to the stable station? The boards will be flashed with "
                  "the stable firmware, every model is closed, and the stable app "
                  "opens. To come back, start the station again and accept Flash now.")


class StableFirmware(FakeFirmware):
    """The stable sketches' FirmwareCheck: `failed` names the boards whose
    upload fails."""

    def __init__(self, failed=(), **kwargs):
        super().__init__(**kwargs)
        self.failed = list(failed)
        self.order = None

    def flash(self, boards, on_line=None, timeout=None):
        if self.order is not None:
            self.order.append("flash")
        answer = super().flash(boards, on_line=on_line, timeout=timeout)
        answer["results"] = {b: "FAILED" if b in self.failed else "ok" for b in boards}
        if self.failed:
            answer.update(ok=False, returncode=1)
        return answer


@pytest.fixture
def stable(tmp_path):
    """A bundle's stable/ folder: its launcher and its sketches."""
    import os as _os
    root = tmp_path / "stable"
    (root / "firmware").mkdir(parents=True)
    (root / ("station-stable.exe" if _os.name == "nt" else "station-stable")).write_text("")
    return root


def switching(stable, firmware, order, controller=None):
    return Setup(controller or RecordingController(), stable_root=stable,
                 stable_firmware=firmware,
                 launch_stable=lambda argv, cwd: order.append(("launch", argv, cwd)),
                 exit_app=lambda: order.append("exit"))


def wait_stable(panel):
    thread = panel._flash_thread
    if thread is not None:
        thread.join(5.0)
        assert not thread.is_alive()


def test_a_checkout_has_no_switch_to_stable(panel):
    assert "Stable" not in [s["title"] for s in panel.schema["sections"]]
    assert "no stable app" in refused(lambda: panel.switch_to_stable(True))


def test_a_bundle_with_stable_beside_it_offers_the_switch(fake_types, stable):
    panel = switching(stable, StableFirmware(), [])
    titles = [s["title"] for s in panel.schema["sections"]]
    # Upkeep after Launch, the station's defaults last (UX audit
    # 2026-10-08; the account section led the schema before, user-system
    # section 2.4).
    assert titles[-4:] == ["Update", "Firmware", "Stable", Setup.ACCOUNT_SECTION]
    [button] = [e for e in panel.schema["sections"][-2]["elements"]
                if e["type"] == "button"]
    assert button["command"] == "switch_to_stable"
    assert button["confirm"] == STABLE_CONFIRM
    question = asked(panel.switch_to_stable)
    assert question.prompt == STABLE_CONFIRM and question.command == "switch_to_stable"


def test_the_switch_closes_flashes_stable_launches_it_then_exits(fake_types, stable):
    order = []
    firmware = StableFirmware(lines=["Sketches: stable", "Summary:", "  DC Probe ok"])
    firmware.order = order
    controller = RecordingController()
    panel = switching(stable, firmware, order, controller)
    connect(panel, "alpha")
    assert panel.run("launch").is_ok
    assert controller.model_names == ["Alpha", "Screen"]
    assert panel.run("switch_to_stable", args=(True,)).is_ok
    wait_stable(panel)
    assert controller.model_names == [] and "reset" in controller.calls
    assert firmware.flashes == [["Stepper Probe", "DC Probe", "Chuck Positioner",
                                 "Temperature Controller"]]
    exe = stable / ("station-stable.exe" if __import__("os").name == "nt" else "station-stable")
    assert order == ["flash", ("launch", [str(exe)], str(stable)), "exit"]


def test_a_board_that_fails_to_flash_stops_the_switch(fake_types, stable, warnings):
    order = []
    firmware = StableFirmware(failed=["Chuck Positioner"],
                              lines=["  Chuck Positioner FAILED"])
    panel = switching(stable, firmware, order)
    assert panel.switch_to_stable(True)
    wait_stable(panel)
    assert [o for o in order if o != "flash"] == [], "nothing launched, no exit"
    [failed] = [e for e in warnings if e.title == "Switch to Stable Failed"]
    assert "Chuck Positioner" in failed.message
    assert "keeps running" in failed.message
    assert panel.firmware_progress == ""


def test_the_switch_waits_for_a_scan_and_a_flash(fake_types, stable, monkeypatch):
    panel = switching(stable, StableFirmware(), [])
    monkeypatch.setattr(Setup, "is_scanning", property(lambda self: True))
    assert "scan" in refused(lambda: panel.switch_to_stable(True))


# -- A5: no release yet --------------------------------------------------------

def test_no_release_yet_reads_as_such_on_the_update_line(panel):
    panel._publish_check({"status": "no_release", "behind": 0, "log": [],
                          "reason": "No release has been published yet.",
                          "tag": "v1.2.0"})
    assert panel.update_status == "No release has been published yet."
    assert panel.has_update is False


def test_a_failed_flash_warns_with_what_to_do(fake_types, warnings):
    class Hinting(FakeFirmware):
        def flash(self, boards, on_line=None, timeout=None):
            answer = super().flash(boards, on_line=on_line, timeout=timeout)
            answer["hints"] = ["Install Rosetta 2, then flash again."]
            return answer
    panel = checked(Hinting(flash_ok=False, lines=["  DC Probe FAILED"]))
    assert panel.flash_firmware(True)
    wait_firmware(panel)
    [failed] = [e for e in warnings if e.title == "Firmware Flash Failed"]
    assert "Install Rosetta 2, then flash again." in failed.message


# -- owner 2026-10-07: no Flash now for a board that is not plugged in ------

def test_the_startup_offer_names_only_boards_the_scan_found(
        fake_types, firmware_checking, warnings):
    firmware = FakeFirmware(result=firmware_result(stale=["Stepper Probe"],
                                                   never=["DC Probe"]))
    panel = Setup(RecordingController(), firmware=firmware)
    panel._found = {"/dev/ttyACM0": "Stepper Probe"}   # the DC Probe is off
    panel._scan_settled = True
    _listening(panel)
    [offer] = _prompts(warnings, events.FIRMWARE_OUT_OF_DATE)
    assert offer.message.startswith("Stepper Probe out of date. Flash it now?")
    assert "DC Probe" not in offer.message


def test_no_offer_when_no_out_of_date_board_is_plugged_in(
        fake_types, firmware_checking, warnings):
    firmware = FakeFirmware(result=firmware_result(never=["DC Probe"]))
    panel = Setup(RecordingController(), firmware=firmware)
    panel._found = {"/dev/ttyACM0": "Stepper Probe"}
    panel._scan_settled = True
    _listening(panel)
    assert _prompts(warnings, events.FIRMWARE_OUT_OF_DATE) == []
    assert "DC Probe" in panel.firmware_status   # the row still says so


def test_a_running_scan_holds_the_offer_until_it_settles(
        fake_types, firmware_checking, warnings):
    firmware = FakeFirmware(result=firmware_result(stale=["Stepper Probe"]))
    panel = Setup(RecordingController(), firmware=firmware)
    panel._scan_thread = object()                    # a scan started, not done
    _listening(panel)
    assert _prompts(warnings, events.FIRMWARE_OUT_OF_DATE) == []
    panel._found = {"/dev/ttyACM0": "Stepper Probe"}
    panel._scan_settled = True
    panel._deliver_startup_offer()                   # what the scan's end does
    assert len(_prompts(warnings, events.FIRMWARE_OUT_OF_DATE)) == 1


def test_answering_the_sign_in_screen_rescans_when_the_station_has_scanned(
        fake_types, monkeypatch):
    panel = Setup(RecordingController())
    scans = []
    monkeypatch.setattr(panel, "scan", lambda: scans.append(1))
    panel.open_as_guest()
    assert scans == []                  # never scanned (a test, no app): no rescan
    panel._scan_thread = threading.Thread(target=lambda: None)   # one ran and ended
    panel.open_as_guest()
    assert scans == [1]

