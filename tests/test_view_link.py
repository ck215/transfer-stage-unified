"""rb-link-views: what every view says about a model's serial link, decided
once in `views.base` from the state alone (the rb-link contract):

    state["link"] = {"status": "verified" | "unverified" | "simulated" |
                               "lost" | "reconnecting" | "closed" | "connecting",
                     "losses": int, "reconnects": int, "dropped": int,
                     "stalls": int, "stalled": bool, "last_loss": "HH:MM:SS" | None}

absent for a model without a port. The toolkit halves are the recording
double in test_core_fakes; Tk, Qt and the Web draw the same words in their
own tests.
"""
import pytest

import schema as sch
from result import Result
from views import base as view_base

from test_core_fakes import FakePanelView


def _link(**overrides):
    link = {"status": "verified", "losses": 0, "reconnects": 0, "dropped": 0,
            "stalls": 0, "stalled": False, "last_loss": None}
    link.update(overrides)
    return link


class LinkedStation:
    """The Controller surface a PanelView touches, for one linked model."""

    def __init__(self):
        self.link = _link()
        self.values = {"position_x": "12", "position_age": "0.1",
                       "is_auto": False, "is_estopped": False}
        self.devices = {"SerialPort": "verified", "Gamepad": "bound"}

    def schema(self, _name):
        return sch.schema(
            sch.section("Position", sch.readonly("X:", "position_x", rail=True)),
            sch.section(
                "System Control",
                sch.toggle("Autonomous:", "is_auto", "set_mode", "On", "Off",
                           on_args=["auto"], off_args=["disabled"]),
                sch.button("Step", "step", role="go"),
                sch.button("Zero", "zero"),
            ),
            sch.section(
                "Diagnostics",
                sch.readonly("Position age (s):", "position_age", role="info"),
                tier=3, disclosure="Diagnostics"),
            sch.section(
                "Safety",
                sch.toggle("Stop", "is_estopped", "toggle_estop", "Stopped",
                           "Stop this model", on_role="danger", off_role="danger"),
                tier=3, disclosure="Diagnostics"),
        )

    def state(self, _name=None):
        state = {"name": "Probe", "mode": "idle", "values": dict(self.values),
                 "age": 0.1, "devices": dict(self.devices)}
        if self.link is not None:
            state["link"] = dict(self.link)
        return state

    def run(self, name, command, inputs=None, args=()):
        return Result(Result.OK)

    def options(self, _name, _command):
        return []


@pytest.fixture
def station():
    return LinkedStation()


@pytest.fixture
def view(station):
    built = FakePanelView(station, "Probe")
    built._build()
    return built


# -- the words -----------------------------------------------------------------

@pytest.mark.parametrize("link, words", [
    (_link(status="reconnecting", last_loss="12:41:07"),
     ("error", "Link lost 12:41:07, reconnecting…")),
    (_link(status="lost", last_loss="12:41:07"),
     ("error", "Link lost 12:41:07; not reconnecting")),
    (_link(status="reconnecting"), ("error", "Link lost, reconnecting…")),
    (_link(stalled=True), ("warning", "No position for 7 s; link up, check the board")),
    (_link(status="simulated", stalled=True),
     ("warning", "No position for 7 s; link up, check the board")),
    # Stalled means nothing once the link itself is down or closed: the
    # down link is the news.
    (_link(status="lost", stalled=True, last_loss="09:00:00"),
     ("error", "Link lost 09:00:00; not reconnecting")),
    (_link(status="closed", stalled=True), ("", "")),
    (_link(), ("", "")),
    (None, ("", "")),
])
def test_v1_the_link_is_said_in_operator_words(link, words):
    state = {"values": {"position_age": "7.2"}, "age": 0.1}
    if link is not None:
        state["link"] = link
    assert view_base.link_notice(state) == words


def test_v1_a_stall_with_no_position_age_falls_back_to_the_models_age():
    state = {"values": {}, "age": 3.6, "link": _link(stalled=True)}
    assert view_base.link_notice(state)[1] == "No position for 4 s; link up, check the board"
    state = {"values": {}, "age": None, "link": _link(stalled=True)}
    assert view_base.link_notice(state)[1] == "No position; link up, check the board"


def test_v1_a_down_link_holds_modes_and_go_commands_never_the_stop():
    toggle = sch.toggle("Autonomous:", "is_auto", "set_mode", "On", "Off")
    stop = sch.toggle("Stop", "is_estopped", "toggle_estop", "Stopped", "Stop",
                      on_role="danger", off_role="danger")
    assert view_base.link_holds(toggle)
    assert view_base.link_holds(sch.button("Step", "step", role="go"))
    assert not view_base.link_holds(stop)
    assert not view_base.link_holds(sch.button("Zero", "zero"))
    assert not view_base.link_holds(sch.button("Halt", "halt", role="go", stop=True))


# -- the panel: one implementation, driven by state ----------------------------

def test_v1_a_reconnecting_link_greys_the_modes_mutes_the_numbers_and_says_so(
        view, station):
    step, auto = "Step", "is_auto"
    assert view.enabled[auto] is True and view.enabled[step] is True
    assert view.stale is False and view.notices == []

    station.link = _link(status="reconnecting", losses=1, last_loss="12:41:07")
    station.devices["SerialPort"] = "reconnecting"
    view._refresh()
    assert view.link_down is True
    assert view.stale is True, "the readouts wear the stale mark"
    assert view.enabled[auto] is False and view.enabled[step] is False
    assert view.enabled["is_estopped"] is True, "the stop is never held"
    assert view.enabled["Zero"] is True, "a neutral command is not a mode"
    assert view.notices == [("error", "Link lost 12:41:07, reconnecting…")]
    element = next(e for e in view._elements if e.get("model_attr") == auto)
    assert view._gate_words(element) == "Link lost: wait for it to reconnect"

    station.link = _link(status="verified", losses=1, reconnects=1,
                         last_loss="12:41:07")
    station.devices["SerialPort"] = "verified"
    view._refresh()
    assert view.link_down is False and view.stale is False
    assert view.enabled[auto] is True and view.enabled[step] is True
    assert view.notices == []


def test_v1_a_stalled_link_is_a_warning_and_leaves_the_controls_live(view, station):
    station.link = _link(stalled=True, stalls=1)
    station.values["position_age"] = "5.0"
    view._refresh()
    assert view.notices == [("warning", "No position for 5 s; link up, check the board")]
    assert view.enabled["is_auto"] is True and view.stale is False


def test_v1_a_model_without_a_link_is_untouched(view, station):
    station.link = None
    view._refresh()
    assert view.notices == [] and view.link_down is False
    assert view.enabled["is_auto"] is True
