"""One look for all three views. Tk and Qt read these values directly; the
Web view serves `css_variables()`. Nothing else in `views/` names a colour or
a font size."""

#: Text face (Bench sheet, tiered, 2026-09-25). The Web view self-hosts it;
#: Tk and Qt use it when installed on the station PC and fall back to the
#: platform sans otherwise (E4: install it there).
FONT_FAMILY = "Public Sans"
FONT_FALLBACK = "Helvetica"
FONT_SIZE = 12            # base, in points; launch with --font-size to change
#: Numerals and the two page headlines: a wide, semi-bold grotesk with
#: tabular figures. Tk/Qt: a static "Archivo SemiExpanded" instance.
NUMERAL_FAMILY = "Archivo"
NUMERAL_STRETCH = 118     # percent, where the toolkit supports it
NUMERAL_WEIGHT = 600

from palette import (BACKGROUND, SURFACE, TEXT, MUTED,  # noqa: F401
                             SIGNAL, TRACE)                      # one palette

#: role -> (background, foreground)
#:
#: Owner ruling 2026-09-22: one red. `danger` is the signal colour and it is
#: the ONLY role that carries it, so the eye has exactly one thing to find in
#: a hurry. Bench sheet (2026-09-25): `go`, the one you press, is ink-filled
#: with sheet-coloured text; `neutral` is the panel tone; `info` is the sheet
#: itself. `warning` is NO LONGER the trace colour - trace is reserved for
#: changing numbers - a warning is a panel-toned control with an ink hollow
#: mark beside it (SEVERITY_MARK_HOLLOW).


def mix(colour, other, amount):
    """`amount` of `other` blended into `colour`, 0..1. The ONE mixing helper:
    every derived shade in every view comes from here, so a shade can never
    be one sign error away from its neighbour (UI audit 2026-09-24, DS-12)."""
    a = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


#: The quiet ladder, derived from the panel lit by ink rather than named as
#: literals (DS-4): `info` barely leaves the panel, `go` is the one you press.
ROLES = {
    "info": (mix(BACKGROUND, SURFACE, 0.5), TEXT),
    "neutral": (SURFACE, TEXT),
    "go": (TEXT, BACKGROUND),
    "danger": (SIGNAL, "#ffffff"),
    "warning": (SURFACE, TEXT),
}
#: Disabled: the sheet with ink at 45 % (5.9:1 on the sheet) and, in the
#: views, a dashed muted edge so a disabled control is not a blank.
DISABLED = (BACKGROUND, mix(BACKGROUND, TEXT, 0.45))
SEVERITY_ROLE = {"error": "danger", "warning": "warning", "info": "info"}

#: Event text stays legible: severity is a MARK beside the line, not the ink
#: of the line. Signal on the window is 3.21:1 and on a panel 2.73:1, under
#: the 4.5:1 floor, so an error line drawn in red was the hardest line to
#: read (F14: DS-2, AUD-5, UXPM-4/10).
SEVERITY_INK = {"error": TEXT, "warning": TEXT, "info": MUTED}
SEVERITY_MARK = {"error": SIGNAL, "warning": TEXT, "info": MUTED}
#: A warning's mark is a HOLLOW square (an error's is solid), so the two
#: differ in shape as well as colour and neither borrows the trace.
SEVERITY_MARK_HOLLOW = frozenset({"warning"})

#: The stop object's keyboard-focus ring is ink: a trace ring is what
#: "latched" looks like, so focus must never borrow it (F9: DS-1, UXPM-5).
STOP_FOCUS = TEXT

#: Hairlines and wells, once (DS-3, DS-8). RULE separates rows inside a
#: panel; RULE_STRONG separates panels; WELL is where typed text sits; LIFT
#: is a hovered control.
#: Bench sheet: a row rule is the panel tone on the sheet; the rule that
#: HEADS an entry is 2 px of ink (RULE_STRONG). Typed text sits in a
#: panel-toned well with a muted underline (INPUT_BORDER), no box.
RULE = SURFACE
RULE_STRONG = TEXT
RULE_STRONG_PX = 2
WELL = SURFACE
LIFT = mix(SURFACE, TEXT, 0.06)
INPUT_BORDER = MUTED

#: Spacing steps in pixels (DS-7). PAD/GAP/INSET below stay as the three
#: names the views already use; anything else is one of these, never a sum.
SPACE = (2, 4, 6, 8, 12, 16, 20, 24, 28, 32, 44)

#: Readings (E, 2026-09-25): a live number is never the size of its caption.
#: `focal` is the opened model's rail readouts, `primary` a single-value
#: model's reading, `compact` a closed probe's X/Y/Z, `secondary` a change.
READING_SIZES = {"focal": 52, "primary": 40, "compact": 32, "secondary": 26}
CAPTION_SIZE = 13
#: The stop object: A's disc set in the rail. Always red, in every state;
#: latched it reads "Clear" and the ring thickens. Diameters in px at the
#: base font size; views scale them with `size()`.
STOP = {"diameter": 136, "ring": 3, "ring_latched": 6, "gap": 6,
        "diameter_narrow": 120}
#: The per-model stop is a small switch, not a second red disc.
SWITCH = {"track": (34, 20), "knob": 14, "on_fill": SIGNAL,
          "off_edge": MUTED, "knob_on": "#ffffff", "knob_off": MUTED}
