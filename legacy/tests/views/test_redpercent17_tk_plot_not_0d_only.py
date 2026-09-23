"""REDPERCENT-17 (Tk half): the finding no longer applies.

The audit describes Tk's `open_plot_window` as always calling
`render_red_percent_figure("0D", ...)` -- no 1D/2D/3D, on a saved CSV the
operator picks via a file dialog. That whole hand-built matplotlib plot
window is gone (see the comment on `RedPercentView` in
src/views/tkinter/view.py: "About 240 lines of hand-built widgets used to
live here"). Tk's Red Percent card now renders the schema `plot` composite
like every other device (D-6): a live sparkline of `plot_series()`'s
`{"x": [...], "y": [...]}`, drawn straight to a `tk.Canvas`
(`DynamicView._build_plot`/`_redraw_plot`) -- there is no saved-CSV picker,
no `plot_type` argument, and no dependency on `render_red_percent_figure`
at all, so there is no "0D-only" restriction left to fix: the live plot
was never dimension-selectable to begin with, in any of the three views.

This is coverage, not a fix -- pinning that `render_red_percent_figure`
(the function the audit says Tk hard-codes to "0D") is not reachable from
Tk any more, so a future change cannot reintroduce a saved-CSV path that
silently drops back to 0D-only without this test noticing.
"""
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"


def test_tk_view_does_not_import_or_call_render_red_percent_figure():
    view_source = (_SRC / "views/tkinter/view.py").read_text()
    assert "render_red_percent_figure" not in view_source, (
        "Tk's live plot composite must not reach into the saved-CSV "
        "renderer at all -- if it does, the 0D-only call the audit "
        "describes is back"
    )
    assert "open_plot_window" not in view_source, (
        "the hand-built saved-CSV plot window this finding was filed "
        "against has been replaced by the schema `plot` composite; a "
        "reintroduction of open_plot_window needs its own dim selection, "
        "not a silent 0D default"
    )


def test_tk_plot_composite_draws_from_the_live_series_not_a_file():
    view_source = (_SRC / "views/tkinter/view.py").read_text()
    # _redraw_plot (DynamicView) is the only plot-drawing path Tk has for
    # Red Percent; it must read the schema's data_command (plot_series),
    # never a file path or a plot_type.
    assert "def _redraw_plot(self, entry):" in view_source
    assert "data_command" in view_source


def test_plot_series_is_dimension_agnostic():
    """plot_series() returns one series over every logged sample -- it has
    no plot_type/dim1/dim2/dim3 concept to be "stuck at 0D" in, unlike the
    saved-CSV renderer (model.plot_data.render_red_percent_figure) this
    finding was actually about."""
    import inspect

    from model.redpercent_system import RedPercentSystem

    sig = inspect.signature(RedPercentSystem.plot_series)
    assert list(sig.parameters) == ["self"], (
        "plot_series must stay a plain live-series accessor with no "
        "dim/plot_type parameters to default incorrectly"
    )
