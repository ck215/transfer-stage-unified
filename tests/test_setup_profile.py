"""The Account section of Setup (owner request 2026-10-07, AC-3), which
replaced the Phase 1 Profile row.

Setup is the composition root: it opens the accounts file
(`users.sqlite`), migrates the Phase 1 name-only profiles into it, keeps the
session (the signed-in `User`, a Guest by default), applies the station's
defaults and the user's config to every model it builds and, on sign-in, to
the models already open, reverts them on sign-out, and keeps the signed-in
user's sheet (the `User` page) in the Controller. Everything here goes
through `run()`, the way a view drives it, and the password never reaches
the log, an event, `state` or a result.

The section is still titled "Profile" (`Setup.ACCOUNT_SECTION`): two tests
outside this item's write set pin the title (see the handoff).
"""
import json
import sqlite3

import pytest

import schema as sch
from controller.setup import Setup
from events import events
from model import profile as pf
from model.user_store import UserStore
from panel import Panel
from param import Param
from test_setup import RecordingController


CONFIGS = [{"model": "Stepper Probe", "port": "SIM", "gamepad": "None", "sim": True},
           {"model": "Transfer Map", "port": "On", "sim": False},
           {"model": "Sample Map", "port": "On", "sim": False}]
PASSWORD = "correct-horse-4821"
EMAIL = "ian@uci.edu"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STATION_PROFILES_DIR", str(tmp_path / "profiles"))
    return tmp_path / "profiles"


@pytest.fixture
def setup(root):
    panel = Setup(RecordingController())
    yield panel
    panel.controller.reset()


@pytest.fixture
def heard():
    """Every event and every debug line published while the test runs."""
    lines = []
    original = events.debug

    def capture(title, message, **kwargs):
        lines.append(f"{title} {message}")
        return original(title, message, **kwargs)

    since = events.latest_id
    events.debug = capture
    try:
        yield lambda: lines + [f"{e.title} {e.message}" for e in events.since(since)]
    finally:
        events.debug = original


def _section(panel):
    return next(s for s in panel.schema["sections"] if s["title"] == Setup.ACCOUNT_SECTION)


def create(panel, email=EMAIL, password=PASSWORD):
    asked = panel.run("create_account", {"account_email": email,
                                         "account_password": password})
    assert asked.needs_confirm, asked.reason
    done = panel.run(asked.command, asked.inputs, (*asked.args, True))
    assert done.is_ok, done.reason
    return done


def sign_in(panel, email=EMAIL, password=PASSWORD):
    return panel.run("sign_in", {"account_email": email, "account_password": password})


def models(panel):
    return panel.controller.models


# -- the section --------------------------------------------------------------------------

def test_the_account_section_comes_first_with_email_password_and_four_commands(setup):
    assert setup.schema["sections"][0]["title"] == Setup.ACCOUNT_SECTION == "Profile"
    elements = _section(setup)["elements"]
    by_attr = {e.get("model_attr"): e for e in elements if e.get("model_attr")}
    assert by_attr["account_email"]["type"] == "entry"
    password = by_attr["account_password"]
    assert password["type"] == "entry" and password.get("secret") is True
    assert "account_password" in Setup.SECRET_INPUTS
    commands = {e.get("command"): e for e in elements if e.get("command")}
    assert {"sign_in", "open_as_guest", "create_account", "sign_out",
            "save_station_settings"} <= set(commands)
    assert commands["sign_in"]["inputs"] == ["account_email", "account_password"]
    assert commands["create_account"]["inputs"] == ["account_email", "account_password"]
    assert commands["create_account"]["text"] == "Create account…"
    assert by_attr["account_status"]["type"] == "readonly"
    assert not [e for e in elements if e["type"] == "dropdown"], "no profile picker"
    for gone in ("set_profile_user", "add_profile", "remember_settings"):
        assert gone not in commands


