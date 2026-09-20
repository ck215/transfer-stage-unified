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

import io
import re
import tokenize
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"


def _code_lines(source):
    """The source with comments and string literals blanked out.

    Needed because several of these invariants are *about* names that the
    surrounding comments have to mention in order to explain why they are
    banned. Matching prose would make the guard fire on its own explanation.
    Falls back to the raw text if the file will not tokenize.
    """
    lines = source.splitlines()
    try:
        blanked = list(lines)
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                for n in range(tok.start[0], tok.end[0] + 1):
                    blanked[n - 1] = ""
        return blanked
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return lines


def _scan(pattern, root, exclude=(), code_only=True):
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
        source = path.read_text()
        raw = source.splitlines()
        haystack = _code_lines(source) if code_only else raw
        for lineno, line in enumerate(haystack, 1):
            if regex.search(line):
                hits.append((str(rel), lineno, raw[lineno - 1].strip()))
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

    Read by **introspecting the built models**, which is what this docstring
    always claimed and what a regex over the source only approximated. Schema
    v2 (S10) moved commands from dict literals (`"command": "toggle_auton"`)
    to builder keyword arguments, and the old pattern silently matched
    nothing — caught by the vacuity guard below, which is the entire reason
    that guard exists.
    """
    from unittest.mock import patch
    from model import schema as sch
    from model.probes import BaseProbe, StepperProbe, DCProbe, ChuckPositioner
    from model.redpercent_system import RedPercentSystem
    from model.rotator_system import RotatorSystem
    from model.temperature_system import TemperatureSystem

    commands = set()
    with patch('model.probes.serial'), patch('controller.serial.serial'), \
         patch('model.probes.ErrorPopupManager'), \
         patch('controller.gamepad.ControllerPoller'):
        for cls in (StepperProbe, DCProbe, ChuckPositioner, RedPercentSystem,
                    RotatorSystem, TemperatureSystem):
            if issubclass(cls, BaseProbe):
                model = cls("SIM", "None")
            elif cls is TemperatureSystem:
                model = cls(port=None)
            else:
                model = cls()
            for element in sch.elements(model.ui_schema):
                for key in ("command", "options_command", "data_command",
                            "source_command"):
                    name = element.get(key)
                    if name:
                        commands.add(name)
            teardown = getattr(model, "teardown", None)
            if callable(teardown):
                try:
                    teardown()
                except Exception:
                    pass
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

# HELD since S2 (2026-09-19). The xfail is retired — strict xfail reported the
# fix as an XPASS failure, which is what forced this marker to be removed and
# the stage to be closed rather than the invariant quietly passing unnoticed.
#
# What was here: bootstrap populated a bare dict named `active_models` and
# handed it over (the manager was a passive container, not the thing that owned
# registration), and the PySide view reached into the manager's dict to `del` a
# model on tab close. S2 replaced both with register()/release(), and renamed
# bootstrap's local build buffer to `built_models` — it was named after the
# manager's field, which is what made it read as a violation.
I_1_5_PATTERN = r"active_models\["
I_1_5_OWNER = "model/system_manager.py"


def _i_1_5_hits():
    return _scan(I_1_5_PATTERN, SRC, exclude=(I_1_5_OWNER,))


def test_i_1_5_active_models_written_only_by_system_manager():
    hits = _i_1_5_hits()
    assert not hits, _report("I-1.5", hits)


# ---------------------------------------------------------------------------
# I-2.3 — No code outside the transport touches `.ser`.  Fixed in S3 (RC-2).
# ---------------------------------------------------------------------------

# HELD since S3 (2026-09-19). What was here: 13 direct `.ser` touches outside
# the transport — 3 in probes.py, 10 in temperature_system.py — each one a
# write or read that bypassed the transport's lock and its error handling
# entirely. They now go through write_command()/read_line(), which raise
# TransportError instead of swallowing failures.
# Matches `.ser.` and also `"ser"`/`'ser'` as an attribute name, which is how
# a getattr(transport, 'ser', None) check slipped past this guard until S8.
I_2_3_PATTERN = r"\.ser\.|getattr\([^)]*['\"]ser['\"]"
I_2_3_OWNER = "controller/serial.py"


def _i_2_3_hits():
    return _scan(I_2_3_PATTERN, SRC, exclude=(I_2_3_OWNER,))


def test_i_2_3_serial_handle_confined_to_transport():
    hits = _i_2_3_hits()
    assert not hits, _report("I-2.3", hits)


# ---------------------------------------------------------------------------
# I-4.1 — No view drives a control loop.  Fixed in S5 (RC-4/RC-13).
# ---------------------------------------------------------------------------

# HELD since S5 (2026-09-19). What was here: 32 view-owned loop calls — 15 in
# PySide, 13 in Tk, 4 in the web adapter. Each frontend ran its own gamepad
# pump and its own position/status polling, at rates that disagreed (Tk 50 ms
# vs PySide 20 ms for manual input), and PySide sampled *again* from its render
# tick on top of its dedicated timers. The web adapter sampled inline in
# /api/state, so the rate was whatever the browser polled at and a stalled read
# blocked the HTTP handler. The loops belong to the models now.
I_4_1_PATTERN = r"\b(" + "|".join(VIEW_FORBIDDEN_CALLS) + r")\b"


def _i_4_1_hits():
    return _scan(I_4_1_PATTERN, SRC / "views")


def test_i_4_1_views_do_not_drive_control_loops():
    hits = _i_4_1_hits()
    assert not hits, _report("I-4.1", hits)


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
    # This invariant is *about* string literals, so it is the one scan that
    # must keep them. It matches quoted names only, which was measured at zero
    # false positives against generic words like "stop" and "home".
    return _scan(pattern, SRC / "views", code_only=False)


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


# ---------------------------------------------------------------------------
# S1 — owner decisions D-9 and D-11, closed by deletion.
#
# These pass now. They are here rather than in a feature test file because
# what they assert is an *absence*, and an absence is only durable if
# something keeps checking for it. Each one is the closure evidence for the
# findings named beside it.
# ---------------------------------------------------------------------------

# Names that only exist to reconnect a serial port at runtime. D-11 purges the
# feature, so every one of these must stay absent from the source tree.
# reboot_model had an exemption here through S1, because its four lifecycle
# tests were coverage S2 needed. S2 replaced it with reconfigure(), so the
# exemption is gone and the name is banned outright.
D11_NAMES = (
    r"\breconnect_serial\b",
    r"\breboot_model\b",
    r"Serial Reconnect",
    r'"command"\s*:\s*"reconnect"',
)


def test_d11_no_runtime_serial_reconnect():
    """SERIAL-14, PYSIDE-13, DC-14, STEPPER-12 — D-11 purge stays purged."""
    offenders = []
    for pattern in D11_NAMES:
        offenders += _scan(pattern, SRC)
    assert not offenders, _report("D-11 (runtime serial reconnect)", offenders)


def test_d11_serial_port_is_readonly_in_every_schema():
    """DC-14, STEPPER-12 — an editable port that nothing reads is a dead control."""
    hits = _scan(
        r'"type"\s*:\s*"entry".*"model_attr"\s*:\s*"serial_port"', SRC / "model"
    )
    assert not hits, _report("D-11 (serial_port must be readonly)", hits)


def test_d9_macos_defaults_to_tkinter():
    """MANAGER-14 — D-9: Tkinter is the macOS default until things stabilize."""
    import app

    assert app.select_view(None, "darwin") == "legacy"
    # An explicit flag still wins on macOS.
    assert app.select_view("web", "darwin") == "web"
    assert app.select_view("pyside", "darwin") == "pyside"
    # Other platforms are unchanged: PySide when importable, Web otherwise.
    assert app.select_view(None, "linux", pyside_available=True) == "pyside"
    assert app.select_view(None, "linux", pyside_available=False) == "web"


def test_manager14_launcher_rejects_unknown_flags():
    """MANAGER-14 — a typo must error, not fall through to a platform default.

    Under parse_known_args, `--pyside6` was silently dropped and the macOS
    default started a hardware-capable web server instead.
    """
    import app

    # The word appears in the comment explaining why it is gone; match a call.
    assert not re.search(r"parse_known_args\s*\(", (SRC / "app.py").read_text())
    assert not hasattr(app, "launch_web"), (
        "launch_web was only reachable from the unreachable 'unknown view' "
        "branch and ignored --port/--no-browser"
    )


# ---------------------------------------------------------------------------
# S5 / RC-13 — SDL has one owner.
#
# root-causes.md names `_ensure_pygame_video()` as an anti-fix: it re-inits SDL
# after whichever poller happened to tear it down, which made the symptom
# survivable and so removed the pressure to fix the ownership. Its own
# docstring described it as a recurring patch. This is the guard that stops it
# coming back, in that form or another.
# ---------------------------------------------------------------------------

SDL_OWNER = "controller/input_service.py"


def test_pygame_quit_is_called_only_by_the_input_service():
    """Process-wide teardown belongs to one place, reached only at exit."""
    hits = _scan(r"pygame\.quit\(\)|pygame\.joystick\.quit\(\)", SRC,
                 exclude=(SDL_OWNER,))
    assert not hits, _report("RC-13 (pygame.quit outside the input service)", hits)


def test_the_ensure_pygame_video_anti_fix_has_not_returned():
    hits = _scan(r"def _ensure_pygame_video|_ensure_pygame_video\(\)", SRC)
    assert not hits, _report("RC-13 (anti-fix _ensure_pygame_video)", hits)


def test_no_module_level_poller_refcount():
    """The refcount could not tell "nobody is using SDL" from "nobody happens
    to hold a poller object right now"."""
    hits = _scan(r"_active_poller_count", SRC)
    assert not hits, _report("RC-13 (bind-counting refcount)", hits)


