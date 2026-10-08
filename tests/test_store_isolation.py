"""Store isolation (owner ruling 2026-10-08, DATA SAFETY): "Signed in but no
map -> it prompts for creation of a new db, does NOT default to another
user's data!"

For both stores (the Transfer Map's `map_store`, the Sample DB's
`sample_store`): a signed-in user with no setting of their own gets the
model's store prompt (`new_store`, the folder prefilled with
`~/transfer-stage-runs/stores/<email>/`) - never the station's choices
file, never another user's setting. Switching user closes the previous
user's stores and opens the new user's (or prompts). The env overrides
(STATION_MAP_DB / STATION_SAMPLE_DB) still win, for tests and dev.
"""
import pytest

pytestmark = pytest.mark.usefixtures("profiles_on", "sample_map_on")

from controller import user_config
from events import events
from model import store_choice
from model import transfer_map as tm_module
from model.sample_store import SampleStore
from model.transfer_map import TrialStore
from model.user_store import UserStore
from test_setup_profile import CONFIGS, PASSWORD, create, models, root, setup, sign_in  # noqa: F401

A, B = "alice@uci.edu", "bob@uci.edu"
NAMES = ("Transfer Map", "Sample DB")


@pytest.fixture(autouse=True)
def quiet_event_log():
    """The maps here warn "Store Not Chosen" on purpose; the event log
    collapses a repeat, so none is left behind for a later module."""
    yield
    for reset in (events.clear, events._debug_seen.clear):
        reset()


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """No env store, a private choices file holding the station's (an old,
    shared) store for both models, and A's own stores on disk."""
    monkeypatch.delenv("STATION_MAP_DB", raising=False)
    monkeypatch.delenv("STATION_SAMPLE_DB", raising=False)
    monkeypatch.setenv("STATION_CONFIG", str(tmp_path / "choices" / "station.json"))
    user_config.forget()
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(tm_module, "_install_root", lambda: install)
    monkeypatch.setattr(store_choice, "install_root", lambda: install)
    paths = {}
    for who in ("station", "a"):
        paths[who, "map"] = tmp_path / who / "trials.sqlite"
        paths[who, "sample"] = tmp_path / who / "samples.sqlite"
        paths[who, "map"].parent.mkdir(parents=True, exist_ok=True)
        TrialStore(paths[who, "map"]).ensure()
        SampleStore(paths[who, "sample"]).ensure()
    user_config.write("map_store", str(paths["station", "map"]))
    user_config.write("sample_store", str(paths["station", "sample"]))
    yield paths
    user_config.forget()


def _a_with_stores(panel, paths):
    create(panel, A)
    UserStore().put_setting(A, "map_store", str(paths["a", "map"]))
    UserStore().put_setting(A, "sample_store", str(paths["a", "sample"]))


def _seen(panel):
    """Every path a store model shows or holds: the open store and the two
    fields of its prompt."""
    out = []
    for name in NAMES:
        model = models(panel)[name]
        out += [str(model.db_path), str(model.store_path), str(model.store_dir),
                str(model.store_status)]
    return " | ".join(out)


def _prompting(panel, email):
    for name in NAMES:
        model = models(panel)[name]
        assert not model.has_store, f"{name} opened {model.db_path} for {email}"
        assert model.phase == "new_store", f"{name}: {model.phase}"
        assert model.store_dir == str(store_choice.suggested_dir(email)), name


def test_a_user_with_no_setting_gets_the_prompt_for_both_models_not_the_stations(setup, disk):
    create(setup, B)
    setup.build(CONFIGS)
    _prompting(setup, B)
    assert str(disk["station", "map"]) not in _seen(setup)
    assert str(disk["station", "sample"]) not in _seen(setup)


def test_user_b_signed_in_after_user_a_never_sees_as_store(setup, disk):
    _a_with_stores(setup, disk)
    setup.build(CONFIGS)
    assert models(setup)["Transfer Map"].db_path == disk["a", "map"]
    assert models(setup)["Sample DB"].db_path == disk["a", "sample"]
    # Straight from A to B (no Guest in between) ...
    create(setup, B)
    _prompting(setup, B)
    for key in ("map", "sample"):
        assert str(disk["a", key]) not in _seen(setup)
        assert str(disk["station", key]) not in _seen(setup)
    # ... and through Guest.
    setup.run("switch_user")
    assert sign_in(setup, B).is_ok
    _prompting(setup, B)
    for key in ("map", "sample"):
        assert str(disk["a", key]) not in _seen(setup)


def test_a_store_b_makes_is_bs_and_a_gets_theirs_back(setup, disk, tmp_path):
    _a_with_stores(setup, disk)
    setup.build(CONFIGS)
    create(setup, B)
    tmap = models(setup)["Transfer Map"]
    tmap.store_dir = str(tmp_path / "b")
    assert setup.controller.run("Transfer Map", "new_store").is_ok
    assert UserStore().setting(B, "map_store") == str(tmp_path / "b" / "transfer_map.sqlite")
    assert UserStore().setting(A, "map_store") == str(disk["a", "map"])
    assert user_config.read("map_store") == str(disk["station", "map"]), "never the station's file"
    assert sign_in(setup, A).is_ok
    assert models(setup)["Transfer Map"].db_path == disk["a", "map"]
    assert models(setup)["Sample DB"].db_path == disk["a", "sample"]


def test_an_existing_user_with_both_settings_opens_directly(setup, disk):
    """The operator (ialbinog@uci.edu) has both settings: unchanged."""
    _a_with_stores(setup, disk)
    setup.run("sign_out")
    setup.build(CONFIGS)
    assert sign_in(setup, A).is_ok
    assert models(setup)["Transfer Map"].db_path == disk["a", "map"]
    assert models(setup)["Sample DB"].db_path == disk["a", "sample"]
    assert models(setup)["Transfer Map"].phase != "new_store"


def test_a_missing_own_store_prompts_rather_than_the_stations(setup, disk, tmp_path):
    create(setup, A)
    UserStore().put_setting(A, "map_store", str(tmp_path / "gone.sqlite"))
    setup.build(CONFIGS)
    assert not models(setup)["Transfer Map"].has_store
    assert str(disk["station", "map"]) not in _seen(setup)


def test_the_env_override_still_wins(setup, disk, tmp_path, monkeypatch):
    env = tmp_path / "env" / "trials.sqlite"
    monkeypatch.setenv("STATION_MAP_DB", str(env))
    create(setup, B)
    setup.build(CONFIGS)
    assert models(setup)["Transfer Map"].db_path == env.resolve()


def test_switching_user_mid_trial_is_refused(setup, disk, monkeypatch):
    _a_with_stores(setup, disk)
    setup.build(CONFIGS)
    tmap = models(setup)["Transfer Map"]
    monkeypatch.setattr(type(tmap), "is_active", property(lambda self: True))
    result = setup.run("create_account", {"account_email": B, "account_password": PASSWORD})
    if result.needs_confirm:
        result = setup.run(result.command, result.inputs, (*result.args, True))
    assert not result.is_ok
    assert setup.user.email == A
    assert models(setup)["Transfer Map"].db_path == disk["a", "map"]