def test_a_new_station_is_a_guest_and_launching_as_guest_changes_nothing(setup):
    assert setup.account_status == "Guest (station defaults)"
    assert setup.user.is_guest
    setup.build(CONFIGS)
    assert "User" not in setup.controller.model_names, "a Guest has no sheet"
    probe = models(setup)["Stepper Probe"]
    assert int(probe.x_step) == int(probe.PARAMS["x_step"].default)
    tmap, smap = models(setup)["Transfer Map"], models(setup)["Sample Map"]
    assert (tmap.operator_id, tmap.operator_auth) == ("guest", "guest")
    assert (smap.owner, smap.owner_auth) == ("guest", "guest")


# -- creating an account and signing in ----------------------------------------------------

def test_create_account_asks_first_then_signs_in(setup, root):
    asked = setup.run("create_account", {"account_email": " Ian@UCI.edu ",
                                         "account_password": PASSWORD})
    assert asked.needs_confirm and "ian@uci.edu" in asked.reason
    assert not asked.inputs, "the password never rides on the confirmation"
    assert UserStore(root / "users.sqlite").user(EMAIL) is None, "nothing before yes"
    done = setup.run(asked.command, asked.inputs, (*asked.args, True))
    assert done.is_ok, done.reason
    assert setup.account_status == "Signed in as ian (password)"
    assert UserStore(root / "users.sqlite").verify(EMAIL, PASSWORD)
    assert "User" in setup.controller.model_names
    assert models(setup)["User"].email == EMAIL


def test_create_account_refuses_a_bad_email_a_short_password_and_a_taken_email(setup):
    assert setup.run("create_account", {"account_email": "ian",
                                        "account_password": PASSWORD}).is_refused
    short = setup.run("create_account", {"account_email": EMAIL, "account_password": "xq7"})
    assert short.is_refused and "xq7" not in short.reason
    create(setup)
    setup.run("sign_out")
    taken = setup.run("create_account", {"account_email": EMAIL,
                                         "account_password": PASSWORD})
    assert taken.is_refused and "already" in taken.reason


def test_sign_in_needs_the_right_password(setup):
    create(setup)
    setup.run("sign_out")
    wrong = sign_in(setup, password="not-the-password")
    assert wrong.is_refused and setup.user.is_guest
    assert "not-the-password" not in wrong.reason
    nobody = sign_in(setup, email="bo@uci.edu")
    assert nobody.is_refused and "Create account" in nobody.reason
    assert setup.run("sign_in", {"account_email": "  "}).is_refused
    right = sign_in(setup)
    assert right.is_ok and setup.account_status == "Signed in as ian (password)"
    assert UserStore().user(EMAIL)["last_sign_in"]


def test_the_password_never_reaches_the_log_events_state_or_a_result(setup, heard):
    results = [setup.run("create_account", {"account_email": EMAIL,
                                            "account_password": PASSWORD})]
    results.append(setup.run("create_account", None, (True,)))
    results.append(setup.run("sign_out"))
    results.append(sign_in(setup, password="a-wrong-guess-99"))
    results.append(setup.run("_commit", {"account_password": PASSWORD}))
    results.append(sign_in(setup))
    assert results[-1].is_ok
    said = " ".join(heard())
    said += json.dumps(setup.state) + json.dumps(setup.controller.state(), default=str)
    said += " ".join(json.dumps(r.to_dict(), default=str) + repr(r.value) for r in results)
    for secret in (PASSWORD, "a-wrong-guess-99"):
        assert secret not in said
    assert "<redacted>" in said
    assert setup.state["values"]["account_password"] == ""
    assert setup._pending_password == "", "a typed password does not linger"


def test_open_as_guest_and_sign_out(setup):
    assert setup.run("sign_out").is_refused                  # nobody to sign out
    assert setup.run("open_as_guest").is_ok and setup.user.is_guest
    create(setup)
    assert setup.run("open_as_guest").is_ok
    assert setup.account_status == "Guest (station defaults)"
    assert "User" not in setup.controller.model_names
    sign_in(setup)
    assert setup.run("sign_out").is_ok and setup.user.is_guest


# -- the config reaches the models; sign-out reverts -----------------------------------------

