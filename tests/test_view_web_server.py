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
import threading
import time
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
        # L1 (round 7): what `Model.stop_confirmed` says - None unless
        # latched, then whether the hardware confirmed. The Controller's
        # `stop_state` and the per-entry mark read it.
        self.stop_confirmed = None
        self.jams = 0
        self.loaded = None
        # G4: how often the detached gamepad log's source was asked for.
        self.gamepad_calls = 0

    # -- what the Controller needs -------------------------------------
    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def estop(self):
        self.is_estopped = True
        self.stop_confirmed = self.stop_confirms
        return self.stop_confirms

    def clear_estop(self, confirmed=False):
        self.is_estopped = False
        self.stop_confirmed = None

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
                # Updated (O16): the per-model switch's words as core's
                # Model schema now says them ("Stop this model" / "Stopped").
                sch.toggle("Stop", "is_estopped", "toggle_estop",
                           "Stopped", "Stop this model", on_role="danger",
                           off_role="danger"),
                sch.dropdown("Source", "source", "set_source", "source_options"),
                region,
                sch.file_save("Save", "save"),
                sch.plot("Red", "series"),
                sch.image("Figure", "figure"),
                sch.log_stream("Log", "log_lines"),
                # G4: behind a button, polled only while its panel is open.
                sch.log_stream("Gamepad Log:", "gamepad_lines", detached=True),
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
                         "stop_confirmed": self.stop_confirmed if self.is_estopped else None,
                         "is_active": self.is_active,
                         "devices": dict(self.devices_state)})
        # CON-5: where every real model publishes it (`Model.state`): at the
        # top level of its state, not among the values. The fake used to put
        # it in `values`, which hid a 409 on the real Red Percent's Save.
        if self.output_root:
            snapshot["output_root"] = str(self.output_root)
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

    def gamepad_lines(self):
        self.gamepad_calls += 1
        return ["LX +0.50", "button A down"]

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
        # G3: the row's Launch box. Ticked here so the existing browser
        # scenarios find a live Port dropdown; the checkbox test unticks it.
        self.probe_enabled = True
        self.ticks = []
        self.refuse_ticks = 0         # how many ticks to refuse, then accept

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
                # G3: the Launch box first, and the dropdown live only while
                # it is ticked - the real Setup row's shape.
                sch.checkbox("Launch", "probe_enabled", "set_probe_enabled",
                             tooltip="Launch Fake Probe"),
                sch.dropdown("Port", "port", "set_port", "port_options",
                             enabled_by="probe_enabled"),
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

    def set_probe_enabled(self, flag):
        # What the browser sent, exactly: a JSON boolean arrives as a bool.
        self.ticks.append(flag)
        if self.refuse_ticks:
            self.refuse_ticks -= 1
            raise Refused("the row cannot be ticked right now")
        self.probe_enabled = bool(flag)
        return self.probe_enabled


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
    # A browser names its origin on every JSON POST; since round 8 (WDG8-1)
    # the server requires it, so the helper sends what a browser would.
    sent = {"Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{view.port}"}
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
    # G3: the Launch box first; its gate and its accessible name reach the
    # browser untouched - the server special-cases no element type.
    assert [e["type"] for e in rows[0]["elements"]] == ["checkbox", "dropdown", "readonly"]
    box, port = rows[0]["elements"][:2]
    assert box["tooltip"] == "Launch Fake Probe" and box["command"] == "set_probe_enabled"
    assert port["enabled_by"] == "probe_enabled"
    assert data["state"]["values"]["probe_enabled"] is True, "a boolean became a string"


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
    assert status == 200 and b"<title>Transfer stage</title>" in body  # sentence case, as the rail and Tk name the station (L22)
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
                            {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{view.port}"})
    # an empty body is read as {} and rejected as a missing command, not a 400
    assert status == 200
    url = f"http://127.0.0.1:{view.port}/api/run"
    request = urllib.request.Request(url, data=b"{not json", method="POST",
                                     headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{view.port}"})
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
                            {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{view.port}"})
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
    // `until` waits on a function run IN the page; `when` on one run here.
    const when = async (fn, ms) => {
      const end = Date.now() + (ms || 5000);
      for (;;) {
        const got = await fn();
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
    its centre lands on, it still stops, and a second ack is kept, not
    written over the first.

    Updated (rb-ack A3): the dialog shows one title at a time, so the
    second failure is no longer a second line under the first. Two jams are
    one title: the log folds the identical second one into the first's
    count, or - past its window - the page folds it into the open entry.
    Either way it is one dialog, and nothing was overwritten."""
    view, controller, probe = station
    out = _browse(view, r"""
      await api('/api/run', { name: 'Fake Probe', command: 'jam', inputs: {}, args: [] });
      await api('/api/run', { name: 'Fake Probe', command: 'jam', inputs: {}, args: [] });
      await until(() => !document.getElementById('ack-modal').hidden);
      await sleep(600);
      const lines = await page.evaluate(() => Array.from(
        document.querySelectorAll('#ack-text .ack-line')).map((n) => n.textContent));
      const title = await page.evaluate(() => document.getElementById('ack-title').textContent);
      const onTop = await page.evaluate(%s);
      const box = await page.evaluate(() => {
        const b = document.getElementById('full-stop').getBoundingClientRect();
        return [b.left + b.width / 2, b.top + b.height / 2];
      });
      await page.mouse.click(box[0], box[1]);
      await sleep(600);
      const state = await api('/api/state');
      return { lines, title, onTop, latched: state.is_estopped };
    """ % _STOP_HIT, tmp_path)
    assert out["onTop"], "the ack overlay covers the stop"
    assert out["title"] == "Command failed", out
    lines = out["lines"]
    assert len(lines) == 1, lines
    # F19: the ack carries the operator sentence; the raw detail ("the port
    # did not answer") now goes to the log file only.
    assert "did not complete" in lines[0], lines
    assert out["latched"] is True, "a click on the stop under an open ack did not stop"


def _publish_when(probe, steps):
    """Publish from the station side once the page has asked for it: each
    step is (a predicate on the fake probe, a function that publishes). The
    page triggers a step with a command it runs (jam, home), so an event is
    never published before the tab opened (which would make it history)."""
    def watch():
        for ready, publish in steps:
            deadline = time.monotonic() + 30.0
            while not ready(probe) and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(0.3)
            publish()
    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


#: What the acknowledgement dialog says right now.
_ACK_NOW = r"""() => {
  const count = document.getElementById('ack-count');
  const title = document.getElementById('ack-title');
  const ok = document.getElementById('ack-ok');
  return {
    open: !document.getElementById('ack-modal').hidden,
    title: title ? title.textContent : null,
    body: Array.from(document.querySelectorAll('#ack-text .ack-line')).map((n) => n.textContent),
    waiting: count.hidden ? '' : count.textContent,
    key: ok.textContent.trim(),
    buttons: document.querySelectorAll('#ack-modal button').length,
    focused: document.activeElement === ok,
    labelledBy: document.querySelector('#ack-modal .dialog').getAttribute('aria-labelledby'),
  };
}"""


@needs_browser
def test_ack_one_title_at_a_time_understood_escape_and_return_answer_it(
        station, tmp_path):
    """rb-ack A3: the dialog wears the event's title as its heading and the
    message as its body; ONE key, Understood, focused; a second and a third
    title wait behind the first; the stop works under it and does not
    answer it; Escape, Return and a click each acknowledge one title."""
    view, controller, probe = station
    events.clear()      # an earlier test's jam must not fold this one's
    _publish_when(probe, [(lambda p: p.jams >= 1, lambda: (
        events.warn(events.IDLE_TIMEOUT, "Fake Probe was idle for 300 s, so "
                    "it was powered down.", source="Fake Probe", ack=True),
        events.warn(events.ROTATOR_UNREACHABLE, "The stage stopped answering. "
                    "Check its cable and power.", source="Rotator", ack=True)))])
    out = _browse(view, r"""
      const ack = () => page.evaluate(%(now)s);
      await api('/api/run', { name: 'Fake Probe', command: 'jam', inputs: {}, args: [] });
      await until(() => document.getElementById('ack-count').textContent === '2 more waiting', 10000);
      const first = await ack();
      const onTop = await page.evaluate(%(hit)s);
      const box = await page.evaluate(() => {
        const b = document.getElementById('full-stop').getBoundingClientRect();
        return [b.left + b.width / 2, b.top + b.height / 2];
      });
      await page.mouse.click(box[0], box[1]);
      await sleep(600);
      const latched = (await api('/api/state')).is_estopped;
      const afterStop = await ack();
      await page.keyboard.press('Escape');
      await sleep(200);
      const second = await ack();
      await page.keyboard.press('Enter');
      await sleep(200);
      const third = await ack();
      if (third.open) await page.click('#ack-ok');
      await sleep(200);
      const done = await ack();
      return { first, onTop, latched, afterStop, second, third, done };
    """ % {"now": _ACK_NOW, "hit": _STOP_HIT}, tmp_path)
    first = out["first"]
    assert first["open"] and first["title"] == "Command failed", first
    assert first["body"] and first["body"][0].startswith("Jam did not complete"), first
    assert first["waiting"] == "2 more waiting", first
    assert first["key"] == "Understood" and first["buttons"] == 1, first
    assert first["focused"], "Understood is the default"
    assert first["labelledBy"] == "ack-title", first
    assert out["onTop"], "the ack overlay covers the stop"
    assert out["latched"] is True, "the stop under an open ack did not stop"
    assert out["afterStop"]["open"] and out["afterStop"]["title"] == "Command failed", (
        "the stop answered the acknowledgement for the operator")
    second = out["second"]
    assert second["open"] and second["title"] == "Idle timeout", second
    assert second["body"] == ["Fake Probe was idle for 300 s, so it was powered down."]
    assert second["waiting"] == "1 more waiting", second
    third = out["third"]
    assert third["open"] and third["title"] == "Rotator unreachable", third
    assert third["waiting"] == "", third
    assert out["done"]["open"] is False, out["done"]


@needs_browser
def test_ack_a_repeat_of_the_open_title_counts_and_does_not_reopen(station,
                                                                   tmp_path):
    view, controller, probe = station
    events.clear()      # an earlier test's jam must not fold this one's
    _publish_when(probe, [
        (lambda p: p.jams >= 1, lambda: events.warn(
            events.IDLE_TIMEOUT, "Fake Probe was idle for 300 s, so it was "
            "powered down.", source="Fake Probe", ack=True)),
        (lambda p: any(run[0] == "home" for run in p.runs), lambda: events.warn(
            events.IDLE_TIMEOUT, "Fake Probe was idle for 301 s, so it was "
            "powered down.", source="Fake Probe", ack=True)),
    ])
    out = _browse(view, r"""
      const ack = () => page.evaluate(%(now)s);
      await api('/api/run', { name: 'Fake Probe', command: 'jam', inputs: {}, args: [] });
      await until(() => document.getElementById('ack-count').textContent === '1 more waiting', 10000);
      await page.click('#ack-ok');
      await until(() => (document.getElementById('ack-title') || {}).textContent === 'Idle timeout');
      await page.evaluate(() => {
        window.ackHid = 0;
        const modal = document.getElementById('ack-modal');
        new MutationObserver(() => { if (modal.hidden) window.ackHid += 1; })
          .observe(modal, { attributes: true, attributeFilter: ['hidden'] });
      });
      await api('/api/run', { name: 'Fake Probe', command: 'home', inputs: { x_step: '1' }, args: [] });
      await until(() => (document.querySelector('#ack-text .ack-line') || {}).textContent
        === 'Fake Probe was idle for 301 s, so it was powered down. (x2)', 10000);
      const repeated = await ack();
      const hid = await page.evaluate(() => window.ackHid);
      await page.click('#ack-ok');
      await sleep(200);
      return { repeated, hid, done: await ack() };
    """ % {"now": _ACK_NOW}, tmp_path)
    repeated = out["repeated"]
    assert repeated["open"] and repeated["title"] == "Idle timeout", repeated
    assert repeated["body"] == ["Fake Probe was idle for 301 s, so it was "
                                "powered down. (x2)"], repeated
    assert repeated["waiting"] == "", repeated
    assert out["hid"] == 0, "the open dialog was closed and reopened"
    assert out["done"]["open"] is False, "one Understood reads every repeat"


@needs_browser
def test_a_stop_that_never_reaches_the_station_says_so_on_the_rail(station, tmp_path):
    """F2 (WDG-2, CRIT-1, HC-3): the fetch is refused; the rail says the stop
    did not reach the station and the link says it is not answering."""
    view, controller, probe = station
    out = _browse(view, r"""
      await page.setRequestInterception(true);
      page.on('request', (r) => (r.url().includes('/api/estop_all') ? r.abort() : r.continue()));
      // Updated (L, round 7): the link line is RECORDED from before the
      // press. It says "Not answering" at the failed stop and the next state
      // poll that succeeds (a quarter second later) rightly clears it, so a
      // sample taken after the rail alert appeared could land either side.
      await page.evaluate(() => {
        window.linkSaid = [];
        const link = document.getElementById('connection');
        new MutationObserver(() => window.linkSaid.push(link.textContent))
          .observe(link, { childList: true, characterData: true, subtree: true });
      });
      await page.click('#full-stop');
      await until(() => !document.getElementById('rail-alert').hidden);
      await until(() => window.linkSaid.some((t) => t.startsWith('Not answering')));
      return page.evaluate(() => ({
        alert: document.getElementById('rail-alert').textContent,
        link: window.linkSaid.find((t) => t.startsWith('Not answering')) || '',
        face: document.querySelector('#full-stop .mushroom-face').textContent,
      }));
    """, tmp_path)
    assert out["alert"].startswith("Stop did not reach the station — "), out
    assert out["link"].startswith("Not answering"), out
    assert out["face"] == "Stop", "the stop claims a latch it never delivered"
    assert probe.is_estopped is False


@needs_browser
def test_a_stop_a_model_did_not_confirm_names_that_model(station, tmp_path):
    """F2: `unconfirmed` is surfaced by model. Updated (E, 2026-09-25): at
    the model's OWN entry - a signal head rule and "Stop not confirmed.
    Treat as live." - instead of a line on the rail."""
    view, controller, probe = station
    probe.stop_confirms = False
    out = _browse(view, r"""
      await page.click('#full-stop');
      await until(() => document.querySelector('.card.is-unconfirmed'));
      return page.evaluate(() => {
        const card = document.querySelector('.card.is-unconfirmed');
        const s = document.createElement('span');
        s.style.color = getComputedStyle(document.documentElement).getPropertyValue('--signal').trim();
        document.body.appendChild(s);
        const mark = card.querySelector('.unconfirmed-mark');
        return { title: card.querySelector('.card-title').textContent, mark: mark.hidden ? '' : mark.textContent,
                 rule: getComputedStyle(card).borderTopColor, signal: getComputedStyle(s).color,
                 // Updated (K4): the head's one button is the Overview's
                 // "Open" press target, which is not a Dismiss.
                 dismiss: card.querySelectorAll('.card-head button:not(.card-open)').length };
      });
    """, tmp_path)
    assert out["title"] == "Fake Probe" and out["mark"] == "Stop not confirmed. Treat as live.", out
    assert out["rule"] == out["signal"] and out["dismiss"] == 0, out


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
        const muted = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
        const probe = document.createElement('span');
        probe.style.color = muted;
        document.body.appendChild(probe);
        const mutedRgb = getComputedStyle(probe).color;
        return {
          live, mutedRgb, card: getComputedStyle(v).color,
          badge: !card.querySelector('.stale-badge').hidden,
          badgeText: card.querySelector('.stale-badge').textContent,
          isLive: card.classList.contains('is-live'),
          link: document.getElementById('connection').textContent,
        };
      }, live);
    """, tmp_path)
    assert out["live"] != out["mutedRgb"]
    # Updated (E): the rail carries no readouts any more (no value said
    # twice), so the entry's own badge is the word.
    assert out["card"] == out["mutedRgb"], out
    assert out["badge"] and not out["isLive"] and out["badgeText"] == "Stale", out
    assert re.fullmatch(r"Not answering since \d\d:\d\d:\d\d", out["link"]), out


@needs_browser
def test_the_stop_has_a_keyboard_path_and_an_ink_focus_ring(station, tmp_path):
    """F9 + F17 + G5: Ctrl+. stops from inside a text box and Meta+. does
    nothing (one chord on every platform, and no copy names Cmd); Enter on the focused
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
      r.byMeta = (await api('/api/state')).is_estopped;
      r.visible = await page.evaluate(() => document.body.innerText);
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
    assert out["byMeta"] is False, "Meta+. stopped: a chord that exists on one platform"
    assert out["typed"] == "", "the shortcut typed into the entry"
    assert "Ctrl+." in out["title"] and "Cmd" not in out["title"], out["title"]
    assert "Ctrl+." in out["visible"], "the rail does not say the shortcut"
    for word in ("Cmd", "\u2318", "Mac"):
        assert word not in out["visible"], f"the page names {word!r}"
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
          cls: card.className, bar: getComputedStyle(card).borderTopColor,
          signal: getComputedStyle(s).color, value: getComputedStyle(v).color,
          muted: getComputedStyle(muted).color,
          badge: card.querySelector('.stale-badge').hidden ? '' : card.querySelector('.stale-badge').textContent,
          rail: document.getElementById('rail-alert').textContent,
        };
      });
    """, tmp_path)
    assert "is-lost" in out["cls"] and "is-live" not in out["cls"], out
    assert out["bar"] == out["signal"] and out["value"] == out["muted"], out
    # Updated (E): the signal mark is the entry's head rule (no card bars);
    # the rail's readout flag is gone with the readouts.
    assert out["badge"] == "Connection lost", out
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
        // Updated (E): the rail is a column on the left - clear of it means
        // right of it.
        const rail = document.querySelector('.rail').getBoundingClientRect();
        return { next: group.nextElementSibling === status, text: status.textContent,
                 inView: box.top >= 0 && box.left >= rail.right && box.bottom <= innerHeight };
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
    # Updated (N3): leaving asks while something is ENERGIZED (a probe in a
    # mode counts), which is wider than active; this model is both.
    probe.is_energized = True
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
    # Updated (E): trace is for a CHANGING number; one that holds still
    # reads in ink.
    assert out["colors"]["number"] != out["colors"]["trace"]
    assert out["colors"]["number"] != out["colors"]["muted"]
    assert out["colors"]["notSet"] == out["colors"]["muted"]
    assert out["option"]["title"] == "/dev/cu.usbmodem1234567890123"
    assert "…" in out["option"]["text"] and out["option"]["text"].endswith("7890123")
    assert out["leaveAsks"] is True


@needs_browser
def test_quit_asks_first_then_the_page_says_the_station_is_down(station, tmp_path):
    """G2: Quit asks on the page's own confirmation (Cancel focused); Cancel
    leaves everything running. Quit confirmed: the server is asked once, the
    page says the station has shut down, and it polls nothing any more - no
    state, no heartbeat, no reconnect attempts."""
    view, controller, probe = station
    out = _browse(view, r"""
      const seen = { state: 0, heartbeat: 0, quit: 0 };
      page.on('request', (r) => {
        const u = r.url();
        if (u.includes('/api/state')) seen.state += 1;
        if (u.includes('/api/heartbeat')) seen.heartbeat += 1;
        if (u.includes('/api/quit')) seen.quit += 1;
      });
      const quit = await page.evaluate(() => {
        const b = document.getElementById('quit-link');
        return b && { text: b.textContent.trim(), name: b.getAttribute('aria-label'),
                      cls: b.className, beside: b.parentElement.contains(document.getElementById('setup-link')) };
      });
      await page.click('#quit-link');
      await until(() => !document.getElementById('confirm-modal').hidden);
      const asked = await page.evaluate(() => ({
        text: document.getElementById('confirm-text').textContent,
        yes: document.getElementById('confirm-yes').textContent,
        focused: document.activeElement && document.activeElement.id,
      }));
      await page.click('#confirm-no');
      const before = seen.state;
      await sleep(800);
      const afterCancel = { polled: seen.state - before, quit: seen.quit,
        link: await page.evaluate(() => document.getElementById('connection').textContent) };
      await page.click('#quit-link');
      await until(() => !document.getElementById('confirm-modal').hidden);
      await page.click('#confirm-yes');
      await until(() => document.getElementById('connection').textContent.startsWith('The station has shut down'));
      const quiet = { state: seen.state, heartbeat: seen.heartbeat };
      await sleep(2500);
      return page.evaluate((quit, asked, afterCancel, quiet, seen) => {
        const card = Array.from(document.querySelectorAll('.card'))
          .find((c) => !c.classList.contains('setup-card'));
        return {
          quit, asked, afterCancel,
          link: document.getElementById('connection').textContent,
          statesAfter: seen.state - quiet.state,
          beatsAfter: seen.heartbeat - quiet.heartbeat,
          quitRequests: seen.quit,
          offline: document.body.classList.contains('is-offline'),
          cardsInert: document.getElementById('cards').inert,
          badge: !card.querySelector('.stale-badge').hidden,
          stopDisabled: document.getElementById('full-stop').disabled,
          muted: (() => {
            const v = Array.from(card.querySelectorAll('.value')).find((n) => n.textContent === '0.000');
            const probe = document.createElement('span');
            probe.style.color = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
            document.body.appendChild(probe);
            return getComputedStyle(v).color === getComputedStyle(probe).color;
          })(),
          rackDimmed: Number(getComputedStyle(document.getElementById('cards')).opacity) < 1,
          quitDisabled: document.getElementById('quit-link').disabled,
        };
      }, quit, asked, afterCancel, quiet, seen);
    """, tmp_path)
    # Signature (spec "Controls"): Quit is a text key - no face, no lip.
    assert out["quit"] == {"text": "Quit", "name": "Quit the station program",
                           "cls": "ghost rail-control text-key", "beside": True}, out["quit"]
    assert out["asked"]["text"] == ("Quit the station? This stops every model, closes "
                                    "every port and exits the program."), out["asked"]
    assert out["asked"]["yes"] == "Quit" and out["asked"]["focused"] == "confirm-no"
    assert out["afterCancel"]["polled"] > 0 and out["afterCancel"]["quit"] == 0, out
    # Updated (E): status by exception - a station that answers says nothing.
    assert out["afterCancel"]["link"] == "", out
    assert out["quitRequests"] == 1, out
    assert view._halt.is_set(), "the server was never asked to quit"
    assert out["link"] == "The station has shut down. You can close this tab.", out
    assert out["statesAfter"] == 0 and out["beatsAfter"] == 0, out
    assert out["offline"] and out["cardsInert"] and out["badge"], out
    assert out["stopDisabled"] and out["quitDisabled"], out
    assert out["muted"] and out["rackDimmed"], out


# --------------------------------------------------------------------------
# G3: the Setup row's Launch checkbox
# --------------------------------------------------------------------------
@needs_browser
def test_the_launch_box_sends_the_new_boolean_and_gates_the_port(station, tmp_path):
    """G3: a real checkbox, first column of the Setup table under a "Launch"
    caption. Unticking sends `false` and greys the Port dropdown out (really
    disabled); ticking sends `true` and brings it back. The box follows the
    MODEL: a refused tick is undone by the next poll, and a tick made on the
    station side shows up without anyone touching the box."""
    view, _, _ = station
    setup = view.setup
    out = _browse(view, r"""
      const r = {};
      await page.click('#setup-link');
      await sleep(400);
      const box = '#drawer-body input[type="checkbox"]';
      r.shape = await page.evaluate((sel) => {
        const b = document.querySelector(sel);
        const row = b.closest('.section-row');
        const cells = Array.from(row.children).filter((c) => c.classList.contains('cell'));
        const head = Array.from(document.querySelectorAll('#drawer-body .table-head .head-cell'))
          .map((c) => c.textContent);
        // Updated (L4, round 7): the box has two labels - its column's
        // "Launch" caption and the row's name, which shares its target.
        const labels = b.id ? Array.from(document.querySelectorAll('label[for="' + b.id + '"]'))
          .map((l) => l.textContent) : [];
        const label = labels.find((t) => t === 'Launch');
        return { firstCell: cells[0].contains(b), head, label: label || null, labels,
                 name: b.getAttribute('aria-label'), title: b.title, checked: b.checked };
      }, box);
      const portDisabled = () => page.evaluate(
        () => document.querySelector('#drawer-body select').disabled);
      r.portBefore = await portDisabled();
      await page.click(box);
      await until(() => document.querySelector('#drawer-body select').disabled);
      r.afterUntick = { port: await portDisabled(),
        checked: await page.evaluate((sel) => document.querySelector(sel).checked, box) };
      await page.click(box);
      await until(() => !document.querySelector('#drawer-body select').disabled);
      r.afterTick = { port: await portDisabled(),
        checked: await page.evaluate((sel) => document.querySelector(sel).checked, box) };
      return r;
    """, tmp_path)
    assert out["shape"]["firstCell"], out["shape"]
    assert out["shape"]["head"][0] == "Launch", out["shape"]
    assert out["shape"]["label"] == "Launch", out["shape"]
    assert out["shape"]["labels"] == ["Fake Probe", "Launch"], out["shape"]
    assert out["shape"]["name"] == "Launch Fake Probe", out["shape"]
    assert out["shape"]["title"] == "Launch Fake Probe", out["shape"]
    assert out["shape"]["checked"] is True, out["shape"]
    assert out["portBefore"] is False
    assert out["afterUntick"] == {"port": True, "checked": False}, out
    assert out["afterTick"] == {"port": False, "checked": True}, out
    # JSON booleans, never the strings "true" / "false".
    assert setup.ticks == [False, True], setup.ticks


@needs_browser
def test_the_launch_box_follows_the_model_not_the_click(station, tmp_path):
    """G3: the state poll sets the box; a refused untick is put back by it,
    and a row the station unticked by itself (not through the box) shows
    unticked with its Port dropdown greyed out."""
    view, _, _ = station
    setup = view.setup
    setup.refuse_ticks = 1
    out = _browse(view, r"""
      const r = {};
      await page.click('#setup-link');
      await sleep(400);
      const box = '#drawer-body input[type="checkbox"]';
      await page.click(box);
      await sleep(900);
      r.afterRefused = await page.evaluate((sel) => document.querySelector(sel).checked, box);
      r.portAfterRefused = await page.evaluate(() => document.querySelector('#drawer-body select').disabled);
      await api('/api/run', { name: '__setup__', command: 'set_probe_enabled', inputs: {}, args: [false] });
      await until(() => !document.querySelector('#drawer-body input[type="checkbox"]').checked);
      r.fromModel = await page.evaluate((sel) => document.querySelector(sel).checked, box);
      r.portFromModel = await page.evaluate(() => document.querySelector('#drawer-body select').disabled);
      return r;
    """, tmp_path)
    assert out["afterRefused"] is True, "a refused untick left the box unticked"
    assert out["portAfterRefused"] is False
    assert out["fromModel"] is False and out["portFromModel"] is True, out
    assert setup.ticks == [False, False], setup.ticks



# --------------------------------------------------------------------------
# G4: the Gamepad log behind a button, in an in-page panel
# --------------------------------------------------------------------------
@needs_browser
def test_the_gamepad_log_opens_in_one_panel_that_never_covers_the_stop(station, tmp_path):
    """G4: a detached log stream is a "Gamepad log…" button, not a feed. It
    opens ONE non-modal panel under the rail (the stop stays what a click at
    its centre lands on, and it stops); pressing the button again raises the
    same panel; Escape closes it and focus returns to the button; the source
    command is asked for only while the panel is open; closing the card
    removes the panel."""
    view, controller, probe = station
    out = _browse(view, r"""
      const r = {};
      const asked = [];
      page.on('request', (q) => { if (q.url().includes('command=gamepad_lines')) asked.push(Date.now()); });
      const opener = () => page.evaluate(() => Array.from(document.querySelectorAll('.card button'))
        .find((b) => b.textContent === 'Gamepad log…'));
      r.shape = await page.evaluate(() => {
        const card = Array.from(document.querySelectorAll('.card')).find((c) => !c.classList.contains('setup-card'));
        const button = Array.from(card.querySelectorAll('button')).find((b) => b.textContent === 'Gamepad log…');
        return { button: Boolean(button),
                 feeds: Array.from(card.querySelectorAll('pre.feed')).map((f) => f.getAttribute('aria-label')),
                 popup: button && button.getAttribute('aria-haspopup'),
                 expanded: button && button.getAttribute('aria-expanded') };
      });
      await sleep(2500);
      r.askedWhileClosed = asked.length;
      await page.evaluate(() => Array.from(document.querySelectorAll('.card button'))
        .find((b) => b.textContent === 'Gamepad log…').click());
      await until(() => { const w = document.querySelector('.log-window'); return w && !w.hidden
        && w.querySelector('.feed').textContent.includes('button A down'); });
      r.open = await page.evaluate(() => {
        const w = document.querySelector('.log-window');
        const b = w.getBoundingClientRect();
        const rail = document.querySelector('.rail');
        w.dataset.mark = 'first';
        return { role: w.getAttribute('role'), modal: w.getAttribute('aria-modal'),
                 title: document.getElementById(w.getAttribute('aria-labelledby')).textContent,
                 lines: w.querySelector('.feed').textContent,
                 below: b.left >= rail.getBoundingClientRect().right - 0.5,
                 z: Number(getComputedStyle(w).zIndex), railZ: Number(getComputedStyle(rail).zIndex),
                 overlay: w.classList.contains('overlay') || Boolean(w.closest('.overlay')),
                 focusIn: w.contains(document.activeElement),
                 expanded: Array.from(document.querySelectorAll('.card button'))
                   .find((x) => x.textContent === 'Gamepad log…').getAttribute('aria-expanded') };
      });
      r.stopOnTop = await page.evaluate(%s);
      const box = await page.evaluate(() => {
        const b = document.getElementById('full-stop').getBoundingClientRect();
        return [b.left + b.width / 2, b.top + b.height / 2];
      });
      await page.mouse.click(box[0], box[1]);
      await sleep(600);
      r.latched = (await api('/api/state')).is_estopped;
      r.stillOpen = await page.evaluate(() => !document.querySelector('.log-window').hidden);
      await page.evaluate(() => Array.from(document.querySelectorAll('.card button'))
        .find((b) => b.textContent === 'Gamepad log…').click());
      await sleep(200);
      r.again = await page.evaluate(() => ({
        count: document.querySelectorAll('.log-window').length,
        mark: document.querySelector('.log-window').dataset.mark,
        focusIn: document.querySelector('.log-window').contains(document.activeElement) }));
      await page.keyboard.press('Escape');
      await sleep(300);
      r.closed = await page.evaluate(() => ({
        hidden: !document.querySelector('.log-window') || document.querySelector('.log-window').hidden,
        focus: document.activeElement && document.activeElement.textContent,
        expanded: document.activeElement && document.activeElement.getAttribute('aria-expanded') }));
      r.askedWhileOpen = asked.length - r.askedWhileClosed;
      const before = asked.length;
      await sleep(2500);
      r.askedAfterClose = asked.length - before;
      await page.evaluate(() => Array.from(document.querySelectorAll('.card button'))
        .find((b) => b.textContent === 'Gamepad log…').click());
      await until(() => document.querySelector('.log-window') && !document.querySelector('.log-window').hidden);
      await page.click('.log-window .log-window-close');
      await sleep(300);
      r.byClose = await page.evaluate(() => ({
        hidden: document.querySelector('.log-window').hidden,
        focus: document.activeElement && document.activeElement.textContent }));
      await page.evaluate(() => Array.from(document.querySelectorAll('.card button'))
        .find((b) => b.textContent === 'Gamepad log…').click());
      await api('/api/close_model', { name: 'Fake Probe' });
      await until(() => !Array.from(document.querySelectorAll('.card')).some((c) => !c.classList.contains('setup-card')));
      r.afterCardClosed = await page.evaluate(() => document.querySelectorAll('.log-window').length);
      return r;
    """ % _STOP_HIT, tmp_path)
    assert out["shape"]["button"], out["shape"]
    assert out["shape"]["feeds"] == ["Log"], "the detached log was drawn as a feed on the card"
    assert out["shape"]["popup"] == "dialog" and out["shape"]["expanded"] == "false", out["shape"]
    assert out["askedWhileClosed"] == 0, "the closed log's source was polled"
    opened = out["open"]
    assert opened["role"] == "dialog" and opened["modal"] == "false", opened
    assert opened["title"] == "Fake Probe — Gamepad log", opened
    assert "LX +0.50" in opened["lines"] and "button A down" in opened["lines"], opened
    assert opened["below"] and 0 < opened["z"] < opened["railZ"], opened
    assert not opened["overlay"], "the log panel is an overlay scrim"
    assert opened["focusIn"] and opened["expanded"] == "true", opened
    assert out["stopOnTop"], "the log panel covers the stop"
    assert out["latched"] is True, "a click on the stop with the log open did not stop"
    assert out["stillOpen"] is True
    assert out["again"] == {"count": 1, "mark": "first", "focusIn": True}, out["again"]
    assert out["closed"] == {"hidden": True, "focus": "Gamepad log…", "expanded": "false"}, out["closed"]
    assert out["askedWhileOpen"] >= 1, "the open log was never polled"
    assert out["askedAfterClose"] == 0, "the log was polled after it closed"
    assert out["byClose"] == {"hidden": True, "focus": "Gamepad log…"}, out["byClose"]
    assert out["afterCardClosed"] == 0, "the panel outlived its card"


# --------------------------------------------------------------------------
# G6 (round-4 audit IMP-0): the unconfirmed-stop line is state, not an event
# --------------------------------------------------------------------------
@needs_browser
def test_the_unconfirmed_stop_line_goes_when_the_latch_clears(station, tmp_path):
    """G6: a stop a model did not confirm puts a line on the rail; once the
    latch is cleared - here from ANOTHER client, so only the poll can know -
    the line goes and the rail's alert region is hidden again, instead of
    claiming a live, unconfirmed stop beside a face that reads "Stop". A
    later stop that is again unconfirmed brings the line back; clearing it
    through the page's own control removes it too."""
    view, controller, probe = station
    probe.stop_confirms = False
    out = _browse(view, r"""
      const r = {};
      // Updated (E): the line is the mark at the model's own entry.
      const rail = () => page.evaluate(() => {
        const mark = document.querySelector('.card .unconfirmed-mark');
        return { hidden: mark.hidden, text: mark.hidden ? '' : mark.textContent,
                 face: document.querySelector('#full-stop .mushroom-face').textContent };
      });
      await page.click('#full-stop');
      await until(() => !document.querySelector('.card .unconfirmed-mark').hidden);
      await sleep(400);
      r.stopped = await rail();
      await api('/api/clear_estop_all', { confirmed: true });
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Stop');
      await sleep(700);
      r.clearedElsewhere = await rail();
      await page.click('#full-stop');
      await until(() => !document.querySelector('.card .unconfirmed-mark').hidden);
      await sleep(400);
      r.again = await rail();
      await page.click('#full-stop');
      await until(() => !document.getElementById('confirm-modal').hidden);
      await page.click('#confirm-yes');
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Stop');
      await sleep(700);
      r.clearedHere = await rail();
      return r;
    """, tmp_path)
    line = "Stop not confirmed. Treat as live."
    assert out["stopped"]["face"] == "Clear" and line in out["stopped"]["text"], out
    assert out["clearedElsewhere"] == {"hidden": True, "text": "", "face": "Stop"}, out
    assert out["again"]["face"] == "Clear" and line in out["again"]["text"], out
    assert out["clearedHere"] == {"hidden": True, "text": "", "face": "Stop"}, out


