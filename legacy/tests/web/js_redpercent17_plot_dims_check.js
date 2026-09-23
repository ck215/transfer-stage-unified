'use strict';
// Harness for
// test_redpercent17_plot_dims.py::test_plot_dialog_dim_pickers_populate_and_post.
//
// REDPERCENT-17 (Web renderer half): '/api/plot' picked the first N header
// dims in file order with no way for the operator to choose, and showed a
// generic "Failed to generate plot" toast on any server error -- including
// the blank-PNG case a 2D/3D request with a missing dimension used to
// produce. This proves app.js now: (1) parses the uploaded CSV's own
// header to populate the three dim pickers the moment a file is chosen,
// (2) sends the operator's picks as dim1/dim2/dim3 on Generate, and
// (3) surfaces the server's actual message on failure instead of one
// fixed string. Same vm-sandbox + Object.create(prototype) technique as
// js_web13_stop_monitoring_save_check.js.
const vm = require('vm');
const fs = require('fs');

const appJsPath = process.argv[2];
const source = fs.readFileSync(appJsPath, 'utf8')
  + '\nglobalThis.__TransferStageApp = TransferStageApp;\n';

const CSV_TEXT = [
  '# Probe Name,TestProbe',
  '',
  'Red Percent,Stepper X Location,Stepper Y Location',
  '10.5,1.0,2.0',
  '20.5,3.0,4.0',
].join('\n');

let fetchCalls = [];
let fetchResult = { image_base64: 'AAAA', dims: ['X', 'Y'] };
function fakeFetch(url, opts) {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  fetchCalls.push({ url, body });
  return Promise.resolve({ ok: true, status: 200, json: async () => fetchResult });
}

function fakeElement(overrides) {
  const children = [];
  return Object.assign({
    dataset: {},
    classList: { add: () => {}, remove: () => {}, contains: () => false, toggle: () => {} },
    value: '',
    files: [],
    _listeners: {},
    get firstChild() { return children.length ? children[0] : null; },
    removeChild(child) {
      const idx = children.indexOf(child);
      if (idx !== -1) children.splice(idx, 1);
    },
    appendChild(child) { children.push(child); },
    get _children() { return children; },
  }, overrides);
}

let toasts = [];

class FakeFileReader {
  readAsText(file) {
    // Synchronous on purpose -- the harness controls ordering explicitly,
    // there is no real async I/O to wait on.
    this.result = file.content;
    if (this.onload) this.onload({ target: { result: file.content } });
  }
}

const elements = {};
function elementFor(id) {
  if (!elements[id]) elements[id] = fakeElement({ id });
  return elements[id];
}

const sandbox = {
  console,
  AbortController,
  Headers,
  setTimeout,
  clearTimeout,
  FileReader: FakeFileReader,
  fetch: (...args) => fakeFetch(...args),
  confirm: () => true,
  prompt: () => null,
  document: {
    getElementById: (id) => elementFor(id),
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => fakeElement({}),
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  const failures = [];
  const app = Object.create(App.prototype);
  app.showToast = (msg, kind) => toasts.push({ msg, kind });

  // dispatchCommand('plot_data_ui') wires the file-chosen and Generate
  // handlers onto the (fake) DOM elements it looks up by id.
  app.dispatchCommand('Red Percent Window', 'plot_data_ui');

  const fileInput = elementFor('plot-csv-upload');
  const dim1Select = elementFor('plot-dim1-select');
  const dim2Select = elementFor('plot-dim2-select');
  const dim3Select = elementFor('plot-dim3-select');
  const typeSelect = elementFor('plot-type-select');
  const outImg = elementFor('plot-output-img');

  if (typeof fileInput.onchange !== 'function') {
    failures.push('plot_data_ui must attach an onchange handler to plot-csv-upload');
  } else {
    fileInput.files = [{ content: CSV_TEXT }];
    fileInput.onchange();

    if (dim1Select._children.length !== 3) { // "(auto)" + X + Y
      failures.push(`dim1 select: expected 3 options (auto+X+Y), got ${dim1Select._children.length}`);
    }
    const dim1Values = dim1Select._children.map(c => c.value);
    if (!dim1Values.includes('X') || !dim1Values.includes('Y')) {
      failures.push(`dim pickers: expected X and Y as options, got ${JSON.stringify(dim1Values)}`);
    }
  }

  // Pick dim1=Y, dim2=X (deliberately reversed from file order) and
  // generate a 2D plot -- the POST body must carry exactly that pick,
  // not file order.
  typeSelect.value = '2D';
  dim1Select.value = 'Y';
  dim2Select.value = 'X';

  const btnGen = elementFor('btn-generate-plot');
  if (typeof btnGen.onclick !== 'function') {
    failures.push('plot_data_ui must attach an onclick handler to btn-generate-plot');
  } else {
    fetchCalls = [];
    fetchResult = { image_base64: 'AAAA', dims: ['X', 'Y'] };
    fileInput.files = [{ content: CSV_TEXT }];
    await btnGen.onclick();
    await sleep(20);

    if (fetchCalls.length !== 1 || fetchCalls[0].url !== '/api/plot') {
      failures.push(`expected one POST to /api/plot, got ${JSON.stringify(fetchCalls.map(c => c.url))}`);
    } else {
      const sentBody = fetchCalls[0].body;
      if (sentBody.dim1 !== 'Y' || sentBody.dim2 !== 'X') {
        failures.push(`expected dim1=Y dim2=X in the request, got dim1=${sentBody.dim1} dim2=${sentBody.dim2}`);
      }
      if (sentBody.plot_type !== '2D') {
        failures.push(`expected plot_type=2D, got ${sentBody.plot_type}`);
      }
    }
    if (outImg.src !== 'data:image/png;base64,AAAA') {
      failures.push(`expected plot image to be set from image_base64, got ${outImg.src}`);
    }

    // Server-reported failure: the actual message must reach the toast,
    // not a fixed generic string.
    toasts = [];
    fetchResult = { status: 'error', message: 'No samples with both Y and X recorded alongside Red Percent.' };
    await btnGen.onclick();
    await sleep(20);
    const lastToast = toasts[toasts.length - 1];
    if (!lastToast || lastToast.msg !== fetchResult.message) {
      failures.push(`expected the server's own message in the failure toast, got ${JSON.stringify(lastToast)}`);
    }
  }

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
