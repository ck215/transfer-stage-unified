'use strict';
// Harness for test_web_ui_js.py::test_shared_fetch_wrapper_aborts_a_hung_request.
//
// Loads the real app.js source into a minimal sandbox and exercises the
// shared fetch wrapper (the attachSessionToken IIFE, WEB-22) against a real
// TCP server that accepts the connection and never responds - simulating a
// stalled request. window.__FETCH_TIMEOUT_MS_OVERRIDE__ shrinks the
// wrapper's timeout so this doesn't have to wait out the real 20s default.
const http = require('http');
const vm = require('vm');
const fs = require('fs');

const appJsPath = process.argv[2];
const source = fs.readFileSync(appJsPath, 'utf8');

const server = http.createServer(() => {
  // Deliberately never call res.end() / res.write() - the connection just
  // sits open, as a stalled hardware/server response would.
});

server.listen(0, '127.0.0.1', () => {
  const port = server.address().port;
  const origin = `http://127.0.0.1:${port}`;

  const sandbox = {
    console,
    AbortController,
    Headers,
    setTimeout,
    clearTimeout,
    fetch,
  };
  sandbox.document = { querySelector: () => null };
  sandbox.window = {
    location: { origin },
    fetch,
    addEventListener: () => {},
    __FETCH_TIMEOUT_MS_OVERRIDE__: 50,
  };

  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'app.js' });

  const started = Date.now();
  sandbox.window.fetch(origin + '/api/state').then(
    () => {
      console.log('FAIL_RESOLVED_INSTEAD_OF_ABORTING');
      server.close();
      process.exit(1);
    },
    (err) => {
      const elapsed = Date.now() - started;
      server.close();
      if (err && err.name === 'AbortError' && elapsed < 2000) {
        console.log('OK aborted after ' + elapsed + 'ms');
        process.exit(0);
      } else {
        console.log('FAIL_WRONG_REJECTION name=' + (err && err.name) + ' elapsed=' + elapsed);
        process.exit(1);
      }
    }
  );

  setTimeout(() => {
    console.log('FAIL_TIMEOUT_NO_SETTLE');
    server.close();
    process.exit(2);
  }, 5000);
});
