"""`model.plot_data` — parsing a run CSV and deciding what to plot.

Ported from `tests/core/test_plot_data.py`,
`tests/core/test_redpercent17_plot_blank_figure_message.py` and the parser
half of `tests/core/test_redpercent_run_artifacts.py`.

`tests/conftest.py` replaces matplotlib with a `MagicMock`, so asserting on
Figure/Axes calls asserts on the mock. These assert on the two things that
are actually ours: what came out of the parser, and which series the plot
asked for (`_plot_request`).
"""
import json

import pytest

from model import plot_data


NEW_CSV = (
    "t_s,red_percent,x_position_steps,x_velocity_steps_per_s,"
    "y_position_steps,y_velocity_steps_per_s,position_age_s\n"
    "0.000,10.5,100,,200,,0.010\n"
    "0.002,11.5,100,,200,,0.012\n"
    "0.100,12.5,110,100.0,205,50.0,0.001\n"
)

LEGACY_CSV = (
    "# Metadata\n"
    "# Probe Name,TestProbe\n"
    "# Probe Tilt Angle,45\n"
    "\n"
    "Red Percent,Timestamp,Stepper X Location,Stepper X Velocity\n"
    "10.0,1234.5,1.0,0.5\n"
    "11.0,1235.5,2.0,0.5\n"
)


# --- parsing --------------------------------------------------------------

def test_parses_the_column_vocabulary_run_log_writes():
    result = plot_data.parse_run_csv(NEW_CSV)

    assert result["red_percents"] == [10.5, 11.5, 12.5]
    assert result["times"] == [0.0, 0.002, 0.1]
    assert result["dims"] == ["X", "Y"]
    assert result["dim_data"]["X"] == [100.0, 100.0, 110.0]
    assert result["position_ages"] == [0.01, 0.012, 0.001]
    assert result["dropped_rows"] == 0


def test_an_unmeasured_cell_is_none_and_the_row_survives():
    """REDPERCENT-16/ERRORS-7. A velocity is only measurable between two
    distinct position samples, so most rows have an empty velocity cell. That
    must not cost the row its red percent — and the empty cell must never be
    read back as a `0.0` anyone could mistake for a measurement."""
    result = plot_data.parse_run_csv(NEW_CSV)

    assert result["dim_velocity"]["X"] == [None, None, 100.0]
    assert 0.0 not in result["dim_velocity"]["X"]
    assert len(result["dim_velocity"]["X"]) == len(result["red_percents"])


def test_every_series_stays_the_same_length():
    result = plot_data.parse_run_csv(NEW_CSV)
    expected = len(result["red_percents"])
    for series in (result["times"], result["position_ages"],
                   *result["dim_data"].values(),
                   *result["dim_velocity"].values()):
        assert len(series) == expected


def test_a_row_with_no_readable_red_percent_is_dropped_and_counted():
    text = ("t_s,red_percent,x_position_steps\n"
            "0.0,10.5,1.0\n"
            "0.1,bad,2.0\n"
            "0.2,,3.0\n"
            "0.3,40.5,4.0\n")
    result = plot_data.parse_run_csv(text)

    assert result["red_percents"] == [10.5, 40.5]
    assert result["dim_data"]["X"] == [1.0, 4.0]
    assert result["dropped_rows"] == 2


def test_a_malformed_position_cell_blanks_the_cell_not_the_row():
    """The old parser dropped the whole row. At `every_frame` rates that
    throws away a perfectly good red sample because one position read
    failed."""
    text = ("t_s,red_percent,x_position_steps\n"
            "0.0,10.5,1.0\n"
            "0.1,20.5,bad\n"
            "0.2,30.5,3.0\n")
    result = plot_data.parse_run_csv(text)

    assert result["red_percents"] == [10.5, 20.5, 30.5]
    assert result["dim_data"]["X"] == [1.0, None, 3.0]


def test_a_legacy_csv_with_a_comment_block_still_loads():
    """REDPERCENT-22: files already on disk keep working. The parser drops
    '#' rows and finds the header by name."""
    result = plot_data.parse_run_csv(LEGACY_CSV)

    assert result["metadata"] == {"Probe Name": "TestProbe",
                                  "Probe Tilt Angle": "45"}
    assert result["red_percents"] == [10.0, 11.0]
    assert result["times"] == [1234.5, 1235.5]
    assert result["dims"] == ["X"]
    assert result["dim_data"]["X"] == [1.0, 2.0]
    assert result["dim_velocity"]["X"] == [0.5, 0.5]


