"""The Tk renderer: widgets only.

`views.base` holds every decision that is not a widget — what a
command does with its inputs, when a control is greyed out, what a refusal
looks like as a *state*, which events become a popup. This module supplies
the Tk widgets that state is drawn with, and nothing else. There is no model
import, no `setattr` on a model, and no model-specific subclass: Red Percent,
the rotator and Setup are schemas, not classes.

Three things this replaces are worth naming, because each was a defect.

* **`DynamicView` read the model directly** (`getattr(self.model, attr)`,
  `self.model.execute_command(...)`, a `RedPercentView` subclass that knew
  what "stop monitoring" meant). The Controller is the only backend object
  here now, reached through `PanelView._call`.
* **Colour and font were spelled out at 40-odd call sites** — `'darkgreen'`,
  `('Arial', 10, 'bold')`, `bg='black'` — so the same control looked
  different in each frontend. Every colour and every font in this file comes
  from `views.theme`.
* **Closing a tab called `notebook.forget()`** and left the model running with
  its coils energized and no way to reach it (VIEW-TKINTER-1). A tab close is
  `Controller.remove`, which estops and destructs; the Models menu reopens
  from the remembered config.

Tk-specific hazards this file is deliberate about:

* `tk.Button` ignores `bg`/`fg` on macOS Aqua, so a white-on-red button
  renders white-on-white. Buttons are `tk.Label`s styled as buttons, which is
  what the old view settled on for the same reason.
* Every `after` callback is cancelled on close. The old view registered five
  `after` loops per panel and cancelled none, which is how a popup loop ended
  up rescheduling itself onto a destroyed widget (VIEW-TKINTER-2).
* `<FocusOut>` fires both when the operator leaves the application and when
  *this* application opens a dialog. `focus_get()` tells them apart; treating
  a child dialog as focus loss is the defect behind VIEW-TKINTER-9. Focus
  gates input and never stops anything (D-4).
"""
import base64
import math
import os
import re
import shutil
import time
import tkinter as tk
from tkinter import ttk, filedialog
from tkinter import font as tkfont

import schema as sch
from events import events
from views import theme
from views import base as view_base
from views import picking
from views.base import Dashboard, PanelView, event_line, join_names, stop_words

SOURCE = "TkView"

#: Spacing comes from the theme: PAD around a panel or a section, GAP
#: between a label and its control and between rows, INSET for the left
#: indent of a section's body under its title. Any other distance is a step
#: of `theme.SPACE` - never a sum of these three (DS-7).
PAD, GAP, INSET = theme.PAD, theme.GAP, theme.INSET
SPACE = theme.SPACE

#: The type scale (Operate mode): one family and the theme's ratio, one
#: `theme.size(step)` per step. SMALL is a table header or the event log,
#: BASE is a caption, an entry or a button, STEP_1 is a section title and a
#: readout (numbers first: the value is one step louder than its label),
#: STEP_2 is a panel's name.
RATIO = theme.TYPE_RATIO
SMALL, BASE, STEP_1, STEP_2 = -1, 0, 1, 2

#: Widths. `FIELD_WIDTH` (characters) is an entry's request; a table's
#: dropdown is `DROPDOWN_WIDTH`, wide enough for a macOS port name.
#: `VALUE_PX` is the least a sheet cell's readout is given before it elides.
VALUE_PX, FIELD_WIDTH, DROPDOWN_WIDTH = 168, 8, 22

#: The Bench sheet (E, 2026-09-25). The rail is `RAIL_PX` wide, and
#: `RAIL_NARROW_PX` in a window under `NARROW_WINDOW_PX`, where the entries
#: also stack in one column. Wider, the models that are not opened share
#: rows of up to `SHEET_COLUMNS`, each column at least `COLUMN_PX` of the
#: design size (so 28 pt text gets fewer, wider columns).
RAIL_PX, RAIL_NARROW_PX, NARROW_WINDOW_PX = 248, 200, 1000
#: The rail's face and the nameplate's (Signature): the raised rail in
#: RAIL, the one CAP plate on it. The rail's side margin and the plate's
#: are steps of `theme.SPACE`, so the stop's plate fits 248 px.
RAIL_FACE, PLATE_FACE = theme.RAIL, theme.CAP
RAIL_PADX, PLATE_PADX = SPACE[5], SPACE[1]
SHEET_COLUMNS, COLUMN_PX = 3, 300

#: The type sizes the theme gives in pixels (readings, captions, the stop's
#: face) are drawn at the base font size the theme was designed at, and
#: scale with `--font-size` from there.
DESIGN_POINTS = 12

#: A readout is drawn in the trace colour only while its value is changing:
#: for `CHANGING_S` seconds after it last changed; ink otherwise.
CHANGING_S = 1.0

#: Axis letters: a readout captioned with one of these is a coordinate, and
#: a section of them is captioned once, the letters inline ("Position",
#: then X 12  Y -3  Z 0).
AXIS_LETTERS = ("X", "Y", "Z")

#: A table's columns when the window is short of width (UXPM5-5). The status
#: column takes nearly all the slack (`STATUS_WEIGHT` to a dropdown's 1) and
#: keeps room for its words: at least its value's width, up to
#: `STATUS_MIN_CHARS` characters (a longer identifier is cut in the middle,
#: whole in its tooltip). The dropdowns are the elastic columns: they give
#: up width before a status word does.
STATUS_WEIGHT, STATUS_MIN_CHARS = 100, 16

#: Lines the event tray shows once "Show events" is pressed. Folded, it is
#: one line: the latest warning or error, or nothing (status by exception).
EVENT_LOG_LINES = 5

#: The smallest an indicator lamp gets, in pixels. It grows with the text
#: beside it (`_lamp_px`): a 16 px dot beside 37 px text read as a speck.
LAMP_PX = 16

#: What a readout with nothing in it shows. An empty coloured label renders as
#: a bare stripe of colour, which reads as a broken widget rather than as "no
#: value yet" — it is what "Position age (s):" looked like at the bench.
EMPTY_READOUT = "--"

#: The end of a value too long for its cell. The whole value is the cell's
#: tooltip, so nothing is ever cut off silently.
ELLIPSIS = "…"

#: The stop object (Signature): a red key in an ink collar, in the rail's
#: nameplate (`theme.STOP`), always signal red. `STOP_DIAMETER` is the
#: collar's floor at the design size; the object grows from its
#: face font (`_Mushroom.diameter_for`), so "Clear" fits at every
#: `--font-size` (F7). The pulse is one breath (up 7 %, back) when the latch
#: closes, about 400 ms, and none at all with `STATION_NO_MOTION=1`.
STOP_DIAMETER, STOP_DIAMETER_NARROW = theme.STOP["diameter"], theme.STOP["diameter_narrow"]
PULSE_FRAMES, PULSE_FRAME_MS = (1.03, 1.06, 1.07, 1.05, 1.025, 1.0), 65
NO_MOTION_ENV = "STATION_NO_MOTION"

#: Copy the view owns: the stop's face, what a press will do (its tooltip),
#: and the line under it. The Web view's words, so an action keeps its name
#: in every frontend.
STOP_FACE, CLEAR_FACE = "Stop", "Clear"
STOP_HINT, CLEAR_HINT = "Stop every model", "Clear the stop on every model"
STOP_LINE = "Stop"
LATCHED_LINE = "Stopped: every model latched"
STOPPED_HEADLINE = "Every model is stopped."
STOPPED_NEXT = "Clear the stop on the rail to continue."
UNCONFIRMED_LINE = "Stop not confirmed. Treat as live."
#: A model whose disable failed (`is_faulted`, O4): the same hazard as a
#: stop that did not confirm, marked the same way at its entry, with the
#: model's own fault reason under the words.
FAULTED_LINE = "Disable failed. Treat as live."
#: The rail's per-model marks (round 7, L1; O4, O16): an ink square before
#: a latched model's name; the warning glyph in signal (and the lamp slot in
#: signal, Signature) before one that did not confirm or whose disable
#: failed; the words are the line's tooltip, so colour never carries it alone.
RAIL_MARK_WORDS = {"latched": "Stopped", "unconfirmed": "Did not confirm the stop",
                   "faulted": "Disable failed", "energized": "Energized"}
#: A model's own stop, the small switch in its Diagnostics (O16, PM8-8): one
#: word per thing. The disc is "Stop" / "Clear"; this switch is "Stop this
#: model" / "Stopped", whatever the schema's state words.
SWITCH_WORDS = {False: "Stop this model", True: "Stopped"}
#: The event title the views key on for a stop that did not confirm; its
#: line leaves the band and the tray when the latch opens (L2).
STOP_NOT_CONFIRMED = "Stop Not Confirmed"
STATION_TITLE = "Transfer stage"
#: The idle countdown (Tier N): one line per probe under the disc while its
#: `idle_remaining` is inside the warning window, rendered from state every
#: tick (no local timer that can drift), with Extend. The window is the
#: model's `idle_warn_seconds`; this is the fallback when a state has none.
IDLE_WARN_SECONDS = 60.0
IDLE_LINE = "{name} powers down in {seconds} s."
EXTEND_WORD = "Extend"
#: The event the countdown line replaces as the live word (O13): its tray
#: line is history only, never the folded latest line.
IDLE_SOON_TITLE = "Idle Timeout Soon"
#: Titled confirmations whose buttons name what they do (L14, TK7-10). The
#: Clear question's words are core's and name any model that did not
#: confirm; the answer that keeps things as they are is the default.
CLEAR_DIALOG = {"title": "Clear the stop", "yes_text": "Clear the stop",
                "no_text": "Keep it stopped"}
QUIT_DIALOG = {"title": "Quit", "yes_text": "Quit", "no_text": "Stay"}
CONFIRM_TITLE = "Confirm"
SIMULATION_LINE = "Simulation, no hardware attached"

#: The stop's keyboard shortcut, from anywhere in the window (F9). It only
#: ever STOPS: clearing the latch stays a deliberate press on the disc and a
#: confirmation. ONE chord on every platform (G5, owner ruling 2026-09-25:
#: no shortcut exists on one OS only), so the Mac gets Control-period too.
STOP_KEYS = ("<Control-period>",)
STOP_KEY_NAME = "Ctrl+."

#: Keyboard focus is a 2 px ring in ink on every focusable control (F25,
#: WCAG 2.4.13); the stop's ring is `theme.STOP_FOCUS`.
FOCUS_PX = 2
FOCUS_INK = theme.TEXT

#: The edge of anything typed into, and of an outlined command: the theme's
#: input border (muted, 5.4:1 on the panel, 5.9:1 on the sheet).
INPUT_BORDER = theme.INPUT_BORDER

#: The ttk styles the view draws with. A tick box (G3) is an ink box with a
#: sheet-coloured check mark, never the signal colour; its focus mark is the
#: `_Ring` around it, so ttk's own is off. A slider is flat: a panel track,
#: an ink thumb, muted when disabled.
CHECK_STYLE = "Station.TCheckbutton"
SCALE_STYLE = "Station.Horizontal.TScale"
WELL_SCALE_STYLE = "StationWell.Horizontal.TScale"
WELL_COMBO_STYLE = "Well.TCombobox"

#: Every control a pointer presses is at least this tall, ring included
#: (WCAG 2.5.8), at every font size; a command at least `COMMAND_PX` (L4:
#: 44 px everywhere is an owner call, so not here).
MIN_TARGET_PX = 24
COMMAND_PX = 36

#: Why a command is greyed out (L3) is `views.base.gate_reason` (O3): one
#: table for three views, read in both directions from the element's own
#: gate lists, so Step in manual mode says "In manual mode".
LATCHED_REASON = view_base.GATE_WORDS["latched"][0]
FAULT_REASON = view_base.GATE_WORDS["fault"][0]

#: A slider's keyboard (L6): an arrow moves 1 % of the travel (at least
#: one unit), Page Up / Page Down 10 %; Home and End do nothing - one stray
#: key used to commit the maximum speed.
SLIDER_ARROW_FRACTION, SLIDER_PAGE_FRACTION = 0.01, 0.10
SLIDER_KEYS = {"Left": -1, "Down": -1, "Right": 1, "Up": 1}
SLIDER_PAGE_KEYS = {"Prior": 1, "Next": -1}

#: The chevron a disclosure wears, closed and open.
CHEVRON = {False: "\u25b8", True: "\u25be"}
#: The overview entry's affordance (K4) and, with the model's name, its
#: tooltip: "Open Stepper Probe".
OPEN_WORD = "Open"
#: The rail's first item: the page of every launched model (K4).
OVERVIEW_PAGE = "Overview"
#: The quiet press at the foot of a model's well that closes it (L13, as
#: Web): it stops and disconnects the model, so it asks first.
CLOSE_MODEL_TEXT = "Close this model\u2026"
#: A detached log window's foot (L12).
CLOSE_WORD = "Close"
#: An image pane before the model has drawn one: one caption line (L15).
NO_FIGURE = "No figure yet."

#: The event tray's marks (status by exception): a solid signal square for
#: an error, a hollow ink one for a warning. Info is not drawn in the tray.
SEVERITY_WORD = {"error": "Error", "warning": "Warning", "info": "Info"}
MARK_SOLID, MARK_HOLLOW = "\u25a0", "\u25a1"
TRAY_SEVERITIES = ("warning", "error")

#: Unacknowledged errors the band lists by name before it summarises.
BAND_LINES = 3
#: Warnings and errors the tray's history keeps.
TRAY_HISTORY = 200


def _event_line(event):
    """One event as the band and the tray say it (L11): the severity word,
    then `views.base.event_line` - the title in sentence case, a colon, the
    message, the repeat count - so the three views word an event alike
    (ARCH-3). No bracketed source, no Title Case, no SHOUTING. The word stays
    because the mark's colour must not carry the severity alone (F14)."""
    word = SEVERITY_WORD.get(getattr(event, "severity", ""), "")
    line = event_line(event)
    return f"{word}: {line}" if word else line

#: True once the dashboard has found itself on Aqua. Tk there assumes 96 dpi
#: (`tk scaling` 1.33), so a 12 pt font is drawn 16 px tall: a third larger
#: than every native Mac control around it, which is the "everything is
#: oversized" the owner saw. A Mac point IS a logical pixel, so on Aqua the
#: theme's points are handed to Tk as pixels (a negative size).
_PIXEL_FONTS = False

#: The faces Tk found installed (`_resolve_families`, once the root exists):
#: the text face, else its fallback; the static "Rubik SemiBold" numeral
#: face (`theme.NUMERAL_WEIGHT` 600 is that face, so it is drawn at Tk's
#: normal weight), else the numeral family in bold, else the text face in
#: bold. Tk has no OpenType feature switch, so tabular figures are the
#: face's own (Rubik's and Helvetica's digits are tabular).
_TEXT_FAMILY = theme.FONT_FAMILY
_NUMERAL_FAMILY = theme.FONT_FAMILY
_NUMERAL_WEIGHT = "bold"
#: The static face Tk and Qt install for `NUMERAL_WEIGHT` 600.
NUMERAL_STATIC = f"{theme.NUMERAL_FAMILY} SemiBold"


def _resolve_families(root):
    """Pick the installed faces once, from the theme's names."""
    global _TEXT_FAMILY, _NUMERAL_FAMILY, _NUMERAL_WEIGHT
    try:
        installed = set(str(name) for name in tkfont.families(root))
    except Exception:
        return
    if not installed:
        return
    text = theme.FONT_FAMILY if theme.FONT_FAMILY in installed else theme.FONT_FALLBACK
    numeral = next((name for name in (NUMERAL_STATIC, theme.NUMERAL_FAMILY)
                    if name in installed), text)
    _TEXT_FAMILY, _NUMERAL_FAMILY = text, numeral
    _NUMERAL_WEIGHT = "normal" if numeral == NUMERAL_STATIC else "bold"
    events.debug("Faces", f"text={text!r} numerals={numeral!r}", source=SOURCE)


def _font(step=BASE, bold=False):
    """One step of the type scale, `theme.size(step)`."""
    size = theme.size(step)
    return (_TEXT_FAMILY, -size if _PIXEL_FONTS else size,
            "bold" if bold else "normal")


def _design_px(px):
    """A size the theme gives in pixels, at the current `--font-size`."""
    return max(1, round(px * theme.FONT_SIZE / DESIGN_POINTS))


def _numeral_font(px):
    """A reading, the stop's face or a headline: the numeral face at 600
    (`_NUMERAL_WEIGHT`), at `px` design pixels (Tk takes a negative size as
    pixels)."""
    return (_NUMERAL_FAMILY, -_design_px(px), _NUMERAL_WEIGHT)


def _reading_font(kind):
    return _numeral_font(theme.READING_SIZES[kind])


def _value_font():
    """A readout that is not a rail reading (a statistic, a status): the
    numeral face, bold, one step over the text."""
    size = theme.size(STEP_1)
    return (_NUMERAL_FAMILY, -size if _PIXEL_FONTS else size, _NUMERAL_WEIGHT)


def _caption_font():
    """A caption, a unit, an axis letter: `theme.CAPTION_SIZE`, muted."""
    return (_TEXT_FAMILY, -_design_px(theme.CAPTION_SIZE), "normal")


def _axis_font():
    """An axis letter (Signature): `theme.AXIS_LETTER_SIZE`, bold, muted."""
    return (_TEXT_FAMILY, -_design_px(theme.AXIS_LETTER_SIZE), "bold")


def _counter(background):
    """The other of the two grounds: a well on the sheet is panel-toned, an
    input inside a panel well is sheet-toned (design-Sheet.md: WELL is
    context-dependent)."""
    return theme.SURFACE if background == theme.BACKGROUND else theme.BACKGROUND


#: The tray's and the pocket's 1 px top line: a sunk part's upper edge
#: (the Web draws it as `SHADOW_INSET`).
TRAY_LINE = theme.EDGE_SOFT


def _tray_line(holder):
    """The 1 px line across the top of a tray or a pocket."""
    line = tk.Frame(holder, height=1, background=TRAY_LINE)
    line.pack(side="top", fill="x")
    return line


def _field_ground(background):
    """Where typed text sits (Signature): a sunk window one step down from
    its ground - SURFACE on the sheet or the rail, DEEP inside a tray, and
    SURFACE again inside a DEEP pocket."""
    return theme.DEEP if background == theme.SURFACE else theme.SURFACE


#: The grounds a child may take from its parent: the sheet, the tray, the
#: pocket, the rail, and a raised plate.
GROUNDS = (theme.BACKGROUND, theme.SURFACE, theme.DEEP, theme.RAIL, theme.CAP)


def _bg(widget):
    """The ground a widget is drawn on, so a child matches it."""
    try:
        colour = widget.cget("background")
    except Exception:
        colour = None
    return colour if colour in GROUNDS else _page()


def _page():
    """The sheet: where every reading sits. Panel-toned wells, the rail and
    inputs on the sheet are `theme.SURFACE` (Bench sheet, E)."""
    return theme.BACKGROUND


def _motion_reduced():
    """`STATION_NO_MOTION=1`: the latch changes face and ring, but nothing
    moves (AUD-14). Read on every pulse, so a test can set it."""
    return os.environ.get(NO_MOTION_ENV, "").strip() not in ("", "0")


_SHOUTED = re.compile(r"^[A-Z]{4,}[:.,]?$")
_CAPITALISED = re.compile(r"^[A-Z][a-z]+$")


def _sentence(text):
    """The Web view's `sentence`: a SHOUTED word of four letters or more comes
    down ("FULL STOP" -> "Full stop"); capitals that carry meaning ("X",
    "DC", "ID") stay. The schema is the model's vocabulary and is not edited;
    this is presentation."""
    words = [word.lower() if _SHOUTED.match(word) else word
             for word in str("" if text is None else text).split(" ")]
    joined = " ".join(words)
    return joined[:1].upper() + joined[1:]


def _label(text):
    """The Web view's `sentenceCase`, for a caption, heading or button: the
    interior Capitalised words come down too ("Coordinate Frame" ->
    "Coordinate frame") and the trailing colon goes - the value sits beside
    its label on a panel, not after it in a form."""
    words = re.sub(r"\s*:\s*$", "", _sentence(text)).split(" ")
    return " ".join(word.lower() if index and _CAPITALISED.match(word) else word
                    for index, word in enumerate(words))


_UNIT = re.compile(r"^(.*\S)\s*\(([^(),]{1,6})\)$")


def _split_unit(text):
    """A caption and its unit: "Position (deg)" -> ("Position", "deg"). A
    presentation transform, like the axis letters; the schema is unedited.
    A parenthetical that is not a short unit ("Velocity (x, y, z)") stays."""
    match = _UNIT.match(text or "")
    return (match.group(1), match.group(2)) if match else (text, "")


def _is_number(text):
    """True for a reading ("-352", "24.98", "0.18"), not for a word or an
    identifier: only a changing NUMBER is drawn in the trace colour."""
    try:
        float(str(text).replace(",", "").split(" ")[0])
    except (TypeError, ValueError):
        return False
    return True


def _tier_of(section):
    """A section's tier; 1 when the schema does not say."""
    return section.get("tier") or 1


def _axis_of(text):
    """"X" for a readout captioned "X:", else None."""
    letter = _label(text).strip()
    return letter if letter in AXIS_LETTERS else None


_MEASURES = {}


def _measure(font):
    """A `tkfont.Font` for `font`, or None when Tk cannot say (no display, or
    the test stand-in)."""
    try:
        measure = _MEASURES.get(font)
        if measure is None:
            measure = _MEASURES[font] = tkfont.Font(font=font)
        return measure
    except Exception:
        return None


def _text_width(font, text):
    """Pixels `text` takes in `font`, or None when Tk cannot say."""
    try:
        width = _measure(font).measure(text)
    except Exception:
        return None
    return width if isinstance(width, int) else None


def _width_px(font, text):
    """`_text_width`, or an estimate from the size when Tk cannot measure:
    a Helvetica glyph is about 0.55 em wide, 0.62 em in bold."""
    width = _text_width(font, text)
    if width is not None:
        return width
    em = abs(font[1]) * (4 / 3 if font[1] > 0 else 1)     # points -> px
    return math.ceil(em * (0.62 if font[2] == "bold" else 0.55) * len(text))


def _line_px(step=BASE, bold=False):
    """The line height of one type step in pixels, measured when Tk can."""
    font = _font(step, bold)
    try:
        height = _measure(font).metrics("linespace")
        if isinstance(height, int) and height > 0:
            return height
    except Exception:
        pass
    em = abs(font[1]) * (4 / 3 if font[1] > 0 else 1)
    return math.ceil(em * 1.25)


def _target_pady(step=BASE):
    """Vertical padding that makes a pressable `MIN_TARGET_PX` tall with its
    two-pixel ring, at this font size - never less than GAP."""
    return max(GAP, math.ceil((MIN_TARGET_PX - _line_px(step) - 2 * FOCUS_PX) / 2))


def _command_pady(step=BASE, lip=0):
    """Vertical padding that makes a command `COMMAND_PX` tall with its ring
    (L4), at this font size - its lip included, when it is a key."""
    return max(GAP, math.ceil((COMMAND_PX - _line_px(step) - 2 * FOCUS_PX - lip) / 2))


def _key_font():
    """A key's legend (Signature: 14/600): the text face, bold - Tk has no
    600 weight."""
    return _font(bold=True)


def _field_pady():
    """An entry's or a dropdown's inner vertical padding: the field, its
    ring and its underline at least `MIN_TARGET_PX` tall (L4, TK7-6)."""
    chrome = 2 * FOCUS_PX + UNDERLINE_PX
    return max(1, math.ceil((MIN_TARGET_PX - _line_px() - chrome) / 2))


def _lamp_px():
    """A lamp scales with the caption beside it, from a 16 px floor."""
    return max(LAMP_PX, round(0.8 * _line_px()))


# -- Signature: the key family, the lamp slot, the glyphs ---------------------
#
# Every raised part is one family (Signature, 2026-09-27): a face, a rim and
# a lip below it. ttk `clam` cannot draw a thick bottom edge on one side, so
# a key here is frames: a seat in the ground, a lip frame in `KEY_LIP` under
# the rim frame, the face inside. Pressed or latched, the lip folds and the
# face drops by the difference - the pads move, the height does not. Rims
# are 1 px and corners square: the accepted fallback (tone and lip carry
# the part).

def _lip_px(kind):
    """The lip under a raised part of `kind` (`theme.KEY_LIP_PX`), in whole
    pixels: Tk draws no half pixel (the switch knob's 2.5 is 2)."""
    return max(1, int(theme.KEY_LIP_PX[kind]))


def _key_pads(kind, is_down):
    """(drop, lip) for a key: up, the face stands on its whole lip; down
    (pressed or latched), the lip folds to `KEY_LIP_PX["pressed"]` and the
    face drops by what the lip lost, so the key's height never changes."""
    lip = _lip_px(kind)
    if not is_down:
        return 0, lip
    folded = min(lip, _lip_px("pressed"))
    return lip - folded, folded


def _key_look(kind, ground):
    """(face, legend, rim, lip) of a key of `kind`: `neutral` a CAP face
    with the KEY_RIM rim and KEY_LIP lip; `go` (and a latched key) the ink
    face with its GO_LIP; `danger` the stop's red, its lip the SKIRT;
    `disabled` the key's silhouette in ghost tones on its ground (EDGE rim,
    EDGE_SOFT lip, DISABLED legend); `ghost` a text key, no rim, no lip."""
    if kind == "go":
        face, legend = theme.colors("go")
        return face, legend, face, theme.GO_LIP
    if kind == "danger":
        face, legend = theme.colors("danger")
        return face, legend, face, theme.SKIRT
    if kind == "disabled":
        return ground, theme.DISABLED[1], theme.EDGE, theme.EDGE_SOFT
    if kind == "ghost":
        return ground, theme.TEXT, ground, ground
    face, legend = theme.colors("neutral")
    return face, legend, theme.KEY_RIM, theme.KEY_LIP


#: Which glyph of the station's set (`theme.ICONS`) a key carries before its
#: legend, by the element's command (or a log's attribute): Home, Start run,
#: Save run..., Gamepad log... (rule 6). A key carries at most one.
KEY_GLYPHS = {"home": "home", "start_run": "run", "save": "download",
              "gamepad_log": "gamepad"}
#: A key's glyph and a canvas mark, in design pixels.
GLYPH_PX = 16


def _lamp_size(rail=False):
    """The lamp slot (rule 5), (w, h) at the current font size."""
    width, height = theme.LAMP["size_rail" if rail else "size"]
    return _design_px(width), _design_px(height)


#: What a lamp slot shows: hidden (the space kept), hollow (off), ink (on
#: or shown), lit (on, in an ink key), unconfirmed (a stop that did not
#: confirm), ghost (a disabled key's).
LAMP_STATES = ("hidden", "off", "on", "lit", "unconfirmed", "ghost")


def _lamp_colours(state, face):
    """(fill, edge) of a lamp slot in `state` on a key `face`, or None when
    it is hidden. Ink or SIGNAL, never trace."""
    if state == "hidden":
        return None
    if state == "on":
        return theme.LAMP["on"], theme.LAMP["on"]
    if state == "lit":
        return theme.CAP, theme.CAP
    if state == "unconfirmed":
        return theme.LAMP["unconfirmed"], theme.LAMP["unconfirmed"]
    if state == "ghost":
        return face, theme.EDGE
    return theme.LAMP["off"], theme.LAMP["edge"]


def _draw_lamp(canvas, state, face, rail=False):
    """Redraw a lamp slot's Canvas: one rectangle, filled and edged."""
    width, height = _lamp_size(rail)
    colours = _lamp_colours(state, face)
    try:
        canvas.delete("all")
        canvas.configure(background=face)
        if colours is not None:
            canvas.create_rectangle(0, 0, width - 1, height - 1, fill=colours[0],
                                    outline=colours[1], tags=("lamp",))
    except Exception as exc:
        events.debug("Lamp Slot Draw Failed", str(exc), source=SOURCE,
                     exception=exc, every=5.0)


_PATH_TOKEN = re.compile(r"[A-Za-z]|-?\d*\.?\d+")
_STROKES = {}


def _glyph_strokes(name):
    """`theme.ICONS[name]` as polylines on its 20 px grid: M, L, H, V and Z
    exactly, a curve (C) by its end point - at 16 px a 2 px corner reads the
    same. The glyphs are the theme's; this only reads them."""
    strokes = _STROKES.get(name)
    if strokes is not None:
        return strokes
    strokes = []
    for path in re.findall(r'd="([^"]+)"', theme.ICONS[name]):
        tokens = _PATH_TOKEN.findall(path)
        command, x, y, start, line = "M", 0.0, 0.0, (0.0, 0.0), None
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.isalpha():
                command = token.upper()
                index += 1
                if command == "Z" and line is not None:
                    line.append(start)
                    x, y = start
                continue
            take = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6}.get(command, 2)
            values = [float(v) for v in tokens[index:index + take]]
            index += take
            if command == "H":
                x = values[0]
            elif command == "V":
                y = values[0]
            else:
                x, y = values[-2], values[-1]
            if command == "M":
                line = [(x, y)]
                strokes.append(line)
                start = (x, y)
                command = "L"           # further pairs after M are lines
            elif line is not None:
                line.append((x, y))
    _STROKES[name] = strokes
    return strokes


def _draw_glyph(canvas, name, x, y, size, colour, turn=0, tags=("glyph",)):
    """Draw glyph `name` into `size` px square at (x, y), in the set's one
    stroke (`theme.ICON_STROKE` on the 20 px grid), round caps and joins.
    `turn=90` turns it clockwise (an open disclosure points down)."""
    scale = size / 20.0
    width = max(1.0, theme.ICON_STROKE * scale)
    for stroke in _glyph_strokes(name):
        points = []
        for px, py in stroke:
            if turn == 90:
                px, py = 20.0 - py, px
            points += [x + px * scale, y + py * scale]
        if len(points) == 2:
            points += points
        canvas.create_line(*points, fill=colour, width=width, capstyle="round",
                           joinstyle="round", tags=tags)


def _clip_line(start, end, box):
    """The part of the segment start-end inside `box` (x0, y0, x1, y1), as
    four coordinates, or None (Liang-Barsky)."""
    (x0, y0), (x1, y1) = start, end
    dx, dy = x1 - x0, y1 - y0
    low, high = 0.0, 1.0
    for p, q in ((-dx, x0 - box[0]), (dx, box[2] - x0),
                 (-dy, y0 - box[1]), (dy, box[3] - y0)):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            low = max(low, t)
        else:
            high = min(high, t)
    if low > high:
        return None
    return (x0 + low * dx, y0 + low * dy, x0 + high * dx, y0 + high * dy)


def _mark_canvas(parent, background):
    """A canvas for a warning mark: the glyph, `GLYPH_PX` square."""
    size = _design_px(GLYPH_PX)
    return tk.Canvas(parent, width=size, height=size, background=background,
                     highlightthickness=0)


def _draw_warning(canvas, colour):
    """The error mark (rule 6): the warning glyph, where a red square was."""
    try:
        canvas.delete("all")
        if colour:
            _draw_glyph(canvas, "warning", 0, 0, _design_px(GLYPH_PX), colour,
                        tags=("warning",))
    except Exception as exc:
        events.debug("Mark Draw Failed", str(exc), source=SOURCE, exception=exc,
                     every=5.0)


_ICON_IMAGES = {}


def _icon_image(name, colour, size=None):
    """A key's glyph as a PhotoImage of `theme.icon_svg`, from Tk 8.7 on (its
    SVG photo format). Below 8.7 there is none and the legend stands alone:
    no PNG pipeline. Cached, so every image stays alive."""
    if not name or _tk_version() < 8.7:
        return None
    size = size or _design_px(GLYPH_PX)
    key = (name, size, colour)
    if key in _ICON_IMAGES:
        return _ICON_IMAGES[key]
    svg = theme.icon_svg(name, size, colour)
    image = None
    for options in ({"format": "svg"}, {}):
        try:
            image = tk.PhotoImage(data=svg, **options)
            break
        except Exception as exc:
            events.debug("Glyph Not Loaded", f"{name}: {exc}", source=SOURCE,
                         exception=exc, every=30.0)
    _ICON_IMAGES[key] = image
    return image


#: Where the operator last dragged each detached log window, for the
#: session: (panel name, caption) -> (x, y). A reopened window goes back
#: there instead of to the computed place (I1).
_LOG_POSITIONS = {}

_GEOMETRY = re.compile(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)")


def _parse_geometry(text):
    """'WxH+X+Y' -> (x, y, w, h), or None."""
    match = _GEOMETRY.fullmatch(str(text or "").strip())
    if not match:
        return None
    width, height, x, y = (int(part) for part in match.groups())
    return (x, y, width, height)


def _rect_of(widget):
    """A widget's rect on the screen, (x, y, w, h), or None when Tk cannot say."""
    try:
        rect = (widget.winfo_rootx(), widget.winfo_rooty(),
                widget.winfo_width(), widget.winfo_height())
    except Exception:
        return None
    return rect if all(isinstance(v, int) for v in rect) else None


