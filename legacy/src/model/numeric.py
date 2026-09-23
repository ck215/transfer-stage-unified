import math

def num(value, default, *, minimum=None, integer=False):
    """Coerce `value` to a float, falling back to `default` on any parse
    failure, NaN, or Inf. Optionally clamps to `minimum` and casts to int.
    """
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            v = float(default)
    except (TypeError, ValueError):
        v = float(default)

    if minimum is not None and v < minimum:
        v = minimum
    return int(v) if integer else v

def safe_float(value):
    """Parse `value` as a float; return None (not a fallback default) on
    any parse failure, NaN, or Inf. For call sites where an invalid value
    must abort the action entirely rather than silently substitute a
    default and proceed (e.g. a malformed rotation angle must never
    silently become "0 degrees and move anyway" — that's a real safety
    difference, not just a formatting one).
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v
