"""The Sample Map's frame math (proposal-flake-coordinates.md section 3).

Pure functions, like `transfer_map_analysis`: no model, no store, no
matplotlib, so every rule is testable on synthetic corners.

Frames (3.1):

- **Stage frame G**: the locating axes' `(x, y)` in counts, boot-relative.
- **Sample frame S**: origin at corner A, +x along A->B, +y perpendicular
  toward the chip's interior (C and D have positive y), right-handed
  looking down the objective; units um.
- **Counts to um**: `K = diag(k_x, k_y)`, one um per count per axis
  (`um_per_count`). The chuck's and the DC probe's are owner bench facts
  not known yet: asking for one raises `BenchFactMissing` rather than
  guessing.

The rigid frame from A and B is the default; typed chip dimensions make the
affine fit the default (3.3). Degenerate input is refused in the
operator's words (`FrameRefused`, 3.4). Extent metrics (3.5) are computed
from a polygon in um at read time; `lateral_um` is the longest chord
(owner, 2026-10-04, Q16).

**The Rotator turns the chip** (owner, 2026-10-04): the SMC100 spins it about
a centre c that is neither corner A nor the stage origin. A registration is
fitted at the Rotator's angle phi0; at phi the chip has turned by
s (phi - phi0) about c, with s the Rotator's sense against the stage axes
(-1 when they are mirrored), so the current frame is the registered one
followed by that turn (`RotatedFrame`). c and s come from one feature marked
at several angles (`rotation_centre`). Turns are done in the stage's own
units, which assumes square axes (k_x = k_y, as every locating source has
today); a skewed stage shows up as the calibration's residual.
"""
import math

import numpy

#: Closure (the return to A) under this is "good", under the next "check",
#: else "poor"; rectangularity over the last asks whether the chip's shape
#: should be "quad". Owner-adjustable.
CLOSURE_GOOD_UM = 10.0
CLOSURE_CHECK_UM = 30.0
RECTANGULARITY_ASK_UM = 50.0
#: Degenerate corners (3.4): A and B closer than this (um: about the
#: proposal's 50 stepper counts, and the same rule for typed micrometer mm),
#: or an angle at A outside these bounds.
MIN_AB_UM = 30.0
MIN_CORNER_ANGLE_DEG = 20.0
MAX_CORNER_ANGLE_DEG = 160.0

#: The Rotator (owner 2026-10-04: it turns the chip). Under ROTATOR_SAME_DEG
#: is no turn (about 1 um at 5 mm). A calibration's marks must span at least
#: MIN_CALIBRATION_TURN_DEG and lie MIN_CALIBRATION_CHORD_UM apart; its
#: residual, and the closure of a corner re-marked after a turn, use the
#: good/check words below. A three-point fit whose two senses fit within
#: SENSE_MARGIN of each other cannot tell which way the Rotator turns.
#: Owner-adjustable, like the corner closure.
ROTATOR_SAME_DEG = 0.01
MIN_CALIBRATION_TURN_DEG = 2.0
MIN_CALIBRATION_CHORD_UM = 30.0
ROTATOR_CLOSURE_GOOD_UM = 10.0
ROTATOR_CLOSURE_CHECK_UM = 30.0
SENSE_MARGIN = 3.0

#: um per count of each locating axis. The stepper's lead screw gives
#: 0.625; the chuck's and the DC probe's are bench facts the owner has not
#: measured yet (flake-coords section 11, item 2): None until then.
UM_PER_COUNT = {"stepper": 0.625, "chuck": None, "dc": None}


class FrameRefused(ValueError):
    """Input the frame cannot use; the message is the operator's words."""


class BenchFactMissing(LookupError):
    """A value only the owner can measure at the bench is not known yet."""


def um_per_count(kind, typed=None):
    """The um per count of axis `kind`: the operator's `typed` value when
    given, else the table's. Never a guess: an unknown one raises."""
    if typed is not None:
        value = float(typed)
        if not value > 0:
            raise FrameRefused("Type a positive um per count.")
        return value
    if kind not in UM_PER_COUNT:
        raise BenchFactMissing(f"No um per count is known for a {kind!r} axis.")
    value = UM_PER_COUNT[kind]
    if value is None:
        raise BenchFactMissing(
            f"The {kind} axis's um per count is not known yet: move it 1000 "
            "counts against a stage micrometer slide and type the result "
            "(an owner bench fact).")
    return value


