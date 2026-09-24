"""The Web API, driven over a real socket on an ephemeral port.

No browser, no hardware: a real `Controller` holding a fake Panel-shaped
model, the real `Setup`-shaped panel, and `urllib` playing the browser - and,
where it matters, playing a cross-site page instead.

Ported from `tests/web/test_web_server.py`, `test_web_security.py`,
`test_web_19_client_heartbeat.py` and `test_dc6_web_refusal_is_403.py`.
"""
import base64
import email.message
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import pytest

import schema as sch
from controller.controller import Controller
from events import events
from panel import Panel
from param import Param
from result import NeedsConfirm, Refused
from views.web.server import ApiHandler, SETUP_NAME, WebView


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
        # For the browser tests (Tier F): what the model says about its
        # devices, whether its stop confirms, and what it was asked to load.
        self.devices_state = {}
        self.stop_confirms = True
        self.jams = 0
        self.loaded = None

    # -- what the Controller needs -------------------------------------
    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def estop(self):
        self.is_estopped = True
        return self.stop_confirms

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
                sch.button("Jam", "jam"),
                sch.file_open("Load run", "load_run", extensions=("csv",)),
            ))

    @property
    def state(self):
        if self.state_raises:
            raise RuntimeError("the model blew up")
        snapshot = super().state
        snapshot.update({"age": 0.0, "is_estopped": self.is_estopped,
                         "is_active": self.is_active,
                         "devices": dict(self.devices_state),
                         "values": dict(snapshot["values"],
                                        output_root=self.output_root or "")})
        return snapshot

    # -- commands --------------------------------------------------------
    def home(self):
        self.runs.append(("home", self.x_step))
        return "homed"

    def park(self):
        raise Refused("the stage is not parked from here")

    def jam(self):
        # A command that FAILS (not refuses): each one is an error that asks
        # to be acknowledged, and each message is new so none are merged.
        self.jams += 1
        raise OSError(f"the port did not answer ({self.jams})")

    def load_run(self, path):
        self.loaded = path
        return {"samples": 1}

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
        self.detected = "not found"

    @property
    def schema(self):
        # Addendum 2's shape: a header section, then one ROW per model type.
        # `layout` is the renderer's only instruction to lay a section out
        # horizontally, so it has to survive the trip to the browser.
        return sch.schema(
            sch.section(
                "Hardware",
                sch.button("Refresh", "scan"),
            ),
            sch.section(
                "Fake Probe",
                sch.dropdown("Port", "port", "set_port", "port_options"),
                sch.readonly("Detected:", "detected"),
                layout="row",
            ),
        )

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


def test_setup_carries_every_sections_layout_through_unchanged(station):
    """Addendum 2: `layout` is how the schema asks for one line per model,
    and the Web client is the renderer furthest from it - the hint crosses a
    JSON boundary that no desktop view crosses. A route that dropped or
    defaulted it would leave the browser drawing the vertical wizard the
    owner rejected, with nothing failing anywhere else."""
    view, _, _ = station
    status, data = _get(view, "/api/setup")
    assert status == 200
    served = data["schema"]["sections"]
    declared = view.setup.schema["sections"]
    assert [s["layout"] for s in served] == [s["layout"] for s in declared]
    assert [s["layout"] for s in served] == ["column", "row"]
    rows = [s for s in served if s["layout"] == "row"]
    assert [s["title"] for s in rows] == ["Fake Probe"]
    assert [e["type"] for e in rows[0]["elements"]] == ["dropdown", "readonly"]


def test_a_models_schema_carries_its_section_layout_too(station):
    """The same hint, the other route: nothing about `layout` is specific to
    Setup."""
    view, controller, probe = station
    status, data = _get(view, "/api/schema?name=Fake+Probe")
    assert status == 200
    declared = probe.schema["sections"]
    assert [s["layout"] for s in data["sections"]] == [s["layout"] for s in declared]


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


# --------------------------------------------------------------------------
# /api/upload: a file chosen in the browser, for a file_open command (F13)
# --------------------------------------------------------------------------
def _upload(view, filename, content=b"red,x\n1,2\n", command="load_run"):
    return _post(view, "/api/upload", {
        "name": "Fake Probe", "command": command, "filename": filename,
        "content": base64.b64encode(content).decode("ascii")})


