"""The Transfer Map's arithmetic: the extrema detector, the force indices and
a small Gaussian process. Pure functions over plain lists; no model, no
store, no matplotlib, so every rule here is testable on a synthetic profile.

A **profile** is the raw red-percent slice of one trial, from Arm to
Finish: `{"t": [s since arming], "red": [%], "z": [steps] (optional)}`. The
raw slice is the truth; every number below is computed from it when asked,
never stored in its place (owner ruling 2026-09-27).

**The factor** (RG-2, 2026-10-07) is which column drives the extrema: `red`
by default, so every stored number stays where it is, or any other column
RGB analysis records (`FACTOR_COLUMNS`), or a ratio of two spelled
`"red/green"`. `detect`, `baseline_of` and `force_indices` take it as
`factor=`; the profile then carries that column beside `red` (a profile
recorded before RGB analysis carries red only, and asking it for another
column is a ValueError naming the column).

The owner's picture of a lowering: hovering gives a baseline; as the tip
approaches, the reflection brightens to a maximum; then a shadow overcasts
it; pushed further, the tip flexes until it breaks. There is no force
sensor. The shadow's depth, normalised by the curve's own maximum and
minimum, is a RELATIVE force scale, and the owner asked for several
definitions of it side by side rather than one chosen in code. Every
definition here is oriented the same way: **larger means more force**
(a deeper or longer shadow).
"""
import math
import statistics
from collections import deque

import numpy

#: The detector's smoothing window, in samples (odd).
MEDIAN_WINDOW = 5
#: The baseline is the median of the samples in this many seconds after Arm.
BASELINE_SECONDS = 1.0

# -- the validity mask (bench trial 32, 2026-10-06: 62 % of its rows were
# glitches from the screen grab, and they set the stored extrema) -----------
#: Rule (a). A red of exactly 0.0 is a black grab (an all-black frame), never
#: a measurement: the live trace of a lit sample does not reach it.
BLACK_RED = 0.0
#: Rule (b). A value that occurs EXACTLY this many times in one trial, and
#: whose immediate neighbours differ from it, is a stale grab: the screen
#: returned an older picture, so the same number comes back between live ones.
#: A still scene repeats too, but its repeats sit side by side (equal
#: neighbours), which is how the two are told apart.
STALE_MIN_REPEATS = 10
#: Rule (b), second condition. The repeats must also be at least this share
#: of the trial's non-black samples. A quantised live trace repeats its own
#: levels 10+ times too, with stale rows between them as neighbours (bench
#: trial 32: 0.4241, 0.4302 ...); those are ~2 % of the rows each, while the
#: stale picture (1.3576) is half of them. Without this, rule (b) removes the
#: live trace and keeps the transients.
STALE_MIN_SHARE = 0.05
#: Rule (c). A sample further than this many median absolute deviations from
#: the rolling median around it is a transient (a partly painted frame).
OUTLIER_MAD = 5
#: Rule (c). The width, in seconds, of that rolling median.
ROLLING_WINDOW_S = 0.05
#: Rule (c). The window always holds at least this many surviving samples
#: (it widens past `ROLLING_WINDOW_S` when the trace is sparse: the store
#: keeps change rows only, and after rules (a) and (b) a glitch-ridden trial
#: has only a few live rows per second). Fewer than this many survivors in
#: the whole trial and the rule abstains: a median of three points is no judge.
ROLLING_MIN_SAMPLES = 7
#: Rule (c). The MAD of a still scene is zero; the spread is never taken below
#: this fraction of the trace's robust range (5th to 95th percentile), so a
#: perfectly steady window does not flag its own rounding noise.
MAD_FLOOR_FRACTION = 0.01
#: MAD to standard deviation, for a normal distribution.
MAD_SCALE = 1.4826


# -- smoothing ---------------------------------------------------------------

