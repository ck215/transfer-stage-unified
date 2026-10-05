"""The Sample Map (flake-coords Phase 1): where on the chip each flake is.

Corners A-D marked with the optical crosshair (owner 2026-10-04: the tip
never touches a corner), the chip's frame from them, flakes flagged with
their sample-frame position, a bounding-box extent, and a guidance readout
toward a chosen flake. Guidance only: no model commands another model's
motion (owner ruling 2026-10-04). Fakes only: no hardware, no real store.
"""
import json
import math
import time

import pytest

from controller import setup as station_setup
from events import events
from model import sample_frame as sf
from model import sample_store as ss
from model.sample_map import SampleMap, TYPED

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeStage:
    """Locating axes, duck-typed as the Sample Map reads them (a probe or
    the Chuck Positioner): position in counts, its age, velocity, epoch."""

    def __init__(self, name="Stepper Probe", xy=(1000, 2000), z=500):
        self.NAME = name
        self.mode_name = "manual"
        self.velocity = (0.0, 0.0, 0.0)
        self.position_epoch = 1
        self.position_time = time.monotonic()
        self.move_to(*xy, z=z)

    def move_to(self, x, y, z=None):
        z = self.position[2] if z is None and hasattr(self, "position") else (z or 0)
        self.position = (int(round(x)), int(round(y)), z)
        self.position_time = time.monotonic()

    @property
    def position_age(self):
        return round(time.monotonic() - self.position_time, 2)


class FakeRed:
    """Red Percent, duck-typed: the reading and a picture of the region."""

    def __init__(self):
        self.current_red = 12.5
        self.run_id = "run-7"
        self.specimen_id = "S1"
        self.frames = 0

    def subscribe(self, fn):
        pass

    def grab_frame(self):
        self.frames += 1
        return PNG


THETA = math.radians(10.0)
K = 0.625                          # the stepper's um per count


def _stage_of(q, origin=(1000.0, 2000.0), theta=THETA, k=K):
    c, s = math.cos(theta), math.sin(theta)
    return (origin[0] + (c * q[0] - s * q[1]) / k, origin[1] + (s * q[0] + c * q[1]) / k)


@pytest.fixture
def stage():
    return FakeStage()


@pytest.fixture
def sample_map(stage):
    model = SampleMap()
    model.open()
    model.on_model_added(stage.NAME, stage)
    model.sample_id = "S1"
    model.run("save_sample", {"sample_id": "S1", "material": "WSe2"})
    yield model
    model.close()


def _mark(model, stage, label, q, confirmed=False):
    stage.move_to(*_stage_of(q))
    result = model.run("mark_corner", None, (label,) + ((True,) if confirmed else ()))
    assert result.is_ok, result
    return result


def _register(model, stage, w=5000.0, h=4000.0, corners="ABD"):
    ideal = {"A": (0, 0), "B": (w, 0), "C": (w, h), "D": (0, h)}
    for label in corners:
        _mark(model, stage, label, ideal[label])


# -- the class and the store -----------------------------------------------------

def test_the_class_is_a_portless_model_registered_after_the_transfer_map():
    assert SampleMap.NAME == "Sample Map"
    assert SampleMap.IDENTITY is None and SampleMap.RESOURCES == ()
    assert SampleMap.HOST is None                  # its own page
    names = list(station_setup.MODEL_TYPES)
    assert names.index("Sample Map") == names.index("Transfer Map") + 1
    model = SampleMap(port="SIM", gamepad=None, sim=True)
    assert model.devices == [] and model._expects_heartbeat() is False
    assert model._halt_hardware() is True and model.is_active is False


def test_construction_creates_nothing_and_open_announces_the_store(tmp_path,
                                                                   monkeypatch):
    path = tmp_path / "data" / "sample_map.sqlite"
    monkeypatch.setenv("STATION_SAMPLE_DB", str(path))
    model = SampleMap()
    assert not path.exists()
    since = events.latest_id
    model.open()
    try:
        assert path.exists()
        ready = [e for e in events.since(since) if e.title == "Database Ready"
                 and e.source == "Sample Map"]
        assert ready and str(path) in ready[0].message
    finally:
        model.close()


