"""User-system Phase 1: local profiles (proposal-user-system.md sections 2-3).

Owner answers of 2026-10-04: sign in by name (+ PIN); with no lab server a
session is `offline-unverified`; no PIN hash is ever cached on a station
(Q1). The user/station split of Q4: users may keep step sizes, manual and
autonomous speeds and the rotator step; heater PID and offset, Red
Percent's red_min and the um-per-count table are station-only; the brake
fields are never a preference. Local JSON files only; no network.
"""
import json

import pytest

from events import events
from model import profile as pf
from param import Param

PARAMS = {
    "Stepper Probe": {n: Param(n, "int", default=d, minimum=1, maximum=3200)
                      for n, d in (("x_step", 4), ("man_full_speed", 300),
                                   ("full_speed", 400), ("slow_speed", 50),
                                   ("brake_distance", 10))},
    "Temperature Controller": {"p_term": Param("p_term", "float", default=1.0, minimum=0),
                               "setpoint": Param("setpoint", "float", default=25.0)},
    "Red Percent": {"red_min": Param("red_min", "int", default=150, minimum=0, maximum=255)},
    "Sample Map": {"um_per_count": Param("um_per_count", "text", default="")},
    "Rotator": {"step_deg": Param("step_deg", "float", default=1.0, minimum=0.01)},
}


def params_of(name):
    return PARAMS.get(name, {})


@pytest.fixture
def root(tmp_path):
    return tmp_path / "profiles"


@pytest.fixture
def service(root):
    source = pf.LocalFilesSource(root)
    source.add_user("ialbinog", "Ian")
    source.add_user("trainee1", "A trainee")
    return pf.ProfileService(source, params_of)


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
        "Sample Map": {"um_per_count": "0.4"}}, params_of)
    assert clean == {"Stepper Probe": {"man_full_speed": 250},
                     "Temperature Controller": {"p_term": 2.0},
                     "Sample Map": {"um_per_count": "0.4"}}
    assert any("brake_distance" in p for p in problems)


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


# -- the service ---------------------------------------------------------------------------

def test_nobody_signed_in_is_the_station_profile(service):
    assert service.current_user == "station" and service.auth == "station"
    assert service.users[0] == {"username": "station", "display_name": "Station"}
    assert [u["username"] for u in service.users[1:]] == ["ialbinog", "trainee1"]


def test_sign_in_is_offline_unverified_and_the_pin_goes_nowhere(service, root):
    since = events.latest_id
    session = service.sign_in("ialbinog", pin="4821")
    assert session["username"] == "ialbinog"
    assert session["auth"] == service.auth == "offline-unverified"
    assert service.current_user == "ialbinog"
    assert "4821" not in json.dumps(session)
    assert all("4821" not in (e.message or "") for e in events.since(since))
    assert all("4821" not in p.read_text() for p in root.rglob("*") if p.is_file())
    assert "unverified" in service.status
    service.sign_out()
    assert service.current_user == "station"


def test_an_unknown_name_is_refused(service):
    with pytest.raises(pf.ProfileError, match="No profile"):
        service.sign_in("nobody")


def test_effective_model_params_follow_the_signed_in_user(service):
    service.save_station({"Stepper Probe": {"man_full_speed": 250},
                          "Red Percent": {"red_min": 140}})
    service.sign_in("ialbinog")
    service.remember({"Stepper Probe": {"x_step": 8, "man_full_speed": 280}})
    effective, provenance = service.effective_model_params()
    assert effective["Stepper Probe"] == {"x_step": 8, "man_full_speed": 280}
    assert effective["Red Percent"] == {"red_min": 140}
    assert provenance["Stepper Probe.x_step"] == "user"
    assert provenance["Red Percent.red_min"] == "station"
    service.sign_out()
    effective, _ = service.effective_model_params()
    assert effective["Stepper Probe"] == {"man_full_speed": 250}


def test_remember_refuses_station_only_params_and_the_station_profile(service):
    with pytest.raises(pf.ProfileError, match="Sign in"):
        service.remember({"Stepper Probe": {"x_step": 8}})
    service.sign_in("trainee1")
    with pytest.raises(pf.ProfileError, match="station"):
        service.remember({"Red Percent": {"red_min": 120}})


def test_a_hand_edited_bad_value_falls_back_and_warns_once(service, root):
    service.save_station({"Stepper Probe": {"man_full_speed": 250}})
    (root / "users").mkdir(parents=True, exist_ok=True)
    (root / "users" / "ialbinog.json").write_text(json.dumps(
        {"model_params": {"Stepper Probe": {"man_full_speed": 99999}}}))
    service.sign_in("ialbinog")
    events.forget("Profile Value Ignored")
    since = events.latest_id
    effective, provenance = service.effective_model_params()
    assert effective["Stepper Probe"]["man_full_speed"] == 250
    assert provenance["Stepper Probe.man_full_speed"] == "station"
    service.effective_model_params()
    warned = [e for e in events.since(since) if e.title == "Profile Value Ignored"]
    assert len(warned) == 1 and "man_full_speed" in warned[0].message


def test_the_profiles_root_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_PROFILES_DIR", str(tmp_path / "p"))
    assert pf.profiles_root() == tmp_path / "p"
    monkeypatch.delenv("STATION_PROFILES_DIR")
    monkeypatch.setenv("TRANSFER_STAGE_DATA_ROOT", str(tmp_path / "data"))
    assert pf.profiles_root() == tmp_path / "data" / "profiles"
