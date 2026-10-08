"""The user profiles are off by default (owner 2026-10-06) until they are
validated; their code is kept whole (branch `feature/sample-map-profiles`)
and `STATION_PROFILES=1` turns them on. The Sample Map was held the same
way; since the merge of the lab's stage into the 2026-10-07 round it is ON
by default (the approved procedure picks every trial's flake from it) and
`STATION_SAMPLE_MAP=0` turns it off: an owner call flagged to the lead.
These pin the defaults and the flags."""
import os
import subprocess
import sys

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _probe(env_extra=None):
    """Import Setup in a fresh interpreter (the registry is built at import)
    and report what a station would show."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("STATION_SAMPLE_MAP", "STATION_PROFILES")}
    env.update(env_extra or {})
    env["PYTHONPATH"] = SRC + os.pathsep + os.path.join(SRC, "model")
    code = ("import json; from controller import setup as s; "
            "print(json.dumps({'models': list(s.MODEL_TYPES), "
            "'sample': s.SAMPLE_MAP_ENABLED, 'profiles': s.PROFILES_ENABLED}))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, cwd=os.path.dirname(SRC), timeout=60)
    assert out.returncode == 0, out.stderr
    import json
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_by_default_the_sample_map_is_on_and_there_are_no_profiles():
    got = _probe()
    assert got["sample"] is True and got["profiles"] is False
    assert got["models"][-2:] == ["Transfer Map", "Sample Map"]


def test_the_flags_turn_profiles_on_and_the_sample_map_off():
    got = _probe({"STATION_SAMPLE_MAP": "0", "STATION_PROFILES": "1"})
    assert got["sample"] is False and got["profiles"] is True
    assert "Sample Map" not in got["models"]
    assert got["models"][-1] == "Transfer Map"


def test_only_the_exact_values_flip_a_flag():
    got = _probe({"STATION_SAMPLE_MAP": "false", "STATION_PROFILES": "yes"})
    assert got["sample"] is True and got["profiles"] is False


def _titles_and_commands(monkeypatch, on):
    from controller import setup as station_setup
    from test_setup_registry import RecordingController
    monkeypatch.setattr(station_setup, "PROFILES_ENABLED", on)
    panel = station_setup.Setup(RecordingController())
    import schema as sch
    titles = [section["title"] for section in panel.schema["sections"]]
    commands = {e.get("command") for e in sch.elements(panel.schema)}
    return titles, commands


def test_setup_has_no_profile_row_or_sign_in_when_profiles_are_off(monkeypatch):
    titles, commands = _titles_and_commands(monkeypatch, False)
    assert titles[0] == "Update" and "Profile" not in titles
    assert not commands & {"sign_in", "sign_out", "add_profile", "remember_settings"}


def test_the_profile_row_returns_when_profiles_are_on(monkeypatch):
    titles, commands = _titles_and_commands(monkeypatch, True)
    assert titles[0] == "Profile" and "sign_in" in commands


def test_a_model_is_not_given_an_operator_when_profiles_are_off(monkeypatch):
    from controller import setup as station_setup

    class Model:
        NAME = "X"
        operator_id = operator_auth = "station"

    class Fake:
        profiles = type("P", (), {"current_user": "someone", "auth": "offline-unverified",
                                  "effective_model_params": lambda self: ({}, {})})()
        NAME = "Setup"
    monkeypatch.setattr(station_setup, "PROFILES_ENABLED", False)
    model = Model()
    station_setup.Setup._apply_profile(Fake(), model)
    assert (model.operator_id, model.operator_auth) == ("station", "station")
    monkeypatch.setattr(station_setup, "PROFILES_ENABLED", True)
    station_setup.Setup._apply_profile(Fake(), model)
    assert (model.operator_id, model.operator_auth) == ("someone", "offline-unverified")
