"""The bench scale (UX audit 2026-10-07, proposal 6; Setup > Display): a
Setup choice that raises the Web view's base size one step up the theme's
type scale and the sheet's keys to 44 px, for gloves and for reading across
the bench. The launch flag `--font-size` already sets the base; this steps up
from it, so there is one size mechanism, not two. The choice is remembered
the way the store choice is: a Guest's in the station's choices file, a
signed-in user's in their own settings."""
import json
import re
from pathlib import Path

import pytest

import schema as sch
from controller import setup as station_setup
from controller import user_config
from controller.setup import Setup
from views import theme
from test_setup import RecordingController
from test_setup_profile import EMAIL, create, sign_in

STATIC = Path(__file__).resolve().parents[1] / "src" / "views" / "web" / "static"
STYLES = (STATIC / "styles.css").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture
def setup():
    panel = Setup(RecordingController())
    yield panel
    panel.controller.reset()


def _display(panel):
    return next(s for s in panel.schema["sections"] if s["title"] == "Display")


def test_setup_offers_one_bench_scale_toggle_in_a_last_display_section(setup):
    boxes = [e for e in sch.elements(setup.schema) if e.get("model_attr") == "bench_scale"]
    assert len(boxes) == 1 and boxes[0]["type"] == "toggle"   # Setup has no tick boxes
    assert boxes[0]["command"] == "set_bench_scale"
    assert boxes[0]["on_args"] == [True] and boxes[0]["off_args"] == [False]
    assert boxes[0]["text"] == "Bench scale"
    assert boxes[0] in _display(setup)["elements"]
    titles = [s["title"] for s in setup.schema["sections"]]
    # After the station's upkeep rows; Station defaults, when there, stays last.
    assert titles[-1] == Setup.ACCOUNT_SECTION or titles[-1] == "Display"
    assert titles[-2 if titles[-1] == Setup.ACCOUNT_SECTION else -1] == "Display"


def test_it_starts_off_and_the_state_carries_it(setup):
    assert setup.bench_scale is False
    assert setup.state["values"]["bench_scale"] is False


def test_choosing_it_takes_effect_and_is_remembered_for_the_next_launch(setup):
    assert setup.run("set_bench_scale", args=(True,)).is_ok
    assert setup.state["values"]["bench_scale"] is True
    assert json.loads(user_config.path().read_text())["bench_scale"] is True
    again = Setup(RecordingController())
    try:
        assert again.bench_scale is True
        assert again.run("set_bench_scale", args=(False,)).is_ok
        assert Setup(RecordingController()).bench_scale is False
    finally:
        again.controller.reset()


def test_a_choice_that_cannot_be_written_holds_for_the_session(setup, monkeypatch):
    def refuse(key, value):
        raise OSError("read-only")
    monkeypatch.setattr(user_config, "write", refuse)
    assert setup.run("set_bench_scale", args=(True,)).is_ok
    assert setup.bench_scale is True


@pytest.mark.usefixtures("profiles_on", "sample_map_on")
def test_a_signed_in_user_keeps_their_own_and_a_guest_the_stations(setup):
    create(setup)
    assert sign_in(setup).is_ok
    assert setup.run("set_bench_scale", args=(True,)).is_ok
    assert setup.users.setting(EMAIL, "bench_scale") is True
    assert user_config.read("bench_scale", False) is False      # not the station's
    assert setup.run("switch_user").is_ok
    assert setup.run("open_as_guest").is_ok
    assert setup.bench_scale is False                           # the Guest's own
    assert sign_in(setup).is_ok
    assert setup.bench_scale is True


def test_one_size_mechanism_the_theme_serves_the_step_beside_the_base():
    css = theme.css_variables()
    assert f"--font-size: {theme.FONT_SIZE}pt;" in css
    assert f"--font-size-bench: {theme.size(1)}pt;" in css
    assert theme.size(1) > theme.FONT_SIZE
    old = theme.FONT_SIZE
    try:
        theme.set_font_size(16)          # what --font-size 16 does at launch
        assert f"--font-size-bench: {theme.size(1)}pt;" in theme.css_variables()
    finally:
        theme.set_font_size(old)


def test_the_stylesheet_steps_the_base_up_and_the_sheet_keys_to_44px():
    rule = re.search(r"html\[data-bench\]\s*\{([^}]*)\}", STYLES)
    assert rule, "no bench-scale rule"
    assert "--font-size: var(--font-size-bench)" in rule.group(1)
    assert re.search(r"--hit-panel:\s*max\(44px,\s*2\.25rem\)", rule.group(1))
    # Every sheet key is sized from --hit-panel, so one variable moves them all.
    assert STYLES.count("min-height: var(--hit-panel)") >= 8


def test_the_client_sets_the_root_attribute_from_the_setup_state():
    body = re.search(r"applyBenchScale\(setupState\) \{(.*?)\n  \}", APP_JS, re.S)
    assert body, "no applyBenchScale"
    assert "values.bench_scale" in body.group(1)
    assert "toggleAttribute('data-bench'" in body.group(1)
    assert "this.reserveLogSpace()" in body.group(1)
    assert "this.applyBenchScale(setupState)" in APP_JS


def test_the_page_declares_its_scheme_and_the_region_canvas_is_an_image():
    assert '<meta name="color-scheme" content="light">' in INDEX
    assert re.search(r'<canvas id="region-canvas"[^>]*role="img"', INDEX)
