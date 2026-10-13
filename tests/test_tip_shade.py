"""The tip's shade and the live force status (owner 2026-10-06): the right
half of the tip, median green, tracked against a baseline taken at the start
of the video. Contact is the shade staying clearly above baseline; force is
how far the shade has fallen back down its peak. Pure functions in
model/tip_shade.py."""
import math

import numpy as np
import pytest

from model import tip_shade as ts


def synth(contact_s=4.0, rise_s=3.0, peak=1.5, fall_s=4.0, base=150.0, end_s=14.0, hz=15,
          noise=0.0, seed=1):
    """A shade series: flat at `base`, a smooth rise to base*peak starting at
    `contact_s`, then a fall back toward base over `fall_s`. -> (t, shade)."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, end_s, 1 / hz)
    out = np.full_like(t, base)
    up = (t >= contact_s) & (t < contact_s + rise_s)
    out[up] = base + (peak - 1) * base * (1 - np.cos(math.pi * (t[up] - contact_s) / rise_s)) / 2
    dn = t >= contact_s + rise_s
    frac = np.clip((t[dn] - contact_s - rise_s) / fall_s, 0, 1)
    out[dn] = base + (peak - 1) * base * (1 + np.cos(math.pi * frac)) / 2
    return t, out + rng.normal(0, noise, len(t))


def run(t, shade, z=None):
    tr = ts.ShadeTracker()
    states = []
    for i, (tt, ss) in enumerate(zip(t, shade)):
        tr.update(float(tt), float(ss), None if z is None else float(z[i]))
        states.append(tr.status)
    return tr, states


def test_the_shade_is_the_median_green_of_the_right_half():
    rgb = np.zeros((10, 8, 3), dtype=np.uint8)
    rgb[:, :4, 1] = 200                     # left half: ignored
    rgb[:, 4:, 1] = 90
    rgb[0, 5, 1] = 255                      # one hot pixel does not move a median
    assert ts.right_half_median_green(rgb) == 90.0


def test_the_shade_of_a_degenerate_frame_is_none():
    assert ts.right_half_median_green(None) is None
    assert ts.right_half_median_green(np.zeros((0, 0, 3), np.uint8)) is None


def test_no_contact_before_the_baseline_and_while_the_shade_is_flat():
    t, s = synth(contact_s=99)
    tr, states = run(t, s)
    assert set(states) == {"No contact"}
    assert tr.baseline == pytest.approx(150.0)
    assert tr.contact_t is None and tr.position is None


def test_contact_is_declared_after_a_sustained_rise_and_back_dated_to_its_start():
    t, s = synth(contact_s=4.0)
    tr, states = run(t, s)
    first = states.index("Contact")
    assert t[first] > 4.0 + 0.4            # not before the rise has lasted
    assert t[first] < 4.0 + 2.0
    assert 4.0 <= tr.contact_t < t[first]  # but dated from where the rise began (smoothed)


def test_a_brief_spike_is_not_contact():
    t, s = synth(contact_s=99)
    s = s.copy()
    s[40:43] += 80                          # three frames of flash
    tr, states = run(t, s)
    assert tr.contact_t is None and set(states) == {"No contact"}


def test_position_is_zero_at_the_peak_and_one_back_at_baseline():
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=4.0, end_s=14.0)
    tr, states = run(t, s)
    assert tr.position == pytest.approx(1.0, abs=0.08)
    assert states[-1] == "High"


def test_status_walks_contact_low_medium_high_on_the_way_down():
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=6.0, end_s=16.0)
    _, states = run(t, s)
    order = [w for i, w in enumerate(states) if i == 0 or w != states[i - 1]]
    assert order == ["No contact", "Contact", "Low", "Medium", "High"]


def test_the_peak_is_the_highest_smoothed_shade_since_contact():
    t, s = synth(peak=1.4, base=120.0)
    tr, _ = run(t, s)
    assert tr.peak == pytest.approx(1.4 * 120.0, rel=0.03)


def test_noise_does_not_break_the_walk():
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=6.0, end_s=16.0, noise=2.0)
    _, states = run(t, s)
    order = [w for i, w in enumerate(states) if i == 0 or w != states[i - 1]]
    assert order[:2] == ["No contact", "Contact"] and order[-1] == "High"
    assert "Low" in order and "Medium" in order


def test_a_spike_after_contact_does_not_become_the_peak():
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=6.0, end_s=16.0)
    s = s.copy(); s[int(8 * 15):int(8 * 15) + 2] = 255.0
    tr, _ = run(t, s)
    assert tr.peak < 1.6 * 150.0


def test_contact_lowered_counts_steps_since_the_first_z_seen():
    t, s = synth(contact_s=4.0)
    z = 1000.0 - np.clip((t - 1.0) * 10, 0, None)      # lowering 10 steps a second from 1 s
    tr, _ = run(t, s, z)
    assert tr.contact_lowered == pytest.approx(10 * (tr.contact_t - 1.0), abs=1.0)


def test_the_mark_freezes_a_snapshot():
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=6.0, end_s=16.0)
    tr = ts.ShadeTracker()
    snap = None
    for tt, ss in zip(t, s):
        tr.update(float(tt), float(ss), None)
        if snap is None and tt >= 8.5:
            snap = tr.mark()
    assert snap["status"] in ("Low", "Medium")
    assert tr.mark_snapshot == snap and snap["position"] == pytest.approx(tr.mark_snapshot["position"])
    assert tr.position > snap["position"]                # the trace ran on, the snapshot did not


def test_a_second_mark_keeps_the_first_snapshot():
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=6.0, end_s=16.0)
    tr = ts.ShadeTracker()
    first = None
    for tt, ss in zip(t, s):
        tr.update(float(tt), float(ss), None)
        if tt >= 7.0 and first is None:
            first = tr.mark()
        if tt >= 10.0:
            tr.mark()
            break
    assert tr.mark_snapshot == first


def test_a_mark_before_contact_has_no_force():
    tr = ts.ShadeTracker()
    for i in range(30):
        tr.update(i / 15, 150.0, None)
    snap = tr.mark()
    assert snap["position"] is None and snap["status"] == "No contact"


def test_words_for_the_status_field():
    assert ts.status_text("No contact") == "No contact"
    assert ts.status_text("Contact") == "Contact"
    assert ts.status_text("Low") == "Low force"
    assert ts.status_text("Medium") == "Medium force"
    assert ts.status_text("High") == "High force"
    assert ts.status_text(None) == ""


def test_the_thresholds_are_named_constants():
    assert ts.PEAK_PASSED == 0.10 and ts.LOW_MAX == pytest.approx(1 / 3)
    assert ts.MEDIUM_MAX == pytest.approx(2 / 3)
    assert ts.classify(0.05) == "Contact" and ts.classify(0.2) == "Low"
    assert ts.classify(0.5) == "Medium" and ts.classify(0.9) == "High"
    assert ts.classify(None) == "No contact"


# -- owner 2026-10-12: the force from Z travel past the shade's peak ----------
# The shade's baseline moved tonight (~145 -> ~120 on the same tips), which
# stretched the shade scale: trial 46's tip broke reading 0.57 "Medium". The
# steps the probe was lowered past the smoothed shade's peak are consistent
# across tips; they classify the force whenever Z is known, and the shade
# position stays the fallback (and is still kept, for history).

import csv
import pathlib

TRIAL_46 = pathlib.Path(__file__).parent / "fixtures" / "trial46_shade_z.csv"


def lowered(t, contact_s=4.0, peak_at_s=7.0, z0=1000.0, rate=3.0, sign=-1.0):
    """Z for a probe lowered `rate` steps a second from 1 s (lowering is Z
    DECREASING, whatever the sign of the absolute value: `z0` may be
    positive or negative)."""
    return np.array([z0 - rate * max(0.0, tt - 1.0) for tt in t])


def walk_to(past, z0=1000.0, rate=3.0):
    """A trial lowered until `past` steps beyond the smoothed shade's peak,
    then held: the tracker at the end, its states on the way."""
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=30.0, end_s=40.0)
    z = lowered(t, z0=z0, rate=rate)
    tr = ts.ShadeTracker()
    states = []
    for tt, ss, zz in zip(t, s, z):
        tr.update(float(tt), float(ss), float(zz))
        states.append(tr.status)
        if tr.past_peak is not None and tr.past_peak >= past:
            break
    return tr, states


def test_the_past_peak_thresholds_are_the_owners_named_constants():
    assert ts.PAST_PEAK_LOW_MAX == 10
    assert ts.PAST_PEAK_MEDIUM_MAX == 17
    assert ts.PAST_PEAK_HIGH_MAX == 21
    assert ts.PAST_PEAK_CONTACT_MAX == 2
    c = ts.classify_past_peak
    assert c(None) == "No contact"
    assert c(-3) == "Contact" and c(0) == "Contact" and c(1) == "Contact"
    assert c(2) == "Low" and c(9) == "Low"
    assert c(10) == "Medium" and c(16) == "Medium"
    assert c(17) == "High" and c(20) == "High"
    assert c(21) == "Break risk" and c(23) == "Break risk" and c(96) == "Break risk"


def test_break_risk_has_words_for_the_status_field():
    assert ts.status_text("Break risk") == "Break risk"


def test_past_peak_is_none_before_contact_and_without_z():
    t, s = synth(contact_s=99)
    tr, _ = run(t, s, lowered(t))
    assert tr.past_peak is None and tr.peak_z is None
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=6.0, end_s=16.0)
    tr, _ = run(t, s)                                   # no Z at all
    assert tr.past_peak is None and tr.peak_z is None


def test_the_peak_z_is_the_probes_z_where_the_smoothed_shade_peaked():
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=6.0, end_s=16.0)
    z = lowered(t)
    tr, _ = run(t, s, z)
    smooth_peak_t = tr._t[tr._smooth.index(max(tr._smooth))]
    assert tr.peak_z == pytest.approx(1000.0 - 3.0 * (smooth_peak_t - 1.0), abs=0.01)
    assert tr.past_peak == pytest.approx(tr.peak_z - z[-1])


@pytest.mark.parametrize("past, status", [(0, "Contact"), (5, "Low"),
                                          (12, "Medium"), (19, "High"),
                                          (23, "Break risk")])
def test_each_class_is_read_from_the_steps_past_the_peak(past, status):
    tr, _ = walk_to(past)
    assert tr.past_peak == pytest.approx(past, abs=1.0)
    assert tr.status == status


@pytest.mark.parametrize("z0", [1000.0, 25.0, -17021.0])
def test_lowering_is_z_decreasing_whatever_the_sign_of_z(z0):
    tr, states = walk_to(23, z0=z0)
    order = [w for i, w in enumerate(states) if i == 0 or w != states[i - 1]]
    assert order == ["No contact", "Contact", "Low", "Medium", "High", "Break risk"]
    assert tr.past_peak > 0


def test_raising_the_probe_after_the_peak_is_not_force():
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=6.0, end_s=16.0)
    z = np.array([1000.0 if tt < 7.5 else 1000.0 + 5 * (tt - 7.5) for tt in t])
    tr, _ = run(t, s, z)
    assert tr.past_peak < 0 and tr.status == "Contact"
    assert tr.position > ts.MEDIUM_MAX                   # the shade alone said High


def test_held_z_holds_the_class_while_the_shade_drifts():
    """Z is the measure: with the probe held past the peak, the class stays
    where the travel put it, whatever the shade does next."""
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=6.0, end_s=20.0)
    z = np.array([1000.0 - 3.0 * min(max(0.0, tt - 1.0), 9.0) for tt in t])
    tr, states = run(t, s, z)
    assert tr.status == "Medium" and states[-60:] == ["Medium"] * 60
    assert tr.position > ts.MEDIUM_MAX                   # the shade alone said High


def test_without_z_the_shade_position_still_classifies():
    """Fallback (a trial rebuilt with no Z, a probe that is not wired): the
    shade position's thirds, exactly as before."""
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=6.0, end_s=16.0)
    _, states = run(t, s)
    order = [w for i, w in enumerate(states) if i == 0 or w != states[i - 1]]
    assert order == ["No contact", "Contact", "Low", "Medium", "High"]


