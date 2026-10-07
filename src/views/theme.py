"""One look for all three views. Tk and Qt read these values directly; the
Web view serves `css_variables()`. Nothing else in `views/` names a colour,
a font, a size, a radius, a shadow or an icon path.

The look is "Signature" (owner ruling 2026-09-27, on the "Bench sheet,
tiered" layout of 2026-09-25): a light mineral faceplate on which every
raised part is one family - a face, a `KEY_RIM` outline and a `KEY_LIP`
below it - and the stop is a red key in an ink collar. Spec:
`docs/archive/handoff/tactile3-Signature.md`; the ratified rules:
`docs/rebuild/DESIGN_BRIEF.md`, "Signature".
"""

#: Text face. The Web view self-hosts it; Tk and Qt use it when installed
#: on the station PC and fall back to the platform sans otherwise.
FONT_FAMILY = "Figtree"
FONT_FALLBACK = "Helvetica"
FONT_SIZE = 12            # base, in points; launch with --font-size to change
#: Numerals and the two page headlines: Rubik 600, tabular figures
#: ("1111", "0000" and "8888" set at one width). Tk/Qt: the static
#: "Rubik SemiBold" face. No width axis: the stretch is 100.
NUMERAL_FAMILY = "Rubik"
NUMERAL_STRETCH = 100     # percent, where the toolkit supports it
NUMERAL_WEIGHT = 600

from palette import (BACKGROUND, SURFACE, TEXT, MUTED,  # noqa: F401
                             SIGNAL, TRACE)                      # one palette


def mix(colour, other, amount):
    """`amount` of `other` blended into `colour`, 0..1. The ONE mixing helper:
    every derived shade in every view comes from here, so a shade can never
    be one sign error away from its neighbour (UI audit 2026-09-24, DS-12)."""
    a = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


WHITE = "#ffffff"

#: Derived faces and edges (Signature), one job each. Every one is a
#: `mix()` of the six palette values, never a new colour:
#:   CAP       the face of a raised part (a key, the fader cap, the switch
#:             knob, the nameplate); ink on it 15.45
#:   RAIL      the raised rail's own face; ink 14.82
#:   DEEP      a pocket inside a tray (tier-2 fields, Diagnostics, the plot
#:             window); ink 10.79, muted 4.65
#:   EDGE      a disabled part's dashed rim; 3.58 on the sheet
#:   EDGE_SOFT a disabled part's ghost lip
#:   SKIRT     the stop key's visible side, and the socket band when latched;
#:             the stop's own red, shaded, spent on nothing else
#:   KEY_RIM   the 1.5 px outline of every raised part; 4.38 on the sheet,
#:             3.83 on the panel (the 3:1 control-edge floor)
#:   KEY_LIP   the lip below a key; 6.54 on the sheet
#:   GO_LIP    the lip below an ink (`go`) key
CAP = mix(BACKGROUND, WHITE, 0.60)
RAIL = mix(BACKGROUND, WHITE, 0.40)
DEEP = mix(SURFACE, TEXT, 0.06)
EDGE = mix(BACKGROUND, TEXT, 0.55)
EDGE_SOFT = mix(BACKGROUND, TEXT, 0.30)
SKIRT = mix(SIGNAL, TEXT, 0.40)
KEY_RIM = mix(BACKGROUND, TEXT, 0.62)
KEY_LIP = mix(BACKGROUND, TEXT, 0.75)
GO_LIP = mix(TEXT, "#000000", 0.55)

#: The lip below each kind of raised part, in px. `pressed` is what any lip
#: folds to when the part is down (the face drops by the difference, so the
#: part's height never changes).
KEY_LIP_PX = {"key": 4, "small": 3, "knob": 2.5, "kbd": 2, "pressed": 1}
KEY_RIM_PX = 1.5
#: Web-only garnish. No state is carried by any of these (rule 1): the
#: rim and the lip carry the part; Tk and Qt draw none of them.
SHADOW_RAISED = "0 3px 6px -3px rgba(26,31,34,.38)"
SHADOW_INSET = "inset 0 2px 3px rgba(26,31,34,.16), inset 0 -1px 0 rgba(255,255,255,.75)"
ENGRAVE = "0 1px 0 rgba(255,255,255,.7)"      # text-shadow under captions and legends