def test_an_uploaded_run_lands_in_the_models_output_folder(station, tmp_path):
    view, _, _ = station
    status, data = _upload(view, "run 1.csv")
    assert status == 200 and data["status"] == "ok"
    assert data["path"] == os.path.join(os.path.realpath(tmp_path), "uploads", "run 1.csv")
    with open(data["path"], "rb") as handle:
        assert handle.read() == b"red,x\n1,2\n"
    # never overwritten
    _, again = _upload(view, "run 1.csv", b"second")
    assert again["path"].endswith("run 1-1.csv")


def test_an_upload_is_a_bare_name_with_a_declared_extension(station, tmp_path):
    view, _, _ = station
    _, data = _upload(view, "../../escape.csv")
    assert data["path"] == os.path.join(os.path.realpath(tmp_path), "uploads", "escape.csv")
    status, data = _upload(view, "notes.txt")
    assert status == 400 and data["status"] == "refused"
    assert data["reason"].startswith("Choose a .csv file")
    status, data = _upload(view, "run.csv", command="home")
    assert status == 403, "an upload for a command that opens no file"


def test_an_upload_goes_through_the_same_guards_as_a_command(station):
    view, _, _ = station
    status, _ = _post(view, "/api/upload", {"name": "Fake Probe"},
                      headers={"Origin": "http://evil.example"})
    assert status == 403


# --------------------------------------------------------------------------
# The client in a real browser (Tier F). Headless Chrome through puppeteer,
# against the real server above; skipped where node or puppeteer is absent.
# Each test is a stop-path or focus behaviour a static read of app.js cannot
# see: what is on top at the stop's centre, what happens when a fetch fails.
# --------------------------------------------------------------------------
PUPPETEER = os.environ.get(
    "STATION_PUPPETEER",
    "/opt/homebrew/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer")
NODE = shutil.which("node")
needs_browser = pytest.mark.skipif(
    NODE is None or not os.path.isdir(PUPPETEER),
    reason="node and puppeteer are not available in this environment")