RADIUS = {"control": 6, "input": 4, "well": 10, "switch": 10}
#: Tiers of prominence (owner ruling 2026-09-25). Tier 1 is always drawn;
#: tier 2 sits behind one disclosure per model; tier 3 behind a second one
#: inside it. A section names its own disclosure text; these are defaults.
TIER_LABELS = {2: "Configure", 3: "Diagnostics"}
#: Status by exception: a readonly whose value is one of these is a normal
#: state and is not drawn in tier 1 (it remains in the state for the API).
QUIET_VALUES = frozenset({"", "--", "Connected", "connected", "Idle", "idle",
                          "No", "no", "None", "none", "Not recording"})

#: The type scale (DS-5): one ratio, steps relative to FONT_SIZE. -1 is a
#: caption, 0 the base, 1 a readout or section title, 2 a panel name.
TYPE_RATIO = 1.2


#: Spacing scale, in pixels. Every view lays out with these three numbers.
PAD = 8        # around a panel or a section
GAP = 4        # between a label and its control, between rows
INSET = 12     # left indent of a section's body under its title


def set_font_size(points):
    global FONT_SIZE
    FONT_SIZE = max(8, min(28, int(points)))


def size(step):
    """Font size in points for a type-scale step (see TYPE_RATIO)."""
    return max(8, round(FONT_SIZE * TYPE_RATIO ** step))


def font(scale=1.0, bold=False):
    """(family, size, weight) - the Tk font tuple; Qt builds a QFont from it."""
    return (FONT_FAMILY, max(8, round(FONT_SIZE * scale)), "bold" if bold else "normal")


def colors(role):
    return ROLES.get(role, ROLES["neutral"])


def toggle_colors(element, is_on):
    """What a toggle looks like in each state, from what the state MEANS.
    An ON toggle is filled with its on_role; an OFF toggle is the same role
    outlined on the surface colour, so ON/OFF differ in every theme without
    relying on green-vs-red."""
    role = element.get("on_role" if is_on else "off_role", "neutral")
    background, foreground = colors(role)
    if is_on:
        return {"background": background, "foreground": foreground, "border": background}
    # OFF: outlined on the sheet. A danger toggle keeps its red outline (the
    # stop must read as the stop when off); every other role is outlined in
    # ink, because a panel-toned outline on the sheet would be invisible.
    border = SIGNAL if role == "danger" else TEXT
    return {"background": BACKGROUND, "foreground": TEXT, "border": border}


def css_variables():
    lines = [f"--font-family: {FONT_FAMILY}, {FONT_FALLBACK}, sans-serif;", f"--font-size: {FONT_SIZE}pt;",
             f"--numeral-family: {NUMERAL_FAMILY}, {FONT_FAMILY}, sans-serif;",
             f"--numeral-stretch: {NUMERAL_STRETCH}%;", f"--numeral-weight: {NUMERAL_WEIGHT};",
             f"--bg: {BACKGROUND};", f"--surface: {SURFACE};", f"--text: {TEXT};",
             f"--muted: {MUTED};", f"--signal: {SIGNAL};", f"--trace: {TRACE};",
             f"--pad: {PAD}px;", f"--gap: {GAP}px;", f"--inset: {INSET}px;"]
    for role, (bg, fg) in ROLES.items():
        lines += [f"--{role}-bg: {bg};", f"--{role}-fg: {fg};"]
    lines += [f"--disabled-bg: {DISABLED[0]};", f"--disabled-fg: {DISABLED[1]};",
              f"--stop-focus: {STOP_FOCUS};", f"--rule: {RULE};",
              f"--rule-strong: {RULE_STRONG};", f"--well: {WELL};", f"--lift: {LIFT};",
              f"--input-border: {INPUT_BORDER};"]
    for severity in ("error", "warning", "info"):
        lines += [f"--{severity}-ink: {SEVERITY_INK[severity]};",
                  f"--{severity}-mark: {SEVERITY_MARK[severity]};"]
    lines += [f"--space-{i}: {px}px;" for i, px in enumerate(SPACE)]
    lines += [f"--reading-{name}: {px}px;" for name, px in READING_SIZES.items()]
    lines += [f"--caption-size: {CAPTION_SIZE}px;", f"--rule-strong-px: {RULE_STRONG_PX}px;",
              f"--stop-diameter: {STOP['diameter']}px;", f"--stop-ring: {STOP['ring']}px;",
              f"--stop-ring-latched: {STOP['ring_latched']}px;", f"--stop-gap: {STOP['gap']}px;",
              f"--stop-diameter-narrow: {STOP['diameter_narrow']}px;"]
    lines += [f"--radius-{name}: {px}px;" for name, px in RADIUS.items()]
    lines += [f"--switch-track-w: {SWITCH['track'][0]}px;", f"--switch-track-h: {SWITCH['track'][1]}px;",
              f"--switch-knob: {SWITCH['knob']}px;"]
    lines += [f"--warning-mark-hollow: {1 if 'warning' in SEVERITY_MARK_HOLLOW else 0};"]
    lines += [f"--size-{name}: {size(step)}pt;"
              for name, step in (("caption", -1), ("base", 0), ("readout", 1), ("title", 2))]
    return ":root {\n  " + "\n  ".join(lines) + "\n}\n"
