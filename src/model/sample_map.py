"""The Sample Map: where on the chip each flake is (proposal-flake-coordinates.md).

The operator marks the chip's corners with the optical **crosshair** (owner
2026-10-04: the tip never touches a corner), the station fits the chip's
frame (`model.sample_frame`), and every flake flagged afterwards is stored
in that frame, so it can be found again after the chip is remounted:
re-mark the corners, pick the flake, and follow the **guidance** readout.
Guidance only (owner ruling 2026-10-04): nothing here commands another
model's motion; automatic go-to is revisited after zeroing lands.

A model with no hardware, like the Transfer Map: it owns its store
(`data/sample_map.sqlite`, `model.sample_store`) and reads its peers by duck
type. Locating axes are any model with `position`, `position_time`,
`position_age` and `velocity` (a probe, the Chuck Positioner); their
`position_epoch` (the core change of section 6) says when the firmware's
counter restarted, which ends a registration. A rig without probes types
its micrometer readings instead ("Typed readings", section 8). Red Percent,
when open, gives the red reading and the picture at the flag.

Bench facts (section 11) are never guessed: the chuck's and the DC probe's
um per count raise `BenchFactMissing` until the owner measures them (or the
chip's typed dimensions let the affine fit absorb the scale).
"""
import datetime
import math
import sys
import threading
import time
from pathlib import Path

import schema as sch
from events import events
from model import plot_data
from model import sample_frame as sf
from model import sample_store as ss
from model.base import Model
from param import Param
from result import NeedsConfirm, Refused

#: The locating source of a rig without probes (section 8): readings typed
#: from the micrometers, in mm.
TYPED = "Typed readings"
#: A typed reading is in mm; the frame works in um.
UM_PER_MM = 1000.0
#: A mark is refused while the stage moves or its position is older than this.
SETTLE_AGE_S = 0.5
#: Guidance says "On F03" inside this distance (um), about the pointing error.
ON_TARGET_UM = 10.0
#: Which `sample_frame.UM_PER_COUNT` entry a locating model's counts use.
STAGE_KINDS = {"Stepper Probe": "stepper", "Chuck Positioner": "chuck",
               "DC Probe": "dc"}
CORNERS = ("A", "B", "C", "D")
CHECK = "A'"


