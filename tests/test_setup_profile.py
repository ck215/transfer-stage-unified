"""The Profile row on Setup (user-system Phase 1, section 2.4).

Setup is the composition root: it builds the ProfileService over local
files, applies the effective model parameters (Q4 split) to every model it
builds and, on sign-in, to the models already open, and tells the Transfer
Map and the Sample Map who is working (`operator_id`, `owner`) and how that
was established (`offline-unverified` until a lab server checks a PIN).
"""
import json
import sqlite3

import pytest

pytestmark = pytest.mark.usefixtures("profiles_on", "sample_map_on")   # the held features, on

import schema as sch
from controller.setup import Setup
from events import events
from model import profile as pf
from panel import Panel
from param import Param
from test_setup import RecordingController


CONFIGS = [{"model": "Stepper Probe", "port": "SIM", "gamepad": "None", "sim": True},
           {"model": "Transfer Map", "port": "On", "sim": False},
           {"model": "Sample Map", "port": "On", "sim": False}]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("STATION_PROFILES_DIR", str(tmp_path / "profiles"))
    panel = Setup(RecordingController())
    yield panel
    panel.controller.reset()


def _elements(panel, title):
    section = next(s for s in panel.schema["sections"] if s["title"] == title)
    return section["elements"]


def test_the_profile_row_comes_first_and_shows_no_pin_box_yet(setup):
    """Q1 asks for a PIN, but with no lab server nothing can check one and
    no hash may be cached: Phase 1 signs in by name, offline-unverified, and
    the PIN box arrives with the server (and a masked entry type)."""
    assert setup.schema["sections"][0]["title"] == "Profile"
    elements = _elements(setup, "Profile")
    commands = {e.get("command") for e in elements}
    assert {"set_profile_user", "sign_in", "sign_out", "remember_settings",
            "save_station_settings", "add_profile"} <= commands
    assert not [e for e in sch.elements(setup.schema)
                if "pin" in str(e.get("model_attr", "")).lower()]
    assert setup.profile_options() == ["Station"]
    assert "Station profile" in setup.profile_status


def test_a_profile_is_added_and_signed_in_by_name(setup):
    assert setup.run("add_profile", {"profile_new_name": "ialbinog"}).is_ok
    assert setup.profile_options() == ["Station", "ialbinog"]
    assert setup.run("set_profile_user", None, ("ialbinog",)).is_ok
    assert setup.run("sign_in").is_ok
    assert setup.profiles.current_user == "ialbinog"
    assert "offline" in setup.profile_status and "unverified" in setup.profile_status
    assert setup.run("sign_out").is_ok
    assert setup.profiles.current_user == "station"
    assert setup.run("add_profile", {"profile_new_name": "../x"}).is_refused


def test_built_models_carry_the_operator_and_how_it_was_established(setup, tmp_path):
    setup.run("add_profile", {"profile_new_name": "ialbinog"})
    setup.build(CONFIGS)
    tmap = setup.controller._models["Transfer Map"]
    smap = setup.controller._models["Sample Map"]
    assert (tmap.operator_id, tmap.operator_auth) == ("station", "station")
    assert (smap.owner, smap.owner_auth) == ("station", "station")
    setup.run("set_profile_user", None, ("ialbinog",))
    setup.run("sign_in")
    assert (tmap.operator_id, tmap.operator_auth) == ("ialbinog", "offline-unverified")
    assert (smap.owner, smap.owner_auth) == ("ialbinog", "offline-unverified")
    smap.run("save_sample", {"sample_id": "S1"})
    smap.run("set_source", None, ("Typed readings",))
    assert smap.run("flag_flake", {"reading_x_mm": "1", "reading_y_mm": "2"}).is_ok
    flake = smap._store.flakes()[0]
    assert (flake["owner"], flake["owner_auth"]) == ("ialbinog", "offline-unverified")


def test_station_and_user_params_reach_the_models_q4(setup):
    setup.run("add_profile", {"profile_new_name": "ialbinog"})
    setup.profiles.save_station({"Stepper Probe": {"man_full_speed": 250},
                                 "Red Percent": {"red_min": 140}})
    setup.build(CONFIGS)
    probe = setup.controller._models["Stepper Probe"]
    assert int(probe.man_full_speed) == 250                  # station default at build
    setup.run("set_profile_user", None, ("ialbinog",))
    setup.run("sign_in")
    setup.profiles.remember({"Stepper Probe": {"x_step": 9}})
    setup.run("sign_in")                                     # re-applies live
    assert int(probe.x_step) == 9 and int(probe.man_full_speed) == 250


def test_remember_my_settings_saves_only_user_params(setup, tmp_path):
    setup.run("add_profile", {"profile_new_name": "ialbinog"})
    setup.build(CONFIGS)
    assert "Sign in" in setup.run("remember_settings").reason
    setup.run("set_profile_user", None, ("ialbinog",))
    setup.run("sign_in")
    probe = setup.controller._models["Stepper Probe"]
    probe.x_step = 7
    probe.slow_speed = 33                                    # a brake field: never
    assert setup.run("remember_settings").is_ok
    saved = json.loads((tmp_path / "profiles" / "users" / "ialbinog.json").read_text())
    stepper = saved["model_params"]["Stepper Probe"]
    assert stepper["x_step"] == 7
    assert "slow_speed" not in stepper and "brake_distance" not in stepper
    assert "Red Percent" not in saved["model_params"] or \
        "red_min" not in saved["model_params"]["Red Percent"]


def test_save_station_settings_asks_and_keeps_the_brakes_out(setup, tmp_path):
    setup.build(CONFIGS)
    smap = setup.controller._models["Sample Map"]
    smap.um_per_count = "0.4"
    assert setup.run("save_station_settings").needs_confirm
    assert setup.run("save_station_settings", None, (True,)).is_ok
    saved = json.loads((tmp_path / "profiles" / "station.json").read_text())
    assert saved["model_params"]["Sample Map"] == {"um_per_count": "0.4"}
    assert "slow_speed" not in saved["model_params"].get("Stepper Probe", {})


# -- the core hooks (lead): apply_defaults and secret inputs ------------------------

class Little(Panel):
    NAME = "Little"
    PARAMS = {"speed": Param("speed", "int", default=5, minimum=1, maximum=10),
              "word": Param("word", "text", default="")}
    SECRET_INPUTS = frozenset({"word"})

    @property
    def schema(self):
        return sch.schema(sch.section("S", sch.entry("Speed", "speed", self.PARAMS["speed"]),
                                      sch.entry("Word", "word", self.PARAMS["word"]),
                                      sch.button("Go", "go", inputs=("speed", "word"))))

    def go(self):
        return self.speed


def test_apply_defaults_validates_like_run_and_reports_what_it_refused():
    little = Little()
    refused = little.apply_defaults({"speed": 7, "nope": 1})
    assert little.speed == 7 and refused == {"nope": "not a parameter of Little"}
    refused = little.apply_defaults({"speed": 99})
    assert little.speed == 7 and "at most 10" in refused["speed"]


def test_a_secret_input_never_reaches_the_log(tmp_path):
    lines = []
    events.subscribe(lambda e: lines.append(e))
    little = Little()
    import logging
    seen = []
    original = events.debug

    def capture(title, message, **kwargs):
        seen.append(message)
        return original(title, message, **kwargs)

    events.debug = capture
    try:
        assert little.run("go", {"speed": "3", "word": "4821"}).is_ok
    finally:
        events.debug = original
    assert seen and all("4821" not in m for m in seen), seen
    assert any("<redacted>" in m for m in seen)
