/**
 * Tutorials: step-by-step coaching drawn over the console, driven by the
 * data files in /tutorials/. Loaded after app.js and standing on nothing of
 * it: the DOM it can see and the same /api/state the page reads.
 *
 * Rules this file does not bend:
 *   - it never runs a command. The operator presses every control; the only
 *     requests are GETs (the tutorial files and /api/state), and the only
 *     thing it presses itself is a rail link, to show a page.
 *   - nothing from a file is ever assigned as markup: every node is built
 *     with createElement and every string lands in textContent.
 *   - it asks /api/state no faster than every POLL_MS, and only while a
 *     tutorial is running.
 */
(function () {
  'use strict';

  const POLL_MS = 500;
  const LAYOUT_MS = 250;
  const INDEX_URL = '/tutorials/index.json';
  const STORE_PREFIX = 'station.tutorial.';
  const NOT_SIM = 'This tutorial runs on simulated hardware. '
    + 'Set every port to SIM in Setup first.';
  const GAP = 10;
  const EDGE = 8;

  // -- small helpers ---------------------------------------------------------
  function make(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function button(label, cls, onClick) {
    const node = make('button', cls, label);
    node.type = 'button';
    node.addEventListener('click', onClick);
    return node;
  }

  function norm(text) {
    return String(text === undefined || text === null ? '' : text)
      .replace(/\s+/g, ' ').replace(/\s*(…|\.\.\.)\s*$/, '').trim();
  }

  function isVisible(node) {
    if (!node || !node.isConnected) return false;
    if (node.closest('[hidden], .is-phase-off')) return false;
    if (typeof node.checkVisibility === 'function') {
      return node.checkVisibility({ visibilityProperty: true });
    }
    return node.getClientRects().length > 0;
  }

  // -- progress, remembered per tutorial id ---------------------------------
  function readProgress(id) {
    try {
      const raw = window.localStorage.getItem(STORE_PREFIX + id);
      const got = raw ? JSON.parse(raw) : null;
      return got && Number.isInteger(got.step) && got.step >= 0 ? got.step : null;
    } catch (err) { return null; }
  }

  function writeProgress(id, step) {
    try {
      if (step === null) window.localStorage.removeItem(STORE_PREFIX + id);
      else window.localStorage.setItem(STORE_PREFIX + id, JSON.stringify({ step }));
    } catch (err) { /* storage blocked: the tutorial still runs */ }
  }

  // -- the files -------------------------------------------------------------
  function validWait(wait) {
    if (wait === null || wait === undefined) return true;
    if (typeof wait !== 'object') return false;
    if (wait.click === true) return true;
    const s = wait.state;
    return Boolean(s && typeof s.name === 'string' && typeof s.key === 'string' && 'equals' in s);
  }

  function validTutorial(t) {
    return Boolean(t && typeof t.id === 'string' && typeof t.title === 'string'
      && (t.requires === 'sim' || t.requires === 'any')
      && Array.isArray(t.steps) && t.steps.length
      && t.steps.every((s) => s && typeof s.page === 'string' && typeof s.say === 'string'
        && (!s.anchor || typeof s.anchor.text === 'string' || typeof s.anchor.selector === 'string')
        && validWait(s.wait)));
  }

  async function getJson(url) {
    const answer = await fetch(url, { cache: 'no-store' });
    if (!answer.ok) throw new Error(url + ' ' + answer.status);
    return answer.json();
  }

  async function loadTutorials() {
    const index = await getJson(INDEX_URL);
    const files = Array.isArray(index.tutorials) ? index.tutorials : [];
    const found = [];
    const failed = [];
    for (const file of files) {
      try {
        const t = await getJson('/tutorials/' + encodeURIComponent(String(file)));
        if (validTutorial(t)) found.push(t); else failed.push(String(file));
      } catch (err) { failed.push(String(file)); }
    }
    return { found, failed };
  }

  // -- the station's state (the page's own route, at a bounded cadence) ----
  async function readState() {
    const answer = await fetch('/api/state', { cache: 'no-store' });
    if (!answer.ok) throw new Error('state ' + answer.status);
    return answer.json();
  }

  function modelStates(state) {
    return (state && state.models && typeof state.models === 'object') ? state.models : {};
  }

  /** A hardware link that is not simulated. `hardware_devices` names the
   *  links a model owns, SIM ones included (a simulated serial port is still
   *  a link); the device's own status says whether it is real, the same
   *  word the rail's "Simulation, no hardware attached" line reads. */
  function hasHardware(state) {
    return Object.values(modelStates(state)).some((m) => {
      const links = (m && Array.isArray(m.hardware_devices)) ? m.hardware_devices : [];
      const devices = (m && m.devices && typeof m.devices === 'object') ? m.devices : {};
      return links.some((kind) => devices[kind] !== 'simulated');
    });
  }

  function stateMatches(state, spec) {
    const m = modelStates(state)[spec.name];
    if (!m) return false;
    let got = m[spec.key];
    if (got === undefined && m.values && typeof m.values === 'object') got = m.values[spec.key];
    return got === spec.equals;
  }

  // -- the runtime -----------------------------------------------------------
  const ui = { panel: null, list: null, note: null, card: null, ring: null, link: null };
  let tutorials = [];
  let failedFiles = [];
  let run = null;       // { t, i, anchor, placed, hold, armed }
  let lastState = null;
  let pollTimer = null;
  let layoutTimer = null;
  let polling = false;

  function stepOf() { return run.t.steps[run.i]; }

  function resolveAnchor(spec) {
    if (!spec) return null;
    if (spec.selector) {
      let node = null;
      try { node = document.querySelector(spec.selector); } catch (err) { node = null; }
      return isVisible(node) ? node : null;
    }
    const mine = (node) => ui.card.contains(node) || ui.panel.contains(node);
    // A control first (its text may carry the ellipsis a picker's button
    // does, which the schema's words do not), then a label, by its words.
    const want = norm(spec.text);
    for (const node of document.querySelectorAll('button, [role="button"], a, summary')) {
      if (!mine(node) && norm(node.textContent) === want && isVisible(node)) return node;
    }
    const exact = String(spec.text).replace(/\s+/g, ' ').trim();
    for (const node of document.querySelectorAll('label')) {
      if (!mine(node) && node.textContent.replace(/\s+/g, ' ').trim() === exact
        && isVisible(node)) return node;
    }
    return null;
  }

  function showPage(name) {
    const nav = document.getElementById('model-nav');
    if (!nav) return;
    let link = null;
    if (name === 'Overview') link = nav.querySelector('.overview-link');
    else {
      for (const node of nav.querySelectorAll('.model-link[data-model]')) {
        if (node.dataset.model === name) { link = node; break; }
      }
    }
    if (!link || link.getAttribute('aria-current') === 'page') return;
    // The page itself, not a press on its rail key: a press closes every
    // side window (owner 2026-10-07), and a step about Settings keeps it.
    const station = window.station;
    if (station && typeof station.showPage === 'function') {
      station.showPage(name === 'Overview' ? null : name);
    } else {
      link.click();
    }
  }

  /** Where the card may sit: right of the rail on a side rail, under it on
   *  the phone bar, and above the tray. The stop is in the rail. */
  function bounds() {
    const rail = document.querySelector('.rail');
    const tray = document.getElementById('log-panel');
    const b = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
    if (rail) {
      const r = rail.getBoundingClientRect();
      if (r.height >= window.innerHeight * 0.6) b.left = r.right; else b.top = r.bottom;
    }
    if (tray) {
      const t = tray.getBoundingClientRect();
      if (t.height > 0 && t.top > b.top) b.bottom = Math.min(b.bottom, t.top);
    }
    return b;
  }

  function place() {
    if (!run) return;
    const b = bounds();
    const card = ui.card;
    card.style.maxWidth = Math.max(180, Math.min(352, b.right - b.left - 2 * EDGE)) + 'px';
    const cw = card.offsetWidth;
    const ch = card.offsetHeight;
    const clampX = (x) => Math.max(b.left + EDGE, Math.min(x, b.right - cw - EDGE));
    const clampY = (y) => Math.max(b.top + EDGE, Math.min(y, b.bottom - ch - EDGE));
    const a = run.anchor;
    if (!a || !isVisible(a)) {
      ui.ring.hidden = true;
      card.style.left = clampX(b.right - cw - EDGE * 2) + 'px';
      card.style.top = clampY(b.top + EDGE * 2) + 'px';
      return;
    }
    const r = a.getBoundingClientRect();
    ui.ring.hidden = false;
    ui.ring.style.left = (r.left - 4) + 'px';
    ui.ring.style.top = (r.top - 4) + 'px';
    ui.ring.style.width = (r.width + 8) + 'px';
    ui.ring.style.height = (r.height + 8) + 'px';
    let x = clampX(r.left);
    let y;
    if (r.bottom + GAP + ch <= b.bottom - EDGE) y = r.bottom + GAP + 4;
    else if (r.top - GAP - ch >= b.top + EDGE) y = r.top - GAP - 4 - ch;
    else if (r.right + GAP + cw <= b.right - EDGE) { x = r.right + GAP + 4; y = clampY(r.top); }
    else if (r.left - GAP - cw >= b.left + EDGE) { x = r.left - GAP - 4 - cw; y = clampY(r.top); }
    else y = clampY(r.bottom + GAP);
    card.style.left = x + 'px';
    card.style.top = y + 'px';
  }

  function layout() {
    if (!run) return;
    const step = stepOf();
    if (!run.anchor || !isVisible(run.anchor)) {
      run.anchor = resolveAnchor(step.anchor);
      run.placed = false;
    }
    if (run.anchor && !run.placed) {
      run.placed = true;
      run.anchor.scrollIntoView({ block: 'center', inline: 'nearest' });
    }
    place();
  }

  function draw() {
    const step = stepOf();
    const total = run.t.steps.length;
    ui.card.querySelector('.tutorial-title').textContent = run.t.title;
    ui.card.querySelector('.tutorial-count').textContent = 'Step ' + (run.i + 1) + ' of ' + total;
    ui.card.querySelector('.tutorial-say').textContent = step.say;
    const hint = ui.card.querySelector('.tutorial-hint');
    const wait = step.wait;
    hint.textContent = !wait ? '' : (wait.click ? 'Press the highlighted control.'
      : 'This step moves on by itself when the station gets there.');
    hint.hidden = !hint.textContent;
    ui.card.querySelector('.tutorial-back').disabled = run.i === 0;
    ui.card.querySelector('.tutorial-next').textContent = run.i === total - 1 ? 'Done' : 'Next';
  }

  function enter(i, viaBack) {
    run.i = i;
    run.anchor = null;
    run.placed = false;
    const step = stepOf();
    // After Back, a step whose condition already holds waits for it to
    // stop holding first, or it would advance at once.
    run.armed = !(viaBack && step.wait && step.wait.state && lastState
      && stateMatches(lastState, step.wait.state));
    writeProgress(run.t.id, i);
    // Setup withdraws once the station runs; a page step is not about it,
    // and an open drawer would sit over the page's controls.
    const drawer = document.getElementById('setup-drawer');
    const away = document.getElementById('drawer-close');
    const aboutSetup = step.anchor && step.anchor.selector
      && drawer && drawer.querySelector(step.anchor.selector);
    if (step.page !== 'Overview' && drawer && drawer.classList.contains('open')
      && away && !aboutSetup) away.click();
    showPage(step.page);
    draw();
    ui.card.hidden = false;
    layout();
    // A page switch draws on the next frame; look again then.
    window.setTimeout(layout, 120);
    window.setTimeout(checkState, 0);
  }

  function advance() {
    if (!run) return;
    if (run.i >= run.t.steps.length - 1) { finish(); return; }
    enter(run.i + 1, false);
  }

  function checkState() {
    if (!run || !lastState) return;
    const wait = stepOf().wait;
    if (!wait || !wait.state) return;
    const holds = stateMatches(lastState, wait.state);
    if (!run.armed) { if (!holds) run.armed = true; return; }
    if (holds) advance();
  }

  async function pollOnce() {
    if (!run || polling) return;
    polling = true;
    try { lastState = await readState(); } catch (err) { /* keep the last */ }
    polling = false;
    checkState();
  }

  function onClick(event) {
    if (!run || !run.anchor) return;
    const wait = stepOf().wait;
    if (!wait || !wait.click) return;
    if (run.anchor.contains(event.target)) window.setTimeout(advance, 0);
  }

  function onKey(event) {
    if (event.key !== 'Escape') return;
    // An open dialog owns its own Escape.
    if (document.querySelector('.overlay:not([hidden])')) return;
    if (run) { stop(); return; }
    if (!ui.panel.hidden) closePanel();
  }

  function begin(t, step) {
    run = { t, i: 0, anchor: null, placed: false, armed: true };
    document.addEventListener('click', onClick, true);
    pollTimer = window.setInterval(pollOnce, POLL_MS);
    layoutTimer = window.setInterval(layout, LAYOUT_MS);
    window.addEventListener('resize', layout);
    window.addEventListener('scroll', layout, true);
    closePanel();
    enter(step, false);
    pollOnce();
  }

  function teardown() {
    window.clearInterval(pollTimer);
    window.clearInterval(layoutTimer);
    pollTimer = null;
    layoutTimer = null;
    document.removeEventListener('click', onClick, true);
    window.removeEventListener('resize', layout);
    window.removeEventListener('scroll', layout, true);
    ui.card.hidden = true;
    ui.ring.hidden = true;
    run = null;
  }

  function stop() {
    if (run) teardown();
    renderList();
  }

  function finish() {
    const id = run.t.id;
    teardown();
    writeProgress(id, null);
    renderList();
  }

  async function start(t, from) {
    ui.note.hidden = true;
    if (t.requires === 'sim') {
      let state = null;
      try { state = await readState(); } catch (err) { state = null; }
      if (!state) {
        ui.note.textContent = 'The station did not answer. Try again in a moment.';
        ui.note.hidden = false;
        return;
      }
      if (hasHardware(state)) {
        ui.note.textContent = NOT_SIM;
        ui.note.hidden = false;
        return;
      }
      lastState = state;
    }
    if (run) teardown();
    const last = t.steps.length - 1;
    begin(t, Math.max(0, Math.min(from || 0, last)));
  }

  // -- the page ------------------------------------------------------------
  function renderList() {
    const list = ui.list;
    while (list.firstChild) list.removeChild(list.firstChild);
    if (!tutorials.length) {
      list.appendChild(make('p', 'tutorial-empty',
        failedFiles.length ? 'The tutorial files could not be read.' : 'There are no tutorials yet.'));
      return;
    }
    for (const t of tutorials) {
      const row = make('div', 'tutorial-row');
      const text = make('div', 'tutorial-row-text');
      text.appendChild(make('h3', 'tutorial-row-title', t.title));
      const saved = readProgress(t.id);
      const meta = t.steps.length + ' steps' + (t.requires === 'sim' ? ' - simulated hardware' : '');
      text.appendChild(make('p', 'tutorial-row-meta', meta));
      row.appendChild(text);
      const actions = make('div', 'tutorial-row-actions');
      if (needsSignIn(t)) {
        // Owner 2026-10-07: a Guest has no Transfer Map or Sample Map, so a
        // tutorial on one is listed, greyed, with the way to it.
        const start = button('Sign in to use', 'button role-neutral', () => {});
        start.disabled = true;
        start.title = 'This tutorial uses a page only signed-in users have. '
          + 'Sign in from the account menu (Switch user).';
        actions.appendChild(start);
        row.classList.add('is-locked');
        row.appendChild(actions);
        list.appendChild(row);
        continue;
      }
      if (saved !== null && saved > 0 && saved < t.steps.length) {
        actions.appendChild(button('Resume at step ' + (saved + 1), 'button role-neutral',
          () => start(t, saved)));
      }
      actions.appendChild(button('Start', 'button role-go', () => start(t, 0)));
      row.appendChild(actions);
      list.appendChild(row);
    }
  }

  /** Setup's `state.account`: who works, and which pages a Guest lacks. */
  let account = null;

  async function readAccount() {
    try {
      const setup = await getJson('/api/setup');
      account = (setup && setup.state && setup.state.account) || null;
    } catch (err) { /* keep the last */ }
  }

  function needsSignIn(t) {
    if (!account || account.enabled === false || account.signed_in) return false;
    const locked = Array.isArray(account.signed_in_only) ? account.signed_in_only : [];
    return t.steps.some((step) => locked.indexOf(step.page) !== -1);
  }

  async function openPanel() {
    ui.panel.hidden = false;
    ui.link.setAttribute('aria-expanded', 'true');
    renderList();
    await readAccount();
    if (!ui.panel.hidden) renderList();
  }

  function closePanel() {
    ui.panel.hidden = true;
    ui.link.setAttribute('aria-expanded', 'false');
  }

  function build() {
    ui.link = document.getElementById('tutorials-link');
    if (!ui.link) return false;
    const panel = make('aside', 'tutorial-panel');
    panel.id = 'tutorial-panel';
    panel.setAttribute('aria-label', 'Tutorials');
    panel.dataset.sideWindow = '';
    panel.hidden = true;
    const head = make('header', 'tutorial-panel-head');
    head.appendChild(make('h2', 'tutorial-panel-title', 'Tutorials'));
    head.appendChild(button('Close', 'ghost rail-control', closePanel));
    panel.appendChild(head);
    panel.appendChild(make('p', 'tutorial-intro',
      'A tutorial points at the controls and waits for you to press them. '
      + 'It never presses one for you. Escape stops it.'));
    ui.note = make('p', 'tutorial-note');
    ui.note.setAttribute('role', 'alert');
    ui.note.hidden = true;
    panel.appendChild(ui.note);
    ui.list = make('div', 'tutorial-list');
    panel.appendChild(ui.list);
    document.body.appendChild(panel);
    ui.panel = panel;

    const card = make('div', 'tutorial-card');
    card.id = 'tutorial-card';
    card.setAttribute('role', 'region');
    card.setAttribute('aria-label', 'Tutorial step');
    card.hidden = true;
    const top = make('div', 'tutorial-card-top');
    top.appendChild(make('span', 'tutorial-count'));
    top.appendChild(make('span', 'tutorial-title'));
    card.appendChild(top);
    const say = make('p', 'tutorial-say');
    say.setAttribute('aria-live', 'polite');
    card.appendChild(say);
    card.appendChild(make('p', 'tutorial-hint'));
    const row = make('div', 'tutorial-card-actions');
    row.appendChild(button('Stop', 'ghost tutorial-stop', stop));
    row.appendChild(button('Back', 'button role-neutral tutorial-back',
      () => { if (run && run.i > 0) enter(run.i - 1, true); }));
    row.appendChild(button('Next', 'button role-neutral tutorial-next', advance));
    card.appendChild(row);
    document.body.appendChild(card);
    ui.card = card;

    const ring = make('div', 'tutorial-ring');
    ring.setAttribute('aria-hidden', 'true');
    ring.hidden = true;
    document.body.appendChild(ring);
    ui.ring = ring;

    ui.link.setAttribute('aria-controls', 'tutorial-panel');
    ui.link.setAttribute('aria-expanded', 'false');
    ui.link.addEventListener('click', () => { if (ui.panel.hidden) openPanel(); else closePanel(); });
    document.addEventListener('keydown', onKey);
    // A side window (owner 2026-10-07): the page closes it when a rail page
    // is pressed, by this event (app.js SIDE_WINDOWS_CLOSE).
    window.addEventListener('station-side-windows-close', () => { if (!ui.panel.hidden) closePanel(); });
    return true;
  }

  async function init() {
    if (!build()) return;
    try {
      const got = await loadTutorials();
      tutorials = got.found;
      failedFiles = got.failed;
    } catch (err) {
      tutorials = [];
      failedFiles = ['index'];
    }
    renderList();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
