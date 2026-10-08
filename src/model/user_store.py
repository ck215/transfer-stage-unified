"""The accounts file: who may sign in at this station, and what each of them
keeps (owner request 2026-10-07: "user accounts re-enabled in the MVC shape").

    <profiles root>/users.sqlite      beside users.json and station.json

The profiles root is `profile.profiles_root()` (`STATION_PROFILES_DIR`, else
`<data root>/profiles`), so the accounts live outside the checkout, are never
committed, and every test gets its own file.

Three tables, `PRAGMA user_version` 1:

- `users(email PRIMARY KEY, name, password_hash, password_salt, created_at,
  last_sign_in, note)`. An email is an identity string, checked loosely (one
  `@`, no spaces) and kept lower-case; nothing is ever sent to it. A row with
  no hash is an account that must set its password at its next sign-in (the
  Phase 1 profiles migrate that way: `must_set_password`, derived from the
  missing hash so the flag and the hash cannot disagree).
- `preferences(email, model_name, param, value)`, one row per remembered
  parameter, the value JSON-encoded so an int stays an int.
- `settings(email, key, value)`: kept for later, nothing reads it yet. Three
  keys are reserved (`RESERVED_SETTINGS`), each a JSON value, none of them a
  Param and so none under the Q4 split:
  `layout` - how the Web view is arranged for this person (open disclosures
  by model and tier, collapsed sections, page order); the User sheet would
  publish it in `state` and take it back through one internal command with
  the JSON as its argument, so the view still holds only the Controller, and
  a Guest has none (the view's defaults).
  `default_fields` - `{model: {attr: value}}` for text fields that are not
  preferences (a sample-ID prefix, the default figure, an export folder),
  handed to each model by `User.load_into` through an optional duck-typed
  hook, so a model without the hook is untouched.
  `sample_base` - which Sample Map store this person works in: a path the
  Sample Map opens on sign-in (as `TransferMap.choices` keeps the station's
  trial store), or one shared store filtered by the `owner` column the
  Sample Map already writes; which of the two is the owner's call.

**Passwords** (supersedes Q1 of 2026-10-04, "no credential on a station", by
the owner's request of 2026-10-07): `hashlib.scrypt` with a 16-byte salt per
user, the cost written beside the digest (`scrypt$n$r$p$hex`) so it can grow
without a migration, compared with `hmac.compare_digest`. A password is a
call argument only: no read returns a hash or a salt, no repr shows one, no
event or refusal quotes one, and nothing here logs anything about one.

The Sample and Transfer Map stores' conventions: a connection per call and
one write lock; `CREATE TABLE IF NOT EXISTS`, then an additive migration by
column presence; a read of a missing file answers empty and creates nothing.
SQL text is built only from this module's own names; every value is bound.
"""
import datetime
import hashlib
import hmac
import json
import os
import sqlite3
import threading
from pathlib import Path

from model import profile

FILE_NAME = "users.sqlite"
#: `PRAGMA user_version`. 1: the first accounts file (2026-10-07).
SCHEMA_VERSION = 1
#: The shortest password an account may have (owner-adjustable; a PIN's
#: length, since Q1 asked for a PIN).
MIN_PASSWORD = 4
#: scrypt's cost: 16 MiB and about 40 ms on the lab Mac. Stored per hash.
SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_LENGTH = 2 ** 14, 8, 1, 32
SALT_BYTES = 16

_TABLES = {
    "users": (("email", "TEXT PRIMARY KEY"), ("name", "TEXT"),
              ("password_hash", "TEXT"), ("password_salt", "TEXT"),
              ("created_at", "TEXT"), ("last_sign_in", "TEXT"), ("note", "TEXT")),
    "preferences": (("email", "TEXT"), ("model_name", "TEXT"), ("param", "TEXT"),
                    ("value", "TEXT")),
    "settings": (("email", "TEXT"), ("key", "TEXT"), ("value", "TEXT")),
}
_KEYS = {"preferences": "PRIMARY KEY (email, model_name, param)",
         "settings": "PRIMARY KEY (email, key)"}
_CREATE = tuple(
    "CREATE TABLE IF NOT EXISTS " + table + " ("
    + ", ".join([name + " " + kind for name, kind in cols]
                + ([_KEYS[table]] if table in _KEYS else [])) + ")"
    for table, cols in _TABLES.items())
#: `settings` keys kept for later (AC-4, 2026-10-07); see the module docstring.
LAYOUT, DEFAULT_FIELDS, SAMPLE_BASE = "layout", "default_fields", "sample_base"
RESERVED_SETTINGS = (LAYOUT, DEFAULT_FIELDS, SAMPLE_BASE)
#: What a read hands back about an account: never the hash or the salt.
PUBLIC = ("email", "name", "created_at", "last_sign_in", "note")