# -- the locating axes ----------------------------------------------------------------

def test_no_source_until_locating_axes_arrive(stage):
    model = SampleMap()
    model.open()
    try:
        assert model.mode_name == "no_source"
        assert model.source_options == [TYPED]
        model.on_model_added("Red Percent", FakeRed())      # not a stage
        assert model.mode_name == "no_source"
        model.on_model_added(stage.NAME, stage)
        assert model.source == stage.NAME                  # the first is taken
        assert model.source_options == [stage.NAME, TYPED]
        assert model.mode_name == "unregistered"
        assert model.run("set_source", None, ("Rotator",)).is_refused
    finally:
        model.close()


def test_a_mark_needs_a_sample_and_a_stage_at_rest(stage):
    model = SampleMap()
    model.open()
    model.on_model_added(stage.NAME, stage)
    try:
        assert "sample ID" in model.run("mark_corner", None, ("A",)).reason
        model.run("save_sample", {"sample_id": "S1"})
        stage.velocity = (40.0, 0.0, 0.0)
        assert "moving" in model.run("mark_corner", None, ("A",)).reason
        stage.velocity = (0.0, 0.0, 0.0)
        stage.position_time = time.monotonic() - 2.0
        assert "stale" in model.run("mark_corner", None, ("A",)).reason
        stage.position_time = time.monotonic()
        assert model.run("mark_corner", None, ("A",)).is_ok
    finally:
        model.close()


# -- registration ------------------------------------------------------------------------

def test_a_and_b_register_the_chip_and_the_frame_maps_both_ways(sample_map, stage):
    _register(sample_map, stage, corners="AB")
    assert sample_map.mode_name == "registered"
    frame = sample_map.frame()
    assert frame.to_sample(_stage_of((300.0, 700.0))) == pytest.approx((300.0, 700.0), abs=1.0)
    reg = sample_map.registration
    assert reg["frame_source"] == "stage:Stepper Probe" and reg["position_epoch"] == 1
    assert reg["k_x_um"] == K and reg["fit_kind"] == "rigid"
    assert reg["quality"] == "unchecked"
    assert sample_map.corners_text == "Corners: A B - -"


def test_re_marking_a_corner_asks_and_quotes_the_distance(sample_map, stage):
    _register(sample_map, stage, corners="AB")
    stage.move_to(1016, 2000)                      # 16 counts = 10 um from A
    result = sample_map.run("mark_corner", None, ("A",))
    assert result.needs_confirm and "10 um" in result.reason, result.reason
    assert sample_map.run(result.command, result.inputs, (*result.args, True)).is_ok
    corners = {c["label"]: c for c in sample_map._store.corners(sample_map.registration["registration_id"])}
    assert corners["A"]["stage_x"] == 1016


def test_d_gives_the_size_and_the_angle_and_c_the_rectangularity(sample_map, stage):
    _register(sample_map, stage, corners="ABDC")
    assert "90.0 deg" in sample_map.frame_text
    assert "5.00 x 4.00 mm" in sample_map.frame_text
    assert "Rectangularity: 0 um" in sample_map.check_text


def test_check_corner_a_measures_closure_and_names_the_quality(sample_map, stage):
    _register(sample_map, stage, corners="AB")
    stage.move_to(1008, 2000)                      # 8 counts = 5 um from A
    assert sample_map.run("check_corner").is_ok
    assert sample_map.registration["closure_um"] == pytest.approx(5.0, abs=1.0)
    assert sample_map.registration["quality"] == "good"
    assert "Closure: 5 um (good)" in sample_map.check_text


