"""The way in, sign-in screen -> Setup -> the station (owner 2026-10-07).

1. A step indicator ("1 Sign in, 2 Setup, 3 Dashboard") heads the sign-in
   screen and pre-launch Setup, the shown step marked aria-current="step",
   and is gone after the launch. It sits in the screen's flow: it never
   covers the rail's Quit, and at phone width the page does not scroll
   sideways.
2. The launch is one move: Setup slides away, the entries settle in, and
   focus lands on the page (#cards), not on the body.
3. After the launch nothing this page opens covers the rail's Stop: Settings,
   the account menu, the Tutorials list, a confirmation - at desktop and
   phone width.

Driven in headless Chrome through the harness in test_view_web_server.py
(skipped where node or puppeteer is absent), with the account fake of
test_view_web_sign_in.py as Setup.
"""
from test_view_web_server import _browse, needs_browser
from test_view_web_sign_in import served  # noqa: F401  (the fixture)

#: The step list as the page shows it.
_STEPS = r"""() => {
  const list = document.getElementById('steps');
  const shown = (n) => !n.hidden && n.getClientRects().length > 0;
  const items = Array.from(list.querySelectorAll('.step'));
  const box = (n) => { const b = n.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
  const quit = document.getElementById('quit-link');
  const overlaps = (a, b) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
  return {
    shown: shown(list),
    host: list.parentElement && (list.parentElement.id || list.parentElement.className),
    words: items.filter(shown).map((n) => n.textContent.trim()),
    current: items.filter((n) => n.getAttribute('aria-current') === 'step').map((n) => n.dataset.step),
    done: items.filter((n) => n.classList.contains('is-done')).map((n) => n.dataset.step),
    coversQuit: shown(list) && shown(quit) && overlaps(box(list), box(quit)),
    sideways: document.documentElement.scrollWidth > window.innerWidth,
  };
}"""

_CLICK = r"""
const click = async (words) => {
  for (const b of await page.$$('#drawer-body button')) {
    if ((await b.evaluate((n) => n.textContent)) === words) { await b.click(); return; }
  }
  throw new Error('no button ' + words);
};
"""


@needs_browser
def test_the_steps_mark_the_way_in_and_go_after_the_launch(served, tmp_path):
    view, controller, setup = served(chosen=False)
    out = _browse(view, _CLICK + r"""
      const steps = %(steps)s;
      await until(() => !document.getElementById('sign-in-gate').hidden);
      const signIn = await page.evaluate(steps);
      await page.setViewport({ width: 390, height: 844 });
      await sleep(300);
      const signInPhone = await page.evaluate(steps);
      await page.setViewport({ width: 1400, height: 900 });
      await page.click('#gate-guest');
      await until(() => document.getElementById('setup-drawer').classList.contains('open'));
      await sleep(300);
      const setupL = await page.evaluate(steps);
      await page.setViewport({ width: 390, height: 844 });
      await sleep(300);
      const setupPhone = await page.evaluate(steps);
      await page.setViewport({ width: 1400, height: 900 });
      await sleep(200);
      await click('Launch');
      await until(() => !document.getElementById('setup-drawer').classList.contains('open'));
      const leaving = await page.evaluate(steps);
      await sleep(800);
      const launched = await page.evaluate(steps);
      const focus = await page.evaluate(() => document.activeElement && document.activeElement.id);
      // Settings after the launch: no steps there.
      await page.click('#setup-link');
      await until(() => document.getElementById('setup-drawer').classList.contains('open'));
      await sleep(300);
      const settings = await page.evaluate(steps);
      return { signIn, signInPhone, setupL, setupPhone, leaving, launched, focus, settings };
    """ % {"steps": _STEPS}, tmp_path)
    sign_in = out["signIn"]
    assert sign_in["shown"] and sign_in["host"] == "gate-body", sign_in
    assert sign_in["words"] == ["Sign in", "Setup", "Dashboard"], sign_in
    assert sign_in["current"] == ["sign-in"] and sign_in["done"] == [], sign_in
    setup_l = out["setupL"]
    assert setup_l["shown"] and setup_l["host"] == "setup-drawer", setup_l
    assert setup_l["current"] == ["setup"] and setup_l["done"] == ["sign-in"], setup_l
    for name in ("signInPhone", "setupPhone", "signIn", "setupL"):
        state = out[name]
        assert state["shown"], (name, state)
        assert not state["coversQuit"], f"the steps cover Quit ({name})"
        assert not state["sideways"], f"the page scrolls sideways ({name})"
    # The launch lights "Dashboard" while Setup slides away (or the list is
    # already gone under a slow poll); then it is gone for the session.
    assert out["leaving"]["current"] in (["station"], []), out["leaving"]
    assert out["launched"]["shown"] is False, out["launched"]
    assert out["settings"]["shown"] is False, out["settings"]
    assert out["focus"] == "cards", f"focus after the launch: {out['focus']!r}"


