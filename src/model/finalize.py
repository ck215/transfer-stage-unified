"""The data finalizer's rules: which trials still lack data, in what order a
person walks them, and what a typed estimate may be.

Pure functions over trial rows (dicts), so the Transfer Map's commands and
the window that drives them share one definition and the rules are tested
without a database. Nothing here reads a file or touches a device.

A trial's *properties* are the measurements recorded after the cut: the AFM
width, sample thickness, channel height and trench depth, and the optical
width. Each is NULL until someone measures or estimates it. The owner works
through them one sample at a time, with the trial's video and pictures beside
the form, and may fill AFM, optical, both, or neither.
"""
import math
import re

#: The properties the finalizer reports as unfilled, in the order it shows
#: them: (column, label). `width_um` is the AFM width (the one that makes a
#: trial `measured`); the optical width never does.
PROPERTIES = (
    ("width_um", "Channel width (AFM)"),
    ("thickness_nm", "Sample thickness"),
    ("channel_height_nm", "Channel height"),
    ("trench_depth_nm", "Trench depth"),
    ("width_optical_um", "Channel width (optical)"),
)

LABELS = dict(PROPERTIES)

#: Each value's uncertainty column.
SIGMA_OF = {
    "width_um": "width_sigma_um",
    "thickness_nm": "thickness_sigma_nm",
    "channel_height_nm": "channel_height_sigma_nm",
    "trench_depth_nm": "trench_depth_sigma_nm",
    "width_optical_um": "width_optical_sigma_um",
}

#: Numeric columns a person may type, and the lowest each may be:
#: ("positive") > 0, ("zero") >= 0, ("any") unbounded (a height can read
#: below the substrate). Uncertainties are never negative.
NUMERIC = {
    "width_um": "positive", "thickness_nm": "positive",
    "width_optical_um": "positive",
    "channel_height_nm": "any", "trench_depth_nm": "any",
    **{sigma: "zero" for sigma in SIGMA_OF.values()},
}

#: Text columns a person may correct while finalizing.
TEXT = ("sample_id", "chip_id", "flake_id", "cut_id", "note")

METHOD = "width_optical_method"


def missing(row):
    """The property columns this trial has no value for, in display order."""
    return [column for column, _label in PROPERTIES if row.get(column) is None]


def _number_key(text):
    """Sort key that reads digits as numbers ("flake 9" before "flake 10")."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part.lower())
            for part in re.split(r"(\d+)", str(text or "")) if part != ""]


def _order(row):
    return (_number_key(row.get("sample_id")), _number_key(row.get("chip_id")),
            _number_key(row.get("flake_id")), row["id"])


def queue(rows, *, include_all=False, only_missing=False):
    """The trials to walk, one sample at a time.

    Ordered by sample, chip, flake, then trial number. Invalid and aborted
    trials are left out unless `include_all`; `only_missing` keeps just the
    trials with an unfilled property. Each entry is the row's identity plus
    `missing` (columns), `missing_labels`, `sample_position` (1-based place among the sample's listed
    trials) and `sample_count`.
    """
    chosen = [r for r in rows if include_all
              or (not r.get("invalid") and r.get("status") != "aborted")]
    if only_missing:
        chosen = [r for r in chosen if missing(r)]
    chosen.sort(key=_order)
    sizes = {}
    for r in chosen:
        sizes[r.get("sample_id") or ""] = sizes.get(r.get("sample_id") or "", 0) + 1
    seen = {}
    entries = []
    for r in chosen:
        key = r.get("sample_id") or ""
        seen[key] = seen.get(key, 0) + 1
        entries.append({
            "id": r["id"], "sample_id": r.get("sample_id") or "",
            "chip_id": r.get("chip_id") or "", "flake_id": r.get("flake_id") or "",
            "cut_id": r.get("cut_id") or "", "tip_id": r.get("tip_id") or "",
            "status": r.get("status"), "invalid": bool(r.get("invalid")),
            "missing": missing(r),
            "missing_labels": [LABELS[c] for c in missing(r)],
            "sample_position": seen[key], "sample_count": sizes[key]})
    return entries


def _number(name, text):
    try:
        value = float(str(text).strip())
    except ValueError:
        raise ValueError(f"{name}: type a number.") from None
    if not math.isfinite(value):
        raise ValueError(f"{name}: type a finite number.")
    rule = NUMERIC[name]
    if rule == "positive" and value <= 0:
        raise ValueError(f"{name}: must be above zero.")
    if rule == "zero" and value < 0:
        raise ValueError(f"{name}: an uncertainty is never negative.")
    return value


def parse_fields(raw, methods, current=None):
    """Typed text -> the values to write. A blank box means "leave it as it
    is", so it is dropped; anything else is checked, and a name this does not
    know is refused (the id, the status and the paths are never typed here).
    An uncertainty needs its value, typed now or already on record in
    `current`. ValueError says which box and why."""
    current = current or {}
    out = {}
    for name, text in raw.items():
        if name in NUMERIC:
            if text is None or str(text).strip() == "":
                continue
            out[name] = _number(name, text)
        elif name == METHOD:
            if text is None or str(text).strip() == "":
                continue
            if text not in methods:
                raise ValueError(f"{name}: {text!r} is not one of "
                                 f"{', '.join(methods)}.")
            out[name] = text
        elif name in TEXT:
            if text is None:
                continue
            out[name] = str(text).strip()
        else:
            raise ValueError(f"{name} is not something the finalizer sets.")
    for value, sigma in SIGMA_OF.items():
        if sigma in out and out.get(value) is None and current.get(value) is None:
            raise ValueError(f"{sigma}: type the value this is the uncertainty of.")
    return out


def status_after(row, updates):
    """The trial's status once `updates` are written: an AFM width (typed now
    or already there) makes it `measured`, an optical one never does, and an
    aborted trial stays aborted (as `attach_afm` does)."""
    if row.get("status") == "aborted":
        return "aborted"
    afm = updates.get("width_um", row.get("width_um"))
    return "measured" if afm is not None else (
        "recorded" if row.get("status") == "recorded" else row.get("status"))
