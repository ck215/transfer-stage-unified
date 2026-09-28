/**
 * The Web client. It is `src/views/base.py` written in JavaScript.
 *
 * `PanelCard` is `PanelView`: one render function per element type, every
 * writable entry's current value travelling with every command, the same
 * needs_confirm / refused contract, the same gating rule read from the
 * schema. `Dashboard` is `Dashboard`: model cards, the global FULL STOP
 * toggle, the event log, an acknowledged modal only for `needs_ack`.
 *
 * Two rules this file does not bend:
 *   - nothing from the server is ever assigned to innerHTML. Every node is
 *     built with createElement and every string lands in textContent, so a
 *     model name or a refusal reason cannot become markup (WEB-9).
 *   - every fetch is bounded by an AbortController. An unbounded fetch used
 *     to hang the poll cycle forever behind one stalled request (WEB-22).
 */
'use strict';

const STATE_POLL_MS = 250;
const DATA_POLL_MS = 1000;
const HEARTBEAT_MS = 2000;
const STALE_AFTER_S = 1.0;
const SETUP_NAME = '__setup__';
//: rb-restart R4: after Restart the page asks for /api/state this often, for
//: this long, and reloads itself once a NEW station (another `boot`) answers.
const RESTART_POLL_MS = 2000;
const RESTART_WAIT_MS = 60000;
const RESTARTING_TEXT = 'Restarting the station\u2026';
const RESTART_GAVE_UP = 'The station did not come back; start it by hand.';
//: The acknowledgement's second key when the notice carries an action (R1).
const ACK_LATER = 'Later';
//: How many of a model's key numbers are set as readings (large numerals) on
//: its sheet entry. The rest of its readonly values are captions and values.
const RAIL_READOUTS = 4;
//: A number is drawn in the trace colour only while it is CHANGING: once it
//: has held still this long it reads in ink (Bench sheet, 2026-09-25: trace
//: is for changing numbers and plot lines only).
const CHANGING_MS = 1000;
//: Status by exception: a readonly whose value is one of these is a normal
//: state and is not drawn in tier 1. Loaded from theme.QUIET_VALUES
//: (/api/theme.json) at start, so the three views read one list.
let QUIET_VALUES = new Set();
//: The default disclosure texts, likewise from the theme (TIER_LABELS). A
//: section that names its own `disclosure` wins.
let TIER_LABELS = { 2: 'Details', 3: 'Diagnostics' };
//: The keyboard path to the stop (F9): Ctrl+., the one chord on every
//: platform and in every view (G5, owner ruling 2026-09-25: no shortcut
//: exists on one OS only). It works with focus in a text box. It only ever
//: STOPS; clearing the latch stays a deliberate, confirmed act.
const STOP_KEY = '.';
const STOP_KEY_HINT = 'Ctrl+.';
//: What the page says about the stop before the first state arrives:
//: `views.base.stop_words` for a station with nothing latched.
const NO_STOP_WORDS = { face: 'Stop', action: 'stop', headline: '', subline: '', rail: '' };
//: The event title the Controller publishes when a model did not confirm a
//: stop (controller.py). The views key on the title; its words are core's.
let UNCONFIRMED_TITLE = 'Stop Not Confirmed';
//: O13: the idle warning's event is history in the log; the live number is
//: the rail's countdown line (N2), never a frozen tray line.
let IDLE_SOON_TITLE = 'Idle Timeout Soon';
//: O12: the watchdog's warning, taken back by the next heartbeat that lands.
let SILENT_TITLE = 'Browser Silent';
//: The watchdog's seconds as the server serves them (/api/theme.json,
//: `watchdog`): the close-tab line says the station's own number (N3).
let WATCHDOG = null;
//: How long before the idle timeout a probe's countdown line appears, when
//: its state does not say (`idle_warn_seconds`).
const IDLE_WARN_DEFAULT_S = 60;
//: How many characters of a dropdown option are shown before it is elided
//: from the middle: the tail of a port or gamepad name is what tells two
//: devices apart, so the middle goes, never the end (F15).
const OPTION_CHARS = 22;

// ==========================================================================
// fetch, bounded. Overridable so a test can shrink it.
// ==========================================================================
function fetchTimeoutMs() {
  return window.__FETCH_TIMEOUT_MS__ || 8000;
}

async function api(path, options) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), fetchTimeoutMs());
  try {
    return await fetch(path, Object.assign({}, options, { signal: controller.signal }));
  } finally {
    clearTimeout(timer);
  }
}

async function apiGet(path) {
  const response = await api(path, { method: 'GET' });
  return await response.json();
}

async function apiPost(path, body) {
  const response = await api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return await response.json();
}

/** apiPost for the stop path: an answer that is not a 200 is a failure to
 *  report, not a body to read (F2). */
async function apiPostChecked(path, body) {
  const response = await api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!response.ok) {
    const failure = new Error('HTTP ' + response.status);
    failure.status = response.status;
    throw failure;
  }
  return await response.json();
}

// ==========================================================================
// the heartbeat's own thread (F, 2026-09-28: "the app going out of focus
// stops controller polling")
// ==========================================================================
//: A dedicated Worker, built from this string (no file of its own), that
//: checks in every HEARTBEAT_MS by itself. A hidden tab's page timers are
//: throttled (Chrome: 1/s, then 1/min after five minutes); a worker's are
//: not, so switching to the microscope window no longer reads to the
//: watchdog as a gone browser. It says whether the page is hidden, which the
//: server logs. It ends on `pagehide` and at shutdown only: a closed tab or a
//: crashed browser is silence, and the watchdog's rules are unchanged. Its
//: fetch is bounded like every other (WEB-22).
const HEARTBEAT_WORKER_SOURCE = [
  "'use strict';",
  "let url = '', every = 0, bound = 8000, hidden = false, timer = null;",
  "async function beat() {",
  "  const controller = new AbortController();",
  "  const clock = setTimeout(() => controller.abort(), bound);",
  "  try {",
  "    const response = await fetch(url, { method: 'POST', signal: controller.signal,",
  "      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ hidden }) });",
  "    const answer = await response.json();",
  "    postMessage({ ok: Boolean(answer && answer.status === 'ok') });",
  "  } catch (err) {",
  "    postMessage({ ok: false });",
  "  } finally {",
  "    clearTimeout(clock);",
  "  }",
  "}",
  "onmessage = (message) => {",
  "  const said = message.data || {};",
  "  if ('hidden' in said) hidden = Boolean(said.hidden);",
  "  if (said.start && !timer) {",
  "    url = said.url; every = said.every; bound = said.bound || bound;",
  "    beat();",
  "    timer = setInterval(beat, every);",
  "  }",
  "};",
].join('\n');

/** The worker, or null where the page cannot have one (the caller then
 *  beats from its own timer). */
function heartbeatWorker() {
  if (typeof Worker === 'undefined' || typeof Blob === 'undefined'
      || typeof URL === 'undefined' || !URL.createObjectURL) return null;
  const source = URL.createObjectURL(new Blob([HEARTBEAT_WORKER_SOURCE],
                                              { type: 'text/javascript' }));
  try {
    return new Worker(source);
  } catch (err) {
    return null;
  } finally {
    URL.revokeObjectURL(source);
  }
}

// ==========================================================================
// schema.is_enabled, mirrored. One rule, three views.
// ==========================================================================
function isEnabled(element, mode, values) {
  // G3: an element with `enabled_by` is live only while that value (a Launch
  // checkbox's) is true. A caller without values skips this rule, as the
  // Python one does; the mode gates below still apply either way.
  const by = element.enabled_by;
  if (by && values !== null && values !== undefined && !values[by]) return false;
  const disabled = element.disabled_when;
  if (disabled && disabled.indexOf(mode) !== -1) return false;
  const enabled = element.enabled_when;
  if (enabled && enabled.indexOf(mode) === -1) return false;
  return true;
}

// ==========================================================================
// small DOM helpers - no innerHTML anywhere in this file
// ==========================================================================
function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function row(element, extraClass) {
  const node = make('div', 'row' + (extraClass ? ' ' + extraClass : ''));
  node.appendChild(make('label', 'label', captionText(element)));
  return node;
}

/** A control's caption, in sentence case. When the element declares its
 *  `unit`, a "(unit)" at the end of the caption goes: the unit is drawn
 *  beside the value, and "Step (deg) ... deg" said it twice (L22, IMP7-15). */
function captionText(element) {
  const text = sentenceCase((element && (element.text || element.model_attr || element.command)) || '');
  return element && element.unit ? text.replace(/\s*\([^)]*\)\s*$/, '') : text;
}

/** Whose control this is, for its accessible name: the model's name, or in
 *  Setup the row's (L16, IMP7-10) - so three "Step" buttons are "Step,
 *  Stepper Probe", "Step, DC Probe", ... */
function ownerOf(panel) {
  if (!panel) return '';
  if (panel.name === SETUP_NAME) return panel.rowOwner || '';
  return sentence(panel.title || panel.name || '');
}

/** An accessible name that starts with the words on the control (WCAG
 *  2.5.3, label in name) and ends with whose it is. */
function nameFor(words, owner) {
  const said = String(words || '').trim();
  if (!owner || said.toLowerCase().indexOf(String(owner).toLowerCase()) !== -1) return said;
  return said ? said + ', ' + owner : String(owner);
}

/** Tie a row's caption to its control, so the caption is clickable and a
 *  screen reader names the control by it - and by its model (L16). Ids are
 *  per panel and per attribute, which is unique inside one page. */
let controlSerial = 0;
function labelControl(node, control, element, panel) {
  controlSerial += 1;
  control.id = 'control-' + controlSerial;
  const caption = node.querySelector('.label');
  if (caption) caption.htmlFor = control.id;
  control.setAttribute('aria-label', nameFor(captionText(element), ownerOf(panel)));
}

/** A control's title is its own words (a hint, the whole of a long value)
 *  unless it is disabled, when it is WHY (L3). Both are kept, so the reason
 *  goes and the control's own title comes back when the gate opens. */
function titler(control) {
  let own = control.title || '';
  let reason = '';
  const apply = () => {
    const next = reason || own;
    if (control.title !== next) control.title = next;
  };
  return {
    own: (text) => { own = String(text || ''); apply(); },
    reason: (text) => { reason = String(text || ''); apply(); },
  };
}

//: Why a control is greyed, in both directions: `views.base.GATE_WORDS`,
//: served as `gate_words` in /api/theme.json (O3, IMP8-1). Each word maps
//: to [the words while the mode is in the element's `disabled_when`, the
//: words while it is missing from its `enabled_when`], so "In manual mode"
//: and "Not in manual mode" can never be swapped again. One table, three
//: views: none is kept here (the one this file had said the inverse).
let GATE_WORDS = {};

/** One side of a gate word's pair, or '' when the table has none. */
function gateWords(mode, direction) {
  const pair = GATE_WORDS[String(mode || '')];
  return (pair && pair[direction]) || '';
}

/** Why `isEnabled` says no: `views.base.gate_reason`, mirrored - read from
 *  the element's own gate lists in the same order as the Python - plus the
 *  one rule it does not cover, a Setup row's Launch tick (`enabled_by`).
 *  '' when the element is live. */
function gateReason(element, mode, values) {
  const by = element.enabled_by;
  if (by && values !== null && values !== undefined && !values[by]) {
    return /_enabled$/.test(by) ? 'Tick Launch on this row first' : 'Not available yet';
  }
  const word = String(mode || '');
  const disabled = element.disabled_when || [];
  if (disabled.indexOf(word) !== -1) return gateWords(word, 0) || 'In ' + word + ' mode';
  const enabled = element.enabled_when || [];
  if (enabled.length && enabled.indexOf(word) === -1) {
    const wanted = String(enabled[0]);
    return gateWords(wanted, 1) || 'Not in ' + wanted + ' mode';
  }
  return '';
}

//: The commands that take hardware DOWN (`Panel.UNGATED_COMMANDS` in
//: src/panel.py, plus `set_mode` to "disabled"): never held back by the
//: busy guard (O10), because a stop must never wait for an earlier press.
const STOP_COMMANDS = ['toggle_estop', 'estop', 'clear_estop', 'halt', 'stop_run', 'extend_idle'];

function isStopCommand(element, args) {
  const command = element && element.command;
  if (STOP_COMMANDS.indexOf(command) !== -1) return true;
  return command === 'set_mode' && Array.isArray(args) && args[0] === 'disabled';
}

/** O4: what a faulted entry says at its head. A probe in FAULT is a
 *  disable that did not reach the board; any other fault is still a device
 *  in a state this page cannot vouch for. */
function faultWords(state) {
  return state && state.mode === 'fault' ? 'Disable failed. Treat as live.'
    : 'Faulted. Treat as live.';
}

/** Names as a sentence says them: "A", "A and B", "A, B and C". */
function joinNames(names) {
  const list = (names || []).map((n) => sentence(n));
  if (list.length < 2) return list.join('');
  return list.slice(0, -1).join(', ') + ' and ' + list[list.length - 1];
}

/** L6 (round 7, TK7-5): how far one key moves a slider - 1 % of the travel
 *  per arrow, 10 % per Page key, rounded and never less than 1; Home and
 *  End do nothing (End set the maximum speed in one key). null: not a key
 *  the slider answers. */
function sliderKeyDelta(low, high, key) {
  const travel = Math.abs(Number(high) - Number(low));
  const step = Math.max(1, Math.round(travel / 100));
  const page = Math.max(1, Math.round(travel / 10));
  switch (key) {
    case 'ArrowRight': case 'ArrowUp': return step;
    case 'ArrowLeft': case 'ArrowDown': return -step;
    case 'PageUp': return page;
    case 'PageDown': return -page;
    case 'Home': case 'End': return 0;
    default: return null;
  }
}

/** What an empty data element says. The view owns this copy: an empty
 *  state says what to do next, not that there is nothing. A model that
 *  declares its own (`empty` on the element) wins. */
const EMPTY_STATES = {
  series: 'No samples yet. They plot here as the model reports them.',
  figure: 'No run loaded. Load run opens a saved CSV and plots it here.',
  gamepad_log: 'No gamepad input yet. Choose a gamepad under Configuration, '
    + 'then enter manual mode to drive with it.',
};

function emptyText(element, command) {
  return element.empty || EMPTY_STATES[command]
    || 'Nothing here yet. It fills in as the model reports.';
}

/** Sentence case, as the design brief asks for everywhere. Only a SHOUTED
 *  word is brought back down - a word of four letters or more that is all
 *  capitals - so "FULL STOP" becomes "Full stop" while "X Position:" and
 *  "Sync X: OFF" keep the capitals that carry meaning. The schema is the
 *  model's vocabulary and is not edited from here; this is presentation. */
function sentence(text) {
  const words = String(text === null || text === undefined ? '' : text)
    .split(' ')
    .map((word) => (/^[A-Z]{4,}[:.,]?$/.test(word) ? word.toLowerCase() : word));
  const joined = words.join(' ');
  return joined ? joined.charAt(0).toUpperCase() + joined.slice(1) : joined;
}

/** Sentence case for a heading or a label: `sentence` first, then the
 *  interior Capitalised words come down too, so "Coordinate Frame" reads
 *  "Coordinate frame" and "Probe Tilt Angle:" reads "Probe tilt angle". A
 *  word that is not simply Capitalised - an initialism like ID or DC, an
 *  axis letter, a unit - is left exactly as the model wrote it, and so is
 *  the first word. The trailing colon goes with it: the value is beside the
 *  label on a panel, not after it in a form. */
function sentenceCase(text) {
  const words = sentence(text).replace(/\s*:\s*$/, '').split(' ');
  return words
    .map((word, index) => (index > 0 && /^\(?[A-Z][a-z]+\)?$/.test(word)
                           ? word.toLowerCase() : word))
    .join(' ');
}

/** A SHOUTED word brought down ("FULL STOP" -> "full stop"), nothing else
 *  touched: an event's message is a sentence already, often one that starts
 *  with a model's name. */
function unshout(text) {
  return String(text === null || text === undefined ? '' : text).split(' ')
    .map((word) => (/^[A-Z]{4,}[:.,;]?$/.test(word) ? word.toLowerCase() : word))
    .join(' ');
}

/** An event as the operator reads it (L11, round 7, IMP7-4): its title in
 *  sentence case, then its message - never the raw log line with its
 *  "[source]" prefix and Title Case. A repeat keeps its count. A line the
 *  page wrote itself (`notice`, no title) is said as it was written. */
function eventText(event) {
  if (!event) return '';
  if (!event.title) return String(event.text || event.message || '');
  const title = sentenceCase(event.title);
  const message = unshout(event.message || '').trim();
  const count = Number(event.count) > 1 ? ' (x' + event.count + ')' : '';
  return (message ? title + ': ' + message : title) + count;
}

/** The acknowledgement queue (rb-ack A3): one entry per title, oldest
 *  first. A repeat of a queued title joins its entry - counted, never a
 *  second dialog - so the open one is redrawn, not reopened. Returns the
 *  entry's index. Pure, so a test without a browser runs the real code. */
function ackEnqueue(queue, event) {
  const title = String((event && event.title) || '');
  const index = queue.findIndex((entry) => entry.title === title);
  if (index >= 0) {
    queue[index].events.push(event);
    return index;
  }
  queue.push({ title, events: [event] });
  return queue.length - 1;
}

/** What the dialog says for the head of the queue: the title in sentence
 *  case as the heading, the newest message as the body with the repeat
 *  count, how many other titles wait, and the one key's words. */
function ackWords(queue) {
  const entry = queue[0];
  if (!entry) return null;
  const latest = entry.events[entry.events.length - 1] || {};
  const repeats = entry.events.reduce(
    (sum, event) => sum + Math.max(1, Number(event.count) || 1), 0);
  const message = unshout(latest.message || latest.text || '').trim();
  const waiting = queue.length - 1;
  const words = {
    title: entry.title ? sentenceCase(entry.title) : 'Notice',
    body: message + (repeats > 1 ? ' (x' + repeats + ')' : ''),
    waiting: waiting > 0 ? waiting + ' more waiting' : '',
    key: 'Understood',
  };
  // rb-restart R1: an action is a second key. The newest event's action
  // wins; the action's label is the default key, Later the other.
  const action = ackAction(entry);
  if (action) {
    words.key = String(action.label || 'Continue');
    words.later = ACK_LATER;
    words.action = action;
  }
  return words;
}

/** The action of a queue entry: the newest of its events that carries one
 *  ({label, name, command, args}), or null. */
function ackAction(entry) {
  const found = (entry && entry.events || []).slice().reverse()
    .find((event) => event && event.action && event.action.command);
  return found ? found.action : null;
}

/** A Restart that the station accepted: the page waits for the new one. */
function isRestartAnswer(name, command, result) {
  return name === SETUP_NAME && command === 'restart_station'
    && Boolean(result) && result.status === 'ok';
}

/** The event that says a model did not confirm a stop (L2). */
function isUnconfirmedEvent(event) {
  return Boolean(event && event.title === UNCONFIRMED_TITLE);
}

/** The face of a toggle's state, rendered from the schema's true/false
 *  text. The schema writes "Sync X: OFF" beside a row captioned "Sync X",
 *  and "AUTONOMOUS MODE (Click to Stop)"; on a panel the caption is already
 *  beside the control, so the repeated caption goes, a bare ON/OFF reads as
 *  a word, and an aside in brackets becomes the control's tooltip. Returns
 *  { text, hint }. Presentation only - the schema is not edited from here. */
function toggleFace(text, caption) {
  let face = String(text === null || text === undefined ? '' : text).trim();
  let hint = '';
  const aside = face.match(/^(.*?)\s*\(([^)]*)\)\s*$/);
  if (aside && aside[1]) { face = aside[1]; hint = sentenceCase(aside[2]); }
  const lead = String(caption || '').replace(/\s*:\s*$/, '').trim();
  if (lead && face.toLowerCase().indexOf(lead.toLowerCase() + ':') === 0) {
    face = face.slice(lead.length + 1).trim();
  }
  if (/^(ON|OFF)$/.test(face)) face = face.charAt(0) + face.slice(1).toLowerCase();
  return { text: sentenceCase(face), hint };
}

/** One of the station's glyphs (theme.ICONS, rule 6): a span the
 *  stylesheet masks with the served --icon-<name>, painted in the colour
 *  of the text beside it. Decorative: the words say it. */
function glyph(name) {
  const node = make('span', 'glyph glyph-' + name);
  node.setAttribute('aria-hidden', 'true');
  return node;
}

/** A disclosure's key (Signature): a 24 px member of the key family
 *  holding the disclosure glyph, which turns a quarter when open and the
 *  key sinks (styles.css). Built node by node, no markup. */
function chevron() {
  const key = make('span', 'disc-key');
  key.setAttribute('aria-hidden', 'true');
  key.appendChild(glyph('disclosure'));
  return key;
}

const SVG_NS = 'http://www.w3.org/2000/svg';

/** The tripped-flag window (theme.FLAG): an ink frame showing SIGNAL with
 *  an ink hatch, which drops into the frame (styles.css). Built node by
 *  node; the colours are the stylesheet's, by class. */
let flagSerial = 0;
function flagWindow() {
  flagSerial += 1;
  const clipId = 'flag-clip-' + flagSerial;
  const part = (tag, attrs) => {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  };
  const svg = part('svg', { class: 'flag-window', viewBox: '0 0 30 20', 'aria-hidden': 'true',
                            focusable: 'false' });
  const defs = part('defs', {});
  const clip = part('clipPath', { id: clipId });
  clip.appendChild(part('rect', { x: '2', y: '2', width: '26', height: '16', rx: '2' }));
  defs.appendChild(clip);
  svg.appendChild(defs);
  svg.appendChild(part('rect', { class: 'flag-frame', x: '1', y: '1', width: '28', height: '18', rx: '3' }));
  const window_ = part('g', { 'clip-path': 'url(#' + clipId + ')' });
  const flag = part('g', { class: 'flag' });
  flag.appendChild(part('rect', { class: 'flag-fill', x: '2', y: '2', width: '26', height: '16' }));
  flag.appendChild(part('path', { class: 'flag-hatch', d: 'M-2 22L14 -2M8 22L24 -2M18 22L34 -2' }));
  window_.appendChild(flag);
  svg.appendChild(window_);
  return svg;
}

/** The glyph a command carries before its legend (Signature, "Legends
 *  with a glyph"): Home, Start run, Save, a gamepad log. By what the
 *  command does, not by its words, so a reworded legend keeps its glyph;
 *  at most one per key. */
const COMMAND_GLYPHS = { home: 'home', start_run: 'run' };
function commandGlyph(element) {
  if (!element) return '';
  if (element.type === 'file_save') return 'download';
  if (element.type === 'log_stream' && /gamepad/i.test(String(element.text || ''))) return 'gamepad';
  return COMMAND_GLYPHS[element.command] || '';
}

/** The Overview's grid (K4): how many entries share the row that entry
 *  `index` of `count` sits on. Rows of three; a lone entry is never left on
 *  the last row - the last four go two and two instead - and three or fewer
 *  share one row. The sheet has six columns, so an entry spans 6 / this. */
