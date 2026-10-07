"""Profiles: who is at the station, and the preferences that follow them
(proposal-user-system.md sections 2-3, Phase 1: local files, no server).

Scopes merge `lab < station < user < session`, later wins (section 2.3).
The lab scope is the code's own `Param` defaults, so it is never written;
the station and user scopes are JSON files under the profiles root.

Owner answers of 2026-10-04:

- **Q1, signing in**: by name, with a PIN once a lab server can check one.
  With no server (all of Phase 1) a session is `offline-unverified`, and no
  PIN or PIN hash is ever stored, logged or compared on a station: a PIN
  passed to `sign_in` is dropped on the floor. Records written in such a
  session say so (`operator_auth` / `owner_auth`).
- **Q4, what a user may keep**: `USER_PARAMS` (step sizes, manual and
  autonomous speed, the rotator step); `STATION_PARAMS` are station-only
  (the heater's PID and offset, Red Percent's `red_min`, the Sample Map's
  um per count); `NEVER_PARAMS` (the brake fields) are never a preference.
  Anything else (a setpoint, a target) is the operator's live value, not a
  preference. Validation runs on the effective document against each
  `Param`'s bounds; a bad value falls back one scope and warns once.

Phase 1 applies `model_params` and attribution. The `launch`,
`default_controller` and `controller_binds` namespaces of section 3 are not
read yet (the gamepad's identity and the binds touch the jog path and come
with their own tests); a document carrying them is kept intact.
"""
import json
import os
import re
from pathlib import Path

from events import events

STATION = "station"
STATION_DISPLAY = "Station"
#: How a session's identity was established: nobody signed in, signed in
#: with nothing to check the PIN against (Phase 1), and (Phase 2) checked.
AUTH_STATION, AUTH_OFFLINE, AUTH_VERIFIED = "station", "offline-unverified", "verified"

#: Q4 (owner 2026-10-04).
USER_PARAMS = frozenset({"x_step", "y_step", "z_step", "x_dist", "y_dist", "z_dist",
                         "full_speed", "man_full_speed", "step_deg"})
STATION_PARAMS = frozenset({"p_term", "i_term", "d_term", "offset", "red_min",
                            "um_per_count"})
NEVER_PARAMS = frozenset({"slow_speed", "brake_distance"})

_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
#: Keys that would be a credential in a user record: refused, never written.
_SECRET_KEYS = ("pin", "pin_hash", "password", "password_hash")


class ProfileError(ValueError):
    """A profile operation refused; the message is the operator's words."""


def profiles_root():
    """`STATION_PROFILES_DIR`, else `<data root>/profiles`, the data root being
    `TRANSFER_STAGE_DATA_ROOT` or `~/transfer-stage-runs` (the logs' root),
    outside the checkout and never committed."""
    configured = os.environ.get("STATION_PROFILES_DIR")
    if configured:
        return Path(configured).expanduser()
    root = os.environ.get("TRANSFER_STAGE_DATA_ROOT") or "~/transfer-stage-runs"
    return Path(root).expanduser() / "profiles"


# -- the merge (pure) ------------------------------------------------------------

def merge(layers):
    """`layers` = [(scope, document), ...] lowest first. Objects merge key by
    key, anything else replaces; `null` is not a value. Returns
    `(effective, provenance)` with provenance keyed by dotted path."""
    effective, provenance = {}, {}

    def walk(into, doc, scope, prefix):
        for key, value in doc.items():
            path = f"{prefix}{key}"
            if value is None:
                raise ProfileError(f"{path} is null in the {scope} document: a "
                                   "document sets values, it cannot unset one.")
            if isinstance(value, dict):
                into.setdefault(key, {})
                if not isinstance(into[key], dict):
                    into[key] = {}
                walk(into[key], value, scope, path + ".")
            else:
                into[key] = value
                provenance[path] = scope
    for scope, doc in layers:
        walk(effective, doc or {}, scope, "")
    return effective, provenance


RENAMED_MODELS = {"Red Percent": "RGB Analysis"}


def validate_model_params(scope, body, params_of):
    """Keep what `scope` may set and its Param accepts; return `(clean,
    problems)`, each problem one sentence naming the value."""
    clean, problems = {}, []
    for model_name, values in (body or {}).items():
        params = params_of(model_name) or {}
        if not params and model_name in RENAMED_MODELS:
            # Red Percent became RGB Analysis on 2026-10-07; a profile saved
            # under the old key keeps working and is written back under the
            # new one when the station knows only the new name.
            model_name = RENAMED_MODELS[model_name]
            params = params_of(model_name) or {}
        if not isinstance(values, dict):
            problems.append(f"{model_name}: expected a table of parameters.")
            continue
        for name, raw in values.items():
            where = f"{model_name}.{name}"
            if name in NEVER_PARAMS:
                problems.append(f"{where} is never a preference (a brake field).")
                continue
            allowed = USER_PARAMS if scope == "user" else USER_PARAMS | STATION_PARAMS
            if name not in USER_PARAMS | STATION_PARAMS:
                problems.append(f"{where} is not a preference (an operator's live "
                                "value).")
                continue
            if name not in allowed:
                problems.append(f"{where} is station-only: a user profile cannot "
                                "set it.")
                continue
            param = params.get(name)
            if param is None:
                problems.append(f"{where} is not a parameter of {model_name}.")
                continue
            ok, value = param.parse(raw)
            if not ok:
                problems.append(f"{where}: {value}.")
                continue
            clean.setdefault(model_name, {})[name] = value
    return clean, problems


# -- local files (Phase 1's PrefsSource) --------------------------------------------

