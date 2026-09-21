"""S13 items 5-7 — REDPERCENT-21, 22, 23.

A monitoring run has to be joinable to the physical trial that produced it.
These tests pin the three things that makes true:

  21  a run has an identity, and an output location that is not the CWD
  22  the CSV is a plain rectangle; the configuration goes to a sidecar
  23  operator annotation has somewhere to live, separate from what the
      station actually did

Owner-added scope (2026-09-20), not audit findings — see the `Source:` lines
in docs/architecture/audit/redpercent.md.
"""
import csv
import json
import os
from pathlib import Path

import pytest

from model.redpercent_system import RedPercentDataLog, RedPercentSystem

pytestmark = pytest.mark.redpercent


@pytest.fixture
def logged_system(tmp_path):
    """A system with a finished run's worth of data, writing under tmp_path."""
    sysm = RedPercentSystem()
    sysm.output_root = tmp_path / "runs"
    sysm.run_id = "C001"
    sysm.probe_name = "tip-3"
    sysm.probe_tilt_angle = 12.5
    sysm.sync_dimensions = ["X", "Y"]
    sysm.focus_area = {"top": 10, "left": 20, "width": 640, "height": 480}
    sysm.baseline_red = 4.25
    sysm.data_log = RedPercentDataLog(list(sysm.sync_dimensions),
                                      sysm.probe_name, sysm.probe_tilt_angle)
    for i in range(5):
        sysm.data_log.add_entry(10.0 + i, {"X": float(i), "Y": -float(i)},
                                {"X": 0.5, "Y": 0.5})
    return sysm


# --- REDPERCENT-21: run identity and output location ----------------------

def test_redpercent_21_autosave_never_writes_a_bare_relative_path(logged_system):
    """The defect: `autosave_log` built `redpercent_log_<ts>.csv` with no
    directory, so the file landed wherever the launcher happened to start."""
    written = logged_system.autosave_log()
    assert written is not None, "autosave_log must report where it wrote"
    written = Path(written)
    assert written.is_absolute(), f"autosave wrote a relative path: {written}"
    assert written.parent != Path("."), "autosave wrote into the CWD"
    assert written.exists()


def test_redpercent_21_every_artifact_of_a_run_carries_the_run_id(logged_system):
    """A file has to stay self-describing after someone moves it."""
    logged_system.autosave_log()
    produced = sorted(p.name for p in logged_system.run_dir().iterdir())
    assert produced, "the run produced no artifacts"
    for name in produced:
        assert name.startswith("C001_"), f"{name} does not carry its run id"


def test_redpercent_21_the_output_root_does_not_follow_the_process_cwd(tmp_path,
                                                                      monkeypatch):
    """Three launchers start in three different directories. The run's output
    location must not be one of the things that varies between them."""
    monkeypatch.chdir(tmp_path)
    before = RedPercentSystem().output_root
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    after = RedPercentSystem().output_root
    assert Path(before).is_absolute()
    assert before == after, "output_root moved when the CWD moved"


def test_redpercent_21_an_unset_run_id_still_produces_a_unique_directory(tmp_path):
    """Unattended stops happen. They must not collide or land loose."""
    sysm = RedPercentSystem()
    sysm.output_root = tmp_path
    sysm.run_id = ""
    assert sysm.effective_run_id(), "an unset run id must fall back, not be empty"
    assert sysm.run_dir().parent == Path(tmp_path)


# --- REDPERCENT-22: a readable CSV and an interpretable sidecar -----------

def test_redpercent_22_the_csv_is_a_rectangle_a_default_reader_opens(logged_system):
    """The defect: `# Metadata` and friends were four DATA rows written before
    the header, so a default `read_csv` takes `# Metadata` as the header."""
    logged_system.autosave_log()
    csv_path = logged_system.run_dir() / "C001_position.csv"
    with open(csv_path, newline="") as fh:
        rows = [r for r in csv.reader(fh)]

    assert rows, "the CSV is empty"
    header, body = rows[0], rows[1:]
    assert header[0] == "Red Percent", (
        f"first row is not the header — a default reader would use {header!r} "
        "as column names")
    assert not any(cell.startswith("#") for row in rows for cell in row), (
        "a '#' cell survives; this is not a comment convention, it is data")
    assert all(len(r) == len(header) for r in body), "ragged rows"
    assert len(body) == 5