#: role -> (background, foreground)
#:
#: Owner ruling 2026-09-22: one red. `danger` is the signal colour and it is
#: the ONLY role that carries it, so the eye has exactly one thing to find in
#: a hurry. Signature (2026-09-27): `neutral` is a CAP-faced key, `go`, the
#: one you press, is an ink key with a CAP legend; `info` is the sheet
#: itself; `warning` is a CAP key with the warning glyph beside it, never
#: the trace (trace is reserved for changing numbers).
ROLES = {
    "info": (mix(BACKGROUND, SURFACE, 0.5), TEXT),
    "neutral": (CAP, TEXT),
    "go": (TEXT, CAP),
    "danger": (SIGNAL, WHITE),
    "warning": (CAP, TEXT),
}
#: Disabled: the sheet with ink at 72 % (5.96 on the sheet, 5.21 on the
#: panel, 4.68 on DEEP; rule 8) and, in the views, the part's own
#: silhouette in ghost tones (EDGE rim, EDGE_SOFT lip), never a blank.
DISABLED = (BACKGROUND, mix(BACKGROUND, TEXT, 0.72))
SEVERITY_ROLE = {"error": "danger", "warning": "warning", "info": "info"}

#: Event text stays legible: severity is a MARK beside the line, not the ink
#: of the line (F14: DS-2, AUD-5, UXPM-4/10). Signature: the mark is the
#: warning glyph (`ICONS["warning"]`) in the mark colour; a warning's is
#: hollow-stroked in ink, an error's in SIGNAL.
SEVERITY_INK = {"error": TEXT, "warning": TEXT, "info": MUTED}
SEVERITY_MARK = {"error": SIGNAL, "warning": TEXT, "info": MUTED}
SEVERITY_MARK_HOLLOW = frozenset({"warning"})

#: The stop object's keyboard-focus ring is ink: a trace ring is what
#: "latched" looks like, so focus must never borrow it (F9: DS-1, UXPM-5).
STOP_FOCUS = TEXT

#: Hairlines and wells, once (DS-3, DS-8). RULE separates rows inside a
#: panel; RULE_STRONG (2 px of ink with a 1 px white highlight under it on
#: the Web: an engraved line) heads an entry; WELL is where typed text sits
#: (a sunk window with a MUTED floor lip, INPUT_BORDER); LIFT is a hovered
#: control.
RULE = SURFACE
RULE_STRONG = TEXT
RULE_STRONG_PX = 2
#: The rule that heads an UNCONFIRMED model's entry: SIGNAL, and one step
#: thicker than the ink rule (Signature: 3 px).
RULE_ALARM_PX = 3
WELL = SURFACE
LIFT = mix(SURFACE, TEXT, 0.06)
INPUT_BORDER = MUTED
#: The 1 px line along the top of a tray or pocket (14 % ink on the panel),
#: the flattened form of SHADOW_INSET's upper edge; Tk and Qt draw this.
TRAY_LINE = mix(SURFACE, TEXT, 0.14)

#: Spacing steps in pixels (DS-7). PAD/GAP/INSET below stay as the three
#: names the views already use; anything else is one of these, never a sum.
SPACE = (2, 4, 6, 8, 12, 16, 20, 24, 28, 32, 44)

