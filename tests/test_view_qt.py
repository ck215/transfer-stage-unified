"""The Qt view's toolkit-free half: the pure helpers and the build contract.

Nothing here needs a QApplication, so these run in the ordinary gate. The
widget behaviour is in `test_view_qt_widgets.py`, every test of which is
`qt`-marked and run by the lead.
"""
import importlib.util
import re
import sys

import pytest

import schema as sch
from views import qt, theme


# ---------------------------------------------------------------------------
# The build contract
# ---------------------------------------------------------------------------

def test_every_schema_element_type_has_a_renderer():
    """`PanelView.__init__` refuses to construct when one is missing, which is
    how feature parity is enforced rather than hoped for. Checking it here as
    well means a deleted `_make_` is a failing test, not a failing launch."""
    missing = [name for name in sorted(sch.ELEMENT_TYPES)
               if not callable(getattr(qt.QtPanelView, f"_make_{name}", None))]
    assert not missing, f"QtPanelView cannot render: {missing}"


def test_no_renderer_exists_for_a_type_the_schema_does_not_declare():
    """A `_make_` with no element type behind it is a control no other view
    has - the RC-7 drift schema v2 exists to stop."""
    made = {name[len("_make_"):] for name in dir(qt.QtPanelView)
            if name.startswith("_make_") and name != "_make_section"}
    assert made == set(sch.ELEMENT_TYPES)


def test_both_section_layouts_offer_the_builders_one_api():
    """`_make_section` returns a container, not a QFormLayout, so none of the
    element builders carries a layout branch. Every container answers the
    same calls and says how wide a control in it should be. Updated (E): the
    column section is a `FlowSection` on the Bench sheet (captions over
    controls, wrapping like words), and the calls gained `add_inline` (a
    status word) and `add_axis` (X Y Z under one caption)."""
    for container in (qt.FlowSection, qt.TableRow, qt.TableBar):
        for call in ("add", "add_wide", "add_inline", "add_axis"):
            assert callable(getattr(container, call)), (container, call)
        assert isinstance(container.control_width, int)
    # A row's cells are sized; a flow's controls size themselves.
    assert qt.TableRow.control_width > 0
    assert qt.FlowSection.control_width == 0
    assert qt.TableRow.is_row is True and qt.FlowSection.is_row is False


# ---------------------------------------------------------------------------
# Styling: theme only
# ---------------------------------------------------------------------------

def _theme_colours():
    """Every colour value the theme module holds: its tokens, its role pairs,
    its dicts. A colour in the sheet must be one of these, never a mix the
    view made for itself."""
    found = set()

    def walk(value):
        if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            found.add(value.lower())
        elif isinstance(value, (tuple, list, set, frozenset)):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
    for name in dir(theme):
        if not name.startswith("_"):
            walk(getattr(theme, name))
    return found


def test_the_stylesheet_uses_only_theme_colours():
    """Updated (E, 2026-09-25): the known set is the theme's own members -
    the Bench sheet tokens, roles, DISABLED, LIFT, INPUT_BORDER, TRACE - and
    nothing qt.py derived from the old dark tokens (its INPUT_BORDER is
    gone; the sheet carries the trace for changing readings)."""
    known = _theme_colours()
    used = {c.lower() for c in re.findall(r"#[0-9a-fA-F]{3,8}", qt.stylesheet())}
    assert used <= known, f"not from theme: {used - known}"
    assert theme.TRACE.lower() in used


def test_the_stylesheet_follows_the_launch_font_size():
    original = theme.FONT_SIZE
    try:
        theme.set_font_size(9)
        assert "font-size: 9pt;" in qt.stylesheet()
        theme.set_font_size(20)
        sheet = qt.stylesheet()
        assert "font-size: 20pt;" in sheet
        assert "font-size: 9pt;" not in sheet
    finally:
        theme.set_font_size(original)


def test_the_stylesheet_names_every_role():
    sheet = qt.stylesheet()
    for role in sch.ROLES:
        assert f'QPushButton[role="{role}"]' in sheet


def test_the_stylesheet_dresses_the_table_and_the_rail():
    """The polish pass is in the sheet, not sprinkled through the builders:
    a rule here reaches every panel at once and follows --font-size. (Was
    "...and_the_toolbar": the row of toolbar tabs that repeated the dock
    titles is gone, and the rail replaced it.)"""
    sheet = qt.stylesheet()
    # Updated (E): the rail carries no numbers any more (the sheet's entries
    # do, once), so `railValue` gave way to the entry's rule, the well, the
    # tier-3 strip and the rail's model list.
    for selector in ("QLabel#columnHeader", "QLabel#rowTitle", "QFrame#rail",
                     "QPushButton#railModel", "QFrame#entryRule", "QFrame#well",
                     "QFrame#diagnostics", "QDockWidget::title"):
        assert f"{selector} {{" in sheet, selector
    assert "QToolBar" not in sheet


def test_a_readout_and_an_entry_do_not_look_the_same():
    """The owner's "readouts distinct from entries". Updated (E): an entry is
    a panel-toned well (`theme.WELL`) with a muted underline and no box; a
    readout is the numeral face straight on the surface, no well and no
    border, ink at rest and the trace only while it changes (`live`).
    Signature (2026-09-27): the field has its own rule - the select is now a
    key (`QComboBox`), no longer dressed as a field."""
    sheet = qt.stylesheet()
    entries = sheet.split("QLineEdit {")[1].split("}")[0]
    readouts = sheet.split("QLabel#valueLabel {")[1].split("}")[0]
    live = sheet.split('QLabel#valueLabel[live="true"] {')[1].split("}")[0]
    assert f"background-color: {theme.WELL};" in entries
    assert "border: none" in entries and "border-bottom:" in entries
    assert "background" not in readouts and "border" not in readouts
    assert f"color: {theme.TEXT};" in readouts
    assert f"color: {theme.TRACE};" in live


def test_a_readout_at_rest_is_drawn_muted():
    """ "off" is a readout, not a bold label; it does not compete with a
    number that is moving."""
    sheet = qt.stylesheet()
    quiet = sheet.split('QLabel#valueLabel[quiet="true"] {')[1].split("}")[0]
    assert f"color: {theme.MUTED};" in quiet


def test_every_control_has_hover_focus_pressed_and_disabled_states():
    sheet = qt.stylesheet()
    for selector in ("QPushButton:hover", "QPushButton:focus",
                     "QPushButton:pressed", "QPushButton:disabled",
                     "QComboBox:hover", "QComboBox:focus",
                     "QLineEdit:hover", "QLineEdit:focus"):
        assert selector in sheet, selector
    # Updated (F25): focus is two pixels of ink, not trace - a trace ring is
    # what the latched stop looks like.
    focus = sheet.split("QPushButton:focus {")[1].split("}")[0]
    assert f"2px solid {theme.STOP_FOCUS}" in focus
    assert f"2px solid {theme.TRACE}" not in sheet


def test_signal_red_is_spent_on_the_stop_object_alone():
    """One red. A plain button with the `danger` role (Red Percent's "Stop"
    run) renders as an ordinary command; the stop disc paints itself.
    Updated (E): an ordinary command is outlined on the sheet, so `danger`
    is dressed exactly like `neutral` (was: the neutral panel fill)."""
    sheet = qt.stylesheet()
    assert theme.SIGNAL.lower() not in sheet.lower()
    danger = sheet.split('QPushButton[role="danger"] {')[1].split("}")[0]
    neutral = sheet.split('QPushButton[role="neutral"] {')[1].split("}")[0]
    assert danger == neutral


