import errno
import http.server
import json
import os
import mimetypes
import secrets
import threading
from urllib.parse import urlparse
import io
import base64
from typing import Optional

from .web_adapter import WebModelAdapter


# One token per launch. It is injected into the served HTML, so the dashboard
# has it and a cross-site page does not (RC-10). Regenerated per process, never
# persisted — this is a same-machine boundary, not an account system.
SESSION_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-Stage-Token"


class WebAPIHandler(http.server.BaseHTTPRequestHandler):
    """
    Standard-library HTTP handler providing REST endpoints and static file serving
    for the unified transfer stage MVC web dashboard.
    Interacts with models exclusively via WebModelAdapter to preserve MVC boundaries.
    """
    adapter: Optional[WebModelAdapter] = WebModelAdapter()
    static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))

    # A POST body here is always the small JSON commands/configs this API
    # actually accepts (WEB-21). The read used to be `Content-Length`
    # bytes, unbounded, so a slow or malicious client claiming a huge
    # Content-Length handed this handler thread's memory over on request.
    MAX_POST_BODY_BYTES = 1 * 1024 * 1024  # 1 MiB

    # One mss instance, reused across /api/screenshot calls instead of
    # opening (and platform-side registering) a fresh capture context per
    # request (WEB-21). Grabs are serialized under _mss_lock rather than
    # relying on mss being safe for concurrent use from multiple threads.
    _mss_instance = None
    _mss_lock = threading.Lock()

    # ---- security boundary (RC-10 item 2) -------------------------------
    #
    # This server drives physical hardware from an unauthenticated localhost
    # port. Before this, any page the operator's browser happened to visit
    # could issue a cross-site form POST to /api/command and move the stage,
    # and could GET /api/screenshot to read the operator's screen. Browsers
    # send such "simple" requests without a preflight and without asking.
    #
    # Three checks, each of which alone defeats the common case:
    #   1. application/json content type — a cross-site form cannot set it,
    #      so requiring it forces a preflight the browser will refuse;
    #   2. Origin/Referer must match the address we are bound to;
    #   3. a per-launch token that only the served HTML carries.

    def _bound_hosts(self):
        host, port = self.server.server_address[:2]
        names = {host, "127.0.0.1", "localhost", "[::1]", "::1"}
        return {f"{n}:{port}" for n in names} | names

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            referer = self.headers.get("Referer")
            if referer is None:
                # No Origin and no Referer: not a browser-initiated cross-site
                # request. The token check still has to pass.
                return True
            origin = referer
        parsed = urlparse(origin)
        return parsed.netloc in self._bound_hosts()

    def _token_ok(self):
        return secrets.compare_digest(
            self.headers.get(TOKEN_HEADER, ""), SESSION_TOKEN)

    def _authorize(self, require_json):
        """Returns True if the request may proceed; otherwise answers it."""
        if require_json:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                self._send_json(415, {
                    "status": "error",
                    "message": "Content-Type: application/json is required"})
                return False
        if not self._origin_ok():
            self._send_json(403, {
                "status": "error",
                "message": "Cross-origin request refused"})
            return False
        if not self._token_ok():
            self._send_json(403, {
                "status": "error",
                "message": "Missing or invalid session token"})
            return False
        return True

    # Maintain backward compatibility if external code references system_manager directly
    @classmethod
    def get_system_manager(cls):
        return cls.adapter.system_manager if cls.adapter else None

    @classmethod
    def set_system_manager(cls, val):
        if cls.adapter is None:
            cls.adapter = WebModelAdapter(val)
        else:
            cls.adapter.set_system_manager(val)

    def _send_json(self, status_code, data):
        # default=str: a route handing back e.g. a raw exception object or a
        # timestamp should degrade to its string form, not take the whole
        # response down with a raise from inside json.dumps itself.
        payload = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        # No CORS header: the dashboard is same-origin (served by this same
        # process). A wildcard origin here would let any external site the
        # user's browser visits issue cross-origin requests that drive
        # physical stage hardware.
        self.end_headers()
        self.wfile.write(payload)

    def _serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        clean_rel = path.lstrip("/")
        full_path = os.path.abspath(os.path.join(self.static_dir, clean_rel))

        # Security: prevent directory traversal outside static_dir
        try:
            if os.path.commonpath([full_path, self.static_dir]) != self.static_dir:
                self.send_error(404, "File Not Found")
                return
        except ValueError:
            self.send_error(404, "File Not Found")
            return

        if not os.path.isfile(full_path):
            self.send_error(404, "File Not Found")
            return

        content_type, _ = mimetypes.guess_type(full_path)
        if not content_type:
            content_type = "application/octet-stream"

        try:
            with open(full_path, "rb") as f:
                content = f.read()
            if content_type == "text/html":
                # The dashboard learns the token by being served it. A
                # cross-site page cannot read this because it cannot read our
                # HTML (same-origin policy) — which is the whole mechanism.
                meta = (f'<meta name="stage-token" content="{SESSION_TOKEN}">'
                        ).encode("utf-8")
                if b"<head>" in content:
                    content = content.replace(b"<head>", b"<head>" + meta, 1)
                else:
                    content = meta + content
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error reading file: {e}")

    def do_GET(self):
        # Every route body runs under this envelope (WEB-14): an unhandled
        # exception used to propagate out of BaseHTTPRequestHandler and drop
        # the connection with no response at all, which the browser reports
        # as a bare network failure indistinguishable from the server being
        # down. A route that fails now still answers, with a 500 and a
        # message, so the operator sees a specific error instead of a dead
        # dashboard tile.
        try:
            self._do_GET_impl()
        except Exception as e:
            import traceback
            print(f"[WebAPIHandler] GET {self.path} failed:\n{traceback.format_exc()}")
            try:
                self._send_json(500, {"status": "error", "message": str(e)})
            except Exception:
                pass

    def _do_GET_impl(self):
        parsed = urlparse(self.path)
        route = parsed.path

        adapter = self.adapter or WebModelAdapter()

        if route == "/api/devices":
            devices = adapter.get_devices()
            self._send_json(200, devices)

        elif route == "/api/state":
            state = adapter.get_state()
            self._send_json(200, state)

        elif route == "/api/system/status":
            info = adapter.get_system_info() if hasattr(adapter, "get_system_info") else {"status": adapter.mode}
            self._send_json(200, info)

        elif route == "/api/setup/scan":
            scan_result = adapter.scan_hardware()
            self._send_json(200, scan_result)

        elif route == "/api/setup/scan/status":
            # WEB-15 residue: poll side of POST /api/setup/scan/start. Never
            # blocks — probe_device_at runs on the adapter's background
            # thread, this just reads whatever it has filled in so far.
            self._send_json(200, adapter.get_scan_status())

        elif route == "/api/logs":
            logs = adapter.get_logs()
            self._send_json(200, {"logs": logs})

        elif route == "/api/errors":
            # `?since=<id>` and a non-destructive read (RC-8 item 3). The
            # route used to `pop_errors()`, so with two tabs open whichever
            # polled first consumed the error and the other never saw it
            # (ERRORS-2, WEB-17).
            from urllib.parse import parse_qs
            qs = parse_qs(parsed.query)
            try:
                since = int(qs.get("since", ["0"])[0])
            except (TypeError, ValueError):
                since = 0
            errors = adapter.errors_since(since)
            self._send_json(200, {
                "errors": errors,
                "latest_id": adapter.latest_error_id(),
            })

        elif route == "/api/options":
            from urllib.parse import parse_qs
            qs = parse_qs(parsed.query)
            device = qs.get("device", [None])[0]
            command = qs.get("command", [None])[0]
            if not device or not command:
                self._send_json(400, {"status": "error", "message": "device and command query params required"})
            else:
                result = adapter.resolve_options(device, command)
                self._send_json(result.get("code", 200), result)

        elif route == "/api/screenshot":
            # Reads the operator's actual screen, so it is guarded like a POST
            # (minus the JSON content type, which a GET does not carry).
            if not self._authorize(require_json=False):
                return
            # Imported lazily (WEB-21): a headless launch, or any test that
            # never hits this route, used to pay for mss/PIL at module import
            # time regardless, and a machine missing either package could not
            # import this module at all just to serve the rest of the API.
            import mss
            from PIL import Image
            try:
                with self.__class__._mss_lock:
                    if self.__class__._mss_instance is None:
                        self.__class__._mss_instance = mss.mss()
                    sct = self.__class__._mss_instance
                    monitor = sct.monitors[1]
                    sct_img = sct.grab(monitor)
                    img = Image.frombytes('RGB', sct_img.size, sct_img.bgra, 'raw', 'BGRX')
                    img.thumbnail((800, 600), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    img.save(buf, format='JPEG', quality=60)
                    img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                    self._send_json(200, {
                        "status": "success",
                        "image": f"data:image/jpeg;base64,{img_b64}",
                        "original_width": monitor["width"],
                        "original_height": monitor["height"],
                        "monitor_left": monitor["left"],
                        "monitor_top": monitor["top"]
                    })
            except Exception as e:
                self._send_json(500, {"status": "error", "message": str(e)})

        else:
            self._serve_static(route)

    def do_POST(self):
        # Same envelope as do_GET (WEB-14) — a raising command handler must
        # still answer the request instead of dropping the connection.
        try:
            self._do_POST_impl()
        except Exception as e:
            import traceback
            print(f"[WebAPIHandler] POST {self.path} failed:\n{traceback.format_exc()}")
            try:
                self._send_json(500, {"status": "error", "message": str(e)})
            except Exception:
                pass

    def _do_POST_impl(self):
        if not self._authorize(require_json=True):
            return
        parsed = urlparse(self.path)
        route = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        if length > self.MAX_POST_BODY_BYTES:
            # Drain the declared body off the wire in bounded chunks before
            # answering, rather than reading it into one bytes object (the
            # thing this cap exists to avoid) or leaving it unread. Bailing
            # out here without draining races the client's still-in-flight
            # write: the connection resets under it and the client sees a
            # bare broken pipe instead of the 413 this is trying to deliver.
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
            return self._send_json(413, {
                "status": "error",
                "message": f"Request body too large (max {self.MAX_POST_BODY_BYTES} bytes)"})
        body = self.rfile.read(length) if length > 0 else b"{}"

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return self._send_json(400, {"status": "error", "message": "Invalid JSON"})

        adapter = self.adapter or WebModelAdapter()

        if route == "/api/plot":
            try:
                import io
                from model.plot_data import parse_red_percent_csv, render_red_percent_figure

                csv_data = data.get("csv_data", "")
                plot_type = data.get("plot_type", "0D")

                parsed = parse_red_percent_csv(csv_data)
                dims = parsed["dims"]

                # REDPERCENT-17: an operator-chosen dim wins if it names an
                # axis this CSV actually has; an empty/absent/unrecognised
                # one falls back to file order, exactly the old behavior,
                # so a client that has not been updated yet (or sends no
                # selection for 0D/1D, which ignore dim1-3 anyway) still
                # gets a plot.
                def _pick(requested, index):
                    if requested and requested in dims:
                        return requested
                    return dims[index] if len(dims) > index else None

                dim1 = _pick(data.get("dim1"), 0)
                dim2 = _pick(data.get("dim2"), 1)
                dim3 = _pick(data.get("dim3"), 2)

                fig = render_red_percent_figure(plot_type, dim1, dim2, dim3, parsed["red_percents"], parsed["dim_data"])

                buf = io.BytesIO()
                fig.savefig(buf, format='png')

                return self._send_json(200, {
                    "image_base64": base64.b64encode(buf.getvalue()).decode('utf-8'),
                    "dims": dims,
                })
            except Exception as e:
                import traceback
                print(f"[WebAPIHandler] /api/plot failed:\n{traceback.format_exc()}")
                return self._send_json(500, {"status": "error", "message": str(e)})

        if route == "/api/setup/initialize":
            if not isinstance(data, dict):
                return self._send_json(400, {"status": "error", "message": "JSON body must be an object"})
            configs = data.get("device_configs", data.get("configs", data))
            init_func = getattr(adapter, "initialize_system", adapter.initialize_setup)
            result = init_func(configs)
            code = result.get("code", 200)
            return self._send_json(code, result)

        if route == "/api/setup/scan/start":
            # WEB-15 residue: kicks off the background device-type probe;
            # /api/setup/scan/status (GET) polls it. 409 if one is already
            # running (adapter enforces single-flight, not this route).
            result = adapter.start_hardware_scan()
            code = result.get("code", 200)
            return self._send_json(code, result)

        if not isinstance(data, dict):
            return self._send_json(400, {"status": "error", "message": "JSON body must be an object"})

        if route == "/api/command":
            device_name = data.get("device")
            command_name = data.get("command")
            args = data.get("args", [])
            # D-5: the field values the command declared travel with it, so
            # the model validates them as a set instead of acting on whatever
            # a prior set_attr happened to leave behind.
            inputs = data.get("inputs", {})
            if not isinstance(inputs, dict):
                return self._send_json(400, {"status": "error",
                                             "message": "inputs must be an object"})

            if not device_name or not command_name:
                return self._send_json(400, {"status": "error", "message": "Missing device or command"})

            result = adapter.dispatch_command(device_name, command_name, args,
                                              inputs=inputs)
            code = result.get("code", 200)
            return self._send_json(code, result)

        elif route == "/api/set_attr":
            device_name = data.get("device")
            attr = data.get("attr")
            value = data.get("value")

            if not device_name or not attr:
                return self._send_json(400, {"status": "error", "message": "Missing device or attr"})

            result = adapter.set_device_attribute(device_name, attr, value)
            code = result.get("code", 200)
            return self._send_json(code, result)

        elif route == "/api/system/full_stop":
            result = adapter.full_stop_all()
            code = result.get("code", 200)
            return self._send_json(code, result)

        elif route == "/api/client/heartbeat":
            # WEB-19 (D-8, client half). Body is deliberately unused today -
            # `{}` from app.js - so this stays a cheap, fixed-cost POST no
            # matter how the payload evolves later.
            result = adapter.record_client_heartbeat()
            code = result.get("code", 200)
            return self._send_json(code, result)

        else:
            self.send_error(404, "Endpoint not found")


# Property descriptor on WebAPIHandler class to seamlessly bridge class-level system_manager
class _SystemManagerDescriptor:
    def __get__(self, instance, owner):
        return owner.adapter.system_manager if owner.adapter else None

    def __set__(self, owner_or_instance, val):
        if WebAPIHandler.adapter is None:
            WebAPIHandler.adapter = WebModelAdapter(val)
        else:
            WebAPIHandler.adapter.set_system_manager(val)


WebAPIHandler.system_manager = _SystemManagerDescriptor()


# `_BufferProxy` and the two class-level buffers it backed are gone
# (ERRORS-12). It presented `WebAPIHandler.log_buffer` / `.error_buffer` as
# list-like objects that forwarded to *whichever* WebModelAdapter the
# handler class currently held — and built a throwaway one, with no system
# manager, if it held none. Two consequences, one of them live:
#
#   * the error copy duplicated the bus. S11 made the bus the source of
#     truth for `/api/errors` (`errors_since`/`latest_id`), so the mirror
#     was a second account of the same events that nothing read;
#   * the log copy went to the handler class's adapter, which is not the
#     adapter a given WebDashboardServer serves `/api/logs` from until
#     `start()` re-points it. A poller log emitted before that landed in an
#     adapter nothing reads.
#
# Callers name the adapter they mean now: `adapter.append_log(...)` and
# `adapter.get_logs()`.


class ThreadingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


class WebDashboardServer:
    def __init__(self, system_manager=None, host="127.0.0.1", port=8080, adapter=None):
        if adapter is not None:
            self.adapter = adapter
            if system_manager is not None:
                self.adapter.set_system_manager(system_manager)
        else:
            # Share existing handler adapter if already configured and matching
            if WebAPIHandler.adapter and system_manager is None:
                self.adapter = WebAPIHandler.adapter
            else:
                self.adapter = WebModelAdapter(system_manager)

        self.host = host
        self.port = port
        self.server = None
        self.thread = None

    @property
    def system_manager(self):
        """The adapter is the one place a manager lives (RC-10 item 1)."""
        return self.adapter.system_manager if self.adapter else None

    def start(self, background=True):
        handler_cls = WebAPIHandler
        handler_cls.adapter = self.adapter

        # If the port is busy, try the next one (WEB-16). Only
        # errno.EADDRINUSE means "busy" - any other OSError (permission
        # denied, bad host, address not available) is a real failure and
        # must not be silently treated as "keep incrementing the port",
        # which used to mask it. And if every attempt in the range really
        # is EADDRINUSE, `self.server` falls out of this loop as None; the
        # old code then let `self.server.serve_forever` raise a bare
        # AttributeError below instead of saying what actually happened.
        self.server = None
        first_port = self.port
        last_err = None
        for _attempt in range(10):
            try:
                self.server = ThreadingHTTPServer((self.host, self.port), handler_cls)
                break
            except OSError as e:
                if e.errno != errno.EADDRINUSE:
                    raise
                last_err = e
                self.port += 1

        if self.server is None:
            raise RuntimeError(
                f"Could not bind the web dashboard to any port in "
                f"{first_port}-{first_port + 9} on {self.host}: {last_err}")

        # With port=0 the OS picks an ephemeral port, so self.port has to be
        # read back from the socket or callers build URLs for port 0.
        self.port = self.server.server_address[1]

        if background:
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
        else:
            self.server.serve_forever()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