# ---------------------------------------------------------------------------
# I-9.1 / I-9.2 — One composition root, and cross-model wiring by event.
# S12 (RC-9).
#
# The behavioural halves are `tests/core/test_composition_root.py`. These are
# the structural ones: the wiring cannot come back as a fourth hand-written
# copy, and no launcher can grow its own device enumeration again.
#
# `root-causes.md` names "add Red Percent linking to a fifth call site" as an
# anti-fix in so many words. This is the guard that makes adding one fail.
# ---------------------------------------------------------------------------

REGISTRY_OWNER = "model/redpercent_system.py"


def test_i_9_1_only_the_dependent_model_writes_available_probes():
    """The assignment was pasted into Tk's launcher, PySide's launcher and
    the web adapter, and nothing anywhere refreshed it afterwards."""
    hits = _scan(r"available_probes\s*=[^=]|\.available_probes", SRC,
                 exclude=(REGISTRY_OWNER,))
    assert not hits, _report("I-9.1 (available_probes written outside the model)", hits)


def test_i_9_2_controller_enumeration_has_one_implementation():
    """Three enumerations, and the web one shelled out to a literal `python3`
    and then invented controllers when it found none (MANAGER-18, WEB-15)."""
    hits = _scan(r"pygame\.joystick\.get_count|joystick\.Joystick\(", SRC,
                 exclude=(SDL_OWNER,))
    assert not hits, _report("I-9.2 (controller enumeration outside the input service)", hits)


