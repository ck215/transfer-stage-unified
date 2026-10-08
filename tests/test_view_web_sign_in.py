"""The Web view's sign-in flow (2026-10-07): the sign-in screen, then Setup,
then the station.

1. A password typed into a `secret` entry was lost on blur: the entry
   committed itself (O14, `_commit`), the station's state says "" for a
   secret, the box redrew empty, and the next Sign in sent
   `"account_password": ""`. A secret never commits itself, keeps its text
   until a command that declares it runs, and is cleared after that.
2. Signing in withdrew Setup: the signed-in user's sheet (`User`) is a model
   in the Controller, and the drawer read any model as a launch.
3. The sign-in screen: shown while Setup's `state.account.chosen` is false,
   beside the rail (the stop stays reachable), in front of everything else;
   its three choices are Setup commands with the typed email and password as
   their inputs; a refusal is said on the screen, a confirmation is the
   page's own dialog; once chosen, Setup opens (or the sheet, when the
   station is running).

Driven in headless Chrome through the harness in test_view_web_server.py
(skipped where node or puppeteer is absent).
"""
import pytest

import schema as sch
from controller.controller import Controller
from model.user import secret
from panel import Panel
from param import Param
from result import NeedsConfirm, Refused
from views.web.server import WebView

from test_view_web_server import FakeProbe, _browse, needs_browser

RIGHT = "right-password-1"


class AccountSetup(Panel):
    """Setup's account surface, faked: the gate's commands allowed by name
    (as `controller.setup.Setup` allows them), a section with a secret
    entry (bug 1), and `state.account` (the gate's flag)."""
    NAME = "Setup"
    PARAMS = {"account_email": Param("account_email", "text", default="", label="Email"),
              "account_password": Param("account_password", "text", default="",
                                        label="Password"),
              "note": Param("note", "text", default="", label="Note")}
    SECRET_INPUTS = frozenset({"account_password"})
    GATE = frozenset({"sign_in", "create_account", "open_as_guest"})

    def __init__(self, chosen=False, with_account=True):
        super().__init__()
        self.chosen = chosen
        self.with_account = with_account
        self.is_launched = False
        self.signed_in = None
        self.calls = []                 # (command, email, password)
        self.commits = []
        self.controller = None

    @property
    def account_password(self):
        return ""                       # never read back, like the real one

    @account_password.setter
    def account_password(self, value):
        self._password = value

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Account",
            sch.entry("Email", "account_email", self.PARAMS["account_email"]),
            secret(sch.entry("Password", "account_password",
                             self.PARAMS["account_password"])),
            sch.button("Sign in", "sign_in", role="go",
                       inputs=("account_email", "account_password")),
            sch.entry("Note", "note", self.PARAMS["note"]),
            sch.button("Save note", "save_note"),
            sch.button("Switch user", "switch_user"),
            sch.button("Add sheet", "add_sheet"),
            sch.button("Launch", "launch"),
            layout="row",
        ))

    @property
    def state(self):
        snapshot = super().state
        snapshot["is_launched"] = self.is_launched
        if self.with_account:
            snapshot["account"] = {"enabled": True, "chosen": self.chosen,
                                   "signed_in": bool(self.signed_in),
                                   "email": self.signed_in or "",
                                   "status": self.signed_in or "Guest", "sheet": "User"}
        return snapshot

    def _allows(self, command, args=()):
        if command in self.GATE:
            return
        super()._allows(command, args)

    def _apply_inputs(self, inputs):
        inputs = dict(inputs or {})
        for name in ("account_email", "account_password"):
            if name in inputs:
                setattr(self, name, inputs.pop(name))
        super()._apply_inputs(inputs)

    def _take(self):
        password, self._password = getattr(self, "_password", ""), ""
        return password

    def run(self, command, inputs=None, args=()):
        if command == "_commit":
            self.commits.append(sorted(inputs or {}))
        return super().run(command, inputs, args)

    def sign_in(self, confirmed=False):
        password = self._take()
        self.calls.append(("sign_in", self.account_email, password))
        if password != RIGHT:
            raise Refused("That email and password do not match an account on "
                          "this station.")
        self.signed_in, self.chosen = self.account_email, True
        return "ok"

    def create_account(self, confirmed=False):
        self.calls.append(("create_account", self.account_email,
                           getattr(self, "_password", "")))
        if not confirmed:
            raise NeedsConfirm(f"Create an account for {self.account_email}?",
                               "create_account")
        self._take()
        self.signed_in, self.chosen = self.account_email, True
        return "ok"

    def open_as_guest(self):
        self.calls.append(("open_as_guest", "", ""))
        self.signed_in, self.chosen = None, True
        return "ok"

    def switch_user(self):
        self.signed_in, self.chosen = None, False
        return "ok"

    def save_note(self):
        self.calls.append(("save_note", self.note, getattr(self, "_password", "")))
        return "ok"

    def add_sheet(self):
        """What a sign-in does to the Controller: the user's sheet appears."""
        self.controller.add("User", FakeProbe(), {})
        return "ok"

    def launch(self):
        self.controller.add("Fake Probe", FakeProbe(), {})
        self.is_launched = True
        return "ok"


