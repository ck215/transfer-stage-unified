"""User-system Phase 1: local profiles (proposal-user-system.md sections 2-3),
as they stand since the accounts (owner request 2026-10-07): the station
scope, the merge and the Q4 split stay here; who is signed in is the User
model's (`tests/test_user.py`, `tests/test_user_store.py`).

Owner answers of 2026-10-04: sign in by name (+ PIN), `offline-unverified`
with no lab server, no PIN hash cached on a station (Q1) - superseded by the
accounts of 2026-10-07 (scrypt-hashed passwords in `users.sqlite`); the JSON
files below still never hold a credential. The user/station split of Q4
stands: users may keep step sizes, manual and autonomous speeds and the
rotator step; heater PID and offset, Red Percent's red_min and the
um-per-count table are station-only; the brake fields are never a
preference. Local files only; no network.
"""
import json

import pytest

from events import events
from model import profile as pf
from panel import Panel
from param import Param

PARAMS = {
    "Stepper Probe": {n: Param(n, "int", default=d, minimum=1, maximum=3200)
                      for n, d in (("x_step", 4), ("man_full_speed", 300),
                                   ("full_speed", 400), ("slow_speed", 50),
                                   ("brake_distance", 10))},
    "Temperature Controller": {"p_term": Param("p_term", "float", default=1.0, minimum=0),
                               "setpoint": Param("setpoint", "float", default=25.0)},
    "Red Percent": {"red_min": Param("red_min", "int", default=150, minimum=0, maximum=255)},
    "Sample DB": {"um_per_count": Param("um_per_count", "text", default="")},
    "Rotator": {"step_deg": Param("step_deg", "float", default=1.0, minimum=0.01)},
}


def params_of(name):
    return PARAMS.get(name, {})


@pytest.fixture
def root(tmp_path):
    return tmp_path / "profiles"


@pytest.fixture
def service(root):
    return pf.ProfileService(pf.LocalFilesSource(root), params_of)


# -- the merge (pure) -----------------------------------------------------------------

def test_later_scopes_win_absent_keys_inherit_and_provenance_names_the_scope():
    effective, provenance = pf.merge([
        ("lab", {"Stepper Probe": {"x_step": 4, "man_full_speed": 300}}),
        ("station", {"Stepper Probe": {"man_full_speed": 250}}),
        ("user", {"Stepper Probe": {"x_step": 8}, "Rotator": {"step_deg": 0.5}}),
    ])
    assert effective == {"Stepper Probe": {"x_step": 8, "man_full_speed": 250},
                         "Rotator": {"step_deg": 0.5}}
    assert provenance == {"Stepper Probe.x_step": "user",
                          "Stepper Probe.man_full_speed": "station",
                          "Rotator.step_deg": "user"}


def test_lists_replace_and_null_is_not_a_value():
    effective, _ = pf.merge([("lab", {"a": [1, 2]}), ("user", {"a": [3]})])
    assert effective == {"a": [3]}
    with pytest.raises(pf.ProfileError, match="null"):
        pf.merge([("lab", {"a": 1}), ("user", {"a": None})])


# -- the Q4 split -----------------------------------------------------------------------------

def test_the_q4_lists():
    assert {"x_step", "y_step", "z_step", "x_dist", "y_dist", "z_dist",
            "full_speed", "man_full_speed", "step_deg"} == set(pf.USER_PARAMS)
    assert {"p_term", "i_term", "d_term", "offset", "red_min",
            "um_per_count"} == set(pf.STATION_PARAMS)
    assert {"slow_speed", "brake_distance"} == set(pf.NEVER_PARAMS)


def test_a_user_document_keeps_only_user_params_and_says_why():
    clean, problems = pf.validate_model_params("user", {
        "Stepper Probe": {"x_step": 8, "slow_speed": 10},
        "Temperature Controller": {"p_term": 2.0, "setpoint": 30.0},
        "Red Percent": {"red_min": 120}}, params_of)
    assert clean == {"Stepper Probe": {"x_step": 8}}
    assert any("slow_speed" in p and "never" in p for p in problems), problems
    assert any("p_term" in p and "station" in p for p in problems), problems
    assert any("setpoint" in p and "not a preference" in p for p in problems), problems
    assert any("red_min" in p and "station" in p for p in problems), problems


