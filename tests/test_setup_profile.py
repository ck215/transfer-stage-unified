"""The Account section of Setup (owner request 2026-10-07, AC-3), which
replaced the Phase 1 Profile row.

Setup is the composition root: it opens the accounts file
(`users.sqlite`), migrates the Phase 1 name-only profiles into it, keeps the
session (the signed-in `User`, a Guest by default), applies the station's
defaults and the user's config to every model it builds and, on sign-in, to
the models already open, reverts them on sign-out, and keeps the signed-in
user as its own `Setup.user`. Everything here goes
through `run()`, the way a view drives it, and the password never reaches
the log, an event, `state` or a result.

Owner 2026-10-07 (later): the user is a settings menu, not a device - Setup
keeps it as `Setup.user`, never in the Controller, and the section here is
the station-wide half ("Station defaults"). A Guest gets the tool controls
only: no Transfer Map or Sample Map.
"""
import json
import sqlite3

import pytest

pytestmark = pytest.mark.usefixtures("profiles_on", "sample_map_on")   # the held features, on

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
           {"model": "Sample DB", "port": "On", "sim": False}]
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

def test_the_station_defaults_section_comes_first_and_only_saves_them(setup):
    """Owner 2026-10-07: the user is a settings menu of its own (the rail's
    account menu: who is in, Switch user, the name, the password, my
    defaults), so Setup keeps the station-wide half only: Save station
    settings. Signing in happens on the sign-in screen."""
    assert setup.schema["sections"][0]["title"] == Setup.ACCOUNT_SECTION == "Station defaults"
    elements = _section(setup)["elements"]
    assert [(e["type"], e.get("command") or e.get("model_attr")) for e in elements] == [
        ("button", "save_station_settings")]
    shown = {e.get("command") or e.get("model_attr") for e in sch.elements(setup.schema)}
    assert not shown & {"switch_user", "account_status", "remember_current"}
    # 2026-10-08: the backup is one user's own, on the account menu.
    assert not shown & {"backup_status", "backup_dir", "set_backup_dir", "back_up_now"}
    assert "account_password" in Setup.SECRET_INPUTS
    for gone in ("set_profile_user", "add_profile", "remember_settings"):
        assert gone not in {e.get("command") for e in elements}


def test_the_gate_commands_are_setups_although_no_control_shows_them(setup):
    """The sign-in screen sends `sign_in` / `create_account` /
    `open_as_guest` / `sign_out` with the typed email and password as the
    command's inputs; Setup still allows exactly those, and nothing else
    that its schema does not show."""
    shown = {e.get("command") for e in sch.elements(setup.schema)}
    assert not (Setup.GATE_COMMANDS & shown), "the gate's commands are off the page"
    asked = setup.run("create_account", {"account_email": EMAIL,
                                         "account_password": PASSWORD})
    assert asked.needs_confirm, asked.reason
    assert setup.run(asked.command, asked.inputs, (*asked.args, True)).is_ok
    assert setup.run("sign_out").is_ok
    assert sign_in(setup).is_ok and not setup.user.is_guest
    assert setup.run("open_as_guest").is_ok and setup.user.is_guest
    assert setup.run("not_a_command").is_refused
    refused = setup.run("sign_in", {"account_email": EMAIL, "station_version": "x"})
    assert refused.is_refused and "station_version" in refused.reason


def test_the_session_is_unchosen_until_sign_in_create_or_guest(setup):
    """The gate's flag: False at start, True after any of the three choices,
    False again after Sign out or Switch user; published in `state`."""
    def chosen():
        state = setup.state
        assert state["account_chosen"] is state["account"]["chosen"]
        return state["account_chosen"]

    assert chosen() is False and setup.account["signed_in"] is False
    assert "sheet" not in setup.state["account"], "the user is no model's page"
    assert setup.state["account"]["name"] == "Guest"
    assert setup.run("open_as_guest").is_ok and chosen() is True
    assert setup.run("switch_user").is_ok and chosen() is False
    create(setup)
    assert chosen() is True and setup.state["account"]["signed_in"] is True
    assert setup.state["account"]["email"] == EMAIL
    assert setup.run("switch_user").is_ok
    assert chosen() is False and setup.user.is_guest, "Switch user signs out"
    assert "User" not in setup.controller.model_names
    assert "User" not in setup.controller.closed_names, "a Guest is offered a sheet to reopen"
    assert sign_in(setup).is_ok and chosen() is True
    assert setup.run("sign_out").is_ok and chosen() is False
    assert sign_in(setup, password="wrong-password-1").is_refused
    assert chosen() is False, "a refused sign-in chooses nothing"


def test_with_accounts_off_there_is_nothing_to_choose(root, monkeypatch):
    from controller import setup as station_setup
    monkeypatch.setattr(station_setup, "PROFILES_ENABLED", False)
    panel = Setup(RecordingController())
    assert panel.state["account_chosen"] is True
    assert panel.state["account"]["enabled"] is False
    assert panel.run("sign_in", {"account_email": EMAIL}).is_refused


