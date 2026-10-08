"""The account menu (owner 2026-10-07): "The safety switch in the 'user'
model makes no sense, it is not a device. The user object should be treated
as another settings menu similar to tutorials and setup ... Rename setup to
settings after the initial settings menu"; then "Clicking on a sidebar field
should close any open side windows like setup", "user should be at the top
of the overview", and "guest users should have no access to transfer map or
sample map, just tool controls".

1. The signed-in `User` is Setup's own (`Setup.user`), never a Controller
   model: not in `state.models`, the nav, the stop, energized or active.
   The page reaches its sheet through `/api/user` and `__user__`.
2. The rail's account key, at the top of the page list, opens the sheet
   beside the rail (Settings closes); its name, password, "Remember current
   values as my defaults" and Sign out work from there.
3. The drawer and its key read "Setup" before the launch and "Settings"
   after it (no note on what is fixed: owner ruling 2026-10-08, a row's
   Hard reset applies a changed port).
4. A press on a rail page closes every side window and focuses the page; a
   pending confirmation keeps priority.
5. A Guest gets the tool controls only: no Transfer Map or Sample Map, and
   their commands are refused by the server.

Driven against a real Setup on SIM models (no serial port is opened: no
scan runs) and, for the page, headless Chrome through the harness in
test_view_web_server.py (skipped where node or puppeteer is absent).
"""
import pytest

from controller.controller import Controller
from model.user_store import UserStore
from views.web.server import USER_NAME, WebView

from test_view_web_server import _browse, _get, _post, needs_browser

pytestmark = pytest.mark.usefixtures("profiles_on", "sample_map_on")

EMAIL, PASSWORD = "ialbinog@uci.edu", "correct-horse-4821"


@pytest.fixture
def station(tmp_path, monkeypatch):
    """A real Setup and Controller: a SIM Stepper Probe row, the maps on,
    and an account on the station. `make(signed_in=, launched=)`."""
    monkeypatch.setenv("STATION_PROFILES_DIR", str(tmp_path / "profiles"))
    monkeypatch.setenv("STATION_MAP_DB", str(tmp_path / "db" / "map.sqlite"))
    from controller.setup import Setup
    made = []

    def make(signed_in=True, launched=True, chosen=True):
        controller = Controller()
        setup = Setup(controller)
        setup.users.create(EMAIL, PASSWORD, name="ialbinog")
        assert setup.run("set_stepper_probe_port", args=("SIM",)).is_ok
        if signed_in:
            done = setup.run("sign_in", {"account_email": EMAIL,
                                         "account_password": PASSWORD})
            assert done.is_ok, done.reason
        elif chosen:
            assert setup.run("open_as_guest").is_ok
        if launched:
            setup.launch()
        view = WebView(controller, setup, port=0, open_browser=False)
        assert view.open()
        made.append((view, controller))
        return view, controller, setup
    yield make
    for view, controller in made:
        view.close()
        controller.close()


# -- 1: the user is not a device -----------------------------------------------------------------

def test_the_user_is_in_no_device_aggregate(station):
    view, controller, setup = station()
    assert "Stepper Probe" in controller.model_names
    assert "User" not in controller.model_names
    status, state = _get(view, "/api/state")
    assert status == 200 and "User" not in state["models"]
    assert "User" not in state.get("energized", [])
    assert "User" not in str(state.get("stop")) and "User" not in str(state.get("stop_words"))
    status, answer = _post(view, "/api/estop_all", {})
    assert "User" not in answer["confirmed"], "the FULL STOP stopped the user"
    _post(view, "/api/clear_estop_all", {"confirmed": True})
    assert controller.run("User", "sign_out").is_refused, "a model named User answered"


def test_the_sheet_is_served_and_run_as_the_account_menu(station):
    view, controller, setup = station()
    status, sheet = _get(view, "/api/user")
    assert status == 200 and sheet["state"]["user_name"] == "ialbinog"
    commands = {e.get("command") for s in sheet["schema"]["sections"] for e in s["elements"]}
    assert {"switch_user", "sign_out", "rename", "change_password",
            "remember_current"} <= commands
    assert not commands & {"toggle_estop", "estop", "clear_estop"}
    status, renamed = _post(view, "/api/run", {"name": USER_NAME, "command": "rename",
                                               "inputs": {"display_name": "Ian A."}})
    assert renamed["status"] == "ok", renamed
    assert UserStore().user(EMAIL)["name"] == "Ian A."
    assert setup.state["account"]["name"] == "Ian A."


# -- 5: a Guest has the tool controls only -------------------------------------------------------

