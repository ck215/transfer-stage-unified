"""CAP-1: RGB analysis never measures, logs, forwards or records a black,
stale or partial grab, and it samples near the source's own rate.

The bench (trial 32, 707 index rows): AmLite repaints its camera image at
~7 fps; a region grab that lands mid-repaint returns a black fill, a constant
older picture ("stale", red 1.3576 there), or a partly painted frame (live
only in the top rows). The run loop grabbed at kHz with no sleep, so about
half of every profile was glitch rows, and the video flashed (audit
`capture.md`, CAP-1/CAP-2). Owner ruling 2026-10-07: sample on SETTLED frames
at the source rate.

The fakes here are read-indexed (`ScriptedCapture`: the i-th read sees the
i-th picture), so what the loop must accept and reject is fixed by the
script and the counters are exact; or clock-driven (`ClockCapture`), for the
rate. No display is touched: `Screen` takes the fake as its capture factory.
"""
import threading
import time

import numpy
import pytest

from devices.screen import Screen
from events import events
from model.rgb_analysis import RgbAnalysis


# ---------------------------------------------------------------------
# pictures: a lit field with a few red pixels, as the bench region was
# ---------------------------------------------------------------------

H, W = 20, 20                      # 400 pixels: one red pixel is 0.25 %
FIELD = (120, 140, 60)             # AmLite's yellow-green scene (RGB)
OLD_FIELD = (90, 70, 50)           # the stale picture: an older, darker view


def picture(red_pixels, field=FIELD):
    """An RGB frame: `field` everywhere and `red_pixels` pure-red pixels
    counted back from the bottom-right, so a partial paint of the TOP rows
    carries none of them (its red differs from the live one's, as on the
    bench)."""
    frame = numpy.empty((H, W, 3), dtype=numpy.uint8)
    frame[:, :] = field
    if red_pixels:
        frame.reshape(-1, 3)[-red_pixels:] = (200, 0, 0)
    return frame


def partial(frame, rows=5):
    """A repaint caught part-way: the top `rows` painted, the rest black."""
    out = numpy.zeros_like(frame)
    out[:rows] = frame[:rows]
    return out


LIVE = [picture(n) for n in (2, 3, 4, 5, 6)]   # 0.5, 0.75, 1.0, 1.25, 1.5 %
BLACK = numpy.zeros((H, W, 3), dtype=numpy.uint8)
STALE = picture(11, OLD_FIELD)                 # 2.75 %: no live value
DARK = numpy.full((H, W, 3), (10, 12, 8), dtype=numpy.uint8)  # dim, not zero


def red_of(frame):
    """The red percent at the default threshold, computed here rather than
    by the code under test."""
    r, g, b = (frame[:, :, i].astype(int) for i in range(3))
    return float(numpy.count_nonzero((r > 150) & (g < 100) & (b < 100))
                 / (frame.shape[0] * frame.shape[1]) * 100.0)


LIVE_REDS = {red_of(frame) for frame in LIVE}
BAD = {"black": BLACK, "stale": STALE, "dark": DARK,
       **{f"partial{k}": partial(frame) for k, frame in enumerate(LIVE)}}


def same(a, b):
    return a.shape == b.shape and numpy.array_equal(a[:, :, :3], b[:, :, :3])


def name_of(frame):
    for k, live in enumerate(LIVE):
        if same(frame, live):
            return f"L{k}"
    for name, bad in BAD.items():
        if same(frame, bad):
            return name
    return "unknown"


# ---------------------------------------------------------------------
# the fakes
# ---------------------------------------------------------------------

class ScriptedCapture:
    """The i-th read sees `script[i]`, then the last entry for ever. A fresh
    buffer per read, as mss returns one."""

    def __init__(self, script):
        self.script = list(script)
        self.grabs = 0
        self._lock = threading.Lock()

    def grab(self, region):
        with self._lock:
            index = min(self.grabs, len(self.script) - 1)
            self.grabs += 1
        return self.script[index].copy()

    def close(self):
        pass


class ClockCapture:
    """A camera on a clock: the picture changes every `period` seconds, or
    never (`period=None`: a stalled viewer). Reads cost nothing, so a loop
    with no cap reads it as fast as Python can."""

    def __init__(self, period):
        self.period, self.started, self.grabs = period, time.monotonic(), 0

    def grab(self, region):
        self.grabs += 1
        if self.period is None:
            return LIVE[2].copy()
        shown = int((time.monotonic() - self.started) / self.period)
        return LIVE[shown % len(LIVE)].copy()

    def close(self):
        pass


