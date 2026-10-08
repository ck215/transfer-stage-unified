"""`model.user.User`: the signed-in person (owner request 2026-10-07,
AC-2), a settings menu and not a device (owner, later that day).

A Panel (`NAME = "User"`, no devices, no stop switch, never in the
Controller: Setup keeps it as `Setup.user`) that OWNS the signed-in user's config: `config()` reads it from the
accounts file, `remember()` writes it (the Q4 split still decides what a
user may keep), `operator()` is who the Transfer Map and the Sample DB
stamp on their records. A Guest is a User with no account row: its config
is empty, so every model keeps the station's defaults.

`load_into` applies the station's defaults and the user's config to every
open model through `apply_defaults` (what Setup did for a profile at build
and at sign-in); `revert` undoes it from each model's own Params, the
station's saved defaults over them, never a restart. Both are driven here
through a fake controller that holds models the way the real one does.

The User is not in `setup.MODEL_TYPES` (it would get a Setup row) and not
a Model, so the device contract (`test_model_contract.py`) is not its.
"""
import json

import pytest

import schema as sch
from controller.controller import Controller
from controller.setup import MODEL_TYPES
from events import events
from model import profile as pf
from model.base import Model
from model.user import AUTH_GUEST, AUTH_PASSWORD, GUEST, User
from model.user_store import AccountError, UserStore
from panel import Panel
from param import Param

PASSWORD = "correct-horse-4821"


class FakeProbe(Model):
    """A probe as far as preferences go: two user parameters, a brake field
    and a live value."""
    NAME = "Stepper Probe"
    PARAMS = {p.name: p for p in (
        Param("x_step", "int", default=4, minimum=1, maximum=3200),
        Param("man_full_speed", "int", default=300, minimum=1, maximum=3200),
        Param("slow_speed", "int", default=50, minimum=1),
        Param("setpoint", "float", default=25.0))}

    @property
    def schema(self):
        P = self.PARAMS
        return sch.schema(sch.section("Motion", *[sch.entry(n, n, P[n]) for n in P]),
                          self._safety_section())

    def _expects_heartbeat(self):
        return False


class FakeHeater(FakeProbe):
    NAME = "Temperature Controller"
    PARAMS = {"p_term": Param("p_term", "float", default=1.0, minimum=0)}


class FakeTransferMap(FakeProbe):
    NAME = "Transfer Map"
    PARAMS = {}

    def __init__(self):
        super().__init__()
        self.operator_id, self.operator_auth = "station", "station"


class FakeSampleMap(FakeProbe):
    NAME = "Sample DB"
    PARAMS = {}

    def __init__(self):
        super().__init__()
        self.owner, self.owner_auth = "station", "station"


FAKES = {cls.NAME: cls for cls in (FakeProbe, FakeHeater, FakeTransferMap, FakeSampleMap)}


def params_of(name):
    return getattr(FAKES.get(name), "PARAMS", {})


class FakeController:
    """The two things the User and Setup use of a Controller: `models`, and
    `add` telling every open model about the newcomer and the newcomer about
    every open model (`Controller.add`'s `on_model_added` both ways)."""

    def __init__(self):
        self.models = {}

    def add(self, name, model):
        for other_name, other in self.models.items():
            other.on_model_added(name, model)
            model.on_model_added(other_name, other)
        self.models[name] = model
        return model


@pytest.fixture
def store(tmp_path):
    found = UserStore(tmp_path / "profiles" / "users.sqlite")
    found.create("ian@uci.edu", PASSWORD, name="Ian")
    return found


@pytest.fixture
def profiles(tmp_path):
    return pf.ProfileService(pf.LocalFilesSource(tmp_path / "profiles"), params_of)


@pytest.fixture
def station():
    fake = FakeController()
    for cls in FAKES.values():
        fake.add(cls.NAME, cls())
    return fake


def signed_in(store, **kwargs):
    return User(store=store, email="ian@uci.edu", params_of=params_of, **kwargs)


# -- the class -------------------------------------------------------------------------------

