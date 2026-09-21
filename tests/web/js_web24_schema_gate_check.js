'use strict';
// Harness for test_web24_schema_gate.py::test_pollstate_gates_from_schema_not_labels.
//
// WEB-24: pollState's interlock block used to re-derive "mode" from raw
// polled flags (auton_flag/manual_flag/system_enabled) and find controls by
// innerText.includes('Full Stop'/'Enable'/'Power Down') instead of reading
// the schema's own enabled_when/disabled_when -- the one contract Tk's
// _sync_gates (schema.is_enabled) and PySide's gate walker both read. This
// proves the Web client now gates the same way: from _findSchemaElementFor
// Control + _modeNameFor + _isEnabled, and that a renamed button label
// changes nothing about what gets disabled.
const vm = require('vm');
const fs = require('fs');

const appJsPath = process.argv[2];
const source = fs.readFileSync(appJsPath, 'utf8')
  + '\nglobalThis.__TransferStageApp = TransferStageApp;\n';

let fetchResponse = {};
function fakeFetch(url) {
  if (typeof url === 'string' && url.endsWith('/api/state')) {
    return Promise.resolve({ ok: true, status: 200, json: async () => fetchResponse });
  }
  return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: 'ok' }) });
}

function fakeControl(overrides) {
  // A real element always has .dataset/.classList/.options (even empty),
  // and pollState's toggle/select branches read all three unconditionally
  // for every id getElementById returns -- missing any one throws inside
  // pollState's own try/catch, which swallows it and aborts the whole
  // poll silently (the same fake-DOM gap that made REDPERCENT-19's first
  // harness look like a "loop only processes one attribute" VM bug).
  return Object.assign({
    dataset: {},
    disabled: false,
    innerText: '',
    value: '',
    placeholder: '',
    options: [],
    classList: { add: () => {}, remove: () => {}, contains: () => false, toggle: () => {} },
  }, overrides);
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
    getElementById: () => fakeControl({}),
  },
};
sandbox.window = {
  location: { origin: 'http://127.0.0.1:0' },
  fetch: (...args) => fakeFetch(...args),
  confirm: () => true,
  addEventListener: () => {},
};

vm.createContext(sandbox);

try {
  vm.runInContext(source, sandbox, { filename: 'app.js' });
} catch (e) {
  console.log('FAIL_LOAD: ' + e.message);
  process.exit(1);
}

const App = sandbox.__TransferStageApp;
if (!App) {
  console.log('FAIL_NO_CLASS');
  process.exit(1);
}

function makeApp(controls, cardBody) {
  const app = Object.create(App.prototype);
  app.dom = {};
  app.deviceState = {};
  app.knownDevices = undefined;
  app.deviceStaleCounts = undefined;
  app.devices = {
    'Probe X': {
      sections: [
        {
          elements: [
            { type: 'entry', model_attr: 'x_step', disabled_when: ['autonomous', 'manual'] },
            { type: 'toggle', model_attr: 'auton_flag', command: 'toggle_auton' },
            { type: 'toggle', model_attr: 'manual_flag', command: 'toggle_manual' },
            { type: 'button', command: 'macro_start_auton', disabled_when: ['autonomous', 'manual'] },
            { type: 'button', command: 'clear_estop' },
            // Deliberately labeled "Full Stop" like the removed per-device
            // control the old code text-matched, with the same
            // disabled_when as Start Stepping -- the point is that
            // renaming its label changes nothing about its gate.
            { type: 'button', command: 'legacy_named_full_stop', disabled_when: ['autonomous', 'manual'] },
          ],
        },
      ],
    },
  };
  app.setConnectionState = () => {};
  app._markAllKnownDevicesMissedCycle = () => {};
  app._markDeviceSeen = () => {};
  app._markDeviceMissedCycle = () => {};
  app._applyConnectionGate = () => {};
  app.updateDeviceStatusBadges = () => {};
  sandbox.document.querySelector = (sel) => (sel.indexOf('.card-body') !== -1 ? cardBody : null);
  return app;
}

async function pollWith(app, attrs) {
  fetchResponse = { 'Probe X': attrs };
  await app.pollState();
}