#: What every scenario starts with: the page loaded and the probe's card up.
_PRELUDE = r"""
const puppeteer = require(%(puppeteer)s);
const BASE = %(base)s;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] });
  const errors = [];
  let result;
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1400, height: 900 });
    page.on('pageerror', (e) => errors.push(e.message));
    const until = async (fn, ms) => {
      const end = Date.now() + (ms || 5000);
      for (;;) {
        const got = await page.evaluate(fn);
        if (got || Date.now() > end) return got;
        await sleep(100);
      }
    };
    const card = () => until(() => Array.from(document.querySelectorAll('.card'))
      .some((c) => !c.classList.contains('setup-card')));
    const api = (path, body) => page.evaluate(async (p, b) => {
      const r = await fetch(p, b === undefined ? {} : { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
      return r.json();
    }, path, body);
    await page.goto(BASE + '/', { waitUntil: 'load' });
    await card();
    await sleep(400);
    result = await (async () => {
%(body)s
    })();
  } finally {
    await browser.close();
  }
  console.log('RESULT ' + JSON.stringify({ result, errors }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _browse(view, body, tmp_path):
    script = tmp_path / "scenario.cjs"
    script.write_text(_PRELUDE % {"puppeteer": json.dumps(PUPPETEER),
                                  "base": json.dumps(f"http://127.0.0.1:{view.port}"),
                                  "body": body})
    done = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=90)
    assert done.returncode == 0, done.stderr[-2000:]
    line = [ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")][-1]
    out = json.loads(line[len("RESULT "):])
    assert not out["errors"], f"the page threw: {out['errors']}"
    return out["result"]


#: What is on top at the centre of the rail's stop.
_STOP_HIT = r"""() => {
  const b = document.getElementById('full-stop').getBoundingClientRect();
  const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
  return Boolean(hit && hit.closest('#full-stop'));
}"""


@needs_browser
def test_an_acknowledgement_never_covers_the_stop_and_two_both_show(station, tmp_path):
    """F1 (WDG-1, HC-2): with an ack open the stop is still what a click at
    its centre lands on, it still stops, and a second ack is added under the
    first instead of replacing it."""
    view, controller, probe = station
    out = _browse(view, r"""
      await api('/api/run', { name: 'Fake Probe', command: 'jam', inputs: {}, args: [] });
      await api('/api/run', { name: 'Fake Probe', command: 'jam', inputs: {}, args: [] });
      await until(() => !document.getElementById('ack-modal').hidden
        && document.querySelectorAll('#ack-text .ack-line').length === 2);
      const lines = await page.evaluate(() => Array.from(
        document.querySelectorAll('#ack-text .ack-line')).map((n) => n.textContent));
      const onTop = await page.evaluate(%s);
      const box = await page.evaluate(() => {
        const b = document.getElementById('full-stop').getBoundingClientRect();
        return [b.left + b.width / 2, b.top + b.height / 2];
      });
      await page.mouse.click(box[0], box[1]);
      await sleep(600);
      const state = await api('/api/state');
      return { lines, onTop, latched: state.is_estopped };
    """ % _STOP_HIT, tmp_path)
    assert out["onTop"], "the ack overlay covers the stop"
    assert len(out["lines"]) == 2 and out["lines"][0] != out["lines"][1], out["lines"]
    assert all("the port did not answer" in line for line in out["lines"])
    assert out["latched"] is True, "a click on the stop under an open ack did not stop"


@needs_browser
def test_a_stop_that_never_reaches_the_station_says_so_on_the_rail(station, tmp_path):
    """F2 (WDG-2, CRIT-1, HC-3): the fetch is refused; the rail says the stop
    did not reach the station and the link says it is not answering."""
    view, controller, probe = station
    out = _browse(view, r"""
      await page.setRequestInterception(true);
      page.on('request', (r) => (r.url().includes('/api/estop_all') ? r.abort() : r.continue()));
      await page.click('#full-stop');
      await until(() => !document.getElementById('rail-alert').hidden);
      return page.evaluate(() => ({
        alert: document.getElementById('rail-alert').textContent,
        link: document.getElementById('connection').textContent,
        face: document.querySelector('#full-stop .mushroom-face').textContent,
      }));
    """, tmp_path)
    assert out["alert"].startswith("Stop did not reach the station — "), out
    assert out["link"].startswith("Not answering"), out
    assert out["face"] == "Stop", "the stop claims a latch it never delivered"
    assert probe.is_estopped is False


@needs_browser
def test_a_stop_a_model_did_not_confirm_names_that_model(station, tmp_path):
    """F2: `unconfirmed` is surfaced by model name, on the rail."""
    view, controller, probe = station
    probe.stop_confirms = False
    out = _browse(view, r"""
      await page.click('#full-stop');
      await until(() => !document.getElementById('rail-alert').hidden);
      return page.evaluate(() => document.getElementById('rail-alert').textContent);
    """, tmp_path)
    assert "Fake Probe has not confirmed it" in out, out


@needs_browser
def test_offline_every_readout_is_muted_and_marked_stale(station, tmp_path):
    """F4 (CRIT-1): the state poll fails; nothing still looks live."""
    view, _, _ = station
    out = _browse(view, r"""
      const live = await page.evaluate(() => {
        const v = Array.from(document.querySelectorAll('.card .value'))
          .find((n) => n.textContent === '0.000');
        return getComputedStyle(v).color;
      });
      await page.setRequestInterception(true);
      page.on('request', (r) => (r.url().includes('/api/state') ? r.abort() : r.continue()));
      await until(() => document.body.classList.contains('is-offline'));
      await sleep(300);
      return page.evaluate((live) => {
        const card = Array.from(document.querySelectorAll('.card'))
          .find((c) => !c.classList.contains('setup-card'));
        const v = Array.from(card.querySelectorAll('.value')).find((n) => n.textContent === '0.000');
        const rail = document.querySelector('.readout-value');
        const muted = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
        const probe = document.createElement('span');
        probe.style.color = muted;
        document.body.appendChild(probe);
        const mutedRgb = getComputedStyle(probe).color;
        return {
          live, mutedRgb, card: getComputedStyle(v).color, rail: getComputedStyle(rail).color,
          badge: !card.querySelector('.stale-badge').hidden,
          isLive: card.classList.contains('is-live'),
          railFlag: document.querySelector('.readout-flag').textContent,
          link: document.getElementById('connection').textContent,
        };
      }, live);
    """, tmp_path)
    assert out["live"] != out["mutedRgb"]
    assert out["card"] == out["mutedRgb"] and out["rail"] == out["mutedRgb"], out
    assert out["badge"] and not out["isLive"] and out["railFlag"] == "Stale", out
    assert re.fullmatch(r"Not answering since \d\d:\d\d:\d\d", out["link"]), out


@needs_browser
def test_the_stop_has_a_keyboard_path_and_an_ink_focus_ring(station, tmp_path):
    """F9 + F17: Ctrl+. and Cmd+. stop from inside a text box; Enter on the focused
    mushroom stops; its focus ring is --stop-focus, not the latched trace;
    the clear confirmation opens on Cancel and Enter there does not clear."""
    view, controller, probe = station
    out = _browse(view, r"""
      const r = {};
      await page.focus('input[name="label"]');
      await page.keyboard.down('Control'); await page.keyboard.press('Period'); await page.keyboard.up('Control');
      await sleep(500);
      r.byShortcut = (await api('/api/state')).is_estopped;
      r.typed = await page.evaluate(() => document.querySelector('input[name="label"]').value);
      await api('/api/clear_estop_all', { confirmed: true });
      await sleep(500);
      await page.focus('input[name="label"]');
      await page.keyboard.down('Meta'); await page.keyboard.press('Period'); await page.keyboard.up('Meta');
      await sleep(500);
      r.byCmd = (await api('/api/state')).is_estopped;
      await api('/api/clear_estop_all', { confirmed: true });
      await sleep(500);
      for (let i = 0; i < 40; i++) {
        await page.keyboard.press('Tab');
        if (await page.evaluate(() => document.activeElement.id === 'full-stop')) break;
      }
      r.ring = await page.evaluate(() => getComputedStyle(document.getElementById('full-stop')).outlineColor);
      r.focusToken = await page.evaluate(() => {
        const s = document.createElement('span');
        s.style.color = getComputedStyle(document.documentElement).getPropertyValue('--stop-focus').trim();
        document.body.appendChild(s);
        return getComputedStyle(s).color;
      });
      r.title = await page.evaluate(() => document.getElementById('full-stop').title);
      await page.keyboard.press('Enter');
      await sleep(600);
      r.byEnter = (await api('/api/state')).is_estopped;
      await page.focus('#full-stop');
      await page.keyboard.press('Enter');
      await until(() => !document.getElementById('confirm-modal').hidden);
      r.defaultFocus = await page.evaluate(() => document.activeElement.id);
      await page.keyboard.press('Enter');
      await sleep(600);
      r.afterCancel = (await api('/api/state')).is_estopped;
      r.confirmHidden = await page.evaluate(() => document.getElementById('confirm-modal').hidden);
      return r;
    """, tmp_path)
    assert out["byShortcut"] is True, "Ctrl+. did not stop from a text box"
    assert out["byCmd"] is True, "Cmd+. did not stop"
    assert out["typed"] == "", "the shortcut typed into the entry"
    assert "Ctrl+." in out["title"] and "Cmd+." in out["title"]
    assert out["ring"] == out["focusToken"], out
    assert out["byEnter"] is True, "Enter on the focused stop did not stop"
    assert out["defaultFocus"] == "confirm-no", "the clear confirmation defaults to clearing"
    assert out["afterCancel"] is True and out["confirmHidden"], out


@needs_browser
def test_a_lost_device_turns_the_card_signal_and_the_rail_names_it(station, tmp_path):
    """F3 (HC-1): `state.devices` says the serial port is lost while the
    model's own loop still ticks - the card must not look live."""
    view, _, probe = station
    probe.devices_state = {"SerialPort": "lost"}
    out = _browse(view, r"""
      await until(() => !document.getElementById('rail-alert').hidden);
      return page.evaluate(() => {
        const card = Array.from(document.querySelectorAll('.card'))
          .find((c) => !c.classList.contains('setup-card'));
        const s = document.createElement('span');
        s.style.color = getComputedStyle(document.documentElement).getPropertyValue('--signal').trim();
        document.body.appendChild(s);
        const muted = document.createElement('span');
        muted.style.color = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
        document.body.appendChild(muted);
        const v = Array.from(card.querySelectorAll('.value')).find((n) => n.textContent === '0.000');
        return {
          cls: card.className, bar: getComputedStyle(card).borderLeftColor,
          signal: getComputedStyle(s).color, value: getComputedStyle(v).color,
          muted: getComputedStyle(muted).color,
          badge: card.querySelector('.stale-badge').hidden ? '' : card.querySelector('.stale-badge').textContent,
          rail: document.getElementById('rail-alert').textContent,
          flag: document.querySelector('.readout-flag').textContent,
        };
      });
    """, tmp_path)
    assert "is-lost" in out["cls"] and "is-live" not in out["cls"], out
    assert out["bar"] == out["signal"] and out["value"] == out["muted"], out
    assert out["badge"] == "Connection lost" and out["flag"] == "Connection lost", out
    assert "Fake Probe lost its serial port" in out["rail"], out


