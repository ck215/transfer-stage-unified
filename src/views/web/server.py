"""The Web frontend: one HTTP server, one JSON handler, one watchdog.

    browser  <-- JSON -->  ApiHandler  -->  Controller / Setup
                               ^
                            WebView (server thread + heartbeat watchdog)

`ApiHandler` is a thin wrapper: every route is one of the three calls a
desktop view makes (`schema`, `state`, `run`) plus the handful the Dashboard
makes (`estop_all`, `remove`, `reopen`). The 904-line `WebModelAdapter` is
gone - it was a second, drifting copy of the view logic, and every bug it
had was a bug the desktop views did not share.

Two things live here and nowhere else:

* **The security boundary.** Owner ruling 2026-09-21: localhost only. The
  socket binds 127.0.0.1, every request must come from a loopback client,
  and every state-changing route must additionally carry a JSON content
  type and an `Origin`/`Host` naming this server - a page open in the same
  browser can still POST to localhost, which is what MANAGER-15 / WEB-10
  are about. The per-launch session token is purged with the owner ruling:
  it defended a boundary that no longer exists.
* **Browser liveness (D-8, WEB-19 / WEB-23).** The browser tab checks in on
  `POST /api/heartbeat`; a watchdog here warns after `WARN_SECONDS` of
  silence and FULL STOPs after `STOP_SECONDS`, but only while the station is
  actually doing something and only once a client has checked in at all.
  It used to be a private copy inside `BaseProbe`, plus a mixin bolted onto
  the heater and the rotator - three watchdogs, three sets of timings, and
  a Tk session that carried them for no reason. One station, one watchdog.
"""
import base64
import errno
import http.server
import json
import mimetypes
import os
import threading
import time
import webbrowser
from urllib.parse import parse_qs, urlparse

import schema as sch
from events import events
from result import Refused
from views import theme

#: `name` that targets the Setup panel instead of a model.
SETUP_NAME = "__setup__"

SOURCE = "Web"

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_LOOPBACK = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"})


