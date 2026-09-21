"""REDPERCENT-6: PySide save_log_ui should use model.save_log, not bypass it.

The PySide "Save Log" button bypasses RedPercentSystem.save_log(), which means
late edits to probe_name and probe_tilt_angle are not synced to the CSV before
saving. The fix is to call model.save_log(file_path) which:
1. Syncs metadata from model to data_log
2. Saves the CSV
3. Saves the station_meta.json sidecar
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import os


def test_redpercent6_save_log_uses_model_method(qtbot):
    """save_log_ui should call model.save_log, not bypass it."""
    from model.redpercent_system import RedPercentSystem, RedPercentDataLog
    from views.pyside.view import RedPercentDynamicView

    model = RedPercentSystem()
    model.probe_name = "TestProbe"
    model.probe_tilt_angle = 45.0

    # Add some data
    model.data_log = RedPercentDataLog()
    model.data_log.red_values.append(50.0)

    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)

    # Mock the file dialog to return a path
    with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName") as mock_dialog, \
         patch.object(model, "save_log") as mock_save_log:
        mock_dialog.return_value = ("/tmp/test_log.csv", "")

        # Call save_log_ui
        view.save_log_ui()

        # Model's save_log should have been called
        mock_save_log.assert_called_once_with("/tmp/test_log.csv")


def test_redpercent6_metadata_synced_before_save(qtbot):
    """Metadata should be synced from model to data_log before saving."""
    from model.redpercent_system import RedPercentSystem, RedPercentDataLog
    from views.pyside.view import RedPercentDynamicView
    import json

    with tempfile.TemporaryDirectory() as tmpdir:
        model = RedPercentSystem()
        model.probe_name = "UpdatedProbe"
        model.probe_tilt_angle = 35.5

        # Add some data
        model.data_log = RedPercentDataLog()
        model.data_log.red_values.append(50.0)

        view = RedPercentDynamicView(model)
        qtbot.addWidget(view)

        csv_path = os.path.join(tmpdir, "test_log.csv")

        # Mock the file dialog
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName") as mock_dialog:
            mock_dialog.return_value = (csv_path, "")

            # Call save_log_ui
            view.save_log_ui()

            # Verify that the CSV and metadata files were created
            assert os.path.exists(csv_path), "CSV file should exist"

            # Check the metadata file
            meta_path = os.path.join(tmpdir, "test_log_station_meta.json")
            assert os.path.exists(meta_path), "Metadata file should exist"

            # Read the metadata and verify it has the correct probe info
            with open(meta_path, 'r') as f:
                meta = json.load(f)

            assert meta["probe_name"] == "UpdatedProbe", "Probe name should be synced"
            assert meta["probe_tilt_angle"] == 35.5, "Probe tilt angle should be synced"
