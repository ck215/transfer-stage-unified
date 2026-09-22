"""The Qt view's toolkit-free half: the pure helpers and the build contract.

Nothing here needs a QApplication, so these run in the ordinary gate. The
widget behaviour is in `test_view_qt_widgets.py`, every test of which is
`qt`-marked and run by the lead.
"""
import importlib.util
import re
import sys

import pytest

from station import schema as sch
from station.views import qt, theme


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


# ---------------------------------------------------------------------------
# Styling: theme only
# ---------------------------------------------------------------------------

def test_the_stylesheet_uses_only_theme_colours():
    known = {theme.BACKGROUND, theme.SURFACE, theme.TEXT, theme.MUTED}
    known |= {value for pair in theme.ROLES.values() for value in pair}
    known |= set(theme.DISABLED)
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
    """`station.app` picks a view at runtime, and importing the view module
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
