"""`model.user_store`: the accounts file (owner request 2026-10-07, AC-1).

One sqlite file beside the profiles (`users.sqlite`): users by email, their
preferences per model and parameter, and a `settings` table kept for later
(GUI layout, default fields, per-user sample bases). Passwords are scrypt
hashes with a per-user salt, compared in constant time, and never leave the
store: no read returns them, no repr shows them, no event carries them.
"""
import hashlib
import hmac
import json
import sqlite3

import pytest

from events import events
from model import profile as pf
from model import user_store as us

PASSWORD = "correct-horse-4821"


@pytest.fixture
def store(tmp_path):
    return us.UserStore(tmp_path / "profiles" / "users.sqlite")


def _everything_the_store_says(store, email):
    """Every way the store hands an account back, as one string."""
    parts = [repr(store), str(store), repr(store.users()), repr(store.user(email)),
             json.dumps(store.users()), json.dumps(store.user(email))]
    return " ".join(parts)


# -- the file -------------------------------------------------------------------------

def test_the_file_sits_beside_the_profiles(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_PROFILES_DIR", str(tmp_path / "p"))
    assert us.default_path() == tmp_path / "p" / "users.sqlite"
    assert us.UserStore().path == tmp_path / "p" / "users.sqlite"


def test_reading_creates_nothing(store):
    assert store.users() == [] and store.user("a@b.c") is None
    assert store.preferences("a@b.c") == {} and store.settings("a@b.c") == {}
    assert store.verify("a@b.c", PASSWORD) is False
    assert not store.path.exists() and not store.path.parent.exists()


def test_a_new_file_has_the_three_tables_at_version_1(store):
    store.create("ian@uci.edu", PASSWORD, name="Ian")
    db = sqlite3.connect(str(store.path))
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == us.SCHEMA_VERSION == 1
        columns = {table: [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
                   for table in ("users", "preferences", "settings")}
    finally:
        db.close()
    assert columns["users"] == ["email", "name", "password_hash", "password_salt",
                                "created_at", "last_sign_in", "note"]
    assert columns["preferences"] == ["email", "model_name", "param", "value"]
    assert columns["settings"] == ["email", "key", "value"]


def test_migration_is_presence_based_and_additive(store):
    """A file from before a column existed gains it and keeps its rows; the
    version is written once the file has everything."""
    store.path.parent.mkdir(parents=True)
    db = sqlite3.connect(str(store.path))
    db.execute("CREATE TABLE users (email TEXT PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users (email, name) VALUES ('old@lab.org', 'Old')")
    db.commit()
    db.close()
    assert store.user("old@lab.org")["name"] == "Old"     # a read migrates nothing
    store.rename("old@lab.org", "Older")                 # the first write does
    db = sqlite3.connect(str(store.path))
    try:
        have = [r[1] for r in db.execute("PRAGMA table_info(users)")]
        version = db.execute("PRAGMA user_version").fetchone()[0]
    finally:
        db.close()
    assert {"password_hash", "password_salt", "created_at", "last_sign_in",
            "note"} <= set(have)
    assert version == 1
    record = store.user("old@lab.org")
    assert record["name"] == "Older" and record["must_set_password"] is True


# -- emails ----------------------------------------------------------------------------

def test_an_email_is_an_identity_string_checked_loosely():
    assert us.check_email("  Ian.Albino@UCI.edu ") == "ian.albino@uci.edu"
    for bad in ("", "ian", "ian@@uci.edu", "a@b@c", "ian @uci.edu", "ian@", "@uci.edu",
                "ian@uci\tedu", None):
        with pytest.raises(us.AccountError):
            us.check_email(bad)


def test_create_refuses_a_taken_email_in_any_case(store):
    store.create("ian@uci.edu", PASSWORD)
    with pytest.raises(us.AccountError, match="already"):
        store.create("IAN@uci.edu", "another-password")
    assert [u["email"] for u in store.users()] == ["ian@uci.edu"]


def test_a_new_account_is_named_after_its_email_unless_named(store):
    assert store.create("ian@uci.edu", PASSWORD)["name"] == "ian"
    assert store.create("bo@uci.edu", PASSWORD, name="Bo")["name"] == "Bo"


# -- passwords --------------------------------------------------------------------------

def test_a_password_is_a_salted_scrypt_hash_never_the_text(store):
    store.create("ian@uci.edu", PASSWORD)
    store.create("bo@uci.edu", PASSWORD)
    db = sqlite3.connect(str(store.path))
    try:
        rows = db.execute("SELECT email, password_hash, password_salt FROM users "
                          "ORDER BY email").fetchall()
    finally:
        db.close()
    (_, hash_bo, salt_bo), (_, hash_ian, salt_ian) = rows
    assert len(bytes.fromhex(salt_ian)) == 16 and salt_ian != salt_bo
    assert hash_ian != hash_bo, "the same password under two salts must differ"
    algorithm, n, r, p, digest = hash_ian.split("$")
    assert algorithm == "scrypt"
    assert digest == hashlib.scrypt(PASSWORD.encode(), salt=bytes.fromhex(salt_ian),
                                    n=int(n), r=int(r), p=int(p),
                                    dklen=len(bytes.fromhex(digest))).hex()
    assert PASSWORD.encode() not in store.path.read_bytes()


def test_verify_accepts_the_password_and_nothing_else(store):
    store.create("ian@uci.edu", PASSWORD)
    assert store.verify("ian@uci.edu", PASSWORD) is True
    assert store.verify(" IAN@uci.edu ", PASSWORD) is True
    for wrong in ("", PASSWORD.upper(), PASSWORD + " ", PASSWORD[:-1], None):
        assert store.verify("ian@uci.edu", wrong) is False
    assert store.verify("nobody@uci.edu", PASSWORD) is False


def test_the_compare_is_constant_time(store, monkeypatch):
    """The digest comparison goes through `hmac.compare_digest`, never `==`."""
    store.create("ian@uci.edu", PASSWORD)
    compared = []
    real = hmac.compare_digest

    def spy(a, b):
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(us.hmac, "compare_digest", spy)
    assert store.verify("ian@uci.edu", PASSWORD) is True
    assert store.verify("ian@uci.edu", "wrong-password") is False
    assert len(compared) == 2


def test_a_short_password_is_refused_and_not_quoted(store):
    with pytest.raises(us.AccountError) as refused:
        store.create("ian@uci.edu", "abc")
    assert "abc" not in str(refused.value)
    assert str(us.MIN_PASSWORD) in str(refused.value)
    assert store.user("ian@uci.edu") is None


def test_set_password_replaces_the_hash_and_the_salt(store):
    store.create("ian@uci.edu", PASSWORD)
    store.set_password("ian@uci.edu", "a-new-password")
    assert store.verify("ian@uci.edu", "a-new-password")
    assert not store.verify("ian@uci.edu", PASSWORD)
    with pytest.raises(us.AccountError, match="No account"):
        store.set_password("nobody@uci.edu", "a-new-password")


def test_no_secret_in_any_repr_read_or_event(store):
    since = events.latest_id
    store.create("ian@uci.edu", PASSWORD, name="Ian")
    store.verify("ian@uci.edu", PASSWORD)
    store.verify("ian@uci.edu", "a-wrong-password")
    store.set_password("ian@uci.edu", "second-password-77")
    store.touch_sign_in("ian@uci.edu")
    db = sqlite3.connect(str(store.path))
    try:
        stored_hash, stored_salt = db.execute(
            "SELECT password_hash, password_salt FROM users").fetchone()
    finally:
        db.close()
    said = _everything_the_store_says(store, "ian@uci.edu")
    said += " ".join(f"{e.title} {e.message}" for e in events.since(since))
    for secret in (PASSWORD, "a-wrong-password", "second-password-77",
                   stored_hash, stored_salt, stored_hash.split("$")[-1]):
        assert secret not in said
    assert set(store.user("ian@uci.edu")) == {
        "email", "name", "created_at", "last_sign_in", "note", "must_set_password"}


# -- preferences and settings -------------------------------------------------------------

def test_preferences_round_trip_with_their_types(store):
    store.create("ian@uci.edu", PASSWORD)
    store.remember("ian@uci.edu", "Stepper Probe", {"x_step": 8, "man_full_speed": 250})
    store.remember("ian@uci.edu", "Rotator", {"step_deg": 0.5})
    store.remember("ian@uci.edu", "Stepper Probe", {"x_step": 9})   # an update, not a row
    assert store.preferences("ian@uci.edu") == {
        "Stepper Probe": {"x_step": 9, "man_full_speed": 250},
        "Rotator": {"step_deg": 0.5}}
    assert isinstance(store.preferences("IAN@uci.edu")["Rotator"]["step_deg"], float)
    assert store.preferences("bo@uci.edu") == {}


def test_preferences_belong_to_an_account(store):
    with pytest.raises(us.AccountError, match="No account"):
        store.remember("nobody@uci.edu", "Rotator", {"step_deg": 0.5})


def test_settings_are_a_hook_for_later(store):
    store.create("ian@uci.edu", PASSWORD)
    assert store.setting("ian@uci.edu", "layout") is None
    assert store.setting("ian@uci.edu", "layout", "default") == "default"
    store.put_setting("ian@uci.edu", "layout", {"collapsed": ["Rotator"]})
    store.put_setting("ian@uci.edu", "layout", {"collapsed": []})
    assert store.settings("ian@uci.edu") == {"layout": {"collapsed": []}}


def test_rename_and_the_sign_in_stamp(store):
    store.create("ian@uci.edu", PASSWORD)
    assert store.user("ian@uci.edu")["last_sign_in"] is None
    store.touch_sign_in("ian@uci.edu")
    store.rename("ian@uci.edu", "  Ian A.  ")
    record = store.user("ian@uci.edu")
    assert record["name"] == "Ian A." and record["last_sign_in"]
    with pytest.raises(us.AccountError):
        store.rename("ian@uci.edu", "   ")


# -- the Phase 1 profiles migrate ------------------------------------------------------------

def test_each_profile_becomes_an_account_that_must_set_a_password(store, tmp_path):
    source = pf.LocalFilesSource(tmp_path / "profiles")
    source.add_user("ialbinog", "Ian")
    source.add_user("Trainee1", "A trainee")
    source.put("user", "ialbinog", "model_params",
               {"Stepper Probe": {"x_step": 8}, "Rotator": {"step_deg": 0.5}})
    assert store.migrate_profiles(source) == ["ialbinog", "trainee1"]
    ian = store.user("ialbinog")
    assert ian["name"] == "Ian" and ian["must_set_password"] is True
    assert "profile" in ian["note"]
    assert store.preferences("ialbinog") == {"Stepper Probe": {"x_step": 8},
                                            "Rotator": {"step_deg": 0.5}}
    assert store.verify("ialbinog", "") is False and store.verify("ialbinog", "x") is False
    store.set_password("ialbinog", PASSWORD)
    assert store.user("ialbinog")["must_set_password"] is False
    assert store.verify("ialbinog", PASSWORD)


def test_migration_runs_once_and_never_overwrites(store, tmp_path):
    source = pf.LocalFilesSource(tmp_path / "profiles")
    source.add_user("ialbinog", "Ian")
    store.migrate_profiles(source)
    store.set_password("ialbinog", PASSWORD)
    store.remember("ialbinog", "Rotator", {"step_deg": 2.0})
    source.put("user", "ialbinog", "model_params", {"Rotator": {"step_deg": 9.0}})
    assert store.migrate_profiles(source) == []
    assert store.verify("ialbinog", PASSWORD)
    assert store.preferences("ialbinog") == {"Rotator": {"step_deg": 2.0}}


def test_nothing_to_migrate_creates_no_file(store, tmp_path):
    assert store.migrate_profiles(pf.LocalFilesSource(tmp_path / "profiles")) == []
    assert not store.path.exists()


def test_the_reserved_settings_keys_are_documented_hooks(store):
    """AC-4: per-user GUI layout, default fields and sample bases are kept
    for later under fixed `settings` keys; nothing reads them yet, and each
    is described where the table is."""
    assert us.RESERVED_SETTINGS == (us.LAYOUT, us.DEFAULT_FIELDS, us.SAMPLE_BASE) == (
        "layout", "default_fields", "sample_base")
    for key in us.RESERVED_SETTINGS:
        assert f"`{key}`" in us.__doc__, key
    store.create("ian@uci.edu", PASSWORD)
    store.put_setting("ian@uci.edu", us.LAYOUT, {"open": {"Rotator": [2]}})
    store.put_setting("ian@uci.edu", us.DEFAULT_FIELDS, {"Transfer Map": {"sample_id": "S"}})
    store.put_setting("ian@uci.edu", us.SAMPLE_BASE, None)
    assert set(store.settings("ian@uci.edu")) == set(us.RESERVED_SETTINGS)
