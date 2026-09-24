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
//: How many of a model's key numbers the status rail carries. More than this
//: and the rail stops being readable at a glance, which is its whole job.
const RAIL_READOUTS = 4;
//: The keyboard path to the stop (F9). Alt+. - Option+. on a Mac - is bound
//: by no browser on any platform this station runs on, works with focus in
//: a text box, and is matched on the physical key so the character a Mac
//: types for Option+. ("≥") does not matter. It only ever STOPS; clearing
//: the latch stays a deliberate, confirmed act.
const STOP_KEY_CODE = 'Period';
const STOP_KEY_HINT = 'Alt+. (Option+. on a Mac)';
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
// schema.is_enabled, mirrored. One rule, three views.
// ==========================================================================
function isEnabled(element, mode) {
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
  node.appendChild(make('label', 'label',
                        sentenceCase(element.text || element.model_attr || '')));
  return node;
}

/** Tie a row's caption to its control, so the caption is clickable and a
 *  screen reader names the control by it. Ids are per panel and per
 *  attribute, which is unique inside one page. */
let controlSerial = 0;
function labelControl(node, control, element) {
  controlSerial += 1;
  control.id = 'control-' + controlSerial;
  const caption = node.querySelector('.label');
  if (caption) caption.htmlFor = control.id;
  control.setAttribute('aria-label',
    sentenceCase(element.text || element.model_attr || element.command || ''));
}

/** What an empty data element says. The view owns this copy: an empty
 *  state says what to do next, not that there is nothing. A model that
 *  declares its own (`empty` on the element) wins. */
