"""One look for all three views. Tk and Qt read these values directly; the
Web view serves `css_variables()`. Nothing else in `views/` names a colour or
a font size."""

FONT_FAMILY = "Helvetica"
FONT_SIZE = 12            # base, in points; launch with --font-size to change

from station.palette import BACKGROUND, SURFACE, TEXT, MUTED  # noqa: F401  one palette

#: role -> (background, foreground)
ROLES = {
    "neutral": ("#3a3a3a", "#f0f0f0"),
    "go": ("#1b5e20", "#ffffff"),
    "danger": ("#8e0000", "#ffffff"),
    "warning": ("#e65100", "#000000"),
    "info": ("#0d47a1", "#ffffff"),
}
DISABLED = ("#2a2a2a", "#6a6a6a")
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
             f"--muted: {MUTED};"]
    for role, (bg, fg) in ROLES.items():
        lines += [f"--{role}-bg: {bg};", f"--{role}-fg: {fg};"]
    return ":root {\n  " + "\n  ".join(lines) + "\n}\n"
