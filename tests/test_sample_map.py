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


# -- the Rotator turns the chip (owner 2026-10-04: model it now) -----------------------

class FakeRotator:
    """The SMC100 Rotator, duck-typed as the Sample Map reads it: the angle
    (`position_deg`, None for no reading) and its `motion_state` words."""

    def __init__(self, angle=0.0):
        self.NAME = "Rotator"
        self.position_deg = angle
        self.motion_state = "Ready"


ROT_CENTRE = (6_500.0, 4_100.0)       # counts: where the Rotator turns the chip
ROT_SENSE = -1                         # its sense against the stage axes


def _turned(p, dphi):
    a = math.radians(ROT_SENSE * dphi)
    c, s = math.cos(a), math.sin(a)
    dx, dy = p[0] - ROT_CENTRE[0], p[1] - ROT_CENTRE[1]
    return (ROT_CENTRE[0] + c * dx - s * dy, ROT_CENTRE[1] + s * dx + c * dy)


def _go(stage, q, rotator):
    """Put the crosshair on chip point `q` with the chip turned to the
    Rotator's angle (registered at 0)."""
    stage.move_to(*_turned(_stage_of(q), rotator.position_deg))


@pytest.fixture
def rotator(sample_map):
    rot = FakeRotator(0.0)
    sample_map.on_model_added(rot.NAME, rot)
    return rot


def _calibrate(model, stage, rotator, angles=(-15.0, 0.0, 12.0)):
    feature = (2_000.0, 1_500.0)               # any feature on the chip
    for a in angles:
        rotator.position_deg = a
        _go(stage, feature, rotator)
        result = model.run("mark_rotation_point")
        assert result.is_ok, result
    result = model.run("fit_rotation_centre")
    assert result.is_ok, result
    rotator.position_deg = 0.0


def test_a_registration_records_the_rotator_angle(sample_map, stage, rotator):
    rotator.position_deg = 5.0
    _register(sample_map, stage, corners="AB")
    reg = sample_map.registration
    assert reg["rotator_phi0_deg"] == 5.0 and reg["rotator_name"] == "Rotator"
    assert sample_map.mode_name == "registered"


@pytest.mark.parametrize("state", (None, "Not referenced - run Home",
                                   "Communication lost", "Homing"))
def test_an_unknown_rotator_angle_blocks_marks_and_guidance(sample_map, stage, rotator, state):
    _register(sample_map, stage, corners="AB")
    stage.move_to(*_stage_of((1_000, 1_000)))
    assert sample_map.run("flag_flake").is_ok
    if state is None:
        rotator.position_deg = None
    else:
        rotator.motion_state = state
    assert sample_map.mode_name == "rotator_unknown"
    assert sample_map.guidance == ""
    assert not sample_map.run("mark_corner", None, ("C",)).is_ok
    assert not sample_map.run("flag_flake").is_ok
    assert "rotator_unknown" in SampleMap.GATE_REASONS
    from views.base import GATE_WORDS
    assert GATE_WORDS["rotator_unknown"][0] and GATE_WORDS["rotator_unknown"][1] is None


def test_a_registration_made_with_the_rotator_waits_while_it_is_closed(sample_map, stage, rotator):
    _register(sample_map, stage, corners="AB")
    sample_map.on_model_removed(rotator.NAME, rotator)
    assert sample_map.mode_name == "rotator_unknown"
    sample_map.on_model_added(rotator.NAME, rotator)
    assert sample_map.mode_name == "registered"


def test_an_uncalibrated_turn_ends_the_registration(sample_map, stage, rotator):
    _register(sample_map, stage, corners="AB")
    first = sample_map.registration["registration_id"]
    rotator.position_deg = 10.0
    valid, reason = sample_map._validity()
    assert not valid and "Rotator turned" in reason and "calibrate" in reason
    assert sample_map.mode_name == "unregistered"
    assert sample_map.guidance == ""
    _go(stage, (0, 0), rotator)
    assert sample_map.run("mark_corner", None, ("A",)).is_ok
    old = sample_map._store.registration(first)
    assert old["invalidated_at"] and "Rotator turned" in old["invalidated_reason"]
    assert sample_map.registration["rotator_phi0_deg"] == 10.0


def test_turning_back_to_phi0_without_a_command_is_still_valid(sample_map, stage, rotator):
    """The SMC100's angle is absolute after homing: back at phi0 the marks
    are right again, so a read alone never ends a registration."""
    _register(sample_map, stage, corners="AB")
    rotator.position_deg = 10.0
    assert sample_map.mode_name == "unregistered"
    rotator.position_deg = 0.0
    assert sample_map.mode_name == "registered"