const EMPTY_STATES = {
  series: 'No samples yet. Start a run and red % is plotted here as it records.',
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

/** The one bold object on the page, in the two sizes it comes in: the
 *  dashboard's stop on the rail and the per-model stop in a Safety section.
 *  One control, one shape, wherever it appears. */
function mushroom(label) {
  const button = make('button', 'mushroom');
  button.type = 'button';
  const face = make('span', 'mushroom-face', label);
  button.appendChild(face);
  return { button, face };
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

//: Element types that DO something rather than say something.
const COMMAND_TYPES = ['button', 'file_save', 'file_open'];

/** A row of commands is not a table row: Setup's Launch row carries the
 *  whole selection summary, and in a shared grid that one long sentence
 *  widens the first column of every model row. A row that holds a command
 *  spans the table instead of lining up with it. */
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

/** A card with a lot to say takes two columns of the page and lays its own
 *  sections out in two columns, rather than becoming one very tall stripe
 *  beside a mostly empty one (the bench look, 2026-09-22). A row-layout card
 *  is a table and is never split into columns. */
function isWideCard(sections) {
  const list = sections || [];
  if (list.some(isRowSection)) return false;
  const drawn = list.reduce((total, s) => total + (s.elements || []).length, 0);
  return list.length >= 6 || drawn >= 24;
}

/** The key numbers the status rail carries for a model, derived rather than
 *  named: the readonly elements of its FIRST schema section, which is where
 *  every model in this station puts what the operator watches (the probe's
 *  coordinate frame, the heater's temperature, the rotator's stage). A first
 *  section that happens to hold no readout falls through to the next one
 *  that does, so a model is never silently absent from the rail. */
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

/** "X Position:" is a form label; on the rail it is a caption beside a
 *  number, so it drops the colon that pointed at the box. */
function railLabel(element) {
  return sentenceCase(element.text || element.model_attr || '');
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
// readValue?, isDirty? }. This is the seam a toolkit subclass fills in
// base.py - here the toolkit is the DOM.
// ==========================================================================
function renderReadonly(panel, element) {
  const node = row(element);
  const value = make('span', 'value is-empty ' + roleClass(element.role), '--');
  value.setAttribute('translate', 'no');
  node.appendChild(value);
  return {
    node,
    setText: (text) => {
      const shown = readoutText(text);
      if (value.textContent === shown) return;
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
    },
    setEnabled: (flag) => { node.classList.toggle('disabled', !flag); },
  };
}

function renderEntry(panel, element) {
  const node = row(element);
  const group = make('div', 'group');
  const input = make('input', 'input');
  labelControl(node, input, element);
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
  group.appendChild(input);
  if (element.unit) group.appendChild(make('span', 'unit', element.unit));
  node.appendChild(group);
  let served = '';
  // A text box narrower than what it holds shows the whole of it on hover.
  if (!numeric) input.addEventListener('input', () => { input.title = input.value; });
  return {
    node,
    // Never overwrite what the operator is typing: focused, or edited away
    // from the last value the server sent.
    isDirty: () => document.activeElement === input || input.value !== served,
    readValue: () => input.value,
    setText: (text) => {
      // An int box never shows "5.000", whatever the state formatted.
      const next = isInt ? intText(text)
        : ((text === null || text === undefined) ? '' : String(text));
      if (next === served && input.value === served) return;
      served = next;
      input.value = served;
      if (!numeric) input.title = served;
    },
    setEnabled: (flag) => {
      if (input.disabled === !flag) return;
      input.disabled = !flag;
      node.classList.toggle('disabled', !flag);
    },
  };
}

/** `disabled`, written only when it changes (F21). */
function enabler(control) {
  return (flag) => { if (control.disabled === !flag) return; control.disabled = !flag; };
}

function renderButton(panel, element) {
  const node = make('div', 'row command');
  const button = make('button', 'button ' + roleClass(element.role),
                      sentenceCase(element.text || element.command));
  button.type = 'button';
  button.addEventListener('click', () => panel.run(element));
  node.appendChild(button);
  return {
    node,
    setEnabled: enabler(button),
  };
}

function renderToggle(panel, element) {
  // The Safety section's stop is not a button that happens to be red: it is
  // the same physical object as the dashboard's, one size down, so the
  // operator never has to work out which control stops this model.
  if (element.model_attr === 'is_estopped') return renderStopToggle(panel, element);
  // A toggle looks like a toggle: a lamp that is lit or not, the state's
  // words beside it, and aria-pressed for anything that cannot see the lamp.
  const node = row(element);
  const button = make('button', 'button toggle off');
  button.type = 'button';
  const lamp = make('span', 'toggle-lamp');
  lamp.setAttribute('aria-hidden', 'true');
  const face = make('span', 'toggle-face');
  button.appendChild(lamp);
  button.appendChild(face);
  labelControl(node, button, element);
  button.addEventListener('click', () => panel.runToggle(element));
  node.appendChild(button);
  let last = null;
  const show = (on) => {
    if (last === Boolean(on)) return;
    last = Boolean(on);
    const shown = toggleFace(on ? (element.true_text || 'On')
                                : (element.false_text || 'Off'), element.text);
    face.textContent = shown.text;
    // The aside, if the schema wrote one; otherwise the words themselves,
    // so a face cut short by the fixed width is still readable in full.
    button.title = shown.hint || shown.text;
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
    button.className = 'button toggle ' + roleClass(on ? element.on_role : element.off_role)
      + (on ? ' on' : ' off');
  };
  show(false);
  return {
    node,
    setOn: show,
    setEnabled: enabler(button),
  };
}

/** A model's own FULL STOP, drawn as the mushroom. The copy is the rail's
 *  copy - "Stop" then "Clear" - because an action keeps its name through
 *  the whole flow; the schema's own wording is the button's title. */
function renderStopToggle(panel, element) {
  const node = row(element);
  const { button, face } = mushroom('Stop');
  button.classList.add('mini');
  button.addEventListener('click', () => panel.runToggle(element));
  node.appendChild(button);
  let last = null;
  const model = sentence(panel.title || panel.name);
  const show = (on) => {
    if (last === Boolean(on)) return;
    last = Boolean(on);
    face.textContent = on ? 'Clear' : 'Stop';
    button.classList.toggle('is-latched', last);
    button.title = sentence(on ? (element.true_text || '')
                               : (element.false_text || ''));
    // Named for its model, so a list of buttons does not read "Stop, Stop"
    // (WDG-4).
    button.setAttribute('aria-label', on ? 'Clear the stop on ' + model : 'Stop ' + model);
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
  };
  show(false);
  return {
    node,
    setOn: show,
    setEnabled: enabler(button),
  };
}

function renderDropdown(panel, element) {
  const node = row(element);
  const select = make('select', 'select');
  labelControl(node, select, element);
  select.name = element.model_attr || '';
  const placeholder = make('option', null, 'Select…');
  placeholder.value = '';
  placeholder.disabled = true;
  select.appendChild(placeholder);
  select.addEventListener('change', () => {
    select.title = select.value;
    if (select.value !== '') panel.run(element, [select.value]);
  });
  // Options are re-read on focus, not once at first render: a probe added
  // after the page loaded has to appear in the list (WEB-22).
  select.addEventListener('focus', () => panel.loadOptions(element, select));
  node.appendChild(select);
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
      if (select.title !== select.value) select.title = select.value;
    },
    setEnabled: enabler(select),
  };
}

function renderRegionSelect(panel, element) {
  const node = row(element);
  const value = make('span', 'value is-empty', 'Not set');
  const button = make('button', 'button ' + roleClass(element.role), 'Pick region');
  button.type = 'button';
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
  };
}