def median5(values, window=MEDIAN_WINDOW):
    """A running median of `window` samples, same length as `values`; the
    ends use a shrunken window. A profile shorter than the window is
    returned as it is."""
    data = numpy.asarray(list(values), dtype=float)
    if data.size < window:
        return data
    half = window // 2
    padded = numpy.pad(data, half, mode="edge")
    stacked = numpy.lib.stride_tricks.sliding_window_view(padded, window)
    return numpy.median(stacked, axis=1)


def normalise(values):
    """`((v - min) / (max - min), min, max)`; `(None, v, v)` for a flat
    profile, which has no shape to normalise."""
    data = numpy.asarray(list(values), dtype=float)
    if data.size == 0:
        return None, None, None
    low, high = float(data.min()), float(data.max())
    if not high > low:
        return None, low, high
    return (data - low) / (high - low), low, high


def _index_at(times, t):
    """The index of the sample nearest `t` (times ascending)."""
    times = numpy.asarray(times, dtype=float)
    i = int(numpy.searchsorted(times, t))
    if i <= 0:
        return 0
    if i >= times.size:
        return times.size - 1
    return i if abs(times[i] - t) < abs(times[i - 1] - t) else i - 1


def settled_mask(t, red):
    """Which samples of a trial are real readings: `ndarray[bool]`, True =
    settled. Three rules, each a named constant above: (a) red == 0.0 is a
    black grab; (b) a value repeating exactly `STALE_MIN_REPEATS` times while
    its neighbours differ is a stale grab; (c) a sample more than
    `OUTLIER_MAD` MADs from the rolling median over `ROLLING_WINDOW_S` is a
    transient. Every extremum and force definition is taken on the True
    samples only. Pure; a clean profile comes back all True."""
    t = numpy.asarray(list(t), dtype=float)
    red = numpy.asarray(list(red), dtype=float)
    n = min(t.size, red.size)
    t, red = t[:n], red[:n]
    ok = numpy.isfinite(red)
    ok &= red != BLACK_RED                                       # (a)
    if n >= STALE_MIN_REPEATS:                                   # (b)
        values, inverse, counts = numpy.unique(red, return_inverse=True,
                                               return_counts=True)
        lit = max(1, int(numpy.count_nonzero(ok)))
        frequent = counts[inverse.ravel()] >= max(STALE_MIN_REPEATS,
                                                  STALE_MIN_SHARE * lit)
        same_prev = numpy.zeros(n, dtype=bool)
        same_prev[1:] = red[1:] == red[:-1]
        same_next = numpy.zeros(n, dtype=bool)
        same_next[:-1] = same_prev[1:]
        ok &= ~(frequent & ~same_prev & ~same_next)
    candidates = numpy.flatnonzero(ok)                           # (c)
    if candidates.size >= ROLLING_MIN_SAMPLES:
        tv, rv = t[candidates], red[candidates]
        spread = float(numpy.percentile(rv, 95) - numpy.percentile(rv, 5))
        floor = MAD_FLOOR_FRACTION * spread
        half = ROLLING_WINDOW_S / 2.0
        reach = ROLLING_MIN_SAMPLES // 2
        lo = numpy.searchsorted(tv, tv - half, side="left")
        hi = numpy.searchsorted(tv, tv + half, side="right")
        for k in range(tv.size):
            first = max(0, min(lo[k], k - reach))
            last = min(tv.size, max(hi[k], k + reach + 1))
            window = rv[first:last]
            centre = numpy.median(window)
            mad = max(MAD_SCALE * numpy.median(numpy.abs(window - centre)),
                      floor)
            if abs(rv[k] - centre) > OUTLIER_MAD * mad:
                ok[candidates[k]] = False
    return ok


# -- the factor (RG-2, 2026-10-07) ------------------------------------------
#: The profile columns a factor may name: the red share (the default and the
#: column every stored profile has), the green and blue shares (%), and the
#: region's mean red, green and blue (0-255), as RGB analysis records them.
FACTOR_COLUMNS = ("red", "green", "blue", "r_mean", "g_mean", "b_mean")
DEFAULT_FACTOR = "red"