function sheetAcross(count, index) {
  if (count <= 3) return Math.max(count, 1);
  const twos = count % 3 === 1 ? 4 : (count % 3 === 2 ? 2 : 0);
  return index >= count - twos ? 2 : 3;
}

/** Status by exception: is this value a normal state, not worth drawing in
 *  tier 1? `raw` is what the state served, `shown` how it reads. */
function isQuiet(raw, shown) {
  if (raw === null || raw === undefined) return true;
  return QUIET_VALUES.has(String(raw)) || QUIET_VALUES.has(String(shown));
}

/** An axis readout: "X:", "Y position:". Its letter is set inline beside the
 *  number, and the section's title is said once above the line. */
function axisLetter(element) {
  const text = String((element && element.text) || '').trim();
  const bare = text.match(/^([XYZ])\s*:?$/);
  if (bare) return bare[1];
  const named = text.match(/^([XYZ]) position\s*:?$/i);
  return named ? named[1].toUpperCase() : '';
}

// ==========================================================================
// int entries: an "int" Param shows no decimals and takes none (Addendum 2).
//
// Both of these are pure string functions on purpose - they are the only
// part of the client a test can run without a browser.
// ==========================================================================

/** The text an int box shows for a served value. The state carries a
 *  formatted string ("5.000" for a float-formatted attribute, "5" for an
 *  int), and refresh must never put a decimal into an int box. A value that
 *  is not a number at all is handed back untouched rather than blanked. */
function intText(value) {
  if (value === null || value === undefined) return '';
  const text = String(value).trim();
  if (text === '') return '';
  const number = Number(text);
  if (!isFinite(number)) return text;
  return String(Math.trunc(number));
}

/** Typed input an int box refuses: a decimal separator or an exponent.
 *  `input.step = '1'` only makes the browser's own stepper move by one; it
 *  does not stop the operator typing "2.5" and the box handing that to a
 *  command. */
function rejectsIntInput(data) {
  return typeof data === 'string' && /[.,eE]/.test(data);
}

// ==========================================================================
// row sections: `sch.section(..., layout="row")` is laid out horizontally by
// every renderer (Addendum 2). The desktop views use a grid; so does this
// one, and the columns line up across rows because every row section in a
// card is a `display: contents` child of one grid - the Setup panel is one
// table, one line per model, not one vertical tab per model.
// ==========================================================================
function isRowSection(section) {
  return section && section.layout === 'row';
}

/** A section's tier of prominence (schema.section's `tier`, E 2026-09-25):
 *  1 always drawn, 2 behind the model's disclosure, 3 behind Diagnostics
 *  inside it. A schema from before tiers is all tier 1. */
function sectionTier(section) {
  const tier = Number(section && section.tier);
  return tier === 2 || tier === 3 ? tier : 1;
}

//: Element types that DO something rather than say something.
const COMMAND_TYPES = ['button', 'file_save', 'file_open'];

/** A row of commands is not a table row: Setup's Devices and Launch rows
 *  hold buttons and a sentence (the scan status; the selection count, or
 *  while scanning why Launch waits), and in a shared grid those would set the
 *  widths of every model row's columns. A row that holds a command spans the
 *  table instead of lining up with it. A checkbox is not a command: the
 *  Launch box is a column of each model row (G3). */
function isCommandRow(section) {
  return isRowSection(section)
    && (section.elements || []).some((e) => COMMAND_TYPES.indexOf(e.type) !== -1);
}

/** How many element columns the widest data row needs. */
function rowColumnCount(sections) {
  let widest = 0;
  for (const section of (sections || [])) {
    if (!isRowSection(section) || isCommandRow(section)) continue;
    const drawn = (section.elements || []).filter((e) => e.type !== 'internal');
    if (drawn.length > widest) widest = drawn.length;
  }
  return widest;
}

/** The grid: a label column, then one column per element, the last of which
 *  takes the slack so a row's status sits hard right. */
function rowGridColumns(columns) {
  const middle = columns > 1 ? 'repeat(' + (columns - 1) + ', max-content) ' : '';
  return 'max-content ' + middle + 'minmax(0, 1fr)';
}

/** The key numbers of a model - set as READINGS (large numerals) on its
 *  sheet entry - derived rather than named: the readonly elements flagged
 *  `rail: true`, or else those of its FIRST schema section, which is where
 *  every model in this station puts what the operator watches. (The name is
 *  the schema's: `rail` once meant the top rail; the readings left it for
 *  the sheet, so a value is never said twice.) */
function railElements(schema) {
  // A model that SAYS what it watches (`rail: true` on a readonly) wins;
  // the first-section rule below is the fallback for one that does not.
  const flagged = [];
  for (const section of ((schema && schema.sections) || [])) {
    for (const element of (section.elements || [])) {
      if (element.type === 'readonly' && element.model_attr && element.rail) flagged.push(element);
    }
  }
  if (flagged.length) return flagged.slice(0, RAIL_READOUTS);
  for (const section of ((schema && schema.sections) || [])) {
    const readouts = (section.elements || [])
      .filter((element) => element.type === 'readonly' && element.model_attr);
    if (readouts.length) return readouts.slice(0, RAIL_READOUTS);
  }
  return [];
}

/** `PanelView._refresh`: `age is not None and age > 1.0`. A model with no
 *  loop to be stale about reports a null age, and null is not stale - every
 *  card wore a "stale" badge in SIM when this was a bare comparison. */
function isStale(state) {
  const age = state ? state.age : null;
  return age !== null && age !== undefined && age > STALE_AFTER_S;
}

/** What a readout shows for a served value. A boolean reads as a word an
 *  operator would say ("Yes" / "No"), not as a programming literal. */
function readoutText(text) {
  if (text === '' || text === null || text === undefined) return '--';
  const word = String(text);
  if (/^(true|false)$/i.test(word)) return /^true$/i.test(word) ? 'Yes' : 'No';
  return word;
}

function roleClass(role) {
  return 'role-' + (role || 'neutral');
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/** Write text only when it changed. A poll that rewrites an unchanged node
 *  costs a layout, re-announces a live region and drops hover and focus
 *  (F21, WDG-3/6). */
function putText(node, text) {
  const next = String(text === null || text === undefined ? '' : text);
  if (node.textContent !== next) node.textContent = next;
}

function putAttr(node, name, value) {
  const next = String(value);
  if (node.getAttribute(name) !== next) node.setAttribute(name, next);
}

/** Shorten from the middle, keeping the head and the tail:
 *  "/dev/cu.usbmodem1234567890123" -> "/dev/cu.us…34567890123". */
function elideMiddle(text, max) {
  const whole = String(text === null || text === undefined ? '' : text);
  const limit = Math.max(5, max || OPTION_CHARS);
  if (whole.length <= limit) return whole;
  const tail = Math.ceil((limit - 1) / 2);
  const head = limit - 1 - tail;
  return whole.slice(0, head) + '…' + whole.slice(whole.length - tail);
}

/** Words that report an absence or an at-rest state. They are read, not
 *  watched, so they are muted - trace is for numbers and lit lamps only
 *  (F24, CRIT-6, HC-16). */
const QUIET_WORDS = ['--', 'off', 'none', 'no', 'not set', 'not scanned',
  'not detected', 'not found', 'nothing selected', 'simulated', 'idle',
  'closed', 'unbound'];

/** How a readout is set: a number in trace, a quiet word muted, any other
 *  word (an identifier, a state name) in ink. */
function readoutKind(text) {
  const word = String(text === null || text === undefined ? '' : text).trim();
  if (word === '' || QUIET_WORDS.indexOf(word.toLowerCase()) !== -1) return 'quiet';
  if (/^[-+−]?(\d|\.\d)/.test(word)) return 'number';
  return 'word';
}

/** A device class as an operator would say it. */
const DEVICE_WORDS = {
  SerialPort: 'serial port',
  Gamepad: 'gamepad',
  SMC100: 'SMC100 controller',
  Screen: 'screen capture',
};

function deviceWord(kind) {
  if (DEVICE_WORDS[kind]) return DEVICE_WORDS[kind];
  return String(kind || 'device').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase();
}

/** The devices a model reports as lost, as words: `state.devices` is
 *  `{SerialPort: "lost", ...}` (model/base.py). */
function lostDevices(state) {
  const devices = (state && state.devices) || {};
  return Object.keys(devices).filter((kind) => devices[kind] === 'lost').map(deviceWord);
}

/** MOD-5 / CON-6: the device class names that were hardware links before a
 *  model published `hardware_devices`; read ONLY for a state without the
 *  key, never when the key is there as an empty list
 *  (`views.base.LEGACY_LINK_DEVICES`). */
const LEGACY_LINK_DEVICES = ['SerialPort', 'SMC100'];

/** The hardware links in one model's state, as device class names:
 *  `state.hardware_devices` names them (`Device.is_hardware`), so a new
 *  link counts without this file learning its name
 *  (`views.base.hardware_links`). */
function hardwareLinks(state) {
  const devices = (state && state.devices) || {};
  const declared = Boolean(state) && Object.prototype.hasOwnProperty.call(state, 'hardware_devices');
  const names = declared ? (state.hardware_devices || []) : LEGACY_LINK_DEVICES;
  return Object.keys(devices).filter((kind) => names.indexOf(kind) !== -1);
}

/** The rail's line, said only when there is no hardware: "Simulation, no
 *  hardware attached"; a mix names the simulated models; real hardware,
 *  nothing. */
function simLineText(models) {
  const simulated = [];
  let hardware = 0;
  for (const name of Object.keys(models || {})) {
    const devices = (models[name] && models[name].devices) || {};
    if (Object.keys(devices).some((k) => devices[k] === 'simulated')) simulated.push(sentence(name));
    else if (hardwareLinks(models[name]).length) hardware += 1;
  }
  if (simulated.length && !hardware) return 'Simulation, no hardware attached';
  if (simulated.length) return 'Simulated: ' + simulated.join(', ');
  return '';
}

/** What travels with `element`'s command (MOD-6 / CON-8,
 *  `views.base.PanelView._gather_inputs`): the entries it declares in
 *  `inputs`, edited or not, validated as a set (D-5); plus every writable
 *  entry the operator edited and has not committed, so nothing just typed is
 *  lost. A clean entry the command does not declare stays home, so a bad or
 *  stale box elsewhere cannot refuse an unrelated command. */
function gatherInputsFor(element, widgets) {
  const declared = (element && element.inputs) || [];
  const inputs = {};
  for (const widget of widgets || []) {
    const entry = widget.element;
    if (!entry || entry.type !== 'entry' || !entry.writable || !widget.readValue) continue;
    const edited = widget.isEdited ? widget.isEdited()
      : Boolean(widget.isDirty && widget.isDirty());
    if (declared.indexOf(entry.model_attr) !== -1 || edited) {
      inputs[entry.model_attr] = widget.readValue();
    }
  }
  return inputs;
}

/** What went wrong with a request, as the end of a sentence. */
function failureReason(err) {
  if (err && err.name === 'AbortError') {
    return 'no answer within ' + Math.round(fetchTimeoutMs() / 1000) + ' s';
  }
  if (err && err.status) return 'the station answered ' + err.status;
  return 'the connection failed';
}

/** A clock time for "since …" sentences. */
function clockTime(date) {
  const pad = (n) => (n < 10 ? '0' : '') + n;
  return pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds());
}

// ==========================================================================
// The renderers: one per entry in schema.ELEMENT_TYPES.
//
// Each returns a widget: { node, setText?, setOn?, setData?, setEnabled,
// readValue?, isDirty?, isEdited? }. This is the seam a toolkit subclass fills in
// base.py - here the toolkit is the DOM.
// ==========================================================================
/** A readonly value: a caption over a value. Numbers are set in the numeral
 *  face with tabular figures; a word in the text face; nothing-to-report
 *  muted. A number is in the trace colour only while it is changing, and
 *  settles to ink once it has held still for CHANGING_MS (the per-readout
 *  last-change time is `changedAt`). In tier 1 a normal state
 *  (theme.QUIET_VALUES) is not drawn at all: status by exception. */
function renderReadonly(panel, element) {
  const node = row(element, 'stat');
  if (element.model_attr) node.dataset.attr = element.model_attr;
  const value = make('span', 'value is-empty ' + roleClass(element.role), '--');
  value.setAttribute('translate', 'no');
  node.appendChild(value);
  if (element.unit) node.appendChild(make('span', 'unit', element.unit));
  let changedAt = 0;
  let isChanging = false;
  const widget = {
    node,
    value,
    /** Settle a number that has stopped changing. Written only on the edge,
     *  so an idle page mutates nothing (F21). */
    tick: (now) => {
      const next = changedAt > 0 && now - changedAt < CHANGING_MS;
      if (next === isChanging) return;
      isChanging = next;
      value.classList.toggle('is-changing', isChanging);
    },
    setText: (text) => {
      let shown = readoutText(text);
      // L17: Setup's statuses are words the model wrote in lower case
      // ("simulated", "on"); they read in sentence case like every line.
      if (panel.name === SETUP_NAME && readoutKind(shown) !== 'number') shown = sentence(shown);
      // Status by exception, tier 1 only: tiers 2 and 3 are where a normal
      // state is still read on purpose. A model's key reading (`rail: true`)
      // is never hidden: unknown is information, drawn "--" muted at the
      // reading's own size (L10, IMP7-6).
      const hide = widget.tier === 1 && !element.rail && isQuiet(text, shown);
      if (node.hidden !== hide) node.hidden = hide;
      value.classList.toggle('is-dash', shown === '--');
      if (value.textContent === shown) return;
      const was = value.textContent;
      value.textContent = shown;
      // Nothing to report is not a reading: it is muted, whatever the
      // element's role, so an empty fault line is never a red "--". A word
      // is not a number either: trace is for numbers (F24).
      const kind = readoutKind(shown);
      value.classList.toggle('is-empty', kind === 'quiet');
      value.classList.toggle('is-word', kind === 'word');
      // A long identifier wraps inside its row; its whole text is also one
      // hover away (F15, HC-8).
      value.title = kind === 'word' ? shown : '';
      // The first value is not a change; a number that moves is.
      if (kind === 'number' && was !== '--' && was !== '') {
        changedAt = Date.now();
        widget.tick(changedAt);
      } else if (kind !== 'number') {
        changedAt = 0;
        widget.tick(Date.now());
      }
    },
    setEnabled: (flag) => { node.classList.toggle('disabled', !flag); },
  };
  return widget;
}

function renderEntry(panel, element) {
  const node = row(element);
  const group = make('div', 'group');
  const input = make('input', 'input');
  labelControl(node, input, element, panel);
  const title = titler(input);
  input.name = element.model_attr || '';
  input.autocomplete = 'off';
  input.spellcheck = false;
  // The input type comes from the schema (`value_type`), never from sniffing
  // the current value - which is what reclassified a cleared box as text.
  const isInt = element.value_type === 'int';
  const numeric = isInt || element.value_type === 'float';
  input.type = numeric ? 'number' : 'text';
  if (isInt) {
    input.step = '1';
    input.inputMode = 'numeric';
    // A decimal point is refused as it is typed, pasted or dropped, in both
    // of the ways a browser offers to refuse it.
    input.addEventListener('beforeinput', (event) => {
      if (rejectsIntInput(event.data)) event.preventDefault();
    });
    input.addEventListener('keydown', (event) => {
      if (rejectsIntInput(event.key)) event.preventDefault();
    });
  } else if (numeric) {
    input.step = String(Math.pow(10, -(element.decimals === undefined ? 3 : element.decimals)));
    input.inputMode = 'decimal';
  }
  if (element.min !== undefined && element.min !== null) input.min = String(element.min);
  if (element.max !== undefined && element.max !== null) input.max = String(element.max);
  // `slider: [low, high]` (E, 2026-09-25): a range BESIDE the entry, never
  // instead of it. The range writes the entry and the entry writes the
  // range; a command still reads the ENTRY (gatherInputsFor), so what travels
  // is exactly what the box says and the wire is unchanged.
  const slider = numeric && Array.isArray(element.slider)
    ? renderSlider(input, element, isInt, panel) : null;
  if (slider) {
    node.classList.add('has-slider');
    group.appendChild(slider.node);
  }
  group.appendChild(input);
  if (element.unit) group.appendChild(make('span', 'unit', element.unit));
  node.appendChild(group);
  // L4: the entry's well is its target - a press on its unit or the space
  // around it puts the caret in the box (the slider keeps its own press).
  group.addEventListener('mousedown', (event) => {
    if (event.target === group || event.target.classList.contains('unit')) {
      event.preventDefault();
      if (!input.disabled) input.focus();
    }
  });
  let served = '';
  // A text box narrower than what it holds shows the whole of it on hover.
  if (!numeric) input.addEventListener('input', () => { title.own(input.value); });
  // A new value is a new question: the last refusal's mark goes.
  input.addEventListener('input', () => {
    if (input.hasAttribute('aria-invalid')) input.removeAttribute('aria-invalid');
  });
  // O10 (WDG8-3): the wheel over a focused number box changed it silently,
  // and the next command carried it. The wheel scrolls the page instead.
  if (numeric) {
    input.addEventListener('wheel', (event) => {
      if (document.activeElement !== input) return;
      event.preventDefault();
      window.scrollBy(0, event.deltaY);
    }, { passive: false });
  }
  // O14 (PM8-7): Return, or leaving the box, commits what it says - only if
  // it says something the station does not hold. Escape puts back what the
  // station holds.
  const commit = () => {
    if (input.disabled || !element.writable || input.value === served) return;
    panel.commit(element, input.value);
  };
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); commit(); }
    else if (event.key === 'Escape' && input.value !== served) {
      input.value = served;
      if (slider) slider.follow();
    }
  });
  input.addEventListener('change', commit);
  if (slider) slider.onRelease(commit);
  const widget = {
    node,
    control: input,
    // Never overwrite what the operator is typing: focused, or edited away
    // from the last value the server sent - by the box or by its slider.
    isDirty: () => document.activeElement === input || input.value !== served,
    // Typed and not committed (MOD-6): what travels with a command. Focus
    // alone is not an edit - a focused box is not refreshed, so its
    // unchanged text may be a value behind.
    isEdited: () => input.value !== served,
    readValue: () => input.value,
    /** A commit landed: what the box says is what the station holds. */
    accept: (value) => { served = String(value); },
    /** A commit was refused: the box says what the station holds again. */
    revert: () => {
      input.value = served;
      if (slider) slider.follow();
    },
    setText: (text) => {
      // An int box never shows "5.000", whatever the state formatted.
      const next = isInt ? intText(text)
        : ((text === null || text === undefined) ? '' : String(text));
      if (next === served && input.value === served) return;
      served = next;
      input.value = served;
      if (!numeric) title.own(served);
      if (slider) slider.follow();
    },
    setEnabled: (flag) => {
      if (input.disabled === !flag) return;
      input.disabled = !flag;
      if (slider) slider.setEnabled(flag);
      node.classList.toggle('disabled', !flag);
    },
    setReason: (reason) => {
      title.reason(reason);
      if (slider) slider.setReason(reason);
    },
  };
  return widget;
}

/** The slider half of a slider entry: a 6 px sunk groove, an ink fill to
 *  the value and a fader cap (Signature: a 16 x 30 key cap with a lip and
 *  an ink index line) drawn over the range's own invisible thumb, at the
 *  thumb's place (styles.css). It never animates: a tweened position is a
 *  position the probe never held. Its travel is the
 *  schema's display range; the entry's Param still validates what is typed,
 *  so the range clamps only what it can show, never what the box holds. */
function renderSlider(input, element, isInt, panel) {
  const low = Number(element.slider[0]);
  const high = Number(element.slider[1]);
  const node = make('span', 'slider');
  node.appendChild(make('span', 'slider-track'));
  node.appendChild(make('span', 'slider-fill'));
  const range = make('input', 'slider-range');
  range.type = 'range';
  range.min = String(low);
  range.max = String(high);
  const decimals = element.decimals === undefined ? 3 : element.decimals;
  range.step = isInt ? '1' : String(Math.pow(10, -decimals));
  range.tabIndex = 0;
  range.setAttribute('aria-label', nameFor(captionText(element) + ' slider', ownerOf(panel)));
  const title = titler(range);
  node.appendChild(range);
  const cap = make('span', 'slider-cap');
  cap.setAttribute('aria-hidden', 'true');
  node.appendChild(cap);
  let filled = null;
  const paint = () => {
    const at = Math.max(low, Math.min(high, Number(range.value)));
    const share = high > low ? ((at - low) / (high - low)) * 100 : 0;
    const next = share.toFixed(2) + '%';
    if (next === filled) return;
    filled = next;
    node.style.setProperty('--fill', next);
    node.style.setProperty('--fill-f', (share / 100).toFixed(4));
  };
  /** The entry's number, shown on the range (clamped to its travel). */
  const follow = () => {
    const number = Number(input.value);
    if (input.value.trim() === '' || !isFinite(number)) return;
    const at = String(Math.max(low, Math.min(high, number)));
    if (range.value !== at) range.value = at;
    paint();
  };
  range.addEventListener('input', () => {
    const number = Number(range.value);
    input.value = isInt ? String(Math.round(number)) : number.toFixed(decimals);
    paint();
  });
  // L6: the keyboard moves 1 % of the travel per arrow and 10 % per Page
  // key, from what the ENTRY says; Home and End do nothing. The value goes
  // to the box, as a drag does, and travels with the next command.
  range.addEventListener('keydown', (event) => {
    const delta = sliderKeyDelta(low, high, event.key);
    if (delta === null) return;
    event.preventDefault();
    if (!delta || range.disabled) return;
    const typed = Number(input.value);
    const from = input.value.trim() !== '' && isFinite(typed) ? typed : Number(range.value);
    const next = Math.max(low, Math.min(high, from + delta));
    input.value = isInt ? String(Math.round(next)) : next.toFixed(decimals);
    follow();
  });
  input.addEventListener('input', follow);
  input.addEventListener('change', follow);
  // O14: a released drag, or a released slider key, commits (Tk and Qt
  // commit on release too). The entry owns the commit; this only says when.
  let released = null;
  range.addEventListener('change', () => { if (released) released(); });
  range.addEventListener('keyup', (event) => {
    if (released && sliderKeyDelta(low, high, event.key)) released();
  });
  follow();
  paint();
  return {
    node,
    range,
    follow,
    onRelease: (fn) => { released = fn; },
    setEnabled: (flag) => {
      if (range.disabled === !flag) return;
      range.disabled = !flag;
      node.classList.toggle('disabled', !flag);
    },
    setReason: (reason) => title.reason(reason),
  };
}

/** O10 (WDG8-2): a command whose answer is still out reads busy - to the
 *  eye (.is-busy) and to a screen reader (aria-busy) - and stays focusable,
 *  so focus is not thrown off it while it works. */
function busyMarker(control) {
  return (flag) => {
    control.classList.toggle('is-busy', Boolean(flag));
    if (flag) putAttr(control, 'aria-busy', 'true');
    else if (control.hasAttribute('aria-busy')) control.removeAttribute('aria-busy');
  };
}