#: Readings (E, 2026-09-25; sizes Signature 2026-09-27): a live number is
#: never the size of its caption. `focal` is the opened model's rail
#: readouts, `primary` a single-value model's reading, `compact` a closed
#: probe's X/Y/Z, `secondary` a change. Under 1000 px the focal reading is
#: `READING_FOCAL_NARROW` so X/Y/Z hold one line.
READING_SIZES = {"focal": 60, "primary": 44, "compact": 36, "secondary": 30}
READING_FOCAL_NARROW = 54
CAPTION_SIZE = 13
AXIS_LETTER_SIZE = 14
STATISTIC_SIZE = 24        # a tier-2 statistic
#: The stop object (Signature): a SIGNAL key with a SKIRT below it, seated
#: in a sunk SURFACE socket inside an ink collar; a pale socket band shows
#: between key and collar. Latched (every model): the key drops
#: `drop_latched` px and loses its skirt, the socket floods SKIRT, the
#: collar turns SIGNAL, and the release glyph appears above "Clear".
#: Sizes in px at the base font size; views scale them with `size()`.
#: `diameter` is the whole object (the collar's outer edge). `ring` /
#: `ring_latched` keep their old names: the collar's width, idle and
#: latched (the "thicker ring" rule is now the collar turning red).
STOP = {"diameter": 172, "collar": 10, "key": 124, "skirt": 6, "lift": 4,
        "drop_latched": 6, "ring": 10, "ring_latched": 10, "gap": 6,
        "diameter_narrow": 150, "collar_narrow": 9, "key_narrow": 106,
        "face_pt": 32, "face_pt_narrow": 28,
        "collar_fill": TEXT, "collar_edge": KEY_RIM, "collar_latched": SIGNAL,
        "socket": SURFACE, "socket_latched": SKIRT,
        "face": SIGNAL, "legend": WHITE, "skirt_fill": SKIRT}
#: The single "on" sign (rule 5): a small lamp window. Hollow off; ink on
#: or shown (a latched mode key, the shown rail page, "Running"); SIGNAL
#: only for a model whose stop did not confirm. Never trace.
LAMP = {"size": (6, 13), "size_rail": (6, 12), "radius": 2,
        "off": SURFACE, "on": TEXT, "unconfirmed": SIGNAL, "edge": KEY_RIM}
#: The tripped-flag window at an unconfirmed model's entry (rule 4): an
#: ink frame showing SIGNAL with an ink hatch; it drops in once, 200 ms.
FLAG = {"size": (30, 20), "frame": TEXT, "fill": SIGNAL, "hatch": TEXT,
        "drop_ms": 200, "ease": "cubic-bezier(.16,1,.3,1)"}
#: The per-model stop is a small switch, not a second red disc: a sunk
#: track with a KEY_RIM edge and a key-cap knob; on, the track is SIGNAL
#: and the knob white with a SKIRT lip.
SWITCH = {"track": (42, 24), "knob": 18, "on_fill": SIGNAL,
          "off_edge": KEY_RIM, "knob_on": WHITE, "knob_off": CAP,
          "knob_lip": KEY_LIP, "knob_lip_on": SKIRT}
#: Radii grow with size. `control`, `input`, `well` and `switch` are the
#: names the views already use (control = key, well = tray).
RADIUS = {"control": 8, "input": 6, "well": 14, "switch": 7,
          "key": 8, "small": 6, "fader": 5, "pocket": 10, "tray": 14,
          "plate": 12}
#: The fader (slider thumb): a key cap with a lip and an ink index line.
FADER = {"cap": (16, 30), "index": (2, 14), "groove": 6}
#: Motion, in ms. Never slower than the state it reports; numbers and the
#: fader never animate; `prefers-reduced-motion` zeroes every one while the
#: pressed, latched and flagged STATES still apply.
MOTION = {"press": 70, "release": 110, "latch": 120, "clear": 180,
          "disclosure": 160, "tray": 200, "switch": 120, "flag": 200}
#: Tiers of prominence (owner ruling 2026-09-25). Tier 1 is always drawn;
#: tier 2 sits behind one disclosure per model; tier 3 behind a second one
#: inside it. A section names its own disclosure text; these are defaults.
TIER_LABELS = {2: "Configure", 3: "Diagnostics"}
#: Status by exception: a readonly whose value is one of these is a normal
#: state and is not drawn in tier 1 (it remains in the state for the API).
QUIET_VALUES = frozenset({"", "--", "Connected", "connected", "Idle", "idle",
                          "No", "no", "None", "none", "Not recording"})

