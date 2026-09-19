import http.server
import json
import os
import mimetypes
import threading
from urllib.parse import urlparse
import mss
import io
import base64
from PIL import Image
from typing import Optional

from .web_adapter import WebModelAdapter


class WebAPIHandler(http.server.BaseHTTPRequestHandler):
    """
    Standard-library HTTP handler providing REST endpoints and static file serving
    for the unified transfer stage MVC web dashboard.
    Interacts with models exclusively via WebModelAdapter to preserve MVC boundaries.
    """
    adapter: Optional[WebModelAdapter] = WebModelAdapter()
    static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "static"))

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
        payload = json.dumps(data).encode("utf-8")
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
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error reading file: {e}")

    def do_GET(self):
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

        elif route == "/api/logs":
            logs = adapter.get_logs()
            self._send_json(200, {"logs": logs})

        elif route == "/api/errors":
            errors = adapter.pop_errors()
            self._send_json(200, {"errors": errors})

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
            try:
                with mss.mss() as sct:
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
        parsed = urlparse(self.path)
        route = parsed.path
        length = int(self.headers.get("Content-Length", 0))
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
                dim1 = dims[0] if len(dims) >= 1 else None
                dim2 = dims[1] if len(dims) >= 2 else None
                dim3 = dims[2] if len(dims) >= 3 else None

                fig = render_red_percent_figure(plot_type, dim1, dim2, dim3, parsed["red_percents"], parsed["dim_data"])

                buf = io.BytesIO()
                fig.savefig(buf, format='png')

                return self._send_json(200, {"image_base64": base64.b64encode(buf.getvalue()).decode('utf-8')})
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

        if not isinstance(data, dict):
            return self._send_json(400, {"status": "error", "message": "JSON body must be an object"})

        if route == "/api/command":
            device_name = data.get("device")
            command_name = data.get("command")
            args = data.get("args", [])

            if not device_name or not command_name:
                return self._send_json(400, {"status": "error", "message": "Missing device or command"})

            result = adapter.dispatch_command(device_name, command_name, args)
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


# Backward compatibility aliases for module-level buffers
class _BufferProxy:
    def __init__(self, buffer_type):
        self.buffer_type = buffer_type

    def append(self, item):
        if WebAPIHandler.adapter is None:
            WebAPIHandler.adapter = WebModelAdapter()
        if self.buffer_type == "log":
            WebAPIHandler.adapter.append_log(item)
        else:
            WebAPIHandler.adapter.append_error(item)

    def clear(self):
        if WebAPIHandler.adapter is not None:
            if self.buffer_type == "log":
                with WebAPIHandler.adapter._state_lock:
                    WebAPIHandler.adapter.log_buffer.clear()
            else:
                WebAPIHandler.adapter.pop_errors()

    def __iter__(self):
        if WebAPIHandler.adapter is not None:
            if self.buffer_type == "log":
                return iter(WebAPIHandler.adapter.get_logs())
            else:
                return iter(list(WebAPIHandler.adapter.error_buffer))
        return iter([])

    def __len__(self):
        if WebAPIHandler.adapter is not None:
            if self.buffer_type == "log":
                return len(WebAPIHandler.adapter.get_logs())
            else:
                return len(WebAPIHandler.adapter.error_buffer)
        return 0

    def __getitem__(self, idx):
        if WebAPIHandler.adapter is not None:
            if self.buffer_type == "log":
                return WebAPIHandler.adapter.get_logs()[idx]
            else:
                return WebAPIHandler.adapter.error_buffer[idx]
        raise IndexError("Buffer is empty")


WebAPIHandler.log_buffer = _BufferProxy("log")
WebAPIHandler.error_buffer = _BufferProxy("error")


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

        self.system_manager = self.adapter.system_manager
        self.host = host
        self.port = port
        self.server = None
        self.thread = None

    def start(self, background=True):
        handler_cls = WebAPIHandler
        handler_cls.adapter = self.adapter
        
        # If port is busy, find next open port
        for attempt in range(10):
            try:
                self.server = ThreadingHTTPServer((self.host, self.port), handler_cls)
                break
            except OSError:
                self.port += 1

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