/** `disabled`, written only when it changes (F21). */
function enabler(control) {
  return (flag) => { if (control.disabled === !flag) return; control.disabled = !flag; };
}

/** A command. Disabled, its title says why (L3); a `go` command also has
 *  one muted caption under its row saying the same (PanelCard places it
 *  and says at most one per row). Named by its words and its model (L16). */
function renderButton(panel, element) {
  const node = make('div', 'row command');
  const words = sentenceCase(element.text || element.command);
  const button = make('button', 'button ' + roleClass(element.role), words);
  button.type = 'button';
  button.setAttribute('aria-label', nameFor(words, ownerOf(panel)));
  const mark = commandGlyph(element);
  if (mark) button.insertBefore(glyph(mark), button.firstChild);
  button.addEventListener('click', () => panel.run(element));
  node.appendChild(button);
  const title = titler(button);
  const widget = {
    node,
    setEnabled: enabler(button),
    setReason: (reason) => { title.reason(reason); widget.reason = reason; },
    setBusy: busyMarker(button),
    reason: '',
  };
  if (element.role === 'go') {
    widget.note = make('p', 'gate-note');
    widget.note.hidden = true;
  }
  return widget;
}

function renderToggle(panel, element) {
  // The Safety section's stop is not a button that happens to be red: it is
  // the same physical object as the dashboard's, one size down, so the
  // operator never has to work out which control stops this model.
  if (element.model_attr === 'is_estopped') return renderStopToggle(panel, element);
  // A toggle looks like a toggle: a lamp that is lit or not, the state's
  // words beside it, and aria-pressed for anything that cannot see the lamp.
  const node = row(element, 'toggle-row');
  const button = make('button', 'button toggle off');
  button.type = 'button';
  const lamp = make('span', 'toggle-lamp');
  lamp.setAttribute('aria-hidden', 'true');
  const face = make('span', 'toggle-face');
  button.appendChild(lamp);
  button.appendChild(face);
  labelControl(node, button, element, panel);
  const title = titler(button);
  button.addEventListener('click', () => panel.runToggle(element));
  node.appendChild(button);
  const owner = ownerOf(panel);
  let last = null;
  const show = (on) => {
    if (last === Boolean(on)) return;
    last = Boolean(on);
    const shown = toggleFace(on ? (element.true_text || 'On')
                                : (element.false_text || 'Off'), element.text);
    // A face that says the action ("Enter manual mode") needs no caption
    // beside it; a bare "On"/"Off" does ("Sync X  On"). The caption stays
    // in the page either way as the control's label.
    node.classList.toggle('bare-face', /^(On|Off)$/.test(shown.text));
    face.textContent = shown.text;
    // The aside, if the schema wrote one; otherwise the words themselves,
    // so a face cut short by the fixed width is still readable in full.
    title.own(shown.hint || shown.text);
    // L16 (WCAG 2.5.3): the name is the words on the face, and the model;
    // aria-pressed says the state.
    button.setAttribute('aria-label', nameFor(shown.text, owner));
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
    button.className = 'button toggle ' + roleClass(on ? element.on_role : element.off_role)
      + (on ? ' on' : ' off');
  };
  show(false);
  return {
    node,
    setOn: show,
    setEnabled: enabler(button),
    setReason: (reason) => title.reason(reason),
    setBusy: busyMarker(button),
  };
}

/** A model's own stop, in Diagnostics (tier 3): a small switch, not a second
 *  red disc - the rail's disc is the stop an operator reaches for (E,
 *  2026-09-25). Off it is an outlined track; latched the track is signal red
 *  with a white knob, the one place besides the disc that red may sit. The
 *  schema's own wording is its title. */
function renderStopToggle(panel, element) {
  const node = row(element, 'switch-row');
  // L4: the track and its words are one target, 36 px tall - the drawn
  // track alone was 34 x 20.
  const button = make('button', 'switch');
  button.type = 'button';
  button.setAttribute('role', 'switch');
  const track = make('span', 'switch-track');
  track.setAttribute('aria-hidden', 'true');
  track.appendChild(make('span', 'switch-knob'));
  button.appendChild(track);
  // O16 (PM8-8): one word for one thing - the switch's words are the
  // schema's ("Stop this model" / "Stopped"); its tooltip says how it clears.
  const wordsFor = (on) => sentence((on ? element.true_text : element.false_text)
    || (on ? 'Stopped' : 'Stop this model'));
  const words = make('span', 'switch-words', wordsFor(false));
  button.appendChild(words);
  button.addEventListener('click', () => panel.runToggle(element));
  node.appendChild(button);
  const title = titler(button);
  let last = null;
  const model = sentence(panel.title || panel.name);
  const show = (on) => {
    if (last === Boolean(on)) return;
    last = Boolean(on);
    const said = wordsFor(on);
    putText(words, said);
    button.classList.toggle('is-latched', last);
    title.own(sentence(on ? (element.tooltip_on || element.tooltip || '')
                          : (element.tooltip || '')));
    // Named by its words and its model, so a list of switches does not read
    // "Stop, Stop" (WDG-4, L16).
    button.setAttribute('aria-label', nameFor(said, model));
    button.setAttribute('aria-checked', on ? 'true' : 'false');
  };
  show(false);
  return {
    node,
    setOn: show,
    setEnabled: enabler(button),
    setReason: (reason) => title.reason(reason),
  };
}

/** A tick box (G3): the box IS the value. Its caption is the row's label;
 *  the schema's tooltip, when there is one, is its accessible name and its
 *  title, so a column of "Launch" boxes reads "Launch Stepper probe", ...
 *  The box is set from the state on every poll and never trusted: a change
 *  sends the NEW value computed from the model (PanelCard.runCheckbox), and
 *  the poll after the answer puts the box where the model says it is. */
function renderCheckbox(panel, element) {
  const node = row(element, 'check');
  const input = make('input', 'checkbox');
  input.type = 'checkbox';
  labelControl(node, input, element, panel);
  input.name = element.model_attr || '';
  if (element.tooltip) {
    input.setAttribute('aria-label', element.tooltip);
    input.title = element.tooltip;
  }
  input.addEventListener('change', () => panel.runCheckbox(element));
  node.appendChild(input);
  return {
    node,
    setOn: (on) => {
      // Diffed against the box itself, not a remembered value: a click the
      // model refused has flipped the box, and only this puts it back.
      const next = Boolean(on);
      if (input.checked !== next) input.checked = next;
    },
    setEnabled: (flag) => {
      if (input.disabled === !flag) return;
      input.disabled = !flag;
      node.classList.toggle('disabled', !flag);
    },
  };
}

function renderDropdown(panel, element) {
  const node = row(element);
  const select = make('select', 'select');
  labelControl(node, select, element, panel);
  const title = titler(select);
  select.name = element.model_attr || '';
  const placeholder = make('option', null, 'Select…');
  placeholder.value = '';
  placeholder.disabled = true;
  select.appendChild(placeholder);
  select.addEventListener('change', () => {
    title.own(select.value);
    if (select.value !== '') panel.run(element, [select.value]);
  });
  // Options are re-read on focus, not once at first render: a probe added
  // after the page loaded has to appear in the list (WEB-22).
  select.addEventListener('focus', () => panel.loadOptions(element, select));
  // Signature: the select is a key with the disclosure glyph turned down
  // at its right; the glyph is drawn over the key and passes clicks on.
  const key = make('span', 'select-key');
  key.appendChild(select);
  key.appendChild(glyph('disclosure'));
  node.appendChild(key);
  panel.loadOptions(element, select);
  return {
    node,
    setText: (text) => {
      const wanted = (text === null || text === undefined) ? '' : String(text);
      if (select.value !== wanted) {
        const known = Array.prototype.some.call(select.options, (o) => o.value === wanted);
        if (known || wanted === '') select.value = wanted;
      }
      // The whole name of what is chosen, one hover away (F15).
      title.own(select.value);
    },
    setEnabled: enabler(select),
    setReason: (reason) => title.reason(reason),
  };
}

function renderRegionSelect(panel, element) {
  const node = row(element, 'region');
  const value = make('span', 'value is-empty', 'Not set');
  // The schema's words, with the ellipsis a control that opens a picker
  // carries ("Set capture region…").
  const button = make('button', 'button ' + roleClass(element.role),
                      sentenceCase(element.text || 'Pick region') + '…');
  button.type = 'button';
  button.setAttribute('aria-label', nameFor(button.textContent, ownerOf(panel)));
  const title = titler(button);
  button.addEventListener('click', () => panel.pickRegion(element));
  node.appendChild(value);
  node.appendChild(button);
  return {
    node,
    setText: (text) => {
      const shown = (text === '' || text === null || text === undefined) ? 'Not set' : String(text);
      if (value.textContent === shown) return;
      value.textContent = shown;
      value.classList.toggle('is-empty', shown === 'Not set');
    },
    setEnabled: enabler(button),
    setReason: (reason) => title.reason(reason),
  };
}

function renderFileSave(panel, element) {
  const node = make('div', 'row command');
  const button = make('button', 'button ' + roleClass(element.role),
                      sentenceCase(element.text || 'Save'));
  button.type = 'button';
  button.setAttribute('aria-label', nameFor(button.textContent, ownerOf(panel)));
  button.insertBefore(glyph(commandGlyph(element)), button.firstChild);
  button.addEventListener('click', () => panel.download(element));
  node.appendChild(button);
  const title = titler(button);
  return {
    node,
    setEnabled: enabler(button),
    setReason: (reason) => title.reason(reason),
  };
}

/** The file a `file_open` command reads is a path on the STATION, which is
 *  this same machine (the server answers localhost only). Two ways to name
 *  it: type the path, or choose a file here, which is uploaded into the
 *  model's own output folder (/api/upload) and loaded from there. Either
 *  way the command gets the path it declares (F13, WDG-5). */
function renderFileOpen(panel, element) {
  const node = make('div', 'row file-open');
  const label = sentenceCase(element.text || 'Open');
  const path = make('input', 'input path-input');
  path.type = 'text';
  path.autocomplete = 'off';
  path.spellcheck = false;
  path.placeholder = 'Path to a saved run';
  path.setAttribute('aria-label', nameFor(label + ': path on the station', ownerOf(panel)));
  path.addEventListener('input', () => { path.title = path.value; });
  const extensions = (element.extensions || []).map((e) => '.' + String(e).replace(/^\./, ''));
  const picker = make('input', 'file-picker');
  picker.type = 'file';
  if (extensions.length) picker.accept = extensions.join(',');
  picker.tabIndex = -1;
  picker.setAttribute('aria-hidden', 'true');
  const choose = make('button', 'button', 'Choose file…');
  choose.type = 'button';
  choose.setAttribute('aria-label', nameFor('Choose file…', ownerOf(panel)));
  choose.title = 'Choose a ' + (extensions.join(' or ') || 'file')
    + ' on this computer; it is copied to the station and loaded';
  choose.addEventListener('click', () => picker.click());
  picker.addEventListener('change', async () => {
    const file = picker.files && picker.files[0];
    picker.value = '';
    if (!file) return;
    const saved = await panel.upload(element, file);
    if (saved) {
      path.value = saved;
      path.title = saved;
      await panel.run(element, [saved]);
    }
  });
  const button = make('button', 'button ' + roleClass(element.role), label);
  button.type = 'button';
  button.setAttribute('aria-label', nameFor(label, ownerOf(panel)));
  const load = () => {
    const typed = path.value.trim();
    if (!typed) {
      panel.showRefused('Type the path of a saved run, or choose a file.', element);
      path.focus();
      return;
    }
    panel.run(element, [typed]);
  };
  button.addEventListener('click', load);
  path.addEventListener('keydown', (event) => { if (event.key === 'Enter') load(); });
  node.appendChild(path);
  node.appendChild(choose);
  node.appendChild(button);
  node.appendChild(picker);
  const setButton = enabler(button);
  const setChoose = enabler(choose);
  const setPath = enabler(path);
  return {
    node,
    setEnabled: (flag) => { setButton(flag); setChoose(flag); setPath(flag); },
  };
}

function renderPlot(panel, element) {
  const node = row(element, 'wide');
  const frame = make('div', 'plot-frame');
  const canvas = make('canvas', 'plot');
  canvas.width = 420;
  canvas.height = 180;
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', sentence(element.text || 'plot'));
  // The empty state is text on the page, not pixels on the canvas: it reads
  // at the page's size and a screen reader can find it.
  const empty = make('p', 'empty-note', emptyText(element, element.data_command));
  frame.appendChild(canvas);
  frame.appendChild(empty);
  node.appendChild(frame);
  // L15: with nothing to draw the pane is its one caption line, not a
  // 200 px box; the canvas takes its room when there is a series.
  frame.classList.add('is-empty');
  let drawn = null;
  return {
    node,
    dataCommand: element.data_command,
    setData: (data) => {
      // Redrawn only when the series, the room it has, or whether it is
      // live changed (F21). A plot behind a closed disclosure has no room:
      // it is drawn when it is opened, on the next data cycle.
      const frozen = Boolean(panel.isFrozen && panel.isFrozen());
      const key = JSON.stringify(data) + '|' + frame.clientWidth + '|' + frozen;
      if (key === drawn) return;
      drawn = key;
      // The canvas is given its room before it is drawn, so it is sized to
      // it; toggle(force) and a guarded `hidden`, so a redraw that changes
      // nothing writes nothing (F21).
      const has = normalisePoints(data).length >= 2;
      frame.classList.toggle('is-empty', !has);
      const isDrawn = drawSeries(canvas, data, element, frozen);
      if (empty.hidden !== isDrawn) empty.hidden = isDrawn;
      frame.classList.toggle('is-empty', !isDrawn);
    },
    setEnabled: (flag) => { node.classList.toggle('disabled', !flag); },
  };
}

function renderImage(panel, element) {
  const node = row(element, 'wide');
  const frame = make('div', 'plot-frame');
  const picture = make('img', 'picture');
  picture.alt = sentence(element.text || 'image');
  picture.width = 640;
  picture.height = 360;
  // A model with nothing to draw can answer with no picture at all; the
  // frame then says what to do next instead of showing a broken image.
  const empty = make('p', 'empty-note', emptyText(element, element.data_command));
  empty.hidden = true;
  // Written only when they change: the picture reloads every second (F21).
  // L15: no picture is one caption line, not the picture's room.
  const shown = (isShown) => {
    if (empty.hidden !== isShown) empty.hidden = isShown;
    if (picture.hidden !== !isShown) picture.hidden = !isShown;
    frame.classList.toggle('is-empty', !isShown);
  };
  picture.addEventListener('load', () => shown(true));
  picture.addEventListener('error', () => shown(false));
  frame.appendChild(picture);
  frame.appendChild(empty);
  node.appendChild(frame);
  return {
    node,
    dataCommand: element.data_command,
    isBinary: true,
    setData: (url) => { picture.src = url; },
    setEnabled: (flag) => { node.classList.toggle('disabled', !flag); },
  };
}

/** A lamp is lit or it is not: a dot and a word. Lit is ink, off is a
 *  hollow ring, and a lamp whose ACTIVE role is danger (a fault, a lost
 *  stage) is the signal colour - never the trace, which is for numbers. */
function renderIndicator(panel, element) {
  const node = row(element, 'lamp-row');
  const lamp = make('span', 'lamp');
  node.appendChild(lamp);
  let last = null;
  return {
    node,
    setOn: (on) => {
      if (last === Boolean(on)) return;
      last = Boolean(on);
      lamp.className = 'lamp ' + roleClass(on ? element.on_role : element.off_role)
        + (on ? ' on' : ' off');
      lamp.textContent = on ? 'Yes' : 'No';
    },
    setEnabled: (flag) => { node.classList.toggle('disabled', !flag); },
  };
}

function renderLogStream(panel, element) {
  if (element.detached) return renderDetachedLog(panel, element);
  const node = row(element, 'wide');
  const feed = make('pre', 'feed');
  feed.dataset.empty = emptyText(element, element.source_command);
  feed.tabIndex = 0;
  feed.setAttribute('aria-label', sentence(element.text || 'log'));
  node.appendChild(feed);
  return {
    node,
    dataCommand: element.source_command,
    setData: (data) => {
      const text = ((data && data.lines) || []).join('\n');
      if (feed.textContent === text) return;
      feed.textContent = text;
      feed.scrollTop = feed.scrollHeight;
    },
    setEnabled: (flag) => { node.classList.toggle('disabled', !flag); },
  };
}

/** A detached log stream (G4): a button in the element's place, opening ONE
 *  non-modal panel that holds the feed. The panel is not an overlay - there
 *  is no scrim, the page behind it stays live - and it sits under the rail
 *  in z-order, so the stop is never covered (F1). Escape or Close shuts it
 *  and gives focus back to the button (F12); pressing the button again
 *  brings the open panel forward instead of making a second one. Its source
 *  is polled only while it is open (PanelCard.wantsData), and it goes when
 *  its card goes.
 *
 *  It opens in its own card, directly under its button, and pushes the
 *  rest of the card down (I3, UXPM5-3): pinned to the rack's corner it
 *  covered the next card's inputs and that card's own button. In the card's
 *  flow it can cover nothing, it always lies inside the rack, and it is
 *  next to what opened it. One panel is open at a time: opening another
 *  closes this one (Dashboard.openFloating). */
function renderDetachedLog(panel, element) {
  const node = make('div', 'row opener');
  const caption = sentenceCase(element.text || element.source_command || 'log');
  const button = make('button', 'button role-neutral', caption + '…');
  button.type = 'button';
  button.setAttribute('aria-label', nameFor(caption + '…', ownerOf(panel)));
  const mark = commandGlyph(element);
  if (mark) button.insertBefore(glyph(mark), button.firstChild);
  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-expanded', 'false');
  node.appendChild(button);
  const dashboard = panel.dashboard;
  let win = null;
  let feed = null;
  let widget = null;

  const isOpen = () => Boolean(win && !win.hidden && win.isConnected);

  const build = () => {
    controlSerial += 1;
    const titleId = 'log-window-title-' + controlSerial;
    win = make('section', 'log-window');
    win.id = 'log-window-' + controlSerial;
    win.setAttribute('role', 'dialog');
    win.setAttribute('aria-modal', 'false');
    win.setAttribute('aria-labelledby', titleId);
    win.tabIndex = -1;
    win.hidden = true;
    const head = make('header', 'log-window-head');
    const title = make('h2', 'log-window-title',
                       sentence(panel.title || panel.name) + ' — ' + caption);
    title.id = titleId;
    title.setAttribute('translate', 'no');
    const close = make('button', 'ghost log-window-close', 'Close');
    close.type = 'button';
    close.title = 'Close the ' + caption.toLowerCase() + ' (Escape)';
    close.addEventListener('click', () => hide());
    head.appendChild(title);
    head.appendChild(close);
    feed = make('pre', 'feed log-window-feed');
    feed.dataset.empty = emptyText(element, element.source_command);
    feed.tabIndex = 0;
    feed.setAttribute('aria-label', caption);
    win.appendChild(head);
    win.appendChild(feed);
    // Escape inside the panel closes it. The page's own Escape handler
    // leaves a key that started in here to this one (Dashboard).
    win.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      hide();
    });
    button.setAttribute('aria-controls', win.id);
    win.closeFloating = () => hide();
    node.appendChild(win);
  };

  const show = () => {
    if (!win) build();
    if (!isOpen()) {
      win.hidden = false;
      button.setAttribute('aria-expanded', 'true');
      if (dashboard) dashboard.openFloating(win);
      // Not a second wait for the data cadence: it opens with its lines.
      panel.loadData(widget);
    } else if (dashboard) {
      dashboard.raiseFloating(win);
    }
    win.focus({ preventScroll: true });
    // In the card's flow it may open below the fold: bring it into view,
    // clear of the rail above and the tray below (scroll-margin, CSS).
    win.scrollIntoView({ block: 'nearest' });
  };

  const hide = () => {
    if (!isOpen()) return;
    const hadFocus = win.contains(document.activeElement);
    win.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    if (dashboard) dashboard.closeFloating(win);
    if (hadFocus || document.activeElement === document.body) {
      if (dashboard) dashboard.restoreFocus(button, dashboard.dom.cards);
      else button.focus({ preventScroll: true });
    }
  };

  button.addEventListener('click', show);
  widget = {
    node,
    dataCommand: element.source_command,
    isOpen,
    setData: (data) => {
      if (!feed) return;
      const text = ((data && data.lines) || []).join('\n');
      if (feed.textContent === text) return;
      feed.textContent = text;
      feed.scrollTop = feed.scrollHeight;
    },
    setEnabled: (flag) => { node.classList.toggle('disabled', !flag); },
    /** The card is going: so is its panel. */
    dispose: () => {
      if (!win) return;
      const hadFocus = win.contains(document.activeElement);
      if (dashboard) dashboard.closeFloating(win);
      win.remove();
      win = null;
      feed = null;
      if (hadFocus && dashboard) dashboard.restoreFocus(null, dashboard.dom.cards);
    },
  };
  return widget;
}

function renderInternal(panel, element) {
  // Declared so the conformance test passes and so an `internal` element
  // cannot silently fall through to "nothing rendered, nothing said".
  return { node: null, setEnabled: () => {} };
}

const ELEMENT_RENDERERS = {
  readonly: renderReadonly,
  entry: renderEntry,
  button: renderButton,
  toggle: renderToggle,
  checkbox: renderCheckbox,
  dropdown: renderDropdown,
  region_select: renderRegionSelect,
  file_save: renderFileSave,
  file_open: renderFileOpen,
  plot: renderPlot,
  image: renderImage,
  indicator: renderIndicator,
  log_stream: renderLogStream,
  internal: renderInternal,
};

// ==========================================================================
// plot drawing - deliberately dumb, and tolerant of whatever shape the
// model's series command hands back.
// ==========================================================================
function normalisePoints(data) {
  const raw = (data && data.data !== undefined) ? data.data : data;
  if (!raw) return [];
  if (Array.isArray(raw.x) && Array.isArray(raw.y)) {
    return raw.x.map((x, i) => [Number(x), Number(raw.y[i])]);
  }
  if (!Array.isArray(raw)) return [];
  return raw.map((point, index) => {
    if (Array.isArray(point)) return [Number(point[0]), Number(point[1])];
    if (point && typeof point === 'object') return [Number(point.x), Number(point.y)];
    return [index, Number(point)];
  }).filter((p) => isFinite(p[0]) && isFinite(p[1]));
}

/** A tick label: as few digits as the span needs. */
function tickText(value, span) {
  const digits = span >= 100 ? 0 : span >= 10 ? 1 : 2;
  return Number(value).toFixed(digits);
}

/** The Bench sheet's plot (design-Sheet.md): no frame; the sheet's own
 *  tone for three horizontal gridlines; one muted x-axis; the series in the
 *  trace colour at 1.75 px - or in muted when the model is frozen (latched,
 *  stale, not answering), because a frozen line is not a live one. Sized to
 *  the room it has, at the screen's pixel density, and the canvas's own
 *  size is written only when it changes (F21). */