def test_a_calibrated_turn_keeps_guidance_on_the_flake(sample_map, stage, rotator):
    _register(sample_map, stage, corners="AB")
    _calibrate(sample_map, stage, rotator)
    cal = sample_map._calibration()
    assert cal is not None and cal["sense"] == ROT_SENSE
    assert (cal["centre_x"], cal["centre_y"]) == pytest.approx(ROT_CENTRE, abs=3.0)
    q = (3_000.0, 1_000.0)
    stage.move_to(*_stage_of(q))
    assert sample_map.run("flag_flake").is_ok
    rotator.position_deg = 20.0
    assert sample_map.mode_name == "registered"
    _go(stage, q, rotator)                     # the crosshair on the turned flake
    assert sample_map.guidance.startswith("On F01")
    here = sample_map.frame().to_sample(stage.position[:2])
    assert here == pytest.approx(q, abs=5.0)
    stage.move_to(*_turned(_stage_of((0, 0)), 20.0))
    target = sample_map.frame().to_stage(q)
    assert target == pytest.approx(_turned(_stage_of(q), 20.0), abs=3.0)


def test_a_flake_flagged_after_a_turn_lands_in_the_chip_frame(sample_map, stage, rotator):
    _register(sample_map, stage, corners="AB")
    _calibrate(sample_map, stage, rotator)
    rotator.position_deg = -12.0
    q = (2_500.0, 800.0)
    _go(stage, q, rotator)
    assert sample_map.run("flag_flake").is_ok
    flake = sample_map.selected_flake
    assert (flake["sample_x_um"], flake["sample_y_um"]) == pytest.approx(q, abs=5.0)


def test_a_corner_marked_after_a_calibrated_turn_is_stored_at_phi0(sample_map, stage, rotator):
    _register(sample_map, stage, corners="AB")
    _calibrate(sample_map, stage, rotator)
    rotator.position_deg = 15.0
    _go(stage, (0, 4_000), rotator)
    assert sample_map.run("mark_corner", None, ("D",)).is_ok
    corners = sample_map._corner_points(sample_map.registration)
    assert corners["D"] == pytest.approx(_stage_of((0, 4_000)), abs=3.0)
    assert sample_map.registration["rotator_phi0_deg"] == 0.0


def test_the_calibration_expires_with_the_stage_epoch(sample_map, stage, rotator):
    _register(sample_map, stage, corners="AB")
    _calibrate(sample_map, stage, rotator)
    assert sample_map._calibration() is not None
    stage.position_epoch = 2                   # the probe reconnected
    assert sample_map._calibration() is None
    assert "expired" in sample_map.rotator_text or "Calibrate" in sample_map.rotator_text


def test_two_marks_reuse_the_sense_of_an_earlier_calibration(sample_map, stage, rotator):
    _register(sample_map, stage, corners="AB")
    feature = (2_000.0, 1_500.0)

    def two_marks():
        for a in (0.0, 20.0):
            rotator.position_deg = a
            _go(stage, feature, rotator)
            assert sample_map.run("mark_rotation_point").is_ok
        rotator.position_deg = 0.0
    two_marks()                                # no earlier calibration: refused
    refused = sample_map.run("fit_rotation_centre")
    assert not refused.is_ok and "third angle" in str(refused)
    assert sample_map.run("clear_rotation_points").is_ok
    _calibrate(sample_map, stage, rotator)     # three marks: the sense is known now
    two_marks()
    assert sample_map.run("fit_rotation_centre").is_ok
    cal = sample_map._calibration()
    assert cal["method"] == "chord" and cal["sense"] == ROT_SENSE
    assert (cal["centre_x"], cal["centre_y"]) == pytest.approx(ROT_CENTRE, abs=3.0)


@pytest.mark.parametrize("offset_um, word", ((3.0, "good"), (50.0, "poor")))
def test_a_check_after_a_turn_reports_the_rotator_closure(sample_map, stage, rotator,
                                                          offset_um, word):
    _register(sample_map, stage, corners="AB")
    _calibrate(sample_map, stage, rotator)
    rotator.position_deg = 18.0
    predicted = _turned(_stage_of((0, 0)), 18.0)
    stage.move_to(predicted[0] + offset_um / K, predicted[1])
    assert sample_map.run("check_corner").is_ok
    reg = sample_map.registration
    assert reg["rotator_closure_um"] == pytest.approx(offset_um, abs=3.0)
    assert reg["rotator_quality"] == word
    assert reg["rotator_calibration_uid"] == sample_map._calibration()["calibration_uid"]
    assert reg["closure_um"] is None           # the unrotated closure is untouched
    assert "Rotator closure" in sample_map.check_text


def test_a_registration_made_without_a_rotator_is_stale_once_one_opens(sample_map, stage):
    _register(sample_map, stage, corners="AB")
    assert sample_map.registration["rotator_phi0_deg"] is None
    sample_map.on_model_added("Rotator", FakeRotator(3.0))
    valid, reason = sample_map._validity()
    assert not valid and "without the Rotator" in reason


def test_the_rotator_fields_are_exported(sample_map, stage, rotator):
    _register(sample_map, stage, corners="AB")
    _calibrate(sample_map, stage, rotator)
    rotator.position_deg = 18.0
    stage.move_to(*_turned(_stage_of((0, 0)), 18.0))
    assert sample_map.run("check_corner").is_ok
    doc = sample_map._store.export_document("bench", "dev")
    reg = doc["registrations"][-1]
    for key in ("rotator_phi0_deg", "rotator_name", "rotator_calibration_uid",
                "rotator_closure_um", "rotator_quality"):
        assert key in reg
    assert "rotator_calibrations" not in doc        # station-only (Q4)
