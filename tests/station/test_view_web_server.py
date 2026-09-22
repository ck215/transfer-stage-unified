"""The Web API, driven over a real socket on an ephemeral port.

No browser, no hardware: a real `Controller` holding a fake Panel-shaped
model, the real `Setup`-shaped panel, and `urllib` playing the browser - and,
where it matters, playing a cross-site page instead.

Ported from `tests/web/test_web_server.py`, `test_web_security.py`,
`test_web_19_client_heartbeat.py` and `test_dc6_web_refusal_is_403.py`.
"""
import email.message
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import pytest

from station import schema as sch
from station.controller import Controller
from station.events import events
from station.panel import Panel
from station.param import Param
from station.result import NeedsConfirm, Refused
from station.views.web.server import ApiHandler, SETUP_NAME, WebView


# --------------------------------------------------------------------------
# a model the Controller can hold: a real Panel, so the allow-list, the input
# validation and the Refused/NeedsConfirm contract are the real ones.
# --------------------------------------------------------------------------
class FakeProbe(Panel):
    NAME = "Fake Probe"
    PARAMS = {"x_step": Param("x_step", "float", default=1.0, unit="mm"),
              "label": Param("label", "text", default="", label="Label")}

    def __init__(self, root=None):
        super().__init__()
        self.position = "0.000"
        self.region = None
        self.source = None
        self.source_options = ["None", "Probe A"]
        self.is_estopped = False
        self.is_active = False
        self.is_faulted = False
        self.mode = "idle"
        self.is_open = False
        self.output_root = root
        self.written = None
        self.state_raises = False
        self.runs = []

    # -- what the Controller needs -------------------------------------
    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def estop(self):
        self.is_estopped = True
        return True

    def clear_estop(self, confirmed=False):
        self.is_estopped = False

    def on_model_added(self, name, model):
        pass

    def on_model_removed(self, name, model):
        pass

    # -- what a view needs ---------------------------------------------
    @property
    def mode_name(self):
        return self.mode

    @property
    def schema(self):
        region = sch.region_select("Region", "set_region", model_attr="region")
        # Pending the core change request: the picker's image source.
        region["data_command"] = "screen_image"
        return sch.schema(
            sch.section(
                "Motion",
                sch.readonly("Position", "position"),
                sch.entry("X step", "x_step", self.PARAMS["x_step"]),
                sch.entry("Label", "label", self.PARAMS["label"]),
                sch.button("Home", "home", inputs=["x_step"]),
                sch.button("Park", "park", disabled_when=["running"]),
                sch.toggle("FULL STOP", "is_estopped", "toggle_estop",
                           "LATCHED", "FULL STOP", on_role="danger",
                           off_role="danger"),
                sch.dropdown("Source", "source", "set_source", "source_options"),
                region,
                sch.file_save("Save", "save"),
                sch.plot("Red", "series"),
                sch.image("Figure", "figure"),
                sch.log_stream("Log", "log_lines"),
                sch.indicator("Fault", "is_faulted"),
            ))

    @property
    def state(self):
        if self.state_raises:
            raise RuntimeError("the model blew up")
        snapshot = super().state
        snapshot.update({"age": 0.0, "is_estopped": self.is_estopped,
                         "is_active": self.is_active,
                         "values": dict(snapshot["values"],
                                        output_root=self.output_root or "")})
        return snapshot

    # -- commands --------------------------------------------------------
    def home(self):
        self.runs.append(("home", self.x_step))
        return "homed"

    def park(self):
        raise Refused("the stage is not parked from here")

    def toggle_estop(self, confirmed=False):
        if not confirmed:
            raise NeedsConfirm("Latch the FULL STOP?", "toggle_estop", {}, ())
        self.is_estopped = not self.is_estopped
        return self.is_estopped

    def set_source(self, name=None):
        self.source = name
        return name

    def set_region(self, left=0, top=0, width=0, height=0):
        self.region = {"left": left, "top": top, "width": width, "height": height}
        return self.region

    def save(self):
        path = os.path.join(self.written_root, "run.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("red,x\n1,2\n")
        self.written = path
        return path

    @property
    def written_root(self):
        # Never the repo root, even in the "no output root declared" case.
        return self.output_root or tempfile.mkdtemp(prefix="station-web-")

    def series(self):
        return [[0, 1.0], [1, 2.0], [2, 1.5]]

    def figure(self):
        return b"\x89PNG\r\n\x1a\nfake"

    def log_lines(self):
        return ["first line", "second line"]

    def screen_image(self):
        return {"image": b"\x89PNG\r\n\x1a\nscreen", "width": 2560,
                "height": 1440, "left": -1000, "top": 0}


class FakeSetup(Panel):
    NAME = "Setup"

    def __init__(self):
        super().__init__()
        self.scanned = 0
        self.ports = ["SIM", "COM3"]

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Hardware",
            sch.button("Scan", "scan"),
            sch.dropdown("Port", "port", "set_port", "port_options"),
        ))

    @property
    def port_options(self):
        return self.ports

    def scan(self):
        self.scanned += 1
        return self.scanned

    def set_port(self, port=None):
        return port


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def station(tmp_path):
    controller = Controller()
    probe = FakeProbe(root=str(tmp_path))
    controller.factory = lambda config: FakeProbe(root=config.get("root"))
    controller.add("Fake Probe", probe, {"root": str(tmp_path)})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, probe
    finally:
        view.close()


