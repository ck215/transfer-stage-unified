import pytest
from model.plot_data import parse_red_percent_csv, render_red_percent_figure

def test_parse_well_formed_csv():
    csv_text = """# Probe Name,TestProbe
# Probe Tilt Angle,45

Red Percent,Stepper X Location,Stepper X Velocity
10.5,1.0,0.1
20.5,2.0,0.2
"""
    result = parse_red_percent_csv(csv_text)
    assert result["metadata"] == {"Probe Name": "TestProbe", "Probe Tilt Angle": "45"}
    assert result["red_percents"] == [10.5, 20.5]
    assert result["dims"] == ["X"]
    assert result["dim_data"] == {"X": [1.0, 2.0]}

def test_parse_zero_synced_dimensions():
    csv_text = """# Probe Name,TestProbe

Red Percent
10.5
20.5
"""
    result = parse_red_percent_csv(csv_text)
    assert result["metadata"] == {"Probe Name": "TestProbe"}
    assert result["red_percents"] == [10.5, 20.5]
    assert result["dims"] == []
    assert result["dim_data"] == {}

def test_parse_missing_header():
    csv_text = """# Probe Name,TestProbe

Not Red Percent,Other
10.5,1.0
"""
    result = parse_red_percent_csv(csv_text)
    assert result["metadata"] == {"Probe Name": "TestProbe"}
    assert result["red_percents"] == []
    assert result["dims"] == []
    assert result["dim_data"] == {}

def test_parse_malformed_data_row():
    csv_text = """# Probe Name,TestProbe

Red Percent,Stepper X Location
10.5,1.0
bad,2.0
30.5,bad
40.5,4.0
"""
    result = parse_red_percent_csv(csv_text)
    assert result["red_percents"] == [10.5, 40.5]
    assert result["dim_data"] == {"X": [1.0, 4.0]}

def test_render_red_percent_figure():
    # matplotlib.figure is globally mocked for the whole test suite
    # (tests/conftest.py), so `Figure` here is a MagicMock, not a real
    # class -- isinstance() checks against it aren't meaningful. Instead
    # verify the function doesn't raise for every plot_type/edge case, and
    # verify the length-mismatch guards actually work by checking whether
    # the shared mock ax's plotting methods were called.
    from matplotlib.figure import Figure

    # 0D
    fig = render_red_percent_figure("0D", None, None, None, [1, 2], {})
    assert fig is not None

    # 1D normal
    fig = render_red_percent_figure("1D", "X", None, None, [1, 2], {"X": [0.1, 0.2]})
    assert fig is not None

    # 1D missing dim_data fallback
    fig = render_red_percent_figure("1D", "Y", None, None, [1, 2], {"X": [0.1, 0.2]})
    assert fig is not None

    # 2D normal: lengths match, the scatter guard must fire
    Figure.reset_mock()
    ax = Figure.return_value.add_subplot.return_value
    ax.reset_mock()
    render_red_percent_figure("2D", "X", "Y", None, [1, 2], {"X": [0.1, 0.2], "Y": [0.5, 0.6]})
    assert ax.scatter.called

    # 2D mismatched length: the guard must skip scatter() entirely rather
    # than crash or plot misaligned data
    ax.reset_mock()
    fig = render_red_percent_figure("2D", "X", "Y", None, [1, 2, 3], {"X": [0.1, 0.2], "Y": [0.5, 0.6]})
    assert fig is not None
    assert not ax.scatter.called

    # 3D normal
    fig = render_red_percent_figure("3D", "X", "Y", "Z", [1, 2], {"X": [0.1, 0.2], "Y": [0.5, 0.6], "Z": [0.8, 0.9]})
    assert fig is not None

    # 3D mismatched length
    fig = render_red_percent_figure("3D", "X", "Y", "Z", [1], {"X": [0.1, 0.2], "Y": [0.5, 0.6], "Z": [0.8, 0.9]})
    assert fig is not None