class FlickerCapture:
    """A viewer that never holds still: every read differs from the last."""

    def __init__(self):
        self.grabs = 0

    def grab(self, region):
        self.grabs += 1
        return LIVE[self.grabs % len(LIVE)].copy()

    def close(self):
        pass


def _model(tmp_path, capture):
    model = RgbAnalysis(screen=Screen(factory=lambda: capture))
    model.output_root = tmp_path / "runs"
    model.run_name = "CAP1"
    model.open()
    model.set_region(0, 0, W, H)
    return model


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


#: The bench's repaint, read by read. Each live picture held for two reads
#: settles; each transient seen once is a repaint in progress; a transient
#: seen twice in a row held still for at least the settle gap.
BENCH_SCRIPT = [
    ("L0", LIVE[0]), ("L0", LIVE[0]),               # settles: the baseline
    ("black", BLACK), ("stale", STALE),             # black -> stale -> live,
    ("L1", LIVE[1]), ("L1", LIVE[1]),               #   within one repaint
    ("stale", STALE), ("stale", STALE),             # the stale picture, held
    ("black", BLACK), ("black", BLACK),             # a black fill, held
    ("L1", LIVE[1]), ("L1", LIVE[1]),               # the same live picture
    ("partial2", partial(LIVE[2])),                 # a partial paint
    ("L2", LIVE[2]), ("L2", LIVE[2]),
    ("black", BLACK), ("stale", STALE),             # another repaint
    ("L3", LIVE[3]), ("L3", LIVE[3]),
    ("dark", DARK), ("dark", DARK),                 # dim fill, no red, held
    ("stale", STALE), ("stale", STALE),             # stale again, held
    ("black", BLACK), ("stale", STALE),             # the full bench sequence
    ("partial4", partial(LIVE[4])),                 #   ending in a partial
    ("L4", LIVE[4]), ("L4", LIVE[4]),               # and L4 for ever after
]


@pytest.fixture
def bench(tmp_path):
    """A run over BENCH_SCRIPT, recording everything the loop lets out: the
    rows (subscribe), the frames (subscribe_frames), every red % it
    publishes, and every event it writes."""
    capture = ScriptedCapture(frame for _, frame in BENCH_SCRIPT)
    model = _model(tmp_path, capture)
    seen = {"rows": [], "frames": [], "published": [], "events": []}
    model.subscribe(lambda t, red, positions: seen["rows"].append(red))
    model.subscribe_frames(lambda t, frame, red: seen["frames"].append((frame, red)))
    publish = model._publish_red

    def recording_publish(red):
        seen["published"].append(red)
        return publish(red)

    model._publish_red = recording_publish
    real = {name: getattr(events, name) for name in ("debug", "info", "warn", "error")}

    def spy(name):
        def call(title, message, **kwargs):
            if kwargs.get("source") == RgbAnalysis.NAME:
                seen["events"].append((name, title, message))
            return real[name](title, message, **kwargs)
        return call

    for name in real:
        setattr(events, name, spy(name))
    try:
        model.start_run(confirmed=True)
        run = model._run
        assert _wait_for(lambda: capture.grabs >= len(BENCH_SCRIPT) + 4
                         and run.frames >= 8), (capture.grabs, run.frames)
        model.end_run()
        run.thread.join(2.0)
        assert not run.thread.is_alive()
        yield model, run, seen
    finally:
        for name, fn in real.items():
            setattr(events, name, fn)
        model.close()


# ---------------------------------------------------------------------
# 1-3, 5: nothing bad gets out
# ---------------------------------------------------------------------

def test_no_glitch_grab_becomes_a_row(bench):
    """Pre-fix: black (0.0), stale (2.75) and the partial paints (0.0) were
    each measured and logged, as on the bench."""
    model, run, seen = bench
    assert run.log.red_values, "the run logged nothing at all"
    assert set(run.log.red_values) <= LIVE_REDS, run.log.red_values
    assert seen["rows"] == run.log.red_values
    # One row per settled live change, in order: L0, L1, L2, L3, L4.
    assert run.log.red_values == [red_of(frame) for frame in LIVE]