@needs_browser
def test_load_run_sends_a_typed_path_or_an_uploaded_file(station, tmp_path):
    """F13 (WDG-5): the command gets the path it declares."""
    view, _, probe = station
    chosen = tmp_path / "chosen.csv"
    chosen.write_text("red,x\n1,2\n")
    out = _browse(view, r"""
      await page.type('.path-input', '/data/runs/typed.csv');
      await page.evaluate(() => Array.from(document.querySelectorAll('.file-open button'))
        .find((b) => b.textContent === 'Load run').click());
      await sleep(600);
      const typed = await page.evaluate(() => document.querySelector('.file-open').closest('.card')
        .querySelector('.status').hidden);
      const input = await page.$('.file-picker');
      await input.uploadFile(%s);
      await sleep(1200);
      return { typedStatusHidden: typed };
    """ % json.dumps(str(chosen)), tmp_path)
    assert out["typedStatusHidden"] is True
    assert probe.loaded == os.path.join(os.path.realpath(tmp_path), "uploads", "chosen.csv")


@needs_browser
def test_a_refusal_sits_under_its_control_in_view_and_clears_on_success(station, tmp_path):
    """F10 (CRIT-3): the refusal is next to Park, visible below the rail, and
    the next good command from the card clears it."""
    view, _, _ = station
    out = _browse(view, r"""
      const click = (text) => page.evaluate((t) => Array.from(document.querySelectorAll('.card button'))
        .find((b) => b.textContent === t).click(), text);
      await click('Park');
      await until(() => Array.from(document.querySelectorAll('.card .status')).some((s) => !s.hidden));
      const placed = await page.evaluate(() => {
        const status = Array.from(document.querySelectorAll('.card .status')).find((s) => !s.hidden);
        const park = Array.from(document.querySelectorAll('.card button')).find((b) => b.textContent === 'Park');
        const group = park.closest('.actions') || park.closest('.row');
        const box = status.getBoundingClientRect();
        const rail = document.querySelector('.rail').getBoundingClientRect();
        return { next: group.nextElementSibling === status, text: status.textContent,
                 inView: box.top >= rail.bottom && box.bottom <= innerHeight };
      });
      await click('Home');
      await sleep(600);
      placed.cleared = await page.evaluate(() => Array.from(document.querySelectorAll('.card .status')).every((s) => s.hidden));
      return placed;
    """, tmp_path)
    assert out["next"], "the refusal is not under the control that caused it"
    assert out["text"] == "the stage is not parked from here"
    assert out["inView"], "the refusal landed off screen or under the rail"
    assert out["cleared"], "a successful command did not clear the refusal"