def _overlap(a, b):
    """The intersection of two rects, or None."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def _flow_lines(widths, room, gap):
    """Which line each of a run of widgets goes on, filling left to right:
    a widget that would end past `room` starts the next line. A widget
    wider than `room` gets a line to itself. No `room`: one line."""
    lines, line, used = [], 0, 0
    for width in widths:
        if room is not None and used and used + width > room:
            line, used = line + 1, 0
        lines.append(line)
        used += width + gap
    return lines


def _clamp(value, low, high):
    return max(low, min(value, high))


def _log_window_rect(station, screen, natural, minimum, free=None, gap=0):
    """Where a detached log window goes: its outer rect (x, y, w, h).

    `station` is the station window's outer rect, `screen` the screen's,
    `natural` the window's own outer size and `minimum` the least it may
    shrink to. The window goes OUTSIDE the station window - to its right,
    else below it, else to its left - at its natural size where that fits,
    shrunk towards `minimum` where only that fits, clamped to the screen.
    With no room outside, it goes over `free` (the event tray), the one
    part of the station window that holds no control: never over a panel,
    whose Safety column carries a Stop and a Fault lamp (UXPM5-1).
    """
    sx, sy, sw, sh = station
    left, top, width, height = screen
    right, bottom = left + width, top + height
    nat_w, nat_h = natural
    min_w, min_h = minimum

    def beside(x_room, x_of):
        if x_room < min_w or height < min_h:
            return None
        w, h = min(nat_w, x_room), min(nat_h, height)
        return (x_of(w), _clamp(sy, top, bottom - h), w, h)

    def under():
        y = sy + sh + gap
        room = bottom - y
        if room < min_h or width < min_w:
            return None
        w, h = min(nat_w, width), min(nat_h, room)
        return (_clamp(sx + sw - w, left, right - w), y, w, h)

    candidates = [
        beside(right - (sx + sw + gap), lambda w: sx + sw + gap),
        under(),
        beside(sx - gap - left, lambda w: sx - gap - w),
    ]
    fitted = [rect for rect in candidates if rect is not None]
    for rect in fitted:
        if rect[2:] == (min(nat_w, width), min(nat_h, height)):
            return rect
    if fitted:
        return max(fitted, key=lambda rect: rect[2] * rect[3])
    room = _overlap(free, screen) if free is not None else None
    if room is not None:
        w, h = min(nat_w, room[2]), min(nat_h, room[3])
        return (room[0] + room[2] - w, room[1], w, h)
    # Nowhere clear at all (the tray itself is off the screen): the screen's
    # bottom-right corner, which is at least not the top of any panel.
    w, h = min(nat_w, width), min(nat_h, height)
    return (right - w, bottom - h, w, h)


def _elide(font, text, room, middle=False):
    """`text`, or as much of it as fits in `room` px with an ellipsis: at the
    end for prose, in the MIDDLE for an identifier (`middle=True`), whose
    tail is the part that tells two apart ("/dev/cu.usbm…14201")."""
    full = _text_width(font, text)
    if full is None or room is None or room <= 1 or full <= room:
        return text
    low, high = 0, len(text)
    while low < high:
        keep = (low + high + 1) // 2
        width = _text_width(font, _cut(text, keep, middle))
        if width is not None and width <= room:
            low = keep
        else:
            high = keep - 1
    return _cut(text, low, middle)


def _cut(text, keep, middle):
    """`text` shortened to `keep` characters plus an ellipsis."""
    if keep >= len(text):
        return text
    if not middle:
        return text[:keep].rstrip() + ELLIPSIS
    tail = math.ceil(keep * 0.6)
    head = keep - tail
    return text[:head].rstrip() + ELLIPSIS + (text[-tail:] if tail else "")


def _elide_middle(text, limit):
    """`text` in at most `limit` characters, the ellipsis in the middle."""
    text = str(text)
    if limit < 5 or len(text) <= limit:
        return text
    return _cut(text, limit - 1, True)


def _is_identifier(text):
    """One unbroken token (a Run ID, a port): elide it in the middle."""
    text = str(text).strip()
    return bool(text) and " " not in text


def _tcl_error():
    """`tk.TclError`, or `Exception` when Tk is a stand-in.

    The test harness replaces `tkinter` with a mock whose `TclError` is a mock
    *instance*, and `except <instance>` raises `TypeError` at the moment the
    handler is needed — turning a recoverable bad tab index into a crash
    inside an event handler.
    """
    candidate = getattr(tk, "TclError", None)
    if isinstance(candidate, type) and issubclass(candidate, BaseException):
        return candidate
    return Exception


_TCL_ERROR = _tcl_error()


def _windowing_system(widget):
    """"aqua", "win32" or "x11". Unknown answers are treated as x11."""
    try:
        return str(widget.tk.call("tk", "windowingsystem"))
    except Exception:
        return "x11"


def _tk_version():
    try:
        return float(getattr(tk, "TkVersion", 9.0))
    except (TypeError, ValueError):
        return 9.0


def _close_tab_button(widget):
    """The event that closes a tab: the MIDDLE button, on every platform
    (VIEW-TKINTER-18, G5).

    The gesture is one gesture; only Tk's numbering of it differs, which is
    the branch the toolkit forces. Button 2 is the middle button on X11 and
    Win32, and on Aqua from Tk 8.7 on; Tk 8.6 on Aqua numbered the right
    button 2 and the middle 3. The previous branch bound 3 - the RIGHT
    button - on X11 and Win32, so a right-click closed a tab there and not
    on a Mac.
    """
    if _windowing_system(widget) == "aqua" and _tk_version() < 8.7:
        return "<ButtonPress-3>"
    return "<ButtonPress-2>"


class ClosableNotebook(ttk.Notebook):
    """A notebook whose tabs can be dragged to reorder and clicked to close.

    Pure widget: it resolves a click to a tab index and hands that to
    `on_close_tab`. What closing *means* belongs to the dashboard, because it
    means destructing a model.

    `on_close_tab` is actually assigned now. `DraggableClosableNotebook`
    declared the same attribute, never wrote it, and so always fell through to
    `forget()` — a one-way removal ttk offers no way back from
    (VIEW-TKINTER-1).
    """

    def __init__(self, master=None, on_close_tab=None, **kwargs):
        super().__init__(master, **kwargs)
        self.on_close_tab = on_close_tab
        self._dragging = None
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind(_close_tab_button(self), self._on_middle_press)

    # -- close -------------------------------------------------------------
    def close_tab(self, index):
        if self.on_close_tab is not None:
            self.on_close_tab(index)
        else:
            try:
                self.forget(index)
            except _TCL_ERROR:
                pass

    def _on_middle_press(self, event):
        index = self._tab_at(event)
        if index is not None:
            self.close_tab(index)

    # -- drag to reorder ---------------------------------------------------
    def _on_press(self, event):
        self._dragging = self._tab_at(event)

    def _on_drag(self, event):
        if self._dragging is None:
            return
        target = self._tab_at(event)
        if target is None or target == self._dragging:
            return
        try:
            self.insert(target, self.tabs()[self._dragging])
        except (_TCL_ERROR, IndexError):
            return
        self._dragging = target

    def _on_release(self, _event):
        self._dragging = None

    def _tab_at(self, event):
        try:
            return self.index(f"@{event.x},{event.y}")
        except (_TCL_ERROR, ValueError):
            return None


class _RegionPicker:
    """A borderless overlay the operator drags a box on, over a picture of
    the desktop.

    Tk had no picker at all — `_select_region` asked for "x,y,width,height" as
    *text* in a modal prompt, which is why REDPERCENT-18 is still open. This
    is the same drag interaction the Qt view gets, in Tk idiom: a topmost
    `Toplevel` sized to the virtual desktop, a rubber band on a Canvas,
    Escape to cancel, and screen coordinates out.

    What the operator sees is a screenshot taken before the overlay opened,
    drawn on the canvas and dimmed a little - never transparency. At the
    bench (2026-09-27, a Linux PC) the overlay was a 0.3-alpha sheet: an X11
    session without a compositor accepts `-alpha` and never honours it, and
    "a white view covered the whole display during selection". With a
    picture the overlay is opaque on purpose and `-alpha` is not requested.
    Without one (capture unavailable, or a picture that will not decode) the
    alpha overlay is the fallback, and says the screen could not be pictured.
    That is a fallback on what the model supplied, not a platform branch.

    A drag smaller than `MINIMUM_DRAG` is reported rather than returned: a
    stray click used to capture a 1x1 region, and a 1x1 focus area reads 100%
    red forever.
    """

    MINIMUM_DRAG = 10      # px; below this a drag is a misclick, not a region
    INSTRUCTION = "Drag a box around the area to watch. Esc cancels."
    NO_PICTURE = " (the screen could not be pictured)"
    #: How far the picture is blended toward the page colour: enough for the
    #: band and the instruction to read over it, little enough that the
    #: area being picked is plainly visible.
    DIM = 0.25

    def __init__(self, master):
        self.master = master
        self.region = None
        self.reason = ""
        self._origin = None
        self._press = (0, 0)
        self._band = None
        self.top = None
        self.canvas = None
        #: The picture on the canvas. Tk drops an image the moment Python
        #: does, so the picker keeps the reference for its lifetime.
        self.photo = None
        self._picture_item = None
        self._placed = None
        self._screenshot = None
        self._desktop = None

    def pick(self, screenshot=None):
        """Blocks until the operator drags or cancels. -> (x, y, w, h) | None.

        `screenshot` is the model's `screen_image`: `{"image": png, "left",
        "top", "width", "height"}`, or None when the screen could not be
        captured. The caller takes it BEFORE this opens, so the overlay is
        not in the picture."""
        self._build(screenshot=screenshot)
        try:
            self.master.wait_window(self.top)
        except Exception as exc:
            events.debug("Region Wait Failed", str(exc), source=SOURCE,
                         exception=exc)
        events.debug("Region Picked", f"region={self.region} reason={self.reason!r}",
                     source=SOURCE)
        return self.region

    def _build(self, screenshot=None):
        geometry, size = self._virtual_desktop()
        desktop = self._desktop_box()
        # Decoded and scaled before the overlay exists: a picture that will
        # not decode is known before anything is asked of the window.
        picture = self._picture_data(screenshot, desktop)
        self.top = tk.Toplevel(self.master)
        self.top.overrideredirect(True)
        self._request("-topmost", True)
        self.top.geometry(geometry)
        self.top.configure(background=theme.BACKGROUND)
        self.canvas = tk.Canvas(self.top, highlightthickness=0, cursor="crosshair",
                                background=theme.BACKGROUND)
        self.canvas.pack(fill="both", expand=True)
        if picture is not None:
            try:
                self.photo = tk.PhotoImage(data=picture)
                # Anchored where the desktop's origin falls in the overlay,
                # never stretched to it: re-placed if the overlay lands
                # somewhere other than where it was asked to go.
                _scale, x, y = picking.picture_placement(screenshot, desktop,
                                                         desktop)
                self._picture_item = self.canvas.create_image(
                    x, y, anchor="nw", image=self.photo)
                self._placed = (x, y)
                self._screenshot = screenshot
                self._desktop = desktop
                events.debug("Region Picture Placed", picking.placement_line(
                    screenshot, desktop, desktop), source=SOURCE)
            except Exception as exc:
                events.debug("Region Picture Failed", str(exc), source=SOURCE,
                             exception=exc)
                self.photo = None
        if self.photo is None:
            # The fallback: see-through where a compositor honours it.
            self._request("-alpha", 0.3)
        self._draw_instruction(size[0], is_pictured=self.photo is not None)
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.top.bind("<Escape>", self._on_escape_press)
        if self.photo is not None:
            self.top.bind("<Configure>", self._on_overlay_configure)
        try:
            self.top.focus_force()
            self.canvas.grab_set()
        except Exception as exc:
            events.debug("Overlay Grab Failed", str(exc), source=SOURCE,
                         exception=exc)

    def _request(self, attribute, value):
        try:
            self.top.attributes(attribute, value)
        except Exception as exc:
            events.debug("Overlay Attribute Refused",
                         f"{attribute}={value}: {exc}", source=SOURCE,
                         exception=exc)

    def _picture_data(self, screenshot, desktop):
        """The screenshot at its logical desktop size (`picking`: 1:1 on the
        bench, halved on a Retina Mac, never stretched to the overlay) and
        dimmed, as base64 PNG for `tk.PhotoImage` (Tk reads PNG natively,
        as `_show_image` does), or None when there is no picture to show."""
        if not isinstance(screenshot, dict) or not screenshot.get("image"):
            events.debug("Region Picture", "none: the screen could not be "
                         "captured", source=SOURCE)
            return None
        try:
            import io
            from PIL import Image
            image = Image.open(io.BytesIO(bytes(screenshot["image"]))).convert("RGB")
            scale, _x, _y = picking.picture_placement(screenshot, desktop, desktop)
            if not screenshot.get("width") or not screenshot.get("height"):
                # No bounds to measure by: the picture's own pixels are them.
                screenshot = dict(screenshot, width=image.width,
                                  height=image.height)
                scale, _x, _y = picking.picture_placement(screenshot, desktop,
                                                          desktop)
            drawn = picking.drawn_size(screenshot, scale)
            if image.size != drawn:
                image = image.resize(drawn)
            ground = Image.new("RGB", drawn, theme.BACKGROUND)
            image = Image.blend(image, ground, self.DIM)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", compress_level=1)
        except Exception as exc:
            events.debug("Region Picture Failed", f"the screenshot would not "
                         f"decode: {exc}", source=SOURCE, exception=exc)
            return None
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def _on_overlay_configure(self, _event=None):
        """The overlay was mapped, moved or resized: keep the desktop's
        origin under the pixel `x_root` names. A window manager that keeps
        the overlay out of a panel moves the picture, it does not squash
        it."""
        if self.photo is None or self._picture_item is None:
            return
        overlay = self._overlay_box()
        if overlay is None:
            return
        _scale, x, y = picking.picture_placement(self._screenshot,
                                                 self._desktop, overlay)
        if (x, y) == self._placed:
            return
        try:
            self.canvas.coords(self._picture_item, x, y)
        except Exception as exc:
            events.debug("Region Picture Move Failed", str(exc), source=SOURCE,
                         exception=exc)
            return
        self._placed = (x, y)
        events.debug("Region Picture Placed", picking.placement_line(
            self._screenshot, self._desktop, overlay), source=SOURCE)

    def _overlay_box(self):
        """The overlay's actual `(left, top, width, height)` on the desktop,
        or None before Tk can say."""
        try:
            box = (int(self.top.winfo_rootx()), int(self.top.winfo_rooty()),
                   int(self.top.winfo_width()), int(self.top.winfo_height()))
        except Exception:
            return None
        return box if box[2] > 1 and box[3] > 1 else None

    def _desktop_box(self):
        """The logical virtual desktop as `(left, top, width, height)`."""
        widget = self.top if self.top is not None else self.master
        return (self._measure(widget, "winfo_vrootx", None),
                self._measure(widget, "winfo_vrooty", None),
                self._measure(widget, "winfo_vrootwidth", "winfo_screenwidth"),
                self._measure(widget, "winfo_vrootheight", "winfo_screenheight"))

    def _draw_instruction(self, width, is_pictured):
        """One line, top centre, on a patch of the page so it reads over any
        picture."""
        text = self.INSTRUCTION + ("" if is_pictured else self.NO_PICTURE)
        pad = SPACE[4]
        try:
            line = self.canvas.create_text(max(1, width) // 2, pad, anchor="n",
                                           text=text, fill=theme.TEXT,
                                           font=_font(BASE))
        except Exception as exc:
            events.debug("Region Instruction Failed", str(exc), source=SOURCE,
                         exception=exc)
            return
        try:
            left, top, right, bottom = self.canvas.bbox(line)
            patch = self.canvas.create_rectangle(
                left - pad, top - pad // 2, right + pad, bottom + pad // 2,
                fill=theme.BACKGROUND, outline="")
            self.canvas.tag_raise(line, patch)
        except Exception:
            pass

    def _virtual_desktop(self):
        """The whole virtual desktop, so a region on a second monitor is
        reachable. Falls back to the primary screen. -> (geometry, (w, h))."""
        widget = self.top if self.top is not None else self.master
        width = self._measure(widget, "winfo_vrootwidth", "winfo_screenwidth")
        height = self._measure(widget, "winfo_vrootheight", "winfo_screenheight")
        left = self._measure(widget, "winfo_vrootx", None)
        top = self._measure(widget, "winfo_vrooty", None)
        return f"{width}x{height}+{left}+{top}", (width, height)

    @staticmethod
    def _measure(widget, name, fallback_name):
        for candidate in (name, fallback_name):
            if candidate is None:
                continue
            try:
                value = int(getattr(widget, candidate)())
            except Exception:
                continue
            if value:
                return value
        return 0

    # -- drag --------------------------------------------------------------
    def _on_canvas_press(self, event):
        self._origin = (event.x_root, event.y_root)
        self._press = (event.x, event.y)      # widget coords, for the band
        try:
            self._band = self.canvas.create_rectangle(
                event.x, event.y, event.x, event.y,
                outline=theme.TEXT, width=2)
        except Exception:
            self._band = None

    def _on_canvas_drag(self, event):
        if self._origin is None or self._band is None:
            return
        start_x, start_y = self._press
        try:
            self.canvas.coords(self._band, start_x, start_y, event.x, event.y)
        except Exception:
            pass

    def _on_canvas_release(self, event):
        if self._origin is None:
            return self._finish()
        left, top = self._origin
        width, height = abs(event.x_root - left), abs(event.y_root - top)
        if width < self.MINIMUM_DRAG or height < self.MINIMUM_DRAG:
            self.reason = (f"Region ignored: drag at least {self.MINIMUM_DRAG} "
                           f"px in both directions (got {width}x{height}).")
        else:
            self.region = (min(left, event.x_root), min(top, event.y_root),
                           width, height)
        return self._finish()

    def _on_escape_press(self, _event=None):
        self.reason = "Region selection cancelled."
        return self._finish()

    def _finish(self):
        try:
            self.canvas.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
        return "break"


class _Tooltip:
    """The whole of a value that its cell had to shorten.

    Shown after a short hover, gone on leave or on a click; says nothing when
    the value fitted (`text` is then empty). `above=True` opens it above the
    pointer, for a control at the bottom of the window (the stop), so the
    tip never sits over the control it describes. `bind=False` leaves the
    widget's own Enter/Leave handlers alone; the owner calls `enter`/`leave`.
    """

    DELAY_MS = 450

    def __init__(self, widget, above=False, bind=True):
        self.widget = widget
        self.text = ""
        self.above = above
        self._after_id = None
        self._top = None
        if not bind:
            return
        for sequence, handler in (("<Enter>", self._on_enter),
                                  ("<Leave>", self._on_leave),
                                  ("<ButtonPress>", self._on_leave)):
            try:
                widget.bind(sequence, handler, add="+")
            except Exception:
                pass

    def _on_enter(self, _event=None):
        if not self.text or self._after_id is not None:
            return
        try:
            self._after_id = self.widget.after(self.DELAY_MS, self._show)
        except Exception:
            self._after_id = None

    def _show(self):
        self._after_id = None
        if not self.text or self._top is not None:
            return
        try:
            top = tk.Toplevel(self.widget)
            top.overrideredirect(True)
            label = tk.Label(top, text=self.text, font=_font(SMALL), justify="left",
                             background=theme.BACKGROUND, foreground=theme.TEXT,
                             padx=PAD, pady=GAP, highlightthickness=1,
                             highlightbackground=theme.RULE, wraplength=480)
            label.pack()
            x = self.widget.winfo_pointerx() + SPACE[4]
            y = self.widget.winfo_pointery() + SPACE[5]
            if self.above:
                top.update_idletasks()
                y = self.widget.winfo_pointery() - SPACE[4] - top.winfo_reqheight()
            top.geometry(f"+{x}+{y}")
            self._top = top
        except Exception as exc:
            events.debug("Tooltip Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _on_leave(self, _event=None):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self._top is not None:
            try:
                self._top.destroy()
            except Exception:
                pass
            self._top = None

    enter, leave = _on_enter, _on_leave
    close = _on_leave


#: An input's underline: 1.5 px in the brief; Tk draws whole pixels.
UNDERLINE_PX = 2


class _Ring:
    """The two-pixel focus ring every focusable control wears (F25).

    Aqua draws no highlight ring on a Label and a one-pixel one elsewhere, so
    the ring is two frames: an outer pixel the colour of whatever it sits on,
    and an inner pixel that is the control's resting border. Focused, both
    turn ink - a 2 px ring - and nothing moves, because the ring's pixels
    are always there.

    `underline=True` is an input on the Bench sheet: no box at rest (the
    inner pixel is the ground too) and a muted underline under the field;
    focused, the same 2 px ink ring and an ink underline.
    """

    def __init__(self, parent, background, border=INPUT_BORDER, underline=False,
                 lip=None):
        self.background, self.border = background, border
        self.underline = underline
        self.outer = tk.Frame(parent, background=background,
                              padx=FOCUS_PX - 1, pady=FOCUS_PX - 1)
        # A key (Signature): `lip` names its kind in `theme.KEY_LIP_PX`. The
        # seat is the ground the face drops into; the lip frame shows its
        # colour under the rim (`inner`) by the rim's bottom pad.
        self.lip_kind = lip
        self.seat = self.lip = None
        self.is_down = False
        self.pads = None
        holder = self.outer
        if lip is not None:
            self.seat = tk.Frame(self.outer, background=background)
            self.seat.pack(fill="both", expand=True)
            self.lip = tk.Frame(self.seat, background=theme.KEY_LIP)
            holder = self.lip
        self.inner = tk.Frame(holder, background=background if underline
                              else border, padx=1, pady=1)
        if lip is None:
            self.inner.pack(fill="both", expand=True)
        else:
            self._seat_key()
        self.line = None
        if underline:
            self.line = tk.Frame(self.inner, height=UNDERLINE_PX, background=border)
            self.line.pack(side="bottom", fill="x")
        self.is_focused = False

    def _seat_key(self):
        """Pack the lip and the face at this state's pads."""
        pads = _key_pads(self.lip_kind, self.is_down)
        if pads == self.pads:
            return
        self.pads = pads
        drop, lip = pads
        try:
            self.lip.pack(fill="both", expand=True, pady=(drop, 0))
            self.inner.pack(fill="both", expand=True, pady=(0, lip))
        except Exception as exc:
            events.debug("Key Not Seated", str(exc), source=SOURCE, exception=exc,
                         every=5.0)

    def set_down(self, is_down):
        """Pressed or latched: the lip folds and the face drops (the pads
        move; the key's height does not)."""
        self.is_down = bool(is_down)
        if self.lip is not None:
            self._seat_key()

    def set_lip(self, colour):
        if self.lip is None:
            return
        try:
            if self.lip.cget("background") != colour:
                self.lip.configure(background=colour)
        except Exception:
            pass

    def paint(self, is_focused=None, border=None):
        if is_focused is not None:
            self.is_focused = is_focused
        if border is not None:
            self.border = border
        rest = self.background if self.underline else self.border
        try:
            self.outer.configure(background=FOCUS_INK if self.is_focused
                                 else self.background)
            self.inner.configure(background=FOCUS_INK if self.is_focused else rest)
            if self.line is not None:
                self.line.configure(background=FOCUS_INK if self.is_focused
                                    else self.border)
        except Exception:
            pass


class _Press:
    """A chrome command for the window itself - the rail's Setup and Quit,
    Acknowledge, Show events, a confirmation's Yes and No: a Label drawn as
    a button (`tk.Button` ignores its colours on Aqua), in the ring, with
    hover, focus and Return/Space.

    A key of the family by default (Signature): a CAP face, the KEY_RIM rim
    and the KEY_LIP lip; `ghost` is a text key, no face and no lip (the
    rail's Quit); `set_active(True)` is the key latched down in ink (the
    rail's Setup while Setup is the page shown). Under the pointer the rim
    turns ink; pressed by the pointer, the lip folds until the release."""

    def __init__(self, parent, text, on_press, background, ghost=False):
        self.on_press = on_press
        self.background = background
        self.ghost = ghost
        self.is_active = False
        self.is_pressed = False
        self.ring = _Ring(parent, background,
                          border=background if ghost else theme.KEY_RIM,
                          lip=None if ghost else "key")
        self.fill, self.ink = _key_look("ghost" if ghost else "neutral", background)[:2]
        self.widget = tk.Label(self.ring.inner, text=text, font=_key_font(),
                               background=self.fill, foreground=self.ink,
                               relief="flat", padx=SPACE[4],
                               pady=_command_pady(lip=0 if ghost else _lip_px("key")),
                               borderwidth=0, cursor="hand2", takefocus=1,
                               highlightthickness=0)
        self.widget.pack(fill="both", expand=True)
        self.is_hovered = False
        self.widget.bind("<Button-1>", self._on_pointer_press)
        for sequence in ("<Return>", "<space>"):
            self.widget.bind(sequence, self._on_press)
        self.widget.bind("<ButtonRelease-1>", lambda _e: self._set_pressed(False))
        self.widget.bind("<Enter>", lambda _e: self._hover(True))
        self.widget.bind("<Leave>", lambda _e: self._hover(False))
        self.widget.bind("<FocusIn>", lambda _e: self.ring.paint(True))
        self.widget.bind("<FocusOut>", lambda _e: self.ring.paint(False))
        self._hover(False)

    @property
    def frame(self):
        return self.ring.outer

    def _on_pointer_press(self, event=None):
        self._set_pressed(True)
        return self._on_press(event)

    def _on_press(self, _event=None):
        self.on_press()
        return "break"

    def _set_pressed(self, is_pressed):
        if is_pressed == self.is_pressed:
            return
        self.is_pressed = is_pressed
        self._hover(self.is_hovered)

    def set_active(self, is_active):
        if is_active == self.is_active:
            return
        self.is_active = is_active
        self._hover(self.is_hovered)

    def _hover(self, is_hovered):
        self.is_hovered = is_hovered
        if self.ghost:
            fill, ink, rim, lip = _key_look("ghost", self.background)
            if is_hovered:
                fill = _counter(self.background)
        else:
            fill, ink, rim, lip = _key_look("go" if self.is_active else "neutral",
                                            self.background)
            if is_hovered and not self.is_active:
                rim = theme.TEXT
        try:
            self.widget.configure(background=fill, foreground=ink)
        except Exception:
            pass
        self.ring.set_lip(lip)
        self.ring.set_down(self.is_active or self.is_pressed)
        self.ring.paint(border=rim)

    def set_text(self, text):
        try:
            if self.widget.cget("text") != text:
                self.widget.configure(text=text)
        except Exception:
            pass


class _Switch:
    """The per-model stop (E, tier 3): a small switch, not a second red disc.
    Signature: a sunk track with a KEY_RIM edge and a key-cap knob (a face
    on a lip). Off, the track is the field ground and the knob a CAP cap at
    the left; on (latched), a SIGNAL track and a white knob with a SKIRT rim
    and lip at the right - the one other place the signal colour is spent,
    because it IS the latch. A Canvas: Tk has no switch. Keyboard: focus
    ring in ink, Return and Space press it."""

    def __init__(self, master, on_press, background):
        self.on_press = on_press
        self.background = background
        self.is_on = None
        self.is_focused = False
        width, height = (_design_px(v) for v in theme.SWITCH["track"])
        self.track = (width, height)
        pad = FOCUS_PX + SPACE[0]
        self.canvas = tk.Canvas(master, width=width + 2 * pad, height=height + 2 * pad,
                                background=background, highlightthickness=0,
                                takefocus=1, cursor="hand2")
        for sequence in ("<Button-1>", "<Return>", "<space>"):
            self.canvas.bind(sequence, self._on_press)
        self.canvas.bind("<FocusIn>", lambda _e: self._focus(True))
        self.canvas.bind("<FocusOut>", lambda _e: self._focus(False))
        self.draw()

    def _on_press(self, _event=None):
        self.on_press()
        return "break"

    def _focus(self, is_focused):
        self.is_focused = is_focused
        self.draw()

    def set_on(self, is_on):
        is_on = bool(is_on)
        if is_on == self.is_on:
            return
        self.is_on = is_on
        self.draw()

    def draw(self):
        canvas = self.canvas
        try:
            canvas.delete("all")
        except Exception:
            return
        width, height = self.track
        pad = FOCUS_PX + SPACE[0]
        x0, y0, x1, y1 = pad, pad, pad + width, pad + height
        on = bool(self.is_on)
        fill = theme.SWITCH["on_fill"] if on else _field_ground(self.background)
        edge = theme.SKIRT if on else theme.SWITCH["off_edge"]
        knob = _design_px(theme.SWITCH["knob"])
        inset = max(1, (height - knob) // 2)
        face = theme.SWITCH["knob_on"] if on else theme.SWITCH["knob_off"]
        rim = theme.SKIRT if on else theme.KEY_RIM
        lip_colour = theme.SWITCH["knob_lip_on"] if on else theme.SWITCH["knob_lip"]
        lip = _lip_px("knob")
        try:
            if self.is_focused:
                canvas.create_rectangle(x0 - FOCUS_PX, y0 - FOCUS_PX, x1 + FOCUS_PX,
                                        y1 + FOCUS_PX, outline=FOCUS_INK,
                                        width=FOCUS_PX, tags=("focus",))
            canvas.create_rectangle(x0, y0, x1 - 1, y1 - 1, fill=fill, outline=edge,
                                    tags=("track",))
            kx0 = x1 - inset - knob if on else x0 + inset
            ky0 = y0 + inset
            # The knob is a key cap: its lip, then its face on it.
            canvas.create_rectangle(kx0, ky0, kx0 + knob - 1, ky0 + knob - 1,
                                    fill=lip_colour, outline=rim, tags=("knob-lip",))
            canvas.create_rectangle(kx0, ky0, kx0 + knob - 1, ky0 + knob - 1 - lip,
                                    fill=face, outline=rim, tags=("knob",))
        except Exception as exc:
            events.debug("Switch Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)


class _Disclosure:
    """The one press that shows a model's next tier (E): a small key of the
    family holding the disclosure glyph, then the section's `disclosure`
    words in ink (Signature). Open, the key sinks (a SURFACE face on a
    folded lip) and the glyph turns down. Keyboard: the 2 px ink ring,
    Return and Space."""

    def __init__(self, parent, text, on_toggle, background):
        self.text = text
        self.on_toggle = on_toggle
        self.is_open = False
        self.background = background
        self.draws = 0
        self.ring = _Ring(parent, background, border=background)
        size = self.size = _design_px(SPACE[7])      # the 24 px disclosure key
        self.key = tk.Canvas(self.ring.inner, width=size, height=size,
                             background=background, highlightthickness=0,
                             cursor="hand2")
        self.key.pack(side="left", padx=(SPACE[1], 0))
        self.widget = tk.Label(self.ring.inner, text=self._face(), font=_font(bold=True),
                               background=background, foreground=theme.TEXT,
                               relief="flat", padx=SPACE[3], pady=_target_pady(),
                               cursor="hand2", takefocus=1, highlightthickness=0)
        self.widget.pack(side="left", fill="both", expand=True)
        for sequence in ("<Button-1>", "<Return>", "<space>"):
            self.widget.bind(sequence, self._on_press)
        self.key.bind("<Button-1>", self._on_press)
        self.widget.bind("<FocusIn>", lambda _e: self.ring.paint(True))
        self.widget.bind("<FocusOut>", lambda _e: self.ring.paint(False))
        self.draw()

    @property
    def frame(self):
        return self.ring.outer

    def _face(self):
        return self.text

    def _on_press(self, _event=None):
        self.on_toggle(not self.is_open)
        return "break"

    def set_open(self, is_open):
        is_open = bool(is_open)
        if is_open == self.is_open:
            return
        self.is_open = is_open
        self.draw()

    def draw(self):
        """The 24 px key: its lip, its face on it (up: CAP on the 3 px lip;
        open: SURFACE, sunk onto a 1 px lip), the glyph (turned down when
        open)."""
        canvas, size = self.key, self.size
        drop, lip = _key_pads("small", self.is_open)
        face = theme.SURFACE if self.is_open else theme.CAP
        glyph = _design_px(GLYPH_PX)
        self.draws += 1
        try:
            canvas.delete("all")
            canvas.create_rectangle(0, drop, size - 1, size - 1, fill=theme.KEY_LIP,
                                    outline=theme.KEY_RIM, tags=("lip",))
            canvas.create_rectangle(0, drop, size - 1, size - 1 - lip, fill=face,
                                    outline=theme.KEY_RIM, tags=("face",))
            top = drop + (size - lip - drop - glyph) / 2
            _draw_glyph(canvas, "disclosure", (size - glyph) / 2, top, glyph,
                        theme.TEXT, turn=90 if self.is_open else 0)
        except Exception as exc:
            events.debug("Disclosure Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)


class _ConfirmDialog:
    """A yes/no question that never takes the stop away.

    `messagebox.askyesno` is application-modal and answered Yes to Return,
    so the latch-clear confirmation both blocked the stop while it was up and
    released the latch on a reflex keystroke (F1, F17). This window takes no
    grab - the stop disc and its shortcut work while it is open - and its
    default is No: focus starts on No, Return answers No unless the operator
    has moved to Yes, Escape and the window's close button answer No.
    """

    #: One question at a time: a second one while the first is open (a
    #: stop press re-entering the clear path) is declined, not stacked.
    is_open = False

    def __init__(self, master, prompt, yes_text="Yes", no_text="No",
                 title=CONFIRM_TITLE):
        self.master = master
        self.prompt = str(prompt or "")
        self.yes_text, self.no_text = yes_text, no_text
        self.title = title or CONFIRM_TITLE
        self.answer = False
        self.top = self.yes = self.no = None

    def ask(self):
        if _ConfirmDialog.is_open:
            events.debug("Confirm Declined", "another question is open",
                         source=SOURCE)
            return False
        _ConfirmDialog.is_open = True
        try:
            self._build()
            try:
                self.master.wait_window(self.top)
            except Exception as exc:
                events.debug("Confirm Wait Failed", str(exc), source=SOURCE,
                             exception=exc)
        finally:
            _ConfirmDialog.is_open = False
        return self.answer

    def _build(self):
        top = self.top = tk.Toplevel(self.master)
        for call in (lambda: top.title(self.title),
                     lambda: top.transient(self.master.winfo_toplevel()),
                     lambda: top.resizable(False, False)):
            try:
                call()
            except Exception:
                pass
        top.configure(background=theme.SURFACE)
        tk.Label(top, text=self.prompt, font=_font(), justify="left", anchor="w",
                 wraplength=420, background=theme.SURFACE, foreground=theme.TEXT,
                 padx=SPACE[5], pady=SPACE[5]).pack(fill="x")
        row = tk.Frame(top, background=theme.SURFACE)
        row.pack(fill="x", padx=SPACE[5], pady=(0, SPACE[5]))
        self.no = _Press(row, self.no_text, lambda: self._answer(False),
                         theme.SURFACE)
        self.no.frame.pack(side="right")
        self.yes = _Press(row, self.yes_text, lambda: self._answer(True),
                          theme.SURFACE)
        self.yes.frame.pack(side="right", padx=(0, SPACE[3]))
        top.bind("<Return>", lambda _e: self._answer(False))
        top.bind("<Escape>", lambda _e: self._answer(False))
        try:
            top.protocol("WM_DELETE_WINDOW", lambda: self._answer(False))
        except Exception:
            pass
        self._centre()
        try:
            self.no.widget.focus_set()
        except Exception:
            pass

    def _centre(self):
        try:
            self.top.update_idletasks()
            owner = self.master.winfo_toplevel()
            x = owner.winfo_rootx() + (owner.winfo_width() - self.top.winfo_reqwidth()) // 2
            y = owner.winfo_rooty() + (owner.winfo_height() - self.top.winfo_reqheight()) // 3
            self.top.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _answer(self, value):
        self.answer = bool(value)
        try:
            self.top.destroy()
        except Exception:
            pass
        return "break"


class _AckDialog:
    """An event that wants acknowledging, as a window like the latch-release
    question (rb-ack, owner 2026-09-28: "proper popups ... similar to the
    popup for the latch release").

    Built from `_ConfirmDialog` and held to the same rules: no grab and no
    `wait_window` (the disc and Ctrl+. work while it is open; this window
    does not even block the caller), the event's title as the window title
    and as its heading, its message as the body in the confirm dialog's type
    and wrap. ONE key, Understood, holds the focus; Return, Escape and the
    window's close button all answer it. The dashboard owns the queue: this
    shows one event at a time and `update` redraws it in place when the
    same title repeats.
    """

    def __init__(self, master, event, on_understood, repeats=1, waiting=0):
        self.master = master
        self.on_understood = on_understood
        self.event = event
        self.top = self.key = self.title_label = self.body = self.count = None
        self._build(repeats, waiting)

    @staticmethod
    def title_of(event):
        """The title in sentence case, as every line says it (L11)."""
        return event_line({"title": getattr(event, "title", "") or "Notice"})

    @staticmethod
    def body_of(event, repeats):
        message = str(getattr(event, "message", "") or "").strip()
        return f"{message} (x{repeats})" if repeats > 1 else message

    def _build(self, repeats, waiting):
        top = self.top = tk.Toplevel(self.master)
        title = self.title_of(self.event)
        for call in (lambda: top.title(title),
                     lambda: top.transient(self.master.winfo_toplevel()),
                     lambda: top.resizable(False, False)):
            try:
                call()
            except Exception:
                pass
        top.configure(background=theme.SURFACE)
        self.title_label = tk.Label(
            top, text=title, font=_font(bold=True), justify="left", anchor="w",
            wraplength=420, background=theme.SURFACE, foreground=theme.TEXT,
            padx=SPACE[5], pady=0)
        self.title_label.pack(fill="x", pady=(SPACE[5], 0))
        self.body = tk.Label(top, text=self.body_of(self.event, repeats),
                             font=_font(), justify="left", anchor="w",
                             wraplength=420, background=theme.SURFACE,
                             foreground=theme.TEXT, padx=SPACE[5],
                             pady=SPACE[3])
        self.body.pack(fill="x")
        row = tk.Frame(top, background=theme.SURFACE)
        row.pack(fill="x", padx=SPACE[5], pady=(SPACE[2], SPACE[5]))
        self.count = tk.Label(row, text="", font=_font(), anchor="w",
                              background=theme.SURFACE, foreground=theme.MUTED)
        self.count.pack(side="left")
        self.key = _Press(row, "Understood", self._understood, theme.SURFACE)
        self.key.frame.pack(side="right")
        top.bind("<Return>", lambda _e: self._understood())
        top.bind("<Escape>", lambda _e: self._understood())
        try:
            top.protocol("WM_DELETE_WINDOW", self._understood)
        except Exception:
            pass
        self.set_waiting(waiting)
        _ConfirmDialog._centre(self)
        try:
            self.key.widget.focus_set()
        except Exception:
            pass

    def update(self, event, repeats, waiting):
        """The same title again: new words, a count, the same window."""
        self.event = event
        try:
            self.body.configure(text=self.body_of(event, repeats))
        except Exception:
            pass
        self.set_waiting(waiting)

    def set_waiting(self, waiting):
        try:
            self.count.configure(text=f"{waiting} more waiting" if waiting else "")
        except Exception:
            pass

    def _understood(self):
        self.on_understood(self)
        return "break"

    def close(self):
        try:
            self.top.destroy()
        except Exception:
            pass


def _confirm(master, prompt, title=CONFIRM_TITLE, yes_text="Yes", no_text="No"):
    """The one confirmation both the dashboard and a panel ask. -> bool.
    The dashboard's own questions are titled and name their answers
    (`CLEAR_DIALOG`, `QUIT_DIALOG`)."""
    return _ConfirmDialog(master, prompt, yes_text=yes_text, no_text=no_text,
                          title=title).ask()


class _Mushroom:
    """The stop object (Signature, 2026-09-27): a red key in an ink guard
    collar, with a pale socket band between them - from across a room, a
    bullseye. The legend is white numerals: `Stop`, or the release glyph
    over `Clear` once EVERY model is latched.

    ALWAYS red: it does not go quiet while nothing moves, and it is never
    dimmed. Idle, the key stands `STOP["lift"]` px above centre with its
    SKIRT showing `STOP["skirt"]` px below it. Latched, the key is down
    (`drop_latched` px lower, no skirt, a darker crescent across the top of
    its face), the socket band floods SKIRT and the collar turns SIGNAL:
    one solid red coin. It breathes ONCE, on the edge, not for as long as
    the latch stays closed. A Canvas, because a round control is the one
    shape Tk's widgets do not have, and because `tk.Button` ignores its
    colours on Aqua anyway.

    Its diameter comes from its face font (F7): the face is the numeral
    face at `STOP["face_pt"]` design px, scaled with `--font-size`, and the
    object grows until "Clear" and its glyph fit on the key. It is redrawn
    only when something it shows changes (F21).
    """

    def __init__(self, master, on_press, background, narrow=False):
        floor = STOP_DIAMETER_NARROW if narrow else STOP_DIAMETER
        self.narrow = bool(narrow)
        self.face_font = _numeral_font(
            theme.STOP["face_pt_narrow" if narrow else "face_pt"])
        self.diameter = self.diameter_for(self.face_font, floor, narrow)
        self.on_press = on_press
        self.background = background
        self.face = STOP_FACE
        self.is_latched = None           # unknown until the first sync
        self.scale = 1.0
        self.is_hovered = self.is_focused = False
        self._pulse_ids = []
        self.draws = 0
        # Room around the object for the pulse (7 %) and the focus ring
        # (SPACE[1] out, FOCUS_PX wide) on every side.
        self.size = int(self.diameter * 1.08) + 2 * (SPACE[1] + FOCUS_PX + 1)
        self.canvas = tk.Canvas(master, width=self.size, height=self.size,
                                background=background, highlightthickness=0,
                                takefocus=1, cursor="hand2")
        self.tooltip = _Tooltip(self.canvas, above=False, bind=False)
        bindings = (("<Button-1>", self._on_press), ("<Return>", self._on_press),
                    ("<KP_Enter>", self._on_press), ("<space>", self._on_press),
                    ("<Enter>", lambda _e: self._set_flag("is_hovered", True)),
                    ("<Leave>", lambda _e: self._set_flag("is_hovered", False)),
                    ("<FocusIn>", lambda _e: self._set_flag("is_focused", True)),
                    ("<FocusOut>", lambda _e: self._set_flag("is_focused", False)))
        for sequence, handler in bindings:
            self.canvas.bind(sequence, handler)
        self.draw()

    # -- size --------------------------------------------------------------
    @staticmethod
    def key_ratio(narrow=False):
        """The key's share of the whole object (124 of 172; 106 of 150)."""
        if narrow:
            return theme.STOP["key_narrow"] / theme.STOP["diameter_narrow"]
        return theme.STOP["key"] / theme.STOP["diameter"]

    @classmethod
    def ring_width(cls, diameter=None, narrow=False):
        """How far the key's face sits inside the object's edge: the
        collar and the socket band."""
        diameter = STOP_DIAMETER if diameter is None else diameter
        return (diameter - diameter * cls.key_ratio(narrow)) / 2

    @staticmethod
    def glyph_px(font):
        """The release glyph over "Clear": a little smaller than the word."""
        return max(8, round(abs(font[1]) * 0.8))

    @classmethod
    def diameter_for(cls, font, floor, narrow=False):
        """The smallest object, from `floor` up, whose key holds its face -
        "Clear" and the glyph over it, with their line height - with room
        to spare."""
        half_width = max(_width_px(font, CLEAR_FACE), _width_px(font, STOP_FACE)) / 2
        half_height = (abs(font[1]) * 0.9 + cls.glyph_px(font)) / 2
        corner = math.hypot(half_width, half_height) + SPACE[0]
        diameter = int(floor)
        while corner > diameter * cls.key_ratio(narrow) / 2 - SPACE[0]:
            diameter += 1
        return diameter

    # -- input -------------------------------------------------------------
    def _on_press(self, _event=None):
        self.tooltip.leave()
        try:
            self.canvas.focus_set()
        except Exception:
            pass
        self.on_press()
        return "break"

    def _set_flag(self, name, value):
        """Hover and keyboard focus: the collar thickens a pixel under the
        pointer and a focus ring in ink shows where Return / Space land."""
        if name == "is_hovered":
            (self.tooltip.enter if value else self.tooltip.leave)()
        if getattr(self, name) == value:
            return
        setattr(self, name, value)
        self.draw()

    # -- state -------------------------------------------------------------
    def set_latched(self, is_latched):
        """Face, key and collar follow the latch; the pulse answers its
        closing. Nothing is drawn when nothing changed."""
        is_latched = bool(is_latched)
        was = self.is_latched
        if was == is_latched:
            return
        self.is_latched = is_latched
        self.face = CLEAR_FACE if is_latched else STOP_FACE
        if is_latched and was is False:
            self.pulse()
        elif not is_latched:
            self.cancel()
            self.scale = 1.0
        self.draw()

    @property
    def ring(self):
        """The collar's width now (a pixel more under the pointer). Latched
        it is the same width in SIGNAL: the "thicker ring" of the rule is
        now the collar turning red."""
        key = "collar_narrow" if self.narrow else "collar"
        return _design_px(theme.STOP[key]) + (1 if self.is_hovered else 0)

    def pulse(self):
        self.cancel()
        if _motion_reduced():
            return
        for index, scale in enumerate(PULSE_FRAMES):
            try:
                self._pulse_ids.append(self.canvas.after(
                    PULSE_FRAME_MS * (index + 1),
                    lambda s=scale: self._pulse_frame(s)))
            except Exception:
                break

    def _pulse_frame(self, scale):
        if scale == self.scale:
            return
        self.scale = scale
        self.draw()

    def cancel(self):
        for token in self._pulse_ids:
            try:
                self.canvas.after_cancel(token)
            except Exception:
                pass
        self._pulse_ids = []

    # -- drawing -----------------------------------------------------------
    def geometry(self):
        """Where the parts go now: centre, the collar's outer radius, the
        socket's radius, the key's radius and the face's centre height."""
        stop = theme.STOP
        centre = self.size / 2
        radius = self.diameter / 2 * self.scale
        socket = radius - self.ring
        key = radius * self.key_ratio(self.narrow)
        face_y = centre - _design_px(stop["lift"])
        if self.is_latched:
            face_y += _design_px(stop["drop_latched"])
        return centre, radius, socket, key, face_y

    def draw(self):
        canvas = self.canvas
        try:
            canvas.delete("all")
        except Exception:
            return
        self.draws += 1
        stop = theme.STOP
        latched = bool(self.is_latched)
        centre, radius, socket, key, face_y = self.geometry()
        legend = stop["legend"]
        try:
            if self.is_focused:
                reach = radius + SPACE[1]
                canvas.create_oval(centre - reach, centre - reach,
                                   centre + reach, centre + reach,
                                   outline=theme.STOP_FOCUS, width=FOCUS_PX,
                                   tags=("focus",))
            collar = stop["collar_latched"] if latched else stop["collar_fill"]
            canvas.create_oval(centre - radius, centre - radius, centre + radius,
                               centre + radius, fill=collar,
                               outline=stop["collar_edge"], tags=("collar",))
            band = stop["socket_latched"] if latched else stop["socket"]
            canvas.create_oval(centre - socket, centre - socket, centre + socket,
                               centre + socket, fill=band, outline=band,
                               tags=("socket",))
            left, right = centre - key, centre + key
            if latched:
                # Down: no skirt; a darker crescent across the top of the
                # face (the face's own oval, the red drawn a fold lower).
                fold = _lip_px("key") - _lip_px("pressed")
                canvas.create_oval(left, face_y - key, right, face_y + key,
                                   fill=stop["skirt_fill"],
                                   outline=stop["skirt_fill"], tags=("crescent",))
                canvas.create_oval(left, face_y - key + fold, right, face_y + key,
                                   fill=stop["face"], outline=stop["face"],
                                   tags=("face",))
                glyph = self.glyph_px(self.face_font)
                word_px = abs(self.face_font[1])
                top = face_y - (glyph + word_px * 0.9) / 2
                _draw_glyph(canvas, "clear", centre - glyph / 2, top, glyph, legend,
                            tags=("release",))
                canvas.create_text(centre, top + glyph + word_px * 0.45,
                                   text=self.face, fill=legend, font=self.face_font,
                                   tags=("legend",))
            else:
                skirt = _design_px(stop["skirt"])
                canvas.create_oval(left, face_y - key + skirt, right, face_y + key + skirt,
                                   fill=stop["skirt_fill"],
                                   outline=stop["skirt_fill"], tags=("skirt",))
                canvas.create_oval(left, face_y - key, right, face_y + key,
                                   fill=stop["face"], outline=stop["face"],
                                   tags=("face",))
                canvas.create_text(centre, face_y, text=self.face, fill=legend,
                                   font=self.face_font, tags=("legend",))
        except Exception as exc:
            events.debug("Stop Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)


#: What a device class is called in a sentence to the operator.
DEVICE_WORDS = {"SerialPort": "serial port", "Gamepad": "gamepad",
                "SMC100": "SMC100 controller", "Screen": "screen capture"}


def _device_list(names):
    words = [DEVICE_WORDS.get(name, name) for name in names]
    return ", ".join(words) or "a device"


#: Which tiers each model had open, for the session: (model, tier) -> bool.
#: A model closed and reopened, or an entry rebuilt, opens as it was left.
_DISCLOSED = {}


class TkPanelView(PanelView):
    """One model's entry on the Bench sheet, rendered from a schema.

    `PanelView.__init__` refuses to construct unless every element type in
    `schema.ELEMENT_TYPES` has a `_make_<type>` here, so a schema element this
    renderer cannot draw is a construction-time failure rather than a silent
    blank in one frontend.

    An entry is a 2 px ink rule, the model's name, then its tier-1 body; its
    tier-2 sections sit in ONE panel-toned well behind a disclosure, and its
    tier-3 sections in a second disclosure ("Diagnostics") inside the well
    (E, 2026-09-25). Every tier is BUILT at once - a command's inputs travel
    whatever is shown (MOD-6) - and a closed tier is simply not
    mapped, nor polled for data.

    `sheet` is the dashboard's scrolling sheet when the entry lives on it;
    without one (Setup's page, a test) the view scrolls its own body.

    `host` is another entry's view when this model is drawn on that model's
    page (`Model.HOST`, owner ruling 2026-09-28: Red Percent on the Transfer
    Map, one dashboard). The view is still this model's own - every element
    binds to its name - but its head and tier 1 are a GROUP in the host's
    body after the host's tier 1, and its disclosure and well follow the
    host's well. Its head is its name one step down and its mode word.
    """

    #: How often the dropdown option lists are re-read. Options can be
    #: expensive (a port scan), so they do not ride the 100 ms refresh.
    OPTIONS_REFRESH_MS = 2000
    #: How often an `image` element's data command may run. Producing one is
    #: a figure render, which must not happen ten times a second.
    IMAGE_REFRESH_MS = 1000
    #: The wheel, per platform: `<MouseWheel>` on Aqua and Win32, buttons 4
    #: and 5 on X11.
    WHEEL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")

    def __init__(self, master, controller, name, panel=None, sheet=None, host=None):
        super().__init__(controller, name, panel)
        self._host_view = host          # the entry this one is drawn on, or None
        self._hosted = {}               # name -> the views drawn on this entry
        self._group_slot = None         # in `_body`, after tier 1: the groups
        self._tail_slot = None          # in `_body`, after the well: their wells
        self._is_closed = False
        if host is not None:
            master = host._open_slots()
        self._widgets = {}          # id(element) -> {widget, var, ...}
        self._grid = {}             # id(container) -> the grid cursor
        self._flows = {}            # id(flow strip) -> its wrapping lines
        self._table = None          # the frame the current run of row sections shares
        self._row_kinds = []        # per schema section: "table" | "bar" | None
        self._section_kinds = []    # per schema section: "axes" | None
        self._table_columns = {}    # a table's shared caption -> its grid column
        self._section_index = 0
        self._is_scrollbar_shown = True
        self._section_titles = []
        self._after_id = None
        self._options_due = 0
        self._is_stale = None
        self._slow_commands = set()     # data commands worth caching
        self._cached_results = {}       # command -> (monotonic, Result)
        self._acting = None             # the element whose command is running
        self._notice = None             # the refusal line, at its control
        self._notice_text = ""
        self._last_state = {}
        self.lost_devices = ()          # device names whose link is lost
        self._is_paused = False
        self._sheet = sheet
        self._is_opened = sheet is None     # a page of its own is the one opened
        self._is_unconfirmed = False
        self._fault = ""                # the model's fault reason (O4)
        self._tiers = {}                # tier -> the frame its sections go in
        self._tier_text = {}            # tier -> its disclosure's words
        self._tier_count = {}           # tier -> how many sections it holds
        self._disclosures = {}          # tier -> _Disclosure
        self._well = self._diagnostics = None
        self._well_holder = None        # what is packed to show tier 2
        self._well_canvas = self._well_window = None
        self._is_well_mapped = False    # the tier-2 well is packed now
        self._building_tier = 1
        self._readings = []             # (element, base kind) - re-sized by prominence
        self._why_labels = {}           # id(section container) -> its reason caption
        self._inset = 0 if sheet is not None else INSET

        self.frame = tk.Frame(master, background=_page())
        if host is not None:
            self.frame.pack(side="top", fill="x")
            # Its disclosure and well sit in the host's tail slot, after the
            # host's own well; the anchor keeps the disclosure first there.
            self._foot = tk.Frame(host._tail_slot, background=_page())
            self._foot.pack(side="top", fill="both", expand=True)
            self._foot_anchor = tk.Frame(self._foot, background=_page(), height=0)
            self._foot_anchor.pack(side="top", fill="x")
            host._hosted[name] = self
        else:
            self._foot = self._foot_anchor = None
        # The entry's head: a 2 px ink rule (signal while a link is lost or
        # a stop did not confirm), then the model's name. The tier-2
        # disclosure is not here: it sits at the foot of the body (K3).
        self._rule = tk.Frame(self.frame, height=theme.RULE_STRONG_PX,
                              background=theme.RULE_STRONG)
        self._rule.pack(fill="x", padx=self._inset, pady=(self._inset, SPACE[4]))
        # On the overview the head is the press that opens the device page
        # (K4): the name, and "Open" with a chevron at its right; it wears
        # the focus ring and takes Return and Space. On the device page it
        # is only the name - the rail is the navigation.
        self.on_open = None             # the dashboard's "show this model"
        self._head_ring = _Ring(self.frame, _page(), border=_page())
        self._head_ring.outer.pack(fill="x", padx=self._inset, pady=(0, GAP))
        self._head = tk.Frame(self._head_ring.inner, background=_page(),
                              takefocus=0, highlightthickness=0)
        self._head.pack(fill="both", expand=True)
        self._title = tk.Label(self._head, text=name, font=self._name_font(),
                               anchor="w", background=_page(),
                               foreground=theme.TEXT)
        self._title.pack(side="left")
        # A hosted group's head says its mode beside its name, in the
        # caption's muted ink, as its state word.
        self._state_word = None
        if host is not None:
            self._state_word = tk.Label(self._head, text="", font=_caption_font(),
                                        anchor="w", background=_page(),
                                        foreground=theme.MUTED)
            self._state_word.pack(side="left", padx=(SPACE[3], 0))
        self._head_right = tk.Frame(self._head, background=_page())
        self._head_right.pack(side="right")
        self._open_label = tk.Label(self._head_right, text=f"{CHEVRON[False]} {OPEN_WORD}",
                                    font=_font(bold=True), background=_page(),
                                    foreground=theme.TEXT, padx=SPACE[2],
                                    cursor="hand2", highlightthickness=0)
        self._open_tip = _Tooltip(self._head)
        for widget in (self._head, self._title, self._head_right, self._open_label):
            widget.bind("<Button-1>", self._on_head_pressed)
        for sequence in ("<Return>", "<space>"):
            self._head.bind(sequence, self._on_head_pressed)
        self._head.bind("<FocusIn>", lambda _e: self._paint_head_ring(True))
        self._head.bind("<FocusOut>", lambda _e: self._paint_head_ring(False))
        # "Stop not confirmed. Treat as live." (or, for a failed disable,
        # "Disable failed. Treat as live." and the fault's reason under it,
        # O4) - on its own line under the head, packed only while it is so
        # (never squeezed beside the name).
        self._mark_row = tk.Frame(self.frame, background=_page())
        # The tripped-flag window (Signature, rule 4): drawn once per
        # episode, when the hazard begins.
        flag_w, flag_h = (_design_px(v) for v in theme.FLAG["size"])
        self._mark = tk.Canvas(self._mark_row, width=flag_w, height=flag_h,
                               background=_page(), highlightthickness=0)
        self._mark.pack(side="left", anchor="n", padx=(0, SPACE[3]), pady=SPACE[0])
        self._flag_draws = 0
        mark_words = tk.Frame(self._mark_row, background=_page())
        mark_words.pack(side="left", fill="x", expand=True)
        self._mark_text = tk.Label(mark_words, text=UNCONFIRMED_LINE,
                                   font=_font(bold=True), anchor="w",
                                   background=_page(), foreground=theme.TEXT)
        self._mark_text.pack(side="top", anchor="w")
        self._mark_reason = tk.Label(mark_words, text="", font=_font(), anchor="w",
                                     justify="left", wraplength=640,
                                     background=_page(), foreground=theme.TEXT)
        # What is wrong with the entry as a whole (a lost link), in ink under
        # the head; packed only while there is something to say.
        self._health = tk.Label(self.frame, text="", anchor="w", justify="left",
                                font=_font(), wraplength=640,
                                background=_page(), foreground=theme.TEXT)
        # A refusal with no control to sit under (a region picker's reason
        # lands at its control; this is the fallback). It wraps: a 300-
        # character refusal lost its recovery clause to the window edge.
        self._status = tk.Label(self.frame, text="", anchor="w", justify="left",
                                font=_font(), wraplength=640,
                                background=_page(), foreground=theme.TEXT)
        if sheet is None:
            self._build_scroll_area()
        else:
            self._build_entry_body()

        self._build()
        self.on_close = None            # the dashboard's "close this model"
        self._close_press = None
        if sheet is not None:
            self._build_well_foot()
        for tier in (2, 3):
            if tier in self._tiers:
                self._set_tier_open(tier, _DISCLOSED.get((self.name, tier), False),
                                    refresh=False)
        self._apply_page()
        self._schedule_refresh()
        events.debug("Panel Built", f"{name}: {len(self._elements)} elements",
                     source=SOURCE)

    # -- the entry's body ----------------------------------------------------
    def _build_entry_body(self):
        """On the sheet the entry does not scroll by itself: the sheet does.
        Its body is a plain frame; `_canvas` is the sheet's, so a refusal
        below the fold is scrolled into view on the sheet."""
        self._area = None
        self._scrollbar = None
        self._body_window = None
        self._canvas = getattr(self._sheet, "canvas", None)
        self._pinned = tk.Frame(self.frame, background=_page())
        self._body = tk.Frame(self.frame, background=_page())
        self._body.pack(side="top", fill="both", expand=True)

    def _name_font(self):
        """The opened model's name is a step louder than a closed one's; a
        hosted group's is one step down from its host's."""
        if self._host_view is not None:
            return _font(STEP_1, bold=True)
        return _font(STEP_2 if self._is_opened else STEP_1, bold=True)

    # -- a hosted model's group (Model.HOST) ------------------------------------
    def _open_slots(self):
        """The two slots a hosted view is drawn in, made on the first one:
        the group slot after tier 1 (raised above tier 1 in the stacking
        order, so Tab reaches it after tier 1 and before the disclosure),
        and the tail slot after the well. -> the group slot"""
        if self._group_slot is None:
            self._tier_frame(1)
            self._group_slot = tk.Frame(self._body, background=_page())
            self._tail_slot = tk.Frame(self._body, background=_page())
            try:
                self._group_slot.lift(self._tiers[1])
            except Exception as exc:
                events.debug("Group Not Ordered", f"{self.name}: {exc}",
                             source=SOURCE, exception=exc)
        return self._group_slot

    def _foot_parent(self):
        """Where the tier-2 disclosure and the well are made: the body, or
        for a hosted view its own foot in the host's tail slot."""
        return self._foot if self._foot is not None else self._body

    def _detach(self, name):
        """A hosted view closed: its group and its well leave this page."""
        self._hosted.pop(name, None)
        self._seat_group()
        self._seat_tail()

    def _seat_group(self):
        """The groups, after tier 1, on the device page only."""
        slot = self._group_slot
        if slot is None:
            return
        try:
            if self._hosted and self._is_opened:
                slot.pack(side="top", fill="x", padx=self._inset,
                          pady=(SPACE[6], 0), after=self._tiers[1])
            else:
                slot.pack_forget()
        except Exception as exc:
            events.debug("Group Not Shown", f"{self.name}: {exc}", source=SOURCE,
                         exception=exc)

    def _seat_tail(self):
        """The groups' disclosures and wells, after this entry's well; the
        slot takes the page's height only while one of their wells is shown."""
        slot = self._tail_slot
        if slot is None:
            return
        try:
            if not (self._hosted and self._is_opened):
                slot.pack_forget()
                return
            after = (self._well_holder if self._is_well_mapped
                     else self._disclosures[2].frame if 2 in self._disclosures
                     else self._group_slot)
            is_tall = any(view.well_canvas is not None for view in self._hosted.values())
            slot.pack(side="top", fill="both" if is_tall else "x", expand=is_tall,
                      padx=self._inset, after=after)
        except Exception as exc:
            events.debug("Group Well Not Shown", f"{self.name}: {exc}",
                         source=SOURCE, exception=exc)

    @property
    def hosted_views(self):
        """The views drawn on this entry, in the order they joined."""
        return list(self._hosted.values())

    # -- the scroll area ---------------------------------------------------
    def _build_scroll_area(self):
        """The panel's controls live on a scrolling canvas.

        A panel taller than the window used to make the *window* taller, and
        the window is not allowed to grow. A tall panel scrolls; the rail and
        its stop do not move. The scrollbar shows only while there is
        something to scroll to.
        """
        area = tk.Frame(self.frame, background=_page())
        area.pack(side="top", fill="both", expand=True)
        self._area = area
        # The panel's commit row (Setup's Launch), when it has one, is pinned
        # here under the scroll area: packed only once a row is pinned.
        self._pinned = tk.Frame(self.frame, background=_page())
        self._scrollbar = ttk.Scrollbar(area, orient="vertical")
        self._scrollbar.pack(side="right", fill="y")
        self._canvas = tk.Canvas(area, background=_page(),
                                 highlightthickness=0,
                                 yscrollcommand=self._scrollbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._body = tk.Frame(self._canvas, background=_page())
        try:
            self._scrollbar.configure(command=self._canvas.yview)
            self._body_window = self._canvas.create_window(
                (0, 0), window=self._body, anchor="nw")
        except Exception as exc:
            self._body_window = None
            events.debug("Scroll Area Not Wired", str(exc), source=SOURCE,
                         exception=exc)
        self._body.bind("<Configure>", self._on_body_resized)
        self._canvas.bind("<Configure>", self._on_canvas_resized)
        # The wheel is bound application-wide only while the pointer is over
        # this panel, so two open panels never scroll each other.
        self.frame.bind("<Enter>", self._on_pointer_enter)
        self.frame.bind("<Leave>", self._on_pointer_leave)

    def _on_body_resized(self, _event=None):
        """The scrollable extent is whatever the controls actually occupy."""
        try:
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        except Exception as exc:
            events.debug("Scrollregion Not Set", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        self._sync_scrollbar()

    def _on_canvas_resized(self, event=None):
        """Keep the body as wide as the viewport: a canvas window is sized to
        its content otherwise, and a table would stop at its widest row
        instead of reaching the window's edge."""
        width = getattr(event, "width", 0)
        if self._body_window is None or not width:
            return
        try:
            self._canvas.itemconfigure(self._body_window, width=width)
        except Exception as exc:
            events.debug("Body Width Not Set", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        self._sync_scrollbar()
        self._wrap_to(width)

    def _wrap_to(self, width):
        """Long lines wrap to the panel instead of running off its edge."""
        room = max(SPACE[6] * 8, width - 2 * SPACE[6])
        for label in (self._status, self._health, self._notice):
            if label is None:
                continue
            try:
                label.configure(wraplength=room)
            except Exception:
                pass

    def _sync_scrollbar(self):
        """A scrollbar only when the controls are taller than the viewport; a
        bare trough beside a panel that fits is chrome with nothing to do."""
        if self._scrollbar is None:
            return
        try:
            content = self._body.winfo_reqheight()
            viewport = self._canvas.winfo_height()
        except Exception:
            return
        if not (isinstance(content, int) and isinstance(viewport, int)) or viewport <= 1:
            return
        needed = content > viewport
        if needed == self._is_scrollbar_shown:
            return
        self._is_scrollbar_shown = needed
        try:
            if needed:
                self._scrollbar.pack(side="right", fill="y", before=self._canvas)
            else:
                self._scrollbar.pack_forget()
                self._canvas.yview_moveto(0)
        except Exception as exc:
            events.debug("Scrollbar Not Synced", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _on_pointer_enter(self, _event=None):
        for sequence in self.WHEEL_EVENTS:
            try:
                self.frame.bind_all(sequence, self._on_wheel)
            except Exception as exc:
                events.debug("Wheel Not Bound", f"{sequence}: {exc}",
                             source=SOURCE, exception=exc)

    def _on_pointer_leave(self, _event=None):
        if self._sheet is not None:
            return          # the sheet owns the wheel, not its entries
        for sequence in self.WHEEL_EVENTS:
            try:
                self.frame.unbind_all(sequence)
            except Exception:
                pass

    def _on_wheel(self, event):
        """One notch, in whichever dialect the platform speaks: `delta` on
        Aqua and Win32, Button-4/5 on X11. Only the sign is used — a Windows
        notch is 120 and an Aqua one is 1."""
        number, delta = getattr(event, "num", 0), getattr(event, "delta", 0)
        if number == 4:
            step = -1
        elif number == 5:
            step = 1
        elif delta:
            step = -1 if delta > 0 else 1
        else:
            return None
        try:
            self._canvas.yview_scroll(step, "units")
        except Exception as exc:
            events.debug("Wheel Scroll Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        return "break"

    def _call(self, command, inputs=None, args=()):
        """`PanelView._refresh` re-reads every plot, image and log source on
        every 100 ms tick. That is right for a series and wrong for an image,
        which a model renders. Image sources are cached here for
        `IMAGE_REFRESH_MS`; nothing else is cached, and a command carrying
        inputs or args never is.
        """
        if command not in self._slow_commands or inputs or args:
            return super()._call(command, inputs, args)
        now = time.monotonic()
        stamped = self._cached_results.get(command)
        if stamped is not None and (now - stamped[0]) * 1000 < self.IMAGE_REFRESH_MS:
            return stamped[1]
        result = super()._call(command, inputs, args)
        self._cached_results[command] = (now, result)
        return result

    # -- the poll tick -----------------------------------------------------
    def _schedule_refresh(self):
        try:
            self._after_id = self.frame.after(self.REFRESH_MS, self._on_refresh_tick)
        except Exception as exc:
            events.debug("Refresh Not Scheduled", str(exc), source=SOURCE,
                         exception=exc)

    def _on_refresh_tick(self):
        self._after_id = None
        if self._is_paused:
            return
        try:
            self._refresh()
        except KeyError:
            # The model was removed between the tick and now. The dashboard
            # closes this panel; stop ticking rather than log every 100 ms.
            events.debug("Panel Gone", f"{self.name} is no longer open",
                         source=SOURCE)
            return
        except Exception as exc:
            events.debug("Refresh Failed", f"{self.name}: {exc}", source=SOURCE,
                         exception=exc, every=1.0)
        self._schedule_refresh()

    def pause(self):
        """Stop ticking while hidden (F21): the minimised Setup panel was
        re-reading its schema and state ten times a second for nobody."""
        self._is_paused = True
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def resume(self):
        """Tick again, starting with an immediate refresh."""
        if not self._is_paused:
            return
        self._is_paused = False
        self._on_refresh_tick()

    @property
    def is_ticking(self):
        return self._after_id is not None

    def close(self):
        if self._is_closed:
            return
        self._is_closed = True
        # The groups drawn on this entry are inside its frame: they close
        # first (the dashboard moves them to pages of their own before a
        # host closes; this is the teardown's order).
        for view in list(self._hosted.values()):
            view.close()
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        # The wheel binding is application-wide while the pointer is here; a
        # closed panel must not be left holding it.
        self._on_pointer_leave()
        # A log window outlives nothing it belongs to (G4).
        for element in list(self._elements):
            if self._entry_for(element).get("window") is not None:
                self._close_log_window(element, restore_focus=False)
        super().close()
        self._widgets.clear()
        for widget in (self.frame, self._foot):
            if widget is None:
                continue
            try:
                widget.destroy()
            except Exception as exc:
                events.debug("Panel Destroy Failed", str(exc), source=SOURCE,
                             exception=exc)
        if self._host_view is not None:
            self._host_view._detach(self.name)
        events.debug("Panel Closed", self.name, source=SOURCE)

    # -- layout: planning ---------------------------------------------------
    #: Element types drawn with a caption. A button names itself; these
    #: name the value they show.
    CAPTIONED = ("readonly", "entry", "dropdown", "indicator", "checkbox")
    #: Element types that are commands.
    COMMANDS = ("button", "toggle", "file_save", "file_open", "region_select")

    def _build(self):
        self._plan()
        super()._build()

    def _plan(self):
        """Decide, before anything is drawn, which row sections are the rows
        of a table and what its columns are; which column sections are a
        set of axis readings; what each tier's disclosure says.

        A caption that two or more row sections share ("Port", "Gamepad",
        "Status") is a column: it is said ONCE, in a header row, and every
        row puts its control for it in that column. A row section whose
        captions are its own ("Scan", "Selected") is a bar across the table.
        Setup is six rows of the first kind between a bar of each kind.
        """
        sections = list(self._schema().get("sections") or [])
        captions = []
        self._section_kinds = []
        for section in sections:
            tier = _tier_of(section)
            self._tier_count[tier] = self._tier_count.get(tier, 0) + 1
            if tier > 1 and tier not in self._tier_text:
                self._tier_text[tier] = (section.get("disclosure")
                                         or theme.TIER_LABELS.get(tier, "Details"))
            readouts = [element for element in section.get("elements") or []
                        if element.get("type") == "readonly"]
            is_axes = (len(readouts) > 1 and all(
                element.get("rail") and _axis_of(element.get("text", ""))
                for element in readouts))
            self._section_kinds.append("axes" if is_axes else None)
            if section.get("layout") != "row":
                captions.append(None)
                continue
            captions.append([_label(element.get("text", ""))
                             for element in section.get("elements") or []
                             if element.get("type") in self.CAPTIONED])
        counts = {}
        for names in captions:
            for name in set(names or ()):
                counts[name] = counts.get(name, 0) + 1
        shared = {name for name, count in counts.items() if count > 1}
        self._row_kinds, order = [], []
        for names in captions:
            if names is None:
                self._row_kinds.append(None)
            elif names and set(names) <= shared:
                self._row_kinds.append("table")
                order += [name for name in names if name not in order]
            else:
                self._row_kinds.append("bar")
        self._table_columns = {name: index + 1 for index, name in enumerate(order)}

    # -- tiers ----------------------------------------------------------------
    def _tier_frame(self, tier):
        """Where a section of `tier` goes. Tier 1 is the body itself; tier 2
        is ONE panel-toned well under it, behind a disclosure that is the
        last thing in the tier-1 body, left-aligned, with the well directly
        beneath it (K3: the press and what it reveals are never a screen
        apart); tier 3 is a strip inside the well with a 2 px muted rule
        down its left, behind a second disclosure. Built on the first
        section that needs it, mapped only while open.

        The disclosure is created before the well, so the keyboard reaches
        it after the tier-1 controls and before the well's."""
        frame = self._tiers.get(tier)
        if frame is not None:
            return frame
        if tier <= 1:
            frame = tk.Frame(self._body, background=_page())
            frame.pack(side="top", fill="x", padx=self._inset)
            self._tiers[1] = frame
            return frame
        if self._well is None:
            if 1 not in self._tiers:
                self._tier_frame(1)
            opener = _Disclosure(self._foot_parent(), self._tier_text.get(
                2, theme.TIER_LABELS[2]), lambda is_open: self.set_disclosure(2, is_open),
                _page())
            self._disclosures[2] = opener
            self._show_opener()
            if self._sheet is not None:
                well = self._well = self._build_well_scroller()
            else:
                # A page of its own (Setup, a test): the tray without the
                # sheet's scroller, so without its top line.
                well = self._well = self._well_holder = tk.Frame(
                    self._foot_parent(), background=theme.SURFACE, padx=SPACE[5],
                    pady=SPACE[4])
            self._tiers[2] = tk.Frame(well, background=theme.SURFACE)
            self._tiers[2].pack(side="top", fill="x")
        if tier == 3 and 3 not in self._tiers:
            opener = _Disclosure(self._well, self._tier_text.get(
                3, theme.TIER_LABELS[3]), lambda is_open: self.set_disclosure(3, is_open),
                theme.SURFACE)
            opener.frame.pack(side="top", anchor="w", pady=(SPACE[3], 0))
            self._disclosures[3] = opener
            # The pocket (Signature): a DEEP window sunk in the tray, under
            # its 1 px top line.
            holder = self._diagnostics = tk.Frame(self._well, background=theme.SURFACE)
            _tray_line(holder)
            pocket = tk.Frame(holder, background=theme.DEEP, padx=SPACE[4],
                              pady=SPACE[2])
            pocket.pack(side="top", fill="x", expand=True)
            self._tiers[3] = tk.Frame(pocket, background=theme.DEEP)
            self._tiers[3].pack(side="left", fill="x", expand=True)
        return self._tiers[min(tier, 3)]

    # -- the well's own scroll (L5) ----------------------------------------------
    #: The least height the well keeps on the device page before the sheet
    #: itself has to scroll (a tall tier 1 at 28 pt in a short window).
    WELL_FLOOR_PX = 160

    def _build_well_scroller(self):
        """On the sheet the well scrolls by itself (L5, TK7-3): the device
        page gives the entry the sheet's height, the head and tier 1 keep
        their place above, and only the well - tiers 2 and 3 - moves under
        the wheel. The well is a frame on a canvas of its own; the canvas
        asks for no height, so the entry's natural height is its head and
        tier 1. -> the well frame"""
        area = self._well_holder = tk.Frame(self._foot_parent(), background=_page())
        _tray_line(area)
        self._well_bar = ttk.Scrollbar(area, orient="vertical")
        canvas = self._well_canvas = tk.Canvas(area, height=1, background=_page(),
                                               highlightthickness=0,
                                               yscrollcommand=self._well_bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        well = tk.Frame(canvas, background=theme.SURFACE, padx=SPACE[5],
                        pady=SPACE[4])
        self._is_well_bar_shown = False
        # The well becomes the canvas's window only when it is first shown
        # (`_embed_well`): an embedded window in a canvas that is not mapped
        # sent Tk 9's geometry into a loop that never went idle.
        self._well_window = None
        try:
            self._well_bar.configure(command=canvas.yview)
        except Exception as exc:
            events.debug("Well Not Wired", str(exc), source=SOURCE, exception=exc)
        well.bind("<Configure>", self._on_well_resized, add="+")
        canvas.bind("<Configure>", self._on_well_canvas_resized, add="+")
        return well

    def _embed_well(self):
        if self._well_window is not None or self._well_canvas is None:
            return
        try:
            self._well_window = self._well_canvas.create_window(
                (0, 0), window=self._well, anchor="nw")
            width = self._well_canvas.winfo_width()
            if isinstance(width, int) and width > 1:
                self._well_canvas.itemconfigure(self._well_window, width=width)
        except Exception as exc:
            events.debug("Well Not Embedded", str(exc), source=SOURCE, exception=exc)

    def _on_well_resized(self, _event=None):
        try:
            self._well_canvas.configure(scrollregion=self._well_canvas.bbox("all"))
        except Exception:
            pass
        self._sync_well_bar()

    def _on_well_canvas_resized(self, event=None):
        """The well is as wide as its canvas, so its flows wrap to it."""
        width = getattr(event, "width", 0)
        if self._well_window is None or not isinstance(width, int) or width <= 1:
            return
        try:
            self._well_canvas.itemconfigure(self._well_window, width=width)
        except Exception:
            pass
        self._sync_well_bar()

    def _sync_well_bar(self):
        """A scrollbar beside the well only while it has more than it shows."""
        try:
            content = self._well.winfo_reqheight()
            viewport = self._well_canvas.winfo_height()
        except Exception:
            return
        if not (isinstance(content, int) and isinstance(viewport, int)) or viewport <= 1:
            return
        needed = content > viewport
        if needed == self._is_well_bar_shown:
            return
        self._is_well_bar_shown = needed
        try:
            if needed:
                self._well_bar.pack(side="right", fill="y", before=self._well_canvas)
            else:
                self._well_bar.pack_forget()
                self._well_canvas.yview_moveto(0)
        except Exception:
            pass

    @property
    def well_canvas(self):
        """The canvas the wheel scrolls on the device page while the well is
        shown (L5), else None."""
        canvas = getattr(self, "_well_canvas", None)
        if canvas is None or not self._is_opened or not self.is_disclosed(2):
            return None
        return canvas

    def scroll_well(self, step, widget=None):
        """The wheel over the well scrolls the well (L5). -> True if it did."""
        canvas = self.well_canvas
        if canvas is None:
            return False
        if widget is not None:
            node = widget
            while node is not None and node is not self._well_holder:
                node = getattr(node, "master", None)
            if node is None:
                return False
        try:
            canvas.yview_scroll(step, "units")
        except Exception:
            return False
        return True

    def _scroll_well_to(self, widget):
        """Bring a widget inside the well into view by scrolling the well
        (a refusal under a tier-2 control). -> True when it was the well's."""
        canvas = self.well_canvas
        if canvas is None:
            return False
        node, top = widget, 0
        try:
            while node is not None and node is not self._well:
                top += node.winfo_y()
                node = node.master
            if node is None:
                return False
            bottom = top + widget.winfo_reqheight()
            total = self._well.winfo_height()
            first, last = canvas.yview()
        except Exception:
            return False
        numbers = (top, bottom, total, first, last)
        if not all(isinstance(v, (int, float)) for v in numbers) or total <= 1:
            return True
        view_top, view_bottom = first * total, last * total
        if top >= view_top and bottom <= view_bottom:
            return True
        target = (top - SPACE[4] if top < view_top
                  else bottom + SPACE[4] - (view_bottom - view_top))
        try:
            canvas.yview_moveto(max(0.0, min(1.0, target / total)))
        except Exception:
            pass
        return True

    def _build_well_foot(self):
        """"Close this model…" at the foot of the model's well (L13, as
        Web): the quietest control the entry has - text only - because it
        destructs the model. It says what it does when pointed at and the
        dashboard asks before it does it. A model with no tier-2 sections
        still gets the well, for this."""
        self._tier_frame(2)
        foot = tk.Frame(self._well, background=theme.SURFACE)
        foot.pack(side="bottom", fill="x", pady=(SPACE[4], 0))
        press = self._close_press = _Press(foot, CLOSE_MODEL_TEXT,
                                           self._on_close_pressed, theme.SURFACE,
                                           ghost=True)
        press.frame.pack(side="left")
        tip = _Tooltip(press.widget)
        tip.text = (f"Close {self.name}: it stops and disconnects. Reopen it "
                    "from the Models menu.")

    def _on_close_pressed(self):
        if callable(self.on_close):
            self.on_close(self.name)

    def _show_opener(self):
        """The tier-2 disclosure at the foot of the tier-1 body (K3)."""
        opener = self._disclosures.get(2)
        if opener is None:
            return
        if self._foot_anchor is not None:
            after = self._foot_anchor
        elif self._group_slot is not None and self._hosted and self._is_opened:
            after = self._group_slot
        else:
            after = self._tiers[1]
        try:
            opener.frame.pack(side="top", anchor="w", padx=self._inset,
                              pady=(SPACE[3], 0), after=after)
        except Exception as exc:
            events.debug("Disclosure Not Shown", f"{self.name}: {exc}",
                         source=SOURCE, exception=exc)

    def set_disclosure(self, tier, is_open):
        """Open or close a tier; remembered for the model for the session."""
        return self._set_tier_open(tier, is_open)

    def is_disclosed(self, tier):
        """True while `tier` is shown (tier 3 needs tier 2 open as well)."""
        if tier <= 1:
            return True
        if not self._is_opened:
            return False            # an overview entry shows tier 1 only (K4)
        if not _DISCLOSED.get((self.name, 2), False) or 2 not in self._tiers:
            return False
        return tier == 2 or bool(_DISCLOSED.get((self.name, 3), False))

    def _set_tier_open(self, tier, is_open, refresh=True):
        is_open = bool(is_open)
        if tier not in self._tiers:
            return False
        _DISCLOSED[(self.name, tier)] = is_open
        opener = self._disclosures.get(tier)
        if opener is not None:
            opener.set_open(is_open)
        self._map_tier(tier)
        events.debug("Tier Toggled", f"{self.name} tier {tier} -> "
                     f"{'open' if is_open else 'closed'}", source=SOURCE)
        if refresh and is_open and self._is_opened:
            # What was hidden was not polled: fill it now, not in 100 ms.
            try:
                self._refresh()
            except Exception as exc:
                events.debug("Refresh Failed", f"{self.name}: {exc}", source=SOURCE,
                             exception=exc, every=1.0)
        return True

    def _map_tier(self, tier):
        """Map `tier`'s frame as the page and the memory say: the well only
        on the device page and only while remembered open; the Diagnostics
        strip inside it while remembered open."""
        is_open = bool(_DISCLOSED.get((self.name, tier), False))
        target = self._well_holder if tier == 2 else self._diagnostics
        if target is None:
            return
        try:
            if tier == 2 and is_open and self._is_opened:
                # Directly under its press, no gap (K3). On the sheet the
                # well takes the rest of the page and scrolls by itself (L5).
                on_sheet = self._sheet is not None
                if on_sheet:
                    self._embed_well()
                target.pack(side="top", fill="both" if on_sheet else "x",
                            expand=on_sheet, padx=self._inset,
                            pady=(0, SPACE[3]), after=self._disclosures[2].frame)
                self._is_well_mapped = True
            elif tier == 3 and is_open:
                target.pack(side="top", fill="x", padx=self._inset,
                            pady=(SPACE[2], 0))
            else:
                target.pack_forget()
                if tier == 2:
                    self._is_well_mapped = False
        except Exception as exc:
            events.debug("Tier Not Shown", f"{self.name} tier {tier}: {exc}",
                         source=SOURCE, exception=exc)
        if tier == 2:
            # The groups' wells follow this well; a group's well opening
            # gives the host's tail slot the page's height.
            self._seat_tail()
            if self._host_view is not None:
                self._host_view._seat_tail()

    # -- the two pages (K4) -----------------------------------------------------
    def _apply_page(self):
        """An overview entry (on the sheet, not opened) is tier 1 only: no
        disclosure, no well, and its head is the press that opens the device.
        The device page shows the disclosures and the remembered wells.
        `_DISCLOSED` is not touched either way."""
        is_press = self._sheet is not None and not self._is_opened
        self._seat_group()
        opener = self._disclosures.get(2)
        if opener is not None:
            if self._is_opened:
                self._show_opener()
            else:
                try:
                    opener.frame.pack_forget()
                except Exception:
                    pass
        for tier in (2, 3):
            if tier in self._tiers:
                self._map_tier(tier)
        self._seat_tail()
        self._open_tip.text = f"{OPEN_WORD} {self.name}" if is_press else ""
        try:
            if is_press:
                self._open_label.pack(side="right")
            else:
                self._open_label.pack_forget()
            cursor = "hand2" if is_press else ""
            for widget in (self._head, self._title, self._head_right):
                widget.configure(cursor=cursor)
            self._head.configure(takefocus=1 if is_press else 0)
        except Exception as exc:
            events.debug("Head Not Set", f"{self.name}: {exc}", source=SOURCE,
                         exception=exc)
        if not is_press:
            self._paint_head_ring(False)

    def _paint_head_ring(self, is_focused):
        self._head_ring.paint(is_focused)

    def _on_head_pressed(self, _event=None):
        """A press on an overview entry's head opens its device page."""
        if self._sheet is None or self._is_opened or self.on_open is None:
            return None
        events.debug("Entry Head Pressed", self.name, source=SOURCE)
        try:
            self._open_tip._on_leave()
        except Exception:
            pass
        self.on_open(self.name)
        return "break"

    # -- prominence -----------------------------------------------------------
    def set_prominence(self, is_opened):
        """The opened model - the device page - has `focal` readings, its
        disclosures and its remembered wells; an overview entry's probe
        readings are `compact` and it shows tier 1 only (K4). A single value
        stays `primary` and a change `secondary` either way
        (`theme.READING_SIZES`)."""
        is_opened = bool(is_opened)
        if is_opened == self._is_opened:
            return
        self._is_opened = is_opened
        self._apply_page()
        if is_opened and self.is_disclosed(2):
            # What was hidden was not polled: fill it now, not in 100 ms.
            try:
                self._refresh()
            except Exception as exc:
                events.debug("Refresh Failed", f"{self.name}: {exc}", source=SOURCE,
                             exception=exc, every=1.0)
        try:
            self._title.configure(font=self._name_font())
        except Exception:
            pass
        for element, kind in self._readings:
            widget = self._entry_for(element).get("widget")
            if widget is None:
                continue
            font = _reading_font(self._reading_kind(kind))
            entry = self._entry_for(element)
            entry["font"] = font
            entry["drawn_font"] = None
            self._paint_empty(entry, entry.get("shown_text") == EMPTY_READOUT)

    def _reading_kind(self, kind):
        if kind == "axes":
            return "focal" if self._is_opened else "compact"
        return kind

    # -- layout: sections ----------------------------------------------------
    def _make_section(self, title, layout="column"):
        """A section of the entry. `layout` is the schema's hint.

        `column` is the sheet's form: the section's controls are CELLS - a
        caption over its control, a command on its own - that flow left to
        right and wrap by measured width, so nothing is cut at any font size.
        A set of axis readings says its caption once ("Position") with the
        letters inline. `row` is the table form (`_make_row_section`).
        """
        index = self._section_index
        self._section_index += 1
        sections = list(self._schema().get("sections") or [])
        section = sections[index] if index < len(sections) else {}
        tier = _tier_of(section)
        self._building_tier = tier
        if layout == "row":
            kinds = self._row_kinds
            kind = kinds[index] if index < len(kinds) and kinds[index] else "bar"
            return self._make_row_section(
                title, kind, pinned=kind == "bar" and self._is_commit_row(index),
                parent=self._tier_frame(tier))
        # A column section ends the table: the next row section starts a new
        # one, so a schema that interleaves the two still renders in order.
        self._table = None
        parent = self._tier_frame(tier)
        background = _bg(parent)
        container = tk.Frame(parent, background=background)
        container.pack(side="top", fill="x", anchor="w", pady=(0, SPACE[4]))
        try:
            container.grid_columnconfigure(0, weight=1)
        except Exception as exc:
            events.debug("Section Columns Refused", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        is_axes = (index < len(self._section_kinds)
                   and self._section_kinds[index] == "axes")
        heading = None
        shared_unit = ""
        if is_axes:
            # One caption for the set, its unit said once: "Position, steps",
            # then X Y Z inline (design-Sheet.md).
            units = {element.get("unit") or "" for element in section.get("elements") or []
                     if element.get("type") == "readonly"}
            shared_unit = units.pop() if len(units) == 1 else ""
            caption = _label(title) + (f", {shared_unit}" if shared_unit else "")
            heading = tk.Label(container, text=caption, font=_caption_font(),
                               anchor="w", background=background,
                               foreground=theme.MUTED)
        elif (tier > 1 and self._tier_count.get(tier, 0) > 1
              and _label(title).lower() != _label(self._tier_text.get(tier, "")).lower()):
            # A heading that says what its disclosure just said is noise
            # ("Diagnostics" under "Diagnostics", L18).
            heading = tk.Label(container, text=_label(title), font=_font(bold=True),
                               anchor="w", background=background,
                               foreground=theme.TEXT)
        if heading is not None:
            heading.grid(row=0, column=0, sticky="w", pady=(0, GAP))
            self._section_titles.append(heading)
        self._grid[id(container)] = {
            "layout": "cells", "row": 1, "strip": None, "tier": tier,
            "axes": is_axes, "inline": tier >= 3, "readings": 0,
            "shared_unit": shared_unit}
        return container

    def _is_commit_row(self, index):
        """The schema's LAST section, a bar holding a `go` command (Setup's
        Launch row): the step the whole panel leads to. It is pinned under
        the scroll area, so it is on screen at every font size - at 28 pt
        it was scrolled below the rows (UXPM5-5)."""
        sections = list(self._schema().get("sections") or [])
        if index != len(sections) - 1:
            return False
        return any(element.get("type") == "button" and element.get("role") == "go"
                   for element in sections[index].get("elements") or [])

    def _make_row_section(self, title, kind, pinned=False, parent=None):
        """One line of the table: the row's name in column 0, then its cells.

        Every row section in a run shares ONE grid, because columns only line
        up inside a single grid. A `table` row puts each control under its
        caption's header; a `bar` row (its captions are its own) spans the
        table with its captions inline. A rule sets the header off from the
        rows, and the rows off from a bar that follows them.
        """
        if pinned:
            self._table = None
        if self._table is None:
            if pinned:
                try:
                    self._pinned.pack(side="bottom", fill="x",
                                      before=self._area or self._body)
                except Exception as exc:
                    events.debug("Pinned Row Not Placed", str(exc), source=SOURCE,
                                 exception=exc)
                # The commit row is set off by the entry's own rule: 2 px ink.
                tk.Frame(self._pinned, height=theme.RULE_STRONG_PX,
                         background=theme.RULE_STRONG).pack(fill="x", padx=INSET)
            self._table = tk.Frame(self._pinned if pinned else (parent or self._body),
                                   background=_page())
            self._table.pack(fill="x", padx=INSET if pinned else 0,
                             pady=(SPACE[4], SPACE[4]) if pinned else (GAP, GAP))
            width = len(self._table_columns)
            self._grid[id(self._table)] = {
                "layout": "row", "row": -1, "kind": None, "bar": None,
                "has_header": False, "width": width, "extra": width + 1}
            if width:
                # The last shared column (the status) takes the slack, so a
                # long status has room instead of being cut off.
                self._stretch_column(self._table, width, STATUS_WEIGHT)
        state = self._grid[id(self._table)]
        span = max(1, state["width"])
        if state["kind"] is not None:
            state["row"] += 1           # the previous row's refusal line
            if kind == "table" and state["kind"] == "table":
                # A panel-toned hairline between two rows of the table, in
                # the refusal line's cell (a refusal is drawn over it).
                tk.Frame(self._table, height=1, background=theme.RULE).grid(
                    row=state["row"], column=0, columnspan=span + 1, sticky="sew")
        if kind == "table" and not state["has_header"]:
            state["row"] += 1
            for caption, column in self._table_columns.items():
                tk.Label(self._table, text=caption, font=_caption_font(), anchor="w",
                         background=_page(), foreground=theme.MUTED
                         ).grid(row=state["row"], column=column, sticky="w",
                                padx=(0, SPACE[5]), pady=(GAP, 0))
                # A header is never cut: its column keeps its width (28 pt).
                need = _width_px(_caption_font(), caption) + SPACE[5] + SPACE[1]
                sizes = state.setdefault("minsize", {})
                sizes[column] = max(sizes.get(column, 0), need)
                try:
                    self._table.grid_columnconfigure(column, minsize=sizes[column])
                except Exception:
                    pass
            state["row"] += 1
            self._hairline(self._table, state["row"], span + 1)
            state["has_header"] = True
        elif kind == "bar" and state["kind"] == "table":
            state["row"] += 1
            self._hairline(self._table, state["row"], span + 1)
        state["row"] += 1
        state["kind"] = kind
        state["extra"] = state["width"] + 1
        caption = tk.Label(self._table, text=_sentence(title),
                           font=_font(STEP_1 if kind == "table" else BASE, bold=True),
                           anchor="w", background=_page(), foreground=theme.TEXT)
        caption.grid(row=state["row"], column=0, sticky="w",
                     padx=(0, SPACE[5]), pady=SPACE[2])
        self._section_titles.append(caption)
        state["caption"] = caption
        state["bar"] = None
        if kind == "bar":
            bar = tk.Frame(self._table, background=_page())
            bar.grid(row=state["row"], column=1, columnspan=span, sticky="ew",
                     pady=GAP)
            state["bar"] = bar
        return self._table

    def _hairline(self, container, row, span):
        tk.Frame(container, height=1, background=theme.RULE).grid(
            row=row, column=0, columnspan=span, sticky="ew", pady=(GAP, GAP))

    # -- layout: cells ---------------------------------------------------------
    def _cursor(self, container):
        return self._grid.setdefault(id(container), {
            "layout": "cells", "row": 1, "strip": None, "tier": 1,
            "axes": False, "inline": False, "readings": 0})

    def _stretch_column(self, container, column, weight=1):
        """Let one column absorb the slack (and give it back first)."""
        try:
            container.grid_columnconfigure(column, weight=weight)
        except Exception as exc:
            events.debug("Column Weight Refused", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _strip(self, container):
        """The section's current flow of cells, started on first use: every
        cell and command until the next full-width element shares it."""
        state = self._cursor(container)
        strip = state.get("strip")
        if strip is not None:
            return strip
        strip = tk.Frame(container, background=_bg(container))
        strip.grid(row=state["row"], column=0, sticky="ew")
        state["row"] += 1
        state["strip"] = strip
        self._flows[id(strip)] = {"items": [], "rows": [], "layout": None,
                                  "hidden": set(), "width": None}
        strip.bind("<Configure>",
                   lambda event, s=strip: self._flow_strip(
                       s, getattr(event, "width", None)), add="+")
        return strip

    def _add_to_flow(self, strip, widget):
        flow = self._flows[id(strip)]
        flow["items"].append(widget)
        flow["layout"] = None
        self._flow_strip(strip, None)
        return widget

    def _field(self, container, element):
        """Where one captioned control goes. -> (parent, place)

        `place(widget, fill)` positions the control; `fill` says what it is:
        "value" a readout, "field" an entry or dropdown, "mark" a lamp, a box
        or a slider. On the sheet a control is a CELL: its caption above it
        (beside it in Diagnostics), its unit after it; an axis reading
        carries its letter instead of a caption.
        """
        state = self._cursor(container)
        text = _label(element.get("text", ""))
        self._register(element, slot=self._notice_slot(container))
        if state["layout"] == "cells":
            strip = self._strip(container)
            background = _bg(strip)
            axis = _axis_of(element.get("text", "")) if state.get("axes") else None
            caption, unit = text, ""
            if element.get("type") in ("readonly", "entry"):
                caption, unit = _split_unit(text)
            unit = element.get("unit") or unit
            if axis and unit and unit == state.get("shared_unit"):
                unit = ""           # said once, in the section's caption
            cell = tk.Frame(strip, background=background)
            label = None
            if caption and not axis:
                label = tk.Label(cell, text=caption, font=_caption_font(), anchor="w",
                                 background=background, foreground=theme.MUTED)
                if state.get("inline"):
                    label.pack(side="left", anchor="s", padx=(0, SPACE[3]))
                else:
                    label.pack(side="top", anchor="w")
            line = tk.Frame(cell, background=background)
            line.pack(side="left" if state.get("inline") else "top", anchor="sw")
            if axis:
                tk.Label(line, text=axis, font=_axis_font(), background=background,
                         foreground=theme.MUTED).pack(side="left", anchor="s",
                                                      padx=(0, SPACE[1]), pady=SPACE[2])
            self._register(element, box=cell, caption=label, strip=strip)
            placed = []

            def place(widget, fill):
                widget.pack(in_=line, side="left", anchor="s",
                            padx=(0, SPACE[2]) if fill == "mark" else 0)
                if not placed:
                    placed.append(widget)
                    if label is not None and not state.get("inline"):
                        # A captioned cell hangs from the line's top, so
                        # the captions in a line share one (TK7-9).
                        self._flows[id(strip)].setdefault("captioned", set()).add(
                            id(cell))
                    self._add_to_flow(strip, cell)
                if unit and fill in ("value", "field"):
                    unit_label = tk.Label(line, text=unit, font=_caption_font(),
                                          anchor="w", background=background,
                                          foreground=theme.MUTED)
                    unit_label.pack(side="left", anchor="s", padx=(SPACE[1], 0))
                    self._register(element, unit_label=unit_label)
                return widget
            return line, place
        bar = state.get("bar")
        if bar is not None:
            tk.Label(bar, text=text, font=_caption_font(), anchor="w",
                     background=_page(), foreground=theme.MUTED
                     ).pack(side="left", padx=(0, SPACE[3]))

            def place(widget, fill):
                widget.pack(side="left", fill="x" if fill == "value" else None,
                            expand=fill == "value", padx=(0, SPACE[5]))
                return widget
            return bar, place
        column = self._table_columns.get(text)
        if column is None:
            column = state["extra"]
            state["extra"] += 1

        def place(widget, fill):
            # A table's field (a dropdown) is elastic: it fills its cell and
            # its column shrinks before the status column does (UXPM5-5).
            widget.grid(row=state["row"], column=column, pady=GAP,
                        padx=(0, SPACE[5]),
                        sticky="ew" if fill in ("value", "field") else "w")
            if fill == "field" and column not in state.setdefault("elastic", set()):
                state["elastic"].add(column)
                if column != state.get("width"):
                    self._stretch_column(container, column)
            if fill == "value":
                self._register(element, table=(container, column))
            return widget
        return container, place

    def _command_slot(self, container, element=None):
        """Where one command goes. -> (parent, place)

        On the sheet a command is a cell of the section's flow, beside the
        values it acts on ("Move by 1.0  Move -  Move +  Home"), bottom-
        aligned with them, wrapping by measured width so no caption is ever
        cut (UXPM5-2, IMP-3). A refusal of any of them is shown on the
        section's notice row, under its controls.
        """
        state = self._cursor(container)
        if state["layout"] == "cells":
            strip = self._strip(container)
            if element is not None:
                self._register(element, slot=self._notice_slot(container))
            return strip, lambda widget: self._add_to_flow(strip, widget)
        if element is not None:
            self._register(element, slot=self._notice_slot(container))
        bar = state.get("bar")
        if bar is not None:
            return bar, lambda widget: widget.pack(side="left", padx=(0, PAD))
        column = state["extra"]
        state["extra"] += 1
        return container, lambda widget: widget.grid(
            row=state["row"], column=column, sticky="w", padx=(0, PAD), pady=GAP)

    def _flow_strip(self, strip, width):
        """Lay a flow of cells out in as many lines as its width needs.

        Each line is a frame of its own, so a line's cells are packed side
        by side at their own widths (a grid would share column widths between
        lines). The cells stay children of the strip and are packed `in_` a
        line, raised above it so the line frame does not hide them; they sit
        on the line's baseline, so a command lines up with the value beside
        it. A hidden cell (a quiet status, E) takes no place.
        """
        flow = self._flows.get(id(strip))
        if flow is None or not flow["items"]:
            return
        if not isinstance(width, int) or width <= 1:
            try:
                width = strip.winfo_width()
            except Exception:
                width = None
        if isinstance(width, int) and width > 1:
            flow["width"] = width
            self._fit_sliders(flow, width)
        shown = [widget for widget in flow["items"] if id(widget) not in flow["hidden"]]
        widths = []
        for widget in shown:
            try:
                wanted = widget.winfo_reqwidth()
            except Exception:
                wanted = None
            widths.append(wanted if isinstance(wanted, int) else 0)
        # Every cell carries its gap on its right, the last one too.
        room = width - SPACE[5] if isinstance(width, int) and width > 1 else None
        layout = tuple(_flow_lines(widths, room, SPACE[5]))
        key = (layout, tuple(id(widget) for widget in shown))
        if key == flow["layout"]:
            return
        # A dead band: the same cells at the same sizes, a pixel or two
        # either side of the width they were laid out at, keep their lines.
        # A flow that wraps at exactly its column's width changes the row's
        # requests, the grid hands the column's odd pixel elsewhere, and the
        # flow unwraps - forever (the overview at 1056 px, K4). The cells'
        # own gap on the right absorbs the difference.
        laid = flow.get("laid") or (None, None, None)
        if (flow["layout"] is not None and laid[0] == key[1]
                and laid[1] == tuple(widths) and isinstance(width, int)
                and isinstance(laid[2], int)
                and 0 < abs(width - laid[2]) <= self.FLOW_DEAD_BAND_PX):
            return
        flow["layout"] = key
        flow["laid"] = (key[1], tuple(widths), width)
        lines = flow["rows"]
        count = (max(layout) + 1) if layout else 0
        while len(lines) < count:
            lines.append(tk.Frame(strip, background=_bg(strip)))
        for index, line in enumerate(lines):
            try:
                if index < count:
                    line.pack(side="top", anchor="w", fill="x",
                              pady=(0 if index == 0 else SPACE[3], 0))
                else:
                    line.pack_forget()
            except Exception as exc:
                events.debug("Cell Line Not Placed", str(exc), source=SOURCE,
                             exception=exc, every=5.0)
        for widget in flow["items"]:
            if id(widget) in flow["hidden"]:
                try:
                    widget.pack_forget()
                except Exception:
                    pass
        captioned = flow.get("captioned") or set()
        for widget, index in zip(shown, layout):
            try:
                widget.pack_forget()
                widget.pack(in_=lines[index], side="left",
                            anchor="nw" if id(widget) in captioned else "sw",
                            padx=(0, SPACE[5]))
                widget.lift()
            except Exception as exc:
                events.debug("Cell Not Placed", str(exc), source=SOURCE,
                             exception=exc, every=5.0)

    #: How far a flow's width may move before its lines are recomputed.
    #: Less than the gap every cell carries, so nothing is cut.
    FLOW_DEAD_BAND_PX = 2

    #: The shortest a slider gets before its cell may wrap instead.
    SLIDER_MIN_PX = 80

    def _fit_sliders(self, flow, width):
        """A slider gives up length before its cell overflows the column:
        slider, entry and unit always fit, the slider between
        `SLIDER_MIN_PX` and `VALUE_PX` (design px)."""
        for element in flow.get("sliders", ()):
            entry = self._entry_for(element)
            scale, field, unit = entry.get("scale"), entry.get("cell"), entry.get("unit_label")
            if scale is None or field is None:
                continue
            # What sits beside the slider does not depend on its length, so
            # the fit converges in one pass (a cell's own request lags a
            # length change until Tk is idle, and chasing it oscillated).
            try:
                others = field.winfo_reqwidth() + (unit.winfo_reqwidth() if unit else 0)
            except Exception:
                continue
            if not isinstance(others, int):
                continue
            others += 2 * FOCUS_PX + SPACE[2] + SPACE[1]
            length = max(_design_px(self.SLIDER_MIN_PX),
                         min(_design_px(VALUE_PX), width - SPACE[5] - others))
            if abs((entry.get("slider_px") or _design_px(VALUE_PX)) - length) <= SPACE[0]:
                continue
            entry["slider_px"] = length
            try:
                scale.configure(length=length)
            except Exception:
                pass

    def _set_shown(self, element, is_shown):
        """Status by exception (E): a tier-1 readout whose value is normal
        (`theme.QUIET_VALUES`) takes no place; it comes back when it is not."""
        entry = self._entry_for(element)
        box, strip = entry.get("box"), entry.get("strip")
        flow = self._flows.get(id(strip)) if strip is not None else None
        if box is None or flow is None:
            return
        is_hidden = id(box) in flow["hidden"]
        if is_hidden == (not is_shown):
            return
        (flow["hidden"].discard if is_shown else flow["hidden"].add)(id(box))
        entry["is_quiet"] = not is_shown
        flow["layout"] = None
        self._flow_strip(strip, flow.get("width"))

    def _wide_slot(self, container, caption=None):
        """Where a plot, a picture or a log goes: the full width of its
        section, under its caption, after the cells before it; cells after
        it start a new flow. -> (parent, place)"""
        state = self._cursor(container)
        if state["layout"] == "cells":
            state["strip"] = None
            background = _bg(container)
            if caption:
                tk.Label(container, text=_label(caption), font=_caption_font(),
                         anchor="w", background=background, foreground=theme.MUTED
                         ).grid(row=state["row"], column=0, sticky="w",
                                pady=(SPACE[3], 0))
                state["row"] += 1
            row = state["row"]
            state["row"] += 1
            return container, lambda widget, sticky="ew": widget.grid(
                row=row, column=0, sticky=sticky, pady=GAP)
        parent, place = self._command_slot(container)
        return parent, lambda widget, sticky=None: place(widget)

    #: The grid row of a section's notice: under everything in it; the
    #: muted reason a `go` command is greyed out sits just above it (L3).
    NOTICE_ROW = 999
    WHY_ROW = 998

    def _notice_slot(self, container):
        """(container, row, column, span): the grid cell where a refusal of
        a control is shown. On the sheet it is the section's notice row,
        under its controls; in a table, the row under the table row."""
        state = self._cursor(container)
        if state["layout"] == "cells":
            return (container, self.NOTICE_ROW, 0, 1)
        return (container, state["row"] + 1, 1, max(1, state.get("width") or 1))

    def _register(self, element, **widgets):
        entry = self._widgets.setdefault(id(element), {})
        entry.setdefault("tier", self._building_tier)
        entry.update(widgets)
        entry.setdefault("is_enabled", True)
        return entry

    def _entry_for(self, element):
        return self._widgets.get(id(element), {})

    # -- commands: keys of one family --------------------------------------------
    def _button_label(self, parent, element, on_click, text=None):
        """A command as a key (Signature): a `tk.Label` face (`tk.Button`
        ignores bg/fg on Aqua) inside a rim frame on a lip frame - the ONE
        helper that builds every command key. -> the frame to place.

        It sits in a `_Ring` with a lip: Aqua does not draw a Label's
        highlight ring, and ttk `clam` cannot draw a thick edge on one side.
        It has every state a control needs: hover (the rim turns ink, a
        filled face steps to muted), pressed (the lip folds and the face
        drops, until the release), keyboard focus (a 2 px ink ring; Return
        and Space press it), disabled (the key's silhouette in ghost tones,
        and a click does nothing). A toggle is a latching key: down in ink
        while on, with its lamp slot lit. A key carries at most one glyph
        of the set, before its legend (`KEY_GLYPHS`). It is at least
        `MIN_TARGET_PX` tall at every font size.
        """
        background = _bg(parent)
        ring = _Ring(parent, background, border=theme.KEY_RIM, lip="key")
        lamp = None
        if element.get("type") == "toggle":
            width, height = _lamp_size()
            lamp = tk.Canvas(ring.inner, width=width, height=height,
                             background=theme.CAP, highlightthickness=0,
                             cursor="hand2")
            lamp.pack(side="left", padx=(SPACE[3], 0))
        widget = tk.Label(ring.inner, text=_label(element.get("text", "") if text is None
                                                 else text),
                          font=_key_font(), relief="flat", padx=SPACE[4],
                          pady=_command_pady(lip=_lip_px("key")), borderwidth=0,
                          cursor="hand2", takefocus=1, highlightthickness=0)
        widget.pack(side="left", fill="both", expand=True)
        glyph = (KEY_GLYPHS.get(element.get("command"))
                 or KEY_GLYPHS.get(element.get("model_attr")))
        # Why it is greyed out, when it is (L3): its own hover text, shown
        # by the command's own Enter/Leave so no handler is replaced.
        gate_tip = _Tooltip(widget, bind=False)
        self._register(element, widget=widget, outline=ring.inner, ring=ring,
                       ground=background, gate_tip=gate_tip, lamp_slot=lamp,
                       glyph=glyph)

        def _on_widget_click(_event=None, element=element):
            if not self._entry_for(element).get("is_enabled", True):
                return "break"
            on_click(element)
            return "break"

        def _on_pointer_press(event=None, element=element):
            if self._entry_for(element).get("is_enabled", True):
                _on_flag("is_pressed", True)
            return _on_widget_click(event)

        def _on_flag(name, value, element=element):
            self._entry_for(element)[name] = value
            if name == "is_hovered":
                (gate_tip.enter if value else gate_tip.leave)()
            self._paint_command(element)

        for part in (widget, lamp):
            if part is None:
                continue
            part.bind("<Button-1>", _on_pointer_press)
            part.bind("<ButtonRelease-1>", lambda _e: _on_flag("is_pressed", False))
        widget.bind("<Return>", _on_widget_click)
        widget.bind("<space>", _on_widget_click)
        widget.bind("<Enter>", lambda _e: _on_flag("is_hovered", True))
        widget.bind("<Leave>", lambda _e: _on_flag("is_hovered", False))
        widget.bind("<FocusIn>", lambda _e: _on_flag("is_focused", True))
        widget.bind("<FocusOut>", lambda _e: _on_flag("is_focused", False))
        self._paint_command(element)
        return ring.outer

    @staticmethod
    def _command_colors(role, ground=None):
        """(face, legend, rim) of a command key, from its role.

        Signature: `go` is the ink key - the one you press; every other
        command is a CAP key with the KEY_RIM rim. Only the stop object
        carries the signal colour: a danger command (a model's own "Stop
        run") renders like any other (DS-9), and a warning is a CAP key,
        never the trace colour, which is reserved for changing numbers."""
        face, legend, rim, _lip = _key_look("go" if role == "go" else "neutral",
                                            ground or _page())
        return face, legend, rim

    def _key_state(self, element):
        """(kind, is_down, lamp) of a command key now: its look in
        `_key_look`, whether its lip is folded, and its lamp slot's state."""
        entry = self._entry_for(element)
        is_toggle = element["type"] == "toggle"
        is_on = bool(entry.get("is_on")) if is_toggle else False
        if not entry.get("is_enabled", True):
            return "disabled", False, "ghost" if is_toggle else None
        if is_toggle and is_on:
            danger = element.get("on_role") == "danger"
            return ("danger" if danger else "go"), True, "lit"
        if is_toggle:
            return "neutral", bool(entry.get("is_pressed")), "off"
        kind = "go" if element.get("role") == "go" else "neutral"
        return kind, bool(entry.get("is_pressed")), None

    def _paint_command(self, element):
        """Configure only what changed: an unchanged command costs a tuple
        compare, not a dozen Tk calls (F21)."""
        entry = self._entry_for(element)
        widget = entry.get("widget")
        if widget is None:
            return
        ground = entry.get("ground") or _page()
        kind, is_down, lamp = self._key_state(element)
        face, legend, rim, lip = _key_look(kind, ground)
        if (element["type"] == "toggle" and kind == "neutral"
                and element.get("off_role") == "danger"):
            # A danger toggle keeps its red rim when off: the stop must
            # read as the stop (`theme.toggle_colors`).
            rim = theme.toggle_colors(element, False)["border"]
        if entry.get("is_hovered") and entry.get("is_enabled", True):
            # A filled key steps to muted; a CAP key's rim turns ink.
            if face == theme.TEXT:
                face = theme.MUTED
            else:
                rim = theme.TEXT
        is_focused = bool(entry.get("is_focused"))
        cursor = "hand2" if entry.get("is_enabled", True) else "arrow"
        key = (face, legend, rim, lip, is_down, lamp, is_focused, cursor)
        entry["border"] = rim
        if entry.get("paint") == key:
            return
        entry["paint"] = key
        options = dict(background=face, foreground=legend, cursor=cursor)
        image = _icon_image(entry.get("glyph"), legend)
        if image is not None:
            options.update(image=image, compound="left")
        try:
            widget.configure(**options)
        except Exception:
            pass
        ring = entry.get("ring")
        if ring is not None:
            ring.set_lip(lip)
            ring.set_down(is_down)
            ring.paint(is_focused, rim)
        if entry.get("lamp_slot") is not None:
            entry["lamp_state"] = lamp
            _draw_lamp(entry["lamp_slot"], lamp, face)

    def _role_colors(self, role):
        """The schema names the meaning; the theme owns the palette."""
        return theme.colors(role or "neutral")

    # -- element renderers -------------------------------------------------
    def _make_readonly(self, container, element):
        """A readout: a number, no box - never mistaken for a field to type
        into.

        On the sheet (E) a rail reading is drawn big in the numeral face at
        `theme.READING_SIZES` - `focal` for the opened model's axes,
        `compact` for a closed one's, `primary` for a single value,
        `secondary` for a change - and any other readout a step louder than
        its caption. It is ink at rest and the trace colour only while its
        value is changing (`CHANGING_S`). In a table's status column and in
        a bar it reads left to right like the text it is.

        A value longer than its room is cut with an ellipsis and the whole
        of it is the tooltip.
        """
        parent, place = self._field(container, element)
        state = self._cursor(container)
        is_text = state["layout"] == "row"
        var = tk.StringVar(value="")
        kind = None
        if is_text:
            font = _font(STEP_1)
        elif element.get("rail"):
            if state.get("axes"):
                kind = "axes"
            else:
                kind = "primary" if not state["readings"] else "secondary"
            state["readings"] += 1
            font = _reading_font(self._reading_kind(kind))
        elif state.get("inline"):
            font = _font()
        else:
            font = _value_font()
        background = _bg(parent)
        options = dict(text="", font=font, relief="flat", background=background,
                       foreground=theme.TEXT, highlightthickness=0)
        if is_text:
            options.update(width=1, anchor="w")
        else:
            options.update(anchor="w")
        mark = None
        if (element.get("role") or "neutral") == "danger" and not is_text:
            # A fault reason is ink with a signal mark beside it: signal
            # text is too faint to read on the panel, and this is the one
            # readout the operator must read during a fault (F14, AUD-5).
            size = _lamp_px()
            mark = tk.Canvas(parent, width=size, height=size, background=background,
                             highlightthickness=0)
            place(mark, "mark")
        value = tk.Label(parent, **options)
        place(value, "value")
        tooltip = _Tooltip(value)
        self._register(element, widget=value, var=var, font=font, tooltip=tooltip,
                       shown=None, mark=mark, changed_at=None, is_text=is_text)
        if kind is not None:
            self._readings.append((element, kind))
        value.bind("<Configure>", lambda _e, el=element: self._fit_readout(el),
                   add="+")

    def _fit_readout(self, element):
        """Show as much of the value as its room holds; the rest is the
        tooltip. A table's status has its cell; a sheet readout has the
        width of its flow. With no measure of either, show it all."""
        entry = self._entry_for(element)
        widget, var = entry.get("widget"), entry.get("var")
        if widget is None or var is None:
            return
        text = str(var.get() or "")
        room = None
        if entry.get("is_text"):
            try:
                room = widget.winfo_width() - 2 * int(widget.cget("padx") or 0) - 2
            except Exception:
                room = None
            room = self._make_room(entry, text, room)
        else:
            flow = self._flows.get(id(entry.get("strip"))) or {}
            width = flow.get("width")
            if isinstance(width, int) and width > 1:
                room = max(_design_px(VALUE_PX), width - SPACE[6])
        shown = _elide(entry.get("font"), text, room if isinstance(room, int) else None,
                       middle=_is_identifier(text))
        tooltip = entry.get("tooltip")
        if tooltip is not None:
            tooltip.text = text if shown != text else ""
        if entry.get("shown") == shown:
            return
        entry["shown"] = shown
        try:
            widget.configure(text=shown)
        except Exception:
            pass

    def _make_room(self, entry, text, room):
        """A table's status column is never narrower than the word in it, up
        to `STATUS_MIN_CHARS` characters: "simulated" read "…" at 28 pt
        while each dropdown kept 560 px (UXPM5-5). -> the room to elide to."""
        table = entry.get("table")
        if table is None or not text:
            return room
        container, column = table
        font = entry.get("font")
        need = _text_width(font, text)
        if need is None:
            return room
        # The cell's right padding is inside the column, and the label's own
        # padding and border inside the cell: the word gets what is left.
        need = min(need, _width_px(font, "0" * STATUS_MIN_CHARS)) + SPACE[3] + SPACE[5]
        sizes = self._grid.setdefault(id(container), {}).setdefault("minsize", {})
        if need > sizes.get(column, 0):
            sizes[column] = need
            try:
                container.grid_columnconfigure(column, minsize=need)
            except Exception as exc:
                events.debug("Column Minsize Refused", str(exc), source=SOURCE,
                             exception=exc, every=5.0)
            return max(room, need - SPACE[5]) if isinstance(room, int) else room
        return room

    def _make_entry(self, container, element):
        """A field: a well in the other ground (panel-toned on the sheet,
        sheet-toned inside a panel well), no box, a muted underline, the
        number right-aligned; focus is the 2 px ink ring (F25).

        An entry with `slider` (E) gets a `ttk.Scale` BESIDE it - never
        instead: the entry keeps the precision, the slider is the common
        adjustment. Moving the slider writes the entry's text; releasing it
        commits that text through the Controller like Return does; typing
        or a refresh moves the slider. A command carries the entry's text
        while it is edited or declared (D-5, MOD-6)."""
        parent, place = self._field(container, element)
        background = _bg(parent)
        var = tk.StringVar(value="")
        scale = None
        travel = element.get("slider")
        if travel:
            low, high = travel
            # The slider wears the same 2 px ink focus ring as the entry;
            # the arrow keys move it, and their release commits.
            track = _Ring(parent, background, border=background)
            scale = ttk.Scale(track.inner, from_=low, to=high, orient="horizontal",
                              style=WELL_SCALE_STYLE if background == theme.SURFACE
                              else SCALE_STYLE, length=_design_px(VALUE_PX),
                              takefocus=1,
                              command=lambda value, el=element: self._on_slider_moved(
                                  el, value))
            scale.pack(fill="both", expand=True)
            place(track.outer, "mark")
            scale.bind("<FocusIn>", lambda _e, r=track: r.paint(True), add="+")
            scale.bind("<FocusOut>", lambda _e, r=track: r.paint(False), add="+")
            scale.bind("<ButtonRelease-1>",
                       lambda _e, el=element: self._on_entry_commit(el), add="+")
            for key, direction in SLIDER_KEYS.items():
                scale.bind(f"<{key}>", lambda _e, el=element, d=direction:
                           self._on_slider_key(el, d))
            for key, direction in SLIDER_PAGE_KEYS.items():
                scale.bind(f"<{key}>", lambda _e, el=element, d=direction:
                           self._on_slider_key(el, d, page=True))
            for key in ("<Home>", "<End>"):
                scale.bind(key, lambda _e: "break")
            scale.bind("<KeyRelease>", lambda event, el=element:
                       self._on_slider_key_released(el, event), add="+")
        ring = _Ring(parent, background, underline=True)
        widget = tk.Entry(ring.inner, textvariable=var, font=_font(),
                          width=FIELD_WIDTH, justify="right", relief="flat",
                          borderwidth=0, highlightthickness=0,
                          background=_field_ground(background), foreground=theme.TEXT,
                          insertbackground=theme.TEXT,
                          disabledbackground=theme.DISABLED[0],
                          disabledforeground=theme.DISABLED[1])
        widget.pack(fill="both", expand=True, ipady=_field_pady(), ipadx=SPACE[1])
        if element.get("value_type") in ("int", "float"):
            self._attach_validator(widget, element)
        place(ring.outer, "field")
        # The well is the target (L4): a press on its ring or its underline
        # puts the cursor in the field, as a press on the field does.
        for part in (ring.outer, ring.inner, ring.line):
            if part is not None:
                part.bind("<Button-1>", lambda _e, w=widget: self._focus_field(w))
        widget.bind("<Return>", lambda _e, el=element: self._on_entry_commit(el))
        widget.bind("<FocusIn>", lambda _e, r=ring: r.paint(True))
        widget.bind("<FocusOut>", lambda _e, el=element, r=ring:
                    (r.paint(False), self._on_entry_commit(el)))
        if scale is not None:
            widget.bind("<KeyRelease>", lambda _e, el=element: self._sync_slider(el),
                        add="+")
        self._register(element, widget=widget, var=var, last_text="",
                       cell=ring.outer, ring=ring, scale=scale, underline=ring.line)
        strip = self._entry_for(element).get("strip")
        if scale is not None and id(strip) in self._flows:
            self._flows[id(strip)].setdefault("sliders", []).append(element)

    @staticmethod
    def _focus_field(widget):
        try:
            if str(widget.cget("state")) != "disabled":
                widget.focus_set()
                widget.icursor("end")
        except Exception:
            pass
        return "break"

    def _on_slider_key(self, element, direction, page=False):
        """An arrow or Page key on a slider (L6): 1 % (10 %) of the travel,
        at least one unit, never past either end."""
        entry = self._entry_for(element)
        scale, travel = entry.get("scale"), element.get("slider")
        if scale is None or not travel or not entry.get("is_enabled", True):
            return "break"
        low, high = travel
        fraction = SLIDER_PAGE_FRACTION if page else SLIDER_ARROW_FRACTION
        step = max(1, round((high - low) * fraction))
        try:
            now = float(scale.get())
        except (TypeError, ValueError):
            return "break"
        scale.set(max(low, min(high, now + direction * step)))
        return "break"

    def _on_slider_key_released(self, element, event=None):
        """Commit as a pointer release does - but only for the keys that
        move the slider (a Tab into it used to commit)."""
        keysym = getattr(event, "keysym", "")
        if keysym in SLIDER_KEYS or keysym in SLIDER_PAGE_KEYS:
            return self._on_entry_commit(element)
        return None

    def _on_slider_moved(self, element, value):
        """The slider writes the entry: an int field gets a whole number."""
        entry = self._entry_for(element)
        var = entry.get("var")
        if var is None or entry.get("is_syncing"):
            return
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        text = (str(int(round(number))) if element.get("value_type") == "int"
                else f"{number:.{int(element.get('decimals') or 0)}f}"
                if element.get("decimals") is not None else f"{number:g}")
        if var.get() != text:
            var.set(text)
            self._bounds_hint(element)

    def _sync_slider(self, element, text=None):
        """The entry moves the slider, clamped to the slider's travel. A text
        that is not a number leaves the slider where it is."""
        entry = self._entry_for(element)
        scale, var = entry.get("scale"), entry.get("var")
        travel = element.get("slider")
        if scale is None or not travel:
            return
        if text is None:
            text = var.get() if var is not None else ""
        try:
            number = float(text)
        except (TypeError, ValueError):
            return
        low, high = travel
        entry["is_syncing"] = True
        try:
            scale.set(max(low, min(high, number)))
        except Exception:
            pass
        finally:
            entry["is_syncing"] = False

    def _attach_validator(self, widget, element):
        try:
            command = (self.frame.register(
                lambda text, el=element: self._validate_entry(text, el)), "%P")
            widget.configure(validate="key", validatecommand=command)
        except Exception as exc:
            events.debug("Validator Not Attached",
                         f"{element.get('model_attr')}: {exc}", source=SOURCE,
                         exception=exc)

    def _validate_entry(self, text, element):
        """Keystroke validation from the declared type — never from `float()`
        on whatever the box currently holds.

        Guessing numeric-ness from the current contents is RC-6's defect: a
        box the operator had cleared came back as text and lost its validator
        for the rest of the session. Bounds are deliberately NOT enforced per
        keystroke (typing "50" passes through "5", which may be below a
        minimum of 10); `_bounds_hint` colours the field instead, and the
        model refuses out-of-range values as a set when the command runs.
        """
        if element.get("value_type") == "int":
            # An int field takes no decimal point at all — not as a partial
            # entry either, because there is no integer a "." is on the way
            # to. The float pass-throughs below would have let "." stand in
            # the box until the commit refused it (Addendum 2).
            if text in ("", "-", "+"):
                return True
            body = text[1:] if text[0] in "+-" else text
            return body.isdigit()
        if text in ("", "-", "+", ".", "-.", "+."):
            return True
        try:
            number = float(text)
        except ValueError:
            return False
        return number == number and number not in (float("inf"), float("-inf"))

    def _bounds_hint(self, element):
        """Flag an entry whose current text is outside min/max: its underline
        turns ink (E: a warning is ink, never the trace colour). Typing is
        never blocked; the model refuses the set when a command runs."""
        entry = self._entry_for(element)
        widget, var, ring = entry.get("widget"), entry.get("var"), entry.get("ring")
        if widget is None or var is None:
            return
        low, high = element.get("min"), element.get("max")
        try:
            number = float(var.get())
        except (TypeError, ValueError):
            number = None
        is_out = number is not None and ((low is not None and number < low)
                                         or (high is not None and number > high))
        entry["is_out_of_range"] = is_out
        if ring is not None:
            ring.paint(border=theme.TEXT if is_out else INPUT_BORDER)

    def _on_entry_commit(self, element):
        """Return / focus-out / a slider's release commits the typed value
        through the Controller.

        The old view did `setattr(self.model, attr, text)` from the widget
        callback, which wrote unvalidated text straight onto the model. A
        commit is a `_commit` command now: `Panel._apply_inputs` validates it
        and refuses by name. Only this field is sent, so a half-typed value in
        another box cannot refuse this edit; a *command* carries its declared
        inputs plus every edited box through `_gather_inputs` (D-5, MOD-6).
        """
        attr = element.get("model_attr")
        result = self._call("_commit", {attr: self._read_entry(element)})
        previous, self._acting = self._acting, element
        try:
            if result.is_refused:
                self._show_refused(result.reason)
            elif result.is_ok:
                self._show_refused("")
        finally:
            self._acting = previous
        self._sync_slider(element)
        return result

    def _on_dropdown_selected(self, element):
        entry = self._entry_for(element)
        if not entry.get("is_enabled", True):
            return None
        var = entry.get("var")
        shown = var.get() if var else ""
        # The box shows a middle-elided label; the command gets the name.
        full = (entry.get("labels") or {}).get(shown, shown)
        return self._run(element, args=(full,))

    def _refresh_options(self, element):
        """Re-read a dropdown's choices. Never mid-refresh by default: an
        options command can be a port scan."""
        command = element.get("options_command")
        entry = self._entry_for(element)
        widget, var = entry.get("widget"), entry.get("var")
        if not command or widget is None:
            return
        try:
            options = [str(o) for o in self._options(command)]
        except Exception as exc:
            events.debug("Options Failed", f"{command}: {exc}", source=SOURCE,
                         exception=exc, every=5.0)
            return
        shown = var.get() if var is not None else ""
        current = (entry.get("labels") or {}).get(shown, shown)
        if current and current not in options:
            options = [current] + options
        entry["options"] = options
        self._relabel(element)

    def _relabel(self, element):
        """The labels a dropdown shows for its options, at its `chars`."""
        entry = self._entry_for(element)
        widget, options = entry.get("widget"), entry.get("options") or []
        if widget is None:
            return
        # Long names are cut in the MIDDLE so the part that tells two ports
        # or fifty gamepads apart stays in view (F15); a label that would
        # collide with another keeps its whole name.
        limit = entry.get("chars") or DROPDOWN_WIDTH
        labels, seen = {}, set()
        for option in options:
            label = _elide_middle(option, limit)
            if label in seen:
                label = option
            seen.add(label)
            labels[label] = option
        entry["labels"] = labels
        entry["label_of"] = {full: label for label, full in labels.items()}
        values = list(labels)
        if entry.get("values") == values:
            return
        entry["values"] = values
        try:
            widget.configure(values=values)
        except Exception:
            pass

    def _on_region_clicked(self, element):
        # The picture first, then the overlay: the overlay must never be in
        # the screenshot it shows (bench 2026-09-27).
        screenshot = self._region_screenshot(element)
        picker = _RegionPicker(self.frame)
        region = picker.pick(screenshot=screenshot)
        if region is None:
            previous, self._acting = self._acting, element
            try:
                self._show_refused(picker.reason)
            finally:
                self._acting = previous
            return None
        self._show_refused("")
        return self._run(element, args=region)

    def _region_screenshot(self, element):
        """The element's `data_command` (Red Percent's `screen_image`): the
        desktop as `{"image": png, ...}`, or None - no command, a refusal, a
        failure or no capture all mean the picker falls back to its alpha
        overlay rather than not opening."""
        command = element.get("data_command")
        if not command:
            return None
        try:
            result = self._call(command)
        except Exception as exc:
            events.debug("Region Screenshot Failed", f"{self.name}.{command}: "
                         f"{exc}", source=SOURCE, exception=exc)
            return None
        value = result.value if getattr(result, "is_ok", False) else None
        return value if isinstance(value, dict) and value.get("image") else None

    def _on_save_clicked(self, element):
        """Ask for a destination, let the model write, then copy it there.

        The model owns the file it produces and returns its path; the view
        owns the dialog. That split is what lets the same `file_save` element
        render in the Web client, which has no file dialog at all.
        """
        extensions = element.get("extensions") or ["csv"]
        destination = filedialog.asksaveasfilename(
            title=element.get("text", "Save"),
            defaultextension="." + extensions[0],
            filetypes=[(e.upper(), "*." + e) for e in extensions])
        if not destination:
            return None
        result = self._run(element)
        if not result.is_ok or not result.value:
            return result
        try:
            if str(result.value) != str(destination):
                shutil.copyfile(str(result.value), str(destination))
        except OSError as exc:
            events.error("Save Failed", f"could not copy {result.value} to "
                         f"{destination}: {exc}", source=SOURCE, exception=exc)
        return result

    def _on_open_clicked(self, element):
        extensions = element.get("extensions") or ["csv"]
        path = filedialog.askopenfilename(
            title=element.get("text", "Open"),
            filetypes=[(e.upper(), "*." + e) for e in extensions])
        if not path:
            return None
        return self._run(element, args=(path,))

    def _redraw_plot(self, element, series):
        """The series as a trace polyline over a muted baseline, on the
        ground it sits on; a latched model's line is muted, because a frozen
        line drawn in trace reads as live (design-Sheet.md)."""
        entry = self._entry_for(element)
        canvas = entry.get("widget")
        if canvas is None:
            return
        values = [v for v in list((series or {}).get("y") or [])
                  if isinstance(v, (int, float))]
        is_empty = len(values) < 2
        want = self._empty_plot_px() if is_empty else _design_px(self.PLOT_PX)
        if entry.get("plot_px") != want:
            entry["plot_px"] = want
            try:
                canvas.configure(height=want)
            except Exception:
                pass
        width, height = 360, want
        try:
            # The canvas fills its section; draw to the size it was given.
            measured = canvas.winfo_width(), canvas.winfo_height()
            if all(isinstance(v, int) and v > 1 for v in measured):
                width = measured[0]
        except Exception:
            pass
        is_frozen = (self._last_state or {}).get("mode") == "latched"
        # The same series at the same size is the same picture: it was
        # deleted and redrawn ten times a second regardless (F21).
        key = (tuple(values), width, height, is_frozen)
        if entry.get("plot_key") == key:
            return
        entry["plot_key"] = key
        try:
            canvas.delete("all")
        except Exception:
            return
        if is_empty:
            # An empty plot says why, in the model's words, on one line at
            # the left like any caption (REDPERCENT-17, L15).
            try:
                canvas.create_text(0, height / 2, text=self._empty_text(element),
                                   anchor="w", fill=theme.MUTED,
                                   font=_caption_font())
            except Exception:
                pass
            return
        low, high = min(values), max(values)
        span = (high - low) or 1.0
        step = width / max(len(values) - 1, 1)
        points = []
        for index, value in enumerate(values):
            points.append(index * step)
            points.append(height - ((value - low) / span) * (height - 10) - 5)
        try:
            canvas.create_line(*points, fill=theme.MUTED if is_frozen else theme.TRACE,
                               width=2)
        except Exception as exc:
            events.debug("Plot Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    @staticmethod
    def _empty_plot_px():
        """One caption line: what an empty plot takes (L15)."""
        return int(_design_px(theme.CAPTION_SIZE) * 1.6) + 2

    @staticmethod
    def _empty_text(element):
        return str(element.get("empty") or "No data yet.")

    def _show_image(self, element, data):
        """PNG bytes (or base64 text) -> PhotoImage. Tk reads PNG natively."""
        entry = self._entry_for(element)
        widget = entry.get("widget")
        if widget is None or not data or entry.get("data") == data:
            return
        encoded = (base64.b64encode(data).decode("ascii")
                   if isinstance(data, (bytes, bytearray)) else str(data))
        try:
            photo = tk.PhotoImage(data=encoded)
            widget.configure(image=photo, text="")
        except Exception as exc:
            events.debug("Image Failed", str(exc), source=SOURCE, exception=exc,
                         every=5.0)
            return
        # Tk drops an image the moment Python does: keep the reference.
        entry["photo"], entry["data"] = photo, data

    def _refresh_log(self, element, lines):
        entry = self._entry_for(element)
        widget = entry.get("feed") if element.get("detached") else entry.get("widget")
        if widget is None:
            return
        text = "\n".join(str(line) for line in list(lines or [])[-40:])
        is_empty = not text.strip()
        if is_empty and element.get("detached"):
            text = self._log_empty_text(element)
        if entry.get("last_text") == text:
            return
        entry["last_text"] = text
        try:
            widget.configure(state="normal",
                             foreground=theme.MUTED if is_empty else theme.TEXT)
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.configure(state="disabled")
            widget.see("end")
        except Exception as exc:
            events.debug("Log Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _make_button(self, container, element):
        parent, place = self._command_slot(container, element)
        place(self._button_label(parent, element, lambda el: self._run(el)))

    def _make_toggle(self, container, element):
        """A two-state command. A model's own stop (`is_estopped`) is not a
        button: it is a small switch (E, tier 3) with its state in words
        beside it; the rail's disc is the stop an operator reaches for."""
        if element.get("model_attr") == "is_estopped":
            parent, place = self._field(container, element)
            switch = _Switch(parent, lambda el=element: self._on_switch_pressed(el),
                             background=_bg(parent))
            place(switch.canvas, "mark")
            words = tk.Label(parent, text=SWITCH_WORDS[False],
                             font=_font(), background=_bg(parent),
                             foreground=theme.TEXT)
            place(words, "mark")
            tooltip = _Tooltip(switch.canvas)
            tooltip.text = str(element.get("tooltip") or "")
            self._register(element, widget=switch.canvas, switch=switch,
                           words=words, tooltip=tooltip)
            return
        if self._needs_caption(element) and self._cursor(container)["layout"] == "cells":
            # "Off" alone does not say what is off: the caption goes over it
            # ("Sync X"), as over a field.
            parent, place = self._field(container, element)
            place(self._button_label(parent, element, lambda el: self._run_toggle(el),
                                     text=element.get("false_text", "")), "mark")
            return
        parent, place = self._command_slot(container, element)
        place(self._button_label(parent, element, lambda el: self._run_toggle(el),
                                 text=element.get("false_text", "")))

    @staticmethod
    def _needs_caption(element):
        """A toggle whose state words do not name it ("On" / "Off" under
        "Sync X") needs its caption; "Enter autonomous mode" names itself."""
        caption = _label(element.get("text", "")).split(" ")
        words = f"{element.get('true_text', '')} {element.get('false_text', '')}".lower()
        return bool(caption[0]) and caption[0].lower() not in words

    def _make_checkbox(self, container, element):
        """A tick box (G3): a `ttk.Checkbutton` on a BooleanVar.

        The variable is the model's value, written by `_set_on` on every
        refresh and never read back: a click sends the NEW value through
        `_run_checkbox`, which reads the model, so a box drawn one refresh
        behind still flips the right way. In a table it is a narrow column
        under its caption ("Launch", said once in the header); elsewhere its
        caption sits in front of it, like a lamp's. It wears the same focus
        ring as a dropdown, and its hover text is the schema's `tooltip`.
        """
        parent, place = self._field(container, element)
        var = tk.BooleanVar(value=False)
        ring = _Ring(parent, _bg(parent), border=_bg(parent))
        widget = ttk.Checkbutton(ring.inner, variable=var, takefocus=1,
                                 style=CHECK_STYLE,
                                 command=lambda el=element: self._on_checkbox_clicked(el))
        widget.pack(fill="both", expand=True)
        place(ring.outer, "mark")
        widget.bind("<FocusIn>", lambda _e, r=ring: r.paint(True))
        widget.bind("<FocusOut>", lambda _e, r=ring: r.paint(False))
        tooltip = _Tooltip(widget)
        tooltip.text = str(element.get("tooltip") or "")
        self._register(element, widget=widget, var=var, cell=ring.outer,
                       ring=ring, tooltip=tooltip)
        caption = self._cursor(container).get("caption")
        if self._cursor(container)["layout"] == "row" and caption is not None:
            # The row's name and its tick are one target (L4): Setup's
            # "Stepper Probe" ticks the Stepper Probe's Launch box.
            try:
                caption.configure(cursor="hand2")
            except Exception:
                pass
            caption.bind("<Button-1>",
                         lambda _e, el=element: self._on_row_name_pressed(el))

    def _on_checkbox_clicked(self, element):
        """Tk has already flipped the variable. A greyed box runs nothing and
        is put back to what the model holds."""
        entry = self._entry_for(element)
        if not entry.get("is_enabled", True):
            values = (self._last_state or {}).get("values") or {}
            self._set_on(element, bool(values.get(element.get("model_attr"))))
            return None
        return self._run_checkbox(element)

    def _on_row_name_pressed(self, element):
        """A press on a table row's name ticks its box, as a press on the
        box does: the model's value is read and the new one sent."""
        if not self._entry_for(element).get("is_enabled", True):
            return "break"
        self._run_checkbox(element)
        try:
            self._entry_for(element)["widget"].focus_set()
        except Exception:
            pass
        return "break"

    def _on_switch_pressed(self, element):
        if not self._entry_for(element).get("is_enabled", True):
            return None
        return self._run_toggle(element)

    def _make_dropdown(self, container, element):
        """A dropdown whose choices are re-read when it is opened
        (`postcommand`) and every `OPTIONS_REFRESH_MS` — the ⟳ glyph beside
        every dropdown did what opening it now does."""
        parent, place = self._field(container, element)
        var = tk.StringVar(value="")
        is_table = self._cursor(container)["layout"] == "row"
        background = _bg(parent)
        # A dropdown is a select (Signature): a neutral key - CAP face,
        # KEY_RIM rim, KEY_LIP lip - with its arrow at the right.
        # How many characters a name may keep: a table's box is sized in
        # characters; a sheet cell's to a value's room.
        chars = (DROPDOWN_WIDTH - 1 if is_table else
                 max(FIELD_WIDTH, (_design_px(VALUE_PX) - SPACE[6])
                     // max(1, _width_px(_font(), "0"))))
        options = dict(textvariable=var, state="readonly",
                       width=DROPDOWN_WIDTH if is_table else chars,
                       postcommand=lambda el=element: self._refresh_options(el))
        if background == theme.SURFACE:
            options["style"] = WELL_COMBO_STYLE
        ring = _Ring(parent, background, border=theme.KEY_RIM, lip="key")
        try:
            widget = ttk.Combobox(ring.inner, font=_key_font(), **options)
        except Exception:
            # ttk takes `font` on a Combobox on current builds; an older one
            # refuses it, and the style's font is then used.
            widget = ttk.Combobox(ring.inner, **options)
        widget.pack(fill="both", expand=True)
        place(ring.outer, "field")
        widget.bind("<<ComboboxSelected>>",
                    lambda _e, el=element: self._on_dropdown_selected(el))
        widget.bind("<FocusIn>", lambda _e, r=ring: r.paint(True))
        widget.bind("<FocusOut>", lambda _e, r=ring: r.paint(False))
        self._register(element, widget=widget, var=var, options=[],
                       tooltip=_Tooltip(widget), cell=ring.outer, ring=ring,
                       chars=chars, labels={}, label_of={})
        if is_table:
            widget.bind("<Configure>",
                        lambda event, el=element: self._fit_dropdown(
                            el, getattr(event, "width", None)), add="+")
        self._refresh_options(element)

    def _fit_dropdown(self, element, width):
        """A table dropdown given less than its request (the window is short
        of width) cuts its names to what it shows, in the middle, so the
        box never hides the end of a name without saying so."""
        entry = self._entry_for(element)
        glyph = _width_px(_font(), "0")
        if not isinstance(width, int) or width <= 1 or not glyph:
            return
        arrow = _line_px()
        chars = max(5, min(DROPDOWN_WIDTH - 1, (width - arrow) // glyph))
        if chars == entry.get("chars"):
            return
        entry["chars"] = chars
        self._relabel(element)
        text = entry.get("last_text")
        if text is not None:
            entry["last_text"] = None
            self._set_text(element, text)

    def _make_region_select(self, container, element):
        parent, place = self._command_slot(container, element)
        place(self._button_label(parent, element, self._on_region_clicked))
        var = tk.StringVar(value=sch.format_region(None))
        shown = tk.Label(parent, textvariable=var, font=_caption_font(), anchor="w",
                         background=_bg(parent), foreground=theme.MUTED)
        place(shown)
        # The captured region is *drawn*, not announced: PySide's confirming
        # modal over an always-on-top overlay is PYSIDE-12's deadlock.
        self._register(element, var=var)
        # A line of commands never continues past the region it reports.
        self._cursor(container)["strip"] = None

    def _make_file_save(self, container, element):
        parent, place = self._command_slot(container, element)
        place(self._button_label(parent, element, self._on_save_clicked))

    def _make_file_open(self, container, element):
        parent, place = self._command_slot(container, element)
        place(self._button_label(parent, element, self._on_open_clicked))

    #: A plot's height once it has a series to draw.
    PLOT_PX = 160

    def _make_plot(self, container, element):
        """A Canvas polyline in the trace colour. No matplotlib: the model
        publishes the series and each renderer draws it (D-6). Until it has
        two points it is one caption line tall and says the schema's own
        sentence (`element["empty"]`, L15/L22): two 600 px panes of "no
        data" stacked Diagnostics out of reach."""
        parent, place = self._wide_slot(container, element.get("text"))
        canvas = tk.Canvas(parent, height=self._empty_plot_px(), width=360,
                           background=_bg(parent), highlightthickness=0)
        place(canvas)
        self._register(element, widget=canvas, plot_px=None)

    def _make_image(self, container, element):
        parent, place = self._wide_slot(container, element.get("text"))
        widget = tk.Label(parent, background=_bg(parent),
                          foreground=theme.MUTED, text=NO_FIGURE,
                          font=_caption_font(), padx=0, pady=0)
        place(widget, "w")
        self._slow_commands.add(element.get("data_command"))
        self._register(element, widget=widget, photo=None, data=None)

    def _make_indicator(self, container, element):
        """A lamp, not a second copy of the label.

        The label went on the lamp as well as in front of it, so a fault
        indicator read "Fault   Fault" and said nothing about the fault. The
        caption names it once; the lamp carries only the state: lit, it is
        filled with ink (signal for a danger lamp - a latch, a fault; never
        the trace colour, which is for changing numbers, E); unlit, it is an
        empty muted ring.
        """
        parent, place = self._field(container, element)
        size = _lamp_px()
        widget = tk.Canvas(parent, width=size, height=size,
                           background=_bg(parent), highlightthickness=0)
        place(widget, "mark")
        self._register(element, widget=widget, lamp=None, size=size)

    @staticmethod
    def _lamp_colors(element, is_on):
        """(fill, ring) of a lamp."""
        if not is_on:
            return "", theme.MUTED
        lit = theme.SIGNAL if element.get("on_role") == "danger" else theme.TEXT
        return lit, lit

    def _make_log_stream(self, container, element):
        """A scrolling feed on the card, or - `detached` (G4) - a command in
        its place that opens the feed in a window of its own."""
        if element.get("detached"):
            parent, place = self._command_slot(container, element)
            place(self._button_label(parent, element, self._open_log_window,
                                     text=_label(element.get("text", "")) + ELLIPSIS))
            self._register(element, window=None, feed=None, last_text=None)
            return
        parent, place = self._wide_slot(container, element.get("text"))
        widget = self._log_text(parent)
        place(widget)
        self._register(element, widget=widget, last_text=None)

    @staticmethod
    def _log_text(parent, lines=EVENT_LOG_LINES + 1):
        """The feed itself: read-only text in the small step, in a well of
        the other ground (no box, E)."""
        return tk.Text(parent, height=lines, width=48,
                       state="disabled", relief="flat", font=_font(SMALL),
                       background=_field_ground(_bg(parent)), foreground=theme.TEXT,
                       highlightthickness=0, padx=SPACE[3], pady=SPACE[3],
                       wrap="word")

    # -- a detached log stream's window (G4, I1) --------------------------
    #: The feed's natural size, in characters and lines; the least it may
    #: shrink to where the screen is short of room.
    LOG_FEED_CHARS, LOG_FEED_LINES = 48, 12
    LOG_MIN_CHARS, LOG_MIN_LINES = 24, 4
    #: The window's padding, the feed's border and the scrollbar, for when
    #: Tk cannot measure the window itself.
    LOG_CHROME_PX = 48

    #: Set by the dashboard: -> {"page": rect, "free": rect}, the notebook's
    #: rect and the event tray's, on the screen. Without it the panel's own
    #: frame is the page and there is no free region.
    log_window_bounds = None

    #: The station window's menubar, set by the dashboard. A log window
    #: wears the same object (I7): on Aqua a Toplevel with no menu shows the
    #: system's defaults, and Models and Setup left the menu bar while the
    #: log had focus. Elsewhere it is the same menu inside the log window.
    menubar = None

    def share_menubar(self, menubar):
        """Hand every open log window the station's (new) menubar."""
        self.menubar = menubar
        for entry in list(self._widgets.values()):
            window = entry.get("window")
            if window is not None:
                self._wear_menubar(window)

    def _wear_menubar(self, window):
        if self.menubar is None:
            return
        try:
            window.configure(menu=self.menubar)
        except Exception as exc:
            events.debug("Log Window Menu Refused", str(exc), source=SOURCE,
                         exception=exc)

    def _open_log_window(self, element):
        """ONE non-modal window per stream: pressing again raises it. No
        grab and no `wait_window`, so the stop disc and Ctrl+. work while
        it is open; Escape and the close button close it."""
        entry = self._entry_for(element)
        window = entry.get("window")
        if window is not None:
            try:
                if window.winfo_exists():
                    window.deiconify()
                    window.lift()
                    (entry.get("feed") or window).focus_set()
                    if entry.get("feed_ring") is not None:
                        entry["feed_ring"].paint(True)
                    return window
            except Exception:
                pass
            entry["window"] = entry["feed"] = None
        label = _label(element.get("text", ""))
        window = tk.Toplevel(self.frame)
        try:
            window.title(self._log_title(element))
        except Exception:
            pass
        window.configure(background=_page())
        self._wear_menubar(window)
        close = lambda _event=None, el=element: self._close_log_window(el)
        # The foot first: packed at the bottom before the feed takes the
        # rest, so a short window never pushes Close off it (L12).
        foot = tk.Frame(window, background=_page())
        foot.pack(side="bottom", fill="x", padx=SPACE[4], pady=(0, SPACE[4]))
        closer = _Press(foot, CLOSE_WORD, close, _page())
        closer.frame.pack(side="right")
        body = tk.Frame(window, background=_page())
        body.pack(fill="both", expand=True, padx=SPACE[4], pady=SPACE[4])
        scrollbar = ttk.Scrollbar(body, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        # The feed takes the focus on open, in the ring every focusable
        # control wears (O16): the window used to take it with no mark.
        feed_ring = _Ring(body, _page(), border=_page())
        feed_ring.outer.pack(side="left", fill="both", expand=True)
        feed = self._log_text(feed_ring.inner, lines=12)
        feed.pack(fill="both", expand=True)
        try:
            feed.configure(takefocus=1)
        except Exception:
            pass
        feed.bind("<FocusIn>", lambda _e, r=feed_ring: r.paint(True))
        feed.bind("<FocusOut>", lambda _e, r=feed_ring: r.paint(False))
        try:
            feed.configure(yscrollcommand=scrollbar.set)
            scrollbar.configure(command=feed.yview)
        except Exception as exc:
            events.debug("Log Scrollbar Not Wired", str(exc), source=SOURCE,
                         exception=exc)
        window.bind("<Escape>", close)
        try:
            window.protocol("WM_DELETE_WINDOW", close)
        except Exception:
            pass
        self._place_log_window(window, element)
        entry.update(window=window, feed=feed, last_text=None, closer=closer,
                     feed_ring=feed_ring)
        self._refresh_log(element, [])
        events.debug("Log Window Opened", f"{self.name}/{label}", source=SOURCE)
        # Filled now rather than on the next tick.
        data = self._call(element["source_command"])
        if data.is_ok:
            self._refresh_log(element, data.value)
        try:
            feed.focus_set()
        except Exception:
            pass
        feed_ring.paint(True)       # Tk sends FocusIn only once it is mapped
        return window

    def _log_title(self, element):
        """"Stepper Probe gamepad log" (L12): the model, then the feed's
        own name in sentence case - no dash."""
        label = _label(element.get("text", ""))
        return f"{self.name} {label[:1].lower()}{label[1:]}"

    @staticmethod
    def _log_empty_text(element):
        """What an empty feed says: "No gamepad input yet." for the
        gamepad log; "Nothing logged yet." for any other."""
        label = _label(element.get("text", "")).strip()
        if label.lower().endswith(" log") and len(label) > 4:
            return f"No {label[:-4].lower()} input yet."
        return "Nothing logged yet."

    def _log_key(self, element):
        return (self.name, _label((element or {}).get("text", "")))

    def _log_sizes(self, window, decoration):
        """(natural, minimum) outer sizes: the feed's own request when Tk
        can measure it, else estimated from the type scale."""
        chrome = self.LOG_CHROME_PX
        glyph = _width_px(_font(SMALL), "0")
        line = _line_px(SMALL)
        try:
            window.update_idletasks()
            width, height = window.winfo_reqwidth(), window.winfo_reqheight()
        except Exception:
            width = height = None
        if not (isinstance(width, int) and isinstance(height, int)
                and width > 1 and height > 1):
            width = glyph * self.LOG_FEED_CHARS + chrome
            height = line * self.LOG_FEED_LINES + chrome
        minimum = (min(width, glyph * self.LOG_MIN_CHARS + chrome),
                   min(height, line * self.LOG_MIN_LINES + chrome) + decoration)
        return (width, height + decoration), minimum

    def _place_log_window(self, window, element=None):
        """Put the window where it covers no control of this panel (I1).

        It used to open at a fixed 520x300 at the station window's top
        right, which is where a probe's Safety column is: its Stop disc,
        Fault lamp, Step, the opener and every Configuration value. Now it
        goes where the operator last dragged it this session, else outside
        the station window, else over the event tray - never over the page
        (`_log_window_rect`). Sized to its feed, not a constant.
        """
        try:
            owner = self.frame.winfo_toplevel()
            decoration = max(0, owner.winfo_rooty() - owner.winfo_y())
            station = (owner.winfo_x(), owner.winfo_y(), owner.winfo_width(),
                       owner.winfo_height() + decoration)
            screen = (0, 0, owner.winfo_screenwidth(), owner.winfo_screenheight())
            if not all(isinstance(v, int) for v in station + screen):
                raise TypeError("no geometry to place against")
        except Exception as exc:
            events.debug("Log Window Unplaced", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
            return None
        natural, minimum = self._log_sizes(window, decoration)
        bounds = {}
        if callable(self.log_window_bounds):
            try:
                bounds = self.log_window_bounds() or {}
            except Exception as exc:
                events.debug("Log Window Bounds Failed", str(exc), source=SOURCE,
                             exception=exc, every=5.0)
        remembered = _LOG_POSITIONS.get(self._log_key(element))
        if remembered is not None:
            width, height = natural
            x = _clamp(remembered[0], 0, max(0, screen[2] - width))
            y = _clamp(remembered[1], 0, max(0, screen[3] - height))
            rect = (x, y, min(width, screen[2]), min(height, screen[3]))
        else:
            rect = _log_window_rect(station, screen, natural, minimum,
                                    free=bounds.get("free"), gap=SPACE[4])
        x, y, width, height = rect
        geometry = f"{width}x{max(1, height - decoration)}+{x}+{y}"
        try:
            window.geometry(geometry)
        except Exception as exc:
            events.debug("Log Window Geometry Refused", str(exc), source=SOURCE,
                         exception=exc)
            return None
        if element is not None:
            self._entry_for(element)["placed"] = (x, y)
        events.debug("Log Window Placed", f"{self.name}: {geometry}", source=SOURCE)
        return rect

    def _remember_log_position(self, element, window):
        """A window the operator moved goes back there next time."""
        placed = self._entry_for(element).get("placed")
        try:
            now = _parse_geometry(window.geometry())
        except Exception:
            now = None
        if now is None or placed is None:
            return
        if abs(now[0] - placed[0]) > 2 or abs(now[1] - placed[1]) > 2:
            _LOG_POSITIONS[self._log_key(element)] = now[:2]

    def _close_log_window(self, element, restore_focus=True):
        entry = self._entry_for(element)
        window = entry.get("window")
        entry.update(window=None, feed=None, last_text=None)
        if window is not None:
            self._remember_log_position(element, window)
            try:
                window.destroy()
            except Exception:
                pass
            events.debug("Log Window Closed",
                         f"{self.name}/{_label(element.get('text', ''))}",
                         source=SOURCE)
        button = entry.get("widget")
        if restore_focus and button is not None:
            try:
                button.focus_set()      # F12: focus goes back where it came from
            except Exception:
                pass
        return "break"

    def _wants_data(self, element):
        """A detached stream is polled only while its window is open, and
        nothing in a closed tier is polled at all: Red Percent's plot and
        its analysis figure are on demand (E)."""
        if element.get("type") == "log_stream" and element.get("detached"):
            return self._entry_for(element).get("window") is not None
        return self.is_disclosed(self._entry_for(element).get("tier") or 1)

    def _make_internal(self, container, element):
        """Renders nothing. The element exists so its command is in the
        schema-derived allow-list."""
        return None

    # -- what base.PanelView drives ---------------------------------------
    def _read_entry(self, element):
        var = self._entry_for(element).get("var")
        return var.get() if var is not None else ""

    def _entry_is_dirty(self, element):
        """True while the operator owns the box: it holds focus, or its text
        differs from what the last refresh put there.

        Without this the 100 ms tick overwrites the field between keystrokes
        and it cannot be typed into at all.
        """
        entry = self._entry_for(element)
        widget, var = entry.get("widget"), entry.get("var")
        if widget is None or var is None:
            return False
        try:
            if widget.focus_get() is widget:
                return True
        except Exception:
            pass
        return var.get() != entry.get("last_text", "")

    def _entry_is_edited(self, element):
        """Typed and not committed (MOD-6): the text differs from what the
        last refresh put there. Focus alone is not an edit - a focused box
        is not refreshed, so its unchanged text may be a value behind, and
        sending it would put that stale value back."""
        entry = self._entry_for(element)
        var = entry.get("var")
        if entry.get("widget") is None or var is None:
            return False
        return var.get() != entry.get("last_text", "")

    def _display_text(self, element, text):
        """What an int field shows: an integer, with no decimal point.

        `Param.format` already renders a declared `int` as `str(int(number))`,
        so a model whose Param table matches its schema arrives here correct.
        This is the backstop for the values that reach a view without a Param
        behind them — `Setup.scan_progress` is one, a plain attribute the
        schema types and `Panel._text_for` hands over as `str(raw)` — because
        an int box showing "5.000" while its validator refuses "." is a
        contradiction the operator has to look at (Addendum 2).
        """
        if element["type"] == "readonly" and not str(text).strip():
            # A readout with no value says so. It never says it with an empty
            # coloured label, which is a stripe of colour with no meaning.
            return EMPTY_READOUT
        if element["type"] == "readonly" and str(text).strip().lower() in ("true", "false"):
            # A boolean reads as the operator would say it, as in the Web and
            # Qt views ("Yes" / "No"), not as a programming literal.
            return "Yes" if str(text).strip().lower() == "true" else "No"
        if element.get("value_type") != "int" or not text:
            return text
        try:
            number = float(text)
        except (TypeError, ValueError):
            return text
        return str(int(number))

    def _style_readout(self, element, text):
        """A readout is ink at rest and the trace colour only while its value
        is changing (E: trace is for changing numbers). A danger readout (a
        fault reason) is ink with a signal mark beside it (F14). Nothing to
        show is `--` in muted ink; an entry whose readings are stale or whose
        link is lost mutes every value, because a frozen number in trace or
        ink reads as live (F3). A table's words are text, never trace."""
        entry = self._entry_for(element)
        widget = entry.get("widget")
        if widget is None:
            return
        is_danger = (element.get("role") or "neutral") == "danger"
        changed_at = entry.get("changed_at")
        is_changing = (changed_at is not None and not entry.get("is_text")
                       and _is_number(text)
                       and time.monotonic() - changed_at < CHANGING_S)
        self._paint_empty(entry, text == EMPTY_READOUT)
        if text == EMPTY_READOUT or self._is_quiet():
            foreground = theme.MUTED
        elif is_changing and not is_danger:
            foreground = theme.TRACE
        else:
            foreground = theme.TEXT
        marked = is_danger and text not in ("", EMPTY_READOUT)
        if entry.get("colors") != foreground:
            entry["colors"] = foreground
            try:
                widget.configure(foreground=foreground)
            except Exception:
                pass
        mark = entry.get("mark")
        if mark is not None and entry.get("marked") != marked:
            entry["marked"] = marked
            try:
                mark.delete("all")
                if marked:
                    size = int(mark.cget("width") or LAMP_PX)
                    mark.create_rectangle(2, 2, size - 2, size - 2, fill=theme.SIGNAL,
                                          outline=theme.SIGNAL)
            except Exception:
                pass

    def _paint_empty(self, entry, is_empty):
        """"--" is no value (TK7-14, L22): muted, at the reading's own size
        so a tier-1 position never shrinks away, but in the text face at
        regular weight - in the bold numeral face it drew two heavy bars
        that read as a redaction. The unit goes while there is no number."""
        font = entry.get("font")
        widget = entry.get("widget")
        if font is None or widget is None:
            return
        want = (_TEXT_FAMILY, font[1], "normal") if is_empty else font
        if entry.get("drawn_font") != want:
            entry["drawn_font"] = want
            try:
                widget.configure(font=want)
            except Exception:
                pass
        unit = entry.get("unit_label")
        if unit is not None and entry.get("unit_hidden") != is_empty:
            entry["unit_hidden"] = is_empty
            try:
                if is_empty:
                    unit.pack_forget()
                else:
                    unit.pack(side="left", anchor="s", padx=(SPACE[1], 0))
            except Exception:
                pass

    def _is_quiet(self):
        """True while the entry's values cannot be trusted as live."""
        return bool(self._is_stale) or bool(self.lost_devices)

    def _set_text(self, element, text):
        entry = self._entry_for(element)
        var = entry.get("var")
        if var is None:
            return
        raw = "" if text is None else str(text)
        text = self._display_text(element, raw)
        if element["type"] == "readonly":
            previous = entry.get("shown_text")
            if previous not in (None, "", EMPTY_READOUT) and previous != text:
                entry["changed_at"] = time.monotonic()
            entry["shown_text"] = text
            self._style_readout(element, text)
            if ((entry.get("tier") or 1) == 1 and entry.get("box") is not None
                    and not element.get("rail")):
                # Status by exception (E): a normal status is silence. A
                # reading is never hidden - no value is shown as "--".
                self._set_shown(element, raw.strip() not in theme.QUIET_VALUES
                                and text not in theme.QUIET_VALUES)
        if element["type"] == "dropdown":
            options = entry.get("options") or []
            if text and text not in options:
                self._refresh_options(element)
            shown = (entry.get("label_of") or {}).get(text) or _elide_middle(
                text, entry.get("chars") or DROPDOWN_WIDTH)
            tooltip = entry.get("tooltip")
            if tooltip is not None:
                # The whole name, whenever the box shows less of it.
                tooltip.text = text if shown != text else ""
            if var.get() != shown:
                var.set(shown)
            entry["last_text"] = text
            return
        if var.get() != text:
            var.set(text)
        entry["last_text"] = text
        if element["type"] == "readonly":
            self._fit_readout(element)
        if element["type"] == "entry":
            self._bounds_hint(element)
            self._sync_slider(element, text)

    def _set_on(self, element, is_on):
        entry = self._entry_for(element)
        widget = entry.get("widget")
        if widget is None:
            return
        was_on = entry.get("is_on")
        entry["is_on"] = is_on
        if entry.get("switch") is not None:
            entry["switch"].set_on(is_on)
            words = entry.get("words")
            text = SWITCH_WORDS[bool(is_on)]
            tooltip = entry.get("tooltip")
            if tooltip is not None:
                tooltip.text = str(element.get("tooltip_on" if is_on else "tooltip")
                                   or element.get("tooltip") or "")
            try:
                if words is not None and words.cget("text") != text:
                    words.configure(text=text)
            except Exception:
                pass
            return
        if element["type"] == "checkbox":
            # Every refresh, not only on a change: the widget flips its own
            # variable on a click, and the model has the last word.
            var = entry.get("var")
            if var is not None and var.get() != bool(is_on):
                var.set(bool(is_on))
            return
        if element["type"] == "indicator":
            # Never text: the caption in front of the lamp already names it,
            # and a lamp that repeats its own label ("Fault   Fault") is the
            # one thing it must not say.
            lamp = self._lamp_colors(element, is_on)
            if entry.get("lamp") == lamp:
                return
            entry["lamp"] = lamp
            size = entry.get("size") or LAMP_PX
            try:
                widget.delete("all")
                widget.create_oval(2, 2, size - 2, size - 2, fill=lamp[0],
                                   outline=lamp[1], width=1.5)
            except Exception as exc:
                events.debug("Lamp Draw Failed", str(exc), source=SOURCE,
                             exception=exc, every=5.0)
            return
        text = _label(element["true_text"] if is_on else element["false_text"])
        try:
            if widget.cget("text") != text:
                widget.configure(text=text)
        except Exception as exc:
            events.debug("Toggle Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        if was_on != is_on:
            self._paint_command(element)

    def _set_data(self, element, data):
        kind = element["type"]
        if kind == "plot":
            self._redraw_plot(element, data)
        elif kind == "log_stream":
            self._refresh_log(element, data)
        elif kind == "image":
            self._show_image(element, data)

    def _set_enabled(self, element, is_enabled):
        """Gate entries too, which neither desktop view did.

        The flag is authoritative rather than the widget's `state` option: a
        `tk.Label` styled as a button has no usable `state`, so the old view's
        `cget("state") == "disabled"` guard was reading an option it had
        failed to set (VIEW-TKINTER-14). A stop disc is gated like any
        command but never drawn dimmed.
        """
        entry = self._entry_for(element)
        if not entry:
            return
        if is_enabled and self._held_by_fault(element):
            is_enabled = False
        was_enabled = entry.get("is_enabled", True)
        entry["is_enabled"] = bool(is_enabled)
        widget = entry.get("widget")
        if widget is None:
            return
        if element["type"] in ("entry", "dropdown", "checkbox"):
            # A disabled Combobox cannot be opened and a disabled
            # Checkbutton cannot be ticked: the ttk state is the gate the
            # operator meets, the flag is the one the handlers check.
            live = "readonly" if element["type"] == "dropdown" else "normal"
            try:
                widget.configure(state=live if is_enabled else "disabled")
            except Exception:
                pass
            ring = entry.get("ring")
            if ring is not None and ring.lip is not None:
                # A select keeps its silhouette disabled, in ghost tones.
                kind = "neutral" if is_enabled else "disabled"
                _face, _legend, rim, lip = _key_look(kind, ring.background)
                ring.set_lip(lip)
                ring.paint(border=rim)
        elif element["type"] in self.COMMANDS and entry.get("switch") is None:
            if was_enabled != bool(is_enabled) or "painted" not in entry:
                entry["painted"] = True
                self._paint_command(element)
            self._say_why(element, entry, is_enabled)
        scale = entry.get("scale")
        if scale is not None and was_enabled != bool(is_enabled):
            # A disabled slider is muted and does not move (its style maps
            # the disabled state).
            try:
                scale.state(["!disabled"] if is_enabled else ["disabled"])
            except Exception:
                pass
        if was_enabled != bool(is_enabled):
            events.debug("Gate Changed",
                         f"{self.name}/{element.get('text') or element.get('command')}"
                         f" -> {'enabled' if is_enabled else 'disabled'}",
                         source=SOURCE)

    @staticmethod
    def _is_mode_toggle(element):
        return element.get("type") == "toggle" and element.get("command") == "set_mode"

    def _held_by_fault(self, element):
        """A faulted model's mode toggles are drawn disabled (O4): in FAULT
        both read "Enter ... mode", the face of a safe, unpowered probe, and
        a press would energize coils whose last disable never landed. The
        stop disc and the model's own stop are never held."""
        return (self._is_mode_toggle(element)
                and bool((self._last_state or {}).get("is_faulted")))

    def _gate_reason(self, element):
        """Why `element` is greyed out now (L3), from the gate it failed:
        the stop latch first, a fault holding the mode toggles (O4), an
        `enabled_by` value that is off, then the mode, in
        `views.base.gate_reason`'s words (O3). "" when nothing says."""
        state = self._last_state or {}
        mode = state.get("mode") or ""
        values = state.get("values") or {}
        if mode == "latched":
            return LATCHED_REASON
        if self._held_by_fault(element):
            return FAULT_REASON
        by = element.get("enabled_by")
        if by and not values.get(by):
            names = [e.get("text") for e in self._elements
                     if e.get("model_attr") == by and e is not element]
            return f"{_label(names[0])} is off" if names else _label(by.replace("_", " "))
        return view_base.gate_reason(element, mode)

    def _say_why(self, element, entry, is_enabled):
        """A disabled command carries its reason as hover text; a `go`
        command also says it in one muted caption under its row (L3)."""
        reason = "" if is_enabled else self._gate_reason(element)
        tip = entry.get("gate_tip")
        if tip is not None:
            tip.text = reason
        if element.get("role") != "go" or entry.get("why_reason") == reason:
            return
        entry["why_reason"] = reason
        slot = entry.get("slot")
        if slot is None:
            return
        container = slot[0]
        why = self._why_labels.get(id(container))
        if why is None:
            if not reason:
                return
            why = tk.Label(container, text="", font=_caption_font(), anchor="w",
                           justify="left", background=_bg(container),
                           foreground=theme.MUTED)
            self._why_labels[id(container)] = why
        # One caption per row: the first greyed go command in it speaks.
        reasons = [self._entry_for(e).get("why_reason") for e in self._elements
                   if e.get("role") == "go"
                   and (self._entry_for(e).get("slot") or (None,))[0] is container]
        text = next((r for r in reasons if r), "")
        try:
            why.configure(text=text)
            if text:
                why.grid(row=self.WHY_ROW, column=0, columnspan=max(1, slot[3]),
                         sticky="w", pady=(0, GAP))
            else:
                why.grid_forget()
        except Exception as exc:
            events.debug("Reason Not Shown", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _set_stale(self, is_stale):
        """Grey the panel title when the model's state has stopped updating."""
        if is_stale == self._is_stale:
            return
        self._is_stale = is_stale
        self._paint_health()
        events.debug("Staleness Changed", f"{self.name} stale={is_stale}",
                      source=SOURCE)

    def _set_lost(self, lost):
        """A device whose link is lost (F3, HC-1): the model says so in
        `state.devices` and nothing else on the panel did - the toggle stayed
        lit and the numbers stayed trace. Now the grouping rule turns signal,
        the title and a line under it say what was lost, and every value is
        muted."""
        lost = tuple(lost)
        if lost == self.lost_devices:
            return
        self.lost_devices = lost
        self._paint_health()
        events.debug("Link State Changed", f"{self.name} lost={list(lost)}",
                     source=SOURCE)

    def _paint_health(self):
        """The head rule is 2 px of ink; it turns signal while a device link
        is lost (F3) or while this model's stop did not confirm (E), and the
        head says which in words."""
        lost, is_stale = self.lost_devices, bool(self._is_stale)
        if lost:
            title = f"{self.name} (connection lost)"
        elif is_stale:
            title = f"{self.name} (stale)"
        else:
            title = self.name
        heading = theme.MUTED if is_stale and not lost else theme.TEXT
        is_alarm = bool(lost) or self._is_unconfirmed or bool(self._fault)
        try:
            self._title.configure(foreground=heading, text=title)
            self._rule.configure(background=theme.SIGNAL if is_alarm
                                 else theme.RULE_STRONG,
                                 height=theme.RULE_STRONG_PX)
        except Exception:
            pass
        try:
            if lost:
                self._health.configure(text=(
                    f"Connection lost: {_device_list(lost)}. Its readings are "
                    "frozen. Press Stop, check the cable, then relaunch from "
                    "Setup."))
                self._health.pack(fill="x", padx=self._inset, pady=(0, GAP),
                                  after=self._head_ring.outer)
            else:
                self._health.configure(text="")
                self._health.pack_forget()
        except Exception:
            pass
        for element in self._elements:
            if element["type"] == "readonly":
                entry = self._entry_for(element)
                entry["colors"] = None
                self._style_readout(element, entry.get("shown_text", ""))

    def set_unconfirmed(self, is_unconfirmed):
        """This model's stop did not confirm (E): a signal head rule and the
        words "Stop not confirmed. Treat as live." at its own entry, for as
        long as the latch it describes."""
        self.set_hazard(is_unconfirmed, self._fault)

    def set_hazard(self, is_unconfirmed, fault=""):
        """The two ways a model can be live while it looks safe, marked the
        same way at its own entry (E, O4): a stop that did not confirm, and
        a disable that failed (`is_faulted`). A signal head rule, the words,
        and for a fault the model's own reason under them. A stop that did
        not confirm keeps its words; the reason still shows."""
        is_unconfirmed, fault = bool(is_unconfirmed), str(fault or "").strip()
        if (is_unconfirmed, fault) == (self._is_unconfirmed, self._fault):
            return
        was_marked = self._is_unconfirmed or bool(self._fault)
        self._is_unconfirmed, self._fault = is_unconfirmed, fault
        is_marked = is_unconfirmed or bool(fault)
        try:
            if not is_marked:
                self._mark.delete("all")
            elif not was_marked:
                self._draw_flag()
            self._mark_text.configure(text=UNCONFIRMED_LINE if is_unconfirmed
                                      else FAULTED_LINE)
            self._mark_reason.configure(text=fault)
            if fault:
                self._mark_reason.pack(side="top", anchor="w", fill="x")
            else:
                self._mark_reason.pack_forget()
            if is_marked:
                self._mark_row.pack(fill="x", padx=self._inset, pady=(0, GAP),
                                    after=self._head_ring.outer)
            else:
                self._mark_row.pack_forget()
        except Exception as exc:
            events.debug("Unconfirmed Mark Failed", str(exc), source=SOURCE,
                         exception=exc)
        events.debug("Entry Hazard Changed", f"{self.name} unconfirmed="
                     f"{is_unconfirmed} faulted={bool(fault)}", source=SOURCE)
        self._paint_health()

    def _draw_flag(self):
        """The tripped flag: an ink frame, the SIGNAL flag in its window,
        an ink hatch across it (`theme.FLAG`). Tk draws it in place; the
        Web's 200 ms drop is the one authored motion and stays Web-only."""
        canvas = self._mark
        width, height = (_design_px(v) for v in theme.FLAG["size"])
        inset = SPACE[0]
        window = (inset, inset, width - inset, height - inset)
        self._flag_draws += 1
        try:
            canvas.delete("all")
            canvas.create_rectangle(0, 0, width - 1, height - 1, fill=theme.FLAG["frame"],
                                    outline=theme.FLAG["frame"], tags=("frame",))
            canvas.create_rectangle(*window, fill=theme.FLAG["fill"],
                                    outline=theme.FLAG["fill"], tags=("flag",))
            # Three hatch bars at the Web's slope, cut to the window.
            step = width / 3
            for index in range(3):
                x = index * step - inset
                segment = _clip_line((x, height + inset), (x + height * 2 / 3, -inset),
                                     window)
                if segment is not None:
                    canvas.create_line(*segment, fill=theme.FLAG["hatch"],
                                       width=_design_px(SPACE[0]), tags=("hatch",))
        except Exception as exc:
            events.debug("Flag Draw Failed", str(exc), source=SOURCE, exception=exc)

    def _confirm(self, prompt):
        return _confirm(self.frame, prompt)

    def _show_refused(self, reason):
        """Non-modal, and AT the control that caused it (F10): the row
        under it, wrapped, scrolled into view, gone on the next success.
        With no control to point at it is the line at the foot of the
        panel. A refusal used to land in one place for every control and
        was cut off at about 60 % of its length."""
        self._clear_notice()
        self._notice_text = reason or ""
        if not reason:
            try:
                self._status.configure(text="")
                self._status.pack_forget()
            except Exception:
                pass
            return
        slot = self._entry_for(self._acting).get("slot") if self._acting else None
        if slot is None:
            # A refusal with no control to sit under: a line at the foot of
            # the entry, packed only while it has something to say.
            try:
                self._status.configure(text=reason, foreground=theme.TEXT,
                                       background=theme.SURFACE)
                self._status.pack(fill="x", side="bottom", padx=self._inset,
                                  pady=(0, GAP))
            except Exception:
                pass
            return
        container, row, column, span = slot
        try:
            # Ink on the other ground: a refusal is something to read, and
            # it is set off by tone, never by the trace or the signal colour.
            notice = tk.Label(container, text=reason, font=_font(), anchor="w",
                              justify="left", wraplength=self._notice_room(container),
                              background=_counter(_bg(container)),
                              foreground=theme.TEXT, padx=SPACE[3], pady=SPACE[1])
            notice.grid(row=row, column=column, columnspan=span, sticky="ew",
                        pady=(0, GAP))
        except Exception as exc:
            events.debug("Refusal Not Placed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
            return
        self._notice = notice
        self._scroll_into_view(notice)

    def _clear_notice(self):
        if self._notice is not None:
            try:
                self._notice.destroy()
            except Exception:
                pass
            self._notice = None
        try:
            self._status.configure(background=_page())
        except Exception:
            pass

    def _notice_room(self, container):
        """Wrap width for a refusal: its container's width when Tk knows it,
        else the panel's."""
        for widget in (container, self._canvas):
            try:
                width = widget.winfo_width()
            except Exception:
                continue
            if isinstance(width, int) and width > SPACE[6] * 8:
                return width - 2 * SPACE[3]
        return 640

    def _scroll_into_view(self, widget):
        """Scroll the panel just enough that `widget` is fully visible. On
        the sheet, the sheet scrolls."""
        if self._sheet is not None:
            if self._scroll_well_to(widget):
                return
            scroll = getattr(self._sheet, "scroll_into_view", None)
            if callable(scroll):
                scroll(widget)
            return
        try:
            widget.update_idletasks()
            top, node = 0, widget
            while node is not None and node is not self._body:
                top += node.winfo_y()
                node = node.master
            bottom = top + widget.winfo_reqheight()
            total = self._body.winfo_height()
            first, last = self._canvas.yview()
        except Exception:
            return
        numbers = (top, bottom, total, first, last)
        if not all(isinstance(v, (int, float)) for v in numbers) or total <= 1:
            return
        view_top, view_bottom = first * total, last * total
        if top >= view_top and bottom <= view_bottom:
            return
        if top < view_top:
            target = top - SPACE[4]
        else:
            target = bottom + SPACE[4] - (view_bottom - view_top)
        try:
            self._canvas.yview_moveto(max(0.0, min(1.0, target / total)))
        except Exception:
            pass

    def _run(self, element, args=()):
        """`PanelView._run`, remembering which control is acting so its
        refusal can be drawn at it. A failed command is already on the
        alert band; the refusal line from an earlier attempt is cleared
        rather than left to describe something else."""
        previous, self._acting = self._acting, element
        try:
            result = super()._run(element, args)
        finally:
            self._acting = previous
        if result is not None and getattr(result, "is_failed", False):
            self._show_refused("")
        return result

    def _state(self):
        state = super()._state()
        self._last_state = state if isinstance(state, dict) else {}
        return state

    def _apply_theme(self):
        widgets = (self.frame, self._body) if self._sheet is not None else (
            self.frame, self._body, self._canvas)
        for widget in widgets:
            try:
                widget.configure(background=_page())
            except Exception:
                pass

    @staticmethod
    def _hazard_of(state):
        """(stop did not confirm, the fault's reason or "") from a state."""
        state = state if isinstance(state, dict) else {}
        return (state.get("stop_confirmed") is False,
                (state.get("fault") or "") if state.get("is_faulted") else "")

    def _refresh(self):
        super()._refresh()
        if self._panel is None:
            # The model's own word on its stop (L1): a latch whose hardware
            # did not confirm is marked here however it was set - the disc,
            # the chord, the gamepad or the model's own switch. A failed
            # disable is marked the same way (O4).
            state = self._last_state
            is_unconfirmed, fault = self._hazard_of(state)
            if not self._is_opened:
                # The overview entry of a host carries the stop state of the
                # models drawn on its page (they have no entry of their own).
                for name in self._hosted:
                    try:
                        other = self.controller.state(name)
                    except Exception:
                        continue
                    other_unconfirmed, other_fault = self._hazard_of(other)
                    is_unconfirmed = is_unconfirmed or other_unconfirmed
                    if other_fault and not fault:
                        fault = f"{name}: {other_fault}"
            self.set_hazard(is_unconfirmed, fault)
            if self._state_word is not None:
                try:
                    mode = str(state.get("mode") or "").replace("_", " ")
                    self._state_word.configure(text=_sentence(mode))
                except Exception:
                    pass
        devices = self._last_state.get("devices") or {}
        self._set_lost([name for name, status in sorted(devices.items())
                        if str(status) == "lost"])
        self._options_due -= self.REFRESH_MS
        if self._options_due <= 0:
            self._options_due = self.OPTIONS_REFRESH_MS
            for element in self._elements:
                if element["type"] == "dropdown":
                    self._refresh_options(element)


class _Sheet:
    """The scrolling sheet the model entries sit on (E): one canvas, one
    body, one wheel. An entry is laid out on it by the dashboard and never
    scrolls by itself; the rail - and the stop in it - never scrolls at all.
    """

    WHEEL_EVENTS = TkPanelView.WHEEL_EVENTS

    def __init__(self, master):
        self.frame = tk.Frame(master, background=_page())
        self.scrollbar = ttk.Scrollbar(self.frame, orient="vertical")
        self.canvas = tk.Canvas(self.frame, background=_page(), highlightthickness=0,
                                yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, background=_page(), padx=SPACE[10],
                             pady=SPACE[9])
        self.is_scrollbar_shown = False
        # The device page (L5): the body is exactly as tall as the sheet -
        # or `fit_floor()` when that is taller - so the page itself does not
        # scroll and the opened model's well scrolls instead
        # (`wheel_target`). The overview scrolls as it always has.
        self.fit = False
        self.fit_floor = None           # -> px the page needs at least
        self.wheel_target = None        # (step) -> True when it scrolled
        self.viewport = 0
        self.fitted = 0                 # the window height last applied
        try:
            self.scrollbar.configure(command=self.canvas.yview)
            self.window = self.canvas.create_window((0, 0), window=self.body,
                                                    anchor="nw")
        except Exception as exc:
            self.window = None
            events.debug("Sheet Not Wired", str(exc), source=SOURCE, exception=exc)
        self.body.bind("<Configure>", self._on_body_resized)
        self.canvas.bind("<Configure>", self._on_canvas_resized)
        self.frame.bind("<Enter>", self._on_pointer_enter)
        self.frame.bind("<Leave>", self._on_pointer_leave)

    def _on_body_resized(self, _event=None):
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception:
            pass
        self._sync_scrollbar()

    def _on_canvas_resized(self, event=None):
        width = getattr(event, "width", 0)
        height = getattr(event, "height", 0)
        if isinstance(height, int) and height > 1:
            self.viewport = height
        if self.window is None or not width:
            return
        try:
            self.canvas.itemconfigure(self.window, width=width)
        except Exception:
            pass
        self.refit()
        self._sync_scrollbar()

    def set_fit(self, is_fit):
        """The device page (fit) or the overview (the body's own height)."""
        self.fit = bool(is_fit)
        self.refit()

    def refit(self):
        """Apply the fit: the viewport's height, or the page's floor when
        that is more. 0 gives the body back its own height. Only a change
        is applied."""
        want = 0
        if self.fit and self.viewport > 1:
            floor = 0
            if callable(self.fit_floor):
                try:
                    floor = int(self.fit_floor() or 0)
                except Exception:
                    floor = 0
            want = max(self.viewport, floor)
        if want == self.fitted or self.window is None:
            return
        self.fitted = want
        try:
            self.canvas.itemconfigure(self.window, height=want)
            if want:
                self.canvas.yview_moveto(0)
        except Exception:
            pass
        self._sync_scrollbar()

    def _sync_scrollbar(self):
        try:
            content = self.body.winfo_reqheight()
            viewport = self.canvas.winfo_height()
        except Exception:
            return
        if not (isinstance(content, int) and isinstance(viewport, int)) or viewport <= 1:
            return
        if self.fit:
            content = max(content, self.fitted)
        needed = content > viewport
        if needed == self.is_scrollbar_shown:
            return
        self.is_scrollbar_shown = needed
        try:
            if needed:
                self.scrollbar.pack(side="right", fill="y", before=self.canvas)
            else:
                self.scrollbar.pack_forget()
                self.canvas.yview_moveto(0)
        except Exception:
            pass

    def _on_pointer_enter(self, _event=None):
        for sequence in self.WHEEL_EVENTS:
            try:
                self.frame.bind_all(sequence, self._on_wheel)
            except Exception:
                pass

    def _on_pointer_leave(self, _event=None):
        for sequence in self.WHEEL_EVENTS:
            try:
                self.frame.unbind_all(sequence)
            except Exception:
                pass

    def _on_wheel(self, event):
        number, delta = getattr(event, "num", 0), getattr(event, "delta", 0)
        if number == 4:
            step = -1
        elif number == 5:
            step = 1
        elif delta:
            step = -1 if delta > 0 else 1
        else:
            return None
        if (self.fit and callable(self.wheel_target)
                and self.wheel_target(step, getattr(event, "widget", None))):
            return "break"
        try:
            self.canvas.yview_scroll(step, "units")
        except Exception:
            pass
        return "break"

    def scroll_to_top(self):
        try:
            self.canvas.yview_moveto(0)
        except Exception:
            pass

    def scroll_into_view(self, widget):
        """Scroll just enough that `widget` is fully on screen."""
        try:
            widget.update_idletasks()
            top, node = 0, widget
            while node is not None and node is not self.body:
                top += node.winfo_y()
                node = node.master
            bottom = top + widget.winfo_reqheight()
            total = self.body.winfo_height()
            first, last = self.canvas.yview()
        except Exception:
            return
        numbers = (top, bottom, total, first, last)
        if not all(isinstance(v, (int, float)) for v in numbers) or total <= 1:
            return
        view_top, view_bottom = first * total, last * total
        if top >= view_top and bottom <= view_bottom:
            return
        target = (top - SPACE[4] if top < view_top
                  else bottom + SPACE[4] - (view_bottom - view_top))
        try:
            self.canvas.yview_moveto(max(0.0, min(1.0, target / total)))
        except Exception:
            pass


class TkDashboard(Dashboard):
    """The window (Bench sheet, E): a rail on the left - the station's name,
    A's stop disc, the model list, Setup and Quit - and the sheet, where
    every open model is an entry, the opened one first and full width.
    Setup is a page of its own in place of the sheet. The event tray and
    the alert band sit under the sheet.

    Everything that decides *policy* — which events pop up, what closing a
    model means, what the stop toggle does — is in `Dashboard`. This class
    owns the widgets and the marshalling onto the Tk thread.
    """

    REFRESH_MS = 200
    SETUP_TAB = "Setup"
    SHEET_TAB = "Models"
    QUIT_PROMPT = ("Quit the station? This stops every model, closes every port "
                   "and exits.")

    def __init__(self, controller, setup):
        super().__init__(controller, setup)
        self._panels = {}       # name -> TkPanelView
        self._frames = {}       # name -> its frame: Setup's page, a model's entry
        self._hosts = {}        # hosted name -> the host whose page draws it
        self._menu_vars = {}    # name -> BooleanVar in the Models menu
        self._after_id = None
        self._is_focused = None
        self._is_setup_collapsed = False
        self._is_opening = False
        self._setup_menu = None      # the "Show Setup" menu, once built
        self._menubar = None         # the menubar every log window wears too (I7)
        self._alerts = []            # unacknowledged needs_ack events
        self._ack_dialog = None      # the one acknowledgement shown (rb-ack)
        self._station_text = None
        self._sim_text = None
        self._opened = None          # the device page's model; None = the overview
        self._bring_forward = None   # a Models-menu reopen: shown on its own page
        self._overview_item = None   # the rail's "Overview": (ring, label)
        self._shown_page = self.SETUP_TAB
        self._sheet_key = None
        self._sheet_rows = []
        self._rail_items = {}        # name -> (ring, label)
        self._stop_seen = None       # (latched, unconfirmed, every) last drawn
        self._rail_marks = {}        # name -> (Canvas, _Tooltip) before its line
        self._rail_lamps = {}        # name (None: Overview) -> its lamp slot
        self._faulted = ()           # models whose disable failed (O4), station order
        self._idle_lines = {}        # name -> the countdown line's widgets (Tier N)
        self._energized = ()         # `state()["energized"]` (O6), station order
        self._energy_marks = {}      # name -> the Canvas for its energized ring
        self._confirm_words = None   # the dialog words for the next `_confirm`
        self._tray_events = []       # the tray's warnings and errors, oldest first
        self._is_tray_open = False
        self._tray_count = 0         # lines in the tray's history
        self._images = []            # PhotoImages ttk draws with (kept alive)
        self._is_narrow = None

        self.root = tk.Tk()
        self.root.title(STATION_TITLE)     # the rail's name, not a third one (TK7-18)
        self.root.geometry("1400x900")
        self.root.configure(background=theme.BACKGROUND)
        try:
            self.root.minsize(720, 520)
        except Exception:
            pass
        _resolve_families(self.root)
        self._configure_styles()

        # Pack order is allocation order. The rail is packed FIRST, on the
        # left, and the stop is the first thing in it: nothing the sheet
        # holds can push the stop off the window (the stop once went off the
        # bottom of the screen the first time a tall panel opened). In the
        # main column the tray and the alert band take their strips at the
        # bottom before the notebook - the one widget that expands - gets
        # what is left. An error never covers, dims or blocks the stop (F1).
        self._build_rail()
        self._main = tk.Frame(self.root, background=theme.BACKGROUND)
        self._main.pack(side="left", fill="both", expand=True)
        self._build_event_panel()
        self._build_alert_band()

        self.notebook = ClosableNotebook(self._main, on_close_tab=self._on_tab_close)
        self.notebook.pack(side="top", fill="both", expand=True)
        # Tab order (TK7-7): the hidden tab strip takes no focus, and the
        # band and the tray come AFTER the sheet - Tk traverses siblings in
        # stacking order, and they were created (and packed) before it.
        try:
            self.notebook.configure(takefocus=0)
        except Exception:
            pass
        for strip in (self._band, self._tray):
            try:
                strip.lift()
            except Exception:
                pass
        self._sheet_page = ttk.Frame(self.notebook)
        self._sheet = _Sheet(self._sheet_page)
        self._sheet.frame.pack(fill="both", expand=True)
        self._sheet.fit_floor = self._device_floor
        self._sheet.wheel_target = self._scroll_device_well
        self._build_headline()
        self.notebook.add(self._sheet_page, text=self.SHEET_TAB)
        self._bind_stop_keys()

        self._build_menu_bar()

        # D-4: gate input on focus, never stop. Bound on the root, filtered to
        # the root's own events so a child widget's focus traffic is not a
        # window focus change.
        self.root.bind("<FocusIn>", self._on_window_focus)
        self.root.bind("<FocusOut>", self._on_window_focus)
        self.root.bind("<Configure>", self._on_window_resized, add="+")
        # The window's close button asks the Quit question (O9, PM8-6). The
        # OS's forced Quit (`::tk::mac::Quit`) is left as it is: P3 is the
        # owner's call.
        self.root.protocol("WM_DELETE_WINDOW", self._on_quit_clicked)
        self._hook_os_quit()

    # -- construction ------------------------------------------------------
    def _configure_styles(self):
        """Fonts in the Mac's own points, and ttk drawn from the theme.

        Aqua's ttk ignores colours, so ttk runs the `clam` theme here,
        coloured from the same six tokens, on every platform: no tab strip
        (the rail is the way between pages), fields with no box, a flat
        slider, an ink tick box, quiet scrollbars.
        """
        global _PIXEL_FONTS
        _PIXEL_FONTS = _windowing_system(self.root) == "aqua"
        base, panel, text = theme.BACKGROUND, theme.SURFACE, theme.TEXT
        muted = theme.MUTED
        arrow = max(12, theme.size(BASE))
        try:
            style = ttk.Style(self.root)
            style.theme_use("clam")
        except Exception as exc:
            events.debug("ttk Theme Not Set", str(exc), source=SOURCE, exception=exc)
            return
        # A select is a key (Signature): its face CAP, its rim and lip drawn
        # by the frames around it (`_Ring(lip="key")`), so clam's own border
        # is the face's colour.
        cap = theme.CAP
        combo = dict(background=cap, foreground=text, arrowcolor=text,
                     lightcolor=cap, darkcolor=cap, bordercolor=cap,
                     fieldbackground=cap, padding=(SPACE[2], _field_pady() + 1),
                     arrowsize=arrow, focuscolor=FOCUS_INK)
        settings = [
            (".", dict(background=base, foreground=text, font=_font(),
                       bordercolor=panel, lightcolor=base, darkcolor=base,
                       troughcolor=panel, focuscolor=FOCUS_INK,
                       fieldbackground=panel, selectbackground=panel,
                       selectforeground=text, insertcolor=text, arrowcolor=muted)),
            ("TFrame", dict(background=base)),
            ("TNotebook", dict(background=base, borderwidth=0, tabmargins=(0, 0, 0, 0),
                               tabposition="nw", lightcolor=base, darkcolor=base,
                               bordercolor=base)),
            # The tab strip is not drawn (the rail chooses the page); should
            # a tab ever take focus, its ring is ink, 2 px (AUD-10).
            ("TNotebook.Tab", dict(background=base, foreground=muted, font=_font(),
                                   padding=(SPACE[5], _target_pady() + FOCUS_PX),
                                   borderwidth=0, bordercolor=base, lightcolor=base,
                                   darkcolor=base, focuscolor=FOCUS_INK,
                                   focusthickness=FOCUS_PX)),
            ("TCombobox", dict(combo)),
            (WELL_COMBO_STYLE, dict(combo)),
            (CHECK_STYLE, dict(background=base, foreground=text,
                               indicatorbackground=base, indicatorforeground=text,
                               upperbordercolor=muted, lowerbordercolor=muted,
                               indicatorsize=_lamp_px(), indicatormargin=0,
                               padding=SPACE[1], focusthickness=0,
                               focuscolor=base)),
            # A slider (E): drawn by `_style_slider` - the widget's own ground
            # around a 4 px track and a round thumb.
            (SCALE_STYLE, dict(background=base, borderwidth=0)),
            (WELL_SCALE_STYLE, dict(background=panel, borderwidth=0)),
            ("Vertical.TScrollbar", dict(background=panel, troughcolor=base,
                                         bordercolor=base, lightcolor=panel,
                                         darkcolor=panel, arrowcolor=muted,
                                         gripcount=0, arrowsize=arrow)),
        ]
        combo_map = dict(
            foreground=[("disabled", theme.DISABLED[1]), ("readonly", text)],
            arrowcolor=[("disabled", theme.DISABLED[1]), ("hover", text)],
            selectforeground=[("readonly", text)])
        maps = [
            ("TNotebook.Tab", dict(
                background=[("selected", base), ("active", panel)],
                foreground=[("selected", text), ("active", text)])),
            # Disabled, the face is its ground (the ghost silhouette).
            ("TCombobox", dict(combo_map,
                               fieldbackground=[("disabled", base), ("readonly", cap)],
                               background=[("disabled", base), ("active", cap)],
                               selectbackground=[("readonly", cap)])),
            (WELL_COMBO_STYLE, dict(combo_map,
                                    fieldbackground=[("disabled", panel), ("readonly", cap)],
                                    background=[("disabled", panel), ("active", cap)],
                                    selectbackground=[("readonly", cap)])),
            (CHECK_STYLE, dict(
                background=[("active", base)],
                indicatorbackground=[("disabled", base), ("pressed", panel)],
                indicatorforeground=[("disabled", theme.DISABLED[1])],
                upperbordercolor=[("disabled", panel), ("hover", text)],
                lowerbordercolor=[("disabled", panel), ("hover", text)])),
            ("Vertical.TScrollbar", dict(background=[("active", muted)])),
        ]
        for name, options in settings:
            try:
                style.configure(name, **options)
            except Exception as exc:
                events.debug("ttk Style Refused", f"{name}: {exc}", source=SOURCE,
                             exception=exc)
        for name, options in maps:
            try:
                style.map(name, **options)
            except Exception as exc:
                events.debug("ttk Style Map Refused", f"{name}: {exc}",
                             source=SOURCE, exception=exc)
        try:
            # No tab strip: the rail is the way to Setup and to each model.
            style.layout("TNotebook.Tab", [])
        except Exception as exc:
            events.debug("Tab Strip Kept", str(exc), source=SOURCE, exception=exc)
        self._style_check_mark(style)
        self._style_slider(style)
        # A combobox's open list is a plain Tk listbox, styled by option.
        for option, value in (("*TCombobox*Listbox.background", base),
                              ("*TCombobox*Listbox.foreground", text),
                              ("*TCombobox*Listbox.selectBackground", panel),
                              ("*TCombobox*Listbox.selectForeground", text),
                              ("*TCombobox*Listbox.font", _font())):
            try:
                self.root.option_add(option, value)
            except Exception:
                pass

    def _style_check_mark(self, style):
        """The Launch box (Setup): an ink box with a check mark in the
        sheet's tone when ticked, a muted edge when not - never an "x" (the
        clam indicator's), never the signal colour. Drawn once into images
        ttk's indicator element shows by state."""
        size = _lamp_px()

        def box(fill, edge, tick):
            image = tk.PhotoImage(width=size, height=size)
            image.put(edge, to=(0, 0, size, size))
            image.put(fill, to=(2, 2, size - 2, size - 2))
            if tick is not None:
                # Two strokes, 2 px wide: down-right, then up-right.
                points = ((0.24, 0.52), (0.42, 0.70), (0.78, 0.30))
                for (x0, y0), (x1, y1) in zip(points, points[1:]):
                    steps = size * 2
                    for index in range(steps + 1):
                        x = round((x0 + (x1 - x0) * index / steps) * size)
                        y = round((y0 + (y1 - y0) * index / steps) * size)
                        image.put(tick, to=(x - 1, y - 1, x + 1, y + 1))
            return image

        try:
            off = box(theme.BACKGROUND, theme.MUTED, None)
            on = box(theme.TEXT, theme.TEXT, theme.BACKGROUND)
            off_disabled = box(theme.BACKGROUND, theme.SURFACE, None)
            on_disabled = box(theme.DISABLED[1], theme.DISABLED[1], theme.BACKGROUND)
            self._images = [off, on, off_disabled, on_disabled]
            style.element_create("Station.check", "image", off,
                                 ("disabled", "selected", on_disabled),
                                 ("disabled", off_disabled), ("selected", on))
            style.layout(CHECK_STYLE, [("Checkbutton.padding", {
                "sticky": "nswe", "children": [
                    ("Station.check", {"side": "left", "sticky": ""})]})])
        except Exception as exc:
            events.debug("Check Mark Not Drawn", str(exc), source=SOURCE,
                         exception=exc)

    def _style_slider(self, style):
        """The slider (Signature): a sunk `FADER["groove"]` px groove in the
        field ground and a fader cap from the key family - a CAP face, the
        KEY_RIM rim, a 3 px KEY_LIP lip and an ink index line
        (`theme.FADER`). Disabled, the cap is flat on the sheet in ghost
        tones (EDGE rim and index, EDGE_SOFT lip). ttk's own scale is a
        bevelled box, so its trough and slider are images here; the scale
        positions whatever element is called `slider`. The ink fill left of
        the cap is Web-only: a ttk scale has no fill element."""
        width, height = (_design_px(v) for v in theme.FADER["cap"])
        index_w, index_h = (max(1, _design_px(v)) for v in theme.FADER["index"])
        groove = _design_px(theme.FADER["groove"])
        lip = _lip_px("small")

        def cap(face, rim, lip_colour, index):
            image = tk.PhotoImage(width=width, height=height)
            image.put(rim, to=(0, 0, width, height))
            image.put(face, to=(1, 1, width - 1, height - 1 - lip))
            image.put(lip_colour, to=(1, height - 1 - lip, width - 1, height - 1))
            left = (width - index_w) // 2
            top = max(1, (height - lip - index_h) // 2)
            image.put(index, to=(left, top, left + index_w, top + index_h))
            return image

        # The trough is as tall as a target (L4): a press anywhere in the
        # band moves the slider, not only on the cap.
        band = max(height, MIN_TARGET_PX)

        def rail(colour):
            image = tk.PhotoImage(width=3 * groove, height=band)
            top = (band - groove) // 2
            image.put(theme.KEY_RIM, to=(0, top, 3 * groove, top + groove))
            image.put(colour, to=(0, top + 1, 3 * groove, top + groove - 1))
            return image

        try:
            live = cap(theme.CAP, theme.KEY_RIM, theme.KEY_LIP, theme.TEXT)
            ghost = cap(theme.BACKGROUND, theme.EDGE, theme.EDGE_SOFT, theme.EDGE)
            for name, ground in ((SCALE_STYLE, theme.BACKGROUND),
                                 (WELL_SCALE_STYLE, theme.SURFACE)):
                prefix = name.split(".")[0]
                trough = rail(_field_ground(ground))
                self._images += [trough]
                style.element_create(f"{prefix}.Scale.trough", "image", trough,
                                     border=(groove, 0), sticky="ew")
                style.element_create(f"{prefix}.Scale.slider", "image", live,
                                     ("disabled", ghost))
                style.layout(name, [(f"{prefix}.Scale.trough", {
                    "sticky": "ew", "children": [
                        (f"{prefix}.Scale.slider", {"side": "left", "sticky": ""})]})])
            self._images += [live, ghost]
        except Exception as exc:
            events.debug("Slider Not Drawn", str(exc), source=SOURCE, exception=exc)

    # -- the rail ----------------------------------------------------------
    def _rail_width(self, is_narrow):
        """248 px (200 under 1000 px), never narrower than the nameplate
        around the stop and its focus ring (at 28 pt the stop grows)."""
        want = RAIL_NARROW_PX if is_narrow else RAIL_PX
        plate = self._stop.size + 2 * (PLATE_PADX + 1)
        return max(want, plate + 2 * RAIL_PADX)

    def _build_rail(self):
        """The rail (Signature): the nameplate - the station's name and,
        when simulated, the line that says so; the stop; "Stop: Ctrl+."
        with the chord as two keycaps; the latched line - then the lost-link
        line, the idle lines, the page list, and Setup and Quit at the foot.
        The plate is packed before the list, so a long list at 28 pt can
        never push the stop off (the list gives way first)."""
        rail = self._rail = tk.Frame(self.root, background=RAIL_FACE,
                                     padx=RAIL_PADX, pady=SPACE[6])
        rail.pack(side="left", fill="y")
        # The nameplate: one CAP plate with a 1 px KEY_RIM edge (the Web's
        # engraved double frame and fasteners are dropped in Tk).
        self._plate_edge = tk.Frame(rail, background=theme.KEY_RIM, padx=1, pady=1)
        self._plate_edge.pack(side="top", fill="x")
        plate = self._plate = tk.Frame(self._plate_edge, background=PLATE_FACE,
                                       padx=PLATE_PADX, pady=SPACE[4])
        plate.pack(fill="both", expand=True)
        head = tk.Frame(plate, background=PLATE_FACE, padx=SPACE[2])
        head.pack(side="top", fill="x")
        tk.Label(head, text=STATION_TITLE, font=_font(STEP_1, bold=True), anchor="w",
                 background=PLATE_FACE, foreground=theme.TEXT).pack(fill="x")
        self._sim_line = tk.Label(head, text="", font=_caption_font(), anchor="w",
                                  justify="left", wraplength=RAIL_PX - 2 * SPACE[6],
                                  background=PLATE_FACE, foreground=theme.MUTED)
        tk.Frame(plate, height=1, background=theme.RULE).pack(
            side="top", fill="x", padx=SPACE[2], pady=(SPACE[3], 0))
        self._stop = _Mushroom(plate, self._on_stop_clicked, background=PLATE_FACE)
        self._stop_button = self._stop.canvas
        self._stop_button.pack(side="top", pady=(SPACE[2], 0))
        self._stop_bar = plate
        self._stop_key = STOP_KEY_NAME
        self._stop_hint = self._build_chord(plate)
        self._stop_hint.pack(side="top", pady=(0, SPACE[1]))
        self._stop.tooltip.text = self._hint(False)
        # "Stopped: every model latched", led by the warning glyph: packed
        # only while the latch holds.
        self._latched_row = tk.Frame(plate, background=PLATE_FACE)
        self._latched_mark = _mark_canvas(self._latched_row, PLATE_FACE)
        self._latched_mark.pack(side="left", anchor="n", padx=(SPACE[1], SPACE[2]),
                                pady=SPACE[0])
        _draw_warning(self._latched_mark, theme.SIGNAL)
        self._latched_line = tk.Label(self._latched_row, text=LATCHED_LINE,
                                      font=_font(bold=True), anchor="w", justify="left",
                                      wraplength=RAIL_PX - 3 * SPACE[5],
                                      background=PLATE_FACE, foreground=theme.TEXT)
        self._latched_line.pack(side="left", fill="x")
        # The station line: which model lost which link (F3), in ink led by
        # the warning glyph in SIGNAL; packed only while a link is lost.
        self._station_row = tk.Frame(rail, background=RAIL_FACE)
        self._station_mark = _mark_canvas(self._station_row, RAIL_FACE)
        self._station_mark.pack(side="left", anchor="n", padx=(0, SPACE[2]),
                                pady=SPACE[0])
        self._station_line = tk.Label(self._station_row, text="", font=_font(),
                                      anchor="w", justify="left",
                                      wraplength=RAIL_PX - 3 * SPACE[5],
                                      background=RAIL_FACE, foreground=theme.TEXT)
        self._station_line.pack(side="left", fill="x")
        # The idle countdown (Tier N): one line per probe about to power
        # down, with Extend; packed under the plate, above the page list,
        # only while there is one. Never a modal, never over the stop.
        self._idle_box = tk.Frame(rail, background=RAIL_FACE)
        # The page list is CREATED before the foot, so Tab reaches the
        # models before Setup and Quit, as the eye does (TK7-7); the foot is
        # PACKED first, so a long list at 28 pt gives way before it does.
        self._model_list = tk.Frame(rail, background=RAIL_FACE)
        # Setup (a neutral key) and Quit (a text key), at the foot.
        foot = tk.Frame(rail, background=RAIL_FACE)
        foot.pack(side="bottom", fill="x")
        self._setup_press = _Press(foot, "Setup", self._on_setup_clicked, RAIL_FACE)
        self._setup_press.frame.pack(side="left")
        self._setup_button = self._setup_press.widget
        self._quit_press = _Press(foot, "Quit", self._on_quit_clicked, RAIL_FACE,
                                  ghost=True)
        self._quit_press.frame.pack(side="left", padx=(SPACE[4], 0))
        self._model_list.pack(side="top", fill="x", pady=(SPACE[5], 0))
        self._set_rail_width(False)

    def _build_chord(self, parent):
        """"Stop:" in muted, then the chord as two small keycaps joined by
        "+" (Signature): each a CAP face, a KEY_RIM rim and a 2 px lip.
        The words read "Stop: Ctrl+." (`chord_text`). -> the row"""
        row = tk.Frame(parent, background=PLATE_FACE)
        self._chord_parts = []

        def words(text, colour, font):
            label = tk.Label(row, text=text, font=font, background=PLATE_FACE,
                             foreground=colour, padx=0)
            label.pack(side="left", padx=(0, SPACE[0]))
            self._chord_parts.append(label)

        def keycap(text):
            lip = tk.Frame(row, background=theme.KEY_LIP)
            lip.pack(side="left", padx=(0, SPACE[0]))
            rim = tk.Frame(lip, background=theme.KEY_RIM, padx=1, pady=1)
            rim.pack(pady=(0, _lip_px("kbd")))
            cap = tk.Label(rim, text=text, font=_font(SMALL, bold=True),
                           background=theme.CAP, foreground=theme.TEXT,
                           padx=SPACE[1], pady=0)
            cap.pack()
            self._chord_parts.append(cap)

        words(f"{STOP_LINE}:", theme.MUTED, _caption_font())
        first, second = self._stop_key.split("+", 1)
        keycap(first)
        words("+", theme.TEXT, _caption_font())
        keycap(second)
        return row

    @property
    def chord_text(self):
        """What the chord row says, read off its parts: "Stop: Ctrl+."."""
        parts = [str(part.cget("text")) for part in getattr(self, "_chord_parts", ())]
        return f"{parts[0]} {''.join(parts[1:])}" if parts else ""

    def _set_rail_width(self, is_narrow):
        if is_narrow == self._is_narrow:
            return
        self._is_narrow = is_narrow
        width = self._rail_width(is_narrow)
        try:
            self._rail.configure(width=width)
            self._rail.pack_propagate(False)
        except Exception:
            pass
        labels = [self._sim_line, self._latched_line, self._station_line]
        labels += [line["text"] for line in self._idle_lines.values()]
        for label in labels:
            try:
                label.configure(wraplength=width - 3 * SPACE[5])
            except Exception:
                pass

    def _rail_line(self, text, on_press, mark_name=None):
        """One line of the rail's page list (Signature): the lamp slot, then
        the name. The slot is lit ink on the shown page, SIGNAL for a model
        whose stop did not confirm (or whose disable failed), and hidden
        otherwise - its space kept, so the names align. A model's line
        (`mark_name`) also carries its latched square and its energized
        ring (L1, O6) before the name."""
        ring = _Ring(self._model_list, RAIL_FACE, border=RAIL_FACE)
        width, height = _lamp_size(rail=True)
        lamp = tk.Canvas(ring.inner, width=width, height=height,
                         background=RAIL_FACE, highlightthickness=0)
        lamp.pack(side="left", padx=(SPACE[3], 0))
        self._rail_lamps[mark_name] = lamp
        label = tk.Label(ring.inner, text=text, font=_font(), anchor="w",
                         background=RAIL_FACE, foreground=theme.TEXT,
                         padx=SPACE[3], pady=_target_pady(), cursor="hand2",
                         takefocus=1, highlightthickness=0)
        if mark_name is not None:
            size = _lamp_px()
            mark = tk.Canvas(ring.inner, width=size, height=size,
                             background=RAIL_FACE, highlightthickness=0)
            mark.pack(side="left", padx=(SPACE[1], 0))
            self._rail_marks[mark_name] = (mark, _Tooltip(label))
            # The energized ring (O6) sits after the stop mark, in ink.
            energy = tk.Canvas(ring.inner, width=size, height=size,
                               background=RAIL_FACE, highlightthickness=0)
            energy.pack(side="left", padx=(SPACE[1], 0))
            self._energy_marks[mark_name] = energy
        label.pack(fill="x")
        for sequence in ("<Button-1>", "<Return>", "<space>"):
            label.bind(sequence, lambda _e: on_press())
        label.bind("<FocusIn>", lambda _e, r=ring: r.paint(True))
        label.bind("<FocusOut>", lambda _e, r=ring: r.paint(False))
        return ring, label

    def _build_rail_list(self):
        """The page list (K4): "Overview" first, then one line per open
        model. A press shows that page; the middle button on a model's line
        closes it, as it closed a tab. With no model open there is no
        overview to show, and no line for it."""
        items = list(self._rail_items.values())
        if self._overview_item is not None:
            items.append(self._overview_item)
        for ring, _label in items:
            try:
                ring.outer.destroy()
            except Exception:
                pass
        self._rail_items = {}
        self._rail_marks = {}
        self._rail_lamps = {}
        self._energy_marks = {}
        self._overview_item = None
        names = self._page_names()
        if names:
            ring, label = self._overview_item = self._rail_line(
                OVERVIEW_PAGE, self._on_overview_pressed)
            ring.outer.pack(side="top", fill="x", pady=(0, SPACE[3]))
        for name in names:
            ring, label = self._rail_line(name, lambda n=name: self._on_rail_pressed(n),
                                          mark_name=name)
            ring.outer.pack(side="top", fill="x")
            label.bind(_close_tab_button(self.root),
                       lambda _e, n=name: self._on_rail_close(n))
            self._rail_items[name] = (ring, label)
        self._paint_rail()
        self._paint_rail_marks()

    def _rail_ground(self, name):
        """The shown page is a sunk SURFACE pad; every other line the rail."""
        is_current = self._shown_page != self.SETUP_TAB and name == self._opened
        return theme.SURFACE if is_current else RAIL_FACE

    def _rail_lamp_state(self, name):
        """A line's lamp slot: SIGNAL for a model whose stop did not confirm
        or whose disable failed; ink on the shown page; else hidden."""
        seen = self._stop_seen or ((), (), False)
        members = self._members(name) if name is not None else ()
        if any(m in (seen[1] or ()) or m in self._faulted for m in members):
            return "unconfirmed"
        if self._rail_ground(name) == theme.SURFACE:
            return "on"
        return "hidden"

    def _paint_rail_lamp(self, name):
        lamp = self._rail_lamps.get(name)
        if lamp is not None:
            _draw_lamp(lamp, self._rail_lamp_state(name), self._rail_ground(name),
                       rail=True)

    def _paint_rail(self):
        """The shown page is a sunk SURFACE pad with its lamp lit while the
        sheet is shown - the overview or the device's model; Setup is down
        in ink while its page is."""
        on_sheet = self._shown_page != self.SETUP_TAB
        lines = [(name, ring, label) for name, (ring, label) in self._rail_items.items()]
        if self._overview_item is not None:
            lines.append((None, *self._overview_item))
        for name, ring, label in lines:
            ground = self._rail_ground(name)
            is_current = ground == theme.SURFACE
            try:
                label.configure(background=ground, font=_font(bold=is_current))
                ring.inner.configure(background=ground)
                mark = self._rail_marks.get(name) if name else None
                if mark is not None:
                    # The marks sit in the line's own ground, lit or not.
                    mark[0].configure(background=ground)
                    self._energy_marks[name].configure(background=ground)
            except Exception:
                pass
            ring.background = ring.border = ground
            self._paint_rail_lamp(name)
        self._setup_press.set_active(not on_sheet)

    def _on_rail_pressed(self, name):
        self.show_model(name)
        return "break"

    def _on_overview_pressed(self):
        self.show_overview()
        return "break"

    def _on_rail_close(self, name):
        """The middle button on a model's line closes it (the tab gesture,
        VIEW-TKINTER-18), after the same question "Close this model…" asks
        (O9, PM8-5): `Controller.remove` stops and destructs it; the Models
        menu reopens it."""
        events.debug("Rail Close Requested", name, source=SOURCE)
        self._confirm_close_model(name)
        return "break"

    def _confirm_close_model(self, name):
        """Every close gesture on a model - "Close this model…" (L13), the
        rail's middle button, the Models menu (O9): it stops and disconnects
        the model, so it asks first, in words that say so; the Models menu
        reopens it. -> True when the model was closed."""
        prompt = (f"Close {name}?\n\nIt stops and disconnects. You can reopen "
                  "it from the Models menu.")
        if _confirm(self.root, prompt, title=f"Close {name}",
                    yes_text=f"Close {name}", no_text="Keep it open"):
            events.debug("Close Model Confirmed", name, source=SOURCE)
            self.close_model(name)
            return True
        events.debug("Close Model Declined", name, source=SOURCE)
        return False

    def _quit_prompt(self):
        """The Quit question, naming what is energized now (N4): "Quit the
        station? Stepper Probe and Rotator are energized; quitting stops and
        disconnects them." With nothing energized, the plain sentence."""
        try:
            energized = list((self.controller.state() or {}).get("energized") or ())
        except Exception as exc:
            events.debug("Energized Unread", str(exc), source=SOURCE, exception=exc)
            energized = []
        if not energized:
            return self.QUIT_PROMPT
        many = len(energized) > 1
        return (f"Quit the station? {join_names(energized)} "
                f"{'are' if many else 'is'} energized; quitting stops and "
                f"disconnects {'them' if many else 'it'}.")

    def _on_quit_clicked(self):
        """Quit asks first (it stops every model and exits), naming what is
        energized; the window's close button asks the same question (O9)."""
        if self._closing:
            return
        if _confirm(self.root, self._quit_prompt(), **QUIT_DIALOG):
            self.close()
        else:
            events.debug("Quit Declined", "the station stays up", source=SOURCE)

    # -- the sheet -----------------------------------------------------------
    def _build_headline(self):
        """"Every model is stopped." at the head of the sheet, while latched."""
        self._headline = tk.Frame(self._sheet.body, background=_page())
        self._headline_lines = (
            tk.Label(self._headline, text=STOPPED_HEADLINE, font=_numeral_font(40),
                     anchor="w", justify="left", background=_page(),
                     foreground=theme.TEXT),
            tk.Label(self._headline, text=STOPPED_NEXT, font=_font(), anchor="w",
                     justify="left", background=_page(), foreground=theme.MUTED))
        for line in self._headline_lines:
            line.pack(side="top", anchor="w", fill="x")
        self._sheet.canvas.bind("<Configure>", self._on_sheet_resized, add="+")
        self._is_headline_shown = False

    def _on_sheet_resized(self, event=None):
        """The headline wraps to the sheet rather than run off it."""
        width = getattr(event, "width", 0)
        if not isinstance(width, int) or width <= SPACE[10] * 4:
            return
        for line in self._headline_lines:
            try:
                line.configure(wraplength=width - 2 * SPACE[10])
            except Exception:
                pass

    def _sheet_columns(self):
        """How many entries share a row after the opened one: one under
        `NARROW_WINDOW_PX`, else as many as keep each at least `COLUMN_PX`
        at the current font size, up to `SHEET_COLUMNS`."""
        try:
            width = self.root.winfo_width()
        except Exception:
            width = None
        if not isinstance(width, int) or width <= 1:
            return SHEET_COLUMNS
        if width < NARROW_WINDOW_PX:
            return 1
        room = width - self._rail_width(False) - 2 * SPACE[10]
        return max(1, min(SHEET_COLUMNS, room // _design_px(COLUMN_PX)))

    def _on_window_resized(self, event=None):
        if event is not None and getattr(event, "widget", self.root) is not self.root:
            return
        width = getattr(event, "width", None)
        if isinstance(width, int) and width > 1:
            self._set_rail_width(width < NARROW_WINDOW_PX)
        self._lay_out_sheet()

    def _lay_out_sheet(self, force=False):
        """One of the sheet's two pages (K4). The overview: every open model
        in schema order, in rows of `_sheet_columns()`, each row's entries
        sharing it equally, tier 1 only. The device page: the opened model
        alone, full width. A device that no longer exists gives way to the
        overview. Laid out again only when the models, the page or the
        column count change."""
        names = self._page_names()
        if self._opened in self._hosts:
            self._opened = self._hosts[self._opened]
        if self._opened not in names:
            self._opened = None
        columns = self._sheet_columns()
        key = (tuple(names), self._opened, columns, tuple(sorted(self._hosts.items())))
        if key == self._sheet_key and not force:
            return
        self._sheet_key = key
        for name in names:
            view = self._panels[name]
            view.set_prominence(name == self._opened)
            try:
                view.frame.grid_forget()
            except Exception:
                pass
        for name, host in self._hosts.items():
            # A group is drawn, and polled, only on its host's device page.
            view = self._panels[name]
            is_shown = host == self._opened
            view.set_prominence(is_shown)
            if is_shown:
                view.resume()
            else:
                view.pause()
        for name in names:
            if self._panels[name].hosted_views:
                self._panels[name]._apply_page()
        for row in self._sheet_rows:
            try:
                row.destroy()
            except Exception:
                pass
        if self._opened is not None:
            groups = [[self._opened]]
        else:
            groups = [names[index:index + columns]
                      for index in range(0, len(names), columns)]
        self._sheet_rows = []
        is_device = self._opened is not None
        for group in groups:
            row = tk.Frame(self._sheet.body, background=_page())
            # The device page's one row takes the page's height: its entry's
            # well scrolls inside it (L5).
            row.pack(side="top", fill="both" if is_device else "x",
                     expand=is_device, pady=(0, 0 if is_device else SPACE[9]))
            if is_device:
                try:
                    row.grid_rowconfigure(0, weight=1)
                except Exception:
                    pass
            # Every row has the page's columns, filled or not, so a short
            # last row's entries are as wide as the full rows' (TK7-8).
            width = 1 if self._opened is not None else columns
            for index in range(width):
                try:
                    if index:
                        row.grid_columnconfigure(2 * index - 1, weight=0,
                                                 minsize=SPACE[10])
                    row.grid_columnconfigure(2 * index, weight=1, uniform="entries")
                except Exception:
                    pass
            for index, name in enumerate(group):
                # The gutter is a column of its own, not the entry's padding:
                # with the padding on every entry but the first, the uniform
                # columns' leftover pixel moved with the widest request, and
                # an entry whose flow wraps at that very width (Red Percent
                # in a third at 1400 px) swapped 309 and 310 px forever.
                column = 2 * index
                try:
                    if index:
                        row.grid_columnconfigure(column - 1, weight=0,
                                                 minsize=SPACE[10])
                    row.grid_columnconfigure(column, weight=1, uniform="entries")
                    frame = self._panels[name].frame
                    frame.grid(in_=row, row=0, column=column,
                               sticky="nsew" if is_device else "new")
                    frame.lift()
                except Exception as exc:
                    events.debug("Entry Not Placed", f"{name}: {exc}", source=SOURCE,
                                 exception=exc, every=5.0)
            self._sheet_rows.append(row)
        self._sheet.set_fit(is_device)
        self._paint_rail()
        events.debug("Sheet Laid Out", f"page={self._opened or OVERVIEW_PAGE} "
                     f"columns={columns} "
                     f"rows={[len(g) for g in groups]}", source=SOURCE)

    def _device_floor(self):
        """The least the device page needs (L5): the head and tier 1 as
        they ask, plus room for the well when it is shown. More than the
        window, and the sheet scrolls as a last resort."""
        view = self._panels.get(self._opened) if self._opened else None
        try:
            need = self._sheet.body.winfo_reqheight()
        except Exception:
            return 0
        if not isinstance(need, int):
            return 0
        for each in ([view] + view.hosted_views) if view is not None else ():
            if each.well_canvas is not None:
                need += each.WELL_FLOOR_PX
        return need

    def _scroll_device_well(self, step, widget=None):
        view = self._panels.get(self._opened) if self._opened else None
        if view is None:
            return False
        return any(each.scroll_well(step, widget)
                   for each in [view] + view.hosted_views)

    def show_model(self, name):
        """The device page (K4): what a press on a model's rail line or on
        its overview head does - `name` alone, full width and focal, with its
        disclosures, scrolled to the top; Setup gives way. -> bool"""
        if name not in self._panels or name == self.SETUP_TAB:
            return False
        host = self._hosts.get(name)
        self._opened = host or name
        self._show_sheet_page()
        if host is not None:
            # A model drawn on another's page: that page, at its group.
            self._sheet.scroll_into_view(self._panels[name].frame)
            events.debug("Model Shown", f"{name} on {host}", source=SOURCE)
            return True
        events.debug("Model Shown", name, source=SOURCE)
        return True

    # -- models drawn on another model's page (Model.HOST) ----------------------
    def _page_names(self):
        """The models with a page (a rail line, an overview entry): every
        open one but Setup and those drawn on another model's page."""
        return [n for n in self._panels
                if n != self.SETUP_TAB and not self._hosts.get(n)]

    def _members(self, name):
        """A page's models: its own, then the ones drawn on it."""
        return [name] + [n for n, host in self._hosts.items() if host == name]

    def _station_models(self):
        """`state()["models"]`, or None when it cannot be read."""
        try:
            return (self.controller.state() or {}).get("models") or {}
        except Exception as exc:
            events.debug("Station State Unread", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
            return None

    def _placement(self, models, adding=None):
        """Where each open model is drawn, from `state()["models"][n]["host"]`:
        hosted name -> host, for a host that has a page of its own here.
        `adding` is a model whose view is being built."""
        wanted, known = {}, set(self._panels) | {adding}
        for name, state in models.items():
            host = state.get("host") if isinstance(state, dict) else None
            if (name in known and host and host != name
                    and host in self._panels and host != self.SETUP_TAB):
                wanted[name] = host
        # A host that is itself drawn elsewhere hosts nothing: no chains.
        return {name: host for name, host in wanted.items() if host not in wanted}

    def _new_view(self, name, host=None):
        view = TkPanelView(self._sheet.body, self.controller, name,
                           sheet=self._sheet,
                           host=self._panels.get(host) if host else None)
        view.log_window_bounds = self._log_window_bounds
        view.on_open = self.show_model
        view.on_close = self._confirm_close_model
        return view

    def _place(self, name, host):
        """Build `name`'s view again where it is now drawn: a Tk widget
        cannot change parents, so a group moving onto a page, or back to a
        page of its own, is a new view (its uncommitted edits go with the
        old one)."""
        old = self._panels.get(name)
        if old is not None:
            try:
                old.close()
            except Exception as exc:
                events.debug("Panel Close Failed", f"{name}: {exc}", source=SOURCE,
                             exception=exc)
        view = self._new_view(name, host)
        self._panels[name] = view
        self._frames[name] = view.frame
        if host:
            self._hosts[name] = host
        else:
            self._hosts.pop(name, None)
        events.debug("Entry Placed", f"{name} on {host}" if host
                     else f"{name} on its own page", source=SOURCE)
        return view

    def _rehome(self, models=None):
        """Draw every model where `state()` says it belongs. A view whose
        model has already left the controller is left alone: its removal
        is on its way. -> True when a model moved."""
        models = self._station_models() if models is None else models
        if models is None:
            return False
        placement = self._placement(models)
        moved = [name for name in self._panels
                 if name != self.SETUP_TAB and name in models
                 and placement.get(name) != self._hosts.get(name)]
        for name in moved:
            self._place(name, placement.get(name))
        if moved:
            self._build_rail_list()
            self._lay_out_sheet(force=True)
        return bool(moved)

    def show_overview(self):
        """The overview (K4): every open model, tier 1 only. -> bool"""
        self._opened = None
        self._show_sheet_page()
        events.debug("Overview Shown", "every open model", source=SOURCE)
        return True

    def _show_sheet_page(self):
        if not self._is_setup_collapsed and self.SETUP_TAB in self._frames:
            self._collapse_setup()
        self._select_sheet()
        self._lay_out_sheet()
        self._sheet.scroll_to_top()
        self._paint_rail()

    def _select_sheet(self):
        try:
            self.notebook.select(self._sheet_page)
        except Exception as exc:
            events.debug("Sheet Not Selected", str(exc), source=SOURCE, exception=exc)
        self._shown_page = self.SHEET_TAB

    # -- the stop ----------------------------------------------------------
    def _hint(self, is_latched):
        """What a press will do - and, unlatched, the key that does it from
        anywhere in the window (F9). The disc's tooltip."""
        return CLEAR_HINT if is_latched else f"{STOP_HINT} ({self._stop_key})"

    def _bind_stop_keys(self):
        """The stop from anywhere, focus wherever it is: an entry, a tab, a
        confirmation, the region picker. It only ever stops."""
        for sequence in STOP_KEYS:
            try:
                self.root.bind_all(sequence, self._on_stop_key)
            except Exception as exc:
                events.debug("Stop Key Not Bound", f"{sequence}: {exc}",
                             source=SOURCE, exception=exc)

    def _on_stop_key(self, _event=None):
        """The chord always stops and never clears (L1): a partial stop -
        one model latched from its own switch - is not "already stopped",
        so the chord stops the rest. Only with every model latched is
        there nothing left for it to do."""
        events.debug("Stop Key", "the stop shortcut was pressed", source=SOURCE)
        if self._stop_words()["action"] != "clear":
            self.controller.estop_all()
        self._sync_stop_button()
        return "break"

    #: What the view draws when the controller serves no stop state: nothing
    #: latched, so the disc stays a working Stop (the safe press). There is
    #: no guess from `is_estopped` (O17): that fallback read ONE latched
    #: model as "every model is stopped", the pre-L1 rule.
    NO_STOP_STATE = {"latched": [], "unconfirmed": [], "every": False}

    def _stop_state(self):
        """`Controller.stop_state`: which models are latched, which of them
        did not confirm, and whether that is every model."""
        try:
            state = self.controller.stop_state
        except Exception as exc:
            events.debug("Stop State Unread", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
            state = None
        if not isinstance(state, dict):
            events.debug("Stop State Missing", repr(state), source=SOURCE, every=5.0)
            state = dict(self.NO_STOP_STATE)
        return state

    def _stop_words(self, state=None):
        """What the disc, the headline and the rail say (`stop_words`)."""
        return stop_words(state if state is not None else self._stop_state())

    # -- the alert band and the tray -----------------------------------------
    def _build_alert_band(self):
        """Errors that need acknowledging, listed by source, under the sheet
        and never over the stop (F1, HC-2). It replaces
        `messagebox.showerror`, which was application-modal: five queued
        errors made five dialogs, and the stop could not take a click while
        one was up."""
        self._band = tk.Frame(self._main, background=theme.BACKGROUND,
                              padx=SPACE[10], pady=SPACE[3])
        self._band_mark = _mark_canvas(self._band, theme.BACKGROUND)
        self._band_mark.pack(side="left", anchor="n", padx=(0, SPACE[2]),
                             pady=SPACE[1])
        self._band_ack = _Press(self._band, "Acknowledge", self._acknowledge,
                                theme.BACKGROUND)
        self._band_ack.frame.pack(side="right", anchor="n", padx=(SPACE[3], 0))
        self._band_text = tk.Label(self._band, text="", font=_font(), anchor="w",
                                   justify="left", wraplength=720,
                                   background=theme.BACKGROUND,
                                   foreground=theme.TEXT)
        self._band_text.pack(side="left", fill="x", expand=True)
        self._band.bind("<Configure>", self._on_band_resized)
        _draw_warning(self._band_mark, theme.SIGNAL)

    def _on_band_resized(self, event=None):
        """The band's words wrap in what the mark and Acknowledge leave."""
        width = getattr(event, "width", 0)
        if not isinstance(width, int) or width <= SPACE[6] * 10:
            return
        try:
            button = self._band_ack.frame.winfo_reqwidth()
        except Exception:
            button = SPACE[10] * 3
        if not isinstance(button, int):
            button = SPACE[10] * 3
        room = width - 2 * SPACE[10] - button - _lamp_px() - 3 * SPACE[3]
        try:
            self._band_text.configure(wraplength=max(SPACE[10] * 4, room))
        except Exception:
            pass

    @property
    def is_alert_shown(self):
        return bool(self._alerts)

    def _render_alerts(self):
        alerts = self._alerts
        if not alerts:
            self._sync_ack_dialog()
            try:
                self._band.pack_forget()
            except Exception:
                pass
            return
        self._sync_ack_dialog()
        if len(alerts) == 1:
            text = _event_line(alerts[0])
        else:
            shown = [_event_line(event) for event in alerts[-BAND_LINES:]]
            more = len(alerts) - len(shown)
            kind = ("errors" if all(getattr(e, "severity", "") == "error"
                                    for e in alerts) else "notices")
            text = "\n".join([f"{len(alerts)} {kind} need acknowledgement."]
                             + (["..."] if more else []) + shown)
        try:
            self._band_text.configure(text=text)
            self._band.pack(side="bottom", fill="x", after=self._tray)
        except Exception as exc:
            events.debug("Alert Band Failed", str(exc), source=SOURCE,
                         exception=exc)

    def _ack_group(self):
        """The oldest waiting title and every queued event under it: one
        dialog per title, so a repeat of the title shown counts in that
        window instead of queueing a second one (rb-ack A3). The band still
        lists every event (HC-2: none is overwritten)."""
        if not self._alerts:
            return []
        title = self._alerts[0].title
        return [event for event in self._alerts if event.title == title]

    def _sync_ack_dialog(self):
        """The dialog shows the oldest waiting title, or nothing: every path
        that changes the queue (a new event, Understood, the band's
        Acknowledge, L2's dropped stop lines, close) ends here."""
        dialog = self._ack_dialog
        group = [] if self._closing else self._ack_group()
        if not group:
            if dialog is not None:
                self._ack_dialog = None
                dialog.close()
            return
        latest = group[-1]                  # the newest words for the title
        repeats = sum(max(1, int(getattr(e, "count", 1) or 1)) for e in group)
        waiting = len({e.title for e in self._alerts}) - 1
        if dialog is not None and dialog.event.title == latest.title:
            if dialog.event is not latest:
                events.debug("Alert Repeated", f"{latest.severity}/"
                             f"{latest.title} x{repeats}", source=SOURCE)
            dialog.update(latest, repeats, waiting)
            return
        if dialog is not None:
            dialog.close()
        try:
            self._ack_dialog = _AckDialog(self.root, latest, self._understood,
                                          repeats=repeats, waiting=waiting)
        except Exception as exc:        # the band still says it
            self._ack_dialog = None
            events.debug("Ack Dialog Failed", str(exc), source=SOURCE,
                         exception=exc)

    def _understood(self, dialog):
        """The dialog's one key: the title shown is read (every repeat of
        it), the next title (if any) takes the window; after the last, focus
        goes to the stop."""
        if dialog is not self._ack_dialog or not self._alerts:
            dialog.close()
            return
        group = self._ack_group()
        self._alerts = [e for e in self._alerts if e.title != group[0].title]
        events.debug("Alert Acknowledged", f"{group[-1].severity}/"
                     f"{group[-1].title}" + (f" x{len(group)}" if len(group) > 1
                                              else ""), source=SOURCE)
        self._render_alerts()
        if not self._alerts:
            try:
                self._stop_button.focus_set()
            except Exception:
                pass

    def _acknowledge(self):
        """One press clears every listed error; focus goes to the stop."""
        count, self._alerts = len(self._alerts), []
        events.debug("Alerts Acknowledged", f"{count} acknowledged", source=SOURCE)
        try:
            had_focus = self.root.focus_get() is self._band_ack.widget
        except Exception:
            had_focus = False
        self._render_alerts()
        if had_focus:
            try:
                self._stop_button.focus_set()
            except Exception:
                pass

    def _build_event_panel(self):
        """The tray: status by exception (E). Warnings and errors only - the
        folded line led by the warning glyph (ink for a warning, SIGNAL for
        an error; Signature), the history by a hollow ink square or a solid
        signal one, and the severity's word - folded to the latest one line;
        "Show events" unfolds `EVENT_LOG_LINES` of them. Info goes to the
        log file, not here: normal is silence."""
        frame = tk.Frame(self._main, background=theme.BACKGROUND)
        frame.pack(side="bottom", fill="x")
        self._tray = frame
        tk.Frame(frame, height=1, background=theme.RULE).pack(side="top", fill="x")
        body = tk.Frame(frame, background=theme.BACKGROUND, padx=SPACE[10],
                        pady=SPACE[3])
        body.pack(side="top", fill="x")
        self._events_press = _Press(body, "Show events", self._toggle_tray,
                                    theme.BACKGROUND)
        self._events_press.frame.pack(side="right", anchor="s")
        # Folded: the latest warning or error, whole (it wraps), its mark
        # beside it. Unfolded: the history, scrolled to its end.
        self._latest = tk.Frame(body, background=theme.BACKGROUND)
        self._latest.pack(side="left", fill="x", expand=True, padx=(0, SPACE[5]))
        self._latest_mark = _mark_canvas(self._latest, theme.BACKGROUND)
        self._latest_mark.pack(side="left", anchor="n", padx=(0, SPACE[2]),
                               pady=SPACE[1])
        self._latest_text = tk.Label(self._latest, text="", font=_font(), anchor="w",
                                     justify="left", wraplength=720,
                                     background=theme.BACKGROUND,
                                     foreground=theme.TEXT)
        self._latest_text.pack(side="left", fill="x", expand=True)
        self._latest.bind("<Configure>", self._on_tray_resized, add="+")
        self._event_text = tk.Text(body, height=EVENT_LOG_LINES, state="disabled",
                                   wrap="word", relief="flat", font=_font(),
                                   padx=SPACE[3], pady=SPACE[2], highlightthickness=0,
                                   background=theme.SURFACE, foreground=theme.TEXT,
                                   cursor="arrow")
        # Creation order is tag priority: `latest` outranks `info` and is
        # outranked by `warning` and `error`. A line's text is its
        # severity's INK; its colour is the MARK before it (F14).
        tags = [(severity, theme.SEVERITY_INK[severity]) for severity in ("info",)]
        tags += [("latest", theme.TEXT)]
        tags += [(severity, theme.SEVERITY_INK[severity])
                 for severity in ("warning", "error")]
        tags += [(f"mark-{severity}", theme.SEVERITY_MARK[severity])
                 for severity in ("info", "warning", "error")]
        for tag, foreground in tags:
            try:
                self._event_text.tag_configure(tag, foreground=foreground)
            except Exception:
                pass

    def _toggle_tray(self):
        """Unfold the tray to its history of warnings and errors, or fold it
        back to the latest one. It grows upward; the rail is untouched."""
        self._is_tray_open = not self._is_tray_open
        try:
            if self._is_tray_open:
                self._latest.pack_forget()
                self._event_text.pack(side="left", fill="x", expand=True,
                                      padx=(0, SPACE[5]))
                self._event_text.see("end")
            else:
                self._event_text.pack_forget()
                self._latest.pack(side="left", fill="x", expand=True,
                                  padx=(0, SPACE[5]))
        except Exception as exc:
            events.debug("Tray Not Toggled", str(exc), source=SOURCE, exception=exc)
        self._events_press.set_text("Hide events" if self._is_tray_open
                                    else "Show events")

    def _on_tray_resized(self, event=None):
        width = getattr(event, "width", 0)
        if isinstance(width, int) and width > SPACE[6] * 10:
            try:
                self._latest_text.configure(wraplength=width - SPACE[6] * 2)
            except Exception:
                pass

    def _build_menu_bar(self):
        """The Models menu — tick to open, untick to close — and the way back
        to Setup.

        A closed model is still listed, which is the reopen path Tk never had:
        a forgotten tab was gone for the session (VIEW-TKINTER-1). The Setup
        entry is the same idea for the wizard, which minimises once the first
        model launches.
        """
        try:
            menubar = tk.Menu(self.root)
            menu = tk.Menu(menubar, tearoff=0)
            setup_menu = tk.Menu(menubar, tearoff=0)
        except Exception as exc:
            events.debug("Menu Not Built", str(exc), source=SOURCE, exception=exc)
            return
        self._menu_vars = {}
        open_names = list(self.controller.model_names)
        for name in open_names + [n for n in self.controller.closed_names
                                  if n not in open_names]:
            variable = tk.BooleanVar(value=name in open_names)
            self._menu_vars[name] = variable
            menu.add_checkbutton(label=name, variable=variable,
                                 command=lambda n=name: self._on_model_toggled(n))
        menubar.add_cascade(label="Models", menu=menu)
        setup_menu.add_command(label="Show Setup", command=self.restore_setup)
        menubar.add_cascade(label=self.SETUP_TAB, menu=setup_menu)
        self._setup_menu = setup_menu
        self._menubar = menubar
        try:
            self.root.configure(menu=menubar)
        except Exception as exc:
            events.debug("Menubar Not Attached", str(exc), source=SOURCE,
                         exception=exc)
        for view in list(self._panels.values()):
            view.share_menubar(menubar)

    def _hook_os_quit(self):
        """Route the OS's own Quit through `close()` (VIEW-TKINTER-8).

        Not a command this view adds (G5): Tk on Aqua gives EVERY app an
        application menu with Quit in it, and its OS-owned shortcut. Left
        unhooked, Tk answers it with `Tcl_Exit`, which ends the process past
        `close()` and past Python's atexit, so nothing is stopped and no port
        is closed (reproduced on Tk 9.0.4 with a Quit Apple event). The hook
        only makes that forced command take the same path as the window's
        close button. It is platform-neutral code: X11 and Win32 never call
        it.
        """
        try:
            self.root.createcommand("::tk::mac::Quit", self.close)
        except Exception as exc:
            events.debug("OS Quit Not Hooked", str(exc), source=SOURCE,
                         exception=exc)

    # -- lifecycle ---------------------------------------------------------
    def open(self):
        """Show Setup first, then run. Returns when the window closes.

        Setup is a panel like any other — `TkPanelView` over the Setup schema —
        which is why there is one wizard for three views instead of three
        wizards that disagree.
        """
        events.debug("View Opening", "Tk dashboard", source=SOURCE)
        self._add_setup_panel()
        super().open()               # subscribe BEFORE anything can publish
        self._is_opening = True
        try:
            for name in self.controller.model_names:
                self._add_panel(name)
        finally:
            self._is_opening = False
        self._schedule_refresh()
        events.info("Dashboard Open", "Tk dashboard ready", source=SOURCE)
        self.root.mainloop()
        if not self._closing:
            # Not an error: `Controller._hook_exit` (atexit + SIGINT/TERM/HUP)
            # owns the teardown of last resort. Worth a line in the log file
            # when the loop ends by some path other than close().
            events.debug("Loop Ended", "the Tk main loop returned without a "
                         "close path", source=SOURCE)
        return None

    def _log_window_bounds(self):
        """What a panel's detached log window must not cover (the notebook:
        Setup or the sheet) and where it may go when the screen has no room
        outside the station window (the event tray). I1."""
        return {"page": _rect_of(self.notebook),
                "free": _rect_of(getattr(self, "_tray", None))}

    def _add_setup_panel(self):
        """Setup's page: its headline, then its one table (E: restyled, the
        Launch box first, the Launch row pinned)."""
        frame = ttk.Frame(self.notebook)
        view = TkPanelView(frame, self.controller, self.SETUP_TAB, panel=self.setup)
        view.log_window_bounds = self._log_window_bounds
        view.share_menubar(self._menubar)
        try:
            view._rule.pack_forget()
            view._title.configure(font=_numeral_font(44))
        except Exception:
            pass
        view.frame.pack(fill="both", expand=True, padx=SPACE[8], pady=(SPACE[6], 0))
        try:
            # Setup's page comes first, before the sheet.
            self.notebook.insert(0, frame, text=self.SETUP_TAB)
        except Exception:
            self.notebook.add(frame, text=self.SETUP_TAB)
        try:
            self.notebook.select(frame)
        except Exception:
            pass
        self._shown_page = self.SETUP_TAB
        self._panels[self.SETUP_TAB] = view
        self._frames[self.SETUP_TAB] = frame
        self._paint_rail()

    def close(self):
        """Unsubscribe first, then tear the widgets down.

        `Dashboard.close` sets `_closing` and unsubscribes before anything
        else, so nothing opened from inside this path can raise a modal that
        blocks the exit.
        """
        if self._closing:
            return
        events.debug("View Closing", "Tk dashboard", source=SOURCE)
        if self._ack_dialog is not None:
            dialog, self._ack_dialog = self._ack_dialog, None
            dialog.close()
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        super().close()
        self._stop.cancel()
        for name in list(self._panels):
            self._destroy_panel(name)
        try:
            self.root.quit()
            self.root.destroy()
        except Exception as exc:
            events.debug("Root Destroy Failed", str(exc), source=SOURCE,
                         exception=exc)
        events.debug("View Closed", "Tk dashboard", source=SOURCE)

    # -- the refresh tick --------------------------------------------------
    def _schedule_refresh(self):
        try:
            self._after_id = self.root.after(self.REFRESH_MS, self._on_refresh_tick)
        except Exception as exc:
            events.debug("Dashboard Tick Not Scheduled", str(exc), source=SOURCE,
                         exception=exc)

    def _on_refresh_tick(self):
        self._after_id = None
        if self._closing:
            return
        self._sync_stop_button()
        self._sync_station_state()
        self._sync_station_line()
        self._sheet.refit()
        self._schedule_refresh()

    def _sync_stop_button(self):
        """Everything the station says about the stop, from
        `Controller.stop_state` through `views.base.stop_words` (L1), so the
        disc, the headline, its subline and the rail line never disagree:

        - the disc reads Clear (thicker ring, one breath on the edge) only
          while EVERY model is latched; a partial stop leaves it a Stop;
        - the headline and its subline are drawn only when there is one
          ("Every model is stopped." / "Stopped. Rotator did not confirm.");
        - the rail line under the disc names a partial stop or the models
          that did not confirm; each model's rail line wears its own mark;
        - "Stop: Ctrl+." stays under the disc in every state.

        Nothing is redrawn while the state holds still (F21). When the latch
        opens, the "Stop Not Confirmed" lines leave the band and the tray
        (L2): they describe a latch that no longer exists."""
        state = self._stop_state()
        latched = tuple(state.get("latched") or ())
        unconfirmed = tuple(state.get("unconfirmed") or ())
        key = (latched, unconfirmed, bool(state.get("every")))
        if key == self._stop_seen:
            return
        was_latched = bool(self._stop_seen and self._stop_seen[0])
        self._stop_seen = key
        words = self._stop_words(state)
        is_clear = words["action"] == "clear"
        events.debug("Stop Words Changed",
                     f"face={words['face']} rail={words['rail']!r} "
                     f"headline={words['headline']!r}", source=SOURCE)
        self._stop.set_latched(is_clear)
        self._stop.tooltip.text = self._hint(is_clear)
        if was_latched and not latched:
            self._drop_stop_lines()
        try:
            if words["rail"]:
                self._latched_line.configure(text=words["rail"])
                self._latched_row.pack(side="top", fill="x", after=self._stop_hint,
                                       pady=(0, SPACE[4]))
            else:
                self._latched_row.pack_forget()
            if words["headline"]:
                headline, subline = self._headline_lines
                headline.configure(text=words["headline"])
                subline.configure(text=words["subline"] or STOPPED_NEXT)
                first = self._sheet_rows[0] if self._sheet_rows else None
                if first is not None:
                    self._headline.pack(side="top", fill="x", pady=(0, SPACE[8]),
                                        before=first)
                else:
                    self._headline.pack(side="top", fill="x", pady=(0, SPACE[8]))
            else:
                self._headline.pack_forget()
            self._is_headline_shown = bool(words["headline"])
        except Exception as exc:
            events.debug("Stop Button Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        self._paint_rail_marks(latched, unconfirmed)

    def _sync_station_state(self):
        """The station at once (`Controller.state()`), read once a tick: which
        models are faulted (O4). Drawn only when it changes (F21)."""
        try:
            station = self.controller.state()
        except Exception as exc:
            events.debug("Station State Unread", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
            return
        if not isinstance(station, dict):
            return
        models = station.get("models") or {}
        self._rehome(models)
        faulted = tuple(name for name, state in models.items()
                        if isinstance(state, dict) and state.get("is_faulted"))
        energized = tuple(station.get("energized") or ())
        if (faulted, energized) != (self._faulted, self._energized):
            events.debug("Rail Marks Changed", f"faulted={list(faulted)} "
                         f"energized={list(energized)}", source=SOURCE)
            self._faulted, self._energized = faulted, energized
            self._paint_rail_marks()
        due = {}
        for name, state in models.items():
            if not isinstance(state, dict):
                continue
            remaining = state.get("idle_remaining")
            window = state.get("idle_warn_seconds") or IDLE_WARN_SECONDS
            if remaining is not None and remaining <= window:
                due[name] = remaining
        self._sync_idle_lines(due)

    def _sync_idle_lines(self, due):
        """One countdown line per probe inside its warning window, in
        station order (Tier N). The seconds are the state's, redrawn each
        tick; a line goes when its probe leaves the window or its mode."""
        if list(due) != list(self._idle_lines):
            for line in self._idle_lines.values():
                line["tooltip"].close()
                try:
                    line["row"].destroy()
                except Exception:
                    pass
            self._idle_lines = {name: self._idle_line(name) for name in due}
            events.debug("Idle Lines Changed", ", ".join(due) or "none",
                         source=SOURCE)
            try:
                if due:
                    self._idle_box.pack(side="top", fill="x", before=self._model_list,
                                        pady=(0, SPACE[4]))
                else:
                    self._idle_box.pack_forget()
            except Exception as exc:
                events.debug("Idle Lines Not Placed", str(exc), source=SOURCE,
                             exception=exc)
        for name, remaining in due.items():
            seconds = max(0, int(math.ceil(float(remaining))))
            text = IDLE_LINE.format(name=name, seconds=seconds)
            label = self._idle_lines[name]["text"]
            try:
                if label.cget("text") != text:
                    label.configure(text=text)
            except Exception:
                pass

    def _idle_line(self, name):
        """"Stepper Probe powers down in 42 s." in ink, and Extend under it."""
        row = tk.Frame(self._idle_box, background=RAIL_FACE)
        row.pack(side="top", fill="x", pady=(0, SPACE[3]))
        text = tk.Label(row, text="", font=_font(), anchor="w", justify="left",
                        wraplength=self._rail_width(bool(self._is_narrow)) - 3 * SPACE[5],
                        background=RAIL_FACE, foreground=theme.TEXT)
        text.pack(side="top", fill="x")
        extend = _Press(row, EXTEND_WORD, lambda n=name: self._on_extend(n),
                        RAIL_FACE)
        extend.frame.pack(side="top", anchor="w", pady=(SPACE[1], 0))
        tooltip = _Tooltip(extend.widget)
        tooltip.text = f"{EXTEND_WORD} {name}"
        return {"row": row, "text": text, "extend": extend, "tooltip": tooltip}

    def _on_extend(self, name):
        """Extend: the probe's idle clock starts again (`extend_idle`). The
        line goes as soon as the state says so."""
        result = self.controller.run(name, "extend_idle")
        events.debug("Idle Extended", f"{name}: {getattr(result, 'status', result)}"
                     f" {getattr(result, 'reason', '') or ''}".rstrip(), source=SOURCE)
        self._sync_station_state()
        return result

    def _paint_rail_marks(self, latched=None, unconfirmed=None):
        """A latched model's rail line: an ink square before its name and
        the tooltip "Stopped"; one whose stop did not confirm, or whose
        disable failed (O4): its lamp slot in SIGNAL (Signature, rule 4)
        and the warning glyph in SIGNAL where the "!" square was (O16,
        rule 6), and "Did not confirm the stop" / "Disable failed" (L1).
        Shape and words, never colour alone."""
        if latched is None:
            seen = self._stop_seen or ((), (), False)
            latched, unconfirmed = seen[0], seen[1]
        size = _lamp_px()
        for name, (canvas, tooltip) in self._rail_marks.items():
            # A host's line carries the models drawn on its page (they have
            # no line of their own): the worst of them shows.
            members = self._members(name)
            if any(m in (unconfirmed or ()) for m in members):
                fill, words = theme.SIGNAL, RAIL_MARK_WORDS["unconfirmed"]
            elif any(m in self._faulted for m in members):
                fill, words = theme.SIGNAL, RAIL_MARK_WORDS["faulted"]
            elif any(m in latched for m in members):
                fill, words = theme.TEXT, RAIL_MARK_WORDS["latched"]
            else:
                fill, words = None, ""
            energy = self._energy_marks.get(name)
            is_energized = any(m in self._energized for m in members)
            if is_energized:
                words = (f"{words}; {RAIL_MARK_WORDS['energized'].lower()}" if words
                         else RAIL_MARK_WORDS["energized"])
            tooltip.text = words
            try:
                if energy is not None:
                    energy.delete("all")
                    if is_energized:
                        inset = max(3, size // 4)
                        energy.create_oval(inset, inset, size - inset, size - inset,
                                           outline=theme.TEXT, fill="", width=2)
            except Exception:
                pass
            self._paint_rail_lamp(name)
            try:
                canvas.delete("all")
                if fill == theme.SIGNAL:
                    # The alarm carries a shape of its own, the warning
                    # glyph: told apart from "stopped" without colour (A11Y-6).
                    _draw_glyph(canvas, "warning", 0, 0, size, fill,
                                tags=("warning",))
                elif fill is not None:
                    inset = max(3, size // 4)
                    canvas.create_rectangle(inset, inset, size - inset, size - inset,
                                            fill=fill, outline=fill)
            except Exception:
                pass

    def _drop_stop_lines(self):
        """The latch opened: a "Stop Not Confirmed" line describes a stop
        that is no longer set, so it leaves the band and the tray (L2)."""
        kept = [event for event in self._alerts if event.title != STOP_NOT_CONFIRMED]
        tray = [event for event in self._tray_events
                if event.title != STOP_NOT_CONFIRMED]
        if len(kept) == len(self._alerts) and len(tray) == len(self._tray_events):
            return
        events.debug("Stop Lines Dropped", "the latch opened", source=SOURCE)
        self._alerts = kept
        self._render_alerts()
        self._tray_events = tray
        self._render_tray()

    def _sync_station_line(self):
        """Name every model whose link is lost, and the device, in the rail
        under the stop - the one place the operator's eye passes on the way
        to it. And say "Simulation, no hardware attached" while every open
        model's hardware is the simulator."""
        lost = [(name, view.lost_devices) for name, view in self._panels.items()
                if getattr(view, "lost_devices", ())]
        text = "; ".join(f"{name}: {_device_list(devices)} connection lost"
                         for name, devices in lost)
        self._sync_sim_line()
        if text == self._station_text:
            return
        self._station_text = text
        try:
            self._station_line.configure(text=text)
            _draw_warning(self._station_mark, theme.SIGNAL if text else None)
            if text:
                self._station_row.pack(side="top", fill="x", before=self._model_list,
                                       pady=(0, SPACE[4]))
            else:
                self._station_row.pack_forget()
        except Exception as exc:
            events.debug("Station Line Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _sync_sim_line(self):
        """The links are the ones each model's state declares
        (`hardware_devices`, MOD-5), through `views.base.hardware_links`; a
        gamepad or the screen is not one."""
        statuses = []
        for name, view in self._panels.items():
            if name == self.SETUP_TAB:
                continue
            links = view_base.hardware_links(getattr(view, "_last_state", None))
            statuses += [str(status) for status in links.values()]
        text = (SIMULATION_LINE if statuses and all(status == "simulated"
                                                    for status in statuses) else "")
        if text == self._sim_text:
            return
        self._sim_text = text
        try:
            self._sim_line.configure(text=text)
            if text:
                self._sim_line.pack(fill="x")
            else:
                self._sim_line.pack_forget()
        except Exception:
            pass

    def _on_stop_clicked(self, _event=None):
        """The disc is `Dashboard.toggle_estop_all` (O17, as Qt): it clears
        only while every model is latched, and asks first in the Clear
        words; otherwise a press is `estop_all`. The rule lives once, in the
        base."""
        events.debug("Stop Pressed", "the disc", source=SOURCE)
        self._confirm_words = CLEAR_DIALOG
        try:
            result = self.toggle_estop_all()
        finally:
            self._confirm_words = None
        self._sync_stop_button()
        return result

    # -- panels ------------------------------------------------------------
    def _add_panel(self, name):
        if name in self._panels:
            return
        models = self._station_models() or {}
        host = self._placement(models, adding=name).get(name)
        view = self._new_view(name, host)
        self._panels[name] = view
        self._frames[name] = view.frame
        if host:
            self._hosts[name] = host
        # A host launched after its hosted models takes them onto its page.
        self._rehome(models)
        self._build_menu_bar()
        self._build_rail_list()
        if self._is_opening:
            self._lay_out_sheet()
        elif name == self._bring_forward:
            # A model reopened from the Models menu is brought forward, on
            # its own page: the reopen used to change nothing on screen
            # (AUD-13).
            self._bring_forward = None
            self.show_model(name)
        else:
            # A launch lands on the overview (K4): every model it started.
            self.show_overview()
        events.debug("Entry Opened", name, source=SOURCE)

    def _remove_panel(self, name):
        if name not in self._panels:
            return
        # The models drawn on this page get pages of their own back before
        # it goes: their widgets are inside it.
        still_open = set(self.controller.model_names)
        for hosted in [n for n, host in self._hosts.items() if host == name]:
            if hosted in still_open:
                self._place(hosted, None)
            else:
                # Leaving too: its own removal takes the entry away.
                self._panels[hosted].close()
                self._hosts.pop(hosted, None)
        self._destroy_panel(name)
        self._build_menu_bar()
        self._build_rail_list()
        self._lay_out_sheet()
        events.debug("Entry Closed", name, source=SOURCE)
        if self._is_setup_collapsed and not any(
                other != self.SETUP_TAB for other in self._panels):
            # An empty sheet is a dead end. The next step from "no model
            # is open" is Setup, so Setup comes back rather than a blank page.
            self.restore_setup()

    # -- the Setup page, minimised and brought back --------------------------
    def _collapse_setup(self):
        """`Dashboard` calls this when the first model launches: the wizard
        has done its job and the models want the window. A press on a model
        in the rail does the same.

        The page is *hidden*, never destroyed — the panel view keeps its
        widgets, its state and its refresh tick, so Refresh and Launch work
        the moment it comes back.
        """
        frame = self._frames.get(self.SETUP_TAB)
        if frame is None or self._is_setup_collapsed:
            return
        for step in (lambda: self.notebook.hide(frame),
                     lambda: self.notebook.forget(frame)):
            try:
                step()
                break
            except Exception as exc:
                events.debug("Setup Not Hidden", str(exc), source=SOURCE,
                             exception=exc)
        else:
            return
        self._is_setup_collapsed = True
        view = self._panels.get(self.SETUP_TAB)
        if view is not None:
            view.pause()             # hidden: no tick until it is back (F21)
        self._select_sheet()
        self._show_setup_button(True)
        self._build_menu_bar()
        events.info("Setup Minimised", "Setup is on the rail and the menu bar; "
                    "reopen it to re-scan or relaunch", source=SOURCE)

    def restore_setup(self):
        """Bring the Setup page back and show it. -> bool.

        Reopenable as often as the operator likes, and idempotent: asking for
        Setup while it is already showing shows it rather than adding a
        second page.
        """
        frame = self._frames.get(self.SETUP_TAB)
        if frame is None:
            return False
        try:
            self.notebook.add(frame, text=self.SETUP_TAB)
        except Exception as exc:
            events.debug("Setup Not Restored", str(exc), source=SOURCE,
                         exception=exc)
            return False
        try:
            self.notebook.select(frame)
        except Exception as exc:
            events.debug("Setup Not Selected", str(exc), source=SOURCE,
                         exception=exc)
        if self._is_setup_collapsed:
            events.debug("Setup Restored", "the Setup page is back", source=SOURCE)
        self._is_setup_collapsed = False
        self._shown_page = self.SETUP_TAB
        view = self._panels.get(self.SETUP_TAB)
        if view is not None:
            view.resume()
        self._show_setup_button(False)
        self._build_menu_bar()
        return True

    def _show_setup_button(self, is_shown):
        """The rail's Setup is always there (E); it is ink-filled while its
        page is the one shown, outlined while it is a press away."""
        self._paint_rail()

    def _on_setup_clicked(self, _event=None):
        return self.restore_setup()

    def _destroy_panel(self, name):
        view = self._panels.pop(name, None)
        frame = self._frames.pop(name, None)
        self._hosts.pop(name, None)
        if view is not None:
            try:
                view.close()
            except Exception as exc:
                events.debug("Panel Close Failed", f"{name}: {exc}", source=SOURCE,
                             exception=exc)
        if frame is not None and name == self.SETUP_TAB:
            for step in (lambda: self.notebook.forget(frame), frame.destroy):
                try:
                    step()
                except Exception:
                    pass

    def _on_tab_close(self, index):
        """A close gesture on a page. Only models close, and a model is not a
        page any more (its line in the rail takes the gesture): Setup and
        the sheet cannot be closed."""
        name = self._name_at(index)
        if name is None or name == self.SETUP_TAB:
            return
        events.debug("Tab Close Requested", name, source=SOURCE)
        self.close_model(name)

    def _name_at(self, index):
        try:
            tab_id = self.notebook.tabs()[index]
        except (IndexError, TypeError, _TCL_ERROR):
            return None
        for name, frame in self._frames.items():
            if str(frame) == str(tab_id):
                return name
        return None

    def _on_model_toggled(self, name):
        variable = self._menu_vars.get(name)
        wants_open = bool(variable.get()) if variable is not None else True
        events.debug("Model Menu Toggled", f"{name} -> "
                     f"{'open' if wants_open else 'closed'}", source=SOURCE)
        if wants_open:
            self._bring_forward = name
            try:
                self.open_model(name)
            except Exception:
                self._bring_forward = None
                raise
        elif not self._confirm_close_model(name) and variable is not None:
            variable.set(True)      # declined: the model is still open

    # -- events ------------------------------------------------------------
    def _marshal(self, fn):
        """Any thread -> the Tk thread. Tk is not thread-safe and an event can
        be published from a model's worker."""
        try:
            self.root.after(0, fn)
        except Exception as exc:
            events.debug("Marshal Failed", str(exc), source=SOURCE, exception=exc)

    def _show_event(self, event):
        """Status by exception (E): a warning or an error is one line in the
        tray - a mark in the severity's colour and shape (a hollow ink
        square, a solid signal one), then the line in the severity's ink
        (F14, UXPM-10): its severity word, title and message in sentence
        case, no bracketed source (L11). Info is silence here."""
        severity = event.severity if event.severity in theme.SEVERITY_ROLE else "info"
        if severity not in TRAY_SEVERITIES:
            return
        self._tray_events.append(event)
        del self._tray_events[:-TRAY_HISTORY]
        self._render_tray()

    def _render_tray(self):
        """The folded line is the latest event; the history is every kept
        one, the latest tagged. Redrawn whole, so a line can also leave
        (L2)."""
        # The idle warning is history only (O13): the countdown line under
        # the disc is its live word, so its frozen "in 60 s" never becomes
        # the folded line.
        latest = next((event for event in reversed(self._tray_events)
                       if event.title != IDLE_SOON_TITLE), None)
        try:
            if latest is None:
                _draw_warning(self._latest_mark, None)
                self._latest_text.configure(text="")
            else:
                # The tray line is led by the warning glyph (Signature, rule
                # 6): SIGNAL for an error, ink for a warning.
                severity = latest.severity
                _draw_warning(self._latest_mark, theme.SEVERITY_MARK[severity])
                self._latest_text.configure(text=_event_line(latest),
                                            foreground=theme.SEVERITY_INK[severity])
        except Exception:
            pass
        try:
            self._event_text.configure(state="normal")
            self._event_text.delete("1.0", "end")
            for index, event in enumerate(self._tray_events):
                severity = event.severity
                mark = (MARK_HOLLOW if severity in theme.SEVERITY_MARK_HOLLOW
                        else MARK_SOLID)
                if index:
                    self._event_text.insert("end", "\n")
                tags = (severity, "latest") if event is latest else (severity,)
                self._event_text.insert("end", f"{mark} ", (f"mark-{severity}",))
                self._event_text.insert("end", _event_line(event), tags)
            self._event_text.see("end")
            self._event_text.configure(state="disabled")
        except Exception as exc:
            events.debug("Event Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _show_popup(self, event):
        """An event that needs acknowledging joins the queue: the band lists
        it and `_AckDialog` shows the oldest (rb-ack). Neither is modal:
        `Dashboard._on_event` decided it earned acknowledgement, not that it
        may take the stop away."""
        if self._closing:
            return
        events.debug("Alert Shown", f"{event.severity}/{event.title}", source=SOURCE)
        self._alerts.append(event)
        del self._alerts[:-50]
        self._render_alerts()

    def _confirm(self, prompt):
        """The base's question (the disc's Clear), in the words the caller
        set for it: "Clear the stop" / "Keep it stopped" (L14)."""
        return _confirm(self.root, prompt, **(self._confirm_words or {}))

    # -- focus -------------------------------------------------------------
    def _on_window_focus(self, event=None):
        """D-4: gate gamepad input while unfocused; never stop.

        `focus_get()` returns the widget holding focus *within this
        application*, so a non-None answer means a child dialog took it and
        focus is still ours. Treating that as focus loss is VIEW-TKINTER-9.
        """
        if event is not None and getattr(event, "widget", self.root) is not self.root:
            return
        try:
            is_focused = self.root.focus_get() is not None
        except Exception:
            is_focused = False      # the window is going away: gate closed
        if is_focused == self._is_focused:
            return
        self._is_focused = is_focused
        events.debug("Focus Gate Changed",
                     f"input {'enabled' if is_focused else 'gated'}", source=SOURCE)
        self._on_focus_change(is_focused)