def test_a_new_station_is_a_guest_and_launching_as_guest_changes_nothing(setup):
    assert setup.account_status == "Guest (station defaults)"
    assert setup.user.is_guest
    setup.build(CONFIGS)
    assert "User" not in setup.controller.model_names, "a user is never a model"
    probe = models(setup)["Stepper Probe"]
    assert int(probe.x_step) == int(probe.PARAMS["x_step"].default)


# -- a Guest has the tool controls only (owner 2026-10-07) --------------------------------------

def test_a_guest_launch_has_no_transfer_map_or_sample_map(setup):
    """"Guest users should have no access to transfer map or sample map,
    just tool controls": a Guest's launch leaves them (and what the Map
    hosts) out, and their Setup rows say why."""
    setup.build(CONFIGS)
    assert setup.controller.model_names == ["Stepper Probe"]
    assert not [c for c in setup.configs if c["model"] in (
        "Transfer Map", "Sample DB", "RGB Analysis")], setup.configs
    statuses = {row["name"]: row["status"] for row in setup.state["rows"]}
    assert statuses["Transfer Map"] == statuses["Sample DB"] == "sign in to use"
    assert set(setup.state["account"]["signed_in_only"]) >= {
        "Transfer Map", "Sample DB", "RGB Analysis"}


def test_a_guest_cannot_build_or_reopen_a_map(setup):
    with pytest.raises(Exception, match="signed-in users"):
        setup.model_from_config({"model": "Transfer Map", "port": None, "sim": False})
    assert "signed-in users" in setup.session_refusal("Sample DB", "save_sample")
    assert setup.session_refusal("Sample DB", "toggle_estop") == "", "a stop is never refused"
    assert setup.session_refusal("Stepper Probe", "set_mode") == ""
    create(setup)
    assert setup.session_refusal("Transfer Map", "arm") == ""


def test_signing_in_adds_the_maps_to_a_running_station_and_guest_removes_them(setup):
    setup.build(CONFIGS)
    probe = models(setup)["Stepper Probe"]
    create(setup)
    assert {"Transfer Map", "Sample DB"} <= set(setup.controller.model_names)
    assert models(setup)["Stepper Probe"] is probe, "nothing restarted"
    tmap = models(setup)["Transfer Map"]
    assert (tmap.operator_id, tmap.operator_auth) == (EMAIL, "password")
    assert setup.run("switch_user").is_ok and setup.user.is_guest
    assert setup.controller.model_names == ["Stepper Probe"]
    assert models(setup)["Stepper Probe"] is probe


def test_switching_to_guest_mid_trial_is_refused(setup, monkeypatch):
    from model.transfer_map import TransferMap
    create(setup)
    setup.build(CONFIGS)
    monkeypatch.setattr(TransferMap, "is_active", property(lambda self: True))
    for command in ("switch_user", "sign_out", "open_as_guest"):
        refused = setup.run(command)
        assert refused.is_refused and "trial" in refused.reason, (command, refused.reason)
        assert not setup.user.is_guest and "Transfer Map" in setup.controller.model_names
    monkeypatch.undo()
    assert setup.run("switch_user").is_ok
    assert "Transfer Map" not in setup.controller.model_names


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
    assert "User" not in setup.controller.model_names, "the user is Setup's, not a model"
    assert setup.user.email == EMAIL


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
    assert setup.state["values"].get("account_password", "") == ""
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
    assert setup.controller.calls[len(calls):] == [
        "remove:Transfer Map", "remove:Sample DB"], "nothing but the maps closed"


def test_switching_users_reverts_the_first_users_values(setup):
    create(setup)
    UserStore().remember(EMAIL, "Stepper Probe", {"x_step": 9})
    setup.build(CONFIGS)
    create(setup, email="bo@uci.edu")
    probe = models(setup)["Stepper Probe"]
    assert int(probe.x_step) == int(probe.PARAMS["x_step"].default)
    assert setup.user.email == "bo@uci.edu"


def test_built_models_carry_the_operator_and_how_it_was_established(setup):
    setup.build(CONFIGS)            # a Guest's: the maps come with the sign-in
    create(setup)
    tmap, smap = models(setup)["Transfer Map"], models(setup)["Sample DB"]
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
    assert "Transfer Map" not in setup.controller.model_names


# -- the sheet: the account menu, Setup's own (never a model) ----------------------------------

def test_launch_while_signed_in_has_no_user_model(setup):
    create(setup)
    UserStore().remember(EMAIL, "Stepper Probe", {"x_step": 9})
    built = setup.build(CONFIGS)
    assert built == ["Stepper Probe", "Transfer Map", "Sample DB"]
    assert "User" not in setup.controller.model_names
    assert setup.user.email == EMAIL
    assert int(models(setup)["Stepper Probe"].x_step) == 9


