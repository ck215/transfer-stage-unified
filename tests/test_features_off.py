"""The user profiles are off by default (owner 2026-10-06) until they are
validated; their code is kept whole (branch `feature/sample-map-profiles`)
and `STATION_PROFILES=1` turns them on. The Sample DB was held the same
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
    # Accounts (owner 2026-10-07) are on by default; Guest keeps the defaults.
    got = _probe()
    assert got["sample"] is True and got["profiles"] is True
    assert got["models"][-2:] == ["Transfer Map", "Sample DB"]


def test_the_flags_turn_profiles_on_and_the_sample_map_off():
    got = _probe({"STATION_SAMPLE_MAP": "0", "STATION_PROFILES": "1"})
    assert got["sample"] is False and got["profiles"] is True
    assert "Sample DB" not in got["models"]
    assert got["models"][-1] == "Transfer Map"


def test_only_the_exact_values_flip_a_flag():
    # Accounts (2026-10-07) are on unless STATION_PROFILES is exactly "0".
    got = _probe({"STATION_SAMPLE_MAP": "false", "STATION_PROFILES": "yes"})
    assert got["sample"] is True and got["profiles"] is True
    got = _probe({"STATION_PROFILES": "0"})
    assert got["profiles"] is False


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
    assert titles[0] == "Update" and "Account" not in titles and "Profile" not in titles
    assert not commands & {"sign_in", "sign_out", "add_profile", "remember_settings"}


def test_the_profile_row_returns_when_profiles_are_on(monkeypatch):
    titles, commands = _titles_and_commands(monkeypatch, True)
    # Updated (2026-10-07, the sign-in gate): signing in is on the sign-in
    # screen before Setup; the section keeps the way back to it.
    assert titles[0] == "Account" and "switch_user" in commands


def test_a_model_is_not_given_an_operator_when_profiles_are_off(monkeypatch):
    """Accounts off (STATION_PROFILES=0): a model keeps what it was built with.
    On: the Guest stamps it (guest/guest) until someone signs in."""
    from controller import setup as station_setup
    from test_setup_registry import RecordingController

    class Model:
        NAME = "X"
        operator_id = operator_auth = "station"

        def apply_defaults(self, values):
            return {}

    monkeypatch.setattr(station_setup, "PROFILES_ENABLED", False)
    panel = station_setup.Setup(RecordingController())
    model = Model()
    panel._apply_account(model)
    assert (model.operator_id, model.operator_auth) == ("station", "station")
    monkeypatch.setattr(station_setup, "PROFILES_ENABLED", True)
    panel._apply_account(model)
    assert model.operator_auth == "guest"
