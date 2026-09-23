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
ROLES = {
    "info": ("#2f363f", TEXT),
    "neutral": ("#363e49", TEXT),
    "go": ("#414b58", TEXT),
    "danger": (SIGNAL, "#ffffff"),
    "warning": (TRACE, BACKGROUND),
}
DISABLED = (SURFACE, "#6b7280")
SEVERITY_ROLE = {"error": "danger", "warning": "warning", "info": "info"}


#: Spacing scale, in pixels. Every view lays out with these three numbers.
PAD = 8        # around a panel or a section
GAP = 4        # between a label and its control, between rows
INSET = 12     # left indent of a section's body under its title


def set_font_size(points):
    global FONT_SIZE
    FONT_SIZE = max(8, min(28, int(points)))


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
    return ":root {\n  " + "\n  ".join(lines) + "\n}\n"
