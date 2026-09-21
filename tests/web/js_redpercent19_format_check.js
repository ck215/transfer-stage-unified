'use strict';
// Harness for test_redpercent19_web_format_schema.py::test_format_honored_in_pollstate
//
// REDPERCENT-19: app.js pollState writes String(val) for readonly numerics with no
// formatting step. The model declares current_red/red_change with format=".2f"
// in ui_schema. This harness proves pollState now reads and applies the format
// key when present.

const vm = require('vm');
const fs = require('fs');

const appJsPath = process.argv[2];
const source = fs.readFileSync(appJsPath, 'utf8')
  + '\nglobalThis.__TransferStageApp = TransferStageApp;\n';

let fetchResponse = {};
let mockElements = {};
let loggingEnabled = false;

function fakeFetch(url, opts) {
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
    getElementById: (id) => {
      if (loggingEnabled) {
        console.error(`[getElementById] ${id}`);
      }
      // Return a mock element that tracks innerText writes
      if (!mockElements[id]) {
        const el = { _innerText: '' };
        Object.defineProperty(el, 'innerText', {
          get() { return this._innerText; },
          set(val) {
            if (loggingEnabled) {
              console.error(`  -> innerText = ${val}`);
            }
            this._innerText = val;
          },
        });
        mockElements[id] = el;
      }
      return mockElements[id];
    },
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

function makeApp() {
  const app = Object.create(App.prototype);
  app.dom = {};
  app.deviceState = {};
  app.knownDevices = undefined;
  app.deviceStaleCounts = undefined;
  app.devices = {
    'Red Percent Window': {
      sections: [
        {
          elements: [
            {
              type: 'readonly',
              model_attr: 'current_red',
              text: 'Current Red %:',
              format: '.2f',
            },
            {
              type: 'readonly',
              model_attr: 'red_change',
              text: 'Red Change %:',
              format: '.2f',
            },
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
  return app;
}

async function main() {
  const failures = [];

  // Test 1: Format ".2f" is applied to readonly numeric values
  {
    mockElements = {};  // Reset elements for this test
    const app = makeApp();

    fetchResponse = {
      'Red Percent Window': {
        current_red: 12.3456789,
        red_change: 0.0,
        monitoring: false,
      },
    };

    try {
      loggingEnabled = false;  // Disable logging for cleaner output
      await app.pollState();
    } catch (e) {
      failures.push(`pollState threw: ${e.message}`);
    }

    const currentRedId = 'val-Red_Percent_Window-current_red';
    const redChangeId = 'val-Red_Percent_Window-red_change';

    const currentRedText = mockElements[currentRedId]?._innerText || '';
    const redChangeText = mockElements[redChangeId]?._innerText || '';

    // .2f format means 2 decimal places
    if (currentRedText !== '12.35') {
      failures.push(`current_red formatting: expected '12.35', got '${currentRedText}'`);
    }
    // JavaScript's toFixed for 0.0 returns "0.00"
    if (redChangeText !== '0.00') {
      failures.push(`red_change formatting: expected '0.00', got '${redChangeText}'`);
    }
  }

  // Test 2: No format key falls back to String(val)
  {
    mockElements = {};  // Reset elements for this test
    const app = makeApp();
    app.devices['Test Device'] = {
      sections: [
        {
          elements: [
            {
              type: 'readonly',
              model_attr: 'some_value',
              text: 'Some Value:',
              // No format key
            },
          ],
        },
      ],
    };

    fetchResponse = {
      'Test Device': {
        some_value: 12.3456789,
      },
    };

    try {
      loggingEnabled = false;
      await app.pollState();
    } catch (e) {
      failures.push(`pollState threw in test 2: ${e.message}`);
    }

    const valueId = 'val-Test_Device-some_value';
    const valueText = mockElements[valueId]?._innerText || '';

    if (valueText !== '12.3456789') {
      failures.push(`no format fallback: expected '12.3456789', got '${valueText}'`);
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