class AccountError(ValueError):
    """An account operation refused; the message is the operator's words and
    never quotes a password."""


def default_path():
    """`<profiles root>/users.sqlite`."""
    return profile.profiles_root() / FILE_NAME


def now():
    """Local time with its UTC offset, to the second."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_email(text):
    """The key an identity is stored and looked up under."""
    return "" if text is None else str(text).strip().lower()


def check_email(text):
    """A new account's email, loosely: one `@` with something on each side,
    no whitespace. Returns it normalized; raises AccountError otherwise."""
    email = normalize_email(text)
    local, at, domain = email.partition("@")
    if (not at or not local or not domain or "@" in domain
            or any(c.isspace() for c in email)):
        raise AccountError("An email is one '@' with a name on each side and no "
                           "spaces, for example name@uci.edu.")
    return email


def check_password(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise AccountError(f"A password is at least {MIN_PASSWORD} characters.")
    return password


def hash_password(password, salt=None):
    """`(hash_text, salt_hex)` for `password`; a fresh salt unless given."""
    salt = os.urandom(SALT_BYTES) if salt is None else salt
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N,
                            r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LENGTH)
    return (f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${digest.hex()}", salt.hex())


def password_matches(password, hash_text, salt_hex):
    """Recompute under the stored cost and compare in constant time. False
    for anything malformed, never an exception that could carry the text."""
    if not isinstance(password, str) or not hash_text or not salt_hex:
        return False
    try:
        algorithm, n, r, p, digest_hex = str(hash_text).split("$")
        if algorithm != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(actual, expected)


def _public(row):
    record = {key: row.get(key) for key in PUBLIC}
    record["must_set_password"] = not row.get("password_hash")
    return record


class UserStore:
    """The SQLite file. A connection per call, one lock for writes; a read of
    a file that does not exist answers empty and creates nothing."""

    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.Lock()

    def __repr__(self):
        return f"UserStore({str(self.path)!r})"

    @property
    def exists(self):
        return self.path.is_file()

    # -- plumbing -----------------------------------------------------------------
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    def write(self, fn):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = self._connect()
            try:
                with db:
                    for statement in _CREATE:
                        db.execute(statement)
                    self._migrate(db)
                    return fn(db)
            finally:
                db.close()

    @staticmethod
    def _migrate(db):
        """Every column the file lacks is added (additive only); the version
        is written once the file has them all."""
        if db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            return
        for table, cols in _TABLES.items():
            have = {row[1] for row in db.execute("PRAGMA table_info(" + table + ")")}
            for name, kind in cols:
                if name not in have:
                    # A column added later cannot be a key; the type is enough.
                    db.execute("ALTER TABLE " + table + " ADD COLUMN " + name
                               + " " + kind.replace(" PRIMARY KEY", ""))
        db.execute("PRAGMA user_version = " + str(int(SCHEMA_VERSION)))

    def read(self, sql, args=()):
        if not self.exists:
            return []
        db = self._connect()
        try:
            return [dict(row) for row in db.execute(sql, args)]
        except sqlite3.OperationalError:
            return []       # a table or column an older file does not have yet
        finally:
            db.close()

    def _row(self, email):
        rows = self.read("SELECT * FROM users WHERE email = ?", (normalize_email(email),))
        return rows[0] if rows else None

    def _known(self, db, email):
        found = db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if found is None:
            raise AccountError(f"No account for {email} on this station.")

    # -- accounts -------------------------------------------------------------------
    def users(self):
        """Every account, by email; public fields only."""
        return [_public(row) for row in self.read("SELECT * FROM users ORDER BY email")]

    def user(self, email):
        """One account's public fields, or None."""
        row = self._row(email)
        return _public(row) if row else None

    def create(self, email, password, name=None, note=""):
        """A new account with a password. Refuses a taken email (in any case),
        a malformed one and a short password, before anything is written."""
        email = check_email(email)
        check_password(password)
        name = str(name or "").strip() or email.partition("@")[0]
        hashed, salt = hash_password(password)

        def _do(db):
            if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise AccountError(f"An account for {email} already exists. Sign in "
                                   "instead.")
            db.execute("INSERT INTO users (email, name, password_hash, password_salt, "
                       "created_at, note) VALUES (?, ?, ?, ?, ?, ?)",
                       (email, name, hashed, salt, now(), str(note or "")))
        self.write(_do)
        return self.user(email)

    def must_set_password(self, email):
        record = self.user(email)
        return bool(record and record["must_set_password"])

    def verify(self, email, password):
        """True only for the account's own password. An unknown email, an
        account with no password yet and a wrong password are all False."""
        row = self._row(email)
        if row is None:
            return False
        return password_matches(password, row.get("password_hash"),
                                row.get("password_salt"))

    def set_password(self, email, password):
        """A new hash under a new salt (the first password of a migrated
        profile, or a change the caller has already checked)."""
        email = normalize_email(email)
        check_password(password)
        hashed, salt = hash_password(password)

        def _do(db):
            self._known(db, email)
            db.execute("UPDATE users SET password_hash = ?, password_salt = ? "
                       "WHERE email = ?", (hashed, salt, email))
        self.write(_do)

    def rename(self, email, name):
        email, name = normalize_email(email), str(name or "").strip()
        if not name:
            raise AccountError("Type a name to show for this account.")

        def _do(db):
            self._known(db, email)
            db.execute("UPDATE users SET name = ? WHERE email = ?", (name, email))
        self.write(_do)
        return name

    def touch_sign_in(self, email):
        email = normalize_email(email)

        def _do(db):
            self._known(db, email)
            db.execute("UPDATE users SET last_sign_in = ? WHERE email = ?", (now(), email))
        self.write(_do)

    # -- preferences --------------------------------------------------------------------
    def preferences(self, email):
        """`{model_name: {param: value}}`, as remembered; {} for none."""
        out = {}
        for row in self.read("SELECT model_name, param, value FROM preferences "
                             "WHERE email = ? ORDER BY model_name, param",
                             (normalize_email(email),)):
            try:
                value = json.loads(row["value"])
            except (TypeError, ValueError):
                continue        # a hand-edited row that is not JSON: skipped
            out.setdefault(row["model_name"], {})[row["param"]] = value
        return out

    def remember(self, email, model_name, values):
        """Upsert `{param: value}` for one model. The caller decides what may
        be kept (`User.remember` applies the Q4 split); this only stores."""
        email = normalize_email(email)
        rows = [(email, str(model_name), str(param), json.dumps(value))
                for param, value in (values or {}).items()]

        def _do(db):
            self._known(db, email)
            db.executemany("INSERT OR REPLACE INTO preferences (email, model_name, "
                           "param, value) VALUES (?, ?, ?, ?)", rows)
        self.write(_do)
        return dict(values or {})

    # -- settings (a hook; nothing reads them yet) ----------------------------------------
    def settings(self, email):
        out = {}
        for row in self.read("SELECT key, value FROM settings WHERE email = ? "
                             "ORDER BY key", (normalize_email(email),)):
            try:
                out[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                continue
        return out

    def setting(self, email, key, default=None):
        return self.settings(email).get(key, default)

    def put_setting(self, email, key, value):
        email = normalize_email(email)

        def _do(db):
            self._known(db, email)
            db.execute("INSERT OR REPLACE INTO settings (email, key, value) "
                       "VALUES (?, ?, ?)", (email, str(key), json.dumps(value)))
        self.write(_do)
        return value

    # -- the Phase 1 profiles ---------------------------------------------------------------
    def migrate_profiles(self, source):
        """Each name-only profile of `source` (a `profile.LocalFilesSource`)
        becomes an account with no password, its remembered parameters with
        it, so nothing the bench saved is lost; the account sets its password
        at its first sign-in. Never overwrites an account that exists, and
        writes nothing when there is nothing new. Returns the identities
        migrated now. The JSON files stay where they are."""
        known = {u["email"] for u in self.users()}
        fresh = []
        for record in source.users():
            identity = normalize_email(record["username"])
            if identity in known or identity in {f[0] for f in fresh}:
                continue
            body = source.documents("user", record["username"]).get("model_params", {})
            fresh.append((identity, record["username"], record["display_name"],
                          body if isinstance(body, dict) else {}))
        if not fresh:
            return []
        stamp = now()

        def _do(db):
            done = []
            for identity, username, name, body in fresh:
                if db.execute("SELECT 1 FROM users WHERE email = ?",
                              (identity,)).fetchone():
                    continue
                db.execute("INSERT INTO users (email, name, created_at, note) "
                           "VALUES (?, ?, ?, ?)",
                           (identity, name, stamp,
                            f"migrated from the profile {username!r} (Phase 1); "
                            "sets a password at the first sign-in"))
                for model_name, values in body.items():
                    if not isinstance(values, dict):
                        continue
                    db.executemany(
                        "INSERT OR IGNORE INTO preferences (email, model_name, param, "
                        "value) VALUES (?, ?, ?, ?)",
                        [(identity, str(model_name), str(param), json.dumps(value))
                         for param, value in values.items() if value is not None])
                done.append(identity)
            return done
        return self.write(_do)