def _vec(p):
    return numpy.asarray(p, dtype=float)


def _um(p, k):
    """Stage counts to um, axis by axis."""
    return _vec(p) * _vec(k)


def _angle_deg(u, v):
    cos = float(numpy.dot(u, v) / (numpy.linalg.norm(u) * numpy.linalg.norm(v)))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _cross(u, v):
    return float(u[0] * v[1] - u[1] * v[0])


class RigidFrame:
    """Sample <- stage: q = F R(-theta) K (p - p_A); stage <- sample:
    p = p_A + K^-1 R(theta) F q, with F flipping y when `handedness` is -1."""

    kind = "rigid"

    def __init__(self, p_a, theta, k, handedness=1):
        self.p_a = tuple(float(v) for v in p_a)
        self.theta = float(theta)
        self.k = tuple(float(v) for v in k)
        self.handedness = 1 if handedness >= 0 else -1

    def to_sample(self, p):
        dx, dy = (_vec(p) - _vec(self.p_a)) * _vec(self.k)
        c, s = math.cos(self.theta), math.sin(self.theta)
        return (float(c * dx + s * dy), float(self.handedness * (-s * dx + c * dy)))

    def to_stage(self, q):
        qx, qy = float(q[0]), self.handedness * float(q[1])
        c, s = math.cos(self.theta), math.sin(self.theta)
        dx, dy = c * qx - s * qy, s * qx + c * qy
        return (float(self.p_a[0] + dx / self.k[0]), float(self.p_a[1] + dy / self.k[1]))


def rigid_frame(a, b, k, d=None):
    """The default frame from corners A and B (stage counts), with the
    handedness from D when it is marked (3.2). Refuses degenerate corners."""
    a, b = _vec(a), _vec(b)
    if float(numpy.linalg.norm(_um(b, k) - _um(a, k))) < MIN_AB_UM:
        raise FrameRefused("Corners A and B are the same point: mark B further "
                           "from A.")
    ab = _um(b, k) - _um(a, k)
    theta = math.atan2(ab[1], ab[0])
    handedness = 1
    if d is not None:
        ad = _um(d, k) - _um(a, k)
        if float(numpy.linalg.norm(ad)) == 0.0:
            raise FrameRefused("Corners A and D are the same point.")
        angle = _angle_deg(ab, ad)
        if not MIN_CORNER_ANGLE_DEG <= angle <= MAX_CORNER_ANGLE_DEG:
            raise FrameRefused("B and D are nearly in line with A: not a corner.")
        handedness = -1 if _cross(ab, ad) < 0 else 1
    return RigidFrame(a, theta, k, handedness)


def dimensions(a, b, d, k):
    """The chip's width |AB|, height (D's distance from the AB line) and the
    angle at A, in um and degrees."""
    ab = _um(b, k) - _um(a, k)
    ad = _um(d, k) - _um(a, k)
    width = float(numpy.linalg.norm(ab))
    return {"width_um": width, "height_um": abs(_cross(ab, ad)) / width,
            "angle_at_a_deg": _angle_deg(ab, ad)}


def _rectangle(params, handedness):
    theta, tx, ty, w, h = params
    u = numpy.array([math.cos(theta), math.sin(theta)])
    v = handedness * numpy.array([-math.sin(theta), math.cos(theta)])
    t = numpy.array([tx, ty])
    return numpy.array([t, t + w * u, t + w * u + h * v, t + h * v])


def rectangularity(corners, k):
    """RMS distance (um) from the four marked corners to the best-fitting
    rectangle (theta, t, W, H by least squares, 3.2). A check on the chip
    and the pointing together, not the frame's quality number."""
    if not all(name in corners for name in "ABCD"):
        raise FrameRefused("Rectangularity needs all four corners marked.")
    pts = numpy.array([_um(corners[name], k) for name in "ABCD"])
    ab, ad = pts[1] - pts[0], pts[3] - pts[0]
    handedness = -1 if _cross(ab, ad) < 0 else 1
    params = numpy.array([math.atan2(ab[1], ab[0]), pts[0][0], pts[0][1],
                          float(numpy.linalg.norm(ab)),
                          abs(_cross(ab, ad)) / float(numpy.linalg.norm(ab))])
    for _ in range(50):          # Gauss-Newton; a handful of steps converge
        residual = (_rectangle(params, handedness) - pts).ravel()
        jacobian = numpy.empty((residual.size, params.size))
        for j in range(params.size):
            step = numpy.zeros(params.size)
            step[j] = 1e-6 * max(1.0, abs(params[j]))
            jacobian[:, j] = ((_rectangle(params + step, handedness) - pts).ravel()
                              - residual) / step[j]
        delta = numpy.linalg.lstsq(jacobian, -residual, rcond=None)[0]
        params = params + delta
        if float(numpy.max(numpy.abs(delta))) < 1e-9:
            break
    offsets = _rectangle(params, handedness) - pts
    return float(math.sqrt(numpy.mean(numpy.sum(offsets ** 2, axis=1))))


