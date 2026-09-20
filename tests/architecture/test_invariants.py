"""Structural invariants — the S0 harness.

These are the grep-style invariants stated in
``docs/architecture/root-causes.md``. They are deliberately crude: each one
asserts that a *name* appears only where the intended command structure
allows it. That makes the redundancy this repair exists to remove visible as
a number, and makes its regression cheap to detect.

Every invariant currently fails, because none of the repair stages have
landed. Each is therefore ``xfail(strict=True)`` and names the stage that
fixes it. Strict is deliberate and matches the known-bad quarantine policy:
when the stage lands and the invariant starts holding, pytest reports XPASS
**as a failure**, which forces someone to delete the marker here and mark the
stage done rather than letting a satisfied invariant sit unnoticed.

Alongside each ``xfail``ed invariant is a ``_no_new_violations`` guard that
runs *now* and pins the violation count per file. Without it this harness
would do nothing at all until S2 — the guard is what stops the leak spreading
to a fourth view or a third model while the earlier stages are in flight.

Policy and per-stage gates: ``docs/implementation/testing.md``.
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"


def _scan(pattern, root, exclude=()):
    """Every line under `root` matching `pattern`, as (relpath, lineno, text).

    `exclude` holds paths (relative to src/) where the name is legitimate —
    the one module the invariant says owns it.
    """
    regex = re.compile(pattern)
    excluded = {Path(e) for e in exclude}
    hits = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(SRC)
        if rel in excluded:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if regex.search(line):
                hits.append((str(rel), lineno, line.strip()))
    return hits


def _counts(hits):
    counts = {}
    for rel, _lineno, _text in hits:
        counts[rel] = counts.get(rel, 0) + 1
    return counts


def _report(name, hits):
    lines = [f"{name}: {len(hits)} violating lines in {len(_counts(hits))} files"]
    lines += [f"  {rel}:{lineno}: {text}" for rel, lineno, text in hits]
    return "\n".join(lines)


def _assert_no_new(name, hits, baseline, stage):
    """The violation set may shrink, never grow, before `stage` lands."""
    current = _counts(hits)
    grown = {
        rel: (baseline.get(rel, 0), count)
        for rel, count in current.items()
        if count > baseline.get(rel, 0)
    }
    assert not grown, (
        f"{name} spread to new code. This invariant is a known violation "
        f"owned by {stage}, but it must not get worse in the meantime.\n"
        + "\n".join(
            f"  {rel}: was {was}, now {now}"
            for rel, (was, now) in sorted(grown.items())
        )
        + "\n\nEither move the new code behind the owning module, or — if the "
        "growth is a deliberate step of the repair — update the baseline in "
        "this file in the same commit.\n\n"
        + _report(name, hits)
    )


# The six device names. These are hardcoded rather than imported because
# there is no single authority to import them from — build_models() dispatches
# on them in an if/elif chain (app_bootstrap.py:139-164), app_bootstrap's
# DEVICE_MAP knows only four of them, and pyside/view.py:786-789 keeps its own
# copy of the list. That duplication *is* RC-7, and S10 is where it collapses
# into one schema-driven source. Until then, this list is the fourth copy, and
# it is deliberately the one that fails loudly when the others drift.
DEVICE_NAMES = (
    "Stepper Probe",
    "DC Probe",
    "Chuck Positioner",
    "Temperature Controller",
    "SMC100 Rotator",
    "Red Percent Window",
)

# Loop-driving calls that belong to a model, never to a view (RC-4/RC-13).
VIEW_FORBIDDEN_CALLS = (
    "read_position",
    "poll_status",
    "send_manual_mode_command",
    "start_polling",
    "get_mapped_state",
)


def _schema_command_names():
    """Every `command` value declared in a model's ui_schema.

    Derived rather than hardcoded so the invariant cannot drift as schemas
    change: a command added to a model is a command views are forbidden to
    name, from the moment it is added.
    """
    commands = set()
    for path in sorted((SRC / "model").glob("*.py")):
        commands |= set(
            re.findall(r'"command"\s*:\s*"([a-zA-Z_]+)"', path.read_text())
        )
    return commands


# ---------------------------------------------------------------------------
# Vacuity guard
#
# Every test below passes when it finds nothing. A wrong path or a silently
# empty pattern would therefore make the whole harness green — and, under
# strict xfail, make four XPASS failures look like four stages landing. Prove
# the scan is actually looking at the source tree first.
# ---------------------------------------------------------------------------


def test_harness_is_not_vacuous():
    assert SRC.is_dir(), f"source tree not found at {SRC}"
    for expected in (
        "model/system_manager.py",
        "controller/serial.py",
        "views/pyside/view.py",
        "views/tkinter/view.py",
        "views/web/web_adapter.py",
        "app_bootstrap.py",
    ):
        assert (SRC / expected).is_file(), f"expected source file missing: {expected}"
    assert _schema_command_names(), (
        "no ui_schema `command` values found in src/model/ — the I-7.1 command "
        "pattern would match nothing and pass vacuously"
    )


# ---------------------------------------------------------------------------
# I-1.5 — Only SystemManager writes to active_models.  Fixed in S2 (RC-1).
# ---------------------------------------------------------------------------

I_1_5_PATTERN = r"active_models\["
I_1_5_OWNER = "model/system_manager.py"
I_1_5_BASELINE = {
    # build_models() populates a bare dict and hands it over — the manager is
    # a passive container rather than the thing that owns registration.
    "app_bootstrap.py": 6,
    # The view reaches into the manager's dict to delete a model on tab close
    # (pyside/view.py:846). S2 replaces this with release(); S6 makes closing
    # a tab mean hide, so nothing is deleted here at all.
    "views/pyside/view.py": 1,
}


def _i_1_5_hits():
    return _scan(I_1_5_PATTERN, SRC, exclude=(I_1_5_OWNER,))


@pytest.mark.xfail(
    strict=True,
    reason="[invariant I-1.5, owned by S2] bootstrap builds the dict and the "
    "PySide view deletes from it; SystemManager is not yet the only writer",
)
def test_i_1_5_active_models_written_only_by_system_manager():
    hits = _i_1_5_hits()
    assert not hits, _report("I-1.5", hits)


def test_i_1_5_no_new_violations():
    _assert_no_new("I-1.5", _i_1_5_hits(), I_1_5_BASELINE, "S2")


# ---------------------------------------------------------------------------
# I-2.3 — No code outside the transport touches `.ser`.  Fixed in S3 (RC-2).
# ---------------------------------------------------------------------------

I_2_3_PATTERN = r"\.ser\."
I_2_3_OWNER = "controller/serial.py"
I_2_3_BASELINE = {
    # Raw writes that bypass the transport's command discipline entirely —
    # no ACK, no connection-state check, no error routing.
    "model/probes.py": 3,
    "model/temperature_system.py": 10,
}


def _i_2_3_hits():
    return _scan(I_2_3_PATTERN, SRC, exclude=(I_2_3_OWNER,))


@pytest.mark.xfail(
    strict=True,
    reason="[invariant I-2.3, owned by S3] probes.py and temperature_system.py "
    "still write to the serial handle directly, bypassing the transport",
)
def test_i_2_3_serial_handle_confined_to_transport():
    hits = _i_2_3_hits()
    assert not hits, _report("I-2.3", hits)


def test_i_2_3_no_new_violations():
    _assert_no_new("I-2.3", _i_2_3_hits(), I_2_3_BASELINE, "S3")


# ---------------------------------------------------------------------------
# I-4.1 — No view drives a control loop.  Fixed in S5 (RC-4/RC-13).
# ---------------------------------------------------------------------------

I_4_1_PATTERN = r"\b(" + "|".join(VIEW_FORBIDDEN_CALLS) + r")\b"
I_4_1_BASELINE = {
    # Each frontend runs its own copy of the polling and manual-mode loop,
    # which is why the three disagree on rate and on neutral-on-exit.
    "views/web/web_adapter.py": 4,
    "views/pyside/view.py": 15,
    "views/tkinter/view.py": 13,
}


def _i_4_1_hits():
    return _scan(I_4_1_PATTERN, SRC / "views")


@pytest.mark.xfail(
    strict=True,
    reason="[invariant I-4.1, owned by S5] all three frontends still own "
    "polling and manual-mode loops that belong to the model",
)
def test_i_4_1_views_do_not_drive_control_loops():
    hits = _i_4_1_hits()
    assert not hits, _report("I-4.1", hits)


def test_i_4_1_no_new_violations():
    _assert_no_new("I-4.1", _i_4_1_hits(), I_4_1_BASELINE, "S5")


# ---------------------------------------------------------------------------
# I-7.1 — No device or command name literal in a view.  Fixed in S10 (RC-7).
#
# The schema-driven renderer's own type switch (button/dropdown/entry) is not
# covered by this pattern: it matches device names and declared `command`
# values only, never element types.
# ---------------------------------------------------------------------------

I_7_1_BASELINE = {
    # Special-cased device and command names — every one of these is a branch
    # the schema was supposed to make unnecessary.
    "views/web/web_adapter.py": 4,
    "views/pyside/view.py": 19,
    "views/tkinter/view.py": 3,
}


def _i_7_1_hits():
    names = sorted(set(DEVICE_NAMES) | _schema_command_names())
    pattern = r"[\"'](" + "|".join(re.escape(n) for n in names) + r")[\"']"
    return _scan(pattern, SRC / "views")


@pytest.mark.xfail(
    strict=True,
    reason="[invariant I-7.1, owned by S10] views still hardcode the device "
    "list and branch on cmd_name instead of rendering the schema",
)
def test_i_7_1_views_name_no_device_or_command():
    hits = _i_7_1_hits()
    assert not hits, _report("I-7.1", hits)


def test_i_7_1_no_new_violations():
    _assert_no_new("I-7.1", _i_7_1_hits(), I_7_1_BASELINE, "S10")
