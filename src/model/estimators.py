"""A bank of per-frame estimators over several crops of the capture region.

Owner ruling 2026-10-07: the force model is to be CHOSEN on the new footage,
not assumed. The lab's shade (the median green of the right half) and the
station's red share are two candidates among many, so this module computes a
whole family of them on every accepted frame, keeps the last minute in a
fixed ring, and returns it normalised so that curves of different units can
be drawn on one axis and compared.

Everything here is pure: a frame in, numbers out; no I/O, no clock, no
threads of its own. `numpy` only (matplotlib is imported inside
`render_png`, for the one caller that wants a picture).

Names
-----
A crop is one of `CROP_NAMES`; an estimator one of `ESTIMATOR_NAMES`; a
series key is `"<crop>.<estimator>"` (`KEYS`, `full.red_share`).

Crops of an HxWx3 RGB frame (`CROPS`): `full`; `right_half` (the columns from
`W // 2` on, as the lab's `right_half_median_green` takes them); `left_half`
(the columns before `W // 2`); `centre` (the middle 50 % in both axes: rows
`H // 4 .. H - H // 4`, columns `W // 4 .. W - W // 4`); and `custom`, an
operator rectangle `(left, top, width, height)` in region-relative pixels,
`None` while unused.

Estimators of a crop (`ESTIMATORS`, each `crop_frame -> float`, NaN for an
empty crop):

    shade_g_median       the median green (the lab's shade); 0-255
    red_share            % of pixels the red mask passes
    green_share          % of pixels the green mask passes
    blue_share           % of pixels the blue mask passes
    r_mean g_mean b_mean the mean of each channel; 0-255
    luma_mean            0.299 R + 0.587 G + 0.114 B, averaged; 0-255
    red_minus_green_mean r_mean - g_mean; -255..255

The three masks and every threshold are `RgbAnalysis`'s own (imported on
first use, never copied here), so the red share is the number the run logs.

Cost
----
The bank does not call the registry once per crop. The frame is cut once
into at most twelve tiles on the crops' common edges; each tile gets its
mask counts, channel sums and a 256-bin green histogram in one pass; a crop
is a sum of tiles, and its median is read off the summed histogram (equal to
`numpy.median`, the two middle values averaged, to the bit). Only a `custom`
rectangle is measured on its own.
"""
import math
import threading

import numpy

CROP_NAMES = ("full", "right_half", "left_half", "centre", "custom")
ESTIMATOR_NAMES = ("shade_g_median", "red_share", "green_share", "blue_share",
                   "r_mean", "g_mean", "b_mean", "luma_mean",
                   "red_minus_green_mean")
#: Every series key, crop-major: `full.shade_g_median`, `full.red_share`, ...
KEYS = tuple(f"{crop}.{name}" for crop in CROP_NAMES
             for name in ESTIMATOR_NAMES)

#: The ring's capacity is the window times this ceiling; the loop never
#: samples faster (`MIN_SAMPLE_INTERVAL_S` is 1/60, but the source and the
#: gate keep it near 7-15 Hz).
ASSUMED_MAX_HZ = 20.0
#: The baseline of a series is the median of its first stretch this long.
BASELINE_S = 1.0
#: A normalised series is scaled by this percentile of its departure from
#: the baseline, so it reaches about +-1 at its usual extremes.
SPAN_PERCENTILE = 95.0
#: `luma_mean`'s weights (Rec. 601).
LUMA = (0.299, 0.587, 0.114)

_NAN = float("nan")


# -- the thresholds: RgbAnalysis's own ---------------------------------------

class Limits:
    """The mask thresholds, as `RgbAnalysis` states them. A plain holder:
    `r_min`/`g_max`/`b_max` are the red mask's (r_min is the operator's
    `red_min`), `g_min`/`b_min` and `r_max` the green and blue masks'."""
    __slots__ = ("r_min", "g_max", "b_max", "g_min", "b_min", "r_max",
                 "green_cap", "blue_cap")

    def __init__(self, r_min, g_max, b_max, g_min, b_min, r_max,
                 green_cap, blue_cap):
        self.r_min, self.g_max, self.b_max = r_min, g_max, b_max
        self.g_min, self.b_min, self.r_max = g_min, b_min, r_max
        self.green_cap, self.blue_cap = green_cap, blue_cap

    def with_red_min(self, r_min):
        return Limits(r_min, self.g_max, self.b_max, self.g_min, self.b_min,
                      self.r_max, self.green_cap, self.blue_cap)