def _check_username(username):
    if not isinstance(username, str) or not _USERNAME.match(username) or \
            username.lower() == STATION:
        raise ProfileError("A profile name is letters, digits, '.', '-' and '_' "
                           "(the UCInetID by convention), and not 'station'.")
    return username


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path)


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        events.warn("Profile File Unreadable", f"{path} could not be read; it is "
                    "ignored until fixed.", exception=exc)
        return {}


class LocalFilesSource:
    """`users.json`, `station.json` and `users/<name>.json` under `root`.
    Reads create nothing; writes are atomic. Never holds a credential."""

    def __init__(self, root):
        self.root = Path(root)

    def users(self):
        records = _read_json(self.root / "users.json").get("users", [])
        out = []
        for record in records:
            if isinstance(record, dict) and _USERNAME.match(str(record.get("username", ""))):
                out.append({"username": record["username"],
                            "display_name": str(record.get("display_name")
                                                or record["username"])})
        return out

    def put_user_record(self, record):
        if any(k in record for k in _SECRET_KEYS):
            raise ProfileError("A PIN or password is never stored on a station "
                               "(Q1): the lab server keeps credentials.")
        username = _check_username(record.get("username"))
        users = self.users()
        if any(u["username"].lower() == username.lower() for u in users):
            raise ProfileError(f"A profile named {username} already exists.")
        users.append({"username": username,
                      "display_name": str(record.get("display_name") or username)})
        _write_json(self.root / "users.json", {"users": users})
        return username

    def add_user(self, username, display_name=None):
        return self.put_user_record({"username": username, "display_name": display_name})

    def _path(self, scope, scope_id):
        if scope == "station":
            return self.root / "station.json"
        if scope == "user":
            return self.root / "users" / f"{_check_username(scope_id)}.json"
        raise ProfileError(f"{scope!r} is not a scope this station keeps.")

    def documents(self, scope, scope_id):
        """namespace -> body for one scope; {} when the file does not exist."""
        return _read_json(self._path(scope, scope_id))

    def put(self, scope, scope_id, namespace, body):
        path = self._path(scope, scope_id)
        docs = _read_json(path)
        docs[namespace] = body
        _write_json(path, docs)
        return body


# -- the service ----------------------------------------------------------------

class ProfileService:
    """Who is signed in, and their effective preferences. `params_of(name)`
    returns a model class's PARAMS (Setup passes the registry's)."""

    def __init__(self, source, params_of):
        self.source = source
        self.params_of = params_of
        self._user = STATION
        self._auth = AUTH_STATION
        self._warned = set()

    @property
    def users(self):
        return [{"username": STATION, "display_name": STATION_DISPLAY},
                *self.source.users()]

    @property
    def current_user(self):
        return self._user

    @property
    def auth(self):
        return self._auth

    def sign_in(self, username, pin=None):
        """Phase 1: there is no lab server to check `pin` against and no PIN
        hash may be cached (Q1), so the session is `offline-unverified` and
        the PIN is not stored, compared or logged."""
        del pin
        if username in (STATION, STATION_DISPLAY):
            self.sign_out()
            return self.session
        known = {u["username"]: u for u in self.source.users()}
        if username not in known:
            raise ProfileError(f"No profile named {username} on this station.")
        self._user, self._auth = username, AUTH_OFFLINE
        self._warned.clear()
        events.info("Signed In", f"{username} (offline: no lab server checked a "
                    "PIN; records say offline-unverified).", source="Profile")
        return self.session

    def sign_out(self):
        if self._user != STATION:
            events.info("Signed Out", f"{self._user}; the Station profile is active.",
                        source="Profile")
        self._user, self._auth = STATION, AUTH_STATION
        self._warned.clear()

    @property
    def session(self):
        return {"username": self._user, "auth": self._auth}

    @property
    def status(self):
        if self._user == STATION:
            return "Station profile (nobody signed in): the station's defaults."
        return (f"{self._user}, signed in offline, unverified (no lab server "
                "yet): records name you with that mark.")

    def _layer(self, scope, scope_id):
        body = self.source.documents(scope, scope_id).get("model_params", {})
        clean, problems = validate_model_params(scope, body, self.params_of)
        for problem in problems:
            key = (scope, scope_id, problem)
            if key not in self._warned:
                self._warned.add(key)
                events.warn("Profile Value Ignored",
                            f"{scope} profile{' ' + scope_id if scope_id else ''}: "
                            f"{problem} The next scope's value applies.",
                            source="Profile")
        return clean

    def effective_model_params(self):
        """`(effective, provenance)` over station and (signed in) user
        scopes; the lab scope is the models' own defaults, already set."""
        layers = [("station", self._layer("station", ""))]
        if self._user != STATION:
            layers.append(("user", self._layer("user", self._user)))
        return merge(layers)

    def remember(self, model_params):
        """Write `{model: {param: value}}` into the signed-in user's profile
        ("Remember for me"); refuses station-only and never parameters."""
        if self._user == STATION:
            raise ProfileError("Sign in first: the Station profile keeps no "
                               "personal settings.")
        clean, problems = validate_model_params("user", model_params, self.params_of)
        if problems:
            raise ProfileError(" ".join(problems))
        current = self.source.documents("user", self._user).get("model_params", {})
        merged, _ = merge([("user", current), ("user", clean)])
        self.source.put("user", self._user, "model_params", merged)
        return clean

    def save_station(self, model_params):
        """Write the station's defaults (station-only and user parameters;
        never the brakes). Refuses what it cannot keep."""
        clean, problems = validate_model_params("station", model_params, self.params_of)
        if problems:
            raise ProfileError(" ".join(problems))
        current = self.source.documents("station", "").get("model_params", {})
        merged, _ = merge([("station", current), ("station", clean)])
        self.source.put("station", "", "model_params", merged)
        return clean

    def add_profile(self, username, display_name=None):
        return self.source.add_user(username, display_name)
