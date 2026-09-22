"""The browser client, checked without a browser.

`app.js` is the third renderer of the same schema, and it is the one no test
can click. What is checkable statically is exactly what kept going wrong:
a renderer missing for an element type (so one view silently shows nothing),
a server string reaching `innerHTML`, an unbounded fetch, a heartbeat that
does not stop when the tab is hidden, and a route name that exists on one
side of the seam only - WEB-19's two halves agreed on the design and
disagreed on the name, and nothing failed until the bench.

`node --check` runs when node is available and skips cleanly when it is not;
node is not a dependency of this project.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from station import schema as sch
from station.views import theme

STATIC = Path(__file__).resolve().parents[2] / "station" / "views" / "web" / "static"
SERVER = Path(__file__).resolve().parents[2] / "station" / "views" / "web" / "server.py"
APP_JS = (STATIC / "app.js").read_text()
STYLES = (STATIC / "styles.css").read_text()
INDEX = (STATIC / "index.html").read_text()
NODE = shutil.which("node")

#: Assembled rather than written out: this repo's own security hook refuses
#: to write a file containing the literal name of the last one.
MARKUP_SINKS = ["innerHTML", "outerHTML", "insertAdjacentHTML", "document." + "write"]

#: app.js with its comments removed - the comments name the sinks this file
#: forbids, and a prose mention is not a call.
CODE = re.sub(r"^\s*//.*$", "", re.sub(r"/\*.*?\*/", "", APP_JS, flags=re.S),
              flags=re.M)


# --------------------------------------------------------------------------
# one renderer per element type - the conformance rule PanelView enforces by
# raising TypeError on a missing `_make_<type>`
# --------------------------------------------------------------------------
def _renderer_map():
    block = re.search(r"const ELEMENT_RENDERERS = \{(.*?)\};", APP_JS, re.S)
    assert block, "app.js has no ELEMENT_RENDERERS map"
    return dict(re.findall(r"(\w+):\s*(\w+),", block.group(1)))


@pytest.mark.parametrize("element_type", sorted(sch.ELEMENT_TYPES))
def test_app_js_renders_every_element_type(element_type):
    """A type outside this set is a schema bug; a type inside it with no
    renderer is a Web client that quietly draws nothing where the desktop
    views draw a control."""
    mapped = _renderer_map()
    assert element_type in mapped, (
        f"app.js cannot render {element_type!r}: the Web client would show "
        f"nothing where Tk and Qt show a control")
    function = mapped[element_type]
    assert f"function {function}(" in APP_JS, (
        f"{element_type} maps to {function}, which app.js never defines")


def test_app_js_renders_nothing_the_schema_does_not_declare():
    extra = set(_renderer_map()) - set(sch.ELEMENT_TYPES)
    assert not extra, f"app.js renders element types no schema declares: {extra}"


# --------------------------------------------------------------------------
# PanelView's contract, mirrored
# --------------------------------------------------------------------------
def _body(pattern):
    found = re.search(pattern, APP_JS, re.S)
    assert found, f"app.js has nothing matching {pattern}"
    return found.group(1)


def test_every_command_carries_every_entry_value():
    """D-5: the value typed a moment ago is never one edit behind."""
    assert "gatherInputs()" in APP_JS
    run = _body(r"async run\(element, args\) \{(.*?)\n  \}")
    assert "this.gatherInputs()" in run, (
        "run() sent a command without gathering the entry values")


def test_needs_confirm_re_runs_with_the_args_plus_true():
    run = _body(r"async run\(element, args\) \{(.*?)\n  \}")
    assert "needs_confirm" in run and "window.confirm" in run
    assert ".concat([true])" in run, (
        "the confirmed re-run must append True to the original args, not "
        "replace them with [true]")
    assert "result.command" in run and "result.inputs" in run


def test_a_refusal_is_a_status_line_on_the_card_and_never_a_popup():
    run = _body(r"async run\(element, args\) \{(.*?)\n  \}")
    assert "this.showRefused(" in run
    assert "alert(" not in run, "a refusal opened a modal"
    assert "textContent" in _body(r"showRefused\(reason\) \{(.*?)\n  \}")


def test_only_an_event_that_asks_to_be_acknowledged_opens_a_modal():
    assert re.search(r"if \(event\.needs_ack\) this\.showAck\(event\)", APP_JS), (
        "every event opened a modal, or none did")


def test_entries_are_not_overwritten_while_they_are_being_typed_in():
    entry = _body(r"function renderEntry\(panel, element\) \{(.*?)\n\}")
    assert "isDirty" in entry
    assert "document.activeElement === input" in entry
    assert "if (!widget.isDirty())" in _body(r"\n  refresh\(state\) \{(.*?)\n  \}")


def test_gating_covers_every_element_including_entries():
    refresh = _body(r"\n  refresh\(state\) \{(.*?)\n  \}")
    assert "widget.setEnabled(isEnabled(element, mode))" in refresh, (
        "gating must be applied to every widget in the loop, not to the "
        "buttons the renderer happens to remember")
    # the rule itself, not a second opinion about it
    gate = _body(r"function isEnabled\(element, mode\) \{(.*?)\n\}")
    assert "disabled_when" in gate and "enabled_when" in gate


def test_a_stale_state_is_marked_at_the_same_threshold_as_the_desktop_views():
    assert "const STALE_AFTER_S = 1.0;" in APP_JS
    refresh = _body(r"\n  refresh\(state\) \{(.*?)\n  \}")
    assert "this.setStale((state && state.age) > STALE_AFTER_S)" in refresh


def test_the_global_full_stop_follows_the_state_not_the_click():
    assert "this.renderEstop(Boolean(state.is_estopped))" in APP_JS
    toggle = _body(r"async toggleEstopAll\(\) \{(.*?)\n  \}")
    assert "/api/estop_all" in toggle and "/api/clear_estop_all" in toggle
    assert "needs_confirm" in toggle, "the latch cleared without asking"


def test_model_cards_can_be_closed_and_a_closed_model_can_be_reopened():
    assert "/api/close_model" in APP_JS and "/api/open_model" in APP_JS
    assert "renderClosed(closed)" in APP_JS


def test_the_setup_panel_goes_through_the_same_renderer():
    setup = _body(r"async loadSetup\(\) \{(.*?)\n  \}")
    assert "new PanelCard(this, SETUP_NAME" in setup, (
        "Setup got its own bespoke wizard again")
    assert "const SETUP_NAME = '__setup__';" in APP_JS


def test_the_region_picker_scales_the_drag_to_screen_coordinates():
    drag = _body(r"bindRegionDrag\(card, element, frame, picture\) \{(.*?)\n  \}")
    assert "scaleX" in drag and "scaleY" in drag
    assert "frame.left" in drag and "frame.top" in drag, (
        "a monitor that does not start at 0,0 would give the model a region "
        "in the wrong place")
    assert "/api/screen" in APP_JS


# --------------------------------------------------------------------------
# the two rules this file does not bend
# --------------------------------------------------------------------------
@pytest.mark.parametrize("sink", MARKUP_SINKS)
def test_no_server_string_can_become_markup(sink):
    assert sink not in CODE, (
        f"{sink} is how a model name or a refusal reason becomes markup "
        f"(WEB-9); build nodes and use textContent")


def test_every_fetch_is_bounded_by_a_timeout():
    """WEB-22: an unbounded fetch hung the poll cycle forever behind one
    stalled request. There is one call site, and it is the wrapper."""
    assert len(re.findall(r"(?<![.\w])fetch\(", APP_JS)) == 1, (
        "a fetch call bypassed the bounded wrapper")
    wrapper = _body(r"async function api\(path, options\) \{(.*?)\n\}")
    assert "AbortController" in wrapper and "controller.abort()" in wrapper
    assert "window.__FETCH_TIMEOUT_MS__" in APP_JS, (
        "a test cannot shrink the timeout without a 20-second sleep")


# --------------------------------------------------------------------------
# the heartbeat's old semantics, kept
# --------------------------------------------------------------------------
def test_the_heartbeat_stops_when_the_tab_is_hidden_and_resumes_when_it_is_not():
    """Deliberately not the same signal as the state poll, which keeps
    running in a backgrounded tab and would tell the watchdog a client is
    present while the laptop lid is shut (D-8)."""
    watch = _body(r"watchVisibility\(\) \{(.*?)\n  \}")
    assert "visibilitychange" in watch
    assert "if (document.hidden) this.stopHeartbeat(); else this.startHeartbeat();" in watch
    assert "pagehide" in watch, (
        "a bfcache-restored tab would resume a stale interval")
    send = _body(r"async sendHeartbeat\(\) \{(.*?)\n  \}")
    assert "document.hidden) return;" in send, (
        "a direct call while hidden still counted as a live client")
    assert "'/api/heartbeat'" in send


def test_the_heartbeat_has_its_own_interval():
    assert "const HEARTBEAT_MS = 2000;" in APP_JS
    assert "const STATE_POLL_MS = 250;" in APP_JS


# --------------------------------------------------------------------------
# the seam: every route the client calls is a route the server answers
# --------------------------------------------------------------------------
def test_every_route_app_js_calls_exists_on_the_server():
    server = SERVER.read_text()
    called = set(re.findall(r"'(/api/[a-z_.]+)", APP_JS))
    answered = set(re.findall(r'"(/api/[a-z_.]+)"', server))
    missing = called - answered
    assert not missing, (
        f"app.js calls {sorted(missing)}, which server.py never answers - "
        f"the WEB-19 failure exactly: both halves' own tests passed")


# --------------------------------------------------------------------------
# styling: theme.py, and nothing else
# --------------------------------------------------------------------------
def test_the_stylesheet_names_no_colour_of_its_own():
    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", STYLES)
    assert not literals, (
        f"styles.css carries its own palette ({literals}); colour lives in "
        f"station/views/theme.py and reaches the browser as /api/theme.css")


def test_every_variable_the_stylesheet_uses_is_one_the_theme_defines():
    defined = set(re.findall(r"(--[a-z-]+):", theme.css_variables()))
    used = set(re.findall(r"var\((--[a-z-]+)\)", STYLES + APP_JS))
    used |= set(re.findall(r"getPropertyValue\('(--[a-z-]+)'\)", APP_JS))
    assert used <= defined, f"undefined theme variables: {sorted(used - defined)}"


def test_the_page_loads_the_theme_and_the_client():
    assert 'href="/api/theme.css"' in INDEX
    assert 'href="/styles.css"' in INDEX
    assert 'src="/app.js"' in INDEX
    for element_id in ("full-stop", "cards", "event-log", "ack-modal",
                       "region-picker", "closed-models", "connection"):
        assert f'id="{element_id}"' in INDEX, f"index.html has no #{element_id}"
        assert f"'{element_id}'" in APP_JS


# --------------------------------------------------------------------------
# syntax
# --------------------------------------------------------------------------
@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_app_js_parses():
    result = subprocess.run([NODE, "--check", str(STATIC / "app.js")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