def test_i_9_2_no_fabricated_controllers():
    """A name no enumeration produced, offered in one frontend's wizard only.

    Quoted literals, so the comments that have to name the defect in order to
    explain it do not trip their own guard.
    """
    hits = _scan(r"""["']Virtual Controller""", SRC, code_only=False)
    assert not hits, _report("I-9.2 (fabricated controller names)", hits)


def test_i_9_3_the_disabled_in_setup_flag_is_gone():
    """RC-9 item 3. It was computed from a key normalization had already
    dropped, so it was False for every model ever built (WEB-4)."""
    hits = _scan(r"_disabled_in_setup", SRC)
    assert not hits, _report("RC-9 item 3 (_disabled_in_setup)", hits)


# ---------------------------------------------------------------------------
# I-7.2 — The same schema renders in all three views.  S10 (RC-7).
#
# Stated in root-causes.md as "the same schema renders the same set of
# controls in Tk, PySide, and Web, checked by a golden-structure test per
# renderer", and named as an S10 exit invariant in plan.md. **It had no
# harness.** `tests/ui/test_schema_v2.py` carried an `I-7.2` heading over its
# writability checks, which are I-7.3 — so the invariant read as covered
# while the thing it actually asserts was never tested.
#
# What that cost, concretely: two element types were declared in
# `schema.ELEMENT_TYPES`, resolved by the conformance test, rendered by two
# renderers, and quietly meant something else in the third. PySide's
# `region_select` wrote `model.focus_area` itself instead of running the
# declared command, and its `plot` opened a CSV file dialog without ever
# reading `data_command`. Both were found by hand.
#
# Behavioural parity per composite is `tests/ui/test_composites.py`. This is
# the cheaper half: every declared type is handled by every renderer, so a
# type added to the schema cannot be rendered by two views and silently
# fall through in the third.
# ---------------------------------------------------------------------------