def test_station_defaults_and_the_users_config_reach_the_models_q4(setup, root):
    setup.profiles.save_station({"Stepper Probe": {"man_full_speed": 250},
                                 "RGB Analysis": {"red_min": 140}})
    create(setup)
    UserStore().remember(EMAIL, "Stepper Probe", {"x_step": 9})
    setup.run("sign_out")
    setup.build(CONFIGS)
    probe = models(setup)["Stepper Probe"]
    assert int(probe.man_full_speed) == 250                  # station default at build
    assert int(probe.x_step) == int(probe.PARAMS["x_step"].default)
    assert sign_in(setup).is_ok                              # applied live
    assert int(probe.x_step) == 9 and int(probe.man_full_speed) == 250


def test_sign_out_rebuilds_the_station_defaults_without_a_restart(setup):
    setup.profiles.save_station({"Stepper Probe": {"man_full_speed": 250}})
    create(setup)
    UserStore().remember(EMAIL, "Stepper Probe", {"x_step": 9, "man_full_speed": 280})
    setup.build(CONFIGS)
    probe = models(setup)["Stepper Probe"]
    assert (int(probe.x_step), int(probe.man_full_speed)) == (9, 280)
    calls = list(setup.controller.calls)
    assert setup.run("sign_out").is_ok
    assert models(setup)["Stepper Probe"] is probe, "the same model, not a rebuild"
    assert (int(probe.x_step), int(probe.man_full_speed)) == (
        int(probe.PARAMS["x_step"].default), 250)
    assert [c for c in setup.controller.calls[len(calls):]
            if c != "remove:User"] == [], "nothing but the sheet closed"


def test_switching_users_reverts_the_first_users_values(setup):
    create(setup)
    UserStore().remember(EMAIL, "Stepper Probe", {"x_step": 9})
    setup.build(CONFIGS)
    create(setup, email="bo@uci.edu")
    probe = models(setup)["Stepper Probe"]
    assert int(probe.x_step) == int(probe.PARAMS["x_step"].default)
    assert models(setup)["User"].email == "bo@uci.edu"


def test_built_models_carry_the_operator_and_how_it_was_established(setup):
    setup.build(CONFIGS)
    tmap, smap = models(setup)["Transfer Map"], models(setup)["Sample Map"]
    create(setup)
    assert (tmap.operator_id, tmap.operator_auth) == (EMAIL, "password")
    assert (smap.owner, smap.owner_auth) == (EMAIL, "password")
    # Reason: the live sheet has no Sample ID entry (the sample is picked), so
    # the dormant flake path below is driven with the method.
    smap.sample_id = "S1"
    smap.save_sample()
    # The flake commands are dormant since 2026-10-07 (the Sample Map is an
    # image store; flake-coordinate homing is retired for now), so they are
    # off the allow-list: drive the method directly. The provenance under
    # test is unchanged.
    smap.set_source("Typed readings")
    smap.reading_x_mm, smap.reading_y_mm = 1.0, 2.0
    smap.flag_flake()
    flake = smap._store.coord_flakes()[0]
    assert (flake["owner"], flake["owner_auth"]) == (EMAIL, "password")
    setup.run("sign_out")
    assert (tmap.operator_id, tmap.operator_auth) == ("guest", "guest")


# -- the sheet: a page while someone is signed in ---------------------------------------------

def test_launch_while_signed_in_keeps_the_users_sheet(setup):
    create(setup)
    UserStore().remember(EMAIL, "Stepper Probe", {"x_step": 9})
    built = setup.build(CONFIGS)
    assert built == ["Stepper Probe", "Transfer Map", "Sample Map"]
    assert "User" in setup.controller.model_names
    sheet = models(setup)["User"]
    assert sheet.email == EMAIL and not sheet.is_estopped
    assert int(models(setup)["Stepper Probe"].x_step) == 9