def test_a_guest_launch_has_no_maps_and_their_commands_are_refused(station):
    from model.transfer_map import TransferMap
    view, controller, setup = station(signed_in=False)
    assert controller.model_names == ["Stepper Probe"], controller.model_names
    # Whatever is open, the server refuses a Guest a map's command - and
    # never a stop.
    controller.add(TransferMap.NAME, TransferMap(), {"model": TransferMap.NAME})
    status, refused = _post(view, "/api/run", {"name": TransferMap.NAME,
                                               "command": "arm", "inputs": {}})
    assert refused["status"] == "refused" and "signed-in users" in refused["reason"]
    status, stopped = _post(view, "/api/run", {"name": TransferMap.NAME,
                                               "command": "toggle_estop", "inputs": {}})
    assert "signed-in users" not in (stopped.get("reason") or "")
    controller.remove(TransferMap.NAME)
    done = setup.run("switch_user")
    assert done.is_ok
    signed = setup.run("sign_in", {"account_email": EMAIL, "account_password": PASSWORD})
    assert signed.is_ok, signed.reason
    assert TransferMap.NAME in controller.model_names, "the sign-in did not add the map"


# -- the page ------------------------------------------------------------------------------------

#: What the rail and the side windows show.
_SHOWN = r"""() => ({
  setupWord: document.getElementById('setup-link').textContent,
  setupHidden: document.getElementById('setup-link').hidden,
  drawerTitle: document.querySelector('#setup-drawer .drawer-title').textContent,
  drawerLabel: document.getElementById('setup-drawer').getAttribute('aria-label'),
  // The "Devices are fixed while the station runs" note is gone (owner
  // ruling 2026-10-08: a row's Hard reset applies a changed port).
  note: Boolean(document.getElementById('settings-note')),
  drawer: document.getElementById('setup-drawer').classList.contains('open'),
  account: document.getElementById('account-drawer').classList.contains('open'),
  accountText: document.getElementById('account-link').textContent.trim(),
  accountShown: !document.getElementById('account-link').hidden,
  accountAbove: (() => {
    const a = document.getElementById('account-link').getBoundingClientRect();
    const o = document.querySelector('#model-nav .overview-link');
    return Boolean(o) && a.bottom <= o.getBoundingClientRect().top + 1;
  })(),
  nav: Array.from(document.querySelectorAll('#model-nav .model-link')).map((l) => l.textContent),
  dots: Array.from(document.querySelectorAll('#model-nav .nav-dot')).length,
  gate: !document.getElementById('sign-in-gate').hidden,
  confirm: !document.getElementById('confirm-modal').hidden,
  focusInCards: document.getElementById('cards').contains(document.activeElement),
  focus: document.activeElement && (document.activeElement.id || document.activeElement.className),
})"""

_HELPERS = r"""
  const shown = () => page.evaluate(%(shown)s);
  const press = async (root, words) => {
    const want = words.toLowerCase();
    for (const b of await page.$$(root + ' button')) {
      const [text, seen] = await b.evaluate((n) => [n.textContent.trim().toLowerCase(),
                                                    n.getClientRects().length > 0]);
      if (text === want && seen) { await b.evaluate((n) => n.click()); return; }
    }
    throw new Error('no button ' + words + ' in ' + root);
  };
  const type = async (root, attr, text) => {
    const box = root + ' input[name="' + attr + '"]';
    await page.$eval(box, (n) => { n.focus(); n.select(); });
    await page.keyboard.press('Backspace');
    await page.keyboard.type(text);
  };
""" % {"shown": _SHOWN}


@needs_browser
def test_the_account_menu_opens_beside_the_rail_and_works(station, tmp_path):
    view, controller, setup = station()
    controller.models["Stepper Probe"].x_step = 7
    out = _browse(view, _HELPERS + r"""
      await until(() => !document.getElementById('account-link').hidden);
      const start = await shown();
      await page.click('#setup-link');
      await until(() => document.getElementById('setup-drawer').classList.contains('open'));
      const settings = await shown();
      await page.click('#account-link');
      await until(() => document.getElementById('account-drawer').classList.contains('open'));
      await until(() => document.querySelector('#account-body .card'));
      const opened = await shown();
      const stopOnTop = await page.evaluate(() => {
        const b = document.getElementById('full-stop').getBoundingClientRect();
        const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
        return Boolean(hit && hit.closest('#full-stop'));
      });
      await type('#account-body', 'display_name', 'Ian A.');
      await press('#account-body', 'Save name');
      await until(() => document.getElementById('account-link').textContent.includes('Ian A.'));
      await type('#account-body', 'current_password', '%(password)s');
      await type('#account-body', 'new_password', 'brand-new-pass-1');
      await press('#account-body', 'Change password');
      await sleep(600);
      await press('#account-body', 'Remember current values as my defaults');
      await sleep(600);
      const renamed = await shown();
      await page.keyboard.press('Escape');
      await sleep(300);
      const escaped = await shown();
      await page.click('#account-link');
      await until(() => document.getElementById('account-drawer').classList.contains('open'));
      await until(() => document.querySelector('#account-body .card'));
      await press('#account-body', 'Sign out');
      await until(() => !document.getElementById('sign-in-gate').hidden);
      const out = await shown();
      return { start, settings, opened, stopOnTop, renamed, escaped, out };
    """ % {"password": PASSWORD}, tmp_path)
    start = out["start"]
    assert start["accountShown"] and start["accountText"] == "ialbinog", start
    assert start["accountAbove"], "the account key is not above Overview"
    assert "User" not in " ".join(start["nav"]) and "ialbinog" not in " ".join(start["nav"])
    assert start["dots"] == len(start["nav"]) - 1, "a dot for something that is no device"
    assert out["settings"]["drawer"] and out["settings"]["setupWord"] == "Settings"
    opened = out["opened"]
    assert opened["account"] and not opened["drawer"], "two side windows at once"
    assert out["stopOnTop"], "the account menu covers the stop"
    assert out["renamed"]["accountText"] == "Ian A."
    assert UserStore().user(EMAIL)["name"] == "Ian A."
    assert UserStore().verify(EMAIL, "brand-new-pass-1"), "Change password did not land"
    assert UserStore().preferences(EMAIL)["Stepper Probe"]["x_step"] == 7
    assert not out["escaped"]["account"], "Escape did not close the account menu"
    assert out["out"]["gate"] and setup.user.is_guest
    assert "Transfer Map" not in controller.model_names, "a Guest kept the map"