def parse_factor(factor):
    """`"green"` -> `("green", None)`; `"red/green"` -> `("red", "green")`,
    a ratio. Case and spaces are ignored. ValueError for anything that is
    not one `FACTOR_COLUMNS` name or a ratio of two."""
    if not isinstance(factor, str):
        raise ValueError(f"a factor is a column name or a ratio such as "
                         f"'red/green', not {factor!r}")
    parts = [part.strip().lower() for part in factor.split("/")]
    if len(parts) > 2 or not all(parts):
        raise ValueError(f"{factor!r} is not a factor: name one of "
                         f"{', '.join(FACTOR_COLUMNS)}, or a ratio of two "
                         "such as 'red/green'")
    for part in parts:
        if part not in FACTOR_COLUMNS:
            raise ValueError(f"{part!r} (in the factor {factor!r}) is not a "
                             f"profile column; the columns are "
                             f"{', '.join(FACTOR_COLUMNS)}")
    return parts[0], (parts[1] if len(parts) == 2 else None)


def _factor_column(profile, name):
    """One column of the profile as floats, a None cell as NaN. ValueError
    naming the column when the profile has none, or only empty cells where
    it has samples (a row recorded before the column existed)."""
    cells = profile.get(name)
    if cells is None or (len(profile.get("t") or ())
                         and all(v is None for v in cells)):
        raise ValueError(f"the profile has no {name!r} column (a profile "
                         "recorded before RGB analysis carries red only)")
    return numpy.asarray([numpy.nan if v is None else v for v in cells],
                         dtype=float)


def factor_values(profile, factor=DEFAULT_FACTOR):
    """The factor's series over the profile (`ndarray[float]`): one column,
    or one column over another, where a None cell or a zero denominator is
    NaN (and so never a settled sample)."""
    numerator, denominator = parse_factor(factor)
    top = _factor_column(profile, numerator)
    if denominator is None:
        return top
    bottom = _factor_column(profile, denominator)
    n = min(top.size, bottom.size)
    with numpy.errstate(divide="ignore", invalid="ignore"):
        ratio = top[:n] / bottom[:n]
    ratio[~numpy.isfinite(ratio)] = numpy.nan
    return ratio


def _settled_factor(profile, factor):
    """`_settled` for a factor other than red: the factor's values over
    the rows `settled_mask` keeps on the RED column, less any NaN of the
    factor's own. A glitch is a property of the grab, not of a column, and
    rule (a) does not carry over (a green share of 0.0 is a reading: the
    bench scene has no green-dominant pixel), so every factor is read over
    the same settled rows. A profile with no red column is masked by the
    factor's NaNs only."""
    values = factor_values(profile, factor)
    t = numpy.asarray(list(profile.get("t") or ()), dtype=float)
    red = numpy.asarray([numpy.nan if v is None else v
                         for v in (profile.get("red") or ())], dtype=float)
    n = min(t.size, values.size, red.size if red.size else values.size)
    t, values = t[:n], values[:n]
    mask = (settled_mask(t, red[:n]) if red.size
            else numpy.ones(n, dtype=bool))
    mask &= numpy.isfinite(values)
    z = profile.get("z")
    z = (numpy.asarray([numpy.nan if v is None else v for v in z],
                       dtype=float)[:n] if z and len(z) >= n else None)
    return mask, t[mask], values[mask], (None if z is None else z[mask])


def _settled(profile, factor=DEFAULT_FACTOR):
    """`(mask, t, red, z)`: the mask over the whole profile and the settled
    samples of each column (`z` None without a Z column). With a `factor`
    other than red, `red` is that factor's settled series (RG-2)."""
    if factor != DEFAULT_FACTOR and parse_factor(factor) != (DEFAULT_FACTOR, None):
        return _settled_factor(profile, factor)
    t = numpy.asarray(list(profile.get("t") or ()), dtype=float)
    red = numpy.asarray(list(profile.get("red") or ()), dtype=float)
    n = min(t.size, red.size)
    t, red = t[:n], red[:n]
    mask = settled_mask(t, red)
    z = profile.get("z")
    z = (numpy.asarray([numpy.nan if v is None else v for v in z],
                       dtype=float)[:n] if z and len(z) >= n else None)
    return mask, t[mask], red[mask], (None if z is None else z[mask])


