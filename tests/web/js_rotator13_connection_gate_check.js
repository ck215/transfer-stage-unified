'use strict';
// Harness for test_web_ui_js.py::test_disconnected_device_controls_are_disabled_and_noted.
//
// Loads the real app.js source into a minimal sandbox (same technique as
// js_fetch_timeout_check.js) and exercises TransferStageApp._applyConnectionGate
// directly - the ROTATOR-13 web-half fix: a device reporting connection_status
// "disconnected" (the rotator with a SIM/None port chief among them - it has
// no simulator to fall back on) must have its card controls force-disabled
// and a visible reason shown, not just an easy-to-miss badge change.
//
// The class constructor touches a couple dozen document.getElementById
// calls and wires event listeners - standing up a full fake DOM for that
// is exactly the "framework project" the brief says to stop short of. This
// sidesteps it: the class is only ever *defined* by loading the source (the
// constructor never runs), and the instance under test is built with
// Object.create(TransferStageApp.prototype) so _applyConnectionGate (and
// the sanitizeId it calls) are reachable without ever constructing one.
const vm = require('vm');
const fs = require('fs');

const appJsPath = process.argv[2];
// `class TransferStageApp` is a top-level lexical (let-like) declaration -
// unlike `var`/`function`, it is never installed as a property of the
// vm context's global object, so it has to be exported explicitly.
const source = fs.readFileSync(appJsPath, 'utf8')
  + '\nglobalThis.__TransferStageApp = TransferStageApp;\n';

const notesById = {};

function makeNoteElement() {
  return { textContent: '', style: {} };
}

const sandbox = {
  console,
  AbortController,
  Headers,
  setTimeout,
  clearTimeout,
  fetch,
  document: {
    getElementById: (id) => {
      if (id.startsWith('note-')) {
        if (!notesById[id]) notesById[id] = makeNoteElement();
        return notesById[id];
      }
      return null;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
  },
};
sandbox.window = {
  location: { origin: 'http://127.0.0.1:0' },
  fetch,
  addEventListener: () => {},
};

vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'app.js' });

const App = sandbox.__TransferStageApp;
if (!App) {
  console.log('FAIL_NO_CLASS');
  process.exit(1);
}

const app = Object.create(App.prototype);

function makeCardBody() {
  const controls = [{ disabled: false }, { disabled: false }, { disabled: false }];
  return { controls, cardBody: { querySelectorAll: () => controls } };
}

const failures = [];

function check(devName, status, expectDisabled) {
  const { controls, cardBody } = makeCardBody();
  const result = app._applyConnectionGate(devName, cardBody, status);
  const note = sandbox.document.getElementById(`note-${app.sanitizeId(devName)}`);
  const allDisabled = controls.every((c) => c.disabled === true);
  const noteVisible = note.style.display !== 'none' && note.textContent.length > 0;

  if (expectDisabled) {
    if (result !== true) failures.push(`${devName}/${status}: expected gate return true`);
    if (!allDisabled) failures.push(`${devName}/${status}: expected every control disabled`);
    if (!noteVisible) failures.push(`${devName}/${status}: expected a visible note`);
  } else {
    if (result !== false) failures.push(`${devName}/${status}: expected gate return false`);
    if (allDisabled) failures.push(`${devName}/${status}: controls must not be force-disabled`);
    if (noteVisible) failures.push(`${devName}/${status}: note must be hidden`);
  }
}

check('SMC100 Rotator', 'disconnected', true);
check('SMC100 Rotator', 'offline', true);
check('SMC100 Rotator', 'lost', true);
check('SMC100 Rotator', 'closed', true);
check('SMC100 Rotator', 'hardware', false);
check('SMC100 Rotator', 'simulated', false);
check('SMC100 Rotator', 'unverified', false);

if (failures.length) {
  console.log('FAIL: ' + failures.join(' | '));
  process.exit(1);
}
console.log('OK');
process.exit(0);
