'use strict';
// Harness for
// test_web_19_client_heartbeat.py::test_heartbeat_stops_when_tab_hidden_and_resumes_when_visible.
//
// Same vm-sandbox technique as js_fetch_timeout_check.js,
// js_rotator13_connection_gate_check.js and
// js_web13_stop_monitoring_save_check.js: load the real app.js source,
// build an instance via Object.create(TransferStageApp.prototype) to skip
// the constructor's DOM wiring, and drive the WEB-19 (D-8 client half)
// heartbeat methods directly against a fake document/window that records
// which event listeners got registered so the test can fire them the way
// a real browser would.
const vm = require('vm');
const fs = require('fs');

const appJsPath = process.argv[2];
const source = fs.readFileSync(appJsPath, 'utf8')
  + '\nglobalThis.__TransferStageApp = TransferStageApp;\n';

let fetchCalls = [];
function fakeFetch(url) {
  fetchCalls.push(url);
  return Promise.resolve({
    ok: true,
    status: 200,
    json: async () => ({ status: 'ok', code: 200 }),
  });
}

const listeners = { document: {}, window: {} };
const fakeDocument = {
  hidden: false,
  addEventListener: (evt, cb) => { listeners.document[evt] = cb; },
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
};
const fakeWindowFetch = (...args) => fakeFetch(...args);
const fakeWindow = {
  location: { origin: 'http://127.0.0.1:0' },
  fetch: fakeWindowFetch,
  addEventListener: (evt, cb) => { listeners.window[evt] = cb; },
};

const sandbox = {
  console,
  AbortController,
  Headers,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  fetch: (...args) => fakeFetch(...args),
  document: fakeDocument,
  window: fakeWindow,
};

vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'app.js' });

const App = sandbox.__TransferStageApp;
if (!App) {
  console.log('FAIL_NO_CLASS');
  process.exit(1);
}

// Shrink the real 2000ms interval so this test does not have to wait it out.
App.HEARTBEAT_INTERVAL_MS = 20;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  const failures = [];
  const app = Object.create(App.prototype);

  app.setupClientHeartbeatVisibility();
  if (typeof listeners.document.visibilitychange !== 'function') {
    failures.push('setupClientHeartbeatVisibility must register a visibilitychange listener');
  }
  if (typeof listeners.window.pagehide !== 'function') {
    failures.push('setupClientHeartbeatVisibility must register a pagehide listener');
  }

  // Visible: heartbeats go out on the interval.
  fetchCalls = [];
  fakeDocument.hidden = false;
  app.startClientHeartbeat();
  await sleep(95);
  const visibleCount = fetchCalls.length;
  if (visibleCount < 2) {
    failures.push(`expected multiple heartbeats while visible, got ${visibleCount}`);
  }
  fetchCalls.forEach((url) => {
    if (String(url).indexOf('/api/client/heartbeat') === -1) {
      failures.push(`unexpected heartbeat URL: ${url}`);
    }
  });

  // Hidden: the registered visibilitychange listener must stop them.
  if (typeof listeners.document.visibilitychange === 'function') {
    fakeDocument.hidden = true;
    listeners.document.visibilitychange();
  }
  fetchCalls = [];
  await sleep(95);
  if (fetchCalls.length !== 0) {
    failures.push(`expected zero heartbeats while hidden, got ${fetchCalls.length}`);
  }

  // Visible again: must resume without any extra wiring (no reload).
  if (typeof listeners.document.visibilitychange === 'function') {
    fakeDocument.hidden = false;
    listeners.document.visibilitychange();
  }
  fetchCalls = [];
  await sleep(95);
  if (fetchCalls.length < 2) {
    failures.push(`expected heartbeats to resume when visible again, got ${fetchCalls.length}`);
  }

  // pagehide (tab closed) stops it outright, same as hidden.
  if (typeof listeners.window.pagehide === 'function') {
    listeners.window.pagehide();
  }
  fetchCalls = [];
  await sleep(95);
  if (fetchCalls.length !== 0) {
    failures.push(`expected zero heartbeats after pagehide, got ${fetchCalls.length}`);
  }

  if (failures.length) {
    console.log('FAIL: ' + failures.join(' | '));
    process.exit(1);
  }
  console.log('OK');
  process.exit(0);
}

main();