function drawSeries(canvas, data, element, frozen) {
  const context = canvas.getContext('2d');
  const style = getComputedStyle(document.documentElement);
  const trace = style.getPropertyValue('--trace').trim();
  const muted = style.getPropertyValue('--muted').trim();
  const grid = style.getPropertyValue('--bg').trim();
  const face = style.getPropertyValue('--font-family').trim() || 'sans-serif';
  const ratio = window.devicePixelRatio || 1;
  const cssWidth = canvas.clientWidth || 420;
  const cssHeight = canvas.clientHeight || 180;
  const width = Math.round(cssWidth * ratio);
  const height = Math.round(cssHeight * ratio);
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, cssWidth, cssHeight);
  const points = normalisePoints(data);
  // Fewer than two points is not a line: the frame's empty state says so,
  // in page text, rather than a caption painted small into the canvas.
  if (points.length < 2) return false;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const x0 = Math.min.apply(null, xs);
  const x1 = Math.max.apply(null, xs);
  const y0 = Math.min.apply(null, ys);
  const y1 = Math.max.apply(null, ys);
  const spanX = (x1 - x0) || 1;
  const spanY = (y1 - y0) || 1;
  const left = 48;
  const right = 12;
  const top = 10;
  const bottom = 26;
  const plotW = Math.max(10, cssWidth - left - right);
  const plotH = Math.max(10, cssHeight - top - bottom);
  const at = (x, y) => [left + ((x - x0) / spanX) * plotW,
                        top + plotH - ((y - y0) / spanY) * plotH];
  context.font = '12px ' + face;
  context.textBaseline = 'middle';
  context.lineWidth = 1;
  for (let i = 0; i < 3; i += 1) {
    const value = y0 + (spanY * i) / 2;
    const y = Math.round(at(x0, value)[1]) + 0.5;
    context.strokeStyle = grid;
    context.beginPath();
    context.moveTo(left, y);
    context.lineTo(left + plotW, y);
    context.stroke();
    context.fillStyle = muted;
    context.textAlign = 'right';
    context.fillText(tickText(value, spanY), left - 8, y);
  }
  const axisY = top + plotH + 6.5;
  context.strokeStyle = muted;
  context.beginPath();
  context.moveTo(left, axisY);
  context.lineTo(left + plotW, axisY);
  context.stroke();
  context.fillStyle = muted;
  context.textBaseline = 'alphabetic';
  context.textAlign = 'left';
  context.fillText(tickText(x0, spanX), left, cssHeight - 4);
  context.textAlign = 'right';
  context.fillText(String(element.x_label || tickText(x1, spanX)), left + plotW, cssHeight - 4);
  context.beginPath();
  points.forEach((point, index) => {
    const [x, y] = at(point[0], point[1]);
    if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
  });
  context.strokeStyle = frozen ? muted : trace;
  context.lineWidth = 1.75;
  context.lineJoin = 'round';
  context.stroke();
  return true;
}

/** The header row of a row-section table: an empty name cell, then the
 *  captions of the widest data row, whose cells define the columns. The
 *  per-cell captions stay in the DOM as the controls' labels, but are only
 *  shown here, once (styles.css). */
function tableHead(sections, columns) {
  let widest = null;
  for (const section of (sections || [])) {
    if (!isRowSection(section) || isCommandRow(section)) continue;
    const drawn = (section.elements || []).filter((e) => e.type !== 'internal');
    if (drawn.length === columns) { widest = drawn; break; }
  }
  if (!widest) return null;
  const head = make('div', 'section section-row table-head');
  head.setAttribute('aria-hidden', 'true');
  head.appendChild(make('span', 'row-title'));
  for (const element of widest) {
    head.appendChild(make('span', 'cell head-cell',
                          sentenceCase(element.text || element.model_attr || '')));
  }
  return head;
}

/** Consecutive commands sit on one line, as one action group, instead of
 *  stacking one per row at four different widths ("Start", "Stop", "Reset
 *  baseline", "Save" were four rows). Order is the schema's; only the
 *  grouping is the view's. A data row keeps its cells, because they are
 *  its table columns. */
function groupCommands(cells, isTableRow) {
  if (isTableRow) return cells;
  const out = [];
  let group = null;
  for (const cell of cells) {
    const isCommand = cell.classList && cell.classList.contains('command');
    if (!isCommand) { group = null; out.push(cell); continue; }
    if (!group) { group = make('div', 'actions'); out.push(group); }
    group.appendChild(cell);
  }
  return out;
}

// ==========================================================================
// PanelCard - PanelView, in the DOM.
// ==========================================================================
class PanelCard {
  constructor(dashboard, name, schema, options) {
    this.dashboard = dashboard;
    this.name = name;
    this.schema = schema;
    this.options = options || {};
    this.widgets = [];
    this.values = {};
    this.lastData = 0;
    this.isOffline = false;
    this.lost = [];
    this.title = (options && options.title) || name;
    //: The host whose page draws this model (`state.host`, Model.HOST), or
    //: null: its own page. While hosted, the entry is a group inside the
    //: host's entry and its tier-2 disclosure sits in `tiersNode`, after
    //: the host's own (handoff/brief-dashboard-contract.md).
    this.hostName = null;
    this.tiersNode = null;
    this.titleNode = null;
    //: What the head says about the stop, before any guest is folded in.
    this.headStop = { latched: false, flagged: false, words: '' };
    // An ENTRY on the sheet (Bench sheet, 2026-09-25): a 2 px ink rule, the
    // model's name, its tier-1 body; tiers 2 and 3 behind one disclosure.
    // The DOM hook keeps its old name, `card`, which is what the tests and
    // the other layers find a model's node by.
    this.node = make('section', 'card');
    this.node.tabIndex = -1;
    const head = make('header', 'card-head');
    this.head = head;
    const title = make('h2', 'card-title', sentence(this.title));
    title.setAttribute('translate', 'no');
    this.titleNode = title;
    controlSerial += 1;
    title.id = 'model-title-' + controlSerial;
    this.node.setAttribute('aria-labelledby', title.id);
    head.appendChild(title);
    // The head's right side says only what is NOT normal: stale, lost,
    // stopped, a stop that did not confirm. Normal is silence.
    const side = make('span', 'card-side');
    this.staleBadge = make('span', 'stale-badge', 'Stale');
    this.staleBadge.hidden = true;
    side.appendChild(this.staleBadge);
    this.stateWord = make('span', 'card-state');
    this.stateWord.hidden = true;
    side.appendChild(this.stateWord);
    // A stop this model did not confirm (G6, I8): marked at its own entry,
    // with no Dismiss - it stands for as long as the latch it describes.
    // Signature: led by the tripped-flag window, which drops in once.
    this.unconfirmedMark = make('span', 'unconfirmed-mark');
    this.flagWindow = flagWindow();
    this.flagWindow.addEventListener('animationend',
      () => this.flagWindow.classList.remove('is-dropping'));
    this.unconfirmedMark.appendChild(this.flagWindow);
    this.unconfirmedText = make('span', 'unconfirmed-text', 'Stop not confirmed. Treat as live.');
    this.unconfirmedMark.appendChild(this.unconfirmedText);
    this.unconfirmedMark.hidden = true;
    side.appendChild(this.unconfirmedMark);
    head.appendChild(side);
    if (options && options.openable) {
      // The Overview's press target (K4): the whole head opens the device
      // page. It is one real button, "Open" and the disclosure chevron at
      // the head's right, whose hit area is stretched over the head
      // (styles.css) - so the name stays a heading, Return and Space work,
      // and the focus ring is drawn on the head. The body is not a target:
      // it holds controls. On the device page it is not drawn at all.
      const open = make('button', 'card-open');
      open.type = 'button';
      open.appendChild(make('span', 'card-open-text', 'Open'));
      open.appendChild(chevron());
      open.setAttribute('aria-label', 'Open ' + name);
      open.title = 'Open ' + name;
      open.addEventListener('click', () => dashboard.showPage(name));
      head.appendChild(open);
      this.openButton = open;
    }
    if (options && options.closable) {
      // Closing a model is housekeeping, not a stop: it is chrome, and the
      // signal red is spent on the stop alone ("one red"). But it destructs
      // the model (owner ruling: close = destruct), so it is the quietest
      // control the entry has, it sits at the foot of the model's details,
      // it says what it does when pointed at, and it asks first
      // (Dashboard.closeModel).
      const close = make('button', 'ghost card-close', 'Close this model…');
      close.type = 'button';
      close.title = 'Close ' + name + ': it stops and disconnects. '
        + 'Reopen it from the rail.';
      close.setAttribute('aria-label', 'Close this model: ' + name);
      close.addEventListener('click', () => dashboard.closeModel(name));
      this.closeButton = close;
    }
    this.node.appendChild(head);
    // A lost device is a standing condition, not a refusal: it has its own
    // line under the header, and it stays until the device is back (F3).
    this.alert = make('p', 'card-alert');
    this.alert.hidden = true;
    this.node.appendChild(this.alert);
    // O4 (IMP8-2): a fault's own reason, in tier 1 while it stands - a
    // failed disable was two disclosures down. Ink; the red is the rule.
    this.faultLine = make('p', 'fault-line');
    this.faultLine.hidden = true;
    this.node.appendChild(this.faultLine);
    // The refusal line. It starts under the header and moves to sit under
    // the control that caused it (showRefused, F10).
    this.status = make('p', 'status');
    controlSerial += 1;
    this.status.id = 'status-' + controlSerial;
    this.status.setAttribute('role', 'status');
    this.status.setAttribute('aria-live', 'polite');
    this.status.hidden = true;
    this.node.appendChild(this.status);
    this.body = make('div', 'card-body');
    this.node.appendChild(this.body);
    this.well = null;
    this.deep = null;
    //: The commands whose answer is still out (O10): a second press on one
    //: of them is swallowed, and it reads busy until the answer lands.
    this.inFlight = new Set();
    //: The entry a refusal was pinned to (aria-invalid), if any.
    this.invalid = null;
    this.build();
  }

  /** Where a section of `tier` is drawn: tier 1 in the entry's body, tier 2
   *  in the well behind the model's disclosure, tier 3 in the Diagnostics
   *  strip inside that well. */
  containerFor(tier) {
    if (tier === 2 || tier === 3) {
      if (!this.well) this.well = make('div', 'tier-well');
      if (tier === 2) return this.well;
      if (!this.deep) this.deep = make('div', 'tier-deep');
      return this.deep;
    }
    return this.body;
  }

  build() {
    const sections = this.schema.sections || [];
    const columns = rowColumnCount(sections);
    if (columns) {
      // One grid for the whole card body; every row section is a
      // `display: contents` child of it, so the columns line up down the
      // card instead of each row measuring itself.
      this.body.classList.add('table');
      // A table earns the full width of the page: its columns are only
      // worth aligning if there is room to read them across.
      this.node.classList.add('table-card');
      this.body.style.gridTemplateColumns = rowGridColumns(columns);
    }
    // The model's key numbers are set as readings; the first non-axis one
    // is its primary reading and any after it secondary (a change).
    const readings = new Set(railElements(this.schema));
    let hasPrimary = false;
    let hasHead = false;
    //: The `go` commands of each section, for the one caption a row says
    //: about why it cannot go (L3; placed below, filled by refresh).
    this.goRows = [];
    for (const section of sections) {
      const tier = sectionTier(section);
      const isRow = isRowSection(section);
      const spans = isRow && isCommandRow(section);
      // Whose controls these are, for their names: in Setup, the row's
      // model (L16).
      this.rowOwner = isRow ? sentence(section.title || '') : '';
      // A table has one header row: the column captions are said once,
      // above the first data row, instead of beside every cell.
      if (isRow && !spans && !hasHead) {
        hasHead = true;
        const head = tableHead(sections, columns);
        if (head) this.body.appendChild(head);
      }
      const block = make('div', 'section' + (isRow ? ' section-row' : '')
                         + (spans ? ' section-span' : ''));
      // An untitled row claims no name column (the rule views/qt.py settled
      // on); a titled one's caption is the row's name. A well does not open
      // onto a heading that repeats its own disclosure ("Diagnostics" under
      // "Diagnostics", L18): the title stays for a screen reader only.
      const hasTitle = !isRow || Boolean(section.title);
      let rowTitle = null;
      if (hasTitle) {
        const repeats = tier !== 1 && !isRow
          && sentenceCase(section.title || '').toLowerCase()
            === String(this.tierLabel(sections, tier)).toLowerCase();
        rowTitle = isRow
          ? make('label', 'row-title', sentence(section.title || ''))
          : make('h3', 'section-title' + (repeats ? ' sr-only' : ''), sentenceCase(section.title || ''));
        block.appendChild(rowTitle);
      }
      const cells = [];
      const axes = [];
      const goes = [];
      for (const element of (section.elements || [])) {
        const render = ELEMENT_RENDERERS[element.type];
        if (!render) {
          cells.push(make('p', 'status', 'cannot render ' + element.type));
          continue;
        }
        const widget = render(this, element);
        widget.element = element;
        widget.tier = tier;
        this.widgets.push(widget);
        if (widget.note) goes.push(widget);
        // L17: a command that exists only while a scan runs ("Cancel scan")
        // is not drawn outside one, rather than sitting greyed on its own.
        const only = element.enabled_when || [];
        widget.onlyWhileOn = COMMAND_TYPES.indexOf(element.type) !== -1
          && only.length === 1 && only[0] === 'scanning';
        // L4: in a Setup row the model's name and its Launch tick are one
        // target: the name is the tick's label.
        if (rowTitle && isRow && element.type === 'checkbox' && !rowTitle.htmlFor) {
          const box = widget.node && widget.node.querySelector('input');
          if (box) rowTitle.htmlFor = box.id;
        }
        if (widget.node) {
          if (isRow) widget.node.classList.add('cell');
          if (tier === 1 && element.type === 'readonly' && readings.has(element)) {
            const letter = axisLetter(element);
            widget.node.classList.add('reading');
            if (letter) {
              // "Position" once, then X Y Z inline beside their numbers.
              widget.node.classList.add('reading-axis');
              putText(widget.node.querySelector('.label'), letter);
              axes.push(widget.node);
            } else {
              widget.node.classList.add(hasPrimary ? 'reading-secondary' : 'reading-primary');
              hasPrimary = true;
            }
          }
          cells.push(widget.node);
        }
      }
      if (axes.length > 1) {
        const group = make('div', 'axis-group');
        group.setAttribute('role', 'group');
        const caption = make('span', 'axis-caption', sentenceCase(section.title || 'Position'));
        controlSerial += 1;
        caption.id = 'axis-caption-' + controlSerial;
        group.setAttribute('aria-labelledby', caption.id);
        const line = make('div', 'axis-line');
        for (const node of axes) line.appendChild(node);
        group.appendChild(caption);
        group.appendChild(line);
        const at = cells.indexOf(axes[0]);
        for (const node of axes) cells.splice(cells.indexOf(node), 1);
        cells.splice(at, 0, group);
        block.classList.add('has-axes');
      }
      // A row with fewer controls than the widest one is padded just before
      // its last cell, so the status column stays the status column. A row
      // that spans the table has no columns to line up with.
      if (isRow && !spans) {
        const wanted = columns + (hasTitle ? 0 : 1);
        while (cells.length && cells.length < wanted) {
          cells.splice(cells.length - 1, 0, make('span', 'cell filler'));
        }
      }
      for (const cell of groupCommands(cells, isRow && !spans)) block.appendChild(cell);
      // L3: the notes of this section's `go` commands sit under the row
      // (after their action group); refresh shows at most one.
      for (const widget of goes) {
        const at = widget.node.closest('.actions') || widget.node;
        at.parentNode.insertBefore(widget.note, at.nextSibling);
      }
      if (goes.length) this.goRows.push({ goes, block });
      this.containerFor(tier).appendChild(block);
    }
    this.rowOwner = '';
    this.buildTiers(sections);
  }

  /** The words of the disclosure over `tier`: a section's own, or the
   *  theme's default. */
  tierLabel(sections, tier) {
    const own = (sections || []).find((s) => sectionTier(s) === tier && s.disclosure);
    return (own && own.disclosure) || TIER_LABELS[tier] || (tier === 2 ? 'Details' : 'Diagnostics');
  }

  /** The model's one disclosure (tier 2), a panel-toned well under the
   *  entry, and inside it a second one (tier 3, "Diagnostics") over a strip
   *  marked by a muted rule. Their open state is the page's, per model,
   *  for the session (Dashboard.tierState) - not the browser's storage. */
  buildTiers(sections) {
    if (!this.well && !this.deep && !this.closeButton) return;
    this.containerFor(2);
    const label = (tier) => this.tierLabel(sections, tier);
    this.disclose2 = this.makeDisclosure(2, label(2), this.well);
    this.node.appendChild(this.disclose2);
    this.node.appendChild(this.well);
    if (this.deep) {
      this.disclose3 = this.makeDisclosure(3, label(3), this.deep);
      this.well.appendChild(this.disclose3);
      this.well.appendChild(this.deep);
    }
    if (this.closeButton) {
      const foot = make('div', 'well-foot');
      foot.appendChild(this.closeButton);
      this.well.appendChild(foot);
    }
    const remembered = (this.dashboard && this.dashboard.tierState)
      ? this.dashboard.tierState(this.name) : {};
    this.setTierOpen(2, Boolean(remembered[2]), true);
    if (this.deep) this.setTierOpen(3, Boolean(remembered[3]), true);
  }

  makeDisclosure(tier, text, controls) {
    const button = make('button', 'disclosure');
    button.type = 'button';
    button.dataset.tier = String(tier);
    button.appendChild(chevron());
    button.appendChild(make('span', 'disclosure-text', text));
    // L16: "Diagnostics" is said with its model; "Configure Stepper Probe"
    // already is.
    if (this.name !== SETUP_NAME) button.setAttribute('aria-label', nameFor(text, ownerOf(this)));
    controlSerial += 1;
    controls.id = 'tier-' + tier + '-' + controlSerial;
    button.setAttribute('aria-controls', controls.id);
    button.setAttribute('aria-expanded', 'false');
    button.addEventListener('click',
      () => this.setTierOpen(tier, button.getAttribute('aria-expanded') !== 'true'));
    return button;
  }

  /** Open or close tier 2 or 3. `restoring` is the build putting back what
   *  the page remembered, which is not a new choice to remember. */
  setTierOpen(tier, isOpen, restoring) {
    const target = tier === 2 ? this.well : this.deep;
    const button = tier === 2 ? this.disclose2 : this.disclose3;
    if (!target || !button) return;
    const open = Boolean(isOpen);
    if (target.hidden !== !open) target.hidden = !open;
    putAttr(button, 'aria-expanded', open ? 'true' : 'false');
    this.mark('tier-' + tier + '-open', open);
    if (!restoring && this.dashboard && this.dashboard.rememberTier) {
      this.dashboard.rememberTier(this.name, tier, open);
    }
    // What was just revealed is fetched now, not a data cycle later.
    if (open && !restoring) {
      for (const widget of this.widgets) {
        if (widget.dataCommand && !widget.isOpen && widget.node && target.contains(widget.node)) {
          this.loadData(widget);
        }
      }
    }
  }

  /** Whether `widget` sits behind a disclosure that is shut. */
  isBehindClosedTier(widget) {
    const node = widget && widget.node;
    if (!node) return false;
    // Tiers 2 and 3 exist only on the device page (K4): on the Overview the
    // well is not drawn, whatever its remembered state.
    if (this.well && this.well.contains(node) && !this.isOpened()) return true;
    return Boolean((this.well && this.well.hidden && this.well.contains(node))
      || (this.deep && this.deep.hidden && this.deep.contains(node)));
  }

  /** The device page's model (K4): alone, full width, its axis readings at
   *  the focal size (theme.READING_SIZES), its disclosures drawn. A hosted
   *  model is opened with its host's page. */
  setOpened(isOpened) {
    this.mark('is-opened', isOpened);
  }

  /** A state class on the entry, and on its tiers where a host draws those
   *  apart from it: what mutes a frozen number there reads the same class. */
  mark(name, isOn) {
    const on = Boolean(isOn);
    this.node.classList.toggle(name, on);
    if (this.tiersNode) this.tiersNode.classList.toggle(name, on);
  }

  /** Whether `node` is this model's: in its entry, or in its tiers. */
  owns(node) {
    return Boolean(node && (this.node.contains(node)
      || (this.tiersNode && this.tiersNode.contains(node))));
  }

  // -- hosted on another model's page (Model.HOST) ------------------------
  /** Draw this model on `host`'s page: its entry becomes a group after the
   *  host's tier 1 (its head the group's heading, one step down), its
   *  tier-2 disclosure and well follow the host's. Nothing is rebuilt:
   *  every control is still this model's and runs against its name. */
  attachTo(host) {
    if (this.hostName === host.name) return;
    this.detachFromHost();
    this.hostName = host.name;
    this.node.classList.add('is-hosted');
    for (const other of ['span-2', 'span-3', 'span-6', 'is-pinned']) this.node.classList.remove(other);
    if (this.titleNode) this.titleNode.setAttribute('aria-level', '3');
    host.node.insertBefore(this.node, host.disclose2 || host.firstGuestTiers() || null);
    if (this.disclose2) {
      const tiers = make('div', 'card-tiers');
      for (const name of ['is-latched', 'stale', 'is-lost', 'is-opened']) {
        if (this.node.classList.contains(name)) tiers.classList.add(name);
      }
      tiers.appendChild(this.disclose2);
      tiers.appendChild(this.well);
      this.tiersNode = tiers;
      host.node.appendChild(tiers);
    }
  }

  /** Back to a page of its own (the host closed, or it stopped hosting). */
  detachFromHost() {
    if (!this.hostName) return;
    this.hostName = null;
    this.node.classList.remove('is-hosted');
    if (this.titleNode) this.titleNode.removeAttribute('aria-level');
    if (this.tiersNode) {
      this.node.appendChild(this.disclose2);
      this.node.appendChild(this.well);
      this.tiersNode.remove();
      this.tiersNode = null;
    }
    if (this.node.parentNode) this.node.parentNode.removeChild(this.node);
  }

  /** The first guest's tiers on this entry, which the host's own come before. */
  firstGuestTiers() {
    return this.node.querySelector(':scope > .card-tiers');
  }

  /** What the head says about the stop: its own, and on the Overview (where
   *  a hosted model has no entry) the worse of its own and its guests' -
   *  a latch or an unconfirmed stop anywhere on the page shows. */
  paintHead() {
    let { latched, flagged, words } = this.headStop;
    const guests = (this.dashboard && this.dashboard.guestsOf) ? this.dashboard.guestsOf(this.name) : [];
    if (!this.isOpened()) {
      for (const guest of guests) {
        const theirs = guest.headStop;
        if (theirs.flagged && !flagged) { flagged = true; words = theirs.words; }
        latched = latched || theirs.latched;
      }
    }
    this.setStateWord(latched ? 'Stopped' : '');
    this.setUnconfirmed(flagged, words);
  }

  isOpened() {
    return this.node.classList.contains('is-opened');
  }