@pytest.fixture
def served():
    """An empty station (nothing launched) with the account fake as Setup."""
    views = []

    def make(**kwargs):
        controller = Controller()
        setup = AccountSetup(**kwargs)
        setup.controller = controller
        view = WebView(controller, setup, port=0, open_browser=False)
        assert view.open(), "the server did not bind an ephemeral port"
        views.append(view)
        return view, controller, setup
    yield make
    for view in views:
        view.close()


#: What the page shows right now.
_SHOWN = r"""() => ({
  gate: !document.getElementById('sign-in-gate').hidden,
  drawer: document.getElementById('setup-drawer').classList.contains('open'),
  setupLink: !document.getElementById('setup-link').hidden,
  error: document.getElementById('gate-error').textContent,
  password: document.getElementById('gate-password').value,
  confirm: !document.getElementById('confirm-modal').hidden,
  stopOnTop: (() => {
    const b = document.getElementById('full-stop').getBoundingClientRect();
    const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    return Boolean(hit && hit.closest('#full-stop'));
  })(),
  cardsInert: document.getElementById('cards').inert,
  drawerInert: document.getElementById('setup-drawer').inert,
  focus: document.activeElement && document.activeElement.id,
})"""


# -- 1: a secret entry keeps its text ------------------------------------------------------

@needs_browser
def test_a_typed_password_survives_leaving_the_box_and_travels_with_sign_in(served, tmp_path):
    """The bug as reproduced: type the email, type the password, Tab out,
    press Sign in. Before the fix the blur committed the password, the box
    redrew "" from state and Sign in sent an empty password."""
    view, controller, setup = served(chosen=True)
    out = _browse(view, r"""
      await until(() => document.getElementById('setup-drawer').classList.contains('open'));
      const box = (attr) => '#drawer-body input[name="' + attr + '"]';
      await page.click(box('account_email'));
      await page.keyboard.type('ian@uci.edu');
      await page.click(box('account_password'));
      await page.keyboard.type('%(right)s');
      await page.keyboard.press('Tab');
      await sleep(900);                 // several polls: a redraw would have emptied it
      const kept = await page.$eval(box('account_password'), (n) => [n.value, n.type]);
      // A command that does not declare the secret does not carry it.
      await page.click(box('note'));
      await page.keyboard.type('hello');
      const save = await page.$$('#drawer-body button');
      for (const b of save) {
        if ((await b.evaluate((n) => n.textContent)) === 'Save note') { await b.click(); break; }
      }
      await sleep(400);
      for (const b of await page.$$('#drawer-body button')) {
        if ((await b.evaluate((n) => n.textContent)) === 'Sign in') { await b.click(); break; }
      }
      await sleep(600);
      const after = await page.$eval(box('account_password'), (n) => n.value);
      return { kept, after };
    """ % {"right": RIGHT}, tmp_path)
    assert out["kept"] == [RIGHT, "password"], out
    assert ["account_password"] not in setup.commits, "a secret committed itself on blur"
    assert ["account_email"] in setup.commits, "a plain entry still commits on blur"
    assert ("save_note", "hello", "") in setup.calls, (
        f"a command that does not declare the password carried it: {setup.calls}")
    assert ("sign_in", "ian@uci.edu", RIGHT) in setup.calls, setup.calls
    assert out["after"] == "", "the password stayed in the box after Sign in ran"


