"""`views.theme` — one look for three views, and the toggle colours.

RC-7's colour half: elements used to carry `bg`/`fg` hex strings that only Tk
could honour, so the same control looked different in each frontend. The
schema names the *meaning* and this module is the only place that turns a
meaning into a colour.
"""
import pytest

import schema as sch
from views import theme

from test_core_fakes import FakeModel

pytestmark = pytest.mark.schema


@pytest.fixture(autouse=True)
def restore_font_size():
    saved = theme.FONT_SIZE
    yield
    theme.FONT_SIZE = saved


# -- the palette -------------------------------------------------------------

def test_the_theme_names_a_colour_for_every_schema_role():
    assert set(theme.ROLES) == set(sch.ROLES), (
        "a role the schema can emit with no colour is a control one renderer "
        "draws and another does not")


def test_every_role_is_a_background_foreground_pair_of_hex_colours():
    for role, pair in theme.ROLES.items():
        assert len(pair) == 2, role
        assert all(c.startswith("#") and len(c) == 7 for c in pair), role


def test_an_unknown_role_falls_back_to_neutral_rather_than_raising():
    assert theme.colors("chartreuse") == theme.ROLES["neutral"]
    assert theme.colors(None) == theme.ROLES["neutral"]


def test_every_severity_maps_to_a_role_a_renderer_can_draw():
    assert set(theme.SEVERITY_ROLE) == {"error", "warning", "info"}
    assert set(theme.SEVERITY_ROLE.values()) <= set(theme.ROLES)


def test_the_disabled_pair_is_dimmer_than_any_live_role():
    """Bench sheet (2026-09-25): a disabled control sits on the sheet itself
    with ink at 45 %, so it recedes from every panel-toned live control."""
    background, foreground = theme.DISABLED
    assert (background, foreground) != theme.ROLES["neutral"]
    assert background == theme.BACKGROUND
    assert foreground not in (theme.TEXT, theme.MUTED)


# -- fonts -------------------------------------------------------------------

def test_font_returns_the_tk_tuple_qt_can_also_build_from():
    family, size, weight = theme.font()
    assert family == theme.FONT_FAMILY and size == theme.FONT_SIZE
    assert weight == "normal"
    assert theme.font(bold=True)[2] == "bold"


def test_font_scales_and_never_goes_below_readable():
    assert theme.font(2.0)[1] == round(theme.FONT_SIZE * 2)
    assert theme.font(0.01)[1] == 8


def test_set_font_size_clamps_to_a_usable_range():
    theme.set_font_size(1)
    assert theme.FONT_SIZE == 8
    theme.set_font_size(999)
    assert theme.FONT_SIZE == 28
    theme.set_font_size(14)
    assert theme.FONT_SIZE == 14


def test_set_font_size_accepts_the_string_a_command_line_hands_it():
    theme.set_font_size("16")
    assert theme.FONT_SIZE == 16


# -- toggle_colors: ON and OFF differ without relying on green-vs-red --------

def test_an_on_toggle_is_filled_with_its_on_role():
    element = sch.toggle("Auto", "is_auto", "set_mode", "ON", "OFF",
                         on_role="go", off_role="neutral")
    style = theme.toggle_colors(element, True)
    assert style["background"] == theme.ROLES["go"][0]
    assert style["foreground"] == theme.ROLES["go"][1]
    assert style["border"] == theme.ROLES["go"][0]


def test_an_off_toggle_is_a_cap_key_with_the_family_rim():
    """Signature (2026-09-27): OFF is a CAP-faced key outlined in KEY_RIM,
    one member of the key family, as the Signature artboards draw it; the
    role's own colour is kept only for danger (the stop must read as the
    stop). Was: the sheet with a muted outline (Bench sheet, 2026-09-25)."""
    element = sch.toggle("Auto", "is_auto", "set_mode", "ON", "OFF",
                         on_role="go", off_role="neutral")
    style = theme.toggle_colors(element, False)
    assert style["background"] == theme.CAP
    assert style["border"] == theme.KEY_RIM


@pytest.mark.parametrize("role", sorted(sch.ROLES))
def test_on_and_off_are_distinguishable_for_every_role(role):
    """Even when on_role == off_role, which is what the safety toggle does."""
    element = sch.toggle("T", "flag", "cmd", "ON", "OFF",
                         on_role=role, off_role=role)
    on = theme.toggle_colors(element, True)
    off = theme.toggle_colors(element, False)
    assert on != off, f"{role}: ON and OFF render identically"
    assert on["background"] != off["background"], role


def test_the_safety_toggle_is_a_danger_filled_on_not_a_green_one():
    """"A latched estop is `on_role="danger"` — not green because it happens
    to be 'on'.\""""
    toggle = FakeModel().schema["sections"][-1]["elements"][0]
    assert toggle["text"] == "Stop"  # F20: one word for the stop object (2026-09-24)
    on = theme.toggle_colors(toggle, True)
    off = theme.toggle_colors(toggle, False)
    assert on["background"] == theme.ROLES["danger"][0]
    assert on["background"] != theme.ROLES["go"][0]
    assert off["border"] == theme.ROLES["danger"][0], (
        "an unlatched FULL STOP must still read as the dangerous control")
    assert on != off


def test_an_indicator_uses_the_same_two_role_shape():
    element = sch.indicator("Fault", "is_faulted")
    assert theme.toggle_colors(element, True)["background"] == theme.ROLES["danger"][0]
    assert theme.toggle_colors(element, False)["background"] == theme.BACKGROUND