def _baseline(t, red):
    if not len(red):
        return None
    first = [r for s, r in zip(t, red) if s - t[0] <= BASELINE_SECONDS]
    return float(statistics.median(first or list(red[:1])))


def baseline_of(profile, factor=DEFAULT_FACTOR):
    """Median raw red (the `factor`'s value, RG-2) over the first
    `BASELINE_SECONDS` of the settled samples of the profile."""
    _mask, t, red, _z = _settled(profile, factor)
    return _baseline(t, red)


# -- the detector ------------------------------------------------------------

def detect(profile, operator_t=None, factor=DEFAULT_FACTOR):
    """The approach peak and the shadow's dip, found automatically.

    `factor` (RG-2) names the column that drives them: "red" (the default),
    another of `FACTOR_COLUMNS`, or a ratio "a/b". The keys below keep their
    names whatever the factor: `red_max`, `red_min` and `baseline` are then
    the factor's values. ValueError for a factor the profile has no column
    for.

    On the 5-sample median of the SETTLED red trace (`settled_mask`): the
    maximum is the global maximum BEFORE the operator's Mark (before the end,
    without one); the minimum is the deepest point AFTER that maximum. Returns
    `None` for an empty profile, else::

        {"max_t", "min_t", "max_i", "min_i",   # None when the trace is flat;
                                               # the indices are into the
                                               # profile as given
         "red_max", "red_min",                 # smoothed red at the two (None
                                               # when nothing is settled)
         "baseline",                           # median of the first second
         "settled_mask", "masked_share"}       # ndarray[bool] over the rows,
                                               # and the share that is False
    """
    mask, t, red, _z = _settled(profile, factor)
    if mask.size == 0:
        return None
    found = {"max_t": None, "min_t": None, "max_i": None, "min_i": None,
             "red_max": None, "red_min": None, "baseline": None,
             "settled_mask": mask,
             "masked_share": float(1.0 - mask.mean())}
    if t.size == 0:
        return found
    smooth = median5(red)
    found.update(red_max=float(smooth.max()), red_min=float(smooth.min()),
                 baseline=_baseline(t, red))
    if not smooth.max() > smooth.min():
        return found
    end = smooth.size
    if operator_t is not None:
        end = max(1, int(numpy.searchsorted(t, operator_t, side="right")))
    i_max = int(numpy.argmax(smooth[:end]))
    i_min = i_max + int(numpy.argmin(smooth[i_max:]))
    original = numpy.flatnonzero(mask)
    found.update(max_i=int(original[i_max]), min_i=int(original[i_min]),
                 max_t=float(t[i_max]), min_t=float(t[i_min]),
                 red_max=float(smooth[i_max]), red_min=float(smooth[i_min]))
    return found


# -- the force definitions ---------------------------------------------------