# --------------------------------------------------------------------------
# I3 (audit round 5, UXPM5-3): the log panel belongs to its own card
# --------------------------------------------------------------------------
@pytest.fixture
def sim_station():
    """The real six models, every one in SIM, behind a real Setup."""
    from controller.setup import Setup, SIM
    controller = Controller()
    setup = Setup(controller)
    for key, row in setup._rows.items():
        getattr(setup, f"set_{key}_enabled")(True)
        if row["needs_port"]:
            getattr(setup, f"set_{key}_port")(SIM)
    assert len(setup.launch()) == 7   # the six plus the Transfer Map (Tier S)
    view = WebView(controller, setup, port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller
    finally:
        view.close()


#: Open one card's Gamepad log and measure it against every other card.
_LOG_LAYOUT = r"""
  const measure = (owner) => page.evaluate((owner) => {
    const cards = Array.from(document.querySelectorAll('#cards .card'));
    const titleOf = (c) => (c.querySelector('.card-title') || {}).textContent;
    const mine = cards.find((c) => titleOf(c) === owner);
    const open = Array.from(document.querySelectorAll('.log-window'))
      .filter((w) => !w.hidden && w.getClientRects().length);
    if (open.length !== 1) return { open: open.length };
    const w = open[0];
    const p = w.getBoundingClientRect();
    const rack = document.getElementById('cards').getBoundingClientRect();
    const hits = [];
    for (const card of cards) {
      if (card === mine || card.classList.contains('setup-card')) continue;
      for (const c of card.querySelectorAll('button, input, select, textarea')) {
        const r = c.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        const ix = Math.min(p.right, r.right) - Math.max(p.left, r.left);
        const iy = Math.min(p.bottom, r.bottom) - Math.max(p.top, r.top);
        if (ix > 0.5 && iy > 0.5) hits.push(titleOf(card) + ': ' + (c.textContent || c.getAttribute('aria-label') || c.tagName).trim());
      }
    }
    const inside = p.left >= rack.left - 0.5 && p.right <= rack.right + 0.5
      && p.top >= rack.top - 0.5 && p.bottom <= rack.bottom + 0.5;
    return { open: 1, owner: mine && mine.contains(w), hits, inside,
             title: document.getElementById(w.getAttribute('aria-labelledby')).textContent,
             rect: [p.left, p.top, p.right, p.bottom], rack: [rack.left, rack.top, rack.right, rack.bottom] };
  }, owner);
  const openLog = (owner) => page.evaluate((owner) => {
    const card = Array.from(document.querySelectorAll('#cards .card'))
      .find((c) => (c.querySelector('.card-title') || {}).textContent === owner);
    // Updated (E): the Gamepad log is a tier-3 resource - Configure, then
    // Diagnostics, then the log. Updated (K4): tiers live on the device
    // page, so the model is pressed in the rail first.
    document.querySelector('#model-nav [data-model="' + owner + '"]').click();
    for (const tier of ['2', '3']) {
      const d = card.querySelector('.disclosure[data-tier="' + tier + '"]');
      if (d && d.getAttribute('aria-expanded') !== 'true') d.click();
    }
    Array.from(card.querySelectorAll('button')).find((b) => b.textContent === 'Gamepad log…').click();
  }, owner);
  const r = {};
  await until(() => document.querySelectorAll('#cards .card:not(.setup-card)').length >= 6, 8000);
  if (await page.evaluate(() => document.body.classList.contains('drawer-open')
      || !document.getElementById('scrim').hidden)) {
    await page.click('#drawer-close');
    await sleep(400);
  }
  for (const width of [1400, 900]) {
    await page.setViewport({ width, height: 900 });
    await sleep(500);
    await openLog('Stepper Probe');
    await until(() => Array.from(document.querySelectorAll('.log-window')).some((w) => !w.hidden));
    await sleep(300);
    r['stepper' + width] = await measure('Stepper Probe');
    await openLog('DC Probe');
    await sleep(400);
    r['dc' + width] = await measure('DC Probe');
    r['stepperClosed' + width] = await page.evaluate(() => Array.from(document.querySelectorAll('.log-window'))
      .filter((w) => !w.hidden).length);
    r['stopOnTop' + width] = await page.evaluate(%(stop_hit)s);
    await page.keyboard.press('Escape');
    await sleep(300);
  }
  return r;
"""


@needs_browser
def test_the_gamepad_log_panel_opens_in_its_own_card_and_covers_no_other(sim_station, tmp_path):
    """I3 (UXPM5-3): with all six SIM models up, the Stepper Probe's log opens
    inside the Stepper Probe's card, within the rack, over none of another
    card's buttons, inputs or selects - at 1400 and at 900. Opening the DC
    Probe's log closes the Stepper's: one panel at a time. The rail's stop
    stays what a click at its centre lands on."""
    view, controller = sim_station
    out = _browse(view, _LOG_LAYOUT % {"stop_hit": _STOP_HIT}, tmp_path)
    for width in (1400, 900):
        for who, title in (("stepper", "Stepper Probe — Gamepad log"),
                           ("dc", "DC Probe — Gamepad log")):
            got = out[f"{who}{width}"]
            assert got["open"] == 1, (width, who, got)
            assert got["title"] == title, (width, got)
            assert got["hits"] == [], f"at {width} the {who} log covers {got['hits']}"
            assert got["inside"], f"at {width} the {who} log leaves the rack: {got}"
            assert got["owner"], f"at {width} the {who} log is not in its own card"
        assert out[f"stepperClosed{width}"] == 1, "two log panels open at once"
        assert out[f"stopOnTop{width}"], f"at {width} the log panel covers the stop"


# --------------------------------------------------------------------------
# I5 (audit round 5, UXPM5-4): the page after Quit reads as shut down
# --------------------------------------------------------------------------
_SHUT_DOWN = "The station has shut down. You can close this tab."

#: Quit through the page, then describe what is left of it.
_QUIT_AND_READ = r"""
  const announced = [];
  await page.exposeFunction('noteLive', (t) => announced.push(t));
  await page.evaluate(() => {
    const region = document.getElementById('connection');
    new MutationObserver(() => window.noteLive(region.textContent))
      .observe(region, { childList: true, characterData: true, subtree: true });
  });
  await page.click('#quit-link');
  await until(() => !document.getElementById('confirm-modal').hidden);
  await page.click('#confirm-yes');
  await until(() => document.body.classList.contains('is-shut-down'));
  await sleep(1500);
  const end = await page.evaluate(() => {
    const css = getComputedStyle(document.documentElement);
    const swatch = (name) => {
      const s = document.createElement('span');
      s.style.color = css.getPropertyValue(name).trim();
      document.body.appendChild(s);
      const c = getComputedStyle(s).color;
      s.remove();
      return c;
    };
    const signal = swatch('--signal');
    const text = swatch('--text');
    const sized = document.createElement('span');
    sized.style.fontSize = 'var(--t-readout)';
    document.body.appendChild(sized);
    const readout = getComputedStyle(sized).fontSize;
    sized.remove();
    const red = [];
    for (const el of document.querySelectorAll('body *')) {
      if (!el.getClientRects().length) continue;
      const s = getComputedStyle(el);
      if (s.visibility === 'hidden' || s.display === 'none') continue;
      const paints = [s.color, s.backgroundColor, s.backgroundImage];
      for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
        if (parseFloat(s['border' + side + 'Width']) > 0) paints.push(s['border' + side + 'Color']);
      }
      if (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) paints.push(s.outlineColor);
      if (paints.some((p) => p && p.includes(signal))) {
        red.push(el.tagName + '#' + el.id + '.' + el.className);
      }
    }
    const link = document.getElementById('connection');
    const stop = document.getElementById('full-stop');
    const enabled = Array.from(document.querySelectorAll('button, input, select, textarea'))
      .filter((c) => !c.disabled).map((c) => c.id || c.textContent.trim() || c.tagName);
    return {
      link: link.textContent, linkIsFocused: document.activeElement === link,
      linkTabIndex: link.getAttribute('tabindex'),
      linkSize: getComputedStyle(link).fontSize, readout,
      linkInk: getComputedStyle(link).color === text,
      red, enabled,
      stop: { disabled: stop.disabled, ariaDisabled: stop.getAttribute('aria-disabled'),
              face: stop.textContent.trim(), keys: stop.getAttribute('aria-keyshortcuts'),
              latched: stop.classList.contains('is-latched'),
              ring: getComputedStyle(stop).borderTopColor },
      railAlert: { hidden: document.getElementById('rail-alert').hidden,
                   lines: document.querySelectorAll('.rail-alert-line').length,
                   dismiss: document.querySelectorAll('.rail-alert-dismiss').length },
      ackOpen: !document.getElementById('ack-modal').hidden,
      ackLines: document.querySelectorAll('#ack-text .ack-line').length,
      tray: { collapsed: document.getElementById('event-log').hidden,
              latest: document.getElementById('tray-latest').textContent },
    };
  });
  end.announced = announced;
  return end;
"""


def _assert_shut_down(out, unconfirmed=()):
    """Updated (O5, IMP8-3): a model that did not confirm the stop Quit ran
    is named on the rail and keeps its entry's red rule; that is the only
    red left. Everything else is as I5 set it."""
    assert out["link"] == _SHUT_DOWN, out["link"]
    assert out["linkIsFocused"] and out["linkTabIndex"] == "-1", out
    assert out["linkSize"] == out["readout"], (out["linkSize"], out["readout"])
    assert out["linkInk"], "the sentence is not in ink"
    assert out["announced"] and out["announced"][-1] == _SHUT_DOWN, out["announced"]
    assert out["announced"].count(_SHUT_DOWN) == 1, out["announced"]
    if unconfirmed:
        assert out["red"] and all(("rail-alert" in r or "card" in r or "nav-mark" in r
                                   or "unconfirmed" in r) for r in out["red"]), out["red"]
    else:
        assert out["red"] == [], f"signal red is still on the page: {out['red']}"
    assert out["enabled"] == [], f"controls still enabled: {out['enabled']}"
    stop = out["stop"]
    assert stop["disabled"] and stop["ariaDisabled"] == "true", stop
    assert stop["face"] not in ("Stop", "Clear") and not stop["latched"], stop
    assert stop["keys"] is None, stop
    lines = len(unconfirmed)
    assert out["railAlert"] == {"hidden": not lines, "lines": lines, "dismiss": 0}, out["railAlert"]
    assert not out["ackOpen"] and out["ackLines"] == 0, out
    assert out["tray"] == {"collapsed": True, "latest": "Quit from the Web console"}, out["tray"]


@needs_browser
def test_after_quit_while_latched_the_page_reads_as_shut_down(station, tmp_path):
    """I5 (UXPM5-4): Quit while latched and unconfirmed. Afterwards the rail
    says only "The station has shut down…" in ink at readout size, focused
    and announced once; the stop disc is inert (no red, no ring, disabled);
    the unconfirmed line and its Dismiss, the acknowledgement and the raw
    error are gone; every control is disabled."""
    view, controller, probe = station
    probe.stop_confirms = False
    out = _browse(view, r"""
      await page.click('#full-stop');
      await until(() => document.querySelector('.card.is-unconfirmed'));
      await sleep(600);
    """ + _QUIT_AND_READ, tmp_path)
    _assert_shut_down(out, unconfirmed=["Fake Probe"])


@needs_browser
def test_after_quit_while_live_the_disc_is_the_same_inert_disc(station, tmp_path):
    """I5: Quit unlatched leaves no "Stop" face either: the same end-state."""
    view, controller, probe = station
    out = _browse(view, _QUIT_AND_READ, tmp_path)
    _assert_shut_down(out)


# --------------------------------------------------------------------------
# I6 (Web part): the chord's hint follows the stop's face
# --------------------------------------------------------------------------
@needs_browser
def test_the_stop_chord_is_announced_only_while_the_face_is_stop(station, tmp_path):
    """I6: Ctrl+. stops and never clears (F9). While the face reads "Clear",
    the button must not advertise the chord as its shortcut; it does again
    once the latch clears.

    Updated (L1, round 7): the rail's "Stop: Ctrl+." hint is visible in
    every state - the chord always stops, a model the latch did not reach
    included - so it no longer hides while the face reads "Clear"."""
    view, controller, probe = station
    out = _browse(view, r"""
      const read = () => page.evaluate(() => {
        const stop = document.getElementById('full-stop');
        const hint = document.querySelector('.stop-hint');
        return { face: stop.textContent.trim(), keys: stop.getAttribute('aria-keyshortcuts'),
                 hint: Boolean(hint && !hint.hidden && hint.getClientRects().length) };
      });
      const r = {};
      r.live = await read();
      await page.click('#full-stop');
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Clear');
      r.latched = await read();
      await api('/api/clear_estop_all', { confirmed: true });
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Stop');
      r.cleared = await read();
      return r;
    """, tmp_path)
    assert out["live"] == {"face": "Stop", "keys": "Control+Period", "hint": True}, out
    assert out["latched"] == {"face": "Clear", "keys": None, "hint": True}, out
    assert out["cleared"] == {"face": "Stop", "keys": "Control+Period", "hint": True}, out


# --------------------------------------------------------------------------
# I8 (audit round 6, WDG6-1): the unconfirmed-stop line cannot be dismissed
# --------------------------------------------------------------------------
@needs_browser
def test_the_unconfirmed_stop_line_has_no_dismiss_and_leaves_only_with_the_latch(station, tmp_path):
    """I8 (WDG6-1, S1): "Stop latched, but X has not confirmed it. Treat it
    as live." describes hardware the page cannot see. While the latch holds
    it carries no Dismiss, and a click where Dismiss used to sit leaves it
    standing; it goes only when the latch clears."""
    view, controller, probe = station
    probe.stop_confirms = False
    out = _browse(view, r"""
      const r = {};
      // Updated (E): the line is the mark at the model's own entry.
      const line = () => page.evaluate(() => {
        const node = document.querySelector('.card .unconfirmed-mark');
        if (!node || node.hidden) return { present: false, hidden: true };
        const b = node.getBoundingClientRect();
        return { present: true, hidden: node.hidden,
                 // Updated (K4): the Overview's "Open" is the head's press
                 // target, not a Dismiss; a click on the mark opens the
                 // device page, where the mark still stands.
                 dismiss: node.closest('.card-head').querySelectorAll('button:not(.card-open), .rail-alert-dismiss').length,
                 at: [b.right - 10, b.top + b.height / 2] };
      });
      await page.keyboard.down('Control');
      await page.keyboard.press('.');
      await page.keyboard.up('Control');
      await until(() => !document.querySelector('.card .unconfirmed-mark').hidden);
      await sleep(400);
      r.latched = await line();
      await page.mouse.click(r.latched.at[0], r.latched.at[1]);
      await sleep(700);
      r.afterClick = await line();
      r.stillLatched = (await api('/api/state')).is_estopped;
      await api('/api/clear_estop_all', { confirmed: true });
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Stop');
      await sleep(700);
      r.cleared = await line();
      return r;
    """, tmp_path)
    assert out["latched"]["present"] and not out["latched"]["hidden"], out
    assert out["latched"]["dismiss"] == 0, "the unconfirmed-stop line can be dismissed"
    assert out["stillLatched"] is True
    assert out["afterClick"]["present"] and not out["afterClick"]["hidden"], out
    assert out["cleared"] == {"present": False, "hidden": True}, out


# --------------------------------------------------------------------------
# I9 (audit round 6, WDG6-2): phone width with Setup open
# --------------------------------------------------------------------------
@needs_browser
def test_at_phone_width_with_setup_open_nothing_scrolls_sideways(sim_station, tmp_path):
    """I9 (WDG6-2): at 390x844, Setup reopened after launch, the page does not
    scroll sideways, every rail number keeps a real width, and in the Setup
    table no cell paints over its neighbour (the tick box over the row name,
    the Gamepad select over Status)."""
    view, controller = sim_station
    out = _browse(view, r"""
      await until(() => document.querySelectorAll('#cards .card:not(.setup-card)').length >= 6, 8000);
      await page.setViewport({ width: 390, height: 844 });
      await sleep(400);
      if (!await page.evaluate(() => document.getElementById('scrim').hidden === false)) {
        await page.click('#setup-link');
      }
      await sleep(800);
      return page.evaluate(() => {
        // Updated (E): the rail has no readouts; what must keep a real size
        // at phone width is the stop.
        const disc = document.getElementById('full-stop').getBoundingClientRect();
        const values = [document.getElementById('full-stop')];
        const zero = (disc.width < 40 || disc.right > innerWidth || disc.bottom > innerHeight) ? 1 : 0;
        const overlaps = [];
        const table = document.querySelector('#drawer-body .card-body.table');
        if (table) {
          for (const rowNode of table.querySelectorAll('.section-row:not(.table-head)')) {
            const cells = Array.from(rowNode.children).filter((c) => c.getClientRects().length);
            const parts = [];
            for (const c of cells) {
              const inner = c.querySelectorAll('input, select, button, .row-title, .value');
              for (const n of (inner.length ? inner : [c])) {
                // A word that overflows its track paints where its text is,
                // not where its box is: measure the text itself.
                let b = n.getBoundingClientRect();
                if (n.matches('.row-title, .value') && n.textContent.trim()) {
                  const range = document.createRange();
                  range.selectNodeContents(n);
                  b = range.getBoundingClientRect();
                }
                if (b.width && b.height) parts.push([n, b]);
              }
            }
            for (let i = 0; i < parts.length; i++) {
              for (let j = i + 1; j < parts.length; j++) {
                const [na, a] = parts[i]; const [nb, b] = parts[j];
                if (na.contains(nb) || nb.contains(na)) continue;
                const ix = Math.min(a.right, b.right) - Math.max(a.left, b.left);
                const iy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
                if (ix > 0.5 && iy > 0.5) overlaps.push((na.className || na.tagName) + ' x ' + (nb.className || nb.tagName)
                  + ' in ' + (rowNode.querySelector('.row-title') || {}).textContent);
              }
            }
          }
        }
        return { scrollWidth: document.documentElement.scrollWidth, values: values.length, zero,
                 drawerOpen: document.getElementById('scrim').hidden === false,
                 table: Boolean(table), overlaps };
      });
    """, tmp_path)
    assert out["drawerOpen"] and out["table"], out
    assert out["values"] > 0, out
    assert out["scrollWidth"] <= 390, f"the page scrolls sideways: {out}"
    assert out["zero"] == 0, "the stop is not a real target inside the viewport"
    assert out["overlaps"] == [], out["overlaps"]


# --------------------------------------------------------------------------
# E (Bench sheet, tiered, 2026-09-25): tiers, the slider, status by
# exception, the disc. A model with all three tiers and a slider entry.
# --------------------------------------------------------------------------
class TieredProbe(Panel):
    NAME = "Tiered Probe"
    PARAMS = {"speed": Param("speed", "int", default=400, minimum=1, maximum=5000,
                             label="Manual speed"),
              "x_step": Param("x_step", "int", default=16, label="X step size")}

    def __init__(self):
        super().__init__()
        self.position_x = "12"
        self.link = "Connected"
        self.is_estopped = False
        self.is_active = False
        self.mode = "idle"
        self.sent = []

    def open(self):
        pass

    def close(self):
        pass

    def estop(self):
        self.is_estopped = True
        return True

    def clear_estop(self, confirmed=False):
        self.is_estopped = False

    def on_model_added(self, name, model):
        pass

    def on_model_removed(self, name, model):
        pass

    @property
    def mode_name(self):
        return self.mode

    @property
    def schema(self):
        P = self.PARAMS
        return sch.schema(
            sch.section("Position", sch.readonly("X:", "position_x", rail=True),
                        sch.readonly("Link:", "link")),
            sch.section("Speeds",
                        sch.entry("Manual speed:", "speed", P["speed"], slider=(1, 1000)),
                        # L3: gated in manual mode, so a test can read why.
                        sch.button("Go", "go", inputs=("speed",), role="go",
                                   disabled_when=("manual",))),
            sch.section("Configuration", sch.entry("X step size:", "x_step", P["x_step"]),
                        tier=2, disclosure="Configure"),
            sch.section("Diagnostics", sch.readonly("Link:", "link"),
                        tier=3, disclosure="Diagnostics"),
        )

    @property
    def state(self):
        snapshot = super().state
        snapshot.update({"age": 0.0, "is_estopped": self.is_estopped,
                         "is_active": False, "devices": {}})
        return snapshot

    def go(self):
        self.sent.append(self.speed)
        return "went"


@pytest.fixture
def tiered_station():
    controller = Controller()
    probe = TieredProbe()
    controller.factory = lambda config: TieredProbe()
    controller.add("Tiered Probe", probe, {})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, probe
    finally:
        view.close()


#: Open the page with the drawer shut, and helpers over the one entry.
_TIERED = r"""
  if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
    await page.click('#drawer-close');
    await sleep(300);
  }
  // Updated (K4): tiers 2 and 3 live on the device page; the rail opens it.
  const openDevice = async () => {
    await page.click('#model-nav [data-model="Tiered Probe"]');
    await sleep(300);
  };
  await openDevice();
  const card = () => page.evaluateHandle(() => Array.from(document.querySelectorAll('#cards .card'))
    .find((c) => c.querySelector('.card-title').textContent === 'Tiered Probe'));
  const read = () => page.evaluate(() => {
    const c = Array.from(document.querySelectorAll('#cards .card'))
      .find((n) => n.querySelector('.card-title').textContent === 'Tiered Probe');
    const d2 = c.querySelector('.disclosure[data-tier="2"]');
    const d3 = c.querySelector('.disclosure[data-tier="3"]');
    const well = document.getElementById(d2.getAttribute('aria-controls'));
    const deep = document.getElementById(d3.getAttribute('aria-controls'));
    const step = c.querySelector('input[name="x_step"]');
    return { d2: d2.getAttribute('aria-expanded'), d3: d3.getAttribute('aria-expanded'),
             d2text: d2.textContent, d3text: d3.textContent,
             wellShown: Boolean(well.getClientRects().length),
             deepShown: Boolean(deep.getClientRects().length),
             stepShown: Boolean(step.getClientRects().length),
             deepInWell: well.contains(deep) && well.contains(d3) };
  });
"""


@needs_browser
def test_tier_two_opens_on_demand_holds_tier_three_and_is_remembered(tiered_station, tmp_path):
    """E: tier-2 content is hidden until its disclosure is pressed; tier 3
    sits inside the tier-2 well behind its own disclosure; the open state is
    the page's, per model - a model closed and reopened comes back as it
    was left, and nothing is written to the browser's storage."""
    view, controller, probe = tiered_station
    out = _browse(view, _TIERED + r"""
      const r = {};
      r.start = await read();
      await page.click('.card .disclosure[data-tier="2"]');
      await sleep(200);
      r.open2 = await read();
      await page.click('.card .disclosure[data-tier="3"]');
      await sleep(200);
      r.open3 = await read();
      await api('/api/close_model', { name: 'Tiered Probe' });
      await until(() => !document.querySelector('#cards .card'));
      await api('/api/open_model', { name: 'Tiered Probe' });
      await until(() => document.querySelector('#cards .card .disclosure'));
      await sleep(400);
      // Updated (K4): closing the shown model went back to the Overview.
      await openDevice();
      r.reopened = await read();
      r.storage = await page.evaluate(() => localStorage.length + sessionStorage.length);
      await page.click('.card .disclosure[data-tier="2"]');
      await sleep(200);
      r.closed = await read();
      return r;
    """, tmp_path)
    start = out["start"]
    assert start["d2"] == "false" and not start["wellShown"] and not start["stepShown"], start
    assert start["d2text"] == "Configure" and start["d3text"] == "Diagnostics", start
    assert start["deepInWell"], "tier 3 is not inside the tier-2 well"
    assert out["open2"]["d2"] == "true" and out["open2"]["stepShown"], out["open2"]
    assert out["open2"]["d3"] == "false" and not out["open2"]["deepShown"], out["open2"]
    assert out["open3"]["d3"] == "true" and out["open3"]["deepShown"], out["open3"]
    assert out["reopened"]["d2"] == "true" and out["reopened"]["d3"] == "true", out["reopened"]
    assert out["storage"] == 0, "the open state went to the browser's storage"
    assert out["closed"]["d2"] == "false" and not out["closed"]["wellShown"], out["closed"]


@needs_browser
def test_a_disclosure_leads_focus_straight_into_what_it_opens(tiered_station, tmp_path):
    """E: aria-expanded tracks the state and focus order runs body, then
    the disclosure, then the well it opened. Updated (K3): the disclosure is
    drawn where it sits in the page's order, at the foot of the body."""
    view, controller, probe = tiered_station
    out = _browse(view, _TIERED + r"""
      const r = {};
      await page.focus('.card input[name="speed"]');
      const seen = [];
      for (let i = 0; i < 4; i++) {
        await page.keyboard.press('Tab');
        seen.push(await page.evaluate(() => {
          const a = document.activeElement;
          return a.classList.contains('disclosure') ? 'disclosure-' + a.dataset.tier : (a.name || a.textContent.trim());
        }));
      }
      r.closedOrder = seen;
      await page.focus('.card .disclosure[data-tier="2"]');
      await page.keyboard.press('Enter');
      await sleep(200);
      r.expanded = await page.evaluate(() => document.querySelector('.card .disclosure[data-tier="2"]').getAttribute('aria-expanded'));
      await page.keyboard.press('Tab');
      r.next = await page.evaluate(() => document.activeElement.name);
      return r;
    """, tmp_path)
    assert out["closedOrder"][:2] == ["Go", "disclosure-2"], out
    assert out["expanded"] == "true", out
    assert out["next"] == "x_step", "Tab from the open disclosure did not enter its well"


@needs_browser
def test_the_slider_and_its_entry_follow_each_other_and_the_command_reads_the_entry(tiered_station, tmp_path):
    """E: the slider sits BESIDE the entry. Moving it writes the entry;
    typing in the entry moves it; the command still carries the entry's
    value (no new route)."""
    view, controller, probe = tiered_station
    out = _browse(view, _TIERED + r"""
      const r = {};
      r.shape = await page.evaluate(() => {
        const input = document.querySelector('.card input[name="speed"]');
        const range = input.closest('.row').querySelector('input[type="range"]');
        return { both: Boolean(range), min: range.min, max: range.max, value: range.value,
                 fill: range.parentElement.style.getPropertyValue('--fill') };
      });
      await page.evaluate(() => {
        const range = document.querySelector('.card input[type="range"]');
        range.value = '750';
        range.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await sleep(600);
      r.entryAfterSlide = await page.evaluate(() => document.querySelector('.card input[name="speed"]').value);
      await page.$eval('.card input[name="speed"]', (el) => { el.value = ''; });
      await page.click('.card input[name="speed"]');
      await page.keyboard.type('250');
      r.rangeAfterType = await page.evaluate(() => document.querySelector('.card input[type="range"]').value);
      await page.evaluate(() => Array.from(document.querySelectorAll('.card button')).find((b) => b.textContent === 'Go').click());
      await sleep(700);
      return r;
    """, tmp_path)
    assert out["shape"]["both"] and out["shape"]["min"] == "1" and out["shape"]["max"] == "1000", out
    assert out["shape"]["value"] == "400" and out["shape"]["fill"].startswith("39.9"), out
    assert out["entryAfterSlide"] == "750", "the slider did not write the entry"
    assert out["rangeAfterType"] == "250", "the entry did not move the slider"
    assert probe.sent == [250], f"the command did not carry the entry's value: {probe.sent}"


@needs_browser
def test_a_quiet_value_is_not_drawn_in_tier_one_but_is_in_diagnostics(tiered_station, tmp_path):
    """E, status by exception: "Connected" is a normal state (theme
    QUIET_VALUES) - absent from tier 1, still read in tier 3; a value that
    stops being quiet appears in tier 1."""
    view, controller, probe = tiered_station
    out = _browse(view, _TIERED + r"""
      const r = {};
      const links = () => page.evaluate(() => Array.from(document.querySelectorAll('.card .row'))
        .filter((n) => n.querySelector('.label') && n.querySelector('.label').textContent === 'Link')
        .map((n) => ({ tier1: Boolean(n.closest('.card-body')), hidden: n.hidden })));
      r.quiet = await links();
      r.link = await page.evaluate(() => document.getElementById('connection').textContent);
      return r;
    """, tmp_path)
    tier1 = [row for row in out["quiet"] if row["tier1"]]
    tier3 = [row for row in out["quiet"] if not row["tier1"]]
    assert tier1 == [{"tier1": True, "hidden": True}], out
    assert tier3 == [{"tier1": False, "hidden": False}], out
    assert out["link"] == "", "the link line speaks while all is well"
    probe.link = "Lost"
    out = _browse(view, _TIERED + r"""
      return page.evaluate(() => Array.from(document.querySelectorAll('.card .card-body .row'))
        .filter((n) => !n.hidden).map((n) => n.textContent));
    """, tmp_path)
    assert any("Lost" in text for text in out), out


@needs_browser
def test_the_disc_reads_stop_and_clear_with_its_ring(tiered_station, tmp_path):
    """E: A's disc in the rail, always signal red; "Stop" live, "Clear" once
    latched, with the rail's sentence and the sheet's headline; back to
    "Stop" when cleared.

    Signature (owner ruling 2026-09-27; theme.STOP): the ring is now an ink
    COLLAR, 10 px in both states, holding a pale SURFACE socket band;
    latched, the collar turns SIGNAL, the band floods SKIRT (7.11:1 against
    the idle band), the key drops `drop_latched` px and the release glyph
    shows above "Clear". It was a signal band that thickened 3 -> 6 px."""
    view, controller, probe = tiered_station
    out = _browse(view, _TIERED + r"""
      const readDisc = () => page.evaluate(() => {
        const css = getComputedStyle(document.documentElement);
        const swatch = (name) => { const s = document.createElement('span');
          s.style.color = css.getPropertyValue(name).trim(); document.body.appendChild(s);
          const c = getComputedStyle(s).color; s.remove(); return c; };
        const ring = getComputedStyle(document.getElementById('stop-ring'));
        const disc = getComputedStyle(document.getElementById('full-stop'));
        const glyph = document.querySelector('#full-stop .mushroom-glyph');
        const collar = ring.borderTopColor;
        return { face: document.querySelector('#full-stop .mushroom-face').textContent,
                 ring: ring.borderTopWidth,
                 collar: collar === swatch('--text') ? 'ink' : (collar === swatch('--signal') ? 'signal' : collar),
                 band: ring.backgroundColor === swatch('--surface') ? 'surface'
                   : (ring.backgroundColor === swatch('--skirt') ? 'skirt' : ring.backgroundColor),
                 discRed: disc.backgroundColor === swatch('--signal'),
                 top: parseFloat(disc.top),
                 glyph: Boolean(glyph.getClientRects().length),
                 latched: !document.getElementById('rail-latched').hidden,
                 headline: !document.getElementById('sheet-headline').hidden };
      });
      const r = {};
      r.live = await readDisc();
      await page.click('#full-stop');
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Clear');
      await page.mouse.move(5, 5);           // off the disc: its hover tone is not its colour
      await sleep(400);
      r.latched = await readDisc();
      await api('/api/clear_estop_all', { confirmed: true });
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Stop');
      await sleep(400);
      r.cleared = await readDisc();
      return r;
    """, tmp_path)
    live, latched = dict(out["live"]), dict(out["latched"])
    drop = latched.pop("top") - live.pop("top")
    assert live == {"face": "Stop", "ring": "10px", "collar": "ink", "band": "surface",
                    "discRed": True, "glyph": False, "latched": False, "headline": False}, out
    assert latched == {"face": "Clear", "ring": "10px", "collar": "signal", "band": "skirt",
                       "discRed": True, "glyph": True, "latched": True, "headline": True}, out
    assert drop == 6, out
    assert out["cleared"] == out["live"], out


@needs_browser
def test_the_stop_is_reachable_with_red_percents_details_open_at_900(sim_station, tmp_path):
    """E: with Red Percent's Details (its whole tier 2) open and scrolled to
    the bottom at 900x900, the disc is in the viewport and what a click at
    its centre lands on, and the click stops."""
    view, controller = sim_station
    out = _browse(view, r"""
      await until(() => document.querySelectorAll('#cards .card:not(.setup-card)').length >= 6, 8000);
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(400);
      }
      await page.setViewport({ width: 900, height: 900 });
      // Updated (D, 2026-09-28): Red Percent is drawn on the Transfer Map's
      // page, its details after the Map's own.
      await page.evaluate(() => {
        Array.from(document.querySelectorAll('.model-link')).find((b) => b.textContent === 'Transfer Map').click();
        Array.from(document.querySelectorAll('#cards .disclosure[data-tier="2"]'))
          .find((d) => d.textContent.trim() === 'Red Percent details').click();
      });
      await sleep(800);
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      await sleep(400);
      const r = { onTop: await page.evaluate(%s) };
      r.inView = await page.evaluate(() => {
        const b = document.getElementById('full-stop').getBoundingClientRect();
        return b.top >= 0 && b.bottom <= innerHeight && b.left >= 0 && b.width >= 100;
      });
      r.scrolled = await page.evaluate(() => window.scrollY > 0);
      const box = await page.evaluate(() => {
        const b = document.getElementById('full-stop').getBoundingClientRect();
        return [b.left + b.width / 2, b.top + b.height / 2];
      });
      await page.mouse.click(box[0], box[1]);
      await sleep(1500);
      r.latched = (await api('/api/state')).is_estopped;
      return r;
    """ % _STOP_HIT, tmp_path)
    assert out["scrolled"], "the details did not make the page scroll; the test proves nothing"
    assert out["onTop"] and out["inView"], out
    assert out["latched"] is True, "a click on the disc did not stop"


# --------------------------------------------------------------------------
# Tier K (2026-09-26): the disclosure sits where it opens (K3); two pages on
# the sheet, Overview and the device page (K4)
# --------------------------------------------------------------------------
#: Press a page in the rail by its words ("Overview" or a model's name).
_PAGES = r"""
  const press = async (words) => {
    await page.evaluate((w) => Array.from(document.querySelectorAll('#model-nav button'))
      .find((b) => b.textContent === w).click(), words);
    await sleep(300);
  };
  const pages = () => page.evaluate(() => {
    const nav = Array.from(document.querySelectorAll('#model-nav button'));
    const shown = (n) => Boolean(n && n.getClientRects().length);
    const cards = Array.from(document.querySelectorAll('#cards .card'));
    const sheet = document.getElementById('cards').getBoundingClientRect();
    return {
      nav: nav.map((b) => b.textContent),
      current: nav.filter((b) => b.getAttribute('aria-current')).map((b) => b.textContent),
      shown: cards.filter(shown).map((c) => c.querySelector('.card-title').textContent),
      wells: cards.filter((c) => shown(c.querySelector('.tier-well'))).length,
      disclosures: cards.filter((c) => shown(c.querySelector('.disclosure'))).length,
      opens: cards.filter(shown).map((c) => {
        const o = c.querySelector('.card-head .card-open');
        return o && shown(o) ? { text: o.textContent, name: o.getAttribute('aria-label') } : null;
      }),
      fullWidth: cards.filter(shown).every((c) => c.getBoundingClientRect().width >= sheet.width - 1),
    };
  });
"""


@needs_browser
def test_the_tier_two_disclosure_sits_at_the_foot_of_the_body_above_its_well(tiered_station, tmp_path):
    """K3: the disclosure is not in the entry's head; it is the last thing
    after the tier-1 body, left-aligned with it, it says the schema's
    phrase, and the well it opens follows it with no gap.

    Signature (spec "Disclosure"): the tray follows its disclosure key 6 px
    below, not flush - the key is now a raised part with a lip, and the
    tray a sunk well; the gap is the one step between them."""
    view, controller, probe = tiered_station
    out = _browse(view, _TIERED + _PAGES + r"""
      await press('Tiered Probe');
      const geo = () => page.evaluate(() => {
        const c = Array.from(document.querySelectorAll('#cards .card'))
          .find((n) => n.querySelector('.card-title').textContent === 'Tiered Probe');
        const d = c.querySelector('.disclosure[data-tier="2"]');
        const well = document.getElementById(d.getAttribute('aria-controls'));
        const body = c.querySelector('.card-body').getBoundingClientRect();
        const head = c.querySelector('.card-head').getBoundingClientRect();
        const b = d.getBoundingClientRect();
        const w = well.getBoundingClientRect();
        return { inHead: Boolean(d.closest('.card-head')), text: d.textContent,
                 before: d.previousElementSibling && d.previousElementSibling.className,
                 after: d.nextElementSibling === well,
                 belowBody: b.top >= body.bottom - 0.5, belowHead: b.top > head.bottom,
                 leftGap: b.left - body.left, gap: well.hidden ? null : w.top - b.bottom };
      });
      const r = { closed: await geo() };
      await page.click('.card .disclosure[data-tier="2"]');
      await sleep(250);
      r.open = await geo();
      return r;
    """, tmp_path)
    closed, opened = out["closed"], out["open"]
    assert not closed["inHead"] and closed["belowHead"], closed
    assert closed["text"] == "Configure", closed
    assert "card-body" in closed["before"] and closed["after"], closed
    assert closed["belowBody"] and abs(closed["leftGap"]) <= 4, closed
    assert opened["gap"] is not None and abs(opened["gap"] - 6) <= 1, opened


def _page_names(controller):
    """The models with a page of their own: all but the ones a host draws
    (Updated, D 2026-09-28: Red Percent is on the Transfer Map's page)."""
    models = controller.state()["models"]
    return [n for n in controller.model_names if not models[n].get("host")]


@needs_browser
def test_the_rail_leads_with_an_overview_of_every_model_with_no_wells(sim_station, tmp_path):
    """K4: the rail's first item is Overview and it is the page at launch;
    the overview shows every launched model with its head a press target
    ("Open", named "Open <model>") and no well or disclosure anywhere."""
    view, controller = sim_station
    out = _browse(view, _PAGES + r"""
      await until(() => document.querySelectorAll('#cards .card:not(.setup-card)').length >= 6, 8000);
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(400);
      }
      return pages();
    """, tmp_path)
    names = _page_names(controller)
    assert out["nav"][0] == "Overview" and out["nav"][1:] == names, out
    assert out["current"] == ["Overview"], out
    assert sorted(out["shown"]) == sorted(names), out
    assert out["wells"] == 0 and out["disclosures"] == 0, out
    assert out["opens"] == [{"text": "Open", "name": "Open " + n} for n in out["shown"]], out


@needs_browser
def test_a_device_page_shows_one_model_and_overview_brings_them_all_back(sim_station, tmp_path):
    """K4: a press on a model (the rail, or an overview head - anywhere on
    it, or Return on its Open) shows only that model, full width, with its
    disclosure (the schema's phrase, K2); Overview returns; a tier opened on
    the device page is open again on the next visit."""
    view, controller = sim_station
    out = _browse(view, _PAGES + r"""
      await until(() => document.querySelectorAll('#cards .card:not(.setup-card)').length >= 6, 8000);
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(400);
      }
      const r = {};
      await page.evaluate(() => { window.cardOf = (t) => Array.from(document.querySelectorAll('#cards .card'))
        .find((c) => c.querySelector('.card-title').textContent === t); });
      await press('Stepper Probe');
      r.byRail = await pages();
      r.text = await page.evaluate(() => cardOf('Stepper Probe').querySelector('.disclosure[data-tier="2"]').textContent);
      await page.evaluate(() => cardOf('Stepper Probe').querySelector('.disclosure[data-tier="2"]').click());
      await sleep(250);
      await press('Overview');
      r.back = await pages();
      // Anywhere on the head: its title, not the Open word.
      const title = await page.evaluateHandle(() => Array.from(document.querySelectorAll('#cards .card-title'))
        .find((t) => t.textContent === 'Stepper Probe'));
      await title.click();
      await sleep(300);
      r.byHead = await pages();
      r.remembered = await page.evaluate(() => cardOf('Stepper Probe')
        .querySelector('.disclosure[data-tier="2"]').getAttribute('aria-expanded'));
      await press('Overview');
      await page.evaluate(() => cardOf('DC Probe').querySelector('.card-open').focus());
      await page.keyboard.press('Enter');
      await sleep(300);
      r.byKey = await pages();
      return r;
    """, tmp_path)
    names = _page_names(controller)
    for key in ("byRail", "byHead"):
        page = out[key]
        assert page["shown"] == ["Stepper Probe"] and page["current"] == ["Stepper Probe"], page
        assert page["fullWidth"] and page["disclosures"] == 1 and page["opens"] == [None], page
    assert out["text"] == "Configure Stepper Probe", out
    assert sorted(out["back"]["shown"]) == sorted(names) and out["back"]["wells"] == 0, out["back"]
    assert out["back"]["current"] == ["Overview"], out["back"]
    assert out["remembered"] == "true", "the tier's open state did not survive the trip"
    assert out["byHead"]["wells"] == 1, out["byHead"]
    assert out["byKey"]["shown"] == ["DC Probe"] and out["byKey"]["current"] == ["DC Probe"], out


@needs_browser
def test_closing_the_shown_device_returns_to_the_overview(sim_station, tmp_path):
    """K4: the device page's model is closed; the sheet goes back to the
    overview of the models that remain, and the rail says so."""
    view, controller = sim_station
    out = _browse(view, _PAGES + r"""
      await until(() => document.querySelectorAll('#cards .card:not(.setup-card)').length >= 6, 8000);
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(400);
      }
      await press('Rotator');
      const r = { device: await pages() };
      await api('/api/close_model', { name: 'Rotator' });
      await until(() => !Array.from(document.querySelectorAll('#cards .card-title'))
        .some((t) => t.textContent === 'Rotator'));
      await sleep(300);
      r.after = await pages();
      return r;
    """, tmp_path)
    assert out["device"]["shown"] == ["Rotator"], out
    after = out["after"]
    assert after["current"] == ["Overview"] and "Rotator" not in after["nav"], after
    # Seven models, one closed (Tier S); Red Percent is on the Map's entry (D).
    assert len(after["shown"]) == 5 and after["wells"] == 0, after


# --------------------------------------------------------------------------
# Tier L (audit round 7, 2026-09-26): the Web view's rows
# --------------------------------------------------------------------------
def test_the_state_carries_the_words_every_view_says_about_the_stop(station):
    """L1: the server serves `views.base.stop_words(controller.stop_state)`
    beside `stop`, so the client never re-derives the disc's face, the
    headline or the rail line."""
    view, controller, probe = station
    status, data = _get(view, "/api/state")
    assert status == 200
    assert data["stop_words"] == {"face": "Stop", "action": "stop", "headline": "",
                                  "subline": "", "rail": ""}, data
    probe.stop_confirms = False
    controller.estop_all()
    _, data = _get(view, "/api/state")
    # The three facts; `since` (the latch time, L1 follow-up) is a fourth key
    # the page uses on reload and this test does not pin.
    assert {k: data["stop"][k] for k in ("latched", "unconfirmed", "every")} == {
        "latched": ["Fake Probe"], "unconfirmed": ["Fake Probe"], "every": True}
    assert data["stop_words"]["face"] == "Clear" and data["stop_words"]["action"] == "clear"
    assert data["stop_words"]["headline"] == "Stopped. Fake Probe did not confirm."


@pytest.fixture
def two_probes(tmp_path):
    """Two models, so one of them can be latched from its own switch."""
    controller = Controller()
    first, second = FakeProbe(root=str(tmp_path)), FakeProbe(root=str(tmp_path))
    controller.factory = lambda config: FakeProbe(root=config.get("root"))
    controller.add("Fake Probe", first, {"root": str(tmp_path)})
    controller.add("Other Probe", second, {"root": str(tmp_path)})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, first, second
    finally:
        view.close()


#: The stop as the page says it: the disc, its hint, the rail line, the
#: headline and subline, the per-model marks in the rail, and the tray.
_STOP_READ = r"""
  const readStop = () => page.evaluate(() => {
    const stop = document.getElementById('full-stop');
    const hint = document.querySelector('.stop-hint');
    const rail = document.getElementById('rail-latched');
    const head = document.getElementById('sheet-headline');
    const marks = {};
    for (const link of document.querySelectorAll('#model-nav .model-link[data-model]')) {
      const m = link.querySelector('.nav-mark');
      marks[link.dataset.model] = m && !m.hidden && m.getClientRects().length
        ? { words: m.textContent.trim(), title: m.title,
            latched: m.classList.contains('is-latched'),
            unconfirmed: m.classList.contains('is-unconfirmed') } : null;
    }
    const tray = document.getElementById('tray-latest');
    const text = tray.querySelector('.tray-text');
    return { face: stop.textContent.trim(), keys: stop.getAttribute('aria-keyshortcuts'),
             label: stop.getAttribute('aria-label'),
             hint: Boolean(hint && !hint.hidden && getComputedStyle(hint).visibility !== 'hidden'
                           && hint.getClientRects().length),
             rail: rail.hidden ? '' : rail.textContent.trim(),
             headline: head.hidden ? '' : head.querySelector('.headline').textContent,
             subline: head.hidden ? '' : head.querySelector('.headline-note').textContent,
             marks, tray: tray.textContent.trim(),
             trayTitle: text ? text.title : null,
             trayClips: text ? getComputedStyle(text).textOverflow : null };
  });
  const ackAll = async () => {
    if (!(await page.evaluate(() => document.getElementById('ack-modal').hidden))) {
      await page.click('#ack-ok');
      await sleep(200);
    }
  };
"""


@needs_browser
def test_one_models_own_stop_leaves_the_disc_a_stop_for_the_rest(two_probes, tmp_path):
    """L1 (S1, IMP7-1/2/3): Fake Probe is latched from its own switch; Other
    Probe is live. The disc still reads Stop, still advertises the chord,
    and a press stops the live one; there is no "every model" headline, the
    rail line names the one stopped model and the rail's list marks it.
    Then a global stop that Other Probe does not confirm: the disc reads
    Clear, the headline names the model that did not confirm and says to
    treat it as live, and the rail marks it "did not confirm". The chord's
    hint is visible throughout."""
    view, controller, first, second = two_probes
    first.is_estopped = True
    first.stop_confirmed = True
    second.stop_confirms = False
    out = _browse(view, _STOP_READ + r"""
      await until(() => document.querySelectorAll('#model-nav .model-link[data-model]').length === 2);
      await until(() => !document.getElementById('rail-latched').hidden);
      await sleep(300);
      const r = { partial: await readStop() };
      await page.click('#full-stop');
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Clear');
      await sleep(600);
      await ackAll();
      r.unconfirmed = await readStop();
      r.state = await api('/api/state');
      return r;
    """, tmp_path)
    partial = out["partial"]
    assert partial["face"] == "Stop" and partial["keys"] == "Control+Period", partial
    assert partial["label"] == "Stop every model" and partial["hint"], partial
    assert partial["headline"] == "" and partial["rail"] == "Stopped: Fake Probe", partial
    assert partial["marks"]["Other Probe"] is None, partial
    mark = partial["marks"]["Fake Probe"]
    assert mark and mark["latched"] and not mark["unconfirmed"], partial
    assert mark["words"] == "stopped" and mark["title"] == "Stopped", mark
    # The press on a Stop face was estop_all: the live model is latched too.
    assert out["state"]["stop"]["latched"] == ["Fake Probe", "Other Probe"], out["state"]
    after = out["unconfirmed"]
    assert after["face"] == "Clear" and after["keys"] is None and after["hint"], after
    assert after["headline"] == "Stopped. Other Probe did not confirm.", after
    assert after["subline"] == "Treat it as live until you have checked it by hand.", after
    assert after["rail"] == "Stopped: Other Probe did not confirm", after
    other = after["marks"]["Other Probe"]
    assert other and other["unconfirmed"] and other["words"] == "did not confirm", after
    assert other["title"] == "Did not confirm the stop", other
    assert after["marks"]["Fake Probe"]["latched"], after
    # Web only: the tray line ellipsizes in its own span and says it whole
    # on hover.
    assert after["trayClips"] == "ellipsis" and after["trayTitle"] == after["tray"], after


@needs_browser
def test_the_unconfirmed_line_does_not_outlive_its_latch_nor_come_back_on_reload(
        two_probes, tmp_path, monkeypatch):
    """L2 (S2, IMP7-5): while the latch is set the tray says the stop was not
    confirmed, and a reload still says so; once the latch is cleared the
    line goes from the tray, and a reload does not bring it back."""
    # The event log is one per process: the previous test's identical event
    # would otherwise be merged into this one (a count, not a new id).
    monkeypatch.setattr(events, "DEDUPE_SECONDS", 0.0)
    view, controller, first, second = two_probes
    second.stop_confirms = False
    out = _browse(view, _STOP_READ + r"""
      const r = {};
      await page.click('#full-stop');
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Clear');
      await sleep(600);
      await ackAll();
      r.latched = await readStop();
      await page.reload({ waitUntil: 'load' });
      await card();
      await sleep(800);
      r.reloadLatched = await readStop();
      await page.click('#full-stop');
      await until(() => !document.getElementById('confirm-modal').hidden);
      await page.click('#confirm-yes');
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Stop');
      await sleep(700);
      r.cleared = await readStop();
      await page.reload({ waitUntil: 'load' });
      await card();
      await sleep(800);
      r.reloadCleared = await readStop();
      return r;
    """, tmp_path)
    for key in ("latched", "reloadLatched"):
        assert "Other Probe did not confirm" in out[key]["tray"], (key, out[key])
    for key in ("cleared", "reloadCleared"):
        state = out[key]
        assert "confirm" not in state["tray"], (key, state)
        assert state["rail"] == "" and state["headline"] == "", (key, state)
        assert all(mark is None for mark in state["marks"].values()), (key, state)


@needs_browser
def test_a_disabled_command_says_why(station, tiered_station, tmp_path):
    """L3 (S2, IMP7-7): a command greyed by its gate carries the reason as
    its title; a `go` command also says it in one muted caption under its
    row, and the caption goes when the command is live again."""
    view, controller, probe = station
    probe.mode = "running"
    out = _browse(view, r"""
      await until(() => Array.from(document.querySelectorAll('.card button'))
        .some((b) => b.textContent === 'Park' && b.disabled));
      return page.evaluate(() => {
        const park = Array.from(document.querySelectorAll('.card button')).find((b) => b.textContent === 'Park');
        const home = Array.from(document.querySelectorAll('.card button')).find((b) => b.textContent === 'Home');
        return { park: park.title, parkDisabled: park.disabled, home: home.title };
      });
    """, tmp_path)
    assert out == {"park": "A run is in progress", "parkDisabled": True, "home": ""}, out

    tview, tcontroller, tprobe = tiered_station
    tprobe.mode = "manual"
    out = _browse(tview, _TIERED + r"""
      const readGo = () => page.evaluate(() => {
        const go = Array.from(document.querySelectorAll('.card button')).find((b) => b.textContent === 'Go');
        const notes = Array.from(document.querySelectorAll('.card .gate-note'))
          .filter((n) => !n.hidden && n.getClientRects().length).map((n) => n.textContent);
        const note = document.querySelector('.card .gate-note');
        return { disabled: go.disabled, title: go.title, notes,
                 below: note && !note.hidden ? note.getBoundingClientRect().top
                   >= go.getBoundingClientRect().bottom - 0.5 : null };
      });
      await until(() => Array.from(document.querySelectorAll('.card button'))
        .some((b) => b.textContent === 'Go' && b.disabled));
      await sleep(300);
      return readGo();
    """, tmp_path)
    # Updated (O3, IMP8-1): Go is `disabled_when=("manual",)`, so in manual
    # mode the reason names the mode it IS in; "Not in manual mode" was the
    # inverse. The words are the served `views.base.GATE_WORDS`.
    assert out["disabled"] and out["title"] == "In manual mode", out
    assert out["notes"] == ["In manual mode"] and out["below"], out


#: Every visible pressable, measured: its box, and whether it is a command.
_TARGETS = r"""
  const targets = () => page.evaluate(() => {
    const out = [];
    const pressables = document.querySelectorAll(
      'button, input:not([type="file"]), select, [role="switch"], a[href]');
    for (const node of pressables) {
      if (node.closest('[hidden]') || node.classList.contains('skip-link')) continue;
      const b = node.getBoundingClientRect();
      if (!b.width || !b.height) continue;
      const style = getComputedStyle(node);
      if (style.visibility === 'hidden') continue;
      out.push({ what: (node.getAttribute('aria-label') || node.textContent || node.name
                        || node.className).trim().slice(0, 40),
                 w: Math.round(b.width * 10) / 10, h: Math.round(b.height * 10) / 10,
                 command: node.matches('button.button') });
    }
    return out;
  });
"""


@needs_browser
def test_every_pressable_is_a_real_target(sim_station, tmp_path):
    """L4 (S2, IMP7-8): every pressable is at least 24 px both ways (WCAG
    2.5.8) and every command at least 36 px tall - on the Overview, on a
    device page with both tiers open, and in Setup, where a row's tick and
    its name are one target (the name is the tick's label). Not 44: that
    is an owner call."""
    view, controller = sim_station
    out = _browse(view, _PAGES + _TARGETS + r"""
      await until(() => document.querySelectorAll('#cards .card:not(.setup-card)').length >= 6, 8000);
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(400);
      }
      const r = { overview: await targets() };
      await press('Stepper Probe');
      await page.evaluate(() => {
        const c = document.querySelector('#cards .card.is-opened');
        for (const t of ['2', '3']) {
          const d = c.querySelector('.disclosure[data-tier="' + t + '"]');
          if (d && d.getAttribute('aria-expanded') !== 'true') d.click();
        }
      });
      await sleep(300);
      r.device = await targets();
      await page.click('#setup-link');
      await sleep(500);
      r.setup = await page.evaluate(() => {
        const rows = Array.from(document.querySelectorAll('#setup-drawer .section-row'))
          .filter((s) => s.querySelector('input[type="checkbox"]'));
        return rows.map((s) => {
          const name = s.querySelector('.row-title');
          const box = s.querySelector('input[type="checkbox"]');
          return { name: name && name.textContent, isLabel: Boolean(name && name.control === box) };
        });
      });
      r.setupTargets = (await targets()).filter((t) => true);
      return r;
    """, tmp_path)
    for key in ("overview", "device", "setupTargets"):
        small = [t for t in out[key] if min(t["w"], t["h"]) < 23.5]
        assert not small, (key, small)
        short = [t for t in out[key] if t["command"] and t["h"] < 35.5]
        assert not short, (key, short)
    assert out["setup"] and all(row["isLabel"] for row in out["setup"]), out["setup"]


@needs_browser
def test_the_slider_keyboard_steps_one_percent_and_ignores_home_and_end(tiered_station, tmp_path):
    """L6 (S2, TK7-5): on the slider an arrow moves 1 % of the travel
    (1..1000 -> 10), Page keys 10 %, and Home / End do nothing - End used
    to set the maximum speed in one key. Nothing is sent: the value travels
    with the next command, as the entry's always has."""
    view, controller, probe = tiered_station
    out = _browse(view, _TIERED + r"""
      const box = () => page.evaluate(() => document.querySelector('.card input[name="speed"]').value);
      await until(async () => true);
      await page.focus('.card .slider-range');
      const r = { start: await box() };
      await page.keyboard.press('ArrowRight'); r.right = await box();
      await page.keyboard.press('ArrowUp'); r.up = await box();
      await page.keyboard.press('ArrowLeft'); r.left = await box();
      await page.keyboard.press('End'); r.end = await box();
      await page.keyboard.press('Home'); r.home = await box();
      await page.keyboard.press('PageUp'); r.pageUp = await box();
      await page.keyboard.press('PageDown'); r.pageDown = await box();
      await sleep(400);
      return r;
    """, tmp_path)
    assert out == {"start": "400", "right": "410", "up": "420", "left": "410",
                   "end": "410", "home": "410", "pageUp": "510", "pageDown": "410"}, out
    assert probe.sent == [], "a key on the slider sent a command"


@needs_browser
def test_a_rail_reading_that_is_unknown_is_drawn_as_a_muted_dash(tiered_station, tmp_path):
    """L10 (IMP7-6, owner's tier ruling): a `rail: true` tier-1 reading is
    never hidden by status-by-exception; unknown, it is "--", muted, at the
    reading's own size. A quiet non-rail value (Link: Connected) still is."""
    view, controller, probe = tiered_station
    probe.position_x = ""
    out = _browse(view, _TIERED + r"""
      await sleep(300);
      return page.evaluate(() => {
        const c = document.querySelector('#cards .card.is-opened');
        const x = c.querySelector('.card-body .reading');
        const v = x.querySelector('.value');
        const link = Array.from(c.querySelectorAll('.card-body .row.stat'))
          .find((r) => r.querySelector('.label').textContent.startsWith('Link'));
        const focal = getComputedStyle(document.documentElement).getPropertyValue('--reading-focal').trim();
        return { shown: !x.hidden && Boolean(x.getClientRects().length), text: v.textContent,
                 muted: v.classList.contains('is-empty'), size: getComputedStyle(v).fontSize, focal,
                 linkShown: Boolean(link && !link.hidden && link.getClientRects().length) };
      });
    """, tmp_path)
    assert out["shown"] and out["text"] == "--" and out["muted"], out
    assert out["size"] == out["focal"], out
    assert out["linkShown"] is False, out


@needs_browser
def test_an_event_line_is_title_and_message_in_sentence_case(station, tmp_path):
    """L11 (IMP7-4): the tray, the log and the acknowledgement say the
    event's title and message in sentence case, without the "[source]"
    prefix of the raw log line."""
    view, controller, probe = station
    out = _browse(view, r"""
      await api('/api/run', { name: 'Fake Probe', command: 'jam', inputs: {}, args: [] });
      await until(() => !document.getElementById('ack-modal').hidden);
      await sleep(300);
      return page.evaluate(() => ({
        // Updated (rb-ack A3): the title is the dialog's heading and the
        // message its body; together they read as the line does.
        ack: document.getElementById('ack-title').textContent + ': '
          + document.querySelector('#ack-text .ack-line').textContent,
        tray: document.getElementById('tray-latest').textContent.trim(),
        log: Array.from(document.querySelectorAll('#event-log .event')).pop().textContent,
      }));
    """, tmp_path)
    for key in ("ack", "tray"):
        assert out[key].startswith("Command failed: Jam did not complete."), out
    assert "[" not in out["ack"] + out["tray"] + out["log"], out
    assert "Command failed: Jam did not complete." in out["log"], out


@needs_browser
def test_an_empty_plot_pane_is_one_caption_tall(sim_station, tmp_path):
    """L15 (IMP7-13 part, TK7-13): Red Percent's details hold two plot panes
    with nothing in them; each is one caption line until it has data, not
    a 200 px dashed box."""
    view, controller = sim_station
    out = _browse(view, _PAGES + r"""
      await until(() => document.querySelectorAll('#cards .card:not(.setup-card)').length >= 6, 8000);
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(400);
      }
      // Updated (D, 2026-09-28): Red Percent's details are on the Transfer
      // Map's page, in the tiers drawn after the Map's own.
      await press('Transfer Map');
      await page.evaluate(() => Array.from(document.querySelectorAll('#cards .card-tiers .disclosure[data-tier="2"]'))
        .find((d) => d.textContent.trim() === 'Red Percent details').click());
      await sleep(1500);
      return page.evaluate(() => Array.from(document.querySelectorAll('#cards .card-tiers .plot-frame'))
        .filter((f) => f.getClientRects().length)
        .map((f) => ({ h: f.getBoundingClientRect().height,
                       note: (f.querySelector('.empty-note') || {}).textContent,
                       empty: Boolean(f.querySelector('.empty-note:not([hidden])')) })));
    """, tmp_path)
    # The live plot has no samples before a run; the analysis figure is a
    # picture the model draws even with no run loaded, so it is not empty.
    empties = [f for f in out if f["empty"]]
    assert len(empties) >= 1, out
    assert all(f["h"] <= 40 for f in empties), out


@needs_browser
def test_every_control_is_named_by_its_words_and_its_model(sim_station, tmp_path):
    """L16 (IMP7-10, WCAG 2.5.3): on the Overview a control's accessible name
    holds the words it shows, and no two controls share a name - "Step" or
    "Enter autonomous mode" is said with its model."""
    view, controller = sim_station
    out = _browse(view, r"""
      await until(() => document.querySelectorAll('#cards .card:not(.setup-card)').length >= 6, 8000);
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(400);
      }
      await sleep(300);
      return page.evaluate(() => {
        const out = [];
        for (const n of document.querySelectorAll('#cards button, #cards input, #cards select')) {
          if (!n.getClientRects().length || n.closest('[hidden]')) continue;
          const labelled = n.getAttribute('aria-label');
          const words = n.matches('button') ? n.textContent.trim() : '';
          out.push({ name: labelled || words, words });
        }
        return out;
      });
    """, tmp_path)
    names = [c["name"] for c in out]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, dupes
    missing = [c for c in out if c["words"] and c["words"].lower() not in c["name"].lower()]
    assert not missing, missing


@needs_browser
def test_setup_says_its_statuses_in_sentence_case_and_cancel_scan_only_while_scanning(sim_station, tmp_path):
    """L17 (IMP7-12): Setup's row statuses start with a capital; the Cancel
    scan command is not drawn unless a scan is running."""
    view, controller = sim_station
    out = _browse(view, r"""
      if (!(await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open')))) {
        await page.click('#setup-link'); await sleep(500);
      }
      await sleep(300);
      return page.evaluate(() => {
        const drawer = document.getElementById('setup-drawer');
        // L17 is about the status lines; any other value (the version sha,
        // the address) is shown as the model gives it (W2).
        const words = Array.from(drawer.querySelectorAll('.section-row .row.stat[data-attr$="_status"] .value'))
          .filter((v) => v.getClientRects().length).map((v) => v.textContent);
        const cancel = Array.from(drawer.querySelectorAll('button')).find((b) => b.textContent === 'Cancel scan');
        return { words, cancel: Boolean(cancel && cancel.getClientRects().length) };
      });
    """, tmp_path)
    lower = [w for w in out["words"] if w[:1].isalpha() and w[:1] != w[:1].upper()]
    assert out["words"] and not lower, out
    assert out["cancel"] is False, out


@needs_browser
def test_a_well_does_not_repeat_its_disclosure_as_a_heading(tiered_station, tmp_path):
    """L18 (IMP7-13): Diagnostics opens onto its content, not onto a second
    "Diagnostics"; a section whose title differs (Configuration under
    Configure) keeps it."""
    view, controller, probe = tiered_station
    out = _browse(view, _TIERED + r"""
      await page.evaluate(() => {
        const c = document.querySelector('#cards .card.is-opened');
        for (const t of ['2', '3']) c.querySelector('.disclosure[data-tier="' + t + '"]').click();
      });
      await sleep(300);
      return page.evaluate(() => Array.from(document.querySelectorAll('#cards .card.is-opened .section-title'))
        .filter((t) => t.closest('.tier-well') && t.getBoundingClientRect().height > 2)
        .map((t) => t.textContent));
    """, tmp_path)
    assert out == ["Configuration"], out


@needs_browser
def test_after_quit_the_tray_stays_opaque_and_boot_warnings_stay_in_the_log(station, tmp_path):
    """L21 (IMP7-9, IMP7-11): a warning from before the tab opened (a port no
    model uses) is history - it is in the log, not the tray's standing line;
    after Quit the tray keeps its opaque fill, so nothing on the sheet shows
    through it."""
    view, controller, probe = station
    events.warn("Port Unverified", "/dev/cu.debug-console opened, but nothing answered "
                "the identity query. Operating blind.", source="SerialPort /dev/cu.debug-console")
    out = _browse(view, r"""
      const r = await page.evaluate(() => ({
        tray: document.getElementById('tray-latest').textContent.trim(),
        log: Array.from(document.querySelectorAll('#event-log .event')).map((e) => e.textContent),
      }));
      await page.click('#quit-link');
      await until(() => !document.getElementById('confirm-modal').hidden);
      await page.click('#confirm-yes');
      await until(() => document.body.classList.contains('is-shut-down'));
      await sleep(300);
      r.opacity = await page.evaluate(() => getComputedStyle(document.getElementById('log-panel')).opacity);
      return r;
    """, tmp_path)
    assert out["tray"] == "", out
    assert any("Operating blind" in line for line in out["log"]), out
    assert out["opacity"] == "1", out


# -- round 8, WDG8-1: the data route and the request guard -------------------

def test_the_data_route_runs_only_declared_data_commands(station):
    """`GET /api/data?command=step` ran a Step from any page's <img>. Only a
    plot / image / log source may be reached this way."""
    view, controller, probe = station
    name = probe.NAME.replace(" ", "%20")
    status, body = _get(view, f"/api/data?name={name}&command=toggle_estop")
    assert status == 403 and body["status"] == "refused"
    assert controller.state()["stop"]["latched"] == [], "nothing ran"


def test_a_json_post_without_an_origin_is_refused(station):
    view, controller, probe = station
    body = {"name": probe.NAME, "command": "toggle_estop", "inputs": {}, "args": []}
    status, _, raw = _request(view, "/api/run", "POST", body,
                              {"Content-Type": "application/json"})
    assert status == 403 and b"Origin" in raw
    assert controller.state()["stop"]["latched"] == []
    status, data = _post(view, "/api/run", body)      # the helper sends Origin
    assert status == 200 and data["status"] in ("ok", "needs_confirm"), data


def test_a_foreign_host_header_is_refused_on_a_plain_get(station):
    view, _, _ = station
    status, _, _ = _request(view, "/api/state", headers={"Host": "evil.example"})
    assert status == 403


def test_every_response_forbids_framing_and_sniffing(station):
    view, _, _ = station
    status, headers, _ = _request(view, "/")
    assert status == 200
    assert headers.get("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in (headers.get("Content-Security-Policy") or "")
    assert headers.get("X-Content-Type-Options") == "nosniff"


def test_theme_json_serves_the_gate_words_and_the_watchdog_seconds(station):
    view, _, _ = station
    status, body = _get(view, "/api/theme.json")
    assert status == 200
    assert body["gate_words"]["manual"] == ["In manual mode", "Not in manual mode"]
    assert body["watchdog"] == {"warn_seconds": 5.0, "stop_seconds": 15.0}


# ==========================================================================
# Tier N + O (2026-09-26): the idle countdown, the close-tab warning, the
# energized and faulted marks, the Quit end state, live regions, busy
# commands, commits, and the words. A probe-shaped model that carries the
# state those read: `idle_remaining`, `is_energized`, `is_faulted`.
# ==========================================================================

class ModeProbe(Panel):
    NAME = "Mode Probe"
    PARAMS = {"x_dist": Param("x_dist", "int", default=0, minimum=-1000, maximum=1000,
                              label="X distance"),
              "x_step": Param("x_step", "int", default=16, minimum=1, maximum=1000,
                              label="X step size"),
              "full_speed": Param("full_speed", "int", default=400, minimum=1,
                                  maximum=5000, label="Autonomous speed")}

    def __init__(self, name="Mode Probe"):
        super().__init__()
        self.NAME = name
        self.mode = "disabled"
        self.is_estopped = False
        self.stop_confirmed = None
        self.stop_confirms = True
        self.idle_remaining = None
        self.idle_warn_seconds = 60
        self.is_energized = False
        self.is_active = False
        self.fault = ""
        self.extended = 0
        self.nudges = []
        self.steps = 0
        self.step_seconds = 0.0
        self.position_x = "0"

    def open(self):
        pass

    def close(self):
        pass

    def on_model_added(self, name, model):
        pass

    def on_model_removed(self, name, model):
        pass

    @property
    def mode_name(self):
        return self.mode

    @property
    def gate_mode(self):
        return "latched" if self.is_estopped else self.mode

    @property
    def is_auto(self):
        return self.mode == "autonomous"

    @property
    def is_faulted(self):
        return bool(self.fault)

    @property
    def schema(self):
        P = self.PARAMS
        return sch.schema(
            sch.section("Position", sch.readonly("X:", "position_x", rail=True)),
            sch.section("Speeds",
                        sch.entry("Autonomous speed:", "full_speed", P["full_speed"],
                                  disabled_when=("autonomous", "manual"), slider=(1, 1000))),
            sch.section(
                "System Control",
                sch.toggle("Autonomous:", "is_auto", "set_mode",
                           "Autonomous mode (press to stop)", "Enter autonomous mode",
                           on_args=["autonomous"], off_args=["disabled"],
                           disabled_when=("latched", "fault")),
                sch.button("Step", "step", inputs=("x_dist", "full_speed"), role="go",
                           disabled_when=("manual", "latched")),
                # CON-1: two buttons, one command, told apart by fixed args.
                sch.button("Nudge -", "nudge", args=(-1,)),
                sch.button("Nudge +", "nudge", args=(1,)),
                {"type": "internal", "command": "extend_idle", "writable": False,
                 "role": "neutral"}),
            sch.section("Configuration",
                        sch.entry("X step size:", "x_step", P["x_step"]),
                        sch.entry("X distance:", "x_dist", P["x_dist"]),
                        tier=2, disclosure=f"Configure {self.NAME}"),
            sch.section("Diagnostics",
                        sch.toggle("Stop", "is_estopped", "toggle_estop",
                                   "Stopped", "Stop this model"),
                        sch.readonly("Fault reason:", "fault", role="danger"),
                        tier=3, disclosure="Diagnostics"),
        )

    @property
    def state(self):
        snapshot = super().state
        snapshot.update({"age": 0.0, "is_estopped": self.is_estopped,
                         "stop_confirmed": self.stop_confirmed if self.is_estopped else None,
                         "is_active": self.is_active, "is_faulted": self.is_faulted,
                         "fault": self.fault, "devices": {},
                         "idle_remaining": self.idle_remaining,
                         "idle_warn_seconds": self.idle_warn_seconds})
        return snapshot

    def set_mode(self, target):
        self.mode = target
        self.is_energized = target != "disabled"
        return target

    def step(self):
        self.steps += 1
        time.sleep(self.step_seconds)
        return "stepped"

    def nudge(self, direction):
        self.nudges.append(direction)
        return direction

    def extend_idle(self):
        self.extended += 1
        self.idle_remaining = 300.0
        return True

    def estop(self):
        self.is_estopped = True
        self.stop_confirmed = self.stop_confirms
        self.mode = "disabled"
        self.is_energized = False
        return self.stop_confirms

    def clear_estop(self, confirmed=False):
        self.is_estopped = False
        self.stop_confirmed = None

    def toggle_estop(self, confirmed=False):
        if self.is_estopped:
            self.clear_estop()
        else:
            self.estop()
        return self.is_estopped


@pytest.fixture
def mode_station():
    """Two probe-shaped models, in station order Stepper then DC."""
    controller = Controller()
    first, second = ModeProbe("Stepper Probe"), ModeProbe("DC Probe")
    controller.add("Stepper Probe", first, {})
    controller.add("DC Probe", second, {})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, controller, first, second
    finally:
        view.close()


#: The drawer shut, and a reader of the rail's idle lines.
_RAIL = r"""
  if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
    await page.click('#drawer-close');
    await sleep(300);
  }
  const idle = () => page.evaluate(() => Array.from(document.querySelectorAll('.idle-line'))
    .filter((l) => l.getClientRects().length)
    .map((l) => { const b = l.querySelector('button');
      return { text: l.querySelector('.idle-text').textContent,
               name: b && b.getAttribute('aria-label'), button: b && b.textContent,
               inRail: Boolean(l.closest('.rail')),
               belowDisc: l.getBoundingClientRect().top
                 >= document.getElementById('stop-ring').getBoundingClientRect().bottom,
               dialog: Boolean(l.closest('[role=dialog],[role=alertdialog]')) }; }));
  const tray = () => page.evaluate(() => document.getElementById('tray-latest').textContent.trim());
  const logText = () => page.evaluate(() => document.getElementById('event-log').textContent);
"""


@needs_browser
def test_n2_the_idle_countdown_is_one_line_per_probe_with_extend(mode_station, tmp_path):
    """N2 (brief-n-views N1): inside the warning window each probe gets ONE
    non-modal rail line under the disc, "<name> powers down in 42 s." with
    Extend ("Extend <name>"), in station order, counting from state; Extend
    runs `extend_idle` on that model and the line goes when state says so.
    Outside the window, or with nothing energized, there is no line; focus
    never moves. O13: the "Idle Timeout Soon" event is history in the log,
    never the tray's line."""
    view, controller, first, second = mode_station
    first.idle_remaining = 120.0             # energized, outside the window
    out = _browse(view, _RAIL + r"""
      await sleep(600);
      return { outside: await idle() };
    """, tmp_path)
    assert out["outside"] == [], out

    first.idle_remaining = 42.0
    second.idle_remaining = 30.3
    # Published while the page is up, so it is news, not history.
    timer = threading.Timer(2.5, lambda: events.warn(
        "Idle Timeout Soon", "Stepper Probe powers its motors down in 42 s unless it "
        "moves or you extend.", source="Stepper Probe"))
    timer.start()
    try:
        out = _browse(view, _RAIL + r"""
          const r = {};
          await page.focus('#log-toggle');
          await when(async () => (await idle()).length === 2);
          r.two = await idle();
          r.focus = await page.evaluate(() => document.activeElement.id);
          r.hit = await page.evaluate(""" + _STOP_HIT + r""");
          await when(async () => (await logText()).includes('Idle timeout soon'), 6000);
          await sleep(300);
          r.tray = await tray();
          r.log = await logText();
          await page.click('.idle-line button[aria-label="Extend Stepper Probe"]');
          await when(async () => (await idle()).length === 1);
          r.after = await idle();
          return r;
        """, tmp_path)
    finally:
        timer.cancel()
    two = out["two"]
    assert [line["text"] for line in two] == ["Stepper Probe powers down in 42\u00a0s.",
                                              "DC Probe powers down in 31\u00a0s."], two
    assert [line["name"] for line in two] == ["Extend Stepper Probe", "Extend DC Probe"], two
    assert all(line["button"] == "Extend" and line["inRail"] and line["belowDisc"]
               and not line["dialog"] for line in two), two
    assert out["focus"] == "log-toggle", "the countdown took focus"
    assert out["hit"], "the countdown covered the disc"
    assert "Idle timeout soon" in out["log"], out["log"]
    assert "Idle timeout soon" not in out["tray"], out["tray"]
    assert first.extended == 1 and second.extended == 0
    assert [line["text"] for line in out["after"]] == ["DC Probe powers down in 31\u00a0s."], out

    second.idle_remaining = None             # left its mode
    out = _browse(view, _RAIL + r"""
      await sleep(600);
      return { lines: await idle() };
    """, tmp_path)
    assert out["lines"] == [], out


@needs_browser
def test_n2_the_countdown_follows_the_polled_seconds(mode_station, tmp_path):
    """The number is the state's, re-read every poll; no local timer."""
    view, controller, first, second = mode_station
    first.idle_remaining = 42.0
    out = _browse(view, _RAIL + r"""
      await when(async () => (await idle()).length === 1);
      const a = (await idle())[0].text;
      await page.evaluate(() => fetch('/api/state'));
      return { a };
    """, tmp_path)
    assert out["a"] == "Stepper Probe powers down in 42\u00a0s.", out
    first.idle_remaining = 7.0
    out = _browse(view, _RAIL + r"""
      await when(async () => (await idle()).length === 1);
      return { b: (await idle())[0].text };
    """, tmp_path)
    assert out["b"] == "Stepper Probe powers down in 7\u00a0s.", out


#: What the close-tab guard does, and the rail's energized words and marks.
_ENERGIZED = r"""
  const guard = () => page.evaluate(() => {
    const e = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(e);
    const line = document.getElementById('energized-line');
    const marks = {};
    for (const link of document.querySelectorAll('#model-nav .model-link[data-model]')) {
      const m = link.querySelector('.nav-energized');
      const s = m && getComputedStyle(m);
      marks[link.dataset.model] = m && !m.hidden && m.getClientRects().length
        ? { words: m.textContent.trim(), title: m.title,
            background: s.backgroundColor, border: s.borderTopWidth,
            afterStopMark: Boolean(link.querySelector('.nav-mark'))
              && Boolean(link.querySelector('.nav-mark').compareDocumentPosition(m)
                         & Node.DOCUMENT_POSITION_FOLLOWING) } : null;
    }
    const swatch = document.createElement('span');
    swatch.style.color = getComputedStyle(document.documentElement).getPropertyValue('--signal').trim();
    document.body.appendChild(swatch);
    const signal = getComputedStyle(swatch).color;
    swatch.remove();
    return { prevented: e.defaultPrevented,
             line: line && !line.hidden && line.getClientRects().length ? line.textContent : '',
             lineRed: line ? getComputedStyle(line).color === signal : null,
             marks, signal };
  });
"""


@needs_browser
def test_n3_the_close_tab_guard_is_armed_exactly_while_something_is_energized(
        mode_station, tmp_path, monkeypatch):
    """N3: `beforeunload` is armed on `state.energized`, not `is_active`: a
    probe merely in a mode arms it, an active-but-not-energized station does
    not. While armed the rail says so in ink under the chord's hint, with the
    watchdog's seconds as served (here 12, to prove they are not the page's
    own 15). O6: an energized model carries a ring before its name in the
    rail, after the stop marks, not red, titled and read "energized"."""
    monkeypatch.setattr(WebView, "STOP_SECONDS", 12.0)
    view, controller, first, second = mode_station
    first.is_active = True                  # active, but nothing energized
    out = _browse(view, _RAIL + _ENERGIZED + r"""
      await sleep(600);
      const r = { idle: await guard() };
      await page.click('#model-nav [data-model="Stepper Probe"]');
      await sleep(300);
      await page.click('.card.is-opened .toggle');
      await when(async () => (await guard()).prevented);
      await sleep(300);
      r.energized = await guard();
      r.hintAbove = await page.evaluate(() => document.querySelector('.stop-hint').getBoundingClientRect().bottom
        <= document.getElementById('energized-line').getBoundingClientRect().top + 0.5);
      await page.click('.card.is-opened .toggle');
      await when(async () => !(await guard()).prevented);
      r.off = await guard();
      return r;
    """, tmp_path)
    assert out["idle"]["prevented"] is False and out["idle"]["line"] == "", out["idle"]
    on = out["energized"]
    assert on["prevented"] is True, on
    assert on["line"] == ("Devices are energized. Disable them before closing this tab; "
                          "the station stops them 12 s after the tab goes."), on
    assert on["lineRed"] is False and out["hintAbove"], on
    mark = on["marks"]["Stepper Probe"]
    assert mark and mark["words"] == "energized" and mark["title"] == "Energized", on
    assert mark["afterStopMark"] and mark["background"] != on["signal"], mark
    assert mark["border"] not in ("0px", ""), "the energized mark is a ring"
    assert on["marks"]["DC Probe"] is None, on
    assert out["off"]["prevented"] is False and out["off"]["line"] == "", out["off"]


#: A faulted entry, its rail mark and its mode toggle.
_FAULT_READ = r"""
  const fault = () => page.evaluate(() => {
    const card = Array.from(document.querySelectorAll('#cards .card'))
      .find((c) => c.querySelector('.card-title').textContent === 'Stepper Probe');
    const mark = card.querySelector('.unconfirmed-mark');
    const reason = card.querySelector('.fault-line');
    const toggle = card.querySelector('.toggle');
    const link = document.querySelector('#model-nav [data-model="Stepper Probe"] .nav-mark');
    const swatch = document.createElement('span');
    swatch.style.color = getComputedStyle(document.documentElement).getPropertyValue('--signal').trim();
    document.body.appendChild(swatch);
    const signal = getComputedStyle(swatch).color;
    swatch.remove();
    return { rule: getComputedStyle(card).borderTopColor === signal,
             mark: mark && !mark.hidden ? mark.textContent : '',
             reason: reason && !reason.hidden ? reason.textContent : '',
             toggle: { disabled: toggle.disabled, title: toggle.title },
             rail: link && !link.hidden ? { words: link.textContent, title: link.title,
                                            faulted: link.classList.contains('is-faulted'),
                                            red: getComputedStyle(link).backgroundColor === signal } : null };
  });
"""


@needs_browser
def test_o4_a_faulted_probe_is_marked_like_an_unconfirmed_stop(mode_station, tmp_path):
    """O4 (IMP8-2): a probe whose disable did not reach the board (FAULT)
    looked like a safely disabled one. Its entry gets the red head rule and
    "Disable failed. Treat as live." with the fault's reason in tier 1, the
    rail a signal mark, and its mode toggle is drawn disabled with "Faulted:
    clear the fault first". When the fault clears, all of it goes."""
    view, controller, first, second = mode_station
    reason = ("The disable did not reach the board, so the motors may still be "
              "powered. Treat it as live and check the connection.")
    first.mode, first.fault = "fault", reason
    out = _browse(view, _RAIL + _FAULT_READ + r"""
      await when(async () => (await fault()).mark !== '');
      await sleep(300);
      return fault();
    """, tmp_path)
    assert out["rule"] and out["mark"] == "Disable failed. Treat as live.", out
    assert out["reason"] == reason, out
    assert out["toggle"] == {"disabled": True, "title": "Faulted: clear the fault first"}, out
    rail = out["rail"]
    assert rail and rail["faulted"] and rail["red"] and rail["words"] == "faulted", out
    assert rail["title"] == "Disable failed: treat as live", rail

    first.mode, first.fault = "disabled", ""
    out = _browse(view, _RAIL + _FAULT_READ + r"""
      await sleep(700);
      return fault();
    """, tmp_path)
    assert not out["rule"] and out["mark"] == "" and out["reason"] == "", out
    assert out["toggle"]["disabled"] is False and out["rail"] is None, out


def test_o5_quit_stops_every_model_before_it_answers_and_names_the_unconfirmed(station):
    """O5 (IMP8-3): the server answered Quit before any stop, so the page
    said "Off" regardless. Now the answer carries `estop_all`'s results and
    the names that did not confirm; a second Quit is the same answer."""
    view, controller, probe = station
    probe.stop_confirms = False
    status, answer = _post(view, "/api/quit", {})
    assert status == 200 and answer["status"] == "ok", answer
    assert probe.is_estopped, "Quit answered before the stop ran"
    assert answer["stopped"] == {"Fake Probe": False}, answer
    assert answer["unconfirmed"] == ["Fake Probe"], answer
    assert _post(view, "/api/quit", {}) == (200, answer)


@needs_browser
def test_o5_after_quit_the_page_names_the_model_that_did_not_confirm(station, tmp_path):
    """O5: the end state says "Off" only for what confirmed. The model that
    did not is named on the rail in the signal colour, its entry keeps its
    red rule and "Stop not confirmed", and its switch does not read Off."""
    view, controller, probe = station
    probe.stop_confirms = False
    out = _browse(view, r"""
      await page.click('#quit-link');
      await until(() => !document.getElementById('confirm-modal').hidden);
      await page.click('#confirm-yes');
      await until(() => document.body.classList.contains('is-shut-down'));
      await sleep(400);
      return page.evaluate(() => {
        const card = Array.from(document.querySelectorAll('#cards .card'))
          .find((c) => c.querySelector('.card-title').textContent === 'Fake Probe');
        const lines = Array.from(document.querySelectorAll('.rail-alert-line'))
          .filter((l) => l.getClientRects().length).map((l) => l.textContent);
        return { link: document.getElementById('connection').textContent, lines,
                 unconfirmed: card.classList.contains('is-unconfirmed'),
                 mark: card.querySelector('.unconfirmed-mark').hidden ? '' : card.querySelector('.unconfirmed-mark').textContent,
                 switchWords: card.querySelector('.switch-words') ? card.querySelector('.switch-words').textContent : null,
                 face: document.querySelector('#full-stop .mushroom-face').textContent,
                 prevented: (() => { const e = new Event('beforeunload', { cancelable: true });
                                     window.dispatchEvent(e); return e.defaultPrevented; })() };
      });
    """, tmp_path)
    assert out["link"] == "The station has shut down. You can close this tab.", out
    assert out["lines"] == ["Fake Probe did not confirm its stop. Check it by hand."], out
    assert out["unconfirmed"] and out["mark"] == "Stop not confirmed. Treat as live.", out
    assert out["face"] == "Off" and out["prevented"] is False, out


@needs_browser
def test_o7_the_stop_and_the_clear_are_announced_and_the_dialogs_do_not_claim_modal(
        station, tmp_path):
    """O7 (A11Y-2/3): the headline and the rail line sit in a polite live
    region inside <main>; the stop and the clear are assertive
    announcements. The dialogs no longer say aria-modal="true" while the
    rail stays operable beside them."""
    view, controller, probe = station
    out = _browse(view, r"""
      const said = { polite: [], assertive: [] };
      await page.exposeFunction('noteSaid', (k, t) => said[k].push(t));
      const found = await page.evaluate(() => {
        const main = document.querySelector('main');
        const polite = document.getElementById('announce-polite');
        const loud = document.getElementById('announce-assertive');
        for (const [k, node] of [['polite', polite], ['assertive', loud]]) {
          if (node) new MutationObserver(() => { if (node.textContent) window.noteSaid(k, node.textContent); })
            .observe(node, { childList: true, characterData: true, subtree: true });
        }
        return { main: Boolean(main),
                 headlineInMain: Boolean(main && main.contains(document.getElementById('sheet-headline'))),
                 politeInMain: Boolean(main && polite && main.contains(polite)),
                 politeLive: polite && polite.getAttribute('aria-live'),
                 loudLive: loud && loud.getAttribute('aria-live'),
                 modal: Array.from(document.querySelectorAll('[aria-modal="true"]')).map((n) => n.id || n.className) };
      });
      await page.click('#full-stop');
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Clear');
      await sleep(500);
      await page.click('#full-stop');
      await until(() => !document.getElementById('confirm-modal').hidden);
      await page.click('#confirm-yes');
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Stop');
      await sleep(500);
      return { found, said };
    """, tmp_path)
    found = out["found"]
    assert found["main"] and found["headlineInMain"] and found["politeInMain"], found
    assert found["politeLive"] == "polite" and found["loudLive"] == "assertive", found
    assert found["modal"] == [], found
    said = out["said"]
    assert "Every model is stopped." in said["assertive"], said
    assert "The stop is cleared." in said["assertive"], said
    assert any("Every model is stopped." in t for t in said["polite"]), said


@needs_browser
def test_o3_a_disabled_reason_names_the_mode_the_model_is_in(mode_station, tmp_path):
    """O3 (IMP8-1): Step is `disabled_when=("manual", ...)`. In manual mode
    its reason is "In manual mode" (it said "Not in manual mode", the
    inverse); the speed entry, gated off in autonomous mode, says "In
    autonomous mode". The words come from the served table."""
    view, controller, first, second = mode_station
    first.mode = "manual"
    second.mode = "autonomous"
    out = _browse(view, _RAIL + r"""
      await sleep(700);
      return page.evaluate(() => {
        const card = (n) => Array.from(document.querySelectorAll('#cards .card'))
          .find((c) => c.querySelector('.card-title').textContent === n);
        const step = Array.from(card('Stepper Probe').querySelectorAll('button'))
          .find((b) => b.textContent === 'Step');
        const note = Array.from(card('Stepper Probe').querySelectorAll('.gate-note'))
          .filter((n) => !n.hidden).map((n) => n.textContent);
        const speed = card('DC Probe').querySelector('input[name="full_speed"]');
        return { step: step.title, disabled: step.disabled, note, speed: speed.title };
      });
    """, tmp_path)
    assert out["disabled"] and out["step"] == "In manual mode", out
    assert out["note"] == ["In manual mode"], out
    assert out["speed"] == "In autonomous mode", out


@needs_browser
def test_o10_a_command_in_flight_is_busy_and_a_second_press_is_swallowed(mode_station, tmp_path):
    """O10 (WDG8-2): two presses on Step 50 ms apart sent two Steps. The
    command is busy (aria-busy, .is-busy) until its answer, and a press
    meanwhile is ignored."""
    view, controller, first, second = mode_station
    first.step_seconds = 0.6
    out = _browse(view, _RAIL + r"""
      await sleep(400);
      const step = await page.evaluateHandle(() => Array.from(document.querySelectorAll('#cards .card'))
        .find((c) => c.querySelector('.card-title').textContent === 'Stepper Probe')
        .querySelector('.button.role-go'));
      await step.click();
      await sleep(50);
      const busy = await page.evaluate((b) => ({ aria: b.getAttribute('aria-busy'),
        cls: b.classList.contains('is-busy') }), step);
      await step.click();
      await sleep(1200);
      const after = await page.evaluate((b) => ({ aria: b.getAttribute('aria-busy'),
        cls: b.classList.contains('is-busy') }), step);
      return { busy, after };
    """, tmp_path)
    assert first.steps == 1, f"{first.steps} Steps were sent"
    assert out["busy"] == {"aria": "true", "cls": True}, out
    assert out["after"]["aria"] != "true" and not out["after"]["cls"], out


@needs_browser
def test_o10_the_wheel_does_not_change_a_focused_number_box(mode_station, tmp_path):
    """O10 (WDG8-3): the wheel over a focused number box changed it
    silently (400400 -> 400403)."""
    view, controller, first, second = mode_station
    out = _browse(view, _RAIL + r"""
      await sleep(400);
      const box = 'input[name="full_speed"]';
      await page.focus(box);
      const b = await page.evaluate((s) => { const r = document.querySelector(s).getBoundingClientRect();
        return [r.left + r.width / 2, r.top + r.height / 2]; }, box);
      await page.mouse.move(b[0], b[1]);
      const before = await page.evaluate((s) => document.querySelector(s).value, box);
      for (let i = 0; i < 3; i += 1) { await page.mouse.wheel({ deltaY: -100 }); await sleep(60); }
      const after = await page.evaluate((s) => document.querySelector(s).value, box);
      return { before, after };
    """, tmp_path)
    assert out["before"] == "400" and out["after"] == "400", out


@needs_browser
def test_o10_a_refusal_lands_at_its_field_and_marks_it(mode_station, tmp_path):
    """O10 (WDG8-4, A11Y-7): a refusal that names a field is put under that
    field - its well opened if it was shut - with aria-invalid and
    aria-describedby pointing at the sentence, and the field takes focus."""
    view, controller, first, second = mode_station
    out = _browse(view, _RAIL + r"""
      await page.click('#model-nav [data-model="Stepper Probe"]');
      await sleep(400);
      // The value arrives with no input or change event, as a paste that
      // never blurred would; the well is shut.
      await page.evaluate(() => { document.querySelector('.card.is-opened input[name="x_step"]').value = '0'; });
      await page.click('.card.is-opened .button.role-go');
      await until(() => { const s = document.querySelector('.card.is-opened .status'); return s && !s.hidden; });
      await sleep(300);
      return page.evaluate(() => {
        const card = document.querySelector('.card.is-opened');
        const box = card.querySelector('input[name="x_step"]');
        const status = card.querySelector('.status');
        const row = box.closest('.row');
        return { text: status.textContent, invalid: box.getAttribute('aria-invalid'),
                 describedBy: box.getAttribute('aria-describedby'), statusId: status.id,
                 underField: row.nextElementSibling === status,
                 wellOpen: !card.querySelector('.tier-well').hidden,
                 focused: document.activeElement === box };
      });
    """, tmp_path)
    assert out["text"] == "X step size must be at least 1", out
    assert out["invalid"] == "true" and out["describedBy"] == out["statusId"] != "", out
    assert out["underField"] and out["wellOpen"] and out["focused"], out


@needs_browser
def test_o10_the_region_can_be_typed_and_the_picker_says_it_is_loading(station, tmp_path):
    """O10 (WDG8-5): the capture region had a pointer-only picker. Four
    labelled number fields (in screen pixels) are the keyboard path, and a
    press on "Use this region" sets exactly what they say. While the grab
    loads, the dialog says so and is aria-busy."""
    view, controller, probe = station
    out = _browse(view, r"""
      const pick = await page.evaluateHandle(() => Array.from(document.querySelectorAll('.card button'))
        .find((b) => b.textContent.startsWith('Region')));
      await pick.click();
      const loading = await page.evaluate(() => ({
        busy: document.querySelector('#region-picker .dialog').getAttribute('aria-busy'),
        help: document.getElementById('region-help').textContent }));
      await until(() => document.querySelector('#region-picker .dialog').getAttribute('aria-busy') !== 'true');
      const fields = await page.evaluate(() => Array.from(document.querySelectorAll('#region-picker input'))
        .map((i) => ({ name: i.name, label: i.labels && i.labels[0] ? i.labels[0].textContent : i.getAttribute('aria-label') })));
      for (const [name, value] of [['x', '-900'], ['y', '20'], ['width', '300'], ['height', '200']]) {
        await page.click('#region-picker input[name="' + name + '"]', { clickCount: 3 });
        await page.keyboard.type(value);
      }
      await page.click('#region-use');
      await until(() => document.getElementById('region-picker').hidden);
      await sleep(300);
      return { loading, fields };
    """, tmp_path)
    assert out["loading"]["busy"] == "true", out
    assert out["loading"]["help"] == "Loading the station's screen…", out
    assert [f["name"] for f in out["fields"]] == ["x", "y", "width", "height"], out
    assert all(f["label"] for f in out["fields"]), out
    assert probe.region == {"left": -900, "top": 20, "width": 300, "height": 200}, probe.region


@needs_browser
def test_o12_browser_silent_leaves_the_tray_on_the_next_heartbeat(station, tmp_path):
    """O12 (PM8-3): "Browser silent … full stop at 15 s" stayed the tray's
    line after the tab came back. The next heartbeat that lands takes it
    back (the log keeps it)."""
    view, controller, probe = station
    timer = threading.Timer(2.5, lambda: events.warn(
        "Browser Silent", "No browser has checked in for 5.2s while devices are "
        "energized. FULL STOP at 15s.", source="Web"))
    timer.start()
    try:
        out = _browse(view, r"""
          // The tab went quiet: no heartbeat has landed for a while.
          await page.evaluate(() => { window.station.stopHeartbeat(); window.station.lastBeatOk = 0; });
          const tray = () => page.evaluate(() => document.getElementById('tray-latest').textContent);
          await when(async () => (await tray()).includes('Browser silent'), 6000);
          const r = { silent: await tray() };
          await page.evaluate(() => window.station.startHeartbeat());
          await when(async () => !(await tray()).includes('Browser silent'), 4000);
          r.back = await tray();
          r.log = await page.evaluate(() => document.getElementById('event-log').textContent);
          return r;
        """, tmp_path)
    finally:
        timer.cancel()
    assert "Browser silent" in out["silent"], out
    assert "Browser silent" not in out["back"], out
    assert "Browser silent" in out["log"], out


@needs_browser
def test_o14_a_released_slider_and_a_returned_entry_commit_at_once(tiered_station, tmp_path):
    """O14 (PM8-7): a drag or a typed speed waited for some later command.
    The slider commits on release and the entry on Enter (or leaving it),
    through `_commit` for that one field, as Tk and Qt do. A refused value
    is said at the field and the box goes back to what the station holds."""
    view, controller, probe = tiered_station
    out = _browse(view, _TIERED + r"""
      const box = '.card.is-opened input[name="speed"]';
      const retype = async (text) => {
        await page.$eval(box, (el) => { el.value = ''; });
        await page.focus(box);
        await page.keyboard.type(text);
      };
      await retype('321');
      await page.keyboard.press('Enter');
      await sleep(600);
      const range = '.card.is-opened .slider-range';
      await page.focus(range);
      await page.keyboard.press('ArrowRight');
      await page.evaluate((s) => document.querySelector(s).dispatchEvent(new Event('change', { bubbles: true })), range);
      await sleep(600);
      const afterSlider = await page.evaluate((s) => document.querySelector(s).value, box);
      await retype('9999');
      await page.keyboard.press('Enter');
      await sleep(700);
      return page.evaluate((s, afterSlider) => {
        const input = document.querySelector(s);
        const status = document.querySelector('.card.is-opened .status');
        return { afterSlider, value: input.value, refusal: status.hidden ? '' : status.textContent,
                 invalid: input.getAttribute('aria-invalid') };
      }, box, afterSlider);
    """, tmp_path)
    assert probe.sent == [], "a commit is not a Go"
    assert out["afterSlider"] == "331" and probe.speed == 331, (out, probe.speed)
    assert out["refusal"] == "Manual speed must be at most 5000", out
    assert out["value"] == "331" and out["invalid"] == "true", out


@needs_browser
def test_o15_the_device_page_pins_its_head_and_tier_one(sim_station, tmp_path):
    """O15 (ARCH parity with L5): on a device page the head and the tier-1
    body stay in view while the details under them scroll."""
    view, controller = sim_station
    out = _browse(view, r"""
      await page.setViewport({ width: 1400, height: 600 });
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(300);
      }
      await page.click('#model-nav [data-model="Stepper Probe"]');
      await sleep(300);
      await page.click('.card.is-opened .disclosure[data-tier="2"]');
      await page.click('.card.is-opened .disclosure[data-tier="3"]');
      await sleep(300);
      const top = () => page.evaluate(() => {
        const c = document.querySelector('.card.is-opened');
        return { head: c.querySelector('.card-head').getBoundingClientRect().top,
                 body: c.querySelector('.card-body').getBoundingClientRect().top,
                 well: c.querySelector('.tier-well').getBoundingClientRect().top,
                 scroll: window.scrollY,
                 room: document.documentElement.scrollHeight - window.innerHeight };
      });
      const before = await top();
      await page.evaluate(() => window.scrollBy(0, 160));
      await sleep(300);
      return { before, after: await top(),
               pinned: await page.evaluate(() => document.querySelector('.card.is-opened').classList.contains('is-pinned')) };
    """, tmp_path)
    before, after = out["before"], out["after"]
    assert before["room"] > 100, f"nothing to scroll: {before}"
    assert after["scroll"] > 0, after
    # Pinned: the head is still at the top of the view and the tier-1 body
    # still right under it, while the page moved 400 px.
    assert -0.5 <= after["head"] <= before["head"] + 0.5, out
    assert abs((after["body"] - after["head"]) - (before["body"] - before["head"])) < 1, out
    assert after["well"] < before["well"] - 100, "the details did not scroll under it"
    assert out["pinned"], "the entry was not pinned"


@needs_browser
def test_o16_one_word_per_stop_and_marks_that_differ_by_shape(two_probes, tmp_path):
    """O16 (PM8-8, A11Y-6, WDG8-7/9): the per-model switch reads "Stop this
    model" and, latched, "Stopped"; the rail's stopped mark is a square and
    the did-not-confirm mark carries a "!" as well as its colour; the
    refusal line's mark is not a box; a log window shows a focus ring when
    it opens."""
    view, controller, first, second = two_probes
    second.stop_confirms = False
    out = _browse(view, _STOP_READ + r"""
      await page.click('#model-nav [data-model="Fake Probe"]');
      await sleep(300);
      const words = () => page.evaluate(() => document.querySelector('.card.is-opened .switch-words').textContent);
      const r = { live: await words() };
      const opener = await page.evaluateHandle(() => Array.from(document.querySelectorAll('.card.is-opened button'))
        .find((b) => b.textContent.startsWith('Gamepad log')));
      await opener.focus();
      await page.keyboard.press('Enter');
      await sleep(300);
      r.ring = await page.evaluate(() => { const f = document.activeElement;
        const s = getComputedStyle(f);
        return { inLog: Boolean(f.closest('.log-window')), outline: s.outlineStyle, width: s.outlineWidth }; });
      await page.keyboard.press('Escape');
      await page.click('#full-stop');
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Clear');
      await sleep(500);
      await ackAll();
      r.latched = await words();
      r.marks = await page.evaluate(() => {
        const read = (n) => { const m = document.querySelector('#model-nav [data-model="' + n + '"] .nav-mark');
          const b = getComputedStyle(m, '::before');
          return { glyph: b.content, cls: m.className }; };
        return { stopped: read('Fake Probe'), unconfirmed: read('Other Probe') };
      });
      r.refusalMark = await page.evaluate(() => {
        const s = document.createElement('p'); s.className = 'status';
        document.querySelector('.card.is-opened').appendChild(s);
        const b = getComputedStyle(s, '::before');
        const out = { border: b.borderTopWidth, clip: b.clipPath, mask: b.maskImage || b.webkitMaskImage };
        s.remove();
        return out;
      });
      return r;
    """, tmp_path)
    assert out["live"] == "Stop this model" and out["latched"] == "Stopped", out
    assert out["ring"]["inLog"] and out["ring"]["outline"] != "none", out["ring"]
    marks = out["marks"]
    assert marks["stopped"]["glyph"] in ("none", "normal", '""'), marks
    assert marks["unconfirmed"]["glyph"] == '"!"', marks
    mark = out["refusalMark"]
    # Signature (rule 6): the refusal's mark is the station's warning glyph,
    # a mask of the served --icon-warning, where it was a clip-path triangle;
    # still not a box.
    assert mark["border"] in ("0px", "") and "data:image/svg+xml" in mark["mask"], mark


@needs_browser
def test_o16_under_640_the_chord_hint_and_every_stop_mark_stay_visible(sim_station, tmp_path):
    """O16 (WDG8-10): under 40rem the rail hid "Stop: Ctrl+." and the list
    scrolled sideways, taking the stop marks of the last models off-edge."""
    view, controller = sim_station
    controller.estop_all()
    out = _browse(view, r"""
      await page.setViewport({ width: 620, height: 900 });
      await sleep(700);
      return page.evaluate(() => {
        const hint = document.querySelector('.stop-hint');
        const w = window.innerWidth;
        const marks = Array.from(document.querySelectorAll('#model-nav .nav-mark')).map((m) => {
          const r = m.getBoundingClientRect();
          return !m.hidden && r.width > 0 && r.left >= 0 && r.right <= w; });
        return { hint: Boolean(hint.getClientRects().length) && getComputedStyle(hint).visibility !== 'hidden'
                 && getComputedStyle(hint).display !== 'none',
                 marks, sideways: document.documentElement.scrollWidth > w };
      });
    """, tmp_path)
    assert out["hint"], out
    # Seven models (Tier S), six links: Red Percent's stop is folded into
    # the Transfer Map's link (D, 2026-09-28).
    assert len(out["marks"]) == 6 and all(out["marks"]), out
    assert not out["sideways"], out


def test_theme_json_serves_the_event_titles_the_page_keys_on(station):
    """ARCH-4: the titles the page matches (the unconfirmed stop, the idle
    warning, the watchdog's warning) are core's constants, served."""
    view, _, _ = station
    status, body = _get(view, "/api/theme.json")
    assert status == 200
    assert body["event_titles"] == {"stop_not_confirmed": events.STOP_NOT_CONFIRMED,
                                    "idle_timeout_soon": events.IDLE_TIMEOUT_SOON,
                                    "browser_silent": events.BROWSER_SILENT}


@needs_browser
def test_con1_a_buttons_own_args_travel_before_the_press_args(mode_station, tmp_path):
    """CON-1 (contract audit, S1): the page dropped a button's schema `args`,
    so the real Rotator's "Move -" moved +5. `views.base` sends
    `element.args + args`; so does the page now."""
    view, controller, first, second = mode_station
    _browse(view, _RAIL + r"""
      await sleep(400);
      const press = (words) => page.evaluate((w) => Array.from(document.querySelectorAll('#cards .card'))
        .find((c) => c.querySelector('.card-title').textContent === 'Stepper Probe')
        .querySelectorAll('button').forEach((b) => { if (b.textContent === w) b.click(); }), words);
      await press('Nudge -');
      await sleep(500);
      await press('Nudge +');
      await sleep(500);
      return true;
    """, tmp_path)
    assert first.nudges == [-1, 1], first.nudges


# --------------------------------------------------------------------------
# Signature (owner ruling 2026-09-27, handoff/tactile3-Signature.md): the key
# family, the lamp slot, the tripped flag, reduced motion. Drawn and read in
# a real browser.
# --------------------------------------------------------------------------
#: Colours by token name, for comparing computed styles.
_SWATCH = r"""
  const swatch = (name) => page.evaluate((n) => {
    const s = document.createElement('span');
    s.style.color = getComputedStyle(document.documentElement).getPropertyValue(n).trim();
    document.body.appendChild(s); const c = getComputedStyle(s).color; s.remove(); return c; }, name);
"""


@needs_browser
def test_signature_a_latching_key_folds_its_lip_and_lights_its_lamp(mode_station, tmp_path):
    """Spec "Controls": the latching mode key off is a neutral key (4 px
    lip) with a hollow lamp slot; on ([aria-pressed="true"]) it is the ink
    key DOWN - the lip folds to 1 px, the face drops 3 px (a top border) -
    with its slot lit CAP; its height never changes. A neutral key pressed
    (:active, held with the mouse) folds the same way."""
    view, controller, first, second = mode_station
    out = _browse(view, _RAIL + _SWATCH + r"""
      await page.click('#model-nav [data-model="Stepper Probe"]');
      await sleep(300);
      const read = () => page.evaluate(() => {
        const b = document.querySelector('.card.is-opened .toggle');
        const s = getComputedStyle(b);
        const lamp = getComputedStyle(b.querySelector('.toggle-lamp'));
        return { pressed: b.getAttribute('aria-pressed'), lip: s.borderBottomWidth,
                 drop: s.borderTopWidth, h: b.getBoundingClientRect().height,
                 face: s.backgroundColor, lamp: lamp.backgroundColor,
                 lampW: lamp.width, lampH: lamp.height };
      });
      const r = { off: await read() };
      await page.click('.card.is-opened .toggle');
      await until(() => document.querySelector('.card.is-opened .toggle').getAttribute('aria-pressed') === 'true');
      await page.mouse.move(5, 5);
      await sleep(400);
      r.on = await read();
      r.ink = await swatch('--text'); r.cap = await swatch('--cap'); r.lampOff = await swatch('--lamp-off');
      // A neutral key held down: find one that is enabled and not a toggle.
      const box = await page.evaluate(() => {
        const b = document.getElementById('log-toggle');
        const q = b.getBoundingClientRect();
        return [q.left + q.width / 2, q.top + q.height / 2, q.height,
                getComputedStyle(b).borderBottomWidth];
      });
      await page.mouse.move(box[0], box[1]);
      await page.mouse.down();
      await sleep(400);
      r.held = await page.evaluate(() => { const b = document.getElementById('log-toggle');
        const s = getComputedStyle(b);
        return { lip: s.borderBottomWidth, drop: s.borderTopWidth, h: b.getBoundingClientRect().height }; });
      await page.mouse.up();
      r.up = { h: box[2], lip: box[3] };
      return r;
    """, tmp_path)
    off, on = out["off"], out["on"]
    assert off["pressed"] == "false" and off["lip"] == "4px" and off["drop"] == "0px", out
    assert off["lamp"] == out["lampOff"] and (off["lampW"], off["lampH"]) == ("6px", "13px"), out
    assert on["pressed"] == "true" and on["lip"] == "1px" and on["drop"] == "3px", out
    assert on["face"] == out["ink"] and on["lamp"] == out["cap"], out
    assert on["h"] == off["h"], "the key's height changed when it went down"
    assert out["up"]["lip"] == "4px", out
    assert out["held"] == {"lip": "1px", "drop": "3px", "h": out["up"]["h"]}, out


@needs_browser
def test_signature_the_rail_lamp_marks_the_shown_page_and_an_unconfirmed_model(station, tmp_path):
    """Spec "Rail" and rule 5: every rail item keeps a lamp slot's room; the
    shown page's lamp is lit ink (the others hidden); a model whose stop did
    not confirm has its lamp lit SIGNAL - never trace."""
    view, controller, probe = station
    probe.stop_confirms = False
    out = _browse(view, _SWATCH + r"""
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(300);
      }
      const lamps = () => page.evaluate(() => Array.from(document.querySelectorAll('#model-nav .model-link'))
        .map((l) => { const b = getComputedStyle(l, '::before'); const m = l.querySelector('.nav-mark');
          const shown = m && !m.hidden && m.getClientRects().length;
          return { name: l.textContent.replace(/stopped|did not confirm|energized/g, '').trim(),
                   lamp: b.visibility === 'visible' ? b.backgroundColor : '',
                   w: b.width, mark: shown ? getComputedStyle(m).backgroundColor : '' }; }));
      const r = { idle: await lamps() };
      await page.click('#full-stop');
      await until(() => document.querySelector('.card.is-unconfirmed'));
      await sleep(300);
      r.unconfirmed = await lamps();
      r.ink = await swatch('--lamp-on'); r.signal = await swatch('--lamp-unconfirmed');
      r.trace = await swatch('--trace');
      return r;
    """, tmp_path)
    overview, model = out["idle"]
    assert overview["name"] == "Overview" and overview["lamp"] == out["ink"], out
    assert model["lamp"] == "" and model["w"] == "6px" and model["mark"] == "", out
    model = out["unconfirmed"][1]
    assert model["mark"] == out["signal"], out
    assert out["trace"] not in {x["lamp"] for x in out["unconfirmed"]} | {x["mark"] for x in out["unconfirmed"]}


@needs_browser
def test_signature_the_flag_drops_once_per_unconfirmed_episode(station, tmp_path):
    """Spec "Unconfirmed" and "Motion": the tripped-flag window drops into
    its frame once, when the mark appears - not again when the entry is
    shown again on another page (display none -> shown restarts a CSS
    animation, which is why the client keys it on .is-dropping) - and once
    more for the next episode, after a clear."""
    view, controller, probe = station
    probe.stop_confirms = False
    out = _browse(view, r"""
      if (await page.evaluate(() => document.getElementById('setup-drawer').classList.contains('open'))) {
        await page.click('#drawer-close'); await sleep(300);
      }
      await page.evaluate(() => { window.drops = 0;
        document.addEventListener('animationstart', (e) => {
          if (e.animationName === 'flag-drop') window.drops += 1; }, true); });
      const drops = () => page.evaluate(() => window.drops);
      const r = {};
      await page.click('#full-stop');
      await until(() => document.querySelector('.card.is-unconfirmed'));
      await sleep(600);
      r.first = await drops();
      r.flag = await page.evaluate(() => { const f = document.querySelector('.card.is-unconfirmed .flag-window');
        const b = f.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height),
          f.classList.contains('is-dropping')]; });
      await page.click('#model-nav [data-model="Fake Probe"]');
      await sleep(400);
      await page.click('#model-nav .overview-link');
      await sleep(400);
      await page.click('#model-nav [data-model="Fake Probe"]');
      await sleep(600);
      r.reshown = await drops();
      await api('/api/clear_estop_all', { confirmed: true });
      await until(() => !document.querySelector('.card.is-unconfirmed'));
      await sleep(300);
      await page.click('#full-stop');
      await until(() => document.querySelector('.card.is-unconfirmed'));
      await sleep(600);
      r.second = await drops();
      return r;
    """, tmp_path)
    assert out["first"] == 1 and out["flag"] == [30, 20, False], out
    assert out["reshown"] == 1, out
    assert out["second"] == 2, out


@needs_browser
def test_signature_reduced_motion_zeroes_every_duration_and_keeps_the_states(tiered_station, tmp_path):
    """Spec "Motion": with prefers-reduced-motion every transition and
    animation is 0 s, and the latched stop is still latched - the collar
    red, the key down - because those are states, not motion."""
    view, controller, probe = tiered_station
    out = _browse(view, r"""
      await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
      await sleep(200);
      await page.click('#full-stop');
      await until(() => document.querySelector('#full-stop .mushroom-face').textContent === 'Clear');
      await page.mouse.move(5, 5);
      return page.evaluate(() => {
        const moving = [];
        for (const el of document.querySelectorAll('body *')) {
          for (const pseudo of [null, '::before', '::after']) {
            const s = getComputedStyle(el, pseudo);
            const d = (s.transitionDuration + ',' + s.animationDuration).split(',')
              .map((x) => parseFloat(x)).filter((x) => x > 0);
            if (d.length) moving.push(el.tagName + '.' + el.className + (pseudo || ''));
          }
        }
        const ring = getComputedStyle(document.getElementById('stop-ring'));
        const sig = document.createElement('span');
        sig.style.color = getComputedStyle(document.documentElement).getPropertyValue('--signal').trim();
        document.body.appendChild(sig);
        return { moving: moving.slice(0, 10),
                 collarRed: ring.borderTopColor === getComputedStyle(sig).color,
                 down: document.getElementById('full-stop').classList.contains('is-latched') };
      });
    """, tmp_path)
    assert out["moving"] == [], out
    assert out["collarRed"] and out["down"], out


# --------------------------------------------------------------------------
# rb-restart: the acknowledgement's action (R1), the restart (R4), the ack
# log (R7)
# --------------------------------------------------------------------------
import views.web.server as web_server


class RestartSetup(FakeSetup):
    """FakeSetup with the two update commands the prompts name."""

    def __init__(self):
        super().__init__()
        self.restarts, self.applies = [], []

    @property
    def schema(self):
        base = FakeSetup.schema.fget(self)
        base["sections"].insert(0, sch.section(
            "Update",
            sch.button("Update now", "apply_update", role="go"),
            sch.button("Restart", "restart_station"),
            layout="row"))
        return base

    def apply_update(self, confirmed=False):
        if not confirmed:
            raise NeedsConfirm("Update the station now?", "apply_update")
        self.applies.append(confirmed)
        return True

    def restart_station(self, confirmed=False):
        if not confirmed:
            raise NeedsConfirm("Restart the station now?", "restart_station")
        self.restarts.append(confirmed)
        return True


def _restart_station(tmp_path, port=0):
    controller = Controller()
    probe = FakeProbe(root=str(tmp_path))
    controller.add("Fake Probe", probe, {"root": str(tmp_path)})
    setup = RestartSetup()
    view = WebView(controller, setup, port=port, open_browser=False)
    assert view.open()
    return view, setup


@pytest.fixture
def restartable(tmp_path):
    view, setup = _restart_station(tmp_path)
    try:
        yield view, setup
    finally:
        view.close()


RESTART_ACTION = ("Restart now", "__setup__", "restart_station", (True,))
UPDATE_ACTION = ("Update now", "__setup__", "apply_update")


def test_state_says_which_station_run_answers(station):
    view, _, _ = station
    first = _get(view, "/api/state")[1]["boot"]
    assert first == web_server.BOOT_ID and first == _get(view, "/api/state")[1]["boot"]


def test_the_setup_name_is_the_events_one():
    assert SETUP_NAME == events.SETUP_PANEL == "__setup__"


def test_the_event_feed_carries_the_action(station):
    view, _, _ = station
    event = events.warn("Restart Needed", "Updated to def5678.", ack=True,
                        source="Setup", action=RESTART_ACTION)
    feed = _get(view, f"/api/events?since={event.id - 1}")[1]["events"]
    [sent] = [e for e in feed if e["id"] == event.id]
    assert sent["action"] == {"label": "Restart now", "name": "__setup__",
                              "command": "restart_station", "args": [True]}


def test_r7_an_acknowledgement_is_logged_at_debug(station, monkeypatch):
    view, _, _ = station
    said = []
    real = events.debug
    monkeypatch.setattr(events, "debug", lambda title, message, **kw: (
        said.append((title, message)), real(title, message, **kw)))
    event = events.warn("Idle Timeout", "idle 300 s", ack=True, source="Probe")
    status, answer = _post(view, "/api/ack", {"id": event.id, "answer": "later"})
    assert status == 200 and answer == {"status": "ok"}
    assert ("Acknowledged", f"event {event.id} (warning/Idle Timeout): later") in said
    # An unknown answer is logged as plain acknowledgement; an id is required.
    _post(view, "/api/ack", {"id": event.id, "answer": "<script>"})
    assert [m for t, m in said if t == "Acknowledged"][-1].endswith(": understood")
    assert _post(view, "/api/ack", {"id": "7"})[0] == 400
    assert _post(view, "/api/ack", {"id": True})[0] == 400
    assert _post(view, "/api/ack", {"id": 10 ** 9})[0] == 200


def test_r7_the_ack_route_keeps_the_local_only_rules(station):
    view, _, _ = station
    status, _ = _post(view, "/api/ack", {"id": 1},
                      headers={"Origin": "http://evil.example"})
    assert status == 403


def test_r4_a_new_station_is_idle_until_a_page_checks_in(tmp_path):
    """The restarted server has no client yet: its watchdog never stops
    anything before the first heartbeat, energized or not."""
    class Energized(Controller):
        is_energized = True
        stops = 0

        def estop_all(self):
            self.stops += 1
            return {}

    controller = Energized()
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    try:
        view._clock = lambda: 10 ** 6
        view._check_heartbeat()
        assert view.heartbeat_age is None and controller.stops == 0
    finally:
        view.close()


#: What the acknowledgement dialog's keys say, and the connection line.
_ACK_KEYS = r"""() => ({
  open: !document.getElementById('ack-modal').hidden,
  keys: Array.from(document.querySelectorAll('#ack-modal button')).map((b) => b.textContent.trim()),
  focused: document.activeElement && document.activeElement.id,
  link: document.getElementById('connection').textContent,
})"""


@needs_browser
def test_restart_an_action_notice_has_two_keys_and_return_runs_it(
        restartable, tmp_path):
    view, setup = restartable
    events.clear()

    def publish():
        time.sleep(1.5)
        events.warn("Restart Needed", "Updated to def5678. Restart the station "
                    "to run it.", source="Setup", ack=True, action=RESTART_ACTION)
    threading.Thread(target=publish, daemon=True).start()
    out = _browse(view, r"""
      const keys = () => page.evaluate(%(keys)s);
      await until(() => !document.getElementById('ack-modal').hidden, 10000);
      const shown = await keys();
      await page.keyboard.press('Enter');
      await sleep(800);
      return { shown, after: await keys() };
    """ % {"keys": _ACK_KEYS}, tmp_path)
    assert out["shown"]["keys"] == ["Restart now", "Later"], out
    assert out["shown"]["focused"] == "ack-ok"
    assert setup.restarts == [True], "Return runs the action, confirmed"
    assert out["after"]["open"] is False
    assert out["after"]["link"] == "Restarting the station…", out


@needs_browser
def test_restart_later_and_escape_run_nothing_and_a_plain_notice_keeps_one_key(
        restartable, tmp_path):
    view, setup = restartable
    events.clear()

    def publish():
        time.sleep(1.5)
        events.warn("Update Ready", "2 new commits are ready: x. Update now, "
                    "then restart the station.", source="Setup", ack=True,
                    action=UPDATE_ACTION)
        events.warn("Restart Needed", "Updated to def5678.", source="Setup",
                    ack=True, action=RESTART_ACTION)
        events.warn("Idle Timeout", "idle 300 s", source="Fake Probe", ack=True)
    threading.Thread(target=publish, daemon=True).start()
    out = _browse(view, r"""
      const keys = () => page.evaluate(%(keys)s);
      await until(() => document.getElementById('ack-count').textContent === '2 more waiting', 10000);
      const first = await keys();
      await page.click('#ack-later');
      await sleep(300);
      const second = await keys();
      await page.keyboard.press('Escape');
      await sleep(300);
      const third = await keys();
      await page.click('#ack-ok');
      await sleep(300);
      return { first, second, third, done: await keys() };
    """ % {"keys": _ACK_KEYS}, tmp_path)
    assert out["first"]["keys"] == ["Update now", "Later"]
    assert out["second"]["keys"] == ["Restart now", "Later"]
    assert out["third"]["keys"] == ["Understood"], "Later leaves with its notice"
    assert out["done"]["open"] is False
    assert setup.applies == [] and setup.restarts == []


@needs_browser
def test_restart_an_action_that_asks_asks_on_the_page(restartable, tmp_path):
    view, setup = restartable
    events.clear()

    def publish():
        time.sleep(1.5)
        events.warn("Update Ready", "2 new commits are ready: x.", source="Setup",
                    ack=True, action=UPDATE_ACTION)
    threading.Thread(target=publish, daemon=True).start()
    out = _browse(view, r"""
      await until(() => !document.getElementById('ack-modal').hidden, 10000);
      await page.click('#ack-ok');
      await until(() => !document.getElementById('confirm-modal').hidden, 5000);
      const question = await page.evaluate(() => document.getElementById('confirm-text').textContent);
      await page.click('#confirm-yes');
      await sleep(600);
      return { question };
    """, tmp_path)
    assert out["question"] == "Update the station now?"
    assert setup.applies == [True]


@needs_browser
def test_r4_the_page_waits_for_the_restarted_station_and_reloads(tmp_path):
    """The reload path against a real server stopped and started again on
    the same port: the page says it is restarting, stops beating, and
    reloads itself once the new station (another boot) answers."""
    view, setup = _restart_station(tmp_path)
    port, boot = view.port, web_server.BOOT_ID
    state = {"new": None}

    def restart_by_hand():
        deadline = time.monotonic() + 30
        while not setup.restarts and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
        view.close()                         # the old station is gone
        time.sleep(3.0)                      # nothing listens for a while
        web_server.BOOT_ID = boot + "-new"   # a new process, as execv makes one
        state["new"] = _restart_station(tmp_path, port=port)
    thread = threading.Thread(target=restart_by_hand, daemon=True)
    thread.start()
    try:
        out = _browse(view, r"""
          await page.evaluate(() => { window.beforeRestart = true; });
          const beats = [];
          page.on('request', (r) => { if (r.url().includes('/api/heartbeat')) beats.push(Date.now()); });
          await page.evaluate(() => document.getElementById('setup-link').click());
          await sleep(300);
          const pressed = Date.now();
          // Press Restart on the Setup card and answer its question.
          const restart = await page.$$eval('button', (bs) => {
            const b = bs.find((x) => x.textContent.trim() === 'Restart');
            if (b) b.click();
            return Boolean(b);
          });
          await until(() => !document.getElementById('confirm-modal').hidden, 5000);
          await page.click('#confirm-yes');
          await until(() => document.getElementById('connection').textContent.startsWith('Restarting'), 5000);
          const waiting = await page.evaluate(() => document.getElementById('connection').textContent);
          const reloaded = await when(async () => {
            try { return await page.evaluate(() => window.beforeRestart === undefined); }
            catch (e) { return false; }
          }, 20000);
          await sleep(1500);
          const after = await page.evaluate(() => ({
            link: document.getElementById('connection').textContent,
            cards: document.querySelectorAll('.card').length }));
          const quietBeats = beats.filter((t) => t > pressed + 1000 && t < pressed + 3500).length;
          return { restart, waiting, reloaded, after, quietBeats };
        """, tmp_path)
    finally:
        thread.join(40)
        web_server.BOOT_ID = boot
        if state["new"] is not None:
            state["new"][0].close()
    assert out["restart"] is True
    assert setup.restarts == [True]
    assert out["waiting"] == "Restarting the station…"
    assert out["reloaded"] is True, "the page did not reload for the new station"
    assert out["after"]["link"] == "" and out["after"]["cards"] >= 1, out
    assert out["quietBeats"] == 0, "the page kept beating while it waited"
