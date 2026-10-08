"""Manual Speed is live in manual mode (owner ruling, 2026-09-26).

The owner: adjusting Manual Speed (`man_full_speed`) on the fly while jogging
is a feature. The operator moves the slider or types in the box while the
probe is in MANUAL, and the next 50 Hz jog frame carries the new value.

Before the ruling every probe entry carried `disabled_when=("autonomous",
"manual")` (DC-6), so the three views greyed the field out and the model's
gated setter refused the write. The ruling unlocks this one field in this one
mode. Everything else stays as it was:

- Autonomous Speed, the step sizes and the target distances stay locked in
  both motion modes; Manual Speed stays locked in AUTONOMOUS.
- A new speed is validated before it can reach a frame (the Param's minimum,
  finite, a number). A refused value leaves the previous speed in effect.
- The stop latch outranks the speed: with it set, no jog frame is written.
- An edit writes nothing to the port. The jog stream picks the value up on
  its next tick, so a slider drag cannot flood the serial line.

The ruling covers all three probe classes (DCProbe and ChuckPositioner
subclass Probe).
"""
import struct

import pytest

import schema as sch
from controller.controller import Controller
from model.probe import ChuckPositioner, DCProbe, StepperProbe
from result import Refused
from test_probe import LEVELS, make_probe

CLASSES = (StepperProbe, DCProbe, ChuckPositioner)


def _jogs(probe, port):
    """Every 42-byte jog packet written, unpacked with the class's format."""
    return [struct.unpack(probe.PACKET_FORMAT, w) for w in port.writes
            if len(w) == struct.calcsize(probe.PACKET_FORMAT)]


def _speed_of_next_frame(probe, port):
    port.writes.clear()
    assert probe._gamepad_tick() is True
    jogs = _jogs(probe, port)
    assert len(jogs) == 1, "one tick writes one jog frame"
    return jogs[0][-1]


@pytest.fixture(params=CLASSES, ids=lambda c: c.__name__)
def manual(request):
    probe, port, gamepad = make_probe(request.param, levels=LEVELS)
    probe.set_mode("manual")
    yield probe, port
    probe._stop_threads()


def _entry(probe, attr):
    return next(e for e in sch.elements(probe.schema)
                if e.get("model_attr") == attr and e["type"] == "entry")


# -- the model --------------------------------------------------------------

def test_manual_speed_is_accepted_in_manual_and_the_next_frame_carries_it(manual):
    probe, port = manual
    default = float(probe.PARAMS["man_full_speed"].default)
    assert _speed_of_next_frame(probe, port) == default

    result = probe.run("_commit", inputs={"man_full_speed": "250"})
    assert result.is_ok, result.reason
    assert _speed_of_next_frame(probe, port) == 250.0

    probe.set_value("man_full_speed", 37)
    assert _speed_of_next_frame(probe, port) == 37.0
    assert probe.is_manual, "the edit does not leave the mode"


def test_the_direct_attribute_write_is_live_in_manual_too(manual):
    probe, port = manual
    probe.man_full_speed = 90
    assert _speed_of_next_frame(probe, port) == 90.0


@pytest.mark.parametrize("bad", ["", "abc", "0", "-5", "nan", "inf", None])
def test_an_invalid_manual_speed_is_refused_and_the_old_one_stays(manual, bad):
    """Never a substituted default, a zero or a huge value: the frame keeps
    the speed the operator last set."""
    probe, port = manual
    assert probe.run("_commit", inputs={"man_full_speed": "250"}).is_ok
    assert probe.run("_commit", inputs={"man_full_speed": bad}).is_refused
    assert _speed_of_next_frame(probe, port) == 250.0
    with pytest.raises(Refused):
        probe.man_full_speed = bad
    assert _speed_of_next_frame(probe, port) == 250.0
    assert probe.man_full_speed == 250


@pytest.mark.parametrize("attr", ["full_speed", "x_step", "y_step", "z_step",
                                  "x_dist", "y_dist", "z_dist"])