def test_the_type_scale_is_one_family_on_a_tight_ratio():
    """Base 12 pt, ratio 1.2: every text size in the sheet is a step of it.
    Updated (E): readings are a second, numeral scale - `theme.READING_SIZES`
    in the numeral face (and the stopped headline at `primary`) - so the
    sizes are the text steps plus the readings, and the families are the
    text face and the numerals' face, nothing else."""
    original = theme.FONT_SIZE
    try:
        theme.set_font_size(12)
        sizes = {int(n) for n in re.findall(r"font-size: (\d+)pt", qt.stylesheet())}
        readings = {qt.reading_pt(kind) for kind in theme.READING_SIZES}
        assert sizes <= {10, 12, 14, 17} | readings, sizes
        families = set(re.findall(r"font-family: ([^;]+);", qt.stylesheet()))
        assert families == {theme.FONT_FAMILY, qt.numeral_family()}
        assert qt.numeral_family().startswith(theme.NUMERAL_FAMILY)
    finally:
        theme.set_font_size(original)


# ---------------------------------------------------------------------------
# Copy: sentence case, as the Web view renders it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("X Position:", "X position"),
    ("Probe Tilt Angle:", "Probe tilt angle"),
    ("Tip / Consumable ID:", "Tip / consumable ID"),
    ("Operator Annotation (intended)", "Operator annotation (intended)"),
    ("FULL STOP", "Full stop"),
    ("Sync X: OFF", "Sync X: OFF"),
    ("Enter Autonomous Mode", "Enter autonomous mode"),
    ("Red % over time", "Red % over time"),
    ("", ""),
])
def test_a_label_is_sentence_case_without_its_colon(text, expected):
    assert qt.sentence_case(text) == expected


def test_sentence_keeps_interior_words_and_only_lowers_shouting():
    assert qt.sentence("LATCHED - click to clear") == "Latched - click to clear"
    assert qt.sentence("Stepper Probe") == "Stepper Probe"


@pytest.mark.parametrize("text", ["off", "Off", "False", "", None, "none",
                                  "nothing selected", "not scanned yet"])
def test_a_value_at_rest_is_quiet(text):
    assert qt.is_quiet_value(text) is True


@pytest.mark.parametrize("text", ["0", "simulated", "detected: Rotator",
                                  "not detected", "0.00", "True"])
def test_a_live_value_is_not_quiet(text):
    """A zero is a reading, and "not detected" is news: neither is at rest."""
    assert qt.is_quiet_value(text) is False


# ---------------------------------------------------------------------------
# The rail and the action lines
# ---------------------------------------------------------------------------

def test_the_rail_carries_what_a_model_flags_for_it():
    schema = sch.schema(
        sch.section("Run", sch.readonly("Run ID:", "run_id")),
        sch.section("Live", sch.readonly("Current Red:", "current_red", rail=True),
                    sch.readonly("Frames:", "frames")))
    assert [e["model_attr"] for e in qt.rail_elements(schema)] == ["current_red"]


def test_the_rail_falls_back_to_the_first_section_with_a_readout():
    schema = sch.schema(
        sch.section("Setup", sch.button("Go", "go")),
        sch.section("Frame", *[sch.readonly(f"{a}:", a) for a in "abcdef"]))
    picked = [e["model_attr"] for e in qt.rail_elements(schema)]
    assert picked == ["a", "b", "c", "d"] and len(picked) == qt.RAIL_READOUTS


def test_a_model_with_no_readout_puts_nothing_on_the_rail():
    assert qt.rail_elements(sch.schema(sch.section("S", sch.button("Go", "go")))) == []
    assert qt.rail_elements(None) == []


def test_a_row_that_runs_something_is_an_action_line():
    devices = sch.section("Devices", sch.button("Refresh", "refresh"),
                          sch.readonly("Scan:", "scan_status"), layout="row")
    model = sch.section("Rotator", sch.dropdown("Port", "p", "set_p", "opts"),
                        sch.readonly("Status:", "s"), layout="row")
    column = sch.section("Launch", sch.button("Launch", "launch"))
    assert qt.is_action_row(devices) is True
    assert qt.is_action_row(model) is False
    assert qt.is_action_row(column) is False       # not a row at all


def _contrast(a, b):
    def luminance(colour):
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                    for c in channels]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_view_mixes_colours_with_the_themes_one_mix():
    """Updated (F23, DS-12): qt.py had its own `mix` that read its amount in
    the opposite direction to Tk's; it is gone, and so is `rgba`. Updated (E):
    its `INPUT_BORDER` (a mix of the old dark tokens) is gone too - the
    border is `theme.INPUT_BORDER`."""
    assert not hasattr(qt, "mix") and not hasattr(qt, "rgba")
    assert not hasattr(qt, "INPUT_BORDER")


def test_an_input_border_is_at_least_3_to_1_on_the_card():
    """WCAG 1.4.11 (AUD-11): the well's border was 1.67:1 on the card.
    Updated (E): the edge is `theme.INPUT_BORDER` (muted), an underline only,
    and it clears 3:1 on both grounds an input sits on. Signature: it is the
    field's 1.5 px floor lip (`KEY_RIM_PX`), and it clears 3:1 on DEEP too,
    the field's ground inside a tray."""
    assert _contrast(theme.INPUT_BORDER, theme.SURFACE) >= 3.0
    assert _contrast(theme.INPUT_BORDER, theme.BACKGROUND) >= 3.0
    assert _contrast(theme.INPUT_BORDER, theme.DEEP) >= 3.0
    sheet = qt.stylesheet()
    entries = sheet.split("QLineEdit {")[1].split("}")[0]
    assert f"border-bottom: {theme.KEY_RIM_PX:g}px solid {theme.INPUT_BORDER}" in entries


def test_every_focusable_has_an_ink_ring_including_tabs_and_scroll_areas():
    sheet = qt.stylesheet()
    # Updated (E): no tabs and no per-panel scroll areas any more; the sheet
    # scrolls as one page, and the disclosures, the slider and the rail's
    # model list are new focusables.
    for selector in ("QScrollArea#sheetScroll:focus", "QToolButton#disclosure:focus",
                     "QSlider:focus", "QPushButton#railModel:focus",
                     "QFrame#tray QTextEdit:focus", "QPushButton#ghost:focus",
                     "QPushButton#iconButton:focus"):
        rule = sheet.split(selector)[1].split("}")[0]
        assert qt.FOCUS_RING in rule, selector


def test_readings_take_the_themes_reading_sizes_and_grow_only_so_far():
    """Replaces "the rail readout is capped one step up" (F25): the rail
    carries no readings now. A reading is `theme.READING_SIZES` px at 96 dpi,
    grows with the launch font up to `READING_GROWTH` (a 52 px focal number
    is not 121 px at 28 pt), and is never smaller than the text beside it."""
    original = theme.FONT_SIZE
    try:
        theme.set_font_size(12)
        assert qt.reading_pt("focal") == round(theme.READING_SIZES["focal"] * 0.75)
        sheet = qt.stylesheet()
        for kind in theme.READING_SIZES:
            rule = sheet.split(f'QLabel#reading[scale="{kind}"] {{')[1].split("}")[0]
            assert f"font-size: {qt.reading_pt(kind)}pt" in rule
        theme.set_font_size(28)
        assert qt.reading_pt("focal") == round(
            theme.READING_SIZES["focal"] * 0.75 * qt.READING_GROWTH)
        assert all(qt.reading_pt(k) >= theme.size(1) for k in theme.READING_SIZES)
    finally:
        theme.set_font_size(original)


def test_the_sheet_uses_the_spacing_scale_not_sums():
    source = open(qt.__file__).read()
    assert not re.findall(r"(PAD|GAP|INSET)\s*[+*]\s*\d", source)
    assert not re.findall(r"(PAD|GAP|INSET)\s*//\s*\d", source)


@pytest.mark.parametrize("kind, word", [("SerialPort", "serial port"),
                                        ("Gamepad", "gamepad"),
                                        ("SMC100", "SMC100")])
def test_a_device_is_named_as_the_operator_would_say_it(kind, word):
    assert qt.device_word(kind) == word