# -- 2: the user's sheet is not a launch ------------------------------------------------------

@needs_browser
def test_signing_in_does_not_withdraw_setup_but_a_launch_does(served, tmp_path):
    view, controller, setup = served(chosen=True)
    out = _browse(view, r"""
      const click = async (words) => {
        for (const b of await page.$$('#drawer-body button')) {
          if ((await b.evaluate((n) => n.textContent)) === words) { await b.click(); return; }
        }
        throw new Error('no button ' + words);
      };
      await until(() => document.getElementById('setup-drawer').classList.contains('open'));
      await click('Add sheet');
      await sleep(1200);
      const afterSheet = await page.evaluate(%(shown)s);
      await click('Launch');
      await until(() => !document.getElementById('setup-drawer').classList.contains('open'));
      const afterLaunch = await page.evaluate(%(shown)s);
      return { afterSheet, afterLaunch };
    """ % {"shown": _SHOWN}, tmp_path)
    assert "User" in controller.model_names
    assert out["afterSheet"]["drawer"] is True, "the User sheet read as a launch"
    assert out["afterLaunch"]["drawer"] is False, "a real launch no longer withdraws Setup"


# -- 3: the sign-in screen --------------------------------------------------------------------

@needs_browser
def test_the_sign_in_screen_comes_first_and_leaves_the_stop_in_reach(served, tmp_path):
    view, controller, setup = served(chosen=False)
    out = _browse(view, r"""
      await until(() => !document.getElementById('sign-in-gate').hidden);
      await sleep(600);
      const shown = await page.evaluate(%(shown)s);
      const words = await page.evaluate(() => document.getElementById('sign-in-gate').innerText);
      const fields = await page.evaluate(() => ['gate-email', 'gate-password'].map((id) => {
        const n = document.getElementById(id);
        return [n.type, n.autocomplete];
      }));
      const tutorials = await page.evaluate(() => document.getElementById('tutorials-link').disabled);
      // The stop still stops from under the screen.
      const box = await page.evaluate(() => {
        const b = document.getElementById('full-stop').getBoundingClientRect();
        return [b.left + b.width / 2, b.top + b.height / 2];
      });
      await page.mouse.click(box[0], box[1]);
      await sleep(400);
      return { shown, words, fields, tutorials };
    """ % {"shown": _SHOWN}, tmp_path)
    shown = out["shown"]
    assert shown["gate"] is True and shown["drawer"] is False, shown
    assert shown["setupLink"] is False, "Setup is reachable before a choice"
    assert shown["stopOnTop"] is True, "the sign-in screen covers the stop"
    assert shown["cardsInert"] is True and shown["drawerInert"] is True, shown
    assert shown["focus"] == "gate-email", shown
    assert out["tutorials"] is True
    assert out["fields"] == [["email", "username"], ["password", "current-password"]]
    for words in ("Sign in", "Email", "Password", "Create account…", "Proceed as guest",
                  "If you have not been trained on accounts for this equipment, proceed "
                  "as guest for standard operation."):
        assert words in out["words"], words


@needs_browser
def test_signing_in_on_the_screen_sends_what_was_typed_and_opens_setup(served, tmp_path):
    view, controller, setup = served(chosen=False)
    out = _browse(view, r"""
      await until(() => !document.getElementById('sign-in-gate').hidden);
      await page.click('#gate-email');
      await page.keyboard.type('ian@uci.edu');
      await page.click('#gate-password');
      await page.keyboard.type('a-wrong-guess');
      await page.keyboard.press('Tab');
      await page.click('#gate-sign-in');
      await until(() => document.getElementById('gate-error').textContent !== '');
      const refused = await page.evaluate(%(shown)s);
      await page.click('#gate-password');
      await page.keyboard.type('%(right)s');
      await page.keyboard.press('Enter');
      await until(() => document.getElementById('sign-in-gate').hidden);
      await sleep(400);
      const after = await page.evaluate(%(shown)s);
      return { refused, after };
    """ % {"shown": _SHOWN, "right": RIGHT}, tmp_path)
    assert setup.calls[0] == ("sign_in", "ian@uci.edu", "a-wrong-guess"), setup.calls
    assert setup.calls[1] == ("sign_in", "ian@uci.edu", RIGHT), setup.calls
    refused = out["refused"]
    assert refused["gate"] is True and "do not match" in refused["error"], refused
    assert refused["password"] == "", "the password stayed in the box after it was sent"
    after = out["after"]
    assert after["gate"] is False and after["drawer"] is True, after
    assert after["drawerInert"] is False