function renderFileSave(panel, element) {
  const node = make('div', 'row command');
  const button = make('button', 'button ' + roleClass(element.role),
                      sentenceCase(element.text || 'Save'));
  button.type = 'button';
  button.addEventListener('click', () => panel.download(element));
  node.appendChild(button);
  return {
    node,
    setEnabled: enabler(button),
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
  path.setAttribute('aria-label', label + ': path on the station');
  path.addEventListener('input', () => { path.title = path.value; });
  const extensions = (element.extensions || []).map((e) => '.' + String(e).replace(/^\./, ''));
  const picker = make('input', 'file-picker');
  picker.type = 'file';
  if (extensions.length) picker.accept = extensions.join(',');
  picker.tabIndex = -1;
  picker.setAttribute('aria-hidden', 'true');
  const choose = make('button', 'button', 'Choose file…');
  choose.type = 'button';
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
  let drawn = null;
  return {
    node,
    dataCommand: element.data_command,
    setData: (data) => {
      // Redrawn only when the series changed (F21).
      const key = JSON.stringify(data);
      if (key === drawn) return;
      drawn = key;
      empty.hidden = drawSeries(canvas, data, element);
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
  const shown = (isShown) => {
    if (empty.hidden !== isShown) empty.hidden = isShown;
    if (picture.hidden !== !isShown) picture.hidden = !isShown;
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

function renderIndicator(panel, element) {
  const node = row(element);
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

function drawSeries(canvas, data, element) {
  const context = canvas.getContext('2d');
  const style = getComputedStyle(document.documentElement);
  // The series is drawn in the trace colour, which is the same colour every
  // live number on the page is set in: one hue means "this is the data".
  const trace = style.getPropertyValue('--trace');
  const muted = style.getPropertyValue('--muted');
  context.clearRect(0, 0, canvas.width, canvas.height);
  const points = normalisePoints(data);
  context.strokeStyle = muted;
  context.strokeRect(0.5, 0.5, canvas.width - 1, canvas.height - 1);
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
  const pad = 24;
  context.beginPath();
  points.forEach((point, index) => {
    const x = pad + ((point[0] - x0) / spanX) * (canvas.width - pad * 2);
    const y = canvas.height - pad - ((point[1] - y0) / spanY) * (canvas.height - pad * 2);
    if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
  });
  context.strokeStyle = trace;
  context.lineWidth = 1.5;
  context.stroke();
  context.fillStyle = muted;
  context.fillText(String(element.y_label || ''), 4, 12);
  context.fillText(String(element.x_label || ''), canvas.width - pad, canvas.height - 6);
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
    this.widgets = [];
    this.values = {};
    this.lastData = 0;
    this.isCollapsed = false;
    this.isOffline = false;
    this.lost = [];
    this.title = (options && options.title) || name;
    this.node = make('section', 'card');
    const head = make('header', 'card-head');
    const title = make('h2', 'card-title', sentence(this.title));
    title.setAttribute('translate', 'no');
    head.appendChild(title);
    this.staleBadge = make('span', 'stale-badge', 'Stale');
    this.staleBadge.hidden = true;
    head.appendChild(this.staleBadge);
    if (options && options.collapsible) {
      // The collapsed card keeps its header bar, so the panel is always one
      // click from being back.
      this.collapseButton = make('button', 'ghost collapse', 'Collapse');
      this.collapseButton.type = 'button';
      this.collapseButton.addEventListener('click',
        () => this.setCollapsed(!this.isCollapsed));
      head.appendChild(this.collapseButton);
    }
    if (options && options.closable) {
      // Closing a module is housekeeping, not a stop: it is chrome, and the
      // signal red is spent on the mushroom alone (design brief, "one red").
      // But it destructs the model (owner ruling: close = destruct), so it
      // is the quietest control on the panel, it says what it does when
      // pointed at, and it asks first (Dashboard.closeModel).
      const close = make('button', 'ghost card-close', 'Close');
      close.type = 'button';
      close.title = 'Close ' + name + ': it stops and disconnects. '
        + 'Reopen it from the rail.';
      close.setAttribute('aria-label', 'Close ' + name);
      close.addEventListener('click', () => dashboard.closeModel(name));
      head.appendChild(close);
    }
    this.node.appendChild(head);
    // A lost device is a standing condition, not a refusal: it has its own
    // line under the header, and it stays until the device is back (F3).
    this.alert = make('p', 'card-alert');
    this.alert.hidden = true;
    this.node.appendChild(this.alert);
    // The refusal line. It starts under the header and moves to sit under
    // the control that caused it (showRefused, F10).
    this.status = make('p', 'status');
    this.status.setAttribute('role', 'status');
    this.status.setAttribute('aria-live', 'polite');
    this.status.hidden = true;
    this.node.appendChild(this.status);
    this.body = make('div', 'card-body');
    this.node.appendChild(this.body);
    this.build();
  }

  build() {
    const sections = this.schema.sections || [];
    if (isWideCard(sections)) this.node.classList.add('wide');
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
    let hasHead = false;
    for (const section of sections) {
      const isRow = isRowSection(section);
      const spans = isRow && isCommandRow(section);
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
      // on); a titled one's caption is the row's name.
      const hasTitle = !isRow || Boolean(section.title);
      if (hasTitle) {
        block.appendChild(isRow
          ? make('span', 'row-title', sentence(section.title || ''))
          : make('h3', 'section-title', sentenceCase(section.title || '')));
      }
      const cells = [];
      for (const element of (section.elements || [])) {
        const render = ELEMENT_RENDERERS[element.type];
        if (!render) {
          cells.push(make('p', 'status', 'cannot render ' + element.type));
          continue;
        }
        const widget = render(this, element);
        widget.element = element;
        this.widgets.push(widget);
        if (widget.node) {
          if (isRow) widget.node.classList.add('cell');
          cells.push(widget.node);
        }
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
      this.body.appendChild(block);
    }
  }

  /** Minimise to the header bar, or open again. The card stays in the page
   *  either way: collapsed is a state, not a removal (Addendum 2). */
  setCollapsed(isCollapsed) {
    this.isCollapsed = Boolean(isCollapsed);
    this.body.hidden = this.isCollapsed;
    this.node.classList.toggle('collapsed', this.isCollapsed);
    // The status line lives above the body, so a refusal raised by a
    // collapsed card is still a sentence the operator can read.
    if (this.collapseButton) {
      // The label says what the click will do: collapsed -> "Expand".
      this.collapseButton.textContent = this.isCollapsed ? 'Expand' : 'Collapse';
    }
  }

  // -- the three calls a view makes ------------------------------------
  async call(command, inputs, args) {
    return await apiPost('/api/run', {
      name: this.name, command, inputs: inputs || {}, args: args || [],
    });
  }

  /** Every writable entry's current text travels with every command, so a
   *  value typed a moment ago is never one edit behind. */
  gatherInputs() {
    const inputs = {};
    for (const widget of this.widgets) {
      const element = widget.element;
      if (element.type === 'entry' && element.writable && widget.readValue) {
        inputs[element.model_attr] = widget.readValue();
      }
    }
    return inputs;
  }

  async run(element, args) {
    let result;
    try {
      result = await this.call(element.command, this.gatherInputs(), args || []);
      // The page's own confirmation, not window.confirm: it defaults to
      // Cancel, and it never blocks the page's stop the way a native
      // dialog blocks every script on it (F17, HC-2).
      if (result.status === 'needs_confirm'
          && await this.dashboard.confirm(result.reason, confirmLabel(result))) {
        const again = (result.args || []).concat([true]);
        result = await this.call(result.command, result.inputs || {}, again);
      }
    } catch (err) {
      this.showRefused('The station did not answer (' + failureReason(err)
        + '). Check that it is running, then try again.', element);
      return { status: 'failed', reason: String(err) };
    }
    if (result.status === 'ok') this.showRefused('');
    else if (result.status !== 'needs_confirm') this.showRefused(result.reason || '', element);
    await this.dashboard.refreshNow();
    return result;
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
      + '&inputs=' + encodeURIComponent(JSON.stringify(this.gatherInputs()));
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
    this.isOffline = false;
    this.values = (state && state.values) || {};
    const mode = (state && state.mode) || '';
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
      } else if (kind === 'toggle' || kind === 'indicator') {
        widget.setOn(Boolean(this.values[attr]));
      } else if (kind === 'plot' || kind === 'image' || kind === 'log_stream') {
        if (wantsData) this.loadData(widget);
      }
      widget.setEnabled(isEnabled(element, mode));
    }
    // A lost device freezes the numbers even while the model's own loop
    // keeps ticking, so `age` alone would call them fresh (F3, HC-1).
    this.lost = lostDevices(state);
    const isLost = this.lost.length > 0;
    this.setStale(isStale(state) || isLost, isLost ? 'Connection lost' : 'Stale');
    this.setAlert(isLost ? sentence(this.title) + ' lost its ' + this.lost.join(' and ')
      + '. Its readings are frozen. Press Stop, check the cable, then relaunch from Setup.'
      : '');
    // The grouping bar down the left of a rack panel is information, not
    // trim: it lights in the trace colour while this model's loop is
    // reporting fresh numbers, and turns signal red while it is latched or
    // has lost a device.
    const age = state ? state.age : null;
    this.node.classList.toggle('is-live', !isLost
      && age !== null && age !== undefined && age <= STALE_AFTER_S);
    this.node.classList.toggle('is-lost', isLost);
    this.node.classList.toggle('is-latched', Boolean(this.values.is_estopped));
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
    this.node.classList.toggle('stale', Boolean(isStale));
  }

  /** Where a refusal about `element` is shown: right under the control, or
   *  under the action group or table row that holds it (F10, CRIT-3). */
  anchorFor(element) {
    const widget = element && this.widgets.find((w) => w.element === element);
    const node = widget && widget.node;
    if (!node || !node.isConnected || !this.node.contains(node)) return null;
    return node.closest('.section-row') || node.closest('.actions') || node;
  }

  /** Non-modal: a refusal is a sentence on the card, never a popup. It sits
   *  under the control that caused it, is brought into view, and shakes as
   *  it arrives - every time, a repeated refusal included - because a line
   *  that simply appears somewhere on a tall panel is a line the operator
   *  never sees. The next successful command from this card clears it. */
  showRefused(reason, element) {
    if (!reason) {
      if (!this.status.hidden) this.status.hidden = true;
      putText(this.status, '');
      return;
    }
    const anchor = this.anchorFor(element);
    if (anchor && anchor.nextSibling !== this.status) {
      anchor.parentNode.insertBefore(this.status, anchor.nextSibling);
    } else if (!anchor && this.status.previousSibling !== this.alert) {
      this.node.insertBefore(this.status, this.alert.nextSibling);
    }
    putText(this.status, reason);
    this.status.hidden = false;
    this.status.classList.remove('shake');
    void this.status.offsetWidth;          // restart the animation
    this.status.classList.add('shake');
    if (this.status.scrollIntoView && !this.node.closest('[hidden]')) {
      this.status.scrollIntoView({ block: 'nearest' });
    }
  }

  close() {
    this.widgets = [];
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
    this.setupCard = null;
    this.isLaunched = false;
    this.isEstopped = false;
    this.isDrawerOpen = false;
    this.isConnected = null;
    this.isActive = false;
    this.railGroups = new Map();
    this.railLines = new Map();
    this.closedKey = null;
    this.ackQueue = [];
    this.confirmPending = null;
    this.dom = {
      stop: document.getElementById('full-stop'),
      stopFace: document.querySelector('.mushroom-face'),
      railAlert: document.getElementById('rail-alert'),
      cards: document.getElementById('cards'),
      closed: document.getElementById('closed-models'),
      log: document.getElementById('event-log'),
      modal: document.getElementById('ack-modal'),
      modalCount: document.getElementById('ack-count'),
      modalText: document.getElementById('ack-text'),
      modalOk: document.getElementById('ack-ok'),
      confirm: document.getElementById('confirm-modal'),
      confirmText: document.getElementById('confirm-text'),
      confirmYes: document.getElementById('confirm-yes'),
      confirmNo: document.getElementById('confirm-no'),
      picker: document.getElementById('region-picker'),
      pickerCanvas: document.getElementById('region-canvas'),
      pickerClose: document.getElementById('region-close'),
      connection: document.getElementById('connection'),
      logPanel: document.getElementById('log-panel'),
      logToggle: document.getElementById('log-toggle'),
      trayLatest: document.getElementById('tray-latest'),
      rail: document.getElementById('rail-readouts'),
      drawer: document.getElementById('setup-drawer'),
      drawerBody: document.getElementById('drawer-body'),
      drawerClose: document.getElementById('drawer-close'),
      scrim: document.getElementById('scrim'),
      setupLink: document.getElementById('setup-link'),
    };
    this.isLogCollapsed = true;
    this.dom.stop.addEventListener('click', () => this.toggleEstopAll());
    this.dom.modalOk.addEventListener('click', () => this.acknowledge());
    this.dom.pickerClose.addEventListener('click', () => this.closeRegionPicker());
    this.dom.confirmYes.addEventListener('click', () => this.answerConfirm(true));
    this.dom.confirmNo.addEventListener('click', () => this.answerConfirm(false));
    this.dom.logToggle.addEventListener('click',
      () => this.setLogCollapsed(!this.isLogCollapsed));
    this.dom.setupLink.addEventListener('click', () => this.setDrawerOpen(true));
    this.dom.drawerClose.addEventListener('click', () => this.setDrawerOpen(false));
    this.dom.scrim.addEventListener('click', () => this.setDrawerOpen(false));
    // One keyboard handler, in the capture phase so nothing on the page can
    // swallow it first. Alt+. stops every model from anywhere, a text box
    // included (F9). Escape answers the top-most thing over the page: a
    // confirmation is cancelled, the region picker closes, the drawer
    // withdraws. It never dismisses an acknowledgement - that wants one.
    document.addEventListener('keydown', (event) => {
      if (event.code === STOP_KEY_CODE && event.altKey && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        this.stopAll();
        return;
      }
      if (event.key !== 'Escape') return;
      if (this.confirmPending) { event.preventDefault(); this.answerConfirm(false); return; }
      if (!this.dom.picker.hidden) { this.closeRegionPicker(); return; }
      if (this.isDrawerOpen && this.dom.modal.hidden) this.setDrawerOpen(false);
    }, true);
    // Closing the tab silences the heartbeat, and 15 s later the watchdog
    // stops the station: while anything is moving, heating or recording,
    // the browser asks first (F24, WDG-12).
    window.addEventListener('beforeunload', (event) => {
      if (!this.isActive) return;
      event.preventDefault();
      event.returnValue = '';
    });
    window.addEventListener('resize', () => this.reserveLogSpace());
    // The rail wraps on a narrow window and the tray grows when it opens;
    // the drawer and the scrim are fixed against both, so both heights are
    // measured, not assumed.
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
    setInert(this.dom.cards, covered || this.isDrawerOpen);
    setInert(this.dom.logPanel, covered);
    setInert(this.dom.drawer, covered || !this.isDrawerOpen);
    for (const layer of overlays) setInert(layer, layer !== top);
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
    const rail = document.querySelector('.rail');
    if (rail) root.setProperty('--rail-h', rail.offsetHeight + 'px');
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
    // Start from the newest event id so a fresh tab does not replay the
    // whole session's log as if it had just happened (ERRORS-3). What has
    // happened is still history: the last few lines go into the log, and
    // the newest is the tray's line, so the tray does not say "Waiting for
    // the station…" beside a rail that says it is connected (WDG-11).
    try {
      const seen = await apiGet('/api/events?since=0');
      this.lastEventId = seen.latest_id || 0;
      const history = (seen.events || []).slice(-20);
      for (const event of history) this.showEvent(event);
      if (!history.length) putText(this.dom.trayLatest, 'No events yet.');
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
  // Deliberately NOT the same signal as the state poll. The state poll keeps
  // running - throttled, not stopped - in a backgrounded tab in every
  // browser this targets, which would tell the watchdog a client is present
  // while the operator is looking at another tab entirely: the exact "closed
  // the laptop lid" case D-8 exists for. This stops outright the moment the
  // tab is hidden and resumes the moment it is visible.
  startHeartbeat() {
    this.stopHeartbeat();
    this.sendHeartbeat();
    this.heartbeatTimer = setInterval(() => this.sendHeartbeat(), HEARTBEAT_MS);
  }

  stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  async sendHeartbeat() {
    if (typeof document !== 'undefined' && document.hidden) return;
    try {
      await apiPost('/api/heartbeat', {});
    } catch (err) {
      // Nothing to recover: a missed heartbeat is the signal itself.
    }
  }

  watchVisibility() {
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.stopHeartbeat(); else this.startHeartbeat();
    });
    window.addEventListener('pagehide', () => this.stopHeartbeat());
  }

  // -- polling ------------------------------------------------------------
  async poll() {
    if (this.isPolling) return;
    this.isPolling = true;
    try {
      const state = await apiGet('/api/state');
      this.setConnected(true);
      await this.applyState(state);
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
    if (this.isConnected === isConnected) return;
    this.isConnected = isConnected;
    const link = this.dom.connection;
    if (isConnected) {
      link.textContent = 'Connected';
      link.title = '';
    } else {
      const since = clockTime(new Date());
      link.textContent = 'Not answering since ' + since;
      link.title = 'The station has not answered since ' + since
        + '. Every number on this page is frozen. Is the station still running?';
      for (const card of this.cards.values()) card.setOffline();
      if (this.setupCard) this.setupCard.setOffline();
      for (const group of this.railGroups.values()) {
        group.node.classList.add('is-stale');
        putText(group.flag, 'Stale');
      }
    }
    link.className = 'link-state' + (isConnected ? '' : ' is-down');
    document.body.classList.toggle('is-offline', !isConnected);
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
        // The stop's own line is always the first thing read.
        if (key === 'stop') this.dom.railAlert.insertBefore(node, this.dom.railAlert.firstChild);
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

  async applyState(state) {
    const models = state.models || {};
    this.isActive = Boolean(state.is_active);
    for (const name of Object.keys(models)) {
      if (!this.cards.has(name)) await this.addCard(name);
      const card = this.cards.get(name);
      if (card) card.refresh(models[name]);
    }
    for (const name of Array.from(this.cards.keys())) {
      if (!(name in models)) this.removeCard(name);
    }
    this.renderRail(models);
    this.renderLostLines(models);
    this.renderEmptyRack();
    this.renderClosed(state.closed || []);
    this.renderEstop(Boolean(state.is_estopped));
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

  // -- the status rail -----------------------------------------------------
  //
  // One readout group per open model, carrying its key numbers - derived
  // from the schema by `railElements`, never named here, so a model this
  // file has never heard of still appears on the rail with the right
  // numbers. The groups are built once per model and only their values are
  // written after that, so the entrance animation plays exactly once.
  renderRail(models) {
    for (const name of Array.from(this.railGroups.keys())) {
      if (name in models) continue;
      const gone = this.railGroups.get(name);
      if (gone.node.parentNode) gone.node.parentNode.removeChild(gone.node);
      this.railGroups.delete(name);
    }
    let index = 0;
    for (const name of Object.keys(models)) {
      let group = this.railGroups.get(name);
      if (!group) {
        group = this.buildRailGroup(name, index);
        if (!group) continue;
        this.railGroups.set(name, group);
        this.dom.rail.appendChild(group.node);
      }
      const values = (models[name] || {}).values || {};
      for (const [attr, node] of group.values) {
        const value = values[attr];
        const text = (value === undefined || value === null || value === '')
          ? '--' : String(value);
        if (node.textContent !== text) {
          node.textContent = text;
          // A long value is cut with an ellipsis on the rail; the whole of
          // it is one hover away, never silently lost.
          node.title = text;
          const kind = readoutKind(text);
          node.classList.toggle('is-empty', kind === 'quiet');
          node.classList.toggle('is-word', kind === 'word');
        }
      }
      // A model whose numbers are not live says so on the rail, in words,
      // and its numbers go muted (F3, F4).
      const state = models[name] || {};
      const isLost = lostDevices(state).length > 0;
      const flag = isLost ? 'Connection lost' : (isStale(state) ? 'Stale' : '');
      putText(group.flag, flag);
      group.node.classList.toggle('is-stale', Boolean(flag));
      group.node.classList.toggle('is-lost', isLost);
      index += 1;
    }
  }

  /** An empty rack says what to do next, and the drawer that does it is
   *  already open behind this. An empty screen is an invitation to act. */
  renderEmptyRack() {
    // Not while the drawer is open: the drawer IS the invitation, and a note
    // underneath it is a sentence nobody can read.
    const isEmpty = this.cards.size === 0 && !this.isDrawerOpen;
    if (isEmpty && !this.emptyNote) {
      this.emptyNote = make('p', 'rack-empty',
        'No modules yet. In Setup, give each device you are using a port - '
        + 'SIM to run against the simulator - and launch.');
      this.dom.cards.appendChild(this.emptyNote);
    } else if (!isEmpty && this.emptyNote) {
      if (this.emptyNote.parentNode) {
        this.emptyNote.parentNode.removeChild(this.emptyNote);
      }
      this.emptyNote = null;
    }
  }

  buildRailGroup(name, index) {
    const card = this.cards.get(name);
    const elements = railElements(card && card.schema);
    if (!elements.length) return null;
    const node = make('div', 'readout-group is-entering');
    node.style.setProperty('--stagger', String(index));
    node.setAttribute('role', 'group');
    node.setAttribute('aria-label', sentence(name));
    const head = make('div', 'readout-head');
    head.appendChild(make('span', 'readout-model', sentence(name)));
    const flag = make('span', 'readout-flag');
    head.appendChild(flag);
    node.appendChild(head);
    const line = make('div', 'readouts');
    const values = new Map();
    for (const element of elements) {
      const readout = make('div', 'readout');
      readout.appendChild(make('span', 'readout-label', railLabel(element)));
      const value = make('span', 'readout-value', '--');
      value.setAttribute('translate', 'no');
      readout.appendChild(value);
      line.appendChild(readout);
      values.set(element.model_attr, value);
    }
    node.appendChild(line);
    return { node, values, flag };
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
    const card = new PanelCard(this, name, schema, { closable: true });
    this.cards.set(name, card);
    this.dom.cards.appendChild(card.node);
  }

  removeCard(name) {
    const card = this.cards.get(name);
    if (card) card.close();
    this.cards.delete(name);
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
  // The mushroom follows the state, never the click. Its copy is the action
  // it will perform - "Stop", then "Clear" once the latch is set - and it
  // pulses exactly once, at the moment the latch closes, not for as long as
  // it stays closed.
  renderEstop(isEstopped) {
    const wasEstopped = this.isEstopped;
    this.isEstopped = isEstopped;
    putText(this.dom.stopFace, isEstopped ? 'Clear' : 'Stop');
    this.dom.stop.classList.toggle('is-latched', isEstopped);
    putAttr(this.dom.stop, 'aria-label',
            isEstopped ? 'Clear the stop on every model' : 'Stop every model');
    // The keyboard path is written on the object itself (F9).
    putAttr(this.dom.stop, 'title', isEstopped
      ? 'Clear the stop on every model (asks first). ' + STOP_KEY_HINT + ' stops again.'
      : 'Stop every model. Keyboard: ' + STOP_KEY_HINT + ', from anywhere on the page.');
    if (isEstopped && !wasEstopped) {
      this.dom.stop.classList.remove('pulse');
      void this.dom.stop.offsetWidth;      // restart the animation
      this.dom.stop.classList.add('pulse');
    } else if (!isEstopped) {
      this.dom.stop.classList.remove('pulse');
    }
  }

  async toggleEstopAll() {
    if (!this.isEstopped) {
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
    const unconfirmed = (answer && answer.unconfirmed) || [];
    this.setRailLine('stop', unconfirmed.length
      ? 'Stop latched, but ' + unconfirmed.map(sentence).join(', ')
        + (unconfirmed.length > 1 ? ' have' : ' has') + ' not confirmed it. Treat '
        + (unconfirmed.length > 1 ? 'them' : 'it') + ' as live.'
      : '', true);
    await this.refreshNow();
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
      this.showEvent(event);
      if (event.needs_ack) this.showAck(event);
    }
    if (typeof answer.latest_id === 'number') this.lastEventId = answer.latest_id;
  }

  showEvent(event) {
    const line = make('div', 'event severity-' + event.severity, event.text);
    this.dom.log.appendChild(line);
    while (this.dom.log.childNodes.length > 200) {
      this.dom.log.removeChild(this.dom.log.firstChild);
    }
    this.dom.log.scrollTop = this.dom.log.scrollHeight;
    // The collapsed tray is one line, and that line is the newest event.
    this.dom.trayLatest.textContent = event.text;
    this.dom.trayLatest.className = 'tray-latest severity-' + event.severity;
  }

  /** Only `needs_ack` opens a modal. Everything else is a line in the log.
   *  The messages queue: a second one arriving before the first is
   *  acknowledged is added under it, never written over it (F1, HC-2). The
   *  modal starts below the rail, so the stop stays in reach. */
  showAck(event) {
    const wasHidden = this.dom.modal.hidden;
    if (wasHidden) this.ackReturn = document.activeElement;
    this.ackQueue.push(String(event.text || ''));
    const list = this.dom.modalText;
    list.appendChild(make('p', 'ack-line', event.text));
    const count = this.ackQueue.length;
    putText(this.dom.modalCount, count > 1 ? count + ' messages need acknowledging' : '');
    this.dom.modalCount.hidden = count < 2;
    putText(this.dom.modalOk, count > 1 ? 'Acknowledge all' : 'Acknowledge');
    if (wasHidden) {
      this.dom.modal.hidden = false;
      this.updateInert();
      this.dom.modalOk.focus({ preventScroll: true });
    }
  }

  acknowledge() {
    this.ackQueue = [];
    clear(this.dom.modalText);
    this.dom.modal.hidden = true;
    this.updateInert();
    this.restoreFocus(this.ackReturn, this.dom.cards);
  }

  // -- the region picker --------------------------------------------------
  async openRegionPicker(card, element) {
    const canvas = this.dom.pickerCanvas;
    this.pickerReturn = document.activeElement;
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
    };
    picture.src = frame.image;
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
      this.closeRegionPicker();
      card.run(element, [
        Math.round(box[0] * scaleX) + left,
        Math.round(box[1] * scaleY) + top,
        Math.round(box[2] * scaleX),
        Math.round(box[3] * scaleY),
      ]);
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