@needs_browser
def test_without_accounts_the_steps_start_at_setup(served, tmp_path):
    view, controller, setup = served(with_account=False)
    out = _browse(view, r"""
      await until(() => document.getElementById('setup-drawer').classList.contains('open'));
      await sleep(400);
      return await page.evaluate(%(steps)s);
    """ % {"steps": _STEPS}, tmp_path)
    assert out["shown"] and out["words"] == ["Setup", "Dashboard"], out
    assert out["current"] == ["setup"], out


@needs_browser
def test_a_running_station_shows_no_steps_even_on_the_sign_in_screen(served, tmp_path):
    """A reload of a running station (or Switch user) is not the way in."""
    view, controller, setup = served(chosen=False)
    setup.launch()
    out = _browse(view, r"""
      await until(() => !document.getElementById('sign-in-gate').hidden);
      await sleep(600);
      return await page.evaluate(%(steps)s);
    """ % {"steps": _STEPS}, tmp_path)
    assert out["shown"] is False, out


#: Whether a click at the centre of the rail's Stop lands on it.
_STOP_ON_TOP = r"""() => {
  const b = document.getElementById('full-stop').getBoundingClientRect();
  if (!b.width || !b.height) return 'not drawn';
  const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
  return Boolean(hit && hit.closest('#full-stop')) || (hit ? hit.id || hit.className : 'nothing');
}"""


@needs_browser
def test_after_the_launch_no_layer_covers_the_rails_stop(served, tmp_path):
    view, controller, setup = served(chosen=True)
    out = _browse(view, _CLICK + r"""
      const onTop = %(on_top)s;
      await until(() => document.getElementById('setup-drawer').classList.contains('open'));
      await click('Launch');
      await until(() => !document.body.classList.contains('is-prelaunch'));
      await sleep(600);
      const seen = {};
      for (const [w, h, size] of [[1400, 900, 'L'], [390, 844, 'phone']]) {
        await page.setViewport({ width: w, height: h });
        await sleep(400);
        seen[size + ' sheet'] = await page.evaluate(onTop);
        await page.click('#setup-link');
        await until(() => document.getElementById('setup-drawer').classList.contains('open'));
        await sleep(350);
        seen[size + ' settings'] = await page.evaluate(onTop);
        await page.click('#account-link');
        await until(() => document.getElementById('account-drawer').classList.contains('open'));
        await sleep(350);
        seen[size + ' account'] = await page.evaluate(onTop);
        await page.click('#tutorials-link');
        await until(() => {
          const p = document.getElementById('tutorial-panel');
          return p && !p.hidden;
        });
        await sleep(350);
        seen[size + ' tutorials'] = await page.evaluate(onTop);
        await page.click('#quit-link');
        await until(() => !document.getElementById('confirm-modal').hidden);
        await sleep(200);
        seen[size + ' confirm'] = await page.evaluate(onTop);
        await page.click('#confirm-no');
        await until(() => document.getElementById('confirm-modal').hidden);
        await page.keyboard.press('Escape');
        await sleep(300);
      }
      return seen;
    """ % {"on_top": _STOP_ON_TOP}, tmp_path)
    covered = {state: hit for state, hit in out.items() if hit is not True}
    assert not covered, f"the rail's Stop is covered: {covered}"
    assert len(out) == 10, out