def test_only_a_lost_device_is_reported():
    assert qt.lost_devices({"devices": {"SerialPort": "lost",
                                        "Gamepad": "open"}}) == ["serial port"]
    assert qt.lost_devices({"values": {}}) == []
    assert qt.lost_sentence("Stepper Probe", ["serial port"]) == (
        "Stepper Probe lost its serial port")


def test_v1_the_rail_mark_carries_the_entrys_link_tier():
    """rb-link-views V1: a link lost or reconnecting is the danger tier on
    the rail (the loud square), outranked only by a stop that did not
    confirm and a fault; a stalled link is the attention tier, under a
    plain latch."""
    stop = {"latched": ["B"], "unconfirmed": ["C"]}
    tiers = {"A": "error", "B": "warning", "C": "error", "D": "warning"}
    assert qt.rail_mark("A", stop, set(), tiers) == "lost"
    assert qt.rail_mark("B", stop, set(), tiers) == "stopped"
    assert qt.rail_mark("C", stop, set(), tiers) == "unconfirmed"
    assert qt.rail_mark("D", stop, set(), tiers) == "attention"
    assert qt.rail_mark("A", stop, {"A"}, tiers) == "faulted"
    assert qt.rail_mark("E", stop, set(), tiers) is None
    assert qt.rail_mark("A", stop, set()) is None, "no tiers: the old rule"
    assert qt.worst_mark(["attention", "lost", "stopped"]) == "lost"
    assert qt.worst_mark(["attention", "stopped"]) == "stopped"
    for kind in ("lost", "attention"):
        assert qt.RAIL_STOP_WORDS[kind]


def test_no_motion_is_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("STATION_NO_MOTION", raising=False)
    assert qt.motion_reduced() is False
    monkeypatch.setenv("STATION_NO_MOTION", "1")
    assert qt.motion_reduced() is True
    monkeypatch.setenv("STATION_NO_MOTION", "0")
    assert qt.motion_reduced() is False


def test_the_module_declares_no_colour_and_no_pixel_font_size():
    """The `style.qss` rule, enforced on the source itself: a colour literal
    here is a second palette that drifts from the Tk and Web views."""
    source = open(qt.__file__).read()
    assert not re.findall(r"#[0-9a-fA-F]{6}\b", source), "hex colour literal"
    assert not re.findall(r"font-size:\s*\d+px", source), "pixel font size"
    # The sheet is generated, never loaded: the module reads no file at all.
    # (It *mentions* `style.qss` in prose - this codebase's comments quote
    # what they replaced, so a grep hit is not evidence by itself.)
    assert not re.findall(r"(?<!def )(?<![\w.])open\s*\(", source), "reads a file"


# ---------------------------------------------------------------------------
# Logical -> physical pixels (REDPERCENT-18)
# ---------------------------------------------------------------------------

def test_an_unscaled_display_converts_to_itself():
    assert qt.to_physical_pixels(100, 50, 400, 300, 1.0) == (100, 50, 400, 300)


def test_a_retina_display_doubles_the_region():
    """The defect: Qt's logical coordinates went to the grabber unchanged, so
    a 200 % display captured a quarter-size rectangle in the wrong place."""
    assert qt.to_physical_pixels(100, 50, 400, 300, 2.0) == (200, 100, 800, 600)


def test_fractional_scaling_rounds_rather_than_truncates():
    """125-150 % scaling is the Windows station PC's case; a truncating
    conversion loses a pixel off every edge. (Half-pixels take Python's
    round-half-to-even, which is why 50 * 1.5 is 75 and not 76.)"""
    assert qt.to_physical_pixels(10, 10, 101, 50, 1.5) == (15, 15, 152, 75)


def test_the_offset_is_measured_from_the_screen_the_drag_happened_on():
    """A second screen at logical x=1920, scaled 2x: the offset *within* that
    screen is what scales, not the absolute coordinate."""
    assert qt.to_physical_pixels(1930, 10, 100, 100, 2.0,
                                 screen_origin=(1920, 0),
                                 screen_physical_origin=(1920, 0)) \
        == (1940, 20, 200, 200)


def test_a_missing_ratio_is_treated_as_unscaled():
    assert qt.to_physical_pixels(1, 2, 3, 4, None) == (1, 2, 3, 4)


# ---------------------------------------------------------------------------
# The plot's arithmetic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("data,expected", [
    (None, []),
    ({}, []),
    ({"y": [1, 2]}, [(0.0, 1.0), (1.0, 2.0)]),
    ({"x": [5, 6], "y": [1, 2]}, [(5.0, 1.0), (6.0, 2.0)]),
    ([3, 4], [(0.0, 3.0), (1.0, 4.0)]),
    ([(1, 2), (3, 4)], [(1.0, 2.0), (3.0, 4.0)]),
    ("nonsense", []),
])
def test_series_points_normalises_what_a_data_command_returned(data, expected):
    assert qt.series_points(data) == expected


def test_an_unreadable_sample_is_dropped_not_raised():
    """This runs on the render tick: one bad sample must not stop the panel."""
    assert qt.series_points({"y": [1, "x", 3]}) == [(0.0, 1.0), (2.0, 3.0)]


def test_the_polyline_stays_inside_the_widget():
    points = [(0, 0), (1, 10), (2, 5)]
    scaled = qt.polyline_points(points, 100, 50, margin=5)
    assert len(scaled) == 3
    assert all(0 <= x <= 100 and 0 <= y <= 50 for x, y in scaled)


def test_a_flat_series_is_drawn_down_the_middle_not_divided_by_zero():
    scaled = qt.polyline_points([(0, 7), (1, 7)], 100, 50)
    assert [y for _, y in scaled] == [25.0, 25.0]


def test_a_series_shorter_than_two_points_draws_nothing():
    assert qt.polyline_points([(0, 1)], 100, 50) == []
    assert qt.polyline_points([(0, 1), (1, 2)], 0, 0) == []


# ---------------------------------------------------------------------------
# Entry validation (PYSIDE-19)
# ---------------------------------------------------------------------------

def test_a_text_entry_gets_no_numeric_validator():
    assert qt.validator_bounds({"value_type": "text"}) is None
    assert qt.validator_bounds({}) is None


def test_the_validator_never_narrows_what_the_model_would_accept():
    """The old validator was fixed at three decimals, so a PID integral term
    of 0.0005 could not be typed at all. Display precision and input
    precision are different questions."""
    _, _, decimals = qt.validator_bounds({"value_type": "float", "decimals": 3})
    assert decimals >= 4
    assert decimals == qt.INPUT_DECIMALS


def test_an_integer_entry_accepts_no_decimals():
    low, high, decimals = qt.validator_bounds(
        {"value_type": "int", "min": 0, "max": 9, "decimals": 0})
    assert (low, high, decimals) == (0.0, 9.0, 0)


def test_an_unbounded_entry_gets_wide_bounds_not_none():
    low, high, _ = qt.validator_bounds({"value_type": "float"})
    assert low < -1e9 < 1e9 < high


# ---------------------------------------------------------------------------
# Integers are integers (Addendum 2)
# ---------------------------------------------------------------------------

def test_only_an_int_entry_asks_for_an_integer_validator():
    assert qt.int_bounds({"value_type": "float", "min": 0, "max": 9}) is None
    assert qt.int_bounds({"value_type": "text"}) is None
    assert qt.int_bounds({}) is None


def test_an_int_entry_carries_its_declared_bounds():
    assert qt.int_bounds({"value_type": "int", "min": 0, "max": 9}) == (0, 9)


def test_an_unbounded_int_entry_stays_inside_a_c_int():
    """`validator_bounds` answers 1e12, which `QIntValidator` cannot store."""
    low, high = qt.int_bounds({"value_type": "int"})
    assert (low, high) == (-qt.INT_LIMIT, qt.INT_LIMIT)
    assert -2 ** 31 <= low and high < 2 ** 31