async function main() {
  const failures = [];

  const setBtn = fakeControl({ dataset: { attr: 'x_step' } });
  const autonToggle = fakeControl({ dataset: { command: 'toggle_auton', attr: 'auton_flag' } });
  const manualToggle = fakeControl({ dataset: { command: 'toggle_manual', attr: 'manual_flag' } });
  const startBtn = fakeControl({ dataset: { command: 'macro_start_auton' }, innerText: 'Start Stepping' });
  const clearBtn = fakeControl({ dataset: { command: 'clear_estop' }, innerText: 'Clear FULL STOP' });
  const stopBtn = fakeControl({ dataset: { command: 'legacy_named_full_stop' }, innerText: 'Full Stop' });
  const controls = [setBtn, autonToggle, manualToggle, startBtn, clearBtn, stopBtn];
  const cardBody = { querySelectorAll: () => controls };

  const app = makeApp(controls, cardBody);

  // Idle: gated controls (x_step Set, Start Stepping) enabled; ungated
  // controls (toggles, Clear FULL STOP) enabled; the "Full Stop"-labeled
  // button gated on "monitoring" (never true here) stays enabled too.
  await pollWith(app, { auton_flag: false, manual_flag: false, connection_status: 'hardware' });
  if (setBtn.disabled) failures.push('idle: x_step Set should be enabled');
  if (startBtn.disabled) failures.push('idle: Start Stepping should be enabled');
  if (autonToggle.disabled) failures.push('idle: auton toggle (ungated) should be enabled');
  if (clearBtn.disabled) failures.push('idle: Clear FULL STOP (ungated) should be enabled');
  if (stopBtn.disabled) failures.push('idle: legacy "Full Stop"-labeled button should be enabled (its own disabled_when does not name "idle")');

  // Autonomous: x_step Set, Start Stepping, and the "Full Stop"-labeled
  // button (it shares the same disabled_when -- its label is irrelevant)
  // must all disable; toggles and the ungated Clear FULL STOP button
  // must not.
  await pollWith(app, { auton_flag: true, manual_flag: false, connection_status: 'hardware' });
  if (!setBtn.disabled) failures.push('autonomous: x_step Set should be disabled');
  if (!startBtn.disabled) failures.push('autonomous: Start Stepping should be disabled');
  if (!stopBtn.disabled) failures.push('autonomous: "Full Stop"-labeled button should be disabled (its disabled_when names "autonomous", regardless of its label)');
  if (autonToggle.disabled) failures.push('autonomous: auton toggle (ungated) should stay enabled');
  if (clearBtn.disabled) failures.push('autonomous: Clear FULL STOP (ungated) should stay enabled');

  // Manual: same gated pair disables under "manual" too.
  await pollWith(app, { auton_flag: false, manual_flag: true, connection_status: 'hardware' });
  if (!setBtn.disabled) failures.push('manual: x_step Set should be disabled');
  if (!startBtn.disabled) failures.push('manual: Start Stepping should be disabled');
  if (manualToggle.disabled) failures.push('manual: manual toggle (ungated) should stay enabled');

  // Rename the "Full Stop"-labeled button's own label to something
  // unrelated, but keep the SAME schema element (same command, same
  // gate). Old app.js decided "is this the stop button" by
  // ctrl.innerText.includes('Full Stop') and force-re-enabled it during
  // autonomous/manual regardless of its own disabled_when -- so the old
  // logic's outcome for this exact button depended on its label, and a
  // rename would flip it. The new logic must produce the *same* outcome
  // (still disabled) before and after the rename, because it never reads
  // the label at all.
  stopBtn.innerText = 'Do The Thing Now';
  await pollWith(app, { auton_flag: true, manual_flag: false, connection_status: 'hardware' });
  if (!setBtn.disabled) failures.push('rename: x_step Set should still be disabled in autonomous mode');
  if (!startBtn.disabled) failures.push('rename: Start Stepping should still be disabled in autonomous mode');
  if (!stopBtn.disabled) failures.push('rename: renamed button should still be disabled in autonomous mode -- gating must not depend on its label text');

  if (failures.length) {
    console.log('FAIL: ' + failures.join(' | '));
    process.exit(1);
  }
  console.log('OK');
  process.exit(0);
}

main().catch(err => {
  console.log('FAIL_ASYNC: ' + err.message);
  process.exit(1);
});