def test_a_file_with_no_recognisable_header_parses_to_nothing():
    result = plot_data.parse_run_csv("# Probe Name,TestProbe\n\n"
                                     "Not Red Percent,Other\n10.5,1.0\n")
    assert result["red_percents"] == []
    assert result["dims"] == []
    assert result["dim_data"] == {}
    assert result["metadata"] == {"Probe Name": "TestProbe"}


def test_zero_synced_axes_parses():
    result = plot_data.parse_run_csv("t_s,red_percent\n0.0,10.5\n0.1,20.5\n")
    assert result["red_percents"] == [10.5, 20.5]
    assert result["dims"] == []


# --- load_run and the sidecar --------------------------------------------

def test_load_run_reads_the_sidecar_beside_the_autosave_name(tmp_path):
    (tmp_path / "C001_position.csv").write_text(NEW_CSV)
    (tmp_path / "C001_station_meta.json").write_text(
        json.dumps({"probe_name": "tip-3", "baseline_red": 4.25}))

    result = plot_data.load_run(tmp_path / "C001_position.csv")

    assert result["red_percents"] == [10.5, 11.5, 12.5]
    assert result["metadata"]["probe_name"] == "tip-3"
    assert result["metadata"]["baseline_red"] == pytest.approx(4.25)


def test_load_run_reads_a_sidecar_named_after_the_csv_stem(tmp_path):
    (tmp_path / "anything.csv").write_text(NEW_CSV)
    (tmp_path / "anything_station_meta.json").write_text(json.dumps({"a": 1}))

    assert plot_data.load_run(tmp_path / "anything.csv")["metadata"] == {"a": 1}


def test_a_corrupt_sidecar_never_makes_the_samples_unreadable(tmp_path):
    (tmp_path / "C001_position.csv").write_text(NEW_CSV)
    (tmp_path / "C001_station_meta.json").write_text("{not json")

    result = plot_data.load_run(tmp_path / "C001_position.csv")
    assert result["red_percents"] == [10.5, 11.5, 12.5]


# --- what gets plotted ----------------------------------------------------

def test_0d_plots_red_against_time_when_the_run_has_one():
    request = plot_data._plot_request("0D", None, None, None, [1.0, 2.0],
                                      {}, times=[0.0, 0.5])
    assert request["kind"] == "line"
    assert request["x"] == [0.0, 0.5]
    assert request["y"] == [1.0, 2.0]
    assert "s" in request["x_label"]


def test_0d_falls_back_to_sample_index_without_a_time_axis():
    request = plot_data._plot_request("0D", None, None, None, [1.0, 2.0], {})
    assert request["x"] == [0, 1]


def test_1d_sorts_by_position_and_never_says_stepper():
    request = plot_data._plot_request("1D", "X", None, None, [3.0, 1.0],
                                      {"X": [2.0, 0.0]})
    assert request["kind"] == "line"
    assert request["x"] == [0.0, 2.0]
    assert request["y"] == [1.0, 3.0]
    assert "Stepper" not in request["x_label"]
    assert "steps" in request["x_label"]


def test_1d_against_an_axis_the_run_never_recorded_explains_itself():
    request = plot_data._plot_request("1D", "Z", None, None, [1.0, 2.0],
                                      {"X": [0.1, 0.2]})
    assert request["kind"] == "message"
    assert "Z" in request["reason"]


def test_2d_missing_the_second_dimension_explains_itself():
    """REDPERCENT-17: this used to fall through every branch and return a
    Figure with no Axes — an indistinguishable blank PNG."""
    request = plot_data._plot_request("2D", "X", None, None, [1.0, 2.0],
                                      {"X": [0.1, 0.2]})
    assert request["kind"] == "message"
    assert "two dimensions" in request["reason"]


def test_3d_missing_the_third_dimension_explains_itself():
    request = plot_data._plot_request("3D", "X", "Y", None, [1.0, 2.0],
                                      {"X": [0.1, 0.2], "Y": [0.5, 0.6]})
    assert request["kind"] == "message"
    assert "three dimensions" in request["reason"]


def test_an_unknown_plot_type_explains_itself():
    request = plot_data._plot_request("bogus", "X", "Y", "Z", [1.0], {})
    assert request["kind"] == "message"
    assert "bogus" in request["reason"]


def test_a_run_with_no_samples_explains_itself():
    assert plot_data._plot_request("0D", None, None, None, [], {})["kind"] == "message"


