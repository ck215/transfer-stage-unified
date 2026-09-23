"""REDPERCENT-17 (renderer half): a plot request the data cannot satisfy
gets an explanatory message, not a blank Figure with no Axes.

Before this, a 2D/3D request whose second/third dimension was unselected
fell through every branch in render_red_percent_figure with no `ax =
fig.add_subplot(...)` ever called -- the returned Figure had zero Axes, an
indistinguishable blank PNG for whichever caller (Web `/api/plot`,
PySide's plot dialog) just `savefig`s whatever comes back. This is exactly
what a 2D plot request against a 1-dimension CSV produces: `dims` has one
entry, so `dim2` is None.

`matplotlib.figure.Figure` is globally mocked (tests/conftest.py), so this
checks *calls* on the shared mock, the same technique
tests/core/test_plot_data.py already uses -- not `isinstance`/real Axes
content.
"""
from matplotlib.figure import Figure

from model.plot_data import render_red_percent_figure


def test_2d_missing_second_dimension_still_gets_an_axes_and_a_message():
    Figure.reset_mock()
    fig = render_red_percent_figure("2D", "X", None, None, [1, 2], {"X": [0.1, 0.2]})
    assert fig is not None
    assert Figure.return_value.add_subplot.called, (
        "a 2D request missing dim2 must still add an Axes -- this is the "
        "exact request shape a 2D plot of a 1-dimension CSV produces"
    )
    ax = Figure.return_value.add_subplot.return_value
    assert ax.text.called, "the Axes must carry an explanatory message"
    assert not ax.scatter.called


def test_2d_mismatched_samples_still_gets_an_axes_and_a_message():
    Figure.reset_mock()
    ax = Figure.return_value.add_subplot.return_value
    ax.reset_mock()
    fig = render_red_percent_figure(
        "2D", "X", "Y", None, [1, 2, 3], {"X": [0.1, 0.2], "Y": [0.5, 0.6]})
    assert fig is not None
    assert ax.text.called, "a length mismatch must still draw a message, not silence"
    assert not ax.scatter.called


def test_3d_missing_third_dimension_still_gets_an_axes_and_a_message():
    Figure.reset_mock()
    fig = render_red_percent_figure(
        "3D", "X", "Y", None, [1, 2], {"X": [0.1, 0.2], "Y": [0.5, 0.6]})
    assert fig is not None
    assert Figure.return_value.add_subplot.called, (
        "a 3D request missing dim3 must still add an Axes"
    )
    ax = Figure.return_value.add_subplot.return_value
    assert ax.text.called
    assert not ax.scatter.called


def test_unknown_plot_type_still_gets_an_axes_and_a_message():
    Figure.reset_mock()
    fig = render_red_percent_figure("bogus", "X", "Y", "Z", [1, 2], {})
    assert fig is not None
    assert Figure.return_value.add_subplot.called
    ax = Figure.return_value.add_subplot.return_value
    assert ax.text.called