def test_the_shade_position_is_still_computed_with_z():
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=4.0, end_s=14.0)
    tr, _ = run(t, s, lowered(t))
    assert tr.position == pytest.approx(1.0, abs=0.08)


def test_the_snapshot_and_columns_carry_the_steps_past_the_peak():
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=30.0, end_s=40.0)
    z = lowered(t)
    tr = ts.ShadeTracker()
    for tt, ss, zz in zip(t, s, z):
        tr.update(float(tt), float(ss), float(zz))
        if tr.past_peak is not None and tr.past_peak >= 12:
            snap = tr.mark()
            break
    assert snap["status"] == "Medium" and snap["past_peak"] == pytest.approx(12, abs=1)
    assert snap["peak_z"] == tr.peak_z
    cols = ts.columns(tr)
    assert cols["force_class"] == "Medium"
    assert cols["force_past_peak_steps"] == snap["past_peak"]
    assert cols["shade_peak_z"] == snap["peak_z"]
    assert cols["force_position"] == snap["position"]   # kept, for history


def test_columns_have_no_past_peak_without_z_or_contact():
    t, s = synth(contact_s=3.0, rise_s=3.0, fall_s=6.0, end_s=16.0)
    tr, _ = run(t, s)
    tr.mark()
    cols = ts.columns(tr)
    assert cols["force_past_peak_steps"] is None and cols["shade_peak_z"] is None
    assert cols["force_class"] == "High"                 # the shade fallback
    tr = ts.ShadeTracker()
    for i in range(30):
        tr.update(i / 15, 150.0, 1000.0)
    tr.mark()
    cols = ts.columns(tr)
    assert cols["force_past_peak_steps"] is None and cols["force_class"] is None