def test_the_user_is_a_settings_panel_not_a_device():
    """Owner 2026-10-07: "the safety switch in the user model makes no
    sense, it is not a device". A Panel (schema, state, run), never a Model:
    no stop, no devices, no heartbeat, no Setup row."""
    assert issubclass(User, Panel) and not issubclass(User, Model)
    assert User.NAME == "User"
    for attr in ("estop", "toggle_estop", "clear_estop", "devices", "is_energized",
                 "is_active", "IDENTITY", "NEEDS_PORT"):
        assert not hasattr(User, attr), f"a User has {attr}, which is a device's"
    assert "User" not in MODEL_TYPES, "a registered User would get a Setup row"


@pytest.mark.parametrize("who", ["guest", "signed_in"])
def test_the_sheet_has_no_stop_switch(who, store):
    user = User.guest() if who == "guest" else signed_in(store)
    commands = {e.get("command") for e in sch.elements(user.schema)}
    assert not commands & {"toggle_estop", "estop", "clear_estop", "halt"}, commands
    titles = [s["title"] for s in user.schema["sections"]]
    assert "Safety" not in titles and "Stop" not in titles, titles
    json.dumps(user.state)


# -- guest semantics -----------------------------------------------------------------------------

def test_a_guest_has_no_config_and_works_as_guest():
    guest = User.guest()
    assert guest.is_guest and guest.config() == {}
    assert guest.operator() == (GUEST, AUTH_GUEST) == ("guest", "guest")
    assert guest.auth == AUTH_GUEST and guest.user_name == "Guest"
    with pytest.raises(AccountError, match="Sign in"):
        guest.remember("Stepper Probe", {"x_step": 8})
    assert "Guest" in guest.who


def test_opening_as_guest_changes_nothing(station, profiles):
    probe = station.models["Stepper Probe"]
    probe.x_step, probe.setpoint = 7, 31.0
    assert User.guest().load_into(station.models, profiles) == {}
    assert (probe.x_step, probe.setpoint, probe.man_full_speed) == (7, 31.0, 300)
    tmap, smap = station.models["Transfer Map"], station.models["Sample DB"]
    assert (tmap.operator_id, tmap.operator_auth) == ("guest", "guest")
    assert (smap.owner, smap.owner_auth) == ("guest", "guest")


def test_a_guest_gets_the_stations_saved_defaults(station, profiles):
    profiles.save_station({"Stepper Probe": {"man_full_speed": 250}})
    User.guest().load_into(station.models, profiles)
    assert station.models["Stepper Probe"].man_full_speed == 250


# -- a signed-in user owns their config -------------------------------------------------------

def test_a_signed_in_user_is_who_the_records_name(store):
    user = signed_in(store)
    assert not user.is_guest and user.email == "ian@uci.edu"
    assert user.operator() == ("ian@uci.edu", AUTH_PASSWORD) == ("ian@uci.edu", "password")
    assert user.user_name == "Ian" and user.display_name == "Ian"
    assert "Ian" in user.who and "ian@uci.edu" in user.who


def test_an_unknown_email_is_no_user(store):
    with pytest.raises(AccountError, match="No account"):
        User(store=store, email="nobody@uci.edu")


def test_config_is_the_stores_and_remember_writes_it(store):
    user = signed_in(store)
    assert user.config() == {}
    assert user.remember("Stepper Probe", {"x_step": 8}) == {"Stepper Probe": {"x_step": 8}}
    assert user.config() == store.preferences("ian@uci.edu") == {
        "Stepper Probe": {"x_step": 8}}


def test_remember_keeps_the_q4_split(store):
    """Station-only values, the brakes and live values are never a user's."""
    user = signed_in(store)
    for model_name, values, why in (
            ("Temperature Controller", {"p_term": 2.0}, "station-only"),
            ("Stepper Probe", {"slow_speed": 10}, "never"),
            ("Stepper Probe", {"setpoint": 30.0}, "not a preference"),
            ("Stepper Probe", {"x_step": 99999}, "at most")):
        with pytest.raises(AccountError, match=why):
            user.remember(model_name, values)
    assert user.config() == {}