def test_every_other_motion_field_stays_locked_in_manual(manual, attr):
    probe, _port = manual
    before = getattr(probe, attr)
    assert probe.run("_commit", inputs={attr: "77"}).is_refused
    with pytest.raises(Refused):
        setattr(probe, attr, "77")
    assert getattr(probe, attr) == before


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_manual_speed_stays_locked_in_autonomous(cls):
    """The ruling is about jogging; an autonomous run keeps its lock."""
    probe, _port, _pad = make_probe(cls, levels=LEVELS)
    try:
        probe.set_mode("autonomous")
        assert probe.run("_commit", inputs={"man_full_speed": "77"}).is_refused
        with pytest.raises(Refused):
            probe.man_full_speed = 77
    finally:
        probe._stop_threads()


def test_the_dc_brake_fields_stay_locked_in_manual():
    probe, _port, _pad = make_probe(DCProbe, levels=LEVELS)
    try:
        probe.set_mode("manual")
        for attr in ("slow_speed", "brake_distance"):
            assert probe.run("_commit", inputs={attr: "5"}).is_refused
    finally:
        probe._stop_threads()


# -- safety: the latch and the port -----------------------------------------

def test_a_set_latch_writes_no_frame_whatever_the_speed(manual):
    """The latch set while the mode still reads MANUAL (the race the pump's
    own check exists for): a speed edit does not put a frame on the wire."""
    probe, port = manual
    probe._estop.set()
    probe.run("_commit", inputs={"man_full_speed": "999"})
    port.writes.clear()
    probe._gamepad_tick()
    assert _jogs(probe, port) == []


def test_after_full_stop_a_speed_edit_moves_nothing(manual):
    probe, port = manual
    probe.estop()
    assert not probe.is_manual
    assert probe.run("_commit", inputs={"man_full_speed": "300"}).is_ok
    port.writes.clear()
    for _ in range(3):
        probe._gamepad_tick()
    assert _jogs(probe, port) == []
    with pytest.raises(Refused):
        probe.set_mode("manual")


def test_a_slider_drag_writes_nothing_until_the_next_tick(manual):
    """A burst of edits is a store update each, never a port write: the jog
    stream is the only writer, one frame per tick, with the latest value."""
    probe, port = manual
    port.writes.clear()
    port.calls.clear()
    for value in range(100, 400, 3):
        assert probe.run("_commit", inputs={"man_full_speed": str(value)}).is_ok
    assert port.calls == [], "an edit must not write to the port"
    assert _speed_of_next_frame(probe, port) == 397.0


# -- the schema: what every view greys out -----------------------------------

@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_the_schema_leaves_manual_speed_live_in_manual_only(cls):
    probe, _port, _pad = make_probe(cls)
    # 2026-10-07: the dials are percent entries; steps/s sit under them as secondaries.
    man, auto = _entry(probe, "man_full_speed_pct"), _entry(probe, "full_speed_pct")
    assert man["disabled_when"] == ["autonomous"]
    assert sch.is_enabled(man, "manual")
    assert not sch.is_enabled(man, "autonomous")
    assert auto["disabled_when"] == ["autonomous", "manual"]
    assert not sch.is_enabled(auto, "manual")
    assert man["slider"] == list(cls.SPEED_SLIDER) or \
        tuple(man["slider"]) == tuple(cls.SPEED_SLIDER)


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_the_state_serves_the_new_speed_while_manual(cls):
    probe, _port, _pad = make_probe(cls, levels=LEVELS)
    try:
        probe.set_mode("manual")
        assert probe.run("_commit", inputs={"man_full_speed": "321"}).is_ok
        state = probe.state
        assert state["mode"] == "manual"
        assert state["values"]["man_full_speed"] == "321"
    finally:
        probe._stop_threads()


# -- the Controller: the path every view takes -------------------------------

