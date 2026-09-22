/**
 * The Web client. It is `station/views/base.py` written in JavaScript.
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
  node.appendChild(make('span', 'label', element.text || element.model_attr || ''));
  return node;
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

/** How many element columns the widest row section needs. */
function rowColumnCount(sections) {
  let widest = 0;
  for (const section of (sections || [])) {
    if (!isRowSection(section)) continue;
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

/** `PanelView._refresh`: `age is not None and age > 1.0`. A model with no
 *  loop to be stale about reports a null age, and null is not stale - every
 *  card wore a "stale" badge in SIM when this was a bare comparison. */
function isStale(state) {
  const age = state ? state.age : null;
  return age !== null && age !== undefined && age > STALE_AFTER_S;
}

function roleClass(role) {
  return 'role-' + (role || 'neutral');
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// ==========================================================================
// The renderers: one per entry in station.schema.ELEMENT_TYPES.
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
    setText: (text) => { value.textContent = text === '' ? '--' : String(text); },
    setEnabled: (flag) => { node.classList.toggle('disabled', !flag); },
  };
}

function renderEntry(panel, element) {
  const node = row(element);
  const group = make('div', 'group');
  const input = make('input', 'input');
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
  const node = make('div', 'row');
  const button = make('button', 'button ' + roleClass(element.role), element.text || element.command);
  button.type = 'button';
  button.addEventListener('click', () => panel.run(element));
  node.appendChild(button);
  return {
    node,
    setEnabled: (flag) => { button.disabled = !flag; },
  };
}

function renderToggle(panel, element) {
  const node = row(element);
  const button = make('button', 'button toggle', element.false_text || 'OFF');
  button.type = 'button';
  button.addEventListener('click', () => panel.runToggle(element));
  node.appendChild(button);
  return {
    node,
    setOn: (on) => {
      button.textContent = on ? (element.true_text || 'ON') : (element.false_text || 'OFF');
      button.className = 'button toggle ' + roleClass(on ? element.on_role : element.off_role)
        + (on ? ' on' : ' off');
    },
    setEnabled: (flag) => { button.disabled = !flag; },
  };
}

function renderDropdown(panel, element) {
  const node = row(element);
  const select = make('select', 'select');
  const placeholder = make('option', null, 'Select...');
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
  const value = make('span', 'value', 'not set');
  const button = make('button', 'button ' + roleClass(element.role), 'Pick region');
  button.type = 'button';
  button.addEventListener('click', () => panel.pickRegion(element));
  node.appendChild(value);
  node.appendChild(button);
  return {
    node,
    setText: (text) => { value.textContent = text === '' ? 'not set' : String(text); },
    setEnabled: (flag) => { button.disabled = !flag; },
  };
}

function renderFileSave(panel, element) {
  const node = make('div', 'row');
  const button = make('button', 'button ' + roleClass(element.role), element.text || 'Save');
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
  const node = make('div', 'row');
  const button = make('button', 'button ' + roleClass(element.role), element.text || 'Open');
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
  const canvas = make('canvas', 'plot');
  canvas.width = 420;
  canvas.height = 180;
  node.appendChild(canvas);
  return {
    node,
    dataCommand: element.data_command,
    setData: (data) => drawSeries(canvas, data, element),
    setEnabled: (flag) => { node.classList.toggle('disabled', !flag); },
  };
}

function renderImage(panel, element) {
  const node = row(element, 'wide');
  const picture = make('img', 'picture');
  picture.alt = element.text || 'image';
  node.appendChild(picture);
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
      lamp.textContent = on ? 'yes' : 'no';
    },
    setEnabled: (flag) => { node.classList.toggle('disabled', !flag); },
  };
}