class _Context:
    """What a definition reads: the smoothed trace, the two extrema (the
    detector's, or the marks given), the baseline and the operator's Mark."""

    def __init__(self, profile, marks, factor=DEFAULT_FACTOR):
        # settled samples only; `red` is the factor's series (RG-2)
        _mask, self.t, red, self.z = _settled(profile, factor)
        self.red = median5(red)
        marks = dict(marks or {})
        #: The tip-shade position on the peak (`model/tip_shade.py`), taken
        #: from the trial's own stored column, not from the red trace.
        self.shade_position = marks.get("shade_position")
        self.operator_t = marks.get("operator_t")
        found = detect(profile, self.operator_t, factor) or {}
        self.baseline = (marks["baseline"] if marks.get("baseline") is not None
                         else found.get("baseline"))
        self.max_t = (marks["auto_max_t"] if marks.get("auto_max_t") is not None
                      else found.get("max_t"))
        self.min_t = (marks["auto_min_t"] if marks.get("auto_min_t") is not None
                      else found.get("min_t"))
        self.ok = (self.t.size > 0 and self.max_t is not None
                   and self.min_t is not None)
        if self.ok:
            self.i_max = _index_at(self.t, self.max_t)
            self.i_min = _index_at(self.t, self.min_t)
            self.peak = float(self.red[self.i_max])     # M
            self.dip = float(self.red[self.i_min])      # m
            self.ok = self.peak > self.dip

    def n(self, red):
        """Normalised red: 1 at the peak, 0 at the dip."""
        return (red - self.dip) / (self.peak - self.dip)


def shadow_vs_baseline(c):
    """(baseline - m) / (M - m): how far below the hover level the shadow
    reached, as a fraction of the curve's own swing."""
    if not c.ok or c.baseline is None:
        return None
    return (c.baseline - c.dip) / (c.peak - c.dip)


def shadow_vs_peak(c):
    """(M - m) / M: the shadow's depth relative to the approach peak."""
    if not c.ok or c.peak == 0:
        return None
    return (c.peak - c.dip) / c.peak


def at_operator_mark(c):
    """(M - red(Mark)) / (M - m) = 1 - normalised red at the operator's
    Mark: 0 at the peak, 1 at the dip. None without a Mark."""
    if not c.ok or c.operator_t is None:
        return None
    return float(1.0 - c.n(c.red[_index_at(c.t, c.operator_t)]))


def dip_area(c):
    """Integral over t >= t(M) of max(0, n_b - n(t)) dt, n_b the normalised
    baseline: the area of the normalised curve below the hover level after
    the peak (normalised units x seconds). Trapezoid rule."""
    if not c.ok or c.baseline is None:
        return None
    nb = c.n(c.baseline)
    below = numpy.clip(nb - c.n(c.red[c.i_max:]), 0.0, None)
    t = c.t[c.i_max:]
    if t.size < 2:
        return 0.0
    return float(numpy.sum((below[1:] + below[:-1]) * numpy.diff(t)) / 2.0)


def fall_slope(c):
    """(n(M) - n(m)) / (t(m) - t(M)) = 1 / fall time: the normalised
    fall rate from the peak to the dip, per second."""
    if not c.ok:
        return None
    span = float(c.t[c.i_min] - c.t[c.i_max])
    return None if span <= 0 else 1.0 / span


def z_past_peak(c):
    """|z(Mark) - z(M)|: steps lowered past the approach peak until the
    operator's Mark (the end, without one). Needs the Z column."""
    if not c.ok or c.z is None or c.z.size != c.t.size:
        return None
    end = (_index_at(c.t, c.operator_t) if c.operator_t is not None
           else c.t.size - 1)
    a, b = c.z[c.i_max], c.z[end]
    if math.isnan(a) or math.isnan(b):
        return None
    return float(abs(b - a))


def shade_position(c):
    """Where the tip's shade stood on its peak at the Mark: 0 at the peak, 1
    back down at its baseline (owner 2026-10-06; `model/tip_shade.py`). Read
    from the stored column; None with no contact before the Mark."""
    return c.shade_position


#: name -> function(context). Open: the owner adds definitions by adding a
#: line here; every figure and export follows. `shade_position` is first
#: because the map's default is the first (owner 2026-10-06).
FORCE_DEFINITIONS = {
    "shade_position": shade_position,
    "shadow_vs_baseline": shadow_vs_baseline,
    "shadow_vs_peak": shadow_vs_peak,
    "at_operator_mark": at_operator_mark,
    "dip_area": dip_area,
    "fall_slope": fall_slope,
    "z_past_peak": z_past_peak,
}


