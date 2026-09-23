"""TEMP-9: Temperature history arrays and get_history() are dead.

The audit says no temperature plot exists anywhere and history is silently
discarded. Owner ruling D-13 is "plot it" — implement the plot through the
generic schema renderer, exactly like Red Percent does.

The model collects tempC/time/sp samples in process_raw_data() under a lock;
get_history() returns copies. The plot needs a schema accessor (`temp_series`
property) that returns `{"x": [...], "y": [...]}`, and a schema declaration
`sch.plot("Temperature over time", "temp_series")` in the ui_schema.

Both Red Percent and temperature already proved the data collection path,
so this is schema plumbing only: no new collection pipeline.
"""
from unittest.mock import MagicMock, patch

import pytest

from model.temperature_system import TemperatureSystem


@pytest.fixture
def temp_system():
    """A simulated temperature system (no port = no reader thread)."""
    with patch('model.temperature_system.serial') as serial_class:
        transport = MagicMock()
        transport.is_open.return_value = False
        serial_class.return_value = transport
        ts = TemperatureSystem(None)  # SIM mode
        yield ts
        ts.close()


def test_temp_series_method_exists(temp_system):
    """The model must have a `temp_series` method for the schema to call."""
    assert hasattr(temp_system, 'temp_series'), (
        "TemperatureSystem must expose a temp_series method for the plot")
    assert callable(getattr(temp_system, 'temp_series', None)), (
        "temp_series must be a method")


def test_temp_series_returns_dict_with_x_y(temp_system):
    """temp_series must return {"x": [...], "y": [...]} like plot_series does."""
    series = temp_system.temp_series()
    assert isinstance(series, dict), "temp_series must return a dict"
    assert "x" in series, "temp_series dict must have 'x' key"
    assert "y" in series, "temp_series dict must have 'y' key"
    assert isinstance(series["x"], list), "x must be a list"
    assert isinstance(series["y"], list), "y must be a list"


def test_temp_series_empty_when_no_samples(temp_system):
    """When no samples are collected, x and y should be empty."""
    series = temp_system.temp_series()
    assert series["x"] == [], "x should be empty initially"
    assert series["y"] == [], "y should be empty initially"


def test_temp_series_populated_after_adding_samples(temp_system):
    """temp_series should reflect samples added to the history."""
    # Manually add samples (simulating what process_raw_data does)
    with temp_system._lock:
        temp_system.time.append(1.0)
        temp_system.tempC.append(20.0)
        temp_system.sp.append(25.0)
        temp_system.time.append(2.0)
        temp_system.tempC.append(21.0)
        temp_system.sp.append(25.0)
        temp_system.cnt = 2

    series = temp_system.temp_series()
    # x should be time values
    assert len(series["x"]) == 2, "x should have 2 samples"
    assert series["x"] == [1.0, 2.0], "x should be time values"
    # y should be temperature values
    assert len(series["y"]) == 2, "y should have 2 samples"
    assert series["y"] == [20.0, 21.0], "y should be temperature values"


def test_plot_schema_declared(temp_system):
    """The schema must declare the plot using sch.plot."""
    schema = temp_system.ui_schema
    # The schema is a dict with "sections" key
    assert isinstance(schema, dict), "ui_schema must return a dict"
    assert "sections" in schema, "ui_schema must have 'sections' key"

    # Find the plot entry in the sections
    # sections are dicts with "elements" key
    found_plot = False
    for section in schema.get("sections", []):
        if isinstance(section, dict):
            # Sections have "elements"; look through them
            for element in section.get("elements", []):
                if isinstance(element, dict) and element.get('type') == 'plot':
                    found_plot = True
                    assert element.get('text') == "Temperature over time", (
                        "plot text should be 'Temperature over time'")
                    assert element.get('data_command') == "temp_series", (
                        "plot should use 'temp_series' data_command")
                    break

    assert found_plot, (
        "ui_schema must declare a plot for temperature history "
        "(like Red Percent does with sch.plot)")