def test_no_glitch_grab_reaches_a_frame_subscriber(bench):
    """The Transfer Map's video and Mark frame read these frames."""
    model, run, seen = bench
    names = [name_of(frame) for frame, _ in seen["frames"]]
    assert names and all(name.startswith("L") for name in names), names
    assert all(red in LIVE_REDS for _, red in seen["frames"])
    assert len(seen["frames"]) == run.frames


def test_no_glitch_grab_reaches_the_red_state(bench):
    model, run, seen = bench
    assert seen["published"] and set(seen["published"]) <= LIVE_REDS
    assert model.current_red == red_of(LIVE[4])
    assert run.baseline_red == red_of(LIVE[0]), "baseline from a glitch"


def test_the_counters_say_what_was_thrown_away(bench):
    """Exact, because the script fixes every read: 8 unconfirmed reads
    (black, stale | partial2 | black, stale | black, stale, partial4), 2
    settled black fills (black, dark-after-red), 2 settled stale pictures."""
    model, run, seen = bench
    assert run.rejected_unsettled == 8
    assert run.rejected_black == 2
    assert run.rejected_stale == 2
    assert run.frames >= 8                     # L0 L1 L1 L2 L3 L4, then L4s
    assert (model.frames_accepted, model.rejected_black, model.rejected_stale,
            model.rejected_unsettled) == (run.frames, 2, 2, 8)
    snapshot = model.state
    assert snapshot["run"]["rejected_black"] == 2
    assert snapshot["run"]["rejected_stale"] == 2
    assert snapshot["run"]["rejected_unsettled"] == 8
    assert snapshot["run"]["accepted"] == run.frames
    assert snapshot["values"]["rejected_black"] == "2"
    assert snapshot["values"]["rejected_stale"] == "2"
    assert snapshot["values"]["rejected_unsettled"] == "8"


def test_a_rejected_grab_writes_no_event(bench):
    """The counters are the record: no event line per rejection, and the
    tray hears only the run's own lifecycle."""
    model, run, seen = bench
    shown = {title for kind, title, _ in seen["events"] if kind != "debug"}
    assert shown <= {"Run Started", "Baseline Set", "Run Ended"}, shown
    written = {title for kind, title, _ in seen["events"] if kind == "debug"}
    assert written <= {"Run Loop", "Run Configuration", "Rate"}, written


def test_the_sidecar_records_the_gate_and_what_it_threw_away(bench):
    """Two runs are comparable only under the same gate: the sidecar says
    which, and what the gate did."""
    import json
    model, run, seen = bench
    model.save()
    meta = json.loads((model.run_dir / "CAP1_station_meta.json").read_text())
    assert (meta["rejected_black"], meta["rejected_stale"],
            meta["rejected_unsettled"]) == (2, 2, 8)
    assert meta["frames_captured"] == run.frames
    assert meta["grabs"] == run.grabs > meta["frames_captured"]
    gate = meta["settle_gate"]
    assert gate["settle_s"] == RgbAnalysis.SETTLE_S
    assert gate["min_sample_interval_s"] == pytest.approx(
        RgbAnalysis.MIN_SAMPLE_INTERVAL_S, abs=1e-6)


def test_the_counters_are_diagnostics_elements(tmp_path):
    model = _model(tmp_path, ScriptedCapture([LIVE[0]]))
    try:
        diagnostics = next(s for s in model.schema["sections"]
                           if s["title"] == "Diagnostics")
        attrs = [e.get("model_attr") for e in diagnostics["elements"]]
        assert attrs == ["position_age", "frames_accepted", "rejected_black",
                         "rejected_stale", "rejected_unsettled"]
        assert diagnostics["tier"] == 3
        assert all(model.state["values"][a] == "0" for a in attrs[1:])
        for attr in attrs[1:]:
            assert not model.set_value(attr, "5").is_ok   # read-only
    finally:
        model.close()


def test_the_first_grab_of_a_run_is_never_the_baseline_if_black(tmp_path):
    """The bench's trial 32 took its extremes from glitches; a run that opens
    on a black fill must not take its baseline (or a row) from it."""
    capture = ScriptedCapture([BLACK, BLACK, BLACK, LIVE[1], LIVE[1]])
    model = _model(tmp_path, capture)
    try:
        model.start_run(confirmed=True)
        run = model._run
        assert _wait_for(lambda: run.frames >= 1)
        model.end_run()
        run.thread.join(2.0)
        assert run.baseline_red == red_of(LIVE[1])
        assert 0.0 not in run.log.red_values
        assert run.rejected_black >= 1
    finally:
        model.close()


