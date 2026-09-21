'use strict';
// Harness for
// test_web_13_stop_monitoring_offers_save.py::test_stop_monitoring_offers_to_save_unsaved_data.
//
// Same vm-sandbox technique as js_fetch_timeout_check.js and
// js_rotator13_connection_gate_check.js: load the real app.js source, then
// exercise TransferStageApp.dispatchCommand's WEB-13 special-case for
// 'stop_monitoring' directly, via Object.create(TransferStageApp.prototype)
// rather than standing up a fake DOM to run the real constructor.
//
// WEB-13's web half mirrors what PySide's view already does in-process
// (`if element.get("command") == "stop_monitoring" and
// self.model.has_unsaved_data`, views/pyside/view.py) - the web client
// only has what /api/state publishes (WebModelAdapter.get_state's
// pending_run_data), so dispatchCommand consults
// this.deviceState[device].pending_run_data before sending the command,
// and offers to run save_log first.
const vm = require('vm');
const fs = require('fs');

const appJsPath = process.argv[2];
const source = fs.readFileSync(appJsPath, 'utf8')
  + '\nglobalThis.__TransferStageApp = TransferStageApp;\n';

let calls;
function fakeFetch(url, opts) {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push({ url, body });
  return Promise.resolve({
    ok: true,
    status: 200,
    json: async () => ({ status: 'ok', code: 200, result: 'ok' }),
  });
}

let confirmReturn = true;
let promptReturn = 'export.csv';

const sandbox = {
  console,
  AbortController,
  Headers,
  setTimeout,
  clearTimeout,
  fetch: (...args) => fakeFetch(...args),
  confirm: () => confirmReturn,
  prompt: () => promptReturn,
  document: {
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
  },
};
sandbox.window = {
  location: { origin: 'http://127.0.0.1:0' },
  fetch: (...args) => fakeFetch(...args),
  confirm: () => confirmReturn,
  addEventListener: () => {},
};

vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'app.js' });

const App = sandbox.__TransferStageApp;
if (!App) {
  console.log('FAIL_NO_CLASS');
  process.exit(1);
}

function makeApp(deviceState) {
  const app = Object.create(App.prototype);
  app.deviceState = deviceState;
  app.showToast = () => {};
  return app;
}

async function main() {
  const failures = [];

  // Case 1: unsaved data present, operator confirms save -> save_log is
  // dispatched (with a file name) before stop_monitoring.
  {
    calls = [];
    confirmReturn = true;
    promptReturn = 'myrun.csv';
    const app = makeApp({
      'Red Percent Window': {
        pending_run_data: { has_data: true, sample_count: 42, run_id: 'r1' },
      },
    });
    await app.dispatchCommand('Red Percent Window', 'stop_monitoring');
    const commands = calls.map((c) => c.body && c.body.command);
    const saveIdx = commands.indexOf('save_log');
    const stopIdx = commands.indexOf('stop_monitoring');
    if (saveIdx === -1) failures.push('case1: expected save_log to be dispatched');
    if (stopIdx === -1) failures.push('case1: expected stop_monitoring to be dispatched');
    if (saveIdx !== -1 && stopIdx !== -1 && saveIdx > stopIdx) {
      failures.push('case1: save_log must be dispatched before stop_monitoring');
    }
    const saveCall = calls.find((c) => c.body && c.body.command === 'save_log');
    if (!saveCall || !Array.isArray(saveCall.body.args) || saveCall.body.args[0] !== 'myrun.csv') {
      failures.push('case1: expected save_log dispatched with the chosen file name');
    }
  }

  // Case 2: unsaved data present, operator declines to save now -> only
  // stop_monitoring goes out, never save_log. (The model still autosaves
  // on its own by default - D-10 - so nothing is destroyed either way.)
  {
    calls = [];
    confirmReturn = false;
    const app = makeApp({
      'Red Percent Window': {
        pending_run_data: { has_data: true, sample_count: 3, run_id: 'r2' },
      },
    });
    await app.dispatchCommand('Red Percent Window', 'stop_monitoring');
    const commands = calls.map((c) => c.body && c.body.command);
    if (commands.includes('save_log')) failures.push('case2: save_log must not be dispatched when operator declines');
    if (!commands.includes('stop_monitoring')) failures.push('case2: stop_monitoring must still be dispatched');
  }

  // Case 3: no unsaved data -> no prompt at all, straight to stop_monitoring.
  {
    calls = [];
    let confirmCalled = false;
    sandbox.confirm = () => { confirmCalled = true; return true; };
    const app = makeApp({
      'Red Percent Window': {
        pending_run_data: { has_data: false, sample_count: 0, run_id: null },
      },
    });
    await app.dispatchCommand('Red Percent Window', 'stop_monitoring');
    sandbox.confirm = () => confirmReturn;
    const commands = calls.map((c) => c.body && c.body.command);
    if (confirmCalled) failures.push('case3: must not prompt when there is no unsaved data');
    if (!commands.includes('stop_monitoring')) failures.push('case3: stop_monitoring must still be dispatched');
  }

  if (failures.length) {
    console.log('FAIL: ' + failures.join(' | '));
    process.exit(1);
  }
  console.log('OK');
  process.exit(0);
}

main();