@needs_browser
def test_a_guests_menu_says_guest_and_goes_to_the_sign_in_screen(station, tmp_path):
    view, controller, setup = station(signed_in=False)
    out = _browse(view, _HELPERS + r"""
      await until(() => !document.getElementById('account-link').hidden);
      await page.click('#account-link');
      await until(() => document.querySelector('#account-body .card'));
      const before = await shown();
      const words = await page.evaluate(() => document.getElementById('account-body').innerText);
      await press('#account-body', 'Sign in / Switch user');
      await until(() => !document.getElementById('sign-in-gate').hidden);
      return { before, words, after: await shown() };
    """, tmp_path)
    assert out["before"]["accountText"] == "Guest"
    assert not [n for n in out["before"]["nav"] if n in ("Transfer Map", "Sample DB")]
    assert "Guest" in out["words"] and "Change password" not in out["words"]
    assert out["after"]["gate"] and not out["after"]["account"]


@needs_browser
def test_setup_before_the_launch_settings_after(station, tmp_path):
    view, controller, setup = station(launched=False)
    out = _browse(view, _HELPERS + r"""
      await until(() => document.getElementById('setup-drawer').classList.contains('open'));
      const before = await shown();
      await press('#drawer-body', 'Launch');
      await until(() => !document.getElementById('setup-drawer').classList.contains('open'), 8000);
      await sleep(300);
      const launched = await shown();
      await page.click('#setup-link');
      await until(() => document.getElementById('setup-drawer').classList.contains('open'));
      const after = await shown();
      const closeTitle = await page.evaluate(() => document.getElementById('drawer-close').title);
      return { before, launched, after, closeTitle };
    """, tmp_path)
    before, after = out["before"], out["after"]
    assert (before["drawerTitle"], before["drawerLabel"]) == ("Setup", "Setup")
    assert before["note"] is False
    assert out["launched"]["setupWord"] == "Settings" and not out["launched"]["setupHidden"]
    assert (after["drawerTitle"], after["drawerLabel"]) == ("Settings", "Settings")
    assert after["note"] is False
    assert "Close Settings" in out["closeTitle"]


@needs_browser
def test_a_press_on_a_rail_page_closes_every_side_window(station, tmp_path):
    """Settings, the account menu and the Tutorials list each close on a
    press of a page in the rail, focus on the page; an open confirmation
    keeps priority (the press is not taken)."""
    view, controller, setup = station()
    out = _browse(view, _HELPERS + r"""
      const probe = '#model-nav [data-model="Stepper Probe"]';
      await until(() => document.querySelector('#model-nav [data-model="Stepper Probe"]'));
      await page.click('#setup-link');
      await until(() => document.getElementById('setup-drawer').classList.contains('open'));
      await page.click(probe);
      await sleep(300);
      const fromSettings = await shown();
      await page.click('#account-link');
      await until(() => document.getElementById('account-drawer').classList.contains('open'));
      await page.click('#model-nav .overview-link');
      await sleep(300);
      const fromAccount = await shown();
      await page.click('#tutorials-link');
      await until(() => !document.getElementById('tutorial-panel').hidden);
      await page.click(probe);
      await sleep(300);
      const tutorialsGone = await page.evaluate(() => document.getElementById('tutorial-panel').hidden);
      // A confirmation keeps priority: Settings open, then Quit asks; a
      // press on a page is not taken, and Cancel answers it.
      await page.click('#setup-link');
      await until(() => document.getElementById('setup-drawer').classList.contains('open'));
      await page.click('#quit-link');
      const asked = await until(() => !document.getElementById('confirm-modal').hidden, 3000);
      await page.click('#model-nav .overview-link');
      await sleep(300);
      const underConfirm = await shown();
      if (asked) await page.click('#confirm-no');
      return { fromSettings, fromAccount, tutorialsGone, asked, underConfirm };
    """, tmp_path)
    s = out["fromSettings"]
    assert not s["drawer"] and s["focusInCards"], s
    a = out["fromAccount"]
    assert not a["account"] and a["focusInCards"], a
    assert out["tutorialsGone"] is True
    assert out["asked"], "Quit did not ask"
    u = out["underConfirm"]
    assert u["confirm"] and u["drawer"], "a press on the rail dismissed a confirmation"
    assert "Stepper Probe" in controller.model_names
