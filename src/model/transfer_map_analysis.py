"""The Transfer Map's arithmetic: the extrema detector, the force indices and
a small Gaussian process. Pure functions over plain lists; no model, no
store, no matplotlib, so every rule here is testable on a synthetic profile.

A **profile** is the raw red-percent slice of one trial, from Arm to
Finish: `{"t": [s since arming], "red": [%], "z": [steps] (optional)}`. The
raw slice is the truth; every number below is computed from it when asked,
never stored in its place (owner ruling 2026-09-27).

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

import numpy

#: The detector's smoothing window, in samples (odd).
MEDIAN_WINDOW = 5
#: The baseline is the median of the samples in this many seconds after Arm.
BASELINE_SECONDS = 1.0


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


def baseline_of(profile):
    """Median raw red over the first `BASELINE_SECONDS` of the profile."""
    t, red = list(profile.get("t") or ()), list(profile.get("red") or ())
    if not red:
        return None
    first = [r for s, r in zip(t, red) if s - t[0] <= BASELINE_SECONDS]
    return float(statistics.median(first or red[:1]))


# -- the detector ------------------------------------------------------------

def detect(profile, operator_t=None):
    """The approach peak and the shadow's dip, found automatically.

    On the 5-sample median of the red trace: the maximum is the global
    maximum BEFORE the operator's Mark (before the end, without one); the
    minimum is the deepest point AFTER that maximum. Returns `None` for an
    empty profile, else::

        {"max_t", "min_t", "max_i", "min_i",   # None when the trace is flat
         "red_max", "red_min",                 # smoothed red at the two
         "baseline"}                           # median of the first second
    """
    t = numpy.asarray(list(profile.get("t") or ()), dtype=float)
    red = list(profile.get("red") or ())
    if t.size == 0 or not red:
        return None
    smooth = median5(red)
    found = {"max_t": None, "min_t": None, "max_i": None, "min_i": None,
             "red_max": float(smooth.max()), "red_min": float(smooth.min()),
             "baseline": baseline_of(profile)}
    if not smooth.max() > smooth.min():
        return found
    end = smooth.size
    if operator_t is not None:
        end = max(1, int(numpy.searchsorted(t, operator_t, side="right")))
    i_max = int(numpy.argmax(smooth[:end]))
    i_min = i_max + int(numpy.argmin(smooth[i_max:]))
    found.update(max_i=i_max, min_i=i_min, max_t=float(t[i_max]),
                 min_t=float(t[i_min]), red_max=float(smooth[i_max]),
                 red_min=float(smooth[i_min]))
    return found


# -- the force definitions ---------------------------------------------------

class _Context:
    """What a definition reads: the smoothed trace, the two extrema (the
    detector's, or the marks given), the baseline and the operator's Mark."""

    def __init__(self, profile, marks):
        self.t = numpy.asarray(list(profile.get("t") or ()), dtype=float)
        self.red = median5(profile.get("red") or ())
        z = profile.get("z")
        self.z = (numpy.asarray([numpy.nan if v is None else v for v in z],
                                dtype=float) if z else None)
        marks = dict(marks or {})
        self.operator_t = marks.get("operator_t")
        found = detect(profile, self.operator_t) or {}
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


#: name -> function(context). Open: the owner adds definitions by adding a
#: line here; every figure and export follows.
FORCE_DEFINITIONS = {
    "shadow_vs_baseline": shadow_vs_baseline,
    "shadow_vs_peak": shadow_vs_peak,
    "at_operator_mark": at_operator_mark,
    "dip_area": dip_area,
    "fall_slope": fall_slope,
    "z_past_peak": z_past_peak,
}


def force_indices(profile, marks=None, definitions=None):
    """`{name: value or None}` for every definition, from one profile and
    its marks (`operator_t`, and optionally `auto_max_t`, `auto_min_t`,
    `baseline` to override the detector). A definition that cannot be
    computed, or raises, is None: a missing number, never an invented one."""
    context = _Context(profile, marks)
    out = {}
    for name, function in (definitions or FORCE_DEFINITIONS).items():
        try:
            value = function(context)
            out[name] = (None if value is None or not math.isfinite(value)
                         else float(value))
        except Exception:
            out[name] = None
    return out


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