def test_a_station_document_takes_station_and_user_params_but_never_the_brakes():
    clean, problems = pf.validate_model_params("station", {
        "Stepper Probe": {"man_full_speed": 250, "brake_distance": 5},
        "Temperature Controller": {"p_term": 2.0},
        "Sample DB": {"um_per_count": "0.4"}}, params_of)
    assert clean == {"Stepper Probe": {"man_full_speed": 250},
                     "Temperature Controller": {"p_term": 2.0},
                     "Sample DB": {"um_per_count": "0.4"}}
    assert any("brake_distance" in p for p in problems)


def test_the_sample_maps_old_name_is_read_as_the_sample_db():
    """Owner 2026-10-07: "Sample Map" is shown as "Sample DB". A document
    (station.json, users.sqlite preferences) saved under the old name keeps
    working, written back under the new one; with both, the new one wins
    whatever order the document lists them in."""
    assert pf.RENAMED_MODELS["Sample Map"] == "Sample DB"
    clean, problems = pf.validate_model_params("station", {
        "Sample Map": {"um_per_count": "0.4"}}, params_of)
    assert (clean, problems) == ({"Sample DB": {"um_per_count": "0.4"}}, [])
    for body in ({"Sample DB": {"um_per_count": "0.9"}, "Sample Map": {"um_per_count": "0.4"}},
                 {"Sample Map": {"um_per_count": "0.4"}, "Sample DB": {"um_per_count": "0.9"}}):
        clean, _ = pf.validate_model_params("station", body, params_of)
        assert clean == {"Sample DB": {"um_per_count": "0.9"}}


def test_a_value_out_of_its_params_bounds_is_dropped_and_named():
    clean, problems = pf.validate_model_params("user", {
        "Stepper Probe": {"man_full_speed": 9999}}, params_of)
    assert clean == {}
    assert any("man_full_speed" in p and "at most 3200" in p for p in problems)


# -- the local files ------------------------------------------------------------------------

def test_reading_creates_nothing(root):
    source = pf.LocalFilesSource(root)
    assert source.users() == [] and source.documents("station", "") == {}
    assert not root.exists()


def test_usernames_are_safe_file_names(root):
    source = pf.LocalFilesSource(root)
    for bad in ("../x", "a b", "", "x/y", ".hidden"):
        with pytest.raises(pf.ProfileError):
            source.add_user(bad, "X")
    with pytest.raises(pf.ProfileError, match="already"):
        source.add_user("ialbinog", "Ian")
        source.add_user("ialbinog", "Ian again")


def test_no_pin_or_hash_is_ever_written(root):
    """Q1: no PIN hashes cached on stations."""
    source = pf.LocalFilesSource(root)
    source.add_user("ialbinog", "Ian")
    with pytest.raises(pf.ProfileError, match="PIN"):
        source.put_user_record({"username": "x1", "display_name": "X", "pin_hash": "abc"})
    root.joinpath("users.json").write_text(json.dumps(
        {"users": [{"username": "ialbinog", "display_name": "Ian", "pin": "1234"}]}))
    assert source.users() == [{"username": "ialbinog", "display_name": "Ian"}]
    text = "".join(p.read_text() for p in root.rglob("*.json"))
    assert "pin_hash" not in text


# -- the service: the station scope and the merge (accounts, 2026-10-07) -----------------
#
# Who is signed in moved to the accounts (`model.user`, `model.user_store`):
# Phase 1's sign-in by name (offline-unverified, Q1) is superseded by the
# owner's request of 2026-10-07, and its tests by `tests/test_user.py` and
# `tests/test_setup_profile.py`. The service keeps the station's defaults
# and layers a user's config over them.

def test_the_service_holds_no_session(service):
    """A signed-in person is the User model's; the service only merges."""
    for gone in ("sign_in", "sign_out", "current_user", "remember", "add_profile"):
        assert not hasattr(service, gone), gone


