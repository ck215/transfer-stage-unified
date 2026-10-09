"""One board per tagged port (owner ruling 2026-10-09, the XYZ Stage).

A model with `PORT_TAGS` owns one board per port resource; each board answers
the scan with `DEV: <letter> <tag>`. The scan places every board by its tag,
and the row launches only when every tag answered on exactly one port. A
partial set, a duplicate tag or an untagged board is a fault the row states,
never a partial launch.

No test here opens a real port (the autouse fixture from test_setup_identify).
"""
import threading

import pytest

from controller import setup as station_setup
from controller.setup import NOT_CONNECTED, SIM, Setup
from devices import serial_port as serial_port_module
from events import events
from model.base import Model
from result import Refused

from tests.test_setup import RecordingController
from tests.test_setup_identify import (  # noqa: F401  (autouse fixture)
    isolated_serial, port_answering)

pytestmark = pytest.mark.usefixtures("profiles_on")

X_PORT, Y_PORT, Z_PORT, SPARE = ("/dev/ttyACM0", "/dev/ttyACM1",
                                 "/dev/ttyACM2", "/dev/ttyACM3")


class TriStage(Model):
    """Three boards, one per axis, each answering `DEV: q <axis>`."""

    NAME = "Tri Stage"
    IDENTITY = "q"
    NEEDS_PORT = True
    NEEDS_GAMEPAD = False
    RESOURCES = ("port_x", "port_y", "port_z")
    PORT_TAGS = {"port_x": "X", "port_y": "Y", "port_z": "Z"}

    def __init__(self, sim=False, port_x=None, port_y=None, port_z=None):
        super().__init__()
        self.sim, self.ports = sim, (port_x, port_y, port_z)


@pytest.fixture
def registry(monkeypatch):
    fresh = dict(station_setup.MODEL_TYPES)
    monkeypatch.setattr(station_setup, "MODEL_TYPES", fresh)
    return fresh


@pytest.fixture
def panel(registry):
    Setup.register(TriStage)
    built = Setup(RecordingController())
    built._ports = [X_PORT, Y_PORT, Z_PORT, SPARE]
    return built


@pytest.fixture
def warnings():
    events.clear()
    seen = []
    events.subscribe(seen.append)
    yield seen
    events.unsubscribe(seen.append)


def _found(panel, boards):
    """What a finished scan leaves: port -> (model name, tag)."""
    panel._found = {port: name for port, (name, _) in boards.items()}
    panel._found_tag = {port: tag for port, (_, tag) in boards.items()}


def _status(panel):
    panel._refresh_rows()
    return panel.tri_stage_status, panel.tri_stage_enabled


ALL_THREE = {X_PORT: ("Tri Stage", "X"), Y_PORT: ("Tri Stage", "Y"),
             Z_PORT: ("Tri Stage", "Z")}


# -- registration ----------------------------------------------------------

def test_port_tags_must_tag_every_port_resource(registry):
    class Partial(TriStage):
        NAME, IDENTITY = "Partial Stage", "w"
        PORT_TAGS = {"port_x": "X", "port_y": "Y"}
    with pytest.raises(ValueError, match="tag every port resource"):
        Setup.register(Partial)


def test_port_tags_must_be_distinct(registry):
    class Twice(TriStage):
        NAME, IDENTITY = "Twice Stage", "w"
        PORT_TAGS = {"port_x": "X", "port_y": "X", "port_z": "Z"}
    with pytest.raises(ValueError, match="distinct"):
        Setup.register(Twice)


def test_port_tags_need_an_identity_letter(registry):
    class Mute(TriStage):
        NAME, IDENTITY = "Mute Stage", None
    with pytest.raises(ValueError, match="no IDENTITY"):
        Setup.register(Mute)


# -- the handshake gives the name and the tag ------------------------------

def test_a_tagged_board_is_named_by_its_letter_and_keeps_its_tag(panel, monkeypatch):
    monkeypatch.setattr(station_setup, "SerialPort", port_answering({500000: "q Y"}))
    assert panel.identify(Y_PORT) == "Tri Stage"
    assert panel.probe_tags[Y_PORT] == "Y"


def test_an_untagged_board_has_no_tag(panel, monkeypatch):
    monkeypatch.setattr(station_setup, "SerialPort", port_answering({500000: "q"}))
    assert panel.identify(X_PORT) == "Tri Stage"
    assert panel.probe_tags[X_PORT] is None


def test_the_scan_records_each_ports_tag(panel, monkeypatch):
    tags = {X_PORT: "X", Y_PORT: "Y", Z_PORT: "Z"}
    monkeypatch.setattr(serial_port_module, "list_ports",
                        lambda: [X_PORT, Y_PORT, Z_PORT], raising=False)
    # No SDL from the scan thread: the real enumeration crashes the
    # interpreter at exit on macOS when run off the main thread.
    monkeypatch.setattr(Setup, "scan_gamepads", lambda self: ["None"])

    def identify(self, port, should_abort=None):
        self.probe_tags[port] = tags[port]
        return "Tri Stage"
    monkeypatch.setattr(Setup, "identify", identify)
    assert panel.scan() is True
    thread = panel._scan_thread
    if isinstance(thread, threading.Thread):
        thread.join(5)
    assert panel._found_tag == tags
    assert (panel.tri_stage_port, panel.tri_stage_port_y, panel.tri_stage_port_z) \
        == (X_PORT, Y_PORT, Z_PORT)