def test_the_controller_commits_manual_speed_while_manual():
    probe, port, _pad = make_probe(StepperProbe, levels=LEVELS)
    controller = Controller()
    controller.add("Stepper Probe", probe)
    try:
        assert controller.run("Stepper Probe", "set_mode",
                              args=("manual",)).is_ok
        assert controller.set_value("Stepper Probe", "man_full_speed",
                                    "222").is_ok
        assert controller.set_value("Stepper Probe", "full_speed",
                                    "222").is_refused
        assert probe._number("man_full_speed") == 222
        assert probe._number("full_speed") == 400
    finally:
        controller.close()


# -- the Web view: over a real socket ----------------------------------------

def test_the_web_api_commits_manual_speed_while_manual():
    from test_view_web_server import FakeSetup, _get, _post
    from views.web.server import WebView

    probe, port, _pad = make_probe(StepperProbe, levels=LEVELS)
    controller = Controller()
    controller.add("Stepper Probe", probe)
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open()
    try:
        status, served = _get(view, "/api/schema?name=Stepper+Probe")
        assert status == 200
        man = next(e for e in sch.elements(served)
                   if e.get("model_attr") == "man_full_speed_pct")   # the dial, not its steps/s secondary
        assert man["disabled_when"] == ["autonomous"]

        _post(view, "/api/run", {"name": "Stepper Probe", "command": "set_mode",
                                 "inputs": {}, "args": ["manual"]})
        assert probe.is_manual
        status, data = _post(view, "/api/run", {
            "name": "Stepper Probe", "command": "_commit",
            "inputs": {"man_full_speed": "180"}, "args": []})
        assert status == 200 and data["status"] == "ok", data
        assert probe._number("man_full_speed") == 180
        status, data = _post(view, "/api/run", {
            "name": "Stepper Probe", "command": "_commit",
            "inputs": {"full_speed": "180"}, "args": []})
        assert data["status"] == "refused"
        status, data = _post(view, "/api/run", {
            "name": "Stepper Probe", "command": "_commit",
            "inputs": {"man_full_speed": "0"}, "args": []})
        assert data["status"] == "refused"
        assert probe._number("man_full_speed") == 180
    finally:
        view.close()
        controller.close()


# -- the Tk view: the stand-in toolkit ----------------------------------------

def test_the_tk_entry_is_live_in_manual_and_commits(monkeypatch):
    import views.tk as tkmod
    from test_view_tk import (FakeController, FakeDialogs, FakeTkModule,
                              FakeTtkModule, FakeWidget, element_of,
                              widget_of)
    monkeypatch.setattr(tkmod, "tk", FakeTkModule)
    monkeypatch.setattr(tkmod, "ttk", FakeTtkModule)
    dialogs = FakeDialogs()
    monkeypatch.setattr(tkmod, "filedialog", dialogs)
    monkeypatch.setattr(tkmod, "messagebox", dialogs, raising=False)
    monkeypatch.setattr(tkmod, "_LOG_POSITIONS", {}, raising=False)

    probe, port, _pad = make_probe(StepperProbe, levels=LEVELS)
    view = tkmod.TkPanelView(FakeWidget(), FakeController(
        **{"Stepper Probe": probe}), "Stepper Probe")
    try:
        man = element_of(view, "entry", "Manual Speed:")
        auto = element_of(view, "entry", "Autonomous Speed:")
        probe.set_mode("manual")
        view._refresh()
        assert view._widgets[id(man)]["is_enabled"] is True
        assert widget_of(view, man).cget("state") == "normal"
        assert view._widgets[id(auto)]["is_enabled"] is False
        assert widget_of(view, auto).cget("state") == "disabled"

        # The dial is a percent since 2026-10-07: 9 % of 3200 = 288 steps/s.
        view._widgets[id(man)]["var"].set("9")
        assert view._on_entry_commit(man).is_ok
        assert probe._number("man_full_speed") == 288
        assert _speed_of_next_frame(probe, port) == 288.0
    finally:
        view.close()
        probe._stop_threads()