def _request(view, path, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{view.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers or {},
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, (error.read() if error.fp else b"")


def _get(view, path):
    status, headers, body = _request(view, path)
    return status, json.loads(body.decode("utf-8"))


def _post(view, path, body, headers=None):
    sent = {"Content-Type": "application/json"}
    sent.update(headers or {})
    status, _, raw = _request(view, path, "POST", body, sent)
    return status, json.loads(raw.decode("utf-8"))


# --------------------------------------------------------------------------
# the read routes
# --------------------------------------------------------------------------
def test_state_names_every_open_model(station):
    view, controller, probe = station
    status, data = _get(view, "/api/state")
    assert status == 200
    assert "Fake Probe" in data["models"]
    assert data["models"]["Fake Probe"]["values"]["position"] == "0.000"
    assert data["is_estopped"] is False and data["is_active"] is False


def test_schema_is_the_models_own_schema(station):
    view, _, probe = station
    status, data = _get(view, "/api/schema?name=Fake+Probe")
    assert status == 200
    types = {e["type"] for e in sch.elements(data)}
    assert {"entry", "button", "toggle", "plot"} <= types


def test_schema_of_a_model_that_is_not_open_is_a_404(station):
    view, _, _ = station
    status, data = _get(view, "/api/schema?name=Nope")
    assert status == 404 and "not open" in data["reason"]


def test_setup_serves_its_schema_and_its_state(station):
    view, _, _ = station
    status, data = _get(view, "/api/setup")
    assert status == 200
    assert data["schema"]["sections"][0]["title"] == "Hardware"
    assert data["state"]["name"] == "Setup"


def test_theme_css_is_the_one_palette(station):
    view, _, _ = station
    status, headers, body = _request(view, "/api/theme.css")
    assert status == 200 and headers["Content-Type"].startswith("text/css")
    assert "--danger-bg" in body.decode("utf-8")


def test_static_files_are_served_and_traversal_is_not(station):
    view, _, _ = station
    status, _, body = _request(view, "/")
    assert status == 200 and b"<title>Transfer Stage</title>" in body
    status, _, _ = _request(view, "/../server.py")
    assert status == 404


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
def test_a_command_carries_every_entry_value_with_it(station):
    view, _, probe = station
    status, data = _post(view, "/api/run", {
        "name": "Fake Probe", "command": "home",
        "inputs": {"x_step": "2.5", "label": "front"}, "args": []})
    assert status == 200 and data["status"] == "ok"
    assert probe.runs == [("home", 2.5)], "the typed value did not travel"
    assert probe.label == "front"


def test_a_refusal_is_a_refusal_and_never_a_server_fault(station):
    """DC-6: a working interlock must not be reported as a 500."""
    view, _, _ = station
    status, data = _post(view, "/api/run", {
        "name": "Fake Probe", "command": "park"})
    assert status == 200, "a refusal came back as an HTTP error"
    assert data["status"] == "refused"
    assert "not parked from here" in data["reason"]


def test_needs_confirm_carries_what_the_re_run_needs(station):
    view, _, probe = station
    status, data = _post(view, "/api/run", {
        "name": "Fake Probe", "command": "toggle_estop"})
    assert data["status"] == "needs_confirm"
    assert data["command"] == "toggle_estop"
    assert data["args"] == [] and data["inputs"] == {}
    status, again = _post(view, "/api/run", {
        "name": "Fake Probe", "command": data["command"],
        "inputs": data["inputs"], "args": data["args"] + [True]})
    assert again["status"] == "ok" and probe.is_estopped is True


def test_a_command_the_schema_does_not_declare_is_refused(station):
    """The allow-list is the Panel's, not a list kept in the web layer."""
    view, _, _ = station
    _, data = _post(view, "/api/run", {
        "name": "Fake Probe", "command": "close"})
    assert data["status"] == "refused" and "not a command" in data["reason"]


def test_a_run_against_a_model_that_is_not_open_is_refused(station):
    view, _, _ = station
    _, data = _post(view, "/api/run", {"name": "Ghost", "command": "home"})
    assert data["status"] == "refused" and "not open" in data["reason"]


def test_setup_is_addressed_by_its_own_name(station):
    view, _, _ = station
    _, data = _post(view, "/api/run", {"name": SETUP_NAME, "command": "scan"})
    assert data["status"] == "ok" and data["value"] == 1
    assert view.setup.scanned == 1


def test_options_come_from_the_panel(station):
    view, _, _ = station
    _, data = _post(view, "/api/options", {
        "name": "Fake Probe", "command": "source_options"})
    assert data["options"] == ["None", "Probe A"]
    _, setup_options = _post(view, "/api/options", {
        "name": SETUP_NAME, "command": "port_options"})
    assert setup_options["options"] == ["SIM", "COM3"]


def test_an_undeclared_options_command_is_refused_not_served(station):
    view, _, _ = station
    _, data = _post(view, "/api/options", {
        "name": "Fake Probe", "command": "written_root"})
    assert data["status"] == "refused" and data["options"] == []


# --------------------------------------------------------------------------
# stop, open, close
# --------------------------------------------------------------------------
def test_estop_all_latches_every_model(station):
    view, controller, probe = station
    status, data = _post(view, "/api/estop_all", {})
    assert status == 200 and data["confirmed"] == {"Fake Probe": True}
    assert probe.is_estopped is True and data["is_estopped"] is True


def test_clearing_the_latch_asks_first(station):
    view, controller, probe = station
    probe.is_estopped = True
    _, asked = _post(view, "/api/clear_estop_all", {})
    assert asked["status"] == "needs_confirm"
    assert probe.is_estopped is True, "the latch cleared without confirmation"
    _, done = _post(view, "/api/clear_estop_all", {"confirmed": True})
    assert done["status"] == "ok" and probe.is_estopped is False


def test_closing_a_model_destructs_it_and_reopening_brings_it_back(station):
    view, controller, probe = station
    status, data = _post(view, "/api/close_model", {"name": "Fake Probe"})
    assert status == 200 and data["status"] == "ok"
    assert probe.is_open is False and probe.is_estopped is True
    _, state = _get(view, "/api/state")
    assert state["closed"] == ["Fake Probe"]
    status, data = _post(view, "/api/open_model", {"name": "Fake Probe"})
    assert status == 200 and "Fake Probe" in controller.model_names


def test_closing_a_model_that_is_not_open_is_a_404(station):
    view, _, _ = station
    status, data = _post(view, "/api/close_model", {"name": "Ghost"})
    assert status == 404 and data["status"] == "error"


def test_reopening_something_never_configured_is_reported_not_raised(station):
    view, _, _ = station
    status, data = _post(view, "/api/open_model", {"name": "Ghost"})
    assert status == 409 and "never configured" in data["reason"]


# --------------------------------------------------------------------------
# events: every reader sees every event (ERRORS-2, WEB-17)
# --------------------------------------------------------------------------
def test_events_are_not_consumed_by_the_first_reader(station):
    view, _, _ = station
    events.clear()
    before = events.latest_id
    events.info("Two Tabs", "one event", source="test")
    _, first = _get(view, f"/api/events?since={before}")
    _, second = _get(view, f"/api/events?since={before}")
    assert [e["title"] for e in first["events"]] == ["Two Tabs"]
    assert [e["title"] for e in second["events"]] == ["Two Tabs"], (
        "the second tab never saw the event the first one read")
    assert first["latest_id"] == second["latest_id"]


def test_events_since_the_latest_id_is_empty(station):
    view, _, _ = station
    _, answer = _get(view, f"/api/events?since={events.latest_id}")
    assert answer["events"] == []


# --------------------------------------------------------------------------
# data, files and the screen
# --------------------------------------------------------------------------
def test_a_plot_series_is_json_and_a_figure_is_a_png(station):
    view, _, _ = station
    status, data = _get(view, "/api/data?name=Fake+Probe&command=series")
    assert status == 200 and data["data"] == [[0, 1.0], [1, 2.0], [2, 1.5]]
    status, headers, body = _request(view, "/api/data?name=Fake+Probe&command=figure")
    assert status == 200 and headers["Content-Type"] == "image/png"
    assert body.startswith(b"\x89PNG")


def test_a_log_stream_comes_back_as_lines(station):
    view, _, _ = station
    _, data = _get(view, "/api/data?name=Fake+Probe&command=log_lines")
    assert data["lines"] == ["first line", "second line"]


def test_a_data_command_the_schema_does_not_declare_is_refused(station):
    view, _, _ = station
    _, data = _get(view, "/api/data?name=Fake+Probe&command=written_root")
    assert data["status"] == "refused"


def test_the_saved_file_downloads_as_an_attachment(station, tmp_path):
    view, _, probe = station
    status, headers, body = _request(view, "/api/file?name=Fake+Probe&command=save")
    assert status == 200, body
    assert body == b"red,x\n1,2\n"
    assert 'filename="run.csv"' in headers["Content-Disposition"]
    assert probe.written == str(tmp_path / "run.csv")


def test_a_save_command_carries_the_entry_values_like_any_other(station):
    """D-5 holds on the download route too: the file name typed into an entry
    a moment ago must travel with the save that uses it."""
    view, _, probe = station
    inputs = urllib.parse.quote(json.dumps({"label": "run-7"}))
    status, _, _ = _request(
        view, f"/api/file?name=Fake+Probe&command=save&inputs={inputs}")
    assert status == 200 and probe.label == "run-7"


def test_a_file_outside_the_models_output_root_is_refused(station, tmp_path):
    """The browser never sends a path; the server still checks the one the
    model reports, so a save command that wandered cannot serve /etc/passwd."""
    view, _, probe = station
    stray = tmp_path.parent / "stray.csv"
    stray.write_text("secret\n")
    probe.save = lambda: str(stray)
    status, _, body = _request(view, "/api/file?name=Fake+Probe&command=save")
    assert status == 403
    assert b"outside the model's output root" in body


def test_a_file_route_without_an_output_root_refuses_rather_than_guesses(station):
    view, _, probe = station
    probe.output_root = None
    status, _, body = _request(view, "/api/file?name=Fake+Probe&command=save")
    assert status == 409 and b"output root" in body


def test_the_screen_comes_from_the_model_that_owns_the_capture(station):
    view, _, _ = station
    status, data = _get(view, "/api/screen")
    assert status == 200
    assert data["image"].startswith("data:image/png;base64,")
    assert (data["width"], data["height"], data["left"]) == (2560, 1440, -1000)


def test_a_screen_command_that_returns_bare_png_bytes_still_works(station):
    """Geometry is optional; a picture is not."""
    view, _, probe = station
    probe.screen_image = lambda: b"\x89PNG\r\n\x1a\nplain"
    status, data = _get(view, "/api/screen")
    assert status == 200 and data["image"].startswith("data:image/png;base64,")


def test_no_model_offering_a_screen_image_is_an_honest_503(station):
    view, controller, _ = station
    controller.remove("Fake Probe")
    status, data = _get(view, "/api/screen")
    assert status == 503 and "region picker" in data["reason"]


# --------------------------------------------------------------------------
# heartbeat
# --------------------------------------------------------------------------
def test_a_heartbeat_arms_the_watchdog(station):
    view, _, _ = station
    assert view.heartbeat_age is None, "the gate was armed before any client"
    status, data = _post(view, "/api/heartbeat", {})
    assert status == 200 and data["status"] == "ok"
    assert view.heartbeat_age is not None


# --------------------------------------------------------------------------
# the security boundary (MANAGER-15, WEB-10)
# --------------------------------------------------------------------------
def test_a_cross_site_form_post_is_refused_on_content_type(station):
    """A cross-site <form> can only send text/plain, form-urlencoded or
    multipart. Requiring JSON forces a preflight the browser refuses."""
    view, _, probe = station
    status, _, _ = _request(view, "/api/run", "POST",
                            {"name": "Fake Probe", "command": "home"},
                            {"Content-Type": "text/plain"})
    assert status == 415
    assert probe.runs == []


def test_a_post_from_another_origin_is_refused(station):
    view, _, probe = station
    status, data = _post(view, "/api/run",
                         {"name": "Fake Probe", "command": "home"},
                         {"Origin": "https://evil.example"})
    assert status == 403 and data["status"] == "refused"
    assert probe.runs == []


def test_a_post_with_a_foreign_host_header_is_refused(station):
    view, _, _ = station
    status, _ = _post(view, "/api/run", {"name": "Fake Probe", "command": "home"},
                      {"Host": "stage.example.com"})
    assert status == 403


def test_the_dashboards_own_origin_is_accepted(station):
    view, _, _ = station
    status, data = _post(view, "/api/run",
                         {"name": "Fake Probe", "command": "home"},
                         {"Origin": f"http://127.0.0.1:{view.port}"})
    assert status == 200 and data["status"] == "ok"


def test_the_screen_route_is_guarded_like_a_command(station):
    """It reads the operator's actual screen, so a foreign origin is refused
    even though it is a GET."""
    view, _, _ = station
    status, _, _ = _request(view, "/api/screen", headers={
        "Origin": "https://evil.example"})
    assert status == 403


def test_reads_stay_open_so_the_first_paint_works(station):
    view, _, _ = station
    status, _ = _get(view, "/api/state")
    assert status == 200


def test_a_non_local_client_is_refused_whatever_it_sends():
    """The socket binds 127.0.0.1, so this is belt and braces - but the check
    is the one that survives a reverse proxy being put in front."""
    handler = ApiHandler.__new__(ApiHandler)
    handler.client_address = ("10.0.0.9", 51000)
    handler.command, handler.path = "POST", "/api/run"
    handler.headers = email.message.Message()
    handler.headers["Content-Type"] = "application/json"
    sent = {}
    handler._send_json = lambda status, data: sent.update(status=status, data=data)
    assert handler._is_local(require_json=True) is False
    assert sent["status"] == 403 and "localhost only" in sent["data"]["reason"]


def test_there_is_no_session_token_left_to_leak(station):
    """Owner ruling 2026-09-21: localhost only, the token is purged. The old
    server injected it into the HTML it served."""
    view, _, _ = station
    _, _, body = _request(view, "/index.html")
    assert b"stage-token" not in body


# --------------------------------------------------------------------------
# a handler that raises still answers (WEB-14)
# --------------------------------------------------------------------------
def test_a_route_that_raises_answers_with_json_not_a_dropped_connection(station):
    view, _, probe = station
    probe.state_raises = True
    status, _, body = _request(view, "/api/state")
    assert status == 500
    assert json.loads(body.decode("utf-8"))["status"] == "error"


def test_an_unparseable_body_is_a_400(station):
    view, _, _ = station
    status, _, _ = _request(view, "/api/run", "POST", None,
                            {"Content-Type": "application/json"})
    # an empty body is read as {} and rejected as a missing command, not a 400
    assert status == 200
    url = f"http://127.0.0.1:{view.port}/api/run"
    request = urllib.request.Request(url, data=b"{not json", method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    assert status == 400


def test_a_body_larger_than_the_cap_is_refused_before_it_is_read(station):
    view, _, _ = station
    status, _, _ = _request(view, "/api/run", "POST",
                            {"name": "x", "pad": "y" * (ApiHandler.MAX_BODY_BYTES + 10)},
                            {"Content-Type": "application/json"})
    assert status == 413


def test_a_route_that_does_not_exist_is_a_json_404(station):
    view, _, _ = station
    status, data = _get(view, "/api/nope")
    assert status == 404 and data["status"] == "error"
