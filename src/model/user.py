"""The signed-in person (owner request 2026-10-07: "each signed-in user a
model that owns a config the Controller loads, Guest as the station
defaults"; and later the same day: "the user object should be treated as
another settings menu similar to tutorials and setup").

A `User` is a `Panel`, NOT a device `Model`: no device, no port, no stop
switch, no status, never in the Controller. Setup (the composition root,
which owns the session) keeps the one current `User` as `Setup.user`, and
the Web view draws its sheet as the rail's account menu through
`views.web.server.USER_NAME`. So it is in none of the device aggregates (the
FULL STOP, the stop words, energized, active, latched, the watchdog, a
launch) by construction, not by a filter in each.

It OWNS one account's config, kept in the accounts file (`model.user_store`):

- `config()` is `{model_name: {param: value}}`, as the account remembered it;
- `remember(model_name, values)` writes it, Q4 deciding what a user may keep
  (`profile.validate_model_params`, scope "user");
- `operator()` is `(operator_id, operator_auth)`, what the Transfer Map
  (`operator_id`, `operator_auth`) and the Sample DB (`owner`,
  `owner_auth`) stamp on what they record: the email and "password" for a
  signed-in user, "guest" and "guest" for a Guest.

A **Guest** is a User with no account row (`User()`, `User.guest()`): its
config is empty, so `load_into` gives every model the station's defaults and
nothing else. Opening as guest changes nothing.

`load_into(models, profiles)` applies the station's saved defaults and this
user's config to every open model through `apply_defaults` (what Setup did
for a Phase 1 profile at build and at sign-in) and stamps the operator;
`revert(models, profiles)` undoes a user: each model's user parameters back
to its own Params' defaults with the station's saved ones over them. Nothing
restarts; a gated setter that refuses is reported, not forced.

**The sheet** (the account menu). Who is signed in, Switch user and Sign
out (Setup's, handed in as `on_switch_user` / `on_sign_out`: the session and
the revert are the composition root's); then the name, Change password, and
"Remember current values as my defaults", which collects every open model's
user parameters (`profile.current_params`, the inverse of `apply_defaults`)
from `models()` - Setup hands in the Controller's open models - the way the
Transfer Map reads other models by duck type. Then the backup of
this user's stores (2026-10-08, moved here from Setup's "Station defaults"):
the "Last backup ... -> folder" line, Backup folder, Set backup folder and
Back up now, each Setup's (handed in as `backup_status`, `backup_folder`,
`on_set_backup_dir` and `on_back_up_now`: the backup service is the
composition root's). A Guest's sheet is who it is and Sign in / Switch user:
a Guest has no backup. The password entries are `SECRET_INPUTS` (the log
says `<redacted>`), carry `secret: True` for a renderer to mask, read back
as "" (never in `state`), and are forgotten when the command ends.
"""
import schema as sch
from events import events
from model import profile as pf
from model.user_store import AccountError, normalize_email
from panel import Panel
from param import Param
from result import Refused

#: `operator_auth` / `owner_auth` words written by the accounts.
AUTH_PASSWORD, AUTH_GUEST = "password", "guest"
#: A Guest's `operator_id`.
GUEST = "guest"
GUEST_NAME = "Guest"
#: The secret entries' attributes: never read back, never logged.
SECRETS = ("current_password", "new_password")


def secret(element):
    """Mark an entry as a secret for a renderer (masked, cleared after use).
    `schema.entry` has no keyword for it yet; the key rides on the plain
    dict, which every renderer already ignores when it does not know it."""
    element["secret"] = True
    return element


def _secret_property(name):
    def read(self):
        return ""           # never in `state`, never echoed to a view

    def write(self, value):
        self._typed[name] = "" if value is None else str(value)
    return property(read, write, doc=f"The typed {name.replace('_', ' ')}; reads as \"\".")


