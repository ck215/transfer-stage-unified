'use strict';
// Harness for
// test_redpercent13_web_plotter.py::test_pollstate_gates_plotter_sample_on_monitoring
// and ::test_plotter_reset_dispatches_reset_baseline_to_the_model.
//
// Same vm-sandbox technique as js_web13_stop_monitoring_save_check.js and
// js_rotator13_connection_gate_check.js: load the real app.js source, then
// drive TransferStageApp.prototype methods directly rather than standing up
// a fake DOM to run the real constructor.
//
// REDPERCENT-13: the Web plotter sampled `current_red` on every poll
// regardless of whether a run was active, and its "Reset" button only ever
// touched client-side state, never the model's own `reset_baseline`. This
// harness proves both are fixed: `pollState` only calls `pushPlotterSample`
// while the model publishes `monitoring: true`, and the Reset click handler
// dispatches `reset_baseline` to the model.
const vm = require('vm');
const fs = require('fs');

const appJsPath = process.argv[2];
const source = fs.readFileSync(appJsPath, 'utf8')
  + '\nglobalThis.__TransferStageApp = TransferStageApp;\n';

let fetchResponse = {};
let dispatchCalls;

function fakeFetch(url, opts) {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  dispatchCalls.push({ url, body });
  if (url === '/api/state' || (typeof url === 'string' && url.endsWith('/api/state'))) {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => fetchResponse,
    });
  }
  return Promise.resolve({
    ok: true,
    status: 200,
    json: async () => ({ status: 'ok', code: 200, result: 'ok' }),
  });
}

const sandbox = {
  console,
  AbortController,
  Headers,
  setTimeout,
  clearTimeout,
  fetch: (...args) => fakeFetch(...args),
  confirm: () => true,
  prompt: () => null,
  document: {
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
  },
};
sandbox.window = {
  location: { origin: 'http://127.0.0.1:0' },
  fetch: (...args) => fakeFetch(...args),
  confirm: () => true,
  addEventListener: () => {},
};

vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'app.js' });

const App = sandbox.__TransferStageApp;
if (!App) {
  console.log('FAIL_NO_CLASS');
  process.exit(1);
}

function makeApp() {
  const app = Object.create(App.prototype);
  app.dom = {};
  app.deviceState = {};
  app.knownDevices = undefined;
  app.deviceStaleCounts = undefined;
  app.plotterData = { timestamps: [], currentRed: [], deltaRed: [] };
  app.maxPlotPoints = 60;
  app.baselineRed = 0.0;
  app.peakRed = 0.0;
  app.showToast = () => {};
  return app;
}

async function main() {
  const failures = [];

  // -- gating on `monitoring` ------------------------------------------

  {
    dispatchCalls = [];
    const app = makeApp();
    const pushed = [];
    app.pushPlotterSample = (v) => pushed.push(v);
    fetchResponse = {
      'Red Percent Window': { current_red: 12.5, monitoring: true },
    };
    await app.pollState();
    if (pushed.length !== 1 || pushed[0] !== 12.5) {
      failures.push('monitoring=true: expected pushPlotterSample(12.5) once, got ' + JSON.stringify(pushed));
    }
  }

  {
    dispatchCalls = [];
    const app = makeApp();
    const pushed = [];
    app.pushPlotterSample = (v) => pushed.push(v);
    fetchResponse = {
      'Red Percent Window': { current_red: 12.5, monitoring: false },
    };
    await app.pollState();
    if (pushed.length !== 0) {
      failures.push('monitoring=false: expected no pushPlotterSample calls, got ' + JSON.stringify(pushed));
    }
  }

  // -- Reset dispatches to the model ------------------------------------

  {
    dispatchCalls = [];
    const app = makeApp();
    let btnHandler = null;
    const fakeBtn = { addEventListener: (evt, cb) => { if (evt === 'click') btnHandler = cb; } };
    app.dom.btnPlotterReset = fakeBtn;
    app.plotterData.currentRed.push(7.5);

    app.initEventListeners();
    if (!btnHandler) {
      failures.push('reset: click handler was never bound to dom.btnPlotterReset');
    } else {
      await btnHandler();
      const commands = dispatchCalls
        .filter((c) => c.body)
        .map((c) => c.body.command);
      if (!commands.includes('reset_baseline')) {
        failures.push('reset: expected reset_baseline to be dispatched to the model, got ' + JSON.stringify(commands));
      }
    }
  }

  if (failures.length) {
    console.log('FAIL: ' + failures.join(' | '));
    process.exit(1);
  }
  console.log('OK');
  process.exit(0);
}

main();
