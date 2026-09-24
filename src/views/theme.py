"""One look for all three views. Tk and Qt read these values directly; the
Web view serves `css_variables()`. Nothing else in `views/` names a colour or
a font size."""

FONT_FAMILY = "Helvetica"
FONT_SIZE = 12            # base, in points; launch with --font-size to change

from palette import (BACKGROUND, SURFACE, TEXT, MUTED,  # noqa: F401
                             SIGNAL, TRACE)                      # one palette

#: role -> (background, foreground)
#:
#: Owner ruling 2026-09-22: one red. `danger` is the signal colour and it is
#: the ONLY role that carries it, so the eye has exactly one thing to find in
#: a hurry. `warning` is the trace colour - the same amber a live number is
#: drawn in, because a warning is something to read, not something to stop
#: for. The other three are quiet steps of the panel, lit by ink: a ladder,
#: so `go` (the one you press) is the lightest and `info` barely leaves the
#: panel, and none of them competes with the stop.


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
    "info": (mix(SURFACE, TEXT, 0.03), TEXT),
    "neutral": (mix(SURFACE, TEXT, 0.06), TEXT),
    "go": (mix(SURFACE, TEXT, 0.12), TEXT),
    "danger": (SIGNAL, "#ffffff"),
    "warning": (TRACE, BACKGROUND),
}
#: Disabled text at 3.3:1 on the panel (was #6b7280, 2.75:1 - DS-8, AUD-11).
DISABLED = (SURFACE, mix(SURFACE, TEXT, 0.40))
SEVERITY_ROLE = {"error": "danger", "warning": "warning", "info": "info"}

#: Event text stays legible: severity is a MARK beside the line, not the ink
#: of the line. Signal on the window is 3.21:1 and on a panel 2.73:1, under
#: the 4.5:1 floor, so an error line drawn in red was the hardest line to
#: read (F14: DS-2, AUD-5, UXPM-4/10).
SEVERITY_INK = {"error": TEXT, "warning": TRACE, "info": MUTED}
SEVERITY_MARK = {"error": SIGNAL, "warning": TRACE, "info": MUTED}

#: The stop object's keyboard-focus ring is ink: a trace ring is what
#: "latched" looks like, so focus must never borrow it (F9: DS-1, UXPM-5).
STOP_FOCUS = TEXT

#: Hairlines and wells, once (DS-3, DS-8). RULE separates rows inside a
#: panel; RULE_STRONG separates panels; WELL is where typed text sits; LIFT
#: is a hovered control.
RULE = mix(SURFACE, TEXT, 0.12)
RULE_STRONG = mix(SURFACE, TEXT, 0.24)
WELL = BACKGROUND
LIFT = mix(SURFACE, TEXT, 0.09)
#: A control's border must be identifiable at 3:1 against the panel;
#: RULE_STRONG (1.98:1) is a separator, not an edge (Tk fix-round request).
INPUT_BORDER = mix(SURFACE, TEXT, 0.40)

#: Spacing steps in pixels (DS-7). PAD/GAP/INSET below stay as the three
#: names the views already use; anything else is one of these, never a sum.
SPACE = (2, 4, 6, 8, 12, 16, 24)

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
    return {"background": SURFACE, "foreground": TEXT, "border": background}


def css_variables():
    lines = [f"--font-family: {FONT_FAMILY}, sans-serif;", f"--font-size: {FONT_SIZE}pt;",
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
    lines += [f"--size-{name}: {size(step)}pt;"
              for name, step in (("caption", -1), ("base", 0), ("readout", 1), ("title", 2))]
    return ":root {\n  " + "\n  ".join(lines) + "\n}\n"