def test_effective_model_params_layer_a_users_config_over_the_station(service):
    service.save_station({"Stepper Probe": {"man_full_speed": 250},
                          "Red Percent": {"red_min": 140}})
    mine = {"Stepper Probe": {"x_step": 8, "man_full_speed": 280}}
    effective, provenance = service.effective_model_params(mine, "ian@uci.edu")
    assert effective["Stepper Probe"] == {"x_step": 8, "man_full_speed": 280}
    assert effective["Red Percent"] == {"red_min": 140}
    assert provenance["Stepper Probe.x_step"] == "user"
    assert provenance["Red Percent.red_min"] == "station"
    effective, _ = service.effective_model_params()
    assert effective["Stepper Probe"] == {"man_full_speed": 250}


def test_a_users_config_never_sets_a_station_only_or_brake_value(service):
    service.save_station({"Temperature Controller": {"p_term": 1.5}})
    effective, provenance = service.effective_model_params(
        {"Temperature Controller": {"p_term": 9.0},
         "Stepper Probe": {"slow_speed": 10, "x_step": 3}}, "ian@uci.edu")
    assert effective["Temperature Controller"] == {"p_term": 1.5}
    assert effective["Stepper Probe"] == {"x_step": 3}
    assert provenance["Temperature Controller.p_term"] == "station"


def test_a_hand_edited_bad_value_falls_back_and_warns_once(service):
    service.save_station({"Stepper Probe": {"man_full_speed": 250}})
    bad = {"Stepper Probe": {"man_full_speed": 99999}}
    events.forget("Profile Value Ignored")
    since = events.latest_id
    effective, provenance = service.effective_model_params(bad, "ialbinog")
    assert effective["Stepper Probe"]["man_full_speed"] == 250
    assert provenance["Stepper Probe.man_full_speed"] == "station"
    service.effective_model_params(bad, "ialbinog")
    warned = [e for e in events.since(since) if e.title == "Profile Value Ignored"]
    assert len(warned) == 1 and "man_full_speed" in warned[0].message
    assert "ialbinog" in warned[0].message


# -- apply_defaults' inverse, and the Params' own defaults ---------------------------------

class Probe(Panel):
    NAME = "Stepper Probe"
    PARAMS = {"x_step": Param("x_step", "int", default=4, minimum=1, maximum=3200),
              "man_full_speed": Param("man_full_speed", "int", default=300, minimum=1),
              "slow_speed": Param("slow_speed", "int", default=50, minimum=1),
              "note": Param("note", "text", default=""),
              "reading": Param("reading", "float", default=0.0)}

    @property
    def reading(self):              # a derived readout: declared, never stored
        return 1.5

    @property
    def schema(self):
        return {"version": 2, "sections": []}


def test_current_params_is_the_inverse_of_apply_defaults():
    probe = Probe()
    probe.x_step, probe.slow_speed = 9, 33
    assert pf.current_params(probe) == {"x_step": 9, "man_full_speed": 300,
                                        "slow_speed": 33}      # blank and read-only left out
    assert pf.current_params(probe, pf.USER_PARAMS) == {"x_step": 9, "man_full_speed": 300}
    other = Probe()
    assert other.apply_defaults(pf.current_params(probe)) == {}
    assert pf.current_params(other) == pf.current_params(probe)


def test_param_defaults_rebuild_what_the_code_declares():
    probe = Probe()
    probe.x_step, probe.man_full_speed = 9, 280
    assert pf.param_defaults(probe, pf.USER_PARAMS) == {"x_step": 4, "man_full_speed": 300}
    assert probe.apply_defaults(pf.param_defaults(probe, pf.USER_PARAMS)) == {}
    assert (probe.x_step, probe.man_full_speed) == (4, 300)
    assert "reading" not in pf.param_defaults(probe)


def test_the_profiles_root_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_PROFILES_DIR", str(tmp_path / "p"))
    assert pf.profiles_root() == tmp_path / "p"
    monkeypatch.delenv("STATION_PROFILES_DIR")
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path / "data"))
    assert pf.profiles_root() == tmp_path / "data" / "profiles"