WEB_RENDERER = SRC / "views/web/static/js/app.js"


def _types_handled_by(path, pattern):
    return set(re.findall(pattern, path.read_text()))


def _python_renderer_types(relpath):
    """Element types the renderer's `el_type` switch names."""
    source = (SRC / relpath).read_text()
    # `el_type == "x"` and `el_type in ["x", "y"]` both count as handling.
    handled = set(re.findall(r'el_type\s*==\s*["\'](\w+)["\']', source))
    for group in re.findall(r'el_type\s+in\s*[\[\(]([^\]\)]+)[\]\)]', source):
        handled.update(re.findall(r'["\'](\w+)["\']', group))
    return handled


RENDERERS = {
    "pyside": lambda: _python_renderer_types("views/pyside/view.py"),
    "tkinter": lambda: _python_renderer_types("views/tkinter/view.py"),
    "web": lambda: _types_handled_by(
        WEB_RENDERER, r"el\.type\s*===\s*['\"](\w+)['\"]"),
}


def test_i_7_2_harness_is_not_vacuous():
    """Each scan must find a renderer switch before its silence means
    anything — the same trap `test_harness_is_not_vacuous` exists for."""
    from model import schema as sch

    assert sch.ELEMENT_TYPES, "schema declares no element types"
    for name, scan in RENDERERS.items():
        assert scan(), f"found no element-type switch in the {name} renderer"


def test_i_7_2_every_renderer_handles_every_declared_element_type():
    from model import schema as sch

    missing = {}
    for name, scan in RENDERERS.items():
        gap = set(sch.ELEMENT_TYPES) - scan()
        if gap:
            missing[name] = sorted(gap)
    assert not missing, (
        "I-7.2: element types declared in schema.ELEMENT_TYPES that a "
        f"renderer does not handle: {missing}. A type no renderer branch "
        "names is a control that silently does not appear in that frontend."
    )


def test_i_7_2_no_renderer_invents_an_element_type():
    """The other direction: a branch for a type the schema cannot emit is
    dead code, which is what PYSIDE-11's `continue`-first `file_picker` arm
    was for as long as anyone can date it."""
    from model import schema as sch

    invented = {}
    for name, scan in RENDERERS.items():
        extra = scan() - set(sch.ELEMENT_TYPES)
        if extra:
            invented[name] = sorted(extra)
    assert not invented, (
        f"I-7.2: renderer branches for undeclared element types: {invented}")


# ---------------------------------------------------------------------------
# I-8.1 / I-8.2 / I-8.3 — the result channel and the event bus.  S11 (RC-8).
#
# Two of the three are behavioural rather than structural, so they are
# asserted against the real bus below rather than by grepping. The structural
# half — that no view opens a modal on its own command path any more — is a
# grep, because that is the shape that hung the Qt suite for three sessions
# and a comment saying "do not do this" does not stop the next one.
# ---------------------------------------------------------------------------

def test_i_8_1_a_refused_command_is_distinguishable_from_a_successful_one():
    """I-8.1. Before S11 both returned `None` and every view said "executed"."""
    from model.base import SchemaCommands
    from model.params import Param
    from results import Ok, Refused

    class Model(SchemaCommands):
        PARAMS = {"speed": Param("speed", "float", default=1.0,
                                 minimum=0.0, maximum=10.0, label="Speed")}

        def __init__(self):
            self.speed = 1.0
            self.ran = 0

        def go(self):
            self.ran += 1

    model = Model()

    good = model.execute_command("go", inputs={"speed": "5"})
    assert isinstance(good, Ok) and bool(good) is True
    assert model.ran == 1

    bad = model.execute_command("go", inputs={"speed": "not-a-number"})
    assert isinstance(bad, Refused)
    assert bool(bad) is False, (
        "I-8.1: a refusal must not be truthy — every `if execute_command(...)` "
        "call site would read it as success")
    assert bad.reason, "a refusal the operator cannot read is not a refusal"
    assert model.ran == 1, "the command body ran despite the refusal"