_defaults = None


def default_limits():
    """`RgbAnalysis`'s constants and its `red_min` default. Imported on first
    use (the model imports this module, so not at the top); cached."""
    global _defaults
    if _defaults is None:
        from model.rgb_analysis import RgbAnalysis as R
        _defaults = Limits(R.PARAMS["red_min"].default, R.GREEN_MAX, R.BLUE_MAX,
                           R.GREEN_MIN, R.BLUE_MIN, R.RED_MAX,
                           R.GREEN_MAX, R.BLUE_MAX)
    return _defaults


# -- crops --------------------------------------------------------------------

def crop_full(frame):
    return frame


def crop_right_half(frame):
    return frame[:, frame.shape[1] // 2:]


def crop_left_half(frame):
    return frame[:, :frame.shape[1] // 2]


def crop_centre(frame):
    height, width = frame.shape[:2]
    return frame[height // 4:height - height // 4,
                 width // 4:width - width // 4]


def crop_custom(frame, rect=None):
    """The operator's rectangle `(left, top, width, height)` clipped to the
    frame, or None while unused (or when nothing of it is inside)."""
    clipped = clip_rect(rect, frame.shape[1], frame.shape[0])
    if clipped is None:
        return None
    left, top, right, bottom = clipped
    return frame[top:bottom, left:right]


def clip_rect(rect, width, height):
    """`(left, top, right, bottom)` of `rect` inside a `width x height`
    frame, or None for no rectangle / no overlap."""
    if rect is None:
        return None
    left, top, w, h = (int(v) for v in rect)
    if w <= 0 or h <= 0:
        return None
    left, top = max(left, 0), max(top, 0)
    right, bottom = min(int(rect[0]) + w, width), min(int(rect[1]) + h, height)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


#: name -> function(frame) -> frame. `custom` answers None without a rect.
CROPS = {"full": crop_full, "right_half": crop_right_half,
         "left_half": crop_left_half, "centre": crop_centre,
         "custom": crop_custom}


# -- one block's statistics ---------------------------------------------------

def _block(frame, limits):
    """`(n, sums(3), counts(3), hist)` of an HxWx3 block: the pixel count, the
    channel sums, how many pixels each of the red, green and blue masks
    passes, and the 256-bin green histogram. One pass; no per-pixel Python."""
    r, g, b = frame[..., 0], frame[..., 1], frame[..., 2]
    n = r.shape[0] * r.shape[1]
    if n == 0:
        return 0, (0, 0, 0), (0, 0, 0), numpy.zeros(256, dtype=numpy.int64)
    g_low = g < limits.g_max
    b_low = b < limits.b_max
    r_low = r < limits.r_max
    g_capped = g_low if limits.green_cap == limits.g_max else g < limits.green_cap
    b_capped = b_low if limits.blue_cap == limits.b_max else b < limits.blue_cap
    red = numpy.count_nonzero((r > limits.r_min) & g_low & b_low)
    green = numpy.count_nonzero((g > limits.g_min) & r_low & b_capped)
    blue = numpy.count_nonzero((b > limits.b_min) & r_low & g_capped)
    sums = (int(r.sum(dtype=numpy.uint64)), int(g.sum(dtype=numpy.uint64)),
            int(b.sum(dtype=numpy.uint64)))
    hist = numpy.bincount(g.ravel(), minlength=256)
    return n, sums, (red, green, blue), hist


def _median_from_hist(hist, n):
    """`numpy.median` of the n values the histogram counts (the two middle
    values averaged for an even n)."""
    cumulative = numpy.cumsum(hist)
    low = int(numpy.searchsorted(cumulative, (n - 1) // 2 + 1))
    high = int(numpy.searchsorted(cumulative, n // 2 + 1))
    return (low + high) / 2.0


def _values(n, sums, counts, hist):
    """The nine estimators of a block's statistics, in ESTIMATOR_NAMES order."""
    if n == 0:
        return (_NAN,) * len(ESTIMATOR_NAMES)
    rm, gm, bm = sums[0] / n, sums[1] / n, sums[2] / n
    return (_median_from_hist(hist, n),
            counts[0] / n * 100.0, counts[1] / n * 100.0,
            counts[2] / n * 100.0,
            rm, gm, bm,
            LUMA[0] * rm + LUMA[1] * gm + LUMA[2] * bm,
            rm - gm)


def _as_array(frame):
    if (frame is None or not isinstance(frame, numpy.ndarray)
            or frame.ndim != 3 or frame.shape[2] < 3):
        return None
    return frame


def _estimator(index):
    def estimate(crop, limits=None):
        crop = _as_array(crop)
        if crop is None or crop.shape[0] * crop.shape[1] == 0:
            return _NAN
        return float(_values(*_block(crop, limits or default_limits()))[index])
    return estimate


shade_g_median = _estimator(0)
red_share = _estimator(1)
green_share = _estimator(2)
blue_share = _estimator(3)
r_mean = _estimator(4)
g_mean = _estimator(5)
b_mean = _estimator(6)
luma_mean = _estimator(7)
red_minus_green_mean = _estimator(8)

#: name -> function(crop_frame) -> float (NaN for an empty crop).
ESTIMATORS = {"shade_g_median": shade_g_median, "red_share": red_share,
              "green_share": green_share, "blue_share": blue_share,
              "r_mean": r_mean, "g_mean": g_mean, "b_mean": b_mean,
              "luma_mean": luma_mean,
              "red_minus_green_mean": red_minus_green_mean}


def key_parts(key):
    """`(crop, estimator)` of a series key; ValueError for an unknown one."""
    crop, _, name = str(key).partition(".")
    if crop not in CROPS or name not in ESTIMATORS:
        raise ValueError(f"{key!r} is not an estimator key")
    return crop, name


# -- the bank -------------------------------------------------------------------

class EstimatorBank:
    """The whole bank, on every accepted frame, in a fixed ring.

    `update(t, frame)` measures every key (`KEYS`) and stores one row;
    `series(...)` returns the last window normalised per series;
    `latest()` the last row. One lock guards the ring and the custom
    rectangle; the measuring happens outside it. The ring is allocated once
    (`capacity` rows) and never grows; an update stores one tuple of floats
    and nothing else.
    """

    def __init__(self, seconds=60.0, custom=None):
        self.seconds = float(seconds)
        self.capacity = max(2, int(math.ceil(self.seconds * ASSUMED_MAX_HZ)))
        self._lock = threading.Lock()
        self._rows = [None] * self.capacity     # (t, values tuple) in KEYS order
        self._head = 0                           # next slot to write
        self._count = 0
        self._custom = None
        self._generation = 0
        if custom is not None:
            self.set_custom(custom)

    # -- configuration -------------------------------------------------------
    @property
    def custom(self):
        return self._custom

    @property
    def generation(self):
        """Bumps whenever the ring's content changes (an update, a reset, a
        new custom rectangle): a cache key for whoever draws it."""
        return self._generation

    def set_custom(self, rect):
        """Set the `custom` crop (left, top, width, height) or None to turn it
        off. A different rectangle makes the stored `custom.*` values mean
        something else, so they are dropped from the ring."""
        if rect is not None:
            rect = tuple(int(v) for v in rect)
            if len(rect) != 4:
                raise ValueError("a custom crop is (left, top, width, height)")
            if rect[2] <= 0 or rect[3] <= 0:
                rect = None
        with self._lock:
            if rect == self._custom:
                return
            self._custom = rect
            start = len(ESTIMATOR_NAMES) * CROP_NAMES.index("custom")
            blank = (_NAN,) * len(ESTIMATOR_NAMES)
            for index, row in enumerate(self._rows):
                if row is not None:
                    values = row[1]
                    self._rows[index] = (row[0], values[:start] + blank
                                         + values[start + len(blank):])
            self._generation += 1

    def reset(self):
        """Forget every row (a new run)."""
        with self._lock:
            self._rows = [None] * self.capacity
            self._head = 0
            self._count = 0
            self._generation += 1

    def __len__(self):
        return self._count

    # -- the frame ------------------------------------------------------------
    def update(self, t, frame, red_min=None):
        """Measure the whole bank on one accepted HxWx3 RGB frame at time `t`
        (seconds). `red_min` overrides the red mask's threshold (the run's).
        A frame that is not an HxWx3 array stores nothing and returns False."""
        frame = _as_array(frame)
        if frame is None or frame.shape[0] * frame.shape[1] == 0:
            return False
        limits = default_limits()
        if red_min is not None and red_min != limits.r_min:
            limits = limits.with_red_min(red_min)
        height, width = frame.shape[:2]
        values = self._bank_values(frame, height, width, limits)
        with self._lock:
            if self._custom is not None:
                values = self._with_custom(values, frame, limits)
            self._rows[self._head] = (float(t), values)
            self._head = (self._head + 1) % self.capacity
            if self._count < self.capacity:
                self._count += 1
            self._generation += 1
        return True

    @staticmethod
    def _bank_values(frame, height, width, limits):
        """The four fixed crops from twelve tiles, plus NaN for `custom`."""
        x = (0, width // 4, width // 2, width - width // 4, width)
        y = (0, height // 4, height - height // 4, height)
        tiles = {}
        for row in range(3):
            for col in range(4):
                block = frame[y[row]:y[row + 1], x[col]:x[col + 1]]
                tiles[row, col] = _block(block, limits)

        def combine(rows, cols):
            n, sums, counts = 0, [0, 0, 0], [0, 0, 0]
            hist = numpy.zeros(256, dtype=numpy.int64)
            for row in rows:
                for col in cols:
                    tn, ts, tc, th = tiles[row, col]
                    if tn:
                        n += tn
                        for i in range(3):
                            sums[i] += ts[i]
                            counts[i] += tc[i]
                        hist += th
            return _values(n, sums, counts, hist)

        every = (0, 1, 2)
        return (combine(every, (0, 1, 2, 3))             # full
                + combine(every, (2, 3))                 # right_half
                + combine(every, (0, 1))                 # left_half
                + combine((1,), (1, 2))                  # centre
                + (_NAN,) * len(ESTIMATOR_NAMES))        # custom

    def _with_custom(self, values, frame, limits):
        crop = crop_custom(frame, self._custom)
        start = len(ESTIMATOR_NAMES) * CROP_NAMES.index("custom")
        if crop is None:
            return values
        return values[:start] + _values(*_block(crop, limits))

    # -- the readers -----------------------------------------------------------
    def latest(self):
        """The last row as `{"t": t, "<crop>.<estimator>": value}` (raw,
        NaN left as NaN), or None before the first update."""
        with self._lock:
            if not self._count:
                return None
            t, values = self._rows[(self._head - 1) % self.capacity]
        row = {"t": t}
        row.update(zip(KEYS, values))
        return row

    def _snapshot(self):
        with self._lock:
            if not self._count:
                return []
            first = (self._head - self._count) % self.capacity
            ring, cap = self._rows, self.capacity
            return [ring[(first + i) % cap] for i in range(self._count)]

    def series(self, keys=None, seconds=None, normalise="baseline",
               max_points=None):
        """`{"t": [...], "<key>": [...]}` for the last `seconds` (default the
        bank's window) before the newest row. A value that was never
        measured (an unused `custom` crop) is None.

        `normalise="baseline"`: each series is `(v - baseline) / span`, the
        baseline being the median of that series' first `BASELINE_S` seconds
        present in the window and the span the 95th percentile of its
        departure from it (the maximum when that is 0; 0 everywhere when it
        never departs). So every curve starts near 0 and moves in its own
        direction by about 1 at its usual extremes, whatever its unit; a
        lone bad frame can overshoot but cannot set the scale.
        `normalise=None`: the raw values. `max_points` thins each series
        (all share one `t`) by an even stride, keeping the newest point.
        """
        keys = list(KEYS if keys is None else keys)
        for key in keys:
            key_parts(key)
        if normalise not in ("baseline", None):
            raise ValueError("normalise is 'baseline' or None")
        rows = self._snapshot()
        window = self.seconds if seconds is None else float(seconds)
        if rows:
            floor = rows[-1][0] - window
            rows = [row for row in rows if row[0] >= floor]
        out = {"t": []}
        out.update({key: [] for key in keys})
        if not rows:
            return out
        t = numpy.fromiter((row[0] for row in rows), dtype=float,
                           count=len(rows))
        picks = [KEYS.index(key) for key in keys]
        table = numpy.array([[row[1][i] for i in picks] for row in rows],
                            dtype=float).reshape(len(rows), len(picks))
        if normalise == "baseline":
            table = self._normalised(t, table)
        keep = self._stride(len(rows), max_points)
        out["t"] = [round(float(v), 3) for v in t[keep]]
        for column, key in enumerate(keys):
            out[key] = [None if math.isnan(v) else round(float(v), 4)
                        for v in table[keep, column]]
        return out

    @staticmethod
    def _normalised(t, table):
        result = numpy.full(table.shape, numpy.nan)
        for column in range(table.shape[1]):
            values = table[:, column]
            present = ~numpy.isnan(values)
            if not present.any():
                continue
            first = t[present][0]
            head = present & (t <= first + BASELINE_S)
            baseline = float(numpy.median(values[head]))
            moved = values - baseline
            size = numpy.abs(moved)
            # The 95th percentile, not the maximum: a single bad frame must
            # not set the scale every other point is drawn at.
            span = float(numpy.nanpercentile(size, SPAN_PERCENTILE))
            if span <= 1e-12:
                span = float(numpy.nanmax(size))
            result[:, column] = moved / span if span > 1e-12 else numpy.where(
                present, 0.0, numpy.nan)
        return result

    @staticmethod
    def _stride(count, max_points):
        if not max_points or count <= max_points:
            return slice(None)
        step = int(math.ceil(count / max_points))
        indices = list(range(count - 1, -1, -step))[::-1]
        return numpy.array(indices, dtype=int)


# -- a picture of the curves -----------------------------------------------------

#: Okabe-Ito, a colour-blind-safe set, then dashes once they run out.
_COLOURS = ("#0072b2", "#d55e00", "#009e73", "#cc79a7", "#e69f00", "#56b4e9",
            "#f0e442", "#000000")


def render_png(series, title="Estimators", size=(6.4, 3.4), dpi=100,
               y_label="relative to baseline"):
    """PNG bytes of a `series()` result: one line per key against `t`, with a
    legend. Drawn through Agg with `Figure` (no pyplot, no display); the
    station's surface and text colours when `palette` is importable. Returns
    b"" when there is nothing to draw."""
    keys = [k for k in series if k != "t"]
    times = series.get("t") or []
    if not keys or len(times) < 2:
        return b""
    import io
    from matplotlib.figure import Figure
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
    except ImportError:                                  # pragma: no cover
        FigureCanvasAgg = None
    try:
        import palette
        surface, text, grid, muted = (palette.SURFACE, palette.TEXT,
                                      palette.GRID, palette.MUTED)
    except Exception:                                    # pragma: no cover
        surface, text, grid, muted = "#ffffff", "#111111", "#dddddd", "#555555"
    figure = Figure(figsize=tuple(size), dpi=dpi, facecolor=surface)
    if FigureCanvasAgg is not None:
        FigureCanvasAgg(figure)
    axes = figure.add_subplot(111)
    axes.set_facecolor(surface)
    for index, key in enumerate(keys):
        values = [numpy.nan if v is None else v for v in series[key]]
        axes.plot(times, values, label=key, linewidth=1.4,
                  color=_COLOURS[index % len(_COLOURS)],
                  linestyle="-" if index < len(_COLOURS) else "--")
    axes.set_xlabel("seconds", color=text, fontsize=9)
    axes.set_ylabel(y_label, color=text, fontsize=9)
    axes.set_title(title, color=text, fontsize=10)
    axes.tick_params(colors=text, labelcolor=text, labelsize=8)
    axes.grid(True, color=grid, linewidth=0.5)
    for spine in axes.spines.values():
        spine.set_color(muted)
    legend = axes.legend(fontsize=7, loc="upper left", frameon=False,
                         ncols=2 if len(keys) > 4 else 1)
    for label in legend.get_texts():
        label.set_color(text)
    try:
        figure.tight_layout()
    except Exception:                                    # pragma: no cover
        pass
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    return buffer.getvalue()