  /** Whether this entry is on the page being shown: every entry on the
   *  Overview, only the opened one on a device page. */
  isShown() {
    const shownPage = this.dashboard && this.dashboard.opened;
    // A hosted model is drawn on its host's page only (not on the Overview).
    if (this.hostName) return shownPage === this.hostName;
    return !shownPage || shownPage === this.name;
  }

  /** A model whose numbers are not live: latched, stale, lost, or the
   *  station not answering. Its readings and plot lines go muted. */
  isFrozen() {
    return this.node.classList.contains('is-latched') || this.node.classList.contains('stale')
      || document.body.classList.contains('is-offline');
  }

  // -- the three calls a view makes ------------------------------------
  async call(command, inputs, args) {
    return await apiPost('/api/run', {
      name: this.name, command, inputs: inputs || {}, args: args || [],
    });
  }

  /** The declared inputs of `element` plus every edited entry
   *  (`gatherInputsFor`, MOD-6). */
  gatherInputs(element) {
    return gatherInputsFor(element, this.widgets);
  }

  async run(element, args) {
    // CON-1: the element's own fixed `args` go first, then the press's -
    // `views.base.PanelView._run`. Two buttons that share a command ("Move -"
    // and "Move +") differ only by these; dropping them moved the wrong way.
    const sent = [...(element.args || []), ...(args || [])];
    // O10 (WDG8-2): one press, one command. A stop-class command is never
    // held back (a second stop is harmless and must never wait).
    const guarded = !isStopCommand(element, sent);
    if (guarded && this.inFlight.has(element)) return { status: 'busy' };
    const widget = this.widgetFor(element);
    if (guarded) {
      this.inFlight.add(element);
      if (widget && widget.setBusy) widget.setBusy(true);
    }
    let result;
    try {
      result = await this.call(element.command, this.gatherInputs(element), sent);
      // The page's own confirmation, not window.confirm: it defaults to
      // Cancel, and it never blocks the page's stop the way a native
      // dialog blocks every script on it (F17, HC-2).
      if (result.status === 'needs_confirm'
          && await this.dashboard.confirm(result.reason, confirmLabel(result))) {
        const again = (result.args || []).concat([true]);
        result = await this.call(result.command, result.inputs || {}, again);
      }
      if (result.status === 'ok') this.showRefused('');
      else if (result.status !== 'needs_confirm') this.showRefused(result.reason || '', element);
      // R4: the station is going away on purpose; the page waits for the
      // new one instead of calling it "not answering".
      if (isRestartAnswer(this.name, element.command, result)) {
        this.dashboard.awaitRestart();
        return result;
      }
      await this.dashboard.refreshNow();
    } catch (err) {
      this.showRefused('The station did not answer (' + failureReason(err)
        + '). Check that it is running, then try again.', element);
      result = { status: 'failed', reason: String(err) };
    } finally {
      if (guarded) {
        this.inFlight.delete(element);
        if (widget && widget.setBusy) widget.setBusy(false);
      }
    }
    return result;
  }

  /** O14 (PM8-7): one entry's value, committed on its own - a released
   *  slider, Return in the box, or leaving it - through `_commit`, the path
   *  Tk and Qt take. Only this field is sent. A refusal is said at the field
   *  and the box goes back to what the station holds. */
  async commit(element, value) {
    const widget = this.widgetFor(element);
    let result;
    try {
      result = await this.call('_commit', { [element.model_attr]: value }, []);
    } catch (err) {
      this.showRefused('The station did not answer (' + failureReason(err)
        + '), so ' + captionText(element).toLowerCase() + ' was not changed.', element);
      return { status: 'failed' };
    }
    if (result.status === 'ok') {
      if (widget && widget.accept) widget.accept(value);
      this.showRefused('');
    } else {
      if (widget && widget.revert) widget.revert();
      this.showRefused(result.reason || '', element);
    }
    await this.dashboard.refreshNow();
    return result;
  }

  widgetFor(element) {
    return this.widgets.find((w) => w.element === element) || null;
  }

  /** O10 (WDG8-4): the entry a refusal is about, read from its words - the
   *  refusal names the field ("X step size must be at least 1"). The
   *  longest caption that starts the sentence wins; null when none does.
   *  (Core's Refused does not name the attribute yet: see the handoff.) */
  fieldFor(reason) {
    const said = String(reason || '').toLowerCase();
    let best = null;
    let length = 0;
    for (const widget of this.widgets) {
      if (widget.element.type !== 'entry' || !widget.control) continue;
      const caption = captionText(widget.element).toLowerCase();
      if (caption && said.indexOf(caption + ' ') === 0 && caption.length > length) {
        best = widget;
        length = caption.length;
      }
    }
    return best;
  }

  /** Copy a chosen file to the station; the path it was saved at, or null
   *  with the reason on the card. */
  async upload(element, file) {
    let content;
    try {
      content = await readAsBase64(file);
    } catch (err) {
      this.showRefused('Could not read ' + file.name + ' on this computer.', element);
      return null;
    }
    let answer;
    try {
      answer = await apiPost('/api/upload', {
        name: this.name, command: element.command, filename: file.name, content,
      });
    } catch (err) {
      this.showRefused('The station did not answer (' + failureReason(err)
        + '), so ' + file.name + ' was not copied to it.', element);
      return null;
    }
    if (!answer || answer.status !== 'ok' || !answer.path) {
      this.showRefused((answer && answer.reason) || ('The station did not take '
        + file.name + '.'), element);
      return null;
    }
    return answer.path;
  }

  runToggle(element) {
    const on = Boolean(this.values[element.model_attr]);
    return this.run(element, on ? (element.off_args || []) : (element.on_args || []));
  }

  /** `PanelView._run_checkbox`: one argument, the new boolean, read from
   *  the model rather than the box - a box drawn one poll behind still
   *  flips the right way. */
  runCheckbox(element) {
    const on = Boolean(this.values[element.model_attr]);
    return this.run(element, [!on]);
  }

  async loadOptions(element, select) {
    let answer;
    try {
      answer = await apiPost('/api/options', {
        name: this.name, command: element.options_command,
      });
    } catch (err) {
      return;
    }
    const options = answer.options || [];
    const previous = select.value;
    clear(select);
    const placeholder = make('option', null, 'Select…');
    placeholder.value = '';
    placeholder.disabled = true;
    select.appendChild(placeholder);
    for (const option of options) {
      // Elided from the middle, whole name as the title: two ports that
      // differ only in their serial suffix stay two different lines (F15).
      const node = make('option', null, elideMiddle(String(option), OPTION_CHARS));
      node.value = String(option);
      node.title = String(option);
      select.appendChild(node);
    }
    select.title = select.value;
    select.value = options.map(String).indexOf(previous) !== -1 ? previous : '';
  }

  async download(element) {
    // One run, one file: /api/file runs the save command and streams what it
    // wrote. The browser never names a path - it names the command.
    const url = '/api/file?name=' + encodeURIComponent(this.name)
      + '&command=' + encodeURIComponent(element.command)
      + '&inputs=' + encodeURIComponent(JSON.stringify(this.gatherInputs(element)));
    let response;
    try {
      response = await api(url, { method: 'GET' });
    } catch (err) {
      this.showRefused('The station did not answer (' + failureReason(err)
        + '). Check that it is running, then save again.', element);
      return;
    }
    const type = response.headers.get('Content-Type') || '';
    if (!response.ok || type.indexOf('application/json') === 0) {
      const failed = 'The file did not download. Try saving again.';
      const answer = await response.json().catch(() => ({ reason: failed }));
      this.showRefused(answer.reason || failed, element);
      return;
    }
    const blob = await response.blob();
    const link = make('a');
    link.href = URL.createObjectURL(blob);
    link.download = filenameFrom(response) || 'download';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(link.href);
    this.showRefused('');
  }

  pickRegion(element) {
    this.dashboard.openRegionPicker(this, element);
  }

  // -- refresh -----------------------------------------------------------
  refresh(state) {
    // A state that crossed the Quit must not re-enable a control (I5).
    if (this.dashboard && this.dashboard.isShutDown) return;
    this.isOffline = false;
    this.values = (state && state.values) || {};
    const mode = (state && state.mode) || '';
    const isFaulted = Boolean(state && state.is_faulted);
    const now = Date.now();
    const wantsData = now - this.lastData >= DATA_POLL_MS;
    if (wantsData) this.lastData = now;
    for (const widget of this.widgets) {
      const element = widget.element;
      const kind = element.type;
      const attr = element.model_attr;
      if (kind === 'entry') {
        if (!widget.isDirty()) widget.setText(this.values[attr] === undefined ? '' : this.values[attr]);
      } else if ((kind === 'readonly' || kind === 'region_select' || kind === 'dropdown') && attr) {
        widget.setText(this.values[attr] === undefined ? '' : this.values[attr]);
      } else if (kind === 'toggle' || kind === 'indicator' || kind === 'checkbox') {
        widget.setOn(Boolean(this.values[attr]));
      } else if (kind === 'plot' || kind === 'image' || kind === 'log_stream') {
        if (wantsData && this.wantsData(widget)) this.loadData(widget);
      }
      // O4: a faulted probe's mode toggles are greyed by their own schema
      // (`disabled_when` carries "fault") and say the served reason.
      widget.setEnabled(isEnabled(element, mode, this.values));
      // L3: a disabled control says why, from the same gate.
      if (widget.setReason) widget.setReason(gateReason(element, mode, this.values));
      if (widget.onlyWhileOn && widget.node) {
        const off = !isEnabled(element, mode, this.values);
        if (widget.node.hidden !== off) widget.node.hidden = off;
      }
      // A number that has held still for CHANGING_MS settles to ink.
      if (widget.tick) widget.tick(now);
    }
    this.sayWhyNotGo(mode);
    // A lost device freezes the numbers even while the model's own loop
    // keeps ticking, so `age` alone would call them fresh (F3, HC-1).
    this.lost = lostDevices(state);
    const isLost = this.lost.length > 0;
    this.setStale(isStale(state) || isLost, isLost ? 'Connection lost' : 'Stale');
    this.setAlert(isLost ? sentence(this.title) + ' lost its ' + this.lost.join(' and ')
      + '. Its readings are frozen. Press Stop, check the cable, then relaunch from Setup.'
      : '');
    // State classes, not colour: a live model is silent; a lost one's head
    // rule turns signal red; a latched one's readings freeze to muted and
    // its head says "Stopped".
    const age = state ? state.age : null;
    const isLatched = Boolean(this.values.is_estopped);
    this.node.classList.toggle('is-live', !isLost
      && age !== null && age !== undefined && age <= STALE_AFTER_S);
    this.mark('is-lost', isLost);
    this.mark('is-latched', isLatched);
    // L1: the entry's own "Stop not confirmed. Treat as live." follows the
    // model's `stop_confirmed` (None unless latched), not an event. O4: a
    // fault is the same hazard - a disable that did not reach the board - so
    // it is marked the same way, with the fault's own reason under the head.
    const isUnconfirmed = Boolean(state) && state.stop_confirmed === false;
    this.headStop = { latched: isLatched, flagged: isUnconfirmed || isFaulted,
                      words: isUnconfirmed ? '' : faultWords(state) };
    this.paintHead();
    this.node.classList.toggle('is-faulted', isFaulted);
    const reason = isFaulted ? String(state.fault || '') : '';
    putText(this.faultLine, reason);
    if (this.faultLine.hidden !== !reason) this.faultLine.hidden = !reason;
  }

  /** L3: under a row whose `go` command is disabled, one muted caption says
   *  why - unless another `go` in the row can go (Setup's Launch and
   *  Relaunch take turns), or the model already says what unblocks it in
   *  the row (Red Percent's "Next step"), or the reason is the latch (the
   *  headline and the entry's head say that once). One caption per row. */
  sayWhyNotGo(mode) {
    const latched = mode === 'latched';
    for (const { goes, block } of this.goRows || []) {
      const canGo = goes.some((w) => !w.reason);
      const said = Array.from(block.querySelectorAll('.row.stat')).some((r) => !r.hidden
        && r.dataset.attr === 'next_step');
      let shown = false;
      for (const widget of goes) {
        // The latch is said once - the headline, the entry's "Stopped" -
        // not under every row of the page; its reason stays in the title.
        const say = !canGo && !said && !shown && Boolean(widget.reason) && !latched;
        if (say) shown = true;
        putText(widget.note, say ? widget.reason : '');
        if (widget.note.hidden !== !say) widget.note.hidden = !say;
      }
    }
  }

  /** The head's one word about the model's state, when it is not normal. */
  setStateWord(text) {
    putText(this.stateWord, text);
    if (this.stateWord.hidden !== !text) this.stateWord.hidden = !text;
  }

  /** G6: this model did not confirm the stop. A signal head rule and the
   *  sentence at its own entry; no Dismiss (I8). */
  setUnconfirmed(isUnconfirmed, words) {
    const flag = Boolean(isUnconfirmed);
    putText(this.unconfirmedText, words || 'Stop not confirmed. Treat as live.');
    if (this.unconfirmedMark.hidden !== !flag) this.unconfirmedMark.hidden = !flag;
    // The flag drops into its window once per episode - when the mark
    // appears - and not again while it stands, whatever re-shows the entry
    // (the class goes when the drop ends).
    // (F21: a class is written only when it changes - a remove of an absent
    // class still rewrites the attribute on every poll.)
    if (flag && !this.isFlagged) this.flagWindow.classList.add('is-dropping');
    if (!flag && this.flagWindow.classList.contains('is-dropping')) {
      this.flagWindow.classList.remove('is-dropping');
    }
    this.isFlagged = flag;
    this.node.classList.toggle('is-unconfirmed', flag);
  }

  /** The station stopped answering: nothing on this card is a live number
   *  any more, whatever it last said (F4, CRIT-1). The next good poll's
   *  refresh() undoes it. */
  setOffline() {
    if (this.isOffline) return;
    this.isOffline = true;
    this.setStale(true, 'Stale');
    this.node.classList.remove('is-live');
  }

  setAlert(text) {
    putText(this.alert, text);
    if (this.alert.hidden !== !text) this.alert.hidden = !text;
  }

  /** `PanelView._wants_data`: whether a data element is polled now. A
   *  detached log (G4) only while its panel is open; a plot or figure
   *  behind a shut disclosure not until it is opened (tiers 2 and 3 are on
   *  demand, so is their traffic); everything else, always. */
  wantsData(widget) {
    if (widget.isOpen) return widget.isOpen();
    // An entry that is not on the shown page (K4) is not drawn either.
    if (!this.isShown()) return false;
    return !this.isBehindClosedTier(widget);
  }

  loadData(widget) {
    const url = '/api/data?name=' + encodeURIComponent(this.name)
      + '&command=' + encodeURIComponent(widget.dataCommand);
    if (widget.isBinary) {
      widget.setData(url + '&t=' + Date.now());
      return;
    }
    apiGet(url).then((data) => {
      if (data && data.status !== 'refused' && data.status !== 'failed') widget.setData(data);
    }).catch(() => {});
  }

  setStale(isStale, label) {
    putText(this.staleBadge, label || 'Stale');
    if (this.staleBadge.hidden !== !isStale) this.staleBadge.hidden = !isStale;
    this.mark('stale', isStale);
  }

  /** Where a refusal about `element` is shown: right under the control, or
   *  under the action group or table row that holds it (F10, CRIT-3). */
  anchorFor(element) {
    const widget = element && this.widgets.find((w) => w.element === element);
    const node = widget && widget.node;
    if (!node || !node.isConnected || !this.owns(node)) return null;
    return node.closest('.section-row') || node.closest('.actions') || node;
  }

  /** Non-modal: a refusal is a sentence on the card, never a popup. It sits
   *  under the control that caused it, is brought into view, and shakes as
   *  it arrives - every time, a repeated refusal included - because a line
   *  that simply appears somewhere on a tall panel is a line the operator
   *  never sees. The next successful command from this card clears it.
   *
   *  O10 (WDG8-4, A11Y-7): when the sentence names an entry, it goes under
   *  THAT entry instead - its well opened if it was shut - and the entry is
   *  marked aria-invalid, described by the sentence, and focused. */
  showRefused(reason, element) {
    this.markInvalid(null);
    if (!reason) {
      if (!this.status.hidden) this.status.hidden = true;
      putText(this.status, '');
      return;
    }
    const field = this.reveal(this.fieldFor(reason)
      || (element && element.type === 'entry' ? this.widgetFor(element) : null));
    const anchor = field ? field.node : this.anchorFor(element);
    if (anchor && anchor.nextSibling !== this.status) {
      anchor.parentNode.insertBefore(this.status, anchor.nextSibling);
    } else if (!anchor && this.status.previousSibling !== this.faultLine) {
      this.node.insertBefore(this.status, this.faultLine.nextSibling);
    }
    putText(this.status, reason);
    this.status.hidden = false;
    this.status.classList.remove('shake');
    void this.status.offsetWidth;          // restart the animation
    this.status.classList.add('shake');
    if (this.status.scrollIntoView && !this.node.closest('[hidden]')) {
      this.status.scrollIntoView({ block: 'nearest' });
    }
    if (field) {
      this.markInvalid(field);
      if (!field.control.disabled) field.control.focus({ preventScroll: true });
    }
  }

  /** `field` if it can be shown: its tier opened on the device page. On
   *  the Overview a tier-2 field is not drawn, so null (the refusal then
   *  sits where the press was). */
  reveal(field) {
    if (!field || !field.node || !this.owns(field.node)) return null;
    const inWell = this.well && this.well.contains(field.node);
    if (inWell && !this.isOpened()) return null;
    if (inWell) this.setTierOpen(2, true);
    if (this.deep && this.deep.contains(field.node)) this.setTierOpen(3, true);
    return field.node.getClientRects().length ? field : null;
  }

  /** aria-invalid on the refused entry, described by the refusal; cleared
   *  from the last one. */
  markInvalid(field) {
    const last = this.invalid;
    if (last && last !== field && last.control) {
      last.control.removeAttribute('aria-invalid');
      last.control.removeAttribute('aria-describedby');
    }
    this.invalid = field || null;
    if (field && field.control) {
      putAttr(field.control, 'aria-invalid', 'true');
      putAttr(field.control, 'aria-describedby', this.status.id);
    }
  }

  close() {
    for (const widget of this.widgets) {
      if (widget.dispose) widget.dispose();
    }
    this.widgets = [];
    if (this.tiersNode) { this.tiersNode.remove(); this.tiersNode = null; }
    if (this.node.parentNode) this.node.parentNode.removeChild(this.node);
  }
}

/** The label on a confirmation's yes-button: the action, not "OK". */
function confirmLabel(result) {
  const command = String((result && result.command) || '');
  if (/clear_estop/.test(command)) return 'Clear the stop';
  return 'Continue';
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function filenameFrom(response) {
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename="?([^";]+)"?/);
  return match ? match[1] : '';
}