#: The station's glyph set (rule 6): nine icons on a 20 px grid, one 1.75
#: stroke, round caps and joins, 2 px corners. A key carries at most one,
#: before its legend. The warning glyph is the error mark. The Web inlines
#: them, Qt loads them through QtSvg, Tk 8.7+ through PhotoImage.
ICON_STROKE = 1.75
ICONS = {
    "stop": '<path d="M7.24 3.35H12.76L16.65 7.24V12.76L12.76 16.65H7.24L3.35 12.76V7.24Z"/>',
    "clear": '<path d="M4.5 16.25H15.5"/><path d="M10 13V4.25"/><path d="M6.25 8L10 4.25L13.75 8"/>',
    "disclosure": '<path d="M8 5.25L12.75 10L8 14.75"/>',
    "gamepad": ('<path d="M6.5 5.75H13.5C15.6 5.75 16.9 7.2 17.3 9.3L17.9 13.1C18.15 14.7 16.6 15.7 '
                '15.4 14.8L13.2 13.1H6.8L4.6 14.8C3.4 15.7 1.85 14.7 2.1 13.1L2.7 9.3C3.1 7.2 4.4 5.75 '
                '6.5 5.75Z"/><path d="M6.25 8.25V11.25"/><path d="M4.75 9.75H7.75"/>'
                '<path d="M12.75 9.25H12.76"/><path d="M14.75 10.75H14.76"/>'),
    "link": ('<path d="M11.5 3.75H16.25V8.5"/><path d="M16.25 3.75L9.5 10.5"/>'
             '<path d="M14.25 12V14.25C14.25 15.35 13.35 16.25 12.25 16.25H5.75C4.65 16.25 3.75 15.35 '
             '3.75 14.25V7.75C3.75 6.65 4.65 5.75 5.75 5.75H8"/>'),
    "run": '<path d="M6.75 4.75L15.25 10L6.75 15.25Z"/>',
    "home": ('<path d="M3.25 9.25L10 3.75L16.75 9.25"/>'
             '<path d="M5.25 8V14.25C5.25 15.35 6.15 16.25 7.25 16.25H12.75C13.85 16.25 14.75 15.35 '
             '14.75 14.25V8"/><path d="M8.5 16.25V12.25H11.5V16.25"/>'),
    "download": '<path d="M10 3.5V12.25"/><path d="M6.25 8.75L10 12.5L13.75 8.75"/><path d="M4 16.25H16"/>',
    "warning": ('<path d="M8.6 4.3C9.2 3.25 10.8 3.25 11.4 4.3L17.1 14.15C17.7 15.2 16.95 16.25 15.75 '
                '16.25H4.25C3.05 16.25 2.3 15.2 2.9 14.15Z"/><path d="M10 8.25V11.25"/>'
                '<path d="M10 13.75H10.01"/>'),
}
ICON_NAMES = ("stop", "clear", "disclosure", "gamepad", "link", "run", "home",
              "download", "warning")