def test_the_sheets_sign_out_is_setups(setup):
    create(setup)
    UserStore().remember(EMAIL, "Stepper Probe", {"x_step": 9})
    setup.build(CONFIGS)
    result = setup.user.run("sign_out")
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
    result = setup.user.run("remember_current")
    assert result.is_ok, result.reason
    kept = UserStore().preferences(EMAIL)
    assert kept["Stepper Probe"]["x_step"] == 7
    assert "slow_speed" not in kept["Stepper Probe"]
    assert "RGB Analysis" not in kept and "Sample DB" not in kept


def test_there_is_no_user_page_to_close_or_reopen(setup):
    """The "Reopen User" path is gone with the User model."""
    create(setup)
    assert not setup.controller.remove("User")
    with pytest.raises(Exception):
        setup.controller.reopen("User")
    with pytest.raises(Exception):
        setup.model_from_config({"model": "User", "sim": False})
    assert setup.account_status.startswith("Signed in")


def test_save_station_settings_asks_and_keeps_the_brakes_out(setup, root):
    create(setup)                   # the Sample Map is a signed-in user's
    setup.build(CONFIGS)
    smap = models(setup)["Sample DB"]
    smap.um_per_count = "0.4"
    assert setup.run("save_station_settings").needs_confirm
    assert setup.run("save_station_settings", None, (True,)).is_ok
    saved = json.loads((root / "station.json").read_text())
    assert saved["model_params"]["Sample DB"] == {"um_per_count": "0.4"}
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


# -- the per-account trial store (2026-10-07) ---------------------------------------------------

@pytest.fixture
def stores(tmp_path, monkeypatch):
    """No STATION_MAP_DB, a private choices file, a tmp install root, and
    two stores on disk: the station's and the user's."""
    from controller import user_config
    from model import transfer_map as tm_module
    from model.transfer_map import TrialStore
    monkeypatch.delenv("STATION_MAP_DB", raising=False)
    monkeypatch.setenv("STATION_CONFIG", str(tmp_path / "choices" / "station.json"))
    user_config.forget()
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(tm_module, "_install_root", lambda: install)
    station, mine = tmp_path / "station.sqlite", tmp_path / "mine.sqlite"
    TrialStore(station).ensure()
    TrialStore(mine).ensure()
    user_config.write("map_store", str(station))
    yield station, mine
    user_config.forget()


def test_a_signed_in_users_store_is_theirs_and_the_station_keeps_its_own(setup, stores):
    from controller import user_config
    station, mine = stores
    create(setup)
    setup.map_store_path = str(mine)
    assert setup.open_map_store() == str(mine)
    assert UserStore().setting(EMAIL, "map_store") == str(mine)
    assert user_config.read("map_store") == str(station), "a Guest keeps the station's"
    assert setup.map_store_status == str(mine)
    setup.run("sign_out")
    assert setup.map_store_status == str(station)


def test_the_maps_own_open_store_remembers_for_the_signed_in_user(setup, stores):
    from controller import user_config
    station, mine = stores
    setup.build(CONFIGS)
    create(setup)
    tmap = models(setup)["Transfer Map"]
    assert tmap.db_path == station, "a user with no store of their own: the station's"
    result = setup.controller.run("Transfer Map", "open_store", {"store_path": str(mine)})
    assert result.is_ok, result.reason
    assert UserStore().setting(EMAIL, "map_store") == str(mine)
    assert user_config.read("map_store") == str(station)


def test_signing_in_opens_the_users_store_and_guest_gets_the_stations_back(setup, stores):
    station, mine = stores
    create(setup)
    UserStore().put_setting(EMAIL, "map_store", str(mine))
    setup.run("sign_out")
    setup.build(CONFIGS)
    assert "Transfer Map" not in setup.controller.model_names, "a Guest has no map"
    assert sign_in(setup).is_ok
    assert models(setup)["Transfer Map"].db_path == mine, "the sign-in's map is on the user's store"
    assert setup.run("switch_user").is_ok
    assert "Transfer Map" not in setup.controller.model_names
    assert sign_in(setup).is_ok
    assert models(setup)["Transfer Map"].db_path == mine
    setup.build(CONFIGS)
    assert models(setup)["Transfer Map"].db_path == mine, "a launch while signed in too"


def test_a_users_missing_store_is_a_warning_not_a_failed_sign_in(setup, stores, heard):
    station, mine = stores
    create(setup)
    UserStore().put_setting(EMAIL, "map_store", str(mine.parent / "gone.sqlite"))
    setup.run("sign_out")
    setup.build(CONFIGS)
    assert sign_in(setup).is_ok
    assert models(setup)["Transfer Map"].db_path == station
    assert any("Trial Store Missing" in line for line in heard())