def force_indices(profile, marks=None, definitions=None,
                  factor=DEFAULT_FACTOR):
    """`{name: value or None}` for every definition, from one profile and
    its marks (`operator_t`, and optionally `auto_max_t`, `auto_min_t`,
    `baseline` to override the detector). A definition that cannot be
    computed, or raises, is None: a missing number, never an invented one.
    Every definition reads the `factor`'s column (RG-2; red by default); a
    factor the profile has no column for is a ValueError, never a None."""
    context = _Context(profile, marks, factor)
    out = {}
    for name, function in (definitions or FORCE_DEFINITIONS).items():
        try:
            value = function(context)
            out[name] = (None if value is None or not math.isfinite(value)
                         else float(value))
        except Exception:
            out[name] = None
    return out


# -- the live extrema (approved proposal 2026-10-07) ------------------------
# `shadow_vs_peak` taken incrementally, one row at a time, on a profile
# still being recorded, from the moment its baseline exists. The mask and
# the definitions above are unchanged; this only applies them live. Owner
# ruling 2026-10-07 (with the lab's merge): the tip's shade
# (`model.tip_shade`) is THE force model and the sheet's Force estimate;
# these extrema are an analysis factor only, never shown as force.

#: The live classes, low to high.
FORCE_CLASSES = ("Low", "Medium", "High")
#: PLACEHOLDER thresholds on `shadow_vs_peak`, (M - r) / M: "Medium" from
#: the first, "High" from the second. The bench's branch classes its trials
#: Low / Medium / High by thresholds of its own (its store's `force_class`);
#: when that branch lands, its thresholds replace these two numbers.
FORCE_CLASS_MEDIUM_FROM = 0.15
FORCE_CLASS_HIGH_FROM = 0.25


def force_class(value):
    """The class of a `shadow_vs_peak` value (None for None)."""
    if value is None:
        return None
    if value >= FORCE_CLASS_HIGH_FROM:
        return FORCE_CLASSES[2]
    if value >= FORCE_CLASS_MEDIUM_FROM:
        return FORCE_CLASSES[1]
    return FORCE_CLASSES[0]


def live_force(baseline, peak, current):
    """`(value, class)`: `shadow_vs_peak` live, (M - r) / M with M the
    running peak of the settled, smoothed trace and r its current value,
    never below 0. `(None, None)` until the baseline exists, or without a
    positive peak."""
    if baseline is None or peak is None or current is None or not peak > 0:
        return None, None
    value = max(0.0, (peak - current) / peak)
    return value, force_class(value)


