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
    the surface, whichever of the two is the lighter (the sheet is light
    since 2026-09-25)."""
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
        lighter, darker = max(colour, surface), min(colour, surface)
        assert (lighter + 0.05) / (darker + 0.05) >= 3.0, i


def test_the_3d_panes_are_dark():
    from matplotlib.figure import Figure
    figure = Figure()
    axes = figure.add_subplot(111, projection="3d")
    plot_data._style_axes(axes)
    import palette
    from matplotlib.colors import to_hex
    for axis in (axes.xaxis, axes.yaxis, axes.zaxis):
        assert to_hex(axis.pane.get_facecolor()) == palette.SURFACE.lower()


# -- MAP-3 (2026-09-27): the Transfer Map's figures --------------------------

def _trials():
    """Four trials: three measured by AFM, one pending."""
    rows = []
    for i, (tilt, speed, force, width) in enumerate((
            (10.0, 100.0, 0.2, 5.0), (20.0, 200.0, 0.5, 7.0),
            (30.0, 300.0, 0.8, 9.0), (25.0, 150.0, 0.6, None))):
        rows.append({"id": i + 1, "tilt": tilt, "speed": speed,
                     "force": {"shadow_vs_peak": force, "dip_area": force * 2,
                               "at_operator_mark": None},
                     "width": width, "width_sigma": 0.5 if width else None})
    return rows


def test_the_map_request_is_speed_by_force_and_splits_measured_from_pending():
    request = plot_data.transfer_request("map", _trials(), "shadow_vs_peak")
    assert request["kind"] == "map"
    assert request["x"] == [100.0, 200.0, 300.0, 150.0]       # speed
    assert request["y"] == [0.2, 0.5, 0.8, 0.6]               # force
    assert "z" not in request and "tilt" not in request["x_label"]
    assert request["measured"] == [True, True, True, False]
    assert request["c"][:3] == [5.0, 7.0, 9.0]


def test_a_trial_without_the_chosen_index_is_left_off_the_map():
    request = plot_data.transfer_request("map", _trials(), "at_operator_mark")
    assert request["kind"] == "message"
    assert "at_operator_mark" in request["reason"] or "force" in request["reason"]


def test_no_trials_explains_itself_for_every_figure():
    for kind in plot_data.TRANSFER_FIGURES:
        request = plot_data.transfer_request(kind, [], "shadow_vs_peak")
        assert request["kind"] == "message", kind


def test_the_heatmap_uses_the_measured_trials_over_speed_and_force():
    request = plot_data.transfer_request("heatmap", _trials(), "shadow_vs_peak")
    assert request["kind"] == "heatmap"
    assert sorted(request["points_c"]) == [5.0, 7.0, 9.0]
    assert sorted(request["points_x"]) == [100.0, 200.0, 300.0]
    assert sorted(request["points_y"]) == [0.2, 0.5, 0.8]
    assert "speed" in request["x_label"] and "force" in request["y_label"]


def test_a_trial_without_a_tilt_is_still_on_the_map():
    rows = _trials()
    for row in rows:
        row["tilt"] = None
    assert plot_data.transfer_request("map", rows, "shadow_vs_peak")["kind"] == "map"
    assert plot_data.transfer_request("heatmap", rows, "shadow_vs_peak")["kind"] == "heatmap"


def test_the_heatmap_carries_a_gp_mean_and_sigma_surface_with_enough_trials():
    request = plot_data.transfer_request("heatmap", _trials(), "shadow_vs_peak")
    mean, sigma = request["mean"], request["sigma"]
    assert len(mean) == len(sigma) == len(request["grid_y"])
    assert len(mean[0]) == len(request["grid_x"])
    assert all(s >= 0 for row in sigma for s in row)


def test_the_compare_request_has_one_panel_per_definition():
    request = plot_data.transfer_request(
        "compare", _trials(), "shadow_vs_peak",
        definitions=("shadow_vs_peak", "dip_area", "at_operator_mark"))
    names = [p["name"] for p in request["panels"]]
    assert names == ["shadow_vs_peak", "dip_area", "at_operator_mark"]
    dip = request["panels"][1]
    assert dip["x"] == [0.4, 1.0, 1.6] and dip["y"] == [5.0, 7.0, 9.0]
    assert request["panels"][2]["x"] == []            # nothing to compare


def test_the_profile_request_carries_the_marks():
    profile = {"t": [0.0, 0.1, 0.2], "red": [1.0, 3.0, 2.0]}
    marks = {"operator_t": 0.15, "max_t": 0.1, "min_t": 0.2, "baseline": 1.0,
             "red_max": 3.0, "red_min": 2.0, "trial_id": 7}
    request = plot_data.transfer_request("profile", [], "shadow_vs_peak",
                                         profile=profile, marks=marks)
    assert request["kind"] == "profile"
    assert request["operator_t"] == 0.15 and request["max_t"] == 0.1
    assert "7" in request["title"]


def test_every_transfer_figure_renders_to_a_png():
    profile = {"t": [0.0, 0.1, 0.2, 0.3], "red": [1.0, 3.0, 2.0, 1.5]}
    marks = {"operator_t": 0.15, "max_t": 0.1, "min_t": 0.2, "baseline": 1.0,
             "trial_id": 1}
    for kind in plot_data.TRANSFER_FIGURES:
        png = plot_data.render_transfer_figure(
            kind, _trials(), "shadow_vs_peak", profile=profile, marks=marks,
            definitions=("shadow_vs_peak", "dip_area"), size=(4.0, 3.0), dpi=50)
        assert png[:8] == b"\x89PNG\r\n\x1a\n", kind
    assert plot_data.render_transfer_figure("map", [], "x")[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_transfer_figures_never_draw_in_the_stop_red():
    """SIGNAL is spent on the stop only (palette ruling)."""
    import palette
    import inspect
    source = inspect.getsource(plot_data._draw_transfer)
    assert "SIGNAL" not in source
    assert palette.SIGNAL.lower() not in source.lower()


def test_the_map_axes_are_padded_so_one_speed_does_not_read_as_a_wrong_one():
    """Bench 2026-09-28: with every trial at one value, autoscaling drew a
    hair-wide axis whose ticks looked like a misread value."""
    one = [{"id": 1, "tilt": 7.0, "speed": 200.0,
            "force": {"shadow_vs_peak": 0.4}, "width": None, "width_sigma": None}]
    request = plot_data.transfer_request("map", one, "shadow_vs_peak")
    limits = plot_data.map_limits(request)
    assert limits["x"] == (199.5, 200.5)
    assert limits["y"] == (0.4 - 0.5, 0.4 + 0.5)
    assert plot_data.render_transfer_figure("map", one, "shadow_vs_peak")[:8] == b"\x89PNG\r\n\x1a\n"
    assert plot_data.map_limits({"x": [], "y": []}) == {"x": None, "y": None}


# -- store v6: the width's source is always visible (owner, 2026-10-04) ------

def _mixed():
    """Three AFM-measured trials, one optical only, one with no width."""
    rows = _trials()[:3]
    for row in rows:
        row["width_source"] = "afm"
    rows.append({"id": 4, "tilt": 25.0, "speed": 150.0,
                 "force": {"shadow_vs_peak": 0.6, "dip_area": 1.2},
                 "width": 8.0, "width_sigma": None, "width_source": "optical"})
    rows.append({"id": 5, "tilt": 15.0, "speed": 250.0,
                 "force": {"shadow_vs_peak": 0.3, "dip_area": 0.6},
                 "width": None, "width_sigma": None, "width_source": None})
    return rows


def test_the_map_fills_afm_rings_optical_and_says_so():
    request = plot_data.transfer_request("map", _mixed(), "shadow_vs_peak")
    assert request["measured"] == [True, True, True, False, False]
    assert request["optical"] == [False, False, False, True, False]
    assert request["c"][3] == 8.0
    assert request["title"] == ("Transfer map\n(filled: AFM width; ringed: "
                                "optical width; hollow: no width yet)")


def test_the_heatmap_uses_afm_only_by_default():
    request = plot_data.transfer_request("heatmap", _mixed(), "shadow_vs_peak")
    assert sorted(request["points_c"]) == [5.0, 7.0, 9.0]
    assert request["points_optical"] == [False, False, False]
    assert "3 AFM" in request["title"] and "optical" not in request["title"]


def test_the_heatmap_takes_optical_on_request_with_a_wider_noise():
    request = plot_data.transfer_request("heatmap", _mixed(), "shadow_vs_peak",
                                         width_source="AFM, else optical")
    assert sorted(request["points_c"]) == [5.0, 7.0, 8.0, 9.0]
    assert request["points_optical"].count(True) == 1
    assert "3 AFM, 1 optical" in request["title"]
    assert plot_data.OPTICAL_SIGMA_FACTOR == 3
    import numpy
    widths = numpy.array([5.0, 7.0, 9.0, 8.0])
    spread = float(widths.std())
    noise = plot_data.width_noise(_mixed()[:4], spread)
    assert noise[:3] == [0.25, 0.25, 0.25]           # the AFM sigma, squared
    assert noise[3] == pytest.approx((3 * 0.05 * spread) ** 2)


def test_compare_follows_the_width_source_and_rings_optical():
    afm_only = plot_data.transfer_request(
        "compare", _mixed(), "shadow_vs_peak", definitions=("dip_area",))
    assert afm_only["panels"][0]["y"] == [5.0, 7.0, 9.0]
    both = plot_data.transfer_request(
        "compare", _mixed(), "shadow_vs_peak", definitions=("dip_area",),
        width_source="AFM, else optical")
    assert both["panels"][0]["y"] == [5.0, 7.0, 9.0, 8.0]
    assert both["panels"][0]["optical"] == [False, False, False, True]
    assert "optical" in both["title"]


def test_an_unknown_width_source_is_refused():
    with pytest.raises(ValueError):
        plot_data.transfer_request("heatmap", _mixed(), "shadow_vs_peak",
                                   width_source="optical only")


def test_mixed_figures_render():
    for kind in ("map", "heatmap", "compare"):
        png = plot_data.render_transfer_figure(
            kind, _mixed(), "shadow_vs_peak", definitions=("dip_area",),
            width_source="AFM, else optical", size=(4.0, 3.0), dpi=50)
        assert png[:8] == b"\x89PNG\r\n\x1a\n", kind


# -- the Sample Map's figure (flake-coords Phase 1, section 2) ------------------

def test_the_sample_request_carries_corners_flakes_and_the_crosshair():
    request = plot_data.sample_map_request(
        corners={"A": (0.0, 0.0), "B": (5000.0, 0.0), "D": (0.0, 4000.0)},
        flakes=[{"label": "F01", "x": 1000.0, "y": 500.0, "selected": True,
                 "extent": [[990, 490], [1010, 490], [1010, 510], [990, 510]]},
                {"label": "F02", "x": None, "y": None, "selected": False, "extent": None}],
        crosshair=(200.0, 300.0), size_um=(5000.0, 4000.0))
    assert request["kind"] == "sample"
    assert request["corners"] == {"A": (0.0, 0.0), "B": (5000.0, 0.0), "D": (0.0, 4000.0)}
    assert [f["label"] for f in request["flakes"]] == ["F01"]      # placed ones only
    assert request["unplaced"] == 1
    assert request["rectangle"] == [(0, 0), (5000.0, 0), (5000.0, 4000.0), (0, 4000.0)]
    assert "1 flake not placed" in request["title"]


def test_the_sample_request_without_corners_is_a_message():
    assert plot_data.sample_map_request({}, [], None, None)["kind"] == "message"


def test_the_sample_figure_renders_and_never_in_the_stop_red():
    import inspect
    import palette
    request = plot_data.sample_map_request(
        {"A": (0.0, 0.0), "B": (100.0, 0.0)},
        [{"label": "F01", "x": 10.0, "y": 20.0, "selected": True, "extent": None}],
        (5.0, 5.0), None)
    png = plot_data.render_sample_figure(request, size=(4.0, 3.0), dpi=50)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    source = inspect.getsource(plot_data.render_sample_figure)
    assert "SIGNAL" not in source and palette.SIGNAL.lower() not in source.lower()
