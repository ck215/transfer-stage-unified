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
