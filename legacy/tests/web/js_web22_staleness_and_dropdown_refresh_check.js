'use strict';
// Harness for
// test_web_22_staleness_and_dropdown_refresh.py::test_staleness_marking_and_dropdown_refresh_on_focus.
//
// WEB-22's remainder (per the wave-4 brief): per-device staleness marking
// and dropdown refresh-on-focus are implemented in app.js but were never
// exercised by any test - no DOM harness existed. This is not a fix; it is
// coverage, using the same vm-sandbox + Object.create(prototype) technique
// as js_fetch_timeout_check.js, js_rotator13_connection_gate_check.js,
// js_web13_stop_monitoring_save_check.js and
// js_web19_client_heartbeat_check.js.
const vm = require('vm');
const fs = require('fs');

const appJsPath = process.argv[2];
const source = fs.readFileSync(appJsPath, 'utf8')
  + '\nglobalThis.__TransferStageApp = TransferStageApp;\n';

let fetchCalls = [];
let fetchOptionsResponse = { status: 'ok', options: ['A', 'B', 'C'] };
function fakeFetch(url) {
  fetchCalls.push(url);
  return Promise.resolve({
    ok: true,
    status: 200,
    json: async () => fetchOptionsResponse,
  });
}

function fakeElement(overrides) {
  const children = [];
  return Object.assign({
    dataset: {},
    classList: { toggle: () => {} },
    style: {},
    disabled: false,
    value: '',
    options: { length: 1 },
    _listeners: {},
    addEventListener(evt, cb) { this._listeners[evt] = cb; },
    get firstChild() { return children.length ? children[0] : null; },
    removeChild(child) {
      const idx = children.indexOf(child);
      if (idx !== -1) children.splice(idx, 1);
    },
    appendChild(child) { children.push(child); },
    _children: children,
  }, overrides);
}

const sandbox = {
  console,
  AbortController,
  Headers,
  setTimeout,
  clearTimeout,
  fetch: (...args) => fakeFetch(...args),
  document: {
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => fakeElement({}),
  },
};
sandbox.window = {
  location: { origin: 'http://127.0.0.1:0' },
  fetch: (...args) => fakeFetch(...args),
  addEventListener: () => {},
};

vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'app.js' });

const App = sandbox.__TransferStageApp;
if (!App) {
  console.log('FAIL_NO_CLASS');
  process.exit(1);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function testStaleness() {
  const failures = [];
  const app = Object.create(App.prototype);

  const controls = [{ disabled: false }, { disabled: false }];
  const cardBody = { querySelectorAll: () => controls };
  const classes = { stale: false };
  const card = {
    classList: {
      toggle: (cls, val) => { if (cls === 'stale') classes.stale = val; },
    },
    querySelector: (sel) => (sel === '.card-body' ? cardBody : null),
  };

  sandbox.document.getElementById = (id) => (id === 'card-Rotator' ? card : null);

  app._markDeviceSeen('Rotator');
  if (classes.stale) failures.push('staleness: freshly-seen device must not be stale');

  app._markDeviceMissedCycle('Rotator');
  app._markDeviceMissedCycle('Rotator');
  if (classes.stale) failures.push('staleness: must not go stale before STALE_AFTER_CYCLES misses');
  if (controls.some((c) => c.disabled)) failures.push('staleness: controls must not be disabled before threshold');

  app._markDeviceMissedCycle('Rotator'); // third consecutive miss == STALE_AFTER_CYCLES
  if (!classes.stale) failures.push('staleness: must go stale at STALE_AFTER_CYCLES misses');
  if (!controls.every((c) => c.disabled)) failures.push('staleness: every control must be disabled once stale');

  app._markDeviceSeen('Rotator');
  if (classes.stale) failures.push('staleness: seeing the device again must clear the stale class');

  return failures;
}

async function testDropdownRefresh() {
  const failures = [];
  const app = Object.create(App.prototype);

  // --- refreshDropdownOptions itself: populates from fetch, keeps a
  // still-valid previous selection.
  fetchCalls = [];
  fetchOptionsResponse = { status: 'ok', options: ['A', 'B', 'C'] };
  const select = fakeElement({ value: 'B', dataset: {} });
  app.refreshDropdownOptions(select, 'Rotator', 'get_available_controllers');
  await sleep(20);

  if (fetchCalls.length !== 1 || fetchCalls[0].indexOf('/api/options') === -1) {
    failures.push('refreshDropdownOptions: expected one /api/options fetch');
  }
  if (fetchCalls[0].indexOf('device=Rotator') === -1
      || fetchCalls[0].indexOf('command=get_available_controllers') === -1) {
    failures.push(`refreshDropdownOptions: expected device/command in URL, got ${fetchCalls[0]}`);
  }
  // 1 placeholder + 3 options
  if (select._children.length !== 4) {
    failures.push(`refreshDropdownOptions: expected 4 option elements, got ${select._children.length}`);
  }
  if (select.value !== 'B') {
    failures.push('refreshDropdownOptions: expected previous selection "B" to be preserved');
  }
  if (select.dataset.populated !== 'true') {
    failures.push('refreshDropdownOptions: expected dataset.populated to be set');
  }

  // --- bindCardInteractiveEvents: a focus listener is attached once, and
  // triggering it re-fetches (this is the actual WEB-22 behavior under
  // test - the dropdown does not go stale forever after first render).
  const boundSelect = fakeElement({
    dataset: { device: 'Rotator', optionsCommand: 'get_available_controllers', populated: 'true' },
    options: { length: 4 },
  });
  sandbox.document.querySelectorAll = (sel) => (sel === 'select[data-options-command]' ? [boundSelect] : []);

  let refreshCalls = 0;
  const originalRefresh = app.refreshDropdownOptions.bind(app);
  app.refreshDropdownOptions = (...args) => { refreshCalls += 1; return originalRefresh(...args); };

  app.bindCardInteractiveEvents();

  if (refreshCalls !== 0) {
    failures.push('bindCardInteractiveEvents: an already-populated select must not auto-refresh on bind');
  }
  if (typeof boundSelect._listeners.focus !== 'function') {
    failures.push('bindCardInteractiveEvents: expected a focus listener on the options dropdown');
  } else {
    boundSelect._listeners.focus();
    await sleep(20);
    if (refreshCalls !== 1) {
      failures.push(`bindCardInteractiveEvents: expected exactly one refresh from focus, got ${refreshCalls}`);
    }
  }

  return failures;
}

async function main() {
  const failures = [
    ...(await testStaleness()),
    ...(await testDropdownRefresh()),
  ];

  if (failures.length) {
    console.log('FAIL: ' + failures.join(' | '));
    process.exit(1);
  }
  console.log('OK');
  process.exit(0);
}

main();
