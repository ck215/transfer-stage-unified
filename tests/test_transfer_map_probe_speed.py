"""The probe's Manual Speed on the trial sheet (owner, 2026-10-12: "a speed
controller for now at those last two stages so I don't have to hop between
tabs").

In the `live` and `marked` steps only, the Transfer Map draws the Manual
Speed of the probe it is coupled to (`TransferMap._probe()`: the probe RGB
Analysis follows, else the first probe in a mode), in the unit the operator
chose on the probe's page. It is a forwarded control, like the capture
region: the probe keeps the setting, the map only reaches it. A write goes
through the probe's own commit path (`Panel.set_value` -> the probe's
`_apply_inputs` -> its gated, strictly parsed setters), so the probe's mode
gate, its Params and its unit list decide, and the next jog frame carries
the value exactly as an edit on the probe's page would. Nothing here moves
an axis or writes to a port.

The fixtures are `test_transfer_map`'s (a temporary store, the real RGB
Analysis over a fake screen, the fake display) and `test_probe`'s fake
port and gamepad: no test opens a serial port.
"""
import struct

import pytest

import schema as sch
from controller.controller import Controller
from events import events
from model.probe import DCProbe, StepperProbe
from model.transfer_map import TransferMap

from test_probe import LEVELS, make_probe
from test_transfer_map import (  # noqa: F401  (fixtures)
    _arm, _give_flake, _wire, fake_display, jpeg_recorder, private_db, red)

PROBE = "Stepper Probe"
#: The forwarded attributes, the probe's own name after "probe_".
UNIT, PCT, STEPS, UM_S, ALT = ("probe_man_full_speed_unit",
                               "probe_man_full_speed_pct",
                               "probe_man_full_speed",
                               "probe_man_full_speed_um_s",
                               "probe_man_full_speed_alt_text")
FORWARDED = (UNIT, PCT, STEPS, UM_S)


def _station(red, cls=StepperProbe, name=PROBE, follow=True):
    probe, port, _gamepad = make_probe(cls, levels=LEVELS)
    model = TransferMap()
    model.open()
    for other_name, other in (("RGB Analysis", red), (name, probe)):
        model.on_model_added(other_name, other)
    if follow:
        red.source_name = name
    model.tip_id = "tip-A"
    _give_flake(model)
    _wire(model)
    return model, probe, port


@pytest.fixture
def station(red):
    model, probe, port = _station(red)
    try:
        yield model, probe, port
    finally:
        model.close()
        probe._stop_threads()


@pytest.fixture
def live(station):
    model, probe, port = station
    probe.set_mode("manual")
    _arm(model)
    assert model.phase == "live"
    return model, probe, port


def _elements_for(model, attr):
    return [e for e in sch.elements(model.schema) if e.get("model_attr") == attr]


def _jog_speed(probe, port):
    """The speed the next 50 Hz jog frame carries (the frame's last float)."""
    port.writes.clear()
    assert probe._gamepad_tick() is True
    size = struct.calcsize(probe.PACKET_FORMAT)
    [frame] = [struct.unpack(probe.PACKET_FORMAT, w) for w in port.writes
               if len(w) == size]
    return frame[-1]


# -- where it is drawn --------------------------------------------------------

def test_the_speed_control_is_drawn_only_in_live_and_marked():
    model = TransferMap()
    schema = model.schema
    for attr in FORWARDED + (ALT,):
        assert _elements_for(model, attr), f"{attr} is not on the sheet"
    for phase in TransferMap.PHASES:
        shown = {e.get("model_attr") for e in sch.shown_elements(schema, phase)}
        for attr in FORWARDED:
            assert (attr in shown) == (phase in ("live", "marked")), (attr, phase)


def test_the_control_is_the_probes_own_unit_choice_and_entries():
    """A choice of the units a probe offers, one entry per unit shown when
    that unit is chosen: the probe page's own pattern, no new element type,
    no options to poll."""
    model = TransferMap()
    [choice] = _elements_for(model, UNIT)
    assert choice["type"] == "choice"
    assert choice["options"] == list(StepperProbe.SPEED_UNITS)
    for attr, unit in ((PCT, "%"), (STEPS, "steps/s"), (UM_S, "µm/s")):
        [entry] = [e for e in _elements_for(model, attr) if e["type"] == "entry"]
        assert entry["shown_when"] == {"attr": UNIT, "values": [unit]}
        assert entry["writable"] is True
    for e in sch.elements(model.schema):
        if e.get("model_attr") in FORWARDED + (ALT,):
            assert e["type"] in sch.ELEMENT_TYPES
            assert "options_command" not in e and "command" not in e


# -- reading the coupled probe ------------------------------------------------

def test_it_shows_the_coupled_probes_manual_speed_and_unit(live):
    model, probe, _port = live
    values = model.state["values"]
    assert values[UNIT] == probe.man_full_speed_unit == "%"
    assert values[PCT] == probe.state["values"]["man_full_speed_pct"]
    assert values[STEPS] == str(probe.man_full_speed)
    assert values[ALT] == probe.man_full_speed_alt_text


def test_an_edit_on_the_probes_page_shows_on_the_sheet(live):
    model, probe, _port = live
    assert probe.set_value("man_full_speed", 250).is_ok
    assert probe.set_value("man_full_speed_unit", "steps/s").is_ok
    values = model.state["values"]
    assert values[STEPS] == "250"
    assert values[UNIT] == "steps/s"
    assert values[ALT] == probe.man_full_speed_alt_text


def test_the_speed_line_names_the_probe(live):
    model, _probe, _port = live
    assert PROBE in model.state["values"]["speed_probe_text"]


# -- setting it, through the probe's own setter -------------------------------

