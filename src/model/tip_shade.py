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

Owner, 2026-10-12: the force CLASS is read from the probe's Z travel past
the shade's peak, not from the shade's position on it. That night the shade's
baseline dropped (~145 -> ~120 on the same tips), which stretched the shade
scale: trial 46's tip broke reading 0.57, "Medium". Replaying this tracker
over the saved shade and Z, the steps lowered from contact to the smoothed
shade's peak were 17-26 on every clean trial, and the steps lowered PAST the
peak at the Mark separated the outcomes cleanly: 44: 7, 45: 8 (Low), 37: 14,
40: 14 (Medium), 34: 20 (High, fine), 49: 23 (survived, Tip5), 46: 23 (the tip
BROKE, Tip7, ~44 past contact). (Trials 42 and 43 read 48 and 96 past the
peak with an anomalous 80-96 steps from contact to the peak: outliers, not
fitted.) So `past_peak` = Z at the peak - Z now (lowering is Z DECREASING, on
every trial, positive or negative absolute Z) classifies whenever Z is known;
without Z the shade position's thirds still do, as before. The position is
computed and stored either way, for history.

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
#: Steps lowered past the smoothed shade's peak (owner 2026-10-12, bench
#: values, tunable; the data are in the module docstring): below
#: PAST_PEAK_CONTACT_MAX the status is "Contact" (rising, or at the peak: the
#: peak's Z follows the shade while it still climbs, so this is "the peak is
#: not yet passed"; the 2 forgives one step of jitter); then Low below
#: PAST_PEAK_LOW_MAX, Medium below PAST_PEAK_MEDIUM_MAX, High below
#: PAST_PEAK_HIGH_MAX, and "Break risk" from it on (the only break seen was
#: at 23 past the peak, trial 46).
PAST_PEAK_CONTACT_MAX = 2
PAST_PEAK_LOW_MAX = 10
PAST_PEAK_MEDIUM_MAX = 17
PAST_PEAK_HIGH_MAX = 21
#: A reading past the baseline (the shade fell below where it began) stays
#: High; the position is held at this much.
POSITION_CEILING = 1.2

#: The label band the recorder adds above each frame (`devices.video`).
BAND_COLOUR = (16, 16, 16)

_WORDS = {"No contact": "No contact", "Contact": "Contact", "Low": "Low force",
          "Medium": "Medium force", "High": "High force",
          "Break risk": "Break risk"}
#: The status words the sheet draws in the danger role (`TransferMap.force_role`).
DANGER = frozenset({"Break risk"})


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
    Mark decides the force; all None for no contact before the Mark.
    `force_class` is the Mark's status (from the steps past the peak when Z
    was known, else from the position); `force_past_peak_steps` and
    `shade_peak_z` are None without Z."""
    snap = tracker.mark_snapshot
    fields = {"contact_lowered": tracker.contact_lowered,
              "shade_baseline": tracker.baseline,
              "force_position": None, "force_class": None,
              "shade_peak": None, "shade_mark": None,
              "force_past_peak_steps": None, "shade_peak_z": None}
    if snap is not None and snap["position"] is not None:
        fields.update(force_position=snap["position"], force_class=snap["status"],
                      shade_peak=snap["peak"], shade_mark=snap["shade"],
                      force_past_peak_steps=snap.get("past_peak"),
                      shade_peak_z=snap.get("peak_z"))
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


def classify_past_peak(steps):
    """The status word for the steps lowered past the shade's peak (None:
    no contact yet, or no Z)."""
    if steps is None:
        return "No contact"
    if steps < PAST_PEAK_CONTACT_MAX:
        return "Contact"
    if steps < PAST_PEAK_LOW_MAX:
        return "Low"
    if steps < PAST_PEAK_MEDIUM_MAX:
        return "Medium"
    if steps < PAST_PEAK_HIGH_MAX:
        return "High"
    return "Break risk"


def status_text(status):
    """What the sheet shows for a status word ("" for none)."""
    return _WORDS.get(status, "")


class ShadeTracker:
    """One trial's shade, frame by frame: baseline, contact, peak, position,
    and the probe's Z at the peak (`peak_z`) and past it (`past_peak`).

    `update(t, shade, z)` takes the next frame (`t` seconds from the first
    frame, `shade` from `right_half_median_green`, `z` the probe's Z or None)
    and returns the status: from `past_peak` when it is known, else from the
    shade `position` (owner 2026-10-12). `mark()` freezes the state at the operator's
    Mark; a second Mark does not replace it (as the first Mark's time rules
    the video's band).
    """

    def __init__(self):
        self._t, self._raw, self._smooth, self._z = [], [], [], []
        self.baseline = None
        self.noise = None
        self.contact_t = None
        self.peak = None
        self.position = None
        self.peak_z = None          # the probe's Z where the smoothed shade peaked
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
        self._z.append(z)
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
        elif smooth > self.peak:
            self.peak, self.peak_z = smooth, z
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
            since = self._smooth[start_i:]
            self.peak = max(since)
            self.peak_z = self._z[start_i + since.index(self.peak)]

    def _place(self, smooth):
        if self.contact_t is None or self.peak is None or self.peak <= self.baseline:
            self.position, self.status = None, "No contact"
            return
        raw = (self.peak - smooth) / (self.peak - self.baseline)
        self.position = max(0.0, min(POSITION_CEILING, raw))
        past = self.past_peak
        self.status = (classify(self.position) if past is None
                       else classify_past_peak(past))

    # -- readings ------------------------------------------------------------
    @property
    def contact_lowered(self):
        """Steps lowered since the first Z seen, at the moment of contact."""
        if self._z0 is None or self._contact_z is None:
            return None
        return self._z0 - self._contact_z

    @property
    def past_peak(self):
        """Steps lowered past the smoothed shade's peak: its Z less the Z
        now (lowering is Z decreasing). None before contact and a peak, and
        while either Z is unknown."""
        if self.contact_t is None or self.peak_z is None or not self._z \
                or self._z[-1] is None:
            return None
        return self.peak_z - self._z[-1]

    @property
    def shade(self):
        """The smoothed shade now."""
        return self._smooth[-1] if self._smooth else None

    def state(self):
        return {"status": self.status, "position": self.position,
                "peak": self.peak, "baseline": self.baseline,
                "shade": self.shade, "contact_lowered": self.contact_lowered,
                "past_peak": self.past_peak, "peak_z": self.peak_z,
                "t": self._t[-1] if self._t else None}

    def mark(self):
        """Freeze the state now, once; returns the snapshot."""
        if self.mark_snapshot is None:
            self.mark_snapshot = self.state()
        return self.mark_snapshot