def test_an_int_validator_widens_a_fractional_bound_rather_than_narrowing_it():
    """PYSIDE-19's rule, restated for integers: the box never refuses what
    `Param.parse` would accept. A minimum of 0.5 admits 1, so the validator
    has to admit it too - and flooring is the only way round that keeps it."""
    assert qt.int_bounds({"value_type": "int", "min": 0.5, "max": 9.5}) == (0, 10)


@pytest.mark.parametrize("text,expected", [
    ("5.000", "5"), ("5", "5"), ("-3.000", "-3"), ("0.0", "0"),
    ("", ""), ("   ", "   "), (None, ""), ("not a number", "not a number"),
])
def test_an_int_entry_is_never_shown_a_decimal_point(text, expected):
    """A refresh that writes "5.000" into a box guarded by a QIntValidator
    leaves the operator editing a field that rejects its own contents."""
    assert qt.display_text({"value_type": "int"}, text) == expected


def test_a_float_entry_keeps_every_decimal_the_model_formatted():
    assert qt.display_text({"value_type": "float"}, "5.000") == "5.000"
    assert qt.display_text({}, "5.000") == "5.000"


# ---------------------------------------------------------------------------
# File dialogs (PYSIDE-18)
# ---------------------------------------------------------------------------

def test_a_bare_filename_gets_the_declared_extension():
    assert qt.with_extension("/tmp/run1", ("csv",)) == "/tmp/run1.csv"


def test_a_filename_that_already_has_one_is_left_alone():
    assert qt.with_extension("/tmp/run1.txt", ("csv",)) == "/tmp/run1.txt"


def test_the_dialog_filter_offers_every_declared_extension_and_all_files():
    assert qt.dialog_filter(("csv", "json")) == (
        "CSV files (*.csv);;JSON files (*.json);;All files (*)")


# ---------------------------------------------------------------------------
# The guarded import
# ---------------------------------------------------------------------------

class _NoPySide6:
    """A meta-path finder that makes `import PySide6` fail."""

    def find_spec(self, name, path=None, target=None):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ImportError("PySide6 is blocked for this test")
        return None


def _import_without_pyside6():
    saved = {name: module for name, module in sys.modules.items()
             if name == "PySide6" or name.startswith("PySide6.")}
    for name in saved:
        del sys.modules[name]
    blocker = _NoPySide6()
    sys.meta_path.insert(0, blocker)
    try:
        spec = importlib.util.spec_from_file_location("_qt_without_pyside6",
                                                      qt.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(saved)


def test_the_module_imports_on_a_machine_with_no_pyside6():
    """`app` picks a view at runtime, and importing the view module
    must not be what decides whether the Tk or Web frontend can launch."""
    module = _import_without_pyside6()
    assert module.HAS_QT is False
    assert module.stylesheet()
    assert module.to_physical_pixels(1, 1, 2, 2, 2.0) == (2, 2, 4, 4)


def test_building_a_qt_widget_without_pyside6_says_so_instead_of_failing_oddly():
    module = _import_without_pyside6()
    # Updated (E): the model's dock became a `SheetEntry` on the sheet.
    for factory in (module.SeriesPlot, module.SheetEntry, module.RegionOverlay,
                     module.SwitchButton, module.FlowLayout):
        with pytest.raises(RuntimeError, match="PySide6 is not installed"):
            factory(None)


# ---------------------------------------------------------------------------
# G3: the tick box's look, from the tokens
# ---------------------------------------------------------------------------

def test_g3_the_tick_box_is_an_ink_square_that_takes_an_ink_check():
    """Updated (E, H10): ticked, the square keeps the sheet fill and an ink
    check is painted on it (`TickBox`), where it used to fill solid ink - a
    filled square read as a lamp."""
    sheet = qt.stylesheet()
    square = sheet.split("QCheckBox::indicator {")[1].split("}")[0]
    assert f"border: 1px solid {theme.TEXT}" in square
    assert _contrast(theme.TEXT, theme.BACKGROUND) >= 3.0
    ticked = sheet.split("QCheckBox::indicator:checked {")[1].split("}")[0]
    assert f"background-color: {theme.BACKGROUND}" in ticked
    assert "QCheckBox::indicator:disabled" in sheet
    assert issubclass(qt.TickBox, qt.QCheckBox) and "paintEvent" in vars(qt.TickBox)


def test_g3_the_tick_box_shows_focus_in_ink_round_the_whole_control():
    sheet = qt.stylesheet()
    focus = sheet.split("QCheckBox:focus {")[1].split("}")[0]
    assert qt.FOCUS_RING in focus


def test_g3_the_tick_box_follows_the_launch_font_size():
    original = theme.FONT_SIZE
    try:
        theme.set_font_size(24)
        square = qt.stylesheet().split("QCheckBox::indicator {")[1].split("}")[0]
        assert "width: 32px" in square and "height: 32px" in square
    finally:
        theme.set_font_size(original)


# ---------------------------------------------------------------------------
# G5: no platform-specific UI (owner ruling 2026-09-25)
# ---------------------------------------------------------------------------

def _import_on(platform, monkeypatch):
    """qt.py executed afresh as if on `platform`."""
    monkeypatch.setattr(sys, "platform", platform)
    spec = importlib.util.spec_from_file_location(f"_qt_on_{platform}",
                                                  qt.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_g5_one_stop_chord_the_physical_control_key_on_every_os(platform,
                                                                monkeypatch):
    """One binding, read "Ctrl+." everywhere. Qt calls the macOS Control key
    "Meta" and the Command key "Ctrl", so on macOS the binding is spelled
    "Meta+." - the same physical keys, never Command+period."""
    module = _import_on(platform, monkeypatch)
    assert module.STOP_SHORTCUT_TEXT == "Ctrl+."
    assert module.STOP_SHORTCUT == ("Meta+." if platform == "darwin" else "Ctrl+.")
    assert not hasattr(module, "STOP_SHORTCUTS")          # no second chord
    assert module.QtDashboard.stop_shortcut_text() == "Ctrl+."


def test_g5_the_view_shows_no_command_key_and_branches_on_the_os_once():
    """No string the operator can see names the Command key, and the only
    platform test left is the Control key's name in Qt's spelling."""
    import ast
    tree = ast.parse(open(qt.__file__).read())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)):
                docstrings.add(id(body[0].value))
    shown = [node.value for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and id(node) not in docstrings]
    for text in shown:
        assert "⌘" not in text and "Cmd" not in text, text
        assert "Command" not in text, text
    platform_tests = [node for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute)
                      and node.attr == "platform"
                      and isinstance(node.value, ast.Name)
                      and node.value.id == "sys"]
    assert len(platform_tests) == 1
    assert "darwin" in shown


# ---------------------------------------------------------------------------
# E (2026-09-25): the Bench sheet, tiered - the view's pure rules
# ---------------------------------------------------------------------------

def test_e_the_opened_model_is_alone_on_top_then_rows_of_three_and_two():
    names = ["Stepper Probe", "DC Probe", "Chuck Positioner",
             "Temperature Controller", "Rotator", "Red Percent"]
    assert qt.entry_rows(names, "Stepper Probe", 3) == [
        ["Stepper Probe"], ["DC Probe", "Chuck Positioner", "Temperature Controller"],
        ["Rotator", "Red Percent"]]
    # A press on Red Percent brings it to the top; the rest keep their order.
    assert qt.entry_rows(names, "Red Percent", 3)[0] == ["Red Percent"]
    assert qt.entry_rows(names, "Red Percent", 2)[1:] == [
        ["Stepper Probe", "DC Probe"], ["Chuck Positioner", "Temperature Controller"],
        ["Rotator"]]
    assert qt.entry_rows(names, None, 1) == [[n] for n in names]
    assert qt.entry_rows(["A", "B", "C", "D", "E"], "A", 3) == [["A"], ["B", "C"], ["D", "E"]]
    assert qt.entry_rows([], None, 3) == []