def test_setting_it_while_live_sets_the_probe_and_the_next_jog_frame(live):
    model, probe, port = live
    assert _jog_speed(probe, port) == 400.0
    result = model.set_value(STEPS, "20")
    assert result.is_ok, result.reason
    assert probe.man_full_speed == 20
    assert probe.state["values"]["man_full_speed"] == "20"
    assert _jog_speed(probe, port) == 20.0
    assert probe.is_manual, "the edit does not leave the mode"


def test_setting_it_while_marked_sets_the_probe(live):
    model, probe, port = live
    assert model.run("mark_force").is_ok
    assert model.phase == "marked"
    assert model.set_value(PCT, "6.3").is_ok
    assert probe.man_full_speed == StepperProbe.pct_to_steps(6.3)
    assert model.set_value(UM_S, "125").is_ok
    assert probe.man_full_speed == 200          # 125 um/s at 0.625 um a count
    assert _jog_speed(probe, port) == 200.0


def test_the_unit_is_the_probes_one_preference(live):
    model, probe, _port = live
    assert model.set_value(UNIT, "µm/s").is_ok
    assert probe.man_full_speed_unit == "µm/s"
    assert probe.state["values"]["man_full_speed_unit"] == "µm/s"


def test_the_probes_mode_gate_still_decides(live):
    """Manual Speed is locked in autonomous on the probe's page; the sheet
    reaches the same gate, and the previous speed stays in effect."""
    model, probe, _port = live
    probe.set_mode("disabled")
    probe.set_mode("autonomous")
    assert probe.mode_name == "autonomous"
    result = model.set_value(STEPS, "100")
    assert result.status == "refused"
    assert "autonomous" in result.reason
    assert probe.man_full_speed == 400


def test_the_probes_params_still_decide(live):
    model, probe, _port = live
    result = model.set_value(STEPS, "0")
    assert result.status == "refused"
    assert probe.man_full_speed == 400
    result = model.set_value(PCT, "abc")
    assert result.status == "refused"
    assert probe.man_full_speed == 400


def test_a_unit_the_coupled_probe_does_not_offer_is_refused_by_the_probe(red):
    model, probe, _port = _station(red, DCProbe, "DC Probe")
    try:
        probe.set_mode("manual")
        _arm(model)
        result = model.set_value(UNIT, "µm/s")
        assert result.status == "refused"
        assert "DC Probe" in result.reason
        assert probe.man_full_speed_unit == "%"
        assert model.state["values"][UM_S] == ""     # the DC Probe has no um/s
    finally:
        model.close()
        probe._stop_threads()


# -- refusals -------------------------------------------------------------------

@pytest.mark.parametrize("attr, value", [(STEPS, "20"), (UNIT, "steps/s")])
def test_outside_live_and_marked_it_is_refused_in_a_sentence(station, attr, value):
    model, probe, _port = station
    probe.set_mode("manual")
    assert model.phase == "setup"
    result = model.set_value(attr, value)
    assert result.status == "refused"
    assert result.reason.endswith(".") and "Stepper Probe" in result.reason
    assert probe.man_full_speed == 400 and probe.man_full_speed_unit == "%"


def test_without_a_coupled_probe_it_is_refused_in_a_sentence(red):
    model = TransferMap()
    model.open()
    try:
        model.on_model_added("RGB Analysis", red)
        model.tip_id = "tip-A"
        _give_flake(model)
        _wire(model)
        _arm(model)
        assert model.phase == "live"
        result = model.set_value(STEPS, "20")
        assert result.status == "refused"
        assert result.reason.endswith(".") and "probe" in result.reason
        values = model.state["values"]
        assert values[UNIT] == "" and values[STEPS] == ""
        assert "No probe" in values["speed_probe_text"]
    finally:
        model.close()


def test_reading_it_every_poll_outside_its_steps_logs_nothing(station, monkeypatch):
    """The client polls state every second: the forwarded readouts are read
    (never refused, never logged) in every step, and nothing new is polled."""
    model, probe, _port = station
    probe.set_mode("manual")
    said = []
    for level in ("info", "debug", "warn"):
        monkeypatch.setattr(events, level,
                            lambda title, *a, _l=level, **k: said.append(title))
    assert model.phase == "setup"
    for _ in range(5):
        model.state
    assert "Refused" not in said, said


def test_constructing_the_map_without_a_probe_seeds_nothing_through_the_probe():
    model = TransferMap()       # raises if a forwarded setter were seeded
    assert model.state["values"][UNIT] == ""


# -- the path a view takes ------------------------------------------------------

def test_the_controller_commit_reaches_the_probe(live):
    model, probe, port = live
    controller = Controller()
    controller._models["Transfer Map"] = model
    controller._locks["Transfer Map"] = __import__("threading").Lock()
    try:
        result = controller.set_value("Transfer Map", STEPS, "300")
        assert result.is_ok, result.reason
        assert probe.man_full_speed == 300
        assert _jog_speed(probe, port) == 300.0
    finally:
        controller._models.pop("Transfer Map")
        controller._locks.pop("Transfer Map")


def test_a_speed_box_never_stands_between_the_operator_and_mark_force(live):
    """The Web client sends every edited, uncommitted entry with a command
    (MOD-6). The speed commits on its own (`_commit`); riding along with Mark
    force or End recording it is dropped, so a bad box cannot refuse them
    and they never change the probe's speed."""
    model, probe, _port = live
    result = model.run("mark_force", {STEPS: "0"})
    assert result.is_ok, result.reason
    assert model.phase == "marked"
    result = model.run("end_recording", {STEPS: "250"})
    assert result.is_ok, result.reason
    assert probe.man_full_speed == 400