def _trial_46():
    with open(TRIAL_46, newline="") as handle:
        return [(float(r["t_s"]), float(r["shade"]), float(r["z"]))
                for r in csv.DictReader(handle)]


def test_trial_46_reads_break_risk_where_the_shade_said_medium():
    """The break (2026-10-12, Tip7): the real telemetry excerpt, `t_s` since
    Arm, `transfer_map.shade` and `stepper_probe.z`. The smoothed shade
    peaked at z=-17057; by z=-17080 (smoothed shade 168) the probe was 23
    steps past it and the tip broke. The shade read 0.57, "Medium"."""
    tr = ts.ShadeTracker()
    for t, shade, z in _trial_46():
        tr.update(t, shade, z)
    assert tr.peak_z == -17057.0
    assert tr.shade == 168.0 and tr._z[-1] == -17080.0
    assert tr.past_peak == 23.0
    assert tr.status == "Break risk"
    # The old estimator, on the same frames: Medium.
    assert ts.classify(tr.position) == "Medium"
    assert tr.position == pytest.approx(0.57, abs=0.01)


def test_trial_46_was_break_risk_from_21_steps_past_the_peak():
    tr = ts.ShadeTracker()
    first = None
    for t, shade, z in _trial_46():
        tr.update(t, shade, z)
        if first is None and tr.status == "Break risk":
            first = (z, tr.past_peak)
    assert first == (-17078.0, 21.0)