def test_an_unknown_um_per_count_is_a_bench_fact_not_a_guess():
    chuck = FakeStage(name="Chuck Positioner")
    model = SampleMap()
    model.open()
    model.on_model_added(chuck.NAME, chuck)
    model.run("save_sample", {"sample_id": "S1"})
    try:
        for label, q in (("A", (0, 0)), ("B", (5000, 0))):
            chuck.move_to(*_stage_of(q))
            assert model.run("mark_corner", None, (label,)).is_ok   # corners kept
        assert model.mode_name == "unregistered"
        assert "um per count" in model.next_step
        assert "um per count" in model.registration_status
        assert model.run("set_um_per_count", {"um_per_count": "0.625"}).is_ok
        assert model.mode_name == "registered"
        assert model.registration["k_x_um"] == 0.625
    finally:
        model.close()


def test_a_reconnect_invalidates_the_frame_and_keeps_the_flakes(sample_map, stage):
    _register(sample_map, stage, corners="AB")
    stage.move_to(*_stage_of((1000.0, 500.0)))
    assert sample_map.run("flag_flake").is_ok
    stage.position_epoch = 2                      # the counter restarted
    assert sample_map.mode_name == "unregistered"
    assert "restarted" in sample_map.registration_status
    assert sample_map.guidance == ""
    flake = sample_map.flakes[0]
    assert (flake["sample_x_um"], flake["sample_y_um"]) == pytest.approx((1000.0, 500.0), abs=1.0)
    # The next write records why the registration ended.
    stage.position_epoch = 2
    _mark(sample_map, stage, "A", (0, 0))
    old = sample_map._store.registrations("S1")[0]
    assert old["invalidated_at"] and "restarted" in old["invalidated_reason"]


def test_removing_the_stage_invalidates_the_registration(sample_map, stage):
    _register(sample_map, stage, corners="AB")
    rid = sample_map.registration["registration_id"]
    sample_map.on_model_removed(stage.NAME, stage)
    assert sample_map.mode_name == "no_source"
    assert sample_map._store.registration(rid)["invalidated_at"]


def test_clear_corners_asks_then_ends_the_registration(sample_map, stage):
    _register(sample_map, stage, corners="AB")
    result = sample_map.run("clear_corners")
    assert result.needs_confirm
    assert sample_map.run("clear_corners", None, (True,)).is_ok
    assert sample_map.mode_name == "unregistered"


# -- flakes -------------------------------------------------------------------------------------

def test_a_flake_is_flagged_with_both_frames_red_percent_and_its_picture(sample_map, stage):
    red = FakeRed()
    sample_map.on_model_added("Red Percent", red)
    _register(sample_map, stage, corners="AB")
    stage.move_to(*_stage_of((1200.0, 800.0)))
    result = sample_map.run("flag_flake", {"flake_quality": "4",
                                           "flake_defects": "bubbles, folds",
                                           "flake_layers": "2",
                                           "flake_note": "near A"})
    assert result.is_ok, result
    flake = sample_map.flakes[0]
    assert flake["label"] == "F01" and flake["sample_id"] == "S1"
    assert (flake["sample_x_um"], flake["sample_y_um"]) == pytest.approx((1200.0, 800.0), abs=1.0)
    assert flake["stage_x"] == stage.position[0]
    assert flake["registration_id"] == sample_map.registration["registration_id"]
    assert flake["red_percent"] == 12.5 and flake["red_run_id"] == "run-7"
    assert flake["quality"] == 4 and flake["defects"] == ["bubbles", "folds"]
    assert flake["layers_estimate"] == 2 and flake["material"] == "WSe2"
    assert flake["owner"] == "station"
    assert open(flake["image_path"], "rb").read() == PNG
    assert sample_map.selected_flake["flake_uid"] == flake["flake_uid"]


def test_a_flake_flagged_before_the_frame_is_kept_stage_only(sample_map, stage):
    assert sample_map.run("flag_flake").is_ok
    flake = sample_map.flakes[0]
    assert flake["sample_x_um"] is None and flake["stage_x"] == stage.position[0]