def _number(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class SampleMap(Model):
    NAME = "Sample Map"
    IDENTITY = None
    NEEDS_PORT = False
    NEEDS_GAMEPAD = False
    RESOURCES = ()
    HOST = None

    GATE_REASONS = {
        **Model.GATE_REASONS,
        "no_source": "no locating axes are open. Open a probe or the Chuck "
                     "Positioner, or pick Typed readings.",
        "unregistered": "the chip's frame is not set. Mark corners A and B "
                        "first.",
    }

    PARAMS = {p.name: p for p in (
        Param("sample_id", "text", default="", label="Sample ID"),
        Param("material", "text", default="", label="Material"),
        Param("substrate", "text", default="", label="Substrate"),
        Param("chip_width_um", "float", default=0.0, minimum=0, decimals=1,
              unit="um", label="Chip width (typed)"),
        Param("chip_height_um", "float", default=0.0, minimum=0, decimals=1,
              unit="um", label="Chip height (typed)"),
        Param("orientation_note", "text", default="", label="How to find corner A"),
        Param("storage_location", "text", default="", label="Stored at"),
        # Station-only (Q4): blank = the table's value for the locating axes.
        Param("um_per_count", "text", default="", label="um per count"),
        Param("reading_x_mm", "text", default="", label="X reading (mm)"),
        Param("reading_y_mm", "text", default="", label="Y reading (mm)"),
        Param("flake_material", "text", default="", label="Flake material"),
        Param("flake_layers", "text", default="", label="Layers (estimate)"),
        Param("flake_quality", "int", default=0, minimum=0, maximum=5,
              label="Quality"),
        Param("flake_defects", "text", default="", label="Defects"),
        Param("flake_note", "text", default="", label="Flake note"),
        Param("thickness_approx_nm", "float", default=0.0, minimum=0, decimals=2,
              unit="nm", label="Approximate thickness"),
        Param("thickness_afm_nm", "float", default=0.0, minimum=0, decimals=2,
              unit="nm", label="AFM thickness"),
        Param("thickness_afm_sigma_nm", "float", default=0.0, minimum=0,
              decimals=2, unit="nm", label="AFM thickness uncertainty"),
    )}

    def __init__(self, port=None, gamepad=None, sim=False, db_path=None):
        super().__init__()
        self.sim = sim
        self.db_path = Path(db_path) if db_path else self.default_db_path()
        self.output_root = self.db_path.parent
        self._store = ss.SampleStore(self.db_path)
        self._lock = threading.RLock()
        self._stages = {}              # name -> locating model, Controller order
        self._source = None            # a stage name, TYPED, or None
        self._red = None
        self._red_name = None
        self._extent_first = None      # (flake_uid, stage point) between presses
        self._selected = None          # flake_uid
        self._shape = ss.SHAPES[0]
        self._thickness_method = ss.THICKNESS_APPROX_METHODS[0]
        #: Who flags (`flakes.owner`): "station" until the user system's
        #: profiles set the signed-in operator.
        self.owner = "station"

    @staticmethod
    def default_db_path():
        """`STATION_SAMPLE_DB`, else `data/sample_map.sqlite` beside the
        Transfer Map's store (a bundle: beside the executable)."""
        import os
        configured = os.environ.get("STATION_SAMPLE_DB")
        if configured:
            return Path(configured).expanduser().resolve()
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / "data" / "sample_map.sqlite"
        return Path(__file__).resolve().parents[2] / "data" / "sample_map.sqlite"

    # -- the Model contract ----------------------------------------------------
    @property
    def devices(self):
        return []

    def _expects_heartbeat(self):
        return False                   # no loop of its own

    def _halt_hardware(self):
        return True                    # moves nothing, records nothing live

    def open(self):
        super().open()
        try:
            self._store.ensure()
        except Exception as exc:
            events.error("Database Not Ready", f"The Sample Map database could "
                         f"not be created at {self.db_path}. Check that the folder "
                         "can be written, or start with --sample-db PATH.",
                         source=self.NAME, exception=exc)
            return
        events.info("Database Ready", f"{self.db_path}: "
                    f"{len(self._store.samples())} sample(s), "
                    f"{len(self._store.flakes())} flake(s)", source=self.NAME)

    @property
    def mode_name(self):
        if self._source is None:
            return "no_source"
        return "registered" if self._validity()[0] else "unregistered"

    # -- peers -----------------------------------------------------------------------
    def on_model_added(self, name, model):
        if all(hasattr(model, a) for a in ("position", "position_time",
                                            "position_age", "velocity")):
            self._stages[name] = model
            if self._source is None:
                self._source = name
            self._touch()
        if callable(getattr(model, "grab_frame", None)) and \
                hasattr(model, "current_red"):
            self._red, self._red_name = model, name

    def on_model_removed(self, name, model=None):
        if name == self._red_name:
            self._red, self._red_name = None, None
        if name in self._stages:
            del self._stages[name]
            reg = self.registration
            if reg is not None and reg["frame_source"] == "stage:" + name:
                self._store.invalidate_registration(
                    reg["registration_id"], f"the {name} was closed")
            if self._source == name:
                self._source = next(iter(self._stages), None)
            self._touch()

    @property
    def source(self):
        return self._source or ""

    @property
    def source_options(self):
        return [*self._stages, TYPED]

    def set_source(self, name):
        if name not in self.source_options:
            raise Refused(f"{name!r} is not open as locating axes.")
        self._source = name
        self._touch()
        return name

    # -- reading the position ---------------------------------------------------------
    def _frame_source(self):
        if self._source == TYPED:
            return "manual:micrometer"
        return "stage:" + str(self._source)

    def _read_position(self):
        """(x, y, z, epoch) in the source's units (counts, or mm typed), or
        Refused: no source, a moving or stale stage, a blank reading."""
        if self._source is None:
            raise Refused("No locating axes are open. Open a probe or the Chuck "
                          "Positioner, or pick Typed readings.")
        if self._source == TYPED:
            x, y = _number(self.reading_x_mm), _number(self.reading_y_mm)
            if x is None or y is None:
                raise Refused("Type the X and Y micrometer readings in mm first.")
            return x, y, None, None
        stage = self._stages.get(self._source)
        if stage is None:
            raise Refused(f"The {self._source} is not open.")
        velocity = getattr(stage, "velocity", None) or (0, 0, 0)
        if any(abs(float(v)) > 0 for v in velocity):
            raise Refused(f"The {self._source} is moving. Wait for it to stop, "
                          "then mark.")
        age = getattr(stage, "position_age", None)
        if age is None or age > SETTLE_AGE_S:
            raise Refused(f"The {self._source}'s position is stale (no reading "
                          "for a moment). Check it is connected, then mark.")
        x, y, z = stage.position
        return float(x), float(y), z, getattr(stage, "position_epoch", None)

    def _current_point(self):
        """The stage now, without refusing (readouts): (x, y) or None."""
        try:
            x, y, _z, _epoch = self._read_position()
        except Refused:
            return None
        return (x, y)

    def _k(self):
        """um per stage unit for the current source: typed (station-only, Q4),
        the micrometer's 1000 um per mm, or the table; BenchFactMissing when
        the owner has not measured it."""
        if self._source == TYPED:
            return (UM_PER_MM, UM_PER_MM)
        typed = _number(self.um_per_count) if str(self.um_per_count).strip() else None
        kind = STAGE_KINDS.get(str(self._source), str(self._source))
        k = sf.um_per_count(kind, typed=typed)
        return (k, k)

    # -- the sample ------------------------------------------------------------------
    def _sample(self):
        sample_id = str(self.sample_id or "").strip()
        sample = self._store.sample(sample_id) if sample_id else None
        if sample is None:
            raise Refused("Type the sample ID (Red Percent's specimen ID) and "
                          "press Save sample first.")
        return sample

    def save_sample(self):
        fields = {"sample_id": self.sample_id, "material": self.material or None,
                  "substrate": self.substrate or None, "shape": self._shape,
                  "width_um": float(self.chip_width_um) or None,
                  "height_um": float(self.chip_height_um) or None,
                  "orientation_note": self.orientation_note or None,
                  "storage_location": self.storage_location or None,
                  "owner": self.owner}
        try:
            sample_id = self._store.put_sample(fields)
        except ss.StoreRefused as refusal:
            raise Refused(str(refusal))
        self.sample_id = sample_id
        self._refit()
        self._touch()
        events.info("Sample Saved", f"Sample {sample_id} saved.", source=self.NAME)
        return sample_id

    @property
    def shape(self):
        return self._shape

    @property
    def shape_options(self):
        return list(ss.SHAPES)

    def set_shape(self, shape):
        if shape not in ss.SHAPES:
            raise Refused(f"{shape!r} is not a chip shape.")
        self._shape = shape
        self._touch()
        return shape

    # -- registration ------------------------------------------------------------------
    @property
    def registration(self):
        """The sample's open registration (the newest one not ended), or None."""
        sample_id = str(self.sample_id or "").strip()
        if not sample_id:
            return None
        open_ones = [r for r in self._store.registrations(sample_id)
                     if not r["invalidated_at"]]
        return open_ones[-1] if open_ones else None

    def _corner_points(self, reg):
        return {c["label"]: (c["stage_x"], c["stage_y"])
                for c in self._store.corners(reg["registration_id"])}

    def _validity(self):
        """(True, "") when the open registration's frame can be used now,
        else (False, the reason in the operator's words)."""
        reg = self.registration
        if reg is None:
            return False, "Not registered: mark corners A and B."
        stale = self._stale_reason(reg)
        if stale:
            return False, "Not valid: " + stale
        if reg["fit_kind"] is None:
            corners = self._corner_points(reg)
            missing = [c for c in "AB" if c not in corners]
            if missing:
                return False, f"Not registered: mark corner {missing[0]}."
            try:
                self._k()
            except sf.BenchFactMissing as missing_fact:
                return False, f"Not registered: {missing_fact}"
            return False, "Not registered."
        return True, ""

    def _stale_reason(self, reg):
        """Why `reg` can never be used again (its axes closed or reconnected,
        or another source is picked), or "" when it still can."""
        source = reg["frame_source"]
        if source.startswith("stage:"):
            name = source[len("stage:"):]
            stage = self._stages.get(name)
            if stage is None:
                return f"the {name} is not open."
            epoch = getattr(stage, "position_epoch", None)
            if reg["position_epoch"] is not None and epoch != reg["position_epoch"]:
                return (f"the {name}'s position counter restarted (it reconnected). "
                        "Mark the corners again.")
        if source != self._frame_source():
            return (f"it was marked with {source}, and {self.source or 'no source'} "
                    "is picked now.")
        return ""

    def frame(self):
        """The open registration's transform (sample <-> stage), or None."""
        if not self._validity()[0]:
            return None
        reg = self.registration
        if reg["fit_kind"] == "rigid":
            return sf.RigidFrame((reg["origin_stage_x"], reg["origin_stage_y"]),
                                 reg["theta_rad"], (reg["k_x_um"], reg["k_y_um"]),
                                 reg["handedness"] or 1)
        return sf.FittedFrame(reg["fit_kind"], [[reg["a11"], reg["a12"]],
                                                [reg["a21"], reg["a22"]]],
                              [reg["t_x"], reg["t_y"]], reg["residual_rms_um"] or 0.0,
                              um_per_count=reg["scale"])

    def _fit(self, corners, sample):
        """The registration fields for `corners` (label -> stage point), or
        None when A and B are not both marked or the scale is unknown.
        FrameRefused for degenerate corners."""
        try:
            k = self._k()
        except sf.BenchFactMissing:
            k = None
        typed = (sample.get("width_um"), sample.get("height_um"))
        marked = [c for c in CORNERS if c in corners]
        if all(typed) and len(marked) >= 3:
            w, h = typed
            ideal = {"A": (0, 0), "B": (w, 0), "C": (w, h), "D": (0, h)}
            fit = sf.affine_fit([corners[c] for c in marked], [ideal[c] for c in marked])
            m = fit.matrix
            fields = {"fit_kind": "affine", "a11": m[0][0], "a12": m[0][1],
                      "a21": m[1][0], "a22": m[1][1], "t_x": fit.shift[0],
                      "t_y": fit.shift[1], "residual_rms_um": fit.rms_um,
                      "k_x_um": k[0] if k else None, "k_y_um": k[1] if k else None}
        elif "A" in corners and "B" in corners and k is not None:
            frame = sf.rigid_frame(corners["A"], corners["B"], k, d=corners.get("D"))
            fields = {"fit_kind": "rigid", "k_x_um": k[0], "k_y_um": k[1],
                      "origin_stage_x": corners["A"][0], "origin_stage_y": corners["A"][1],
                      "theta_rad": frame.theta, "handedness": frame.handedness}
            if "D" in corners:
                dims = sf.dimensions(corners["A"], corners["B"], corners["D"], k)
                fields.update(derived_width_um=dims["width_um"],
                              derived_height_um=dims["height_um"],
                              angle_a_deg=dims["angle_at_a_deg"])
            else:
                fields["derived_width_um"] = float(math.dist(
                    [corners["A"][0] * k[0], corners["A"][1] * k[1]],
                    [corners["B"][0] * k[0], corners["B"][1] * k[1]]))
            if all(c in corners for c in CORNERS):
                fields["rectangularity_um"] = sf.rectangularity(
                    {c: corners[c] for c in CORNERS}, k)
        else:
            if "A" in corners and "B" in corners:
                sf.rigid_frame(corners["A"], corners["B"], (1.0, 1.0),
                               d=corners.get("D"))        # refuse degenerate now
            return {"fit_kind": None}
        if CHECK in corners and fields.get("k_x_um"):
            k = (fields["k_x_um"], fields["k_y_um"])
            closure = sf.closure_um(corners["A"], corners[CHECK], k)
            fields.update(closure_um=closure, quality=sf.closure_word(closure))
        else:
            fields["quality"] = "unchecked"
        return fields

    def _refit(self):
        reg = self.registration
        if reg is None:
            return
        try:
            sample = self._sample()
        except Refused:
            return
        try:
            fields = self._fit(self._corner_points(reg), sample)
        except sf.FrameRefused:
            return
        self._store.update_registration(reg["registration_id"], fields)

    def _end_stale_registration(self):
        """Record why an open registration can no longer be used (a reconnect,
        another source) before a new one starts. Commands only: a read never
        writes."""
        reg = self.registration
        if reg is None:
            return None
        stale = self._stale_reason(reg)
        if not stale:
            return reg
        self._store.invalidate_registration(reg["registration_id"], stale)
        return None

    def mark_corner(self, label, confirmed=False):
        """The crosshair is on corner `label`: record the locating axes'
        position (raw counts or typed mm) and refit the chip's frame."""
        if label not in CORNERS:
            raise Refused(f"{label!r} is not a corner: use A, B, C or D.")
        with self._lock:
            sample = self._sample()
            x, y, z, epoch = self._read_position()
            reg = self._end_stale_registration()
            corners = self._corner_points(reg) if reg is not None else {}
            if label in corners and not confirmed:
                old = corners[label]
                try:
                    k = self._k()
                    distance = f"{math.dist((old[0] * k[0], old[1] * k[1]), (x * k[0], y * k[1])):.0f} um"
                except sf.BenchFactMissing:
                    distance = f"{math.dist(old, (x, y)):.0f} counts"
                raise NeedsConfirm(f"Corner {label} is already marked. Replace it? "
                                   f"The new mark is {distance} from the old one.",
                                   "mark_corner", args=(label,))
            corners[label] = (x, y)
            try:
                fields = self._fit(corners, sample)
            except sf.FrameRefused as refusal:
                raise Refused(str(refusal))
            corner = {"label": label, "stage_x": x, "stage_y": y, "stage_z": z,
                      "method": "typed" if self._source == TYPED else "crosshair",
                      "image_path": self._picture(sample["sample_id"], f"corner_{label}")}
            if reg is None:
                rid = self._store.add_registration(
                    {"sample_id": sample["sample_id"], "frame_source": self._frame_source(),
                     "position_epoch": epoch, **fields}, [corner])
            else:
                rid = reg["registration_id"]
                self._store.put_corner(rid, corner)
                self._store.update_registration(rid, fields)
        self._touch()
        events.info("Corner Marked", f"{sample['sample_id']}: corner {label} at "
                    f"({x:g}, {y:g}).", source=self.NAME)
        return rid

    def check_corner(self):
        """Return to corner A and mark it again: the closure measures stage
        repeatability plus pointing (section 3.2)."""
        with self._lock:
            sample = self._sample()
            reg = self.registration
            if reg is None or not self._validity()[0]:
                raise Refused("Mark corners A and B first.")
            x, y, z, _epoch = self._read_position()
            corners = self._corner_points(reg)
            corners[CHECK] = (x, y)
            fields = self._fit(corners, sample)
            self._store.put_corner(reg["registration_id"], {
                "label": CHECK, "stage_x": x, "stage_y": y, "stage_z": z,
                "method": "typed" if self._source == TYPED else "crosshair"})
            self._store.update_registration(reg["registration_id"], fields)
        self._touch()
        return fields.get("closure_um")

    def clear_corners(self, confirmed=False):
        reg = self.registration
        if reg is None:
            raise Refused("There are no corners to clear.")
        if not confirmed:
            raise NeedsConfirm(f"Clear the corners of {reg['sample_id']}? The flakes "
                               "keep their places on the chip; the corners must be "
                               "marked again before guidance works.", "clear_corners")
        self._store.invalidate_registration(reg["registration_id"],
                                            "cleared by the operator")
        self._touch()
        return reg["registration_id"]

    def set_um_per_count(self):
        """Station-only (Q4): the um per count typed for these axes, when the
        table does not know it (an owner bench fact, section 11)."""
        text = str(self.um_per_count or "").strip()
        if text:
            value = _number(text)
            if value is None or value <= 0:
                raise Refused("Type the um per count as a positive number, or "
                              "leave it blank for the table's value.")
        self._refit()
        self._touch()
        return text

    # -- flakes -----------------------------------------------------------------------
    @property
    def flakes(self):
        sample_id = str(self.sample_id or "").strip()
        return self._store.flakes(sample_id) if sample_id else []

    @property
    def selected_flake(self):
        return self._store.flake(self._selected) if self._selected else None

    @property
    def flake_pick(self):
        flake = self.selected_flake
        return flake["label"] if flake else ""

    @property
    def flake_options(self):
        return [f["label"] for f in self.flakes]

    def select_flake(self, label):
        match = [f for f in self.flakes if f["label"] == label]
        if not match:
            raise Refused(f"No flake {label} on this sample.")
        self._selected = match[0]["flake_uid"]
        self._extent_first = None
        self._touch()
        return label

    def _require_flake(self):
        flake = self.selected_flake
        if flake is None or flake.get("deleted_at"):
            raise Refused("Flag a flake, or pick one, first.")
        return flake

    @staticmethod
    def _defects(text, blank):
        text = str(text or "").strip()
        if not text:
            return blank
        return [d.strip() for d in text.replace(";", ",").split(",") if d.strip()]

    def _picture(self, sample_id, name):
        """Red Percent's capture region as a PNG under data/sample_map/<sample>/,
        or None when Red Percent is not open or the grab fails."""
        red = self._red
        if red is None:
            return None
        try:
            png = red.grab_frame()
        except Exception as exc:
            events.debug("Picture Failed", repr(exc), source=self.NAME)
            return None
        if not png:
            return None
        folder = self.output_root / "sample_map" / str(sample_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.png"
        path.write_bytes(png)
        return str(path)

    def flag_flake(self):
        """A flake under the crosshair: its stage position, and its place on the
        chip when the frame is set (else stage only, placed later)."""
        with self._lock:
            sample = self._sample()
            x, y, z, _epoch = self._read_position()
            layers = str(self.flake_layers or "").strip()
            if layers and not layers.isdigit():
                raise Refused("Type the layer estimate as a whole number, or leave "
                              "it blank.")
            frame = self.frame()
            q = frame.to_sample((x, y)) if frame is not None else (None, None)
            reg = self.registration if frame is not None else None
            red = self._red
            fields = {
                "sample_id": sample["sample_id"],
                "sample_x_um": q[0], "sample_y_um": q[1],
                "registration_id": reg["registration_id"] if reg else None,
                "stage_x": x, "stage_y": y, "stage_z": z,
                "quality": int(self.flake_quality) or None,
                "defects": self._defects(self.flake_defects, None),
                "layers_estimate": int(layers) if layers else None,
                "material": (self.flake_material or "").strip() or sample.get("material"),
                "red_percent": getattr(red, "current_red", None) if red else None,
                "red_run_id": getattr(red, "run_id", None) if red else None,
                "owner": self.owner,
                "note": (self.flake_note or "").strip() or None,
            }
            try:
                uid = self._store.add_flake(fields)
            except ss.StoreRefused as refusal:
                raise Refused(str(refusal))
            label = self._store.flake(uid)["label"]
            picture = self._picture(sample["sample_id"], label)
            if picture:
                self._store.update_flake(uid, {"image_path": picture})
            self._selected = uid
            self._extent_first = None
        self._touch()
        where = f"({q[0]:.0f}, {q[1]:.0f}) um" if q[0] is not None else "stage only"
        events.info("Flake Flagged", f"{sample['sample_id']} {label} at {where}.",
                    source=self.NAME)
        return label

    def mark_extent(self):
        """Two presses at opposite corners of the selected flake: its box in
        the chip's frame (section 3.5, the MVP extent)."""
        with self._lock:
            flake = self._require_flake()
            frame = self.frame()
            if frame is None:
                raise Refused("Mark corners A and B first: the extent is drawn in "
                              "the chip's frame.")
            x, y, _z, _epoch = self._read_position()
            first = self._extent_first
            if first is None or first[0] != flake["flake_uid"]:
                self._extent_first = (flake["flake_uid"], (x, y))
                return (f"{flake['label']}: first corner of its extent marked. Move to "
                        "the opposite corner and press Mark extent again.")
            box = sf.bbox_extent(frame, first[1], (x, y))
            self._extent_first = None
            self._store.update_flake(flake["flake_uid"], {
                "extent_kind": "bbox", "extent_source": box["source"],
                "extent_points_um": [list(p) for p in box["polygon_um"]]})
        self._touch()
        return f"{flake['label']}: extent marked."

    def rate_flake(self):
        """Quality 1-5 and the defects seen (Q18: rateable before an extent).
        A rating with no defects typed means "inspected, none seen"."""
        flake = self._require_flake()
        try:
            self._store.update_flake(flake["flake_uid"], {
                "quality": int(self.flake_quality) or None,
                "defects": self._defects(self.flake_defects, [])})
        except ss.StoreRefused as refusal:
            raise Refused(str(refusal))
        self._touch()
        return flake["label"]

    @property
    def thickness_method(self):
        return self._thickness_method

    @property
    def thickness_method_options(self):
        return list(ss.THICKNESS_APPROX_METHODS)

    def set_thickness_method(self, method):
        if method not in ss.THICKNESS_APPROX_METHODS:
            raise Refused(f"{method!r} is not an approximate-thickness method.")
        self._thickness_method = method
        self._touch()
        return method

    def save_flake_details(self):
        """The approximate thickness (optics) and the AFM thickness, kept
        apart (section 4.1a); no red-percent estimate is made (Q16)."""
        flake = self._require_flake()
        approx = float(self.thickness_approx_nm) or None
        afm = float(self.thickness_afm_nm) or None
        fields = {"thickness_approx_nm": approx,
                  "thickness_approx_method": self._thickness_method if approx else None,
                  "thickness_afm_nm": afm,
                  "thickness_afm_sigma_nm": float(self.thickness_afm_sigma_nm) or None}
        if afm is not None and flake.get("thickness_afm_nm") != afm:
            fields.update(afm_measured_at=ss.now(), afm_by=self.owner)
        self._store.update_flake(flake["flake_uid"], fields)
        self._touch()
        return flake["label"]

    @property
    def flake_status(self):
        flake = self.selected_flake
        return flake["status"] if flake else ""

    @property
    def flake_status_options(self):
        return list(ss.FLAKE_STATUSES)

    def set_flake_status(self, status):
        flake = self._require_flake()
        if status not in ss.FLAKE_STATUSES:
            raise Refused(f"{status!r} is not a flake status.")
        self._store.update_flake(flake["flake_uid"], {"status": status})
        self._touch()
        return status

    def delete_flake(self, confirmed=False):
        flake = self._require_flake()
        if not confirmed:
            raise NeedsConfirm(f"Delete flake {flake['label']}? It stays in the store "
                               "as deleted, so an export tells the lab server.",
                               "delete_flake")
        self._store.delete_flake(flake["flake_uid"])
        self._selected = None
        self._touch()
        return flake["label"]

    # -- guidance (owner ruling 2026-10-04: guidance only) ------------------------------
    @property
    def guidance(self):
        """How far the stage is from the selected flake, in the source's own
        numbers; "" when there is no flake placed or no valid frame."""
        flake = self.selected_flake
        frame = self.frame()
        if flake is None or frame is None or flake.get("sample_x_um") is None:
            return ""
        now = self._current_point()
        if now is None:
            return ""
        target = frame.to_stage((flake["sample_x_um"], flake["sample_y_um"]))
        here = frame.to_sample(now)
        if math.dist(here, (flake["sample_x_um"], flake["sample_y_um"])) <= ON_TARGET_UM:
            return f"On {flake['label']}"
        if self._source == TYPED:
            return (f"To {flake['label']}: set X to {target[0]:.3f} mm (now {now[0]:.3f}), "
                    f"Y to {target[1]:.3f} mm (now {now[1]:.3f})")
        dx, dy = round(target[0] - now[0]), round(target[1] - now[1])
        reg = self.registration
        k = (reg["k_x_um"], reg["k_y_um"]) if reg and reg["k_x_um"] else None
        mm = (f" ({dx * k[0] / UM_PER_MM:+.3f} mm)", f" ({dy * k[1] / UM_PER_MM:+.3f} mm)") \
            if k else ("", "")
        return f"To {flake['label']}: X {dx:+d} counts{mm[0]}, Y {dy:+d} counts{mm[1]}"

    # -- readouts --------------------------------------------------------------------
    @property
    def registration_status(self):
        valid, reason = self._validity()
        if not valid:
            return reason
        reg = self.registration
        epoch = (f", epoch {reg['position_epoch']}"
                 if reg["position_epoch"] is not None else "")
        return f"Registered ({reg['quality']}): {reg['frame_source']}{epoch}"

    @property
    def corners_text(self):
        reg = self.registration
        corners = self._corner_points(reg) if reg else {}
        marks = " ".join(c if c in corners else "-" for c in CORNERS)
        return f"Corners: {marks}" + (" (A checked)" if CHECK in corners else "")

    @property
    def frame_text(self):
        reg = self.registration
        if reg is None or not self._validity()[0]:
            return ""
        if reg["angle_a_deg"] is not None and reg["derived_height_um"] is not None:
            return (f"Angle at A: {reg['angle_a_deg']:.1f} deg; width x height: "
                    f"{reg['derived_width_um'] / UM_PER_MM:.2f} x "
                    f"{reg['derived_height_um'] / UM_PER_MM:.2f} mm (derived)")
        if reg["derived_width_um"]:
            return f"Width: {reg['derived_width_um'] / UM_PER_MM:.2f} mm (A to B)"
        return f"Fit: {reg['fit_kind']}, {reg['residual_rms_um'] or 0:.1f} um RMS"

    @property
    def check_text(self):
        reg = self.registration
        if reg is None:
            return ""
        parts = []
        if reg["rectangularity_um"] is not None:
            parts.append(f"Rectangularity: {reg['rectangularity_um']:.0f} um")
            if sf.rectangularity_asks_quad(reg["rectangularity_um"]) \
                    and self.registration_shape() == "rectangle":
                parts[-1] += " (is the chip a quad?)"
        if reg["closure_um"] is not None:
            parts.append(f"Closure: {reg['closure_um']:.0f} um ({reg['quality']})")
        return "; ".join(parts)

    def registration_shape(self):
        sample = self._store.sample(str(self.sample_id or "").strip())
        return (sample or {}).get("shape") or self._shape

    @property
    def position_text(self):
        now = self._current_point()
        if now is None:
            return ""
        unit = "mm" if self._source == TYPED else "counts"
        text = f"Stage: ({now[0]:g}, {now[1]:g}) {unit}"
        frame = self.frame()
        if frame is not None:
            q = frame.to_sample(now)
            text += f"; chip: ({q[0]:.0f}, {q[1]:.0f}) um"
        return text

    @property
    def flake_text(self):
        flake = self.selected_flake
        if flake is None:
            return ""
        parts = [flake["label"]]
        if flake["sample_x_um"] is not None:
            parts.append(f"({flake['sample_x_um']:.0f}, {flake['sample_y_um']:.0f}) um")
        else:
            parts.append("stage only")
        if flake["quality"]:
            parts.append(f"q{flake['quality']}")
        if flake["defects"]:
            parts.append(", ".join(flake["defects"]))
        if flake["extent_points_um"]:
            m = sf.extent_metrics(flake["extent_points_um"])
            parts.append(f"{flake['extent_kind']} {m['area_um2']:.0f} um2, "
                         f"lateral {m['lateral_um']:.0f} um")
        return "; ".join(parts)

    @property
    def next_step(self):
        if self.gate_mode == "latched":
            return ""
        if self._source is None:
            return "Open a probe or the Chuck Positioner, or pick Typed readings"
        if not str(self.sample_id or "").strip() or \
                self._store.sample(str(self.sample_id).strip()) is None:
            return "Type the sample ID and press Save sample"
        valid, reason = self._validity()
        if not valid:
            if "um per count" in reason:
                return ("Type the um per count for the " + str(self._source)
                        + " and press Set (an owner bench fact)")
            reg = self.registration
            corners = self._corner_points(reg) if reg else {}
            for label in "AB":
                if label not in corners:
                    return f"Put the crosshair on corner {label} and press Mark corner {label}"
            return reason
        if not self.flakes:
            return "Find a flake, put the crosshair on it and press Flag flake"
        return "Flag the next flake, or pick one and follow the guidance"

    @property
    def sample_figure(self):
        reg = self.registration
        if reg is None:
            return b""
        frame = self.frame()
        corners = self._corner_points(reg)
        placed = {label: frame.to_sample(p) for label, p in corners.items()
                  if label in CORNERS} if frame is not None else {}
        if frame is None or not placed:
            return b""
        size = None
        if reg["derived_width_um"] and reg["derived_height_um"]:
            size = (reg["derived_width_um"], reg["derived_height_um"])
        flakes = [{"label": f["label"], "x": f["sample_x_um"], "y": f["sample_y_um"],
                   "selected": f["flake_uid"] == self._selected,
                   "extent": f["extent_points_um"]} for f in self.flakes]
        now = self._current_point()
        request = plot_data.sample_map_request(
            placed, flakes, frame.to_sample(now) if now else None, size)
        return plot_data.render_sample_figure(request)

    @property
    def flakes_log(self):
        lines = []
        for f in self.flakes:
            where = (f"({f['sample_x_um']:.0f}, {f['sample_y_um']:.0f}) um"
                     if f["sample_x_um"] is not None else "stage only")
            quality = f"q{f['quality']}" if f["quality"] else "q-"
            lines.append(f"{f['label']}  {f['status']:<11} {quality}  {where}"
                         f"{'  ' + f['note'] if f['note'] else ''}")
        return lines

    @property
    def samples_log(self):
        return [f"{s['sample_id']}  {s['material'] or '-'}  {s['status']}"
                f"{'  at ' + s['storage_location'] if s['storage_location'] else ''}"
                for s in self._store.samples()]

    @property
    def state(self):
        snapshot = super().state
        snapshot["registered"] = self.mode_name == "registered"
        return snapshot

    # -- data -----------------------------------------------------------------------
    def _exports(self):
        folder = self.output_root / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        return folder, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    def export_json(self):
        """The flake-coords/1 document (section 12) under exports/."""
        import json
        folder, stamp = self._exports()
        path = folder / f"sample_map_{stamp}.json"
        document = self._store.export_document(self._station_name(), self._version())
        path.write_text(json.dumps(document, indent=1))
        events.info("Sample Map Exported", f"{path}", source=self.NAME)
        return str(path)

    def export_csv(self):
        folder, stamp = self._exports()
        paths = self._store.export_csv(folder, stamp)
        return str(next(p for p in paths if p.name.endswith("_flakes.csv")))

    def import_json(self, path):
        import json
        try:
            document = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            raise Refused(f"Could not read {Path(path).name} as JSON.")
        try:
            counts = self._store.import_document(document)
        except ss.StoreRefused as refusal:
            raise Refused(str(refusal))
        self._touch()
        events.info("Sample Map Imported", f"{Path(path).name}: {counts['added']} "
                    f"added, {counts['updated']} updated, {counts['kept']} kept.",
                    source=self.NAME)
        return counts

    @staticmethod
    def _station_name():
        import platform
        return platform.node() or "station"

    @staticmethod
    def _version():
        """The bundle's VERSION stamp (first line), else "dev" (a checkout)."""
        root = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parents[2])
        try:
            return (root / "VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, IndexError):
            return "dev"

    # -- schema -------------------------------------------------------------------------
    @property
    def schema(self):
        P = self.PARAMS
        configure = "Configure Sample Map"
        needs_source = ("no_source",)
        needs_frame = ("no_source", "unregistered")
        return sch.schema(
            sch.section(
                "Sample",
                sch.readonly("Next step", "next_step", role="info"),
                sch.entry("Sample ID", "sample_id", P["sample_id"]),
                sch.entry("Material", "material", P["material"]),
                sch.button("Save sample", "save_sample",
                           inputs=("sample_id", "material"), role="go"),
                sch.readonly("Frame", "registration_status"),
            ),
            sch.section(
                "Corners",
                *(sch.button(f"Mark corner {c}", "mark_corner", args=(c,),
                             disabled_when=needs_source) for c in CORNERS),
                sch.button("Check corner A", "check_corner", disabled_when=needs_frame),
                sch.readonly("Corners", "corners_text"),
                sch.readonly("Chip", "frame_text"),
                sch.readonly("Checks", "check_text"),
                sch.readonly("Position", "position_text"),
            ),
            sch.section(
                "Flakes",
                sch.entry("Quality (1-5, 0 = not rated)", "flake_quality", P["flake_quality"]),
                sch.entry("Defects", "flake_defects", P["flake_defects"]),
                sch.entry("Layers (estimate)", "flake_layers", P["flake_layers"]),
                sch.entry("Flake note", "flake_note", P["flake_note"]),
                sch.button("Flag flake", "flag_flake", role="go",
                           inputs=("flake_quality", "flake_defects", "flake_layers",
                                   "flake_note"), disabled_when=needs_source),
                sch.dropdown("Flake", "flake_pick", "select_flake", "flake_options"),
                sch.readonly("Selected", "flake_text"),
                sch.readonly("Guidance", "guidance", role="info"),
                sch.button("Mark extent", "mark_extent", disabled_when=needs_frame),
                sch.button("Rate flake", "rate_flake",
                           inputs=("flake_quality", "flake_defects")),
                sch.image("Sample map", "sample_figure",
                          empty="No corners yet: mark A and B."),
            ),
            sch.section(
                "Locating axes",
                sch.dropdown("Locating axes", "source", "set_source", "source_options"),
                sch.entry("um per count", "um_per_count", P["um_per_count"]),
                sch.button("Set um per count", "set_um_per_count",
                           inputs=("um_per_count",)),
                sch.entry("X reading (mm)", "reading_x_mm", P["reading_x_mm"]),
                sch.entry("Y reading (mm)", "reading_y_mm", P["reading_y_mm"]),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Sample details",
                sch.entry("Substrate", "substrate", P["substrate"]),
                sch.dropdown("Shape", "shape", "set_shape", "shape_options"),
                sch.entry("Chip width (typed)", "chip_width_um", P["chip_width_um"]),
                sch.entry("Chip height (typed)", "chip_height_um", P["chip_height_um"]),
                sch.entry("How to find corner A", "orientation_note", P["orientation_note"]),
                sch.entry("Stored at", "storage_location", P["storage_location"]),
                sch.button("Save sample details", "save_sample",
                           inputs=("sample_id", "material", "substrate",
                                   "chip_width_um", "chip_height_um",
                                   "orientation_note", "storage_location")),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Flake details",
                sch.entry("Flake material", "flake_material", P["flake_material"]),
                sch.dropdown("Status", "flake_status", "set_flake_status",
                             "flake_status_options"),
                sch.entry("Approximate thickness", "thickness_approx_nm",
                          P["thickness_approx_nm"]),
                sch.dropdown("Approximate by", "thickness_method",
                             "set_thickness_method", "thickness_method_options"),
                sch.entry("AFM thickness", "thickness_afm_nm", P["thickness_afm_nm"]),
                sch.entry("AFM thickness uncertainty", "thickness_afm_sigma_nm",
                          P["thickness_afm_sigma_nm"]),
                sch.button("Save flake details", "save_flake_details",
                           inputs=("thickness_approx_nm", "thickness_afm_nm",
                                   "thickness_afm_sigma_nm")),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Data",
                sch.file_save("Export sample map", "export_json", extensions=("json",)),
                sch.file_save("Export flakes (CSV)", "export_csv", extensions=("csv",)),
                sch.file_open("Import sample map", "import_json", extensions=("json",)),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Diagnostics",
                sch.log_stream("Flakes", "flakes_log"),
                sch.log_stream("Samples", "samples_log"),
                sch.button("Clear corners", "clear_corners"),
                sch.button("Delete flake", "delete_flake"),
                tier=3, disclosure="Diagnostics",
            ),
            self._safety_section(),
        )
