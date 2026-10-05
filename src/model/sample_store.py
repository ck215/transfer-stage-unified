"""The Sample Map's store: `data/sample_map.sqlite` (flake-coords section 4).

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
import sqlite3
import threading
import uuid
from pathlib import Path

SCHEMA = "flake-coords/1"
#: `PRAGMA user_version`. 1: the first store (2026-10-04), with the Q11
#: additive fields from the start (no store existed before).
SCHEMA_VERSION = 1

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

#: Columns stored as JSON text and handed out as lists.
_JSON = {"extent_points_um", "image_region_px", "defects", "trial_ids",
         "run_ids", "tip_ids", "tags"}

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
    ("owner", "TEXT"), ("searched_at", "TEXT"), ("transferred_at", "TEXT"),
    ("note", "TEXT"), ("trial_ids", "TEXT"), ("run_ids", "TEXT"),
    ("tip_ids", "TEXT"), ("tags", "TEXT"), ("legacy_ref", "TEXT"),
    ("created_at", "TEXT"), ("updated_at", "TEXT"), ("deleted_at", "TEXT"),
)
_TABLES = {"samples": SAMPLE_COLUMNS, "registrations": REGISTRATION_COLUMNS,
           "corners": CORNER_COLUMNS, "flakes": FLAKE_COLUMNS}
_NAMES = {table: frozenset(n for n, _k in cols) for table, cols in _TABLES.items()}

_CREATE = tuple(
    "CREATE TABLE IF NOT EXISTS " + table + " ("
    + ", ".join(n + " " + k for n, k in cols)
    + (", PRIMARY KEY (registration_id, label)" if table == "corners" else "")
    + ")" for table, cols in _TABLES.items()
) + (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE INDEX IF NOT EXISTS flakes_sample ON flakes(sample_id)",
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
                           + ", ".join(allowed) + ".")


def _sha256(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def _newer(theirs, ours):
    epoch = "1970-01-01T00:00:00+00:00"
    return _stamp(theirs.get("updated_at") or epoch) > _stamp(ours.get("updated_at") or epoch)


class SampleStore:
    """The SQLite file. A connection per call, one lock for writes; a read of
    a file that does not exist answers empty and creates nothing."""

    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.Lock()

    @property
    def exists(self):
        return self.path.is_file()

    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    def write(self, fn):
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

    def samples(self):
        return self.read("SELECT * FROM samples WHERE deleted_at IS NULL "
                         "ORDER BY sample_id")

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

    def add_flake(self, fields):
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

    def update_flake(self, flake_uid, fields):
        self._validate_flake(fields)
        values = _encode("flakes", {k: v for k, v in fields.items()
                                    if k not in ("flake_uid", "created_at")})
        values.setdefault("updated_at", now())

        def _do(db):
            if not _update(db, "flakes", values, "flake_uid", flake_uid).rowcount:
                raise StoreRefused(f"No flake {flake_uid} in the store.")
        self.write(_do)

    def delete_flake(self, flake_uid):
        """Soft: `deleted_at` is set (Q11), so an export tells the server."""
        stamp = now()
        self.update_flake(flake_uid, {"deleted_at": stamp, "updated_at": stamp})

    def flake(self, flake_uid):
        rows = self.read("SELECT * FROM flakes WHERE flake_uid = ?", (flake_uid,))
        return rows[0] if rows else None

    def flakes(self, sample_id=None, include_deleted=False):
        sql, args = "SELECT * FROM flakes WHERE 1 = 1", []
        if sample_id is not None:
            sql += " AND sample_id = ?"
            args.append(sample_id)
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        return self.read(sql + " ORDER BY sample_id, label", tuple(args))

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
                  for f in self.flakes(include_deleted=True)]
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
        return {"schema": SCHEMA, "exported_at": now(),
                "station": {"name": station_name, "software_version": software_version,
                            "store_uuid": self.meta().get("store_uuid")},
                "samples": self.read("SELECT * FROM samples ORDER BY sample_id"),
                "registrations": registrations, "flakes": flakes, "images": images}

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
            "flakes": self.flakes(include_deleted=True),
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