@needs_browser
def test_create_account_asks_on_the_pages_dialog_over_the_screen(served, tmp_path):
    view, controller, setup = served(chosen=False)
    out = _browse(view, r"""
      await until(() => !document.getElementById('sign-in-gate').hidden);
      await page.click('#gate-email');
      await page.keyboard.type('bo@uci.edu');
      await page.click('#gate-password');
      await page.keyboard.type('%(right)s');
      await page.click('#gate-create');
      await until(() => !document.getElementById('confirm-modal').hidden);
      const asking = await page.evaluate(%(shown)s);
      const text = await page.evaluate(() => document.getElementById('confirm-text').textContent);
      await page.click('#confirm-yes');
      await until(() => document.getElementById('sign-in-gate').hidden);
      await sleep(300);
      return { asking, text, after: await page.evaluate(%(shown)s) };
    """ % {"shown": _SHOWN, "right": RIGHT}, tmp_path)
    assert out["asking"]["confirm"] is True and "bo@uci.edu" in out["text"]
    assert setup.signed_in == "bo@uci.edu"
    assert ("create_account", "bo@uci.edu", RIGHT) in setup.calls
    assert out["after"]["drawer"] is True


@needs_browser
def test_guest_goes_on_switch_user_comes_back_and_a_reload_keeps_the_choice(served, tmp_path):
    view, controller, setup = served(chosen=False)
    out = _browse(view, r"""
      await until(() => !document.getElementById('sign-in-gate').hidden);
      await page.click('#gate-guest');
      await until(() => document.getElementById('sign-in-gate').hidden);
      await sleep(300);
      const guest = await page.evaluate(%(shown)s);
      await page.reload({ waitUntil: 'load' });
      await sleep(1500);
      const reloaded = await page.evaluate(%(shown)s);
      for (const b of await page.$$('#drawer-body button')) {
        if ((await b.evaluate((n) => n.textContent)) === 'Switch user') { await b.click(); break; }
      }
      await until(() => !document.getElementById('sign-in-gate').hidden);
      const switched = await page.evaluate(%(shown)s);
      return { guest, reloaded, switched };
    """ % {"shown": _SHOWN}, tmp_path)
    assert ("open_as_guest", "", "") in setup.calls
    assert out["guest"]["gate"] is False and out["guest"]["drawer"] is True
    assert out["reloaded"]["gate"] is False and out["reloaded"]["drawer"] is True
    assert out["switched"]["gate"] is True and out["switched"]["drawer"] is False


@needs_browser
def test_leaving_the_screen_on_a_running_station_shows_its_sheet(served, tmp_path):
    view, controller, setup = served(chosen=False)
    controller.add("Fake Probe", FakeProbe(), {})
    setup.is_launched = True
    out = _browse(view, r"""
      await until(() => !document.getElementById('sign-in-gate').hidden);
      await sleep(600);
      await page.click('#gate-guest');
      await until(() => document.getElementById('sign-in-gate').hidden);
      await sleep(400);
      return await page.evaluate(%(shown)s);
    """ % {"shown": _SHOWN}, tmp_path)
    assert out["gate"] is False and out["drawer"] is False and out["setupLink"] is True


@needs_browser
def test_a_setup_that_says_nothing_about_accounts_has_no_gate(served, tmp_path):
    view, controller, setup = served(with_account=False)
    out = _browse(view, r"""
      await sleep(800);
      return await page.evaluate(%(shown)s);
    """ % {"shown": _SHOWN}, tmp_path)
    assert out["gate"] is False and out["drawer"] is True