@needs_browser
def test_focus_is_contained_returned_and_never_torn_down_by_a_poll(station, tmp_path):
    """F12 (CRIT-5, WDG-3/6/7): the open drawer keeps Tab out of the rack;
    Escape returns focus to Setup; a Reopen button survives the poll; the
    link live region is not rewritten while nothing changes."""
    view, controller, _ = station
    out = _browse(view, r"""
      const r = {};
      await page.click('#setup-link');
      await sleep(400);
      const visited = [];
      for (let i = 0; i < 30; i++) {
        await page.keyboard.press('Tab');
        visited.push(await page.evaluate(() => Boolean(document.activeElement.closest('#cards'))));
      }
      r.leaked = visited.some(Boolean);
      await page.focus('#drawer-body select');
      await page.keyboard.press('Escape');
      await sleep(400);
      r.returned = await page.evaluate(() => document.activeElement.id);
      r.mutations = await page.evaluate(() => new Promise((done) => {
        let n = 0;
        const watch = new MutationObserver((m) => { n += m.length; });
        watch.observe(document.getElementById('connection'), { childList: true, characterData: true, subtree: true });
        setTimeout(() => { watch.disconnect(); done(n); }, 1500);
      }));
      await api('/api/close_model', { name: 'Fake Probe' });
      await until(() => document.querySelector('#closed-models button'));
      await page.focus('#closed-models button');
      await sleep(1200);
      r.kept = await page.evaluate(() => Boolean(document.activeElement.closest('#closed-models')));
      return r;
    """, tmp_path)
    assert not out["leaked"], "Tab walked into the rack behind the open drawer"
    assert out["returned"] == "setup-link", out
    assert out["mutations"] == 0, "the link live region is rewritten every poll"
    assert out["kept"], "the Reopen button lost focus to a poll"