def test_quality_and_defects_are_refused_in_words(sample_map, stage):
    assert "Quality" in sample_map.run("flag_flake", {"flake_quality": "7"}).reason
    assert "scratches" in sample_map.run("flag_flake",
                                         {"flake_defects": "scratches"}).reason
    assert sample_map.flakes == []


def test_a_flake_is_rated_later_without_an_extent(sample_map, stage):
    assert sample_map.run("flag_flake").is_ok
    assert sample_map.run("rate_flake", {"flake_quality": "5",
                                         "flake_defects": ""}).is_ok
    flake = sample_map.flakes[0]
    assert flake["quality"] == 5 and flake["defects"] == []
    assert flake["extent_kind"] == "none"


def test_mark_extent_twice_draws_the_flakes_box_in_the_chip_frame(sample_map, stage):
    _register(sample_map, stage, corners="AB")
    stage.move_to(*_stage_of((1000.0, 1000.0)))
    sample_map.run("flag_flake")
    stage.move_to(*_stage_of((990.0, 990.0)))
    assert "the opposite corner" in str(sample_map.run("mark_extent").value)
    stage.move_to(*_stage_of((1050.0, 1030.0)))
    assert sample_map.run("mark_extent").is_ok
    flake = sample_map.selected_flake
    assert flake["extent_kind"] == "bbox" and flake["extent_source"] == "stage_corners"
    xs = [p[0] for p in flake["extent_points_um"]]
    assert min(xs) == pytest.approx(990, abs=1.5) and max(xs) == pytest.approx(1050, abs=1.5)
    assert "lateral" in sample_map.flake_text and "um" in sample_map.flake_text


def test_the_extent_needs_the_frame(sample_map, stage):
    sample_map.run("flag_flake")
    assert "Mark corners A and B" in sample_map.run("mark_extent").reason


def test_flake_details_keep_approximate_and_afm_thickness_apart(sample_map, stage):
    sample_map.run("flag_flake")
    assert "red_percent" not in sample_map.thickness_method_options
    assert sample_map.run("set_thickness_method", None, ("optical_contrast",)).is_ok
    assert sample_map.run("save_flake_details", {"thickness_approx_nm": "0.7",
                                                 "thickness_afm_nm": "1.1",
                                                 "thickness_afm_sigma_nm": "0.1"}).is_ok
    flake = sample_map.flakes[0]
    assert (flake["thickness_approx_nm"], flake["thickness_approx_method"]) == \
        (0.7, "optical_contrast")
    assert (flake["thickness_afm_nm"], flake["thickness_afm_sigma_nm"]) == (1.1, 0.1)


def test_status_and_delete(sample_map, stage):
    sample_map.run("flag_flake")
    assert sample_map.run("set_flake_status", None, ("selected",)).is_ok
    assert sample_map.flakes[0]["status"] == "selected"
    assert sample_map.run("set_flake_status", None, ("reserved",)).is_refused
    assert sample_map.run("delete_flake").needs_confirm
    assert sample_map.run("delete_flake", None, (True,)).is_ok
    assert sample_map.flakes == []


# -- guidance (owner ruling 2026-10-04: guidance only, no go-to) -----------------------------

def test_guidance_names_the_move_to_the_selected_flake(sample_map, stage):
    _register(sample_map, stage, corners="AB")
    stage.move_to(*_stage_of((2000.0, 1000.0)))
    sample_map.run("flag_flake")
    stage.move_to(*_stage_of((0.0, 0.0)))
    text = sample_map.guidance
    flake = sample_map.selected_flake
    target = sample_map.frame().to_stage((flake["sample_x_um"], flake["sample_y_um"]))
    dx = round(target[0] - stage.position[0])
    assert text.startswith("To F01: X ") and f"{dx:+d} counts" in text, text
    assert "mm" in text
    stage.move_to(*target)
    assert sample_map.guidance == "On F01"