# ---------------------------------------------------------------------
# 4: the rate cap
# ---------------------------------------------------------------------

def _rates(model, capture, warmup=0.4, window=1.0):
    run = model._run
    time.sleep(warmup)
    grabs, frames, started = capture.grabs, run.frames, time.monotonic()
    time.sleep(window)
    elapsed = time.monotonic() - started
    return (capture.grabs - grabs) / elapsed, (run.frames - frames) / elapsed


def test_the_loop_samples_near_the_source_rate_never_at_khz(tmp_path):
    """A 20 fps camera: the loop settles at about SOURCE_OVERSAMPLE x 20
    samples a second, never above the floor's ceiling, and the measured
    source rate is the camera's. Pre-fix: thousands of reads a second."""
    capture = ClockCapture(period=0.05)
    model = _model(tmp_path, capture)
    try:
        model.start_run(confirmed=True)
        reads, accepted = _rates(model, capture)
        source = model.source_rate_hz
    finally:
        model.end_run()
        model.close()
    ceiling = 1.0 / RgbAnalysis.MIN_SAMPLE_INTERVAL_S
    assert accepted <= ceiling * 1.1, accepted
    assert reads <= ceiling * RgbAnalysis.SETTLE_READS, reads
    assert accepted <= RgbAnalysis.SOURCE_OVERSAMPLE * 20 * 1.3, accepted
    assert accepted >= 5, "the cap starved the loop"
    assert 10 <= source <= 40, source


def test_a_stalled_viewer_is_sampled_slowly_not_spun(tmp_path):
    """No picture ever changes, so no source rate can be measured: the loop
    falls back to its slowest interval rather than spinning on reads."""
    capture = ClockCapture(period=None)
    model = _model(tmp_path, capture)
    try:
        model.start_run(confirmed=True)
        reads, accepted = _rates(model, capture)
    finally:
        model.end_run()
        model.close()
    slowest = 1.0 / RgbAnalysis.MAX_SAMPLE_INTERVAL_S
    assert accepted <= slowest * 1.2, accepted
    assert reads <= 2 * accepted + 4, (reads, accepted)
    assert accepted >= 5, accepted


def test_a_viewer_that_never_settles_yields_nothing_and_never_spins(tmp_path):
    """Every read differs: nothing is accepted, nothing is logged or
    forwarded, and the re-reads stay bounded by the settle gap."""
    capture = FlickerCapture()
    model = _model(tmp_path, capture)
    frames = []
    model.subscribe_frames(lambda t, frame, red: frames.append(red))
    try:
        model.start_run(confirmed=True)
        reads, accepted = _rates(model, capture, warmup=0.1, window=0.5)
        run = model._run
        assert accepted == 0 and run.frames == 0 and run.rows == 0
        assert frames == [] and run.rejected_unsettled > 0
        assert reads <= 1.0 / RgbAnalysis.SETTLE_S + 10, reads
        assert model.is_running, "an unsettled viewer is not a failure"
    finally:
        model.end_run()
        model.close()


# ---------------------------------------------------------------------
# 6: the stop path never waits on a re-read
# ---------------------------------------------------------------------

class StallingReread:
    """The first read returns at once; the second (the confirming re-read)
    blocks until released."""

    def __init__(self):
        self.grabs = 0
        self.blocked = threading.Event()
        self.release = threading.Event()

    def grab(self, region):
        self.grabs += 1
        if self.grabs == 2:
            self.blocked.set()
            self.release.wait(5.0)
        return LIVE[2].copy()

    def close(self):
        pass


@pytest.mark.parametrize("stop", ["end_run", "estop"])
def test_the_stop_never_waits_on_a_re_read_in_flight(tmp_path, stop):
    capture = StallingReread()
    model = _model(tmp_path, capture)
    try:
        model.start_run(confirmed=True)
        run = model._run
        assert capture.blocked.wait(2.0), "the loop never re-read"
        started = time.monotonic()
        result = getattr(model, stop)()
        elapsed = time.monotonic() - started
        assert result is True
        assert elapsed < 0.1, f"{stop} blocked for {elapsed:.3f}s"
        assert not model.is_running
        capture.release.set()
        run.thread.join(2.0)
        assert not run.thread.is_alive()
        assert capture.grabs == 2, "the loop read again after the stop"
    finally:
        capture.release.set()
        model.close()