class LiveForce:
    """The incremental entry point: `add(t, red)` per row, O(MEDIAN_WINDOW)
    each, keeping what `live_force` needs.

    The settled samples so far, as far as one row can tell (`settled_mask`
    judges a whole trial; live, a row is judged against the rows before
    it): rule (a), a black or non-finite red, is refused; rule (b), a value
    already seen at least `STALE_MIN_REPEATS` times and `STALE_MIN_SHARE`
    of the lit rows, arriving after a different value, is refused (live,
    only the previous lit row is known, not the next). Rule (c)'s
    transients are absorbed by the running median: one row never moves it.
    A refused row changes nothing; `settled` says whether the last row was
    kept.

    The baseline is the median of the settled rows in the first
    `BASELINE_SECONDS` (as `baseline_of`), and exists once a settled row
    arrives after them. The running peak M is the maximum of the trailing
    `MEDIAN_WINDOW` median, frozen from the operator's Mark (`frozen=True`,
    as `detect` takes M before the Mark); r is that median now.

    `factor` (RG-2) names the column that drives all three, as `detect`'s
    does: red by default; any other column, or a ratio, is read from the
    row dict `add` is given (RGB analysis's `green`, `blue`, `r_mean`,
    `g_mean`, `b_mean`), over the rows the mask keeps on RED, less any row
    where the factor has no finite value (`_settled_factor`'s rule)."""

    def __init__(self, factor=DEFAULT_FACTOR):
        self.factor = factor
        self._columns = parse_factor(factor)       # ValueError: not a factor
        self.t0 = None
        self.baseline = None
        self.peak = None
        self.current = None
        self.settled = True
        self.estimate = (None, None)
        self._first = []
        self._recent = deque(maxlen=MEDIAN_WINDOW)
        self._previous = None
        self._seen = {}
        self._lit = 0

    def _keep(self, red):
        """Rules (a) and (b) on one row, against the lit rows before it."""
        if red is None or not math.isfinite(red) or red == BLACK_RED:
            return False
        previous, self._previous = self._previous, red
        self._lit += 1
        self._seen[red] = count = self._seen.get(red, 0) + 1
        frequent = count >= max(STALE_MIN_REPEATS, STALE_MIN_SHARE * self._lit)
        return not (frequent and red != previous)

    def _factor_value(self, red, row):
        """The factor's value on this row (red, one column of `row`, or a
        ratio of two), or None when the row has no finite value for it."""
        numerator, denominator = self._columns
        if (numerator, denominator) == (DEFAULT_FACTOR, None):
            return red

        def cell(name):
            raw = red if name == DEFAULT_FACTOR else (row or {}).get(name)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) else None
        top = cell(numerator)
        if top is None or denominator is None:
            return top
        bottom = cell(denominator)
        if not bottom:
            return None
        ratio = top / bottom
        return ratio if math.isfinite(ratio) else None

    def add(self, t, red, frozen=False, row=None):
        """One row of the live profile (`t` seconds, `red` %, and the row's
        other columns in `row` for a factor other than red). -> the
        estimate, `(value, class)` or `(None, None)`."""
        red = None if red is None else float(red)
        self.settled = self._keep(red)
        if not self.settled:
            return self.estimate
        value = self._factor_value(red, row)
        if value is None:
            self.settled = False
            return self.estimate
        red = value
        t = float(t)
        if self.t0 is None:
            self.t0 = t
        if self.baseline is None:
            if t - self.t0 <= BASELINE_SECONDS:
                self._first.append(red)
            else:
                self.baseline = float(statistics.median(self._first or [red]))
        self._recent.append(red)
        self.current = float(statistics.median(self._recent))
        if self.peak is None or (not frozen and self.current > self.peak):
            self.peak = self.current
        self.estimate = live_force(self.baseline, self.peak, self.current)
        return self.estimate


# -- a Gaussian process (phase 2): mean and variance surfaces ---------------

def _rbf(a, b, length, amplitude):
    d = (a[:, None, :] - b[None, :, :]) / length
    return amplitude * numpy.exp(-0.5 * numpy.sum(d * d, axis=2))


def _fit(x, y, length, noise, amplitude):
    x = numpy.asarray(x, dtype=float)
    x = x.reshape(len(x), -1) if x.size else x.reshape(0, 0)
    y = numpy.asarray(y, dtype=float)
    mean = float(y.mean()) if y.size else 0.0
    if amplitude is None:
        amplitude = float(y.var()) if y.size > 1 and y.var() > 0 else 1.0
    if not y.size:
        return x, None, None, mean, amplitude
    # `noise` is one variance for every point, or one per point (an AFM
    # width's own sigma squared: the data's uncertainty enters the fit).
    jitter = numpy.broadcast_to(numpy.asarray(noise, dtype=float), (len(x),))
    k = _rbf(x, x, length, amplitude) + numpy.diag(jitter + 1e-9)
    chol = numpy.linalg.cholesky(k)
    alpha = numpy.linalg.solve(chol.T, numpy.linalg.solve(chol, y - mean))
    return x, chol, alpha, mean, amplitude