class ApiHandler(http.server.BaseHTTPRequestHandler):
    """Thin JSON wrapper over the same Controller calls the desktop views make.

    The server instance carries the `WebView`, so nothing here is class-level
    state. The old handler kept its adapter on the class, which meant a log
    line written before `start()` re-pointed it landed in an object nothing
    read (ERRORS-12).
    """

    #: A body here is always the small JSON this API accepts. The read used to
    #: be `Content-Length` bytes, unbounded (WEB-21).
    MAX_BODY_BYTES = 1 * 1024 * 1024
    #: Routes polled every cycle: logged at debug once a second, not per call.
    POLLED = frozenset({"/api/state", "/api/events", "/api/data", "/api/screen"})

    server_version = "station"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- what the handler holds -------------------------------------------
    @property
    def view(self):
        return getattr(self.server, "view", None)

    @property
    def controller(self):
        return self.view.controller

    # -- the two verbs ----------------------------------------------------
    def do_GET(self):
        """Every route body runs under this envelope (WEB-14).

        An unhandled exception used to propagate out of
        `BaseHTTPRequestHandler` and drop the connection with no response at
        all, which the browser reports as a bare network failure - identical
        to the server being down.
        """
        self._started = time.monotonic()
        try:
            self._route_get()
        except Exception as exc:
            self._send_error_json(exc)

    def do_POST(self):
        self._started = time.monotonic()
        try:
            self._route_post()
        except Exception as exc:
            self._send_error_json(exc)

    # -- routing ----------------------------------------------------------
    def _route_get(self):
        parsed = urlparse(self.path)
        route, query = parsed.path, parse_qs(parsed.query)
        if not self._is_local(require_json=False, require_origin=route in
                              ("/api/screen", "/api/file")):
            return

        if route == "/api/state":
            return self._send_json(200, self.controller.state())

        if route == "/api/schema":
            name = self._one(query, "name")
            if name == SETUP_NAME:
                return self._send_json(200, self.view.setup.schema)
            if name not in self.controller.model_names:
                return self._send_json(404, {"status": "error",
                                             "reason": f"{name} is not open"})
            return self._send_json(200, self.controller.schema(name))

        if route == "/api/setup":
            return self._send_json(200, {"schema": self.view.setup.schema,
                                         "state": self.view.setup.state})

        if route == "/api/events":
            since = self._int(self._one(query, "since"), 0)
            # Non-destructive (ERRORS-2, WEB-17): the route used to pop the
            # queue, so with two tabs open whichever polled first consumed
            # the event and the other never saw it.
            return self._send_json(200, {
                "events": [e.to_dict() for e in events.since(since)],
                "latest_id": events.latest_id})

        if route == "/api/theme.css":
            return self._send_bytes(200, "text/css; charset=utf-8",
                                    theme.css_variables().encode("utf-8"))

        if route == "/api/data":
            return self._send_data(self._one(query, "name"),
                                   self._one(query, "command"))

        if route == "/api/file":
            # A save command may declare inputs like any other, so the entry
            # values travel with this one too (D-5) - as JSON in the query,
            # since the download has to be a GET the browser can stream.
            try:
                inputs = json.loads(self._one(query, "inputs", "{}"))
            except ValueError:
                inputs = {}
            return self._send_file(self._one(query, "name"),
                                   self._one(query, "command"),
                                   inputs if isinstance(inputs, dict) else {})

        if route == "/api/screen":
            return self._send_screen(self._one(query, "name"))

        if route.startswith("/api/"):
            return self._send_json(404, {"status": "error",
                                         "reason": f"no route {route}"})
        return self._serve_static(route)

    def _route_post(self):
        route = urlparse(self.path).path
        if not self._is_local(require_json=True):
            return
        ok, body = self._read_body()
        if not ok:
            return
        if not isinstance(body, dict):
            return self._send_json(400, {"status": "error",
                                         "reason": "body must be a JSON object"})

        if route == "/api/run":
            inputs = body.get("inputs") or {}
            args = body.get("args") or []
            if not isinstance(inputs, dict) or not isinstance(args, list):
                return self._send_json(400, {
                    "status": "error",
                    "reason": "inputs must be an object and args a list"})
            result = self._run(body.get("name"), body.get("command"),
                               inputs, tuple(args))
            return self._send_json(200, self._result_dict(result))

        if route == "/api/options":
            return self._send_options(body.get("name"), body.get("command"))

        if route == "/api/estop_all":
            results = self.controller.estop_all()
            return self._send_json(200, {
                "status": "ok", "confirmed": results,
                "unconfirmed": sorted(n for n, ok in results.items() if not ok),
                "is_estopped": self.controller.is_estopped})

        if route == "/api/clear_estop_all":
            result = self.controller.clear_estop_all(
                confirmed=bool(body.get("confirmed")))
            return self._send_json(200, self._result_dict(result))

        if route == "/api/close_model":
            name = body.get("name")
            closed = self.controller.remove(name)
            return self._send_json(200 if closed else 404, {
                "status": "ok" if closed else "error",
                "reason": "" if closed else f"{name} is not open"})

        if route == "/api/open_model":
            name = body.get("name")
            try:
                self.controller.reopen(name)
            except Exception as exc:
                events.warn("Reopen Failed", f"{name}: {exc}", source=SOURCE,
                            exception=exc)
                return self._send_json(409, {"status": "error",
                                             "reason": str(exc)})
            return self._send_json(200, {"status": "ok", "reason": ""})

        if route == "/api/heartbeat":
            self.view.beat()
            return self._send_json(200, {"status": "ok",
                                         "age": self.view.heartbeat_age})

        return self._send_json(404, {"status": "error",
                                     "reason": f"no route {route}"})

    # -- the three calls a view makes -------------------------------------
    def _run(self, name, command, inputs=None, args=()):
        """`__setup__` targets the Setup panel; anything else is a model."""
        if name == SETUP_NAME:
            return self.view.setup.run(command, inputs, args)
        return self.controller.run(name, command, inputs, args)

    @staticmethod
    def _result_dict(result):
        """`Result.to_dict()` plus what a needs_confirm re-run needs.

        `PanelView` re-runs `result.command` with `result.inputs` and
        `(*result.args, True)`; `to_dict()` carries neither, so a Web client
        could only ever re-run with `[True]` and would drop the arguments the
        original command was called with. See CORE CHANGE REQUESTS.
        """
        data = result.to_dict()
        data["inputs"] = dict(result.inputs or {})
        data["args"] = list(result.args or ())
        return data

    def _send_options(self, name, command):
        try:
            if name == SETUP_NAME:
                options = self.view.setup.options(command)
            else:
                options = self.controller.options(name, command)
        except Refused as refusal:
            return self._send_json(200, {"status": "refused",
                                         "reason": refusal.reason,
                                         "options": []})
        except KeyError:
            return self._send_json(404, {"status": "error", "options": [],
                                         "reason": f"{name} is not open"})
        return self._send_json(200, {"status": "ok", "reason": "",
                                     "options": [str(o) for o in options]})

    def _send_data(self, name, command):
        """One route for the three data element types.

        `plot` and `log_stream` hand back JSON; `image` hands back PNG bytes
        and is served as an image so the browser can put it straight in an
        `<img>`. The element type is not needed here: the value's own shape
        says which it is.
        """
        result = self._run(name, command)
        if not result.is_ok:
            return self._send_json(200, self._result_dict(result))
        value = result.value
        if isinstance(value, (bytes, bytearray)):
            return self._send_bytes(200, "image/png", bytes(value))
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            return self._send_json(200, {"status": "ok", "lines": list(value)})
        return self._send_json(200, {"status": "ok", "data": value})

    def _send_file(self, name, command, inputs=None):
        """Download the file a save command wrote.

        The browser never sends a server path (old finding 9): it names the
        model and the save command, the model decides where the file goes,
        and this route refuses to serve anything that did not land inside
        that model's own output root.
        """
        result = self._run(name, command, inputs or {})
        if not result.is_ok:
            return self._send_json(200, self._result_dict(result))
        path = result.value
        if isinstance(path, dict):
            path = path.get("path")
        if not isinstance(path, str) or not path:
            return self._send_json(200, {
                "status": "error",
                "reason": f"{command} did not report a file it wrote"})
        root = self._output_root(name)
        if not root:
            return self._send_json(409, {
                "status": "error",
                "reason": f"{name} does not declare an output root, so a "
                          f"download cannot be checked against one"})
        full = os.path.realpath(path)
        root = os.path.realpath(root)
        if not self._inside(full, root):
            events.warn("Download Refused", f"{command} wrote {full}, which is "
                        f"outside {name}'s output root", source=SOURCE)
            return self._send_json(403, {
                "status": "refused",
                "reason": "the file is outside the model's output root"})
        if not os.path.isfile(full):
            return self._send_json(404, {"status": "error",
                                         "reason": "the file is not there"})
        with open(full, "rb") as handle:
            payload = handle.read()
        content_type = mimetypes.guess_type(full)[0] or "application/octet-stream"
        name_only = os.path.basename(full).replace('"', "")
        return self._send_bytes(200, content_type, payload, headers={
            "Content-Disposition": f'attachment; filename="{name_only}"'})

    def _send_screen(self, name=None):
        """A bounded screenshot for the region picker.

        The capture belongs to the `Screen` device, which a view may not
        import (`tests/test_architecture.py`), so this asks the model
        that owns one - through its `region_select` element's image command,
        exactly like any other data command. A model that does not offer one
        gets an honest 503 instead of this file growing a second copy of mss
        (the duplication the rebuild exists to remove).
        """
        found = self._screen_command(name)
        if found is None:
            return self._send_json(503, {
                "status": "error",
                "reason": "no open model offers a screen image for the "
                          "region picker"})
        model_name, command = found
        result = self._run(model_name, command)
        if not result.is_ok:
            return self._send_json(200, self._result_dict(result))
        # Always JSON: the picker needs the geometry as much as the picture,
        # because a bounded grab of a second monitor is neither full size nor
        # at the origin. Bare PNG bytes are accepted and the client then
        # scales by the image's own size.
        value = result.value
        payload = dict(value) if isinstance(value, dict) else {"image": value}
        picture = payload.get("image")
        if isinstance(picture, (bytes, bytearray)):
            payload["image"] = ("data:image/png;base64,"
                                + base64.b64encode(picture).decode("ascii"))
        elif not isinstance(picture, str) or not picture:
            return self._send_json(200, {
                "status": "error",
                "reason": f"{command} returned no image"})
        payload.setdefault("status", "ok")
        payload["name"] = model_name
        return self._send_json(200, payload)

    def _screen_command(self, name=None):
        """(model, command) of the first region_select that names an image."""
        names = [name] if name else self.controller.model_names
        for model_name in names:
            try:
                schema = self.controller.schema(model_name)
            except KeyError:
                continue
            for element in sch.elements(schema):
                if element["type"] == "region_select" and element.get("data_command"):
                    return model_name, element["data_command"]
        return None

    def _output_root(self, name):
        try:
            values = self.controller.state(name).get("values", {})
        except KeyError:
            values = {}
        config = self.controller.config(name) or {}
        for source in (values, config):
            for key in ("output_root", "run_dir"):
                if source.get(key):
                    return str(source[key])
        return None

    @staticmethod
    def _inside(full, root):
        try:
            return os.path.commonpath([full, root]) == root
        except ValueError:
            return False

    # -- the security boundary --------------------------------------------
    def _is_local(self, require_json=False, require_origin=False):
        """Owner ruling 2026-09-21: localhost only, no session token.

        Answers the request itself and returns False when it must not
        proceed. Three checks, each of which alone defeats the common case:

        1. the client address is loopback - the socket binds 127.0.0.1, so
           this is belt and braces against a reverse proxy in front of it;
        2. `application/json`, which a cross-site `<form>` cannot set, so
           requiring it forces a preflight the browser will refuse;
        3. `Origin`/`Referer` and `Host` must name this server - a page open
           in the operator's own browser can still POST to localhost.
        """
        client = (self.client_address[0] if self.client_address else "")
        if client not in _LOOPBACK:
            events.warn("Refused Non-Local Request",
                        f"{self.command} {self.path} from {client}", source=SOURCE)
            self._send_json(403, {"status": "refused",
                                  "reason": "this station answers localhost only"})
            return False
        if require_json:
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if content_type != "application/json":
                events.debug("Refused Content Type",
                             f"{self.path} sent {content_type!r}", source=SOURCE)
                self._send_json(415, {
                    "status": "refused",
                    "reason": "Content-Type: application/json is required"})
                return False
        if require_json or require_origin:
            hosts = self._bound_hosts()
            origin = self.headers.get("Origin") or self.headers.get("Referer")
            if origin is not None and urlparse(origin).netloc not in hosts:
                events.warn("Refused Cross-Origin Request",
                            f"{self.command} {self.path} from origin {origin}",
                            source=SOURCE)
                self._send_json(403, {"status": "refused",
                                      "reason": "cross-origin request refused"})
                return False
            host = (self.headers.get("Host") or "").strip()
            if host and host not in hosts:
                events.warn("Refused Foreign Host",
                            f"{self.command} {self.path} sent Host {host}",
                            source=SOURCE)
                self._send_json(403, {"status": "refused",
                                      "reason": "unrecognised Host header"})
                return False
        return True

    def _bound_hosts(self):
        host, port = self.server.server_address[:2]
        names = {host, "127.0.0.1", "localhost", "[::1]", "::1"}
        return {f"{n}:{port}" for n in names} | names

    # -- responses ---------------------------------------------------------
    def _send_json(self, status, data):
        # default=str: a route handing back something exotic degrades to its
        # string form instead of taking the response down from inside dumps.
        self._send_bytes(status, "application/json",
                         json.dumps(data, default=str).encode("utf-8"))

    def _send_bytes(self, status, content_type, payload, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        # No CORS header: the dashboard is same-origin, served by this same
        # process. A wildcard here would let any site the operator's browser
        # visits drive physical hardware.
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
        self._log_request(status, len(payload))

    def _send_error_json(self, exc):
        events.debug("Request Failed", f"{self.command} {self.path}: {exc}",
                     source=SOURCE, exception=exc)
        try:
            self._send_json(500, {"status": "error", "reason": str(exc)})
        except Exception:
            pass    # the connection is already gone; nothing left to say

    def _serve_static(self, route):
        if route in ("", "/"):
            route = "/index.html"
        full = os.path.realpath(os.path.join(_STATIC_DIR, route.lstrip("/")))
        if not self._inside(full, os.path.realpath(_STATIC_DIR)) or not os.path.isfile(full):
            return self._send_json(404, {"status": "error",
                                         "reason": f"no file {route}"})
        content_type = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if content_type.startswith(("text/", "application/javascript")):
            content_type += "; charset=utf-8"
        with open(full, "rb") as handle:
            self._send_bytes(200, content_type, handle.read())

    # -- diagnostics (Addendum 1) -----------------------------------------
    def _log_request(self, status, size):
        route = urlparse(self.path).path
        elapsed = (time.monotonic() - getattr(self, "_started", time.monotonic())) * 1000
        events.debug(f"{self.command} {route}",
                     f"{status} {size} B in {elapsed:.1f} ms", source=SOURCE,
                     every=1.0 if route in self.POLLED else 0.0)

    def log_message(self, format, *args):
        """stdlib access log -> the log file. Never the terminal."""
        events.debug("Access", format % args, source=SOURCE, every=1.0)

    # -- request bodies ----------------------------------------------------
    def _read_body(self):
        length = self._int(self.headers.get("Content-Length"), 0)
        if length > self.MAX_BODY_BYTES:
            # Drain the declared body in bounded chunks before answering.
            # Bailing out without draining races the client's in-flight
            # write: the connection resets under it and the client sees a
            # broken pipe instead of the 413 this is trying to deliver.
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
            self._send_json(413, {
                "status": "refused",
                "reason": f"body larger than {self.MAX_BODY_BYTES} bytes"})
            return False, None
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return True, json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send_json(400, {"status": "error", "reason": "invalid JSON"})
            return False, None

    @staticmethod
    def _one(query, key, default=""):
        found = query.get(key) or []
        return found[0] if found else default

    @staticmethod
    def _int(raw, default):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default


class _StationServer(http.server.ThreadingHTTPServer):
    """Carries the view, so the handler holds no class-level state."""
    allow_reuse_address = True

    def __init__(self, address, handler, view):
        self.view = view
        super().__init__(address, handler)


class WebView:
    """The Web dashboard: the HTTP server plus the browser-liveness watchdog.

    Holds the Controller and the Setup panel and nothing else from the
    backend, exactly like `Dashboard`. It is not a `Dashboard` subclass: the
    browser draws the window, so there is no widget to marshal onto and no
    popup to open here - events reach the client through `/api/events`.
    """

    #: D-8a, PROVISIONAL until measured at the bench (WEB-19, WEB-23).
    WARN_SECONDS = 5.0
    STOP_SECONDS = 15.0
    #: How often the watchdog looks. Not a safety threshold.
    WATCH_SECONDS = 0.5
    #: How many consecutive ports to try before giving up (WEB-16).
    PORT_ATTEMPTS = 10

    def __init__(self, controller, setup, *, host="127.0.0.1", port=8080,
                 open_browser=True, clock=time.monotonic):
        self.controller, self.setup = controller, setup
        self.host, self.port = host, port
        self.open_browser = open_browser
        self._clock = clock
        self._server = None
        self._serve_thread = None
        self._watch_thread = None
        self._halt = threading.Event()
        self._lock = threading.Lock()
        self._last_beat = None
        self._warned = False
        self._stopped = False

    # -- the address the operator opens ------------------------------------
    @property
    def url(self):
        return f"http://{self.host}:{self.port}" if self._server else ""

    @property
    def is_serving(self):
        return self._server is not None

    # -- open / close ------------------------------------------------------
    def open(self):
        """Bind, serve on a thread, start the watchdog. Returns the URL, or
        "" when the port could not be bound - which is reported as an event,
        never as a traceback out of a launcher (WEB-16)."""
        if self._server is not None:
            return self.url
        if not self._bind():
            return ""
        self._halt.clear()
        # A short poll interval only bounds how long `close()` waits for the
        # accept loop to notice the shutdown; it is not a busy loop.
        self._serve_thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
            name="web-server", daemon=True)
        self._serve_thread.start()
        self._watch_thread = threading.Thread(target=self._watch_loop,
                                              name="web-watchdog", daemon=True)
        self._watch_thread.start()
        events.info("Web Dashboard", f"serving at {self.url}", source=SOURCE)
        events.debug("Watchdog Started", f"warn at {self.WARN_SECONDS}s, FULL "
                     f"STOP at {self.STOP_SECONDS}s of browser silence",
                     source=SOURCE)
        if self.open_browser:
            try:
                webbrowser.open(self.url)
            except Exception as exc:      # a headless box has no browser
                events.debug("Browser Not Opened", str(exc), source=SOURCE,
                             exception=exc)
        return self.url

    def _bind(self):
        first, last_error = self.port, None
        for offset in range(self.PORT_ATTEMPTS):
            try:
                self._server = _StationServer((self.host, self.port),
                                              ApiHandler, self)
            except OSError as exc:
                # Only EADDRINUSE means "busy". Any other OSError (permission
                # denied, address not available) is a real failure and must
                # not be masked by walking up the port range (WEB-16).
                if exc.errno != errno.EADDRINUSE:
                    events.error("Web Server Failed",
                                 f"cannot bind {self.host}:{self.port}: {exc}",
                                 source=SOURCE, exception=exc)
                    return False
                last_error = exc
                events.debug("Port Busy", f"{self.host}:{self.port} in use, "
                             f"trying {self.port + 1}", source=SOURCE)
                self.port += 1
                continue
            self.port = self._server.server_address[1]
            return True
        events.error("Web Server Failed",
                     f"no free port in {first}-{first + self.PORT_ATTEMPTS - 1} "
                     f"on {self.host}: {last_error}", source=SOURCE)
        self.port = first
        return False

    def wait(self):
        """Block the launching thread until the view is closed (Ctrl-C, a
        signal, or `close()`), the way the desktop views block in their event
        loops. Without this the launcher returns and the process exits with
        the server still starting."""
        try:
            while not self._halt.is_set():
                self._halt.wait(0.5)
        except KeyboardInterrupt:
            pass
        self.close()

    def close(self):
        """Watchdog, server, then the Controller. Runs at most once."""
        self._halt.set()
        watchdog, self._watch_thread = self._watch_thread, None
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout=self.WATCH_SECONDS * 4)
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
            events.debug("Server Stopped", f"{self.host}:{self.port}", source=SOURCE)
        thread, self._serve_thread = self._serve_thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        self.controller.close()

    # -- browser liveness (D-8 / WEB-19 / WEB-23) --------------------------
    def beat(self):
        """A browser tab checked in. "Now" is read here, so a slow request
        cannot backdate the deadline."""
        with self._lock:
            previous, self._last_beat = self._last_beat, self._clock()
            self._warned = self._stopped = False
        gap = None if previous is None else self._last_beat - previous
        events.debug("Heartbeat", "first check-in" if gap is None
                     else f"gap {gap:.2f}s", source=SOURCE, every=1.0)
        return self._last_beat

    @property
    def heartbeat_age(self):
        """Seconds since the last check-in, or None if no client ever has."""
        with self._lock:
            seen = self._last_beat
        return None if seen is None else round(self._clock() - seen, 3)

    def _watch_loop(self):
        while not self._halt.wait(self.WATCH_SECONDS):
            try:
                self._check_heartbeat()
            except Exception as exc:
                events.debug("Watchdog Check Failed", str(exc), source=SOURCE,
                             exception=exc, every=1.0)

    def _check_heartbeat(self):
        """Warn, then FULL STOP, on browser silence.

        Three rules, carried over from the probe's private copy:

        * **no gate until a client has checked in** - a Tk or Qt session
          never calls `beat()`, so it can never be stopped by this;
        * **never while idle** - silence only matters while the station is
          moving, heating or recording (`Controller.is_active`);
        * **once per silence** - a heater whose stop frame could not be
          written still reads as heating, and without the flag this would
          re-stop and re-report every tick.
        """
        with self._lock:
            seen, warned, stopped = self._last_beat, self._warned, self._stopped
        if seen is None or stopped:
            return
        if not self.controller.is_active:
            return
        silence = self._clock() - seen
        if silence > self.STOP_SECONDS:
            with self._lock:
                if self._stopped:
                    return
                self._stopped = True
            events.debug("Watchdog FULL STOP", f"{silence:.1f}s of browser "
                         f"silence while active", source=SOURCE)
            self.controller.estop_all()
            events.error("Browser Gone - FULL STOP",
                         f"No browser has checked in for {silence:.1f}s while "
                         f"the station was active. Every model is latched.",
                         source=SOURCE)
        elif silence > self.WARN_SECONDS and not warned:
            with self._lock:
                self._warned = True
            events.warn("Browser Silent",
                        f"No browser has checked in for {silence:.1f}s while "
                        f"the station is active. FULL STOP at "
                        f"{self.STOP_SECONDS:.0f}s.", source=SOURCE)