def test_the_sheets_sign_out_is_setups(setup):
    create(setup)
    UserStore().remember(EMAIL, "Stepper Probe", {"x_step": 9})
    setup.build(CONFIGS)
    result = setup.controller.run("User", "sign_out")
    assert result.is_ok, result.reason
    assert setup.user.is_guest and setup.account_status == "Guest (station defaults)"
    assert "User" not in setup.controller.model_names
    probe = models(setup)["Stepper Probe"]
    assert int(probe.x_step) == int(probe.PARAMS["x_step"].default)


def test_remember_current_values_on_the_sheet_keeps_only_user_params(setup):
    create(setup)
    setup.build(CONFIGS)
    probe = models(setup)["Stepper Probe"]
    probe.x_step = 7
    probe.slow_speed = 33                                    # a brake field: never
    result = setup.controller.run("User", "remember_current")
    assert result.is_ok, result.reason
    kept = UserStore().preferences(EMAIL)
    assert kept["Stepper Probe"]["x_step"] == 7
    assert "slow_speed" not in kept["Stepper Probe"]
    assert "RGB Analysis" not in kept and "Sample Map" not in kept


def test_a_closed_sheet_reopens_for_the_same_user(setup):
    create(setup)
    assert setup.controller.remove("User")
    assert setup.account_status.startswith("Signed in"), "closing the page is not a sign-out"
    setup.controller.reopen("User")
    assert models(setup)["User"].email == EMAIL
    setup.run("sign_out")
    with pytest.raises(Exception):
        setup.controller.reopen("User")


def test_save_station_settings_asks_and_keeps_the_brakes_out(setup, root):
    setup.build(CONFIGS)
    smap = models(setup)["Sample Map"]
    smap.um_per_count = "0.4"
    assert setup.run("save_station_settings").needs_confirm
    assert setup.run("save_station_settings", None, (True,)).is_ok
    saved = json.loads((root / "station.json").read_text())
    assert saved["model_params"]["Sample Map"] == {"um_per_count": "0.4"}
    assert "slow_speed" not in saved["model_params"].get("Stepper Probe", {})


# -- the Phase 1 profiles migrate -------------------------------------------------------------

def test_a_phase_1_profile_becomes_an_account_that_sets_its_password_at_first_sign_in(root):
    source = pf.LocalFilesSource(root)
    source.add_user("ialbinog", "Ian")
    source.put("user", "ialbinog", "model_params", {"Stepper Probe": {"x_step": 8}})
    panel = Setup(RecordingController())
    try:
        assert UserStore().user("ialbinog")["must_set_password"] is True
        panel.build(CONFIGS)
        asked = sign_in(panel, email="ialbinog")
        assert asked.needs_confirm and "no password yet" in asked.reason
        assert PASSWORD not in asked.reason and not asked.inputs
        done = panel.run(asked.command, asked.inputs, (*asked.args, True))
        assert done.is_ok, done.reason
        assert panel.account_status == "Signed in as Ian (password)"
        assert int(models(panel)["Stepper Probe"].x_step) == 8, "nothing saved was lost"
        assert UserStore().verify("ialbinog", PASSWORD)
        panel.run("sign_out")
        assert sign_in(panel, email="ialbinog", password="anything-else").is_refused
        assert (root / "users.json").exists(), "the old files stay where they were"
    finally:
        panel.controller.reset()


def test_a_migrated_profile_needs_a_password_typed_to_set_one(root):
    pf.LocalFilesSource(root).add_user("ialbinog", "Ian")
    panel = Setup(RecordingController())
    try:
        empty = sign_in(panel, email="ialbinog", password="")
        assert empty.is_refused and "password" in empty.reason
        assert panel.user.is_guest
    finally:
        panel.controller.reset()


def test_a_station_with_no_profiles_writes_no_accounts_file(setup, root):
    assert not (root / "users.sqlite").exists()


def test_the_accounts_file_holds_no_password_text(setup, root):
    create(setup)
    db = sqlite3.connect(str(root / "users.sqlite"))
    try:
        dump = "\n".join(db.iterdump())
    finally:
        db.close()
    assert PASSWORD not in dump and EMAIL in dump


# -- the core hooks (lead): apply_defaults and secret inputs ------------------------------------

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