def rectangularity_asks_quad(rms_um):
    return rms_um > RECTANGULARITY_ASK_UM


def closure_um(p_a, p_a_again, k):
    """How far the return to A landed from the first A, in um."""
    return float(numpy.linalg.norm(_um(p_a_again, k) - _um(p_a, k)))


def closure_word(um):
    if um < CLOSURE_GOOD_UM:
        return "good"
    if um < CLOSURE_CHECK_UM:
        return "check"
    return "poor"


class FittedFrame:
    """p = M q + t (stage counts from sample um), fitted to marked corners
    with typed chip dimensions (3.3). `rms_um` is the corners' residual in
    the sample frame."""

    def __init__(self, kind, matrix, shift, rms_um, um_per_count=None):
        self.kind = kind
        self.matrix = numpy.asarray(matrix, dtype=float)
        self.shift = numpy.asarray(shift, dtype=float)
        self._inverse = numpy.linalg.inv(self.matrix)
        self.rms_um = float(rms_um)
        self.um_per_count = um_per_count

    def to_stage(self, q):
        p = self.matrix @ _vec(q) + self.shift
        return (float(p[0]), float(p[1]))

    def to_sample(self, p):
        q = self._inverse @ (_vec(p) - self.shift)
        return (float(q[0]), float(q[1]))


def _checked_pairs(stage, sample):
    stage, sample = numpy.asarray(stage, float), numpy.asarray(sample, float)
    if len(stage) != len(sample):
        raise FrameRefused("Each marked corner needs its place on the chip.")
    if len(stage) < 3:
        raise FrameRefused("A fit with typed dimensions needs at least three "
                           "corners.")
    for pts in (stage, sample):
        spread = numpy.linalg.svd(pts - pts.mean(axis=0), compute_uv=False)
        if spread[-1] <= 1e-9 * max(spread[0], 1.0):
            raise FrameRefused("Those corners are in a line: mark three corners "
                               "that are not.")
    return stage, sample


def _rms_in_sample(frame, stage, sample):
    back = numpy.array([frame.to_sample(p) for p in stage])
    return float(math.sqrt(numpy.mean(numpy.sum((back - sample) ** 2, axis=1))))


def affine_fit(stage, sample):
    """p_i = A q_i + t by least squares over the marked corners (3 or more):
    absorbs unknown or unequal um per count and non-orthogonal axes, which
    is what the manual rig needs (3.3, 8)."""
    stage, sample = _checked_pairs(stage, sample)
    design = numpy.column_stack([sample, numpy.ones(len(sample))])
    solution = numpy.linalg.lstsq(design, stage, rcond=None)[0]   # 3 x 2
    frame = FittedFrame("affine", solution[:2].T, solution[2], 0.0)
    frame.rms_um = _rms_in_sample(frame, stage, sample)
    return frame


def similarity_fit(stage, sample):
    """p_i = s R q_i + t (one scale, square axes; a mirrored stage allowed):
    the scale is fitted, so `um_per_count` is reported (Umeyama, 2D)."""
    stage, sample = _checked_pairs(stage, sample)
    mu_p, mu_q = stage.mean(axis=0), sample.mean(axis=0)
    p0, q0 = stage - mu_p, sample - mu_q
    u, sigma, vt = numpy.linalg.svd(p0.T @ q0)
    d = numpy.diag([1.0, numpy.sign(numpy.linalg.det(u @ vt)) or 1.0])
    rotation = u @ d @ vt
    scale = float(numpy.trace(numpy.diag(sigma) @ d) / numpy.sum(q0 ** 2))
    matrix = scale * rotation
    frame = FittedFrame("similarity", matrix, mu_p - matrix @ mu_q, 0.0,
                        um_per_count=1.0 / scale)
    frame.rms_um = _rms_in_sample(frame, stage, sample)
    return frame