def test_load_into_applies_station_then_user_through_apply_defaults(store, station, profiles):
    profiles.save_station({"Stepper Probe": {"man_full_speed": 250, "x_step": 2},
                           "Temperature Controller": {"p_term": 1.5}})
    user = signed_in(store)
    user.remember("Stepper Probe", {"x_step": 8})
    assert user.load_into(station.models, profiles) == {}
    probe = station.models["Stepper Probe"]
    assert (probe.x_step, probe.man_full_speed) == (8, 250)
    assert station.models["Temperature Controller"].p_term == 1.5
    tmap, smap = station.models["Transfer Map"], station.models["Sample DB"]
    assert (tmap.operator_id, tmap.operator_auth) == ("ian@uci.edu", "password")
    assert (smap.owner, smap.owner_auth) == ("ian@uci.edu", "password")


def test_load_into_reports_what_a_model_refused(store, station, profiles, monkeypatch):
    user = signed_in(store)
    user.remember("Stepper Probe", {"x_step": 8})
    probe = station.models["Stepper Probe"]
    monkeypatch.setattr(type(probe), "apply_defaults",
                        lambda self, values: {n: "locked while autonomous" for n in values})
    assert user.load_into(station.models, profiles) == {
        "Stepper Probe": {"x_step": "locked while autonomous"}}


def test_revert_rebuilds_each_models_defaults_from_its_params(store, station, profiles):
    """Sign-out: the user's parameters go back to the code's defaults with
    the station's saved ones over them. Live values (a setpoint) and
    station-only values are not the user's to undo; nothing restarts."""
    profiles.save_station({"Stepper Probe": {"man_full_speed": 250}})
    user = signed_in(store)
    user.remember("Stepper Probe", {"x_step": 8, "man_full_speed": 280})
    user.load_into(station.models, profiles)
    probe = station.models["Stepper Probe"]
    probe.setpoint = 31.0
    station.models["Temperature Controller"].p_term = 2.5
    assert user.revert(station.models, profiles) == {}
    assert (probe.x_step, probe.man_full_speed, probe.setpoint) == (4, 250, 31.0)
    assert station.models["Temperature Controller"].p_term == 2.5


def test_remember_current_collects_the_open_models_user_values(store, station):
    """"Remember current values as my defaults": the inverse of apply_defaults
    over every open model Setup hands it (`models=`), Q4-filtered."""
    user = signed_in(store, models=lambda: station.models)
    probe = station.models["Stepper Probe"]
    probe.x_step, probe.man_full_speed, probe.slow_speed = 6, 260, 33
    station.models["Temperature Controller"].p_term = 2.5
    result = user.run("remember_current")
    assert result.is_ok, result.reason
    assert user.config() == {"Stepper Probe": {"x_step": 6, "man_full_speed": 260}}
    assert "x_step" in user.remembered and "Stepper Probe" in user.remembered


def test_remember_current_reads_the_real_controllers_open_models(store):
    """The User reads the Controller's models; it is never one of them."""
    controller = Controller()
    try:
        controller.add("Stepper Probe", FakeProbe())
        user = signed_in(store, models=lambda: controller.models)
        assert "User" not in controller.model_names
        controller.models["Stepper Probe"].x_step = 11
        assert user.run("remember_current").is_ok
        assert user.config() == {"Stepper Probe": {"x_step": 11, "man_full_speed": 300}}
        controller.remove("Stepper Probe")
        assert user.run("remember_current").is_refused
    finally:
        controller.close()


def test_a_guest_remembers_nothing():
    assert User.guest().run("remember_current").is_refused


# -- the sheet -------------------------------------------------------------------------------------

def _sections(user):
    return {s["title"]: s for s in user.schema["sections"]}


def test_the_menu_is_who_switch_user_sign_out_then_name_password_defaults(store):
    """A menu, so everything at tier 1: who, Switch user and Sign out; the
    name, the password and "Remember current values as my defaults"."""
    sections = _sections(signed_in(store))
    assert list(sections) == ["Signed in", "Name", "Password", "My defaults"]
    assert all(s["tier"] == 1 for s in sections.values())
    first = sections["Signed in"]
    assert [(e["type"], e.get("command") or e.get("model_attr")) for e in first["elements"]] \
        == [("readonly", "who"), ("button", "switch_user"), ("button", "sign_out")]
    commands = {e.get("command") for s in sections.values() for e in s["elements"]}
    assert {"rename", "change_password", "remember_current"} <= commands
    remember = next(e for e in sections["My defaults"]["elements"]
                    if e.get("command") == "remember_current")
    assert remember["text"] == "Remember current values as my defaults"
    secrets = [e["model_attr"] for e in sch.elements(signed_in(store).schema) if e.get("secret")]
    assert secrets == ["current_password", "new_password"]