@needs_browser
def test_an_idle_poll_changes_nothing_and_a_word_is_not_a_number(station, tmp_path):
    """F21, F24, F15: two seconds of idle polling mutate nothing in the rack
    (the figure's own reload aside - F16 is core); trace is for numbers and
    "Not set" is muted; a long option is elided from the middle with its
    whole name as the title; leaving the page while active asks first."""
    view, _, probe = station
    probe.source_options = ["None", "/dev/cu.usbmodem1234567890123"]
    probe.is_active = True
    out = _browse(view, r"""
      const r = {};
      r.mutations = await page.evaluate(() => new Promise((done) => {
        const seen = [];
        const watch = new MutationObserver((list) => {
          for (const m of list) {
            if (m.type === 'attributes' && m.target.tagName === 'IMG' && m.attributeName === 'src') continue;
            seen.push(m.type + ':' + (m.target.className || m.target.nodeName) + ':' + (m.attributeName || ''));
          }
        });
        watch.observe(document.getElementById('cards'), { subtree: true, childList: true, attributes: true, characterData: true });
        setTimeout(() => { watch.disconnect(); done(seen); }, 2000);
      }));
      r.colors = await page.evaluate(() => {
        const root = getComputedStyle(document.documentElement);
        const as = (token) => { const s = document.createElement('span'); s.style.color = root.getPropertyValue(token).trim(); document.body.appendChild(s); return getComputedStyle(s).color; };
        const value = (t) => getComputedStyle(Array.from(document.querySelectorAll('.card .value')).find((n) => n.textContent === t)).color;
        return { number: value('0.000'), notSet: value('Not set'), trace: as('--trace'), muted: as('--muted') };
      });
      r.option = await page.evaluate(() => {
        const o = Array.from(document.querySelectorAll('.card select option')).find((x) => x.value.startsWith('/dev/'));
        return o ? { text: o.textContent, title: o.title } : null;
      });
      r.leaveAsks = await page.evaluate(() => {
        const e = new Event('beforeunload', { cancelable: true });
        window.dispatchEvent(e);
        return e.defaultPrevented;
      });
      return r;
    """, tmp_path)
    assert out["mutations"] == [], out["mutations"][:10]
    assert out["colors"]["number"] == out["colors"]["trace"]
    assert out["colors"]["notSet"] == out["colors"]["muted"]
    assert out["option"]["title"] == "/dev/cu.usbmodem1234567890123"
    assert "…" in out["option"]["text"] and out["option"]["text"].endswith("7890123")
    assert out["leaveAsks"] is True
