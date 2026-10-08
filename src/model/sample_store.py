"""The Sample DB's store: `data/sample_map.sqlite` (flake-coords section 4).

Owner ruling 2026-10-04: a separate file, following the Transfer Map
store's conventions exactly (`PRAGMA user_version` from 1, `_CREATE` with
IF NOT EXISTS, additive `_migrate`, a connection per call and one write
lock, nothing created by construction or by a read), joined to the map's
trials by `sample_id` / `flake_uid`.

The records are section 4.1's, with the owner's answers of 2026-10-04:
quality 1-5 and the six defect tags, rateable before an extent exists
(Q18); approximate and AFM thickness kept apart and never copied into each
other, with no red-percent estimate (Q16), so `red_percent` is stored as
the reading at the flag and is not an approximate-thickness method; and
the additive `flake-coords/1` fields (Q11): every timestamp carries its UTC
offset, plus `registration_uid`, `sample_uid`, `storage_location`, `tags`
and `deleted_at`. Derived numbers (area, `lateral_um`, aspect ratio) are
never stored: `sample_frame.extent_metrics` computes them at read time.

`export_document` / `import_document` are the `flake-coords/1` contract
(section 12): one JSON document, merged by uid with the newer `updated_at`
winning, so two rigs' exports combine before the lab server exists.

SQL text is built only from this module's own table and column names
(`_checked`); every value travels as a bound parameter.
"""
import csv
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path

SCHEMA = "flake-coords/1"
#: `PRAGMA user_version`. 1: the first store (2026-10-04), with the Q11
#: additive fields from the start (no store existed before). 2: the Rotator
#: turns the chip (owner 2026-10-04): each registration records the
#: Rotator's angle phi0 and the closure of a corner re-marked after a turn,
#: and the station keeps its rotation-centre calibrations (station-only,
#: Q4: never exported). Additive, like every migration here. 3: the Sample
#: Map becomes microscope-image storage (owner 2026-10-07): the
#: `sample_images` table, keyed to the same free-text `sample_id` the
#: Transfer Map stamps on its trials. A new table only, so a v2 file keeps
#: every row it has. 4: the sample / chip / flake hierarchy (owner 2026-10-07):
#: `chips`, `sample_flakes` and `materials` tables, and `sample_images` gains
#: `chip_id` / `flake_id` (NULL = the sample's own picture). `samples.material`
#: already existed. The dormant flake-coordinate table keeps the name `flakes`
#: (and its methods are `coord_*`), so the hierarchy's flakes live in
#: `sample_flakes` and are reached by `flakes(sample_id, chip_id)`.
SCHEMA_VERSION = 4

#: Seeded into `materials` when the store is first written.
DEFAULT_MATERIALS = ("hBN", "graphite", "MoS2")

SHAPES = ("rectangle", "quad", "irregular")
SAMPLE_STATUSES = ("active", "stored", "consumed", "discarded")
#: C5: "reserved" is derived on the server, never a status.
FLAKE_STATUSES = ("candidate", "selected", "transferred", "consumed", "discarded")
FIT_KINDS = ("rigid", "similarity", "affine")
REGISTRATION_QUALITY = ("good", "check", "poor", "unchecked")
EXTENT_KINDS = ("none", "bbox", "polygon")
EXTENT_SOURCES = ("stage_corners", "image_trace", "red_mask")
CORNER_METHODS = ("crosshair", "typed", "edge_intersection")
#: Q18 (owner 2026-10-04): the scale and the controlled vocabulary.
QUALITY_RANGE = (1, 5)
DEFECTS = ("cracks", "bubbles", "residue", "folds", "wrinkles", "tears")
#: How an approximate thickness was judged. No "red_percent": the station
#: makes no red-percent thickness estimate (Q16, owner 2026-10-04).
THICKNESS_APPROX_METHODS = ("optical_contrast", "colour", "eye", "raman")
#: `stage:<model NAME>`, the manual rig, or an imported legacy record.
FRAME_SOURCE_PREFIX = "stage:"
FRAME_SOURCES = ("manual:micrometer", "legacy")

#: The Rotator's sense against the stage axes, and how its centre was found.
ROTATOR_SENSES = (1, -1)
ROTATOR_METHODS = ("chord", "circle")

#: Where a picture of a sample came from, and the objectives the lab owns.
IMAGE_INSTRUMENTS = ("transfer_stage", "microscope")
IMAGE_MAGNIFICATIONS = (10, 20, 50, 100)

#: Columns stored as JSON text and handed out as lists.
_JSON = {"extent_points_um", "image_region_px", "defects", "trial_ids",
         "run_ids", "tip_ids", "tags", "points"}