def test_the_stop_lands_mid_settle_without_waiting(tmp_path):
    """A viewer that never settles keeps the loop inside its settle waits;
    every one of them is the run's stop event, so the stop is immediate and
    the thread leaves at once."""
    capture = FlickerCapture()
    model = _model(tmp_path, capture)
    try:
        model.start_run(confirmed=True)
        run = model._run
        assert _wait_for(lambda: run.rejected_unsettled >= 3)
        started = time.monotonic()
        assert model.end_run() is True
        assert time.monotonic() - started < 0.05
        run.thread.join(0.5)
        assert not run.thread.is_alive(), "the loop kept settling after Stop"
        assert run.frames == 0 and run.rows == 0
    finally:
        model.close()


def test_the_settle_constants_are_named_and_sane():
    """No magic numbers inline: the gate and the cap are class constants."""
    assert RgbAnalysis.SETTLE_S >= 0.005
    assert 0 <= RgbAnalysis.SETTLE_TOLERANCE * 100 < RgbAnalysis.CHANGE_STEP
    assert RgbAnalysis.SETTLE_READS >= 3
    assert 0 < RgbAnalysis.MIN_SAMPLE_INTERVAL_S < RgbAnalysis.MAX_SAMPLE_INTERVAL_S
    assert RgbAnalysis.SOURCE_OVERSAMPLE >= 1.0


# ---------------------------------------------------------------------
# RG-1 (2026-10-07): the five numbers beside the red share obey the gate
# ---------------------------------------------------------------------

def six_of(frame):
    """The six numbers at the default thresholds, computed here rather than
    by the code under test: the red, green and blue shares (each channel
    above 150 with the other two below 100) and the channel means."""
    r, g, b = (frame[:, :, i].astype(int) for i in range(3))
    size = r.size

    def share(mask):
        return float(numpy.count_nonzero(mask) / size * 100.0)

    return (share((r > 150) & (g < 100) & (b < 100)),
            share((g > 150) & (r < 100) & (b < 100)),
            share((b > 150) & (r < 100) & (g < 100)),
            float(r.mean()), float(g.mean()), float(b.mean()))


def test_no_glitch_grab_reaches_the_five_new_numbers(tmp_path):
    """Over the bench script: every row a subscriber gets carries the five
    numbers of a settled live picture, one row per live change in order,
    and never those of a black, stale, dark or part-painted grab."""
    capture = ScriptedCapture(frame for _, frame in BENCH_SCRIPT)
    model = _model(tmp_path, capture)
    rows = []
    model.subscribe(lambda t, red, row: rows.append((red, dict(row))))
    try:
        model.start_run(confirmed=True)
        run = model._run
        assert _wait_for(lambda: capture.grabs >= len(BENCH_SCRIPT) + 4
                         and run.frames >= 8), (capture.grabs, run.frames)
        model.end_run()
        run.thread.join(2.0)
        keys = RgbAnalysis.CHANNEL_KEYS
        got = [tuple(row[k] for k in keys) for _, row in rows]
        assert len(got) == len(LIVE), got
        for numbers, frame in zip(got, LIVE):
            assert numbers == pytest.approx(six_of(frame)[1:])
        glitches = {six_of(frame)[1:] for frame in BAD.values()}
        assert not glitches & set(got), "a glitch grab's numbers were published"
        assert [red for red, _ in rows] == [six_of(frame)[0] for frame in LIVE]
        latest = model.state["run"]["latest"]
        assert [latest[k] for k in RgbAnalysis.RGB_KEYS] == \
            pytest.approx(six_of(LIVE[4]))
    finally:
        model.close()


def test_a_viewer_that_never_settles_publishes_none_of_the_six(tmp_path):
    capture = FlickerCapture()
    model = _model(tmp_path, capture)
    rows = []
    model.subscribe(lambda t, red, row: rows.append(row))
    try:
        model.start_run(confirmed=True)
        run = model._run
        assert _wait_for(lambda: run.rejected_unsettled >= 5)
        model.end_run()
        run.thread.join(2.0)
        assert rows == [] and run.frames == 0
        assert model.state["run"]["latest"] == dict.fromkeys(RgbAnalysis.RGB_KEYS)
        assert model.current_green is None and model.mean_red is None
    finally:
        model.close()