def test_2d_keeps_only_the_samples_carrying_both_axes():
    """A row whose position read failed carries `None`, and pairing it with a
    red percent would plot a point that was never measured."""
    request = plot_data._plot_request(
        "2D", "X", "Y", None, [1.0, 2.0, 3.0],
        {"X": [0.1, None, 0.3], "Y": [0.5, 0.6, None]})

    assert request["kind"] == "scatter3d"
    assert request["x"] == [0.1]
    assert request["y"] == [0.5]
    assert request["c"] == [1.0]


def test_2d_with_nothing_left_after_filtering_explains_itself():
    request = plot_data._plot_request("2D", "X", "Y", None, [1.0, 2.0],
                                      {"X": [None, None], "Y": [0.5, 0.6]})
    assert request["kind"] == "message"
    assert "X" in request["reason"] and "Y" in request["reason"]


def test_3d_requests_four_series():
    request = plot_data._plot_request(
        "3D", "X", "Y", "Z", [1.0, 2.0],
        {"X": [0.1, 0.2], "Y": [0.5, 0.6], "Z": [0.8, 0.9]})

    assert request["kind"] == "scatter3d"
    assert (request["x"], request["y"], request["z"], request["c"]) == (
        [0.1, 0.2], [0.5, 0.6], [0.8, 0.9], [1.0, 2.0])


def test_render_figure_returns_bytes_for_every_plot_type():
    """matplotlib is a MagicMock here, so the bytes are empty — what this
    pins is that every path returns bytes rather than a Figure a view would
    have to know how to draw."""
    for plot_type, dims in (("0D", (None, None, None)),
                            ("1D", ("X", None, None)),
                            ("2D", ("X", "Y", None)),
                            ("3D", ("X", "Y", "Z")),
                            ("bogus", ("X", "Y", "Z"))):
        data = {"X": [0.1, 0.2], "Y": [0.5, 0.6], "Z": [0.8, 0.9]}
        rendered = plot_data.render_figure(plot_type, *dims,
                                           red_percents=[1.0, 2.0],
                                           dim_data=data)
        assert isinstance(rendered, bytes), plot_type


# -- F16: the analysis figure ----------------------------------------------

def _png_size(png):
    import struct
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", png[16:24])


def test_the_figure_is_rendered_at_the_size_asked_for():
    """UXPM-2/3: an 800x600 figure was shown at 0.48x on Web and cropped in
    Qt. The caller names the slot; the default is one the views can show."""
    png = plot_data.render_figure("0D", red_percents=[1.0, 2.0],
                                  size=(4.0, 3.0), dpi=100)
    assert _png_size(png) == (400, 300)
    default = _png_size(plot_data.render_figure("0D", red_percents=[1.0]))
    w, h = plot_data.FIGURE_SIZE
    assert default == (int(w * plot_data.FIGURE_DPI), int(h * plot_data.FIGURE_DPI))
    assert default[0] <= 640, "the default must fit a module card"


def test_the_line_is_drawn_in_the_accent_with_markers_only_when_few():
    """UXPM-2: pure blue at 1.55:1 with a marker per sample (a ribbon at 1500
    samples); `palette.ACCENT` was declared for the line and never used."""
    import palette
    few = plot_data._line_style(plot_data.MARKER_LIMIT)
    many = plot_data._line_style(plot_data.MARKER_LIMIT + 1)
    assert few["color"] == many["color"] == palette.ACCENT
    assert few["marker"] == "o"
    assert many["marker"] in (None, "", "None")


def test_the_3d_colormap_is_dark_safe():
    """UXPM-9: coolwarm's middle is light grey and its ends red/blue; on the
    dark panel the low end vanished. Every colour in the map must stand off
    the surface."""
    import palette
    from matplotlib.colors import to_rgb

    def luminance(rgb):
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
               for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    surface = luminance(to_rgb(palette.SURFACE))
    cmap = plot_data._colormap()
    for i in range(0, 256, 15):
        colour = luminance(cmap(i / 255)[:3])
        assert (colour + 0.05) / (surface + 0.05) >= 3.0, i


def test_the_3d_panes_are_dark():
    from matplotlib.figure import Figure
    figure = Figure()
    axes = figure.add_subplot(111, projection="3d")
    plot_data._style_axes(axes)
    import palette
    from matplotlib.colors import to_hex
    for axis in (axes.xaxis, axes.yaxis, axes.zaxis):
        assert to_hex(axis.pane.get_facecolor()) == palette.SURFACE.lower()