def default_fit(dimensions_typed):
    """Rigid (scale from the table) unless the chip's dimensions are typed."""
    return "affine" if dimensions_typed else "rigid"


# -- 3.5 extent ----------------------------------------------------------------

def bbox_extent(frame, p1, p2):
    """The MVP extent: two opposite corners of the flake marked on the stage,
    as an axis-aligned box in the sample frame."""
    return bbox_from_sample(frame.to_sample(p1), frame.to_sample(p2))


def bbox_from_sample(q1, q2):
    """The same box from two points already in the sample frame (the Rotator
    may turn between the two presses, so each is placed when it is made)."""
    (x1, y1), (x2, y2) = q1, q2
    x0, x3 = float(min(x1, x2)), float(max(x1, x2))
    y0, y3 = float(min(y1, y2)), float(max(y1, y2))
    return {"source": "stage_corners",
            "polygon_um": [(x0, y0), (x3, y0), (x3, y3), (x0, y3)]}


def shoelace(polygon):
    """Signed area of a polygon (counter-clockwise positive)."""
    pts = numpy.asarray(polygon, dtype=float)
    x, y = pts[:, 0], pts[:, 1]
    return float(0.5 * (numpy.dot(x, numpy.roll(y, -1)) - numpy.dot(y, numpy.roll(x, -1))))


def _hull(points):
    """Convex hull (monotone chain), counter-clockwise."""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and _cross(numpy.subtract(out[-1], out[-2]),
                                           numpy.subtract(p, out[-2])) <= 0:
                out.pop()
            out.append(p)
        return out
    lower, upper = half(pts), half(reversed(pts))
    return lower[:-1] + upper[:-1]


def extent_metrics(polygon):
    """Area (shoelace), `lateral_um` (the longest chord: Q16, owner
    2026-10-04) and aspect ratio (sides of the minimum-area bounding box,
    long over short) of a polygon in um. Computed, never stored."""
    if len(polygon) < 3:
        raise FrameRefused("An extent needs at least three points.")
    hull = numpy.array(_hull(polygon))
    lateral = max(math.dist(p, q) for p in hull for q in hull)
    best = None
    for i in range(len(hull)):
        edge = hull[(i + 1) % len(hull)] - hull[i]
        length = float(numpy.linalg.norm(edge))
        if length == 0.0:
            continue
        u = edge / length
        v = numpy.array([-u[1], u[0]])
        a, b = hull @ u, hull @ v
        sides = (float(a.max() - a.min()), float(b.max() - b.min()))
        if best is None or sides[0] * sides[1] < best[0] * best[1]:
            best = sides
    long_side, short_side = max(best), min(best)
    return {"area_um2": abs(shoelace(polygon)), "lateral_um": float(lateral),
            "aspect_ratio": long_side / short_side if short_side > 0 else math.inf}


# -- the Rotator (owner 2026-10-04) ------------------------------------------------

def _turn_matrix(sense, dphi_deg):
    a = math.radians((1 if sense >= 0 else -1) * float(dphi_deg))
    c, s = math.cos(a), math.sin(a)
    return numpy.array([[c, -s], [s, c]])


def rotate_about(p, centre, sense, dphi_deg):
    """Where stage point `p` goes when the Rotator turns `dphi_deg` about
    `centre` (stage units) with sense `sense`."""
    p, c = _vec(p), _vec(centre)
    q = c + _turn_matrix(sense, dphi_deg) @ (p - c)
    return (float(q[0]), float(q[1]))


def rotator_closure_word(um):
    if um < ROTATOR_CLOSURE_GOOD_UM:
        return "good"
    if um < ROTATOR_CLOSURE_CHECK_UM:
        return "check"
    return "poor"


class RotationCentre:
    """The Rotator's centre (stage units), sense, and how they were found:
    "chord" (two marks and a known sense, no residual) or "circle" (three
    or more, least squares, with its residual in um and its word)."""

    def __init__(self, centre, sense, method, n_points, residual_um=None):
        self.centre = (float(centre[0]), float(centre[1]))
        self.sense = 1 if sense >= 0 else -1
        self.method = method
        self.n_points = int(n_points)
        self.residual_um = None if residual_um is None else float(residual_um)
        self.quality = ("unchecked" if residual_um is None
                        else rotator_closure_word(self.residual_um))