SAMPLE_COLUMNS = (
    ("sample_id", "TEXT PRIMARY KEY"), ("uid", "TEXT NOT NULL UNIQUE"),
    ("material", "TEXT"), ("substrate", "TEXT"), ("shape", "TEXT"),
    ("width_um", "REAL"), ("height_um", "REAL"), ("orientation_note", "TEXT"),
    ("exfoliated_at", "TEXT"), ("storage_location", "TEXT"), ("owner", "TEXT"),
    ("status", "TEXT NOT NULL DEFAULT 'active'"), ("note", "TEXT"),
    ("legacy_ref", "TEXT"), ("created_at", "TEXT"), ("updated_at", "TEXT"),
    ("deleted_at", "TEXT"),
)
REGISTRATION_COLUMNS = (
    ("registration_id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ("registration_uid", "TEXT NOT NULL UNIQUE"), ("sample_id", "TEXT NOT NULL"),
    ("frame_source", "TEXT NOT NULL"), ("position_epoch", "INTEGER"),
    ("k_x_um", "REAL"), ("k_y_um", "REAL"), ("fit_kind", "TEXT"),
    ("origin_stage_x", "REAL"), ("origin_stage_y", "REAL"),
    ("theta_rad", "REAL"), ("scale", "REAL"), ("a11", "REAL"), ("a12", "REAL"),
    ("a21", "REAL"), ("a22", "REAL"), ("t_x", "REAL"), ("t_y", "REAL"),
    ("handedness", "INTEGER"), ("derived_width_um", "REAL"),
    ("derived_height_um", "REAL"), ("angle_a_deg", "REAL"),
    ("rectangularity_um", "REAL"), ("closure_um", "REAL"),
    ("residual_rms_um", "REAL"), ("quality", "TEXT"), ("z_travel", "REAL"),
    ("registered_at", "TEXT"), ("invalidated_at", "TEXT"),
    ("invalidated_reason", "TEXT"), ("legacy_ref", "TEXT"),
    ("created_at", "TEXT"), ("updated_at", "TEXT"),
    # Version 2: the Rotator's angle at the marks (None: no Rotator open),
    # the calibration a turned check used, and that check's closure.
    ("rotator_name", "TEXT"), ("rotator_phi0_deg", "REAL"),
    ("rotator_calibration_uid", "TEXT"), ("rotator_closure_um", "REAL"),
    ("rotator_quality", "TEXT"),
)
#: Station-only (Q4): one feature marked at several Rotator angles gives the
#: centre (stage units of `frame_source`) and the sense; valid while that
#: source's `position_epoch` holds. `points` is [[x, y, phi_deg], ...].
ROTATOR_CALIBRATION_COLUMNS = (
    ("calibration_uid", "TEXT PRIMARY KEY"), ("rotator_name", "TEXT"),
    ("frame_source", "TEXT NOT NULL"), ("position_epoch", "INTEGER"),
    ("centre_x", "REAL NOT NULL"), ("centre_y", "REAL NOT NULL"),
    ("sense", "INTEGER NOT NULL"), ("n_points", "INTEGER"), ("method", "TEXT"),
    ("residual_um", "REAL"), ("quality", "TEXT"), ("points", "TEXT"),
    ("k_um", "REAL"), ("calibrated_at", "TEXT"), ("invalidated_at", "TEXT"),
    ("invalidated_reason", "TEXT"), ("created_at", "TEXT"), ("updated_at", "TEXT"),
)
CORNER_COLUMNS = (
    ("registration_id", "INTEGER NOT NULL"), ("label", "TEXT NOT NULL"),
    ("stage_x", "REAL"), ("stage_y", "REAL"), ("stage_z", "REAL"),
    ("method", "TEXT"), ("image_path", "TEXT"), ("marked_at", "TEXT"),
)
FLAKE_COLUMNS = (
    ("flake_uid", "TEXT PRIMARY KEY"), ("label", "TEXT NOT NULL"),
    ("sample_id", "TEXT NOT NULL"), ("sample_uid", "TEXT"),
    ("sample_x_um", "REAL"), ("sample_y_um", "REAL"),
    ("registration_id", "INTEGER"), ("stage_x", "REAL"), ("stage_y", "REAL"),
    ("stage_z", "REAL"), ("extent_kind", "TEXT NOT NULL DEFAULT 'none'"),
    ("extent_source", "TEXT"), ("extent_points_um", "TEXT"),
    ("image_path", "TEXT"), ("image_region_px", "TEXT"), ("um_per_px", "REAL"),
    ("image_theta_rad", "REAL"), ("calibration_source", "TEXT"),
    ("red_percent", "REAL"), ("red_min", "REAL"), ("red_run_id", "TEXT"),
    ("red_baseline", "REAL"), ("quality", "INTEGER"), ("defects", "TEXT"),
    ("layers_estimate", "INTEGER"), ("thickness_approx_nm", "REAL"),
    ("thickness_approx_method", "TEXT"), ("thickness_approx_source", "TEXT"),
    ("thickness_afm_nm", "REAL"), ("thickness_afm_sigma_nm", "REAL"),
    ("afm_measured_at", "TEXT"), ("afm_by", "TEXT"), ("afm_file", "TEXT"),
    ("material", "TEXT"), ("status", "TEXT NOT NULL DEFAULT 'candidate'"),
    # owner_auth: how `owner` was established (user-system Q1): "station",
    # "offline-unverified" (no lab server checked a PIN), later "verified".
    ("owner", "TEXT"), ("owner_auth", "TEXT"), ("searched_at", "TEXT"),
    ("transferred_at", "TEXT"),
    ("note", "TEXT"), ("trial_ids", "TEXT"), ("run_ids", "TEXT"),
    ("tip_ids", "TEXT"), ("tags", "TEXT"), ("legacy_ref", "TEXT"),
    ("created_at", "TEXT"), ("updated_at", "TEXT"), ("deleted_at", "TEXT"),
)
#: `path` is RELATIVE to the store's directory, so the store folder moves as
#: one piece (ST-10); `sha256` is of the file as copied in.
IMAGE_COLUMNS = (
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"), ("sample_id", "TEXT NOT NULL"),
    ("instrument", "TEXT NOT NULL CHECK (instrument IN ('transfer_stage', "
                   "'microscope'))"),
    ("magnification", "INTEGER NOT NULL CHECK (magnification IN (10, 20, 50, 100))"),
    ("path", "TEXT NOT NULL"), ("sha256", "TEXT NOT NULL"),
    ("captured_at", "TEXT NOT NULL"), ("note", "TEXT"),
    # Version 4: NULL chip_id and flake_id = the sample's own picture.
    ("chip_id", "TEXT"), ("flake_id", "TEXT"),
)
CHIP_COLUMNS = (
    ("sample_id", "TEXT NOT NULL"), ("chip_id", "TEXT NOT NULL"),
    ("note", "TEXT"), ("created_at", "TEXT"),
)
SAMPLE_FLAKE_COLUMNS = (
    ("sample_id", "TEXT NOT NULL"), ("chip_id", "TEXT NOT NULL"),
    ("flake_id", "TEXT NOT NULL"), ("note", "TEXT"), ("created_at", "TEXT"),
)
MATERIAL_COLUMNS = (("name", "TEXT PRIMARY KEY"),)
_TABLES = {"samples": SAMPLE_COLUMNS, "registrations": REGISTRATION_COLUMNS,
           "corners": CORNER_COLUMNS, "flakes": FLAKE_COLUMNS,
           "rotator_calibrations": ROTATOR_CALIBRATION_COLUMNS,
           "sample_images": IMAGE_COLUMNS, "chips": CHIP_COLUMNS,
           "sample_flakes": SAMPLE_FLAKE_COLUMNS, "materials": MATERIAL_COLUMNS}
_KEYS = {"chips": ("sample_id", "chip_id"),
         "sample_flakes": ("sample_id", "chip_id", "flake_id")}
_NAMES = {table: frozenset(n for n, _k in cols) for table, cols in _TABLES.items()}

_CREATE = tuple(
    "CREATE TABLE IF NOT EXISTS " + table + " ("
    + ", ".join(n + " " + k for n, k in cols)
    + (", PRIMARY KEY (registration_id, label)" if table == "corners" else "")
    + (", PRIMARY KEY (" + ", ".join(_KEYS[table]) + ")" if table in _KEYS else "")
    + ")" for table, cols in _TABLES.items()
) + (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE INDEX IF NOT EXISTS flakes_sample ON flakes(sample_id)",
    "CREATE INDEX IF NOT EXISTS sample_images_sample ON sample_images(sample_id)",
)


class StoreRefused(ValueError):
    """A value the store will not take; the message is the operator's words."""


def now():
    """Local time with its UTC offset (Q11/C1): unambiguous across stations."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def default_path():
    """`data/sample_map.sqlite` beside the Transfer Map's store, local to the
    checkout; `STATION_SAMPLE_DB` overrides it."""
    override = os.environ.get("STATION_SAMPLE_DB")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data" / "sample_map.sqlite"


def _stamp(value):
    """An ISO timestamp as an aware datetime (a naive one is local time)."""
    parsed = datetime.datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.astimezone()


def _checked(table, names):
    """Table and column names come from this module, never from input."""
    if table not in _TABLES:
        raise ValueError(f"not a table: {table!r}")
    unknown = [n for n in names if n not in _NAMES[table]]
    if unknown:
        raise ValueError(f"not a {table} column: " + ", ".join(map(str, unknown)))
    return list(names)


def _insert(db, table, values, verb="INSERT"):
    names = _checked(table, values)
    sql = (verb + " INTO " + table + " (" + ", ".join(names) + ") VALUES ("
           + ", ".join("?" for _ in names) + ")")
    return db.execute(sql, [values[n] for n in names])


def _update(db, table, values, key, key_value):
    names = _checked(table, [n for n in values if n != key])
    _checked(table, [key])
    sql = ("UPDATE " + table + " SET " + ", ".join(n + " = ?" for n in names)
           + " WHERE " + key + " = ?")
    return db.execute(sql, [values[n] for n in names] + [key_value])


def _encode(table, fields):
    return {k: (json.dumps(v) if k in _JSON and v is not None else v)
            for k, v in fields.items() if k in _NAMES[table]}


def _decode(row):
    return {k: (json.loads(v) if k in _JSON and v is not None else v)
            for k, v in row.items()}


def _one_of(value, allowed, what):
    if value is not None and value not in allowed:
        raise StoreRefused(f"{value!r} is not a {what}: use one of "
                           + ", ".join(map(str, allowed)) + ".")


def _sha256(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def _slug(sample_id):
    """A folder name for a free-text sample label. A label that had to be
    changed gets a short hash, so "a b" and "a_b" never share a folder."""
    text = str(sample_id)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._") or "sample"
    if slug != text:
        slug += "-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]
    return slug


def _magnification(value):
    """10, "20" or "20x" -> 10/20/50/100, or a Refused naming the choices."""
    text = str(value).strip().lower().rstrip("x").strip()
    try:
        number = int(text)
    except ValueError:
        number = None
    if number not in IMAGE_MAGNIFICATIONS:
        raise StoreRefused(f"{value!r} is not a magnification: use one of "
                           + ", ".join(f"{m}x" for m in IMAGE_MAGNIFICATIONS) + ".")
    return number


#: Writes through any `SampleStore` in this process, per resolved file: a
#: reader's cache (the pictures' preview) compares `change_token`, so a
#: write from another model's store object shows at its next read.
_writes = {}
_writes_lock = threading.Lock()


def _file_key(path):
    try:
        return str(Path(path).resolve())
    except (OSError, RuntimeError, TypeError, ValueError):
        return str(path)


def _wrote(path):
    key = _file_key(path)
    with _writes_lock:
        _writes[key] = _writes.get(key, 0) + 1


def change_token(path):
    """Changes whenever the store at `path` may have: a write through this
    process (`_wrote`), or the file, its journal or its WAL changed on disk
    (another process). Costs a few `stat` calls, never a SQLite open."""
    key = _file_key(path)
    files = []
    for suffix in ("", "-journal", "-wal"):
        try:
            st = os.stat(key + suffix)
            files.append((st.st_mtime_ns, st.st_size, st.st_ino))
        except OSError:
            files.append(None)
    with _writes_lock:
        written = _writes.get(key, 0)
    return (key, written, tuple(files))


def _newer(theirs, ours):
    epoch = "1970-01-01T00:00:00+00:00"
    return _stamp(theirs.get("updated_at") or epoch) > _stamp(ours.get("updated_at") or epoch)


class SampleStore:
    """The SQLite file. A connection per call, one lock for writes; a read of
    a file that does not exist answers empty and creates nothing."""

    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.Lock()
        self._readonly = False

    @classmethod
    def open_readonly(cls, path):
        """A store that only reads: each call opens a `?mode=ro` connection,
        nothing is created, migrated or written (`write` refuses). Another
        model's look at this file (the Transfer Map's pickers). A file that is
        not there raises `StoreRefused` in words; a file from an older
        version reads what it has (`chips()` of a v3 file is `[]`)."""
        found = cls(path)
        if not found.exists:
            raise StoreRefused(f"There is no sample database at {found.path}.")
        found._readonly = True
        return found

    @property
    def exists(self):
        return self.path.is_file()

    def _connect(self):
        if self._readonly:
            db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=5.0)
        else:
            db = sqlite3.connect(str(self.path), timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    def write(self, fn):
        if self._readonly:
            raise StoreRefused("This sample database was opened read-only.")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = self._connect()
            try:
                with db:
                    for statement in _CREATE:
                        db.execute(statement)
                    self._migrate(db)
                    return fn(db)
            finally:
                db.close()
                _wrote(self.path)

    @staticmethod
    def _migrate(db):
        """Bring the file to `SCHEMA_VERSION`: every column it lacks is added
        (additive only, the Transfer Map's rule), the identity written once."""
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        for table, cols in _TABLES.items():
            have = {row[1] for row in db.execute("PRAGMA table_info(" + table + ")")}
            for name, kind in cols:
                if name not in have:
                    db.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + kind)
        db.executemany("INSERT OR IGNORE INTO materials (name) VALUES (?)",
                       [(m,) for m in DEFAULT_MATERIALS])
        db.execute("CREATE INDEX IF NOT EXISTS sample_images_level "
                   "ON sample_images(sample_id, chip_id, flake_id)")
        db.executemany("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                       [("store_uuid", str(uuid.uuid4())), ("created_at", now())])
        db.execute("PRAGMA user_version = " + str(int(SCHEMA_VERSION)))

    def ensure(self):
        """Create and upgrade the file; True when it was created now."""
        fresh = not self.exists
        self.write(lambda db: None)
        return fresh

    def read(self, sql, args=()):
        if not self.exists:
            return []
        db = self._connect()
        try:
            return [_decode(dict(row)) for row in db.execute(sql, args)]
        except sqlite3.OperationalError:
            return []
        finally:
            db.close()

    def meta(self):
        return {r["key"]: r["value"] for r in self.read("SELECT key, value FROM meta")}

    # -- samples ----------------------------------------------------------------
    def put_sample(self, fields):
        """Insert a chip or update the named one; the uid never changes."""
        sample_id = str(fields.get("sample_id") or "").strip()
        if not sample_id:
            raise StoreRefused("Type the sample ID (the chip's name, as Red "
                               "Percent's specimen ID).")
        _one_of(fields.get("shape"), SHAPES, "shape")
        _one_of(fields.get("status"), SAMPLE_STATUSES, "sample status")
        values = _encode("samples", {**fields, "sample_id": sample_id})
        stamp = now()

        def _do(db):
            row = db.execute("SELECT uid FROM samples WHERE sample_id = ?",
                             (sample_id,)).fetchone()
            if row is None:
                values.setdefault("uid", str(uuid.uuid4()))
                values.setdefault("created_at", stamp)
                values.setdefault("updated_at", stamp)
                _insert(db, "samples", values)
            else:
                values.pop("uid", None)
                values.pop("created_at", None)
                values.setdefault("updated_at", stamp)
                _update(db, "samples", values, "sample_id", sample_id)
            return sample_id
        return self.write(_do)

    def sample(self, sample_id):
        rows = self.read("SELECT * FROM samples WHERE sample_id = ?", (sample_id,))
        return rows[0] if rows else None

    def _has_column(self, table, column):
        """Whether the file on disk has the column (an older file or a missing
        one does not): a read-only store never migrates, so it asks."""
        _checked(table, [])
        return column in {r["name"] for r in self.read(
            "PRAGMA table_info(" + table + ")")}

    def samples(self):
        """Every sample (not soft-deleted) by ID, with `photo_count`: the
        pictures of the sample ITSELF (not its chips' or flakes'). The columns
        are the `samples` table's, so `sample_id`, `material`, `created_at`
        and `note` are all there."""
        level = " AND i.chip_id IS NULL" if self._has_column(
            "sample_images", "chip_id") else ""
        return self.read(
            "SELECT s.*, (SELECT COUNT(*) FROM sample_images i WHERE "
            "i.sample_id = s.sample_id" + level + ") AS photo_count "
            "FROM samples s WHERE s.deleted_at IS NULL ORDER BY s.sample_id")

    # -- the hierarchy: sample > chip > flake (v4, owner 2026-10-07) -----------------
    # IDs are free text, trimmed; "4OCT26" and "4oct26" are the same ID (the
    # Transfer Map compares sample labels that way). Every refusal is a
    # `StoreRefused` (a ValueError) in the operator's words.
    @staticmethod
    def _id(value, what):
        text = str(value if value is not None else "").strip()
        if not text:
            raise StoreRefused(f"Type the {what} ID.")
        return text

    def materials(self):
        """The materials to pick from, in the order they were added. Seeded
        with hBN, graphite and MoS2 the first time the store is written; a
        file with no such table (or none yet) answers the seed."""
        rows = self.read("SELECT name FROM materials ORDER BY rowid")
        return [r["name"] for r in rows] or list(DEFAULT_MATERIALS)

    def add_material(self, name):
        """Add a material (idempotent, case-insensitive); returns the stored
        spelling."""
        name = self._id(name, "material")
        found = {m.lower(): m for m in self.materials()}
        if name.lower() in found and self._has_column("materials", "name"):
            return found[name.lower()]

        def _do(db):
            db.execute("INSERT OR IGNORE INTO materials (name) VALUES (?)", (name,))
            return name
        return self.write(_do)

    def add_sample(self, sample_id, material, note=""):
        """A new sample. Refused: a blank ID, an ID already in the store, a
        material not in `materials()`."""
        sample_id = self._id(sample_id, "sample")
        material = self._known_material(material)
        stamp = now()

        def _do(db):
            if db.execute("SELECT 1 FROM samples WHERE lower(sample_id) = lower(?)",
                          (sample_id,)).fetchone():
                raise StoreRefused(f"Sample {sample_id} is already in the store.")
            _insert(db, "samples", {
                "sample_id": sample_id, "uid": str(uuid.uuid4()),
                "material": material, "status": "active",
                "note": str(note or "").strip() or None,
                "created_at": stamp, "updated_at": stamp})
            return sample_id
        return self.write(_do)

    def _known_material(self, material):
        text = str(material or "").strip()
        if not text:
            raise StoreRefused("Pick the material.")
        found = {m.lower(): m for m in self.materials()}
        if text.lower() not in found:
            raise StoreRefused(f"{text} is not a known material: use "
                               + ", ".join(found.values()) + " or add it first.")
        return found[text.lower()]

    @staticmethod
    def _sample_row(db, sample_id):
        row = db.execute("SELECT sample_id FROM samples WHERE lower(sample_id) = "
                         "lower(?) AND deleted_at IS NULL", (sample_id,)).fetchone()
        if row is None:
            raise StoreRefused(f"There is no sample {sample_id} in the store.")
        return row["sample_id"]

    @staticmethod
    def _chip_row(db, sample_id, chip_id):
        row = db.execute("SELECT chip_id FROM chips WHERE sample_id = ? AND "
                         "lower(chip_id) = lower(?)", (sample_id, chip_id)).fetchone()
        if row is None:
            raise StoreRefused(f"There is no chip {chip_id} on sample {sample_id}.")
        return row["chip_id"]

    @staticmethod
    def _flake_row(db, sample_id, chip_id, flake_id):
        row = db.execute("SELECT flake_id FROM sample_flakes WHERE sample_id = ? "
                         "AND chip_id = ? AND lower(flake_id) = lower(?)",
                         (sample_id, chip_id, flake_id)).fetchone()
        if row is None:
            raise StoreRefused(f"There is no flake {flake_id} on chip {chip_id} "
                               f"of sample {sample_id}.")
        return row["flake_id"]

    def add_chip(self, sample_id, chip_id, note=""):
        """A chip of a stored sample. Refused: a blank ID, a missing sample, a
        chip ID the sample already has."""
        sample_id = self._id(sample_id, "sample")
        chip_id = self._id(chip_id, "chip")

        def _do(db):
            sample = self._sample_row(db, sample_id)
            if db.execute("SELECT 1 FROM chips WHERE sample_id = ? AND "
                          "lower(chip_id) = lower(?)", (sample, chip_id)).fetchone():
                raise StoreRefused(f"Chip {chip_id} is already on sample {sample}.")
            _insert(db, "chips", {"sample_id": sample, "chip_id": chip_id,
                                  "note": str(note or "").strip() or None,
                                  "created_at": now()})
            return chip_id
        return self.write(_do)

    def add_flake(self, sample_id, chip_id, flake_id, note=""):
        """A flake of a stored chip. Refused: a blank ID, a missing sample or
        chip, a flake ID the chip already has. (The dormant flake-coordinate
        records are `add_coord_flake`.)"""
        sample_id = self._id(sample_id, "sample")
        chip_id = self._id(chip_id, "chip")
        flake_id = self._id(flake_id, "flake")

        def _do(db):
            sample = self._sample_row(db, sample_id)
            chip = self._chip_row(db, sample, chip_id)
            if db.execute("SELECT 1 FROM sample_flakes WHERE sample_id = ? AND "
                          "chip_id = ? AND lower(flake_id) = lower(?)",
                          (sample, chip, flake_id)).fetchone():
                raise StoreRefused(f"Flake {flake_id} is already on chip {chip} "
                                   f"of sample {sample}.")
            _insert(db, "sample_flakes", {
                "sample_id": sample, "chip_id": chip, "flake_id": flake_id,
                "note": str(note or "").strip() or None, "created_at": now()})
            return flake_id
        return self.write(_do)

    def chips(self, sample_id):
        """One sample's chips by ID: chip_id, note, created_at, `photo_count`
        (the chip's own pictures) and `flake_count`."""
        if not self._has_column("sample_images", "chip_id"):
            photos = "0"
        else:
            photos = ("(SELECT COUNT(*) FROM sample_images i WHERE i.sample_id = "
                      "c.sample_id AND i.chip_id = c.chip_id AND i.flake_id IS NULL)")
        return [{k: v for k, v in r.items() if k != "sample_id"} for r in self.read(
            "SELECT c.*, " + photos + " AS photo_count, (SELECT COUNT(*) FROM "
            "sample_flakes f WHERE f.sample_id = c.sample_id AND f.chip_id = "
            "c.chip_id) AS flake_count FROM chips c WHERE c.sample_id = ? "
            "ORDER BY c.chip_id", (str(sample_id or "").strip(),))] \
            if self._has_column("chips", "chip_id") else []

    def flakes(self, sample_id, chip_id):
        """One chip's flakes by ID: flake_id, note, created_at, `photo_count`.
        (The dormant flake-coordinate records are `coord_flakes`.)"""
        if not self._has_column("sample_flakes", "flake_id"):
            return []
        photos = ("(SELECT COUNT(*) FROM sample_images i WHERE i.sample_id = "
                  "f.sample_id AND i.chip_id = f.chip_id AND i.flake_id = "
                  "f.flake_id)") if self._has_column("sample_images", "flake_id") \
            else "0"
        return [{k: v for k, v in r.items() if k not in ("sample_id", "chip_id")}
                for r in self.read(
                    "SELECT f.*, " + photos + " AS photo_count FROM sample_flakes f "
                    "WHERE f.sample_id = ? AND f.chip_id = ? ORDER BY f.flake_id",
                    (str(sample_id or "").strip(), str(chip_id or "").strip()))]

    # -- registrations and corners -------------------------------------------------
    def add_registration(self, fields, corners):
        """One mounting's fit and the corners behind it. A legacy record has
        no corners and is invalid from the start (user-system section 11)."""
        source = str(fields.get("frame_source") or "")
        if not (source.startswith(FRAME_SOURCE_PREFIX) or source in FRAME_SOURCES):
            raise StoreRefused(f"{source!r} is not a frame source: use "
                               f"stage:<model>, {', '.join(FRAME_SOURCES)}.")
        _one_of(fields.get("fit_kind"), FIT_KINDS, "fit kind")
        _one_of(fields.get("quality"), REGISTRATION_QUALITY, "registration quality")
        for corner in corners:
            _one_of(corner.get("method"), CORNER_METHODS, "corner method")
        stamp = now()
        values = _encode("registrations", fields)
        values.setdefault("registration_uid", str(uuid.uuid4()))
        values.setdefault("registered_at", stamp)
        values.setdefault("created_at", stamp)
        values.setdefault("updated_at", stamp)
        if source == "legacy" and not values.get("invalidated_at"):
            values["invalidated_at"] = stamp
            values["invalidated_reason"] = "legacy import: no corners were marked"

        def _do(db):
            if db.execute("SELECT 1 FROM samples WHERE sample_id = ?",
                          (values.get("sample_id"),)).fetchone() is None:
                raise StoreRefused(f"No sample {values.get('sample_id')} in the "
                                   "store.")
            rid = _insert(db, "registrations", values).lastrowid
            for corner in corners:
                row = _encode("corners", {**corner, "registration_id": rid})
                row.setdefault("marked_at", stamp)
                _insert(db, "corners", row, verb="INSERT OR REPLACE")
            return rid
        return self.write(_do)

    def registration(self, registration_id):
        rows = self.read("SELECT * FROM registrations WHERE registration_id = ?",
                         (registration_id,))
        return rows[0] if rows else None

    def registrations(self, sample_id=None):
        if sample_id is None:
            return self.read("SELECT * FROM registrations ORDER BY registration_id")
        return self.read("SELECT * FROM registrations WHERE sample_id = ? "
                         "ORDER BY registration_id", (sample_id,))

    def corners(self, registration_id):
        return self.read("SELECT * FROM corners WHERE registration_id = ? "
                         "ORDER BY label", (registration_id,))

    def put_corner(self, registration_id, corner):
        """Add a corner to a registration, or replace the one with its label
        (a re-mark): one transaction, so a mark is never unsaved state."""
        _one_of(corner.get("method"), CORNER_METHODS, "corner method")
        row = _encode("corners", {**corner, "registration_id": registration_id})
        row.setdefault("marked_at", now())
        self.write(lambda db: _insert(db, "corners", row, verb="INSERT OR REPLACE"))

    def update_registration(self, registration_id, fields):
        """Write a refitted transform and its checks onto a registration."""
        _one_of(fields.get("fit_kind"), FIT_KINDS, "fit kind")
        _one_of(fields.get("quality"), REGISTRATION_QUALITY, "registration quality")
        _one_of(fields.get("rotator_quality"), REGISTRATION_QUALITY, "Rotator closure word")
        values = _encode("registrations", {k: v for k, v in fields.items()
                                           if k not in ("registration_id",
                                                        "registration_uid")})
        values.setdefault("updated_at", now())

        def _do(db):
            if not _update(db, "registrations", values, "registration_id",
                           registration_id).rowcount:
                raise StoreRefused(f"No registration {registration_id} in the store.")
        self.write(_do)

    def invalidate_registration(self, registration_id, reason):
        stamp = now()
        self.write(lambda db: db.execute(
            "UPDATE registrations SET invalidated_at = ?, invalidated_reason = ?, "
            "updated_at = ? WHERE registration_id = ?",
            (stamp, reason, stamp, registration_id)))

    # -- the Rotator's calibrations (station-only, Q4) ---------------------------------
    def add_rotator_calibration(self, fields):
        """A rotation-centre fit for `frame_source`'s stage units; its uid."""
        source = str(fields.get("frame_source") or "")
        if not (source.startswith(FRAME_SOURCE_PREFIX) or source == "manual:micrometer"):
            raise StoreRefused(f"{source!r} is not a frame source a Rotator can be "
                               "calibrated against.")
        _one_of(fields.get("sense"), ROTATOR_SENSES, "Rotator sense")
        _one_of(fields.get("method"), ROTATOR_METHODS, "calibration method")
        _one_of(fields.get("quality"), REGISTRATION_QUALITY, "calibration word")
        stamp = now()
        values = _encode("rotator_calibrations", fields)
        values.setdefault("calibration_uid", str(uuid.uuid4()))
        values.setdefault("calibrated_at", stamp)
        values.setdefault("created_at", stamp)
        values.setdefault("updated_at", stamp)
        self.write(lambda db: _insert(db, "rotator_calibrations", values))
        return values["calibration_uid"]

    def rotator_calibration(self, calibration_uid):
        rows = self.read("SELECT * FROM rotator_calibrations WHERE calibration_uid = ?",
                         (calibration_uid,))
        return rows[0] if rows else None

    def rotator_calibrations(self):
        """Every calibration, oldest first (ended ones too)."""
        return self.read("SELECT * FROM rotator_calibrations ORDER BY calibrated_at, rowid")

    def invalidate_rotator_calibration(self, calibration_uid, reason):
        stamp = now()
        self.write(lambda db: db.execute(
            "UPDATE rotator_calibrations SET invalidated_at = ?, invalidated_reason = ?, "
            "updated_at = ? WHERE calibration_uid = ?",
            (stamp, reason, stamp, calibration_uid)))

    # -- flakes -----------------------------------------------------------------
    @staticmethod
    def _validate_flake(fields):
        if "quality" in fields and fields["quality"] is not None:
            q = fields["quality"]
            if isinstance(q, bool) or not isinstance(q, int) or \
                    not QUALITY_RANGE[0] <= q <= QUALITY_RANGE[1]:
                raise StoreRefused(f"Quality is a whole number from {QUALITY_RANGE[0]} "
                                   f"to {QUALITY_RANGE[1]} (or blank), not {q!r}.")
        if fields.get("defects") is not None:
            unknown = [d for d in fields["defects"] if d not in DEFECTS]
            if unknown:
                raise StoreRefused(f"Not a defect: {', '.join(map(str, unknown))}. "
                                   "Use " + ", ".join(DEFECTS) + ".")
        _one_of(fields.get("status"), FLAKE_STATUSES, "flake status")
        _one_of(fields.get("extent_kind"), EXTENT_KINDS, "extent kind")
        _one_of(fields.get("extent_source"), EXTENT_SOURCES, "extent source")
        _one_of(fields.get("thickness_approx_method"), THICKNESS_APPROX_METHODS,
                "approximate-thickness method (red_percent is not one: no "
                "red-percent estimate is made)")

    def add_coord_flake(self, fields):
        """A flake on a known sample, labelled F01.. per sample unless named."""
        self._validate_flake(fields)
        sample_id = fields.get("sample_id")
        stamp = now()
        values = _encode("flakes", fields)

        def _do(db):
            sample = db.execute("SELECT uid FROM samples WHERE sample_id = ?",
                                (sample_id,)).fetchone()
            if sample is None:
                raise StoreRefused(f"No sample {sample_id} in the store. Add the "
                                   "sample first.")
            if not values.get("label"):
                labels = [r[0] for r in db.execute(
                    "SELECT label FROM flakes WHERE sample_id = ?", (sample_id,))]
                numbers = [int(x[1:]) for x in labels
                           if x and x[0] == "F" and x[1:].isdigit()]
                values["label"] = f"F{max(numbers, default=0) + 1:02d}"
            values.setdefault("flake_uid", str(uuid.uuid4()))
            values.setdefault("sample_uid", sample["uid"])
            values.setdefault("searched_at", stamp)
            values.setdefault("created_at", stamp)
            values.setdefault("updated_at", stamp)
            _insert(db, "flakes", values)
            return values["flake_uid"]
        return self.write(_do)

    def update_coord_flake(self, flake_uid, fields):
        self._validate_flake(fields)
        values = _encode("flakes", {k: v for k, v in fields.items()
                                    if k not in ("flake_uid", "created_at")})
        values.setdefault("updated_at", now())

        def _do(db):
            if not _update(db, "flakes", values, "flake_uid", flake_uid).rowcount:
                raise StoreRefused(f"No flake {flake_uid} in the store.")
        self.write(_do)

    def delete_coord_flake(self, flake_uid):
        """Soft: `deleted_at` is set (Q11), so an export tells the server."""
        stamp = now()
        self.update_coord_flake(flake_uid, {"deleted_at": stamp, "updated_at": stamp})

    def coord_flake(self, flake_uid):
        rows = self.read("SELECT * FROM flakes WHERE flake_uid = ?", (flake_uid,))
        return rows[0] if rows else None

    def coord_flakes(self, sample_id=None, include_deleted=False):
        sql, args = "SELECT * FROM flakes WHERE 1 = 1", []
        if sample_id is not None:
            sql += " AND sample_id = ?"
            args.append(sample_id)
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        return self.read(sql + " ORDER BY sample_id, label", tuple(args))

    # -- sample images (v3) -------------------------------------------------------
    @property
    def directory(self):
        return self.path.parent

    def add_image(self, sample_id, source_path, instrument, magnification, note="",
                  *, chip_id=None, flake_id=None):
        """Copy the ORIGINAL file, unmodified, to `images/<sample>[/<chip>
        [/<flake>]]/<time>_<instrument>_<mag>x<ext>` beside the store and
        record it. A name that is taken gets a numeric suffix; nothing is
        overwritten. Returns the row. The picture belongs to the sample, or
        (`chip_id`) to that chip, or (`chip_id` and `flake_id`) to that flake;
        a flake without its chip is refused, and a named chip or flake must
        already be in the store. A bare sample need not be (older stores)."""
        sample_id = str(sample_id or "").strip()
        if not sample_id:
            raise StoreRefused("Type the sample ID the picture belongs to.")
        chip_id = str(chip_id).strip() if chip_id is not None else None
        flake_id = str(flake_id).strip() if flake_id is not None else None
        if flake_id and not chip_id:
            raise StoreRefused("A flake's picture needs its chip ID too.")
        if chip_id == "" or flake_id == "":
            raise StoreRefused("A chip or flake ID cannot be blank.")
        if chip_id:
            def _check(db):
                chip = self._chip_row(db, sample_id, chip_id)
                flake = self._flake_row(db, sample_id, chip, flake_id) if flake_id else None
                return chip, flake
            chip_id, flake_id = self._read_check(_check)
        _one_of(instrument, IMAGE_INSTRUMENTS, "picture source")
        if instrument is None:
            raise StoreRefused("Say which instrument took the picture: "
                               + ", ".join(IMAGE_INSTRUMENTS) + ".")
        mag = _magnification(magnification)
        source = Path(str(source_path))
        if not source.is_file():
            raise StoreRefused(f"Could not find the picture {source.name or source}.")
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = Path("images") / _slug(sample_id)
        if chip_id:
            folder = folder / _slug(chip_id)
        if flake_id:
            folder = folder / _slug(flake_id)
        (self.directory / folder).mkdir(parents=True, exist_ok=True)
        base = f"{stamp}_{instrument}_{mag}x"
        digest = hashlib.sha256()
        relative = None
        for n in range(1, 1000):
            name = base + ("" if n == 1 else f"_{n}") + source.suffix.lower()
            try:
                with open(source, "rb") as src, open(self.directory / folder / name, "xb") as dst:
                    for chunk in iter(lambda: src.read(1 << 20), b""):
                        digest.update(chunk)
                        dst.write(chunk)
            except FileExistsError:
                digest = hashlib.sha256()
                continue
            except OSError as exc:
                raise StoreRefused(f"Could not copy {source.name}: {exc.strerror or exc}.")
            relative = (folder / name).as_posix()
            break
        if relative is None:
            raise StoreRefused("Too many pictures with the same name this second.")
        values = {"sample_id": sample_id, "instrument": instrument,
                  "magnification": mag, "path": relative,
                  "sha256": digest.hexdigest(), "captured_at": now(),
                  "note": str(note or "").strip() or None,
                  "chip_id": chip_id or None, "flake_id": flake_id or None}

        def _do(db):
            return _insert(db, "sample_images", values).lastrowid
        try:
            new_id = self.write(_do)
        except Exception:
            (self.directory / relative).unlink(missing_ok=True)
            raise
        return self.image(new_id)

    def _read_check(self, fn):
        """Run `fn(db)` on a plain connection (no migration): the parent rows
        a picture hangs from must exist before a file is copied."""
        if not self.exists:
            raise StoreRefused("There is no sample database yet.")
        db = self._connect()
        try:
            return fn(db)
        except sqlite3.OperationalError:
            raise StoreRefused("That chip or flake is not in the store.")
        finally:
            db.close()

    def image(self, image_id):
        rows = self.read("SELECT * FROM sample_images WHERE id = ?", (image_id,))
        return rows[0] if rows else None

    def images(self, sample_id=None, chip_id=None, flake_id=None, *, any=False):
        """Pictures, oldest first. No `sample_id`: every picture in the store.
        With one, THAT LEVEL'S OWN pictures: the sample's (`chip_id` None), the
        chip's (`chip_id` given), the flake's (both given). `any=True` widens
        to everything under the level named: `images("S1", any=True)` is the
        sample's, its chips' and its flakes' pictures; with a chip, that
        chip's and its flakes'."""
        if sample_id is None:
            return self.read("SELECT * FROM sample_images ORDER BY id")
        sql, args = "SELECT * FROM sample_images WHERE sample_id = ?", \
            [str(sample_id).strip()]
        if chip_id is not None:
            sql += " AND chip_id = ?"
            args.append(str(chip_id).strip())
        if flake_id is not None:
            sql += " AND flake_id = ?"
            args.append(str(flake_id).strip())
        if not any:
            if chip_id is None:
                sql += " AND chip_id IS NULL"
            if flake_id is None:
                sql += " AND flake_id IS NULL"
        if not self._has_column("sample_images", "chip_id"):
            # A v3 file read as it is: every picture is the sample's own.
            return [] if chip_id is not None or flake_id is not None else \
                self.read("SELECT * FROM sample_images WHERE sample_id = ? "
                          "ORDER BY id", (args[0],))
        return self.read(sql + " ORDER BY id", tuple(args))

    def image_file(self, row):
        """The picture's file: its relative path resolved against the store's
        directory (an absolute path in an old row is returned as it is)."""
        return self.directory / row["path"]

    def delete_image(self, image_id):
        """Remove the row and the file (only a file inside this store's
        `images/` folder is ever unlinked)."""
        row = self.image(image_id)
        if row is None:
            raise StoreRefused(f"There is no picture number {image_id}.")
        self.write(lambda db: db.execute("DELETE FROM sample_images WHERE id = ?",
                                         (image_id,)))
        target = self.image_file(row).resolve()
        if (self.directory / "images").resolve() in target.parents:
            target.unlink(missing_ok=True)
        return row

    def absolute_image_paths(self):
        """How many stored picture paths are absolute (flake and corner rows
        from before the relative-path rule). Left as they are; counted only."""
        count = 0
        for table, column in (("flakes", "image_path"), ("corners", "image_path"),
                              ("sample_images", "path")):
            for row in self.read("SELECT " + column + " AS p FROM " + table
                                 + " WHERE " + column + " IS NOT NULL"):
                count += os.path.isabs(row["p"])
        return count

    # -- flake-coords/1 (section 12) -------------------------------------------------
    def export_document(self, station_name, software_version):
        """The whole record set as one JSON-able document. Derived numbers are
        left out; pictures are referenced by path and hash, never embedded."""
        uids = {r["registration_id"]: r["registration_uid"]
                for r in self.registrations()}
        registrations = [{**r, "corners": self.corners(r["registration_id"])}
                         for r in self.registrations()]
        flakes = [{**f, "registration_uid": uids.get(f["registration_id"]),
                   "observations": []}
                  for f in self.coord_flakes(include_deleted=True)]
        images = []
        for flake in flakes:
            if flake.get("image_path"):
                images.append({"path": flake["image_path"],
                               "sha256": _sha256(flake["image_path"]),
                               "flake_uid": flake["flake_uid"]})
        for reg in registrations:
            for corner in reg["corners"]:
                if corner.get("image_path"):
                    images.append({"path": corner["image_path"],
                                   "sha256": _sha256(corner["image_path"]),
                                   "corner": {"registration_uid": reg["registration_uid"],
                                              "label": corner["label"]}})
        for row in self.images():
            # Additive (v3): the sample's pictures, by relative path and hash.
            entry = {"sample_id": row["sample_id"], "path": row["path"],
                     "sha256": row["sha256"], "instrument": row["instrument"],
                     "magnification": row["magnification"],
                     "captured_at": row["captured_at"], "note": row["note"]}
            for level in ("chip_id", "flake_id"):      # additive (v4): only when set
                if row.get(level):
                    entry[level] = row[level]
            images.append(entry)
        return {"schema": SCHEMA, "exported_at": now(),
                "station": {"name": station_name, "software_version": software_version,
                            "store_uuid": self.meta().get("store_uuid")},
                "samples": self.read("SELECT * FROM samples ORDER BY sample_id"),
                "registrations": registrations, "flakes": flakes, "images": images,
                # Additive (v4): the sample > chip > flake hierarchy.
                "chips": self.read("SELECT * FROM chips ORDER BY sample_id, chip_id"),
                "sample_flakes": self.read("SELECT * FROM sample_flakes ORDER BY "
                                           "sample_id, chip_id, flake_id"),
                "materials": self.materials()}

    def import_document(self, document):
        """Merge a `flake-coords/1` document: records are matched by uid
        (`uid`, `registration_uid`, `flake_uid`) and the newer `updated_at`
        wins; registrations get local ids and the flakes follow them."""
        if document.get("schema") != SCHEMA:
            raise StoreRefused(f"This file is not a {SCHEMA} export "
                               f"(it says {document.get('schema')!r}).")
        for flake in document.get("flakes", []):
            self._validate_flake(flake)
        counts = {"added": 0, "updated": 0, "kept": 0}

        def merge(db, table, key, record, values):
            row = db.execute("SELECT * FROM " + table + " WHERE " + key + " = ?",
                             (record.get(key),)).fetchone()
            if row is None:
                counts["added"] += 1
                return _insert(db, table, values).lastrowid, True
            if _newer(record, dict(row)):
                _update(db, table, values, key, record[key])
                counts["updated"] += 1
            else:
                counts["kept"] += 1
            return (row["registration_id"] if table == "registrations" else None), False

        def _do(db):
            for sample in document.get("samples", []):
                _checked("samples", ["uid"])
                merge(db, "samples", "uid", sample, _encode("samples", sample))
            local_reg = {}
            for reg in document.get("registrations", []):
                values = _encode("registrations", {k: v for k, v in reg.items()
                                                   if k != "registration_id"})
                rid, added = merge(db, "registrations", "registration_uid", reg, values)
                if added:
                    for corner in reg.get("corners", []):
                        _insert(db, "corners", _encode(
                            "corners", {**corner, "registration_id": rid}),
                            verb="INSERT OR REPLACE")
                local_reg[reg.get("registration_uid")] = rid
            for flake in document.get("flakes", []):
                fields = {k: v for k, v in flake.items()
                          if k not in ("observations", "registration_uid")}
                fields["registration_id"] = local_reg.get(flake.get("registration_uid"))
                merge(db, "flakes", "flake_uid", flake, _encode("flakes", fields))
            # Additive (v4): the hierarchy is merged by key, existing rows kept.
            for name in document.get("materials", []):
                db.execute("INSERT OR IGNORE INTO materials (name) VALUES (?)",
                           (str(name),))
            for table in ("chips", "sample_flakes"):
                for record in document.get(table, []):
                    _insert(db, table, _encode(table, record), verb="INSERT OR IGNORE")
            return counts
        return self.write(_do)

    def export_csv(self, folder, stamp):
        """The four tables flat under `folder`, JSON columns as JSON strings."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        tables = {
            "samples": self.read("SELECT * FROM samples ORDER BY sample_id"),
            "registrations": self.registrations(),
            "corners": self.read("SELECT * FROM corners ORDER BY registration_id, label"),
            "flakes": self.coord_flakes(include_deleted=True),
        }
        for table, rows in tables.items():
            path = folder / ("sample_map_" + stamp + "_" + table + ".csv")
            columns = [n for n, _k in _TABLES[table]]
            with open(path, "w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                for row in rows:
                    writer.writerow([json.dumps(row[c]) if c in _JSON and row[c] is not None
                                     else row[c] for c in columns])
            paths.append(path)
        return paths


# -- the picture preview (owner 2026-10-08) -------------------------------------------
#: "Any images should show a preview, with the 50 or 100x view by default and
#: fallback to lower zooms if there are no values": 100x first, then 50x,
#: then the next lower magnification there is, highest first.
PREVIEW_FIRST = (100, 50)
#: The longest side of a preview, in pixels: a microscope picture is ~6 MB;
#: the page gets a small PNG drawn from it, never the original.
PREVIEW_PX = 560
#: Thumbnails kept per model (a few flakes back and forth costs no re-read).
PREVIEW_CACHE = 12


def preview_order(magnifications):
    """The magnifications a preview offers, in the order it prefers them:
    100x, 50x, then every lower one highest first, then anything left
    (a magnification between 50x and 100x, or above 100x) highest first."""
    there = sorted({int(m) for m in magnifications}, reverse=True)
    first = [m for m in PREVIEW_FIRST if m in there]
    lower = [m for m in there if m not in first and m < PREVIEW_FIRST[-1]]
    return first + lower + [m for m in there if m not in first and m not in lower]


def pick_preview(rows, magnification=None):
    """(the picture to show, the magnifications on offer in preview order).

    `rows` are `sample_images` rows; `magnification` the operator's choice,
    honoured only while a picture at it exists (else the default order
    decides). Among several pictures at one magnification the newest wins:
    the latest `captured_at`, then the highest id. No rows: (None, [])."""
    by = {}
    for row in rows:
        try:
            by.setdefault(int(row["magnification"]), []).append(row)
        except (TypeError, ValueError, KeyError):
            continue
    order = preview_order(by)
    if not order:
        return None, []
    try:
        wanted = int(str(magnification).strip().lower().rstrip("x"))
    except (TypeError, ValueError):
        wanted = None
    chosen = wanted if wanted in by else order[0]
    newest = max(by[chosen], key=lambda r: (str(r["captured_at"] or ""), r["id"] or 0))
    return newest, order


def preview_file(store, row):
    """The picture's file, only when it is a file INSIDE the store's folder
    (realpath, so a symlink or a `..` out of it is refused): the same rule
    as the Web view's `/api/image`. None otherwise."""
    if row is None:
        return None
    root = os.path.realpath(store.directory)
    full = os.path.realpath(store.image_file(row))
    if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
        return None
    return Path(full)


def thumbnail_png(path, size=PREVIEW_PX):
    """`path` scaled down to fit `size` x `size`, as PNG bytes."""
    import io
    from PIL import Image
    with Image.open(path) as picture:
        picture.draft("RGB", (size, size))          # a JPEG decodes small
        small = picture.convert("RGB")
        small.thumbnail((size, size))
        out = io.BytesIO()
        small.save(out, "PNG")
        return out.getvalue()


class PicturePreview:
    """One model's preview of the picked level's picture: which picture
    (`pick_preview`), the operator's magnification choice for THIS level
    (a new pick starts again at the default), and a small thumbnail cache.
    The Sample DB and the Transfer Map's trial setup each hold one."""

    NONE = "No picture"

    def __init__(self):
        self._choice = (None, None)          # (level, magnification)
        self._cache = {}
        self._rows = (None, [])              # ((token, level), rows)

    def rows(self, store, level):
        """The picked level's own pictures, else everything under it (a
        sample's chips' and flakes'). Read once per (store change, level):
        every readout of a state poll asks, and the store is read again
        only after `change_token` moved (audit 2026-10-08 item 8: 6-12
        SQLite opens per poll). A read that raises is not cached."""
        if store is None or not level[0]:
            return []
        path = getattr(store, "path", None)
        key = (change_token(path), level) if path is not None else None
        if key is not None and self._rows[0] == key:
            return self._rows[1]
        rows = store.images(*level) or store.images(*level, any=True)
        if key is not None:
            self._rows = (key, rows)
        return rows

    def _wanted(self, level):
        return self._choice[1] if self._choice[0] == level else None

    def pick(self, level, rows):
        return pick_preview(rows, self._wanted(level))

    def choose(self, level, rows, magnification):
        """The operator's magnification for this level: one on offer."""
        _row, order = pick_preview(rows)
        try:
            number = int(str(magnification).strip().lower().rstrip("x"))
        except (TypeError, ValueError):
            number = None
        if number not in order:
            raise StoreRefused(
                f"No picture at {magnification}: "
                + (", ".join(f"{m}x" for m in order) if order else "there are none")
                + ".")
        self._choice = (level, number)
        return f"{number}x"

    def magnification(self, level, rows):
        row, _order = self.pick(level, rows)
        return f"{int(row['magnification'])}x" if row is not None else ""

    def options(self, level, rows):
        return [f"{m}x" for m in self.pick(level, rows)[1]]

    def key(self, level, rows):
        """Changes exactly when the picture shown does: the page refetches
        the preview on a change only, never on every poll."""
        row, _order = self.pick(level, rows)
        return "" if row is None else f"{row['id']}:{row['path']}"

    def text(self, level, rows):
        row, order = self.pick(level, rows)
        if row is None:
            return self.NONE
        mag = int(row["magnification"])
        same = sum(1 for r in rows if _int_or_none(r["magnification"]) == mag)
        when = str(row["captured_at"] or "")[:16].replace("T", " ")
        return (f"{mag}x picture, taken {when}"
                + (f", newest of {same}" if same > 1 else ""))

    def png(self, store, level, rows):
        """The shown picture as a small PNG; b"" when there is none (or its
        file is missing, outside the store, or unreadable)."""
        row, _order = self.pick(level, rows)
        path = preview_file(store, row) if store is not None else None
        if path is None:
            return b""
        try:
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size)
            if key not in self._cache:
                if len(self._cache) >= PREVIEW_CACHE:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = thumbnail_png(path)
            return self._cache[key]
        except Exception:
            return b""


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
