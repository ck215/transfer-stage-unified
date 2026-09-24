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
    thirteen element builders carries a layout branch. Both containers answer
    the same two calls and say how wide a control in them should be."""
    for container in (qt.ColumnSection, qt.TableRow):
        assert callable(container.add) and callable(container.add_wide)
        assert isinstance(container.control_width, int)
    # A row's cells are sized; a column's controls size themselves.
    assert qt.TableRow.control_width > 0
    assert qt.ColumnSection.control_width == 0
    assert qt.TableRow.is_row is True and qt.ColumnSection.is_row is False


# ---------------------------------------------------------------------------
# Styling: theme only
# ---------------------------------------------------------------------------

def test_the_stylesheet_uses_only_theme_colours():
    """Updated (F23): the sheet now also names the theme's rules, well, lift
    and severity inks, and the one input border derived with `theme.mix`."""
    known = {theme.BACKGROUND, theme.SURFACE, theme.TEXT, theme.MUTED}
    known |= {value for pair in theme.ROLES.values() for value in pair}
    known |= set(theme.DISABLED)
    known |= {theme.RULE, theme.RULE_STRONG, theme.WELL, theme.LIFT,
              theme.STOP_FOCUS, qt.INPUT_BORDER}
    known |= set(theme.SEVERITY_INK.values())
    used = set(re.findall(r"#[0-9a-fA-F]{3,8}", qt.stylesheet()))
    assert used <= {c.lower() for c in known} | known, f"not from theme: {used - known}"


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
    for selector in ("QLabel#columnHeader", "QLabel#rowTitle", "QFrame#rail",
                     "QFrame#rail QLabel#railValue", "QDockWidget::title"):
        assert f"{selector} {{" in sheet, selector
    assert "QToolBar" not in sheet


def test_a_readout_and_an_entry_do_not_look_the_same():
    """The owner's "readouts distinct from entries": an entry is a bordered
    well sunk to the window colour, a readout is a number in the trace ink on
    the card (the brief: trace is what a live readout is drawn in). Updated:
    it asserted bold ink, the look before the one-red palette."""
    sheet = qt.stylesheet()
    entries = sheet.split("QLineEdit, QComboBox, QTextEdit {")[1].split("}")[0]
    readouts = sheet.split("QLabel#valueLabel {")[1].split("}")[0]
    assert f"background-color: {theme.BACKGROUND};" in entries
    assert "border: 1px solid" in entries
    assert f"color: {theme.TRACE};" in readouts
    assert "border" not in readouts


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
    run) renders as an ordinary command; the stop object dresses itself."""
    sheet = qt.stylesheet()
    assert theme.SIGNAL.lower() not in sheet.lower()
    danger = sheet.split('QPushButton[role="danger"] {')[1].split("}")[0]
    assert f"background-color: {theme.colors('neutral')[0]};" in danger


def test_the_type_scale_is_one_family_on_a_tight_ratio():
    """Base 12 pt, ratio 1.2: every size in the sheet is a step of it."""
    original = theme.FONT_SIZE
    try:
        theme.set_font_size(12)
        sizes = {int(n) for n in re.findall(r"font-size: (\d+)pt", qt.stylesheet())}
        assert sizes <= {10, 12, 14, 17}, sizes
        families = set(re.findall(r"font-family: ([^;]+);", qt.stylesheet()))
        assert families == {theme.FONT_FAMILY}
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
    the opposite direction to Tk's; it is gone, and so is `rgba`."""
    assert not hasattr(qt, "mix") and not hasattr(qt, "rgba")
    assert qt.INPUT_BORDER == theme.mix(theme.SURFACE, theme.TEXT, 0.40)


def test_an_input_border_is_at_least_3_to_1_on_the_card():
    """WCAG 1.4.11 (AUD-11): the well's border was 1.67:1 on the card."""
    assert _contrast(qt.INPUT_BORDER, theme.SURFACE) >= 3.0
    sheet = qt.stylesheet()
    entries = sheet.split("QLineEdit, QComboBox, QTextEdit {")[1].split("}")[0]
    assert f"border: 1px solid {qt.INPUT_BORDER}" in entries


def test_every_focusable_has_an_ink_ring_including_tabs_and_scroll_areas():
    sheet = qt.stylesheet()
    for selector in ("QTabBar::tab:focus", "QScrollArea#panelScroll:focus",
                     "QFrame#tray QTextEdit:focus", "QPushButton#ghost:focus",
                     "QPushButton#iconButton:focus"):
        rule = sheet.split(selector)[1].split("}")[0]
        assert qt.FOCUS_RING in rule, selector


def test_the_rail_readout_is_capped_one_step_up():
    """F25: two steps up made the 28 pt rail and tray a third of the window."""
    original = theme.FONT_SIZE
    try:
        theme.set_font_size(28)
        sheet = qt.stylesheet()
        value = sheet.split("QFrame#rail QLabel#railValue {")[1].split("}")[0]
        assert f"font-size: {theme.size(1)}pt" in value
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
    for factory in (module.SeriesPlot, module.DeviceDock, module.RegionOverlay):
        with pytest.raises(RuntimeError, match="PySide6 is not installed"):
            factory(None)