def test_a_toggle_with_no_declared_roles_still_renders():
    """Signature: a latching key ON is the ink key, whatever its role."""
    style = theme.toggle_colors({}, True)
    assert style["background"] == theme.ROLES["go"][0]


def test_every_style_key_a_renderer_needs_is_present():
    element = sch.toggle("T", "flag", "cmd", "ON", "OFF")
    for is_on in (True, False):
        assert set(theme.toggle_colors(element, is_on)) == {
            "background", "foreground", "border"}


# -- the Web client gets the same palette ------------------------------------

def test_css_variables_carry_every_role_the_desktop_views_use():
    css = theme.css_variables()
    assert css.startswith(":root {") and css.rstrip().endswith("}")
    for role, (background, foreground) in theme.ROLES.items():
        assert f"--{role}-bg: {background};" in css
        assert f"--{role}-fg: {foreground};" in css


def test_css_variables_carry_the_surface_colours_and_the_font():
    css = theme.css_variables()
    for fragment in (f"--bg: {theme.BACKGROUND};", f"--surface: {theme.SURFACE};",
                     f"--text: {theme.TEXT};", f"--muted: {theme.MUTED};",
                     theme.FONT_FAMILY):
        assert fragment in css


def test_css_variables_follow_a_font_size_change():
    theme.set_font_size(20)
    assert "--font-size: 20pt;" in theme.css_variables()


# -- Signature (2026-09-27): the derived tokens every view reads ---------------

def test_every_derived_shade_is_a_mix_of_the_six_palette_values():
    """The spec's measured values (`handoff/tactile3-Signature.md`), so a
    view that draws them is drawing what the owner ratified."""
    assert theme.CAP == "#f5f7f6" and theme.RAIL == "#f1f2f2"
    assert theme.DEEP == "#cdd1d0" and theme.EDGE == "#767a7c"
    assert theme.EDGE_SOFT == "#aaadad" and theme.SKIRT == "#7e221e"
    assert theme.KEY_RIM == "#686c6e" and theme.KEY_LIP == "#4d5254"
    assert theme.GO_LIP == "#0c0e0f" and theme.DISABLED[1] == "#53585a"


def test_the_key_family_has_one_lip_per_part_and_one_pressed_lip():
    assert set(theme.KEY_LIP_PX) == {"key", "small", "knob", "kbd", "pressed"}
    assert theme.KEY_LIP_PX["pressed"] == 1
    assert all(px > theme.KEY_LIP_PX["pressed"]
               for name, px in theme.KEY_LIP_PX.items() if name != "pressed")


def test_the_stop_is_a_key_in_a_collar_whose_states_are_tones():
    """Latched, the socket floods SKIRT and the collar turns SIGNAL; the
    key itself stays red in every state (the stop rule)."""
    stop = theme.STOP
    assert stop["key"] + 2 * (stop["collar"] + stop["gap"]) <= stop["diameter"]
    assert stop["face"] == theme.SIGNAL and stop["collar_latched"] == theme.SIGNAL
    assert stop["socket"] == theme.SURFACE and stop["socket_latched"] == theme.SKIRT
    assert stop["collar_fill"] == theme.TEXT
    assert stop["key_narrow"] < stop["key"] and stop["diameter_narrow"] < stop["diameter"]


def test_the_lamp_is_never_trace_and_signal_only_for_unconfirmed():
    assert theme.TRACE not in theme.LAMP.values()
    assert theme.LAMP["unconfirmed"] == theme.SIGNAL
    assert theme.LAMP["on"] == theme.TEXT and theme.LAMP["off"] == theme.SURFACE


def test_the_icon_set_is_the_nine_glyphs_drawn_in_one_stroke():
    assert tuple(theme.ICONS) == theme.ICON_NAMES
    assert len(theme.ICON_NAMES) == 9
    svg = theme.icon_svg("warning", 16, theme.SIGNAL)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert 'viewBox="0 0 20 20"' in svg and 'stroke-width="1.75"' in svg
    assert f'stroke="{theme.SIGNAL}"' in svg and 'width="16"' in svg


def test_readings_are_the_signature_sizes_and_weight():
    assert theme.READING_SIZES == {"focal": 60, "primary": 44, "compact": 36, "secondary": 30}
    assert theme.READING_FOCAL_NARROW < theme.READING_SIZES["focal"]
    assert theme.NUMERAL_WEIGHT == 600 and theme.NUMERAL_STRETCH == 100
    assert theme.NUMERAL_FAMILY == "Rubik" and theme.FONT_FAMILY == "Figtree"


def test_css_variables_carry_the_signature_tokens():
    css = theme.css_variables()
    for fragment in (f"--cap: {theme.CAP};", f"--key-rim: {theme.KEY_RIM};",
                     f"--key-lip: {theme.KEY_LIP};", f"--go-lip: {theme.GO_LIP};",
                     f"--skirt: {theme.SKIRT};", "--lip-key: 4px;", "--lip-pressed: 1px;",
                     f"--shadow-raised: {theme.SHADOW_RAISED};", f"--engrave: {theme.ENGRAVE};",
                     "--stop-collar: 10px;", f"--stop-socket-latched: {theme.SKIRT};",
                     "--lamp-w: 6px;", "--flag-w: 30px;", "--motion-press: 70ms;",
                     "--radius-key: 8px;", "--fader-w: 16px;", "--reading-focal: 60px;"):
        assert fragment in css, fragment