def test_k4_the_overview_grid_has_no_leading_row():
    """K4: the overview is the grid alone - `opened=None` (or a name that is
    not running) leads with no model; three across then the rest, the earlier
    rows the fuller."""
    names = ["Stepper Probe", "DC Probe", "Chuck Positioner",
             "Temperature Controller", "Rotator", "Red Percent"]
    assert qt.entry_rows(names, None, 3) == [names[:3], names[3:]]
    assert qt.entry_rows(names[:5], None, 3) == [names[:3], names[3:5]]
    assert qt.entry_rows(names, None, 2) == [names[:2], names[2:4], names[4:]]
    assert qt.entry_rows(names, "Gone", 3) == [names[:3], names[3:]]


def test_k4_the_pages_are_named_in_the_views_own_words():
    """The rail says "Overview"; an overview entry's affordance says "Open"."""
    assert qt.OVERVIEW == "Overview"
    assert qt.OPEN_WORD == "Open"


def test_k4_an_entry_head_has_the_ink_ring_and_a_lift_only_when_pressable():
    sheet = qt.stylesheet()
    rule = sheet.split("QFrame#entryHead:focus")[1].split("}")[0]
    assert qt.FOCUS_RING in rule
    assert 'QFrame#entryHead[pressable="true"]:hover' in sheet
    assert "QToolButton#entryOpen {" in sheet


def test_e_a_label_splits_off_its_unit_but_keeps_a_word():
    assert qt.split_unit("Position (deg):") == ("Position", "deg")
    assert qt.split_unit("Brake Distance (steps):") == ("Brake distance", "steps")
    assert qt.split_unit("Brake Speed (Slow):") == ("Brake speed (Slow)", "")
    assert qt.split_unit("Velocity (x, y, z):") == ("Velocity (x, y, z)", "")
    assert qt.split_unit("Setpoint:", "C") == ("Setpoint", "°C")


def test_e_axis_readings_share_one_caption_and_the_rest_are_primary_then_secondary():
    probe = sch.schema(
        sch.section("Position", *[sch.readonly(f"{a}:", f"position_{a.lower()}", rail=True)
                                  for a in "XYZ"]),
        sch.section("Diagnostics", sch.readonly("Velocity:", "v"), tier=3))
    kinds = qt.reading_kinds(probe)
    assert sorted(kinds.values()) == ["axis", "axis", "axis"]
    assert [qt.axis_letter(e) for e in probe["sections"][0]["elements"]] == ["X", "Y", "Z"]
    red = sch.schema(sch.section("Live", sch.readonly("Current Red:", "r", rail=True),
                                 sch.readonly("Red Change:", "c", rail=True),
                                 sch.readonly("Running:", "running")))
    elements = red["sections"][0]["elements"]
    kinds = qt.reading_kinds(red)
    assert [kinds.get(id(e)) for e in elements] == ["primary", "secondary", None]


def test_e_a_rail_reading_outside_tier_one_is_not_drawn_at_reading_size():
    schema = sch.schema(sch.section("Live", sch.readonly("A:", "a")),
                        sch.section("Stats", sch.readonly("Rows:", "rows", rail=True),
                                    tier=2, disclosure="Details"))
    assert qt.reading_kinds(schema) == {}


@pytest.mark.parametrize("value", ["Connected", "Idle", "No", "None", "--",
                                   "Not recording", "", False, "False"])
def test_e_a_normal_value_is_not_drawn_in_tier_one(value):
    assert qt.is_normal_value(value) is True


@pytest.mark.parametrize("value", ["0", "0.00", "Yes", True, "Moving", "lost"])
def test_e_an_abnormal_value_is_drawn(value):
    assert qt.is_normal_value(value) is False


def test_e_a_tier_names_its_own_disclosure_or_takes_the_themes():
    sections = [sch.section("Live", sch.readonly("A:", "a")),
                sch.section("Stats", sch.readonly("B:", "b"), tier=2, disclosure="Details"),
                sch.section("Diag", sch.readonly("C:", "c"), tier=3)]
    assert qt.disclosure_text(sections, 2) == "Details"
    assert qt.disclosure_text(sections, 3) == theme.TIER_LABELS[3] == "Diagnostics"
    assert qt.disclosure_text(sections[:1], 2) == theme.TIER_LABELS[2]
    assert [qt.tier_of(s) for s in sections] == [1, 2, 3]


def test_e_the_rail_says_simulation_only_when_nothing_is_real_hardware():
    sim = {"devices": {"SerialPort": "simulated", "Gamepad": "unbound"}}
    real = {"devices": {"SerialPort": "verified"}}
    screen = {"devices": {"Screen": "capturing"}}
    assert qt.simulation_line({"A": sim, "B": screen, "C": {}}) == qt.SIM_LINE
    assert qt.simulation_line({"A": sim, "B": real}) == "Simulated: A"
    assert qt.simulation_line({"B": real, "C": screen}) == ""
    assert qt.simulation_line({}) == ""


def test_mod5_the_rail_reads_only_the_declared_hardware_links():
    """MOD-5: with `hardware_devices` in the state, only those devices count;
    an empty list means none; a state without it reads every device."""
    declared = {"devices": {"PiezoLink": "verified", "Screen": "simulated"},
                "hardware_devices": ["PiezoLink"]}
    sim = {"devices": {"SerialPort": "simulated"}, "hardware_devices": ["SerialPort"]}
    none = {"devices": {"SerialPort": "simulated"}, "hardware_devices": []}
    assert qt.simulation_line({"A": declared}) == ""
    assert qt.simulation_line({"A": sim, "B": declared}) == "Simulated: A"
    assert qt.simulation_line({"A": none}) == ""
    assert qt.simulation_line({"A": {"devices": {"SerialPort": "simulated"}}}) == qt.SIM_LINE


def test_e_the_well_the_strip_and_the_slider_are_drawn_from_the_theme():
    """Signature (2026-09-27): the tier-3 strip is the DEEP pocket (radius
    `pocket`), not a muted left rule; the slider's handle is the fader cap -
    CAP face, KEY_RIM rim, a 3 px KEY_LIP lip, radius `fader` - on a
    `FADER["groove"]` (6 px) sunk groove, not an ink knob on a 4 px one."""
    sheet = qt.stylesheet()
    well = sheet.split("QFrame#well {")[1].split("}")[0]
    assert f"background-color: {theme.SURFACE}" in well
    assert f"border-radius: {theme.RADIUS['tray']}px" in well
    strip = sheet.split("QFrame#diagnostics {")[1].split("}")[0]
    assert f"background-color: {theme.DEEP}" in strip
    assert f"border-radius: {theme.RADIUS['pocket']}px" in strip
    assert "border-left" not in strip
    handle = sheet.split("QSlider::handle:horizontal {")[1].split("}")[0]
    assert f"background: {theme.CAP}" in handle
    assert f"solid {theme.KEY_RIM}" in handle
    assert f"border-bottom: {theme.KEY_LIP_PX['small']}px solid {theme.KEY_LIP}" in handle
    assert f"border-radius: {theme.RADIUS['fader']}px" in handle
    fill = sheet.split("QSlider::sub-page:horizontal {")[1].split("}")[0]
    assert f"background: {theme.TEXT}" in fill
    groove = sheet.split("QSlider::groove:horizontal {")[1].split("}")[0]
    assert f"height: {theme.FADER['groove']}px" in groove
    assert f"background: {theme.SURFACE}" in groove