def icon_svg(name, size=16, colour="currentColor"):
    """The complete `<svg>` for one glyph of the set, `size` px square."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 20 20" fill="none" stroke="{colour}" stroke-width="{ICON_STROKE}" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'{ICONS[name]}</svg>')


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
    """What a toggle looks like in each state, from what the state MEANS
    (Signature, 2026-09-27). A latching key ON is the ink key pressed down
    with its lamp lit, whatever its role, except danger, which is filled
    SIGNAL (the stop). OFF is a CAP-faced key with the family's KEY_RIM
    outline, so ON/OFF differ in every theme without relying on
    green-vs-red; a danger toggle keeps its red outline when off, because
    the stop must read as the stop. An indicator is a lamp, not a key: off,
    it sits on the sheet."""
    role = element.get("on_role" if is_on else "off_role", "neutral")
    if is_on:
        background, foreground = colors("danger") if role == "danger" else colors("go")
        return {"background": background, "foreground": foreground, "border": background}
    border = SIGNAL if role == "danger" else KEY_RIM
    face = BACKGROUND if element.get("type") == "indicator" else CAP
    return {"background": face, "foreground": TEXT, "border": border}


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
    # Signature: the derived faces and edges, the lips, the garnish.
    lines += [f"--cap: {CAP};", f"--rail: {RAIL};", f"--deep: {DEEP};", f"--edge: {EDGE};",
              f"--edge-soft: {EDGE_SOFT};", f"--skirt: {SKIRT};", f"--key-rim: {KEY_RIM};",
              f"--key-lip: {KEY_LIP};", f"--go-lip: {GO_LIP};", f"--key-rim-px: {KEY_RIM_PX}px;",
              f"--shadow-raised: {SHADOW_RAISED};", f"--shadow-inset: {SHADOW_INSET};",
              f"--engrave: {ENGRAVE};"]
    lines += [f"--lip-{name}: {px}px;" for name, px in KEY_LIP_PX.items()]
    for severity in ("error", "warning", "info"):
        lines += [f"--{severity}-ink: {SEVERITY_INK[severity]};",
                  f"--{severity}-mark: {SEVERITY_MARK[severity]};"]
    lines += [f"--space-{i}: {px}px;" for i, px in enumerate(SPACE)]
    lines += [f"--reading-{name}: {px}px;" for name, px in READING_SIZES.items()]
    lines += [f"--reading-focal-narrow: {READING_FOCAL_NARROW}px;",
              f"--caption-size: {CAPTION_SIZE}px;", f"--axis-letter-size: {AXIS_LETTER_SIZE}px;",
              f"--statistic-size: {STATISTIC_SIZE}px;", f"--rule-strong-px: {RULE_STRONG_PX}px;"]
    # Every STOP length is a CSS px (the spec's sizes at the base font size);
    # `face_pt` is the legend's size in px on the Web too, its name being
    # the desktop toolkits' (they take points).
    for name, value in STOP.items():
        unit = "" if isinstance(value, str) else "px"
        lines += [f"--stop-{name.replace('_', '-')}: {value}{unit};"]
    lines += [f"--white: {WHITE};", f"--tray-line: {TRAY_LINE};", f"--rule-alarm-px: {RULE_ALARM_PX}px;"]
    lines += [f"--lamp-w: {LAMP['size'][0]}px;", f"--lamp-h: {LAMP['size'][1]}px;",
              f"--lamp-rail-h: {LAMP['size_rail'][1]}px;", f"--lamp-radius: {LAMP['radius']}px;",
              f"--lamp-off: {LAMP['off']};", f"--lamp-on: {LAMP['on']};",
              f"--lamp-unconfirmed: {LAMP['unconfirmed']};", f"--lamp-edge: {LAMP['edge']};",
              f"--flag-w: {FLAG['size'][0]}px;", f"--flag-h: {FLAG['size'][1]}px;",
              f"--flag-frame: {FLAG['frame']};", f"--flag-fill: {FLAG['fill']};",
              f"--flag-hatch: {FLAG['hatch']};", f"--flag-ease: {FLAG['ease']};"]
    lines += [f"--radius-{name}: {px}px;" for name, px in RADIUS.items()]
    lines += [f"--switch-track-w: {SWITCH['track'][0]}px;", f"--switch-track-h: {SWITCH['track'][1]}px;",
              f"--switch-knob: {SWITCH['knob']}px;", f"--switch-off-edge: {SWITCH['off_edge']};",
              f"--switch-knob-off: {SWITCH['knob_off']};", f"--switch-knob-lip: {SWITCH['knob_lip']};",
              f"--switch-knob-lip-on: {SWITCH['knob_lip_on']};",
              f"--fader-w: {FADER['cap'][0]}px;", f"--fader-h: {FADER['cap'][1]}px;",
              f"--fader-index-w: {FADER['index'][0]}px;", f"--fader-index-h: {FADER['index'][1]}px;",
              f"--fader-groove: {FADER['groove']}px;"]
    lines += [f"--motion-{name}: {ms}ms;" for name, ms in MOTION.items()]
    lines += [f"--icon-stroke: {ICON_STROKE};"]
    lines += [f"--warning-mark-hollow: {1 if 'warning' in SEVERITY_MARK_HOLLOW else 0};"]
    lines += [f"--size-{name}: {size(step)}pt;"
              for name, step in (("caption", -1), ("base", 0), ("readout", 1), ("title", 2))]
    return ":root {\n  " + "\n  ".join(lines) + "\n}\n"