def test_nothing_on_the_sheet_moves_the_stage(sample_map):
    commands = {e.get("command") for e in __import__("schema").elements(sample_map.schema)}
    assert not {c for c in commands if c and ("go_to" in c or "move" in c or c == "jog")}


def test_the_manual_rig_registers_from_typed_micrometer_readings():
    model = SampleMap()
    model.open()
    try:
        assert model.run("set_source", None, (TYPED,)).is_ok
        model.run("save_sample", {"sample_id": "M1"})
        for label, (x, y) in (("A", (10.0, 20.0)), ("B", (15.0, 20.0))):
            assert model.run("mark_corner", {"reading_x_mm": str(x),
                                             "reading_y_mm": str(y)}, (label,)).is_ok
        assert model.mode_name == "registered"
        reg = model.registration
        assert reg["frame_source"] == "manual:micrometer" and reg["k_x_um"] == 1000.0
        assert reg["position_epoch"] is None
        model.run("flag_flake", {"reading_x_mm": "12.0", "reading_y_mm": "21.0"})
        assert model.flakes[0]["sample_x_um"] == pytest.approx(2000.0)
        model.reading_x_mm, model.reading_y_mm = "10.0", "20.0"
        assert model.guidance.startswith("To F01: set X to 12.000 mm"), model.guidance
    finally:
        model.close()


# -- figure, logs, export -------------------------------------------------------------------------

def test_the_sample_figure_draws_once_corners_exist(sample_map, stage):
    assert sample_map.sample_figure == b""
    _register(sample_map, stage, corners="ABD")
    stage.move_to(*_stage_of((1000.0, 1000.0)))
    sample_map.run("flag_flake")
    png = sample_map.sample_figure
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_logs_list_samples_and_flakes(sample_map, stage):
    _register(sample_map, stage, corners="AB")
    stage.move_to(*_stage_of((100.0, 200.0)))
    sample_map.run("flag_flake", {"flake_quality": "3"})
    assert any("S1" in line and "WSe2" in line for line in sample_map.samples_log)
    (line,) = sample_map.flakes_log
    assert "F01" in line and "q3" in line and "(100, 200) um" in line


def test_export_and_import_round_trip(sample_map, stage, tmp_path):
    _register(sample_map, stage, corners="AB")
    sample_map.run("flag_flake")
    path = sample_map.export_json()
    document = json.loads(open(path).read())
    assert document["schema"] == "flake-coords/1"
    assert sample_map.export_csv().endswith("_flakes.csv")
    other = SampleMap(db_path=tmp_path / "other.sqlite")
    other.open()
    try:
        counts = other.run("import_json", None, (path,)).value
        assert counts["added"] == 3
        assert other._store.flakes()[0]["flake_uid"] == sample_map.flakes[0]["flake_uid"]
    finally:
        other.close()


def test_a_stop_does_nothing_to_the_store(sample_map, stage):
    sample_map.run("flag_flake")
    sample_map.estop()
    assert sample_map.flakes and sample_map.is_estopped
    sample_map.clear_estop(True)


def test_next_step_walks_the_sheet(stage):
    model = SampleMap()
    model.open()
    try:
        assert model.next_step == "Open a probe or the Chuck Positioner, or pick Typed readings"
        model.on_model_added(stage.NAME, stage)
        assert model.next_step == "Type the sample ID and press Save sample"
        model.run("save_sample", {"sample_id": "S1"})
        assert "corner A" in model.next_step
        _mark(model, stage, "A", (0, 0))
        assert "corner B" in model.next_step
        _mark(model, stage, "B", (5000, 0))
        assert "Flag" in model.next_step
    finally:
        model.close()


def test_owner_follows_the_signed_in_user(sample_map, stage):
    sample_map.owner = "ialbinog"
    sample_map.run("flag_flake")
    assert sample_map.flakes[0]["owner"] == "ialbinog"