def test_e_go_is_ink_filled_and_disabled_is_a_dashed_muted_edge():
    """Signature (2026-09-27): `go` is the ink key with a CAP legend (was
    the sheet's tone) and a GO_LIP; a disabled key keeps its silhouette in
    ghost tones - a 1.5 px dashed EDGE rim, a 4 px EDGE_SOFT lip and the
    DISABLED legend (was a 1 px dashed muted edge, flat)."""
    sheet = qt.stylesheet()
    go = sheet.split('QPushButton[role="go"] {')[1].split("}")[0]
    assert f"background-color: {theme.TEXT}" in go
    assert f"color: {theme.CAP}" in go
    go_edge = sheet.split('QPushButton[role="go"] {')[2].split("}")[0]
    assert f"border-bottom-color: {theme.GO_LIP}" in go_edge
    disabled = sheet.split("QPushButton:disabled {")[1].split("}")[0]
    assert f"color: {theme.DISABLED[1]}" in disabled
    assert f"border: {theme.KEY_RIM_PX:g}px dashed {theme.EDGE}" in disabled
    assert f"border-bottom: {theme.KEY_LIP_PX['key']}px solid {theme.EDGE_SOFT}" in disabled
    assert "background-color: transparent" in disabled


def test_e_a_lamp_is_ink_never_the_trace():
    """The trace is for changing numbers only: a lit lamp is ink (a fault's
    is signal)."""
    connected = sch.indicator("Stage connected", "is_connected",
                              on_role="go", off_role="danger")
    assert qt.lamp_colours(connected, True) == (theme.TEXT, theme.TEXT)
    assert theme.TRACE not in qt.lamp_colours(connected, True)


# ---------------------------------------------------------------------------
# L (2026-09-26): audit round 7 in the Qt view.
# ---------------------------------------------------------------------------

class _Event:
    def __init__(self, title, message, source="Controller", count=1):
        self.title, self.message, self.source, self.count = title, message, source, count
        self.text = f"[{source}] {title}: {message}"


def test_l11_an_event_line_is_its_title_and_message_in_sentence_case():
    """QT7-12: the band printed "Error [Controller] Stop Not Confirmed: ...".
    The view draws the one wording `views.base.event_line` gives all three:
    no source prefix, the title in sentence case."""
    from views import base
    assert qt.event_line is base.event_line
    line = qt.event_line(_Event("Stop Not Confirmed", "Rotator did not confirm the stop."))
    assert line == "Stop not confirmed: Rotator did not confirm the stop."
    assert "[" not in qt.event_line(_Event("Port Silent", "no answer", count=3))


def test_l9_l14_the_questions_are_the_tk_and_web_words():
    assert qt.QUIT_PROMPT == ("Quit the station? This stops every model, closes "
                              "every port and exits.")
    assert qt.QUIT_WORDS == ("Quit the station?", "Quit", "Stay")
    assert qt.CLEAR_WORDS == ("Clear the stop?", "Clear the stop", "Keep it stopped")


def test_l22_the_window_title_is_sentence_case():
    assert 'setWindowTitle("Transfer stage")' in _qt_source()


def _qt_source():
    import pathlib
    return pathlib.Path(qt.__file__).read_text(encoding="utf-8")


def test_l3_a_gate_says_why_in_the_operators_words():
    """Updated (O3): the words are `views.base.gate_reason`'s, which reads the
    element's own gate lists. Stop run while latched is waiting for a run
    ("No run in progress": latched is not in its `disabled_when`), and an
    unknown token reads "In <token> mode" as in Tk and Web."""
    start = sch.button("Start run", "start", role="go",
                       disabled_when=("running", "latched", "no_region"))
    stop = sch.button("Stop run", "end", enabled_when=("running",))
    step = sch.button("Step", "step", disabled_when=("manual", "latched"))
    assert qt.gate_reason(start, "no_region") == "Set a capture region first"
    assert qt.gate_reason(start, "latched") == "Stopped: clear the stop first"
    assert qt.gate_reason(start, "running") == "A run is in progress"
    assert qt.gate_reason(start, "idle") == ""
    assert qt.gate_reason(stop, "idle") == "No run in progress"
    assert qt.gate_reason(stop, "latched") == "No run in progress"
    assert qt.gate_reason(step, "manual") == "In manual mode"
    port = sch.dropdown("Port", "port", "set_port", "ports", enabled_by="on")
    assert qt.gate_reason(port, "idle", {"on": False}, lambda _: "Launch") == (
        "Tick Launch first")
    relaunch = sch.button("Relaunch", "relaunch", role="go", enabled_when=("launched",))
    assert qt.gate_reason(relaunch, "idle") == "Nothing launched yet"
    odd = sch.button("Odd", "odd", disabled_when=("warming_up",))
    assert qt.gate_reason(odd, "warming_up") == "In warming_up mode"


# ---------------------------------------------------------------------------
# N and O (2026-09-26): the idle countdown, the quit prompt, round 8.
# ---------------------------------------------------------------------------

def test_o3_the_gate_words_are_views_bases_and_the_local_table_is_gone():
    """IMP8-1 / ARCH: three copies of the gate table, and Web and Tk said "Not
    in manual mode" while the probe WAS in manual mode. Qt reads
    `views.base.gate_reason`; its own table is deleted."""
    from views import base
    assert not hasattr(qt, "GATE_WORDS") and not hasattr(qt, "WAITING_WORDS")
    step = sch.button("Step", "step", disabled_when=("manual", "latched"))
    assert qt.gate_reason(step, "manual") == "In manual mode"
    assert qt.gate_reason(step, "manual") == base.gate_reason(step, "manual")
    jog = sch.button("Jog", "jog", enabled_when=("manual",))
    assert qt.gate_reason(jog, "idle") == "Not in manual mode"
    auto = sch.button("Auto only", "auto", disabled_when=("autonomous",))
    assert qt.gate_reason(auto, "autonomous") == "In autonomous mode"
    # The one rule base has no word for stays the view's: a control live
    # only while a tick box is ticked names the box.
    port = sch.dropdown("Port", "port", "set_port", "ports", enabled_by="on")
    assert qt.gate_reason(port, "idle", {"on": False}, lambda _: "Launch") == (
        "Tick Launch first")


def test_n2_the_countdown_lists_the_probes_inside_the_window_in_station_order():
    states = {
        "Stepper Probe": {"idle_remaining": 42.0, "idle_warn_seconds": 60},
        "DC Probe": {"idle_remaining": 12.3},                 # no threshold: 60
        "Chuck Positioner": {"idle_remaining": 200.0, "idle_warn_seconds": 60},
        "Temperature Controller": {"mode": "idle"},           # not a probe
        "Rotator": {"idle_remaining": None, "idle_warn_seconds": 60},
    }
    names = ["Chuck Positioner", "DC Probe", "Temperature Controller",
             "Stepper Probe", "Rotator"]
    assert qt.idle_countdowns(names, states) == [("DC Probe", 13),
                                                 ("Stepper Probe", 42)]
    assert qt.countdown_text("Stepper Probe", 42) == "Stepper Probe powers down in 42 s."
    assert qt.EXTEND_WORD == "Extend"


def test_n4_the_quit_prompt_names_what_is_energized():
    assert qt.quit_prompt([]) == qt.QUIT_PROMPT
    assert qt.quit_prompt(["Stepper Probe", "Temperature Controller"]) == (
        "Quit the station? Stepper Probe and Temperature Controller are "
        "energized; quitting stops and disconnects them.")
    assert qt.quit_prompt(["Rotator"]) == (
        "Quit the station? Rotator is energized; quitting stops and "
        "disconnects it.")