def gp_predict(x, y, grid, length=0.3, noise=1e-2, amplitude=None):
    """Closed-form GP regression with an RBF kernel, via Cholesky.

    `x` (n, d) inputs (scale them to comparable units first), `y` (n,)
    values, `grid` (m, d) query points. Returns `(mean, variance)` of the
    latent function at `grid`; the variance excludes the noise term. The
    prior mean is the data mean; `amplitude` defaults to the data variance.
    """
    grid = numpy.asarray(grid, dtype=float)
    x, chol, alpha, mean, amplitude = _fit(x, y, length, noise, amplitude)
    if chol is None:
        return numpy.full(len(grid), mean), numpy.full(len(grid), amplitude)
    ks = _rbf(grid, x, length, amplitude)
    v = numpy.linalg.solve(chol, ks.T)
    variance = numpy.clip(amplitude - numpy.sum(v * v, axis=0), 0.0, None)
    return mean + ks @ alpha, variance


def gp_gradient(x, y, grid, length=0.3, noise=1e-2, amplitude=None):
    """The gradient of the GP mean at `grid` and its variance per axis,
    from the same covariance: d k(g, x_i) / d g = -k (g - x_i) / l^2 and
    Var[df/dg_d] = amplitude / l^2 - v_d' K^-1 v_d. Returns
    `(gradient (m, d), variance (m, d))`."""
    grid = numpy.asarray(grid, dtype=float)
    x, chol, alpha, _mean, amplitude = _fit(x, y, length, noise, amplitude)
    dims = grid.shape[1]
    prior = amplitude / length ** 2
    if chol is None:
        return numpy.zeros((len(grid), dims)), numpy.full((len(grid), dims), prior)
    ks = _rbf(grid, x, length, amplitude)                        # (m, n)
    diff = (grid[:, None, :] - x[None, :, :]) / length ** 2     # (m, n, d)
    dk = -ks[:, :, None] * diff                                  # (m, n, d)
    gradient = numpy.einsum("mnd,n->md", dk, alpha)
    variance = numpy.empty_like(gradient)
    for d in range(dims):
        v = numpy.linalg.solve(chol, dk[:, :, d].T)              # (n, m)
        variance[:, d] = numpy.clip(prior - numpy.sum(v * v, axis=0), 0.0, None)
    return gradient, variance


def cut_speed(t, z, after_t=None, window=5):
    """The speed of the cut, in Z steps per second, from a trial's Z trace:
    the fastest sustained |dz/dt|. The tip is lowered slowly and then
    accelerated for the cut itself (owner, 2026-09-28), so the value of
    interest is the fast segment, not the mean over the lowering. Per-sample
    speeds are smoothed by a running median of `window` samples (a spike
    from one late position row is not a speed), and the maximum is taken
    after the operator's Mark when there is one (`after_t`). None when
    fewer than three usable samples exist or Z never moved."""
    pairs = [(float(a), float(b)) for a, b in zip(t, z)
             if a is not None and b is not None]
    if after_t is not None:
        kept = [p for p in pairs if p[0] >= after_t]
        if len(kept) >= 3:
            pairs = kept
    if len(pairs) < 3:
        return None
    speeds = []
    for (t0, z0), (t1, z1) in zip(pairs, pairs[1:]):
        dt = t1 - t0
        if dt > 0:
            speeds.append(abs(z1 - z0) / dt)
    if not speeds:
        return None
    half = max(1, window // 2)
    smoothed = [statistics.median(speeds[max(0, i - half):i + half + 1])
                for i in range(len(speeds))]
    best = max(smoothed)
    return best if best > 0 else None


def pick_width(row):
    """The width a figure, the log and the export use for one trial, and
    where it came from: `(width, sigma, source)` with source `"afm"` when an
    AFM channel width exists, else `"optical"` when an optical one does
    (store version 6, owner 2026-10-04), else `(None, None, None)`. `row` is
    a trials-table row; a row from before version 6 has no optical columns."""
    if row.get("width_um") is not None:
        return row["width_um"], row.get("width_sigma_um"), "afm"
    if row.get("width_optical_um") is not None:
        return row["width_optical_um"], row.get("width_optical_sigma_um"), "optical"
    return None, None, None