def test_i_8_1_no_view_reports_a_command_outcome_from_its_own_modal():
    """The structural half: a view's command path must not open its own
    error dialog. `execute_command` publishes to the bus, and exactly one
    subscriber decides whether that warrants a modal — which is what stops
    a dialog appearing with nobody able to click it."""
    offenders = []
    for rel, pattern in (
        ("views/pyside/view.py", re.compile(r"QMessageBox\.critical")),
        ("views/tkinter/view.py", re.compile(r"messagebox\.showerror")),
    ):
        path = SRC / rel
        raw = path.read_text().splitlines()
        # Detection runs on the blanked source so the comments explaining
        # why these calls were removed do not trip their own guard. The
        # *window* is read from the raw source instead: `_code_lines` blanks
        # a whole line containing any string literal, and the guard clause
        # here — `if event.severity == "error" and event.requires_ack:` —
        # is exactly such a line.
        lines = _code_lines(path.read_text())
        for n, line in enumerate(lines, 1):
            if not pattern.search(line):
                continue
            # The one legitimate site per view is the bus subscriber's
            # acknowledged-error branch, which lives in the popup manager.
            window = "\n".join(raw[max(0, n - 25):n])
            if "requires_ack" in window:
                continue
            offenders.append(f"{rel}:{n}")

    assert not offenders, (
        "I-8.1: a view opens an error modal outside the bus subscriber: "
        f"{offenders}. Route it through `execute_command`, which publishes a "
        "`Failed` the subscriber renders once.")


def test_i_8_2_a_fault_persisting_for_a_minute_produces_one_event():
    """I-8.2: one event and a visible state, not twelve popups.

    Modelled on the real case — a condition re-reported every 5 s while it
    lasts. The old text-keyed limit *dropped* the repeats, so the log said
    the fault happened once and gave no sign it had lasted a minute. The
    repeat folds into the original now and carries a count, so both the
    "one event" half and the duration survive.
    """
    from error_routing import EventBus

    now = [1000.0]
    bus = EventBus(clock=lambda: now[0])
    seen = []
    bus.subscribe(seen.append)

    for _ in range(12):
        bus.publish("error", "stepper", "Motor Fault", "driver reports fault")
        now[0] += 5.0

    events = bus.since(0)
    assert len(events) == 1, f"I-8.2: expected one event, got {events}"
    assert len(seen) == 1, "I-8.2: one notification, not twelve"
    assert events[0].count == 12
    assert events[0].last_seen - events[0].first_seen == pytest.approx(55.0), (
        "the event must record how long the fault lasted; a dropped repeat "
        "loses that")


def test_i_8_2_a_fault_that_recurs_after_the_window_is_a_new_event():
    """The limit must not hide a fault that comes back later."""
    from error_routing import EventBus

    now = [1000.0]
    bus = EventBus(clock=lambda: now[0])
    bus.publish("error", "stepper", "Motor Fault", "driver reports fault")
    now[0] += 3600.0
    bus.publish("error", "stepper", "Motor Fault", "driver reports fault")
    assert len(bus.since(0)) == 2


def test_i_8_3_every_launcher_installs_the_same_hooks():
    """I-8.3. Each view used to install its own `sys.excepthook` and nothing
    else; `threading.excepthook` existed in the web launcher only, and Tk's
    `report_callback_exception` nowhere. One installer, called by all three.
    """
    source = _code_lines((SRC / "app.py").read_text())
    text = "\n".join(source)

    assert text.count("install_exception_hooks(") >= 3, (
        "I-8.3: all three launchers must call `install_exception_hooks`; "
        f"found {text.count('install_exception_hooks(')} call(s)")

    for banned, why in (
        ("sys.excepthook =", "assign it inside install_exception_hooks"),
        ("threading.excepthook =", "assign it inside install_exception_hooks"),
    ):
        assert banned not in text, (
            f"I-8.3: app.py assigns `{banned}` directly — {why}, so the three "
            "launchers cannot drift apart again")


def test_i_8_3_the_installer_covers_all_three_hooks():
    from error_routing import install_exception_hooks

    # Raw source, not `_code_lines`: two of the three assignments sit on a
    # line that also carries a string literal, and the helper blanks the
    # whole line when it does.
    body = (SRC / "error_routing.py").read_text()
    start = body.index("def install_exception_hooks(")
    installer = body[start:]
    for hook in ("sys.excepthook", "threading.excepthook",
                 "report_callback_exception"):
        assert hook in installer, (
            f"I-8.3: install_exception_hooks misses {hook}")