def test_o4_o6_o16_the_rail_mark_is_one_state_per_model_with_its_words():
    """A model that did not confirm outranks a fault, a fault outranks a plain
    latch; the words ride with the mark (never colour alone)."""
    stop = {"latched": ["Rotator", "DC Probe"], "unconfirmed": ["Rotator"]}
    assert qt.rail_mark("Rotator", stop, faulted=set()) == "unconfirmed"
    assert qt.rail_mark("DC Probe", stop, faulted={"DC Probe"}) == "faulted"
    assert qt.rail_mark("DC Probe", stop, faulted=set()) == "stopped"
    assert qt.rail_mark("Stepper Probe", stop, faulted={"Stepper Probe"}) == "faulted"
    assert qt.rail_mark("Stepper Probe", stop, faulted=set()) is None
    # Updated (rb-link-views V1): the rail line also carries the entry's
    # link tier - a link lost or reconnecting, a stalled link - with words.
    assert qt.RAIL_STOP_WORDS == {"stopped": "stopped",
                                  "unconfirmed": "did not confirm",
                                  "faulted": "faulted", "lost": "link lost",
                                  "attention": "needs attention"}
    assert qt.ENERGIZED_WORD == "energized"


def test_o4_a_fault_is_said_like_an_unconfirmed_stop():
    from views import base
    assert qt.FAULT_LINE == "Disable failed. Treat as live."
    assert qt.FAULT_GATE == base.GATE_WORDS["fault"][0] == (
        "Faulted: clear the fault first")


def test_o9_closing_a_model_asks_in_the_tk_and_web_words():
    assert qt.close_model_words("Rotator") == (
        "It stops and disconnects Rotator. You can reopen it from the rail.",
        "Close Rotator?", "Close Rotator", "Keep it open")


def test_o13_the_idle_warning_is_history_in_the_log_not_the_tray_line():
    assert qt.HISTORY_ONLY_TITLES == frozenset({"Idle Timeout Soon"})


class _Box:
    """A QLineEdit's `text()` and nothing else: no QApplication needed."""

    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


def test_mod6_qt_sends_the_declared_inputs_and_every_edited_box_only():
    """MOD-6 / CON-8, Qt's half: (a) a declared input travels clean, (b) an
    undeclared clean box stays home, (c) an undeclared edited box travels
    even though it is not focused - Qt's refresh rule (`_entry_is_dirty`)
    wants focus, and a click on a button can take it."""
    import types
    from views.base import PanelView
    boxes = {"speed": _Box("5"), "steps": _Box("9"), "note": _Box("typed")}
    clean = {"speed": "5", "steps": "9", "note": "as served"}
    elements = [{"type": "entry", "model_attr": k, "writable": True} for k in boxes]
    stub = types.SimpleNamespace(_elements=elements,
                                 _widget_for=lambda e: boxes[e["model_attr"]],
                                 _clean_text={id(e): clean[e["model_attr"]]
                                              for e in elements})
    stub._read_entry = lambda e: qt.QtPanelView._read_entry(stub, e)
    stub._entry_is_edited = lambda e: qt.QtPanelView._entry_is_edited(stub, e)
    go = {"type": "button", "command": "go", "inputs": ["speed"]}
    assert PanelView._gather_inputs(stub, go) == {"speed": "5", "note": "typed"}
    assert PanelView._gather_inputs(stub, {"type": "button", "command": "halt"}) \
        == {"note": "typed"}


# ---------------------------------------------------------------------------
# Signature (owner ruling 2026-09-27): the key family, the fields, the stop's
# geometry, the glyphs. `handoff/tactile3-Signature.md`, "Controls", "The
# stop", "Per toolkit"; the tokens in `views.theme`.
# ---------------------------------------------------------------------------

def _block(sheet, selector, index=1):
    return sheet.split(f"{selector} {{")[index].split("}")[0]


def test_signature_a_key_is_a_face_a_rim_and_a_lip():
    """Every raised part is one family (rule 2): a CAP face, a 1.5 px
    KEY_RIM outline, a 4 px KEY_LIP bottom edge, radius 8, legend 600."""
    key = _block(qt.stylesheet(), "QPushButton")
    assert f"background-color: {theme.CAP}" in key
    assert f"border: {theme.KEY_RIM_PX:g}px solid {theme.KEY_RIM}" in key
    assert f"border-bottom: {theme.KEY_LIP_PX['key']}px solid {theme.KEY_LIP}" in key
    assert f"border-radius: {theme.RADIUS['key']}px" in key
    assert "font-weight: 600" in key


def test_signature_a_pressed_key_folds_its_lip_and_drops_onto_its_ground():
    """Pressed or checked: the lip folds to 1 px and the top edge becomes
    3 px of the ground, so the face drops and the height holds; inside a
    tray the ground is SURFACE, in the pocket DEEP, on the rail RAIL."""
    sheet = qt.stylesheet()
    down = _block(sheet, "QPushButton:pressed, QPushButton:checked")
    drop = theme.KEY_LIP_PX["key"] - theme.KEY_LIP_PX["pressed"]
    assert drop == 3 == qt.key_drop()
    assert f"border-top: {drop}px solid {theme.BACKGROUND}" in down
    assert f"border-bottom: {theme.KEY_LIP_PX['pressed']}px solid {theme.KEY_LIP}" in down
    for container, ground in (("QFrame#well", theme.SURFACE),
                              ("QFrame#diagnostics", theme.DEEP),
                              ("QFrame#rail", theme.RAIL)):
        rule = sheet.split(f"{container} QPushButton:pressed")[1].split("}")[0]
        assert f"border-top-color: {ground}" in rule, container


def test_signature_focus_rings_the_key_and_keeps_its_lip():
    focus = _block(qt.stylesheet(), "QPushButton:focus")
    assert qt.FOCUS_RING in focus
    assert f"border-bottom: {theme.KEY_LIP_PX['key']}px solid {theme.KEY_LIP}" in focus


def test_signature_the_mode_key_latched_is_the_ink_key_down():
    """The latching mode key: off, a neutral key; on, the ink face DOWN (lip
    folded to 1 px, 3 px drop) - told from `go` by its missing lip and its
    lit lamp slot. The drop is drawn in the ground the key sits on."""
    element = sch.toggle("Manual", "is_manual", "toggle_manual",
                         true_text="Leave manual mode", false_text="Enter manual mode")
    off = qt.toggle_sheet(element, False)
    rest = off.split("QPushButton {")[1].split("}")[0]
    assert f"background-color: {theme.CAP}" in rest
    assert f"border-bottom: {theme.KEY_LIP_PX['key']}px solid {theme.KEY_LIP}" in rest
    on = qt.toggle_sheet(element, True, theme.SURFACE)
    rest = on.split("QPushButton {")[1].split("}")[0]
    assert f"background-color: {theme.TEXT}" in rest and f"color: {theme.CAP}" in rest
    assert f"border-top: 3px solid {theme.SURFACE}" in rest
    assert f"border-bottom: 1px solid {theme.GO_LIP}" in rest
    disabled = on.split("QPushButton:disabled {")[1].split("}")[0]
    assert f"dashed {theme.EDGE}" in disabled and f"solid {theme.EDGE_SOFT}" in disabled
    assert theme.SIGNAL not in off + on and theme.TRACE not in off + on


def test_signature_a_field_is_a_sunk_window_and_the_select_is_a_key():
    sheet = qt.stylesheet()
    field = _block(sheet, "QLineEdit")
    assert f"background-color: {theme.WELL}" in field
    assert f"border-radius: {theme.RADIUS['input']}px" in field
    assert f"font-family: {qt.numeral_family()}" in field
    assert f"background-color: {theme.DEEP}" in _block(sheet, "QFrame#well QLineEdit")
    assert qt.FOCUS_RING in sheet.split("QLineEdit:focus")[1].split("}")[0]
    select = _block(sheet, "QComboBox")
    assert f"background-color: {theme.CAP}" in select
    assert f"border-bottom: {theme.KEY_LIP_PX['key']}px solid {theme.KEY_LIP}" in select
    assert "image: none" in _block(sheet, "QComboBox::down-arrow")