def _fit_turn(points, angles, sense):
    """Least squares for p_i = c + R(s phi_i) u: (c, u, rms in stage units)."""
    rows, rhs = [], []
    for p, phi in zip(points, angles):
        m = _turn_matrix(sense, phi)
        rows.append([1.0, 0.0, m[0, 0], m[0, 1]])
        rows.append([0.0, 1.0, m[1, 0], m[1, 1]])
        rhs.extend(p)
    design, rhs = numpy.array(rows), numpy.array(rhs)
    solution = numpy.linalg.lstsq(design, rhs, rcond=None)[0]
    residual = (design @ solution - rhs).reshape(-1, 2)
    rms = float(math.sqrt(numpy.mean(numpy.sum(residual ** 2, axis=1))))
    return solution[:2], solution[2:], rms


def rotation_centre(points, angles_deg, k, sense=None):
    """The Rotator's centre from one feature marked at several angles.

    `points` are the stage positions (stage units) of the same feature,
    `angles_deg` the Rotator's angle at each, `k` the um per stage unit (for
    the thresholds and the residual). Two marks fix the centre only with a
    known `sense` (the chord's perpendicular bisector has two candidates,
    mirror images); three or more fit both senses and keep the one that
    fits, refusing a set that fits neither (not a turn) or both (ambiguous).
    """
    points = [tuple(float(v) for v in p) for p in points]
    angles = [float(a) for a in angles_deg]
    if len(points) != len(angles):
        raise FrameRefused("Each calibration mark needs the Rotator's angle "
                           "when it was made.")
    if len(points) < 2:
        raise FrameRefused("Mark the same feature at two Rotator angles at least "
                           "(three tell which way it turns).")
    if max(angles) - min(angles) < MIN_CALIBRATION_TURN_DEG:
        raise FrameRefused(f"Turn the Rotator further between marks: at least "
                           f"{MIN_CALIBRATION_TURN_DEG:g} degrees.")
    scale = float(numpy.mean(_vec(k)))
    pts = numpy.array(points)
    spread = max(math.dist(a, b) for a in points for b in points) * scale
    if spread < MIN_CALIBRATION_CHORD_UM:
        raise FrameRefused("The marks are too close together: pick a feature "
                           "farther from the Rotator's centre, or turn further.")
    if len(points) == 2:
        if sense is None:
            raise FrameRefused("Two marks cannot tell which way the Rotator turns: "
                               "mark the feature at a third angle.")
        m = _turn_matrix(sense, angles[1] - angles[0])
        centre = numpy.linalg.solve(numpy.eye(2) - m, pts[1] - m @ pts[0])
        return RotationCentre(centre, sense, "chord", 2)
    fits = {s: _fit_turn(points, angles, s) for s in (1, -1)}
    best = min(fits, key=lambda s: fits[s][2])
    other = -best
    rms_um = fits[best][2] * scale
    if rotator_closure_word(rms_um) == "poor":     # a tilt, a wobble, unequal axes
        raise FrameRefused(f"These marks are not a turn about one centre (they miss "
                           f"by {rms_um:.0f} um): check it is the same feature, and "
                           "that the Rotator spins the chip rather than tilting it.")
    if sense is not None and best != (1 if sense >= 0 else -1):
        raise FrameRefused("These marks turn the other way from the sense given.")
    if fits[other][2] * scale <= max(SENSE_MARGIN * rms_um, 1e-9) and sense is None:
        raise FrameRefused("These marks fit both senses: mark the feature at more "
                           "widely spread angles.")
    return RotationCentre(fits[best][0], best, "circle", len(points), rms_um)


class RotatedFrame:
    """The registered frame (at phi0) followed by the Rotator's turn
    dphi = phi - phi0 about `centre` with `sense`: stage <- sample is
    p = c + R(s dphi)(base.to_stage(q) - c), and back the same way."""

    def __init__(self, base, centre, sense, dphi_deg):
        self.base = base
        self.kind = base.kind
        self.centre = (float(centre[0]), float(centre[1]))
        self.sense = 1 if sense >= 0 else -1
        self.dphi_deg = float(dphi_deg)

    def to_stage(self, q):
        return rotate_about(self.base.to_stage(q), self.centre, self.sense, self.dphi_deg)

    def to_sample(self, p):
        return self.base.to_sample(rotate_about(p, self.centre, self.sense, -self.dphi_deg))
