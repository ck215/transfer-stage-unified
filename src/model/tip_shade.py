"""The tip's shade, and the live force status read from it.

Owner, 2026-10-06. The tip is the large orange object in the capture region,
in frame from the first frame. As it is lowered onto the sample its shade
changes: measured as the median green of the right half of the region, the
shade rises after contact, peaks, and falls back. Where the shade stands on
that peak is the force: near the top is light, well down the far side is
heavy. (The hard red-percent cutoff counted only the tip's darkest edge
pixels and said nothing useful; see docs/rebuild/RECORDING_A_TRIAL.md.)

Recording is unchanged: the operator still selects the whole tip region. The
shade is taken from that region's frames, live on the picture thread and
again, the same way, from a recorded video.

Everything here is pure: a frame in, a number out; a number in, a status out.
"""
import bisect
import statistics

#: The baseline is the shade over this span of the video, in seconds from the
#: first frame (skipping the first 0.3 s, which are start-up).
BASELINE_FROM, BASELINE_TO = 0.3, 1.3
#: The shade is smoothed with a median over this many seconds of frames, to
#: stop single-frame flashes from reading as anything.
WINDOW_S = 1.0
#: Contact is declared once the smoothed shade has stayed above the baseline
#: by max(RISE_SIGMA noise levels, RISE_FRACTION of the baseline) this long.
SUSTAIN_S = 0.5
RISE_SIGMA = 5.0
RISE_FRACTION = 0.05
#: Position on the peak: 0 at the peak, 1 back down at the baseline.
#: Below PEAK_PASSED the status is "Contact" (rising or at the peak); then
#: thirds of the way down read Low, Medium, High. Bench values: the owner's.
PEAK_PASSED = 0.10
LOW_MAX = 1 / 3
MEDIUM_MAX = 2 / 3
#: A reading past the baseline (the shade fell below where it began) stays
#: High; the position is held at this much.
POSITION_CEILING = 1.2

#: The label band the recorder adds above each frame (`devices.video`).
BAND_COLOUR = (16, 16, 16)

_WORDS = {"No contact": "No contact", "Contact": "Contact", "Low": "Low force",
          "Medium": "Medium force", "High": "High force"}


def right_half_median_green(rgb):
    """The tip's shade: the median green over the right half (the columns
    from the middle on, every row) of an HxWx3 RGB array, or None for no
    picture. A median, so a few hot pixels do not move it."""
    import numpy
    if rgb is None or getattr(rgb, "size", 0) == 0 or rgb.ndim != 3:
        return None
    width = rgb.shape[1]
    return float(numpy.median(rgb[:, width // 2:, 1]))


def image_top(rgb):
    """The number of label-band rows above the picture in a recorded frame:
    the first run of six rows that are mostly not the band's colour. (The
    live frames have no band; a recorded one does, and must be cropped.)"""
    import numpy
    deviation = (numpy.abs(rgb.astype(numpy.int16) - BAND_COLOUR).max(axis=2) > 12).mean(axis=1)
    for y in range(len(deviation) - 5):
        if (deviation[y:y + 6] > 0.5).all():
            return y
    return 0


def columns(tracker):
    """The trial row's tip-shade columns from a tracker: the frame at the
    Mark decides the force; all None for no contact before the Mark."""
    snap = tracker.mark_snapshot
    fields = {"contact_lowered": tracker.contact_lowered,
              "shade_baseline": tracker.baseline,
              "force_position": None, "force_class": None,
              "shade_peak": None, "shade_mark": None}
    if snap is not None and snap["position"] is not None:
        fields.update(force_position=snap["position"], force_class=snap["status"],
                      shade_peak=snap["peak"], shade_mark=snap["shade"])
    return fields


def classify(position):
    """The status word for a position on the peak (None: no contact yet)."""
    if position is None:
        return "No contact"
    if position < PEAK_PASSED:
        return "Contact"
    if position < LOW_MAX:
        return "Low"
    if position < MEDIUM_MAX:
        return "Medium"
    return "High"


def status_text(status):
    """What the sheet shows for a status word ("" for none)."""
    return _WORDS.get(status, "")


class ShadeTracker:
    """One trial's shade, frame by frame: baseline, contact, peak, position.

    `update(t, shade, z)` takes the next frame (`t` seconds from the first
    frame, `shade` from `right_half_median_green`, `z` the probe's Z or None)
    and returns the status. `mark()` freezes the state at the operator's
    Mark; a second Mark does not replace it (as the first Mark's time rules
    the video's band).
    """

    def __init__(self):
        self._t, self._raw, self._smooth = [], [], []
        self.baseline = None
        self.noise = None
        self.contact_t = None
        self.peak = None
        self.position = None
        self.status = "No contact"
        self.mark_snapshot = None
        self._z0 = None
        self._contact_z = None
        self._run = None            # (t, z, index) where the shade rose over the line

    # -- the frame -----------------------------------------------------------
    def update(self, t, shade, z=None):
        if shade is None:
            return self.status
        self._t.append(t)
        self._raw.append(shade)
        if self._z0 is None and z is not None:
            self._z0 = z
        start = bisect.bisect_left(self._t, t - WINDOW_S)
        smooth = statistics.median(self._raw[start:])
        self._smooth.append(smooth)
        if self.baseline is None:
            self._set_baseline(t)
            return self.status
        if self.contact_t is None:
            self._look_for_contact(t, z, smooth)
        else:
            self.peak = max(self.peak, smooth)
        self._place(smooth)
        return self.status

    def _set_baseline(self, t):
        if t < BASELINE_TO:
            return
        pairs = [(r, s) for u, r, s in zip(self._t, self._raw, self._smooth)
                 if BASELINE_FROM <= u <= BASELINE_TO]
        if len(pairs) < 5:
            return
        self.baseline = statistics.median(r for r, _ in pairs)
        residual = [r - s for r, s in pairs]
        centre = statistics.median(residual)
        self.noise = 1.4826 * statistics.median(abs(x - centre) for x in residual)

    def _look_for_contact(self, t, z, smooth):
        line = self.baseline + max(RISE_SIGMA * self.noise,
                                   RISE_FRACTION * abs(self.baseline))
        if smooth < line:
            self._run = None
            return
        if self._run is None:
            self._run = (t, z, len(self._smooth) - 1)
        start_t, start_z, start_i = self._run
        if t - start_t >= SUSTAIN_S:
            self.contact_t, self._contact_z = start_t, start_z
            self.peak = max(self._smooth[start_i:])

    def _place(self, smooth):
        if self.contact_t is None or self.peak is None or self.peak <= self.baseline:
            self.position, self.status = None, "No contact"
            return
        raw = (self.peak - smooth) / (self.peak - self.baseline)
        self.position = max(0.0, min(POSITION_CEILING, raw))
        self.status = classify(self.position)

    # -- readings ------------------------------------------------------------
    @property
    def contact_lowered(self):
        """Steps lowered since the first Z seen, at the moment of contact."""
        if self._z0 is None or self._contact_z is None:
            return None
        return self._z0 - self._contact_z

    @property
    def shade(self):
        """The smoothed shade now."""
        return self._smooth[-1] if self._smooth else None

    def state(self):
        return {"status": self.status, "position": self.position,
                "peak": self.peak, "baseline": self.baseline,
                "shade": self.shade, "contact_lowered": self.contact_lowered,
                "t": self._t[-1] if self._t else None}

    def mark(self):
        """Freeze the state now, once; returns the snapshot."""
        if self.mark_snapshot is None:
            self.mark_snapshot = self.state()
        return self.mark_snapshot