def test_redpercent_22_the_sidecar_carries_what_the_csv_cannot(logged_system):
    """Red percent is uninterpretable without the baseline and the ROI size.
    Two runs with the same number and different focus areas are not
    comparable, and nothing in the old artifact said so."""
    logged_system.autosave_log()
    meta_path = logged_system.run_dir() / "C001_station_meta.json"
    assert meta_path.exists(), "no station metadata was written"
    meta = json.loads(meta_path.read_text())

    for key in ("run_id", "probe_name", "probe_tilt_angle", "sync_dimensions",
                "focus_area", "baseline_red", "red_threshold",
                "sample_count", "started_at", "stopped_at"):
        assert key in meta, f"station meta is missing {key!r}"

    assert meta["run_id"] == "C001"
    assert meta["baseline_red"] == pytest.approx(4.25)
    assert meta["focus_area"]["width"] == 640
    assert meta["focus_area"]["height"] == 480
    assert meta["sample_count"] == 5
    assert meta["sync_dimensions"] == ["X", "Y"]


def test_redpercent_22_a_legacy_csv_with_a_comment_block_still_loads():
    """Files already on disk keep working. The parser drops '#' rows and finds
    the header by name, so this must not regress when new files stop
    carrying the block."""
    from model.plot_data import parse_red_percent_csv
    legacy = ("# Metadata\n"
              "# Probe Name,TestProbe\n"
              "# Probe Tilt Angle,45\n"
              "\n"
              "Red Percent,Stepper X Location,Stepper X Velocity\n"
              "10.0,1.0,0.5\n"
              "11.0,2.0,0.5\n")
    result = parse_red_percent_csv(legacy)
    assert result["metadata"] == {"Probe Name": "TestProbe",
                                  "Probe Tilt Angle": "45"}
    assert result["red_percents"] == [10.0, 11.0]


def test_redpercent_22_a_plain_csv_parses_and_reads_its_metadata_from_the_sidecar(
        logged_system):
    """The new shape has to be at least as useful to the app as the old one."""
    from model.plot_data import load_red_percent_run
    logged_system.autosave_log()
    result = load_red_percent_run(logged_system.run_dir() / "C001_position.csv")
    assert result["red_percents"] == [10.0, 11.0, 12.0, 13.0, 14.0]
    assert result["metadata"]["probe_name"] == "tip-3"
    assert result["metadata"]["baseline_red"] == pytest.approx(4.25)


def test_redpercent_22_probe_tilt_angle_is_the_float_its_param_declares():
    """`PARAMS` declares it `float`; `__init__` set it to `""`."""
    sysm = RedPercentSystem()
    assert isinstance(sysm.probe_tilt_angle, float), (
        f"declared float, initialized {type(sysm.probe_tilt_angle).__name__}")


# --- REDPERCENT-23: operator annotation, kept apart from the actuals ------

def test_redpercent_23_operator_annotations_reach_the_sidecar(logged_system):
    logged_system.run_annotations["specimen_id"] = "hBN-04"
    logged_system.run_annotations["consumable_id"] = "tip-3"
    logged_system.run_annotations["note"] = "second cut of the block"
    logged_system.autosave_log()

    meta = json.loads(
        (logged_system.run_dir() / "C001_station_meta.json").read_text())
    assert meta["annotations"]["specimen_id"] == "hBN-04"
    assert meta["annotations"]["note"] == "second cut of the block"


def test_redpercent_23_intended_and_actual_never_share_a_field(logged_system):
    """The load-bearing part. The annotation block holds what the operator
    INTENDED; the meta block holds what the station DID. Merging them loses
    exactly the comparison the experiment exists to make."""
    logged_system.run_annotations["probe_tilt_angle"] = 99.0   # the intent
    logged_system.probe_tilt_angle = 12.5                      # the actual
    logged_system.autosave_log()

    meta = json.loads(
        (logged_system.run_dir() / "C001_station_meta.json").read_text())
    assert meta["probe_tilt_angle"] == pytest.approx(12.5)
    assert meta["annotations"]["probe_tilt_angle"] == pytest.approx(99.0)


def test_redpercent_23_the_annotation_set_is_a_table_not_hardcoded_attributes():
    """Adding a field for the next experiment must not be a code change in
    four places. The default set is data."""
    fields = RedPercentSystem.ANNOTATION_FIELDS
    names = [f.name for f in fields]
    assert "specimen_id" in names
    assert "consumable_id" in names
    assert "note" in names
    assert len(set(names)) == len(names), "duplicate annotation field"


def test_redpercent_23_annotations_are_rendered_by_the_schema_not_per_view():
    """D-6: schema-driven in all three views, so none of them hand-builds it."""
    sysm = RedPercentSystem()
    flat = repr(sysm.ui_schema)
    for name in ("specimen_id", "consumable_id", "note"):
        assert name in flat, f"{name} is not reachable through the schema"