def test_a_guests_menu_is_who_and_sign_in_switch_user(store):
    calls = []
    guest = User.guest(on_switch_user=lambda: calls.append("switch") or "chosen")
    sections = _sections(guest)
    assert list(sections) == ["Signed in"]
    assert [(e["type"], e.get("command") or e.get("model_attr"), e.get("text"))
            for e in sections["Signed in"]["elements"]] == [
        ("readonly", "who", "Signed in"),
        ("button", "switch_user", "Sign in / Switch user")]
    assert "Guest" in guest.state["values"]["who"] and guest.state["is_guest"] is True
    assert guest.run("switch_user").value == "chosen" and calls == ["switch"]


def test_sign_out_and_switch_user_on_the_sheet_are_setups(store):
    calls = []
    user = signed_in(store, on_sign_out=lambda: calls.append("out") or "Guest",
                     on_switch_user=lambda: calls.append("switch") or "Guest")
    assert user.run("sign_out").value == "Guest" and calls == ["out"]
    assert user.run("switch_user").is_ok and calls == ["out", "switch"]
    assert signed_in(store).run("sign_out").is_refused     # not wired to a Setup
    assert signed_in(store).run("switch_user").is_refused
    assert User.guest().run("sign_out").is_refused


def test_rename(store):
    user = signed_in(store)
    assert user.run("rename", {"display_name": "  Ian A. "}).is_ok
    assert store.user("ian@uci.edu")["name"] == "Ian A." == user.user_name
    assert user.run("rename", {"display_name": "   "}).is_refused


def test_change_password_needs_the_current_one_and_never_shows_either(store):
    user = signed_in(store)
    since = events.latest_id
    seen = []
    original = events.debug

    def capture(title, message, **kwargs):
        seen.append(f"{title} {message}")
        return original(title, message, **kwargs)

    events.debug = capture
    try:
        wrong = user.run("change_password", {"current_password": "not-it-at-all",
                                             "new_password": "brand-new-pass"})
        right = user.run("change_password", {"current_password": PASSWORD,
                                             "new_password": "brand-new-pass"})
    finally:
        events.debug = original
    assert wrong.is_refused and "current password" in wrong.reason
    assert right.is_ok, right.reason
    assert store.verify("ian@uci.edu", "brand-new-pass")
    said = " ".join(seen) + " ".join(f"{e.title} {e.message}" for e in events.since(since))
    said += json.dumps(user.state) + wrong.reason + str(right.value)
    for secret in (PASSWORD, "not-it-at-all", "brand-new-pass"):
        assert secret not in said
    assert any("<redacted>" in line for line in seen)
    assert user.state["values"]["current_password"] == ""
    assert user.state["values"]["new_password"] == ""


def test_a_typed_password_does_not_linger_after_the_command(store):
    user = signed_in(store)
    user.run("change_password", {"current_password": "wrong-one-here",
                                 "new_password": "brand-new-pass"})
    assert all(not value for value in user._typed.values())


def test_a_short_new_password_is_refused_without_quoting_it(store):
    user = signed_in(store)
    result = user.run("change_password", {"current_password": PASSWORD,
                                          "new_password": "abc"})
    assert result.is_refused and "abc" not in result.reason
    assert store.verify("ian@uci.edu", PASSWORD)


def test_the_users_state_is_json_and_names_no_secret(store):
    user = signed_in(store)
    user.remember("Stepper Probe", {"x_step": 8})
    state = user.state
    json.dumps(state)
    assert state["values"]["who"] == user.who
    assert state["name"] == "User" and "devices" not in state
    assert state["user_name"] == "Ian" and state["is_guest"] is False
    assert "x_step" in state["values"]["remembered"]
