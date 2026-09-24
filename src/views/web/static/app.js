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

// ==========================================================================
// The renderers: one per entry in schema.ELEMENT_TYPES.
//
// Each returns a widget: { node, setText?, setOn?, setData?, setEnabled,
// readValue?, isDirty? }. This is the seam a toolkit subclass fills in
// base.py - here the toolkit is the DOM.
// ==========================================================================
function renderReadonly(panel, element) {
  const node = row(element);
  const value = make('span', 'value ' + roleClass(element.role), '--');
  node.appendChild(value);
  return {
    node,
    setText: (text) => {
      const shown = readoutText(text);
      value.textContent = shown;
      // Nothing to report is not a reading: it is muted, whatever the
      // element's role, so an empty fault line is never a red "--".
      value.classList.toggle('is-empty', shown === '--');
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
  }
  if (element.min !== undefined && element.min !== null) input.min = String(element.min);
  if (element.max !== undefined && element.max !== null) input.max = String(element.max);
  group.appendChild(input);
  if (element.unit) group.appendChild(make('span', 'unit', element.unit));
  node.appendChild(group);
  let served = '';
  return {
    node,
    // Never overwrite what the operator is typing: focused, or edited away
    // from the last value the server sent.
    isDirty: () => document.activeElement === input || input.value !== served,
    readValue: () => input.value,
    setText: (text) => {
      // An int box never shows "5.000", whatever the state formatted.
      served = isInt ? intText(text)
        : ((text === null || text === undefined) ? '' : String(text));
      input.value = served;
    },
    setEnabled: (flag) => { input.disabled = !flag; node.classList.toggle('disabled', !flag); },
  };
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
    setEnabled: (flag) => { button.disabled = !flag; },
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
  const show = (on) => {
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
    setEnabled: (flag) => { button.disabled = !flag; },
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
  return {
    node,
    setOn: (on) => {
      face.textContent = on ? 'Clear' : 'Stop';
      button.classList.toggle('is-latched', Boolean(on));
      button.title = sentence(on ? (element.true_text || '')
                                 : (element.false_text || ''));
    },
    setEnabled: (flag) => { button.disabled = !flag; },
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
    },
    setEnabled: (flag) => { select.disabled = !flag; },
  };
}

function renderRegionSelect(panel, element) {
  const node = row(element);
  const value = make('span', 'value', 'Not set');
  const button = make('button', 'button ' + roleClass(element.role), 'Pick region');
  button.type = 'button';
  button.addEventListener('click', () => panel.pickRegion(element));
  node.appendChild(value);
  node.appendChild(button);
  return {
    node,
    setText: (text) => { value.textContent = text === '' ? 'Not set' : String(text); },
    setEnabled: (flag) => { button.disabled = !flag; },
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
    setEnabled: (flag) => { button.disabled = !flag; },
  };
}

function renderFileOpen(panel, element) {
  // The browser cannot hand the station a path, and this API deliberately
  // has no route that takes one (old finding 9). The command runs with no
  // argument; a model that needs one refuses, and the refusal shows on the
  // card. See the handoff: an upload route is an owner decision.
  const node = make('div', 'row command');
  const button = make('button', 'button ' + roleClass(element.role),
                      sentenceCase(element.text || 'Open'));
  button.type = 'button';
  button.addEventListener('click', () => panel.run(element));
  node.appendChild(button);
  return {
    node,
    setEnabled: (flag) => { button.disabled = !flag; },
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
  return {
    node,
    dataCommand: element.data_command,
    setData: (data) => { empty.hidden = drawSeries(canvas, data, element); },
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
  picture.addEventListener('load', () => { empty.hidden = true; picture.hidden = false; });
  picture.addEventListener('error', () => { empty.hidden = false; picture.hidden = true; });
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
  return {
    node,
    setOn: (on) => {
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
      const lines = (data && data.lines) || [];
      feed.textContent = lines.join('\n');
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
    this.node = make('section', 'card');
    const head = make('header', 'card-head');
    head.appendChild(make('h2', 'card-title',
                          sentence((options && options.title) || name)));
    this.staleBadge = make('span', 'stale-badge', 'stale');
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
    } catch (err) {
      this.showRefused('the station did not answer: ' + err);
      return { status: 'failed', reason: String(err) };
    }
    if (result.status === 'needs_confirm' && window.confirm(result.reason)) {
      const again = (result.args || []).concat([true]);
      result = await this.call(result.command, result.inputs || {}, again);
    }
    if (result.status === 'ok') this.showRefused('');
    else if (result.status !== 'needs_confirm') this.showRefused(result.reason || '');
    await this.dashboard.refreshNow();
    return result;
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
      const node = make('option', null, String(option));
      node.value = String(option);
      select.appendChild(node);
    }
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
      this.showRefused('the station did not answer: ' + err);
      return;
    }
    const type = response.headers.get('Content-Type') || '';
    if (!response.ok || type.indexOf('application/json') === 0) {
      const answer = await response.json().catch(() => ({ reason: 'download failed' }));
      this.showRefused(answer.reason || 'download failed');
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
    this.setStale(isStale(state));
    // The grouping bar down the left of a rack panel is information, not
    // trim: it lights in the trace colour while this model's loop is
    // reporting fresh numbers, and turns signal red while it is latched.
    const age = state ? state.age : null;
    this.node.classList.toggle('is-live',
      age !== null && age !== undefined && age <= STALE_AFTER_S);
    this.node.classList.toggle('is-latched', Boolean(this.values.is_estopped));
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

  setStale(isStale) {
    this.staleBadge.hidden = !isStale;
    this.node.classList.toggle('stale', Boolean(isStale));
  }

  /** Non-modal: a refusal is a sentence on the card, never a popup. It
   *  shakes once as it arrives - motion that answers an action - because a
   *  line that simply appears at the top of a tall panel is a line the
   *  operator never sees. */
  showRefused(reason) {
    const isNew = Boolean(reason) && reason !== this.status.textContent;
    this.status.textContent = reason || '';
    this.status.hidden = !reason;
    if (!isNew) return;
    this.status.classList.remove('shake');
    void this.status.offsetWidth;          // restart the animation
    this.status.classList.add('shake');
  }

  close() {
    this.widgets = [];
    if (this.node.parentNode) this.node.parentNode.removeChild(this.node);
  }
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
    this.railGroups = new Map();
    this.dom = {
      stop: document.getElementById('full-stop'),
      stopFace: document.querySelector('.mushroom-face'),
      cards: document.getElementById('cards'),
      closed: document.getElementById('closed-models'),
      log: document.getElementById('event-log'),
      modal: document.getElementById('ack-modal'),
      modalText: document.getElementById('ack-text'),
      modalOk: document.getElementById('ack-ok'),
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
    this.dom.modalOk.addEventListener('click', () => { this.dom.modal.hidden = true; });
    this.dom.pickerClose.addEventListener('click', () => { this.dom.picker.hidden = true; });
    this.dom.logToggle.addEventListener('click',
      () => this.setLogCollapsed(!this.isLogCollapsed));
    this.dom.setupLink.addEventListener('click', () => this.setDrawerOpen(true));
    this.dom.drawerClose.addEventListener('click', () => this.setDrawerOpen(false));
    this.dom.scrim.addEventListener('click', () => this.setDrawerOpen(false));
    // Escape closes the drawer, the way every other panel over a page does.
    // It never closes anything else here: the acknowledgement modal wants an
    // acknowledgement, and the region picker has its own Cancel.
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.isDrawerOpen) this.setDrawerOpen(false);
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
    this.isDrawerOpen = Boolean(isOpen);
    this.dom.drawer.classList.toggle('open', this.isDrawerOpen);
    // The scrim dims what the drawer is covering. At boot it is covering an
    // empty rack, so there is nothing to dim and no scrim.
    this.dom.scrim.hidden = !(this.isDrawerOpen && this.cards.size > 0);
    this.dom.setupLink.hidden = this.isDrawerOpen;
    this.dom.drawer.setAttribute('aria-hidden', this.isDrawerOpen ? 'false' : 'true');
    this.dom.drawer.inert = !this.isDrawerOpen;
    // Focus moves into the drawer - to the drawer itself, not to its Close
    // button, which is not the thing the operator came to press.
    if (this.isDrawerOpen) this.dom.drawer.focus({ preventScroll: true });
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
    // whole session's log as if it had just happened (ERRORS-3).
    try {
      const seen = await apiGet('/api/events?since=0');
      this.lastEventId = seen.latest_id || 0;
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

  setConnected(isConnected) {
    this.dom.connection.textContent = isConnected ? 'Connected'
      : 'Not answering. Is the station still running?';
    this.dom.connection.className = 'link-state' + (isConnected ? '' : ' is-down');
  }

  async applyState(state) {
    const models = state.models || {};
    for (const name of Object.keys(models)) {
      if (!this.cards.has(name)) await this.addCard(name);
      const card = this.cards.get(name);
      if (card) card.refresh(models[name]);
    }
    for (const name of Array.from(this.cards.keys())) {
      if (!(name in models)) this.removeCard(name);
    }
    this.renderRail(models);
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
        }
      }
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
    node.appendChild(make('span', 'readout-model', sentence(name)));
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
    return { node, values };
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
    const answer = await apiPost('/api/open_model', { name });
    if (answer.status !== 'ok') window.alert(answer.reason || 'could not reopen ' + name);
    await this.refreshNow();
  }

  async closeModel(name) {
    if (!window.confirm('Close ' + name + '?\n\nIt stops and disconnects. '
                        + 'You can reopen it from the rail.')) return;
    await apiPost('/api/close_model', { name });
    await this.refreshNow();
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
    this.dom.stopFace.textContent = isEstopped ? 'Clear' : 'Stop';
    this.dom.stop.classList.toggle('is-latched', isEstopped);
    this.dom.stop.setAttribute(
      'aria-label', isEstopped ? 'Clear the stop on every model'
                               : 'Stop every model');
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
      await apiPost('/api/estop_all', {});
      await this.refreshNow();
      return;
    }
    let result = await apiPost('/api/clear_estop_all', {});
    if (result.status === 'needs_confirm' && window.confirm(result.reason)) {
      result = await apiPost('/api/clear_estop_all', { confirmed: true });
    }
    await this.refreshNow();
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

  /** Only `needs_ack` opens a modal. Everything else is a line in the log. */
  showAck(event) {
    this.dom.modalText.textContent = event.text;
    this.dom.modal.hidden = false;
    this.dom.modalOk.focus({ preventScroll: true });
  }

  // -- the region picker --------------------------------------------------
  async openRegionPicker(card, element) {
    const canvas = this.dom.pickerCanvas;
    this.dom.picker.hidden = false;
    this.dom.pickerClose.focus({ preventScroll: true });
    const context = canvas.getContext('2d');
    context.clearRect(0, 0, canvas.width, canvas.height);
    let frame;
    try {
      frame = await apiGet('/api/screen?name=' + encodeURIComponent(card.name));
    } catch (err) {
      card.showRefused('no screen image: ' + err);
      this.dom.picker.hidden = true;
      return;
    }
    if (!frame || !frame.image) {
      card.showRefused((frame && frame.reason) || 'the station offers no screen image');
      this.dom.picker.hidden = true;
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
      context.strokeStyle = getComputedStyle(document.documentElement)
        .getPropertyValue('--signal');
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
    canvas.onpointerup = (event) => {
      if (!start) return;
      const box = boxFrom(start, at(event));
      start = null;
      if (box[2] < 4 || box[3] < 4) return;
      this.dom.picker.hidden = true;
      card.run(element, [
        Math.round(box[0] * scaleX) + left,
        Math.round(box[1] * scaleY) + top,
        Math.round(box[2] * scaleX),
        Math.round(box[3] * scaleY),
      ]);
    };
  }
}

if (typeof window !== 'undefined' && window.document && !window.__STATION_NO_BOOT__) {
  window.addEventListener('DOMContentLoaded', () => {
    window.station = new Dashboard(document.body);
    window.station.start();
  });
}