// ==========================================================================
// Dashboard
// ==========================================================================
class Dashboard {
  constructor(root) {
    this.root = root;
    this.cards = new Map();
    this.lastEventId = 0;
    this.isPolling = false;
    this.heartbeatTimer = null;
    this.heartbeatWorker = null;
    this.setupCard = null;
    this.isLaunched = false;
    this.isEstopped = false;
    this.isDrawerOpen = false;
    this.isConnected = null;
    this.isActive = false;
    //: The models holding hardware an operator should undo before leaving
    //: (`state.energized`): what the close-tab guard keys on (N3).
    this.energized = [];
    //: The rail's idle countdown lines, by model (N2).
    this.idleLines = new Map();
    this.idleKey = null;
    //: The faulted models, name -> the words their mark says (O4).
    this.faulted = new Map();
    //: When a heartbeat last landed (O12).
    this.lastBeatOk = 0;
    //: What was last said about the stop, for the announcements (O7).
    this.saidStop = null;
    this.railLines = new Map();
    //: Which page the sheet shows (K4): null is the Overview, else the name
    //: of the model whose device page it is.
    this.opened = null;
    this.navKey = null;
    //: Which model's page draws which (Model.HOST): hosted name -> host
    //: name, from `state.models[name].host`, while both are open.
    this.hostOf = new Map();
    //: Which models' tier-2 and tier-3 disclosures are open: the page's
    //: memory for the session, per model, surviving a close and reopen.
    this.tierMemory = new Map();
    //: The models latched, and those that did not confirm (G6, L1): the
    //: rail's per-model marks. `stopAction` is what a press on the disc does.
    this.latched = null;
    this.unconfirmed = new Set();
    this.stopAction = 'stop';
    //: The event the tray's one line is saying, and when it was put there.
    this.trayEvent = null;
    this.trayAt = 0;
    this.closedKey = null;
    this.ackQueue = [];
    this.confirmPending = null;
    this.isShutDown = false;
    //: The station run this page is talking to (`state.boot`, R4), and
    //: whether it is waiting for a restarted one.
    this.boot = null;
    this.isRestarting = false;
    this.restartTimer = null;
    //: The open in-page panels (a detached log's, G4), oldest first. They sit
    //: under the rail and above the rack, and go inert under an overlay.
    this.floating = [];
    this.dom = {
      stop: document.getElementById('full-stop'),
      stopFace: document.querySelector('.mushroom-face'),
      stopRing: document.getElementById('stop-ring'),
      railAlert: document.getElementById('rail-alert'),
      railLatched: document.getElementById('rail-latched'),
      idleLines: document.getElementById('idle-lines'),
      energizedLine: document.getElementById('energized-line'),
      announcePolite: document.getElementById('announce-polite'),
      announceAssertive: document.getElementById('announce-assertive'),
      headline: document.getElementById('sheet-headline'),
      headlineText: document.querySelector('#sheet-headline .headline'),
      headlineNote: document.querySelector('#sheet-headline .headline-note'),
      nav: document.getElementById('model-nav'),
      simLine: document.getElementById('sim-line'),
      cards: document.getElementById('cards'),
      closed: document.getElementById('closed-models'),
      log: document.getElementById('event-log'),
      modal: document.getElementById('ack-modal'),
      modalCount: document.getElementById('ack-count'),
      modalText: document.getElementById('ack-text'),
      modalOk: document.getElementById('ack-ok'),
      modalDialog: document.querySelector('#ack-modal .dialog'),
      confirm: document.getElementById('confirm-modal'),
      confirmText: document.getElementById('confirm-text'),
      confirmYes: document.getElementById('confirm-yes'),
      confirmNo: document.getElementById('confirm-no'),
      picker: document.getElementById('region-picker'),
      pickerCanvas: document.getElementById('region-canvas'),
      pickerClose: document.getElementById('region-close'),
      pickerUse: document.getElementById('region-use'),
      pickerHelp: document.getElementById('region-help'),
      pickerDialog: document.querySelector('#region-picker .dialog'),
      pickerFields: Array.from(document.querySelectorAll('#region-picker .region-fields input')),
      connection: document.getElementById('connection'),
      logPanel: document.getElementById('log-panel'),
      logToggle: document.getElementById('log-toggle'),
      trayLatest: document.getElementById('tray-latest'),
      trayText: document.querySelector('#tray-latest .tray-text'),
      drawer: document.getElementById('setup-drawer'),
      drawerBody: document.getElementById('drawer-body'),
      drawerClose: document.getElementById('drawer-close'),
      scrim: document.getElementById('scrim'),
      setupLink: document.getElementById('setup-link'),
      quitLink: document.getElementById('quit-link'),
    };
    this.isLogCollapsed = true;
    this.dom.stop.addEventListener('click', () => this.toggleEstopAll());
    this.dom.modalOk.addEventListener('click', () => this.acknowledge(true));
    this.buildAckHeading();
    this.dom.pickerClose.addEventListener('click', () => this.closeRegionPicker());
    this.dom.pickerUse.addEventListener('click', () => this.useTypedRegion());
    for (const field of this.dom.pickerFields) {
      field.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); this.useTypedRegion(); }
      });
    }
    this.dom.confirmYes.addEventListener('click', () => this.answerConfirm(true));
    this.dom.confirmNo.addEventListener('click', () => this.answerConfirm(false));
    this.dom.logToggle.addEventListener('click',
      () => this.setLogCollapsed(!this.isLogCollapsed));
    this.dom.setupLink.addEventListener('click', () => this.setDrawerOpen(true));
    this.dom.quitLink.addEventListener('click', () => this.quitStation());
    this.dom.drawerClose.addEventListener('click', () => this.setDrawerOpen(false));
    this.dom.scrim.addEventListener('click', () => this.setDrawerOpen(false));
    // One keyboard handler, in the capture phase so nothing on the page can
    // swallow it first. Ctrl+. stops every model from anywhere, a
    // text box included (F9). Escape answers the top-most thing over the page: a
    // confirmation is cancelled, the region picker closes, the drawer
    // withdraws, an acknowledgement is Understood.
    window.addEventListener('keydown', (event) => {
      if (this.isShutDown) return;      // nothing left to stop or answer
      if (event.ctrlKey && !event.altKey && event.key === STOP_KEY) {
        event.preventDefault();
        this.stopAll();
        return;
      }
      if (event.key !== 'Escape') return;
      if (this.confirmPending) { event.preventDefault(); this.answerConfirm(false); return; }
      if (!this.dom.picker.hidden) { this.closeRegionPicker(); return; }
      // rb-ack (A3): an acknowledgement has one answer, Understood, so
      // Escape gives it too - as Return on its focused key does.
      if (!this.dom.modal.hidden) { event.preventDefault(); this.acknowledge(); return; }
      // An in-page panel answers its own Escape (G4): the drawer behind it
      // does not also withdraw.
      if (document.activeElement && document.activeElement.closest
          && document.activeElement.closest('.log-window')) return;
      if (this.isDrawerOpen && this.dom.modal.hidden) this.setDrawerOpen(false);
    }, true);
    // Closing the tab silences the heartbeat, and the watchdog then stops
    // the station: while anything is ENERGIZED - a probe merely in a mode
    // included, not only one moving, heating or recording - the browser
    // asks first (F24, WDG-12; N3 keys it on `state.energized`). After Quit
    // there is nothing left to guard.
    window.addEventListener('beforeunload', (event) => {
      if (!this.energized.length || this.isShutDown) return;
      event.preventDefault();
      event.returnValue = '';
    });
    window.addEventListener('resize', () => this.reserveLogSpace());
    window.addEventListener('resize', () => this.pinOpened());
    // The rail is a column on the left, or a bar across the top on a phone,
    // and the tray grows when it opens; the drawer, the scrim and every
    // overlay are fixed against both, so both are measured, not assumed.
    if (typeof ResizeObserver !== 'undefined') {
      const watch = new ResizeObserver(() => this.reserveLogSpace());
      watch.observe(document.querySelector('.rail'));
      watch.observe(this.dom.logPanel);
    }
    this.paintBrowserChrome();
  }

  /** The browser's own chrome takes the page's base colour. The value is
   *  read from the theme, so this file still names no colour. */
  paintBrowserChrome() {
    const base = getComputedStyle(document.documentElement)
      .getPropertyValue('--bg').trim();
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta && base) meta.setAttribute('content', base);
  }

  // -- the Setup drawer ----------------------------------------------------
  //
  // Open at boot, because Setup is where a run begins. It withdraws on launch
  // - the one orchestrated moment on this page, together with the rail's
  // readout groups arriving behind it - and the rail's Setup button, which
  // only exists while the drawer is shut, brings it back.
  setDrawerOpen(isOpen) {
    const wasOpen = this.isDrawerOpen;
    const active = document.activeElement;
    const hadFocus = this.dom.drawer.contains(active);
    this.isDrawerOpen = Boolean(isOpen);
    if (this.isDrawerOpen && !wasOpen && active && active !== document.body && !hadFocus) {
      this.drawerReturn = active;
    }
    this.dom.drawer.classList.toggle('open', this.isDrawerOpen);
    // The scrim dims what the drawer is covering. At boot it is covering an
    // empty rack, so there is nothing to dim and no scrim.
    this.dom.scrim.hidden = !(this.isDrawerOpen && this.cards.size > 0);
    this.dom.setupLink.hidden = this.isDrawerOpen;
    this.dom.drawer.setAttribute('aria-hidden', this.isDrawerOpen ? 'false' : 'true');
    // While the drawer is open the rack behind it is inert, so Tab walks the
    // drawer, the tray and the rail - never a control under the scrim - and
    // the stop on the rail is always one of the places it walks (F12).
    this.updateInert();
    // Focus moves into the drawer - to the drawer itself, not to its Close
    // button, which is not the thing the operator came to press - and back
    // to what opened it when it withdraws (after launch: the Setup button
    // that brings it back).
    if (this.isDrawerOpen && !wasOpen) {
      this.dom.drawer.focus({ preventScroll: true });
    } else if (!this.isDrawerOpen && wasOpen
               && (hadFocus || document.activeElement === document.body)) {
      this.restoreFocus(this.drawerReturn, this.dom.setupLink);
    }
  }

  /** What may take focus right now: the rail never goes inert - the stop
   *  must be reachable from under anything - and every other layer does
   *  while something sits over it (F1, F12, WDG-8). */
  updateInert() {
    const overlays = [this.dom.modal, this.dom.picker, this.dom.confirm];
    const open = overlays.filter((layer) => !layer.hidden);
    const top = open[open.length - 1] || null;
    const covered = Boolean(top);
    const setInert = (node, flag) => { if (node && node.inert !== flag) node.inert = flag; };
    const gone = this.isShutDown;
    setInert(this.dom.cards, covered || this.isDrawerOpen || gone);
    setInert(this.dom.logPanel, covered || gone);
    setInert(this.dom.drawer, covered || !this.isDrawerOpen || gone);
    setInert(this.dom.closed, gone);
    for (const layer of overlays) setInert(layer, layer !== top);
    for (const win of this.floating) setInert(win, covered || gone);
  }

  // -- in-page panels (G4) --------------------------------------------------
  //
  // Non-modal: nothing behind them goes inert and nothing is dimmed. Each
  // opens inside its own card (I3), and only one is open at a time: opening
  // a panel closes any other, so a second log never lands on a neighbour.
  openFloating(win) {
    for (const other of this.floating.slice()) {
      if (other !== win && other.closeFloating) other.closeFloating();
    }
    if (this.floating.indexOf(win) === -1) this.floating.push(win);
    this.raiseFloating(win);
    this.updateInert();
  }

  raiseFloating(win) {
    const at = this.floating.indexOf(win);
    if (at !== -1 && at !== this.floating.length - 1) {
      this.floating.splice(at, 1);
      this.floating.push(win);
    }
  }

  closeFloating(win) {
    const at = this.floating.indexOf(win);
    if (at !== -1) this.floating.splice(at, 1);
  }

  /** Put focus back where it came from, or on a sensible neighbour when
   *  that control has gone, is hidden, or is behind something. */
  restoreFocus(target, fallback) {
    const usable = (node) => node && node.isConnected && !node.hidden
      && !node.closest('[inert]') && node.getClientRects().length > 0;
    const next = usable(target) ? target : (usable(fallback) ? fallback : this.dom.cards);
    if (next && next.focus) next.focus({ preventScroll: true });
  }

  // -- the event bar ------------------------------------------------------
  //
  // It is fixed to the bottom of the viewport, so the page has to reserve
  // exactly as much room as it takes: without that it covers the bottom of
  // the cards, which is what the bench shot showed. The height is measured
  // rather than guessed, because it changes when the bar collapses and when
  // --font-size changes.
  reserveLogSpace() {
    const panel = this.dom.logPanel;
    if (!panel || !document.body || panel.offsetHeight === undefined) return;
    document.body.style.paddingBottom = panel.offsetHeight + 'px';
    const root = document.documentElement.style;
    root.setProperty('--tray-h', panel.offsetHeight + 'px');
    // Where the rail is: a column down the left (its width is the room every
    // layer leaves beside it) or, on a phone, a bar across the top (its
    // height is the room they leave above it). Nothing may cover the stop.
    const rail = document.querySelector('.rail');
    if (!rail) return;
    const box = rail.getBoundingClientRect();
    const isColumn = box.height > box.width;
    const left = (isColumn ? Math.round(box.width) : 0) + 'px';
    const top = (isColumn ? 0 : Math.round(box.height)) + 'px';
    if (root.getPropertyValue('--rail-left') !== left) root.setProperty('--rail-left', left);
    if (root.getPropertyValue('--rail-top') !== top) root.setProperty('--rail-top', top);
  }

  /** Collapsed - which is how it starts - the tray is one line carrying the
   *  latest event. Expanded it is about six. */
  setLogCollapsed(isCollapsed) {
    this.isLogCollapsed = Boolean(isCollapsed);
    this.dom.logPanel.classList.toggle('open', !this.isLogCollapsed);
    this.dom.log.hidden = this.isLogCollapsed;
    this.dom.logToggle.textContent = this.isLogCollapsed
      ? 'Show events' : 'Hide events';
    this.reserveLogSpace();
  }

  async start() {
    // The theme's word rules first: which values are quiet, and what the
    // disclosures are called when a section does not say.
    try {
      const rules = await apiGet('/api/theme.json');
      QUIET_VALUES = new Set((rules && rules.quiet_values) || []);
      if (rules && rules.tier_labels) TIER_LABELS = Object.assign({}, TIER_LABELS, rules.tier_labels);
      if (rules && rules.gate_words) GATE_WORDS = rules.gate_words;
      if (rules && rules.watchdog) WATCHDOG = rules.watchdog;
      // The titles the page keys on, as core names them (events.py).
      const titles = (rules && rules.event_titles) || {};
      if (titles.stop_not_confirmed) UNCONFIRMED_TITLE = titles.stop_not_confirmed;
      if (titles.idle_timeout_soon) IDLE_SOON_TITLE = titles.idle_timeout_soon;
      if (titles.browser_silent) SILENT_TITLE = titles.browser_silent;
    } catch (err) { /* nothing is quiet: every value is drawn */ }
    // Start from the newest event id so a fresh tab does not replay the
    // whole session's log as if it had just happened (ERRORS-3). What has
    // happened is still history: the last few lines go into the log, and
    // the newest is the tray's line, so the tray does not say "Waiting for
    // the station…" beside a rail that says it is connected (WDG-11).
    //
    // Updated (L2, L21, round 7): the history goes into the log only. The
    // tray is status by exception, and a warning from before this tab (a
    // port no model uses, found by the scan at start) is not a standing
    // condition. The one line that does stand is "Stop not confirmed" for
    // the latch that is set now: the newest one, and only while a model is
    // latched - after a Clear it is over and a reload does not revive it.
    try {
      let latched = [];
      try {
        const now = await apiGet('/api/state');
        latched = (now && now.stop && now.stop.latched) || [];
      } catch (err) { /* nothing is known to stand */ }
      const seen = await apiGet('/api/events?since=0');
      this.lastEventId = seen.latest_id || 0;
      const history = (seen.events || []).slice(-20);
      for (const event of history) this.showEvent(event, { history: true });
      const standing = latched.length ? history.filter(isUnconfirmedEvent).pop() : null;
      if (standing) this.setTray(standing);
    } catch (err) { /* the first poll will retry */ }
    this.setLogCollapsed(true);      // one line: the latest event
    await this.loadSetup();
    this.timer = setInterval(() => this.poll(), STATE_POLL_MS);
    this.startHeartbeat();
    this.watchVisibility();
    await this.poll();
  }

  // -- browser liveness, client half (D-8 / WEB-19) ----------------------
  //
  // Deliberately NOT the same signal as the state poll, and since F
  // (2026-09-28) not on the page's thread either: a dedicated worker
  // (heartbeatWorker) beats on its own timer, which a hidden tab's
  // throttling does not reach. A hidden tab is not a gone browser - on a
  // single-screen bench PC the operator switches to the microscope window
  // with the gamepad drive live - so hiding the tab only tells the worker
  // to say so. What silences the heartbeat is the tab going (pagehide) and
  // the station's Quit; then the watchdog stops what is energized.
  startHeartbeat() {
    this.stopHeartbeat();
    if (this.isShutDown) return;
    const hidden = typeof document !== 'undefined' && Boolean(document.hidden);
    const worker = heartbeatWorker();
    if (worker) {
      worker.onmessage = (message) => this.heardBeat(message.data && message.data.ok);
      worker.postMessage({ start: true, every: HEARTBEAT_MS, bound: fetchTimeoutMs(),
        url: new URL('/api/heartbeat', window.location.href).href, hidden });
      this.heartbeatWorker = worker;
      return;
    }
    this.sendHeartbeat();
    this.heartbeatTimer = setInterval(() => this.sendHeartbeat(), HEARTBEAT_MS);
  }

  stopHeartbeat() {
    if (this.heartbeatWorker) {
      this.heartbeatWorker.terminate();
      this.heartbeatWorker = null;
    }
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  /** The page's own beat: the fallback where there is no worker. */
  async sendHeartbeat() {
    try {
      const hidden = typeof document !== 'undefined' && Boolean(document.hidden);
      const answer = await apiPost('/api/heartbeat', { hidden });
      this.heardBeat(Boolean(answer && answer.status === 'ok'));
    } catch (err) {
      // Nothing to recover: a missed heartbeat is the signal itself.
    }
  }

  /** A heartbeat landed (or did not). */
  heardBeat(isOk) {
    if (!isOk || this.isShutDown) return;
    this.lastBeatOk = Date.now();
    // O12 (PM8-3): the browser is back, so the watchdog's warning is
    // over: the tray takes it back (the log keeps it).
    if (this.trayEvent && this.trayEvent.title === SILENT_TITLE) this.setTray(null);
  }

  watchVisibility() {
    // Hidden or shown, the heartbeat goes on; the worker only says which.
    document.addEventListener('visibilitychange', () => {
      if (this.heartbeatWorker) this.heartbeatWorker.postMessage({ hidden: Boolean(document.hidden) });
    });
    // The tab going is the silence the watchdog exists for. A tab restored
    // from the back-forward cache is a browser that came back.
    window.addEventListener('pagehide', () => this.stopHeartbeat());
    window.addEventListener('pageshow', (event) => {
      if (event.persisted && !this.isShutDown) this.startHeartbeat();
    });
  }

  // -- polling ------------------------------------------------------------
  async poll() {
    if (this.isPolling || this.isShutDown) return;
    this.isPolling = true;
    // When this state was asked for: an answer to a question put before a
    // stop must not undo what that stop said (forgetUnconfirmed, G6).
    const askedAt = Date.now();
    try {
      const state = await apiGet('/api/state');
      if (this.isShutDown || this.isRestarting) return;   // crossed Quit or Restart
      // R4: another station run answers (a restart from another tab, or by
      // hand): this page's events, ids and layout are the old one's.
      if (state && state.boot) {
        if (this.boot && state.boot !== this.boot) { this.reloadPage(); return; }
        this.boot = state.boot;
      }
      this.setConnected(true);
      await this.applyState(state, askedAt);
      await this.pollEvents(state.latest_event);
    } catch (err) {
      this.setConnected(false);
    } finally {
      this.isPolling = false;
    }
  }

  async refreshNow() {
    this.isPolling = false;
    await this.poll();
  }

  /** The link line is a live region, so it is written when the link
   *  changes and at no other time (WDG-6). Down, it says since when, and
   *  every number on the page goes muted with the stale mark: a frozen
   *  number must never look like a live one (F4, CRIT-1). */
  setConnected(isConnected) {
    if (this.isShutDown || this.isRestarting || this.isConnected === isConnected) return;
    this.isConnected = isConnected;
    const link = this.dom.connection;
    if (isConnected) {
      // Status by exception: a station that answers says nothing.
      link.textContent = '';
      link.title = '';
    } else {
      const since = clockTime(new Date());
      link.textContent = 'Not answering since ' + since;
      link.title = 'The station has not answered since ' + since
        + '. Every number on this page is frozen. Is the station still running?';
      this.muteReadouts('Stale');
    }
    link.className = 'link-state' + (isConnected ? '' : ' is-down');
    document.body.classList.toggle('is-offline', !isConnected);
  }

  /** Every number on the page stops looking live: muted, with `label` as
   *  its stale mark. */
  muteReadouts(label) {
    for (const card of this.cards.values()) { card.setOffline(); card.setStale(true, label); }
    if (this.setupCard) { this.setupCard.setOffline(); this.setupCard.setStale(true, label); }
  }

  // -- Quit (G2) -----------------------------------------------------------
  //
  // The only way the console ends the program: closing the tab leaves the
  // station running for the next tab, with the watchdog as its guard. Quit
  // is allowed while active - the server's close path stops every model
  // before it closes anything. The server answers first and then exits, so
  // after the answer nothing here polls, beats or reconnects: there is no
  // station left to reach.
  async quitStation() {
    if (this.isShutDown) return;
    const isSure = await this.confirm('Quit the station? This stops every model, '
      + 'closes every port and exits the program.', 'Quit');
    if (!isSure) return;
    let answer = null;
    try {
      answer = await apiPostChecked('/api/quit', {});
    } catch (err) {
      this.setRailLine('quit', 'Quit did not reach the station — ' + failureReason(err)
        + '. It is still running.', true);
      return;
    }
    if (!answer || answer.status !== 'ok') {
      this.setRailLine('quit', 'The station did not agree to quit. It is still running.', true);
      return;
    }
    // O5: the server stops every model BEFORE it answers, and says which
    // did not confirm; the end state names those instead of saying "Off".
    this.showShutDown(Array.isArray(answer.unconfirmed) ? answer.unconfirmed : []);
  }

  /** The end-state after Quit (G2, I5): the page stops asserting anything it
   *  can no longer observe. The rail says one sentence, in ink at readout
   *  size, and focus lands on it; the stop disc is drawn inert (no red, no
   *  ring, not a button any more), whatever face it had; every alert line,
   *  acknowledgement and raw error goes; every control is disabled. */
  showShutDown(unconfirmed) {
    const unsure = (unconfirmed || []).filter((name) => this.cards.has(name));
    this.isShutDown = true;
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    this.stopHeartbeat();
    this.answerConfirm(false);
    this.closeRegionPicker();
    this.setDrawerOpen(false);
    for (const win of this.floating.slice()) {
      if (win.closeFloating) win.closeFloating();
    }
    // Nothing is left to be latched, unconfirmed or acknowledged.
    for (const key of Array.from(this.railLines.keys())) this.setRailLine(key, '');
    this.dom.railAlert.hidden = true;
    this.setUnconfirmed([], []);
    this.dom.railLatched.hidden = true;
    this.dom.headline.hidden = true;
    this.dom.stopRing.classList.remove('is-latched');
    this.dom.stopRing.classList.add('is-off');
    this.ackQueue = [];
    clear(this.dom.modalText);
    this.dom.modal.hidden = true;
    const stop = this.dom.stop;
    stop.classList.remove('is-latched');
    stop.classList.add('is-off');
    // The disc says the same as the page: off. So does every model's
    // switch, whatever it last showed.
    for (const face of document.querySelectorAll('.mushroom-face')) putText(face, 'Off');
    for (const word of document.querySelectorAll('.switch-words')) putText(word, 'Off');
    for (const line of this.idleLines.values()) line.node.remove();
    this.idleLines.clear();
    this.idleKey = null;
    this.dom.idleLines.hidden = true;
    this.renderEnergizedLine();
    this.setEnergized([]);
    stop.removeAttribute('aria-keyshortcuts');
    stop.setAttribute('aria-disabled', 'true');
    stop.setAttribute('aria-label', 'Stop: the station has shut down');
    stop.title = 'The station program has exited: there is nothing left to stop.';
    const hint = document.querySelector('.stop-hint');
    if (hint) hint.hidden = true;
    for (const card of this.cards.values()) card.setUnconfirmed(false);
    // O5 (IMP8-3): "Off" is said only for what confirmed. A model whose
    // stop at Quit did not confirm keeps its red rule and its words, its
    // switch does not read Off, and the rail names it.
    if (unsure.length) {
      for (const name of unsure) {
        const card = this.cards.get(name);
        card.setUnconfirmed(true);
        const word = card.node.querySelector('.switch-words');
        if (word) putText(word, 'Not confirmed');
      }
      this.faulted = new Map();
      this.setUnconfirmed(unsure, unsure);
      this.setRailLine('quit-unconfirmed', joinNames(unsure)
        + (unsure.length > 1 ? ' did not confirm their stops. Check them by hand.'
                             : ' did not confirm its stop. Check it by hand.'));
    }
    this.setLogCollapsed(true);
    this.setTray({ severity: 'info', text: 'Quit from the Web console' });
    document.body.classList.add('is-offline', 'is-shut-down');
    this.muteReadouts('Shut down');
    for (const control of document.querySelectorAll('button, input, select, textarea')) {
      control.disabled = true;
    }
    this.updateInert();
    const link = this.dom.connection;
    link.textContent = 'The station has shut down. You can close this tab.';
    link.title = 'The station program has exited. Start it again to reconnect.';
    link.className = 'link-state is-shut-down';
    link.tabIndex = -1;
    link.focus({ preventScroll: true });
  }

  // -- the rail's alert lines --------------------------------------------
  //
  // Sentences that must be read from across the bench and must not be
  // replaced by the next event: a stop that did not land, a model that lost
  // its device. They sit on the rail, beside the stop, and the rail grows to
  // hold them. Keyed, so a poll that repeats a line does not rewrite it.
  setRailLine(key, text, canDismiss) {
    let line = this.railLines.get(key);
    if (!text) {
      if (line) {
        line.node.remove();
        this.railLines.delete(key);
      }
    } else {
      if (!line) {
        const node = make('div', 'rail-alert-line');
        const words = make('span', 'rail-alert-text');
        node.appendChild(words);
        if (canDismiss) {
          const dismiss = make('button', 'ghost rail-alert-dismiss', 'Dismiss');
          dismiss.type = 'button';
          dismiss.addEventListener('click', () => this.setRailLine(key, ''));
          node.appendChild(dismiss);
        }
        line = { node, words };
        this.railLines.set(key, line);
        // The stop's own lines are always the first thing read.
        if (key === 'stop' || key === 'unconfirmed') {
          this.dom.railAlert.insertBefore(node, this.dom.railAlert.firstChild);
        }
        else this.dom.railAlert.appendChild(node);
      }
      putText(line.words, text);
    }
    const isEmpty = this.railLines.size === 0;
    if (this.dom.railAlert.hidden !== isEmpty) this.dom.railAlert.hidden = isEmpty;
  }

  /** One line per model that has lost a device, named by model and device
   *  (F3, HC-1). */
  renderLostLines(models) {
    const lost = new Set();
    for (const name of Object.keys(models)) {
      const devices = lostDevices(models[name]);
      if (!devices.length) continue;
      lost.add('lost:' + name);
      this.setRailLine('lost:' + name, sentence(name) + ' lost its '
        + devices.join(' and ') + '. Its readings are frozen.');
    }
    for (const key of Array.from(this.railLines.keys())) {
      if (key.indexOf('lost:') === 0 && !lost.has(key)) this.setRailLine(key, '');
    }
  }

  async applyState(state, askedAt) {
    const models = state.models || {};
    this.isActive = Boolean(state.is_active);
    this.energized = Array.isArray(state.energized) ? state.energized : [];
    for (const name of Object.keys(models)) {
      if (!this.cards.has(name)) await this.addCard(name);
      const card = this.cards.get(name);
      if (card) card.refresh(models[name]);
    }
    // Hosting first, removal second: a host that closed hands its guests
    // their own pages before its entry (which holds them) goes.
    this.placeHosted(models);
    for (const name of Array.from(this.cards.keys())) {
      if (!(name in models)) this.removeCard(name);
    }
    // A host's head folds in its guests' stop, so it is painted after all
    // of them have their state.
    for (const card of this.cards.values()) card.paintHead();
    this.renderNav(models);
    this.renderSimLine(models);
    this.layoutSheet();
    this.renderLostLines(models);
    this.renderEmptyRack();
    this.renderClosed(state.closed || []);
    // L1 (round 7): what the page says about the stop is the server's
    // `stop_words` (views.base.stop_words), never re-derived here; which
    // models are latched or did not confirm is `stop`.
    const stop = state.stop || { latched: [], unconfirmed: [], every: false };
    this.renderEstop(state.stop_words || NO_STOP_WORDS);
    this.announceStop(state.stop_words || NO_STOP_WORDS, stop);
    this.faulted = new Map(Object.keys(models).filter((n) => models[n] && models[n].is_faulted)
      .map((n) => [n, models[n].mode === 'fault']));
    this.setUnconfirmed(stop.latched || [], stop.unconfirmed || []);
    this.setEnergized(this.energized);
    this.renderEnergizedLine();
    this.renderIdleLines(models);
    this.forgetStopLine(stop, askedAt);
    let setupState = null;
    if (this.setupCard) {
      try {
        const setup = await apiGet('/api/setup');
        setupState = setup.state;
        this.setupCard.refresh(setupState);
      } catch (err) { /* the next cycle retries */ }
    }
    this.collapseSetupOnLaunch(models, setupState);
  }

  /** An empty rack says what to do next, and the drawer that does it is
   *  already open behind this. An empty screen is an invitation to act. */
  renderEmptyRack() {
    // Not while the drawer is open: the drawer IS the invitation, and a note
    // underneath it is a sentence nobody can read.
    const isEmpty = this.cards.size === 0 && !this.isDrawerOpen;
    if (isEmpty && !this.emptyNote) {
      this.emptyNote = make('p', 'rack-empty',
        'No models yet. In Setup, tick each device you are using, choose its '
        + 'port (SIM runs a model without hardware) and launch.');
      this.dom.cards.appendChild(this.emptyNote);
    } else if (!isEmpty && this.emptyNote) {
      if (this.emptyNote.parentNode) {
        this.emptyNote.parentNode.removeChild(this.emptyNote);
      }
      this.emptyNote = null;
    }
  }

  /** `Dashboard._collapse_setup` in src/views/base.py, mirrored: the
   *  first time a model exists, the wizard gives way to it. The desktop
   *  views hear `added`; the browser polls, so the same signal is "models
   *  exist" - or the Setup panel's own `is_launched`, which it flips inside
   *  `build()` and clears in `stop_system()`.
   *
   *  Edge-triggered, deliberately: the card is collapsed as the station
   *  launches and opened again when it is stopped, and in between the
   *  operator's own Hide / Expand is left alone - a level-triggered version
   *  would slam the card shut 250 ms after every re-open. */
  collapseSetupOnLaunch(models, setupState) {
    if (!this.setupCard) return;
    const hasModels = Object.keys(models || {}).length > 0;
    // No setup state and no models: the setup poll failed, which is not an
    // edge and must not be read as "stopped".
    if (!hasModels && !setupState) return;
    const isLaunched = hasModels || Boolean(setupState.is_launched);
    if (isLaunched === this.isLaunched) return;
    this.isLaunched = isLaunched;
    // A launch lands on the Overview (K4), whatever page was shown before.
    if (isLaunched && this.opened) {
      this.opened = null;
      this.layoutSheet();
      this.renderNav(Object.fromEntries(Array.from(this.cards.keys()).map((n) => [n, {}])));
    }
    this.setDrawerOpen(!isLaunched);
  }

  async addCard(name) {
    let schema;
    try {
      schema = await apiGet('/api/schema?name=' + encodeURIComponent(name));
    } catch (err) {
      return;
    }
    if (!schema || !schema.sections) return;
    const card = new PanelCard(this, name, schema, { closable: true, openable: true });
    // The one launch moment: entries arrive one after another, 60 ms apart,
    // once, as the drawer withdraws.
    card.node.classList.add('is-entering');
    card.node.style.setProperty('--stagger', String(this.cards.size));
    this.cards.set(name, card);
    this.dom.cards.appendChild(card.node);
  }

  removeCard(name) {
    const card = this.cards.get(name);
    // Its guests are never taken down with it.
    for (const guest of this.guestsOf(name)) this.unhost(guest);
    if (card) card.close();
    this.cards.delete(name);
    this.hostOf.delete(name);
  }

  // -- a model drawn on another model's page (Model.HOST) ------------------
  //
  // `state.models[name].host` is the host's name while the host is open
  // (Controller.state), never a class. The hosted model keeps its own entry,
  // controls and name; only where it is drawn changes: inside its host's
  // entry, after the host's tier 1, with no page, link or Overview entry of
  // its own. Its host closed, it is a page like any other again.
  placeHosted(models) {
    const wanted = new Map();
    for (const name of Object.keys(models || {})) {
      const host = models[name] && models[name].host;
      if (host && host !== name && host in models && this.cards.has(host) && this.cards.has(name)) {
        wanted.set(name, host);
      }
    }
    for (const [name, card] of this.cards) {
      const host = wanted.get(name) || null;
      if (card.hostName === host) continue;
      if (host) {
        card.attachTo(this.cards.get(host));
        this.hostOf.set(name, host);
      } else {
        this.unhost(card);
      }
    }
  }

  /** `card` back on the sheet, a page of its own, in station order. */
  unhost(card) {
    if (!card.hostName) return;
    card.detachFromHost();
    this.hostOf.delete(card.name);
    const names = Array.from(this.cards.keys());
    const after = names.slice(names.indexOf(card.name) + 1)
      .map((n) => this.cards.get(n)).find((c) => !c.hostName && c.node.parentNode === this.dom.cards);
    this.dom.cards.insertBefore(card.node, after ? after.node : null);
  }

  /** The cards drawn on `name`'s page. */
  guestsOf(name) {
    const guests = [];
    for (const [guest, host] of this.hostOf) {
      if (host === name && this.cards.has(guest)) guests.push(this.cards.get(guest));
    }
    return guests;
  }

  /** The models with a page (and a link, and an Overview entry) of their own. */
  pageNames() {
    return Array.from(this.cards.keys()).filter((n) => !this.hostOf.has(n));
  }

  // -- the sheet's two pages (K4) ------------------------------------------
  //
  // The Overview: every launched model as a compact entry, rows of three
  // (sheetAcross), its tier-1 body only - no wells, no disclosures. The
  // device page: one model alone, full width, readings focal, its tiers.
  // Which page is `this.opened`; the entries are shown and hidden by CSS on
  // the sheet's class, never moved or rebuilt, so a model's controls, focus
  // and remembered tiers survive every trip between the two.
  layoutSheet() {
    const names = this.pageNames();
    // The shown device was closed, or is gone (or is drawn on another
    // model's page now): back to the Overview.
    if (this.opened && (!this.cards.has(this.opened) || this.hostOf.has(this.opened))) this.opened = null;
    const isDevice = Boolean(this.opened);
    // toggle(…, force), never remove()/add() blindly: an unconditional
    // write rewrites the class attribute, which is a mutation every poll (F21).
    this.dom.cards.classList.toggle('is-device', isDevice);
    this.dom.cards.classList.toggle('is-overview', !isDevice && names.length > 0);
    names.forEach((name, index) => {
      const card = this.cards.get(name);
      card.setOpened(isDevice && name === this.opened);
      const span = isDevice ? null : 'span-' + (6 / sheetAcross(names.length, index));
      for (const other of ['span-2', 'span-3', 'span-6']) {
        card.node.classList.toggle(other, other === span);
      }
    });
    // A guest is opened with its host's page; the host's head says a
    // guest's stop only where the guest is not drawn (paintHead).
    for (const [guest, host] of this.hostOf) {
      const card = this.cards.get(guest);
      if (card) card.setOpened(isDevice && host === this.opened);
    }
    for (const name of names) this.cards.get(name).paintHead();
    this.pinOpened();
  }

  /** O15 (L5's parity): on a device page the entry's head and its tier-1
   *  body stay in view while the details under them scroll - as long as
   *  they leave most of the window to the details (a tall tier 1 on a short
   *  window is not pinned: it would cover what it pins over).
   *  A host's page is never pinned (W1): its tier 1 is the head of a longer
   *  page whose hosted groups are the point of it, and pinned it left a
   *  guest's group a strip to scroll under it. The whole page scrolls. */
  pinOpened() {
    const room = window.innerHeight - (this.dom.logPanel ? this.dom.logPanel.offsetHeight : 0);
    for (const card of this.cards.values()) {
      let pin = false;
      if (card.isOpened() && card.head && !card.hostName && !this.guestsOf(card.name).length) {
        const head = card.head.offsetHeight;
        const tall = head + card.body.offsetHeight;
        pin = tall > 0 && tall <= room * 0.6;
        if (pin) {
          // The body's own negative margin takes the row gap back (CSS), so
          // it pins right under the head.
          const next = Math.round(head) + 'px';
          if (card.node.style.getPropertyValue('--pin-head') !== next) {
            card.node.style.setProperty('--pin-head', next);
          }
        }
      }
      card.node.classList.toggle('is-pinned', pin);
    }
  }

  /** The rail's page list: "Overview" first, then the models by name only
   *  (no value is said twice); the shown page is the current one. Rebuilt
   *  only when the set of models changes. */
  renderNav(models) {
    // A hosted model has no link: its host's link stands for both.
    const names = Object.keys(models).filter((n) => !this.hostOf.has(n));
    const key = names.join('\n');
    if (key !== this.navKey) {
      this.navKey = key;
      clear(this.dom.nav);
      if (names.length) {
        const overview = make('button', 'model-link overview-link', 'Overview');
        overview.type = 'button';
        overview.dataset.page = 'overview';
        overview.addEventListener('click', () => this.showPage(null));
        this.dom.nav.appendChild(overview);
      }
      for (const name of names) {
        const link = make('button', 'model-link');
        // L1: the stop state per model, a square before the name
        // (setUnconfirmed); empty and hidden while the model is live.
        const mark = make('span', 'nav-mark');
        mark.hidden = true;
        link.appendChild(mark);
        // O6: energized - a probe in a mode, a heating heater, a recording
        // run - is a small ink ring after the stop mark; never red.
        // Its word is written only while it shows, so a live link's text is
        // its name alone.
        const ring = make('span', 'nav-energized');
        ring.hidden = true;
        link.appendChild(ring);
        link.appendChild(make('span', 'nav-name', sentence(name)));
        link.type = 'button';
        link.dataset.model = name;
        link.setAttribute('translate', 'no');
        link.addEventListener('click', () => this.showPage(name));
        this.dom.nav.appendChild(link);
      }
      if (this.latched) this.setUnconfirmed(Array.from(this.latched), Array.from(this.unconfirmed));
      this.setEnergized(this.energized);
    }
    for (const link of this.dom.nav.querySelectorAll('.model-link')) {
      const current = link.dataset.page === 'overview' ? !this.opened
        : link.dataset.model === this.opened;
      if (current) putAttr(link, 'aria-current', 'page');
      else if (link.hasAttribute('aria-current')) link.removeAttribute('aria-current');
    }
  }

  /** Show a page: a model's name for its device page, null for the
   *  Overview. The sheet goes to the top; a device page takes focus (its
   *  entry), the Overview leaves focus where the press was. */
  showPage(name) {
    // A hosted model's page is its host's, scrolled to its group.
    const guest = name && this.hostOf.has(name) && this.cards.has(this.hostOf.get(name))
      ? this.cards.get(name) : null;
    const target = guest ? this.hostOf.get(name) : name;
    const page = target && this.cards.has(target) ? target : null;
    this.opened = page;
    this.layoutSheet();
    this.renderNav(Object.fromEntries(Array.from(this.cards.keys()).map((n) => [n, {}])));
    window.scrollTo({ top: 0 });
    if (guest) {
      this.scrollToGroup(guest);
      guest.node.focus({ preventScroll: true });
    } else if (page) {
      this.cards.get(page).node.focus({ preventScroll: true });
    }
    // What the new page reveals is fetched now, not a data cycle later.
    const shown = page ? [this.cards.get(page)].concat(this.guestsOf(page))
      : Array.from(this.cards.values());
    for (const card of shown) {
      for (const widget of card.widgets) {
        if (widget.dataCommand && !widget.isOpen && card.wantsData(widget)) card.loadData(widget);
      }
    }
  }

  /** Bring a hosted model's group into view, clear of the rail and of the
   *  host's pinned head and tier 1 (O15), which would otherwise cover it. */
  scrollToGroup(card) {
    const host = this.cards.get(card.hostName);
    const pinned = host && host.node.classList.contains('is-pinned')
      ? host.head.offsetHeight + host.body.offsetHeight : 0;
    const rail = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--rail-top')) || 0;
    const top = card.node.getBoundingClientRect().top + window.scrollY - rail - pinned;
    window.scrollTo({ top: Math.max(0, top) });
  }

  /** A press on a model's name in the rail: its device page. */
  focusModel(name) {
    this.showPage(name);
  }

  /** The page remembers each model's disclosures for the session. */
  tierState(name) {
    return this.tierMemory.get(name) || {};
  }

  rememberTier(name, tier, isOpen) {
    const memory = Object.assign({}, this.tierState(name));
    memory[tier] = Boolean(isOpen);
    this.tierMemory.set(name, memory);
  }

  /** The rail's simulation line (`simLineText`). */
  renderSimLine(models) {
    const text = simLineText(models);
    putText(this.dom.simLine, text);
    if (this.dom.simLine.hidden !== !text) this.dom.simLine.hidden = !text;
  }

  /** L1 (round 7, IMP7-3): the rail's page list says which models are
   *  latched and which did not confirm, where the eye already is. A latched
   *  model gets an ink square, one that did not confirm a signal square
   *  with a "!" (O16, A11Y-6: shape as well as colour); the word is read to
   *  a screen reader and is the square's title. O4: a faulted model gets
   *  the same signal square - a failed disable is the same hazard. The
   *  entry's own mark follows the model's state (PanelCard). */
  setUnconfirmed(latched, unconfirmed) {
    this.latched = new Set(latched || []);
    this.unconfirmed = new Set(unconfirmed || []);
    for (const link of this.dom.nav.querySelectorAll('.model-link[data-model]')) {
      const name = link.dataset.model;
      const mark = link.querySelector('.nav-mark');
      if (!mark) continue;
      // A host's link stands for the models drawn on its page too: the
      // worst of them shows.
      const group = [name].concat(this.guestsOf(name).map((c) => c.name));
      const faultedOne = group.find((n) => this.faulted.has(n));
      const isUnconfirmed = group.some((n) => this.unconfirmed.has(n));
      const isFaulted = !isUnconfirmed && faultedOne !== undefined;
      const isLatched = !isUnconfirmed && !isFaulted && group.some((n) => this.latched.has(n));
      const words = isUnconfirmed ? 'did not confirm'
        : (isFaulted ? 'faulted' : (isLatched ? 'stopped' : ''));
      const title = isUnconfirmed ? 'Did not confirm the stop'
        : (isFaulted ? (this.faulted.get(faultedOne) ? 'Disable failed: treat as live'
                                               : 'Faulted: treat as live')
          : (isLatched ? 'Stopped' : ''));
      mark.classList.toggle('is-latched', isLatched);
      mark.classList.toggle('is-unconfirmed', isUnconfirmed);
      mark.classList.toggle('is-faulted', isFaulted);
      putText(mark, words);
      putAttr(mark, 'title', title);
      if (mark.hidden !== !words) mark.hidden = !words;
    }
  }

  /** O6: the ring before each energized model's name in the rail. */
  setEnergized(names) {
    const on = new Set(names || []);
    for (const ring of this.dom.nav.querySelectorAll('.model-link[data-model] .nav-energized')) {
      const name = ring.parentNode.dataset.model;
      const group = [name].concat(this.guestsOf(name).map((c) => c.name));
      const hide = this.isShutDown || !group.some((n) => on.has(n));
      putText(ring, hide ? '' : 'energized');
      putAttr(ring, 'title', hide ? '' : 'Energized');
      if (ring.hidden !== hide) ring.hidden = hide;
    }
  }

  /** N3: while anything is energized, the rail says what closing this tab
   *  does, with the watchdog's own seconds as the server serves them. */
  renderEnergizedLine() {
    let text = '';
    if (this.energized.length && !this.isShutDown) {
      const seconds = WATCHDOG && Number(WATCHDOG.stop_seconds);
      const after = seconds > 0
        ? ' ' + (Number.isInteger(seconds) ? String(seconds) : seconds.toFixed(1)) + ' s after'
        : ' soon after';
      text = 'Devices are energized. Disable them before closing this tab; the station '
        + 'stops them' + after + ' the tab goes.';
    }
    const line = this.dom.energizedLine;
    putText(line, text);
    if (line.hidden !== !text) line.hidden = !text;
  }

  /** N2 (brief-n-views N1): one line per probe inside its idle warning
   *  window - "<name> powers down in 42 s." and Extend - in station order,
   *  counted from the polled `idle_remaining` (no local timer that could
   *  drift). A line goes when the number climbs out of the window or the
   *  probe leaves its mode. Never a modal, never takes focus. */
  renderIdleLines(models) {
    const wanted = [];
    if (!this.isShutDown) {
      for (const name of Object.keys(models)) {
        const state = models[name] || {};
        const left = state.idle_remaining;
        if (left === null || left === undefined || !isFinite(Number(left))) continue;
        const warnFor = Number(state.idle_warn_seconds) || IDLE_WARN_DEFAULT_S;
        if (Number(left) > warnFor) continue;
        wanted.push([name, Math.max(0, Math.ceil(Number(left)))]);
      }
    }
    const names = wanted.map((w) => w[0]);
    for (const name of Array.from(this.idleLines.keys())) {
      if (names.indexOf(name) !== -1) continue;
      this.idleLines.get(name).node.remove();
      this.idleLines.delete(name);
    }
    for (const [name, seconds] of wanted) {
      let line = this.idleLines.get(name);
      // A no-break space: the number and its unit never part at a wrap.
      const text = sentence(name) + ' powers down in ' + seconds + '\u00a0s.';
      if (!line) {
        const node = make('div', 'idle-line');
        const words = make('span', 'idle-text');
        const extend = make('button', 'ghost idle-extend', 'Extend');
        extend.type = 'button';
        extend.setAttribute('aria-label', 'Extend ' + sentence(name));
        extend.title = 'Restart the idle timeout of ' + sentence(name)
          + ': it stays energized for another full period';
        extend.addEventListener('click', () => this.extendIdle(name, extend));
        node.appendChild(words);
        node.appendChild(extend);
        line = { node, words };
        this.idleLines.set(name, line);
        this.announce('polite', text + ' Extend is on the rail.');
      }
      putText(line.words, text);
    }
    // Station order: re-appended only when the set or its order changed.
    const key = names.join('\n');
    if (key !== this.idleKey) {
      this.idleKey = key;
      for (const name of names) this.dom.idleLines.appendChild(this.idleLines.get(name).node);
    }
    const hide = names.length === 0;
    if (this.dom.idleLines.hidden !== hide) this.dom.idleLines.hidden = hide;
  }

  /** Extend: `extend_idle` on that model. Stop-class in core (it carries no
   *  entries); a refusal ("Nothing to extend") is a tray line. */
  async extendIdle(name, button) {
    if (button.disabled) return;
    button.disabled = true;
    try {
      const answer = await apiPost('/api/run', { name, command: 'extend_idle', inputs: {}, args: [] });
      if (answer && answer.status !== 'ok') {
        this.notice(sentence(name) + ' was not extended: ' + (answer.reason || 'no reason given'));
      }
    } catch (err) {
      this.notice(sentence(name) + ' was not extended: the station did not answer ('
        + failureReason(err) + ').');
    } finally {
      button.disabled = false;
    }
    await this.refreshNow();
  }

  /** O7: say `text` in the polite or the assertive live region. Emptied
   *  first, so the same sentence twice is still said twice. */
  announce(kind, text) {
    const node = kind === 'assertive' ? this.dom.announceAssertive : this.dom.announcePolite;
    if (!node || !text) return;
    node.textContent = '';
    setTimeout(() => { node.textContent = text; }, 60);
  }

  /** O7 (A11Y-2): the stop, spoken. Assertive on the edges - a latch, a
   *  change in what is latched, the clear; polite for the headline and the
   *  rail line whenever their words change. Nothing is said for the state
   *  the page opens on. */
  announceStop(words, stop) {
    const latched = (stop.latched || []).slice().sort().join('\n');
    const said = [words.headline, words.rail].filter(Boolean).join(' ');
    const last = this.saidStop;
    this.saidStop = { latched, said };
    if (!last) return;
    if (latched !== last.latched) {
      if (!latched) this.announce('assertive', 'The stop is cleared.');
      else this.announce('assertive', words.headline || words.rail || 'Stopped.');
    }
    if (said && said !== last.said) this.announce('polite', said);
  }

  async loadSetup() {
    try {
      const setup = await apiGet('/api/setup');
      if (!setup || !setup.schema || !setup.schema.sections) return;
      this.setupCard = new PanelCard(this, SETUP_NAME, setup.schema,
                                     { title: 'Setup' });
      this.setupCard.node.classList.add('setup-card');
      this.dom.drawerBody.appendChild(this.setupCard.node);
      this.setupCard.refresh(setup.state);
      this.setDrawerOpen(true);        // Setup is where a run begins
    } catch (err) { /* setup is optional once models are built */ }
  }

  /** A model the operator closed is not gone, it is put away. The way back
   *  is on the rail, beside Setup - the other thing that reopens. */
  renderClosed(closed) {
    // Rebuilt only when the list changes: rebuilt every poll, a Reopen
    // button lost keyboard focus four times a second (WDG-3, F21).
    const key = closed.join('\n');
    if (key === this.closedKey) return;
    this.closedKey = key;
    clear(this.dom.closed);
    if (!closed.length) return;
    this.dom.closed.appendChild(make('span', 'closed-label', 'Reopen'));
    for (const name of closed) {
      const button = make('button', 'ghost', sentence(name));
      button.type = 'button';
      button.title = 'Reopen ' + name + ': it is built and connected again.';
      button.addEventListener('click', () => this.openModel(name));
      this.dom.closed.appendChild(button);
    }
  }

  async openModel(name) {
    let answer;
    try {
      answer = await apiPost('/api/open_model', { name });
    } catch (err) {
      answer = { status: 'error', reason: 'the station did not answer (' + failureReason(err) + ')' };
    }
    // Not a popup: only an error event may open one. The tray says it.
    if (answer.status !== 'ok') {
      this.notice(sentence(name) + ' did not reopen: ' + (answer.reason || 'no reason given')
        + '. Check its port in Setup.');
    }
    await this.refreshNow();
  }

  async closeModel(name) {
    const isSure = await this.confirm('Close ' + name + '?\n\nIt stops and disconnects. '
                                      + 'You can reopen it from the rail.', 'Close ' + name);
    if (!isSure) return;
    try {
      await apiPost('/api/close_model', { name });
    } catch (err) {
      this.notice(sentence(name) + ' did not close: the station did not answer ('
        + failureReason(err) + ').');
    }
    await this.refreshNow();
  }

  /** A line the page itself has to say, on the tray. */
  notice(text) {
    this.showEvent({ severity: 'warning', text });
  }

  // -- confirmations -------------------------------------------------------
  //
  // The page's own, not window.confirm: a native dialog blocks every script
  // on the page, the stop's included, and its default button is OK. This
  // one sits below the rail, focuses Cancel, and Escape answers No (F17).
  confirm(text, yesLabel) {
    if (this.confirmPending) this.answerConfirm(false);
    return new Promise((resolve) => {
      const returnTo = document.activeElement;
      putText(this.dom.confirmText, text);
      putText(this.dom.confirmYes, yesLabel || 'Continue');
      this.dom.confirm.hidden = false;
      this.updateInert();
      this.dom.confirmNo.focus({ preventScroll: true });
      this.confirmPending = (answer) => {
        this.confirmPending = null;
        this.dom.confirm.hidden = true;
        this.updateInert();
        this.restoreFocus(returnTo, this.dom.cards);
        resolve(Boolean(answer));
      };
    });
  }

  answerConfirm(answer) {
    if (this.confirmPending) this.confirmPending(answer);
  }

  // -- the global FULL STOP ----------------------------------------------
  //
  // The mushroom follows the state, never the click. Its face and what a
  // press does are the server's `stop_words` (L1, round 7): it reads "Clear"
  // - and a press clears, asking first - only while EVERY model is latched.
  // One model's own switch leaves it a working "Stop" for the rest.
  // Signature: latched, the key drops, the band floods and the collar turns
  // red in one 120 ms transition (styles.css); there is no pulse.
  renderEstop(words) {
    const isClear = words.action === 'clear';
    this.stopAction = isClear ? 'clear' : 'stop';
    this.isEstopped = isClear;
    putText(this.dom.stopFace, words.face || (isClear ? 'Clear' : 'Stop'));
    this.dom.stop.classList.toggle('is-latched', isClear);
    // The collar turns red and the socket band floods with the "Clear"
    // face (Signature, the Stopped artboard).
    this.dom.stopRing.classList.toggle('is-latched', isClear);
    // The rail's line under the disc: a partial stop's names, "every model
    // latched", or the models that did not confirm.
    putText(this.dom.railLatched, words.rail || '');
    if (this.dom.railLatched.hidden !== !words.rail) this.dom.railLatched.hidden = !words.rail;
    // The sheet's headline and its subline. With no subline of its own, a
    // full stop says how to continue.
    putText(this.dom.headlineText, words.headline || '');
    putText(this.dom.headlineNote, words.subline
      || (isClear ? 'Clear the stop on the rail to continue.' : ''));
    if (this.dom.headline.hidden !== !words.headline) this.dom.headline.hidden = !words.headline;
    putAttr(this.dom.stop, 'aria-label',
            isClear ? 'Clear the stop on every model' : 'Stop every model');
    // The chord stops and never clears (F9): while a press clears, the
    // button does not claim the chord as its shortcut. The rail's hint
    // stays in every state, because the chord always stops (L1).
    if (isClear) this.dom.stop.removeAttribute('aria-keyshortcuts');
    else putAttr(this.dom.stop, 'aria-keyshortcuts', 'Control+Period');
    const hint = document.querySelector('.stop-hint');
    if (hint && hint.hidden) hint.hidden = false;
    // The keyboard path is written on the object itself (F9).
    putAttr(this.dom.stop, 'title', isClear
      ? 'Clear the stop on every model (asks first). ' + STOP_KEY_HINT + ' stops again.'
      : 'Stop every model. Keyboard: ' + STOP_KEY_HINT + ', from anywhere on the page.');
  }

  async toggleEstopAll() {
    if (this.stopAction !== 'clear') {
      await this.stopAll();
      return;
    }
    try {
      let result = await apiPostChecked('/api/clear_estop_all', {});
      if (result.status === 'needs_confirm'
          && await this.confirm(result.reason, 'Clear the stop')) {
        result = await apiPostChecked('/api/clear_estop_all', { confirmed: true });
      }
    } catch (err) {
      this.stopFailed('Clear', err);
      return;
    }
    await this.refreshNow();
  }

  /** Stop every model. Never silent: a stop that did not reach the station
   *  says so on the rail, and so does one that reached it but was not
   *  confirmed by every model (F2, WDG-2, CRIT-1, HC-3). */
  async stopAll() {
    // A pending question is moot once the operator has pressed stop.
    this.answerConfirm(false);
    let answer;
    try {
      answer = await apiPostChecked('/api/estop_all', {});
    } catch (err) {
      this.stopFailed('Stop', err);
      return;
    }
    // The stop landed: a line saying an earlier one did not is history.
    this.setRailLine('stop', '');
    // Which models did not confirm is the state of THIS latch (G6), and
    // since L1 it is read from the state (`stop.unconfirmed`, each model's
    // `stop_confirmed`) on the poll this triggers, not from this answer. It
    // has no Dismiss: it describes hardware this page cannot see, and it
    // stands for as long as the latch it describes (I8, WDG6-1).
    await this.refreshNow();
  }

  /** L2 (round 7, IMP7-5): "Stop not confirmed" describes a latch. Once
   *  the poll says no model is latched - cleared here or from any other
   *  client - the tray drops that line: a hazard that is over is not the
   *  one red line on a normal page. A state asked for before the line was
   *  shown is not evidence either way. (The log keeps it: it is history.) */
  forgetStopLine(stop, askedAt) {
    if ((stop.latched || []).length || !isUnconfirmedEvent(this.trayEvent)) return;
    if (askedAt !== undefined && askedAt < (this.trayAt || 0)) return;
    this.setTray(null);
  }

  stopFailed(action, err) {
    this.setRailLine('stop', action + ' did not reach the station — ' + failureReason(err)
      + (action === 'Stop' ? '. Press it again, or stop the hardware at the bench.' : '.'),
      true);
    this.setConnected(false);
  }

  // -- events -------------------------------------------------------------
  async pollEvents(latest) {
    if (latest !== undefined && latest === this.lastEventId) return;
    const answer = await apiGet('/api/events?since=' + this.lastEventId);
    for (const event of (answer.events || [])) {
      // Two polls in flight (a command's refreshNow beside the timer's) can
      // fetch the same new events: each is shown once.
      if (typeof event.id === 'number' && event.id <= this.lastEventId) continue;
      if (typeof event.id === 'number') this.lastEventId = event.id;
      this.showEvent(event);
      if (event.needs_ack) this.showAck(event);
    }
    if (typeof answer.latest_id === 'number') this.lastEventId = answer.latest_id;
  }

  showEvent(event, options) {
    // Each line keeps its severity as a word for a screen reader; the eye
    // gets the mark (a triangle for a warning, a solid signal square for an
    // error - shape as well as colour). The words are the event's title and
    // message (L11); the source it came from is one hover away.
    const text = eventText(event);
    const line = make('div', 'event severity-' + event.severity);
    if (event.severity === 'warning' || event.severity === 'error') {
      line.appendChild(make('span', 'sr-only',
        event.severity === 'error' ? 'Error: ' : 'Warning: '));
    }
    line.appendChild(document.createTextNode(text));
    if (event.source) line.title = String(event.source);
    this.dom.log.appendChild(line);
    while (this.dom.log.childNodes.length > 200) {
      this.dom.log.removeChild(this.dom.log.firstChild);
    }
    this.dom.log.scrollTop = this.dom.log.scrollHeight;
    // The collapsed tray is one line, and it carries warnings and errors
    // only (status by exception): an info event is history, in the log. A
    // line replayed from before this tab opened is history too (L21), bar
    // the one that still stands (start()).
    if (event.severity !== 'warning' && event.severity !== 'error') return;
    if (options && options.history) return;
    // O13 (PM8-4): the idle warning's number froze on the tray; the rail's
    // countdown line is the live one, so the event is history only.
    if (event.title === IDLE_SOON_TITLE) return;
    // O12: a "browser silent" that a landed heartbeat has already answered
    // is over before it is shown.
    if (event.title === SILENT_TITLE && this.lastBeatOk / 1000 > Number(event.last_seen || 0)) return;
    this.setTray(event);
  }

  /** The tray's one line, or nothing (`null`). The words sit in their own
   *  span that ellipsizes, with the whole line as its title, so a narrow
   *  window cuts the end of a sentence, never silently the model's name. */
  setTray(event) {
    const text = event ? eventText(event) : '';
    putText(this.dom.trayText, text);
    putAttr(this.dom.trayText, 'title', text);
    const severity = event && (event.severity === 'warning' || event.severity === 'error')
      ? ' severity-' + event.severity : '';
    if (this.dom.trayLatest.className !== 'tray-latest' + severity) {
      this.dom.trayLatest.className = 'tray-latest' + severity;
    }
    this.trayEvent = event || null;
    this.trayAt = Date.now();
  }

  /** Only `needs_ack` opens a modal. Everything else is a line in the log.
   *  The dialog shows ONE title at a time (rb-ack A3, parity with the
   *  latch-release question): a second title waits behind it, never over
   *  it or under it (F1, HC-2), and a repeat of a queued title is counted
   *  in its entry. The modal starts below the rail, so the stop stays in
   *  reach, and it takes nothing inert that the rail needs. */
  showAck(event) {
    const wasHidden = this.dom.modal.hidden;
    if (wasHidden) this.ackReturn = document.activeElement;
    ackEnqueue(this.ackQueue, event);
    this.renderAck();
    if (wasHidden) {
      this.dom.modal.hidden = false;
      this.updateInert();
      this.dom.modalOk.focus({ preventScroll: true });
    }
  }

  /** The dialog's heading: the event's title, as the confirm dialog's
   *  words are its text. Built here because the markup is not this file's;
   *  the dialog is labelled by the heading and described by the body. */
  buildAckHeading() {
    if (this.dom.modalTitle) return;
    const heading = make('h2', 'dialog-title');
    heading.id = 'ack-title';
    this.dom.modalDialog.insertBefore(heading, this.dom.modalDialog.firstChild);
    this.dom.modalTitle = heading;
    putAttr(this.dom.modalDialog, 'aria-labelledby', 'ack-title');
    putAttr(this.dom.modalDialog, 'aria-describedby', 'ack-text');
    putText(this.dom.modalOk, 'Understood');
  }

  renderAck() {
    const words = ackWords(this.ackQueue);
    if (!words) return;
    putText(this.dom.modalTitle, words.title);
    clear(this.dom.modalText);
    this.dom.modalText.appendChild(make('p', 'ack-line', words.body));
    putText(this.dom.modalCount, words.waiting);
    this.dom.modalCount.hidden = !words.waiting;
    putText(this.dom.modalOk, words.key);
    this.renderAckLater(words.later);
  }

  /** R1: "Later" exists only while the notice shown carries an action (a
   *  plain notice keeps its one key). Built here because the markup is
   *  not this file's; it sits beside the action key and answers like
   *  Escape. */
  renderAckLater(text) {
    if (!text) {
      if (this.dom.modalLater) { this.dom.modalLater.remove(); this.dom.modalLater = null; }
      return;
    }
    if (!this.dom.modalLater) {
      // The two keys share one row, the action first (Tk's order).
      let row = this.dom.modalOk.parentNode;
      if (!row.classList.contains('dialog-actions')) {
        row = make('div', 'dialog-actions');
        row.id = 'ack-actions';
        this.dom.modalOk.parentNode.insertBefore(row, this.dom.modalOk);
        row.appendChild(this.dom.modalOk);
      }
      const later = make('button', 'button role-neutral', text);
      later.type = 'button';
      later.id = 'ack-later';
      later.addEventListener('click', () => this.acknowledge(false));
      row.appendChild(later);
      this.dom.modalLater = later;
    }
    putText(this.dom.modalLater, text);
  }

  /** Understood: the title shown is read, every repeat of it; the next
   *  title takes the dialog, or it closes and focus goes back. */
  acknowledge(acted) {
    const entry = this.ackQueue.shift();
    const action = acted ? ackAction(entry) : null;
    this.logAcknowledged(entry, action ? 'action' : (ackAction(entry) ? 'later' : 'understood'));
    if (this.ackQueue.length) {
      this.renderAck();
      this.dom.modalOk.focus({ preventScroll: true });
    } else {
      clear(this.dom.modalText);
      this.renderAckLater('');
      this.dom.modal.hidden = true;
      this.updateInert();
      this.restoreFocus(this.ackReturn, this.dom.cards);
    }
    if (action) this.runAction(action);
  }

  /** R7: the log file says what the operator answered, as the desktop
   *  views' "Alert Acknowledged" line does. Best effort: a station that
   *  does not answer has no log to write. */
  logAcknowledged(entry, answer) {
    const events = (entry && entry.events) || [];
    const latest = events[events.length - 1];
    if (!latest || typeof latest.id !== 'number') return;
    apiPost('/api/ack', { id: latest.id, answer }).catch(() => {});
  }

  /** R1: an acknowledgement's action, as a press of that button on its
   *  panel: the card's own run, so a refusal shows on the card and a
   *  confirmation is asked as usual. */
  async runAction(action) {
    const name = String(action.name || '');
    const card = name === SETUP_NAME ? this.setupCard : this.cards.get(name);
    const args = Array.isArray(action.args) ? action.args : [];
    if (card) {
      const drawn = card.widgets.find((w) => w.element && w.element.type === 'button'
        && w.element.command === action.command && !(w.element.args || []).length);
      const element = drawn ? drawn.element
        : { type: 'button', command: action.command, args: [], inputs: [] };
      return await card.run(element, args);
    }
    let result;
    try {
      result = await apiPost('/api/run', { name, command: action.command, inputs: {}, args });
      if (result.status === 'needs_confirm'
          && await this.confirm(result.reason, confirmLabel(result))) {
        result = await apiPost('/api/run', { name, command: result.command,
          inputs: result.inputs || {}, args: (result.args || []).concat([true]) });
      }
    } catch (err) {
      this.notice(String(action.label) + ' did not reach the station (' + failureReason(err) + ').');
      return null;
    }
    if (result.status === 'refused') this.notice(String(action.label) + ': ' + (result.reason || ''));
    if (isRestartAnswer(name, action.command, result)) this.awaitRestart();
    return result;
  }

  // -- a restart (rb-restart R4) -------------------------------------------
  //
  // The server answers Restart, closes every model and replaces itself; a
  // new one listens on the same port within seconds. The page says so (not
  // "not answering"), stops beating (there is no one to beat to, and the new
  // station's watchdog stays idle until a page checks in), and reloads once
  // a station with another `boot` answers - or says it did not come back.
  awaitRestart() {
    if (this.isRestarting || this.isShutDown) return;
    this.isRestarting = true;
    this.stopHeartbeat();
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    const link = this.dom.connection;
    link.textContent = RESTARTING_TEXT;
    link.title = 'The station is restarting. This page reloads itself when it is back.';
    link.className = 'link-state is-down is-restarting';
    document.body.classList.add('is-offline');
    this.muteReadouts('Restarting');
    const started = Date.now();
    const old = this.boot;
    const tick = async () => {
      try {
        const state = await apiGet('/api/state');
        if (state && state.boot && state.boot !== old) { this.reloadPage(); return; }
      } catch (err) {
        // Not back yet: the old server is gone, the new one not listening.
      }
      if (Date.now() - started >= RESTART_WAIT_MS) {
        link.textContent = RESTART_GAVE_UP;
        link.title = 'No station answered for ' + Math.round(RESTART_WAIT_MS / 1000)
          + ' s after the restart.';
        link.className = 'link-state is-down is-restart-failed';
        this.restartTimer = null;
        return;
      }
      this.restartTimer = setTimeout(tick, RESTART_POLL_MS);
    };
    this.restartTimer = setTimeout(tick, RESTART_POLL_MS);
  }

  reloadPage() {
    window.location.reload();
  }

  // -- the region picker --------------------------------------------------
  //
  // O10 (WDG8-5): the drag is one way; the four fields are the other - the
  // keyboard's - in the station's own screen pixels. While the grab loads
  // the dialog says so and is aria-busy.
  async openRegionPicker(card, element) {
    const canvas = this.dom.pickerCanvas;
    this.pickerReturn = document.activeElement;
    this.pickerFor = { card, element };
    this.setPickerLoading(true);
    for (const field of this.dom.pickerFields) field.value = '';
    this.dom.picker.hidden = false;
    this.updateInert();
    this.dom.pickerClose.focus({ preventScroll: true });
    const context = canvas.getContext('2d');
    context.clearRect(0, 0, canvas.width, canvas.height);
    let frame;
    try {
      frame = await apiGet('/api/screen?name=' + encodeURIComponent(card.name));
    } catch (err) {
      this.closeRegionPicker();
      card.showRefused('No screen image came back (' + failureReason(err)
        + '). Check that the station is running, then pick again.', element);
      return;
    }
    if (!frame || !frame.image) {
      this.closeRegionPicker();
      card.showRefused((frame && frame.reason) || 'The station offers no screen image.', element);
      return;
    }
    const picture = new Image();
    picture.onload = () => {
      canvas.width = picture.width;
      canvas.height = picture.height;
      context.drawImage(picture, 0, 0);
      this.bindRegionDrag(card, element, frame, picture);
      this.setPickerLoading(false);
    };
    picture.onerror = () => this.setPickerLoading(false);
    picture.src = frame.image;
  }

  setPickerLoading(isLoading) {
    putText(this.dom.pickerHelp, isLoading ? 'Loading the station\'s screen…'
      : 'Drag a rectangle over the part of the screen to watch, or type it below in screen pixels.');
    if (isLoading) putAttr(this.dom.pickerDialog, 'aria-busy', 'true');
    else this.dom.pickerDialog.removeAttribute('aria-busy');
  }

  /** The typed region: four whole numbers, width and height above zero. */
  useTypedRegion() {
    const target = this.pickerFor;
    if (!target || this.dom.picker.hidden) return;
    const numbers = this.dom.pickerFields.map((f) => f.value.trim());
    const whole = numbers.every((v) => /^-?\d+$/.test(v));
    const box = numbers.map(Number);
    if (!whole || box[2] < 1 || box[3] < 1) {
      putText(this.dom.pickerHelp, 'Type four whole numbers; width and height must be at least 1.');
      const first = this.dom.pickerFields.find((f, i) => !/^-?\d+$/.test(f.value.trim())
        || (i >= 2 && Number(f.value) < 1));
      if (first) first.focus({ preventScroll: true });
      return;
    }
    this.closeRegionPicker();
    target.card.run(target.element, box);
  }

  /** Drag on the image, scaled back to screen coordinates: the picture may
   *  be a bounded (downscaled) grab of a monitor that does not start at 0,0. */
  bindRegionDrag(card, element, frame, picture) {
    const canvas = this.dom.pickerCanvas;
    const context = canvas.getContext('2d');
    const scaleX = (frame.width || picture.width) / picture.width;
    const scaleY = (frame.height || picture.height) / picture.height;
    const left = frame.left || 0;
    const top = frame.top || 0;
    let start = null;

    const at = (event) => {
      const box = canvas.getBoundingClientRect();
      return [
        Math.max(0, Math.min(canvas.width, (event.clientX - box.left) * (canvas.width / box.width))),
        Math.max(0, Math.min(canvas.height, (event.clientY - box.top) * (canvas.height / box.height))),
      ];
    };
    const paint = (box) => {
      context.drawImage(picture, 0, 0);
      // Trace, not signal: the box marks what will be measured, and red is
      // the stop's alone (F24, UXPM-12).
      context.strokeStyle = getComputedStyle(document.documentElement)
        .getPropertyValue('--trace');
      context.lineWidth = 2;
      context.strokeRect(box[0], box[1], box[2], box[3]);
    };
    const boxFrom = (a, b) => [
      Math.min(a[0], b[0]), Math.min(a[1], b[1]),
      Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]),
    ];

    // Pointer events, so a pen or a touchscreen at the bench can drag too.
    canvas.onpointerdown = (event) => {
      start = at(event);
      if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);
    };
    canvas.onpointermove = (event) => { if (start) paint(boxFrom(start, at(event))); };
    // An interrupted gesture leaves no stale origin behind (HC-20).
    canvas.onpointercancel = canvas.onlostpointercapture = () => {
      if (!start) return;
      start = null;
      context.drawImage(picture, 0, 0);
    };
    canvas.onpointerup = (event) => {
      if (!start) return;
      const box = boxFrom(start, at(event));
      start = null;
      if (box[2] < 4 || box[3] < 4) return;
      const region = [
        Math.round(box[0] * scaleX) + left,
        Math.round(box[1] * scaleY) + top,
        Math.round(box[2] * scaleX),
        Math.round(box[3] * scaleY),
      ];
      this.dom.pickerFields.forEach((field, i) => { field.value = String(region[i]); });
      this.closeRegionPicker();
      card.run(element, region);
    };
  }

  closeRegionPicker() {
    if (this.dom.picker.hidden) return;
    this.dom.picker.hidden = true;
    this.updateInert();
    this.restoreFocus(this.pickerReturn, this.dom.cards);
  }
}

if (typeof window !== 'undefined' && window.document && !window.__STATION_NO_BOOT__) {
  window.addEventListener('DOMContentLoaded', () => {
    window.station = new Dashboard(document.body);
    window.station.start();
  });
}