function renderLogStream(panel, element) {
  const node = row(element, 'wide');
  const feed = make('pre', 'feed');
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
  const ink = style.getPropertyValue('--text');
  const muted = style.getPropertyValue('--muted');
  context.clearRect(0, 0, canvas.width, canvas.height);
  const points = normalisePoints(data);
  context.strokeStyle = muted;
  context.strokeRect(0.5, 0.5, canvas.width - 1, canvas.height - 1);
  if (points.length < 2) {
    context.fillStyle = muted;
    context.fillText('no samples yet', 12, canvas.height / 2);
    return;
  }
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
  context.strokeStyle = ink;
  context.lineWidth = 1.5;
  context.stroke();
  context.fillStyle = muted;
  context.fillText(String(element.y_label || ''), 4, 12);
  context.fillText(String(element.x_label || ''), canvas.width - pad, canvas.height - 6);
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
    head.appendChild(make('h2', 'card-title', (options && options.title) || name));
    this.staleBadge = make('span', 'badge stale-badge', 'stale');
    this.staleBadge.hidden = true;
    head.appendChild(this.staleBadge);
    if (options && options.collapsible) {
      // The collapsed card keeps its header bar, so the panel is always one
      // click from being back (base.Dashboard._collapse_setup: "minimise the
      // Setup panel; reopenable").
      this.collapseButton = make('button', 'button small collapse', 'Hide');
      this.collapseButton.type = 'button';
      this.collapseButton.addEventListener('click',
        () => this.setCollapsed(!this.isCollapsed));
      head.appendChild(this.collapseButton);
    }
    if (options && options.closable) {
      const close = make('button', 'button role-danger small', 'Close');
      close.type = 'button';
      close.addEventListener('click', () => dashboard.closeModel(name));
      head.appendChild(close);
    }
    this.node.appendChild(head);
    this.status = make('p', 'status');
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
    for (const section of sections) {
      const isRow = isRowSection(section);
      const block = make('div', 'section' + (isRow ? ' section-row' : ''));
      block.appendChild(isRow
        ? make('span', 'row-title', section.title || '')
        : make('h3', 'section-title', section.title || ''));
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
      // its last cell, so the status column stays the status column.
      if (isRow) {
        while (cells.length && cells.length < columns) {
          cells.splice(cells.length - 1, 0, make('span', 'cell filler'));
        }
      }
      for (const cell of cells) block.appendChild(cell);
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
      this.collapseButton.textContent = this.isCollapsed ? 'Expand' : 'Hide';
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
    const placeholder = make('option', null, 'Select...');
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

  /** Non-modal: a refusal is a sentence on the card, never a popup. */
  showRefused(reason) {
    this.status.textContent = reason || '';
    this.status.hidden = !reason;
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
    this.dom = {
      stop: document.getElementById('full-stop'),
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
    };
    this.dom.stop.addEventListener('click', () => this.toggleEstopAll());
    this.dom.modalOk.addEventListener('click', () => { this.dom.modal.hidden = true; });
    this.dom.pickerClose.addEventListener('click', () => { this.dom.picker.hidden = true; });
  }

  async start() {
    // Start from the newest event id so a fresh tab does not replay the
    // whole session's log as if it had just happened (ERRORS-3).
    try {
      const seen = await apiGet('/api/events?since=0');
      this.lastEventId = seen.latest_id || 0;
    } catch (err) { /* the first poll will retry */ }
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
    this.dom.connection.textContent = isConnected ? 'connected' : 'no answer';
    this.dom.connection.className = 'badge ' + (isConnected ? 'role-go' : 'role-danger');
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

  /** `Dashboard._collapse_setup` in station/views/base.py, mirrored: the
   *  first time a model exists, the wizard gives way to it. The desktop
   *  views hear `added`; the browser polls, so the same edge is "models went
   *  from empty to not empty" - or the Setup panel's own `is_launched`,
   *  which is true the moment it has built its models. Once only: an
   *  operator who re-opens the card keeps it open. */
  collapseSetupOnLaunch(models, setupState) {
    if (this.isLaunched || !this.setupCard) return;
    const hasModels = Object.keys(models || {}).length > 0;
    const isLaunched = Boolean(setupState && setupState.is_launched);
    if (!hasModels && !isLaunched) return;
    this.isLaunched = true;
    this.setupCard.setCollapsed(true);
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
                                     { title: 'Setup', collapsible: true });
      this.setupCard.node.classList.add('setup-card');
      this.dom.cards.appendChild(this.setupCard.node);
      this.setupCard.refresh(setup.state);
    } catch (err) { /* setup is optional once models are built */ }
  }

  renderClosed(closed) {
    clear(this.dom.closed);
    if (!closed.length) return;
    this.dom.closed.appendChild(make('span', 'label', 'Closed:'));
    for (const name of closed) {
      const button = make('button', 'button small', name);
      button.type = 'button';
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
    if (!window.confirm('Close ' + name + '? It stops and disconnects.')) return;
    await apiPost('/api/close_model', { name });
    await this.refreshNow();
  }

  // -- the global FULL STOP ----------------------------------------------
  renderEstop(isEstopped) {
    this.dom.stop.textContent = isEstopped ? 'LATCHED - click to clear' : 'FULL STOP';
    this.dom.stop.className = 'button full-stop role-danger ' + (isEstopped ? 'on' : 'off');
    this.isEstopped = isEstopped;
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
  }

  /** Only `needs_ack` opens a modal. Everything else is a line in the log. */
  showAck(event) {
    this.dom.modalText.textContent = event.text;
    this.dom.modal.hidden = false;
  }

  // -- the region picker --------------------------------------------------
  async openRegionPicker(card, element) {
    const canvas = this.dom.pickerCanvas;
    this.dom.picker.hidden = false;
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
        .getPropertyValue('--danger-bg');
      context.lineWidth = 2;
      context.strokeRect(box[0], box[1], box[2], box[3]);
    };
    const boxFrom = (a, b) => [
      Math.min(a[0], b[0]), Math.min(a[1], b[1]),
      Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]),
    ];

    canvas.onmousedown = (event) => { start = at(event); };
    canvas.onmousemove = (event) => { if (start) paint(boxFrom(start, at(event))); };
    canvas.onmouseup = (event) => {
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