class User(Panel):
    NAME = "User"
    SECRET_INPUTS = frozenset(SECRETS)
    PARAMS = {p.name: p for p in (
        Param("display_name", "text", default="", label="Name"),
        Param("current_password", "text", default="", label="Current password"),
        Param("new_password", "text", default="", label="New password"),
        Param("backup_dir", "text", default="", label="Backup folder"),
    )}

    current_password = _secret_property("current_password")
    new_password = _secret_property("new_password")

    def __init__(self, store=None, email=None, params_of=None, models=None,
                 on_sign_out=None, on_switch_user=None, backup_status=None,
                 backup_folder=None, on_set_backup_dir=None, on_back_up_now=None):
        """`User()` is a Guest. `User(store=, email=)` is that account's user;
        an email with no account raises AccountError. `params_of(name)`
        returns a model class's PARAMS (Setup passes the registry's); without
        it the open models' own are used. `models()` returns the open models
        `{name: model}` (Setup passes the Controller's). `on_sign_out()` and
        `on_switch_user()` are Setup's, as are the backup's hooks:
        `backup_status()` (the status line), `backup_folder(email)` (the
        remembered folder, None for the default), `on_set_backup_dir(text)`
        and `on_back_up_now()`."""
        self._typed = {name: "" for name in SECRETS}   # before the Panel seeds them
        super().__init__()
        self._store = store
        self._email = None
        self._params_of = params_of
        self._models_of = models
        self._on_sign_out = on_sign_out
        self._on_switch_user = on_switch_user
        self._backup_status = backup_status
        self._on_set_backup_dir = on_set_backup_dir
        self._on_back_up_now = on_back_up_now
        if store is not None and email:
            record = store.user(email)
            if record is None:
                raise AccountError(f"No account for {normalize_email(email)} on "
                                   "this station.")
            self._email = record["email"]
            self.display_name = record["name"] or ""
            if callable(backup_folder):
                try:
                    self.backup_dir = backup_folder(self._email) or ""
                except Exception:
                    self.backup_dir = ""

    @classmethod
    def guest(cls, **hooks):
        return cls(**hooks)

    def __repr__(self):
        return f"User({self._email or GUEST!r})"

    # -- who ----------------------------------------------------------------------
    @property
    def is_guest(self):
        return self._email is None

    @property
    def email(self):
        """The account's email (its identity), or None for a Guest."""
        return self._email

    @property
    def user_name(self):
        if self.is_guest:
            return GUEST_NAME
        return str(self.display_name or "").strip() or self._email

    @property
    def auth(self):
        return AUTH_GUEST if self.is_guest else AUTH_PASSWORD

    def operator(self):
        """`(operator_id, operator_auth)` for the records the maps write."""
        return (GUEST, AUTH_GUEST) if self.is_guest else (self._email, AUTH_PASSWORD)

    @property
    def who(self):
        if self.is_guest:
            return "Guest: the station's defaults. Sign in to keep your own."
        return f"{self.user_name} ({self._email}), signed in with a password."

    # -- the config ------------------------------------------------------------------
    def config(self):
        """`{model_name: {param: value}}` as this account remembered it; {}
        for a Guest, so every model keeps the station's defaults."""
        if self.is_guest:
            return {}
        return self._store.preferences(self._email)

    def _open_models(self):
        """The open models, never this sheet: `{name: model}`."""
        try:
            found = self._models_of() if callable(self._models_of) else {}
        except Exception:
            found = {}
        return {name: model for name, model in dict(found or {}).items()
                if model is not self and not isinstance(model, User)}

    def _params_for(self, model_name):
        found = self._params_of(model_name) if self._params_of else None
        if not found:
            found = getattr(self._open_models().get(model_name), "PARAMS", None)
        return found or {}

    def remember(self, model_name, values):
        """Write `{param: value}` for one model into this account; refuses
        (AccountError, nothing written) what a user may not keep (Q4) or a
        value its Param refuses. Returns what was written."""
        if self.is_guest:
            raise AccountError("Sign in first: a Guest keeps no defaults.")
        clean, problems = pf.validate_model_params("user", {model_name: values},
                                                   self._params_for)
        if problems:
            raise AccountError(" ".join(problems))
        for name, kept in clean.items():
            self._store.remember(self._email, name, kept)
        return clean

    @property
    def remembered(self):
        """One line: what this account keeps, by model."""
        config = self.config()
        if not config:
            return "Nothing remembered yet." if not self.is_guest else ""
        named = {}
        for name, values in config.items():     # an old model name as it is now
            named.setdefault(pf.RENAMED_MODELS.get(name, name), set()).update(values)
        return "; ".join(f"{name}: {', '.join(sorted(values))}"
                         for name, values in sorted(named.items()))

    # -- applying it -----------------------------------------------------------------
    def load_into(self, models, profiles):
        """Apply the station's saved defaults and this user's config to each
        of `models` (`{name: model}`) through `apply_defaults`, and stamp who
        is working. `profiles` is the `profile.ProfileService` (the station
        scope and the validation). -> `{model: {param: reason}}` for what a
        model did not take."""
        effective, _ = profiles.effective_model_params(self.config(), self._email or "")
        return self._apply(models, lambda name, model: effective.get(name, {}), stamp=True)

    def revert(self, models, profiles):
        """Undo this user's config on `models`: each model's user parameters
        (Q4) back to its Params' defaults, the station's saved defaults over
        them. Live values and station-only values are not a user's to undo.
        Stamps nothing (the next user's `load_into` does)."""
        station, _ = profiles.effective_model_params()

        def values(name, model):
            return {**pf.param_defaults(model, pf.USER_PARAMS), **station.get(name, {})}
        return self._apply(models, values, stamp=False)

    def _apply(self, models, values_for, stamp):
        refused = {}
        operator_id, auth = self.operator()
        for model in list((models or {}).values()):
            if model is self or isinstance(model, User):
                continue
            name = getattr(model, "NAME", None)
            apply = getattr(model, "apply_defaults", None)
            if callable(apply) and name:
                problems = apply(values_for(name, model))
                if problems:
                    refused[name] = dict(problems)
            if stamp:
                if hasattr(model, "operator_id"):
                    model.operator_id, model.operator_auth = operator_id, auth
                if hasattr(model, "owner"):
                    model.owner, model.owner_auth = operator_id, auth
        return refused

    # -- commands ----------------------------------------------------------------------
    def _signed_in(self):
        if self.is_guest:
            raise Refused("Nobody is signed in: a Guest has the station's defaults "
                          "and keeps nothing.")

    def _setups(self, hook, what):
        if hook is None:
            raise Refused(f"This sheet is not connected to the station's Setup, so "
                          f"it cannot {what}.")
        return hook()

    def sign_out(self):
        self._signed_in()
        return self._setups(self._on_sign_out, "sign out")

    def switch_user(self):
        """Back to the sign-in screen (Setup's `switch_user`): a signed-in
        user is signed out first; a Guest just chooses again."""
        return self._setups(self._on_switch_user, "switch user")

    def rename(self):
        self._signed_in()
        try:
            name = self._store.rename(self._email, self.display_name)
        except AccountError as refusal:
            raise Refused(str(refusal))
        self.display_name = name
        return name

    def change_password(self):
        """Needs the current password; the new one is a fresh salted hash.
        Both typed values are forgotten whatever happens."""
        current, new = self._typed["current_password"], self._typed["new_password"]
        try:
            self._signed_in()
            if not self._store.verify(self._email, current):
                raise Refused("The current password is not right; nothing changed.")
            try:
                self._store.set_password(self._email, new)
            except AccountError as refusal:
                raise Refused(str(refusal))
        finally:
            for name in SECRETS:
                self._typed[name] = ""
        events.info("Password Changed", f"{self.user_name} ({self._email}).",
                    source=self.NAME)
        return "Password changed."

    def remember_current(self):
        """"Remember current values as my defaults": every open model's user
        parameters as they are now (Q4: never a station-only value, a brake
        or a live value)."""
        self._signed_in()
        collected = {}
        for name, model in sorted(self._open_models().items()):
            values = pf.current_params(model, pf.USER_PARAMS)
            if values:
                collected[getattr(model, "NAME", name) or name] = values
        if not collected:
            raise Refused("Nothing open has a value you can keep: launch a probe "
                          "or the Rotator first.")
        clean, problems = pf.validate_model_params("user", collected, self._params_for)
        for problem in problems:
            events.warn("Default Not Remembered", problem, source=self.NAME)
        for model_name, values in clean.items():
            self._store.remember(self._email, model_name, values)
        summary = "; ".join(f"{name}: {', '.join(sorted(values))}"
                            for name, values in sorted(clean.items()))
        events.info("Defaults Remembered", f"{self.user_name}: {summary}.",
                    source=self.NAME)
        return summary

    # -- the backup (2026-10-08: here, not on Setup) -------------------------------------
    @property
    def backup_status(self):
        """ "Last backup ... -> folder", or why there is none; "" for a
        Guest (no backup)."""
        if self.is_guest:
            return ""
        if not callable(self._backup_status):
            return "No backup: this sheet is not connected to the station's Setup."
        try:
            return str(self._backup_status())
        except Exception as exc:
            return f"The backup's state could not be read ({exc})."

    def set_backup_dir(self):
        """Set backup folder: this user's own folder (blank = the default)."""
        self._signed_in()
        hook = self._on_set_backup_dir
        typed = str(self.backup_dir or "").strip()
        folder = self._setups(hook and (lambda: hook(typed)), "set the backup folder")
        self.backup_dir = typed and str(folder or "")   # blank stays blank (the default)
        return folder

    def back_up_now(self):
        """Back up now: every open store, on the backup thread."""
        self._signed_in()
        return self._setups(self._on_back_up_now, "back up")

    # -- what a view reads ------------------------------------------------------------------
    @property
    def state(self):
        snapshot = super().state
        snapshot.update({"user_name": self.user_name, "is_guest": self.is_guest,
                         "email": self._email or ""})
        return snapshot

    @property
    def schema(self):
        if self.is_guest:
            # UX audit 2026-10-08 #13: a Guest is not signed in.
            return sch.schema(sch.section(
                "Guest",
                sch.readonly("Working as", "who", role="info"),
                sch.button("Sign in / Switch user", "switch_user", role="go"),
            ))
        P = self.PARAMS
        return sch.schema(
            sch.section(
                "Signed in",
                sch.readonly("Signed in", "who", role="info"),
                sch.button("Switch user", "switch_user", role="neutral"),
                sch.button("Sign out", "sign_out", role="neutral"),
            ),
            sch.section(
                "Name",
                sch.entry("Name", "display_name", P["display_name"]),
                sch.button("Save name", "rename", inputs=("display_name",)),
            ),
            sch.section(
                "Password",
                secret(sch.entry("Current password", "current_password",
                                 P["current_password"])),
                secret(sch.entry("New password", "new_password", P["new_password"])),
                sch.button("Change password", "change_password",
                           inputs=("current_password", "new_password")),
            ),
            sch.section(
                "My defaults",
                sch.button("Remember current values as my defaults",
                           "remember_current", role="go"),
                sch.readonly("Remembered", "remembered"),
            ),
            # 2026-10-08: the backup of this user's stores (was Setup's).
            sch.section(
                "Backup",
                sch.readonly("Backup", "backup_status", role="info"),
                sch.entry("Backup folder", "backup_dir", P["backup_dir"]),
                sch.button("Set backup folder", "set_backup_dir",
                           inputs=("backup_dir",), role="neutral"),
                sch.button("Back up now", "back_up_now", role="neutral"),
            ),
        )