def test_signature_the_rail_is_its_own_face_and_the_shown_page_is_a_sunk_pad():
    sheet = qt.stylesheet()
    assert f"background-color: {theme.RAIL}" in _block(sheet, "QFrame#rail")
    shown = _block(sheet, "QPushButton#railModel:checked")
    assert f"background: {theme.SURFACE}" in shown
    item = _block(sheet, "QPushButton#railModel")
    assert f"padding-left: {qt.rail_text_left()}px" in item
    assert qt.rail_text_left() > theme.INSET + theme.LAMP["size_rail"][0]


def test_signature_the_stop_geometry_follows_the_theme():
    """172 / 10 / 124 with a 6 px skirt, 4 px lift and 6 px latched drop;
    150 / 9 / 106 narrow; a squeezed rail scales the nearer set."""
    full = qt.stop_geometry(theme.STOP["diameter"])
    assert (full["collar"], full["key"], full["skirt"], full["lift"], full["drop"]) == (
        theme.STOP["collar"], theme.STOP["key"], theme.STOP["skirt"],
        theme.STOP["lift"], theme.STOP["drop_latched"])
    assert full["legend_px"] == theme.STOP["face_pt"]
    narrow = qt.stop_geometry(theme.STOP["diameter_narrow"])
    assert (narrow["collar"], narrow["key"]) == (theme.STOP["collar_narrow"],
                                                 theme.STOP["key_narrow"])
    squeezed = qt.stop_geometry(theme.STOP["diameter_narrow"] // 2)
    assert squeezed["key"] == theme.STOP["key_narrow"] / 2


def test_signature_keys_carry_the_specs_glyphs_before_their_legend():
    assert qt.key_glyph("Home") == "home"
    assert qt.key_glyph("Start run") == "run"
    assert qt.key_glyph("Save run…") == "download"
    assert qt.key_glyph("Gamepad log…") == "gamepad"
    assert qt.key_glyph("3D analysis plot…") == "link"
    assert qt.key_glyph("Move +") is None and qt.key_glyph("") is None
    assert {name for _, name in qt.KEY_GLYPHS} <= set(theme.ICON_NAMES)


def test_signature_the_numerals_are_rubik_semibold():
    assert qt.numeral_family() == f"{theme.NUMERAL_FAMILY} SemiBold"
    assert f"font-weight: {theme.NUMERAL_WEIGHT}" in _block(qt.stylesheet(), "QLabel#reading")


def test_signature_axis_letters_are_14_px_700_muted():
    rule = _block(qt.stylesheet(), "QLabel#axisLetter")
    assert "font-weight: 700" in rule and f"color: {theme.MUTED}" in rule
    assert f"font-size: {qt.axis_pt()}pt" in rule


# ---------------------------------------------------------------------------
# The region picker draws on a screenshot (bench 2026-09-27, bare X11)
# ---------------------------------------------------------------------------

def _desktop_png(width=800, height=450):
    """A synthetic two-colour desktop: red on the left, blue on the right."""
    import io
    from PIL import Image
    image = Image.new("RGB", (width, height), (0, 0, 255))
    image.paste((255, 0, 0), (0, 0, width // 2, height))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _PickingView:
    """The surface `_pick_region` and `_region_screenshot` touch."""

    def __init__(self, result=None, raises=None):
        from result import Result
        self.name = "Red Percent"
        self.calls = []
        self.ran = []
        self._result = result if result is not None else Result(Result.OK, value=None)
        self._raises = raises

    def _call(self, command, inputs=None, args=()):
        self.calls.append(command)
        if self._raises is not None:
            raise self._raises
        return self._result

    def _run(self, element, args=()):
        self.ran.append((element["command"], tuple(args)))

    def _region_screenshot(self, element):
        return qt.QtPanelView._region_screenshot(self, element)


def _region_element(data_command="screen_image"):
    return sch.region_select("Set Capture Region", "set_region",
                             model_attr="region", data_command=data_command)


def test_the_qt_region_click_takes_the_picture_before_the_overlay_opens(monkeypatch):
    from result import Result
    shot = {"image": _desktop_png(), "left": 0, "top": 0, "width": 800, "height": 450}
    view = _PickingView(Result(Result.OK, value=shot))
    seen = []

    class Overlay:
        def __init__(self, on_region, parent=None, screenshot=None):
            seen.append(("built", list(view.calls), screenshot))
            self.on_region = on_region

        def show(self):
            seen.append(("shown",))

    monkeypatch.setattr(qt, "RegionOverlay", Overlay)
    element = _region_element()
    qt.QtPanelView._pick_region(view, element)
    assert seen == [("built", ["screen_image"], shot), ("shown",)]
    view._overlay.on_region(10, 20, 30, 40)
    assert view.ran == [("set_region", (10, 20, 30, 40))]


@pytest.mark.parametrize("case", ["no_command", "no_capture", "refused", "raises"])
def test_with_no_picture_the_qt_overlay_gets_none(case):
    from result import Result
    element = _region_element(None if case == "no_command" else "screen_image")
    view = {"no_command": _PickingView(),
            "no_capture": _PickingView(Result(Result.OK, value=None)),
            "refused": _PickingView(Result(Result.REFUSED, reason="no")),
            "raises": _PickingView(raises=RuntimeError("mss gone"))}[case]
    assert view._region_screenshot(element) is None
    assert view.calls == ([] if case == "no_command" else ["screen_image"])


@pytest.mark.qt
def test_the_overlay_over_a_screenshot_is_opaque_and_holds_the_picture(qapp):
    from PySide6.QtCore import Qt
    shot = {"image": _desktop_png(), "left": 0, "top": 0, "width": 800, "height": 450}
    overlay = qt.RegionOverlay(lambda *a: None, screenshot=shot)
    try:
        assert not overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert overlay.picture is not None and not overlay.picture.isNull()
        assert (overlay.picture.width(), overlay.picture.height()) == (800, 450)
        assert "drag" in overlay.instruction_label.text().lower()
        # Painted, the picture is under the tint: the left half reads red.
        overlay.resize(400, 225)
        image = overlay.grab().toImage()
        colour = image.pixelColor(10, image.height() - 10)
        assert colour.red() > 150 and colour.red() > colour.blue() + 80
    finally:
        overlay.close()


@pytest.mark.qt
def test_the_overlay_draws_the_picture_one_to_one_not_stretched_to_itself(qapp):
    """The bench (2026-09-28, Linux, Qt): the window manager kept the overlay
    out of a panel and the picture was stretched to it - "squished ... mild
    offset". Now the picture keeps the desktop's size and is anchored where
    the desktop's origin falls in the overlay."""
    from PySide6.QtCore import QRect
    shot = {"image": _desktop_png(), "left": 0, "top": 0, "width": 800, "height": 450}
    overlay = qt.RegionOverlay(lambda *a: None, screenshot=shot)
    try:
        overlay.desktop = (0, 0, 800, 450)
        overlay.setGeometry(0, 40, 800, 410)          # kept below a 40-px panel
        assert overlay.picture_rect() == QRect(0, -40, 800, 450)
        overlay.setGeometry(0, 0, 800, 450)
        assert overlay.picture_rect() == QRect(0, 0, 800, 450)
        # A capture twice the logical desktop (a Retina Mac) is halved.
        overlay.bounds = dict(shot, width=1600, height=900)
        assert overlay.picture_rect() == QRect(0, 0, 800, 450)
        # A second monitor to the left: the overlay on the primary only.
        overlay.bounds = dict(shot, left=-800, width=1600)
        overlay.desktop = (-800, 0, 1600, 450)
        assert overlay.picture_rect() == QRect(-800, 0, 1600, 450)
    finally:
        overlay.close()


@pytest.mark.qt
@pytest.mark.parametrize("screenshot", [None, {"image": b"not a png", "width": 1,
                                               "height": 1}])
def test_without_a_usable_screenshot_the_overlay_stays_translucent(qapp, screenshot):
    from PySide6.QtCore import Qt
    overlay = qt.RegionOverlay(lambda *a: None, screenshot=screenshot)
    try:
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert overlay.picture is None
    finally:
        overlay.close()