# -- all three, or a fault -------------------------------------------------

def test_all_three_boards_are_placed_by_tag_and_the_row_launches(panel):
    # Found in a scrambled port order: the tags decide, not the order.
    _found(panel, {Z_PORT: ("Tri Stage", "X"), X_PORT: ("Tri Stage", "Z"),
                   Y_PORT: ("Tri Stage", "Y")})
    panel.auto_assign()
    assert (panel.tri_stage_port, panel.tri_stage_port_y, panel.tri_stage_port_z) \
        == (Z_PORT, Y_PORT, X_PORT)
    assert _status(panel) == ("detected: Tri Stage (X, Y, Z)", True)
    config = next(c for c in panel.configs if c["model"] == "Tri Stage")
    assert (config["port_x"], config["port_y"], config["port_z"]) == (Z_PORT, Y_PORT, X_PORT)


def test_a_missing_axis_is_a_fault_and_never_a_partial_launch(panel, warnings):
    _found(panel, {X_PORT: ("Tri Stage", "X"), Y_PORT: ("Tri Stage", "Y")})
    panel.auto_assign()
    assert (panel.tri_stage_port, panel.tri_stage_port_y, panel.tri_stage_port_z) \
        == (NOT_CONNECTED, NOT_CONNECTED, NOT_CONNECTED)
    status, enabled = _status(panel)
    assert (status, enabled) == ("fault: Z missing", False)
    assert any("Z missing" in str(e) for e in warnings)


def test_two_boards_with_one_tag_are_a_fault(panel):
    _found(panel, {X_PORT: ("Tri Stage", "X"), SPARE: ("Tri Stage", "X"),
                   Y_PORT: ("Tri Stage", "Y"), Z_PORT: ("Tri Stage", "Z")})
    panel.auto_assign()
    status, enabled = _status(panel)
    assert not enabled
    assert status.startswith("fault: two boards answered as X")


def test_an_untagged_board_is_a_fault(panel):
    _found(panel, {X_PORT: ("Tri Stage", "X"), Y_PORT: ("Tri Stage", "Y"),
                   Z_PORT: ("Tri Stage", None)})
    panel.auto_assign()
    status, enabled = _status(panel)
    assert not enabled
    assert "no X/Y/Z tag on /dev/ttyACM2" in status


def test_nothing_answering_is_not_connected_not_a_fault(panel):
    _found(panel, {})
    panel.auto_assign()
    assert _status(panel) == ("not connected", False)


def test_a_partial_set_is_refused_at_start(panel):
    _found(panel, {X_PORT: ("Tri Stage", "X"), Y_PORT: ("Tri Stage", "Y")})
    panel.tri_stage_port, panel.tri_stage_port_y, panel.tri_stage_port_z = \
        X_PORT, Y_PORT, SPARE
    with pytest.raises(Refused, match="one board per axis.*Z missing"):
        panel._check_tags("tri_stage", panel._rows["tri_stage"])


def test_a_board_on_the_wrong_axis_port_is_refused(panel):
    _found(panel, ALL_THREE)
    config = {"model": "Tri Stage", "port": Y_PORT, "port_x": Y_PORT,
              "port_y": X_PORT, "port_z": Z_PORT, "sim": False, "gamepad": None}
    with pytest.raises(Refused, match="is the Y board, not X"):
        panel._check_identities([config])


def test_a_vanished_axis_port_is_not_connected_again_not_sim(panel):
    _found(panel, ALL_THREE)
    panel.auto_assign()
    panel._ports = [X_PORT, Z_PORT, SPARE]          # the Y board was unplugged
    panel._drop_stale_selections()
    assert panel.tri_stage_port_y == NOT_CONNECTED
    assert _status(panel)[1] is False


def test_a_simulated_row_launches_all_three_simulated(panel):
    assert panel.run("set_tri_stage_port", args=(SIM,)).is_ok
    assert _status(panel) == ("simulated", True)
    config = next(c for c in panel.configs if c["model"] == "Tri Stage")
    model = panel.model_from_config(config)
    assert model.ports == (SIM, SIM, SIM) and model.sim is True


def test_one_board_models_are_untouched_by_tags(panel):
    """The built-in rows have no tags: their auto-assign is the old one."""
    assert panel._rows["stepper_probe"]["tags"] == {}
    _found(panel, {SPARE: ("Stepper Probe", None)})
    panel.auto_assign()
    assert panel.stepper_probe_port == SPARE
